/*
 * Kraken (Oodle newLZ) decoder for the header-stripped 256 KiB blocks of a
 * PS5 inner image (pfs_image.dat), exported as a tiny C ABI for ctypes.
 *
 * A block is one or two 128 KiB chunks with no Oodle block headers; the NAPS
 * record gives the codec flags and the compressed length of the first chunk.
 *
 * This is a C port of the GPL-3.0-or-later decoder in pkg-to-anyps5
 * (src/kraken.rs, https://github.com/awake-devel/pkg-to-anyps5), which was
 * ported from PS5PCEM (src/pkg/kraken.zig), which follows the LibProsperoPKG
 * managed decoder. Licensed GPL-3.0-or-later.
 *
 * Entropy types: memcpy and Huffman (2- and 4-way). tANS, RLE and recursive
 * entropy blocks are reported as unsupported rather than guessed.
 *
 * Every read is bounds-checked: malformed input returns an error, never
 * reads or writes out of range.
 */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

#define KR_OK 0
#define KR_MALFORMED (-1)
#define KR_NOMEM (-2)
/* Positive return values: unsupported entropy type N. */

#define CHUNK_MAX 0x20000u
#define SEED_SIZE 8u
#define CHUNK0_SUB_LIT 0x01u
#define CHUNK0_NEW_LZ 0x02u
#define CHUNK1_SUB_LIT 0x10u
#define CHUNK1_NEW_LZ 0x20u
#define CHUNK1_RESTART 0x40u

static const uint32_t CODE_PREFIX_ORG[12] = {0x0, 0x0, 0x2, 0x6, 0xE, 0x1E, 0x3E, 0x7E, 0xFE, 0x1FE, 0x2FE, 0x3FE};

static inline uint32_t shl(uint32_t v, int64_t n) { return (n >= 0 && n < 32) ? (v << n) : 0; }
static inline uint32_t shr(uint32_t v, int64_t n) { return (n >= 0 && n < 32) ? (v >> n) : 0; }

static inline uint32_t bsr32(uint32_t x) {
    uint32_t r = 0;
    if (x == 0) return 0;
    while (x >>= 1) r++;
    return r;
}
static inline uint32_t bsf32(uint32_t x) {
    uint32_t r = 0;
    if (x == 0) return 0;
    while (!(x & 1)) { x >>= 1; r++; }
    return r;
}
static inline uint32_t clz32(uint32_t x) {
    uint32_t n = 0;
    if (x == 0) return 31; /* matches the Rust helper */
    while (!(x & 0x80000000u)) { x <<= 1; n++; }
    return n;
}
static inline uint32_t rotl32(uint32_t x, uint32_t n) {
    n &= 31;
    return n ? ((x << n) | (x >> (32 - n))) : x;
}
static inline uint32_t bswap32(uint32_t x) {
    return ((x & 0xFFu) << 24) | ((x & 0xFF00u) << 8) | ((x >> 8) & 0xFF00u) | (x >> 24);
}
static inline uint64_t bswap64(uint64_t x) {
    return ((uint64_t)bswap32((uint32_t)x) << 32) | bswap32((uint32_t)(x >> 32));
}

static inline uint8_t at_byte(const uint8_t *b, size_t len, size_t i) { return i < len ? b[i] : 0; }

static uint32_t read_le32(const uint8_t *b, size_t len, size_t off) {
    uint32_t r = 0;
    for (int i = 0; i < 4; i++) r |= (uint32_t)at_byte(b, len, off + i) << (8 * i);
    return r;
}
static uint64_t read_le64(const uint8_t *b, size_t len, size_t off) {
    uint64_t r = 0;
    for (int i = 0; i < 8; i++) r |= (uint64_t)at_byte(b, len, off + i) << (8 * i);
    return r;
}
static void write_le32(uint8_t *b, size_t len, size_t off, uint32_t v) {
    for (int i = 0; i < 4; i++)
        if (off + i < len) b[off + i] = (uint8_t)(v >> (8 * i));
}
static void write_le64(uint8_t *b, size_t len, size_t off, uint64_t v) {
    for (int i = 0; i < 8; i++)
        if (off + i < len) b[off + i] = (uint8_t)(v >> (8 * i));
}

/* ------------------------------------------------------------------------ */
/* Bit reader: a 32-bit window with the next bits at the top, filled        */
/* forwards from p, or backwards from p down to bound.                      */

typedef struct {
    const uint8_t *b;
    size_t blen;
    size_t p;
    size_t bound;
    uint32_t bits;
    int32_t bit_pos;
    int bwd;
} BitReader;

