"""PS3 discs: keys, decryption, and the .ps3 game folder."""
import io
import os
import re
import struct
import urllib.parse
import zipfile

import requests

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


class Ps3Error(Exception):
    """A PS3 disc that can't become a folder: not a PS3 disc, damaged, or encrypted
    with no key for it (or decrypting turned off)."""


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


def _ps3_keys_from_zip(z, keys, depth=0):
    for info in z.infolist():
        name, low = info.filename, info.filename.lower()
        if info.is_dir():
            continue
        if low.endswith(PS3_KEY_EXTS) and info.file_size <= PS3_KEY_MAX:
            k = _ps3_key_bytes(z.read(info))
            if k:
                keys.setdefault(os.path.splitext(os.path.basename(name))[0], k)
        elif low.endswith(".zip") and depth == 0 and info.file_size <= PS3_KEYZIP_MAX:
            try:                                    # Redump's set: one small zip per disc
                with zipfile.ZipFile(io.BytesIO(z.read(info))) as inner:
                    _ps3_keys_from_zip(inner, keys, 1)
            except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
                continue


_PS3_KEYS = {"sig": None, "keys": {}}


def ps3_keys(folder=None):
    """{disc name: 16-byte key} from the keys folder: loose .key/.dkey files and
    zips of them (one level of zips inside zips). Read in place, cached until a
    file there changes; unreadable files are skipped."""
    folder = folder or ps3_keys_dir()
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return {}
    sig = [folder]
    for n in names:
        try:
            st = os.stat(os.path.join(folder, n))
            sig.append((n, st.st_size, st.st_mtime_ns))
        except OSError:
            pass
    if _PS3_KEYS["sig"] == sig:
        return _PS3_KEYS["keys"]
    keys = {}
    for n in names:
        p, low = os.path.join(folder, n), n.lower()
        if not os.path.isfile(p):
            continue
        try:
            if low.endswith(PS3_KEY_EXTS) and os.path.getsize(p) <= PS3_KEY_MAX:
                with open(p, "rb") as f:
                    k = _ps3_key_bytes(f.read())
                if k:
                    keys.setdefault(os.path.splitext(n)[0], k)
            elif low.endswith(".zip") and os.path.getsize(p) <= PS3_KEYZIP_MAX:
                with zipfile.ZipFile(p) as z:
                    _ps3_keys_from_zip(z, keys)
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as e:
            _core.log("ps3: skipped key file %s (%s)" % (n, e.__class__.__name__))
    _PS3_KEYS.update(sig=sig, keys=keys)
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


