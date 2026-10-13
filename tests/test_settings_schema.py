"""Settings: a golden record of the whole screen, taken before the settings
table replaced the hand-written rows. Every row's label, value and grey line,
every help text, and what left, right and A do on every row (the config after,
toasts, the saved config.json, sounds, reconnects, wake-ups, cache prunes,
modals and pushed screens), on three kinds of box and with default, changed
and hand-edited odd values.

tests/settings_golden.json was written once from the pre-refactor tree and is
only read here. Never regenerate it to make a change pass: a difference is a
visible change. ROMM_GOLDEN=write writes it only when it's missing;
ROMM_GOLDEN=force overwrites it."""
import copy
import json
import os
import shutil
import time

import pytest

import rommlib

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings_golden.json")

# what the box has: every host-dependent input the screen reads
ENVS = {
    "full": dict(threads=8, ram=8 << 30, mksquashfs=True, z3ds=True, ps3=True, rvz=True, dolphin=None,
                 zar=True, x360=None, backup=None, update=None, cache=None, unreadable=False),
    "tools": dict(threads=8, ram=8 << 30, mksquashfs=True, z3ds=True, ps3=True, rvz=True,
                  dolphin="/opt/tools/dolphin-tool", zar=True, x360=("/opt/tools/extract-xiso", "/opt/tools/zarchive"),
                  backup=None, update=None, cache=123456, unreadable=False),
    "bare": dict(threads=8, ram=2 << 30, mksquashfs=False, z3ds=False, ps3=False, rvz=False, dolphin=None,
                 zar=False, x360=None, backup="0.7.0", update="v9.9.9", cache=123456, unreadable=True),
}

CHANGED = {
    "url": "http://romm.lan:8080", "token": "abcdefghijklmnop", "verify_tls": "/etc/ssl/my-ca.pem",
    "verify_tls_ca": "/etc/ssl/my-ca.pem", "swap_ab": True, "button_style": "playstation",
    "check_updates": False, "chd": False, "chd_threads": 3, "chd_low_memory": True, "keep_free_gb": 50,
    "extract_parallel": False, "rvz": True, "xbox_bootable": False, "squashfs": True, "z3ds": False,
    "zar": True, "ps3_folders": False, "ps3_decrypt": True, "cache_limit_mb": 4096, "download_hours": "22-6",
    "background_downloads": False, "speed_while_playing": 10, "notify": False, "manuals": False,
    "es_image": "boxart", "es_video_mb": 0, "es_extra_art": False, "es_tags": False,
    "sounds": "quiet", "theme": "xbox_oled", "platform_map": {"arcade": "mame"},
}

# hand edits that don't crash today
ODD = {
    "chd_threads": "junk", "chd_low_memory": "maybe", "download_hours": "99-1", "sounds": "loud",
    "theme": "nope", "button_style": "", "speed_while_playing": None, "keep_free_gb": 2.5,
    "cache_limit_mb": "junk", "es_image": "huge", "es_video_mb": "lots",
}

STATES = {"defaults": {}, "changed": CHANGED, "odd": ODD}
ITEM_RUNS = [("full", "defaults"), ("full", "changed"), ("full", "odd"), ("tools", "defaults"),
             ("tools", "changed"), ("bare", "defaults"), ("bare", "changed"), ("bare", "odd")]
ACTION_RUNS = [("full", "defaults"), ("full", "changed"), ("full", "odd"), ("tools", "defaults"),
               ("tools", "changed"), ("bare", "defaults")]


def _pin(monkeypatch, env):
    e = ENVS[env]
    monkeypatch.setattr(rommlib, "VERSION", "0.7.1")
    monkeypatch.setattr(rommlib, "VERSION_LABEL", "0.7.1")
    monkeypatch.setattr(rommlib, "cpu_threads", lambda: e["threads"])
    monkeypatch.setattr(rommlib, "total_ram", lambda: e["ram"])
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: (
        ("/usr/bin/mksquashfs" if e["mksquashfs"] else None) if name == "mksquashfs" else which(name, *a, **k)))
    monkeypatch.setattr(rommlib, "z3ds_supported", lambda: e["z3ds"])
    monkeypatch.setattr(rommlib, "ps3_decrypt_supported", lambda: e["ps3"])
    monkeypatch.setattr(rommlib, "rvz_supported", lambda: e["rvz"])
    monkeypatch.setattr(rommlib, "dolphin_tool_path", lambda: e["dolphin"])
    monkeypatch.setattr(rommlib, "zar_supported", lambda: e["zar"])
    monkeypatch.setattr(rommlib, "x360_tools", lambda: e["x360"])
    monkeypatch.setattr(rommlib, "backup_version", lambda *a, **k: e["backup"])
    monkeypatch.setattr(rommlib, "config_unreadable", lambda: e["unreadable"])
    monkeypatch.setattr(rommlib.Api, "platforms", lambda self, *a, **k: [])
    monkeypatch.setattr(rommlib, "newer_release", lambda cfg, force=False: None)
    monkeypatch.setattr(rommlib, "ps3_keys", lambda *a, **k: {})
    monkeypatch.setattr(rommlib, "fetch_ps3_keys", lambda api, pid: 0)