static void br_refill_f(BitReader *r) {
    while (r->bit_pos > 0) {
        uint32_t byte = r->p < r->bound ? at_byte(r->b, r->blen, r->p) : 0;
        r->bits |= shl(byte, r->bit_pos);
        r->bit_pos -= 8;
        r->p++;
    }
}
static void br_refill_b(BitReader *r) {
    while (r->bit_pos > 0) {
        r->p = r->p - 1; /* wrapping */
        uint32_t byte = (r->p < r->blen && r->p >= r->bound) ? r->b[r->p] : 0;
        r->bits |= shl(byte, r->bit_pos);
        r->bit_pos -= 8;
    }
}
static void br_refill(BitReader *r) {
    if (r->bwd) br_refill_b(r); else br_refill_f(r);
}
static BitReader br_forward(const uint8_t *b, size_t blen, size_t start, size_t end) {
    BitReader r = {b, blen, start, end, 0, 24, 0};
    br_refill_f(&r);
    return r;
}
static BitReader br_backward(const uint8_t *b, size_t blen, size_t low, size_t high) {
    BitReader r = {b, blen, high, low, 0, 24, 1};
    br_refill_b(&r);
    return r;
}
static uint32_t br_bit_nr(BitReader *r) {
    uint32_t v = r->bits >> 31;
    r->bits <<= 1;
    r->bit_pos += 1;
    return v;
}
static uint32_t br_bit(BitReader *r) {
    br_refill(r);
    return br_bit_nr(r);
}
static uint32_t br_bits_nr(BitReader *r, uint32_t n) {
    uint32_t v = shr(r->bits, 32 - (int64_t)n);
    r->bits = shl(r->bits, n);
    r->bit_pos += (int32_t)n;
    return v;
}
static uint32_t br_bits_nr_zero(BitReader *r, uint32_t n) {
    uint32_t v = shr(r->bits >> 1, 31 - (int64_t)n);
    r->bits = shl(r->bits, n);
    r->bit_pos += (int32_t)n;
    return v;
}
static int32_t br_fluff(BitReader *r, int32_t num_symbols) {
    if (num_symbols == 256) return 0;
    int32_t x = (257 - num_symbols) < num_symbols ? (257 - num_symbols) : num_symbols;
    x *= 2;
    uint32_t y = bsr32((uint32_t)(x - 1)) + 1;
    uint32_t v = shr(r->bits, 32 - (int64_t)y);
    uint32_t z = (1u << y) - (uint32_t)x;
    if ((v >> 1) >= z) {
        r->bits = shl(r->bits, y);
        r->bit_pos += (int32_t)y;
        return (int32_t)(v - z);
    }
    r->bits = shl(r->bits, (int64_t)y - 1);
    r->bit_pos += (int32_t)y - 1;
    return (int32_t)(v >> 1);
}
static uint32_t br_more_than_24(BitReader *r, uint32_t n) {
    uint32_t rv;
    if (n <= 24) {
        rv = br_bits_nr_zero(r, n);
    } else {
        uint32_t hi = br_bits_nr(r, 24) << (n - 24);
        br_refill(r);
        rv = hi + br_bits_nr(r, n - 24);
    }
    br_refill(r);
    return rv;
}
static uint32_t br_distance(BitReader *r, uint8_t v8) {
    uint32_t v = v8, rv;
    if (v < 0xF0) {
        uint32_t n = (v >> 4) + 4;
        uint32_t w = rotl32(r->bits | 1, n);
        r->bit_pos += (int32_t)n;
        uint32_t m = (2u << n) - 1;
        r->bits = w & ~m;
        rv = ((w & m) << 4) + (v & 0xF) - 248;
    } else {
        uint32_t n = v - 0xF0 + 4;
        uint32_t w = rotl32(r->bits | 1, n);
        r->bit_pos += (int32_t)n;
        uint32_t m = (2u << n) - 1;
        r->bits = w & ~m;
        uint32_t x = 8322816u + ((w & m) << 12);
        br_refill(r);
        x += r->bits >> 20;
        r->bit_pos += 12;
        r->bits <<= 12;
        rv = x;
    }
    br_refill(r);
    return rv;
}
static int br_length(BitReader *r, uint32_t *v) {
    uint32_t n = 31 - bsr32(r->bits);
    if (n > 12) return 0;
    r->bit_pos += (int32_t)n;
    r->bits = shl(r->bits, n);
    br_refill(r);
    n += 7;
    r->bit_pos += (int32_t)n;
    *v = shr(r->bits, 32 - (int64_t)n) - 64;
    r->bits = shl(r->bits, n);
    br_refill(r);
    return 1;
}

/* ------------------------------------------------------------------------ */
/* Huffman                                                                  */

typedef struct {
    uint8_t bits2len[2048 + 16];
    uint8_t bits2sym[2048 + 16];
} HuffLut;

static const uint32_t RICE_VAL[256] = {
    0x80000000, 0x00000007, 0x10000006, 0x00000006, 0x20000005, 0x00000105, 0x10000005, 0x00000005, 0x30000004, 0x00000204, 0x10000104,
    0x00000104, 0x20000004, 0x00010004, 0x10000004, 0x00000004, 0x40000003, 0x00000303, 0x10000203, 0x00000203, 0x20000103, 0x00010103,
    0x10000103, 0x00000103, 0x30000003, 0x00020003, 0x10010003, 0x00010003, 0x20000003, 0x01000003, 0x10000003, 0x00000003, 0x50000002,
    0x00000402, 0x10000302, 0x00000302, 0x20000202, 0x00010202, 0x10000202, 0x00000202, 0x30000102, 0x00020102, 0x10010102, 0x00010102,
    0x20000102, 0x01000102, 0x10000102, 0x00000102, 0x40000002, 0x00030002, 0x10020002, 0x00020002, 0x20010002, 0x01010002, 0x10010002,
    0x00010002, 0x30000002, 0x02000002, 0x11000002, 0x01000002, 0x20000002, 0x00000012, 0x10000002, 0x00000002, 0x60000001, 0x00000501,
    0x10000401, 0x00000401, 0x20000301, 0x00010301, 0x10000301, 0x00000301, 0x30000201, 0x00020201, 0x10010201, 0x00010201, 0x20000201,
    0x01000201, 0x10000201, 0x00000201, 0x40000101, 0x00030101, 0x10020101, 0x00020101, 0x20010101, 0x01010101, 0x10010101, 0x00010101,
    0x30000101, 0x02000101, 0x11000101, 0x01000101, 0x20000101, 0x00000111, 0x10000101, 0x00000101, 0x50000001, 0x00040001, 0x10030001,
    0x00030001, 0x20020001, 0x01020001, 0x10020001, 0x00020001, 0x30010001, 0x02010001, 0x11010001, 0x01010001, 0x20010001, 0x00010011,
    0x10010001, 0x00010001, 0x40000001, 0x03000001, 0x12000001, 0x02000001, 0x21000001, 0x01000011, 0x11000001, 0x01000001, 0x30000001,
    0x00000021, 0x10000011, 0x00000011, 0x20000001, 0x00001001, 0x10000001, 0x00000001, 0x70000000, 0x00000600, 0x10000500, 0x00000500,
    0x20000400, 0x00010400, 0x10000400, 0x00000400, 0x30000300, 0x00020300, 0x10010300, 0x00010300, 0x20000300, 0x01000300, 0x10000300,
    0x00000300, 0x40000200, 0x00030200, 0x10020200, 0x00020200, 0x20010200, 0x01010200, 0x10010200, 0x00010200, 0x30000200, 0x02000200,
    0x11000200, 0x01000200, 0x20000200, 0x00000210, 0x10000200, 0x00000200, 0x50000100, 0x00040100, 0x10030100, 0x00030100, 0x20020100,
    0x01020100, 0x10020100, 0x00020100, 0x30010100, 0x02010100, 0x11010100, 0x01010100, 0x20010100, 0x00010110, 0x10010100, 0x00010100,
    0x40000100, 0x03000100, 0x12000100, 0x02000100, 0x21000100, 0x01000110, 0x11000100, 0x01000100, 0x30000100, 0x00000120, 0x10000110,
    0x00000110, 0x20000100, 0x00001100, 0x10000100, 0x00000100, 0x60000000, 0x00050000, 0x10040000, 0x00040000, 0x20030000, 0x01030000,
    0x10030000, 0x00030000, 0x30020000, 0x02020000, 0x11020000, 0x01020000, 0x20020000, 0x00020010, 0x10020000, 0x00020000, 0x40010000,
    0x03010000, 0x12010000, 0x02010000, 0x21010000, 0x01010010, 0x11010000, 0x01010000, 0x30010000, 0x00010020, 0x10010010, 0x00010010,
    0x20010000, 0x00011000, 0x10010000, 0x00010000, 0x50000000, 0x04000000, 0x13000000, 0x03000000, 0x22000000, 0x02000010, 0x12000000,
    0x02000000, 0x31000000, 0x01000020, 0x11000010, 0x01000010, 0x21000000, 0x01001000, 0x11000000, 0x01000000, 0x40000000, 0x00000030,
    0x10000020, 0x00000020, 0x20000010, 0x00001010, 0x10000010, 0x00000010, 0x30000000, 0x00002000, 0x10001000, 0x00001000, 0x20000000,
    0x00100000, 0x10000000, 0x00000000,
};

