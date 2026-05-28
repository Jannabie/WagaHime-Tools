#!/usr/bin/env python3
"""
ACV1 script/archive extractor for Forlos/vn_re-style archives.

Works for Waga Himegimi ni Eikan o script archives when you already know the
script key (default: 0x3793B711, reported by the community).

What it does:
  - Parses the ACV1 header
  - Decrypts entry metadata
  - Extracts each entry
  - For script archives, applies the documented 2-step XOR + zlib decode
  - Writes both raw decrypted data and decoded text when possible

Usage:
  python acv1_extract.py script.dat
  python acv1_extract.py script.dat --script-key 0x3793B711 --out out_dir

Notes:
  - This is for the "scripts" side of ACV1 archives.
  - Resource archives that rely on original file names are not handled here.
"""

from __future__ import annotations

import argparse
import binascii
import json
import struct
import sys
import zlib
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

MASTER_KEY_DEFAULT = 0x8B6A4E5F
SCRIPT_KEY_DEFAULT = 0x3793B711  # reported for Waga Himegimi ni Eikan o


def u32(x: bytes) -> int:
    return struct.unpack("<I", x)[0]


def u64(x: bytes) -> int:
    return struct.unpack("<Q", x)[0]


def xor_bytes_4(data: bytes, key_u32: int) -> bytes:
    """XOR every full 4-byte chunk with the little-endian bytes of key_u32."""
    out = bytearray(data)
    key = key_u32.to_bytes(4, "little", signed=False)
    limit = len(out) - (len(out) % 4)
    for i in range(0, limit, 4):
        out[i] ^= key[0]
        out[i + 1] ^= key[1]
        out[i + 2] ^= key[2]
        out[i + 3] ^= key[3]
    return bytes(out)


@dataclass
class Entry:
    index: int
    checksum: int
    flags: int
    offset: int
    size: int
    unpacked_size: int


def parse_archive(path: Path, master_key: int = MASTER_KEY_DEFAULT) -> tuple[bytes, List[Entry]]:
    data = path.read_bytes()
    if data[:4] != b"ACV1":
        raise ValueError(f"{path.name}: bad magic {data[:4]!r}, expected b'ACV1'")

    raw_count = u32(data[4:8])
    count = raw_count ^ master_key
    if count < 0 or count > 10_000_000:
        raise ValueError(f"{path.name}: suspicious entry count {count} (raw=0x{raw_count:08x})")

    entries: List[Entry] = []
    table_off = 8
    entry_size = 21
    table_end = table_off + count * entry_size
    if table_end > len(data):
        raise ValueError(
            f"{path.name}: entry table overruns file (count={count}, table_end={table_end}, size={len(data)})"
        )

    for i in range(count):
        off = table_off + i * entry_size
        checksum = u64(data[off:off + 8])
        checksum32 = checksum & 0xFFFFFFFF
        flags = data[off + 8] ^ (checksum & 0xFF)
        file_off = (u32(data[off + 9:off + 13]) ^ checksum32 ^ master_key) & 0xFFFFFFFF
        size = (u32(data[off + 13:off + 17]) ^ checksum32) & 0xFFFFFFFF
        unpacked_size = (u32(data[off + 17:off + 21]) ^ checksum32) & 0xFFFFFFFF
        entries.append(Entry(i, checksum, flags, file_off, size, unpacked_size))
    return data, entries


def decode_script_blob(blob: bytes, checksum: int, script_key: int) -> bytes:
    checksum32 = checksum & 0xFFFFFFFF
    step1 = xor_bytes_4(blob, checksum32)
    step2 = xor_bytes_4(step1, script_key)
    return zlib.decompress(step2)


def best_text_from_bytes(data: bytes) -> str:
    # Use cp932 (Microsoft Shift-JIS) as the primary Japanese codec.
    # cp932 and shift_jis share most of their character space, but diverge on a
    # handful of codepoints (e.g. 0x817C: cp932 -> U+FF0D FULLWIDTH MINUS,
    # shift_jis -> U+2212 MINUS SIGN).  Using cp932 here ensures the repacker
    # can encode the saved .txt back to bytes that are byte-identical to the
    # original archive payload.
    for enc in ("utf-8", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("cp932", errors="replace")


def safe_ext_from_text(text: str) -> str:
    # If it looks like a scenario/script file, txt is a good default.
    # Keep it simple and safe.
    return ".txt"


def extract(path: Path, out_dir: Path, master_key: int, script_key: int, write_raw: bool, write_text: bool) -> None:
    data, entries = parse_archive(path, master_key=master_key)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "archive": path.name,
        "size": len(data),
        "entry_count": len(entries),
        "master_key": f"0x{master_key:08x}",
        "script_key": f"0x{script_key:08x}",
        "entries": [],
    }

    success = 0
    failures = 0

    for e in entries:
        entry_info = asdict(e)
        entry_info["checksum_hex"] = f"0x{e.checksum:016x}"
        entry_info["flags_hex"] = f"0x{e.flags:02x}"

        if e.offset + e.size > len(data):
            entry_info["status"] = "out_of_bounds"
            failures += 1
            manifest["entries"].append(entry_info)
            continue

        blob = data[e.offset:e.offset + e.size]
        entry_base = out_dir / f"{e.index:04d}_{e.checksum:016x}"
        try:
            decoded = decode_script_blob(blob, e.checksum, script_key)
            entry_info["status"] = "ok"
            entry_info["decoded_size"] = len(decoded)
            success += 1

            if write_raw:
                (entry_base.with_suffix(".bin")).write_bytes(decoded)

            if write_text:
                text = best_text_from_bytes(decoded)
                (entry_base.with_suffix(safe_ext_from_text(text))).write_text(
                    text, encoding="utf-8", newline="\n"
                )
        except Exception as ex:
            entry_info["status"] = f"decode_failed: {type(ex).__name__}: {ex}"
            failures += 1
            # Keep the raw encrypted blob too, in case we need to inspect it later.
            if write_raw:
                (entry_base.with_suffix(".cipher.bin")).write_bytes(blob)

        manifest["entries"].append(entry_info)

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[+] Archive: {path.name}")
    print(f"[+] Entries: {len(entries)}")
    print(f"[+] OK: {success}  Failed: {failures}")
    print(f"[+] Output: {out_dir}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Extract ACV1 script archives")
    parser.add_argument("archives", nargs="+", help="One or more .dat ACV1 archives")
    parser.add_argument("--out", default=None, help="Output directory (default: <archive>_extracted)")
    parser.add_argument("--master-key", default=f"0x{MASTER_KEY_DEFAULT:08x}", help="ACV1 master key")
    parser.add_argument("--script-key", default=f"0x{SCRIPT_KEY_DEFAULT:08x}", help="Game script key")
    parser.add_argument("--no-raw", action="store_true", help="Do not write decoded raw bytes")
    parser.add_argument("--no-text", action="store_true", help="Do not write text files")
    args = parser.parse_args(argv)

    master_key = int(str(args.master_key), 0)
    script_key = int(str(args.script_key), 0)

    for archive in args.archives:
        p = Path(archive)
        if not p.exists():
            print(f"[!] Missing file: {p}", file=sys.stderr)
            continue
        out_dir = Path(args.out) if args.out else p.with_name(p.stem + "_extracted")
        if len(args.archives) > 1 and args.out:
            out_dir = out_dir / p.stem
        extract(p, out_dir, master_key, script_key, write_raw=not args.no_raw, write_text=not args.no_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
