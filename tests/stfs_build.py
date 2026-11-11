"""Builds small read-only Xbox 360 packages (STFS) for tests, laid out by hand
the way a console's are: a header, then groups of one hash table and the 170
data blocks it covers, with the second-level table after the first group."""
import hashlib

BLOCK = 0x1000


def _place(n):
    """Where data block n sits, in blocks from the package's first table."""
    if n < 170:
        return 1 + n                         # table 0, then blocks 0..169
    return 173 + (n - 170)                   # ... the level-1 table (171), table 1 (172), blocks 170..339


def build_package(files, kind=0x00000002, title=0x4D2A0001, magic=b"PIRS", folders=()):
    """files: {path: bytes} (paths with "/" need their folder in folders).
    Up to 340 data blocks."""
    names = list(folders) + list(files)
    blocks, chains = [None], {}              # block 0: the file table
    for path, data in files.items():
        first = len(blocks) if data else 0
        for i in range(0, len(data), BLOCK):
            blocks.append(data[i:i + BLOCK].ljust(BLOCK, b"\0"))
        chains[path] = (first, -(-len(data) // BLOCK))
    assert len(blocks) <= 340 and len(names) <= 64
    table = b""
    for path in names:
        base = path.rsplit("/", 1)[-1].encode("cp1252")
        parent = names.index(path.rsplit("/", 1)[0]) if "/" in path else 0xFFFF
        if path in files:
            first, count = chains[path]
            e = base.ljust(0x28, b"\0") + bytes([len(base) | 0x40]) + count.to_bytes(3, "little") * 2 \
                + first.to_bytes(3, "little") + parent.to_bytes(2, "big") + len(files[path]).to_bytes(4, "big")
        else:
            e = base.ljust(0x28, b"\0") + bytes([len(base) | 0x80]) + bytes(9) + parent.to_bytes(2, "big") + bytes(4)
        table += e.ljust(0x40, b"\0")
    blocks[0] = table.ljust(BLOCK, b"\0")
    nxt = {0: 0xFFFFFF}
    for first, count in chains.values():
        for i in range(count):
            nxt[first + i] = first + i + 1 if i + 1 < count else 0xFFFFFF
    total = max(_place(len(blocks) - 1) + 1, 2)
    body = bytearray(total * BLOCK)
    for n, b in enumerate(blocks):
        body[_place(n) * BLOCK:_place(n) * BLOCK + BLOCK] = b
    for t, at in ((0, 0), (1, 172)):
        for rec in range(170):
            n = t * 170 + rec
            if n < len(blocks):
                e = hashlib.sha1(blocks[n]).digest() + b"\x80" + nxt[n].to_bytes(3, "big")
                body[at * BLOCK + rec * 0x18:at * BLOCK + rec * 0x18 + 0x18] = e
    head = bytearray(0xA000)
    head[:4] = magic
    head[0x340:0x344] = (0x971A).to_bytes(4, "big")          # header size: blocks start at 0xA000
    head[0x344:0x348] = kind.to_bytes(4, "big")
    head[0x360:0x364] = title.to_bytes(4, "big")
    head[0x379:0x39D] = bytes([0x24, 0, 1]) + (1).to_bytes(2, "little") + bytes(3) + bytes(0x14) \
        + len(blocks).to_bytes(4, "big") + bytes(4)
    return bytes(head) + bytes(body)
