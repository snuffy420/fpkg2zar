"""Reader for PS5 debug / fake packages (`\\x7FFIH`).

Layers, outermost first:

* FIH header (little-endian): offsets of the PFS segment and the CNT.
* CNT container (big-endian): param.json, icons and other sce_sys metadata.
* Outer PFS (plaintext, seed `PPRPLAIN-NOAUTH!`): holds `pfs_image.dat`
  and `naps_pkg_layout.dat`.
* NAPS layout: maps each 256 KiB logical block of the inner image to its
  stored bytes (raw or Kraken-compressed) inside `pfs_image.dat`.
* Inner PFS (flat mode 0x10): the game's file tree under `uroot/`.

The format knowledge follows pkg-to-anyps5 (GPL-3.0-or-later,
https://github.com/awake-devel/pkg-to-anyps5), PS5PCEM and LibProsperoPKG.
"""

from __future__ import annotations

import bisect
import os
import struct
import threading
from dataclasses import dataclass, field

from . import kraken
from .errors import FormatError, UnsupportedError

FIH_MAGIC = b"\x7fFIH"
CNT_MAGIC = b"\x7fCNT"
PFS_MAGIC = 20130315
PLAIN_NOAUTH_SEED = b"PPRPLAIN-NOAUTH!"
UBLOCK_SIZE = 0x40000
OUTER_BLOCK_SIZE = 0x10000
KIND_MOUNT = 0x40
DIRENT_FILE = 2
DIRENT_DIR = 3


def le16(b, o): return struct.unpack_from("<H", b, o)[0]
def le32(b, o): return struct.unpack_from("<I", b, o)[0]
def le64(b, o): return struct.unpack_from("<Q", b, o)[0]
def be32(b, o): return struct.unpack_from(">I", b, o)[0]
def be64(b, o): return struct.unpack_from(">Q", b, o)[0]


def c_str(b: bytes) -> bytes:
    n = b.find(b"\0")
    return b if n < 0 else b[:n]


# --------------------------------------------------------------------------
# Source: positional reads, one file handle per thread.

class Source:
    def __init__(self, path: str):
        self.path = path
        self.len = os.path.getsize(path)
        self._local = threading.local()
        self._all = []
        self._lock = threading.Lock()

    def _fh(self):
        fh = getattr(self._local, "fh", None)
        if fh is None:
            fh = open(self.path, "rb", buffering=0)
            self._local.fh = fh
            with self._lock:
                self._all.append(fh)
        return fh

    def read_into(self, buf, offset: int) -> None:
        n = len(buf)
        if offset < 0 or offset + n > self.len:
            raise FormatError(f"read of {n:#x} bytes at {offset:#x} is past the end of the {self.len:#x}-byte package")
        fh = self._fh()
        fh.seek(offset)
        mv = memoryview(buf)
        done = 0
        while done < n:
            got = fh.readinto(mv[done:])
            if not got:
                raise FormatError(f"unexpected end of file at {offset + done:#x}")
            done += got

    def read(self, n: int, offset: int) -> bytes:
        buf = bytearray(n)
        self.read_into(buf, offset)
        return bytes(buf)

    def close(self):
        with self._lock:
            for fh in self._all:
                try:
                    fh.close()
                except OSError:
                    pass
            self._all.clear()
        self._local = threading.local()


# --------------------------------------------------------------------------
# FIH + CNT

@dataclass
class Fih:
    format_version: int
    pfs_offset: int
    pfs_size: int
    superblock_offset: int
    cnt_offset: int

    @staticmethod
    def read(src: Source) -> "Fih":
        if src.len < 0x100:
            raise FormatError("file is too small to be a PS5 package")
        h = src.read(0x100, 0)
        if h[:4] != FIH_MAGIC:
            if h[:4] == CNT_MAGIC:
                raise UnsupportedError("this is a PS4 package. fPKG2Zar only converts PS5 fake packages; "
                                       "PS4 packages are encrypted and are not supported")
            if h[:4] == b"\x7fPKG":
                raise UnsupportedError("this is a PS3 package. fPKG2Zar only converts PS5 fake packages; "
                                       "PS3 packages are encrypted and are not supported")
            raise FormatError(f"not a PS5 package (magic {h[:4].hex()}). fPKG2Zar only converts PS5 fake packages")
        if h[5] != 0:
            raise UnsupportedError(f"this is a retail PS5 package (signed byte {h[5]:#04x}). Retail packages are "
                                   "encrypted; fPKG2Zar only converts PS5 fake (fPKG) packages")
        fih = Fih(le16(h, 6), le64(h, 0x10), le64(h, 0x18), le64(h, 0x20), le64(h, 0x58))
        if fih.pfs_offset == 0 or fih.pfs_offset + fih.pfs_size > src.len:
            raise FormatError(f"PFS segment {fih.pfs_offset:#x}+{fih.pfs_size:#x} does not fit in the {src.len:#x}-byte package")
        if fih.cnt_offset >= src.len:
            raise FormatError(f"CNT offset {fih.cnt_offset:#x} is past the end of the package")
        return fih


