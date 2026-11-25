"""PyQt6 front end for fpkg2zar."""

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
import traceback

from PyQt6.QtCore import QEvent, QObject, QSettings, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QPalette, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSlider, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from . import __version__
from . import astrohybrid_fix  # TEMPORARY: remove when AstroHybrid fixes its .zar bug
from .convert import Options, Progress, convert, estimate_ratio, estimate_size, human, inspect, sample_package
from .errors import Cancelled

APP_NAME = "fPKG2Zar"

STYLE = """
/* Dark theme in PS5-style colours: deep navy, console blue, white highlights. */
* { font-family: 'Segoe UI', 'SF Pro Text', 'Helvetica Neue', sans-serif; font-size: 10pt; }
QWidget { background: transparent; color: #eef3fb; }
QWidget#root { background: qlineargradient(x1:0, y1:0, x2:0.35, y2:1, stop:0 #0d2147, stop:0.45 #07122a, stop:1 #04070f); }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QToolTip { background: #0f1b33; color: #eef3fb; border: 1px solid #2a4a7a; padding: 4px 6px; }
QDialog { background: qlineargradient(x1:0, y1:0, x2:0.35, y2:1, stop:0 #0d2147, stop:0.45 #07122a, stop:1 #04070f); }
QWidget#dialogroot { border: 1px solid #24406e; }
QLabel#dlgicon { font-size: 16pt; font-weight: 700; color: #ffffff; border-radius: 20px; min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px; qproperty-alignment: AlignCenter; }
QLabel#dlgicon[kind="info"] { background: #0070d1; }
QLabel#dlgicon[kind="ask"] { background: #0070d1; }
QLabel#dlgicon[kind="warn"] { background: #c98a12; }
QLabel#dlgicon[kind="error"] { background: #c42b3c; }
QLabel#dlgtitle { font-size: 11pt; font-weight: 600; color: #ffffff; }
QMenu { background: #0f1b33; border: 1px solid #24406e; border-radius: 8px; padding: 4px; }
QMenu::item { padding: 6px 22px; border-radius: 6px; color: #eef3fb; background: transparent; }
QMenu::item:selected { background: #0a3f7a; }
QMenu::item:disabled { color: #5f6f8c; }
QMenu::separator { height: 1px; background: #1d3157; margin: 4px 8px; }
QSpinBox::up-button, QSpinBox::down-button { background: #16284a; border: none; width: 16px; border-radius: 3px; margin: 1px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #1d3561; }
QSpinBox::up-arrow { image: url(@ASSETS@/arrow_up.svg); width: 10px; height: 6px; }
QSpinBox::down-arrow { image: url(@ASSETS@/arrow_down.svg); width: 10px; height: 6px; }
QComboBox { background: #0a1427; border: 1px solid #1d3157; border-radius: 8px; padding: 4px 10px; min-height: 24px; color: #eef3fb; }
QComboBox:hover, QComboBox:focus { border-color: #4da3ff; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: url(@ASSETS@/arrow_down.svg); width: 10px; height: 6px; }
QComboBox QAbstractItemView { background: #0f1b33; border: 1px solid #24406e; selection-background-color: #0a3f7a; color: #eef3fb; outline: 0; }
QListView, QTreeView { background: #0a1427; border: 1px solid #1d3157; border-radius: 8px; color: #eef3fb; selection-background-color: #0a3f7a; selection-color: #ffffff; outline: 0; }
QTreeView::item:hover, QListView::item:hover { background: #12264a; }
QFileDialog QToolButton { background: #16284a; border: 1px solid #24406e; border-radius: 6px; padding: 3px; min-width: 24px; min-height: 22px; }
QFileDialog QToolButton:hover { border-color: #ffffff; }

QGroupBox { background: rgba(14, 24, 46, 0.82); border: 1px solid #1d3157; border-radius: 12px; margin-top: 12px; padding: 6px 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; color: #6fb4ff; font-weight: 600; }

QPushButton { background: #16284a; border: 1px solid #24406e; border-radius: 15px; padding: 4px 18px; color: #eef3fb; min-height: 24px; }
QPushButton:hover { background: #1d3561; border-color: #ffffff; }
QPushButton:pressed { background: #0f1e3a; }
QPushButton:disabled { color: #5f6f8c; background: #0e1a31; border-color: #18294a; }
QPushButton#primary { background: #0070d1; border-color: #0070d1; color: #ffffff; font-weight: 600; padding: 6px 28px; }
QPushButton#primary:hover { background: #1a88ef; border-color: #ffffff; }
QPushButton#primary:disabled { background: #123a66; border-color: #123a66; color: #8aa3c4; }
QPushButton#danger { background: #4a1420; border-color: #7a2436; }
QPushButton#danger:hover { background: #6b1c2e; border-color: #ffffff; }
QPushButton#danger:disabled { background: #1d1420; border-color: #2a1a26; color: #6c5a66; }

QLineEdit, QSpinBox, QPlainTextEdit, QTableWidget { background: #0a1427; border: 1px solid #1d3157; border-radius: 8px; padding: 4px 8px; selection-background-color: #0070d1; selection-color: #ffffff; }
QLineEdit:focus, QSpinBox:focus { border-color: #4da3ff; }
QLineEdit, QSpinBox { min-height: 24px; }
QCheckBox { min-height: 22px; spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid #3a5d93; background: #0a1427; }
QCheckBox::indicator:checked { background: #0070d1; border-color: #4da3ff; image: none; }
QCheckBox::indicator:hover { border-color: #ffffff; }

QTabWidget::pane { background: rgba(14, 24, 46, 0.82); border: 1px solid #1d3157; border-radius: 12px; top: -1px; }
QTabBar::tab { background: transparent; color: #8fa2c0; border: none; border-bottom: 3px solid transparent; padding: 6px 18px; margin-right: 4px; font-weight: 600; }
QTabBar::tab:selected { color: #ffffff; border-bottom: 3px solid #0070d1; }
QTabBar::tab:hover { color: #ffffff; }

QHeaderView::section { background: #0f1d38; color: #8fa2c0; border: none; border-bottom: 1px solid #1d3157; padding: 6px; font-weight: 600; }
QTableWidget { gridline-color: #14233f; alternate-background-color: #0d182e; }
QTableWidget::item:selected { background: #0a3f7a; color: #ffffff; }
QTableCornerButton::section { background: #0f1d38; border: none; }

QProgressBar { background: #0a1427; border: 1px solid #1d3157; border-radius: 11px; min-height: 22px; text-align: center; color: #ffffff; font-weight: 600; }
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0050a8, stop:1 #29a8ff); border-radius: 10px; }

QSlider::groove:horizontal { height: 4px; background: #1d3157; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #0070d1; border-radius: 2px; }
QSlider::handle:horizontal { background: #ffffff; width: 16px; margin: -6px 0; border-radius: 8px; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #24406e; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #3a6db0; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #24406e; border-radius: 4px; min-width: 24px; }

QWidget#root { border: 1px solid #24406e; }
QWidget#root[maxed="true"] { border: none; }
QPushButton#winbtn, QPushButton#winclose { background: transparent; border: none; border-radius: 6px; color: #c9d6ea; font-size: 11pt; padding: 0; min-height: 0; }
QPushButton#winbtn:hover, QPushButton#winclose:hover { background: rgba(255, 255, 255, 0.10); color: #ffffff; }
QPushButton#winclose:hover { background: #c42b3c; color: #ffffff; }
QPlainTextEdit#minilog { background: rgba(6, 12, 24, 0.75); color: #b9c7dd; }
QPushButton#aboutbtn { background: transparent; border: 1px solid #24406e; border-radius: 13px; padding: 2px 14px; min-height: 22px; color: #c9d6ea; margin-top: 2px; }
QPushButton#aboutbtn:hover { background: rgba(255, 255, 255, 0.10); border-color: #ffffff; color: #ffffff; }
QTextBrowser#abouttext { background: transparent; border: none; padding: 8px 12px; color: #dfe7f4; }
QLabel#title { font-size: 20pt; font-weight: 700; color: #ffffff; letter-spacing: 1px; }
QLabel#subtitle { color: #8fa2c0; }
QLabel#stat { color: #ffffff; font-size: 11pt; font-weight: 600; }
QLabel#statcap { color: #8fa2c0; }
"""

