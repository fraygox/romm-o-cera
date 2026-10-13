"""The RomM API client, and pairing a box with a code."""
import os
import queue
import re
import threading
import urllib.parse
from concurrent.futures import Future

import requests

from . import core as _core, config as _config, covers as _covers, net as _net


class Api:
    def __init__(self, url, token, verify_tls=True):
        self.base = (url or "").strip().rstrip("/")
        # covers, parallel lookups and downloads share the session: keep enough
        # connections open that none is thrown away and set up again (requests
        # keeps 10), and back off together when RomM is busy (see net.py)
        self.s = _net.http_session(HTTP_POOL)
        self.s.verify = _config.norm_verify(verify_tls)
        self.s.headers.update({"Authorization": "Bearer " + (token or "").strip(),
                               "User-Agent": _core.UA})

    @classmethod
    def from_config(cls, cfg):
        return cls(_config.config_value(cfg, "url"), _config.config_value(cfg, "token"), _config.config_value(cfg, "verify_tls"))

    def get(self, path, params=None, timeout=20):
        if not self.base:
            raise _core.RommError("No RomM server set — open Settings")
        if _config.url_has_userinfo(self.base):
            raise _core.RommError("Remove user:password@ from the server URL — the token is the login")
        try:
            r = self.s.get(self.base + path, params=params, timeout=timeout)
        except requests.exceptions.SSLError:
            raise _core.RommError("TLS certificate rejected — set verify_tls in Settings") from None
        except requests.RequestException as e:
            raise _core.RommError("Can't reach RomM (%s)" % e.__class__.__name__) from None
        except Exception as e:      # e.g. ValueError from a bad header: its text can hold the token
            raise _core.RommError("Request failed (%s)" % e.__class__.__name__) from None
        if r.status_code in (401, 403):
            raise _core.RommError("RomM rejected the token (HTTP %d)" % r.status_code)
        if r.status_code != 200:
            raise _core.RommError("RomM error HTTP %d on %s" % (r.status_code, path))
        try:
            return r.json()
        except ValueError:
            raise _core.RommError("Not a RomM API response — check the URL (auth proxy in front?)")

    def platforms(self):
        return self.get("/api/platforms")

    def collections(self):
        return self.get("/api/collections")

    def smart_collections(self):
        return self.get("/api/collections/smart")

    def virtual_collections(self, vtype):
        return self.get("/api/collections/virtual", {"type": vtype})

    def roms(self, platform_ids=None, search=None, collection=None,
             offset=0, limit=60, order_by="name", char_index=False, regions=None,
             verified=None, exclude_tags=None, player_counts=None):
        """collection: (param, id) with param one of collection_id,
        smart_collection_id, virtual_collection_id. player_counts: RomM's own
        player count values to keep (party_values() picks them)."""
        params = {"offset": offset, "limit": limit,
                  "with_char_index": "true" if char_index else "false",
                  "with_filter_values": "false", "with_rom_id_index": "false",
                  "order_by": order_by, "order_dir": "asc"}
        if platform_ids:
            params["platform_ids"] = list(platform_ids)
        if search:
            params["search_term"] = search
            params["order_by"] = ""   # relevance order
        if collection:
            params[collection[0]] = collection[1]
        if regions:
            params["regions"] = list(regions)
            params["regions_logic"] = "any"
        if verified:
            params["verified"] = "true"
        if exclude_tags:
            params["tags"] = list(exclude_tags)
            params["tags_logic"] = "none"
        if player_counts:
            params["player_counts"] = list(player_counts)
            params["player_counts_logic"] = "any"
        return self.get("/api/roms", params)

    def rom_filters(self):
        """The values RomM's filters take across the library, e.g.
        {"player_counts": ["1", "1-2", "1-4", "2"], "genres": [...], ...}.
        A RomM without /api/roms/filters: the same lists from a one-game
        listing's filter_values."""
        try:
            got = self.get("/api/roms/filters")
        except _core.RommError as e:
            if "HTTP 404" not in str(e) and "HTTP 422" not in str(e):   # 422: an older RomM read it as a rom id
                raise
            got = self.get("/api/roms", {"limit": 1, "with_filter_values": "true",
                                         "with_char_index": "false", "with_rom_id_index": "false"})
            got = got.get("filter_values") if isinstance(got, dict) else None
        return got if isinstance(got, dict) else {}

    def by_hash(self, **hashes):
        """RomM's game with this hash (crc_hash / md5_hash / sha1_hash / ra_hash), or None."""
        params = {k: v for k, v in hashes.items() if v}
        if not params:
            return None
        try:
            r = self.get("/api/roms/by-hash", params)
        except _core.RommError as e:
            if "HTTP 404" in str(e) or "HTTP 422" in str(e):
                return None
            raise
        if isinstance(r, list):
            r = r[0] if r else None
        return r if isinstance(r, dict) and r.get("id") else None

    def rom(self, rom_id):
        return self.get("/api/roms/%d" % rom_id)

    def download_firmware(self, fw_id, name, part, cap, what, too_big):
        """Stream a RomM firmware file to `part`: capped at `cap` bytes, never
        whole in memory, and on disk (fsync) before it returns, so a power cut
        can't leave an empty file once the caller renames it in. Raises
        RommError ("<what> download failed …", or too_big past the cap) with
        `part` removed."""
        try:
            r = self.s.get("%s/api/firmware/%d/content/%s" % (self.base, fw_id, urllib.parse.quote(name)),
                           timeout=60, stream=True)
        except Exception as e:
            raise _core.RommError("%s download failed (%s)" % (what, e.__class__.__name__)) from None
        try:
            if r.status_code != 200:
                _net.discard(r)
                raise _core.RommError("%s download failed: HTTP %d" % (what, r.status_code))
            n = 0
            with open(part, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    n += len(chunk)
                    if n > cap:
                        raise _core.RommError(too_big)
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
        except (_core.RommError, requests.RequestException, OSError) as e:
            try:
                os.remove(part)
            except OSError:
                pass
            if isinstance(e, _core.RommError):
                raise
            raise _core.RommError("%s download failed (%s)" % (what, e.__class__.__name__)) from None
        finally:
            r.close()

    def roms_all(self, limit=500, cap=None, cancel=None, keep=None, **kw):
        """Every page of a roms() listing, in order: the first page gives the
        total, the rest are fetched PAGE_WORKERS at a time. cap: stop at that
        many (rounded up to a page). keep(rom): only the games it's True for
        are kept, each page filtered as it arrives (a big platform's full
        listing is hundreds of MB once parsed). None if cancel was set."""
        def page_at(off):
            page = self.roms(offset=off, limit=limit, **kw)
            got = page.get("items") or []
            return len(got), page.get("total") or 0, [r for r in got if keep(r)] if keep else got
        step, total, items = page_at(0)
        if not step:
            return items
        seen = step
        limit_to = total if cap is None else min(total, cap)
        offsets = list(range(step, limit_to, step))
        pages = fetch_many(page_at, offsets, PAGE_WORKERS, cancel)
        if cancel is not None and cancel.is_set():
            return None
        for off in offsets:
            n, total, got = fetched(pages.pop(off))
            items.extend(got)
            seen += n
            if n < step:                              # the library shrank meanwhile: that's all
                return items
        # it grew meanwhile: the rest one page at a time, as before
        while seen < (total if cap is None else min(total, cap)):
            if cancel is not None and cancel.is_set():
                return None
            n, total, got = page_at(seen)
            if not n:
                break
            items.extend(got)
            seen += n
        return items

    def similar(self, rom_id, limit=30):
        """RomM's similar games, as plain rom dicts (best match first)."""
        out = self.get("/api/roms/%d/similar" % rom_id, {"limit": limit})
        return [x["rom"] for x in out if isinstance(x, dict) and x.get("rom")]

    def all_tags(self):
        """Every filename tag in the library (for the "hide prototypes…" filter)."""
        page = self.get("/api/roms", {"limit": 1, "with_filter_values": "true",
                                      "with_char_index": "false", "with_rom_id_index": "false"})
        return (page.get("filter_values") or {}).get("tags") or []

    def firmware(self, platform_id):
        return self.get("/api/firmware", {"platform_id": platform_id})

    def fetch_cover(self, rom, large=False):
        """Cached cover for a rom: its path, or None when it has none (no
        cover, RomM has no file for it, or what came back isn't usable).
        Raises CoverError when it couldn't be fetched just now (RomM busy,
        unreachable or failing): asking again later may work. A rejected
        token or certificate is None: asking again wouldn't help. Blocking. large=True for
        high-DPI screens (the small cover is ~300 px tall)."""
        keys = ("path_cover_large", "path_cover_small") if large else \
            ("path_cover_small", "path_cover_large")
        path = rom.get(keys[0]) or rom.get(keys[1])
        if not path or not path.startswith("/") or path.startswith("//"):
            return None             # only server-relative paths: the session carries the token
        os.makedirs(_core.COVER_CACHE, exist_ok=True)
        dest = os.path.join(_core.COVER_CACHE, "%d%s.img" % (rom["id"], "-L" if large else ""))
        if os.path.exists(dest):
            _covers.touch_cached(dest)
            return dest
        try:
            r = self.s.get(self.base + path, timeout=15, stream=True)
        except requests.exceptions.SSLError:
            return None                 # won't change by asking again (Settings says why)
        except requests.RequestException as e:
            raise CoverError("cover: %s" % e.__class__.__name__) from None
        try:
            if r.status_code in (400, 401, 403, 404, 410, 422):
                _net.discard(r)
                return None                                   # no such file, or not for this token
            if r.status_code != 200:
                _net.discard(r)
                raise CoverError("cover: HTTP %d" % r.status_code)
            try:
                data = _read_capped(r, COVER_MAX)
            except requests.RequestException as e:
                raise CoverError("cover: %s" % e.__class__.__name__) from None
            except _core.RommError:
                return None                                   # larger than any cover
            if not data:
                return None
            tmp = "%s.%d.tmp" % (dest, threading.get_ident())     # two loads of one cover never share it
            try:
                _core.write_png(data, tmp)
                os.replace(tmp, dest)
            except (OSError, _core.RommError):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return None
            self._cache_written = getattr(self, "_cache_written", 0) + os.path.getsize(dest)
            if self._cache_written > _covers.PRUNE_EVERY and getattr(self, "cache_limit", 0):
                self._cache_written = 0
                _covers.prune_cache(self.cache_limit)
            return dest
        finally:
            r.close()

    def exchange_pair_code(self, code):
        return exchange_pair_code(self.base, code, self.s.verify)


# ------------------------------------------------------------- players

def max_players(count):
    """The most players one of RomM's player counts names: "1-4" is 4, "2" is 2,
    "Up to 8" is 8; 0 when it names no number."""
    nums = [int(n) for n in re.findall(r"\d+", str(count or ""))[:4] if len(n) <= 3]
    return max(nums) if nums else 0


def party_values(counts, n):
    """Of RomM's player count values (GET /api/roms/filters), those meaning n or more players."""
    return [c for c in counts or [] if isinstance(c, str) and max_players(c) >= n]


# ----------------------------------------------------------------- pairing
# RomM 5.x: the owner of a client API token presses Pair on it in RomM's web
# UI and gets a short single-use code (8 characters, valid about a minute);
# the device swaps it for the token, unauthenticated. RomM gives the token a
# NEW secret when it's exchanged, so anything else using that token stops
# working: one token per device.

LOOKUP_WORKERS = 6        # game records at once: RomM's own work (~45 ms each) is the slow part
PAGE_WORKERS = 4          # listing pages at once
# connections kept open to RomM: the UI's 12 cover threads, lookups, pages and a download
HTTP_POOL = 12 + LOOKUP_WORKERS + PAGE_WORKERS + 2


class _Workers:
    """A few daemon threads running submitted calls (like ThreadPoolExecutor,
    whose threads Python waits for at exit: quitting mid-check would wait out
    the requests in flight)."""

    def __init__(self, n):
        self.q, self.n = queue.SimpleQueue(), n
        for _ in range(n):
            threading.Thread(target=self._run, daemon=True).start()

    def submit(self, fn, *args):
        f = Future()
        self.q.put((f, fn, args))
        return f

    def _run(self):
        while True:
            job = self.q.get()
            if job is None:
                return
            f, fn, args = job
            if not f.set_running_or_notify_cancel():
                continue
            try:
                f.set_result(fn(*args))
            except BaseException as e:
                f.set_exception(e)

    def shutdown(self):
        """Calls not started yet are cancelled; the threads end after their current one."""
        while True:
            try:
                job = self.q.get_nowait()
            except queue.Empty:
                break
            if job is not None:
                job[0].cancel()
        for _ in range(self.n):
            self.q.put(None)


def fetch_many(fn, keys, workers=None, cancel=None):
    """{key: fn(key)} for every key, `workers` calls at a time. A call that
    raises keeps its exception as the value (raise it where it's used). Keys
    left once cancel is set are missing."""
    keys = list(keys)
    if not keys:
        return {}

    def one(k):
        if cancel is not None and cancel.is_set():
            return k, None, True
        try:
            return k, fn(k), False
        except Exception as e:                      # handed to the caller, raised where it's used
            return k, e, False
    out = {}
    pool = _Workers(max(1, min(workers or LOOKUP_WORKERS, len(keys))))
    try:
        for k, v, skipped in [f.result() for f in [pool.submit(one, k) for k in keys]]:
            if not skipped:
                out[k] = v
    finally:
        pool.shutdown()
    return out


def fetched(v):
    """A fetch_many / Prefetch result: the value, or raise what the call raised."""
    if isinstance(v, BaseException):
        raise v
    return v


class Prefetch:
    """fn(key) for keys a loop uses in order, each fetched before the loop gets
    there (`workers` at a time, at most `ahead` keys in advance). get() returns
    fn(key) or raises what it raised; a key not in the list, or asked again, is
    fetched there and then."""

    def __init__(self, fn, keys, workers=None, ahead=None):
        workers = workers or LOOKUP_WORKERS
        self.fn = fn
        self.keys = list(dict.fromkeys(keys))
        self.pos = {k: i for i, k in enumerate(self.keys)}
        self.ahead = ahead or 3 * workers
        self.next = 0
        self.futures = {}
        self.ex = _Workers(max(1, workers)) if self.keys else None
        self._fill(0)

    def _fill(self, done_upto):
        while self.ex and self.next < len(self.keys) and self.next < done_upto + self.ahead:
            k = self.keys[self.next]
            self.futures[k] = self.ex.submit(self.fn, k)
            self.next += 1

    def get(self, key):
        i = self.pos.get(key)
        if i is not None:               # the loop is here: keys it went past without asking
            self.next = max(self.next, i)       # are never fetched, the window moves on with it
            for k in [k for k in self.futures if self.pos[k] < i]:
                self.futures.pop(k).cancel()
            self._fill(i + 1)
        f = self.futures.pop(key, None)
        return self.fn(key) if f is None else f.result()

    def close(self):
        if self.ex:
            self.ex.shutdown()
            self.ex = None
            self.futures.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


PAIR_CODE_LEN = 8
PAIR_BAD_CODE = "That code isn't valid or has expired — make a new one in RomM"


def normalize_pair_code(code):
    """The code as RomM wants it (spaces/dashes dropped, upper case), or
    raise RommError with what's wrong. RomM's codes use A-Z and 2-9."""
    c = re.sub(r"[\s\-_.]", "", code or "").upper()
    if not c:
        raise _core.RommError("Type the pairing code from RomM")
    if not re.fullmatch(r"[A-Z0-9]+", c):
        raise _core.RommError("A pairing code is only letters and digits")
    if len(c) != PAIR_CODE_LEN:
        raise _core.RommError("A pairing code has %d characters (that was %d)" % (PAIR_CODE_LEN, len(c)))
    return c


def valid_token(tok):
    """A client API token the app can send: rmm_ + a sane, header-safe body."""
    return isinstance(tok, str) and tok.startswith("rmm_") and 20 <= len(tok) <= 512 \
        and re.fullmatch(r"[\x21-\x7e]+", tok) is not None


def exchange_pair_code(url, code, verify_tls=True, timeout=20):
    """Swap a RomM pairing code for a client API token. Returns
    {"token", "name", "scopes"}. Sends no Authorization header (the exchange
    is unauthenticated; a stale token has no business going along). Errors
    are RommError with a message for the user; the token never appears in one."""
    base = (url or "").strip().rstrip("/")
    if not base:
        raise _core.RommError("Set the server address first")
    if _config.url_has_userinfo(base):
        raise _core.RommError("Remove user:password@ from the server URL — the token is the login")
    c = normalize_pair_code(code)
    try:
        r = requests.post(base + "/api/client-tokens/exchange", json={"code": c},
                          headers={"User-Agent": _core.UA}, timeout=timeout,
                          verify=_config.norm_verify(verify_tls), allow_redirects=False)
    except requests.exceptions.SSLError:
        raise _core.RommError("TLS certificate rejected — set verify_tls in Settings") from None
    except requests.RequestException as e:
        raise _core.RommError("Can't reach RomM (%s)" % e.__class__.__name__) from None
    except Exception as e:
        raise _core.RommError("Request failed (%s)" % e.__class__.__name__) from None
    st = r.status_code
    if st in (400, 404, 410, 422):
        raise _core.RommError(PAIR_BAD_CODE)
    if st == 429:
        raise _core.RommError("Too many tries — wait a minute, then make a new code in RomM")
    if st == 403:
        raise _core.RommError("RomM refused: the token's owner is disabled (HTTP 403)")
    if st == 405 or 300 <= st < 400:
        raise _core.RommError("Not a RomM API response — check the URL (auth proxy in front?)")
    if st != 200:
        raise _core.RommError("RomM error HTTP %d while pairing" % st)
    try:
        body = r.json()
    except ValueError:
        raise _core.RommError("Not a RomM API response — check the URL (auth proxy in front?)") from None
    tok = body.get("raw_token") if isinstance(body, dict) else None
    if not valid_token(tok):
        raise _core.RommError("RomM's reply had no usable token (is this RomM 5 or newer?)")
    name = body.get("name") if isinstance(body.get("name"), str) else ""
    scopes = [s for s in body.get("scopes") or [] if isinstance(s, str)]
    _core.log("paired with RomM: token %r, %d scopes" % (name[:60], len(scopes)))
    return {"token": tok, "name": name[:60], "scopes": scopes}


COVER_MAX = 32 << 20      # a cover bigger than this isn't a cover


class CoverError(_core.RommError):
    """A cover couldn't be fetched just now (RomM busy or unreachable)."""
BIOS_MAX = 1 << 30         # the largest Batocera firmware files are a few hundred MB


def _read_capped(r, cap):
    """A streamed response's body, refusing (RommError) more than cap bytes:
    never let a server make the app read an endless body into memory."""
    out, n = [], 0
    try:
        for chunk in r.iter_content(1 << 16):
            n += len(chunk)
            if n > cap:
                raise _core.RommError("Response larger than %s; refused." % _core.human_size(cap))
            out.append(chunk)
    finally:
        r.close()
    return b"".join(out)
