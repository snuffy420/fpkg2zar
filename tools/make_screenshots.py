"""Renders the README screenshots into assets/screenshots/ using MADE-UP data only.

No real package is opened: the games, title IDs, file names, sizes and log lines below are invented,
so the images show nothing from a real game. Run from the project folder:  py -3 tools\\make_screenshots.py
"""

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from PyQt6.QtCore import QEventLoop, Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication, QTabWidget, QTableWidgetItem  # noqa: E402

from fpkg2zar import gui  # noqa: E402
from fpkg2zar.convert import human  # noqa: E402

OUT = os.path.join(HERE, "assets", "screenshots")
GiB = 1 << 30

# (title, title id, package file name, .pkg size, unpacked size, files, est. ratio of unpacked)
FAKE = [
    ("Starfall Courier", "PPSA00001", "Starfall.Courier.PS5-PPSA00001-v01.000.000.pkg", 18.4 * GiB, 61.2 * GiB, 48211, 0.33),
    ("Moss Garden Odyssey", "PPSA00002", "Moss.Garden.Odyssey.PS5-PPSA00002-v01.002.000.pkg", 9.7 * GiB, 27.9 * GiB, 20544, 0.38),
    ("Neon Kart Rally", "PPSA00003", "Neon.Kart.Rally.PS5-PPSA00003-v01.000.001.pkg", 24.1 * GiB, 88.6 * GiB, 73102, 0.30),
]
FAKE_DIR = r"D:\Games\PS5"
FAKE_OUT = r"E:\ZAR"

FAKE_FILES = [
    ("eboot.bin", 38.2 * (1 << 20)), ("sce_sys/param.json", 6120), ("data/boot/startup.pak", 412 * (1 << 20)),
    ("data/levels/harbor_district.pak", 1.94 * GiB), ("data/levels/sky_market.pak", 1.61 * GiB),
    ("data/levels/old_lighthouse.pak", 1.12 * GiB), ("data/audio/music_main.bnk", 702 * (1 << 20)),
    ("data/audio/voice_en.bnk", 388 * (1 << 20)), ("data/ui/fonts.pak", 21.4 * (1 << 20)),
    ("data/ui/menus.pak", 96.0 * (1 << 20)), ("data/shaders/pipeline_cache.bin", 233 * (1 << 20)),
    ("data/movies/intro.bk2", 845 * (1 << 20)), ("data/movies/credits.bk2", 302 * (1 << 20)),
]


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main():
    os.makedirs(OUT, exist_ok=True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(gui.stylesheet())
    w = gui.MainWindow()
    # keep the user's own settings and folders out of the pictures
    w._ah_folder = ""
    w.chk_ahfix.blockSignals(True)
    w.chk_ahfix.setChecked(False)
    w.chk_ahfix.blockSignals(False)
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    w.resize(1500, 860)
    w.show()
    wait(900)
    w.logv.clear()
    w.minilog.clear()

    w.out_edit.setText(FAKE_OUT)
    w.level.setValue(15)
    jobs = []
    for i, (title, tid, fname, pkg, unpacked, files, ratio) in enumerate(FAKE):
        j = gui.Job(os.path.join(FAKE_DIR, fname))
        j.info = {"title": title, "title_id": tid, "content_id": f"UP0000-{tid}_00-FAKEFAKEFAKE0000", "total": int(unpacked),
                  "pkg_size": int(pkg), "files": [None] * files, "cnt_files": [], "blocks": {}}
        j.ratios[15] = ratio
        w.jobs.append(j)
        jobs.append(j)
        r = w.table.rowCount()
        w.table.insertRow(r)
        for col, text in ((gui.COL_GAME, f"{title}  [{tid}]"), (gui.COL_FILE, fname), (gui.COL_SIZE, human(pkg)),
                          (gui.COL_EST, f"~{human(unpacked * ratio)}"), (gui.COL_STATUS, "")):
            w.table.setItem(r, col, QTableWidgetItem(text))
    w._update_est_label()

    first = jobs[0]
    w._set(first, gui.COL_STATUS, "Done — 61.20 GiB -> 20.31 GiB (33.2%) in 11 min", "#5fd3a5")
    w._set(jobs[1], gui.COL_STATUS, "Converting…", "#4da3ff")
    w._set(jobs[2], gui.COL_STATUS, "Ready — 73102 files, 88.60 GiB unpacked", "#5fd3a5")

    w.log(f"fPKG2Zar {gui.__version__} ready. 16 CPU threads available.")
    for j in jobs:
        i = j.info
        w.log(f"{os.path.basename(j.path)}: {i['content_id']}  {i['title']}  {human(i['total'])} in {len(i['files'])} files")
    for j in jobs[:2]:
        w.log(f"--- {os.path.basename(j.path)} -> {FAKE_OUT}\\{j.info['title_id']} - {j.info['title']}.zar")
        w.log(f"Opening {j.path}")
        w.log(f"{len(j.info['files'])} files, {human(j.info['total'])} to archive")
        lines = [f"{human(s):>11}  {p}" for p, s in FAKE_FILES]
        w._log_files("\n".join(lines))
        if j is first:
            w.log("Done: 61.20 GiB -> 20.31 GiB (33.2%) in 11 min")

    w._busy(True)
    w._progress({"done": int(11.6 * GiB), "total": int(27.9 * GiB), "out": int(4.31 * GiB), "speed": 162.4 * (1 << 20),
                 "eta": 103, "phase": "Converting", "cur": "data/levels/harbor_district.pak"})
    w.s_free.set("812.40 GiB")
    wait(200)
    w.grab().save(os.path.join(OUT, "main.png"))

    w.tabs.setCurrentIndex(1)
    wait(200)
    w.logv.moveCursor(w.logv.textCursor().MoveOperation.Start)
    w.grab().save(os.path.join(OUT, "log.png"))
    w.tabs.setCurrentIndex(0)

    d = gui.AboutDialog(w)
    d.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    d.show()
    tabs = d.findChild(QTabWidget)
    for i, name in enumerate(("about", "help", "donate")):
        tabs.setCurrentIndex(i)
        wait(150)
        d.grab().save(os.path.join(OUT, f"{name}.png"))
    w._busy(False)
    print("screenshots in", OUT)
    os._exit(0)


if __name__ == "__main__":
    main()
