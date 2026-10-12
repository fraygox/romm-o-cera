"""Conversions: the Z3DS codec, the converter table, the conversion queue."""
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import rommlib
from . import core as _core, config as _config, discs as _discs, tools as _tools, ps3 as _ps3


def squashfs_supported():
    return shutil.which("mksquashfs") is not None


def squashfs_enabled(cfg, system):
    return system in _core.SQUASH_SYSTEMS and bool(_config.config_value(cfg, "squashfs")) and squashfs_supported()


def rvz_enabled(cfg):
    return bool(_config.config_value(cfg, "rvz")) and _tools.dolphin_tool_path() is not None


# ---------------------------------------------------------- 3DS -> Z3DS (.zcci / .zcxi)

# Azahar (Batocera 43.1 ships 2125.0.1) opens Z3DS files directly: a 0x20-byte
# header, optional metadata, then a standard zstd *seekable* stream (independent
# frames + a seek table), as Azahar's own "Compress ROM File" writes them
# (src/common/zstd_compression.cpp). Written here with the box's libzstd through
# ctypes: no tool to download. Decrypted dumps only (Azahar can't run encrypted
# ones either). CIA stays as it is for now (32 MiB frames, title info from the CIA).
Z3DS_SYSTEMS = {"3ds"}
Z3DS_SOURCES = {".3ds": ".zcci", ".cci": ".zcci", ".cxi": ".zcxi"}
Z3DS_EXTS = (".zcci", ".zcxi", ".zcia")
Z3DS_FRAME = 256 << 10           # Azahar's frame size for CCI/CXI
Z3DS_LEVEL = 3                   # zstd's default, what Azahar uses
_ZSTD = []


class Z3dsError(Exception):
    pass


def _zstd():
    """The box's libzstd (ctypes), or None."""
    if not _ZSTD:
        import ctypes
        lib = None
        for name in ("libzstd.so.1", "libzstd.so"):
            try:
                lib = ctypes.CDLL(name)
                break
            except OSError:
                continue
        if lib is not None:
            c = ctypes
            lib.ZSTD_compressBound.restype = c.c_size_t
            lib.ZSTD_compressBound.argtypes = [c.c_size_t]
            lib.ZSTD_compress.restype = c.c_size_t
            lib.ZSTD_compress.argtypes = [c.c_void_p, c.c_size_t, c.c_char_p, c.c_size_t, c.c_int]
            lib.ZSTD_decompress.restype = c.c_size_t
            lib.ZSTD_decompress.argtypes = [c.c_void_p, c.c_size_t, c.c_char_p, c.c_size_t]
            lib.ZSTD_isError.restype = c.c_uint
            lib.ZSTD_isError.argtypes = [c.c_size_t]
            try:                                       # per-frame content checksums (zstd 1.4+)
                lib.ZSTD_createCCtx.restype = c.c_void_p
                lib.ZSTD_freeCCtx.argtypes = [c.c_void_p]
                lib.ZSTD_CCtx_setParameter.restype = c.c_size_t
                lib.ZSTD_CCtx_setParameter.argtypes = [c.c_void_p, c.c_int, c.c_int]
                lib.ZSTD_compress2.restype = c.c_size_t
                lib.ZSTD_compress2.argtypes = [c.c_void_p, c.c_void_p, c.c_size_t, c.c_char_p, c.c_size_t]
                lib.has_cctx = True
            except AttributeError:
                lib.has_cctx = False
        _ZSTD.append(lib)
    return _ZSTD[0]


def z3ds_supported():
    return _zstd() is not None


def z3ds_enabled(cfg, system):
    # not config_value: a cfg without "z3ds" (a partial one) never converts 3DS, though it defaults to on
    return system in Z3DS_SYSTEMS and bool((cfg or {}).get("z3ds", False)) and z3ds_supported()


def z3ds_target(path):
    """'X.3ds' -> 'X.zcci' (None if it isn't a file Z3DS packs)."""
    base, ext = os.path.splitext(path)
    out = Z3DS_SOURCES.get(ext.lower())
    return base + out if out else None


