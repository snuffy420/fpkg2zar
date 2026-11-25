"""Builds the GitHub release files in releases/v<version>/.

  fPKG2Zar-<version>-windows.exe          the portable exe (build it first with build_exe.bat)
  fpkg2zar-v<version>-source.7z           the repo files from tools/make_upload.py
  <each file>.sha256                       SHA-256 checksum, in the "hash *name" format sha256sum -c reads

Run from the project folder:  py -3 tools\\make_release.py
Needs 7-Zip (7z.exe on PATH or in Program Files).
"""

import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from fpkg2zar import __version__  # noqa: E402

UPLOAD = os.path.join(HERE, "github_upload", "fpkg2zar")


def find_7z() -> str:
    for p in (shutil.which("7z"), r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if p and os.path.isfile(p):
            return p
    sys.exit("7-Zip not found. Install it from https://www.7-zip.org/ and run this again.")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    exe = os.path.join(HERE, "dist", f"fPKG2Zar-{__version__}-windows.exe")
    if not os.path.isfile(exe):
        sys.exit(f"{exe} is missing. Run build_exe.bat first.")
    out = os.path.join(HERE, "releases", f"v{__version__}")
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)

    subprocess.run([sys.executable, os.path.join(HERE, "tools", "make_upload.py")], check=True, stdout=subprocess.DEVNULL)
    src = os.path.join(out, f"fpkg2zar-v{__version__}-source.7z")
    # Archive the folder itself so the .7z unpacks into fpkg2zar/.
    subprocess.run([find_7z(), "a", "-t7z", "-mx=9", src, "fpkg2zar"], cwd=os.path.dirname(UPLOAD),
                   check=True, stdout=subprocess.DEVNULL)
    shutil.copy2(exe, out)

    for name in sorted(os.listdir(out)):
        path = os.path.join(out, name)
        digest = sha256_file(path)
        with open(path + ".sha256", "w", encoding="ascii", newline="\n") as f:
            f.write(f"{digest} *{name}\n")
        print(f"{digest}  {name}  ({os.path.getsize(path) / 1048576:.2f} MiB)")
    print("release files in", out)


if __name__ == "__main__":
    main()