class Ps3Disc:
    """A PS3 disc image read through a file object (a loose .iso, or the .iso
    inside a zip, read forward as a stream): its regions, its ISO 9660 files,
    and decrypted reads once a key is set."""
    ZERO = bytes(PS3_SECTOR)
    # files whose first bytes are known, to test a key on (and tell a decrypted disc):
    # the nearest to the start, and the next one when it's close by (a key is checked
    # without reading far into a zip; the finished folder is checked again anyway)
    PROBE_NEAR = (256 << 20) // PS3_SECTOR
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
        self.probes = self._probes()
        self.plain = all(data[:len(m)] == m for _, data, m in self.probes)

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

    def raw(self, lba, count):
        off = lba * PS3_SECTOR
        if self.reopen is not None:
            if off < self.pos:                 # back in a zip: from the start again
                self.f.close()
                self.f, self.pos = self.reopen(), 0
            while self.pos < off:              # forward: read past (never seek: that skips the checksum)
                skip = self.f.read(min(PS3_CHUNK, off - self.pos))
                if not skip:
                    raise Ps3Error("cut short at byte %d" % self.pos)
                self.pos += len(skip)
        elif self.pos != off:
            self.f.seek(off)
        data = self.f.read(count * PS3_SECTOR)
        if len(data) != count * PS3_SECTOR:
            raise Ps3Error("cut short at sector %d" % (lba + len(data) // PS3_SECTOR))
        self.pos = off + len(data)
        return data

    def decrypt(self, data, lba, key=None):
        """data (whole sectors from lba) with every encrypted, non-empty sector decrypted."""
        key = key or self.key
        if self.plain or not key:
            return data
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

    def _probes(self):
        """[(lba, first sector as on the disc, magic)] for one or two files with a known
        start whose first sector is encrypted (EBOOT.BIN when there are none)."""
        found = []
        for path, exts in self.files.items():
            lba, length = exts[0]
            if length < 16 or not self.encrypted(lba):
                continue
            for match, magic in self.PROBES:
                if match(path):
                    found.append((lba, magic))
                    break
        found.sort()
        if not found:
            found = [(self.files[PS3_EBOOT][0][0], b"SCE\0")]
        found = found[:1] + [f for f in found[1:2] if f[0] - found[0][0] <= self.PROBE_NEAR]
        return [(lba, self.raw(lba, 1), magic) for lba, magic in found]

    def _walk(self):
        """{"DIR/FILE": [(lba, length), ...]} from the primary volume descriptor
        (Joliet's names when the disc has them). Names are checked: no paths out."""
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
        import heapq
        files, seen = {}, set()
        todo = [(struct.unpack("<I", vd[158:162])[0], "", vd[156:190])]
        while todo:                                      # in disc order: a zip is read forward
            _, prefix, rec = heapq.heappop(todo)
            lba, length = struct.unpack("<I", rec[2:6])[0], struct.unpack("<I", rec[10:14])[0]
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
                r = data[pos:pos + rl]
                pos += rl
                nl = r[32]
                raw_name = r[33:33 + nl]
                if raw_name in (b"\0", b"\1"):
                    continue
                name = raw_name.decode("utf-16-be" if wide else "latin-1", "replace").split(";")[0]
                if not wide:
                    name = name.rstrip(".")
                if not name or name in (".", "..") or "/" in name or "\\" in name or "\0" in name:
                    raise Ps3Error("unsafe name on the disc: %r" % name)
                path = prefix + name
                ext = (struct.unpack("<I", r[2:6])[0], struct.unpack("<I", r[10:14])[0])
                if r[25] & 2:
                    heapq.heappush(todo, (ext[0], path + "/", r))
                    continue
                if pending and pending[0] == path:
                    files[path].append(ext)              # the next piece of a file over 4 GB
                else:
                    if path in files:
                        raise Ps3Error("two files named %s" % path)
                    files[path] = [ext]
                pending = (path,) if r[25] & 0x80 else None
        return files

    def find_key(self, keys):
        """The name of the key that opens this disc (checked on the first sectors of
        files with a known start), or None."""
        for name in ps3_key_order(self.name, keys):
            k = keys[name]
            if all(self.decrypt(data, lba, k)[:len(m)] == m for lba, data, m in self.probes):
                return name
        return None

    def total(self):
        return sum(length for exts in self.files.values() for _, length in exts)

    def extract(self, out, pause=None, progress=None):
        """Write every file under out/ (which must not exist), reading the image
        forward, decrypting as needed. Checks EBOOT.BIN and PARAM.SFO after."""
        pieces, offset = [], {}
        for path, exts in self.files.items():
            at = 0
            for lba, length in exts:
                pieces.append((lba, length, path, at))
                at += length
        pieces.sort()
        total, done = self.total() or 1, 0
        os.makedirs(out)
        real = os.path.realpath(out)
        made = set()
        for lba, length, path, at in pieces:
            dest = os.path.join(out, *path.split("/"))
            if not os.path.realpath(dest).startswith(real + os.sep):
                raise Ps3Error("unsafe name on the disc: %s" % path)
            if path not in made:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                open(dest, "wb").close()
                made.add(path)
            with open(dest, "r+b") as f:
                f.seek(at)
                left, cur = length, lba
                while left > 0:
                    if pause:
                        pause()
                    n = min(PS3_CHUNK, left)
                    secs = (n + PS3_SECTOR - 1) // PS3_SECTOR
                    data = self.decrypt(self.raw(cur, secs), cur)
                    f.write(data[:n])
                    left -= n
                    cur += secs
                    done += n
                    if progress:
                        progress(done / total)
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
    z = zipfile.ZipFile(src)
    try:
        isos = [i for i in z.infolist() if i.filename.lower().endswith(".iso") and not i.is_dir()]
        others = [i for i in z.infolist() if i not in isos and not i.is_dir()
                  and not i.filename.lower().endswith((".txt", ".nfo", ".md5", ".sfv"))]
        if len(isos) != 1 or others:
            raise Ps3Error("the zip doesn't hold exactly one disc image")
        disc = Ps3Disc(z.open(isos[0]), isos[0].file_size, name, reopen=lambda: z.open(isos[0]))
    except Exception:
        z.close()
        raise
    return disc, lambda: (disc.f.close(), z.close())


def ps3_image_size(src):
    """Bytes of the disc image: the .iso, or the one inside a zip."""
    if src.lower().endswith(".zip"):
        with zipfile.ZipFile(src) as z:
            return sum(i.file_size for i in z.infolist() if i.filename.lower().endswith(".iso"))
    return os.path.getsize(src)


def ps3_zip_is_disc(path):
    """True for a zip holding one PS3 disc image (and maybe notes): kept zipped
    for the PS3 converter, which reads it in place."""
    try:
        with zipfile.ZipFile(path) as z:
            names = [i.filename.lower() for i in z.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, OSError):
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


def fetch_ps3_keys(api, platform_id):
    """Copy the disc keys in RomM's PS3 firmware into bios/ps3keys/ (a file already
    there with the same size and MD5 is kept). Returns how many keys the folder
    now holds. A file with no keys in it isn't kept."""
    folder = ps3_keys_dir()
    os.makedirs(folder, exist_ok=True)
    real = os.path.realpath(folder)
    for fw in ps3_key_files(api, platform_id):
        name = os.path.basename(fw["file_name"])
        dest = os.path.join(folder, name)
        if not os.path.realpath(dest).startswith(real + os.sep) or name.startswith("."):
            continue
        if os.path.isfile(dest) and os.path.getsize(dest) == fw.get("file_size_bytes") \
                and fw.get("md5_hash") and _bios.file_md5(dest) == fw["md5_hash"].lower():
            continue
        part = dest + ".part"
        try:
            r = api.s.get("%s/api/firmware/%d/content/%s" % (api.base, fw["id"], urllib.parse.quote(name)),
                          timeout=60, stream=True)
        except Exception as e:
            raise _core.RommError("Key download failed (%s)" % e.__class__.__name__) from None
        try:
            if r.status_code != 200:
                raise _core.RommError("Key download failed: HTTP %d" % r.status_code)
            n = 0
            with open(part, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    n += len(chunk)
                    if n > PS3_KEYZIP_MAX:
                        raise _core.RommError("%s is too big for a key file; not kept." % name)
                    f.write(chunk)
        except (_core.RommError, requests.RequestException, OSError) as e:
            try:
                os.remove(part)
            except OSError:
                pass
            if isinstance(e, _core.RommError):
                raise
            raise _core.RommError("Key download failed (%s)" % e.__class__.__name__) from None
        finally:
            r.close()
        found = {}
        try:
            if name.lower().endswith(".zip"):
                with zipfile.ZipFile(part) as z:
                    _ps3_keys_from_zip(z, found)
            else:
                with open(part, "rb") as f:
                    k = _ps3_key_bytes(f.read(PS3_KEY_MAX + 1))
                found = {name: k} if k else {}
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
            found = {}
        if not found:
            os.remove(part)
            _core.log("ps3: %s from RomM holds no disc keys; not kept" % name)
            continue
        os.replace(part, dest)
        _core.log("ps3: disc keys from RomM: %s (%d)" % (name, len(found)))
    return len(ps3_keys(folder))


_PS3_CHECKED = {}


def ps3_check(src, cfg=None):
    """What a PS3 disc needs: {"plain": True} or {"plain": False, "key": name}.
    Ps3Error when it can't become a folder (and why: decrypting off, no key).
    Remembered until the file, the keys folder or the setting changes."""
    cfg = _config.load_config() if cfg is None else cfg
    try:
        st = os.stat(src)
    except OSError as e:
        raise Ps3Error("can't read it (%s)" % (e.strerror or e)) from None
    keys = ps3_keys() if _config.config_value(cfg, "ps3_decrypt") else {}
    memo = (src, st.st_size, st.st_mtime_ns, bool(_config.config_value(cfg, "ps3_decrypt")), id(keys), len(keys))
    hit = _PS3_CHECKED.get(src)
    if hit and hit[0] == memo:
        if isinstance(hit[1], Ps3Error):
            raise Ps3Error(str(hit[1]))
        return dict(hit[1])
    try:
        res = _ps3_check(src, cfg)
    except Ps3Error as e:
        _PS3_CHECKED[src] = (memo, e)
        raise
    if len(_PS3_CHECKED) > 512:
        _PS3_CHECKED.clear()
    _PS3_CHECKED[src] = (memo, res)
    return dict(res)


def _ps3_check(src, cfg):
    try:
        disc, close = ps3_disc(src)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as e:
        raise Ps3Error("can't read it (%s)" % e.__class__.__name__) from None
    try:
        if disc.plain:
            return {"plain": True, "size": disc.total()}
        if not _config.config_value(cfg, "ps3_decrypt"):
            raise Ps3Error("encrypted; turn on Decrypt PS3 discs (uses disc keys for games you own)")
        if not ps3_decrypt_supported():
            raise Ps3Error("encrypted, and Python's cryptography module isn't on this system")
        name = disc.find_key(ps3_keys())
        if name is None:
            raise Ps3Error("encrypted, and bios/ps3keys has no disc key for it (add the key for "
                           "this game you own)")
        return {"plain": False, "key": name, "size": disc.total()}
    finally:
        close()


def ps3_to_folder(src, out, cfg=None, pause=None, progress=None):
    """Turn a PS3 disc (.iso or zipped .iso) into the game folder out (Name.ps3),
    decrypting with its key when needed. A zip's own checksum is checked by
    reading it to the end. Returns the key's name (None: no key needed)."""
    info = ps3_check(src, cfg)
    disc, close = ps3_disc(src)
    try:
        if not info["plain"]:
            disc.key = ps3_keys()[info["key"]]
        disc.extract(out, pause, progress)
        if src.lower().endswith(".zip"):
            try:                               # the rest of the stream: the zip checks its CRC at the end
                while disc.f.read(PS3_CHUNK):
                    if pause:
                        pause()
            except zipfile.BadZipFile as e:
                raise Ps3Error("the zip is damaged (%s)" % e) from None
        return info.get("key")
    finally:
        close()