def z3ds_info(path):
    """(magic, title-info bytes) of a decrypted CCI/CXI; Z3dsError otherwise."""
    with open(path, "rb") as f:
        h = f.read(0x200)
        if h[0x100:0x104] == b"NCSD":
            f.seek(int.from_bytes(h[0x120:0x124], "little") * 0x200)    # partition 0: the game
            f_ncch, magic = f.read(0x200), b"NCSD"
        elif h[0x100:0x104] == b"NCCH":
            f_ncch, magic = h, b"NCCH"
        else:
            raise Z3dsError("not a 3DS cartridge image")
    if len(f_ncch) < 0x200 or f_ncch[0x100:0x104] != b"NCCH":
        raise Z3dsError("not a 3DS cartridge image")
    if not f_ncch[0x18F] & 0x04:
        raise Z3dsError("an encrypted dump (Azahar runs decrypted ones only)")
    unit = 0x200 << f_ncch[0x18E]
    pid, ver = f_ncch[0x118:0x120], f_ncch[0x112:0x114]
    size = int.from_bytes(f_ncch[0x104:0x108], "little") * unit
    return magic, pid + size.to_bytes(8, "little") + ver + b"\0" * 6


def _z3ds_metadata(items):
    out = bytearray([1])                       # metadata version
    for k, v in items:
        out += bytes([1, len(k)]) + len(v).to_bytes(2, "little") + k + v
    out += b"\0" * 4                           # end marker
    out += b"\0" * ((-len(out)) % 16)
    return bytes(out)


def z3ds_compress(src, dst, threads=2, pause=None, progress=None):
    """Write src (decrypted .3ds/.cci/.cxi) as Z3DS to dst. pause(): called
    between frames, may block (a game is running). progress(fraction).
    Returns the source's CRC32 (hex)."""
    import ctypes
    import zlib
    from concurrent.futures import ThreadPoolExecutor
    lib = _zstd()
    if lib is None:
        raise Z3dsError("libzstd isn't on this system")
    magic, title = z3ds_info(src)
    total = os.path.getsize(src)
    md = _z3ds_metadata([(b"compressor", b"RomM-o-Cera " + rommlib.VERSION.encode()),
                         (b"date", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()).encode()),
                         (b"maxframesize", str(Z3DS_FRAME).encode()), (b"titleinfo", title)])

    def pack(chunk):                       # ctypes drops the GIL: frames compress in parallel
        cap = lib.ZSTD_compressBound(len(chunk))
        buf = ctypes.create_string_buffer(cap)
        if lib.has_cctx:
            # each frame carries zstd's content checksum: a damaged .zcci then fails to
            # decode on its own (Check games, replacing a broken copy), not only against
            # the original's CRC. Azahar reads such frames as usual.
            cc = lib.ZSTD_createCCtx()
            try:
                lib.ZSTD_CCtx_setParameter(cc, 100, Z3DS_LEVEL)      # ZSTD_c_compressionLevel
                lib.ZSTD_CCtx_setParameter(cc, 201, 1)               # ZSTD_c_checksumFlag
                n = lib.ZSTD_compress2(cc, buf, cap, chunk, len(chunk))
            finally:
                lib.ZSTD_freeCCtx(cc)
        else:
            n = lib.ZSTD_compress(buf, cap, chunk, len(chunk), Z3DS_LEVEL)
        if lib.ZSTD_isError(n):
            raise Z3dsError("zstd couldn't compress a frame")
        return buf.raw[:n]
    crc, done, packed, table = 0, 0, 0, []
    with open(src, "rb") as fi, open(dst, "wb") as fo, ThreadPoolExecutor(max(1, int(threads))) as ex:
        fo.write(b"\0" * 0x20 + md)
        pend = []

        def drain(n):
            nonlocal packed
            while len(pend) > n:
                size, fut = pend.pop(0)
                b = fut.result()
                fo.write(b)
                table.append((len(b), size))
                packed += len(b)
        while True:
            if pause:
                pause()
            chunk = fi.read(Z3DS_FRAME)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
            done += len(chunk)
            pend.append((len(chunk), ex.submit(pack, chunk)))
            drain(max(1, int(threads)) * 4)
            if progress and total:
                progress(done / total)
        drain(0)
        seek = b"".join(c.to_bytes(4, "little") + d.to_bytes(4, "little") for c, d in table)
        seek += len(table).to_bytes(4, "little") + b"\0" + (0x8F92EAB1).to_bytes(4, "little")
        seek = (0x184D2A5E).to_bytes(4, "little") + len(seek).to_bytes(4, "little") + seek
        fo.write(seek)
        packed += len(seek)
        fo.seek(0)
        fo.write(b"Z3DS" + magic + bytes([1, 0]) + (0x20).to_bytes(2, "little") + len(md).to_bytes(4, "little")
                 + packed.to_bytes(8, "little") + total.to_bytes(8, "little"))
    if done != total:
        raise Z3dsError("the file changed while it was read")
    return "%08x" % (crc & 0xffffffff)


