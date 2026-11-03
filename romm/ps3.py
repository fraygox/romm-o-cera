"""PS3 discs: keys, decryption, and the .ps3 game folder."""
import bisect
import collections
import heapq
import io
import itertools
import os
import re
import struct
import time
import zipfile
import zlib
try:
    import lzma
except ImportError:                    # a Python built without it: zipfile can't open LZMA members either
    lzma = None

from . import core as _core, config as _config, bios as _bios


# ---------------------------------------------------------- PS3 discs
# A PS3 disc image (Redump's .iso, often zipped) becomes a "Name.ps3" game folder,
# the layout RPCS3 installs updates and DLC for. Redump images are encrypted: sector
# 0 lists the plain regions, and every sector in the gaps between them is AES-128-CBC
# with the disc's key (IV = the sector number). Keys are the user's own, for games
# they own, and never shipped or downloaded from anywhere but the user's RomM:
# bios/ps3keys/ (.key, .dkey, or zips of them), also copied from RomM's PS3
# firmware. Decrypting is off unless turned on; a decrypted image needs no key.

PS3_SECTOR = 2048
PS3_MAGIC = b"PlayStation3"            # sector 1 of every PS3 disc
PS3_EBOOT = "PS3_GAME/USRDIR/EBOOT.BIN"
PS3_SFO = "PS3_GAME/PARAM.SFO"
PS3_KEY_EXTS = (".key", ".dkey")
PS3_KEY_MAX = 4096                     # one key file: 16 bytes, or 32 hex digits and a newline
PS3_KEYZIP_MAX = 256 << 20             # a zip of key files (Redump's whole set is under 1 MB)
PS3_RANKED = 50                        # keys tried in name-likeness order before all the rest
PS3_CHUNK = 8 << 20
PS3_KEYS_RECHECK = 60                  # seconds: the key files are stat'd again after this, even when the folder looks the same
PS3_SWEEP_SECONDS = 2.0                # most time still_encrypted spends trying every key on one disc
PS3_READ_MORE = 1 << 30                # most a zip is read on past the check window to find a part's next known sector
# A byte histogram this far from flat (chi-square, 255 degrees of freedom: mean 255,
# sd 23) is never AES output, so never ciphertext or a wrong key's garbage. Most game
# data is far past it; compressed or encrypted assets look random either way.
PS3_RANDOM_CHI2 = 512


class Ps3Error(Exception):
    """A PS3 disc that can't become a folder: not a PS3 disc, damaged, or encrypted
    with no key for it (or decrypting turned off)."""


# what a damaged zip raises while it's opened or read (bz2's, and a bad offset's seek, are OSErrors)
ZIP_ERRORS = (zipfile.BadZipFile, OSError, zlib.error, EOFError, RuntimeError, NotImplementedError, ValueError,
              struct.error) + ((lzma.LZMAError,) if lzma else ())
ROMM_COPY = " (RomM)"                  # RomM's key file saved beside the user's own one of that name


def ps3_keys_dir():
    return os.path.join(_core.USERDATA, "bios", "ps3keys")


def ps3_decrypt_supported():
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher  # noqa: F401
        return True
    except ImportError:
        return False


def _ps3_key_bytes(data):
    """16 raw bytes, or 32 hex digits (Redump's .key and .dkey); else None."""
    if len(data) == 16:
        return bytes(data)
    try:
        t = bytes(data).decode("ascii").strip()
        return bytes.fromhex(t) if len(t) == 32 else None
    except (UnicodeDecodeError, ValueError):
        return None


def _ps3_add_key(keys, name, k):
    """keys[name] = k; a different key by a name already taken goes in as "name (2)"
    and so on, so both are tried (each is checked against the disc itself)."""
    n, i = name, 1
    while n in keys:
        if keys[n] == k:
            return
        i += 1
        n = "%s (%d)" % (name, i)
    keys[n] = k


def _ps3_keys_from_zip(z, keys, depth=0):
    for info in z.infolist():
        name, low = info.filename, info.filename.lower()
        if info.is_dir():
            continue
        try:
            if low.endswith(PS3_KEY_EXTS) and info.file_size <= PS3_KEY_MAX:
                k = _ps3_key_bytes(z.read(info))
                if k:
                    _ps3_add_key(keys, os.path.splitext(os.path.basename(name))[0], k)
            elif low.endswith(".zip") and depth == 0 and info.file_size <= PS3_KEYZIP_MAX:
                with zipfile.ZipFile(io.BytesIO(z.read(info))) as inner:    # Redump's set: one small zip per disc
                    _ps3_keys_from_zip(inner, keys, 1)
        except ZIP_ERRORS:             # one damaged member: the rest still count
            continue


_PS3_KEYS = {"sig": None, "keys": {}, "gen": 0}


