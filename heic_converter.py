"""
HEIC Converter
==============

A simple GUI tool to batch-convert HEIC/HEIF images to JPEG, PNG, WebP, or PDF.

PDF output has two modes:
  * One PDF per HEIC file (default) -- mirrors JPEG/PNG/WebP.
  * Combine all HEICs into a single multi-page PDF.

Per-file conversions are dispatched to a process pool so multiple CPU cores are
used. The Tk main thread stays responsive by polling a queue.Queue that the
worker dispatcher feeds with progress / log events.

Requirements:
    pip install pillow pillow-heif

Usage:
    python heic_converter.py
"""

import os
import queue
import multiprocessing
import threading
import tkinter as tk
from concurrent.futures import ProcessPoolExecutor, as_completed
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
APP_VERSION = "1.4.0"
APP_AUTHOR = "Liang Xie / Claude Code"
APP_URL = "https://github.com/xieliaing/heic_converter"


# HEIC/HEIF file signatures (ftyp box brands), found at bytes 4..12 of the file.
HEIC_BRANDS = {
    b"heic", b"heix", b"heim", b"heis",
    b"hevc", b"hevx", b"hevm", b"hevs",
    b"mif1", b"msf1", b"heif",
}

# Below this many files, the process-pool overhead (spawning Python interpreters,
# pickling, re-importing pillow_heif) is larger than the parallel speedup, so we
# just convert serially in a thread.
PARALLEL_THRESHOLD = 4

DEFAULT_WORKERS = max(1, (os.cpu_count() or 2) - 1)
MAX_WORKERS_UI = max(1, (os.cpu_count() or 2))  # cap spinbox at total cores


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


# ---------------------------------------------------------------------------
# Worker-side functions (must be top-level / picklable for ProcessPoolExecutor)
# ---------------------------------------------------------------------------

def _worker_init():
    """Run once per worker process. Re-register the HEIF opener since each
    child interpreter imports Pillow/pillow_heif fresh."""
    try:
        import pillow_heif as _phf
        _phf.register_heif_opener()
    except ImportError:
        pass


def _convert_one(task: dict) -> dict:
    """Convert a single HEIC file. Runs in a worker process.

    `task` is a plain dict so it pickles cleanly. Returns a result dict with
    the original input name plus status info.
    """
    src = task["src"]
    out_path = task["out_path"]
    fmt = task["fmt"]
    quality = task["quality"]
    name = os.path.basename(src)

    try:
        if not os.path.isfile(src):
            return {"src": src, "name": name, "status": "skipped",
                    "reason": "File not found"}

        if not is_heic_file(src):
            return {"src": src, "name": name, "status": "skipped",
                    "reason": "Not a valid HEIC/HEIF file"}

        with Image.open(src) as img:
            if fmt == "JPEG":
                if img.mode in ("RGBA", "P", "LA"):
                    img = img.convert("RGB")
                img.save(out_path, "JPEG", quality=quality)
            elif fmt == "PNG":
                img.save(out_path, "PNG")
            elif fmt == "WEBP":
                if img.mode == "P":
                    img = img.convert("RGBA")
                img.save(out_path, "WEBP", quality=quality)
            elif fmt == "PDF":
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(out_path, "PDF", resolution=100.0)
            else:
                return {"src": src, "name": name, "status": "error",
                        "reason": f"Unknown format: {fmt}"}

        return {"src": src, "name": name, "status": "ok",
                "out_name": os.path.basename(out_path)}
    except Exception as e:
        return {"src": src, "name": name, "status": "error", "reason": str(e)}


