"""Optional helper: the community workaround for AstroHybrid's .zar loading bug.

AstroHybrid (https://github.com/imnotsharingmyname/pcportastrobot) ships .NET
support DLLs whose versions don't match the binding redirects in its
AstroHybrid.exe.config, so its zstd library fails to start and every .zar is
rejected with "The type initializer for 'ZstdSharp.Unsafe.Methods' threw an
exception" (its issues #272 and #283).

The workaround from issue #272 replaces four DLLs with these official
Microsoft packages from nuget.org. This module downloads them, checks each
package against the SHA-512 that NuGet publishes, backs up AstroHybrid's
originals (``<name>.dll.fpkg2zar-backup``) and swaps the files in. It can undo
the change from the backups.

Nothing here is part of converting a package; it only touches the AstroHybrid
folder the user picks.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import urllib.request
import zipfile
from dataclasses import dataclass, field

PACKAGES = (
    ("System.Memory", "4.6.0"),
    ("System.Buffers", "4.6.0"),
    ("System.Numerics.Vectors", "4.6.0"),
    ("System.Runtime.CompilerServices.Unsafe", "6.1.0"),
)
# Framework folders inside each package, best match first (AstroHybrid uses net462).
TFMS = ("net462", "net461", "net46", "net45", "netstandard2.0")
BACKUP_SUFFIX = ".fpkg2zar-backup"
NUGET = "https://api.nuget.org/v3"
USER_AGENT = "fPKG2Zar-astrohybrid-fix"


class FixError(Exception):
    pass


@dataclass
class Report:
    folder: str
    replaced: list = field(default_factory=list)      # paths changed this run
    already_ok: list = field(default_factory=list)    # paths that already had the fixed DLL
    missing: list = field(default_factory=list)       # DLL names not found in the folder
    restored: list = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.replaced:
            parts.append(f"replaced {len(self.replaced)} DLL(s)")
        if self.already_ok:
            parts.append(f"{len(self.already_ok)} already fixed")
        if self.restored:
            parts.append(f"restored {len(self.restored)} original DLL(s)")
        if self.missing:
            parts.append("not found: " + ", ".join(self.missing))
        return "; ".join(parts) or "nothing to do"


def cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    d = os.path.join(base, "fPKG2Zar", "nuget")
    os.makedirs(d, exist_ok=True)
    return d


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # HTTPS to api.nuget.org only
        return r.read()


def _published_sha512(pkg_id: str, version: str) -> bytes:
    """The package hash NuGet publishes in its catalog (SHA-512 of the .nupkg)."""
    reg = json.loads(_get(f"{NUGET}/registration5-semver1/{pkg_id.lower()}/{version}.json"))
    entry_url = reg.get("catalogEntry")
    if not isinstance(entry_url, str) or not entry_url.startswith("https://api.nuget.org/"):
        raise FixError(f"NuGet returned no catalog entry for {pkg_id} {version}")
    entry = json.loads(_get(entry_url))
    if entry.get("packageHashAlgorithm", "SHA512").upper() != "SHA512" or "packageHash" not in entry:
        raise FixError(f"NuGet returned no SHA-512 for {pkg_id} {version}")
    return base64.b64decode(entry["packageHash"])


def fetch_dll(pkg_id: str, version: str, log=lambda s: None) -> bytes:
    """Returns the DLL bytes from the official package, downloading and verifying it if needed."""
    name = f"{pkg_id.lower()}.{version}.nupkg"
    path = os.path.join(cache_dir(), name)
    want = None
    data = None
    if os.path.exists(path):
        data = open(path, "rb").read()
    if data is None:
        log(f"Downloading {pkg_id} {version} from nuget.org")
        data = _get(f"{NUGET}-flatcontainer/{pkg_id.lower()}/{version}/{name}", timeout=120)
    want = _published_sha512(pkg_id, version)
    if hashlib.sha512(data).digest() != want:
        try:
            os.remove(path)
        except OSError:
            pass
        raise FixError(f"{pkg_id} {version}: the download does not match the checksum NuGet publishes")
    if not os.path.exists(path):
        open(path, "wb").write(data)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = {n.lower(): n for n in z.namelist()}
        for tfm in TFMS:
            key = f"lib/{tfm}/{pkg_id}.dll".lower()
            if key in names:
                return z.read(names[key])
    raise FixError(f"{pkg_id} {version} has no .NET Framework DLL")


def find_targets(folder: str) -> dict:
    """Maps each DLL name to every copy of it inside the AstroHybrid folder (up to 4 levels deep)."""
    wanted = {f"{pid}.dll".lower(): f"{pid}.dll" for pid, _ in PACKAGES}
    found = {v: [] for v in wanted.values()}
    root_depth = folder.rstrip("\\/").count(os.sep)
    for dp, dirs, files in os.walk(folder):
        if dp.count(os.sep) - root_depth >= 4:
            dirs[:] = []
        for f in files:
            if f.lower() in wanted:
                found[wanted[f.lower()]].append(os.path.join(dp, f))
    return found


def looks_like_astrohybrid(folder: str) -> bool:
    try:
        names = {n.lower() for n in os.listdir(folder)}
    except OSError:
        return False
    return "astrohybrid.exe" in names or "astrohybrid.exe.config" in names or "astrohybrid" in names


def apply(folder: str, log=lambda s: None) -> Report:
    if not os.path.isdir(folder):
        raise FixError(f"{folder} is not a folder")
    rep = Report(folder)
    targets = find_targets(folder)
    for pid, ver in PACKAGES:
        dll = f"{pid}.dll"
        paths = targets[dll]
        if not paths:
            rep.missing.append(dll)
            continue
        new = fetch_dll(pid, ver, log)
        new_hash = hashlib.sha256(new).digest()
        for p in paths:
            cur = open(p, "rb").read()
            if hashlib.sha256(cur).digest() == new_hash:
                rep.already_ok.append(p)
                continue
            backup = p + BACKUP_SUFFIX
            if not os.path.exists(backup):
                shutil.copy2(p, backup)
            tmp = p + ".fpkg2zar-new"
            with open(tmp, "wb") as f:
                f.write(new)
            os.replace(tmp, p)
            rep.replaced.append(p)
            log(f"Replaced {os.path.relpath(p, folder)} with {pid} {ver}")
    return rep


def restore(folder: str, log=lambda s: None) -> Report:
    rep = Report(folder)
    for dp, _dirs, files in os.walk(folder):
        for f in files:
            if f.endswith(BACKUP_SUFFIX):
                b = os.path.join(dp, f)
                orig = b[: -len(BACKUP_SUFFIX)]
                os.replace(b, orig)
                rep.restored.append(orig)
                log(f"Restored the original {os.path.relpath(orig, folder)}")
    return rep