def ps3_keys(folder=None):
    """{disc name: 16-byte key} from the keys folder: loose .key/.dkey files and
    zips of them (one level of zips inside zips). Read in place, cached while the
    folder and its files look the same (the files are looked at again after a minute); unreadable files are skipped."""
    folder = folder or ps3_keys_dir()
    try:
        ds = os.stat(folder)
        dsig = (folder, ds.st_mtime_ns, ds.st_size)
    except OSError:
        dsig = None
    if dsig and _PS3_KEYS.get("dsig") == dsig and time.monotonic() - _PS3_KEYS.get("at", 0) < PS3_KEYS_RECHECK:      # same folder listing: a scan of many discs doesn't stat 4000 key files each
        return _PS3_KEYS["keys"]
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        names = None                         # no folder: no keys (and a new gen if it had some)
    sig = [folder] if names is not None else [folder, None]
    names = names or []
    for n in names:
        try:
            st = os.stat(os.path.join(folder, n))
            sig.append((n, st.st_size, st.st_mtime_ns))
        except OSError:
            pass
    if _PS3_KEYS["sig"] == sig:
        _PS3_KEYS.update(dsig=dsig, at=time.monotonic())
        return _PS3_KEYS["keys"]
    keys = {}
    for n in sorted(names, key=lambda n: (ROMM_COPY + "." in n, n)):      # the user's own files first
        p, low = os.path.join(folder, n), n.lower()
        if not os.path.isfile(p):
            continue
        try:
            if low.endswith(PS3_KEY_EXTS) and os.path.getsize(p) <= PS3_KEY_MAX:
                with open(p, "rb") as f:
                    k = _ps3_key_bytes(f.read())
                if k:
                    _ps3_add_key(keys, os.path.splitext(n)[0], k)
            elif low.endswith(".zip") and os.path.getsize(p) <= PS3_KEYZIP_MAX:
                with zipfile.ZipFile(p) as z:
                    _ps3_keys_from_zip(z, keys)
        except ZIP_ERRORS as e:
            _core.log("ps3: skipped key file %s (%s)" % (n, e.__class__.__name__))
    _PS3_KEYS.update(sig=sig, dsig=dsig, at=time.monotonic(), keys=keys, gen=_PS3_KEYS["gen"] + 1)   # gen: ps3_check's memo knows they changed
    return keys


def _ps3_words(name):
    return set(re.findall(r"[a-z0-9]+", name.lower()))


def _ps3_grams(name):
    s = re.sub(r"[^a-z0-9]+", " ", name.lower())
    return {s[i:i + 3] for i in range(len(s) - 2)}


def ps3_key_order(stem, keys):
    """Key names to try for a disc called stem: the exact name first (any case),
    then the most alike by words and letter shingles, then every other key.
    Order only saves time: each key is checked against the disc itself."""
    want = stem.casefold()
    exact = [n for n in keys if n.casefold() == want]
    words, grams = _ps3_words(stem), _ps3_grams(stem)

    def likeness(n):
        w, g = _ps3_words(n), _ps3_grams(n)
        return (len(words & w) / (len(words | w) or 1)) + (len(grams & g) / (len(grams | g) or 1))
    rest = [n for n in keys if n.casefold() != want]
    scored = sorted(((likeness(n), n) for n in rest), key=lambda t: (-t[0], t[1]))
    ranked = [n for s, n in scored[:PS3_RANKED] if s > 0]
    seen = set(ranked)
    return exact + ranked + sorted(n for n in rest if n not in seen)


def _ps3_iv(lba):
    return b"\0" * 12 + struct.pack(">I", lba & 0xFFFFFFFF)


def _ps3_structured(sector):
    """True when a sector's bytes are far from evenly spread: plain data, never
    ciphertext (see PS3_RANDOM_CHI2)."""
    n = len(sector) / 256
    counts = collections.Counter(sector).values()
    return sum((c - n) ** 2 for c in counts) / n + n * (256 - len(counts)) > PS3_RANDOM_CHI2


def _ps3_spread(seq, n):
    """Up to n items of seq, from its first to its last, evenly spread."""
    if len(seq) <= n:
        return list(seq)
    return [seq[round(k * (len(seq) - 1) / (n - 1))] for k in range(n)]