WELL_KNOWN = {
    0x0001: "digests.bin", 0x0010: "entry_keys.bin", 0x0020: "image_key.bin", 0x0080: "general_digests.bin",
    0x0100: "metas.bin", 0x0200: "entry_names.bin", 0x0400: "license.dat", 0x0401: "license.info",
    0x0402: "nptitle.dat", 0x040A: "imagedigs.dat", 0x1001: "playgo-chunk.dat", 0x1200: "icon0.png",
    0x1220: "pic0.png", 0x1240: "snd0.at9", 0x1280: "icon0.dds", 0x12A0: "pic0.dds", 0x2000: "param.json",
    0x2010: "playgo-hash-table.dat", 0x2011: "playgo-ficm.dat",
}


def is_plain_relative_path(p: str) -> bool:
    if not p or "\\" in p or ":" in p or "\0" in p:
        return False
    return all(part not in ("", ".", "..") for part in p.split("/"))


@dataclass
class CntFile:
    name: str  # relative to sce_sys/
    data: bytes


@dataclass
class Cnt:
    content_id: str
    files: list
    skipped_encrypted: list

    @staticmethod
    def read(src: Source, cnt_offset: int) -> "Cnt":
        h = src.read(0x80, cnt_offset)
        if h[:4] != CNT_MAGIC:
            raise FormatError(f"no CNT container at {cnt_offset:#x}")
        count = be32(h, 16)
        table_off = be32(h, 24)
        body_off = be64(h, 0x20)
        body_size = be64(h, 0x28)
        content_id = c_str(h[0x40:0x64]).decode("ascii", "replace")
        if count == 0 or count > 4096:
            raise FormatError(f"CNT entry count {count} is implausible")
        cnt_size = body_off + body_size
        table = src.read(count * 0x20, cnt_offset + table_off)
        entries = []
        for i in range(count):
            r = table[i * 0x20:(i + 1) * 0x20]
            entries.append((be32(r, 0), be32(r, 4), be32(r, 8), be32(r, 16), be32(r, 20)))

        def read_entry(e):
            _id, _no, _f, off, size = e
            if off + size > cnt_size:
                raise FormatError(f"CNT entry {_id:#06x} runs past the container")
            return src.read(size, cnt_offset + off)

        names = b""
        for e in entries:
            if e[0] == 0x0200 and not (e[2] & 0x80000000):
                names = read_entry(e)
                break
        files, skipped = [], []
        for e in entries:
            eid, name_off, flags1, off, size = e
            if size == 0:
                continue
            raw = c_str(names[name_off:]) if (name_off and name_off < len(names)) else b""
            name = raw.decode("utf-8", "replace") if raw else WELL_KNOWN.get(eid, "unnamed.bin")
            name = name.lstrip("/\\")
            if name.startswith("sce_sys/"):
                name = name[len("sce_sys/"):]
            if flags1 & 0x80000000:
                skipped.append(name)
                continue
            if not name or (name == "unnamed.bin" and eid != 0x040A):
                continue
            if not is_plain_relative_path(name):
                raise FormatError(f"CNT entry {eid:#06x} has the unsafe name {name!r}")
            files.append(CntFile(name, read_entry(e)))
        return Cnt(content_id, files, skipped)


# --------------------------------------------------------------------------
# Outer PFS

@dataclass
class _Layout:
    size: int
    first_ptr: int
    stride: int
    ptr_at: int
    ptr64: bool

    def pointer(self, buf, index, base=0):
        at = base + index * self.stride + self.ptr_at
        return le64(buf, at) if self.ptr64 else le32(buf, at)


