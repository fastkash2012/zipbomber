# 💣 Zip Bomb Builder

A GUI tool for designing, estimating, and building **zip bombs** — with full control over size, recursion, compression, and payload content. Python standard library only, zero dependencies.

![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-none-success)
![GUI](https://img.shields.io/badge/GUI-tkinter-orange)
![License](https://img.shields.io/badge/license-MIT-green)

---

## ✨ Features

| Feature | Description |
|---|---|
| 🧱 **Two bomb types** | **Flat** (single zip, one or many payload files) or **Recursive** (nested zips, like the classic `42.zip`) |
| 📏 **Flexible sizing** | Set a *target unzip size* or the *base payload size* — the other is derived automatically |
| 🌀 **Recursion control** | Choose the number of layers and the fanout (copies packed per layer) |
| 📄 **Payload content** | Zeros (best compression), random bytes (worst case), or any custom repeating text |
| 🗜️ **Compression methods** | Deflate, Bzip2, LZMA, or Stored — with compression level where applicable |
| 📈 **Live estimates** | Sample-compresses your content to predict bomb size and expansion ratio *before* building |
| 📜 **Standalone script export** | Generates a zero-dependency `.py` script that rebuilds the exact same bomb |
| 📊 **Event log & progress bar** | Threaded building so the UI never freezes |
| 📦 **ZIP64 support** | Entries larger than 4 GB work with modern extractors |

---

## 🚀 Getting Started

### Requirements

- Python **3.8+**
- No third-party packages needed — only the standard library (`tkinter` is included with the official Python installers)

### Run it

```bash
git clone https://github.com/yourusername/zip-bomb-builder.git
cd zip-bomb-builder
python zip_bomb_builder_v1.py.py
```

---

## 🖥️ Usage

1. **Choose an output file** (e.g. `bomb.zip`)
2. **Pick the bomb type and size:**
   - *Flat* — one zip that expands to the target size
   - *Recursive* — nested zips; total extraction = `base_size × fanout^layers`
3. **Select payload content** — zeros compress ~1000:1 with Deflate, random data barely compresses at all
4. **Pick a compression method and level**
5. **Estimate** to preview the expected bomb size and expansion ratio
6. **BUILD BOMB** 🎇

### Example configurations

| Goal | Settings |
|---|---|
| Classic flat bomb | Flat · 1 GB target · Zeros · Deflate level 9 → ~1 MB file |
| `42.zip`-style recursive bomb | Recursive · 4 layers · fanout 16 · base payload ~10 MB |
| Worst-case stress test | Random bytes · Stored (no compression) |
| Text bomb | Custom repeating text, e.g. *"All work and no play…"* |

---

## 🧠 How it works

### Flat bombs

A single zip containing highly compressible data (e.g. megabytes of zeros). With Deflate level 9, ~1 GB of zeros compresses to roughly **1 MB** (~1000:1 ratio).

### Recursive bombs

Each layer packs **N copies** of the previous layer's zip file inside a new zip. Because already-compressed data re-compresses slightly, each layer multiplies the total extraction size by roughly the fanout:

```
total_extraction ≈ base_payload × fanout^layers
```

with only a small size increase per layer on disk. Recursive bombs must be **extracted repeatedly** (unzip → unzip the inner `.zip` files → …) to reach their full size — that's what makes them bombs.

---

## 📜 Standalone build scripts

Click **Save build script (.py)** to export a self-contained script with your configuration baked in:

```bash
python make_bomb.py [optional_output_path]
```

The generated script uses only the standard library and rebuilds the exact same bomb — perfect for automation, sharing, or documentation.

---

## ⚠️ Warning / Disclaimer

> Zip bombs can consume all available disk space, memory, or CPU on the machine that extracts them (including *your own*). Always open them in an isolated VM or in tools that limit extraction size.

- **Only use zip bombs on systems you own or have permission to test.**
- Distributing zip bombs with malicious intent may violate computer misuse laws in your jurisdiction.
- Some antivirus products may flag generated files.

This project is intended for **education, stress testing, and research** into compression and archive handling.

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---|---|
| `tkinter` not found | Install Python from [python.org](https://python.org) (includes tkinter) or `sudo apt install python3-tk` on Debian/Ubuntu |
| Bomb barely compresses | Use "Zeros" content, Deflate/Bzip2/LZMA (not "Stored") |
| Extraction fails on old tools | Some extractors don't support ZIP64 or Bzip2/LZMA — use Deflate |
| 4 GB+ total size, flat mode | Fine — ZIP64 is applied automatically by modern `zipfile` |

---

## 📄 License

MIT — do whatever you want, but you break it, you buy it. 😉

---

**⭐ If this project blew up your disk (in a good way), give it a star!**