class _Sfx:
    def __init__(self, rec):
        self.rec, self.level = rec, None

    def set_level(self, lv):
        self.rec["sfx_level"].append(lv)

    def play(self, name):
        self.rec["sfx"].append(name)


@pytest.fixture
def screen(isolated, rb, monkeypatch):
    """(make(env) -> (app, SettingsScreen, rec)) with every outside effect recorded, not done."""
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()

    def make(env):
        _pin(monkeypatch, env)
        app = rb.App(None, headless=True)
        rec = dict(bg=0, prune=[], toasts=[], sfx=[], sfx_level=[], wake=0)
        monkeypatch.setattr(rommlib, "prune_cache", lambda lim, *a, **k: rec["prune"].append(lim))

        def bg(fn, done=None, err=None):
            rec["bg"] += 1
            try:
                res = fn()
            except Exception as e:
                (err or (lambda e: app.toast(str(e), rb.RED)))(e)
                return
            if done:
                done(res)
        app.bg = bg
        toast = app.toast

        def rec_toast(msg, color=None):
            names = {getattr(rb, n): n for n in ("BLUE", "AMBER", "GREEN", "RED", "TEXT")}
            rec["toasts"].append([msg, "None" if color is None else names.get(color, "?")])
            toast(msg, color)
        app.toast = rec_toast
        app.sfx = _Sfx(rec)
        app.input.names = lambda: ["Xbox Wireless Controller"]
        cv = app.downloader.cv
        notify = cv.notify_all

        def rec_notify():
            rec["wake"] += 1
            notify()
        cv.notify_all = rec_notify
        st = rb.SettingsScreen(app)
        app.push(st)
        return app, st, rec

    yield make
    rb.apply_theme("default")


def _reset(rb, app, st, rec, env, state):
    e = ENVS[env]
    app.cfg.clear()
    app.cfg.update(copy.deepcopy(dict(rommlib.DEFAULT_CONFIG, **STATES[state])))
    app.stack[:] = [st]
    app.modal, app.toasts, app.update_tag = None, [], e["update"]
    st.cache_bytes, st.prev = e["cache"], e["backup"]
    rb.apply_theme(app.cfg.get("theme"))
    app.input.set_swap(app.cfg.get("swap_ab"))
    rec.update(bg=0, prune=[], toasts=[], sfx=[], sfx_level=[], wake=0)
    try:
        os.remove(rommlib.CONFIG_PATH)
    except OSError:
        pass


def _jsonable(x):
    return json.loads(json.dumps(x))


def _items(rb, make):
    out = {}
    for env, state in ITEM_RUNS:
        app, st, rec = make(env)
        _reset(rb, app, st, rec, env, state)
        out["%s/%s" % (env, state)] = _jsonable(st.items())
    return out


def _hints(rb, make):
    """Every row's hints (sel on a row shows its section), and the hub's under "§"."""
    app, st, rec = make("full")
    _reset(rb, app, st, rec, "full", "defaults")
    out = {}
    for i, it in enumerate(st.items()):
        st.sel = i
        out.setdefault(it[0], _jsonable(st.hints()))
    return out


