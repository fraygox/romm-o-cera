#!/usr/bin/env python3
"""Synthetic Wii U disc images for wuatool's self-test and RomM-o-Cera's tests.

  make_disc(path, keys, files=None, wux=False) -> {path in the game partition: bytes}

Writes a small disc image the way Cemu's FST/WUD code reads one: header, a
partition table encrypted with the disc key, an SI partition whose FST (disc
key) holds the game partition's ticket, and a GM partition whose FST and files
are encrypted with the ticket's title key (itself encrypted with the "common
key"). The game partition has one unhashed cluster and one hashed cluster
(0x10000-byte blocks with H0/H1/H2 hashes), like real discs. .wux: the same
image in the deduplicated WUX container.

Keys are whatever the caller passes; tests make random ones (random_keys()).
Nothing here is a real key. Needs the cryptography module."""
import hashlib
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SECTOR = 0x8000
BLOCK, HASHES, DATA = 0x10000, 0x400, 0xFC00
OFFSET_FACTOR = 0x20
TITLE_ID = 0x0005000010ABCD00
TITLE_VERSION = 16


def random_keys():
    """{"disc", "title", "common"}: 16 random bytes each (never a real key)."""
    return {k: os.urandom(16) for k in ("disc", "title", "common")}


def _enc(key, iv, data):
    assert len(data) % 16 == 0
    e = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return e.update(data) + e.finalize()


def _pad(data, n):
    return data + b"\0" * ((-len(data)) % n)


def app_xml(title_id=TITLE_ID, version=TITLE_VERSION):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<app type="complex" access="777">\n'
            '  <version type="unsignedInt" length="4">16</version>\n'
            '  <title_id type="hexBinary" length="8">%016X</title_id>\n'
            '  <title_version type="unsignedInt" length="4">%d</title_version>\n'
            '</app>\n' % (title_id, version)).encode()


def default_files(seed=1, title_id=TITLE_ID, version=TITLE_VERSION):
    """The game partition: (path, data, cluster) with cluster "raw" or "hashed"."""
    import random
    rnd = random.Random(seed)

    def noise(n):
        return bytes(rnd.getrandbits(8) for _ in range(n))
    return [
        ("code/app.xml", app_xml(title_id, version), "raw"),
        ("code/cos.xml", b"<?xml version=\"1.0\"?><app/>\n", "raw"),
        ("code/lorem.rpx", noise(70000), "raw"),
        ("content/big.bin", noise(DATA * 18 + 1234), "hashed"),    # more than 16 blocks: two H0 groups
        ("content/text.txt", b"lorem ipsum dolor sit amet " * 3000, "hashed"),
        ("content/sub/deep.dat", noise(5000), "hashed"),
        ("content/empty.bin", b"", "hashed"),
        ("meta/meta.xml", b"<?xml version=\"1.0\"?><menu/>\n", "raw"),
    ]