def _inode_layout(mode: int) -> _Layout:
    signed, b64 = bool(mode & 1), bool(mode & 2)
    if signed and not b64:
        return _Layout(0x2C8, 0x64, 36, 32, False)
    if signed and b64:
        return _Layout(0x310, 0x68, 40, 32, True)
    if not signed and not b64:
        return _Layout(0xA8, 0x64, 4, 0, False)
    return _Layout(0x100, 0x68, 8, 0, True)


def iter_dirents(buf: bytes, block_size: int):
    """Yields (ino, kind, name_bytes). All-zero records pad to the block end."""
    pos, n = 0, len(buf)
    while pos < n:
        rest = n - pos
        if rest < 16:
            if any(buf[pos:]):
                raise FormatError("truncated directory record")
            return
        ino, kind, name_len, rec_size = struct.unpack_from("<IIII", buf, pos)
        if ino == 0 and kind == 0 and name_len == 0 and rec_size == 0:
            pos += min(block_size - pos % block_size, rest)
            continue
        if not (16 <= rec_size <= 0x1000) or name_len > rec_size - 16 or rec_size > rest:
            raise FormatError(f"bad directory record at {pos:#x} (name {name_len} bytes, record {rec_size} bytes)")
        name = c_str(buf[pos + 16:pos + 16 + name_len])
        pos += rec_size
        if not name:
            raise FormatError("directory record without a name")
        yield ino, kind, name


class OuterFile:
    def __init__(self, src: Source, base: int, block_size: int, blocks: list, size: int):
        self.src, self.base, self.block_size, self.blocks, self.size = src, base, block_size, blocks, size

    def read_into(self, buf, offset: int) -> None:
        n = len(buf)
        if offset < 0 or offset + n > self.size:
            raise FormatError(f"read of {n:#x} bytes at {offset:#x} is past the end of a {self.size:#x}-byte outer file")
        mv = memoryview(buf)
        done = 0
        bs = self.block_size
        blocks = self.blocks
        while done < n:
            pos = offset + done
            idx, within = divmod(pos, bs)
            run = 1
            first = blocks[idx]
            while idx + run < len(blocks) and blocks[idx + run] == first + run:
                run += 1
                if run * bs - within >= n - done:
                    break
            take = min(run * bs - within, n - done)
            self.src.read_into(mv[done:done + take], self.base + first * bs + within)
            done += take

    def read_all(self) -> bytes:
        buf = bytearray(self.size)
        self.read_into(buf, 0)
        return bytes(buf)


