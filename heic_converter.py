"""
HEIC to JPEG/PNG Converter
==========================

A simple GUI tool to batch-convert HEIC/HEIF images to JPEG or PNG.

Requirements:
    pip install pillow pillow-heif

Usage:
    python heic_converter.py
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from PIL import Image

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except ImportError:
    HEIF_AVAILABLE = False


# HEIC/HEIF file signatures (ftyp box brands), found at bytes 4..12 of the file.
HEIC_BRANDS = {
    b"heic", b"heix", b"heim", b"heis",
    b"hevc", b"hevx", b"hevm", b"hevs",
    b"mif1", b"msf1", b"heif",
}


def is_heic_file(path: str) -> bool:
    """Verify a file is genuinely HEIC/HEIF by inspecting its ftyp box."""
    try:
        with open(path, "rb") as f:
            header = f.read(32)
        if len(header) < 12 or header[4:8] != b"ftyp":
            return False
        if header[8:12] in HEIC_BRANDS:
            return True
        # Check compatible brands listed after the major brand
        compatible = header[16:32]
        for i in range(0, len(compatible) - 3, 4):
            if compatible[i:i + 4] in HEIC_BRANDS:
                return True
        return False
    except OSError:
        return False


class HEICConverterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("HEIC Converter")
        self.root.geometry("720x580")
        self.root.minsize(640, 520)

        self.input_files: list[str] = []
        self.output_dir = tk.StringVar()
        self.output_format = tk.StringVar(value="JPEG")
        self.jpeg_quality = tk.IntVar(value=92)

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # --- Input files ---
        input_frame = ttk.LabelFrame(self.root, text="Input HEIC Images")
        input_frame.pack(fill="both", expand=True, **pad)

        btn_row = ttk.Frame(input_frame)
        btn_row.pack(fill="x", padx=8, pady=6)
        ttk.Button(btn_row, text="Add Files...", command=self.add_files).pack(side="left")
        ttk.Button(btn_row, text="Remove Selected", command=self.remove_selected).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Clear All", command=self.clear_files).pack(side="left")

        list_frame = ttk.Frame(input_frame)
        list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.file_listbox = tk.Listbox(list_frame, selectmode="extended")
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_listbox.yview)
        self.file_listbox.configure(yscrollcommand=sb.set)
        self.file_listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # --- Output ---
        output_frame = ttk.LabelFrame(self.root, text="Output")
        output_frame.pack(fill="x", **pad)

        dir_row = ttk.Frame(output_frame)
        dir_row.pack(fill="x", padx=8, pady=6)
        ttk.Label(dir_row, text="Folder:").pack(side="left")
        ttk.Entry(dir_row, textvariable=self.output_dir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(dir_row, text="Browse...", command=self.choose_output_dir).pack(side="left")

        opt_row = ttk.Frame(output_frame)
        opt_row.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(opt_row, text="Format:").pack(side="left")
        ttk.Radiobutton(opt_row, text="JPEG", variable=self.output_format, value="JPEG").pack(side="left", padx=4)
        ttk.Radiobutton(opt_row, text="PNG", variable=self.output_format, value="PNG").pack(side="left", padx=4)
        ttk.Label(opt_row, text="   JPEG Quality:").pack(side="left")
        ttk.Spinbox(opt_row, from_=1, to=100, textvariable=self.jpeg_quality, width=5).pack(side="left", padx=4)

        # --- Action ---
        action_frame = ttk.Frame(self.root)
        action_frame.pack(fill="x", **pad)
        self.convert_btn = ttk.Button(action_frame, text="Convert", command=self.start_conversion)
        self.convert_btn.pack(side="left")
        self.progress = ttk.Progressbar(action_frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=8)

        # --- Log ---
        log_frame = ttk.LabelFrame(self.root, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, height=8, wrap="word", state="disabled")
        log_sb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        self.log_text.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        log_sb.pack(side="right", fill="y", pady=8, padx=(0, 8))

        if not HEIF_AVAILABLE:
            self.log("ERROR: pillow-heif is not installed. Run: pip install pillow-heif")

    # ---------- helpers ----------
    def log(self, message: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.root.update_idletasks()

    def add_files(self):
        files = filedialog.askopenfilenames(
            title="Select HEIC images",
            filetypes=[("HEIC images", "*.heic *.heif *.HEIC *.HEIF"), ("All files", "*.*")],
        )
        for f in files:
            if f not in self.input_files:
                self.input_files.append(f)
                self.file_listbox.insert("end", f)

    def remove_selected(self):
        for idx in reversed(self.file_listbox.curselection()):
            del self.input_files[idx]
            self.file_listbox.delete(idx)

    def clear_files(self):
        self.input_files.clear()
        self.file_listbox.delete(0, "end")

    def choose_output_dir(self):
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self.output_dir.set(d)

    # ---------- conversion ----------
    def start_conversion(self):
        if not HEIF_AVAILABLE:
            messagebox.showerror("Missing dependency",
                                 "pillow-heif is not installed.\n\nRun: pip install pillow-heif")
            return
        if not self.input_files:
            messagebox.showwarning("No input", "Please add at least one HEIC file.")
            return
        if not self.output_dir.get():
            messagebox.showwarning("No output folder", "Please choose an output folder.")
            return
        out_dir = Path(self.output_dir.get())
        if not out_dir.is_dir():
            messagebox.showerror("Invalid folder", f"Output folder does not exist:\n{out_dir}")
            return

        self.convert_btn.configure(state="disabled")
        self.progress.configure(value=0, maximum=len(self.input_files))
        threading.Thread(target=self._convert_worker, args=(out_dir,), daemon=True).start()

    def _convert_worker(self, out_dir: Path):
        fmt = self.output_format.get()
        ext = ".jpg" if fmt == "JPEG" else ".png"
        quality = self.jpeg_quality.get()

        success = skipped = failed = 0
        self.log(f"--- Converting {len(self.input_files)} file(s) to {fmt} ---")

        for path in list(self.input_files):
            name = os.path.basename(path)
            try:
                if not os.path.isfile(path):
                    self.log(f"WARNING: File not found, skipping: {name}")
                    skipped += 1
                    continue

                if not is_heic_file(path):
                    self.log(f"WARNING: Not a valid HEIC/HEIF file, skipping: {name}")
                    skipped += 1
                    continue

                with Image.open(path) as img:
                    out_path = out_dir / (Path(name).stem + ext)
                    counter = 1
                    while out_path.exists():
                        out_path = out_dir / f"{Path(name).stem}_{counter}{ext}"
                        counter += 1

                    if fmt == "JPEG":
                        if img.mode in ("RGBA", "P", "LA"):
                            img = img.convert("RGB")
                        img.save(out_path, "JPEG", quality=quality)
                    else:
                        img.save(out_path, "PNG")

                self.log(f"OK: {name}  ->  {out_path.name}")
                success += 1
            except Exception as e:
                self.log(f"ERROR converting {name}: {e}")
                failed += 1
            finally:
                self.progress.step(1)

        self.log(f"--- Done. Success: {success}, Skipped: {skipped}, Failed: {failed} ---")
        self.convert_btn.configure(state="normal")


def main():
    root = tk.Tk()
    HEICConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()