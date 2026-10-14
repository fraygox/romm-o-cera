#!/usr/bin/env python3
"""Hand RomM-o-Cera's pending game info to EmulationStation, through ES's own
API, once ES is idle.

Spawned detached by the app on exit (and by the CHD / download workers).
EmulationStation owns its gamelists: it saves them by itself at any time (its
RetroAchievements hasher writes entries for new files in the background), so
editing gamelist.xml behind its back can always be overwritten — that lost
game info after CHD conversions up to v0.6.3. Since v0.6.4 nothing here
writes gamelist.xml. Instead:

  1. wait until ES is idle: no game (and not this app) running, nothing
     scraping, hashing, installing or updating (/isIdle, Batocera 36+);
  2. read, from gamelist.xml (read only), the info of games about to be
     renamed (CHD conversions), so it can be carried to the new file;
  3. let ES list new files: downloads go in with POST /addgames/<system>
     (only those games, in a moment); anything else, or an ES that doesn't
     take them (Batocera 43.1's rejects every body), gets the full
     /reloadgames, which can take minutes on a big library;
  4. per game, POST /systems/<sys>/games/<id> with only the fields that
     should change (ES keeps the rest), and upload covers/manuals/media with
     POST .../media/<type>. ES updates its in-memory list and its own
     crash-safe journal, and writes gamelist.xml itself.

Entries ES can't take yet (a system or file it doesn't list) are kept and
retried; nothing is dropped silently.
"""
import json
import os
import sys
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rommlib  # noqa: E402

MAX_TRIES = 5
BAD_MAX = 1024 * 1024    # <queue>.bad keeps its newest lines up to this size

TEXT_FIELDS = ("name", "desc", "releasedate", "developer", "publisher", "genre", "players", "rating",
               "region", "lang", "family")
USAGE_FIELDS = ("playcount", "lastplayed", "gametime", "favorite")
# gamelist media tags ES accepts uploads for, and the content types it wants
MEDIA_TAGS = ("image", "thumbnail", "marquee", "video", "fanart", "boxback", "bezel", "manual", "map",
              "titleshot", "wheel", "boxart", "cartridge", "magazine")
IMAGE_MAX = 20 << 20     # an image from RomM bigger than this isn't one
VIDEO_MAX = 50 << 20     # a game's video clip: a bigger one is skipped (and logged)
# Settings → Metadata (v0.11): a rommlib still in memory from before an update lacks these
# (tests/test_package.py): then the cap stays VIDEO_MAX and skips aren't noted
_video_max = getattr(rommlib, "es_video_max", None)
_note_skip = getattr(rommlib, "record_media_skip", lambda *a: None)
_forget_skip = getattr(rommlib, "clear_media_skip", lambda *a: None)
_wants_own_image = getattr(rommlib, "wants_own_image", lambda m: False)
_art_files = getattr(rommlib, "es_art_files", lambda *a: [])
_same_file = getattr(rommlib, "same_file", lambda a, b: False)
_es_media_file = getattr(rommlib, "es_media_file", lambda *a: None)
CTYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
          ".mp4": "video/mp4", ".avi": "video/avi", ".mkv": "video/mkv", ".webm": "video/webm",
          ".pdf": "application/pdf", ".cbz": "application/cbz"}


def lock_path():
    return os.path.join(rommlib.DATA_DIR, "finalize.lock")


def deferred_path():
    # entries ES couldn't take this time (game/system not listed yet): retried next run
    return os.path.join(rommlib.DATA_DIR, "gamelist-deferred.jsonl")


# ------------------------------------------------------------------ ES API

def game_running():
    """True when ES is busy: a game runs (this app included), ES is busy
    itself (/isIdle), or ES is unreachable."""
    return rommlib.es_busy(app_busy=True) != ""


def wait_idle(max_wait=6 * 3600):
    t0 = time.time()
    idle_since = None
    while time.time() - t0 < max_wait:
        if game_running():
            idle_since = None
        elif idle_since is None:
            idle_since = time.time()
        elif time.time() - idle_since >= 5:   # ES settled back in the menu
            return True
        time.sleep(2)
    return False