class OuterPfs:
    def __init__(self, src: Source, pfs_offset: int, pfs_size: int, superblock_offset: int):
        self.src, self.base, self.size = src, pfs_offset, pfs_size
        in_seg = pfs_offset <= superblock_offset and superblock_offset + 0x400 <= pfs_offset + pfs_size
        sb = src.read(0x400, superblock_offset if in_seg else pfs_offset)
        version, magic = le64(sb, 0), le64(sb, 8)
        if magic != PFS_MAGIC or version not in (1, 2):
            raise FormatError(f"no PFS superblock (version {version}, magic {magic:#x})")
        self.mode = le16(sb, 0x1C)
        self.block_size = le32(sb, 0x20)
        if self.block_size == 0 or self.block_size > (1 << 20) or self.block_size % 0x1000:
            raise FormatError(f"PFS block size {self.block_size:#x}")
        seed = sb[0x370:0x380]
        if self.mode & 0x4 and seed != PLAIN_NOAUTH_SEED:
            raise UnsupportedError("the outer PFS is encrypted (seed is not PPRPLAIN-NOAUTH!); this is not a fake package")
        dinode_count = le64(sb, 0x30)
        dinode_blocks = max(le64(sb, 0x40), 1)
        inode_table_block = le64(sb, 0xD8)
        if dinode_count == 0 or dinode_count > (1 << 20):
            raise FormatError(f"outer PFS inode count {dinode_count}")
        self.layout = _inode_layout(self.mode)
        per_block = self.block_size // self.layout.size
        table_len = dinode_blocks * self.block_size
        if table_len > pfs_size:
            raise FormatError("outer inode table does not fit in the PFS segment")
        table = src.read(table_len, pfs_offset + inode_table_block * self.block_size)
        self.inodes = []
        L = self.layout
        for i in range(dinode_count):
            at = (i // per_block) * self.block_size + (i % per_block) * L.size
            rec = table[at:at + L.size]
            if len(rec) < L.size:
                raise FormatError("outer inode table is short")
            direct = [L.pointer(rec, j, L.first_ptr) for j in range(12)]
            indirect = [L.pointer(rec, 12 + j, L.first_ptr) for j in range(5)]
            self.inodes.append((le64(rec, 8), direct, indirect))

    def file(self, ino: int) -> OuterFile:
        if ino >= len(self.inodes):
            raise FormatError(f"outer inode {ino} out of range")
        size, direct, indirect = self.inodes[ino]
        if size > self.size:
            raise FormatError(f"outer inode {ino} is larger than the PFS segment")
        count = -(-size // self.block_size)
        blocks = list(direct[:count])
        per_block = self.block_size // self.layout.stride
        level = 0
        while len(blocks) < count:
            if level >= 3:
                raise UnsupportedError("outer file needs more than triple-indirect blocks")
            self._walk_indirect(indirect[level], level, count, per_block, blocks)
            level += 1
        return OuterFile(self.src, self.base, self.block_size, blocks, size)

    def _walk_indirect(self, block, depth, count, per_block, out):
        buf = self.src.read(self.block_size, self.base + block * self.block_size)
        for i in range(per_block):
            if len(out) >= count:
                break
            ptr = self.layout.pointer(buf, i)
            if depth == 0:
                out.append(ptr)
            else:
                self._walk_indirect(ptr, depth - 1, count, per_block, out)

    def find(self, name: str):
        want = name.encode()
        stack, seen = [0], set()
        while stack:
            d = stack.pop()
            if d in seen:
                continue
            seen.add(d)
            f = self.file(d)
            if f.size > (8 << 20):
                raise FormatError(f"outer directory inode {d} is {f.size:#x} bytes")
            for ino, kind, nm in iter_dirents(f.read_all(), self.block_size):
                if ino >= len(self.inodes) or nm in (b".", b".."):
                    continue
                if kind == DIRENT_DIR:
                    stack.append(ino)
                elif kind == DIRENT_FILE and nm == want:
                    return ino
        return None


# --------------------------------------------------------------------------
# NAPS layout

def _decode_cblock(raw: bytes):
    lo = int.from_bytes(raw[0:8], "little")
    hi = raw[8]
    coffset_mod = lo & 0x3FFFF
    if (lo >> 18) & 1 == 0:
        # (is_run_base, coffset_mod, clen_even_minus1, kde_predictor, shuffle_idx, coffset_start_256k)
        return (False, coffset_mod, (lo >> 38) & 0x1FFFF, (lo >> 56) & 7, (lo >> 59) & 0xF, 0)
    return (True, coffset_mod, 0, 0, 0, ((lo >> 49) & 0x7FFF) | ((hi & 0x1FF) << 15))


def _run_on_disk(base, first):
    return (base[5] // 2) * UBLOCK_SIZE + first[1]


class Naps:
    def __init__(self, blob: bytes):
        if len(blob) < 16:
            raise FormatError("NAPS header is truncated")
        w0 = int.from_bytes(blob[0:8], "little")
        w1 = int.from_bytes(blob[8:16], "little")
        self.num_files = (w0 & 0xFFFFFF) + 1
        self.compression_type = (w0 >> 24) & 3
        self.num_shuffle = (w0 >> 28) & 0xF
        self.num_ublocks = (w0 >> 32) & 0xFFFFFF
        self.num_outer_blocks = w1 & 0xFFFFFF
        self.num_cblock_info = ((w1 >> 24) & 0xFFFFFF) + 2
        pos = 16 + self.num_outer_blocks * 8 + self.num_shuffle * 8
        offs = []
        for i in range(self.num_files):
            at = pos + i * 6
            rec = blob[at:at + 6]
            if len(rec) < 6:
                raise FormatError("NAPS file-offset table is truncated")
            offs.append((int.from_bytes(rec[:5], "little"), rec[5]))
        fidx_end = pos + self.num_files * 6
        num_u2c = (self.num_ublocks + 8) >> 3
        starts = []
        for u2c in (-(-fidx_end // 16) * 16, fidx_end):
            s = -(-(u2c + num_u2c * 10) // 8) * 8
            if s not in starts:
                starts.append(s)
        image_size = self.num_outer_blocks * OUTER_BLOCK_SIZE
        self.candidates, problems = [], []
        for s in starts:
            try:
                self.candidates.append((s, self._read_cblocks(blob, s, self.num_cblock_info, image_size)))
            except FormatError as e:
                problems.append(f"at {s:#x}: {e}")
        if not self.candidates:
            raise FormatError("no valid NAPS CblockInfo table (" + "; ".join(problems) + ")")
        offs.sort(key=lambda t: t[0])  # stable
        self.file_offsets = offs
        self._offs_keys = [o for o, _ in offs]

    @staticmethod
    def _read_cblocks(blob, start, count, image_size):
        end = start + count * 9
        if end > len(blob):
            raise FormatError(f"{count} records end at {end:#x}, past the {len(blob):#x}-byte layout")
        recs = [_decode_cblock(blob[start + i * 9:start + i * 9 + 9]) for i in range(count)]
        for i, r in enumerate(recs):
            if not r[0]:
                continue
            nxt = recs[i + 1] if i + 1 < len(recs) else None
            if nxt is None or nxt[0]:
                raise FormatError(f"run base {i} is not followed by a data record")
            if _run_on_disk(r, nxt) >= image_size:
                raise FormatError(f"run base {i} points past the image")
        return recs

    def mount_size(self) -> int:
        for off, kind in reversed(self.file_offsets):
            if kind == KIND_MOUNT:
                return off
        return 0

    def next_boundary(self, cur, mount):
        i = bisect.bisect_right(self._offs_keys, cur)
        return min(self._offs_keys[i], mount) if i < len(self._offs_keys) else mount

    def first_at_or_after(self, offset):
        i = bisect.bisect_left(self._offs_keys, offset)
        return self.file_offsets[i] if i < len(self.file_offsets) else None


# --------------------------------------------------------------------------
# Inner image

@dataclass
class UBlock:
    logical: int
    on_disk: int
    comp: int      # 0 => sparse (zeros)
    uncomp: int
    kraken: bool
    even_comp: int
    flags: int


def _append_sparse(naps, mount, uncomp, out):
    while uncomp < mount:
        e = naps.first_at_or_after(uncomp)
        if not (e and e[1] == KIND_MOUNT and e[0] == uncomp):
            return uncomp
        end = naps.next_boundary(uncomp, mount)
        ln = min(UBLOCK_SIZE, end - uncomp)
        if ln == 0:
            raise FormatError(f"empty sparse region at {uncomp:#x}")
        out.append(UBlock(uncomp, 0, 0, ln, False, 0, 0))
        uncomp += ln
    return uncomp


def _walk_blocks(naps: Naps, recs, mount):
    out = []
    on_disk = 0
    uncomp = 0
    i = 0
    n = len(recs)
    while i < n:
        r = recs[i]
        if r[0]:
            nxt = recs[i + 1] if i + 1 < n else None
            if nxt is None or nxt[0]:
                raise FormatError(f"NAPS run base {i} is not followed by a data record")
            on_disk = _run_on_disk(r, nxt)
            i += 1
            continue
        uncomp = _append_sparse(naps, mount, uncomp, out)
        if uncomp >= mount or i + 1 >= n:
            break
        file_end = naps.next_boundary(uncomp, mount)
        ulen = min(UBLOCK_SIZE, max(file_end - uncomp, 0))
        if ulen == 0:
            break
        diff = recs[i + 1][1] - r[1]
        if diff <= 0:
            diff += UBLOCK_SIZE
        out.append(UBlock(uncomp, on_disk, diff, ulen, bool(r[3] & 2) or diff != ulen, r[2] + 1, r[3] | (r[4] << 4)))
        on_disk += diff
        uncomp += ulen
        if uncomp >= mount:
            break
        i += 1
    uncomp = _append_sparse(naps, mount, uncomp, out)
    if uncomp != mount:
        raise FormatError(f"NAPS blocks cover {uncomp:#x} bytes, but the mount size is {mount:#x}")
    return out


@dataclass
class InnerFile:
    path: str      # relative to uroot, '/'-separated
    logical: int
    size: int


class InnerImage:
    def __init__(self, image: OuterFile, naps_blob: bytes):
        self.image = image
        naps = Naps(naps_blob)
        mount = naps.mount_size()
        if mount == 0:
            raise FormatError("NAPS layout has no mount size entry")
        walks, problems = [], []
        for start, recs in naps.candidates:
            try:
                walks.append(_walk_blocks(naps, recs, mount))
            except FormatError as e:
                problems.append(f"table at {start:#x}: {e}")
        if len(walks) > 1:
            raise FormatError("two NAPS CblockInfo placements both map the whole image; refusing to guess")
        if not walks:
            raise FormatError("; ".join(problems))
        self.blocks = walks[0]
        self.mount = mount
        self.stats = {"stored": 0, "kraken": 0, "sparse": 0}
        for b in self.blocks:
            if b.comp == 0:
                self.stats["sparse"] += 1
            elif b.kraken:
                self.stats["kraken"] += 1
            else:
                self.stats["stored"] += 1
            if b.on_disk + b.comp > image.size:
                raise FormatError(f"block at logical {b.logical:#x} is stored past the end of pfs_image.dat")
        self._starts = [b.logical for b in self.blocks]
        self.boundaries = [o for o, _ in naps.file_offsets]
        self.dirs: list = []

    # -- block access -------------------------------------------------------
    def find_block(self, logical: int) -> int:
        i = bisect.bisect_right(self._starts, logical) - 1
        if i < 0:
            raise FormatError(f"no block covers logical {logical:#x}")
        b = self.blocks[i]
        if not (b.logical <= logical < b.logical + b.uncomp):
            raise FormatError(f"no block covers logical {logical:#x}")
        return i

    def decode_block(self, index: int) -> bytearray:
        """Returns the decoded bytes of one block. Thread-safe."""
        b = self.blocks[index]
        out = bytearray(b.uncomp)
        if b.comp == 0:
            return out
        payload = bytearray(b.comp)
        self.image.read_into(payload, b.on_disk)
        if b.comp == b.uncomp:
            return payload
        try:
            kraken.decode_block(bytes(payload), b.flags, b.even_comp, out)
        except (FormatError, UnsupportedError) as e:
            raise type(e)(f"{e} in the block at logical {b.logical:#x} (stored at {b.on_disk:#x}, "
                          f"{b.comp} -> {b.uncomp} bytes, flags {b.flags:#x})") from None
        return out

    def read_at(self, logical: int, n: int) -> bytes:
        out = bytearray()
        pos, end = logical, logical + n
        while pos < end:
            i = self.find_block(pos)
            b = self.blocks[i]
            data = self.decode_block(i)
            w = pos - b.logical
            take = min(b.uncomp - w, end - pos)
            out += data[w:w + take]
            pos += take
        return bytes(out)

    # -- file tree ----------------------------------------------------------
    def files(self) -> list:
        """Lists the regular files under uroot (and records directories in self.dirs)."""
        meta_base = max([o for o in self.boundaries if 0 < o < self.mount], default=0)
        first = None
        for i, b in enumerate(self.blocks):
            if b.logical + b.uncomp > meta_base:
                first = i
                break
        if first is None:
            raise FormatError("no blocks after the last file")
        tail_base = self.blocks[first].logical
        tail_len = self.mount - tail_base
        if tail_len > (1 << 30):
            raise FormatError(f"{tail_len:#x} bytes after the last file boundary; the inner metadata should be far smaller")
        tail = self.read_at(tail_base, tail_len)
        sb_off = None
        for off in range(0, max(len(tail) - 0x40, 0) + 1, 0x10000):
            if off + 0x40 > len(tail):
                break
            bs = le32(tail, off + 0x20)
            if le64(tail, off) == 2 and le64(tail, off + 8) == PFS_MAGIC and le64(tail, off + 0x30) > 0 \
                    and (le64(tail, off + 0x38) * bs) & 0xFFFFFFFFFFFFFFFF == self.mount:
                sb_off = off
                break
        if sb_off is None:
            raise FormatError(f"no inner superblock covering {self.mount:#x} bytes after logical {tail_base:#x}")
        bs = le32(tail, sb_off + 0x20)
        count = le64(tail, sb_off + 0x30)
        mode = le16(tail, sb_off + 0x1C)
        if bs == 0 or bs % 0x1000 or count == 0 or count > 1_000_000:
            raise FormatError(f"inner superblock: block size {bs:#x}, {count} inodes")
        if mode & 0x13 != 0x10:
            raise UnsupportedError(f"inner PFS mode {mode:#x} (only the flat 0x10 layout is known)")
        per_block = bs // 0xA8
        table = sb_off + bs
        inodes = []
        for i in range(count):
            at = table + (i // per_block) * bs + (i % per_block) * 0xA8
            if at + 0xA8 > len(tail):
                raise FormatError("inner inode table is truncated")
            inodes.append((le16(tail, at), le64(tail, at + 8), le64(tail, at + 0x60)))

        files, dirs = [], []
        seen = set()

        def local(logical, size):
            s = logical - tail_base
            if s >= 0 and s + size <= len(tail):
                return tail[s:s + size]
            return None

        stack = [(0, "", False)]
        while stack:
            ino, path, under = stack.pop()
            if ino >= len(inodes):
                raise FormatError(f"inner inode {ino} out of range")
            if ino in seen:
                continue
            seen.add(ino)
            mode_i, size, logical = inodes[ino]
            if mode_i & 0xF000 != 0x4000 or size == 0 or size > (8 << 20):
                raise FormatError(f"inner inode {ino} is not a readable directory")
            data = local(logical, size)
            if data is None:
                data = self.read_at(logical, size)
            for cino, kind, nm in iter_dirents(data, bs):
                if nm in (b".", b".."):
                    continue
                try:
                    name = nm.decode("utf-8")
                except UnicodeDecodeError:
                    raise FormatError("inner file name is not UTF-8") from None
                if any(c in name for c in "/\\:"):
                    raise FormatError(f"inner file name {name!r} contains a path separator")
                if cino >= len(inodes):
                    raise FormatError(f"{name}: inode {cino} out of range")
                is_uroot = path == "" and name == "uroot" and not under
                child_path = "" if is_uroot else (name if not path else f"{path}/{name}")
                if kind == DIRENT_DIR:
                    if under:
                        dirs.append(child_path)
                    stack.append((cino, child_path, under or is_uroot))
                elif kind == DIRENT_FILE and under:
                    _m, csize, clog = inodes[cino]
                    if clog + csize > self.mount:
                        raise FormatError(f"{child_path} runs past the end of the image")
                    files.append(InnerFile(child_path, clog, csize))
        if not files:
            raise FormatError("the inner tree has no files under uroot")
        self.dirs = dirs
        return files


# --------------------------------------------------------------------------
# Package

@dataclass
class Package:
    path: str
    src: Source
    fih: Fih
    cnt: Cnt
    inner: InnerImage
    files: list
    dirs: list = field(default_factory=list)

    @staticmethod
    def open(path: str) -> "Package":
        src = Source(path)
        try:
            fih = Fih.read(src)
            cnt = Cnt.read(src, fih.cnt_offset)
            outer = OuterPfs(src, fih.pfs_offset, fih.pfs_size, fih.superblock_offset)
            img = outer.find("pfs_image.dat")
            if img is None:
                raise FormatError("the outer PFS has no pfs_image.dat")
            nino = outer.find("naps_pkg_layout.dat")
            if nino is None:
                raise UnsupportedError("the outer PFS has no naps_pkg_layout.dat; images without a NAPS layout are not supported yet")
            nf = outer.file(nino)
            if nf.size > (256 << 20):
                raise FormatError("naps_pkg_layout.dat is implausibly large")
            inner = InnerImage(outer.file(img), nf.read_all())
            files = inner.files()
            return Package(path, src, fih, cnt, inner, files, inner.dirs)
        except BaseException:
            src.close()
            raise

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files) + sum(len(c.data) for c in self.cnt.files)

    def title(self) -> str:
        """Best-effort game title from sce_sys/param.json."""
        import json
        for c in self.cnt.files:
            if c.name == "param.json":
                try:
                    j = json.loads(c.data.decode("utf-8", "replace"))
                    loc = j.get("localizedParameters", {})
                    lang = loc.get("defaultLanguage", "en-US")
                    t = loc.get(lang, {}).get("titleName")
                    if t:
                        return t
                    for v in loc.values():
                        if isinstance(v, dict) and v.get("titleName"):
                            return v["titleName"]
                except Exception:
                    pass
        return ""

    def title_id(self) -> str:
        cid = self.cnt.content_id
        return cid[7:16] if len(cid) >= 16 else ""

    def close(self):
        self.src.close()
