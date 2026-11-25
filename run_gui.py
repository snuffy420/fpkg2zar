"""Entry point for the portable .exe and for `python run_gui.py`.

`fPKG2Zar.exe --selftest REPORT.txt [game.pkg]` checks the bundled decoder and
zstd without opening a window and writes the result to REPORT.txt. With a
package it also converts the package's sce_sys folder to a small test .zar
next to the report.
"""

import os
import sys


def selftest(report: str, pkg: str = "") -> int:
    lines = []
    ok = True
    try:
        from fpkg2zar import __version__, kraken
        lines.append(f"fPKG2Zar {__version__}")
        kraken.load()
        st, used, out = kraken.test_decode_bytes(bytes([0x80, 0x04, 9, 8, 7, 6]), 4)
        lines.append(f"kraken: ok ({st}, {used}, {out.hex()})")
        import zstandard
        lines.append(f"zstandard: {zstandard.__version__}")
        if pkg:
            from fpkg2zar.convert import Options, convert
            out_zar = os.path.join(os.path.dirname(os.path.abspath(report)), "selftest.zar")
            res = convert(pkg, out_zar, Options(only=("sce_sys", "eboot.bin"), verify=True, overwrite=True), log=lines.append)
            lines.append(f"convert: {res.files} files, verified={res.verified}")
    except Exception as e:  # noqa: BLE001
        ok = False
        lines.append(f"FAILED: {type(e).__name__}: {e}")
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n" + ("PASS\n" if ok else "FAIL\n"))
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        sys.exit(selftest(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""))
    from fpkg2zar.gui import main
    main()