ASSETS_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "fpkg2zar", "assets")


def stylesheet() -> str:
    """The app stylesheet with asset paths filled in (works from source and from the portable exe)."""
    return STYLE.replace("@ASSETS@", ASSETS_DIR.replace("\\", "/"))


COL_GAME, COL_FILE, COL_SIZE, COL_EST, COL_STATUS = range(5)


def safe_filename(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s).strip().rstrip(".")
    return s[:150] or "game"


def fmt_eta(sec: float) -> str:
    if sec <= 0:
        return "--:--"
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Job:
    def __init__(self, path: str):
        self.path = path
        self.info: dict | None = None
        self.error: str = ""
        self.status = "Reading…"
        self.sample = None        # decoded 64 KiB chunks for size estimates
        self.ratios: dict = {}    # zstd level -> estimated ratio
        self.output = ""


class FixEmitter(QObject):
    """TEMPORARY (AstroHybrid fix): carries results from the worker thread."""
    done = pyqtSignal(str, bool, bool)  # message, ok, quiet


class EstimateEmitter(QObject):
    """Carries estimate results from worker threads back to the GUI thread."""
    ready = pyqtSignal(object, int, float, str)  # job, level, ratio, error


class InspectWorker(QObject):
    done = pyqtSignal(object, object, str)  # job, info, error

    def __init__(self, job: Job):
        super().__init__()
        self.job = job

    def run(self):
        try:
            self.done.emit(self.job, inspect(self.job.path), "")
        except Exception as e:  # noqa: BLE001
            self.done.emit(self.job, None, str(e))


class ConvertWorker(QObject):
    progress = pyqtSignal(object)
    log = pyqtSignal(str)
    files = pyqtSignal(str)  # a batch of "file written" lines for the full log
    job_started = pyqtSignal(object)
    job_finished = pyqtSignal(object, bool, str)
    all_done = pyqtSignal()

    def __init__(self, jobs, out_dir, opts: Options):
        super().__init__()
        self.jobs, self.out_dir, self.opts = jobs, out_dir, opts
        self.cancel = threading.Event()

    def run(self):
        for job in self.jobs:
            if self.cancel.is_set():
                break
            self.job_started.emit(job)
            try:
                os.makedirs(self.out_dir or os.path.dirname(job.path), exist_ok=True)
                batch: list = []
                last = [time.monotonic()]

                def flush():
                    if batch:
                        self.files.emit("\n".join(batch))
                        batch.clear()
                    last[0] = time.monotonic()

                def on_file(path, size, how):
                    note = {"linked": "  (same data as an earlier file, stored once)", "metadata": "  (package metadata)"}.get(how, "")
                    batch.append(f"{human(size):>12}  {path}{note}")
                    if len(batch) >= 2000 or time.monotonic() - last[0] >= 0.25:
                        flush()

                def log(line):
                    flush()  # keep the file list and the messages in order
                    self.log.emit(line)

                try:
                    res = convert(job.path, job.output, self.opts,
                                  progress=lambda p: self.progress.emit(_snapshot(p)),
                                  log=log, cancel=self.cancel, on_file=on_file)
                finally:
                    flush()
                msg = f"{human(res.output_bytes)}  ({res.output_bytes / res.input_bytes * 100:.1f}%)" if res.input_bytes else "done"
                if res.verified:
                    msg += "  ✓ verified"
                self.job_finished.emit(job, True, msg)
            except Cancelled:
                self.job_finished.emit(job, False, "Cancelled")
                break
            except Exception as e:  # noqa: BLE001
                self.log.emit("ERROR: " + str(e))
                self.log.emit(traceback.format_exc().rstrip())
                self.job_finished.emit(job, False, f"Failed: {e}")
        self.all_done.emit()


def _snapshot(p: Progress) -> dict:
    return {"phase": p.phase, "done": p.done, "total": p.total, "out": p.out_bytes, "cur": p.current,
            "speed": p.speed, "eta": p.eta, "elapsed": p.elapsed}


class Stat:
    """A caption/value label pair, laid out in a grid by the caller."""

    def __init__(self, caption: str):
        self.caption = QLabel(caption)
        self.caption.setObjectName("statcap")
        self.value = QLabel("—")
        self.value.setObjectName("stat")

    def set(self, s: str):
        self.value.setText(s)