class Ps3Disc:
    """A PS3 disc image read through a file object (a loose .iso, or the .iso
    inside a zip, read forward as a stream): its regions, its ISO 9660 files,
    and decrypted reads once a key is set."""
    ZERO = bytes(PS3_SECTOR)
    # Check sectors, to test a key on and to tell a decrypted disc, in every encrypted
    # part holding files, each kind spread from the part's first to its last: up to
    # CHECK_KNOWN first sectors of files with a known start (a guess from the name),
    # up to CHECK_KNOWN last sectors of files, whose bytes past the file's end are
    # zeros (whatever the file), and CHECK_STATS of file data, which says "plain" when
    # it doesn't look random (see _says). Each part is judged on its own (see _fails);
    # a part nothing tells about (all random-looking, like a big compressed archive)
    # goes by the rest. A loose image is checked at once, a zip only near its start
    # (the rest as extract reads its way there).
    PROBE_NEAR = (256 << 20) // PS3_SECTOR
    CHECK_KNOWN = 5
    CHECK_STATS = 8
    KEY_PROBES = 8                     # check sectors a key is tried on (the right key fits any known one)
    PROBES = ((lambda p: p.endswith("/LIC.DAT"), b"PS3LICDA"),
              (lambda p: p.endswith(".PNG"), b"\x89PNG\r\n\x1a\n"),
              (lambda p: p.endswith("/PARAM.SFO"), b"\0PSF"),
              (lambda p: p.endswith(("/EBOOT.BIN", ".SELF", ".SPRX")), b"SCE\0"))

    def __init__(self, f, size, name="", reopen=None):
        self.f, self.size, self.name = f, size, name
        self.reopen = reopen           # a zip member: read forward only (its checksum stays checked)
        self.pos = 0 if reopen else None
        self.key = None
        if size < 32 * PS3_SECTOR or size % PS3_SECTOR:
            raise Ps3Error("not a PS3 disc image (size)")
        head = self.raw(0, 2)
        if head[PS3_SECTOR:PS3_SECTOR + len(PS3_MAGIC)] != PS3_MAGIC:
            raise Ps3Error("not a PS3 disc image")
        self.regions = self._regions(head[:PS3_SECTOR])
        self.files = self._walk()
        if PS3_EBOOT not in self.files or PS3_SFO not in self.files:
            raise Ps3Error("no game to boot on it (no PS3_GAME/USRDIR/EBOOT.BIN); left as it is")
        self.plain = False
        self.checks = self._checks()
        self.check_lbas = sorted(self.checks)
        self.probes = self._probes()
        tally = self._tally((lba, data, None) for lba, data in self.probes)
        self.plain = not self.checks or any(t[0] for t in tally.values())
        if self.plain and any(self._fails(t, None) for t in tally.values()):
            raise Ps3Error("partly decrypted (some of it is still encrypted); left as it is")

    @staticmethod
    def _regions(s0):
        n = struct.unpack(">I", s0[:4])[0]
        if not 0 < n <= (PS3_SECTOR - 8) // 8:
            raise Ps3Error("damaged region table")
        out, prev = [], 0
        for i in range(n):
            start, last = struct.unpack(">II", s0[8 + 8 * i:16 + 8 * i])
            if start > last or (i and start < prev):
                raise Ps3Error("damaged region table")
            if i and prev < start:
                out.append((prev, start))            # a gap between plain regions: encrypted
            prev = last + 1
        return out

    def encrypted(self, lba):
        return any(a <= lba < b for a, b in self.regions)

    def _read(self, n):
        if self.reopen is None:
            return self.f.read(n)
        try:
            return self.f.read(n)
        except ZIP_ERRORS as e:
            raise Ps3Error("the zip is damaged (%s)" % e.__class__.__name__) from None

    def raw(self, lba, count):
        off = lba * PS3_SECTOR
        if self.reopen is not None:
            if off < self.pos:                 # back in a zip: from the start again
                self.f.close()
                try:
                    self.f = self.reopen()
                except ZIP_ERRORS as e:
                    raise Ps3Error("the zip is damaged (%s)" % e.__class__.__name__) from None
                self.pos = 0
            while self.pos < off:              # forward: read past (never seek: that skips the checksum)
                skip = self._read(min(PS3_CHUNK, off - self.pos))
                if not skip:
                    raise Ps3Error("cut short at byte %d" % self.pos)
                self.pos += len(skip)
        elif self.pos != off:
            self.f.seek(off)
        data = self._read(count * PS3_SECTOR)
        if len(data) != count * PS3_SECTOR:
            raise Ps3Error("cut short at sector %d" % (lba + len(data) // PS3_SECTOR))
        self.pos = off + len(data)
        return data

    def decrypt(self, data, lba, key=None):
        """data (whole sectors from lba) with every encrypted, non-empty sector decrypted."""
        key = key or self.key
        if self.plain or not key:
            return data
        return self._decrypt(data, lba, key)

    def _decrypt(self, data, lba, key):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        n = len(data) // PS3_SECTOR
        out = None
        for a, b in self.regions:
            lo, hi = max(a, lba), min(b, lba + n)
            if lo >= hi:
                continue
            if out is None:
                out = bytearray(data)
            s, e = (lo - lba) * PS3_SECTOR, (hi - lba) * PS3_SECTOR
            chunk = bytes(data[s:e])
            # CBC with one IV per sector, done as one ECB pass: P = D(C) xor previous block
            dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor().update(chunk)
            prev = bytearray(16) + chunk[:-16]
            for i in range(hi - lo):
                prev[i * PS3_SECTOR:i * PS3_SECTOR + 16] = _ps3_iv(lo + i)
            plain = (int.from_bytes(dec, "big") ^ int.from_bytes(prev, "big")).to_bytes(len(chunk), "big")
            out[s:e] = plain
            for i in range(hi - lo):                 # empty sectors were never encrypted
                o = i * PS3_SECTOR
                if chunk[o:o + PS3_SECTOR] == self.ZERO:
                    out[s + o:s + o + PS3_SECTOR] = self.ZERO
        return data if out is None else bytes(out)

    def _says(self, lba, raw, dec):
        """What check sector lba (raw: as on the disc) says of the disc being plain
        (dec None) or encrypted (dec: raw decrypted with the key tried): "fits",
        "against" (a sure sign it's the other way: plain data in a disc said to be
        encrypted), "odd" (a file's start that's neither: a file that isn't what its
        name says, damage, the wrong key, or still encrypted), "wrong" (a file's end
        that's neither and looks random: still encrypted), or None (says nothing:
        empty, or random-looking, as compressed data looks plain or not). A file's
        end with bytes past it that aren't zeros goes by how they look: plain data
        (junk the disc was made with) fits a plain disc, and, decrypted, is no worse
        than a start that isn't what its name says (odd)."""
        if raw == self.ZERO:
            return None
        _, off, want = self.checks[lba]
        if want is not None:
            if raw[off:off + len(want)] == want:
                return "fits" if dec is None else "against"
            if dec is not None and dec[off:off + len(want)] == want:
                return "fits"
            if not off:
                return "odd"
            if dec is None:                      # ciphertext always looks random; junk needn't
                return "fits" if _ps3_structured(raw) else "wrong"
            if _ps3_structured(raw):
                return "against"
            return "fits" if _ps3_structured(dec) else "odd"
        if _ps3_structured(raw):
            return "fits" if dec is None else "against"
        return "fits" if dec is not None and _ps3_structured(dec) else None

    def _tally(self, seen, tally=None):
        """{part: [fits, against, odd, wrong]} from (lba, raw, decrypted or None) check sectors."""
        tally = {} if tally is None else tally
        for lba, raw, dec in seen:
            t = tally.setdefault(self.checks[lba][0], [0, 0, 0, 0])
            said = self._says(lba, raw, dec)
            if said:
                t[("fits", "against", "odd", "wrong").index(said)] += 1
        return tally

    @staticmethod
    def _fails(t, key):
        """Why a part's check sectors say it isn't as the disc is taken to be (plain
        when key is None), or None. A file's end that's wrong (random-looking where
        a plain disc has zeros: ciphertext) is sure; one odd one (see _says)
        among sectors that fit is a file that isn't what its name says, but several,
        the most of them, aren't. Taken as plain, a part whose sectors that say
        anything are all odd is still encrypted."""
        fits, against, odd, wrong = t
        if key is None:
            if wrong or (odd and (not fits or (odd >= 2 and odd > fits))):
                return "part of the image is still encrypted"
            return None
        if against:
            return "part of it is decrypted already"
        if wrong or (odd >= 2 and odd > fits):
            return "damaged, or the wrong key"
        return None

    # Known limits (partly decrypted discs): one can still be taken as plain when its
    # still-encrypted part holds only random-looking data that no key in the keys
    # folder opens; when that part also holds plain data before the point where the
    # zip read stops; or when it lies past what a zip is read to at check time
    # (PROBE_NEAR, then up to PS3_READ_MORE on). With Decrypt PS3 discs off only the
    # best-matching PS3_RANKED keys are tried, and with it on the full sweep stops
    # after PS3_SWEEP_SECONDS. Check games' full check of the converted folder can't
    # see any of this either. Nothing here is extended further: it only ever refuses.

    def _checks(self):
        """{lba: (part, offset, plain bytes there)} of the check sectors (see PROBES);
        offset and bytes are None for one of file data judged by how random it looks."""
        known = [{} for _ in self.regions]          # per part: {lba: (offset, bytes)}
        data = [[] for _ in self.regions]           # per part: [(first, end)] sectors of file data

        def part(lba):
            return next((i for i, (a, b) in enumerate(self.regions) if a <= lba < b), None)
        shared = self._shared_tails()
        for path, exts in self.files.items():
            up = path.upper()
            magic = next((m for match, m in self.PROBES if match(up)), None)
            for j, (lba, length) in enumerate(exts):
                if not length:
                    continue
                last = lba + (length - 1) // PS3_SECTOR
                for i, (a, b) in enumerate(self.regions):
                    if max(a, lba) < min(b, last + 1):
                        data[i].append((max(a, lba), min(b, last + 1)))
                tail = -(-(length % PS3_SECTOR) // 16) * 16    # the file's end, to the next AES block
                if length % PS3_SECTOR and PS3_SECTOR - tail >= 16 and part(last) is not None \
                        and last not in shared:
                    known[part(last)].setdefault(last, (tail, bytes(PS3_SECTOR - tail)))
                if j == 0 and magic and length >= len(magic) and part(lba) is not None:
                    known[part(lba)][lba] = (0, magic)                # a known start beats zeros
        out = {}
        for i in range(len(self.regions)):
            if not data[i]:
                continue
            for start in (True, False):
                pool = sorted(lba for lba, k in known[i].items() if (k[0] == 0) == start)
                for lba in _ps3_spread(pool, self.CHECK_KNOWN):
                    out[lba] = (i,) + known[i][lba]
            spans = sorted(data[i])
            ends = list(itertools.accumulate(e - a for a, e in spans))
            for n in _ps3_spread(range(ends[-1]), self.CHECK_STATS):
                k = bisect.bisect_right(ends, n)
                lba = spans[k][1] - (ends[k] - n)
                out.setdefault(lba, (i, None, None))
        return out

    def _shared_tails(self):
        """Last sectors of files that another file's data also covers (two entries
        sharing an extent with different lengths, or overlapping): the bytes past
        one file's end are the other's, so they can't be checked for zeros."""
        spans = sorted({(lba, lba + (length - 1) // PS3_SECTOR, length) for exts in self.files.values()
                        for lba, length in exts if length})          # the same extent twice is one
        starts = [a for a, _, _ in spans]
        top = []                               # per prefix of spans: the two furthest ends, and whose
        best = ((-1, -1), (-1, -1))
        for i, (_, last, _) in enumerate(spans):
            if last > best[0][0]:
                best = ((last, i), best[0])
            elif last > best[1][0]:
                best = (best[0], (last, i))
            top.append(best)
        out = set()
        for exts in self.files.values():
            for lba, length in exts:
                if not length or not length % PS3_SECTOR:
                    continue
                last = lba + (length - 1) // PS3_SECTOR
                me = bisect.bisect_left(spans, (lba, last, length))
                k = bisect.bisect_right(starts, last) - 1     # spans starting at or before it
                first, second = top[k]
                if (second if first[1] == me else first)[0] >= last:
                    out.add(last)
        return out

    def _probes(self):
        """[(lba, sector as on the disc)] of the check sectors read now: all of them
        from a loose image; from a zip (read forward), those near the first, and on
        past them while a part they reach has nothing in them to tell by (all empty
        or random-looking, as a blank padding file or a big packed one is, or one
        odd start), on to that part's next known sector (or, when no part has
        anything to tell by, the disc's next one). So past its first PROBE_NEAR
        sectors a zip is read only as far as the next file of a known kind, as a
        rule (past the last known sector of the parts the first read reaches only
        when none of them can tell), and only when what it read can't tell; extract
        then reads it again from the start."""
        lbas = self.check_lbas
        if self.reopen is None:
            return [(lba, self.raw(lba, 1)) for lba in lbas]
        probes, told, near, odd = [], set(), set(), collections.Counter()
        later = collections.Counter(self.checks[c][0] for c in lbas if self.checks[c][2] is not None)
        for lba in lbas:
            part, _, want = self.checks[lba]
            if lba - lbas[0] <= self.PROBE_NEAR:
                near.add(part)                 # the parts the first read reaches
            elif lba - lbas[0] > self.PROBE_NEAR + PS3_READ_MORE // PS3_SECTOR:
                break                          # read on far enough: decide from what's known (never a refusal)
            elif not {p for p in near - told if later[p]} and (told or not +later):
                break
            raw = self.raw(lba, 1)
            probes.append((lba, raw))
            if want is not None:
                later[part] -= 1
            said = self._says(lba, raw, None)
            odd[part] += said == "odd"
            if said in ("fits", "wrong") or odd[part] >= 2:      # one odd start could be either
                told.add(part)
        return probes

    def _extent(self, rec):
        lba, length = struct.unpack("<I", rec[2:6])[0], struct.unpack("<I", rec[10:14])[0]
        if length and lba + (length + PS3_SECTOR - 1) // PS3_SECTOR > self.size // PS3_SECTOR:
            raise Ps3Error("damaged file system (a file past the end of the disc)")
        return lba, length

    def _walk(self):
        """{"DIR/FILE": [(lba, length), ...]} from the primary volume descriptor
        (Joliet's names when the disc has them). Names are checked: no paths out,
        none that only differ in case (one would overwrite the other on exFAT)."""
        pvd = joliet = None
        for lba in range(16, 32):
            d = self.raw(lba, 1)
            if d[1:6] != b"CD001":
                raise Ps3Error("no ISO 9660 file system")
            if d[0] == 1 and pvd is None:
                pvd = d
            elif d[0] == 2 and d[88:91] in (b"%/@", b"%/C", b"%/E"):
                joliet = d
            elif d[0] == 255:
                break
        vd = joliet or pvd
        if vd is None:
            raise Ps3Error("no ISO 9660 file system")
        wide = vd is joliet
        files, seen, folded = {}, set(), {}
        todo = [(struct.unpack("<I", vd[158:162])[0], "", vd[156:190])]
        while todo:                                      # in disc order: a zip is read forward
            _, prefix, rec = heapq.heappop(todo)
            lba, length = self._extent(rec)
            if lba in seen or len(seen) > 100000 or length > 64 << 20:
                raise Ps3Error("damaged file system")
            seen.add(lba)
            data = self.raw(lba, (length + PS3_SECTOR - 1) // PS3_SECTOR)
            pos, pending = 0, None
            while pos < length:
                rl = data[pos]
                if rl == 0:                              # records never cross a sector
                    pos = (pos // PS3_SECTOR + 1) * PS3_SECTOR
                    continue
                if rl < 34 or pos % PS3_SECTOR + rl > PS3_SECTOR or pos + rl > length:
                    raise Ps3Error("damaged file system")
                r = data[pos:pos + rl]
                pos += rl
                nl = r[32]
                if not nl or 33 + nl > rl:
                    raise Ps3Error("damaged file system")
                raw_name = r[33:33 + nl]
                if raw_name in (b"\0", b"\1"):
                    continue
                name = raw_name.decode("utf-16-be" if wide else "latin-1", "replace").split(";")[0]
                if not wide:
                    name = name.rstrip(".")
                if not name or name in (".", "..") or "/" in name or "\\" in name or "\0" in name:
                    raise Ps3Error("unsafe name on the disc: %r" % name)
                if len(name.encode("utf-8", "replace")) > 255:
                    raise Ps3Error("damaged file system (a name too long for a file)")
                path = prefix + name
                ext = self._extent(r)
                if not (pending and pending[0] == path):
                    other = folded.get(path.casefold())  # seen before, or only the case differs
                    if other is not None:
                        raise Ps3Error("two files named %s" % path if other == path else
                                       "two files named %s and %s (a game folder can't hold both)"
                                       % (other, path))
                    folded[path.casefold()] = path
                if r[25] & 2:
                    heapq.heappush(todo, (ext[0], path + "/", r))
                    continue
                if pending and pending[0] == path:
                    files[path].append(ext)              # the next piece of a file over 4 GB
                else:
                    files[path] = [ext]
                pending = (path,) if r[25] & 0x80 else None
        return files

    def _opens(self, key, probes):
        """True when key decrypts one of probes (non-empty check sectors) to what it
        should hold. A known sector costs one AES block: the one its known bytes
        start in (CBC: that block decrypted, xor the one before, or the IV)."""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        known = [(lba, raw) for lba, raw in probes if self.checks[lba][2] is not None]
        if known:
            blocks = b"".join(raw[self.checks[lba][1]:self.checks[lba][1] + 16] for lba, raw in known)
            dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor().update(blocks)
            for n, (lba, raw) in enumerate(known):
                _, off, want = self.checks[lba]
                prev = raw[off - 16:off] if off else _ps3_iv(lba)
                got = bytes(x ^ y for x, y in zip(dec[16 * n:16 * n + 16], prev))
                if got[:len(want)] == want[:16]:
                    return True
            return False                       # a right key fits a known sector; stats cost far more
        stats = [(lba, raw) for lba, raw in probes if raw != self.ZERO]
        if not stats:
            return False
        blob = b"".join(raw for _, raw in stats)               # one AES pass for them all
        dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor().update(blob)
        prev = bytearray(16) + blob[:-16]
        for n, (lba, _) in enumerate(stats):
            prev[n * PS3_SECTOR:n * PS3_SECTOR + 16] = _ps3_iv(lba)
        plain = (int.from_bytes(dec, "big") ^ int.from_bytes(prev, "big")).to_bytes(len(blob), "big")
        return any(_ps3_structured(plain[n * PS3_SECTOR:(n + 1) * PS3_SECTOR]) for n in range(len(stats)))

    def _key_probes(self):
        """The check sectors read so far that a key is tried on: known ones first."""
        probes = [p for p in self.probes if p[1] != self.ZERO]
        probes.sort(key=lambda p: self.checks[p[0]][2] is None)
        return probes[:self.KEY_PROBES]

    def find_key(self, keys):
        """The name of the key that opens this disc (checked on the check sectors
        read so far), or None. Ps3Error when one opens it but the rest of what was
        read doesn't fit (damaged, or partly decrypted already) and no other key
        opens all of it: a wrong key can fit a 4-byte start by chance."""
        probes, why = self._key_probes(), None
        for name in ps3_key_order(self.name, keys):
            k = keys[name]
            if not self._opens(k, probes):
                continue
            tally = self._tally((lba, raw, self._decrypt(raw, lba, k)) for lba, raw in self.probes)
            fail = next((w for w in (self._fails(t, k) for t in tally.values()) if w), None)
            if not fail:
                return name
            why = why or fail
        if why:
            raise Ps3Error("its key opens only some of it (%s); left as it is" % why)
        return None

    def still_encrypted(self, keys, sweep=True):
        """For a disc taken as plain: True when a key opens a part nothing else told
        about (all random-looking): that part is still encrypted. Every key is
        tried: the ones named most like the disc on all those sectors, the rest on
        the first two (a stats sector costs a whole sector's AES and a count, so a
        set of thousands of keys still takes a second or two, not ten). sweep False
        (Decrypt PS3 discs off, nothing decrypted): only the ranked keys. sweep True
        stops after PS3_SWEEP_SECONDS (then it answers False, as before this check)."""
        tally = self._tally((lba, raw, None) for lba, raw in self.probes)
        doubt = [(lba, raw) for lba, raw in self.probes if raw != self.ZERO and not tally[self.checks[lba][0]][0]]
        doubt = doubt[:self.KEY_PROBES]
        if not doubt:
            return False
        order = ps3_key_order(self.name, keys)
        end = time.monotonic() + PS3_SWEEP_SECONDS
        for i, n in enumerate(order):
            if i >= PS3_RANKED:
                if not sweep:
                    break
                if time.monotonic() > end:
                    break
            if self._opens(keys[n], doubt if i < PS3_RANKED else doubt[:2]):
                return True
        return False

    def total(self):
        return sum(length for exts in self.files.values() for _, length in exts)

    def extract(self, out, pause=None, progress=None):
        """Write every file under out/ (which must not exist), reading the image
        forward, decrypting as needed. Every check sector is checked on the way
        (an image only partly decrypted, or the wrong key, stops it), and
        EBOOT.BIN and PARAM.SFO after."""
        pieces, offset = [], {}
        for path, exts in self.files.items():
            at = 0
            for lba, length in exts:
                pieces.append((lba, length, path, at))
                at += length
        pieces.sort()
        total, done = self.total() or 1, 0
        left_in = collections.Counter(part for part, _, _ in self.checks.values())
        tally, judged = {}, set()
        os.makedirs(out)
        real = os.path.realpath(out)
        made = set()
        for lba, length, path, at in pieces:
            dest = os.path.join(out, *path.split("/"))
            if not os.path.realpath(dest).startswith(real + os.sep):
                raise Ps3Error("unsafe name on the disc: %s" % path)
            if path not in made:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                open(dest, "xb").close()             # never over another file (names were checked)
                made.add(path)
            with open(dest, "r+b") as f:
                f.seek(at)
                left, cur = length, lba
                while left > 0:
                    if pause:
                        pause()
                    n = min(PS3_CHUNK, left)
                    secs = (n + PS3_SECTOR - 1) // PS3_SECTOR
                    raw = self.raw(cur, secs)
                    data = self.decrypt(raw, cur)
                    i = bisect.bisect_left(self.check_lbas, cur)
                    while i < len(self.check_lbas) and self.check_lbas[i] < cur + secs:
                        c = self.check_lbas[i]
                        i += 1
                        if c in judged:
                            continue
                        judged.add(c)
                        o = (c - cur) * PS3_SECTOR
                        part = self.checks[c][0]
                        self._tally([(c, raw[o:o + PS3_SECTOR], None if self.plain
                                      else data[o:o + PS3_SECTOR])], tally)
                        left_in[part] -= 1
                        why = self._fails(tally[part], None if self.plain else self.key) \
                            if not left_in[part] else None     # a part is judged once all of it is read
                        if why:
                            raise Ps3Error("%s didn't come out right (%s); left as it is" % (path, why))
                    f.write(data[:n])
                    left -= n
                    cur += secs
                    done += n
                    if progress:
                        progress(done / total)
        why = next((w for w in (self._fails(t, None if self.plain else self.key) for t in tally.values()) if w),
                   None)
        if why:
            raise Ps3Error("it didn't come out right (%s); left as it is" % why)
        for path, magic in ((PS3_EBOOT, b"SCE\0"), (PS3_SFO, b"\0PSF")):
            with open(os.path.join(out, *path.split("/")), "rb") as f:
                if f.read(4) != magic:
                    raise Ps3Error("%s didn't come out right (wrong key?)" % path)
        return made


def ps3_disc(src):
    """(Ps3Disc, closer) for a loose .iso or the one .iso in a .zip (read in place),
    named after the file."""
    name = os.path.splitext(os.path.basename(src))[0]
    if not src.lower().endswith(".zip"):
        f = open(src, "rb")
        try:
            return Ps3Disc(f, os.path.getsize(src), name), f.close
        except Exception:
            f.close()
            raise
    try:
        z = zipfile.ZipFile(src)
    except ZIP_ERRORS as e:
        raise Ps3Error("can't read it (%s)" % e.__class__.__name__) from None
    try:
        isos = [i for i in z.infolist() if i.filename.lower().endswith(".iso") and not i.is_dir()]
        others = [i for i in z.infolist() if i not in isos and not i.is_dir()
                  and not i.filename.lower().endswith((".txt", ".nfo", ".md5", ".sfv"))]
        if len(isos) != 1 or others:
            raise Ps3Error("the zip doesn't hold exactly one disc image")
        try:
            f = z.open(isos[0])
        except ZIP_ERRORS as e:
            raise Ps3Error("can't read it (%s)" % e.__class__.__name__) from None
        try:
            disc = Ps3Disc(f, isos[0].file_size, name, reopen=lambda: z.open(isos[0]))
        except Exception:
            f.close()
            raise
    except Exception:
        z.close()
        raise
    return disc, lambda: (disc.f.close(), z.close())


def ps3_image_size(src):
    """Bytes of the disc image: the .iso, or the one inside a zip."""
    if src.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(src) as z:
                return sum(i.file_size for i in z.infolist() if i.filename.lower().endswith(".iso"))
        except ZIP_ERRORS as e:
            raise Ps3Error("can't read it (%s)" % e.__class__.__name__) from None
    return os.path.getsize(src)


def ps3_zip_is_disc(path):
    """True for a zip holding one PS3 disc image (and maybe notes): kept zipped
    for the PS3 converter, which reads it in place."""
    try:
        with zipfile.ZipFile(path) as z:
            names = [i.filename.lower() for i in z.infolist() if not i.is_dir()]
    except ZIP_ERRORS:
        return False
    isos = [n for n in names if n.endswith(".iso")]
    return len(isos) == 1 and all(n in isos or n.endswith((".txt", ".nfo", ".md5", ".sfv")) for n in names)


def ps3_key_files(api, platform_id):
    """RomM's PS3 firmware files that carry disc keys: .key/.dkey files and zips
    whose name says keys (keys.zip). The user's own; RomM-o-Cera ships none."""
    out = []
    for fw in api.firmware(platform_id) if platform_id else []:
        n = (fw.get("file_name") or "").lower()
        if n.endswith(PS3_KEY_EXTS) or (n.endswith(".zip") and "key" in n):
            out.append(fw)
    return out


def _ps3_same_file(path, fw):
    return os.path.isfile(path) and os.path.getsize(path) == fw.get("file_size_bytes") \
        and bool(fw.get("md5_hash")) and _bios.file_md5(path) == fw["md5_hash"].lower()


def fetch_ps3_keys(api, platform_id):
    """Copy the disc keys in RomM's PS3 firmware into bios/ps3keys/ (a file already
    there with the same size and MD5 is kept). A different file of the user's by
    that name stays as it is: RomM's goes beside it as "Name (RomM).ext", and both
    are read. Returns how many keys the folder now holds. A file with no keys in
    it isn't kept."""
    folder = ps3_keys_dir()
    os.makedirs(folder, exist_ok=True)
    real = os.path.realpath(folder)
    for fw in ps3_key_files(api, platform_id):
        name = os.path.basename(fw["file_name"])
        dest = os.path.join(folder, name)
        if not os.path.realpath(dest).startswith(real + os.sep) or name.startswith("."):
            continue
        stem, ext = os.path.splitext(name)
        beside = os.path.join(folder, stem + ROMM_COPY + ext)
        if _ps3_same_file(dest, fw) or _ps3_same_file(beside, fw):
            continue
        part = dest + ".part"
        api.download_firmware(fw["id"], name, part, PS3_KEYZIP_MAX, "Key",
                              "%s is too big for a key file; not kept." % name)
        found = {}
        try:
            if name.lower().endswith(".zip"):
                with zipfile.ZipFile(part) as z:
                    _ps3_keys_from_zip(z, found)
            else:
                with open(part, "rb") as f:
                    k = _ps3_key_bytes(f.read(PS3_KEY_MAX + 1))
                found = {name: k} if k else {}
        except ZIP_ERRORS:
            found = {}
        if not found:
            os.remove(part)
            _core.log("ps3: %s from RomM holds no disc keys; not kept" % name)
            continue
        md5 = _bios.file_md5(part)
        if any(os.path.isfile(p) and _bios.file_md5(p) == md5 for p in (dest, beside)):
            os.remove(part)                  # the same file already (RomM didn't say its MD5)
            continue
        to = beside if os.path.lexists(dest) else dest
        os.replace(part, to)
        _core.log("ps3: disc keys from RomM: %s (%d)" % (os.path.basename(to), len(found)))
    return len(ps3_keys(folder))


_PS3_CHECKED = {}


def ps3_check(src, cfg=None):
    """What a PS3 disc needs: {"plain": True} or {"plain": False, "key": name}.
    Ps3Error when it can't become a folder (and why: decrypting off, no key).
    Remembered until the file, the keys folder or the setting changes."""
    return dict(_ps3_checked(src, cfg)[0])


def _ps3_checked(src, cfg=None):
    """(ps3_check's answer, the key's 16 bytes or None): the key as it was found,
    even if the keys folder has changed since."""
    cfg = _config.load_config() if cfg is None else cfg
    try:
        st = os.stat(src)
    except OSError as e:
        raise Ps3Error("can't read it (%s)" % (e.strerror or e)) from None
    on = bool(_config.config_value(cfg, "ps3_decrypt"))
    ps3_keys()                               # read again if the folder changed: a new gen (used even when off)
    memo = (src, st.st_size, st.st_mtime_ns, on, ps3_keys_dir(), _PS3_KEYS["gen"])
    hit = _PS3_CHECKED.get(src)
    if hit and hit[0] == memo:
        if isinstance(hit[1], Ps3Error):
            raise Ps3Error(str(hit[1]))
        return hit[1]
    try:
        res = _ps3_check(src, cfg)
    except Ps3Error as e:
        _PS3_CHECKED[src] = (memo, e)
        raise
    if len(_PS3_CHECKED) > 512:
        _PS3_CHECKED.clear()
    _PS3_CHECKED[src] = (memo, res)
    return res


def _ps3_check(src, cfg):
    """(answer, key bytes) for _ps3_checked."""
    on = bool(_config.config_value(cfg, "ps3_decrypt"))
    disc, close = ps3_disc(src)
    try:
        if disc.plain:
            # a key that opens a part nothing else tells about: still encrypted there. Tried
            # with Decrypt PS3 discs off too (it only ever refuses, and nothing is decrypted)
            if ps3_decrypt_supported() and disc.still_encrypted(ps3_keys(), sweep=on):
                raise Ps3Error("partly decrypted (some of it is still encrypted); left as it is")
            return {"plain": True, "size": disc.total()}, None
        if not on:
            raise Ps3Error("encrypted; turn on Decrypt PS3 discs (uses disc keys for games you own)")
        if not ps3_decrypt_supported():
            raise Ps3Error("encrypted, and Python's cryptography module isn't on this system")
        keys = dict(ps3_keys())                  # this read's keys: the name found goes with its bytes
        name = disc.find_key(keys)
        if name is None:
            raise Ps3Error("encrypted, and bios/ps3keys has no disc key for it (add the key for "
                           "this game you own)")
        return {"plain": False, "key": name, "size": disc.total()}, keys[name]
    finally:
        close()


def ps3_to_folder(src, out, cfg=None, pause=None, progress=None):
    """Turn a PS3 disc (.iso or zipped .iso) into the game folder out (Name.ps3),
    decrypting with its key when needed. A zip's own checksum is checked by
    reading it to the end. Returns the key's name (None: no key needed)."""
    info, key = _ps3_checked(src, cfg)
    disc, close = ps3_disc(src)
    try:
        if disc.plain != info["plain"]:              # the file changed since it was checked
            raise Ps3Error("it changed while being checked; left as it is")
        disc.key = key
        disc.extract(out, pause, progress)
        if src.lower().endswith(".zip"):
            try:                               # the rest of the stream: the zip checks its CRC at the end
                while disc.f.read(PS3_CHUNK):
                    if pause:
                        pause()
            except ZIP_ERRORS as e:
                raise Ps3Error("the zip is damaged (%s)" % e) from None
        return info.get("key")
    finally:
        close()


PS3_SFB = "PS3_DISC.SFB"


def ps3_sfo(path, strict=False):
    """{key: value} from a PARAM.SFO (text and numbers), or None if it isn't a
    whole one (or can't be read; strict: that raises its OSError instead)."""
    try:
        with open(path, "rb") as f:
            data = f.read(1 << 20)
    except OSError:
        if strict:
            raise
        return None
    if len(data) < 20 or data[:4] != b"\0PSF":
        return None
    keys, vals, n = struct.unpack("<III", data[8:20])
    if 20 + 16 * n > min(keys, len(data)) or vals > len(data):
        return None
    out = {}
    for i in range(n):
        ko, fmt, length, _, do = struct.unpack("<HHIII", data[20 + 16 * i:36 + 16 * i])
        end = data.find(b"\0", keys + ko)
        raw = data[vals + do:vals + do + length]
        if end < 0 or len(raw) != length:
            return None
        out[data[keys + ko:end].decode("latin-1")] = int.from_bytes(raw, "little") if fmt == 0x0404 else \
            raw.split(b"\0", 1)[0].decode("utf-8", "replace")
    return out


def ps3_folder_check(path, full=False, stop=None):
    """Check games on a .ps3 game folder made here: None if it looks whole, else
    (problem, severity). Always: what RPCS3 boots it by (EBOOT.BIN, a PARAM.SFO
    naming the game) and the disc's PS3_DISC.SFB. full: every file read to its
    end; stop() is called between blocks (it raises to end the check). Nothing
    to compare with RomM: its copy is the disc image. A file the box can't read
    (an I/O error, no permission) raises its OSError: the drive's fault as much
    as the folder's, which the caller says."""
    if not os.path.isdir(path):
        return None
    for rel, magic, severity in ((PS3_EBOOT, b"SCE\0", "damaged"), (PS3_SFO, b"\0PSF", "damaged"),
                                 (PS3_SFB, b".SFB", "differs")):
        try:
            with open(os.path.join(path, *rel.split("/")), "rb") as f:
                head = f.read(4)
        except FileNotFoundError:
            return "Missing file: %s" % rel, severity
        if head != magic:
            return "Damaged file: %s" % rel, severity
    if not (ps3_sfo(os.path.join(path, *PS3_SFO.split("/")), strict=True) or {}).get("TITLE_ID"):
        return "Damaged file: %s" % PS3_SFO, "damaged"
    if full:
        def unreadable(e):
            raise e
        for dp, dns, fns in os.walk(path, onerror=unreadable):
            dns.sort()
            for fn in sorted(fns):
                p = os.path.join(dp, fn)
                rel = os.path.relpath(p, path)
                with open(p, "rb") as f:
                    want, got = os.fstat(f.fileno()).st_size, 0
                    for chunk in iter(lambda: f.read(PS3_CHUNK), b""):
                        got += len(chunk)
                        if stop:
                            stop()
                if got != want:
                    return "Can't read %s to its end" % rel, "damaged"
    return None
