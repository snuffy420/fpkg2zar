"""Copies the files that belong in the GitHub repo into github_upload/fpkg2zar/.

Only the listed files and folders are copied: source, assets, docs, scripts, LICENSE and the built
kraken.dll. Test folders, packages, archives, caches and build output are never included.
Run from the project folder:  py -3 tools\\make_upload.py
"""

import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(HERE, "github_upload", "fpkg2zar")

FILES = [
    "README.md", "CHANGELOG.md", "LICENSE", ".gitignore", ".gitattributes", "requirements.txt",
    "run_gui.py", "start.bat", "start.sh", "build_exe.bat",
]
DIRS = ["fpkg2zar", "native", "tests", "tools", "assets", "docs"]
SKIP_DIRS = {"__pycache__", ".venv", "build", "dist", "github_upload", "releases"}
SKIP_EXT = {".pyc", ".obj", ".exp", ".lib", ".pkg", ".zar", ".partial", ".spec"}


def main():
    if os.path.isdir(DEST):
        shutil.rmtree(DEST)
    os.makedirs(DEST)
    n = 0
    for f in FILES:
        shutil.copy2(os.path.join(HERE, f), os.path.join(DEST, f))
        n += 1
    for d in DIRS:
        for dp, dirs, files in os.walk(os.path.join(HERE, d)):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            rel = os.path.relpath(dp, HERE)
            os.makedirs(os.path.join(DEST, rel), exist_ok=True)
            for f in files:
                if os.path.splitext(f)[1].lower() in SKIP_EXT:
                    continue
                shutil.copy2(os.path.join(dp, f), os.path.join(DEST, rel, f))
                n += 1
    total = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(DEST) for f in fs)
    print(f"{n} files, {total / 1024:.0f} KiB -> {DEST}")
    for dp, _, fs in sorted(os.walk(DEST)):
        for f in sorted(fs):
            print("  " + os.path.relpath(os.path.join(dp, f), DEST))


if __name__ == "__main__":
    sys.exit(main())
