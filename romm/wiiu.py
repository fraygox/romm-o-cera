"""Wii U discs: disc keys, and the checks before a .wud/.wux becomes a .wua."""
import io
import os
import struct
import zipfile

from . import core as _core, bios as _bios


# ---------------------------------------------------------- Wii U discs
# A Wii U disc image (.wud, or the same image in the deduplicated .wux container)
# becomes a .wua, the ZArchive of its game partition Cemu opens directly (wuatool,
# romm/tools.py). Its partitions are encrypted with the disc's key, so wuatool
# needs that key: the user's own, for discs they own, never shipped or downloaded
# from anywhere but the user's RomM. Read from Cemu's keys.txt (bios/cemu, where
# Batocera's Cemu reads it) and bios/wiiukeys/ (.key files, key lists, zips of
# them; also copied from RomM's Wii U firmware). The Wii U common key is Cemu's
# own, built into wuatool from Cemu's source.

WUD_MAGIC = 0xCC549EB9                 # at 0x10000 of every Wii U disc
WUX_MAGIC = (b"WUX0", 0x1099D02E)
WIIU_KEY_EXTS = (".key", ".txt")
WIIU_KEY_MAX = 1 << 20                 # one key file or key list
WIIU_KEYZIP_MAX = 256 << 20


class WiiuError(Exception):
    """A Wii U disc image that can't become a .wua: not a disc, or no key for it."""


def cemu_keys_path():
    return os.path.join(_core.USERDATA, "bios", "cemu", "keys.txt")


def wiiu_keys_dir():
    return os.path.join(_core.USERDATA, "bios", "wiiukeys")


def low_example(line):
    """Cemu writes a sample line into a new keys.txt ("... # example key (can be
    deleted)"): it's no disc's key, and counting it said "1 key ready" for none."""
    return "example" in line.lower()


def parse_keys(data):
    """The keys in a key file: 16 raw bytes (Redump's .key), else Cemu's keys.txt
    rules: '#' or ';' starts a comment, spaces, tabs, '-' and '_' are dropped, and a
    line of exactly 32 hex digits is a key."""
    out = []
    try:
        text = bytes(data).decode("ascii")
    except UnicodeDecodeError:
        text = None
    if text is not None:
        for line in text.splitlines():
            raw = line
            for c in "#;":
                line = line.split(c, 1)[0]
            line = "".join(ch for ch in line if ch not in " \t-_\r")
            if len(line) == 32 and not low_example(raw):
                try:
                    out.append(bytes.fromhex(line))
                except ValueError:
                    pass
    if not out and len(data) == 16:
        out.append(bytes(data))
    return out


def _keys_from_zip(z, keys, depth=0):
    for info in z.infolist():
        low = info.filename.lower()
        if info.is_dir():
            continue
        if low.endswith(WIIU_KEY_EXTS) and info.file_size <= WIIU_KEY_MAX:
            keys += parse_keys(z.read(info))
        elif low.endswith(".zip") and depth == 0 and info.file_size <= WIIU_KEYZIP_MAX:
            try:                                    # Redump's set: one small zip per disc
                with zipfile.ZipFile(io.BytesIO(z.read(info))) as inner:
                    _keys_from_zip(inner, keys, 1)
            except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
                continue


def _file_keys(path, name=None):
    """The keys in a key file, a key list or a zip of them (name: the file's real
    name, when path is a temporary one)."""
    low = (name or path).lower()
    if low.endswith(".zip"):
        if os.path.getsize(path) > WIIU_KEYZIP_MAX:
            return []
        keys = []
        with zipfile.ZipFile(path) as z:
            _keys_from_zip(z, keys)
        return keys
    if low.endswith(WIIU_KEY_EXTS) and os.path.getsize(path) <= WIIU_KEY_MAX:
        with open(path, "rb") as f:
            return parse_keys(f.read())
    return []


_KEYS = {"sig": None, "keys": []}


def wiiu_keys():
    """Every disc key here, Cemu's keys.txt first, each once. Cached until a
    file changes; unreadable files are skipped."""
    folder = wiiu_keys_dir()
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        names = []
    paths = [cemu_keys_path()] + [os.path.join(folder, n) for n in names]
    sig = []
    for p in paths:
        try:
            st = os.stat(p)
            sig.append((p, st.st_size, st.st_mtime_ns))
        except OSError:
            pass
    if _KEYS["sig"] == sig:
        return _KEYS["keys"]
    keys, seen = [], set()
    for p in paths:
        if not os.path.isfile(p):
            continue
        try:
            found = _file_keys(p)
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as e:
            _core.log("wiiu: skipped key file %s (%s)" % (os.path.basename(p), e.__class__.__name__))
            continue
        for k in found:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    _KEYS.update(sig=sig, keys=keys)
    return keys


def wiiu_key_file(path):
    """Write every disc key into one key list for wuatool (readable by this user
    only); returns how many."""
    keys = wiiu_keys()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("".join(k.hex() + "\n" for k in keys))
    return len(keys)


