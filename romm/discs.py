"""Disc images: incomplete-image checks, Xbox XISO, PSP .cso, cue/gdi sheets."""
import io
import os
import re
import shutil
import struct
import subprocess
import time

from . import core as _core


# ---------------------------------------------------------- incomplete disc images

def _pvd_volume(sector16):
    """ISO9660 volume size (sectors) from the 2048-byte sector 16, or None."""
    if len(sector16) >= 88 and sector16[1:6] == b"CD001":
        return int.from_bytes(sector16[80:84], "little")
    return None


class _Beyond(Exception):
    """A sector past what was read (and, for a CHD head, maybe past the end)."""
    def __init__(self, lba):
        self.lba = lba


def _files_end(read, limit=4_300_000, have=None):
    """Walk an ISO9660 directory tree: (end, boot_end) — the highest sector
    any entry reaches (exclusive), and the highest the game needs to start
    (root directory, SYSTEM.CNF and the boot program it names). None if
    there's no ISO9660 filesystem. read(lba) returns 2048 bytes or raises
    _Beyond; sectors at/after `have` (the image's end) count as unreadable
    and are recorded rather than raised. Entries past `limit` (no disc is
    that big) are garbage and ignored."""
    pvd = read(16)
    if pvd[1:6] != b"CD001":
        return None
    root = pvd[156:190]
    rlba, rsize = int.from_bytes(root[2:6], "little"), int.from_bytes(root[10:14], "little")
    todo = [("", rlba, rsize)]
    seen, end, files = set(), 0, {}
    boot_end = rlba + (rsize + 2047) // 2048
    while todo and len(seen) < 5000:
        path, lba, size = todo.pop()
        if lba in seen or size <= 0 or lba + (size + 2047) // 2048 > limit:
            continue
        seen.add(lba)
        nsec = (size + 2047) // 2048
        end = max(end, lba + nsec)
        for k in range(nsec):
            if have is not None and lba + k >= have:
                break                                   # past the image: can't be read
            sec = read(lba + k)
            i = 0
            while i < 2048 and sec[i]:
                rec = sec[i:i + sec[i]]
                i += sec[i]
                if len(rec) < 34:
                    break
                name = rec[33:33 + rec[32]]
                if name in (b"\x00", b"\x01"):
                    continue
                flba, fsize = int.from_bytes(rec[2:6], "little"), int.from_bytes(rec[10:14], "little")
                fend = flba + (fsize + 2047) // 2048
                if fend > limit:
                    continue
                full = path + "/" + name.decode("ascii", "replace").split(";")[0].upper()
                if rec[25] & 2:
                    todo.append((full, flba, fsize))
                elif fsize:
                    end = max(end, fend)
                    files[full] = (flba, fsize, fend)
    cnf = files.get("/SYSTEM.CNF")
    if cnf:
        boot_end = max(boot_end, cnf[2])
        if have is None or cnf[0] < have:
            m = re.search(rb"BOOT2?\s*=\s*cdrom0?:\\?([^\s;]+)", read(cnf[0])[:cnf[1]], re.I)
            if m:
                exe = "/" + m.group(1).decode("ascii", "replace").replace("\\", "/").lstrip("/").upper()
                if exe in files:
                    boot_end = max(boot_end, files[exe][2])
    return end, boot_end


def _verdict(have, r, vol=None):
    """(have, need, certain) from _files_end's (end, boot_end): certain only
    when what the game needs to start lies past the end of the image. Then
    the directory tree couldn't be read, so the header's size says how big
    the disc should be."""
    if not r:
        return None
    end, boot_end = r
    sure = boot_end > have
    need = max(end, boot_end, vol or 0) if sure else max(end, boot_end)
    return (have, need, sure)