def _actions(rb, make):
    out = {}
    for env, state in ACTION_RUNS:
        app, st, rec = make(env)
        _reset(rb, app, st, rec, env, state)
        rows = [it[0] for it in st.items() if it[0] != "§"]
        for row in rows:
            for a in ("left", "right", "ok"):
                _reset(rb, app, st, rec, env, state)
                before = copy.deepcopy(app.cfg)
                api = app.api
                st.sel = [it[0] for it in st.items()].index(row)
                st.action(a)
                after = _jsonable(app.cfg)
                r = {
                    "cfg": {k: v for k, v in after.items() if before.get(k, KeyError) != v},
                    "gone": sorted(set(before) - set(after)),
                    "toasts": rec["toasts"], "sfx": rec["sfx"], "sfx_level": rec["sfx_level"],
                    "wake": rec["wake"], "prune": rec["prune"], "bg": rec["bg"],
                    "reconnect": app.api is not api,
                    "modal": app.modal and [type(app.modal).__name__, app.modal.title],
                    "pushed": [type(s).__name__ for s in app.stack[1:]],
                }
                if state != "odd":                 # odd in-memory values: only what the row does
                    try:
                        with open(rommlib.CONFIG_PATH) as f:
                            saved = json.load(f)
                    except OSError:
                        saved = None
                    if saved is not None:
                        saved.pop("config_version", None)
                        want = {k: after[k] for k in rommlib.DEFAULT_CONFIG if k in after}
                        saved = "cfg" if list(saved.items()) == list(want.items()) else saved
                    r["saved"] = saved
                out["%s/%s/%s/%s" % (env, state, row, a)] = r
        app.stack[:] = [st]
    return out


def _record(rb, make):
    return {"items": _items(rb, make), "hints": _hints(rb, make),
            "help": _jsonable(rb.SETTING_HELP), "actions": _actions(rb, make)}


def test_settings_screen_matches_the_golden_record(screen, rb):
    got = _record(rb, screen)
    mode = os.environ.get("ROMM_GOLDEN")
    if mode == "force" or (mode == "write" and not os.path.exists(GOLDEN)):
        with open(GOLDEN, "w") as f:
            json.dump(got, f, indent=1, sort_keys=True, ensure_ascii=False)
            f.write("\n")
    with open(GOLDEN) as f:
        want = json.load(f)
    for part in ("help", "hints", "items"):
        assert got[part] == want[part], part
    assert sorted(got["actions"]) == sorted(want["actions"])
    for k in want["actions"]:
        assert got["actions"][k] == want["actions"][k], k


# ------------------------------------------------------------ config.json

DEFAULTS_LITERAL = [
    ("url", ""), ("token", ""), ("verify_tls", True), ("verify_tls_ca", ""), ("swap_ab", False),
    ("button_style", "auto"), ("check_updates", True), ("chd", True), ("chd_threads", "auto"),
    ("chd_low_memory", "auto"), ("keep_free_gb", 20), ("extract_parallel", True), ("rvz", False),
    ("xbox_bootable", True), ("squashfs", False), ("z3ds", True), ("zar", False), ("wux", True), ("wua", False), ("ps3_folders", True),
    ("ps3_decrypt", False), ("cache_limit_mb", 1024), ("download_hours", "any"), ("background_downloads", True),
    ("speed_while_playing", 0), ("notify", True), ("manuals", True), ("es_image", "screenshot"),
    ("es_video_mb", 50), ("es_extra_art", True), ("es_tags", True), ("sounds", "normal"),
    ("collection_tiles", "medium"), ("theme", "default"), ("update_channel", "stable"),
    ("theme_auto", False),
    ("theme_custom_accent", 195), ("theme_custom_tint", 225), ("theme_custom_strength", 30),
    ("theme_custom_mode", "dark"),
    ("platform_map", {}), ("notes_seen", ""), ("welcome_seen", False),
]


def test_default_config_keys_values_and_order_are_unchanged():
    """The key order is the order config.json is written in."""
    assert list(rommlib.DEFAULT_CONFIG.items()) == DEFAULTS_LITERAL
    assert isinstance(rommlib.DEFAULT_CONFIG, dict)


def test_a_default_config_json_is_written_byte_for_byte_as_before(isolated):
    rommlib.save_config(dict(rommlib.DEFAULT_CONFIG))
    with open(rommlib.CONFIG_PATH) as f:
        text = f.read()
    body = ",\n".join(" %s: %s" % (json.dumps(k), json.dumps(v)) for k, v in DEFAULTS_LITERAL)
    assert text.startswith("{\n" + body)
    data = json.loads(text)
    data.pop("config_version", None)
    assert list(data.items()) == DEFAULTS_LITERAL
    assert text == "{\n" + body + ',\n "config_version": 2\n}'


