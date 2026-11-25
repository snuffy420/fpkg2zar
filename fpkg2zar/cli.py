"""Command line: python -m fpkg2zar game.pkg [-o game.zar]"""

from __future__ import annotations

import argparse
import os
import sys
import threading

from . import __version__
from .convert import Options, convert, estimate_ratio, estimate_size, human, inspect, sample_package
from .errors import Cancelled, Fpkg2ZarError


def _bar(p) -> str:
    frac = p.done / p.total if p.total else 0
    w = 30
    fill = int(frac * w)
    eta = p.eta
    eta_s = f"{int(eta // 3600)}:{int(eta % 3600 // 60):02d}:{int(eta % 60):02d}" if eta else "--:--:--"
    return (f"\r{p.phase:<10} [{'#' * fill}{'.' * (w - fill)}] {frac * 100:5.1f}%  "
            f"{human(p.done)} / {human(p.total)}  {human(p.speed)}/s  ETA {eta_s}  out {human(p.out_bytes)}   ")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fpkg2zar", description="Convert a PS5 fake package (.pkg) to a ZArchive (.zar) without extracting it first.")
    ap.add_argument("input", help="PS5 .pkg file")
    ap.add_argument("-o", "--output", help="output .zar (default: next to the package)")
    ap.add_argument("-l", "--level", type=int, default=15, help="zstd level 1-22 (default 15: near-smallest output; zarchive.exe uses 6)")
    ap.add_argument("-j", "--threads", type=int, default=0, help="worker threads (default: all cores)")
    ap.add_argument("--verify", action="store_true", help="re-read the archive and compare every file afterwards")
    ap.add_argument("--no-sce-sys", action="store_true", help="do not add param.json/icons from the package metadata into sce_sys/")
    ap.add_argument("-f", "--force", action="store_true", help="overwrite an existing output file")
    ap.add_argument("--work-dir", default="", help="write the in-progress archive here, then move it to the output (optional)")
    ap.add_argument("--only", action="append", default=[], metavar="PATH", help="archive only files at or under PATH (repeatable; for testing)")
    ap.add_argument("--list", action="store_true", help="list the package contents and exit")
    ap.add_argument("-V", "--version", action="version", version=f"fpkg2zar {__version__}")
    a = ap.parse_args(argv)

    try:
        if a.list:
            info = inspect(a.input)
            print(f"Content ID : {info['content_id']}")
            print(f"Title      : {info['title']}")
            print(f"Package    : {human(info['pkg_size'])}   inner image {human(info['inner_size'])}   blocks {info['blocks']}")
            for p, s in info["files"]:
                print(f"{s:>16}  {p}")
            for p, s in info["cnt_files"]:
                print(f"{s:>16}  {p}  (package metadata)")
            for p in info["encrypted_cnt"]:
                print(f"{'-':>16}  sce_sys/{p}  (encrypted, skipped)")
            nfiles = len(info['files']) + len(info['cnt_files'])
            print(f"{nfiles} files, {human(info['total'])}")
            r = estimate_ratio(sample_package(a.input), a.level)
            est = estimate_size(info["total"], r, nfiles)
            print(f"Estimated .zar at level {a.level}: ~{human(est)} ({est / info['pkg_size'] * 100:.0f}% of the .pkg), within about 5%")
            return 0

        out = a.output or os.path.splitext(a.input)[0] + ".zar"
        opts = Options(level=a.level, threads=a.threads, include_sce_sys_meta=not a.no_sce_sys, verify=a.verify, overwrite=a.force,
                       work_dir=a.work_dir, only=tuple(a.only))
        cancel = threading.Event()
        tty = sys.stdout.isatty()

        def prog(p):
            if tty:
                sys.stdout.write(_bar(p))
                sys.stdout.flush()

        def log(s):
            if tty:
                sys.stdout.write("\r" + " " * 140 + "\r")
            print(s, flush=True)

        try:
            convert(a.input, out, opts, progress=prog, log=log, cancel=cancel)
        except KeyboardInterrupt:
            cancel.set()
            print("\nCancelled; the partial archive was removed.")
            return 130
        return 0
    except Cancelled:
        print("\nCancelled.")
        return 130
    except (Fpkg2ZarError, OSError, RuntimeError) as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
