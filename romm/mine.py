"""Your side of RomM, read-only: statuses and lists, notes, RetroAchievements
progress, screenshots. Plain functions over RomM's records (no requests)."""
import calendar
import re


# RomM 5.4's own status words (RomUserStatus) and its web UI's names for them
STATUS_LABELS = {"incomplete": "Incomplete", "finished": "Finished", "completed_100": "Completed 100%",
                 "retired": "Retired", "never_playing": "Never Playing"}

# your lists: name -> (RomM's statuses filter values, any of them; order_by; order_dir)
USER_LISTS = {
    "playing": (("now_playing",), "last_played", "desc"),
    "backlog": (("backlogged",), "name", "asc"),
    "finished": (("finished", "completed_100"), "last_played", "desc"),
    "incomplete": (("incomplete",), "last_played", "desc"),
    "retired": (("retired",), "name", "asc"),
    "never_playing": (("never_playing",), "name", "asc"),
}


def utc_seconds(stamp):
    """Epoch seconds of one of RomM's UTC datetimes ("2026-10-01T12:30:00Z",
    "+00:00", fractions): None when there is none or it isn't one."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)", stamp or "") if isinstance(stamp, str) else None
    if not m:
        return None
    try:
        return calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))
    except (ValueError, OverflowError):
        return None


def user_status(rom):
    """Your RomM status of a game, from the rom_user any list or detail record
    carries (RomM 5.x): {"status": "finished" or None, "label": "Finished" or
    "", "now_playing", "backlogged", "hidden": bools, "rating", "difficulty",
    "completion": RomM's ints (0 = unset), "last_played": epoch seconds or
    None, "has_notes": bool}. A game you never marked (RomM's stand-in, id -1,
    or no rom_user at all) has everything off."""
    ru = rom.get("rom_user") if isinstance(rom, dict) else None
    ru = ru if isinstance(ru, dict) else {}

    def num(k):
        v = ru.get(k)
        return v if type(v) is int else 0
    status = ru.get("status") if ru.get("status") in STATUS_LABELS else None
    return {"status": status, "label": STATUS_LABELS.get(status, ""),
            "now_playing": ru.get("now_playing") is True, "backlogged": ru.get("backlogged") is True,
            "hidden": ru.get("hidden") is True, "rating": num("rating"), "difficulty": num("difficulty"),
            "completion": num("completion"), "last_played": utc_seconds(ru.get("last_played")),
            "has_notes": rom.get("has_notes") is True if isinstance(rom, dict) else False}


def on_user_list(rom, name):
    """True when the game's rom_user puts it on that USER_LISTS list."""
    st = user_status(rom)
    if st["hidden"]:
        return False
    flags = {"now_playing": st["now_playing"], "backlogged": st["backlogged"]}
    return any(flags[v] if v in flags else st["status"] == v for v in USER_LISTS[name][0])


def my_notes(detail, user_id=None):
    """Your own notes on a game, newest first, from a detail record's
    all_user_notes (RomM 5.4 keeps notes apart from rom_user): [{"title",
    "content" (RomM's raw Markdown), "updated_at" (epoch seconds), "public"}].
    user_id: yours (Api.me()["id"]); without it, rom_user's."""
    if user_id is None:
        uid = (detail.get("rom_user") or {}).get("user_id")
        user_id = uid if type(uid) is int and uid > 0 else None
    out = []
    for n in detail.get("all_user_notes") or []:
        if not isinstance(n, dict) or user_id is None or n.get("user_id") != user_id:
            continue
        out.append({"title": str(n.get("title") or ""), "content": str(n.get("content") or ""),
                    "updated_at": utc_seconds(n.get("updated_at")), "public": n.get("is_public") is True})
    out.sort(key=lambda n: n["updated_at"] or 0, reverse=True)
    return out


def ra_progress(user, rom):
    """Your RetroAchievements progress in a game, from Api.me()'s
    ra_progression matched on the rom's ra_id: {"earned", "earned_hardcore",
    "total", "last" (epoch seconds or None), "award" ("mastered"... or "")}, or
    None (no RA game, no RA account linked in RomM, or nothing earned yet)."""
    ra_id = rom.get("ra_id")
    prog = (user or {}).get("ra_progression") if isinstance(user, dict) else None
    if not ra_id or not isinstance(prog, dict):
        return None
    for r in prog.get("results") or []:
        if isinstance(r, dict) and r.get("rom_ra_id") == ra_id:
            def num(k):
                v = r.get(k)
                return v if type(v) is int and v >= 0 else 0
            earned = num("num_awarded") or len(r.get("earned_achievements") or [])
            return {"earned": earned, "earned_hardcore": num("num_awarded_hardcore"),
                    "total": num("max_possible"), "last": utc_seconds(r.get("most_recent_awarded_date")),
                    "award": str(r.get("highest_award_kind") or "")}
    return None


def screenshot_paths(rom):
    """The game's screenshots on the RomM server (merged_screenshots: what
    RomM's scrapers stored), as server paths Api.fetch_screenshot takes;
    anything that isn't a plain server path is left out."""
    out = []
    for p in rom.get("merged_screenshots") or []:
        if isinstance(p, str) and p.startswith("/") and not p.startswith("//") \
                and ".." not in p.split("?")[0].split("/") and p not in out:
            out.append(p)
    return out