# hand-edited config.json -> what the engine and the app make of it, as
# changes from a fresh install's. These stay exactly as they are, whatever
# load_config learns to check.
FRESH = {
    "keep_free": 20 << 30, "cache_limit": 1024 << 20, "chd_threads": 6, "chd_low_memory": False,
    "in_hours": True, "hours": "Any time", "token": "", "verify": True, "base": "", "api_verify": True,
    "conv_createcd": True, "conv_createdvd": True, "conv_cso": True, "conv_rvz": False, "conv_squashfs": False,
    "conv_z3ds": True, "conv_zar": False, "conv_wua": False, "conv_wux": False, "conv_ps3": True, "on_swap_ab": False, "on_check_updates": True,
    "on_extract_parallel": True, "on_background_downloads": True, "on_notify": True, "on_manuals": True,
    "sfx": 1.0, "speed": 0.0, "platform_map": {}, "video_max": 50 << 20, "es_image": "screenshot",
}
LOADS = [
    ({}, {}),
    ({"keep_free_gb": "5", "cache_limit_mb": "512", "chd_threads": "4", "chd_low_memory": "false",
      "download_hours": "1-7", "chd": "false", "notify": 0, "manuals": None, "z3ds": [], "verify_tls": "false",
      "rvz": "yes", "zar": "no", "squashfs": 1, "ps3_folders": "", "xbox_bootable": "0"},
     {"keep_free": 5 << 30, "cache_limit": 512 << 20, "chd_threads": 4, "in_hours": False, "hours": "1 am – 7 am",
      "verify": False, "api_verify": False, "conv_rvz": True, "conv_squashfs": True, "conv_z3ds": False,
      "conv_zar": True, "conv_ps3": False, "on_notify": False, "on_manuals": False}),
    ({"keep_free_gb": "junk", "cache_limit_mb": "junk", "chd_threads": "junk", "chd_low_memory": None,
      "download_hours": "99-1", "sounds": "loud", "speed_while_playing": 0, "platform_map": None,
      "url": None, "token": None}, {}),
    ({"keep_free_gb": None, "cache_limit_mb": None, "chd_threads": None, "download_hours": None,
      "verify_tls": None, "xbox_bootable": None, "ps3_folders": 0, "chd_low_memory": "auto"},
     {"conv_ps3": False}),
    ({"keep_free_gb": 0, "cache_limit_mb": 0, "chd_threads": 99, "chd_low_memory": True,
      "download_hours": "22-6", "verify_tls": "/etc/ca.pem", "url": " https://romm.lan/ ", "token": " t0k "},
     {"keep_free": 0, "cache_limit": 0, "chd_threads": 8, "chd_low_memory": True, "in_hours": False,
      "hours": "10 pm – 6 am", "token": "t0k", "verify": "/etc/ca.pem", "base": "https://romm.lan",
      "api_verify": "/etc/ca.pem"}),
    ({"keep_free_gb": [], "cache_limit_mb": [], "chd_threads": [], "download_hours": [],
      "chd_low_memory": 0, "sounds": None, "check_updates": 0}, {"on_check_updates": False}),
    ({"keep_free_gb": 2.5, "cache_limit_mb": -5, "chd_threads": "0", "chd_low_memory": False,
      "download_hours": "any", "sounds": "off", "speed_while_playing": "2"},
     {"keep_free": int(2.5 * (1 << 30)), "cache_limit": 0, "chd_threads": 1, "sfx": 0.0, "speed": 2.0}),
    ({"es_video_mb": "25", "es_image": "boxart"}, {"video_max": 25 << 20, "es_image": "boxart"}),
    ({"es_video_mb": "lots", "es_image": "huge", "es_extra_art": 0, "es_tags": None}, {}),
    ({"es_video_mb": 0, "es_video_mb": -3}, {"video_max": 0}),
]


def _effective(cfg):
    out = {
        "keep_free": rommlib.keep_free_bytes(cfg), "cache_limit": rommlib.cache_limit_bytes(cfg),
        "chd_threads": rommlib.chd_threads(cfg), "chd_low_memory": rommlib.chd_low_memory(cfg),
        "in_hours": rommlib.in_download_hours(cfg), "hours": rommlib.hours_label(cfg["download_hours"]),
        "token": cfg["token"], "verify": cfg["verify_tls"],
    }
    api = rommlib.Api.from_config(cfg)
    out["base"], out["api_verify"] = api.base, api.s.verify
    for c in rommlib.CONVERTERS:
        out["conv_" + c.mode] = c.enabled(cfg, sorted(c.systems)[0])
    for k in ("swap_ab", "check_updates", "extract_parallel", "background_downloads", "notify", "manuals"):
        out["on_" + k] = bool(cfg[k])
    out["sfx"] = {"normal": 1.0, "quiet": 0.4, "off": 0.0}.get(cfg["sounds"], 1.0)     # Sfx.LEVEL
    out["speed"] = float(cfg["speed_while_playing"] or 0)                             # rb_dl's cap
    out["platform_map"] = dict(cfg["platform_map"] or {})
    out["video_max"], out["es_image"] = rommlib.es_video_max(cfg), cfg["es_image"]
    return out