class EsApi:
    """The slice of Batocera ES's HTTP API this uses (Batocera 43)."""

    def __init__(self, base=None):
        self.base = base or rommlib.ES_API
        self._games = {}

    def _req(self, method, path, data=None, ctype=None, timeout=30):
        """(status, body); HTTP errors come back as a status, EsError when ES
        can't be asked."""
        r = rommlib.EsClient(self.base).request(method, path, data, ctype, timeout, errors=False)
        return r.status, r.body

    def games(self, system, refresh=False):
        """{absolute path: game dict} for a system ES lists, or None."""
        if refresh or system not in self._games:
            st, body = self._req("GET", "/systems/%s/games" % urllib.parse.quote(system))
            if st != 200:
                return None
            try:
                self._games[system] = {g["path"]: g for g in json.loads(body)}
            except (ValueError, KeyError, TypeError):
                return None
        return self._games[system]

    def game(self, system, gid):
        st, body = self._req("GET", "/systems/%s/games/%s" % (urllib.parse.quote(system), gid))
        return json.loads(body) if st == 200 else None

    @staticmethod
    def same(a, b):
        """ES reformats numbers ("0.80" reads back "0.800000")."""
        a, b = str(a if a is not None else ""), str(b if b is not None else "")
        if a == b:
            return True
        try:
            return abs(float(a) - float(b)) < 1e-6
        except ValueError:
            return a.strip() == b.strip()

    def set_meta(self, system, gid, fields):
        """Only the given fields change (ES keeps the rest). 404 also means
        "nothing changed" in ES's API, so the result is read back."""
        if not fields:
            return True
        body = json.dumps({k: str(v) for k, v in fields.items()}).encode("utf-8")
        self._req("POST", "/systems/%s/games/%s" % (urllib.parse.quote(system), gid), body, "application/json")
        g = self.game(system, gid)
        return g is not None and all(self.same(g.get(k, ""), v) for k, v in fields.items())

    def set_media(self, system, gid, mtype, data, ctype):
        st, _ = self._req("POST", "/systems/%s/games/%s/media/%s" % (urllib.parse.quote(system), gid, mtype),
                          data, ctype, timeout=120)
        return st == 200

    def media_file(self, system, gid, tag):
        """A game's media file as ES serves it (GET .../media/<tag>): bytes, or None."""
        try:
            st, body = self._req("GET", "/systems/%s/games/%s/media/%s" % (urllib.parse.quote(system), gid, tag))
        except Exception:                      # ES can't be asked
            return None
        return body if st == 200 else None

    def media_present(self, system, gid, tag):
        """ES has the file of this media tag (only the headers of GET .../media/<tag> are
        read): False for a path to a file that is gone, which ES answers with nothing;
        None when ES can't be asked."""
        import urllib.error
        import urllib.request
        url = "%s/systems/%s/games/%s/media/%s" % (self.base, urllib.parse.quote(system), gid, tag)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return int(r.headers.get("Content-Length") or 0) > 0
        except urllib.error.HTTPError as e:
            e.close()
            return False if e.code == 404 else None
        except Exception:
            return None

    def add_games(self, system, games):
        """List games ({absolute path: {tag: text}}) with POST /addgames/<system>:
        the status (see rommlib.es_add_games), or None when ES can't be asked.
        ValueError/UnicodeError: a name XML can't carry."""
        return rommlib.es_add_games(system, games)

    def reload(self):
        rommlib.EsClient(self.base).get("/reloadgames", timeout=120)


# ------------------------------------------------------------------ helpers

def rel(sysdir, path):
    return "./" + os.path.relpath(path, sysdir)


def blank_name(game):
    """ES names a game it has no info for after its file."""
    stem = os.path.splitext(os.path.basename(game.get("path", "")))[0]
    return not game.get("name") or game.get("name") == stem


