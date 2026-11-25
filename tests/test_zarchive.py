import os
import random
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fpkg2zar.zarchive import BLOCK, FOOTER_SIZE, MAGIC, VERSION1, ZArchiveReader, ZArchiveWriter  # noqa: E402


class ZArchiveRoundTrip(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "t.zar")

    def tearDown(self):
        for f in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, f))
        os.rmdir(self.dir)

    def test_round_trip(self):
        rnd = random.Random(42)
        files = {
            "eboot.bin": rnd.randbytes(BLOCK * 3 + 123),          # incompressible, crosses blocks
            "sce_sys/param.json": b'{"titleId":"PPSA00000"}' * 50,  # compressible
            "sce_sys/about/right.sprx": b"",                        # empty file
            "Data/Level01/" + "x" * 200 + ".bin": bytes(BLOCK * 2),  # long name, zeros
            "data/level01/small.txt": b"hello",                    # same dir, different case
            "B.txt": b"B", "a.txt": b"a", "C.txt": b"c",          # sort order
        }
        w = ZArchiveWriter(self.path, level=3, threads=4)
        w.make_dir("empty/dir")
        offsets = {}
        for p, data in files.items():
            w.start_file(p)
            offsets[p] = w.input_offset
            # feed in odd-sized pieces to exercise buffering
            pos = 0
            while pos < len(data):
                n = rnd.randint(1, 100_000)
                w.append(data[pos:pos + n])
                pos += n
        w.add_file_ref("dup/eboot_copy.bin", offsets["eboot.bin"], len(files["eboot.bin"]))
        w.finalize()

        r = ZArchiveReader(self.path)
        try:
            self.assertTrue(r.check_integrity())
            listing = {p: (f, o, s) for p, f, o, s in r.walk()}
            self.assertIn("empty", listing)
            self.assertIn("empty/dir", listing)
            self.assertFalse(listing["empty/dir"][0])
            # case-folded directory merge: Data and data are one directory
            self.assertIn("Data/Level01/small.txt", listing)
            for p, data in files.items():
                key = p if p in listing else p.replace("data/level01", "Data/Level01")
                is_file, off, size = listing[key]
                self.assertTrue(is_file, p)
                self.assertEqual(size, len(data), p)
                self.assertEqual(r.read_file(off, size), data, p)
            _, off, size = listing["dup/eboot_copy.bin"]
            self.assertEqual(r.read_file(off, size), files["eboot.bin"])
            # children of the root are sorted case-insensitively: a, B, C, ...
            root = r.entries[0]
            names = [r._name(r.entries[i][0] & 0x7FFFFFFF) for i in range(root[1], root[1] + root[2])]
            self.assertEqual(names, sorted(names, key=lambda s: s.lower()))
        finally:
            r.close()

    def test_footer_layout(self):
        w = ZArchiveWriter(self.path, threads=1)
        w.start_file("x")
        w.append(b"abc")
        w.finalize()
        with open(self.path, "rb") as f:
            data = f.read()
        foot = data[-FOOTER_SIZE:]
        version, magic = struct.unpack(">II", foot[-8:])  # struct order: totalSize, version, magic
        self.assertEqual((magic, version), (MAGIC, VERSION1))
        self.assertEqual(struct.unpack(">Q", foot[-16:-8])[0], len(data))
        # compressed data section starts at 0 and is whole blocks of input
        off, size = struct.unpack(">QQ", foot[:16])
        self.assertEqual(off, 0)
        self.assertGreater(size, 0)

    def test_abort_removes_file(self):
        w = ZArchiveWriter(self.path, threads=2)
        w.start_file("x")
        w.append(os.urandom(BLOCK * 5))
        w.abort()
        self.assertFalse(os.path.exists(self.path))

    def test_duplicate_names_rejected(self):
        w = ZArchiveWriter(self.path, threads=1)
        w.start_file("A/b.txt")
        with self.assertRaises(FileExistsError):
            w.start_file("a/B.TXT")
        w.abort()


class KrakenUnit(unittest.TestCase):
    def setUp(self):
        from fpkg2zar import kraken
        self.k = kraken

    def test_verbatim_block(self):
        src = bytes(range(1, 9))
        out = bytearray(8)
        self.k.decode_block(src, 0x02, 8, out)
        self.assertEqual(bytes(out), src)

    def test_memcpy_entropy(self):
        st, used, out = self.k.test_decode_bytes(bytes([0x80, 0x04, 9, 8, 7, 6]), 4)
        self.assertEqual((st, used, out), (0, 6, bytes([9, 8, 7, 6])))

    def test_tans_unsupported(self):
        st, _, _ = self.k.test_decode_bytes(bytes([0x90, 0x04, 0x02, 0, 0]), 16)
        self.assertEqual(st, 1)

    def test_garbage_does_not_crash(self):
        rnd = random.Random(7)
        for _ in range(3000):
            n = rnd.randint(0, 3000)
            payload = rnd.randbytes(n)
            out = bytearray(rnd.choice([0x10000, 0x20000, 0x40000]))
            try:
                self.k.decode_block(payload, rnd.randint(0, 0xFF), rnd.randint(0, n + 10), out)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