@pytest.mark.parametrize("i", range(len(LOADS)))
def test_hand_edited_config_values_mean_what_they_meant(isolated, monkeypatch, i):
    _pin(monkeypatch, "tools")
    monkeypatch.setattr(rommlib.time, "localtime",
                        lambda now=None: time.struct_time((2026, 1, 1, 12, 0, 0, 3, 1, 0)))
    data, changes = LOADS[i]
    if data:
        os.makedirs(rommlib.DATA_DIR, exist_ok=True)
        with open(rommlib.CONFIG_PATH, "w") as f:
            json.dump(data, f)
    assert _effective(rommlib.load_config()) == dict(FRESH, **changes)


def _write(data):
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with open(rommlib.CONFIG_PATH, "w") as f:
        json.dump(data, f)


def _saved():
    with open(rommlib.CONFIG_PATH) as f:
        return json.load(f)


def test_an_old_config_without_a_version_loads_as_before_and_saves_one(isolated):
    old = dict(rommlib.DEFAULT_CONFIG, url="https://romm.lan", chd=False, keep_free_gb=5)
    _write(old)
    cfg = rommlib.load_config()
    assert cfg == old and "config_version" not in cfg
    rommlib.save_config(cfg)
    saved = _saved()
    assert saved.pop("config_version") == rommlib.CONFIG_VERSION == 2
    assert saved == old and list(saved) == list(rommlib.DEFAULT_CONFIG)
    assert rommlib.load_config() == old


def test_a_renamed_key_keeps_its_value_through_load_and_save(isolated, monkeypatch):
    monkeypatch.setattr(rommlib, "RENAMED", {"old_k": "chd"})
    monkeypatch.setattr(rommlib, "_logged_once", set())
    _write({"old_k": False})
    cfg = rommlib.load_config()
    assert cfg["chd"] is False and "old_k" not in cfg
    rommlib.save_config(cfg)
    assert _saved()["chd"] is False and "old_k" not in _saved()
    assert rommlib.load_config()["chd"] is False
    _write({"old_k": False, "chd": True})                  # both: the new name wins
    assert rommlib.load_config()["chd"] is True
    with open(rommlib.LOG_PATH) as f:
        assert f.read().count("old_k is now chd") == 1     # once per process


def test_a_newer_releases_config_loads(isolated):
    _write({"config_version": 99, "url": "https://romm.lan", "some_new_key": 1})
    cfg = rommlib.load_config()
    assert cfg["url"] == "https://romm.lan" and "config_version" not in cfg


def test_values_that_broke_the_app_fall_back_to_their_default(isolated, monkeypatch):
    """An int token crashed load_config, a list platform_map crashed the
    platform list, keep_free_gb "x" the Settings row. Now: the default, logged
    once per key however often a worker loads the config."""
    monkeypatch.setattr(rommlib, "_logged_once", set())
    _write({"token": 12345, "url": 7, "platform_map": [1], "keep_free_gb": "x", "speed_while_playing": "fast",
            "sounds": [], "chd": "false"})
    for _ in range(3):
        cfg = rommlib.load_config()
    assert cfg["token"] == "" and cfg["url"] == "" and cfg["platform_map"] == {}
    assert cfg["keep_free_gb"] == 20 and cfg["speed_while_playing"] == 0 and cfg["sounds"] == "normal"
    assert cfg["chd"] == "false"                       # on/off keys keep what they hold (truthy: on)
    with open(rommlib.LOG_PATH) as f:
        log = f.read()
    for k in ("token", "url", "platform_map", "keep_free_gb", "speed_while_playing", "sounds"):
        assert log.count("config.json: %s isn't usable" % k) == 1, k
    assert "12345" not in log and "chd" not in log
    rommlib.save_config(cfg)
    assert _saved()["keep_free_gb"] == 20               # the next save writes the default