def has(game, k):
    if k == "name":
        return not blank_name(game)
    v = str(game.get(k) or "").strip()
    return bool(v) and v not in ("0", "0.000000", "not-a-date-time", "false")


def fetch_bytes(api, url_path, limit=200 << 20, info=None):
    """A RomM asset by server-relative path (token session), or None. A file
    over limit is None too, and sets info["too_big"] (its size when RomM says)."""
    if api is None or not url_path or not url_path.startswith("/") or url_path.startswith("//"):
        return None
    try:
        r = api.s.get(api.base + url_path, timeout=60, stream=True)
    except Exception:
        return None
    try:
        size = int(r.headers.get("Content-Length") or -1) if r.status_code == 200 else -1
        if r.status_code != 200 or size > limit:
            n = 0
            for c in r.iter_content(1 << 14):     # a small error page: read it so the
                n += len(c)                       # connection is kept, not closed
                if n > 1 << 16:
                    break
            if size > limit and info is not None:
                info["too_big"] = size
            return None
        data = r.raw.read(limit + 1, decode_content=True)
        if data and len(data) > limit and info is not None:
            info["too_big"] = len(data)
        return data if data and len(data) <= limit else None
    except Exception:
        return None
    finally:
        r.close()          # the connection goes back to the pool


def image_kind(data):
    """The content type ES takes for these image bytes, or None (WebP, not an image...)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def fetch_media(api, tag, path, video_max=None, info=None):
    """(bytes, content type) of one of RomM's media files as ES takes it, or None:
    images as PNG/JPEG/GIF (anything else converted to PNG), video as MP4 up to
    video_max (VIDEO_MAX by default; a bigger one is logged and skipped, its size
    in info["too_big"]), the manual a PDF."""
    if tag == "video":
        info = {} if info is None else info
        cap = VIDEO_MAX if video_max is None else video_max
        data = fetch_bytes(api, path, cap, info)
        if info.get("too_big"):
            rommlib.log("finalize: video skipped, %d MB is over %d MB: %s" % (
                info["too_big"] >> 20, cap >> 20, path))
        return (data, "video/mp4") if data and data[4:8] == b"ftyp" else None
    if tag == "manual":
        data = fetch_bytes(api, path)
        return (data, "application/pdf") if data and data.startswith(b"%PDF") else None
    data = fetch_bytes(api, path, IMAGE_MAX)
    if not data:
        return None
    kind = image_kind(data)
    if kind:
        return data, kind
    png = as_png(data)
    return (png, "image/png") if png else None


def image_is_box_art_again(esapi, system, g, media):
    """The game's image in ES is byte for byte its thumbnail while the entry offers an image
    of its own (a screenshot): what versions before 0.11 left after a Fill in game info,
    the cover twice. That image is replaced; any other never is."""
    if not g.get("image") or not _wants_own_image(media) or not hasattr(esapi, "media_file"):
        return False
    img = esapi.media_file(system, g["id"], "image")
    return img is not None and img == esapi.media_file(system, g["id"], "thumbnail")


def drop_stale_images(system, g, keep):
    """After the image was replaced under another extension, ES's old file is left in
    images/: removed when it is the thumbnail's copy (and not the new image)."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    thumbs = _art_files(sysdir, g["path"], "thumb")
    for f in _art_files(sysdir, g["path"], "image"):
        try:
            with open(f, "rb") as fh:
                data = fh.read()
            if data != keep and any(_same_file(f, t) for t in thumbs):
                os.remove(f)
        except OSError:
            pass


