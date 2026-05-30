# ACV1 Script Tools

Tools untuk extract, edit, dan repack script archive dari **Waga Himegimi ni Eikan o** (mungkin juga works buat title Forlos/vn_re-engine lainnya).

**Requirements:** Python 3.10+, no external deps.

---

## Files

| File | Kegunaan |
|---|---|
| `acv1_extractor.py` | Decrypt & extract entries dari `.dat` archive |
| `acv1_repacker.py` | Repack script yang udah diedit balik ke `.dat` |
| `wh_script_parser.py` | Parse script ke JSON/CSV buat translasi |

---

## Workflow

### 1. Extract

```bash
python acv1_extractor.py script3.dat
```

Output folder: `script3_extracted/`  
Setiap entry disave sebagai `<index>_<checksum>.txt` (UTF-8), plus `manifest.json` dengan metadata lengkap.

---

### ⚠️ File mana yang boleh diedit

Setelah extract, struktur foldernya bakal kayak gini:

```
script3_extracted/
├── 0000_ad1680ebaeb5758f.txt   ← JANGAN DISENTUH
├── 0001_d3587ef8e0f46d43.txt   ← JANGAN DISENTUH
├── 0002_76f6ad0f25e32254.txt   ← JANGAN DISENTUH
├── 0003_557de59f61f89207.txt   ← JANGAN DISENTUH
├── 0004_a7f158b0cfb5261b.txt   ← JANGAN DISENTUH
├── 0005_8115fa5ee29c3c4f.txt   ← JANGAN DISENTUH
├── 0006_2f15b334e3063475.txt   ← JANGAN DISENTUH
├── 0007_1ccfe3193516609e.txt   ← ✅ mulai dari sini
├── 0008_xxxxxxxxxxxx.txt       ← ✅ dan seterusnya
└── ...
```

**Edit hanya dari index `0007` ke atas.** File `0000`–`0006` itu metadata internal engine — kalau diedit, archive bisa corrupt atau game crash.

> Angka di awal nama file (misal `0007`) adalah entry index. Hex panjang setelahnya (misal `1ccfe3193516609e`) adalah checksum yang dibutuhkan repacker. **Jangan rename file ini.** Nama file harus tetap persis seperti waktu di-extract.

---

### 2. Translate

Daripada edit file `.txt` langsung, mending pakai `wh_script_parser.py` buat export dialogue ke CSV — lebih gampang dikerjain di Google Sheets atau Excel.

#### Export ke CSV

```bash
python wh_script_parser.py export-csv 0007_1ccfe3193516609e.txt --out 0007_dialog.csv
```

Hasilnya bakal kayak gini:

| line_number | is_narration | speaker_internal | speaker_display | voice_id | original_text | translated_text |
|---|---|---|---|---|---|---|
| 45 | no | Foru | Foru | S035_B2_0036 | Okay, I feel better... | *(isi di sini)* |
| 54 | no | Chimes | Chimes | S061_B2_0002 | I-It's okay, I'll do that. | *(isi di sini)* |
| 119 | yes | | | | A tremendous roar... | *(isi di sini)* |

Isi kolom **`translated_text`**. Kalau ada baris yang belum ditranslate, kosongin aja — nanti otomatis fallback ke teks aslinya.

#### Rebuild setelah translate

```bash
# Step 1 — parse ke JSON dulu (cukup sekali per file)
python wh_script_parser.py parse 0007_1ccfe3193516609e.txt --out 0007.json

# Step 2 — rebuild .txt dengan translasi yang udah diisi
python wh_script_parser.py import 0007.json --csv 0007_dialog.csv --out 0007_1ccfe3193516609e.txt
```

> ⚠️ Perhatiin output filename di Step 2: harus persis `0007_1ccfe3193516609e.txt` — sama kayak nama file asli waktu di-extract. Kalau beda, repacker bakal skip dan pakai teks original.

#### Command lainnya

```bash
# Preview dialogue di terminal tanpa nulis file
python wh_script_parser.py show-dialog 0007_1ccfe3193516609e.txt

# Lihat ringkasan jumlah baris, speaker, dan command type
python wh_script_parser.py stats 0007_1ccfe3193516609e.txt

# Export full script structure ke JSON
python wh_script_parser.py parse 0007_1ccfe3193516609e.txt --out 0007.json

# Proses semua .txt sekaligus
python wh_script_parser.py parse-all script3_extracted/ --out-dir json_out/
```

---

### 3. Repack

```bash
python acv1_repacker.py script3.dat script3_extracted --out script3_patched.dat
```

Selesai. Ganti file `.dat` yang asli dengan `script3_patched.dat`.

> Pastiin file `.txt` hasil rebuild udah ada di folder extracted dengan nama yang bener sebelum jalanin ini.

---

## Proof of Concept

| Screenshot |
|:---:|
| ![Translation working in-game](https://i.imgur.com/3c1dV98.jpeg) |
| *Translasi jalan in-game* |

---

## Aturan singkat buat translator

| | Detail |
|---|---|
| ✅ Edit `.txt` dari index `0007` ke atas | Itu yang isinya script |
| ✅ Jangan rename file | Nama harus tetap, misal `0007_1ccfe3193516609e.txt` |
| 🚫 Jangan edit `0000`–`0006` | Metadata engine, bukan dialogue |
| 🚫 Jangan edit `.bin` | Raw bytes, fallback only |
| 🚫 Jangan ubah encoding manual | Repacker udah handle UTF-8 → CP932 otomatis |

---

## Options

### Extractor
```
--out <dir>           Output directory (default: <archive>_extracted)
--master-key 0x...    ACV1 master key (default: 0x8B6A4E5F)
--script-key 0x...    Game script key (default: 0x3793B711)
--no-raw              Skip .bin files
--no-text             Skip .txt files
```

### Repacker
```
--out <path>              Output archive path (required)
--master-key 0x...        ACV1 master key (default: 0x8B6A4E5F)
--script-key 0x...        Game script key (default: 0x3793B711)
--text-encoding cp932     Encoding .txt (default: cp932, jangan diubah)
--level 0-9               zlib compression level (default: 9)
```

### Parser
```
parse <file> [--out <json>]
parse-all <folder> [--out-dir <folder>]
export-csv <file> [--out <csv>]
import <json> [--csv <csv>] [--out <txt>]
show-dialog <file>
stats <file>
```

---

## Format Archive (ACV1)

```
[4 bytes]  Magic: "ACV1"
[4 bytes]  Entry count XOR master_key
[N × 21 bytes]  Entry table (encrypted)
[...]      Packed payloads
```

Setiap entry (21 bytes):
```
[8]  Checksum (u64)
[1]  Flags XOR (checksum & 0xFF)
[4]  File offset XOR checksum32 XOR master_key
[4]  Packed size XOR checksum32
[4]  Unpacked size XOR checksum32
```

Decode per entry: `blob → XOR(checksum32) → XOR(script_key) → zlib.decompress → CP932`  
Encode: kebalikannya.

---

## Notes

- **Compressed size beda** setelah repack itu normal — zlib output memang non-deterministic. Yang penting konten decompressed-nya bener.
- **Encoding**: Script asli CP932. Extractor nyimpen UTF-8 biar gampang diedit, repacker otomatis encode balik ke CP932.
- **Resource archive** dengan original filename tidak dihandle di sini. Tool ini script-only.

---

## Known Keys — Waga Himegimi ni Eikan o

| Key | Value |
|---|---|
| Master key | `0x8B6A4E5F` |
| Script key | `0x3793B711` |
