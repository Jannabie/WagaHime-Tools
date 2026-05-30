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
| `wh_script_parser.py` | Parse extracted scripts into JSON/CSV for translation |

---

## Workflow

### 1. Extract
```bash
python acv1_extractor.py script3.dat
```
Output folder: `script3_extracted/`

Each entry is saved as `<index>_<checksum>.txt` (UTF-8). A `manifest.json` is also written with full entry metadata.

---

### ⚠️ Important: Which files to translate

After extraction you will see files like:

```
script3_extracted/
├── 0000_ad1680ebaeb5758f.txt   ← DO NOT TOUCH
├── 0001_d3587ef8e0f46d43.txt   ← DO NOT TOUCH
├── 0002_76f6ad0f25e32254.txt   ← DO NOT TOUCH
├── 0003_557de59f61f89207.txt   ← DO NOT TOUCH
├── 0004_a7f158b0cfb5261b.txt   ← DO NOT TOUCH
├── 0005_8115fa5ee29c3c4f.txt   ← DO NOT TOUCH
├── 0006_2f15b334e3063475.txt   ← DO NOT TOUCH
├── 0007_1ccfe3193516609e.txt   ← ✅ START HERE — this is the first script file
├── 0008_xxxxxxxxxxxx.txt       ← ✅ translate this too
└── ...
```

**Only edit files from index `0007` and above.** Files `0000`–`0006` are internal engine metadata — editing them will corrupt the archive or crash the game.

> The number at the start of the filename (e.g. `0007`) is the **entry index**. The long hex string after it (e.g. `1ccfe3193516609e`) is a checksum that the repacker needs to identify the entry. **Never rename these files.** The full filename must stay exactly as it was when extracted.

---

### 2. Translate (using the parser)

Instead of editing raw `.txt` files directly, use `wh_script_parser.py` to extract only the dialogue into a clean CSV — much easier to work with in Google Sheets or Excel.

#### Export dialogue to CSV
```bash
python wh_script_parser.py export-csv 0007_1ccfe3193516609e.txt --out 0007_dialog.csv
```

The CSV will look like this:

| line_number | is_narration | speaker_internal | speaker_display | voice_id | original_text | translated_text |
|---|---|---|---|---|---|---|
| 45 | no | Foru | Foru | S035_B2_0036 | Okay, I feel better... | *(fill this in)* |
| 54 | no | Chimes | Chimes | S061_B2_0002 | I-It's okay, I'll do that. | *(fill this in)* |
| 119 | yes | | | | A tremendous roar... | *(fill this in)* |

Fill in the **`translated_text`** column. Leave it blank for lines you haven't translated yet — they will fall back to the original text when rebuilt.

#### Rebuild the script after translating
```bash
# Step 1 — parse the original file to JSON (only once per file)
python wh_script_parser.py parse 0007_1ccfe3193516609e.txt --out 0007.json

# Step 2 — rebuild the .txt with your translations applied
python wh_script_parser.py import 0007.json --csv 0007_dialog.csv --out 0007_1ccfe3193516609e.txt
```

> ⚠️ Notice the output filename in Step 2: `0007_1ccfe3193516609e.txt` — it must be the **exact same name** as the original extracted file. The repacker identifies each entry by this filename. If you rename it, the repacker will not find it and will fall back to the untranslated original.

#### Other useful commands
```bash
# Preview all dialogue in the terminal (no files written)
python wh_script_parser.py show-dialog 0007_1ccfe3193516609e.txt

# See a summary of how many lines, speakers, and command types are in a file
python wh_script_parser.py stats 0007_1ccfe3193516609e.txt

# Export full script structure to JSON (includes commands, labels, everything)
python wh_script_parser.py parse 0007_1ccfe3193516609e.txt --out 0007.json

# Process all .txt files in a folder at once
python wh_script_parser.py parse-all script3_extracted/ --out-dir json_out/
```

---

### 3. Repack
```bash
python acv1_repacker.py script3.dat script3_extracted --out script3_patched.dat
```

Done. Replace the original `.dat` with `script3_patched.dat`.

> The repacker reads the translated `.txt` files from the extracted folder. Make sure your rebuilt `.txt` files are placed back in that same folder with their **original filenames unchanged** before running this step.

---

### Proof of concept

Translation working in-game:

![Translation working in-game](https://i.imgur.com/3c1dV98.jpeg)

---

## File editing rules (summary for translators)

| Rule | Detail |
|---|---|
| ✅ Edit `.txt` files from index `0007` onwards | These contain the actual script |
| ✅ Keep the full filename exactly as-is | e.g. `0007_1ccfe3193516609e.txt` — never rename |
| 🚫 Do not edit files `0000`–`0006` | Engine metadata, not dialogue |
| 🚫 Do not edit `.bin` files | Raw bytes, used as fallback only |
| 🚫 Do not change file encoding manually | The repacker handles UTF-8 → CP932 automatically |

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

### Parser
```
parse <file> [--out <json>]                   Parse one .txt → JSON
parse-all <folder> [--out-dir <folder>]       Parse all .txt files in a folder
export-csv <file> [--out <csv>]               Export dialogue lines to CSV
import <json> [--csv <csv>] [--out <txt>]     Rebuild .txt from JSON + optional CSV
show-dialog <file>                            Pretty-print dialogue to terminal
stats <file>                                  Show node/speaker statistics
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

**Script entries start at index 0007** — After extraction, entries `0000` through `0006` are internal engine data and must not be edited. Only edit files from `0007_....txt` onwards. These are the entries that contain actual in-game dialogue and script text.

**Encoding** — Script files are CP932 (Microsoft Shift-JIS). The extractor saves `.txt` as UTF-8 for easy editing; the repacker re-encodes to CP932 automatically. Do not change `--text-encoding` unless you know what you're doing.

**Compressed size may differ** — After repacking, compressed blob sizes will be slightly different from the original (zlib is not deterministic across runs). This is normal; the game reads the decompressed content, not the compressed stream.

**Other archives** — Resource archives that use original filenames are not handled here. This tool is script-only.

---

## Known Keys (Waga Himegimi ni Eikan o)

| Key | Value |
|---|---|
| Master key | `0x8B6A4E5F` |
| Script key | `0x3793B711` |