def upload_media(api, esapi, system, g, e, cfg=None):
    """Every media file the entry offers that the game has none of in ES yet (a
    path to a file that is gone counts as none; what's there is never replaced,
    but for an image that is only the box art again: image_is_box_art_again):
    the first of its RomM paths that comes.
    A tag none of them comes for is noted (library.record_media_skip), so Fill
    in game info stops asking for it; videos follow today's cap (cfg)."""
    media = entry_media(e)
    if e.get("image_local") and os.path.isfile(e["image_local"]):
        media.pop("image", None)
        if not g.get("image"):
            with open(e["image_local"], "rb") as f:
                esapi.set_media(system, g["id"], "image", f.read(), "image/png")
    if e.get("manual"):
        media["manual"] = [e["manual"]]
    video_max = _video_max(cfg) if cfg is not None and _video_max else None
    if video_max == 0:
        media.pop("video", None)
    # the thumbnail first, so an image that is only the box art again shows as such
    for tag, paths in sorted(media.items(), key=lambda kv: kv[0] != "thumbnail"):
        if tag not in MEDIA_TAGS:
            continue
        again = False
        if g.get(tag) and getattr(esapi, "media_present", lambda *a: None)(system, g["id"], tag) is not False:
            again = tag == "image" and image_is_box_art_again(esapi, system, g, media)
            if not again:
                continue
        info, came = {}, False
        for p in paths:
            got = fetch_media(api, tag, p, video_max, info)
            came = came or bool(got)
            if got and esapi.set_media(system, g["id"], tag, got[0], got[1]):
                _forget_skip(system, e.get("rom_id"), tag)
                if again:
                    drop_stale_images(system, g, got[0])
                break
        else:
            if not came:
                _note_skip(system, e.get("rom_id"), tag, paths[0], info.get("too_big"))


def as_png(data):
    """Covers can be WebP: ES takes png/jpg/gif."""
    try:
        os.makedirs(rommlib.DATA_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".png", dir=rommlib.DATA_DIR)
        os.close(fd)
    except OSError:
        return None
    try:
        rommlib.write_png(data, tmp)
        with open(tmp, "rb") as f:
            return f.read()
    except Exception:
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def entry_fields(e):
    """A gamelist entry's text as ES's fields, in ES's formats; empty ones left out."""
    out = {"name": e.get("name"), "desc": e.get("desc"),
           "genre": ", ".join(e.get("genres") or []) or None,
           "developer": e.get("developer"), "publisher": e.get("publisher"),
           "players": e.get("players"), "region": e.get("region"), "lang": e.get("lang"),
           "family": e.get("family")}
    ms = e.get("releasedate")
    if ms:
        out["releasedate"] = time.strftime("%Y%m%dT000000", time.gmtime(ms / 1000))
    if e.get("rating") is not None:
        out["rating"] = "%.2f" % (float(e["rating"]) / 100)
    return {k: v for k, v in out.items() if v not in (None, "")}


def entry_media(e):
    """{ES media tag: RomM server paths, best first} a gamelist entry offers
    (library.es_media). Entries queued by older versions carry only "cover":
    that is the image."""
    m = e.get("media")
    if isinstance(m, dict):
        return {k: [p for p in v if isinstance(p, str)] for k, v in m.items() if isinstance(v, list) and v}
    return {"image": [e["cover"]]} if e.get("cover") else {}


def node_info(sysdir, g):
    """A gamelist.xml <game> as {text, usage, hidden, media{tag: abs path}}."""
    info = {"text": {}, "usage": {}, "hidden": g.findtext("hidden") == "true", "media": {}}
    for k in TEXT_FIELDS:
        v = (g.findtext(k) or "").strip()
        if v:
            info["text"][k] = v
    for k in USAGE_FIELDS:
        v = (g.findtext(k) or "").strip()
        if v:
            info["usage"][k] = v
    for k in MEDIA_TAGS:
        v = (g.findtext(k) or "").strip()
        if v:
            p = os.path.normpath(os.path.join(sysdir, v))
            if os.path.isfile(p):
                info["media"][k] = p
    return info


def richness(info):
    return len(info["text"]) + len(info["media"]) + len(info["usage"])


def api_info(g, sysdir=None):
    """An ES API game dict as node_info() shape. ES gives its media as URLs: with sysdir,
    the files ES names for them (images/<stem>-image.png...) that are there."""
    info = {"text": {}, "usage": {}, "hidden": str(g.get("hidden")) == "true", "media": {}}
    for k in MEDIA_TAGS if sysdir else ():
        f = _es_media_file(sysdir, g.get("path") or "", k) if g.get(k) else None
        if f:
            info["media"][k] = f
    for k in TEXT_FIELDS:
        if has(g, k):
            info["text"][k] = str(g[k])
    for k in USAGE_FIELDS:
        if has(g, k):
            info["usage"][k] = str(g[k])
    return info


