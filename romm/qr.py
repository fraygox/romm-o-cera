"""QR codes: a small encoder (byte mode, versions 1-6, any error-correction
level), enough for a link the UI shows so a phone can open it. Pure Python,
written to ISO/IEC 18004; no sibling imports."""

# per version 1-6: (EC codewords per block, blocks) for each level; total codewords
QR_EC = {
    "L": ((7, 1), (10, 1), (15, 1), (20, 1), (26, 1), (18, 2)),
    "M": ((10, 1), (16, 1), (26, 1), (18, 2), (24, 2), (16, 4)),
    "Q": ((13, 1), (22, 1), (18, 2), (26, 2), (18, 4), (24, 4)),
    "H": ((17, 1), (28, 1), (22, 2), (16, 4), (22, 4), (28, 4)),
}
QR_CODEWORDS = (26, 44, 70, 100, 134, 172)
_QR_FORMAT_ECL = {"L": 1, "M": 0, "Q": 3, "H": 2}     # the two format bits that name the level
_QR_MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


def _qr_gf_mul(a, b):
    """a * b in GF(256) over the QR polynomial x^8 + x^4 + x^3 + x^2 + 1."""
    r = 0
    for i in range(7, -1, -1):
        r = (r << 1) ^ ((r >> 7) * 0x11D)
        r ^= ((b >> i) & 1) * a
    return r


def _qr_ec_bytes(data, n):
    """The n Reed-Solomon error-correction codewords for one block."""
    gen, root = [0] * (n - 1) + [1], 1          # generator polynomial, highest term implied
    for _ in range(n):
        for j in range(n):
            gen[j] = _qr_gf_mul(gen[j], root)
            if j + 1 < n:
                gen[j] ^= gen[j + 1]
        root = _qr_gf_mul(root, 2)
    rem = [0] * n
    for b in data:
        f = b ^ rem.pop(0)
        rem.append(0)
        for i, g in enumerate(gen):
            rem[i] ^= _qr_gf_mul(g, f)
    return rem


def qr_capacity(version, ecl):
    """Bytes of text one code holds (byte mode)."""
    ec, blocks = QR_EC[ecl][version - 1]
    return QR_CODEWORDS[version - 1] - ec * blocks - 2      # 4 bits mode + 8 bits length, rounded up


def _qr_codewords(data, version, ecl):
    """data's codewords: mode, length, data and padding, split into blocks with
    their error correction, interleaved."""
    ec, nblocks = QR_EC[ecl][version - 1]
    ndata = QR_CODEWORDS[version - 1] - ec * nblocks
    bits = [0, 1, 0, 0] + [(len(data) >> i) & 1 for i in range(7, -1, -1)]
    for b in data:
        bits += [(b >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, ndata * 8 - len(bits))         # terminator
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < ndata:
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    short = nblocks - ndata % nblocks                   # blocks with one data codeword fewer
    blocks, k = [], 0
    for i in range(nblocks):
        n = ndata // nblocks + (i >= short)
        blocks.append(words[k:k + n])
        k += n
    out = []
    for i in range(max(len(b) for b in blocks)):
        out += [b[i] for b in blocks if i < len(b)]
    eccs = [_qr_ec_bytes(b, ec) for b in blocks]
    for i in range(ec):
        out += [e[i] for e in eccs]
    return out


def _qr_format_bits(ecl, mask):
    """The 15 format bits (error-correction level and mask, BCH-protected)."""
    d = _QR_FORMAT_ECL[ecl] << 3 | mask
    rem = d
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return (d << 10 | rem) ^ 0x5412


def _qr_penalty(m):
    """ISO 18004's mask score: lower reads better."""
    n, score = len(m), 0
    lines = m + [list(col) for col in zip(*m)]
    for line in lines:                                  # runs of 5+ alike
        run = 1
        for i in range(1, n + 1):
            if i < n and line[i] == line[i - 1]:
                run += 1
                continue
            if run >= 5:
                score += run - 2
            run = 1
        s = "".join("1" if v else "0" for v in line)     # finder-like 1:1:3:1:1 beside 4 light
        for pat in ("00001011101", "10111010000"):
            i = s.find(pat)
            while i >= 0:
                score += 40
                i = s.find(pat, i + 1)
    for y in range(n - 1):                              # 2x2 blocks alike
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(map(sum, m))
    score += 10 * (abs(dark * 20 - n * n * 10) // (n * n))     # dark share away from 50%, per 5%
    return score


def qr_matrix(data, ecl="M", mask=None):
    """The QR code for data (str: UTF-8) as rows of booleans (True: dark), no
    quiet zone. The smallest version (1-6) that holds it; a higher level of
    error correction when that fits in the same size. ValueError when it's too
    long (106 bytes at level M)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    order = "LMQH"
    version = next((v for v in range(1, 7) if qr_capacity(v, ecl) >= len(data)), None)
    if version is None:
        raise ValueError("too long for a QR code here: %d bytes" % len(data))
    for better in order[order.index(ecl) + 1:]:
        if qr_capacity(version, better) >= len(data):
            ecl = better
    n = 17 + 4 * version
    m = [[False] * n for _ in range(n)]
    fixed = [[False] * n for _ in range(n)]

    def put(x, y, dark):
        m[y][x], fixed[y][x] = dark, True

    for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):     # finders and their light borders
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < n and 0 <= y < n:
                    d = max(abs(dx), abs(dy))
                    put(x, y, d not in (2, 4))
    for i in range(8, n - 8):                           # timing
        put(i, 6, i % 2 == 0)
        put(6, i, i % 2 == 0)
    if version > 1:                                     # the one alignment pattern (versions 2-6)
        c = n - 7
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                put(c + dx, c + dy, max(abs(dx), abs(dy)) != 1)
    for i in range(9):                                  # format areas, filled in below
        for x, y in ((8, i), (i, 8)):
            fixed[y][x] = True
    for i in range(8):
        fixed[8][n - 1 - i] = fixed[n - 1 - i][8] = True
    words = _qr_codewords(data, version, ecl)
    bits = [(w >> (7 - i)) & 1 for w in words for i in range(8)]
    k = 0
    right = n - 1
    while right >= 1:                                   # two-column zigzag from the bottom right
        if right == 6:
            right = 5
        up = ((right + 1) & 2) == 0
        for vert in range(n):
            y = n - 1 - vert if up else vert
            for x in (right, right - 1):
                if not fixed[y][x]:
                    m[y][x] = bool(bits[k]) if k < len(bits) else False
                    k += 1
        right -= 2

    def masked(mk):
        out = [row[:] for row in m]
        f = _QR_MASKS[mk]
        for y in range(n):
            for x in range(n):
                if not fixed[y][x] and f(x, y):
                    out[y][x] = not out[y][x]
        fb = _qr_format_bits(ecl, mk)
        bit = [bool((fb >> i) & 1) for i in range(15)]
        for i in range(6):
            out[i][8] = bit[i]
        out[7][8], out[8][8], out[8][7] = bit[6], bit[7], bit[8]
        for i in range(9, 15):
            out[8][14 - i] = bit[i]
        for i in range(8):
            out[8][n - 1 - i] = bit[i]
        for i in range(8, 15):
            out[n - 15 + i][8] = bit[i]
        out[n - 8][8] = True                            # the dark module
        return out
    if mask is not None:
        return masked(mask)
    return min((masked(mk) for mk in range(8)), key=_qr_penalty)