def _sector_reader(f, raw, base=0):
    """read(lba) over an open image (raw 2352-byte or plain 2048-byte sectors)."""
    def read(lba):
        if raw:
            f.seek(base + lba * 2352)
            sec = f.read(2352)
            if len(sec) < 2352:
                raise _Beyond(lba)
            off = 16 if sec[15] == 1 else 24
            return sec[off:off + 2048]
        f.seek(base + lba * 2048)
        sec = f.read(2048)
        if len(sec) < 2048:
            raise _Beyond(lba)
        return sec
    return read


def _chd_head(path, nbytes, chdman):
    """First nbytes of a CHD's track data (chdman extract*, stopped early),
    plus chdman info's text and whether it's a DVD CHD."""
    import tempfile
    tmpdir = _core.tmp_dir()
    os.makedirs(tmpdir, exist_ok=True)
    t = tempfile.mkdtemp(prefix="head-", dir=tmpdir)
    try:
        info = subprocess.run([chdman, "info", "-i", path], capture_output=True, text=True, timeout=30).stdout
        dvd = "Tag='DVD" in info
        b = os.path.join(t, "x.bin")
        cmd = [chdman, "extractdvd", "-i", path, "-o", b, "-f"] if dvd else \
            [chdman, "extractcd", "-i", path, "-o", os.path.join(t, "x.cue"), "-ob", b, "-f"]
        pr = subprocess.Popen(["nice", "-n", "19"] + cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + 120
        while pr.poll() is None and time.time() < end:
            if os.path.exists(b) and os.path.getsize(b) >= nbytes:
                break
            time.sleep(0.05)
        pr.kill()
        pr.wait()
        with open(b, "rb") as f:
            head = f.read(nbytes)
        return head, info, dvd
    except (OSError, subprocess.SubprocessError):
        return None, "", False
    finally:
        shutil.rmtree(t, ignore_errors=True)


def _quick_ok(read, have):
    """The header says the filesystem fits in the image: complete, no walk needed."""
    vol = _pvd_volume(read(16))
    return vol is not None and vol <= have + 16


def disc_sectors(path, chdman="chdman"):
    """(sectors in the image, sectors its files need, certain) for a
    .cue/.iso/.chd whose first track is ISO9660 data; None when that can't be
    told (audio first track, GDI, unreadable). Header within the image:
    complete. Else the whole directory tree is walked: the root directory,
    SYSTEM.CNF or its boot program past the end is certain proof it's cut
    short (the game can't start); anything else past the end is only a hint
    (some discs list dummy files and directories past their end)."""
    low = path.lower()
    try:
        if low.endswith(".iso"):
            with open(path, "rb") as f:
                have = os.path.getsize(path) // 2048
                read = _sector_reader(f, False)
                try:
                    vol = _pvd_volume(read(16))
                    if _quick_ok(read, have):
                        return (have, vol, False)
                    r = _files_end(read, 4_300_000, have)
                except _Beyond:
                    return None                         # no header at all: not a disc image we can judge
            return _verdict(have, r, vol)
        if low.endswith(".cue"):
            tracks = sheet_sources(path)
            if not tracks or not all(os.path.isfile(t) for t in tracks):
                return None
            with open(tracks[0], "rb") as f:
                raw = f.read(12) == b"\x00" + b"\xff" * 10 + b"\x00"
                size = 2352 if raw else 2048
                have = sum(os.path.getsize(t) for t in tracks) // size
                read = _sector_reader(f, raw)
                try:
                    vol = _pvd_volume(read(16))
                    if _quick_ok(read, have):
                        return (have, vol, False)
                    # a multi-file cue: only the first (data) track's file is read
                    r = _files_end(read, 450_000 if raw else 4_300_000,
                                   os.path.getsize(tracks[0]) // size)
                except _Beyond:
                    return None
            return _verdict(have, r, vol)
        if low.endswith(".chd"):
            want = 64 << 10                             # header first; the tree only if it's in doubt
            while True:
                head, info, dvd = _chd_head(path, want, chdman)
                if not head:
                    return None
                if dvd:
                    m = re.search(r"Logical size:\s*([\d,]+)", info)
                    have = int(m.group(1).replace(",", "")) // 2048 if m else 0
                else:
                    have = sum(int(x) for x in re.findall(r"FRAMES:(\d+)", info))
                raw = not dvd and head[:12] == b"\x00" + b"\xff" * 10 + b"\x00"
                import io
                read = _sector_reader(io.BytesIO(head), raw)
                try:
                    if _quick_ok(read, have):
                        return (have, _pvd_volume(read(16)), False)
                    if want < 8 << 20:
                        want = 8 << 20
                        continue
                    return _verdict(have, _files_end(read, 4_300_000 if dvd else 450_000, have),
                                    _pvd_volume(read(16))) if have else None
                except _Beyond as b:
                    if b.lba <= 16 or len(head) < want or want >= 256 << 20:
                        return None                     # no header, or can't reach it cheaply: don't guess
                    want *= 4                           # a directory further in: read more
    except (OSError, ValueError, IndexError):
        return None
    return None


def disc_incomplete(path, chdman="chdman"):
    """None if complete (or can't tell); else (sectors present, sectors
    needed, certain) — see disc_sectors."""
    r = disc_sectors(path, chdman)
    if not r:
        return None
    have, need, sure = r
    return r if sure or need > have + 16 else None


def xbox_partition(path):
    """Where an Xbox image's game partition starts: 0 for an XISO (what xemu
    boots), the offset for a full redump image, None if it's neither."""
    try:
        with open(path, "rb") as f:
            for off in (0,) + _core.XBOX_PARTITIONS:
                f.seek(off + 0x10000)
                if f.read(len(_core.XDVDFS_MAGIC)) == _core.XDVDFS_MAGIC:
                    return off
    except OSError:
        pass
    return None


def _collapse_front(path, nbytes):
    """Drop the first nbytes of a file in place (Linux fallocate collapse-range:
    no copy, no extra space). False where the filesystem can't (exFAT, btrfs);
    any other failure raises OSError (never a reason to fall back to a copy)."""
    import ctypes
    import ctypes.util
    import errno
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    except OSError:
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        if libc.fallocate(fd, 0x08, ctypes.c_long(0), ctypes.c_long(nbytes)) == 0:   # FALLOC_FL_COLLAPSE_RANGE
            return True
        err = ctypes.get_errno()
        if err in (errno.EOPNOTSUPP, errno.ENOTSUP, errno.ENOSYS):
            return False
        raise OSError(err, os.strerror(err), path)
    finally:
        os.close(fd)


def make_xiso(path, free=None):
    """Make a full redump Xbox image bootable: keep only its game partition.
    In place when the filesystem allows, else through a copy (needs the
    room). Returns the bytes dropped (0 if it was already an XISO, or isn't
    an Xbox image); raises NoSpace when a copy doesn't fit. Locked per image
    and re-checked under the lock: two callers (rb_chd_plan and the worker)
    must never both cut the same image."""
    import fcntl
    with open(path, "rb") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        off = xbox_partition(path)
        if not off:
            return 0
        size = os.path.getsize(path)
        if size <= off:
            raise OSError("%s is smaller than its game partition's offset; left as it is" % path)
        if _collapse_front(path, off):
            return off
        free = _core.free_bytes(os.path.dirname(path)) if free is None else free
        if free < size - off + _core.SPACE_MARGIN:
            raise _core.NoSpace(size - off + _core.SPACE_MARGIN - free)
        tmp = path + ".xiso.part"
        try:
            with open(path, "rb") as src, open(tmp, "wb") as out:
                src.seek(off)
                shutil.copyfileobj(src, out, 4 << 20)
                out.flush()
                os.fsync(out.fileno())
            if os.path.getsize(tmp) != size - off:
                raise OSError("copying %s's game partition came up short; left as it is" % path)
            os.replace(tmp, path)
        except BaseException:           # failed or stopped (the worker's SIGTERM): no half copy left
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return off


def xbox_cut(system, path, romm_file):
    """An Xbox image this box cut to its game partition (make_xiso): exactly
    one partition offset smaller than RomM's full disc, and an XISO now."""
    want = (romm_file or {}).get("file_size_bytes")
    try:
        short = want - os.path.getsize(path) if want else None
    except OSError:
        return False
    return system == "xbox" and short in _core.XBOX_PARTITIONS and xbox_partition(path) == 0


def xiso_to_iso(path):
    """ "X.xiso" -> "X.iso" (Batocera lists Xbox games as .iso / .squashfs only).
    Returns the path the file now has; left alone if an "X.iso" exists."""
    if not path.lower().endswith(".xiso"):
        return path
    new = path[:-5] + ".iso"
    if os.path.exists(new):
        _core.log("xbox: %s kept as is: %s exists" % (os.path.basename(path), os.path.basename(new)))
        return path
    os.rename(path, new)
    return new


# ---------------------------------------------------------- PSP .cso (compressed ISO) -> CHD
# CSO ("CISO"): a 24-byte header (magic, header size, uncompressed size u64,
# block size u32, version u8, index alignment u8), then one u32 per block plus
# an end marker: the block's file offset >> align. Version 0/1: the top bit
# marks a block stored as it is, else it's raw deflate. Version 2 (maxcso) uses
# the top bit for LZ4 instead and stores a block when its size reaches the
# block size; Batocera has no LZ4 for Python, so only deflate-only v2 files are
# read, and ZSO (LZ4 throughout) is refused. Decoded to a temporary ISO that
# chdman createdvd packs (Batocera has no maxcso/ciso tool).
CSO_MAGIC = b"CISO"
CSO_HEADER = 0x18
CSO_MAX_BYTES = 64 << 30                # far past any UMD (1.8 GB): bigger is a broken header
CSO_TEMP_SUFFIX = ".cso-iso.part"       # the decoded ISO beside the CHD while it's packed (ES never lists it)
CSO_NO_LZ4 = "CSO v2/ZSO isn't supported (needs LZ4)"


class CsoError(Exception):
    """A .cso that can't be decoded: damaged, cut short, or a variant this can't read."""


def cso_info(path):
    """Read and validate a .cso's header and block index: {"total", "block",
    "align", "version", "offsets"} (offsets: the n+1 index entries as
    (file offset, top bit) pairs). CsoError for anything that isn't a CSO
    this can decode without guessing."""
    import struct
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        h = f.read(CSO_HEADER)
        if h[:4] == b"ZISO":
            raise CsoError(CSO_NO_LZ4)
        if len(h) < CSO_HEADER or h[:4] != CSO_MAGIC:
            raise CsoError("not a CSO image")
        hsize, total, block, version, align = struct.unpack_from("<IQIBB", h, 4)
        if version not in (0, 1, 2):
            raise CsoError("CSO version %d isn't supported" % version)
        if version == 2 and hsize != CSO_HEADER:
            raise CsoError("damaged CSO header (v2 header size %d)" % hsize)
        if block < 2048 or block > 1 << 24 or block & (block - 1):
            raise CsoError("damaged CSO header (block size %d)" % block)
        if not 0 < total <= CSO_MAX_BYTES:
            raise CsoError("damaged CSO header (image size %d)" % total)
        if total % 2048:
            raise CsoError("not a whole number of 2048-byte sectors (%d bytes)" % total)
        if align > 31:
            raise CsoError("damaged CSO header (index alignment %d)" % align)
        n = -(-total // block)
        end_index = CSO_HEADER + 4 * (n + 1)
        raw = f.read(4 * (n + 1)) if end_index <= size else b""
    if len(raw) != 4 * (n + 1):
        raise CsoError("cut short (the block index doesn't fit in the file)")
    entries = struct.unpack("<%dI" % (n + 1), raw)
    offsets = [((e & 0x7FFFFFFF) << align, e >> 31) for e in entries]
    if version == 2 and any(top for _, top in offsets[:-1]):
        raise CsoError(CSO_NO_LZ4)
    prev = end_index
    for i, (off, _) in enumerate(offsets):
        if off < prev or (i and off == prev):        # every block holds at least one byte
            raise CsoError("damaged block index (entry %d of %d)" % (i, n + 1))
        prev = off
    if offsets[-1][0] > size:
        raise CsoError("cut short (%d of %d bytes)" % (size, offsets[-1][0]))
    return {"total": total, "block": block, "align": align, "version": version, "offsets": offsets}


def cso_decode(src, dst, pause=None, progress=None):
    """Decode a .cso to the plain image at dst; returns its SHA-1 (hex),
    computed while writing. Every block must decode to exactly the block
    size (the last one: what's left of the image, or a whole block that's
    then cut to it) and the total to the header's size; CsoError otherwise
    (dst is left for the caller to remove). pause(): called between blocks
    (waits while a game runs); progress(fraction)."""
    import hashlib
    import zlib
    info = cso_info(src)
    total, block, version, offs = info["total"], info["block"], info["version"], info["offsets"]
    slack = 1 << info["align"]                      # padding allowed after a block's data
    n = len(offs) - 1
    sha = hashlib.sha1()
    written = 0
    with open(src, "rb") as f, open(dst, "wb") as out:
        for i in range(n):
            if not i % 512:
                if pause:
                    pause()
                if progress:
                    progress(written / total)
            start, top = offs[i]
            span = offs[i + 1][0] - start
            want = min(block, total - written)
            f.seek(start)
            data = f.read(span)
            if len(data) != span:
                raise CsoError("cut short (block %d of %d)" % (i, n))
            if (top if version < 2 else span >= block):          # stored as it is
                if not want <= span < want + slack:
                    raise CsoError("damaged block %d of %d (stored, %d bytes for %d)" % (i, n, span, want))
                plain = data[:want]
            else:
                d = zlib.decompressobj(-15)
                try:
                    plain = d.decompress(data, block + 1)
                except zlib.error as e:
                    raise CsoError("damaged block %d of %d (%s)" % (i, n, e)) from None
                if not d.eof or len(d.unused_data) >= slack:
                    raise CsoError("damaged block %d of %d (incomplete deflate data)" % (i, n))
                if len(plain) == block and want < block:
                    plain = plain[:want]                # a last block padded to the full size
                if len(plain) != want:
                    raise CsoError("damaged block %d of %d (%d bytes, expected %d)" % (i, n, len(plain), want))
            out.write(plain)
            sha.update(plain)
            written += want
    if written != total:
        raise CsoError("decoded %d of %d bytes" % (written, total))
    if progress:
        progress(1.0)
    return sha.hexdigest()


# ----------------------------------------------------------- audit

# a cue's FILE line: quoted, or a bare name without spaces (both are valid)
CUE_FILE_RE = re.compile(r'FILE\s+(?:"([^"]+)"|(\S+))', re.I)


def sheet_sources(sheet, strict=False):
    """Track files a .cue/.gdi references (paths). An unreadable sheet: []
    (strict: the OSError is raised)."""
    d = os.path.dirname(sheet)
    out = []
    try:
        with open(sheet, errors="surrogateescape") as f:      # odd bytes map to the real names
            text = f.read()
    except OSError:
        if strict:
            raise
        return out
    if sheet.lower().endswith(".cue"):
        out = [os.path.join(d, m.group(1) or m.group(2)) for m in CUE_FILE_RE.finditer(text)]
    else:
        for line in text.splitlines()[1:]:
            m = re.match(r'\s*\d+\s+\d+\s+\d+\s+\d+\s+("([^"]+)"|(\S+))', line)
            if m:
                out.append(os.path.join(d, m.group(2) or m.group(3)))
    return out


def _sheets_in(path):
    if os.path.isfile(path):
        return [path] if path.lower().endswith((".cue", ".gdi")) else []
    out = []
    for dp, dns, fns in os.walk(path):
        dns[:] = [x for x in dns if x not in _core.MEDIA_DIRS]
        out += [os.path.join(dp, f) for f in fns if f.lower().endswith((".cue", ".gdi"))]
    return out