static const uint8_t RICE_LEN[256] = {
    0, 1, 1, 2, 1, 2, 2, 3, 1, 2, 2, 3, 2, 3, 3, 4, 1, 2, 2, 3, 2, 3, 3, 4, 2, 3, 3, 4, 3, 4, 4, 5, 1, 2, 2, 3, 2, 3, 3, 4, 2, 3, 3, 4, 3,
    4, 4, 5, 2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6, 1, 2, 2, 3, 2, 3, 3, 4, 2, 3, 3, 4, 3, 4, 4, 5, 2, 3, 3, 4, 3, 4, 4, 5, 3, 4,
    4, 5, 4, 5, 5, 6, 2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6, 3, 4, 4, 5, 4, 5, 5, 6, 4, 5, 5, 6, 5, 6, 6, 7, 1, 2, 2, 3, 2, 3, 3,
    4, 2, 3, 3, 4, 3, 4, 4, 5, 2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6, 2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6, 3, 4, 4, 5,
    4, 5, 5, 6, 4, 5, 5, 6, 5, 6, 6, 7, 2, 3, 3, 4, 3, 4, 4, 5, 3, 4, 4, 5, 4, 5, 5, 6, 3, 4, 4, 5, 4, 5, 5, 6, 4, 5, 5, 6, 5, 6, 6, 7, 3,
    4, 4, 5, 4, 5, 5, 6, 4, 5, 5, 6, 5, 6, 6, 7, 4, 5, 5, 6, 5, 6, 6, 7, 5, 6, 6, 7, 6, 7, 7, 8,
};

#define SYMS_LEN 1280
#define CODE_LEN_LEN (512 + 16)

typedef struct {
    size_t p;
    size_t p_end;
    int32_t bit_pos;
} BitReader2;

static int golomb_rice_lengths(const uint8_t *src, size_t src_len, uint8_t *dst, size_t dst_len, size_t size, BitReader2 *br) {
    size_t p = br->p;
    size_t p_end = br->p_end;
    size_t dst_pos = 0;
    if (p >= p_end) return 0;
    int32_t count = -br->bit_pos;
    uint32_t v = (uint32_t)at_byte(src, src_len, p) & shr(255, br->bit_pos > 0 ? br->bit_pos : 0);
    p++;
    for (;;) {
        if (v == 0) {
            count += 8;
        } else {
            uint32_t x = RICE_VAL[v];
            write_le32(dst, dst_len, dst_pos, (uint32_t)count + (x & 0x0F0F0F0Fu));
            write_le32(dst, dst_len, dst_pos + 4, (x >> 4) & 0x0F0F0F0Fu);
            dst_pos += RICE_LEN[v];
            if (dst_pos >= size) break;
            count = (int32_t)(x >> 28);
        }
        if (p >= p_end) return 0;
        v = at_byte(src, src_len, p);
        p++;
    }
    if (dst_pos > size) {
        for (size_t i = 0; i < dst_pos - size; i++) v &= v - 1;
    }
    int32_t bitpos = 0;
    if ((v & 1) == 0) {
        p--;
        bitpos = 8 - (int32_t)bsf32(v);
    }
    br->p = p;
    br->bit_pos = bitpos;
    return 1;
}

static int golomb_rice_bits(const uint8_t *src, size_t src_len, uint8_t *dst, size_t dst_len, size_t size, int32_t bitcount, BitReader2 *br) {
    if (bitcount == 0) return 1;
    size_t dst_pos = 0;
    size_t p = br->p;
    int32_t bitpos = br->bit_pos;
    size_t bits_required = (size_t)(bitpos > 0 ? bitpos : 0) + (size_t)bitcount * size;
    size_t bytes_required = (bits_required + 7) / 8;
    if (br->p_end < p || bytes_required > br->p_end - p) return 0;
    br->p = p + (bits_required >> 3);
    br->bit_pos = (int32_t)(bits_required & 7);
    uint64_t bak = read_le64(dst, dst_len, size);
    while (dst_pos < size) {
        uint64_t cur = read_le64(dst, dst_len, dst_pos);
        uint64_t spread, bits;
        if (bitcount == 1) {
            bits = (uint8_t)shr(bswap32(read_le32(src, src_len, p)), 24 - (int64_t)bitpos);
            p += 1;
            bits = (bits | (bits << 28)) & 0xF0000000FULL;
            bits = (bits | (bits << 14)) & 0x3000300030003ULL;
            bits = (bits | (bits << 7)) & 0x0101010101010101ULL;
            spread = cur * 2 + bswap64(bits);
        } else if (bitcount == 2) {
            bits = (uint16_t)shr(bswap32(read_le32(src, src_len, p)), 16 - (int64_t)bitpos);
            p += 2;
            bits = (bits | (bits << 24)) & 0xFF000000FFULL;
            bits = (bits | (bits << 12)) & 0xF000F000F000FULL;
            bits = (bits | (bits << 6)) & 0x0303030303030303ULL;
            spread = cur * 4 + bswap64(bits);
        } else {
            bits = shr(bswap32(read_le32(src, src_len, p)), 8 - (int64_t)bitpos) & 0xFFFFFFu;
            p += 3;
            bits = (bits | (bits << 20)) & 0xFFF00000FFFULL;
            bits = (bits | (bits << 10)) & 0x3F003F003F003FULL;
            bits = (bits | (bits << 5)) & 0x0707070707070707ULL;
            spread = cur * 8 + bswap64(bits);
        }
        write_le64(dst, dst_len, dst_pos, spread);
        dst_pos += 8;
    }
    write_le64(dst, dst_len, size, bak);
    return 1;
}

typedef struct { int32_t sym, num; } Range;