def _decode_to_rgb_bytes(path: str) -> dict:
    """Worker for combined-PDF mode: decode a HEIC and return raw RGB bytes
    plus dimensions. Returning a serialized PIL Image isn't ideal across
    processes, but raw bytes pickle fast and the parent can rebuild the Image."""
    name = os.path.basename(path)
    try:
        if not os.path.isfile(path):
            return {"src": path, "name": name, "status": "skipped",
                    "reason": "File not found"}
        if not is_heic_file(path):
            return {"src": path, "name": name, "status": "skipped",
                    "reason": "Not a valid HEIC/HEIF file"}
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            data = rgb.tobytes()
            size = rgb.size
        return {"src": path, "name": name, "status": "ok",
                "data": data, "size": size}
    except Exception as e:
        return {"src": path, "name": name, "status": "error", "reason": str(e)}


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class HEICConverterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("760x620")
        self.root.minsize(680, 540)

        self.input_files: list[str] = []
        self.output_dir = tk.StringVar()
        self.output_format = tk.StringVar(value="JPEG")
        self.quality = tk.IntVar(value=92)
        self.combine_pdf = tk.BooleanVar(value=False)
        self.worker_count = tk.IntVar(value=DEFAULT_WORKERS)

        # Cross-thread event queue: workers / dispatcher push tuples,
        # the Tk main loop polls and updates widgets.
        self._event_q: queue.Queue = queue.Queue()
        self._poll_id: str | None = None
        self._dispatch_thread: threading.Thread | None = None

        self._build_menu()
        self._build_ui()
        self._start_polling()

    # ---------- UI construction ----------

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
            "  \u2022 Multi-core parallel conversion\n"
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

        # --- Performance ---
        perf_frame = ttk.LabelFrame(self.root, text="Performance")
        perf_frame.pack(fill="x", **pad)
        perf_row = ttk.Frame(perf_frame)
        perf_row.pack(fill="x", padx=8, pady=8)
        ttk.Label(perf_row, text=f"Worker processes (1–{MAX_WORKERS_UI}):").pack(side="left")
        ttk.Spinbox(perf_row, from_=1, to=MAX_WORKERS_UI,
                    textvariable=self.worker_count, width=5).pack(side="left", padx=6)
        ttk.Label(
            perf_row,
            text=f"(default = {DEFAULT_WORKERS}; "
                 f"serial fallback below {PARALLEL_THRESHOLD} files)",
            foreground="gray",
        ).pack(side="left", padx=6)

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

    # ---------- Tk-side helpers ----------

    def log(self, message: str):
        """Append to log. SAFE to call only from the Tk main thread.
        Workers / background threads must push ('log', message) to the queue."""
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

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
        if fmt in ("JPEG", "WEBP"):
            self.quality_spin.configure(state="normal")
            self.quality_label.configure(foreground="")
        else:
            self.quality_spin.configure(state="disabled")
            self.quality_label.configure(foreground="gray")
        if fmt == "PDF":
            self.combine_pdf_check.configure(state="normal")
        else:
            self.combine_pdf_check.configure(state="disabled")

    # ---------- Cross-thread event pump ----------

    def _start_polling(self):
        """Poll the event queue ~30x/sec and apply updates on the Tk thread."""
        try:
            while True:
                evt = self._event_q.get_nowait()
                kind = evt[0]
                if kind == "log":
                    self.log(evt[1])
                elif kind == "progress":
                    # ('progress', step) -- advance bar by `step`
                    self.progress.step(evt[1])
                elif kind == "done":
                    self.convert_btn.configure(state="normal")
                elif kind == "error_box":
                    messagebox.showerror(evt[1], evt[2])
                elif kind == "warn_box":
                    messagebox.showwarning(evt[1], evt[2])
        except queue.Empty:
            pass
        self._poll_id = self.root.after(33, self._start_polling)

    # ---------- Job planning ----------

    def _resolve_unique_path(self, base_dir: Path, stem: str, ext: str,
                             reserved: set) -> Path:
        """Find a path that doesn't collide with the filesystem OR with names
        already reserved earlier in this batch. `reserved` is mutated."""
        candidate = base_dir / f"{stem}{ext}"
        counter = 1
        while candidate.exists() or str(candidate) in reserved:
            candidate = base_dir / f"{stem}_{counter}{ext}"
            counter += 1
        reserved.add(str(candidate))
        return candidate

    # ---------- Conversion entry point ----------

    def start_conversion(self):
        if not HEIF_AVAILABLE:
            messagebox.showerror("Missing dependency",
                                 "pillow-heif is not installed.\n\nRun: pip install pillow-heif")
            return
        if not self.input_files:
            messagebox.showwarning("No input", "Please add at least one HEIC file.")
            return

        # Validate worker count (Spinbox can hold garbage if user types it)
        try:
            workers = int(self.worker_count.get())
        except (tk.TclError, ValueError):
            workers = DEFAULT_WORKERS
        workers = max(1, min(workers, MAX_WORKERS_UI))
        self.worker_count.set(workers)

        fmt = self.output_format.get()
        combine = (fmt == "PDF" and self.combine_pdf.get())

        if combine:
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
            self._dispatch_thread = threading.Thread(
                target=self._dispatch_combined_pdf,
                args=(list(self.input_files), Path(out_file), workers),
                daemon=True,
            )
            self._dispatch_thread.start()
            return

        if not self.output_dir.get():
            messagebox.showwarning("No output folder", "Please choose an output folder.")
            return
        out_dir = Path(self.output_dir.get())
        if not out_dir.is_dir():
            messagebox.showerror("Invalid folder", f"Output folder does not exist:\n{out_dir}")
            return

        # Plan output paths up front so workers don't race on filename collisions.
        ext_map = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "PDF": ".pdf"}
        ext = ext_map[fmt]
        quality = int(self.quality.get())
        reserved: set = set()
        tasks: list[dict] = []
        for src in list(self.input_files):
            stem = Path(os.path.basename(src)).stem
            out_path = self._resolve_unique_path(out_dir, stem, ext, reserved)
            tasks.append({
                "src": src,
                "out_path": str(out_path),
                "fmt": fmt,
                "quality": quality,
            })

        self.convert_btn.configure(state="disabled")
        self.progress.configure(value=0, maximum=len(tasks))
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_per_file,
            args=(tasks, fmt, workers),
            daemon=True,
        )
        self._dispatch_thread.start()

    # ---------- Dispatchers (run on a background thread) ----------

    def _emit_log(self, msg: str):
        self._event_q.put(("log", msg))

    def _emit_progress(self, step: int = 1):
        self._event_q.put(("progress", step))

    def _emit_done(self):
        self._event_q.put(("done",))

    def _dispatch_per_file(self, tasks: list, fmt: str, workers: int):
        n = len(tasks)
        success = skipped = failed = 0

        if n < PARALLEL_THRESHOLD or workers == 1:
            self._emit_log(f"--- Converting {n} file(s) to {fmt} (serial) ---")
            for task in tasks:
                # Re-register HEIF opener is unnecessary in-process.
                result = _convert_one(task)
                self._handle_result(result)
                if result["status"] == "ok":
                    success += 1
                elif result["status"] == "skipped":
                    skipped += 1
                else:
                    failed += 1
                self._emit_progress(1)
        else:
            self._emit_log(f"--- Converting {n} file(s) to {fmt} "
                           f"using {workers} worker process(es) ---")
            # `mp_context='spawn'` is the safe default on Windows and is also
            # required for PyInstaller --onefile --windowed builds.
            ctx = multiprocessing.get_context("spawn")
            try:
                with ProcessPoolExecutor(
                    max_workers=workers,
                    mp_context=ctx,
                    initializer=_worker_init,
                ) as executor:
                    future_to_task = {
                        executor.submit(_convert_one, t): t for t in tasks
                    }
                    for fut in as_completed(future_to_task):
                        try:
                            result = fut.result()
                        except Exception as e:
                            t = future_to_task[fut]
                            result = {
                                "src": t["src"],
                                "name": os.path.basename(t["src"]),
                                "status": "error",
                                "reason": f"Worker crashed: {e}",
                            }
                        self._handle_result(result)
                        if result["status"] == "ok":
                            success += 1
                        elif result["status"] == "skipped":
                            skipped += 1
                        else:
                            failed += 1
                        self._emit_progress(1)
            except Exception as e:
                self._emit_log(f"FATAL: process pool failed: {e}")

        self._emit_log(
            f"--- Done. Success: {success}, Skipped: {skipped}, Failed: {failed} ---"
        )
        self._emit_done()

    def _handle_result(self, result: dict):
        status = result["status"]
        name = result["name"]
        if status == "ok":
            self._emit_log(f"OK: {name}  ->  {result['out_name']}")
        elif status == "skipped":
            self._emit_log(f"WARNING: {result['reason']}, skipping: {name}")
        else:
            self._emit_log(f"ERROR converting {name}: {result.get('reason','unknown')}")

    def _dispatch_combined_pdf(self, sources: list, out_file: Path, workers: int):
        """Decode pages in parallel, then write the combined PDF serially."""
        n = len(sources)
        success = skipped = failed = 0
        # results indexed by original position so PDF page order matches input order
        decoded: list = [None] * n

        def _store(idx: int, result: dict):
            nonlocal success, skipped, failed
            if result["status"] == "ok":
                decoded[idx] = result
                success += 1
                self._emit_log(f"OK: loaded {result['name']}")
            elif result["status"] == "skipped":
                skipped += 1
                self._emit_log(f"WARNING: {result['reason']}, skipping: {result['name']}")
            else:
                failed += 1
                self._emit_log(f"ERROR loading {result['name']}: {result.get('reason','unknown')}")
            self._emit_progress(1)

        if n < PARALLEL_THRESHOLD or workers == 1:
            self._emit_log(f"--- Combining {n} file(s) into one PDF (serial decode) ---")
            for idx, path in enumerate(sources):
                _store(idx, _decode_to_rgb_bytes(path))
        else:
            self._emit_log(
                f"--- Combining {n} file(s) into one PDF "
                f"(decoding with {workers} worker process(es)) ---"
            )
            ctx = multiprocessing.get_context("spawn")
            try:
                with ProcessPoolExecutor(
                    max_workers=workers,
                    mp_context=ctx,
                    initializer=_worker_init,
                ) as executor:
                    future_to_idx = {
                        executor.submit(_decode_to_rgb_bytes, p): i
                        for i, p in enumerate(sources)
                    }
                    for fut in as_completed(future_to_idx):
                        i = future_to_idx[fut]
                        try:
                            result = fut.result()
                        except Exception as e:
                            result = {
                                "src": sources[i],
                                "name": os.path.basename(sources[i]),
                                "status": "error",
                                "reason": f"Worker crashed: {e}",
                            }
                        _store(i, result)
            except Exception as e:
                self._emit_log(f"FATAL: process pool failed: {e}")
                self._emit_done()
                return

        # Reassemble PIL Images from raw bytes, in original order.
        pages: list[Image.Image] = []
        try:
            for r in decoded:
                if r is None:
                    continue
                pages.append(Image.frombytes("RGB", r["size"], r["data"]))

            if not pages:
                self._emit_log("--- No valid HEIC files to combine. PDF not written. ---")
                self._event_q.put(("warn_box", "Nothing to write",
                                   "No valid HEIC files were found, so no PDF was created."))
                return

            first, rest = pages[0], pages[1:]
            first.save(
                out_file,
                "PDF",
                save_all=True,
                append_images=rest,
                resolution=100.0,
            )
            self._emit_log(
                f"--- Done. Combined {success} page(s) into {out_file.name}. "
                f"Skipped: {skipped}, Failed: {failed} ---"
            )
        except Exception as e:
            self._emit_log(f"ERROR writing combined PDF: {e}")
            self._event_q.put(("error_box", "PDF write failed", f"Could not write PDF:\n{e}"))
        finally:
            for p in pages:
                try:
                    p.close()
                except Exception:
                    pass
            self._emit_done()


def main():
    # CRITICAL for PyInstaller --onefile --windowed on Windows: without this,
    # every spawned worker would re-launch the whole Tk app, fork-bombing the
    # user's machine. Harmless when running from source.
    multiprocessing.freeze_support()

    root = tk.Tk()
    HEICConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