def snapshot_renames(entries, esapi=None):
    """Before ES reloads: the info of every path a CHD conversion is renaming,
    keyed by the new path — from gamelist.xml (with media) and from ES's
    memory (fresher: may not be saved yet; its media by the files ES names),
    richest wins; a deferred entry brings the snapshot it took the first time."""
    carry, cache, live = {}, {}, {}
    for e in entries:
        if "remove" in e:
            gone = [r for r in e["remove"] if not os.path.exists(r)]   # reinstalled since: keep its art
            carry[("media-of-removed", id(e))] = removed_media(
                os.path.join(rommlib.ROMS_ROOT, e["system"]), gone) if gone else []
            continue
        if "rename" not in e:
            continue
        for new, info in (e.get("_carry") or {}).items():
            carry[new] = info
        sysdir = os.path.join(rommlib.ROMS_ROOT, e["system"])
        if esapi is not None and e["system"] not in live:
            live[e["system"]] = esapi.games(e["system"], refresh=True) or {}
        if sysdir not in cache:
            try:
                cache[sysdir] = ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot().findall("game")
            except (OSError, ET.ParseError):
                cache[sysdir] = []
        for old, new in e["rename"].items():
            old_rel = rel(sysdir, old)
            for g in cache[sysdir]:
                p = g.findtext("path") or ""
                if p == old_rel or p.startswith(old_rel.rstrip("/") + "/"):
                    info = node_info(sysdir, g)
                    if richness(info) and (new not in carry or richness(info) > richness(carry[new])):
                        carry[new] = info
            for path, g in (live.get(e["system"]) or {}).items():
                if path == old or path.startswith(old.rstrip("/") + "/"):
                    info = api_info(g, sysdir)       # media ES has only in its journal too
                    base = carry.get(new)
                    if base is None:
                        if richness(info):
                            carry[new] = info
                    else:                    # ES's memory wins for text/usage, the file keeps media
                        base["text"].update(info["text"])
                        base["usage"].update(info["usage"])
                        base["hidden"] = base["hidden"] or info["hidden"]
                        for k, f in info["media"].items():
                            base["media"].setdefault(k, f)
    return carry


def merge_usage(game, usage):
    """Never lose play history: the higher count/time, the later date."""
    out = {}
    for k in ("playcount", "gametime"):
        try:
            old, cur = int(float(usage.get(k) or 0)), int(float(game.get(k) or 0))
        except ValueError:
            continue
        if old > cur:
            out[k] = str(old)
    lp = usage.get("lastplayed") or ""
    if lp and lp > str(game.get("lastplayed") or "") and lp[:1].isdigit():
        out["lastplayed"] = lp
    if usage.get("favorite") == "true" and str(game.get("favorite")) != "true":
        out["favorite"] = "true"
    return out


def removed_media(sysdir, removed):
    """Media files of uninstalled games, from gamelist.xml (read only, before
    ES reloads): those inside the system's media folders that no other entry
    uses. The entries themselves are left to ES (it ignores missing files)."""
    try:
        nodes = ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot().findall("game")
    except (OSError, ET.ParseError):
        return []
    rels = [rel(sysdir, r) for r in removed]
    gone, keep = set(), set()
    for g in nodes:
        p = g.findtext("path") or ""
        dead = any(p == r or p.startswith(r.rstrip("/") + "/") for r in rels)
        for k in MEDIA_TAGS:
            v = (g.findtext(k) or "").strip()
            if v:
                (gone if dead else keep).add(os.path.normpath(os.path.join(sysdir, v)))
    return sorted(f for f in gone - keep
                  if any(f.startswith(os.path.join(sysdir, d) + os.sep) for d in rommlib.GAMELIST_MEDIA_DIRS))