static int32_t huff_convert_to_ranges(Range *range, int32_t num_symbols, int32_t p, const uint8_t *symlen, size_t start, BitReader *bits) {
    int32_t num_ranges = p >> 1;
    int32_t sym_idx = 0;
    size_t off = start;
    if (p & 1) {
        br_refill(bits);
        if (off >= CODE_LEN_LEN) return -1;
        int32_t v = symlen[off++];
        if (v >= 8) return -1;
        uint32_t n = (uint32_t)(v + 1);
        sym_idx = (int32_t)(br_bits_nr(bits, n) + (1u << n) - 1);
    }
    if (num_ranges < 0 || num_ranges >= 128) return -1;
    int32_t syms_used = 0;
    for (int32_t i = 0; i < num_ranges; i++) {
        br_refill(bits);
        if (off + 1 >= CODE_LEN_LEN) return -1;
        uint32_t v = symlen[off];
        if (v >= 9) return -1;
        int32_t num = (int32_t)(br_bits_nr_zero(bits, v) + (1u << v));
        uint32_t v2 = symlen[off + 1];
        if (v2 >= 8) return -1;
        int32_t space = (int32_t)(br_bits_nr(bits, v2 + 1) + (1u << (v2 + 1)) - 1);
        range[i].sym = sym_idx;
        range[i].num = num;
        syms_used += num;
        sym_idx += num + space;
        off += 2;
    }
    if (sym_idx >= 256 || syms_used >= num_symbols || sym_idx + num_symbols - syms_used > 256) return -1;
    range[num_ranges].sym = sym_idx;
    range[num_ranges].num = num_symbols - syms_used;
    return num_ranges + 1;
}

static int32_t huff_code_lengths_old(BitReader *bits, uint8_t *syms, uint32_t *code_prefix) {
    if (br_bit_nr(bits) != 0) {
        int32_t sym = 0, num_symbols = 0, avg = 32;
        int32_t forced = (int32_t)br_bits_nr(bits, 2);
        uint32_t thres = 1u << (31 - (20u >> forced));
        int skip = br_bit(bits) != 0;
        for (;;) {
            if (!skip) {
                if ((bits->bits & 0xFF000000u) == 0) return -1;
                sym += (int32_t)br_bits_nr(bits, 2 * (clz32(bits->bits) + 1)) - 2 + 1;
                if (sym >= 256) break;
            }
            skip = 0;
            br_refill(bits);
            if ((bits->bits & 0xFF000000u) == 0) return -1;
            int32_t n = (int32_t)br_bits_nr(bits, 2 * (clz32(bits->bits) + 1)) - 2 + 1;
            if (sym + n > 256) return -1;
            br_refill(bits);
            num_symbols += n;
            while (n != 0) {
                n--;
                if (bits->bits < thres) return -1;
                uint32_t lz = clz32(bits->bits);
                int32_t v = (int32_t)br_bits_nr(bits, lz + (uint32_t)forced + 1);
                int32_t vv = v + (((int32_t)lz - 1) << forced);
                int32_t codelen = (-(vv & 1) ^ (vv >> 1)) + ((avg + 2) >> 2);
                if (codelen < 1 || codelen > 11) return -1;
                avg = codelen + ((3 * avg + 2) >> 2);
                br_refill(bits);
                uint32_t cp = code_prefix[codelen];
                code_prefix[codelen] = cp + 1;
                if (cp >= SYMS_LEN) return -1;
                syms[cp] = (uint8_t)sym;
                sym++;
            }
            if (sym == 256) break;
        }
        return (sym == 256 && num_symbols >= 2) ? num_symbols : -1;
    }
    int32_t num_symbols = (int32_t)br_bits_nr(bits, 8);
    if (num_symbols == 0) return -1;
    if (num_symbols == 1) {
        syms[0] = (uint8_t)br_bits_nr(bits, 8);
    } else {
        uint32_t codelen_bits = br_bits_nr(bits, 3);
        if (codelen_bits > 4) return -1;
        for (int32_t i = 0; i < num_symbols; i++) {
            br_refill(bits);
            uint32_t sym = br_bits_nr(bits, 8);
            uint32_t codelen = br_bits_nr_zero(bits, codelen_bits) + 1;
            if (codelen > 11) return -1;
            uint32_t cp = code_prefix[codelen];
            code_prefix[codelen] = cp + 1;
            if (cp >= SYMS_LEN) return -1;
            syms[cp] = (uint8_t)sym;
        }
    }
    return num_symbols;
}

static int32_t huff_code_lengths_new(BitReader *bits, uint8_t *syms, uint32_t *code_prefix) {
    int32_t forced_bits = (int32_t)br_bits_nr(bits, 2);
    int32_t num_symbols = (int32_t)br_bits_nr(bits, 8) + 1;
    int32_t fluff = br_fluff(bits, num_symbols);
    uint8_t code_len[CODE_LEN_LEN];
    memset(code_len, 0, sizeof code_len);
    int32_t adj = (24 - bits->bit_pos + 7) >> 3;
    int64_t p2 = (int64_t)bits->p - adj;
    if (p2 < 0) return -1;
    BitReader2 br2 = {(size_t)p2, bits->bound, (bits->bit_pos - 24) & 7};
    if (!golomb_rice_lengths(bits->b, bits->blen, code_len, CODE_LEN_LEN, (size_t)(num_symbols + fluff), &br2)) return -1;
    if (!golomb_rice_bits(bits->b, bits->blen, code_len, CODE_LEN_LEN, (size_t)num_symbols, forced_bits, &br2)) return -1;
    bits->bit_pos = 24;
    bits->p = br2.p;
    bits->bits = 0;
    br_refill(bits);
    bits->bits = shl(bits->bits, br2.bit_pos);
    bits->bit_pos += br2.bit_pos;

    uint32_t running_sum = 0x1e;
    for (int32_t i = 0; i < num_symbols; i++) {
        int32_t v = code_len[i];
        v = -(v & 1) ^ (v >> 1);
        int32_t cl = v + (int32_t)(running_sum >> 2) + 1;
        if (cl < 1 || cl > 11) return -1;
        code_len[i] = (uint8_t)cl;
        running_sum = (uint32_t)((int32_t)running_sum + v);
    }

    Range ranges[128];
    int32_t n_ranges = huff_convert_to_ranges(ranges, num_symbols, fluff, code_len, (size_t)num_symbols, bits);
    if (n_ranges <= 0) return -1;
    size_t cp = 0;
    for (int32_t r = 0; r < n_ranges; r++) {
        int32_t sym = ranges[r].sym, nn = ranges[r].num;
        while (nn != 0) {
            nn--;
            if (cp >= CODE_LEN_LEN) return -1;
            uint32_t clen = code_len[cp++];
            if (clen > 11) return -1;
            uint32_t slot = code_prefix[clen];
            code_prefix[clen] = slot + 1;
            if (slot >= SYMS_LEN) return -1;
            syms[slot] = (uint8_t)sym;
            sym++;
        }
    }
    return num_symbols;
}

