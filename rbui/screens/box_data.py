"""On this box: shared data and the space bar, for its screens."""
import os
import time

import pygame
import rommlib
from rommlib import human_size
from rbui import theme
from rbui.theme import S, SR
from rbui.draw import measure, rrect, text


# ---- On this box: shared data and the space bar


def installed_games(lib):
    """Games RomM-o-Cera installed that are still on the box, biggest first.
    A multi-disc set is one row."""
    out, sets = [], {}
    for rid, rec in lib.snapshot().items():
        paths = [p for p in rec.get("paths", []) if os.path.exists(p)]
        if not paths:
            continue
        g = rec.get("group")
        if g:
            it = sets.get(g["id"])
            if it is None:
                it = sets[g["id"]] = {"id": int(rid), "group": g, "paths": [],
                                      "name": "%s (%d discs)" % (g.get("title") or g["base"],
                                                                 len(g.get("rom_ids") or [])),
                                      "system": rec.get("system"), "when": rec.get("when", "")}
                out.append(it)
            it["paths"] += [p for p in paths if p not in it["paths"]]
            continue
        out.append({"id": int(rid), "name": rec.get("name") or os.path.basename(paths[0]),
                    "system": rec.get("system"), "when": rec.get("when", ""), "paths": paths})
    for it in out:
        it["size"] = sum(rommlib.path_size(p) for p in it.pop("paths"))
    return sorted(out, key=lambda i: -i["size"])


def storage_by_system(lib):
    """Space per system: RomM-o-Cera's games, other games, and media."""
    ours = {os.path.normpath(p) for rec in lib.snapshot().values() for p in rec.get("paths", [])}
    out = []
    for system in sorted(os.listdir(rommlib.ROMS_ROOT)):
        sysdir = os.path.join(rommlib.ROMS_ROOT, system)
        if not os.path.isdir(sysdir) or os.path.islink(sysdir):
            continue
        row = {"system": system, "ours": 0, "other": 0, "media": 0}
        for dp, dns, fns in os.walk(sysdir):
            rel = os.path.relpath(dp, sysdir)
            media = rel != "." and rel.split(os.sep)[0] in rommlib.MEDIA_DIRS
            top = None if rel == "." else os.path.normpath(os.path.join(sysdir, rel.split(os.sep)[0]))
            for fn in fns:
                p = os.path.join(dp, fn)
                try:
                    n = os.lstat(p).st_size
                except OSError:
                    continue
                if media:
                    row["media"] += n
                elif os.path.normpath(p) in ours or (top and top in ours):
                    row["ours"] += n
                elif fn.lower().endswith((".xml", ".txt")) or ".bak" in fn.lower():
                    row["media"] += n
                else:
                    row["other"] += n
        row["total"] = row["ours"] + row["other"] + row["media"]
        if row["total"]:
            out.append(row)
    return sorted(out, key=lambda r: -r["total"])


def disk_space(cfg):
    """(total, free, keep-free reserve) of the roms drive; zeros if unknown."""
    try:
        st = os.statvfs(rommlib.ROMS_ROOT)
        total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
    except OSError:
        total = free = 0
    return total, free, rommlib.keep_free_bytes(cfg)


def pending_roms(app):
    """Downloads still queued or paused: their partial files aren't leftovers."""
    d = app.downloader
    pend = [(j.rom, j.system) for j in list(d.jobs) if j.active or j.state == rommlib.Job.PAUSED]
    return pend + [(it["rom"], it["system"]) for it in rommlib.Downloader.saved_queue()
                   if it.get("rom") and it.get("system")]


UPDATE_WAITS = (rommlib.Job.DOWNLOADING, rommlib.Job.VERIFYING, rommlib.Job.EXTRACTING)


def conversion_rows():
    """Conversions: the one running, the queue, then recent results (newest
    first), from what the worker (rb_chd.py) publishes: rommlib.conversions_state()."""
    state = rommlib.conversions_state()
    cur = state["current"]
    rows = []
    for n, t in state["queue"]:
        if t is None:
            continue
        srcs = t.get("sources") or []
        size = 0
        for x in srcs:
            try:
                size += os.path.getsize(x)
            except OSError:
                pass
        rows.append({"kind": "running" if n == cur.get("task") else "queued", "name": n, "task": t,
                     "title": os.path.splitext(os.path.basename(srcs[0]))[0] if srcs else n,
                     "discs": len(srcs), "size": size, "cur": cur if n == cur.get("task") else None})
    rows.sort(key=lambda r: r["kind"] != "running")
    for d in reversed(state["results"]):
        srcs = d.get("sources") or [""]
        rows.append({"kind": d.get("result"), "name": d.get("task"), "task": d.get("retry"),
                     "title": os.path.splitext(os.path.basename(srcs[0]))[0], "discs": len(srcs),
                     "message": d.get("message") or "", "time": d.get("time", 0), "mode": d.get("mode")})
    return rows


def conversion_status(r):
    """'Converting 42% · about 2 min left' for the running row."""
    cu = r.get("cur") or {}
    pct = cu.get("pct")
    st = cu.get("step") or "Starting"
    if pct:
        st += " %d%%" % pct
        if pct > 2:
            el = time.time() - (cu.get("since") or time.time())
            st += " · about %d min left" % max(1, round(el * (100 - pct) / pct / 60))
    return st, (pct or 0) / 100.0