def upload_file(esapi, system, gid, tag, path):
    ctype = CTYPES.get(os.path.splitext(path)[1].lower())
    if not ctype:
        return False
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return False
    return esapi.set_media(system, gid, tag, data, ctype)


# ------------------------------------------------------------------ apply

def apply(entries, api, es, esapi, carry, cfg=None):
    """Hand entries to ES. Returns the entries ES couldn't take yet."""
    left = []
    games_of = {}

    def find(system, path):
        if system not in games_of:
            games_of[system] = esapi.games(system, refresh=True)
        g = (games_of[system] or {}).get(path)
        return g

    for e in entries:
        system = e["system"]
        try:
            if "remove" in e:
                for f in carry.get(("media-of-removed", id(e)), []):   # ES itself drops the games
                    try:
                        os.remove(f)
                    except OSError:
                        pass
                continue
            if "rename" in e or "hide" in e:
                targets = list(e.get("rename", {}).values()) if "rename" in e else []
                ok = True
                for new in targets:
                    g = find(system, rommlib.primary_path(system, [new], es) or new)
                    if g is None:
                        ok = ok and not os.path.exists(new)    # gone again: nothing to carry
                        continue
                    info = carry.get(new)
                    if info:
                        fields = {k: v for k, v in info["text"].items() if not has(g, k)}
                        fields.update(merge_usage(g, info["usage"]))
                        if info["hidden"] and not new.lower().endswith(".m3u"):
                            fields["hidden"] = "true"      # a disc under a playlist stays hidden; the playlist never
                        ok = esapi.set_meta(system, g["id"], fields) and ok
                        for tag, p in info["media"].items():
                            if not g.get(tag):
                                upload_file(esapi, system, g["id"], tag, p)
                for h in e.get("hide", []):
                    g = find(system, h)
                    if g is None:
                        ok = ok and not os.path.exists(h)
                        continue
                    if str(g.get("hidden")) != "true":
                        ok = esapi.set_meta(system, g["id"], {"hidden": "true"}) and ok
                if not ok:
                    left.append(e)
                continue
            paths = e.get("paths") or []
            prim = (paths[0] if len(paths) == 1 and paths[0].lower().endswith(".m3u")
                    else rommlib.primary_path(system, paths, es))
            if not prim:
                continue                            # nothing ES would list (files gone)
            g = find(system, prim)
            if g is None:
                left.append(e)
                continue
            want = entry_fields(e)
            if e.get("fill"):
                want = {k: v for k, v in want.items() if not has(g, k)}
            else:
                want = {k: v for k, v in want.items() if not EsApi.same(g.get(k, ""), v)}
            if not esapi.set_meta(system, g["id"], want):
                left.append(e)
                continue
            upload_media(api, esapi, system, g, e, cfg)
        except Exception as ex:                    # one bad entry never sinks the batch
            rommlib.log("finalize: entry for %s failed (%r)" % (system, ex))
            left.append(e)
    return left


def wanted(entries, es):
    """{system: primary paths ES must list} for the entries' files on disk."""
    want = {}
    for e in entries:
        ps = list((e.get("rename") or {}).values()) or e.get("paths") or []
        for p in ps:
            if os.path.exists(p):
                prim = p if p.lower().endswith(".m3u") else rommlib.primary_path(e["system"], [p], es)
                if prim:
                    want.setdefault(e["system"], set()).add(prim)
    return want


