#!/usr/bin/env python3
"""ACV1 repacker for VN_re-style archives.

Designed to pair with the extractor script already used for Waga Himegimi ni
Eikan o.

Workflow:
  1) Extract archive with acv1_extractor.py
  2) Edit the generated .txt files in the extracted folder
  3) Repack into a new .dat archive that the game can read

Assumptions:
  - The archive format is ACV1
  - Entry metadata uses the same XOR scheme as the extractor
  - Script blobs are zlib-compressed and then XOR-obfuscated
  - The original archive's entry checksum/flags are preserved

Usage:
  py -3.10 acv1_repacker.py original_script.dat script_extracted --out script_patched.dat

Optional:
  --text-encoding cp932   Encode edited .txt files using CP932/Shift-JIS (default, required for this engine)
  --text-encoding utf-8   Use UTF-8 (WARNING: breaks label offsets for Shift-JIS VN scripts)
  --level 9               zlib compression level

File selection priority per entry:
  1. <index>_<checksum>.txt   (translated text)
  2. <index>_<checksum>.bin   (raw decoded bytes)
  3. <index>_<checksum>.cipher.bin (already-encrypted blob; copied as-is)
  4. Original entry payload from the source archive (fallback)
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

MASTER_KEY_DEFAULT = 0x8B6A4E5F


def u32(x: bytes) -> int:
    return struct.unpack("<I", x)[0]


def u64(x: bytes) -> int:
    return struct.unpack("<Q", x)[0]


def pack_u32(x: int) -> bytes:
    return struct.pack("<I", x & 0xFFFFFFFF)


def pack_u64(x: int) -> bytes:
    return struct.pack("<Q", x & 0xFFFFFFFFFFFFFFFF)


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


def parse_archive(path: Path, master_key: int = MASTER_KEY_DEFAULT) -> Tuple[bytes, List[Entry]]:
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


def encode_script_blob(decoded: bytes, checksum: int, script_key: int, level: int = 9) -> bytes:
    compressed = zlib.compress(decoded, level)
    step1 = xor_bytes_4(compressed, script_key)
    step2 = xor_bytes_4(step1, checksum & 0xFFFFFFFF)
    return step2


def load_manifest(extracted_dir: Path) -> Optional[dict]:
    manifest = extracted_dir / "manifest.json"
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None


def pick_entry_source(extracted_dir: Path, entry: Entry) -> Tuple[str, Path]:
    base = f"{entry.index:04d}_{entry.checksum:016x}"
    candidates = [
        ("text", extracted_dir / f"{base}.txt"),
        ("raw", extracted_dir / f"{base}.bin"),
        ("cipher", extracted_dir / f"{base}.cipher.bin"),
    ]
    for kind, p in candidates:
        if p.exists():
            return kind, p
    return "fallback", Path("")


def read_payload(kind: str, path: Path, text_encoding: str) -> bytes:
    if kind == "text":
        # .txt files are always saved as UTF-8 by the extractor.
        # Re-encode to the target encoding (cp932 by default) so the
        # game engine receives the original byte representation it expects.
        # Using utf-8 here would shift every multi-byte Japanese character
        # from 2 bytes (CP932) to 3 bytes (UTF-8), corrupting all byte
        # offsets the VM uses to resolve labels and jump targets.
        #
        # NOTE: we intentionally do NOT do a codec round-trip check here.
        # Python's shift_jis and cp932 codecs share the same byte space but
        # differ on a small set of Unicode codepoints (e.g. 0x817C maps to
        # U+2212 via shift_jis but U+FF0D via cp932).  If the extractor ran
        # with shift_jis, the saved Unicode text will contain the shift_jis
        # codepoint; encoding that back through cp932 still produces the
        # correct original bytes, even though a naive round-trip decode would
        # yield a different Unicode character.  What matters is the final byte
        # output, not codec-internal codepoint consistency.
        text = path.read_text(encoding="utf-8")
        try:
            return text.encode(text_encoding, errors="strict")
        except UnicodeEncodeError as exc:
            # A character in the edited text genuinely cannot be represented in
            # the target encoding.  Warn and fall back to replacement so the
            # repack can still proceed; the affected glyph will become "?" in
            # the output.
            print(
                f"[!] Warning: {path.name}: character U+{ord(exc.object[exc.start]):04X} "
                f"at position {exc.start} cannot be encoded as {text_encoding}. "
                f"It will be replaced with '?'. "
                f"To suppress this, use --text-encoding utf-8 for that file "
                f"or ensure your edits only contain characters supported by {text_encoding}.",
                file=__import__("sys").stderr,
            )
            return text.encode(text_encoding, errors="replace")
    if kind == "raw":
        return path.read_bytes()
    if kind == "cipher":
        # Already-obfuscated blob. Useful for testing, but not ideal for edited content.
        return path.read_bytes()
    raise ValueError(f"Unsupported payload kind: {kind}")


def pack_archive(
    original_path: Path,
    extracted_dir: Path,
    out_path: Path,
    master_key: int,
    script_key: int,
    text_encoding: str,
    compress_level: int,
) -> None:
    original_data, entries = parse_archive(original_path, master_key=master_key)
    table_size = 8 + len(entries) * 21

    # Resolve payloads first so we know sizes before writing.
    packed_payloads: List[bytes] = []
    payload_sources: List[str] = []
    unpacked_sizes: List[int] = []

    for entry in entries:
        kind, src = pick_entry_source(extracted_dir, entry)
        if kind == "fallback":
            # Use original encrypted bytes if the extracted file is missing.
            if entry.offset + entry.size > len(original_data):
                raise ValueError(f"Entry {entry.index}: original payload is out of bounds")
            blob = original_data[entry.offset:entry.offset + entry.size]
            packed_payloads.append(blob)
            payload_sources.append("original")
            unpacked_sizes.append(entry.unpacked_size)
            continue

        payload_sources.append(f"{kind}:{src.name}")
        payload = read_payload(kind, src, text_encoding)

        if kind == "cipher":
            # Keep the original encrypted blob as-is.
            packed_payloads.append(payload)
            # Fallback to original unpacked size because we do not know it from cipher blob alone.
            unpacked_sizes.append(entry.unpacked_size)
        else:
            # Recompress and re-obfuscate the decoded payload.
            try:
                encoded = encode_script_blob(payload, entry.checksum, script_key, level=compress_level)
            except Exception as ex:
                raise RuntimeError(f"Entry {entry.index}: failed to encode {src.name}: {ex}") from ex
            packed_payloads.append(encoded)
            unpacked_sizes.append(len(payload))

    # Build file.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        f.write(b"ACV1")
        f.write(pack_u32(len(entries) ^ master_key))

        # Reserve table space.
        f.write(b"\x00" * (len(entries) * 21))

        data_offsets: List[int] = []
        current_off = table_size
        for blob in packed_payloads:
            data_offsets.append(current_off)
            f.write(blob)
            current_off += len(blob)

        # Write table.
        f.seek(8)
        for entry, file_off, blob, unpacked_size in zip(entries, data_offsets, packed_payloads, unpacked_sizes):
            checksum32 = entry.checksum & 0xFFFFFFFF
            stored_flags = entry.flags ^ (entry.checksum & 0xFF)
            stored_off = file_off ^ checksum32 ^ master_key
            stored_size = len(blob) ^ checksum32
            stored_unpacked = unpacked_size ^ checksum32

            f.write(pack_u64(entry.checksum))
            f.write(bytes([stored_flags & 0xFF]))
            f.write(pack_u32(stored_off))
            f.write(pack_u32(stored_size))
            f.write(pack_u32(stored_unpacked))

    manifest = {
        "source_archive": original_path.name,
        "output_archive": out_path.name,
        "entry_count": len(entries),
        "master_key": f"0x{master_key:08x}",
        "script_key": f"0x{script_key:08x}",
        "text_encoding": text_encoding,
        "compress_level": compress_level,
        "entries": [
            {
                "index": e.index,
                "checksum": f"0x{e.checksum:016x}",
                "payload_source": src,
                "size": len(blob),
                "unpacked_size": unpacked,
            }
            for e, src, blob, unpacked in zip(entries, payload_sources, packed_payloads, unpacked_sizes)
        ],
    }
    (out_path.with_suffix(out_path.suffix + ".manifest.json")).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[+] Packed: {out_path}")
    print(f"[+] Entries: {len(entries)}")
    print(f"[+] Manifest: {out_path.with_suffix(out_path.suffix + '.manifest.json')}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Repack ACV1 script archives")
    parser.add_argument("original_archive", help="Original .dat archive used as metadata source")
    parser.add_argument("extracted_dir", help="Folder produced by the extractor")
    parser.add_argument("--out", required=True, help="Output archive path")
    parser.add_argument("--master-key", default=f"0x{MASTER_KEY_DEFAULT:08x}", help="ACV1 master key")
    parser.add_argument("--script-key", default="0x3793B711", help="Game script key")
    parser.add_argument("--text-encoding", default="cp932", help="Encoding for edited .txt files (default: cp932 for Shift-JIS VN scripts)")
    parser.add_argument("--level", type=int, default=9, help="zlib compression level (0-9)")
    args = parser.parse_args(argv)

    original_path = Path(args.original_archive)
    extracted_dir = Path(args.extracted_dir)
    out_path = Path(args.out)

    if not original_path.exists():
        print(f"[!] Missing original archive: {original_path}", file=sys.stderr)
        return 1
    if not extracted_dir.exists():
        print(f"[!] Missing extracted folder: {extracted_dir}", file=sys.stderr)
        return 1

    master_key = int(str(args.master_key), 0)
    script_key = int(str(args.script_key), 0)

    pack_archive(
        original_path=original_path,
        extracted_dir=extracted_dir,
        out_path=out_path,
        master_key=master_key,
        script_key=script_key,
        text_encoding=args.text_encoding,
        compress_level=args.level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