class ThemedDialog(QDialog):
    """Message box drawn in the app's theme, with the same blended title bar."""

    ICONS = {"info": "i", "warn": "!", "error": "!", "ask": "?"}

    def __init__(self, parent, kind: str, text: str, buttons=(("OK", "ok"),)):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.result_value = None
        root = QWidget(self)
        root.setObjectName("dialogroot")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(18, 10, 10, 16)
        head = QHBoxLayout()
        title = QLabel(APP_NAME)
        title.setObjectName("dlgtitle")
        head.addWidget(title)
        head.addStretch()
        close = QPushButton("\u2715")
        close.setObjectName("winclose")
        close.setFixedSize(40, 28)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(self.reject)
        head.addWidget(close)
        v.addLayout(head)
        body = QHBoxLayout()
        body.setContentsMargins(0, 8, 8, 8)
        icon = QLabel(self.ICONS.get(kind, "i"))
        icon.setObjectName("dlgicon")
        icon.setProperty("kind", kind)
        body.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        body.addSpacing(14)
        msg = QLabel(text)
        msg.setWordWrap(True)
        msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        msg.setMinimumWidth(320)
        msg.setMaximumWidth(560)
        body.addWidget(msg, 1)
        v.addLayout(body)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 8, 0)
        row.addStretch()
        for label, value in buttons:
            b = QPushButton(label)
            if value in ("ok", "yes"):
                b.setObjectName("primary")
                b.setDefault(True)
            b.clicked.connect(lambda _=False, val=value: self._done(val))
            row.addWidget(b)
        v.addLayout(row)

    def _done(self, value):
        self.result_value = value
        self.accept()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 44:
            self.windowHandle().startSystemMove()  # drag by the title area
        super().mousePressEvent(e)


def show_info(parent, text):
    ThemedDialog(parent, "info", text).exec()


def show_warning(parent, text):
    ThemedDialog(parent, "warn", text).exec()


def ask_yes_no(parent, text) -> bool:
    d = ThemedDialog(parent, "ask", text, buttons=(("No", "no"), ("Yes", "yes")))
    d.exec()
    return d.result_value == "yes"


AUTHOR = "snuffy420"
STUDIO = "zerØ software"
REPO_URL = "https://github.com/snuffy420/fpkg2zar"
CONTACT_URL = "https://snuffy420.github.io/fpkg2zar/docs/contact.html"
BTC_ADDRESS = "bc1qz8877p6vusc22qrctdj4h7w0uk2l9l9h8lwphy"

ABOUT_HTML = f"""
<img src="{ASSETS_DIR.replace(chr(92), "/")}/icon.png" width="72" height="72" align="right">
<h2 style="color:#ffffff; margin-bottom:0">{APP_NAME} <span style="color:#8fa2c0; font-weight:400">v{__version__}</span></h2>
<p style="color:#8fa2c0; margin-top:2px">PS5 fake package (.pkg) to ZArchive (.zar) converter</p>
<p>Made by <b>{AUTHOR}</b> &middot; <b>{STUDIO}</b></p>
<p>Converts a PS5 fPKG straight into a .zar without unpacking the game first. The only disk space needed is
the size of the finished .zar.</p>
<p><b style="color:#6fb4ff">PS5 fake packages only.</b> PS4, PS3 and retail PS5 packages are encrypted and are not supported.</p>
<p><b style="color:#6fb4ff">No proprietary code, keys, ROMs or game data.</b> {APP_NAME} contains none of these. It only reads
the packages you already own and give it.</p>
<p><b style="color:#6fb4ff">Credits</b><br>
ZArchive format by Exzap (MIT-0)<br>
PKG/PFS layout knowledge from pkg-to-anyps5, PS5PCEM and LibProsperoPKG (GPL)<br>
Built with Python, PyQt6 and zstandard</p>
<p>Licence: GPL-3.0-or-later<br>
Source: <a style="color:#4da3ff" href="{REPO_URL}">{REPO_URL}</a><br>
Contact: <a style="color:#4da3ff" href="{CONTACT_URL}">contact form</a></p>
"""

HELP_HTML = f"""
<h3 style="color:#ffffff">Converting a game</h3>
<ol>
<li>Click <b>Add PKG…</b> or drag one or more .pkg files onto the window.</li>
<li>Pick a <b>Save to</b> folder. It can be on any drive.</li>
<li>Choose a compression level (15 is the default) and click <b>Convert</b>.</li>
</ol>
<p>The .zar is written as <i>name.zar.partial</i> and renamed when it finishes. Cancelling deletes the partial file.</p>

<h3 style="color:#ffffff">How it works</h3>
<p>A PS5 fPKG holds a compressed game image inside a plain container. {APP_NAME} reads the package from start to end,
decompresses each block in memory on all CPU cores, and feeds every file straight into the ZArchive writer, which
compresses it again with zstd. Nothing is unpacked to disk, so a 35 GB package needs only about 40 GB free
for the .zar, not 150 GB or more for an extracted folder.</p>
<p>Files that appear twice in the package are stored once and linked. The <b>Est. .zar</b> column samples about
128 MiB of the game to predict the final size, usually within 5%.</p>

<h3 style="color:#ffffff">Compression levels</h3>
<table cellspacing="0" cellpadding="3">
<tr><td><b>1&ndash;5</b></td><td>Fastest, biggest file.</td></tr>
<tr><td><b>6&ndash;14</b></td><td>Balanced.</td></tr>
<tr><td><b>15</b></td><td>Default. Close to the original .pkg size at a reasonable speed.</td></tr>
<tr><td><b>16&ndash;22</b></td><td>About 1% smaller, but 4 to 5 times slower.</td></tr>
</table>
<p>Every level loads the same way. A higher level only makes the file smaller.</p>

<h3 style="color:#ffffff">Options</h3>
<ul>
<li><b>Verify</b> reads the finished .zar back and checks every file. It takes about as long again.</li>
<li><b>Include sce_sys metadata</b> adds the package's icons and info files.</li>
<li><b>Fix AstroHybrid .zar loading</b> is a temporary option. It swaps four Microsoft DLLs in your AstroHybrid
folder for the versions its config expects (its issue #272), backs up the originals and restores them when
unticked. It will be removed once AstroHybrid fixes the bug.</li>
</ul>

<h3 style="color:#ffffff">Supported packages</h3>
<p><b>PS5 fake packages (fPKG) only.</b> Retail PS5, PS4 and PS3 packages are encrypted and {APP_NAME} will say so
and skip them. It does not decrypt anything.</p>

<h3 style="color:#ffffff">Problems</h3>
<ul>
<li><b>Not enough space:</b> the destination needs roughly the <b>Est. .zar</b> size free.</li>
<li><b>Unsupported compression:</b> the package uses a Kraken mode {APP_NAME} can't decode yet. Please report it with the game name.</li>
<li>The <b>Log</b> tab lists every file and any errors. Include it when reporting a problem.</li>
</ul>
<p>Report issues at <a style="color:#4da3ff" href="{REPO_URL}/issues">{REPO_URL}/issues</a>, or use the
<a style="color:#4da3ff" href="{CONTACT_URL}">contact form</a>. Please don't send packages, keys or game files.</p>
"""