static int huff_make_lut(const uint32_t *prefix_cur, HuffLut *lut, const uint8_t *syms) {
    size_t currslot = 0;
    for (int i = 1; i < 11; i++) {
        size_t start = CODE_PREFIX_ORG[i];
        if (prefix_cur[i] < CODE_PREFIX_ORG[i]) return 0;
        size_t count = prefix_cur[i] - CODE_PREFIX_ORG[i];
        if (count != 0) {
            size_t stepsize = (size_t)1 << (11 - i);
            size_t num_to_set = count << (11 - i);
            if (currslot + num_to_set > 2048) return 0;
            if (start + count > SYMS_LEN) return 0;
            memset(lut->bits2len + currslot, i, num_to_set);
            size_t p = currslot;
            for (size_t j = 0; j < count; j++) {
                memset(lut->bits2sym + p, syms[start + j], stepsize);
                p += stepsize;
            }
            currslot += num_to_set;
        }
    }
    if (prefix_cur[11] < CODE_PREFIX_ORG[11]) return 0;
    size_t num_to_set = prefix_cur[11] - CODE_PREFIX_ORG[11];
    if (num_to_set != 0) {
        size_t start = CODE_PREFIX_ORG[11];
        if (currslot + num_to_set > 2048) return 0;
        if (start + num_to_set > SYMS_LEN) return 0;
        memset(lut->bits2len + currslot, 11, num_to_set);
        memcpy(lut->bits2sym + currslot, syms + start, num_to_set);
        currslot += num_to_set;
    }
    return currslot == 2048;
}

static uint32_t reverse11(uint32_t v) {
    uint32_t r = 0;
    for (int i = 0; i < 11; i++) {
        r = (r << 1) | (v & 1);
        v >>= 1;
    }
    return r;
}

static int decode_bytes_core(const uint8_t *src, size_t src_len, uint8_t *out, size_t out_off, size_t out_end,
                             size_t src_off, size_t src_end0, size_t src_mid_org, const HuffLut *lut) {
    int64_t src_i = (int64_t)src_off;
    uint32_t src_bits = 0;
    int32_t src_bitpos = 0;
    int64_t src_mid = (int64_t)src_mid_org;
    uint32_t src_mid_bits = 0;
    int32_t src_mid_bitpos = 0;
    int64_t src_end = (int64_t)src_end0;
    uint32_t src_end_bits = 0;
    int32_t src_end_bitpos = 0;
    size_t dst = out_off;
    const int64_t slen = (int64_t)src_len;
    if (src_i > src_mid || src_mid > src_end || src_end > slen) return 0;
    while (dst < out_end) {
        int64_t bp = (int64_t)((uint32_t)src_bitpos & 31);
        if (src_mid - src_i <= 1) {
            if (src_mid - src_i == 1) {
                if (src_i < 0 || src_i >= slen) return 0;
                src_bits |= shl(src[src_i], bp);
            }
        } else {
            if (src_i < 0 || src_i + 1 >= slen) return 0;
            src_bits |= shl((uint32_t)src[src_i] | ((uint32_t)src[src_i + 1] << 8), bp);
        }
        size_t k = src_bits & 0x7FF;
        int32_t n = lut->bits2len[k];
        src_bitpos -= n;
        src_bits = shr(src_bits, n);
        out[dst++] = lut->bits2sym[k];
        src_i += (7 - src_bitpos) >> 3;
        src_bitpos &= 7;
        if (dst < out_end) {
            int64_t ebp = (int64_t)((uint32_t)src_end_bitpos & 31);
            int64_t mbp = (int64_t)((uint32_t)src_mid_bitpos & 31);
            if (src_end - src_mid <= 1) {
                if (src_end - src_mid == 1) {
                    if (src_mid < 0 || src_mid >= slen) return 0;
                    src_end_bits |= shl(src[src_mid], ebp);
                    src_mid_bits |= shl(src[src_mid], mbp);
                }
            } else {
                if (src_end - 2 < 0 || src_end - 1 >= slen || src_mid < 0 || src_mid + 1 >= slen) return 0;
                uint32_t vv = (uint32_t)src[src_end - 2] | ((uint32_t)src[src_end - 1] << 8);
                src_end_bits |= shl(((vv >> 8) | (vv << 8)) & 0xFFFF, ebp);
                src_mid_bits |= shl((uint32_t)src[src_mid] | ((uint32_t)src[src_mid + 1] << 8), mbp);
            }
            k = src_end_bits & 0x7FF;
            n = lut->bits2len[k];
            out[dst++] = lut->bits2sym[k];
            src_end_bitpos -= n;
            src_end_bits = shr(src_end_bits, n);
            src_end -= (7 - src_end_bitpos) >> 3;
            src_end_bitpos &= 7;
            if (dst < out_end) {
                k = src_mid_bits & 0x7FF;
                n = lut->bits2len[k];
                out[dst++] = lut->bits2sym[k];
                src_mid_bitpos -= n;
                src_mid_bits = shr(src_mid_bits, n);
                src_mid += (7 - src_mid_bitpos) >> 3;
                src_mid_bitpos &= 7;
            }
        }
        if (src_i > src_mid || src_mid > src_end) return 0;
    }
    return src_i == (int64_t)src_mid_org && src_end == src_mid;
}

static int decode_huff(const uint8_t *src, size_t src_len, uint8_t *output, size_t out_len, uint32_t type_div) {
    uint32_t prefix[12];
    memcpy(prefix, CODE_PREFIX_ORG, sizeof prefix);
    uint8_t syms[SYMS_LEN];
    memset(syms, 0, sizeof syms);
    BitReader bits = br_forward(src, src_len, 0, src_len);
    int32_t num_syms;
    if (br_bit_nr(&bits) == 0) {
        num_syms = huff_code_lengths_old(&bits, syms, prefix);
    } else if (br_bit_nr(&bits) == 0) {
        num_syms = huff_code_lengths_new(&bits, syms, prefix);
    } else {
        return 0;
    }
    if (num_syms < 1) return 0;
    int32_t adj = (24 - bits.bit_pos) / 8;
    int64_t sp64 = (int64_t)bits.p - adj;
    if (sp64 < 0) return 0;
    size_t sp = (size_t)sp64;
    if (num_syms == 1) {
        memset(output, syms[0], out_len);
        return 1;
    }
    HuffLut *lut = (HuffLut *)calloc(2, sizeof(HuffLut));
    if (!lut) return 0;
    HuffLut *rev = lut + 1;
    int ok = 0;
    if (!huff_make_lut(prefix, lut, syms)) goto done;
    for (uint32_t i = 0; i < 2048; i++) {
        rev->bits2len[i] = lut->bits2len[reverse11(i)];
        rev->bits2sym[i] = lut->bits2sym[reverse11(i)];
    }
    if (type_div == 1) {
        if (sp + 3 > src_len) goto done;
        size_t split_mid = (size_t)src[sp] | ((size_t)src[sp + 1] << 8);
        sp += 2;
        ok = decode_bytes_core(src, src_len, output, 0, out_len, sp, src_len, sp + split_mid, rev);
        goto done;
    }
    if (sp + 6 > src_len) goto done;
    {
        size_t half = (out_len + 1) >> 1;
        size_t split_mid = (size_t)src[sp] | ((size_t)src[sp + 1] << 8) | ((size_t)src[sp + 2] << 16);
        sp += 3;
        if (split_mid > src_len - sp) goto done;
        size_t src_mid = sp + split_mid;
        size_t split_left = (size_t)src[sp] | ((size_t)src[sp + 1] << 8);
        sp += 2;
        if (src_mid < sp || src_mid - sp < split_left + 2 || src_len - src_mid < 3) goto done;
        size_t split_right = (size_t)src[src_mid] | ((size_t)src[src_mid + 1] << 8);
        if (src_len - (src_mid + 2) < split_right + 2) goto done;
        ok = decode_bytes_core(src, src_len, output, 0, half, sp, src_mid, sp + split_left, rev) &&
             decode_bytes_core(src, src_len, output, half, out_len, src_mid + 2, src_len, src_mid + 2 + split_right, rev);
    }
done:
    free(lut);
    return ok;
}

