# ACV1 Script Tools

Extract, edit, and repack script archives from **Waga Himegimi ni Eikan o** (and potentially other Forlos/vn_re-engine titles).

---

## Requirements

- Python 3.10+
- No external dependencies (stdlib only)

---

## Files

| File | Purpose |
|---|---|
| `acv1_extractor.py` | Decrypt and extract script entries from `.dat` archive |
| `acv1_repacker.py` | Repack edited scripts back into a `.dat` archive |

---

## Workflow

### 1. Extract
```bash
python acv1_extractor.py script3.dat
```
Output folder: `script3_extracted/`

Each entry is saved as `<index>_<checksum>.txt` (UTF-8). A `manifest.json` is also written with full entry metadata.

### 2. Edit / Translate
Open any `.txt` file in the extracted folder and edit the text freely. Files are UTF-8 so any editor works.

> ⚠️ **Only edit the `.txt` files — never the `.bin` files.**
> `.bin` files are raw decoded bytes and are only there as a fallback. The repacker reads `.txt` first. If you edit a `.bin`, it gets ignored as long as a `.txt` with the same name exists.

File priority the repacker uses per entry:
```
1. <index>_<checksum>.txt   ← edit this for translation
2. <index>_<checksum>.bin   ← raw bytes, do not touch
3. <index>_<checksum>.cipher.bin  ← encrypted blob, fallback only
4. original entry from source archive
```

### Proof of concept

Translation working in-game:

![Translation working in-game](https://i.imgur.com/3c1dV98.jpeg)

### 3. Repack
```bash
python acv1_repacker.py script3.dat script3_extracted --out script3_patched.dat
```
Done. Replace the original `.dat` with `script3_patched.dat`.

---

## Options

### Extractor
```
--out <dir>           Output directory (default: <archive>_extracted)
--master-key 0x...    ACV1 master key (default: 0x8B6A4E5F)
--script-key 0x...    Game script key (default: 0x3793B711)
--no-raw              Skip writing decoded .bin files
--no-text             Skip writing .txt files
```

### Repacker
```
--out <path>              Output archive path (required)
--master-key 0x...        ACV1 master key (default: 0x8B6A4E5F)
--script-key 0x...        Game script key (default: 0x3793B711)
--text-encoding cp932     Encoding for .txt files (default: cp932, do not change)
--level 0-9               zlib compression level (default: 9)
```

---

## How It Works

### Archive format (`ACV1`)
```
[4 bytes]  Magic: "ACV1"
[4 bytes]  Entry count XOR master_key
[N × 21 bytes]  Entry table (encrypted)
[...]      Packed payloads
```

Each entry (21 bytes):
```
[8]  Checksum (u64)
[1]  Flags XOR (checksum & 0xFF)
[4]  File offset XOR checksum32 XOR master_key
[4]  Packed size XOR checksum32
[4]  Unpacked size XOR checksum32
```

### Script decode (per entry)
```
blob → XOR(checksum32) → XOR(script_key) → zlib.decompress → raw script text (CP932)
```

Encode is the reverse: `zlib.compress → XOR(script_key) → XOR(checksum32)`.

---

## Important Notes

**Encoding** — Script files are CP932 (Microsoft Shift-JIS). The extractor saves `.txt` as UTF-8 for easy editing; the repacker re-encodes to CP932 automatically. Do not change `--text-encoding` unless you know what you're doing.

**Compressed size may differ** — After repacking, compressed blob sizes will be slightly different from the original (zlib is not deterministic across runs). This is normal; the game reads the decompressed content, not the compressed stream.

**Other archives** — Resource archives that use original filenames are not handled here. This tool is script-only.

---

## Known Keys (Waga Himegimi ni Eikan o)

| Key | Value |
|---|---|
| Master key | `0x8B6A4E5F` |
| Script key | `0x3793B711` |
