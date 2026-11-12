"""Interfaz gráfica de Papiro (tkinter, sin dependencias extra)."""

from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path

from . import __version__
from .converter import Converter, iter_files
from .formats import SUPPORTED

CJK_FONTS = [
    "Noto Sans CJK JP", "Noto Sans JP", "Yu Gothic UI", "Yu Gothic", "Meiryo", "Hiragino Sans",
    "Hiragino Kaku Gothic ProN", "Source Han Sans JP", "IPAexGothic", "IPAGothic", "TakaoGothic",
    "VL Gothic", "MS Gothic",
]

MODES = {"Automático": "auto", "Calidad (más lento)": "calidad", "Rápido": "rapido"}
OCRS = {"Automático": "auto", "Siempre": "siempre", "Nunca": "nunca"}
LANGS = {"Automático": "auto", "Japonés": "ja", "Español": "es", "Inglés": "en"}

PENDING, WORKING, DONE, WARN, FAIL = "En espera", "Convirtiendo…", "Hecho", "Hecho, con avisos", "Error"


class App:
    def __init__(self, root, paths: list[str] | None = None) -> None:
        import tkinter as tk
        from tkinter import font as tkfont
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.root = root
        self.items: dict[str, dict] = {}  # id de fila -> {path, md, warnings, status}
        self.current: str | None = None
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()

        root.title(f"Papiro {__version__} — documentos a Markdown")
        root.geometry("1180x720")
        root.minsize(900, 560)
        try:
            ttk.Style().theme_use("clam")
        except Exception:
            pass

        families = set(tkfont.families(root))
        self.cjk_font = next((f for f in CJK_FONTS if f in families), None)
        base = tkfont.nametofont("TkDefaultFont")
        self.preview_font = tkfont.Font(family=self.cjk_font or base.actual("family"), size=12)
        if self.cjk_font:
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
                try:
                    tkfont.nametofont(name).configure(family=self.cjk_font)
                except Exception:
                    pass

        self._build()
        self._refresh_ocr_status()
        try:  # macOS: archivos soltados sobre el icono de la aplicación
            root.createcommand("::tk::mac::OpenDocument", lambda *files: self.add_paths(files))
        except Exception:
            pass
        try:
            from pathlib import Path as _P

            icon = _P(__file__).resolve().parent / "assets" / "papiro.png"
            if icon.exists():
                self._icon = tk.PhotoImage(file=str(icon))
                root.iconphoto(True, self._icon)
        except Exception:
            pass
        if paths:
            self.add_paths(paths)
        self.root.after(80, self._poll)

    # -- Construcción -------------------------------------------------------

    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        panes = ttk.PanedWindow(root, orient="horizontal")
        panes.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 4))

        # Izquierda: archivos y opciones
        left = ttk.Frame(panes)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        bar = ttk.Frame(left)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(bar, text="Añadir archivos…", command=self.ask_files).pack(side="left")
        ttk.Button(bar, text="Añadir carpeta…", command=self.ask_folder).pack(side="left", padx=6)
        ttk.Button(bar, text="Quitar", command=self.remove_selected).pack(side="left")
        ttk.Button(bar, text="Vaciar", command=self.clear).pack(side="left", padx=6)

        self.tree = ttk.Treeview(left, columns=("estado",), show="tree headings", selectmode="browse", height=12)
        self.tree.heading("#0", text="Archivo")
        self.tree.heading("estado", text="Estado")
        self.tree.column("#0", width=250, stretch=True)
        self.tree.column("estado", width=130, stretch=False)
        self.tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(left, command=self.tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.tag_configure("fail", foreground="#b3261e")
        self.tree.tag_configure("warn", foreground="#9a6700")
        self.hint = ttk.Label(left, text="Añade archivos o carpetas, o arrástralos aquí.", foreground="#666")
        self.hint.grid(row=2, column=0, sticky="w", pady=(4, 0))

        opts = ttk.LabelFrame(left, text="Opciones", padding=8)
        opts.grid(row=3, column=0, columnspan=2, sticky="ew", pady=8)
        opts.columnconfigure(1, weight=1)
        self.mode = tk.StringVar(value="Automático")
        self.ocr = tk.StringVar(value="Automático")
        self.lang = tk.StringVar(value="Automático")
        self.furigana = tk.BooleanVar(value=True)
        rows = [("Modo:", self.mode, MODES), ("OCR:", self.ocr, OCRS), ("Idioma del OCR:", self.lang, LANGS)]
        for i, (label, var, values) in enumerate(rows):
            ttk.Label(opts, text=label).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Combobox(opts, textvariable=var, values=list(values), state="readonly", width=22).grid(
                row=i, column=1, sticky="ew", padx=(8, 0), pady=2)
        ttk.Checkbutton(opts, text="Mantener el furigana: 漢字(かんじ)", variable=self.furigana).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        run = ttk.Frame(left)
        run.grid(row=4, column=0, columnspan=2, sticky="ew")
        run.columnconfigure(0, weight=1)
        self.convert_btn = ttk.Button(run, text="Convertir", command=self.start)
        self.convert_btn.grid(row=0, column=0, sticky="ew", ipady=6)
        self.cancel_btn = ttk.Button(run, text="Cancelar", command=self.cancel, state="disabled")
        self.cancel_btn.grid(row=0, column=1, padx=(6, 0), ipady=6)
        self.progress = ttk.Progressbar(left, maximum=1001)  # 1001: con valor == máximo la barra se vacía
        self.progress.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        self.status = ttk.Label(left, text="Listo.", wraplength=380, justify="left")
        self.status.grid(row=6, column=0, columnspan=2, sticky="w")
        panes.add(left, weight=1)

        # Derecha: vista previa editable
        right = ttk.Frame(panes)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        top = ttk.Frame(right)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(top, text="Vista previa (puedes editarla)").pack(side="left")
        ttk.Button(top, text="Guardar todos en carpeta…", command=self.save_all).pack(side="right")
        ttk.Button(top, text="Guardar .md…", command=self.save_one).pack(side="right", padx=6)
        ttk.Button(top, text="Copiar", command=self.copy).pack(side="right")
        self.text = tk.Text(right, wrap="word", undo=True, font=self.preview_font, padx=10, pady=8)
        self.text.grid(row=1, column=0, sticky="nsew")
        sb2 = ttk.Scrollbar(right, command=self.text.yview)
        sb2.grid(row=1, column=1, sticky="ns")
        self.text.configure(yscrollcommand=sb2.set)
        self.notes = tk.Text(right, height=5, wrap="word", font=self.preview_font, padx=10, pady=6,
                             background="#fff8e1", foreground="#5c4400", state="disabled")
        self.notes.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.notes.grid_remove()
        panes.add(right, weight=3)

        self.ocr_label = ttk.Label(root, text="", foreground="#666")
        self.ocr_label.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 8))

        # Arrastrar y soltar dentro de la ventana (si tkinterdnd2 está disponible)
        try:
            from tkinterdnd2 import DND_FILES

            for widget in (self.tree, self.text):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", lambda e: self.add_paths(self.root.tk.splitlist(e.data)))
        except Exception:
            self.hint.configure(text="Añade archivos o carpetas con los botones de arriba.")

    def _refresh_ocr_status(self) -> None:
        from .pdf import ocr
        from .pdf.layout import docling_installed

        st = ocr.ocr_status()
        mode = "Modo calidad disponible" if docling_installed() else "Solo modo rápido (docling no instalado)"
        if st["motor"] == "tesseract":
            text = f"OCR: Tesseract ({', '.join(st['idiomas'])})"
            if st["faltan"]:
                text += " — FALTAN idiomas: " + ", ".join(st["faltan"])
                self.ocr_label.configure(foreground="#b3261e")
        elif st["motor"] == "rapidocr":
            text = "OCR: RapidOCR (Tesseract no instalado; el japonés vertical no está disponible)"
        else:
            text = "OCR NO INSTALADO: los documentos escaneados no se podrán convertir. Ejecuta de nuevo el instalador."
            self.ocr_label.configure(foreground="#b3261e")
        self.ocr_label.configure(text=f"{mode}  ·  {text}")

    # -- Archivos -----------------------------------------------------------

    def add_paths(self, paths) -> int:
        added = 0
        known = {it["path"] for it in self.items.values()}
        for p in paths:
            p = str(p).strip()
            if not p:
                continue
            path = Path(p)
            files = iter_files(path) if path.is_dir() else [path]
            for f in files:
                if str(f) in known:
                    continue
                if not f.exists() or (f.suffix.lower() not in SUPPORTED and not path.is_file()):
                    continue
                iid = self.tree.insert("", "end", text=f.name, values=(PENDING,))
                self.items[iid] = {"path": str(f), "md": "", "warnings": [], "status": PENDING}
                known.add(str(f))
                added += 1
        if added and self.current is None:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
        self._set_status(f"{len(self.items)} archivo(s) en la lista.")
        return added

    def ask_files(self) -> None:
        from tkinter import filedialog

        exts = " ".join("*" + e for e in sorted(SUPPORTED))
        paths = filedialog.askopenfilenames(title="Añadir archivos", filetypes=[("Documentos compatibles", exts), ("Todos", "*.*")])
        if paths:
            self.add_paths(paths)

    def ask_folder(self) -> None:
        from tkinter import filedialog, messagebox

        folder = filedialog.askdirectory(title="Añadir carpeta")
        if folder and not self.add_paths([folder]):
            messagebox.showinfo("Papiro", "Esa carpeta no contiene archivos compatibles.")

    def remove_selected(self) -> None:
        if self._busy():
            return
        for iid in self.tree.selection():
            self.items.pop(iid, None)
            self.tree.delete(iid)
        self.current = None
        self._show(None)

    def clear(self) -> None:
        if self._busy():
            return
        self.items.clear()
        self.tree.delete(*self.tree.get_children())
        self.current = None
        self._show(None)
        self.progress["value"] = 0
        self._set_status("Listo.")

    # -- Vista previa -------------------------------------------------------

    def _store_edits(self) -> None:
        if self.current in self.items and self.items[self.current]["status"] in (DONE, WARN):
            self.items[self.current]["md"] = self.text.get("1.0", "end-1c")

    def _on_select(self) -> None:
        sel = self.tree.selection()
        self._store_edits()
        self.current = sel[0] if sel else None
        self._show(self.current)

    def _show(self, iid: str | None) -> None:
        self.text.delete("1.0", "end")
        item = self.items.get(iid) if iid else None
        notes: list[str] = []
        if item:
            if item["status"] == FAIL:
                notes = ["No se pudo convertir este archivo:"] + item["warnings"]
            else:
                self.text.insert("1.0", item["md"])
                notes = item["warnings"]
        self.text.edit_reset()
        self.text.yview_moveto(0)
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        if notes:
            self.notes.insert("1.0", "\n".join("• " + n for n in notes))
            self.notes.grid()
        else:
            self.notes.grid_remove()
        self.notes.configure(state="disabled")

    # -- Conversión (en otro hilo: la ventana no se congela) ----------------

    def _busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def start(self) -> None:
        from tkinter import messagebox

        if self._busy():
            return
        if not self.items:
            messagebox.showinfo("Papiro", "Añade primero algún archivo.")
            return
        self._store_edits()
        todo = [iid for iid in self.tree.get_children() if self.items[iid]["status"] in (PENDING, FAIL)]
        if not todo:
            todo = list(self.tree.get_children())
        conv = Converter(mode=MODES[self.mode.get()], ocr=OCRS[self.ocr.get()], lang=LANGS[self.lang.get()],
                         furigana=self.furigana.get())
        self.cancel_flag.clear()
        self.convert_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress["value"] = 0
        jobs = [(iid, self.items[iid]["path"]) for iid in todo]
        for iid, _ in jobs:
            self._mark(iid, PENDING)
        self.worker = threading.Thread(target=self._work, args=(conv, jobs), daemon=True)
        self.worker.start()

    def _work(self, conv: Converter, jobs: list[tuple[str, str]]) -> None:
        n = len(jobs)
        for k, (iid, path) in enumerate(jobs):
            if self.cancel_flag.is_set():
                break
            self.events.put(("start", iid, k, n))

            def progress(done: int, total: int, msg: str, k=k, name=Path(path).name) -> None:
                frac = (k + (done / total if total else 0)) / n
                self.events.put(("progress", frac, f"{name}: {msg}"))

            try:
                res = conv.convert(path, progress=progress, cancel=self.cancel_flag.is_set)
                self.events.put(("done", iid, res.text_content, res.warnings))
            except Exception as exc:
                if type(exc).__name__ == "ConversionCancelled":
                    self.events.put(("cancelled", iid))
                    break
                self.events.put(("error", iid, str(exc) or type(exc).__name__))
            self.events.put(("progress", (k + 1) / n, ""))
        self.events.put(("finished",))

    def cancel(self) -> None:
        self.cancel_flag.set()
        self._set_status("Cancelando… (termina la página en curso)")

    def _mark(self, iid: str, status: str) -> None:
        if iid not in self.items:
            return
        self.items[iid]["status"] = status
        tag = {FAIL: "fail", WARN: "warn"}.get(status, "")
        self.tree.item(iid, values=(status,), tags=(tag,) if tag else ())

    def _poll(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "start":
                    self._mark(ev[1], WORKING)
                    self.tree.see(ev[1])
                elif kind == "progress":
                    self.progress["value"] = int(ev[1] * 1000)
                    if ev[2]:
                        self._set_status(ev[2])
                elif kind == "done":
                    _, iid, md, warnings = ev
                    if iid in self.items:
                        self.items[iid]["md"], self.items[iid]["warnings"] = md, list(warnings)
                        self._mark(iid, WARN if warnings else DONE)
                        if self.current in (None, iid):
                            self.tree.selection_set(iid)
                            self.current = iid
                            self._show(iid)
                elif kind == "error":
                    _, iid, msg = ev
                    if iid in self.items:
                        self.items[iid]["warnings"] = [msg]
                        self._mark(iid, FAIL)
                        if self.current in (None, iid):
                            self.current = iid
                            self._show(iid)
                elif kind == "cancelled":
                    self._mark(ev[1], PENDING)
                elif kind == "finished":
                    self._finished()
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _finished(self) -> None:
        self.convert_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        st = [it["status"] for it in self.items.values()]
        ok, warn, fail = st.count(DONE), st.count(WARN), st.count(FAIL)
        if self.cancel_flag.is_set():
            self._set_status("Conversión cancelada.")
            return
        self.progress["value"] = 1000
        parts = [f"{ok + warn} convertido(s)"]
        if warn:
            parts.append(f"{warn} con avisos (míralos debajo de la vista previa)")
        if fail:
            parts.append(f"{fail} con error")
        self._set_status("Terminado: " + ", ".join(parts) + ".")

    # -- Copiar y guardar ---------------------------------------------------

    def copy(self) -> None:
        text = self.text.get("1.0", "end-1c")
        if text.strip():
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._set_status("Markdown copiado al portapapeles.")

    def save_one(self) -> None:
        from tkinter import filedialog, messagebox

        if self.current not in self.items or not self.text.get("1.0", "end-1c").strip():
            messagebox.showinfo("Papiro", "No hay nada que guardar todavía. Pulsa «Convertir» primero.")
            return
        self._store_edits()
        item = self.items[self.current]
        src = Path(item["path"])
        target = filedialog.asksaveasfilename(
            title="Guardar Markdown", defaultextension=".md", initialfile=src.stem + ".md",
            initialdir=str(src.parent), filetypes=[("Markdown", "*.md"), ("Texto", "*.txt")])
        if target:
            Path(target).write_text(item["md"], encoding="utf-8")
            self._set_status(f"Guardado: {target}")

    def save_all_to(self, folder: str) -> int:
        self._store_edits()
        out = Path(folder)
        out.mkdir(parents=True, exist_ok=True)
        used: set[str] = set()
        count = 0
        for item in self.items.values():
            if item["status"] not in (DONE, WARN):
                continue
            name = Path(item["path"]).stem
            cand, i = name, 2
            while cand.lower() in used:
                cand, i = f"{name}_{i}", i + 1
            used.add(cand.lower())
            (out / f"{cand}.md").write_text(item["md"], encoding="utf-8")
            count += 1
        return count

    def save_all(self) -> None:
        from tkinter import filedialog, messagebox

        if not any(it["status"] in (DONE, WARN) for it in self.items.values()):
            messagebox.showinfo("Papiro", "No hay nada que guardar todavía. Pulsa «Convertir» primero.")
            return
        folder = filedialog.askdirectory(title="Carpeta donde guardar los .md")
        if folder:
            n = self.save_all_to(folder)
            self._set_status(f"{n} archivo(s) guardado(s) en {folder}")


def make_root():
    """Crea la ventana; con arrastrar y soltar si tkinterdnd2 funciona."""
    import tkinter as tk

    try:
        from tkinterdnd2 import TkinterDnD

        return TkinterDnD.Tk()
    except Exception:
        return tk.Tk()


def main(paths: list[str] | None = None) -> int:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("Falta tkinter. En Linux: sudo apt install python3-tk", file=sys.stderr)
        return 1
    root = make_root()
    App(root, paths or [])
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