/* Decodes one entropy-coded byte array into out (capacity out_cap).
 * Returns KR_OK, KR_MALFORMED or a positive unsupported entropy type. */
static int decode_bytes(const uint8_t *src, size_t src_len, uint8_t *out, size_t out_cap, size_t *used, size_t *out_len) {
    if (src_len < 2) return KR_MALFORMED;
    uint32_t chunk_type = (src[0] >> 4) & 7;
    if (chunk_type == 0) {
        size_t size, sp;
        if (src[0] >= 0x80) {
            size = (((size_t)src[0] << 8) | src[1]) & 0xFFF;
            sp = 2;
        } else {
            if (src_len < 3) return KR_MALFORMED;
            size = ((size_t)src[0] << 16) | ((size_t)src[1] << 8) | src[2];
            if (size & ~(size_t)0x3FFFF) return KR_MALFORMED;
            sp = 3;
        }
        if (size > out_cap || src_len - sp < size) return KR_MALFORMED;
        memcpy(out, src + sp, size);
        *used = sp + size;
        *out_len = size;
        return KR_OK;
    }
    size_t src_size, dst_size, sp;
    if (src[0] >= 0x80) {
        if (src_len < 3) return KR_MALFORMED;
        uint32_t bits = ((uint32_t)src[0] << 16) | ((uint32_t)src[1] << 8) | src[2];
        src_size = bits & 0x3FF;
        dst_size = src_size + ((bits >> 10) & 0x3FF) + 1;
        sp = 3;
    } else {
        if (src_len < 5) return KR_MALFORMED;
        uint32_t bits = ((uint32_t)src[1] << 24) | ((uint32_t)src[2] << 16) | ((uint32_t)src[3] << 8) | src[4];
        src_size = bits & 0x3FFFF;
        dst_size = ((((bits >> 18) | ((uint32_t)src[0] << 14)) & 0x3FFFF) + 1);
        if (src_size >= dst_size) return KR_MALFORMED;
        sp = 5;
    }
    if (src_len - sp < src_size || dst_size > out_cap) return KR_MALFORMED;
    if (chunk_type == 2 || chunk_type == 4) {
        if (!decode_huff(src + sp, src_size, out, dst_size, chunk_type >> 1)) return KR_MALFORMED;
    } else {
        return (int)chunk_type;
    }
    *used = sp + src_size;
    *out_len = dst_size;
    return KR_OK;
}

/* ------------------------------------------------------------------------ */
/* LZ                                                                       */

typedef struct {
    uint8_t *lit;  size_t lit_len;
    uint8_t *cmd;  size_t cmd_len;
    int32_t *offs; size_t offs_len;
    int32_t *lens; size_t lens_len;
    /* scratch */
    uint8_t *packed_offs;  size_t packed_offs_len;
    uint8_t *packed_extra; size_t packed_extra_len;
    uint8_t *packed_len;   size_t packed_len_len;
} LzTable;

static int unpack_offsets(const uint8_t *src, size_t src_len, size_t bs_begin, size_t bs_end, int excess_flag, size_t excess_count,
                          int32_t offs_scaling, LzTable *t) {
    BitReader a = br_forward(src, src_len, bs_begin, bs_end);
    BitReader b = br_backward(src, src_len, bs_begin, bs_end);
    size_t u32_len;
    if (!excess_flag) {
        if (b.bits < 0x2000) return 0;
        uint32_t nn = 31 - bsr32(b.bits);
        b.bit_pos += (int32_t)nn;
        b.bits = shl(b.bits, nn);
        br_refill_b(&b);
        nn += 1;
        u32_len = (size_t)(uint32_t)(shr(b.bits, 32 - (int64_t)nn) - 1);
        b.bit_pos += (int32_t)nn;
        b.bits = shl(b.bits, nn);
        br_refill_b(&b);
    } else {
        u32_len = 0;
        for (size_t i = 0; i < t->packed_len_len; i++)
            if (t->packed_len[i] == 255) u32_len++;
    }
    if (u32_len > 512) return 0;
    const uint8_t *po = t->packed_offs;
    size_t pn = t->packed_offs_len;
    if (offs_scaling == 0) {
        size_t i = 0;
        while (i < pn) {
            t->offs[i] = -(int32_t)br_distance(&a, po[i]);
            i++;
            if (i >= pn) break;
            t->offs[i] = -(int32_t)br_distance(&b, po[i]);
            i++;
        }
    } else {
        size_t i = 0;
        while (i < pn) {
            uint32_t cmd = po[i];
            if ((cmd >> 3) > 26) return 0;
            uint32_t off = ((8 + (cmd & 7)) << (cmd >> 3)) | br_more_than_24(&a, cmd >> 3);
            t->offs[i] = (int32_t)(8u - off);
            i++;
            if (i >= pn) break;
            uint32_t cmd2 = po[i];
            if ((cmd2 >> 3) > 26) return 0;
            uint32_t off2 = ((8 + (cmd2 & 7)) << (cmd2 >> 3)) | br_more_than_24(&b, cmd2 >> 3);
            t->offs[i] = (int32_t)(8u - off2);
            i++;
        }
        if (offs_scaling != 1) {
            if (t->packed_extra_len != pn) return 0;
            for (size_t j = 0; j < pn; j++)
                t->offs[j] = (int32_t)((uint32_t)(int32_t)(int8_t)t->packed_extra[j] - (uint32_t)t->offs[j] * (uint32_t)offs_scaling);
        }
    }
    t->offs_len = pn;

    uint32_t u32s[512];
    if (u32_len > 0) {
        BitReader ea, eb;
        BitReader *ra = &a, *rb = &b;
        if (excess_flag) {
            ea = br_forward(src, src_len, bs_end, bs_end + excess_count);
            eb = br_backward(src, src_len, bs_end, bs_end + excess_count);
            ra = &ea;
            rb = &eb;
        }
        size_t i = 0;
        while (i + 1 < u32_len) {
            if (!br_length(ra, &u32s[i]) || !br_length(rb, &u32s[i + 1])) return 0;
            i += 2;
        }
        if (i < u32_len && !br_length(ra, &u32s[i])) return 0;
    }

    size_t u = 0;
    for (size_t i = 0; i < t->packed_len_len; i++) {
        uint32_t v = t->packed_len[i];
        if (v == 255) {
            if (u >= u32_len) return 0;
            v = u32s[u++] + 255;
        }
        t->lens[i] = (int32_t)(v + 3);
    }
    t->lens_len = t->packed_len_len;
    return u == u32_len;
}