def z3ds_header(path):
    """{"size": original size, "complete": the file is as long as its header
    says} from a Z3DS file, or None if it isn't one."""
    try:
        with open(path, "rb") as f:
            h = f.read(0x20)
            end = os.fstat(f.fileno()).st_size
    except OSError:
        return None
    if len(h) < 0x20 or h[:4] != b"Z3DS" or h[8] != 1:
        return None
    base = int.from_bytes(h[0x0A:0x0C], "little") + int.from_bytes(h[0x0C:0x10], "little")
    return {"size": int.from_bytes(h[0x18:0x20], "little"),
            "complete": end == base + int.from_bytes(h[0x10:0x18], "little")}


def z3ds_verify(path, pause=None, progress=None):
    """Read a Z3DS file the way Azahar does (header, seek table) and decode
    every frame: (CRC32 hex, size) of the original; Z3dsError if it's broken."""
    import ctypes
    import zlib
    lib = _zstd()
    if lib is None:
        raise Z3dsError("libzstd isn't on this system")
    with open(path, "rb") as f:
        h = f.read(0x20)
        if len(h) < 0x20 or h[:4] != b"Z3DS" or h[8] != 1:
            raise Z3dsError("not a Z3DS file")
        hsize = int.from_bytes(h[0x0A:0x0C], "little")
        base = hsize + int.from_bytes(h[0x0C:0x10], "little")
        packed, total = int.from_bytes(h[0x10:0x18], "little"), int.from_bytes(h[0x18:0x20], "little")
        f.seek(0, 2)
        end = f.tell()
        if end != base + packed:
            raise Z3dsError("size doesn't match its header (cut short?)")
        f.seek(end - 9)
        n, desc, mg = int.from_bytes(f.read(4), "little"), f.read(1)[0], int.from_bytes(f.read(4), "little")
        if mg != 0x8F92EAB1:
            raise Z3dsError("no seek table")
        esz = 12 if desc & 0x80 else 8
        if n * esz + 17 > packed:
            raise Z3dsError("damaged seek table")
        f.seek(end - 9 - n * esz - 8)
        if int.from_bytes(f.read(4), "little") != 0x184D2A5E:
            raise Z3dsError("damaged seek table")
        f.read(4)
        tab = f.read(n * esz)
        sizes = [(int.from_bytes(tab[i * esz:i * esz + 4], "little"),
                  int.from_bytes(tab[i * esz + 4:i * esz + 8], "little")) for i in range(n)]
        # the table must add up before anything is decoded (and before a buffer is sized from it)
        if sum(c for c, _ in sizes) + n * esz + 17 != packed or sum(d for _, d in sizes) != total \
                or any(d > (1 << 30) for _, d in sizes):
            raise Z3dsError("damaged seek table")
        f.seek(base)
        crc, done = 0, 0
        for i, (c, d) in enumerate(sizes):
            if pause:
                pause()
            out = ctypes.create_string_buffer(d)
            r = lib.ZSTD_decompress(out, d, f.read(c), c)
            if lib.ZSTD_isError(r) or r != d:
                raise Z3dsError("frame %d doesn't decode" % i)
            crc = zlib.crc32(out.raw, crc)
            done += d
            if progress and total:
                progress(done / total)
    if done != total:
        raise Z3dsError("decodes to %d bytes, header says %d" % (done, total))
    return "%08x" % (crc & 0xffffffff), done


def zar_enabled(cfg, system):
    return system in _tools.ZAR_SYSTEMS and bool(_config.config_value(cfg, "zar")) and _tools.x360_tools() is not None


