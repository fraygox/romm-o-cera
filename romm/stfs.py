"""Xbox 360 packages (STFS: the "CON ", "LIVE" and "PIRS" files a disc or the
marketplace carries a game, an add-on or a title update in), read in Python:
what one holds, and its files unpacked to a folder with every block checked
against the SHA-1 the package keeps for it. xenia-canary reads add-ons and
updates only as such unpacked folders."""
import hashlib
import os

STFS_MAGICS = (b"CON ", b"LIVE", b"PIRS")
STFS_BLOCK = 0x1000
STFS_PER_LEVEL = (170, 28900, 4913000)       # data blocks one hash table of each level covers
STFS_END = 0xFFFFFF                          # a block chain's end
STFS_MAX_ENTRIES = 100000                    # far past any package: more is a broken file table
STFS_TITLE, STFS_ADDON, STFS_UPDATE = 0x000D0000, 0x00000002, 0x000B0000     # content types


class StfsError(Exception):
    """Not a package this can read, or one that is damaged."""


def stfs_info(path):
    """A package's header: {"type" (content type), "title" (title id),
    "base" (where its blocks start), "tables" (hash-table blocks per group: 1
    in a read-only package, 2 in one a console wrote), "dir_block",
    "dir_blocks", "blocks"}. StfsError when it isn't an STFS package (a game
    on demand's "SVOD" package isn't one)."""
    try:
        with open(path, "rb") as f:
            head = f.read(0x3B0)
    except OSError as e:
        raise StfsError("can't be read (%s)" % (e.strerror or e))
    if len(head) < 0x3B0 or head[:4] not in STFS_MAGICS:
        raise StfsError("not an Xbox 360 package")
    if int.from_bytes(head[0x3A9:0x3AD], "big") != 0:
        raise StfsError("not an STFS package (a game on demand's disc data)")
    d = head[0x379:0x39D]                                    # the volume descriptor
    if d[0] != 0x24:
        raise StfsError("its volume descriptor isn't one")
    return {"type": int.from_bytes(head[0x344:0x348], "big"), "title": int.from_bytes(head[0x360:0x364], "big"),
            "base": -(-int.from_bytes(head[0x340:0x344], "big") // STFS_BLOCK) * STFS_BLOCK,
            "tables": 1 if d[2] & 1 else 2, "active": bool(d[2] & 2),
            "dir_blocks": int.from_bytes(d[3:5], "little"), "dir_block": int.from_bytes(d[5:8], "little"),
            "blocks": int.from_bytes(d[0x1C:0x20], "big")}


class _Stfs:
    """An open package: its data blocks by number, each one's SHA-1 and the
    next block of its chain from the hash tables between them."""

    def __init__(self, f, info):
        self.f, self.info, self.tables = f, info, {}
        t = info["tables"]
        self.step = (STFS_PER_LEVEL[0] + t, STFS_PER_LEVEL[1] + (STFS_PER_LEVEL[0] + 1) * t)

    def _at(self, off, n=STFS_BLOCK):
        self.f.seek(self.info["base"] + off)
        b = self.f.read(n)
        if len(b) != n:
            raise StfsError("is cut short")
        return b

    def block(self, n):
        if n >= self.info["blocks"]:
            raise StfsError("names a block past its end")
        at = n
        for per in STFS_PER_LEVEL:
            at += (n + per) // per * self.info["tables"]
            if n < per:
                break
        return self._at(at * STFS_BLOCK)

    def _table_block(self, n, level):
        t = self.info["tables"]
        if level == 2:
            return self.step[1]
        if n < STFS_PER_LEVEL[level]:
            return 0 if level == 0 else self.step[level - 1]
        at = n // STFS_PER_LEVEL[level] * self.step[level]
        if level == 0:
            at += (n // STFS_PER_LEVEL[1] + 1) * t
            if n < STFS_PER_LEVEL[1]:
                return at
        return at + t

    def _table(self, n, level, second):
        key = (self._table_block(n, level), second)
        if key not in self.tables:
            if len(self.tables) > 64:
                self.tables.clear()
            self.tables[key] = self._at((key[0] + (1 if second else 0)) * STFS_BLOCK)
        return self.tables[key]

    def entry(self, n):
        """(SHA-1, next block) of data block n."""
        second = False
        if self.info["tables"] == 2:             # a console-written package keeps two copies of each table:
            second = self.info["active"]         # the level above says which one is current
            levels = next((i for i, per in enumerate(STFS_PER_LEVEL) if self.info["blocks"] < per), 0)
            for level in range(levels, 0, -1):
                rec = n // STFS_PER_LEVEL[level - 1] % STFS_PER_LEVEL[0]
                e = self._table(n, level, second)[rec * 0x18:rec * 0x18 + 0x18]
                second = bool(e[0x14] & 0x40)
        rec = n % STFS_PER_LEVEL[0]
        e = self._table(n, 0, second)[rec * 0x18:rec * 0x18 + 0x18]
        return e[:0x14], int.from_bytes(e[0x15:0x18], "big")

    def checked(self, n):
        """Data block n and the next of its chain; StfsError if its SHA-1 doesn't match."""
        sha, nxt = self.entry(n)
        b = self.block(n)
        if hashlib.sha1(b).digest() != sha:
            raise StfsError("is damaged (block %d doesn't match its SHA-1)" % n)
        return b, nxt

    def listing(self):
        """[(path, is a folder, first block, size)], folders before what they hold."""
        out, n = [], self.info["dir_block"]
        for _ in range(self.info["dir_blocks"]):
            if n == STFS_END:
                break
            b, n = self.checked(n)
            for i in range(0, STFS_BLOCK, 0x40):
                e = b[i:i + 0x40]
                if not e[0]:
                    break
                name = e[:e[0x28] & 0x3F].decode("cp1252", "replace")
                parent = int.from_bytes(e[0x32:0x34], "big")
                if not name or name in (".", "..") or "/" in name or "\\" in name or "\0" in name \
                        or (parent != 0xFFFF and (parent >= len(out) or not out[parent][1])):
                    raise StfsError("has a file table entry that can't be right")
                out.append((name if parent == 0xFFFF else out[parent][0] + "/" + name, bool(e[0x28] & 0x80),
                            int.from_bytes(e[0x2F:0x32], "little"), int.from_bytes(e[0x34:0x38], "big")))
                if len(out) > STFS_MAX_ENTRIES:
                    raise StfsError("has a file table that never ends")
        return out


def stfs_listing(path):
    """{path in the package: size} of its files (folders left out). StfsError."""
    info = stfs_info(path)
    try:
        with open(path, "rb") as f:
            return {p: size for p, is_dir, _, size in _Stfs(f, info).listing() if not is_dir}
    except OSError as e:
        raise StfsError("can't be read (%s)" % (e.strerror or e))


def stfs_unpack(path, dest, progress=None, pause=None):
    """Unpack a package's files into the new folder dest, every block checked
    against the SHA-1 the package keeps for it and every file on disk before
    this returns. Returns {"files", "bytes"}. StfsError: nothing can be
    trusted (the caller removes dest). progress(fraction); pause() every
    16 MB (it may wait, or raise to stop)."""
    info = stfs_info(path)
    try:
        with open(path, "rb") as f:
            s = _Stfs(f, info)
            entries = s.listing()
            total = sum(size for _, is_dir, _, size in entries if not is_dir) or 1
            done = files = 0
            os.makedirs(dest)
            for rel, is_dir, block, size in entries:
                to = os.path.join(dest, *rel.split("/"))
                if is_dir:
                    os.makedirs(to, exist_ok=True)
                    continue
                left = size
                with open(to, "xb") as out:
                    while left:
                        if block == STFS_END:
                            raise StfsError("is damaged (%s ends %d bytes early)" % (rel, left))
                        if pause and not done & 0xFFFFFF:        # every 16 MB: it may ask ES what's running
                            pause()
                        b, block = s.checked(block)
                        out.write(b[:left])
                        left -= min(left, STFS_BLOCK)
                        done += STFS_BLOCK
                        if progress and not done & 0xFFFFFF:
                            progress(min(1.0, done / total))
                    out.flush()
                    os.fsync(out.fileno())
                files += 1
    except OSError as e:
        raise StfsError("couldn't be unpacked (%s)" % (e.strerror or e))
    return {"files": files, "bytes": total}


def stfs_kind(path):
    """A package's content type and title id, or (None, None) if it isn't one."""
    try:
        info = stfs_info(path)
    except StfsError:
        return None, None
    return info["type"], info["title"]