class AboutDialog(QDialog):
    """About / Help / Donate window in the app's theme."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.resize(660, 600)
        root = QWidget(self)
        root.setObjectName("dialogroot")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(18, 10, 10, 16)
        head = QHBoxLayout()
        title = QLabel(f"About {APP_NAME}")
        title.setObjectName("dlgtitle")
        head.addWidget(title)
        head.addStretch()
        close = QPushButton("✕")
        close.setObjectName("winclose")
        close.setFixedSize(40, 28)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(self.reject)
        head.addWidget(close)
        v.addLayout(head)

        tabs = QTabWidget()
        tabs.addTab(self._text_tab(ABOUT_HTML), "About")
        tabs.addTab(self._text_tab(HELP_HTML), "Help")
        tabs.addTab(self._donate_tab(), "Donate")
        body = QHBoxLayout()
        body.setContentsMargins(0, 4, 8, 0)
        body.addWidget(tabs)
        v.addLayout(body, 1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 8, 0)
        row.addStretch()
        ok = QPushButton("Close")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        v.addLayout(row)

    @staticmethod
    def _text_tab(html: str) -> QWidget:
        t = QTextBrowser()
        t.setObjectName("abouttext")
        t.setOpenExternalLinks(True)
        t.setHtml(html)
        return t

    def _donate_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)
        msg = QLabel(f"{APP_NAME} is free. If it saved you some disk space and you'd like to say thanks, "
                     "Bitcoin donations are welcome.")
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(msg)
        qr = QLabel()
        pm = QPixmap(os.path.join(ASSETS_DIR, "btc_qr.png"))
        if not pm.isNull():
            qr.setPixmap(pm.scaled(240, 240, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
        qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr.setToolTip("Scan with your wallet (bitcoin:" + BTC_ADDRESS + ")")
        v.addWidget(qr)
        cap = QLabel("Bitcoin (BTC)")
        cap.setObjectName("statcap")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(cap)
        addr_row = QHBoxLayout()
        addr = QLineEdit(BTC_ADDRESS)
        addr.setReadOnly(True)
        addr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        addr.setMinimumWidth(380)
        self._copy_btn = QPushButton("Copy")
        self._copy_btn.clicked.connect(self._copy_address)
        addr_row.addStretch()
        addr_row.addWidget(addr)
        addr_row.addWidget(self._copy_btn)
        addr_row.addStretch()
        v.addLayout(addr_row)
        wallet = QPushButton("Open in wallet")
        wallet.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("bitcoin:" + BTC_ADDRESS)))
        wr = QHBoxLayout()
        wr.addStretch()
        wr.addWidget(wallet)
        wr.addStretch()
        v.addLayout(wr)
        v.addStretch()
        thanks = QLabel(f"Thank you for supporting {STUDIO}!")
        thanks.setObjectName("subtitle")
        thanks.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(thanks)
        return w

    def _copy_address(self):
        QApplication.clipboard().setText(BTC_ADDRESS)
        self._copy_btn.setText("Copied")
        QTimer.singleShot(1500, lambda: self._copy_btn.setText("Copy"))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 44:
            self.windowHandle().startSystemMove()
        super().mousePressEvent(e)


# Qt's own file picker instead of the Windows one, so it follows the theme.
FILE_DIALOG_OPTS = QFileDialog.Option.DontUseNativeDialog


class TitleBar(QWidget):
    """App-drawn title bar: drag to move, double-click to maximize, window buttons on the right."""

    def __init__(self, win: QMainWindow):
        super().__init__()
        self.win = win
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        logo = QLabel()
        pm = QPixmap(os.path.join(ASSETS_DIR, "icon.png"))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            lay.addWidget(logo)
            lay.addSpacing(8)
        t = QLabel(APP_NAME)
        t.setObjectName("title")
        st = QLabel(f"v{__version__}")
        st.setObjectName("subtitle")
        lay.addWidget(t)
        lay.addSpacing(10)
        lay.addWidget(st, 0, Qt.AlignmentFlag.AlignBottom)
        lay.addStretch()
        self.b_about = QPushButton("About")
        self.b_about.setObjectName("aboutbtn")
        self.b_about.setToolTip("About, help and donate")
        self.b_about.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.b_about.clicked.connect(lambda: AboutDialog(win).exec())
        lay.addWidget(self.b_about, 0, Qt.AlignmentFlag.AlignTop)
        lay.addSpacing(8)
        self.b_min = self._btn("—", "Minimize", win.showMinimized)
        self.b_max = self._btn("□", "Maximize", self._toggle_max)
        self.b_close = self._btn("✕", "Close", win.close)
        self.b_close.setObjectName("winclose")
        for b in (self.b_min, self.b_max, self.b_close):
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)

    def _btn(self, text, tip, fn):
        b = QPushButton(text)
        b.setObjectName("winbtn")
        b.setToolTip(tip)
        b.setFixedSize(46, 30)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(fn)
        return b

    def _toggle_max(self):
        self.win.showNormal() if self.win.isMaximized() else self.win.showMaximized()

    def sync_max(self):
        maxed = self.win.isMaximized()
        self.b_max.setText("❐" if maxed else "□")
        self.b_max.setToolTip("Restore" if maxed else "Maximize")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self.win._edges_at(e.globalPosition().toPoint()):
            self.win.windowHandle().startSystemMove()  # keeps Windows snap / Aero shake
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._toggle_max()
        super().mouseDoubleClickEvent(e)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — PS5 PKG to ZAR converter  v{__version__}")
        # The app draws its own title bar so it blends with the background
        # (Windows 10 cannot recolour the system one).
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setMouseTracking(True)
        QApplication.instance().installEventFilter(self)
        self.setMinimumSize(860, 540)
        self.setAcceptDrops(True)
        self.jobs: list[Job] = []
        self._threads: list = []
        self.worker: ConvertWorker | None = None

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(16, 8, 16, 10)
        v.setSpacing(6)

        self.titlebar = TitleBar(self)
        v.addWidget(self.titlebar)

        # queue
        qbox = QWidget()
        ql = QVBoxLayout(qbox)
        ql.setContentsMargins(8, 8, 8, 8)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Game", "Package", "Size", "Est. .zar", "Status"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_GAME, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(COL_FILE, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(COL_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_EST, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(84)
        self.table.setMaximumHeight(130)  # ~3 rows visible, scrolls for more
        ql.addWidget(self.table)
        hint = QLabel("Drop .pkg files here, or use Add.")
        hint.setObjectName("subtitle")
        row = QHBoxLayout()
        self.btn_add = QPushButton("Add PKG…")
        self.btn_remove = QPushButton("Remove")
        self.btn_clear = QPushButton("Clear")
        self.btn_add.clicked.connect(self.add_dialog)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_clear.clicked.connect(self.clear_jobs)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_remove)
        row.addWidget(self.btn_clear)
        row.addStretch()
        row.addWidget(hint)
        ql.addLayout(row)

        # output + options
        obox = QGroupBox("Output and options")
        g = QGridLayout(obox)
        g.setVerticalSpacing(5)
        g.addWidget(QLabel("Save to folder"), 0, 0)
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("Same folder as each package")
        self.out_edit.textChanged.connect(self.update_free)
        g.addWidget(self.out_edit, 0, 1, 1, 3)
        b = QPushButton("Browse…")
        b.clicked.connect(self.pick_out)
        g.addWidget(b, 0, 4)


        g.addWidget(QLabel("Compression"), 2, 0)
        self.level = QSlider(Qt.Orientation.Horizontal)
        self.level.setRange(1, 22)
        self.level.setValue(15)
        self.level_label = QLabel()
        self.level.valueChanged.connect(self._level_text)
        self.est_label = QLabel("Estimated .zar size appears here once a package is added.")
        self.est_label.setObjectName("subtitle")
        self._est_timer = QTimer(self)
        self._est_timer.setSingleShot(True)
        self._est_timer.timeout.connect(self._refresh_estimates)
        self.level.valueChanged.connect(lambda _v: self._est_timer.start(350))
        self._est = EstimateEmitter()
        self._est.ready.connect(self._estimate_ready)
        self._level_text(self.level.value())
        g.addWidget(self.level, 2, 1, 1, 2)
        g.addWidget(self.level_label, 2, 3, 1, 2)
        g.addWidget(self.est_label, 3, 1, 1, 4)

        g.addWidget(QLabel("Threads"), 4, 0)
        self.threads = QSpinBox()
        self.threads.setRange(1, max(1, os.cpu_count() or 4) * 2)
        self.threads.setValue(os.cpu_count() or 4)
        g.addWidget(self.threads, 4, 1)
        self.chk_verify = QCheckBox("Verify after writing (reads the archive back, slower)")
        self.chk_meta = QCheckBox("Add package metadata to sce_sys/ (param.json, icons)")
        self.chk_meta.setChecked(True)
        self.chk_over = QCheckBox("Overwrite existing .zar")
        g.addWidget(self.chk_over, 4, 2, 1, 3)
        g.addWidget(self.chk_verify, 5, 1, 1, 4)
        g.addWidget(self.chk_meta, 6, 1, 1, 4)
        # --- TEMPORARY: AstroHybrid .zar fix (remove when AstroHybrid fixes issue #272/#283) ---
        self.settings = QSettings("fPKG2Zar", "fPKG2Zar")
        self.chk_ahfix = QCheckBox("Fix AstroHybrid .zar loading (community fix for its issue #272)")
        self.chk_ahfix.setToolTip("Replaces 4 Microsoft DLLs in your AstroHybrid folder with the versions its config expects.\n"
                                  "Originals are backed up; untick to restore them.")
        self._ah_folder = self.settings.value("astrohybrid/folder", "", str)
        self.chk_ahfix.setChecked(self.settings.value("astrohybrid/enabled", False, bool) and bool(self._ah_folder))
        self.chk_ahfix.toggled.connect(self._ahfix_toggled)
        self._fix = FixEmitter()
        self._fix.done.connect(self._ahfix_done)
        g.addWidget(self.chk_ahfix, 7, 1, 1, 4)
        # --- end TEMPORARY ---
        g.setColumnStretch(2, 1)

        # progress
        pbox = QGroupBox("Progress")
        pl = QVBoxLayout(pbox)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setFormat("Idle")
        pl.addWidget(self.bar)
        self.cur_label = QLabel("")
        self.cur_label.setObjectName("subtitle")
        pl.addWidget(self.cur_label)
        self.cur_label.setWordWrap(True)
        # Always reserve two lines, so a long file name wrapping doesn't shift the stats below.
        self.cur_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.cur_label.setFixedHeight(self.cur_label.fontMetrics().lineSpacing() * 2 + 4)
        stats = QGridLayout()
        stats.setHorizontalSpacing(16)
        stats.setVerticalSpacing(6)
        self.s_done = Stat("Processed")
        self.s_out = Stat("Archive so far")
        self.s_ratio = Stat("Ratio")
        self.s_speed = Stat("Speed")
        self.s_eta = Stat("Time left")
        self.s_free = Stat("Free at output")
        for i, s in enumerate((self.s_done, self.s_speed, self.s_out, self.s_eta, self.s_ratio, self.s_free)):
            r, c = i // 2, (i % 2) * 2
            stats.addWidget(s.caption, r, c)
            stats.addWidget(s.value, r, c + 1)
        stats.setColumnStretch(1, 1)
        stats.setColumnStretch(3, 1)
        pl.addLayout(stats)
        pl.addStretch()

        actions = QHBoxLayout()
        self.btn_open = QPushButton("Open output folder")
        self.btn_open.clicked.connect(self.open_output)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("danger")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_start = QPushButton("Convert")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self.start)
        actions.addWidget(self.btn_open)
        actions.addStretch()
        actions.addWidget(self.btn_cancel)
        actions.addWidget(self.btn_start)

        self.logv = QPlainTextEdit()
        self.logv.setReadOnly(True)
        self.logv.setFont(QFont("Consolas", 9))
        self.logv.setMaximumBlockCount(400000)  # room for every file of a big game

        # Live view of the latest log lines; fills whatever room is left at the bottom.
        self.minilog = QPlainTextEdit()
        self.minilog.setObjectName("minilog")
        self.minilog.setReadOnly(True)
        self.minilog.setFont(QFont("Consolas", 9))
        self.minilog.setMaximumBlockCount(300)
        self.minilog.setMinimumHeight(48)
        self.minilog.setPlaceholderText("Activity appears here. The full history is in the Log tab.")

        self.tabs = QTabWidget()
        self.tabs.addTab(qbox, "Packages")
        self.tabs.addTab(self.logv, "Log")
        self.tabs.currentChanged.connect(self._tab_changed)

        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(2)
        tl.addWidget(self.tabs, 0)
        self.panels = QWidget()  # options + progress; hidden while the Log tab is open
        h2 = QHBoxLayout(self.panels)
        h2.setContentsMargins(0, 0, 0, 0)
        h2.addWidget(obox, 3)
        h2.addWidget(pbox, 2)
        tl.addWidget(self.panels, 0)
        tl.addWidget(self.minilog, 1)  # takes the spare height
        self._top_layout = tl
        v.addWidget(top, 1)
        v.addLayout(actions)
        # Never let the window shrink far enough to squash or clip a control.
        self.setMinimumSize(root.minimumSizeHint().expandedTo(self.minimumSize()))
        self._fit_to_screen(1600, 900)  # opens at ~900p on screen, never larger than the screen

        self.log(f"{APP_NAME} {__version__} ready. {os.cpu_count()} CPU threads available.")
        self._check_runtime()
        if self.chk_ahfix.isChecked():  # TEMPORARY: re-apply after AstroHybrid updates
            QTimer.singleShot(500, lambda: self._ahfix_run(quiet=True))
        self.update_free()

    # -- helpers ---------------------------------------------------------------
    def _fit_to_screen(self, want_w: int, want_h: int):
        """Opens at a comfortable size that always fits on the current screen, centred."""
        scr = QApplication.primaryScreen()
        if scr is None:
            self.resize(want_w, want_h)
            return
        avail = scr.availableGeometry()
        dpr = scr.devicePixelRatio() or 1.0
        w = min(max(int(want_w / dpr), self.minimumWidth()), avail.width())
        h = min(max(int(want_h / dpr), self.minimumHeight()), avail.height())
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)

    def _check_runtime(self):
        try:
            from . import kraken
            kraken.load()
        except Exception as e:  # noqa: BLE001
            self.log(f"WARNING: {e}")
        try:
            import zstandard  # noqa: F401
        except ImportError:
            self.log("WARNING: the 'zstandard' package is missing. Run:  pip install zstandard")

    def _level_text(self, v):
        if v <= 3:
            hint = "fastest, largest"
        elif v <= 9:
            hint = "fast" + (" (zarchive.exe default)" if v == 6 else "")
        elif v <= 14:
            hint = "balanced"
        elif v <= 16:
            hint = "smallest for the time (recommended)"
        else:
            hint = "very slow, barely smaller"
        self.level_label.setText(f"zstd {v} — {hint}")

    # -- TEMPORARY: AstroHybrid .zar fix ----------------------------------------
    def _ahfix_toggled(self, on: bool):
        if on:
            show_info(self, "This applies the community fix for AstroHybrid's .zar bug (its issue #272):\n\n"
                                  "\u2022 downloads System.Memory 4.6.0, System.Buffers 4.6.0, System.Numerics.Vectors 4.6.0 and "
                                  "System.Runtime.CompilerServices.Unsafe 6.1.0 from nuget.org (official Microsoft packages, "
                                  "checked against NuGet's published checksums)\n"
                                  "\u2022 backs up AstroHybrid's original DLLs and swaps these in\n"
                                  "\u2022 checks again each time fPKG2Zar starts, in case an AstroHybrid update undid it\n\n"
                                  "Untick it any time to restore the originals. Click OK, then pick your AstroHybrid folder.")
            folder = QFileDialog.getExistingDirectory(self, "Choose your AstroHybrid folder", self._ah_folder or "",
                                                      options=FILE_DIALOG_OPTS | QFileDialog.Option.ShowDirsOnly)
            if folder and not astrohybrid_fix.looks_like_astrohybrid(folder):
                if not ask_yes_no(self, f"{folder} doesn't contain AstroHybrid.exe. Use it anyway?"):
                    folder = ""
            if not folder:
                self.chk_ahfix.blockSignals(True)
                self.chk_ahfix.setChecked(False)
                self.chk_ahfix.blockSignals(False)
                return
            self._ah_folder = os.path.normpath(folder)
            self.settings.setValue("astrohybrid/folder", self._ah_folder)
            self.settings.setValue("astrohybrid/enabled", True)
            self._ahfix_run(quiet=False)
        else:
            self.settings.setValue("astrohybrid/enabled", False)
            if self._ah_folder and ask_yes_no(self, "Restore AstroHybrid's original DLLs from the backups?"):
                self._ahfix_run(quiet=False, undo=True)

    def _ahfix_run(self, quiet: bool, undo: bool = False):
        folder = self._ah_folder
        if not folder or not os.path.isdir(folder):
            self.log(f"AstroHybrid fix: folder not found ({folder or 'none set'})")
            return
        self.chk_ahfix.setEnabled(False)

        def work():
            try:
                lines = []
                rep = (astrohybrid_fix.restore if undo else astrohybrid_fix.apply)(folder, log=lines.append)
                msg = "AstroHybrid fix: " + rep.summary()
                ok = not rep.missing or undo
                self._fix.done.emit("\n".join(lines + [msg]), ok, quiet and not rep.replaced and ok)
            except Exception as e:  # noqa: BLE001
                self._fix.done.emit(f"AstroHybrid fix failed: {e}", False, False)

        threading.Thread(target=work, daemon=True).start()

    def _ahfix_done(self, msg: str, ok: bool, quiet: bool):
        self.chk_ahfix.setEnabled(True)
        for line in msg.splitlines():
            self.log(line if ok else "WARNING: " + line)
        if not quiet:
            (show_info if ok else show_warning)(self, msg.splitlines()[-1])
    # -- end TEMPORARY ----------------------------------------------------------

    def _tab_changed(self, i: int):
        log_open = i == 1
        if log_open:
            self.tabs.setTabText(1, "Log")
        # The Log tab gets the whole window; the Packages tab shows everything else.
        self.panels.setVisible(not log_open)
        self.minilog.setVisible(not log_open)
        self._top_layout.setStretchFactor(self.tabs, 1 if log_open else 0)

    def _log_files(self, lines: str):
        """Per-file lines go to the full Log tab only, not the short panel at the bottom."""
        self.logv.appendPlainText(lines)
        if self.tabs.currentIndex() != 1 and not self.tabs.tabText(1).endswith("⚠"):
            self.tabs.setTabText(1, "Log •")

    def log(self, s: str):
        self.logv.appendPlainText(s)
        # The bottom panel shows the latest lines only (no tracebacks).
        if not s.startswith(("Traceback", "  ")):
            self.minilog.appendPlainText(s.splitlines()[0] if s else s)
        if s.startswith("ERROR") and self.tabs.currentIndex() != 1:
            self.tabs.setTabText(1, "Log ⚠")
        elif self.tabs.currentIndex() != 1 and not self.tabs.tabText(1).endswith("⚠"):
            self.tabs.setTabText(1, "Log •")

    def update_free(self):
        d = self.out_edit.text().strip() or (os.path.dirname(self.jobs[0].path) if self.jobs else "")
        if d and os.path.isdir(d):
            try:
                self.s_free.set(human(shutil.disk_usage(d).free))
                return
            except OSError:
                pass
        self.s_free.set("—")

    # -- queue -----------------------------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        files = []
        for p in paths:
            if os.path.isdir(p):
                for dp, _, fn in os.walk(p):
                    files += [os.path.join(dp, f) for f in fn if f.lower().endswith(".pkg")]
            elif p.lower().endswith(".pkg"):
                files.append(p)
        self.add_files(files)

    def add_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Choose PS5 packages", "", "PS5 packages (*.pkg);;All files (*)", options=FILE_DIALOG_OPTS)
        self.add_files(files)

    def add_files(self, files):
        for f in files:
            f = os.path.normpath(f)
            if any(j.path == f for j in self.jobs):
                continue
            job = Job(f)
            self.jobs.append(job)
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, COL_GAME, QTableWidgetItem("…"))
            self.table.setItem(r, COL_FILE, QTableWidgetItem(os.path.basename(f)))
            try:
                sz = human(os.path.getsize(f))
            except OSError:
                sz = "?"
            self.table.setItem(r, COL_SIZE, QTableWidgetItem(sz))
            self.table.setItem(r, COL_EST, QTableWidgetItem("…"))
            self.table.setItem(r, COL_STATUS, QTableWidgetItem(job.status))
            self._inspect(job)
        self.update_free()

    def _inspect(self, job: Job):
        th = QThread(self)
        w = InspectWorker(job)
        w.moveToThread(th)
        th.started.connect(w.run)
        w.done.connect(self._inspected)
        w.done.connect(th.quit)
        th.finished.connect(lambda: self._threads.remove((th, w)) if (th, w) in self._threads else None)
        self._threads.append((th, w))
        th.start()

    def _row(self, job: Job) -> int:
        return self.jobs.index(job) if job in self.jobs else -1

    def _set(self, job, col, text, color=None):
        r = self._row(job)
        if r < 0:
            return
        it = self.table.item(r, col)
        it.setText(text)
        if color:
            it.setForeground(QColor(color))

    def _inspected(self, job: Job, info, err: str):
        if job not in self.jobs:
            return
        if err:
            job.error = err
            job.status = "Unsupported: " + err
            self._set(job, COL_GAME, "—")
            self._set(job, COL_EST, "—")
            self._set(job, COL_STATUS, job.status, "#ff6b81")
            self.log(f"{os.path.basename(job.path)}: {err}")
            return
        job.info = info
        name = info["title"] or info["content_id"] or os.path.basename(job.path)
        if info["title_id"] and info["title"]:
            name = f"{info['title']}  [{info['title_id']}]"
        self._set(job, COL_GAME, name)
        b = info["blocks"]
        job.status = f"Ready — {len(info['files']) + len(info['cnt_files'])} files, {human(info['total'])} unpacked"
        self._set(job, COL_STATUS, job.status, "#5fd3a5")
        self._start_estimate(job)
        self.log(f"{os.path.basename(job.path)}: {info['content_id']}  {info['title']}  "
                 f"{human(info['total'])} in {len(info['files'])} files  (blocks: {b['kraken']} Kraken, {b['stored']} stored, {b['sparse']} sparse)")

    # -- size estimates ------------------------------------------------------
    def _start_estimate(self, job: Job):
        level = self.level.value()

        def work():
            try:
                if job.sample is None:
                    job.sample = sample_package(job.path)
                r = estimate_ratio(job.sample, level)
                self._est.ready.emit(job, level, r, "")
            except Exception as e:  # noqa: BLE001
                self._est.ready.emit(job, level, 0.0, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _refresh_estimates(self):
        level = self.level.value()
        for job in self.jobs:
            if not job.info:
                continue
            if level in job.ratios:
                self._show_estimate(job, level)
            else:
                self._set(job, COL_EST, "…")
                self._start_estimate(job)
        self._update_est_label()

    def _estimate_ready(self, job: Job, level: int, ratio: float, err: str):
        if job not in self.jobs:
            return
        if err:
            self._set(job, COL_EST, "?")
            self.log(f"{os.path.basename(job.path)}: size estimate failed: {err}")
            return
        job.ratios[level] = ratio
        if level == self.level.value():
            self._show_estimate(job, level)
        self._update_est_label()

    def _est_bytes(self, job: Job, level: int):
        r = job.ratios.get(level)
        if r is None or not job.info:
            return None
        files = len(job.info["files"]) + len(job.info["cnt_files"])
        return estimate_size(job.info["total"], r, files)

    def _show_estimate(self, job: Job, level: int):
        b = self._est_bytes(job, level)
        if b is not None:
            self._set(job, COL_EST, f"~{human(b)}")

    def _update_est_label(self):
        level = self.level.value()
        ready = [(j, self._est_bytes(j, level)) for j in self.jobs if j.info]
        ready = [(j, b) for j, b in ready if b is not None]
        if not ready:
            return
        est = sum(b for _, b in ready)
        pkg = sum(j.info["pkg_size"] for j, _ in ready)
        what = "this package" if len(ready) == 1 else f"{len(ready)} packages"
        self.est_label.setText(f"Estimated .zar for {what}: ~{human(est)}  ({est / pkg * 100:.0f}% of the .pkg size)")

    def remove_selected(self):
        if self.worker:
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
            del self.jobs[r]
        self.update_free()

    def clear_jobs(self):
        if self.worker:
            return
        self.table.setRowCount(0)
        self.jobs.clear()
        self.update_free()

    def pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Save .zar archives to", self.out_edit.text() or "", options=FILE_DIALOG_OPTS | QFileDialog.Option.ShowDirsOnly)
        if d:
            self.out_edit.setText(os.path.normpath(d))

    def _output_for(self, job: Job) -> str:
        d = self.out_edit.text().strip() or os.path.dirname(job.path)
        info = job.info or {}
        if info.get("title_id") and info.get("title"):
            base = safe_filename(f"{info['title_id']} - {info['title']}")
        else:
            base = safe_filename(os.path.splitext(os.path.basename(job.path))[0])
        return os.path.join(d, base + ".zar")

    def open_output(self):
        d = self.out_edit.text().strip() or (os.path.dirname(self.jobs[0].path) if self.jobs else "")
        if d and os.path.isdir(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    # -- run -------------------------------------------------------------------
    def start(self):
        todo = [j for j in self.jobs if j.info and not j.status.startswith("Done")]
        if not todo:
            show_info(self, "Add at least one readable .pkg first.")
            return
        out_dir = self.out_edit.text().strip()
        if out_dir and not os.path.isdir(out_dir):
            show_warning(self, "The output folder does not exist.")
            return
        existing = []
        for j in todo:
            j.output = self._output_for(j)
            if os.path.exists(j.output):
                existing.append(j.output)
        if existing and not self.chk_over.isChecked():
            show_warning(self, "These archives already exist (tick Overwrite to replace them):\n\n" + "\n".join(existing))
            return
        opts = Options(level=self.level.value(), threads=self.threads.value(), include_sce_sys_meta=self.chk_meta.isChecked(),
                       verify=self.chk_verify.isChecked(), overwrite=self.chk_over.isChecked())
        self.worker = ConvertWorker(todo, out_dir, opts)
        th = QThread(self)
        self.worker.moveToThread(th)
        th.started.connect(self.worker.run)
        self.worker.progress.connect(self._progress)
        self.worker.log.connect(self.log)
        self.worker.files.connect(self._log_files)
        self.worker.job_started.connect(self._job_started)
        self.worker.job_finished.connect(self._job_finished)
        self.worker.all_done.connect(self._all_done)
        self.worker.all_done.connect(th.quit)
        self._run_thread = th
        self._busy(True)
        th.start()

    def _busy(self, busy: bool):
        self.btn_start.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        for b in (self.btn_add, self.btn_remove, self.btn_clear):
            b.setEnabled(not busy)

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.btn_cancel.setEnabled(False)
            self.log("Cancelling…")

    def _job_started(self, job: Job):
        self._out_dir = os.path.dirname(job.output) or "."
        self._free_at = 0.0
        self._set(job, COL_STATUS, "Converting…", "#4da3ff")
        self.log(f"--- {os.path.basename(job.path)} -> {job.output}")
        self.bar.setValue(0)

    def _job_finished(self, job: Job, ok: bool, msg: str):
        job.status = ("Done — " + msg) if ok else msg
        self._set(job, COL_STATUS, job.status, "#5fd3a5" if ok else "#ff6b81")

    def _all_done(self):
        self._busy(False)
        self.worker = None
        self.bar.setFormat("Finished")
        self.cur_label.setText("")
        self.s_eta.set("—")

    def _progress(self, p: dict):
        frac = p["done"] / p["total"] if p["total"] else 0
        self.bar.setValue(int(frac * 1000))
        self.bar.setFormat(f"{p['phase']}  {frac * 100:.1f}%")
        self.cur_label.setText(p["cur"])
        self.s_done.set(f"{human(p['done'])} / {human(p['total'])}")
        if p["phase"] != "Verifying":
            self.s_out.set(human(p["out"]))
            if p["done"]:
                self.s_ratio.set(f"{p['out'] / p['done'] * 100:.1f}%")
        self.s_speed.set(f"{human(p['speed'])}/s")
        self.s_eta.set(fmt_eta(p["eta"]))
        # Free space at the output, refreshed every 2 seconds while converting.
        now = time.monotonic()
        if now - getattr(self, "_free_at", 0.0) >= 2.0 and getattr(self, "_out_dir", ""):
            self._free_at = now
            try:
                self.s_free.set(human(shutil.disk_usage(self._out_dir).free))
            except OSError:
                pass

    # -- frameless window: resize from the edges ---------------------------------
    _EDGE = 6

    def _edges_at(self, gpos):
        if self.isMaximized():
            return Qt.Edge(0)
        r = self.frameGeometry()
        x, y, m = gpos.x(), gpos.y(), self._EDGE
        e = Qt.Edge(0)
        if r.left() <= x < r.left() + m:
            e |= Qt.Edge.LeftEdge
        elif r.right() - m < x <= r.right():
            e |= Qt.Edge.RightEdge
        if r.top() <= y < r.top() + m:
            e |= Qt.Edge.TopEdge
        elif r.bottom() - m < y <= r.bottom():
            e |= Qt.Edge.BottomEdge
        return e

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress) and isinstance(obj, QWidget) and obj.window() is self:
            gpos = ev.globalPosition().toPoint()
            e = self._edges_at(gpos)
            if t == QEvent.Type.MouseMove and not ev.buttons():
                L, R, T, B = Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge
                if e in (L | T, R | B):
                    self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                elif e in (R | T, L | B):
                    self.setCursor(Qt.CursorShape.SizeBDiagCursor)
                elif e & (L | R):
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                elif e & (T | B):
                    self.setCursor(Qt.CursorShape.SizeVerCursor)
                else:
                    self.unsetCursor()
            elif t == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton and e:
                self.windowHandle().startSystemResize(e)
                return True
        return super().eventFilter(obj, ev)

    def changeEvent(self, e):
        if e.type() == QEvent.Type.WindowStateChange and hasattr(self, "titlebar"):
            self.titlebar.sync_max()
            # a thin edge so the window stays visible against other dark windows
            self.centralWidget().setProperty("maxed", self.isMaximized())
            self.centralWidget().style().unpolish(self.centralWidget())
            self.centralWidget().style().polish(self.centralWidget())
        super().changeEvent(e)

    def closeEvent(self, e):
        if self.worker:
            if not ask_yes_no(self, "A conversion is running. Cancel it and quit?"):
                e.ignore()
                return
            self.worker.cancel.set()
            self._run_thread.quit()
            self._run_thread.wait(15000)
        e.accept()


def main():
    if sys.platform == "win32":  # own taskbar entry and icon instead of python's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("zero.fPKG2Zar")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon = os.path.join(ASSETS_DIR, "icon.ico" if sys.platform == "win32" else "icon.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    w = MainWindow()
    if len(sys.argv) > 1:
        w.add_files([a for a in sys.argv[1:] if os.path.isfile(a)])
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