def hatch(surf, rect, color, step=9):
    """Diagonal stripes: space that's set aside, not used."""
    pr = SR(rect)
    old = surf.get_clip()
    surf.set_clip(pr.clip(old) if old else pr)
    for x in range(pr.left - pr.height, pr.right, max(2, S(step))):
        pygame.draw.line(surf, color, (x, pr.bottom), (x + pr.height, pr.top), max(1, S(2)))
    surf.set_clip(old)


def space_parts(rows, total, free):
    """The space bar's used parts, summed over systems; the rest of the used
    space is Batocera itself, saves and BIOS."""
    parts = [(key, col, lab, sum(r[key] for r in rows or [])) for key, col, lab in theme.SPACE_PARTS]
    rest = max(0, total - free - sum(p[3] for p in parts))
    return parts + [("system", theme.SYSTEM_COL, "Batocera and saves", rest)]


def space_bar(surf, fonts, rect, parts, total, free, keep, cols=2):
    """The whole drive as one bar: each used part in its colour, the free
    space as an empty track whose last stretch, the keep-free reserve
    downloads never use, is striped. Then the legend; returns its bottom."""
    x, y, w, h = rect
    if total <= 0:
        return y
    pr = SR(rect)
    bar = pygame.Surface(pr.size, pygame.SRCALPHA)
    bar.fill(theme.BG2)
    kw = int(pr.width * min(keep, total) / total)
    kr = pygame.Rect(pr.width - kw, 0, kw, pr.height)
    old = bar.get_clip()
    bar.set_clip(kr)
    for sx in range(kr.left - pr.height, kr.right, max(2, S(9))):
        pygame.draw.line(bar, theme.LINE, (sx, pr.height), (sx + pr.height, 0), max(1, S(2)))
    bar.set_clip(old)
    cx = 0
    for key, col, lab, n in parts:
        sw = int(pr.width * n / total)
        if n and sw < S(3):
            sw = S(3)
        if sw:
            bar.fill(col, (cx, 0, sw, pr.height))
            cx += sw
    mask = pygame.Surface(pr.size, pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=pr.height // 2)
    bar.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    surf.blit(bar, pr.topleft)
    usable = max(0, free - keep)
    items = [(lab, n, col) for key, col, lab, n in parts if n] + \
        [("Free for downloads", usable, None), ("Kept free", min(keep, total), "hatch")]
    colw = w // cols
    ly = y + h + 16
    for i, (lab, n, col) in enumerate(items):
        lx, yy = x + (i % cols) * colw, ly + (i // cols) * 28
        sw = pygame.Rect(lx, yy + 3, 14, 14)
        if col == "hatch":
            rrect(surf, theme.BG2, sw, 4)
            hatch(surf, sw, theme.DIM, 5)
        elif col is None:
            rrect(surf, theme.BG2, sw, 4)
            rrect(surf, theme.LINE, sw, 4, 1)
        else:
            rrect(surf, col, sw, 4)
        size = human_size(n)
        text(surf, fonts.small, size, (lx + colw - 28, yy), theme.TEXT, "topright")
        text(surf, fonts.small, lab, (lx + 24, yy), theme.DIM, maxw=colw - 64 - measure(fonts.small, size))
    return ly + ((len(items) + cols - 1) // cols) * 28


def bios_rows(app):
    """BIOS status: systems with games here first (needing attention first),
    then the others RomM has BIOS files for."""
    by_sys = {}
    for p in app.platforms:
        by_sys.setdefault(p["_system"], []).append(p["id"])
    with_games = set()
    try:
        for d in os.listdir(rommlib.ROMS_ROOT):
            full = os.path.join(rommlib.ROMS_ROOT, d)
            if os.path.isdir(full) and any(not n.startswith(".") and n not in
                                           ("gamelist.xml", "_info.txt") + rommlib.GAMELIST_MEDIA_DIRS
                                           for n in os.listdir(full)):
                with_games.add(d)
    except OSError:
        pass
    rows = rommlib.bios_status(app.api if app.platforms else None, by_sys, with_games)
    for r in rows:
        r["here"] = r["system"] in with_games
    return sorted(rows, key=lambda r: (not r["here"], 0 if r["offers"] else 1 if r["missing"] else 2,
                                       r["name"].lower()))


def bios_pill(it):
    """(pill text, colour, detail line) for one system's BIOS row."""
    nmiss, noff = len(it["missing"]), len(it["offers"])
    if not nmiss:
        sub = "%d file%s" % (len(it["required"]), "" if len(it["required"]) == 1 else "s")
        if it["fetched"]:
            f, rec = sorted(it["fetched"].items(), key=lambda kv: kv[1].get("when", ""))[-1]
            sub += " · %d fetched by RomM-o-Cera, last %s" % (len(it["fetched"]), rec.get("when", "")[:10])
        return "Complete", theme.GREEN, sub
    if noff:
        return ("RomM has %d of %d missing" % (noff, nmiss), theme.ACCENT_HI,
                ", ".join(os.path.basename(m["file"]) for m, _ in it["offers"][:4]))
    return ("%d missing, not in RomM" % nmiss, theme.AMBER,
            ", ".join(os.path.basename(m["file"]) for m in it["missing"][:4]))


def plural(n, word, many=None):
    return "%d %s" % (n, word if n == 1 else (many or word + "s"))
