"""ZArchive (.zar) writer and reader.

A reimplementation of Exzap's ZArchive format (https://github.com/Exzap/ZArchive,
MIT-0), byte-compatible with zarchive.exe and with emulators that read .zar.

Layout: all file contents are concatenated into one logical stream, cut into
64 KiB blocks, each zstd-compressed (or stored raw when compression does not
help). After the data come the offset records, the name table, the file tree
(breadth-first, children sorted case-insensitively) and a 144-byte footer that
carries a SHA-256 over the whole archive. Everything is big-endian.

The writer compresses blocks on a thread pool and writes them in order, so the
output is identical to a single-threaded build.
"""

from __future__ import annotations

import hashlib
import os
import struct
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover
    zstd = None

BLOCK = 64 * 1024
ENTRIES_PER_OFFSETRECORD = 16
MAGIC = 0x169F52D6
VERSION1 = 0x61BF3A01
FOOTER_SIZE = 16 * 6 + 32 + 8 + 4 + 4
SYNC_EVERY = 1 << 30  # flush the output to disk about every 1 GiB


def _require_zstd():
    if zstd is None:
        raise RuntimeError("The 'zstandard' package is missing. Install it with:  pip install zstandard")


def _fold(name: bytes) -> bytes:
    """ZArchive compares names ASCII-case-insensitively."""
    return name.translate(_LOWER)


_LOWER = bytes(c + 32 if 65 <= c <= 90 else c for c in range(256))


class _Node:
    __slots__ = ("name", "is_file", "children", "index", "offset", "size", "start_index")

    def __init__(self, name: bytes, is_file: bool):
        self.name = name
        self.is_file = is_file
        self.children = {} if not is_file else None  # folded name -> node
        self.offset = 0
        self.size = 0
        self.start_index = 0


def _split(path: str):
    return [p for p in path.replace("\\", "/").split("/") if p]