static int read_lz_table(const uint8_t *src, size_t src_len, size_t *spp, size_t dst_size, LzTable *t) {
    size_t sp = *spp;
    size_t src_end = src_len;
    int excess_flag = 0;
    size_t excess_count = 0;
    size_t used;
    int st;
    if (sp < src_end && (src[sp] & 0x80)) {
        uint8_t flag = src[sp];
        if ((flag & 0xC0) == 0x80) {
            sp++;
            excess_flag = 1;
            excess_count = flag & 0x3F;
            if (excess_count > 0x1F) {
                if (sp >= src_end) return KR_MALFORMED;
                excess_count += (size_t)src[sp] * 0x20;
                sp++;
            }
            if (src_end < sp + excess_count) return KR_MALFORMED;
            src_end -= excess_count;
        }
    }
#define REGION_OK() do { if (sp > src_end) return KR_MALFORMED; } while (0)
    REGION_OK();
    st = decode_bytes(src + sp, src_end - sp, t->lit, dst_size, &used, &t->lit_len);
    if (st) return st;
    sp += used;
    REGION_OK();
    st = decode_bytes(src + sp, src_end - sp, t->cmd, dst_size, &used, &t->cmd_len);
    if (st) return st;
    sp += used;

    if (src_end < sp || src_end - sp < 3) return KR_MALFORMED;

    int32_t offs_scaling = 0;
    t->packed_extra_len = 0;
    if (src[sp] & 0x80) {
        offs_scaling = (int32_t)src[sp] - 127;
        sp++;
        REGION_OK();
        st = decode_bytes(src + sp, src_end - sp, t->packed_offs, t->cmd_len, &used, &t->packed_offs_len);
        if (st) return st;
        sp += used;
        if (offs_scaling != 1) {
            REGION_OK();
            st = decode_bytes(src + sp, src_end - sp, t->packed_extra, t->packed_offs_len, &used, &t->packed_extra_len);
            if (st) return st;
            if (t->packed_extra_len != t->packed_offs_len) return KR_MALFORMED;
            sp += used;
        }
    } else {
        REGION_OK();
        st = decode_bytes(src + sp, src_end - sp, t->packed_offs, t->cmd_len, &used, &t->packed_offs_len);
        if (st) return st;
        sp += used;
    }

    REGION_OK();
    st = decode_bytes(src + sp, src_end - sp, t->packed_len, dst_size >> 2, &used, &t->packed_len_len);
    if (st) return st;
    sp += used;
#undef REGION_OK

    if (!unpack_offsets(src, src_len, sp, src_end, excess_flag, excess_count, offs_scaling, t)) return KR_MALFORMED;
    *spp = sp;
    return KR_OK;
}

static void copy_match(uint8_t *dst, size_t from, size_t to, size_t len) {
    for (size_t c = 0; c < len; c++) dst[to + c] = dst[from + c];
}

static int process_type1(const LzTable *t, uint8_t *dst, size_t dst_len, size_t start, size_t dst_end) {
    size_t cmd_i = 0, len_i = 0, lit_i = 0, offs_i = 0;
    size_t dst_pos = start;
    int32_t recent[7] = {0, 0, 0, -8, -8, -8, 0};
    while (cmd_i < t->cmd_len) {
        uint32_t f = t->cmd[cmd_i++];
        size_t litlen = f & 3;
        size_t offs_index = f >> 6;
        uint32_t matchlen = (f >> 2) & 0xF;
        if (litlen == 3) {
            if (len_i >= t->lens_len) return 0;
            litlen = (size_t)(uint32_t)t->lens[len_i++];
        }
        recent[6] = offs_i < t->offs_len ? t->offs[offs_i] : 0;
        if (litlen != 0) {
            if (lit_i + litlen > t->lit_len || dst_pos + litlen > dst_end) return 0;
            memcpy(dst + dst_pos, t->lit + lit_i, litlen);
            dst_pos += litlen;
            lit_i += litlen;
        }
        int32_t offset = recent[offs_index + 3];
        recent[offs_index + 3] = recent[offs_index + 2];
        recent[offs_index + 2] = recent[offs_index + 1];
        recent[offs_index + 1] = recent[offs_index];
        recent[3] = offset;
        offs_i += ((offs_index + 1) & 4) >> 2;
        int64_t from = (int64_t)dst_pos + offset;
        if (from < 0 || (size_t)from >= dst_pos) return 0;
        size_t actual;
        if (matchlen != 15) {
            actual = matchlen + 2;
        } else {
            if (len_i >= t->lens_len) return 0;
            actual = 14 + (size_t)(uint32_t)t->lens[len_i++];
        }
        if (dst_pos + actual > dst_end || (size_t)from + actual > dst_len) return 0;
        copy_match(dst, (size_t)from, dst_pos, actual);
        dst_pos += actual;
    }
    if (offs_i != t->offs_len || len_i != t->lens_len) return 0;
    size_t final_len = dst_end - dst_pos;
    if (final_len != t->lit_len - lit_i) return 0;
    memcpy(dst + dst_pos, t->lit + lit_i, final_len);
    return 1;
}

static void delta_literals(uint8_t *dst, size_t dst_len, size_t at, const uint8_t *lit, size_t n, int32_t last_offset) {
    for (size_t c = 0; c < n; c++) {
        int64_t back = (int64_t)(at + c) + last_offset;
        uint8_t prev = (back >= 0 && (size_t)back < dst_len) ? dst[back] : 0;
        dst[at + c] = (uint8_t)(lit[c] + prev);
    }
}

