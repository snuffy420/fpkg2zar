"""PS5 .pkg -> .zar conversion, streamed.

Nothing is extracted to disk. The inner image's 256 KiB blocks are decoded
on a thread pool a little ahead of the writer, sliced into files in package
order, and fed straight to the ZArchive writer, which compresses 64 KiB
blocks on its own pool. Peak extra disk use is the finished .zar itself;
peak memory is a few hundred MiB regardless of game size.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from .errors import Cancelled, Fpkg2ZarError
from .pkg import Package
from .zarchive import ZArchiveReader, ZArchiveWriter


@dataclass
class Options:
    level: int = 15                # zstd level per 64 KiB block (15 = near-smallest output in reasonable time)
    threads: int = 0               # 0 = all cores
    include_sce_sys_meta: bool = True  # add param.json, icons etc. from the CNT into sce_sys/
    verify: bool = False           # re-read the archive and compare every file
    overwrite: bool = False
    work_dir: str = ""             # where the in-progress .partial file goes ("" = next to the output)
    only: tuple = ()               # archive only paths at or under these prefixes (testing)


@dataclass
class Progress:
    phase: str = ""
    done: int = 0
    total: int = 0
    out_bytes: int = 0
    current: str = ""
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def speed(self) -> float:
        e = self.elapsed
        return self.done / e if e > 0 else 0.0

    @property
    def eta(self) -> float:
        s = self.speed
        return (self.total - self.done) / s if s > 0 else 0.0


@dataclass
class Result:
    output: str
    files: int
    dirs: int
    input_bytes: int
    output_bytes: int
    pkg_bytes: int
    seconds: float
    verified: bool
    warnings: list


ProgressFn = Callable[[Progress], None]
LogFn = Callable[[str], None]


def human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TiB"


class _BlockPrefetcher:
    """Decodes inner-image blocks in the order they will be needed."""

    def __init__(self, pkg: Package, sequence, pool: ThreadPoolExecutor, window: int, cancel: threading.Event):
        self.pkg = pkg
        self.seq = iter(sequence)
        self.pool = pool
        self.window = window
        self.cancel = cancel
        self.q: deque = deque()
        self.last_idx = -1
        self.last_data = None
        self._fill()

    def _fill(self):
        while len(self.q) < self.window:
            try:
                i = next(self.seq)
            except StopIteration:
                return
            self.q.append((i, self.pool.submit(self.pkg.inner.decode_block, i)))

    def get(self, i: int):
        if i == self.last_idx:
            return self.last_data
        if self.cancel.is_set():
            raise Cancelled("cancelled")
        while self.q and self.q[0][0] != i:
            # The sequence and the consumer agree on order; anything ahead of
            # the request that does not match it is skipped (cannot normally happen).
            if self.q[0][0] > i:
                break
            self.q.popleft()
            self._fill()
        if self.q and self.q[0][0] == i:
            _, fut = self.q.popleft()
            data = fut.result()
            self._fill()
        else:
            data = self.pkg.inner.decode_block(i)
        self.last_idx, self.last_data = i, data
        return data

    def close(self):
        for _, f in self.q:
            f.cancel()
        self.q.clear()


def plan(pkg: Package, opts: Options):
    """Returns (dirs, inner_files_sorted, cnt_files, warnings)."""
    warnings = []
    files = sorted(pkg.files, key=lambda f: (f.logical, f.path))
    if opts.only:
        pre = [o.strip("/").lower() for o in opts.only]

        def keep(p):
            pl = p.lower()
            return any(pl == o or pl.startswith(o + "/") for o in pre)

        files = [f for f in files if keep(f.path)]
    seen = {f.path.lower() for f in files}
    cnt_files = []
    if opts.include_sce_sys_meta:
        for c in pkg.cnt.files:
            p = f"sce_sys/{c.name}"
            if p.lower() in seen:
                continue
            seen.add(p.lower())
            cnt_files.append((p, c.data))
    if pkg.cnt.skipped_encrypted:
        warnings.append(f"{len(pkg.cnt.skipped_encrypted)} encrypted CNT entries skipped: " + ", ".join(pkg.cnt.skipped_encrypted))
    dirs = set(pkg.dirs)
    if opts.only:
        dirs = set()
        for f in files:
            parts = f.path.split("/")[:-1]
            for k in range(1, len(parts) + 1):
                dirs.add("/".join(parts[:k]))
    for p, _ in cnt_files:
        parts = p.split("/")[:-1]
        for k in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:k]))
    return sorted(dirs, key=lambda d: (d.count("/"), d.lower())), files, cnt_files, warnings


def convert(pkg_path: str, out_path: str, opts: Optional[Options] = None, progress: Optional[ProgressFn] = None,
            log: Optional[LogFn] = None, cancel: Optional[threading.Event] = None,
            on_file: Optional[Callable[[str, int, str], None]] = None) -> Result:
    """on_file(path, size, how) is called after each file is written; how is
    "added", "linked" (same bytes as an earlier file, stored once) or "metadata"."""
    opts = opts or Options()
    cancel = cancel or threading.Event()
    log = log or (lambda s: None)
    progress = progress or (lambda p: None)
    on_file = on_file or (lambda path, size, how: None)
    threads = opts.threads or (os.cpu_count() or 4)
    t0 = time.monotonic()

    if os.path.exists(out_path) and not opts.overwrite:
        raise Fpkg2ZarError(f"{out_path} already exists")
    if os.path.abspath(out_path) == os.path.abspath(pkg_path):
        raise Fpkg2ZarError("the output would overwrite the input package")

    log(f"Opening {pkg_path}")
    pkg = Package.open(pkg_path)
    try:
        st = pkg.inner.stats
        log(f"Content ID {pkg.cnt.content_id}  {pkg.title()}".rstrip())
        log(f"Inner image: {human(pkg.inner.mount)} in {len(pkg.inner.blocks)} blocks "
            f"({st['kraken']} Kraken, {st['stored']} stored, {st['sparse']} sparse)")
        dirs, files, cnt_files, warnings = plan(pkg, opts)
        for w in warnings:
            log("Note: " + w)
        total = sum(f.size for f in files) + sum(len(d) for _, d in cnt_files)
        log(f"{len(files) + len(cnt_files)} files, {len(dirs)} folders, {human(total)} to archive")

        out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
        if not os.path.isdir(out_dir):
            raise Fpkg2ZarError(f"output folder {out_dir} does not exist")
        work_dir = os.path.abspath(opts.work_dir) if opts.work_dir else out_dir
        if not os.path.isdir(work_dir):
            raise Fpkg2ZarError(f"working folder {work_dir} does not exist")
        free = shutil.disk_usage(work_dir).free
        log(f"Free space at {'working folder' if opts.work_dir else 'destination'}: {human(free)}")
        if free < 64 << 20:
            raise Fpkg2ZarError("less than 64 MiB free where the archive is written")
        if opts.work_dir:
            log(f"Writing to {work_dir}, then moving to {out_dir}")

        prog = Progress(phase="Converting", total=total)
        last_emit = [0.0]

        def emit(force=False):
            now = time.monotonic()
            if force or now - last_emit[0] >= 0.1:
                last_emit[0] = now
                prog.out_bytes = writer.compressed_bytes
                progress(prog)

        tmp_path = os.path.join(work_dir, os.path.basename(out_path) + ".partial")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        writer = ZArchiveWriter(tmp_path, level=opts.level, threads=threads)
        hashes = {} if opts.verify else None
        decode_pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="kraken")
        pre = None
        try:
            for d in dirs:
                writer.make_dir(d)

            def sequence():
                last = -1
                keys = set()
                for f in files:
                    if f.size == 0 or (f.logical, f.size) in keys:
                        continue
                    keys.add((f.logical, f.size))
                    a = pkg.inner.find_block(f.logical)
                    b = pkg.inner.find_block(f.logical + f.size - 1)
                    for i in range(a, b + 1):
                        if i != last:
                            yield i
                            last = i

            pre = _BlockPrefetcher(pkg, sequence(), decode_pool, window=threads * 3, cancel=cancel)
            dedupe = {}       # (logical, size) -> stream offset of identical content (hard links)
            dedupe_path = {}  # (logical, size) -> first path with that content
            blocks = pkg.inner.blocks
            for f in files:
                if cancel.is_set():
                    raise Cancelled("cancelled")
                prog.current = f.path
                key = (f.logical, f.size)
                if f.size and key in dedupe:
                    writer.add_file_ref(f.path, dedupe[key], f.size)
                    if hashes is not None:
                        hashes[f.path] = hashes[dedupe_path[key]]
                    prog.done += f.size
                    on_file(f.path, f.size, "linked")
                    emit()
                    continue
                if f.size:
                    dedupe[key] = writer.input_offset
                    dedupe_path[key] = f.path
                writer.start_file(f.path)
                h = hashlib.sha1() if hashes is not None else None
                pos, end = f.logical, f.logical + f.size
                while pos < end:
                    i = pkg.inner.find_block(pos)
                    b = blocks[i]
                    data = pre.get(i)
                    w = pos - b.logical
                    take = min(b.uncomp - w, end - pos)
                    chunk = memoryview(data)[w:w + take]
                    writer.append(chunk)
                    if h:
                        h.update(chunk)
                    pos += take
                    prog.done += take
                    emit()
                if h:
                    hashes[f.path] = h.digest()
                on_file(f.path, f.size, "added")
            for p, data in cnt_files:
                prog.current = p
                writer.start_file(p)
                writer.append(data)
                if hashes is not None:
                    hashes[p] = hashlib.sha1(data).digest()
                prog.done += len(data)
                on_file(p, len(data), "metadata")
            prog.phase = "Finalizing"
            prog.current = ""
            emit(True)
            writer.finalize()
        except BaseException:
            if pre:
                pre.close()
            decode_pool.shutdown(wait=True, cancel_futures=True)
            writer.abort()
            raise
        decode_pool.shutdown(wait=True)

        verified = False
        if opts.verify:
            log("Verifying archive...")
            _verify(tmp_path, hashes, threads, cancel, progress, log)
            verified = True

        if os.path.exists(out_path):
            os.remove(out_path)
        try:
            os.replace(tmp_path, out_path)  # same drive: instant rename
        except OSError:
            log("Moving the archive to the destination drive...")
            prog = Progress(phase="Moving", total=os.path.getsize(tmp_path))
            _copy_with_progress(tmp_path, out_path + ".partial", prog, progress, cancel)
            os.replace(out_path + ".partial", out_path)
            os.remove(tmp_path)
        out_size = os.path.getsize(out_path)
        res = Result(out_path, len(files) + len(cnt_files), len(dirs), total, out_size, os.path.getsize(pkg_path),
                     time.monotonic() - t0, verified, warnings)
        ratio = (out_size / total * 100) if total else 0
        log(f"Done: {human(total)} -> {human(out_size)} ({ratio:.1f}%) in {res.seconds:.0f} s")
        return res
    finally:
        pkg.close()


def _copy_with_progress(src, dst, prog, progress, cancel):
    try:
        with open(src, "rb") as fi, open(dst, "wb") as fo:
            while True:
                if cancel.is_set():
                    raise Cancelled("cancelled")
                buf = fi.read(16 << 20)
                if not buf:
                    break
                fo.write(buf)
                prog.done += len(buf)
                progress(prog)
    except BaseException:
        try:
            os.remove(dst)
        except OSError:
            pass
        raise


def _verify(path, hashes, threads, cancel, progress, log):
    tmp = path
    r = ZArchiveReader(tmp)
    try:
        prog = Progress(phase="Verifying")
        listing = [(p, off, size) for p, is_file, off, size in r.walk() if is_file]
        prog.total = sum(s for _, _, s in listing)
        found = {p for p, _, _ in listing}
        missing = set(hashes) - found
        if missing:
            raise Fpkg2ZarError(f"verify: {len(missing)} files missing from the archive, e.g. {sorted(missing)[0]}")
        lock = threading.Lock()
        last = [0.0]

        def check(item):
            p, off, size = item
            if cancel.is_set():
                raise Cancelled("cancelled")
            h = hashlib.sha1()
            for chunk in r.iter_file(off, size):
                h.update(chunk)
                with lock:
                    prog.done += len(chunk)
                    now = time.monotonic()
                    if now - last[0] > 0.1:
                        last[0] = now
                        prog.current = p
                        progress(prog)
            if h.digest() != hashes.get(p):
                raise Fpkg2ZarError(f"verify: content mismatch in {p}")

        with ThreadPoolExecutor(max_workers=threads) as pool:
            for _ in pool.map(check, listing):
                pass
        if not r.check_integrity():
            raise Fpkg2ZarError("verify: archive SHA-256 does not match its footer")
        log(f"Verified {len(listing)} files and the archive checksum.")
    finally:
        r.close()


def sample_package(pkg_path: str, budget: int = 128 << 20, run: int = 1, threads: int = 0) -> list:
    """Decodes ~`budget` bytes spread evenly over the game image, as 64 KiB chunks.

    Runs of `run` consecutive 256 KiB blocks are taken at even intervals, so
    the sample covers every part of the package (textures, audio, video, code)
    in proportion to its size.
    """
    from .zarchive import BLOCK
    pkg = Package.open(pkg_path)
    try:
        inner = pkg.inner
        n = len(inner.blocks)
        per_run = run * (256 << 10)
        runs = max(1, min(n // max(run, 1), budget // per_run))
        # Evenly spaced by *bytes* of the image, not by block number: small
        # files make many short blocks, and counting blocks would over-sample them.
        idx = []
        seen = set()
        for r in range(runs):
            first = inner.find_block(int((r + 0.5) * inner.mount / runs))
            for i in range(first, min(first + run, n)):
                if i not in seen:
                    seen.add(i)
                    idx.append(i)
        idx.sort()
        with ThreadPoolExecutor(max_workers=threads or min(8, os.cpu_count() or 4)) as pool:
            datas = list(pool.map(inner.decode_block, idx))
        # Pack like the writer does: one stream, cut into 64 KiB blocks.
        stream = b"".join(bytes(d) for d in datas)
        return [stream[i:i + BLOCK] for i in range(0, len(stream) - BLOCK + 1, BLOCK)]
    finally:
        pkg.close()


def estimate_ratio(chunks: list, level: int, threads: int = 0) -> float:
    """Compressed/uncompressed ratio of a sample, exactly as the writer stores blocks."""
    import zstandard as zstd
    from .zarchive import BLOCK
    if not chunks:
        return 1.0
    tls = threading.local()

    def size(c):
        z = getattr(tls, "z", None)
        if z is None:
            z = tls.z = zstd.ZstdCompressor(level=level)
        return min(len(z.compress(c)), BLOCK)

    with ThreadPoolExecutor(max_workers=threads or (os.cpu_count() or 4)) as pool:
        total = sum(pool.map(size, chunks))
    return total / (len(chunks) * BLOCK)


def estimate_size(total_bytes: int, ratio: float, files: int) -> int:
    """Estimated .zar size: compressed data plus index overhead (~40 B per file/folder)."""
    return int(total_bytes * ratio) + files * 40 + 4096


def inspect(pkg_path: str) -> dict:
    pkg = Package.open(pkg_path)
    try:
        return {
            "content_id": pkg.cnt.content_id,
            "title": pkg.title(),
            "title_id": pkg.title_id(),
            "pkg_size": pkg.src.len,
            "inner_size": pkg.inner.mount,
            "blocks": dict(pkg.inner.stats),
            "files": [(f.path, f.size) for f in sorted(pkg.files, key=lambda f: f.path)],
            "cnt_files": [(f"sce_sys/{c.name}", len(c.data)) for c in pkg.cnt.files],
            "encrypted_cnt": list(pkg.cnt.skipped_encrypted),
            "total": pkg.total_size,
        }
    finally:
        pkg.close()