def test_hand_typed_numbers_in_quotes_and_odd_button_labels_open_settings(screen):
    """"keep_free_gb": "20" or "speed_while_playing": "5" (they parse, so they're
    kept) and a non-text button_style made the Settings screen raise on open."""
    _write({"keep_free_gb": "20", "speed_while_playing": "5", "button_style": 1})
    app, st, rec = screen("full")
    assert app.cfg["keep_free_gb"] == "20" and app.cfg["button_style"] == "auto"
    rows = {r[0]: r for r in st.items() if r and len(r) > 1}
    assert "20 GB" in rows["keepfree"] and "5 MB/s" in rows["speed"]
    st.draw(app.canvas)


def test_a_list_of_pairs_platform_map_still_counts(isolated):
    _write({"platform_map": [["arcade", "mame"]], "verify_tls_ca": 5})
    cfg = rommlib.load_config()
    assert cfg["platform_map"] == [["arcade", "mame"]] and cfg["verify_tls_ca"] == 5


def test_settings_opens_on_a_list_of_pairs_platform_map(screen, rb):
    """The platform list reads a list of pairs as a mapping (dict.update), so
    it stays; Settings' arcade row read it with .get and crashed."""
    _write({"platform_map": [["arcade", "mame"]]})
    app, st, rec = screen("full")
    app.cfg.update(rommlib.load_config())
    assert {it[0]: it[2] for it in st.items()}["arcade"] == "roms/mame"


def test_settings_opens_on_a_config_that_crashed_it(screen, rb):
    _write({"keep_free_gb": "x", "speed_while_playing": "fast", "sounds": []})
    app, st, rec = screen("full")
    app.cfg.update(rommlib.load_config())
    rows = {it[0]: it[2] for it in st.items()}
    assert rows["keepfree"] == "20 GB" and rows["speed"] == "No limit" and rows["sounds"] == "On"


@pytest.mark.parametrize("v", [None, "", [], 0])
def test_an_empty_keep_free_still_reads_nothing(screen, rb, v):
    """The row showed "Nothing (fill the disk)" for any falsy value; the
    check must keep them (the engine still reads null as 20 GB)."""
    _write({"keep_free_gb": v})
    assert rommlib.load_config()["keep_free_gb"] == v
    app, st, rec = screen("full")
    app.cfg.update(rommlib.load_config())
    assert {it[0]: it[2] for it in st.items()}["keepfree"] == "Nothing (fill the disk)"


def test_every_default_passes_its_own_check():
    for st in rommlib.SETTINGS:
        assert st.valid is None or st.valid(st.default), st.key


def test_converter_messages_name_the_settings_row():
    for c in rommlib.CONVERTERS:
        assert c.setting_name == rommlib.SETTING[c.setting].label, c.mode
        assert rommlib.SETTING[c.setting].row


# ------------------------------------------------------------ the table stays complete

def test_every_setting_has_one_key_and_a_complete_row(screen, rb):
    keys = [st.key for st in rommlib.SETTINGS]
    rows = [st.row for st in rommlib.SETTINGS if st.row]
    assert len(set(keys)) == len(keys) and len(set(rows)) == len(rows)
    app, scr, rec = screen("full")
    shown = [it[0] for it in scr.items()]
    for st in rommlib.SETTINGS:
        if st.row:
            assert st.row in shown and st.label and st.row in rb.SETTING_HELP, st.key
        if st.kind:
            assert st.kind in ("onoff", "choice") and st.label and st.sub and st.help, st.key
            assert all(e in rb.SettingsScreen.EFFECTS for e in st.effects), st.key
        if st.kind == "choice":
            assert st.default in st.choices and st.row in rb.SettingsScreen.SHOW, st.key
        else:
            assert not st.choices, st.key
        assert st.kind or not st.effects, st.key
    assert set(rb.SettingsScreen.NEEDS) <= set(rows) and set(rb.SettingsScreen.SHOW) <= set(rows)


def test_docs_list_every_key_with_its_default():
    """docs/settings.md's config.json table: every key, the same default."""
    with open(os.path.join(os.path.dirname(GOLDEN), os.pardir, "docs", "settings.md"), encoding="utf-8") as f:
        doc = {}
        for line in f:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[0].startswith("`") and cells[0].endswith("`"):
                v = cells[1].strip("`")
                if v == "—":
                    v = ""
                else:
                    try:
                        v = json.loads(v)
                    except ValueError:
                        pass
                doc[cells[0].strip("`")] = v
    for st in rommlib.SETTINGS:
        if st.key == "verify_tls_ca":                  # Settings' own memory, not for hand-editing
            continue
        assert st.key in doc and doc[st.key] == st.default, st.key