def zar_check(path, full=False):
    """None if a .zar looks whole; else what's wrong. Always: the size and
    magic in its footer. full: also the SHA-256 it carries over the whole file
    (zarchive itself never checks it)."""
    import hashlib
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size < 144:
                return "too short to be a .zar"
            f.seek(size - 144)
            foot = f.read(144)
            if int.from_bytes(foot[-4:], "big") != _tools.ZAR_MAGIC or int.from_bytes(foot[-8:-4], "big") != _tools.ZAR_VERSION:
                return "not a .zar (no ZArchive footer)"
            if int.from_bytes(foot[-16:-8], "big") != size:
                return "cut short (%s of %s)" % (_core.human_size(size), _core.human_size(int.from_bytes(foot[-16:-8], "big")))
            if not full:
                return None
            h = hashlib.sha256()
            f.seek(0)
            left = size - 144
            while left:
                chunk = f.read(min(8 << 20, left))
                if not chunk:
                    return "cut short"
                h.update(chunk)
                left -= len(chunk)
            h.update(foot[:96] + b"\0" * 32 + foot[128:])
            if h.digest() != foot[96:128]:
                return "damaged (its SHA-256 doesn't match)"
    except OSError as e:
        return "can't read it (%s)" % e.strerror
    return None


def xiso_listing(exe, iso):
    """{relative path: size} of the files on an Xbox / Xbox 360 disc image
    (extract-xiso -l), or None if it isn't one."""
    try:
        # surrogateescape: names decode the way os.walk sees them on disk, even when not UTF-8
        p = subprocess.run([exe, "-l", iso], capture_output=True, text=True, errors="surrogateescape", timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    out = {}
    for line in p.stdout.splitlines():
        m = re.match(r"^(/.*[^/]) \((\d+) bytes\)\s*$", line)
        if m:
            out[m.group(1).lstrip("/")] = int(m.group(2))
    return out or None


# what a converter's check() raises for a file it can't convert (planner and worker skip it)
CHECK_ERRORS = (Z3dsError, _discs.CsoError, OSError, _ps3.Ps3Error)


# ---------------------------------------------------------- which conversion applies
# One table answers "this file on this system becomes that, if turned on" for
# every place that asks: the download post-processing (Downloader._maybe_chd),
# the download prompt (disc_layout), the library planner (rb_chd_plan) and the
# worker (rb_chd.convert). The converters themselves live in rb_chd.py.

@dataclass(frozen=True, eq=False)
class Converter:
    mode: str                  # the chd-queue task's "mode" (stored on disk: never rename)
    label: str                 # its name on screen
    tag: str                   # prefix of its log lines ("chd: ...")
    systems: frozenset         # the systems it applies to
    targets: dict              # source extension (lower case) -> output extension
    setting: str               # config key that turns it on
    setting_name: str          # that switch's name in Settings, for messages (its Setting's label)
    off_reason: str            # why the worker skips a task while it's off
    enabled: Callable          # (cfg, system) -> turned on and usable on this box
    chd: bool = False          # made by chdman (createcd / createdvd)
    keep_image_ext: bool = False   # the image's own extension stays in the name: "X.iso.squashfs"
    space_factor: int = 1      # room needed per source byte while the output is verified
    space: Optional[Callable] = None   # space(src) -> bytes needed beside it, when the size alone can't tell
    check: Optional[Callable] = None   # check(src) raises one of CHECK_ERRORS when it can't be converted
    renamed_sources: tuple = ()    # found by the planner too: renamed to a source first ("X.xiso" -> "X.iso")
    planner_skips_done: bool = False   # the planner leaves a source alone when its output is already there

    @property
    def sources(self):
        return tuple(self.targets)

    @property
    def outputs(self):
        return tuple(dict.fromkeys(self.targets.values()))

    def output_ext(self, src):
        """The extension a source (path or ".ext") becomes."""
        return self.targets.get(_ext(src), self.outputs[0])

    def output_name(self, stem, src):
        keep = os.path.splitext(src)[1].lower() if self.keep_image_ext else ""
        return stem + keep + self.output_ext(src)

    def output_path(self, src):
        """Where the output of a loose source lands: next to it."""
        stem = os.path.splitext(os.path.basename(src))[0]
        return os.path.join(os.path.dirname(src), self.output_name(stem, src))


def _ext(path):
    """Lower-case extension of a path or of a bare ".ext"."""
    return os.path.splitext("_" + os.path.basename(path))[1].lower()


CONVERTERS = (
    # CD images become createcd on every CHD system (PS2's CD-based games come as cue/bin too)
    Converter("createcd", "CHD", "chd", frozenset(_core.CHD_CD_SYSTEMS | _core.CHD_DVD_SYSTEMS),
              dict.fromkeys((".cue", ".gdi"), ".chd"), "chd", _config.SETTING["chd"].label, "turned off",
              lambda cfg, system: bool(_config.config_value(cfg, "chd")), chd=True),
    # DVD images (.iso) on PS2/PSP only
    Converter("createdvd", "CHD", "chd", frozenset(_core.CHD_DVD_SYSTEMS), {".iso": ".chd"},
              "chd", _config.SETTING["chd"].label, "turned off",
              lambda cfg, system: bool(_config.config_value(cfg, "chd")), chd=True),
    # PSP .cso: decoded to a temporary ISO beside the CHD, then createdvd like an .iso
    # (mode "cso" is stored in queue tasks: never rename it)
    Converter("cso", "CHD", "chd", frozenset({"psp"}), {".cso": ".chd"},
              "chd", _config.SETTING["chd"].label, "turned off",
              lambda cfg, system: bool(_config.config_value(cfg, "chd")), chd=True, space_factor=2,
              check=lambda src: _discs.cso_info(src), space=lambda src: _discs.cso_info(src)["total"] + os.path.getsize(src)),
    Converter("rvz", "RVZ", "rvz", frozenset(_core.RVZ_SYSTEMS), dict.fromkeys(_core.RVZ_SOURCES, ".rvz"),
              "rvz", _config.SETTING["rvz"].label, "turned off, or dolphin-tool isn't installed",
              lambda cfg, system: rvz_enabled(cfg)),
    # Xbox: images are made bootable (make_xiso, .xiso -> .iso) first, when xbox_bootable is on
    Converter("squashfs", "squashfs", "squashfs", frozenset(_core.SQUASH_SYSTEMS), {".iso": ".squashfs"},
              "squashfs", _config.SETTING["squashfs"].label, "turned off, or mksquashfs is missing",
              lambda cfg, system: squashfs_enabled(cfg, system), keep_image_ext=True,
              renamed_sources=(".xiso",)),
    # encrypted dumps are skipped (Azahar can't run them either)
    Converter("z3ds", "ZCCI", "z3ds", frozenset(Z3DS_SYSTEMS), dict(Z3DS_SOURCES),
              "z3ds", _config.SETTING["z3ds"].label, "turned off, or libzstd is missing",
              lambda cfg, system: z3ds_enabled(cfg, system), check=lambda src: z3ds_info(src),
              planner_skips_done=True),
    # the extracted files and the .zar sit next to the image until it's verified
    Converter("zar", "ZAR", "zar", frozenset(_tools.ZAR_SYSTEMS), {".iso": ".zar"},
              "zar", _config.SETTING["zar"].label, "turned off, or the Xbox 360 tools aren't installed",
              lambda cfg, system: zar_enabled(cfg, system), space_factor=2, planner_skips_done=True),
    # PS3: a disc image (loose, or the one .iso in a zip, read without unzipping it) becomes
    # a "Name.ps3" game folder; encrypted discs need Decrypt PS3 discs and the disc's key
    Converter("ps3", "PS3 folder", "ps3", frozenset({"ps3"}), {".iso": ".ps3", ".zip": ".ps3"},
              "ps3_folders", _config.SETTING["ps3_folders"].label, "turned off",
              lambda cfg, system: bool(_config.config_value(cfg, "ps3_folders")),
              check=lambda src: _ps3.ps3_check(src), space=lambda src: _ps3.ps3_image_size(src)),
)
CONVERTER = {c.mode: c for c in CONVERTERS}


def converters_for(system):
    return [c for c in CONVERTERS if system in c.systems]


def conversion_for(system, path, cfg=None, accepts=None, enabled=True, renamed=False):
    """The Converter that turns this file (a path or a bare ".ext") on this
    system into something smaller, or None: nothing applies, it's turned off
    (not checked with enabled=False), or ES wouldn't list the result
    (accepts(system, ext), when given). renamed: also a file the planner
    renames to a source first (Xbox .xiso)."""
    ext = _ext(path)
    for c in CONVERTERS:
        if system in c.systems and (ext in c.targets or (renamed and ext in c.renamed_sources)):
            if enabled and not c.enabled(cfg, system):
                return None
            if accepts is not None and not accepts(system, c.output_ext(ext)):
                return None
            return c
    return None


def conversion_tasks(system, paths, cfg, accepts=None):
    """[(Converter, [paths])]: the files among paths that would be converted,
    grouped by conversion, in table order (CD images before DVD images)."""
    groups = {}
    for p in paths:
        c = conversion_for(system, p, cfg, accepts)
        if c is not None:
            groups.setdefault(c.mode, (c, []))[1].append(p)
    return [groups[c.mode] for c in CONVERTERS if c.mode in groups]


def conversion_output(mode, stem, src):
    """File name a task of this mode makes from src (an unknown mode: chdman's .chd)."""
    c = CONVERTER.get(mode)
    return c.output_name(stem, src) if c else stem + ".chd"


# conversion modes (the queue task's "mode"): their label on screen and what they make
CONVERT_LABELS = {c.mode: c.label for c in CONVERTERS}
CONVERT_OUTPUTS = {c.mode: c.outputs for c in CONVERTERS}


def chd_status(task_name, library=None, rom_id=None, mode=None):
    """Where a download's conversion stands: "queued", "compressing", "done",
    or None (no task / gave up — the original files stay). mode: the task's
    conversion (any when unknown), for recognising its output."""
    if not task_name:
        return None
    try:
        with open(_core.chd_current_path()) as f:
            if json.load(f).get("task") == task_name:
                return "compressing"
    except (OSError, ValueError):
        pass
    if os.path.exists(os.path.join(_core.CHD_QUEUE_DIR, task_name)):
        return "queued"
    if library is not None and rom_id is not None:
        with library.lock:
            library._reload()
            rec = library.installed.get(str(rom_id)) or {}
        outs = CONVERT_OUTPUTS.get(mode) or tuple(e for v in CONVERT_OUTPUTS.values() for e in v)
        if any(p.lower().endswith(outs) and os.path.exists(p) for p in rec.get("paths", [])):
            return "done"
    return None


def conversions_pending():
    """True when the conversion queue holds a task (any mode: the worker
    re-checks per task whether that conversion is still turned on)."""
    try:
        return any(t.endswith(".json") for t in os.listdir(_core.CHD_QUEUE_DIR))
    except OSError:
        return False


def start_conversions(python=None):
    """Start the conversion worker when the queue holds a task."""
    return conversions_pending() and _core.spawn_detached("rb_chd.py", python)


def _converting_now(name):
    try:
        with open(_core.chd_current_path()) as f:
            return json.load(f).get("task") == name
    except (OSError, ValueError, AttributeError):
        return False


def queue_task_first(name):
    """Move a waiting conversion to the front of the queue. False when the
    worker started it meanwhile (renaming it then would run it twice) or it's
    gone (finished, or removed)."""
    if _converting_now(name):
        return False
    try:
        os.replace(os.path.join(_core.CHD_QUEUE_DIR, name),
                   os.path.join(_core.CHD_QUEUE_DIR, "0000000000000-" + name))
        return True
    except FileNotFoundError:
        return False


def drop_queue_task(name):
    """Take a waiting conversion out of the queue (the original stays). False
    when the worker started it meanwhile or it's gone already."""
    if _converting_now(name):
        return False
    try:
        os.remove(os.path.join(_core.CHD_QUEUE_DIR, name))
        return True
    except FileNotFoundError:
        return False


def publish_queue_task(task, name, exclusive=False):
    """Put a conversion task in the queue whole: the worker lists *.json while
    tasks are written, so it's written under a temp name (not .json) and then
    renamed in. exclusive: never replace an existing task of that name (returns
    None then). Returns the task's path."""
    os.makedirs(_core.CHD_QUEUE_DIR, exist_ok=True)
    path = os.path.join(_core.CHD_QUEUE_DIR, name)
    tmp = os.path.join(_core.CHD_QUEUE_DIR, ".%s.%d-%d.tmp" % (name, os.getpid(), threading.get_ident()))
    try:
        with open(tmp, "w") as f:
            json.dump(task, f)
            f.flush()
            os.fsync(f.fileno())
        if exclusive:
            try:
                os.link(tmp, path)               # fails if the name is taken: no clobber
            except FileExistsError:
                return None
        else:
            os.replace(tmp, path)
        return path
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