def add_games(esapi, entries, es):
    """List a batch's new files in ES without a full reload: per system, POST
    /addgames/<system> with just the games ES doesn't list yet (an entry for
    a listed game would reset what ES keeps beyond the tags we send). True
    when ES lists them all now (or nothing was missing), False when the
    batch needs /reloadgames: it renames or removes games (ES must also
    forget the old files), or ES didn't take them — Batocera 43.1's ES
    answers 204 to every /addgames (fixed upstream 2026-07), an older one
    may not know the file, the address may be down."""
    if any("rename" in e or "remove" in e for e in entries):
        return False
    want, names, hides = wanted(entries, es), {}, {}
    for e in entries:
        for p in e.get("paths") or []:
            if e.get("name"):
                names.setdefault(p, e["name"])
        for h in e.get("hide") or []:               # a set's discs: listed (hidden) with its playlist
            if os.path.exists(h):
                hides.setdefault(e["system"], set()).add(h)
                want.setdefault(e["system"], set())
    reloading = False
    for system, paths in want.items():
        listed = esapi.games(system, refresh=True) or {}
        new = {p: ({"name": names[p]} if names.get(p) else {}) for p in sorted(paths) if p not in listed}
        new.update({h: {"hidden": "true"} for h in sorted(hides.get(system, ())) if h not in listed and h not in new})
        if not new:
            continue
        try:
            st = esapi.add_games(system, new)
        except (ValueError, UnicodeError):          # a name XML can't carry: reload instead
            return False
        if st == 201:                               # a system ES hadn't loaded: it reloads by itself
            rommlib.log("finalize: %d game(s) added to ES (%s, new system; ES reloads)" % (len(new), system))
            reloading = True
            break                                   # the reload lists the rest too
        listed = (esapi.games(system, refresh=True) or {}) if st == 200 else {}
        if st != 200 or any(p not in listed for p in paths):
            rommlib.log("finalize: ES didn't take the new games (%s: %s); reloading all" % (
                system, "HTTP %d" % st if st else "no answer"))
            return False
        rommlib.log("finalize: %d game(s) added to ES (%s)" % (len(new), system))
    if reloading:
        wait_listed(esapi, entries, es)
    return True


def wait_listed(esapi, entries, es, timeout=180):
    """After /reloadgames: wait until ES lists the systems (and new files) we
    need — the reload runs on ES's UI thread after the request returns."""
    want = wanted(entries, es)
    time.sleep(6)
    t0 = time.time()
    while time.time() - t0 < timeout:
        missing = 0
        for system, paths in want.items():
            gs = esapi.games(system, refresh=True)
            missing += len(paths) if gs is None else sum(1 for p in paths if p not in gs)
        if not missing:
            return True
        time.sleep(3)
    return False


# ------------------------------------------------------------------ queue

def valid(e):
    return isinstance(e, dict) and isinstance(e.get("system"), str) and e["system"] and \
        any(k in e for k in ("paths", "hide", "rename", "remove"))