class ZArchiveWriter:
    def __init__(self, path: str, level: int = 6, threads: int | None = None):
        _require_zstd()
        self.path = path
        self.level = level
        self.threads = max(1, threads or (os.cpu_count() or 4))
        self._fh = open(path, "wb")
        self._sha = hashlib.sha256()
        self._written = 0
        self._last_sync = 0
        self._root = _Node(b"", False)
        self._current: _Node | None = None
        self._input_offset = 0
        self._buf = bytearray()
        self._records: list = []  # [base_offset, [16 sizes]]
        self._num_blocks = 0
        self._pool = ThreadPoolExecutor(max_workers=self.threads, thread_name_prefix="zstd")
        self._pending: deque = deque()
        self._window = self.threads * 4
        self._tls = threading.local()
        self._closed = False

    # -- tree ----------------------------------------------------------------
    def _dir(self, parts, create: bool) -> _Node:
        node = self._root
        for p in parts:
            key = _fold(p.encode("utf-8"))
            child = node.children.get(key)
            if child is None:
                if not create:
                    raise FileNotFoundError("/".join(parts))
                child = _Node(p.encode("utf-8"), False)
                node.children[key] = child
            elif child.is_file:
                raise FileExistsError(f"{p} is a file, not a directory")
            node = child
        return node

    def make_dir(self, path: str) -> None:
        self._dir(_split(path), create=True)

    def _new_file_node(self, path: str) -> _Node:
        parts = _split(path)
        if not parts:
            raise ValueError("empty file path")
        d = self._dir(parts[:-1], create=True)
        key = _fold(parts[-1].encode("utf-8"))
        if key in d.children:
            raise FileExistsError(path)
        n = _Node(parts[-1].encode("utf-8"), True)
        d.children[key] = n
        return n

    def start_file(self, path: str) -> None:
        n = self._new_file_node(path)
        n.offset = self._input_offset
        self._current = n

    def add_file_ref(self, path: str, offset: int, size: int) -> None:
        """Adds a file whose bytes already exist in the stream (duplicate content)."""
        n = self._new_file_node(path)
        n.offset, n.size = offset, size
        self._current = None

    @property
    def input_offset(self) -> int:
        return self._input_offset

    # -- data ----------------------------------------------------------------
    def append(self, data) -> None:
        mv = memoryview(data)
        n = len(mv)
        if not n:
            return
        if self._current is not None:
            self._current.size += n
        self._input_offset += n
        pos = 0
        if self._buf:
            take = min(BLOCK - len(self._buf), n)
            self._buf += mv[:take]
            pos = take
            if len(self._buf) == BLOCK:
                self._submit(bytes(self._buf))
                self._buf.clear()
        while n - pos >= BLOCK:
            self._submit(bytes(mv[pos:pos + BLOCK]))
            pos += BLOCK
        if pos < n:
            self._buf += mv[pos:]

    def _compressor(self):
        c = getattr(self._tls, "c", None)
        if c is None:
            c = zstd.ZstdCompressor(level=self.level)
            self._tls.c = c
        return c

    def _compress(self, block: bytes) -> bytes:
        out = self._compressor().compress(block)
        return block if len(out) >= BLOCK else out

    def _submit(self, block: bytes) -> None:
        self._pending.append(self._pool.submit(self._compress, block))
        while len(self._pending) >= self._window:
            self._drain_one()

    def _drain_one(self) -> None:
        data = self._pending.popleft().result()
        offset = self._written
        self._out(data)
        if self._num_blocks % ENTRIES_PER_OFFSETRECORD == 0:
            self._records.append([offset, [0] * ENTRIES_PER_OFFSETRECORD])
        self._records[-1][1][self._num_blocks % ENTRIES_PER_OFFSETRECORD] = len(data) - 1
        self._num_blocks += 1

    def _out(self, data) -> None:
        self._fh.write(data)
        self._sha.update(data)
        self._written += len(data)
        if self._written - self._last_sync >= SYNC_EVERY:
            # Push data to disk so the size shown in Explorer keeps up during a
            # long conversion (Windows otherwise may show 0 KB until the end).
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._last_sync = self._written

    @property
    def compressed_bytes(self) -> int:
        return self._written

    # -- finalize ------------------------------------------------------------
    def finalize(self) -> None:
        self._current = None
        if self._buf:
            self._buf += bytes(BLOCK - len(self._buf))
            self._submit(bytes(self._buf))
            self._buf.clear()
        while self._pending:
            self._drain_one()
        self._pool.shutdown(wait=True)
        data_size = self._written
        while self._written % 8:
            self._out(b"\0")

        # offset records
        rec_off = self._written
        parts = []
        for base, sizes in self._records:
            parts.append(struct.pack(">Q16H", base, *sizes))
        self._out(b"".join(parts))
        rec_size = self._written - rec_off

        # names: one entry per distinct name, in first-use (BFS) order
        name_index: dict = {}
        names: list = []
        order = []
        q = deque([self._root])
        while q:
            node = q.popleft()
            order.append(node)
            if not node.is_file:
                kids = sorted(node.children.values(), key=lambda c: _fold(c.name))
                node.children = kids  # type: ignore[assignment]
                q.extend(kids)
        for node in order[1:]:
            if node.name not in name_index:
                name_index[node.name] = len(names)
                names.append(node.name)
        names_off = self._written
        name_offsets = []
        cur = 0
        blob = bytearray()
        for nm in names:
            nm = nm[:0x7FFF]
            name_offsets.append(cur)
            if len(nm) >= 0x80:
                blob += bytes(((len(nm) & 0x7F) | 0x80, len(nm) >> 7))
                cur += 2
            else:
                blob.append(len(nm))
                cur += 1
            blob += nm
            cur += len(nm)
        self._out(bytes(blob))
        names_size = self._written - names_off

        # file tree, breadth-first
        idx = 1
        for node in order:
            if not node.is_file:
                node.start_index = idx
                idx += len(node.children)
        tree_off = self._written
        tree = bytearray()
        for node in order:
            name_off = 0x7FFFFFFF if node is self._root else name_offsets[name_index[node.name]]
            if node.is_file:
                if node.offset >= (1 << 48) or node.size >= (1 << 48):
                    raise ValueError("file offset or size exceeds 48 bits")
                hi = ((node.size >> 32) & 0xFFFF) << 16 | ((node.offset >> 32) & 0xFFFF)
                tree += struct.pack(">IIII", 0x80000000 | name_off, node.offset & 0xFFFFFFFF, node.size & 0xFFFFFFFF, hi)
            else:
                tree += struct.pack(">IIII", name_off & 0x7FFFFFFF, node.start_index, len(node.children), 0)
        self._out(bytes(tree))
        tree_size = self._written - tree_off
        meta = self._written

        def footer(h: bytes) -> bytes:
            return struct.pack(
                ">QQQQQQQQQQQQ32sQII",
                0, data_size, rec_off, rec_size, names_off, names_size, tree_off, tree_size,
                meta, 0, meta, 0, h, self._written + FOOTER_SIZE, VERSION1, MAGIC,
            )

        self._sha.update(footer(bytes(32)))
        digest = self._sha.digest()
        f = footer(digest)
        self._fh.write(f)
        self._written += len(f)
        self._fh.close()
        self._closed = True

    def abort(self) -> None:
        """Stops work and deletes the partial archive."""
        for fut in self._pending:
            fut.cancel()
        self._pending.clear()
        self._pool.shutdown(wait=True, cancel_futures=True)
        if not self._closed:
            try:
                self._fh.close()
            except OSError:
                pass
            try:
                os.remove(self.path)
            except OSError:
                pass
            self._closed = True