class _Fst:
    """An FST: a tree of folders and files, each file in a cluster at an offset."""

    def __init__(self):
        self.entries = []      # [type, name, a, b, cluster, flags] (a/b: parent/end or offset/size)
        self.names = bytearray(b"\0")

    def name(self, s):
        off = len(self.names)
        self.names += s.encode() + b"\0"
        return off

    def build(self, files, clusters):
        """files: [(path, offset in its cluster, size, cluster index, link)], sorted by path;
        clusters: [(sector offset in the partition, size, hash mode)]."""
        tree = {}
        for f in files:
            node = tree
            parts = f[0].split("/")
            for d in parts[:-1]:
                node = node.setdefault(d, {})
            node[parts[-1]] = f
        ents = [[1, 0, 0, 0, 0, 0]]          # root: name "" at offset 0

        def add(node, parent):
            for nm in sorted(node, key=lambda n: (isinstance(node[n], dict), n)):
                v = node[nm]
                if isinstance(v, dict):
                    i = len(ents)
                    ents.append([1, self.name(nm), parent, 0, 0, 0])
                    add(v, i)
                    ents[i][3] = len(ents)
                else:
                    path, off, size, ci, link = v
                    ents.append([0, self.name(nm), off // OFFSET_FACTOR, size, ci, 0x80 if link else 0])
        add(tree, 0)
        ents[0][3] = len(ents)
        out = bytearray(b"FST\0" + struct.pack(">II", OFFSET_FACTOR, len(clusters)) + b"\0" * 20)
        for sec, size, mode in clusters:
            out += struct.pack(">IIQIB", sec, size, TITLE_ID, 0, mode) + b"\0" * 11
        for typ, nm, a, b, ci, flags in ents:
            out += struct.pack(">IIIHH", ((typ | flags) << 24) | nm, a, b, 0, ci)
        out += self.names
        return bytes(out)


def _raw_cluster(key, index, data):
    """An unhashed cluster: one CBC stream, IV = the cluster index."""
    iv = bytes([index >> 8 & 0xFF, index & 0xFF]) + b"\0" * 14
    return _enc(key, iv, _pad(data, SECTOR))


def _hashed_cluster(key, data):
    """A hashed cluster: 0x10000-byte blocks, 0x400 bytes of H0/H1/H2 hashes then 0xFC00 of data."""
    blocks = [_pad(data[i:i + DATA], DATA) for i in range(0, max(len(data), 1), DATA)]
    h0 = [hashlib.sha1(b).digest() for b in blocks]
    h0 += [hashlib.sha1(b"\0" * DATA).digest()] * ((-len(h0)) % 256)
    h1 = [hashlib.sha1(b"".join(h0[i:i + 16])).digest() for i in range(0, len(h0), 16)]
    h2 = [hashlib.sha1(b"".join(h1[i:i + 16])).digest() for i in range(0, len(h1), 16)]
    h2 += [b"\0" * 20] * ((-len(h2)) % 16)
    out = bytearray()
    for i, b in enumerate(blocks):
        g0, g1, g2 = i // 16 * 16, i // 256 * 16, i // 4096 * 16
        hashes = b"".join(h0[g0:g0 + 16]) + b"".join(h1[g1:g1 + 16]) + b"".join(h2[g2:g2 + 16])
        hashes = hashes.ljust(HASHES, b"\0")
        out += _enc(key, b"\0" * 16, hashes) + _enc(key, h0[i][:16], b)
    return bytes(out)


def _partition(key, files, hdr_hash_type, enc_type):
    """(partition bytes, {path: data}) with files laid out over clusters 1 (raw) and 2 (hashed)."""
    streams = {"raw": bytearray(), "hashed": bytearray()}
    layout = []
    for path, data, kind, *link in files:
        s = streams[kind]
        s += b"\0" * ((-len(s)) % OFFSET_FACTOR)
        layout.append((path, len(s), len(data), 1 if kind == "raw" else 2, bool(link and link[0])))
        s += data
    raw = _raw_cluster(key, 1, bytes(streams["raw"]))
    hashed = _hashed_cluster(key, bytes(streams["hashed"])) if streams["hashed"] else b""
    fst_sectors = 2
    raw_sec = 1 + fst_sectors
    hashed_sec = raw_sec + len(raw) // SECTOR
    clusters = [(1, fst_sectors * SECTOR, 0), (raw_sec, len(raw), 0), (hashed_sec, len(hashed), 2)]   # 2: Cemu's HASH_INTERLEAVED
    fst = _Fst().build(sorted(layout), clusters)
    assert len(fst) <= fst_sectors * SECTOR
    enc_fst = _enc(key, b"\0" * 16, _pad(fst, 16))
    head = struct.pack(">IIIIIIIII", 0xCC93A4F5, SECTOR, 0, 0, 0, len(fst), 1, 0, 0) + bytes([hdr_hash_type, enc_type])
    part = _pad(head, SECTOR) + _pad(enc_fst, SECTOR * fst_sectors) + raw + _pad(hashed, SECTOR)
    return part, {p: d for p, d, _k, *link in files if not (link and link[0])}


def ticket(keys, title_id=TITLE_ID):
    t = bytearray(0x2A4)
    struct.pack_into(">I", t, 0, 0x00010004)
    iv = struct.pack(">Q", title_id) + b"\0" * 8
    t[0x1BF:0x1CF] = _enc(keys["common"], iv, keys["title"])
    struct.pack_into(">II", t, 0x1DC, title_id >> 32, title_id & 0xFFFFFFFF)
    return bytes(t)


UPDATE_ID = TITLE_ID | 0xE00000000         # the game's update (0005000E...), as some discs carry it
DLC_ID = TITLE_ID | 0xC00000000            # its DLC (0005000C...)


def make_disc(path, keys, files=None, wux=False, title_id=TITLE_ID, version=TITLE_VERSION, seed=1,
              extra=(), up=False, titles=None):
    """Write the disc image; returns {path in the game partition: bytes} (what a .wua must hold).
    extra: more game partitions after the base, [(title id, version, seed)] (an update, DLC);
    up: also a console-firmware partition ("UP", never part of a .wua), before the game's.
    titles: a dict that gets {"<title id>_v<version>": {path: bytes}} for every game partition."""
    files = files if files is not None else default_files(seed, title_id, version)
    gms = [(title_id, version, files)] + [(t, v, default_files(sd, t, v)) for t, v, sd in extra]
    names = [b"SI"] + ([b"UP%016X" % 0x0005001010040100] if up else []) + [b"GM%016X" % t for t, _, _ in gms]
    first_gm = len(names) - len(gms)
    tik = [("%02x/title.tik" % (first_gm + i), ticket(keys, t), "raw") for i, (t, _, _) in enumerate(gms)]
    tmd = [("%02x/title.tmd" % (first_gm + i), b"\0" * 0x200, "raw") for i in range(len(gms))]
    si, _ = _partition(keys["disc"], sorted(tik + tmd) + [("01/title.cert", b"\0" * 0x400, "raw")], 0, 1)
    parts = [si] + ([_enc(keys["disc"], b"\0" * 16, bytes(range(256)) * 128)] if up else [])
    game = None
    for i, (t, v, fs) in enumerate(gms):
        gm, got = _partition(keys["title"], fs, 1, 2)
        parts.append(gm)
        game = got if game is None else game
        if titles is not None:
            titles["%016x_v%d" % (t, v)] = got
    table = bytearray(SECTOR)
    struct.pack_into(">II", table, 0, 0xCCA6E67B, SECTOR)
    struct.pack_into(">I", table, 0x1C, len(names))
    sec = 4
    for i, (name, part) in enumerate(zip(names, parts)):
        e = 0x800 + i * 0x80
        table[e:e + len(name)] = name
        table[e + 0x1F] = 1
        struct.pack_into(">I", table, e + 0x20, sec)
        sec += len(part) // SECTOR
    disc = bytearray(SECTOR * 4)
    disc[0:10] = b"WUP-P-ABCD"
    struct.pack_into(">I", disc, 0x10000, 0xCC549EB9)
    disc[0x18000:0x20000] = _enc(keys["disc"], b"\0" * 16, bytes(table))
    disc += b"".join(parts)
    with open(path, "wb") as f:
        f.write(to_wux(bytes(disc)) if wux else disc)
    return game


def to_wux(disc):
    """The WUX container: header, sector index, each distinct sector stored once."""
    sectors = [disc[i:i + SECTOR].ljust(SECTOR, b"\0") for i in range(0, len(disc), SECTOR)]
    seen, index, stored = {}, [], []
    for s in sectors:
        if s not in seen:
            seen[s] = len(stored)
            stored.append(s)
        index.append(seen[s])
    head = b"WUX0" + struct.pack("<IIIQII", 0x1099D02E, SECTOR, 0, len(disc), 0, 0)
    table = head + struct.pack("<%dI" % len(index), *index)
    return _pad(table, SECTOR) + b"".join(stored)


def flip(path, offset):
    """Damage one byte."""
    with open(path, "r+b") as f:
        f.seek(offset)
        b = f.read(1)
        f.seek(offset)
        f.write(bytes([b[0] ^ 0xFF]))


def hashed_cluster_offset(path):
    """Byte offset of the game partition's hashed cluster in an uncompressed image made here."""
    with open(path, "rb") as f:
        data = f.read()
    # the GM partition header is the second partition magic after the SI partition's
    first = data.find(struct.pack(">I", 0xCC93A4F5))
    gm = data.find(struct.pack(">I", 0xCC93A4F5), first + SECTOR)
    return gm, data


if __name__ == "__main__":
    import sys
    k = random_keys()
    make_disc(sys.argv[1], k, wux=sys.argv[1].lower().endswith(".wux"))
    print("made %s with random keys (not saved)" % sys.argv[1])