def _read_jsonl(path):
    """Entries, one per line. A torn or malformed line (power cut mid-append,
    an entry that isn't one) is set aside in <file>.bad, never the batch."""
    out, bad = [], []
    try:
        with open(path, errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    bad.append(line)
                    continue
                (out if valid(e) else bad).append(e if valid(e) else line)
    except OSError:
        return []
    if bad:
        text = "".join(line if line.endswith("\n") else line + "\n" for line in bad)
        try:
            with open(path + ".bad", errors="replace") as f:
                text = f.read() + text
        except OSError:
            pass
        if len(text) > BAD_MAX:                  # keep the newest whole lines
            text = text[text.find("\n", len(text) - BAD_MAX - 1) + 1:]
        rommlib.atomic_write_text(path + ".bad", text)
        rommlib.log("finalize: set aside %d unreadable queue line(s) in %s.bad" % (len(bad), os.path.basename(path)))
    return out


def _write_jsonl(path, entries):
    if not entries:
        if os.path.exists(path):
            os.remove(path)
        return
    rommlib.atomic_write_text(path, "".join(json.dumps(e) + "\n" for e in entries))


def route_renames(entries):
    """A CHD conversion may finish before a download's or a set's entries are
    handed over: point their paths (and a set's discs to hide) at the new
    files, using the batch's renames (a file, or anything inside a folder)."""
    renames = {}
    for e in entries:
        renames.update(e.get("rename") or {})
    if not renames:
        return entries

    def route(p):
        if p in renames:
            return renames[p]
        for old, new in renames.items():
            if p.startswith(old.rstrip("/") + "/"):
                return new
        return p
    for e in entries:
        if "paths" in e:
            e["paths"] = list(dict.fromkeys(route(p) for p in e["paths"]))
        if "hide" in e and "rename" not in e:
            e["hide"] = list(dict.fromkeys(route(p) for p in e["hide"]))
    return entries


def process(entries, api=None, es=None, esapi=None, cfg=None):
    """One batch: route renames, snapshot old info, let ES list the new
    files (/addgames, else a full reload), apply. Returns the entries ES
    couldn't take yet."""
    es = es if es is not None else rommlib.load_es_systems()
    esapi = esapi or EsApi()
    route_renames(entries)
    carry = snapshot_renames(entries, esapi)
    if not add_games(esapi, entries, es):
        esapi.reload()
        wait_listed(esapi, entries, es)
    left = apply(entries, api, es, esapi, carry, cfg)
    for e in left:                           # keep what we knew about renamed games
        if "rename" in e:
            e["_carry"] = {new: carry[new] for new in e["rename"].values() if new in carry}
    return left


def main():
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with rommlib.try_lock(lock_path()) as fd:
        if fd is None:
            return  # another finalize is already waiting; it will pick ours up
        drain()


def drain():
    """Hand ES every queued entry (under the lock), waiting for it to go idle first."""
    # entries ES couldn't take last time: try them again, once per run
    deferred = _read_jsonl(deferred_path())
    if deferred:
        rommlib.append_gamelist(*deferred)      # queue again first, then forget (entries are idempotent)
        _write_jsonl(deferred_path(), [])
    work = rommlib.GAMELIST_QUEUE + ".work"
    failures = 0
    while True:
        queued = os.path.exists(rommlib.GAMELIST_QUEUE) and os.path.getsize(rommlib.GAMELIST_QUEUE) > 0
        if not queued and not os.path.exists(work):
            return
        if not wait_idle():
            rommlib.log("finalize: gave up waiting for ES to go idle")
            return
        # claim the queue (under the writers' lock); a .work left by an
        # interrupted run goes in front of anything newer
        with rommlib.queue_lock():
            old = ""
            if os.path.exists(work):
                with open(work, errors="replace") as f:
                    old = f.read()
            new = ""
            if os.path.exists(rommlib.GAMELIST_QUEUE):
                with open(rommlib.GAMELIST_QUEUE, errors="replace") as f:
                    new = f.read()
            if old and not old.endswith("\n"):
                old += "\n"
            # .work must be durable before the queue goes: a failed write or a
            # kill in between then leaves duplicates at worst (idempotent), never nothing
            rommlib.atomic_write_text(work, old + new)
            if new:
                os.remove(rommlib.GAMELIST_QUEUE)
        entries = _read_jsonl(work)
        cfg = rommlib.load_config()
        api = rommlib.Api.from_config(cfg)
        try:
            if game_running():      # re-check right before reloading ES
                raise RuntimeError("game started")
            left = process(entries, api, cfg=cfg)
            keep = []
            for e in left:
                e["_tries"] = e.get("_tries", 0) + 1
                if e["_tries"] < MAX_TRIES:
                    keep.append(e)
                else:
                    rommlib.log("finalize: giving up on an entry for %s after %d tries: %s" % (
                        e.get("system"), MAX_TRIES, (e.get("paths") or list((e.get("rename") or {}).values()) or ["?"])[0]))
            _write_jsonl(deferred_path(), _read_jsonl(deferred_path()) + keep)
            os.remove(work)
            rommlib.log("finalize: %d entries to ES, %d kept for later" % (len(entries) - len(left), len(keep)))
        except Exception as e:
            # keep the batch (.work stays, in front of anything new) and retry later
            failures += 1
            rommlib.log("finalize: deferred (%r)" % e)
            if failures >= MAX_TRIES:
                rommlib.log("finalize: stopping after %d failed passes; the batch waits for the next run" % failures)
                return
            time.sleep(30)


if __name__ == "__main__":
    main()
