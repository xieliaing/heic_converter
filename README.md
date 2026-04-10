# HEIC Converter

A small, no-frills Windows utility to batch-convert HEIC/HEIF images to **JPEG** or **PNG**.

Built with Python + Tkinter. Pick one or many HEIC files from anywhere on your computer, choose an output folder, and click **Convert**. Files that aren't actually HEIC (for example, a JPEG someone renamed to `.heic`) are automatically detected and skipped with a warning in the log.

---

## Features

- Add multiple HEIC/HEIF files from different locations in a single batch
- Choose any output folder
- Convert to **JPEG** (with adjustable quality) or **PNG**
- Real format validation by inspecting the file header — not just the extension
- Non-HEIC files are skipped, not failed, with a clear warning in the log
- Automatic filename de-duplication (won't overwrite existing files)
- Background conversion thread keeps the UI responsive
- Progress bar and live log

---

## Download (Windows)

Grab the latest prebuilt executable from the [**Releases page**]([../../releases/latest](https://github.com/xieliaing/heic_converter/releases/tag/v0.1.1)).

Download `HEICConverter.exe`, double-click to run. No installation required.

> **Note on SmartScreen:** The first time you run the exe, Windows may show a "Windows protected your PC" warning because the binary is not code-signed. Click **More info → Run anyway**. This is normal for free utilities from independent developers.

---

## Run from source

If you'd rather run the Python script directly:

### Requirements

- Python 3.9 or newer
- `pillow` and `pillow-heif`

### Setup

```bash
git clone https://github.com/YOUR_USERNAME/heic-converter.git
cd heic-converter
pip install -r requirements.txt
python heic_converter.py
```

---

## Build your own executable

If you want to rebuild the `.exe` yourself:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --collect-all pillow_heif --name HEICConverter heic_converter.py
```

The finished binary will be in `dist\HEICConverter.exe`.

The `--collect-all pillow_heif` flag is important — it ensures the native HEIF decoder libraries are bundled correctly.

---

## How to use

1. Click **Add Files...** and pick one or more HEIC images. You can repeat this to add files from different folders.
2. Click **Browse...** next to the Output folder and choose where the converted images should go.
3. Pick the output format: **JPEG** or **PNG**. For JPEG you can also tune the quality (1–100, default 92).
4. Click **Convert**.
5. Watch the log for results. Each file reports one of:
   - `OK: photo.heic -> photo.jpg` — converted successfully
   - `WARNING: Not a valid HEIC/HEIF file, skipping: photo.heic` — the file isn't actually HEIC and was skipped
   - `ERROR converting photo.heic: ...` — something went wrong during conversion

---

## License

MIT — see [LICENSE](LICENSE) for details.