class _Disc:
    """Reads a .wud, or a .wux through its sector index, by disc offset."""

    def __init__(self, f):
        self.f = f
        f.seek(0)
        head = f.read(0x20)
        self.wux = len(head) == 0x20 and head[:4] == WUX_MAGIC[0] and \
            struct.unpack_from("<I", head, 4)[0] == WUX_MAGIC[1]
        if self.wux:
            self.sector, _flags, self.size = struct.unpack_from("<IIQ", head, 8)
            if self.sector < 0x100 or self.sector > (1 << 24) or self.sector & (self.sector - 1):
                raise WiiuError("damaged .wux header")
            self.count = (self.size + self.sector - 1) // self.sector
            self.data = (0x20 + self.count * 4 + self.sector - 1) // self.sector * self.sector

    def read(self, offset, n):
        if not self.wux:
            self.f.seek(offset)
            return self.f.read(n)
        out = b""
        while n:
            i, at = divmod(offset, self.sector)
            if i >= self.count:
                break
            self.f.seek(0x20 + i * 4)
            idx = self.f.read(4)
            if len(idx) < 4:
                break
            self.f.seek(self.data + struct.unpack("<I", idx)[0] * self.sector + at)
            got = self.f.read(min(n, self.sector - at))
            if not got:
                break
            out += got
            offset += len(got)
            n -= len(got)
        return out


def _aes_cbc_decrypt(key, iv, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return d.update(data) + d.finalize()


def disc_key_for(f, keys):
    """The key among keys that decrypts this disc's partition table (Cemu's
    test: the bytes after its header decrypt to zeros), or None."""
    head = _Disc(f).read(0x18100, 48)
    if len(head) < 48:
        return None
    for k in keys:
        if not any(_aes_cbc_decrypt(k, head[:16], head[16:])):
            return k
    return None


_CHECKED = {}


def wiiu_check(src):
    """WiiuError when a .wud/.wux can't become a .wua: not a Wii U disc, or no
    disc key for it. The key is only looked for when Python's cryptography module
    is here (else wuatool says). Remembered until the file or the keys change."""
    try:
        st = os.stat(src)
    except OSError as e:
        raise WiiuError("can't read it (%s)" % (e.strerror or e)) from None
    keys = wiiu_keys()
    memo = (st.st_size, st.st_mtime_ns, _KEYS["sig"], len(keys))
    hit = _CHECKED.get(src)
    if hit and hit[0] == memo:
        if hit[1]:
            raise WiiuError(hit[1])
        return
    why = ""
    try:
        with open(src, "rb") as f:
            d = _Disc(f)
            magic = d.read(0x10000, 4)
            if len(magic) < 4 or struct.unpack(">I", magic)[0] != WUD_MAGIC:
                why = "not a Wii U disc image"
            else:
                try:
                    found = disc_key_for(f, keys)
                except ImportError:
                    found = True
                if not found:
                    why = "no disc key for it (Cemu's keys.txt in bios/cemu, or bios/wiiukeys)"
    except WiiuError as e:
        why = str(e)
    except OSError as e:
        raise WiiuError("can't read it (%s)" % (e.strerror or e)) from None
    _CHECKED[src] = (memo, why)
    if why:
        raise WiiuError(why)


def wiiu_key_files(api, platform_id):
    """RomM's Wii U firmware files that carry disc keys: keys.txt, .key files and
    zips whose name says keys. The user's own; RomM-o-Cera ships none."""
    out = []
    for fw in api.firmware(platform_id) if platform_id else []:
        n = (fw.get("file_name") or "").lower()
        if n.endswith(".key") or (n.endswith((".txt", ".zip")) and "key" in n):
            out.append(fw)
    return out


def fetch_wiiu_keys(api, platform_id):
    """Copy the disc keys in RomM's Wii U firmware into bios/wiiukeys/ (a file
    already there with the same size and MD5 is kept). Returns how many keys
    there are now, Cemu's keys.txt included. A file with no keys isn't kept."""
    folder = wiiu_keys_dir()
    os.makedirs(folder, exist_ok=True)
    real = os.path.realpath(folder)
    for fw in wiiu_key_files(api, platform_id):
        name = os.path.basename(fw["file_name"])
        dest = os.path.join(folder, name)
        if not os.path.realpath(dest).startswith(real + os.sep) or name.startswith("."):
            continue
        if os.path.isfile(dest) and os.path.getsize(dest) == fw.get("file_size_bytes") \
                and fw.get("md5_hash") and _bios.file_md5(dest) == fw["md5_hash"].lower():
            continue
        cap = WIIU_KEYZIP_MAX if name.lower().endswith(".zip") else WIIU_KEY_MAX
        if (fw.get("file_size_bytes") or 0) > cap:
            continue                            # bigger than any key file: not worth fetching
        part = dest + ".part"
        api.download_firmware(fw["id"], name, part, cap, "Key",
                              "%s is too big for a key file; not kept." % name)
        try:
            found = _file_keys(part, name)
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
            found = []
        if not found:
            os.remove(part)
            _core.log("wiiu: %s from RomM holds no disc keys; not kept" % name)
            continue
        os.replace(part, dest)
        _core.log("wiiu: disc keys from RomM: %s (%d)" % (name, len(found)))
    return len(wiiu_keys())