static int process_type0(const LzTable *t, uint8_t *dst, size_t dst_len, size_t start, size_t dst_end) {
    size_t cmd_i = 0, len_i = 0, lit_i = 0, offs_i = 0;
    size_t dst_pos = start;
    int32_t recent[7] = {0, 0, 0, -8, -8, -8, 0};
    int32_t last_offset = -8;
    while (cmd_i < t->cmd_len) {
        uint32_t f = t->cmd[cmd_i++];
        size_t litlen = f & 3;
        size_t offs_index = f >> 6;
        uint32_t matchlen = (f >> 2) & 0xF;
        if (litlen == 3) {
            if (len_i >= t->lens_len) return 0;
            litlen = (size_t)(uint32_t)t->lens[len_i++];
        }
        recent[6] = offs_i < t->offs_len ? t->offs[offs_i] : 0;
        if (litlen != 0) {
            if (lit_i + litlen > t->lit_len || dst_pos + litlen > dst_end) return 0;
            delta_literals(dst, dst_len, dst_pos, t->lit + lit_i, litlen, last_offset);
            dst_pos += litlen;
            lit_i += litlen;
        }
        int32_t offset = recent[offs_index + 3];
        recent[offs_index + 3] = recent[offs_index + 2];
        recent[offs_index + 2] = recent[offs_index + 1];
        recent[offs_index + 1] = recent[offs_index];
        recent[3] = offset;
        last_offset = offset;
        offs_i += ((offs_index + 1) & 4) >> 2;
        size_t actual;
        if (matchlen != 15) {
            actual = matchlen + 2;
        } else {
            if (len_i >= t->lens_len) return 0;
            actual = 14 + (size_t)(uint32_t)t->lens[len_i++];
        }
        if (dst_pos + actual > dst_end) return 0;
        int64_t from = (int64_t)dst_pos + offset;
        if (from < 0 || (size_t)from >= dst_pos) return 0;
        copy_match(dst, (size_t)from, dst_pos, actual);
        dst_pos += actual;
    }
    if (offs_i != t->offs_len || len_i != t->lens_len) return 0;
    size_t rest = t->lit_len - lit_i;
    if (dst_pos + rest != dst_end) return 0;
    delta_literals(dst, dst_len, dst_pos, t->lit + lit_i, rest, last_offset);
    return 1;
}

static int decode_chunk(const uint8_t *src, size_t src_len, uint8_t *dst, size_t dst_full_len, size_t dst_start, size_t dst_len,
                        int with_seed, uint32_t literal_mode) {
    size_t sp = 0;
    if (with_seed) {
        if (src_len < SEED_SIZE || dst_len < SEED_SIZE) return KR_MALFORMED;
        memcpy(dst + dst_start, src, SEED_SIZE);
        sp = SEED_SIZE;
    }
    /* One allocation for every table and scratch array. */
    size_t cap = dst_len + 16;
    size_t cap4 = (dst_len >> 2) + 16;
    size_t total = cap * 4 + cap4 + cap * sizeof(int32_t) + cap4 * sizeof(int32_t);
    uint8_t *mem = (uint8_t *)malloc(total);
    if (!mem) return KR_NOMEM;
    LzTable t;
    memset(&t, 0, sizeof t);
    uint8_t *m = mem;
    t.lit = m; m += cap;
    t.cmd = m; m += cap;
    t.packed_offs = m; m += cap;
    t.packed_extra = m; m += cap;
    t.packed_len = m; m += cap4;
    t.offs = (int32_t *)m; m += cap * sizeof(int32_t);
    t.lens = (int32_t *)m;

    int st = read_lz_table(src, src_len, &sp, dst_len, &t);
    if (st == KR_OK) {
        size_t start = dst_start + (with_seed ? SEED_SIZE : 0);
        size_t end = dst_start + dst_len;
        int ok;
        if (literal_mode == 1) ok = process_type1(&t, dst, dst_full_len, start, end);
        else if (literal_mode == 0) ok = process_type0(&t, dst, dst_full_len, start, end);
        else ok = 0;
        st = ok ? KR_OK : KR_MALFORMED;
    }
    free(mem);
    return st;
}

static int decode_sub_chunk(const uint8_t *src, size_t src_len, uint8_t *dst, size_t dst_full_len, size_t dst_start, size_t dst_len,
                            int lz, int restart, uint32_t literal_mode) {
    if (dst_start + dst_len > dst_full_len) return KR_MALFORMED;
    if (src_len == dst_len) {
        memcpy(dst + dst_start, src, dst_len);
        return KR_OK;
    }
    if (lz) return decode_chunk(src, src_len, dst, dst_full_len, dst_start, dst_len, restart, literal_mode);
    size_t used = 0, decoded = 0;
    int st = decode_bytes(src, src_len, dst + dst_start, dst_len, &used, &decoded);
    if (st) return st;
    if (used != src_len || decoded != dst_len) return KR_MALFORMED;
    return KR_OK;
}

/* Decodes one stored block into dst (dst_len bytes, normally 256 KiB).
 * flags: the NAPS kraken flags (kde_predictor | shuffle_idx << 4).
 * first_chunk_comp: compressed length of the first 128 KiB chunk.
 * Returns 0 on success, -1 malformed, -2 out of memory, or a positive
 * unsupported entropy type. */
EXPORT int kraken_decode_block(const uint8_t *payload, size_t payload_len, uint32_t flags, uint32_t first_chunk_comp,
                               uint8_t *dst, size_t dst_len) {
    if (dst_len == 0) return payload_len == 0 ? KR_OK : KR_MALFORMED;
    uint32_t sub0 = (flags & CHUNK0_SUB_LIT) ? 0 : 1;
    uint32_t sub1 = (flags & CHUNK1_SUB_LIT) ? 0 : 1;
    int one_chunk = first_chunk_comp == 0 || (size_t)first_chunk_comp >= payload_len || dst_len <= CHUNK_MAX;
    if (one_chunk) return decode_sub_chunk(payload, payload_len, dst, dst_len, 0, dst_len, (flags & CHUNK0_NEW_LZ) != 0, 1, sub0);
    size_t chunk0_dst = dst_len < CHUNK_MAX ? dst_len : CHUNK_MAX;
    size_t chunk1_dst = dst_len - chunk0_dst;
    size_t chunk0_comp = first_chunk_comp;
    size_t chunk1_comp = payload_len - chunk0_comp;
    if (chunk1_dst == 0 && chunk1_comp != 0) return KR_MALFORMED;
    int st = decode_sub_chunk(payload, chunk0_comp, dst, dst_len, 0, chunk0_dst, (flags & CHUNK0_NEW_LZ) != 0, 1, sub0);
    if (st) return st;
    if (chunk1_dst > 0)
        st = decode_sub_chunk(payload + chunk0_comp, chunk1_comp, dst, dst_len, chunk0_dst, chunk1_dst, (flags & CHUNK1_NEW_LZ) != 0,
                              (flags & CHUNK1_RESTART) != 0, sub1);
    return st;
}

EXPORT int kraken_abi_version(void) { return 1; }

/* Test hook mirroring the Rust unit tests: decode one entropy array. */
EXPORT int kraken_test_decode_bytes(const uint8_t *src, size_t src_len, uint8_t *out, size_t out_cap, size_t *used, size_t *out_len) {
    return decode_bytes(src, src_len, out, out_cap, used, out_len);
}
