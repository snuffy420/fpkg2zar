"""ctypes binding for the Kraken block decoder in kraken.dll (native/kraken.c).

ctypes releases the GIL for the duration of the call, so several threads
can decode blocks at once.
"""

from __future__ import annotations

import ctypes
import os
import sys

from .errors import FormatError, UnsupportedError

_lib = None


def _candidates():
    here = os.path.dirname(os.path.abspath(__file__))
    if os.name == "nt":
        names = ["kraken.dll"]
    elif sys.platform == "darwin":
        names = ["libkraken.dylib"]
    else:
        names = ["libkraken.so"]
    dirs = [here]
    # PyInstaller one-file bundles unpack next to sys._MEIPASS.
    if hasattr(sys, "_MEIPASS"):
        dirs.insert(0, os.path.join(sys._MEIPASS, "fpkg2zar"))
        dirs.insert(0, sys._MEIPASS)
    for d in dirs:
        for n in names:
            yield os.path.join(d, n)


def load():
    global _lib
    if _lib is not None:
        return _lib
    tried = []
    for path in _candidates():
        if os.path.exists(path):
            lib = ctypes.CDLL(path)
            lib.kraken_decode_block.argtypes = [
                ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t,
            ]
            lib.kraken_decode_block.restype = ctypes.c_int
            lib.kraken_abi_version.restype = ctypes.c_int
            lib.kraken_test_decode_bytes.argtypes = [
                ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t),
            ]
            lib.kraken_test_decode_bytes.restype = ctypes.c_int
            if lib.kraken_abi_version() != 1:
                raise RuntimeError(f"{path} has an unexpected ABI version")
            _lib = lib
            return lib
        tried.append(path)
    how = "native\\build.bat" if os.name == "nt" else "native/build.sh"
    raise RuntimeError(f"The Kraken decoder library was not found. Build it with {how}. Looked in: " + ", ".join(tried))


def decode_block(payload: bytes, flags: int, first_chunk_comp: int, out: bytearray) -> None:
    """Decodes one stored block into `out` (whose length is the block size)."""
    lib = load()
    buf = (ctypes.c_char * len(out)).from_buffer(out)
    st = lib.kraken_decode_block(payload, len(payload), flags, first_chunk_comp, ctypes.addressof(buf), len(out))
    del buf
    if st == 0:
        return
    if st == -1:
        raise FormatError("malformed Kraken data")
    if st == -2:
        raise MemoryError("out of memory in the Kraken decoder")
    raise UnsupportedError(f"unsupported Kraken entropy type {st}")


def test_decode_bytes(src: bytes, cap: int):
    lib = load()
    out = (ctypes.c_char * max(cap, 1))()
    used = ctypes.c_size_t()
    n = ctypes.c_size_t()
    st = lib.kraken_test_decode_bytes(src, len(src), ctypes.addressof(out), cap, ctypes.byref(used), ctypes.byref(n))
    return st, used.value, bytes(out[: n.value]) if st == 0 else b""
