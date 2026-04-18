"""
HEIC Converter
==============

A simple GUI tool to batch-convert HEIC/HEIF images to JPEG, PNG, WebP, or PDF.

PDF output has two modes:
  * One PDF per HEIC file (default) -- mirrors JPEG/PNG/WebP.
  * Combine all HEICs into a single multi-page PDF.

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


APP_NAME = "HEIC Converter"
APP_VERSION = "1.3.0"
APP_AUTHOR = "Liang Xie / Claude Code"
APP_URL = "https://github.com/xieliaing/heic_converter"


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
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("720x580")
        self.root.minsize(640, 520)

        self.input_files: list[str] = []
        self.output_dir = tk.StringVar()
        self.output_format = tk.StringVar(value="JPEG")
        self.quality = tk.IntVar(value=92)
        self.combine_pdf = tk.BooleanVar(value=False)

        self._build_menu()
        self._build_ui()

    def _build_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Exit", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About...", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def show_about(self):
        about_text = (
            f"{APP_NAME}  v{APP_VERSION}\n\n"
            "A small utility to batch-convert HEIC/HEIF images to JPEG, PNG, WebP, or PDF.\n\n"
            "Features:\n"
            "  \u2022 Add multiple HEIC files from any folder\n"
            "  \u2022 Convert to JPEG, PNG, WebP, or PDF\n"
            "  \u2022 Adjustable quality for JPEG and WebP\n"
            "  \u2022 Combine multiple HEICs into a single multi-page PDF\n"
            "  \u2022 Real format validation by inspecting file headers\n"
            "  \u2022 Non-HEIC files are automatically skipped with a warning\n"
            "  \u2022 Automatic filename de-duplication\n\n"
            f"Author: {APP_AUTHOR}\n"
            f"Project: {APP_URL}\n\n"
            "Built with Python, Tkinter, Pillow, and pillow-heif.\n"
            "Licensed under the MIT License."
        )
        messagebox.showinfo(f"About {APP_NAME}", about_text)

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
        opt_row.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(opt_row, text="Format:").pack(side="left")
        ttk.Radiobutton(opt_row, text="JPEG", variable=self.output_format,
                        value="JPEG", command=self._on_format_change).pack(side="left", padx=4)
        ttk.Radiobutton(opt_row, text="PNG", variable=self.output_format,
                        value="PNG", command=self._on_format_change).pack(side="left", padx=4)
        ttk.Radiobutton(opt_row, text="WebP", variable=self.output_format,
                        value="WEBP", command=self._on_format_change).pack(side="left", padx=4)
        ttk.Radiobutton(opt_row, text="PDF", variable=self.output_format,
                        value="PDF", command=self._on_format_change).pack(side="left", padx=4)
        self.quality_label = ttk.Label(opt_row, text="   Quality:")
        self.quality_label.pack(side="left")
        self.quality_spin = ttk.Spinbox(opt_row, from_=1, to=100,
                                        textvariable=self.quality, width=5)
        self.quality_spin.pack(side="left", padx=4)

        pdf_row = ttk.Frame(output_frame)
        pdf_row.pack(fill="x", padx=8, pady=(0, 8))
        self.combine_pdf_check = ttk.Checkbutton(
            pdf_row,
            text="Combine all HEICs into a single multi-page PDF",
            variable=self.combine_pdf,
        )
        self.combine_pdf_check.pack(side="left")

        # Apply initial enabled/disabled state for quality + combine-PDF controls
        self._on_format_change()

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

    def _on_format_change(self):
        """Enable/disable quality spinbox and PDF-combine checkbox based on format."""
        fmt = self.output_format.get()
        # Quality applies to JPEG and WebP only.
        if fmt in ("JPEG", "WEBP"):
            self.quality_spin.configure(state="normal")
            self.quality_label.configure(foreground="")
        else:
            self.quality_spin.configure(state="disabled")
            self.quality_label.configure(foreground="gray")
        # Combine-PDF checkbox only applies when PDF is selected.
        if fmt == "PDF":
            self.combine_pdf_check.configure(state="normal")
        else:
            self.combine_pdf_check.configure(state="disabled")

    # ---------- conversion ----------
    def start_conversion(self):
        if not HEIF_AVAILABLE:
            messagebox.showerror("Missing dependency",
                                 "pillow-heif is not installed.\n\nRun: pip install pillow-heif")
            return
        if not self.input_files:
            messagebox.showwarning("No input", "Please add at least one HEIC file.")
            return

        fmt = self.output_format.get()
        combine = (fmt == "PDF" and self.combine_pdf.get())

        if combine:
            # Combined PDF mode: ask for a single output filename instead of a folder.
            out_file = filedialog.asksaveasfilename(
                title="Save combined PDF as",
                defaultextension=".pdf",
                filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
                initialfile="heic-combined.pdf",
            )
            if not out_file:
                return
            self.convert_btn.configure(state="disabled")
            self.progress.configure(value=0, maximum=len(self.input_files))
            threading.Thread(
                target=self._convert_combined_pdf_worker,
                args=(Path(out_file),),
                daemon=True,
            ).start()
            return

        # Standard per-file mode: need output folder.
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
        ext_map = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "PDF": ".pdf"}
        ext = ext_map[fmt]
        quality = self.quality.get()

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
                    elif fmt == "PNG":
                        img.save(out_path, "PNG")
                    elif fmt == "WEBP":
                        # Pillow's WebP encoder accepts RGB or RGBA.
                        if img.mode == "P":
                            img = img.convert("RGBA")
                        img.save(out_path, "WEBP", quality=quality)
                    elif fmt == "PDF":
                        # PDF output has no alpha channel; flatten to RGB.
                        if img.mode != "RGB":
                            img = img.convert("RGB")
                        img.save(out_path, "PDF", resolution=100.0)

                self.log(f"OK: {name}  ->  {out_path.name}")
                success += 1
            except Exception as e:
                self.log(f"ERROR converting {name}: {e}")
                failed += 1
            finally:
                self.progress.step(1)

        self.log(f"--- Done. Success: {success}, Skipped: {skipped}, Failed: {failed} ---")
        self.convert_btn.configure(state="normal")

    def _convert_combined_pdf_worker(self, out_file: Path):
        """Decode all valid HEICs and write them as pages of a single PDF."""
        success = skipped = failed = 0
        pages: list[Image.Image] = []
        self.log(f"--- Combining {len(self.input_files)} file(s) into one PDF ---")

        try:
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

                    # Open, decode pixels, and immediately copy to a standalone RGB Image
                    # so we can close the source file handle. Keeping many pillow-heif
                    # backed images open simultaneously is wasteful.
                    with Image.open(path) as img:
                        page = img.convert("RGB").copy()
                    pages.append(page)
                    success += 1
                    self.log(f"OK: loaded {name}")
                except Exception as e:
                    self.log(f"ERROR loading {name}: {e}")
                    failed += 1
                finally:
                    self.progress.step(1)

            if not pages:
                self.log("--- No valid HEIC files to combine. PDF not written. ---")
                messagebox.showwarning(
                    "Nothing to write",
                    "No valid HEIC files were found, so no PDF was created."
                )
                return

            # Avoid overwriting an existing file silently if the user picked an existing name;
            # tk's asksaveasfilename already confirms overwrites, so we just honor their choice.
            first, rest = pages[0], pages[1:]
            first.save(
                out_file,
                "PDF",
                save_all=True,
                append_images=rest,
                resolution=100.0,
            )
            self.log(f"--- Done. Combined {success} page(s) into {out_file.name}. "
                     f"Skipped: {skipped}, Failed: {failed} ---")
        except Exception as e:
            self.log(f"ERROR writing combined PDF: {e}")
            messagebox.showerror("PDF write failed", f"Could not write PDF:\n{e}")
        finally:
            # Release the in-memory image copies.
            for p in pages:
                try:
                    p.close()
                except Exception:
                    pass
            self.convert_btn.configure(state="normal")


def main():
    root = tk.Tk()
    HEICConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