class ZArchiveReader:
    """Minimal reader, used to verify archives and in tests."""

    def __init__(self, path: str):
        _require_zstd()
        self._fh = open(path, "rb")
        self._lock = threading.Lock()
        self._fh.seek(0, os.SEEK_END)
        size = self._fh.tell()
        if size < FOOTER_SIZE:
            raise ValueError("file too small to be a ZArchive")
        self._fh.seek(size - FOOTER_SIZE)
        f = self._fh.read(FOOTER_SIZE)
        v = struct.unpack(">QQQQQQQQQQQQ32sQII", f)
        (self.data_off, self.data_size, rec_off, rec_size, names_off, names_size, tree_off, tree_size,
         _mdo, _mds, _mo, _ms, self.hash, total, version, magic) = v
        if magic != MAGIC or version != VERSION1:
            raise ValueError("not a ZArchive (bad footer magic)")
        if total != size:
            raise ValueError(f"footer says {total} bytes but the file is {size}")
        self.size = size
        recs = self._read(rec_off, rec_size)
        self.records = [struct.unpack_from(">Q16H", recs, i) for i in range(0, len(recs), 40)]
        self.names = self._read(names_off, names_size)
        tree = self._read(tree_off, tree_size)
        self.entries = [struct.unpack_from(">IIII", tree, i) for i in range(0, len(tree), 16)]
        self._dctx = threading.local()

    def _read(self, off, n):
        with self._lock:
            self._fh.seek(off)
            return self._fh.read(n)

    def _name(self, off):
        b0 = self.names[off]
        if b0 & 0x80:
            ln = (b0 & 0x7F) | (self.names[off + 1] << 7)
            off += 2
        else:
            ln = b0
            off += 1
        return self.names[off:off + ln].decode("utf-8", "replace")

    def walk(self):
        """Yields (path, is_file, offset, size)."""
        stack = [(0, "")]
        while stack:
            i, path = stack.pop()
            e = self.entries[i]
            if e[0] & 0x80000000:
                off = e[1] | ((e[3] & 0xFFFF) << 32)
                size = e[2] | ((e[3] & 0xFFFF0000) << 16)
                yield path, True, off, size
            else:
                if i != 0:
                    yield path, False, 0, 0
                for c in range(e[1], e[1] + e[2]):
                    nm = self._name(self.entries[c][0] & 0x7FFFFFFF)
                    stack.append((c, f"{path}/{nm}" if path else nm))

    def _block(self, bi: int) -> bytes:
        rec = self.records[bi // ENTRIES_PER_OFFSETRECORD]
        j = bi % ENTRIES_PER_OFFSETRECORD
        off = rec[0] + sum(s + 1 for s in rec[1:1 + j])
        size = rec[1 + j] + 1
        raw = self._read(self.data_off + off, size)
        if size == BLOCK:
            return raw
        d = getattr(self._dctx, "d", None)
        if d is None:
            d = zstd.ZstdDecompressor()
            self._dctx.d = d
        return d.decompress(raw, max_output_size=BLOCK)

    def iter_file(self, offset: int, size: int):
        pos, end = offset, offset + size
        while pos < end:
            bi, w = divmod(pos, BLOCK)
            blk = self._block(bi)
            take = min(BLOCK - w, end - pos)
            yield blk[w:w + take]
            pos += take

    def read_file(self, offset: int, size: int) -> bytes:
        return b"".join(self.iter_file(offset, size))

    def check_integrity(self) -> bool:
        h = hashlib.sha256()
        with self._lock:
            self._fh.seek(0)
            remaining = self.size - FOOTER_SIZE
            while remaining:
                chunk = self._fh.read(min(remaining, 8 << 20))
                h.update(chunk)
                remaining -= len(chunk)
            footer = bytearray(self._fh.read(FOOTER_SIZE))
        footer[96:128] = bytes(32)
        h.update(bytes(footer))
        return h.digest() == self.hash

    def close(self):
        self._fh.close()
