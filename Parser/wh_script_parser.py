#!/usr/bin/env python3
"""
WagaHime Script Parser  —  wh_script_parser.py
===============================================
Translator-friendly parser for Waga Himegimi ni Eikan o script files.

Reads the .txt files produced by acv1_extractor.py, parses every line into
a typed node, and can:
  - Export JSON  (full or dialog-only)
  - Export CSV   (dialog-only, ready for spreadsheet translation)
  - Import JSON/CSV back to the original script text format
  - Round-trip perfectly with acv1_repacker.py (no byte changes to commands)

Usage examples
--------------
  # Parse one file, export full JSON
  python wh_script_parser.py parse 0007.txt --out 0007.json

  # Dialog-only CSV for translation
  python wh_script_parser.py export-csv 0007.txt --out 0007_dialog.csv

  # Rebuild script from translated JSON
  python wh_script_parser.py import 0007.json --out 0007_translated.txt

  # Pretty-print dialog to terminal
  python wh_script_parser.py show-dialog 0007.txt

  # Parse all .txt files in a folder
  python wh_script_parser.py parse-all extracted/ --out-dir json_out/
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional, Union


# ---------------------------------------------------------------------------
# Node types
# ---------------------------------------------------------------------------

@dataclass
class DialogNode:
    """A voiced or unvoiced line of dialogue / narration.

    speaker_internal  : internal tag used in voice filenames (e.g. "Foru")
    speaker_display   : shown in the text box; may differ (e.g. "Host Ponse")
    voice_id          : voice filename key  (e.g. "S035_B2_0036") or ""
    text              : the actual dialogue text, without「」brackets
    is_narration      : True when 【】 (no speaker at all)
    original_line     : verbatim raw line for round-trip fidelity
    line_number       : 1-based line in the source file
    """
    speaker_internal: str
    speaker_display: str
    voice_id: str
    text: str
    is_narration: bool
    original_line: str
    line_number: int
    node_type: str = "dialog"

    # Filled in by the caller if needed
    translated_text: str = ""

    def to_script_line(self) -> str:
        """Reconstruct the original script line (used by the importer)."""
        if self.translated_text:
            return _rebuild_dialog_line(self, self.translated_text)
        return self.original_line


@dataclass
class CommandNode:
    """An engine command: BG, BGM, ST, SE0, WA, SC.FD, MW.FC, etc.

    command   : the command mnemonic  (e.g. "BG", "ST", "SE0")
    args      : everything after the command on that line (raw string)
    original_line : verbatim raw line
    line_number   : 1-based
    """
    command: str
    args: str
    original_line: str
    line_number: int
    node_type: str = "command"

    def to_script_line(self) -> str:
        return self.original_line


@dataclass
class LabelNode:
    """A script label: *label_name"""
    name: str
    original_line: str
    line_number: int
    node_type: str = "label"

    def to_script_line(self) -> str:
        return self.original_line


@dataclass
class CodeNode:
    """Any other script statement: variable declarations, control flow, etc.

    Kept verbatim; the parser does not attempt to interpret these.
    """
    code: str
    original_line: str
    line_number: int
    node_type: str = "code"

    def to_script_line(self) -> str:
        return self.original_line


@dataclass
class BlankNode:
    """An empty line. Preserved for faithful round-trip."""
    original_line: str
    line_number: int
    node_type: str = "blank"

    def to_script_line(self) -> str:
        return self.original_line


ScriptNode = Union[DialogNode, CommandNode, LabelNode, CodeNode, BlankNode]


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Engine commands — single word of uppercase letters, digits, dots
_RE_COMMAND = re.compile(
    r"^([A-Z][A-Z0-9]*(?:\.[A-Z][A-Z0-9]*)*)(?:\s+(.*))?$"
)

# Dialog line  【...】「...」
#   Group 1: everything inside 【】
#   Group 2: text inside 「」 (or rest of line if bracket not closed)
_RE_DIALOG = re.compile(r"^【([^】]*)】(.*)$")

# Inside 【】: "SpeakerInternal@SpeakerDisplay,VoiceID"
#   or just "Speaker,VoiceID"  /  "Speaker"  /  ""
_RE_SPEAKER_BLOCK = re.compile(
    r"^(?:([^@,]*)@)?([^,]*)(?:,(.*))?$"
)

# Label: *name
_RE_LABEL = re.compile(r"^\*(\S+)$")

# Known engine commands (anything that is ALL-CAPS + dots + digits wins over code)
_KNOWN_COMMANDS = {
    "BG", "BGM", "CG", "CS", "AS", "SE0", "SE1", "SE2",
    "ST", "WA", "SC", "MW", "XM", "SELECT", "BG.WH", "BG.XY",
    "BG.DF", "BG.SK", "CG.A", "CG.AN", "CG.CF", "CG.CL", "CG.DEL",
    "CG.SK", "CG.WH", "CG.XY", "MW.FC", "MW.TP", "SC.FD", "SC.FL",
    "SC.SK", "SC.WH", "ST.DEL", "ST.DU", "ST.EM", "ST.RLR", "ST.SK",
    "ST.WA", "ST.WH", "ST.XY", "ST0",
}


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

def parse_line(raw: str, line_number: int) -> ScriptNode:
    """Parse a single raw line into a ScriptNode."""
    # Preserve the original (with newline stripped for storage, restored on emit)
    original = raw.rstrip("\n")

    stripped = original.strip()

    # Blank line
    if not stripped:
        return BlankNode(original_line=original, line_number=line_number)

    # Label
    m = _RE_LABEL.match(stripped)
    if m:
        return LabelNode(name=m.group(1), original_line=original, line_number=line_number)

    # Dialog line
    m = _RE_DIALOG.match(stripped)
    if m:
        speaker_block = m.group(1)   # inside 【】
        text_part     = m.group(2)   # after 】

        # Strip 「」 brackets from text if present
        text = text_part.strip()
        if text.startswith("「") and text.endswith("」"):
            text = text[1:-1]
        elif text.startswith("「"):
            text = text[1:]

        # Parse speaker block
        if not speaker_block:
            # Narration
            return DialogNode(
                speaker_internal="",
                speaker_display="",
                voice_id="",
                text=text,
                is_narration=True,
                original_line=original,
                line_number=line_number,
            )

        sm = _RE_SPEAKER_BLOCK.match(speaker_block)
        if sm:
            internal_raw = (sm.group(1) or "").strip()
            display_raw  = (sm.group(2) or "").strip()
            voice_raw    = (sm.group(3) or "").strip()
        else:
            internal_raw = ""
            display_raw  = speaker_block.strip()
            voice_raw    = ""

        # If no @ present: group(1) is None, group(2) holds the only name
        # In that case display == internal
        if not internal_raw:
            internal_raw = display_raw

        return DialogNode(
            speaker_internal=internal_raw,
            speaker_display=display_raw if display_raw else internal_raw,
            voice_id=voice_raw,
            text=text,
            is_narration=False,
            original_line=original,
            line_number=line_number,
        )

    # Engine command (must start with an uppercase letter/word, optionally dotted)
    m = _RE_COMMAND.match(stripped)
    if m:
        cmd = m.group(1)
        args = (m.group(2) or "").strip()
        # Heuristic: command if the word is in the known set OR is all-caps
        if cmd in _KNOWN_COMMANDS or (cmd.replace(".", "").isupper() and len(cmd) <= 12):
            return CommandNode(
                command=cmd,
                args=args,
                original_line=original,
                line_number=line_number,
            )

    # Everything else: generic code / control flow / variable declarations
    return CodeNode(code=stripped, original_line=original, line_number=line_number)


def parse_file(path: Path) -> List[ScriptNode]:
    """Parse an entire extracted .txt script file into a list of nodes."""
    text = path.read_text(encoding="utf-8")
    nodes: List[ScriptNode] = []
    for i, raw_line in enumerate(text.splitlines(keepends=False), start=1):
        nodes.append(parse_line(raw_line, i))
    return nodes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def iter_dialog(nodes: List[ScriptNode]) -> Iterator[DialogNode]:
    for n in nodes:
        if isinstance(n, DialogNode):
            yield n


def _rebuild_dialog_line(node: DialogNode, new_text: str) -> str:
    """Rebuild 【speaker,voice】「text」 from a DialogNode with new text."""
    if node.is_narration:
        return f"【】{new_text}"

    # Reconstruct speaker block
    has_alias = (node.speaker_internal != node.speaker_display
                 and node.speaker_display)
    if has_alias:
        speaker_block = f"{node.speaker_internal}@{node.speaker_display}"
    else:
        speaker_block = node.speaker_internal

    if node.voice_id:
        speaker_block = f"{speaker_block},{node.voice_id}"

    # Detect whether original used 「」 brackets around text
    orig_text_part = node.original_line.split("】", 1)[-1].strip() if "】" in node.original_line else ""
    if orig_text_part.startswith("「"):
        return f"【{speaker_block}】「{new_text}」"
    return f"【{speaker_block}】{new_text}"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def nodes_to_dict(nodes: List[ScriptNode], source_file: str = "") -> dict:
    """Convert node list to a JSON-serializable dict."""
    return {
        "source_file": source_file,
        "node_count": len(nodes),
        "dialog_count": sum(1 for n in nodes if isinstance(n, DialogNode)),
        "nodes": [asdict(n) for n in nodes],
    }


def nodes_to_script(nodes: List[ScriptNode]) -> str:
    """Reconstruct the original script text from nodes (for round-trip)."""
    return "\n".join(n.to_script_line() for n in nodes)


def dialog_to_csv_rows(nodes: List[ScriptNode]) -> List[dict]:
    """Extract dialog nodes into CSV-friendly dicts."""
    rows = []
    for n in iter_dialog(nodes):
        rows.append({
            "line_number":       n.line_number,
            "is_narration":      "yes" if n.is_narration else "no",
            "speaker_internal":  n.speaker_internal,
            "speaker_display":   n.speaker_display,
            "voice_id":          n.voice_id,
            "original_text":     n.text,
            "translated_text":   n.translated_text or "",
        })
    return rows


# ---------------------------------------------------------------------------
# Import (JSON/CSV → script nodes)
# ---------------------------------------------------------------------------

def import_from_json(json_path: Path) -> List[ScriptNode]:
    """Load a previously exported JSON and reconstruct the node list.

    If 'translated_text' is present on a dialog node, it is used when
    calling nodes_to_script().
    """
    data = json.loads(json_path.read_text(encoding="utf-8"))
    nodes: List[ScriptNode] = []
    for raw in data["nodes"]:
        t = raw["node_type"]
        if t == "dialog":
            n = DialogNode(**{k: v for k, v in raw.items() if k != "node_type"})
            nodes.append(n)
        elif t == "command":
            nodes.append(CommandNode(**{k: v for k, v in raw.items() if k != "node_type"}))
        elif t == "label":
            nodes.append(LabelNode(**{k: v for k, v in raw.items() if k != "node_type"}))
        elif t == "code":
            nodes.append(CodeNode(**{k: v for k, v in raw.items() if k != "node_type"}))
        else:
            nodes.append(BlankNode(**{k: v for k, v in raw.items() if k != "node_type"}))
    return nodes


def apply_csv_translations(nodes: List[ScriptNode], csv_path: Path) -> List[ScriptNode]:
    """Read a translated CSV and patch the DialogNodes in-place.

    The CSV must have columns: line_number, translated_text.
    All other columns are ignored.
    """
    translations: dict[int, str] = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ln = int(row["line_number"])
            tx = row.get("translated_text", "").strip()
            if tx:
                translations[ln] = tx

    for n in nodes:
        if isinstance(n, DialogNode) and n.line_number in translations:
            n.translated_text = translations[n.line_number]
    return nodes


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_parse(args):
    src = Path(args.src)
    nodes = parse_file(src)
    data = nodes_to_dict(nodes, source_file=str(src))
    out = Path(args.out) if args.out else src.with_suffix(".json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[+] Parsed {len(nodes)} nodes ({data['dialog_count']} dialog lines)")
    print(f"[+] JSON → {out}")


def cmd_parse_all(args):
    src_dir = Path(args.src_dir)
    out_dir = Path(args.out_dir) if args.out_dir else src_dir / "json_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_files = sorted(src_dir.glob("*.txt"))
    if not txt_files:
        print(f"[!] No .txt files found in {src_dir}")
        return
    for f in txt_files:
        nodes = parse_file(f)
        data = nodes_to_dict(nodes, source_file=str(f))
        out = out_dir / f.with_suffix(".json").name
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {f.name} → {out.name}  ({data['dialog_count']} dialog lines)")
    print(f"[+] Done. {len(txt_files)} files → {out_dir}")


def cmd_export_csv(args):
    src = Path(args.src)
    nodes = parse_file(src)
    rows = dialog_to_csv_rows(nodes)
    out = Path(args.out) if args.out else src.with_suffix(".csv")
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["line_number", "is_narration", "speaker_internal",
                        "speaker_display", "voice_id", "original_text",
                        "translated_text"],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"[+] {len(rows)} dialog lines → {out}")


def cmd_import(args):
    """Rebuild a .txt script from a JSON (with optional CSV overlay)."""
    json_path = Path(args.json)
    nodes = import_from_json(json_path)

    if args.csv:
        nodes = apply_csv_translations(nodes, Path(args.csv))

    script = nodes_to_script(nodes)

    out = Path(args.out) if args.out else json_path.with_suffix(".txt")
    out.write_text(script, encoding="utf-8")
    translated = sum(1 for n in nodes if isinstance(n, DialogNode) and n.translated_text)
    print(f"[+] {translated} lines translated, {len(nodes)} total nodes → {out}")


def cmd_show_dialog(args):
    src = Path(args.src)
    nodes = parse_file(src)
    count = 0
    for n in iter_dialog(nodes):
        if n.is_narration:
            print(f"  [L{n.line_number:>4}] (narration)  {n.text}")
        else:
            voice = f" [{n.voice_id}]" if n.voice_id else ""
            print(f"  [L{n.line_number:>4}] {n.speaker_display}{voice}")
            print(f"           {n.text}")
        count += 1
    print(f"\n[+] {count} dialog lines in {src.name}")


def cmd_stats(args):
    src = Path(args.src)
    nodes = parse_file(src)
    type_counts: dict[str, int] = {}
    speakers: dict[str, int] = {}
    for n in nodes:
        type_counts[n.node_type] = type_counts.get(n.node_type, 0) + 1
        if isinstance(n, DialogNode) and not n.is_narration:
            speakers[n.speaker_display] = speakers.get(n.speaker_display, 0) + 1

    print(f"\n=== {src.name} ===")
    print("\nNode types:")
    for t, c in sorted(type_counts.items()):
        print(f"  {t:<12} {c}")
    if speakers:
        print("\nSpeaker line counts:")
        for spk, c in sorted(speakers.items(), key=lambda x: -x[1]):
            print(f"  {spk:<20} {c}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        description="WagaHime script parser — translator toolkit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    # parse
    sp = sub.add_parser("parse", help="Parse one .txt → JSON")
    sp.add_argument("src", help="Source .txt file")
    sp.add_argument("--out", help="Output .json path (default: same name)")
    sp.set_defaults(func=cmd_parse)

    # parse-all
    sp = sub.add_parser("parse-all", help="Parse all .txt in a folder → JSON")
    sp.add_argument("src_dir", help="Folder with .txt files")
    sp.add_argument("--out-dir", help="Output folder (default: <src_dir>/json_out)")
    sp.set_defaults(func=cmd_parse_all)

    # export-csv
    sp = sub.add_parser("export-csv", help="Export dialog lines to CSV")
    sp.add_argument("src", help="Source .txt file")
    sp.add_argument("--out", help="Output .csv path (default: same name)")
    sp.set_defaults(func=cmd_export_csv)

    # import
    sp = sub.add_parser("import", help="Rebuild .txt from JSON (+ optional CSV translations)")
    sp.add_argument("json", help="JSON file (from 'parse' command)")
    sp.add_argument("--csv", help="CSV with translated_text column to overlay")
    sp.add_argument("--out", help="Output .txt path")
    sp.set_defaults(func=cmd_import)

    # show-dialog
    sp = sub.add_parser("show-dialog", help="Pretty-print dialog lines to terminal")
    sp.add_argument("src", help="Source .txt file")
    sp.set_defaults(func=cmd_show_dialog)

    # stats
    sp = sub.add_parser("stats", help="Show node statistics for a file")
    sp.add_argument("src", help="Source .txt file")
    sp.set_defaults(func=cmd_stats)

    return p


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
