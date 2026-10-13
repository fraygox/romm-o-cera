"""Tools the app fetches by itself: a setting that needs one is on (Wii U to .wux
is on by default), this version isn't installed, and there's a reason (an older
version installed, or games here it would convert). One toast, a failure said
once per version, the old version removed after an upgrade, and Compress saying
what it waits for."""
import os

import pytest

import rommlib

def _fake_installed(monkeypatch, *specs):
    """Which tools count as installed (and every one as built for this CPU)."""
    from romm import tools as T
    have = {s.slug for s in specs}
    for fn, slug in (("wuatool_path", "wiiu-tools"), ("dolphin_tool_path", "dolphin-tool"),
                     ("x360_tools", "xbox360-tools")):
        monkeypatch.setattr(T, fn, (lambda on: lambda: "/x" if on else None)(slug in have))
    for fn in ("wua_supported", "rvz_supported", "zar_supported"):
        monkeypatch.setattr(T, fn, lambda: True)


@pytest.fixture
def tools(isolated, monkeypatch, tmp_path):
    monkeypatch.setenv("ROMM_BROWSER_TOOLS", str(tmp_path / "tools"))
    _fake_installed(monkeypatch)
    return tmp_path


def _fetch(cfg=None, systems=None):
    return [(s.slug, why) for s, why in rommlib.tools_to_fetch(dict(rommlib.DEFAULT_CONFIG, **(cfg or {})), systems)]


def test_only_for_a_reason_and_only_with_a_setting_on(tools, monkeypatch):
    assert _fetch() == []                                         # no Wii U games: nothing downloads
    os.makedirs(os.path.join(rommlib.ROMS_ROOT, "wiiu", "images"))
    open(os.path.join(rommlib.ROMS_ROOT, "wiiu", "images", "x.wud"), "w").close()   # media folders don't count
    assert _fetch() == []
    os.makedirs(os.path.join(rommlib.ROMS_ROOT, "wiiu", "Game"))
    open(os.path.join(rommlib.ROMS_ROOT, "wiiu", "Game", "Disc.wud"), "w").close()
    assert _fetch() == [("wiiu-tools", "games")]                  # on by default, a disc to compress
    assert _fetch({"wux": False}) == []                           # both Wii U switches off
    assert _fetch({"wux": False, "wua": True}) == [("wiiu-tools", "games")]
    assert _fetch(systems={"psx"}) == [] and _fetch(systems={"wiiu"}) == [("wiiu-tools", "games")]
    _fake_installed(monkeypatch, rommlib.TOOL_WIIU)
    assert _fetch() == []                                         # this version is here


def test_an_older_version_is_an_upgrade_even_without_games(tools):
    root = os.environ["ROMM_BROWSER_TOOLS"]
    os.makedirs(os.path.join(root, "wiiu-tools-2610a"))
    os.makedirs(os.path.join(root, "wiiu-tools-notaversion"))
    assert [os.path.basename(p) for p in rommlib.old_tool_dirs(rommlib.TOOL_WIIU)] == ["wiiu-tools-2610a"]
    assert _fetch() == [("wiiu-tools", "upgrade")]
    os.makedirs(os.path.join(root, "dolphin-tool-2601a"))         # RVZ off: never fetched by itself
    assert _fetch() == [("wiiu-tools", "upgrade")]
    assert ("dolphin-tool", "upgrade") in _fetch({"rvz": True})


def test_install_removes_the_old_version(isolated, tmp_path, monkeypatch):
    """The real archive (when built here) through install_tool: the version before goes."""
    from test_wua import TARBALL
    from test_rvz_overlap import _serve
    if not os.path.exists(TARBALL) or not rommlib.wua_supported():
        pytest.skip("wiiu-tools not built here")
    monkeypatch.setenv("ROMM_BROWSER_TOOLS", str(tmp_path / "tools"))
    old = os.path.join(str(tmp_path / "tools"), "wiiu-tools-2610a")
    os.makedirs(old)
    open(os.path.join(old, "wuatool"), "w").close()
    srv, url = _serve(open(TARBALL, "rb").read())
    try:
        rommlib.install_tool(rommlib.TOOL_WIIU, url=url)
    finally:
        srv.shutdown()
    assert not os.path.exists(old) and rommlib.wuatool_path()
    assert "wiiu-tools: removed wiiu-tools-2610a" in open(rommlib.LOG_PATH).read()


def _app(rb, monkeypatch):
    import pygame
    from test_review_batch import _sync
    pygame.init()
    app = rb.App(None, headless=True)
    app.headless = False                      # fetch_tools does nothing headless
    app.bg = lambda fn, done=None, err=None: _sync(fn, done, err, app)
    return app


def test_the_app_fetches_it_once_with_one_toast(tools, rb, monkeypatch):
    app = _app(rb, monkeypatch)
    os.makedirs(os.path.join(rommlib.ROMS_ROOT, "wiiu"))
    open(os.path.join(rommlib.ROMS_ROOT, "wiiu", "Disc.wud"), "w").close()
    calls = []

    def install(spec, progress=None):
        calls.append(spec.slug)
        assert app.wua_busy                    # Settings' rows show it downloading meanwhile
        return "/x"
    monkeypatch.setattr(rommlib, "install_tool", install)
    app.fetch_tools()
    assert calls == ["wiiu-tools"] and not app.wua_busy
    msgs = [t[0] for t in app.toasts]
    assert msgs == ["Getting wiiu-tools for Wii U compression…", "wiiu-tools %s is ready" % rommlib.WIIU_TOOLS["version"]]


def test_a_failure_is_said_once_per_version(tools, rb, monkeypatch):
    app = _app(rb, monkeypatch)
    os.makedirs(os.path.join(rommlib.ROMS_ROOT, "wiiu"))
    open(os.path.join(rommlib.ROMS_ROOT, "wiiu", "Disc.wud"), "w").close()
    os.makedirs(rommlib.DATA_DIR)

    def install(spec, progress=None):
        raise rommlib.RommError("Couldn't download wuatool (HTTP 404).")
    monkeypatch.setattr(rommlib, "install_tool", install)
    app.fetch_tools(systems={"wiiu"})
    app.fetch_tools()                          # tried again, quietly
    red = [t[0] for t in app.toasts if t[1] == rb.theme.RED]
    assert red == ["Couldn't get wiiu-tools (Couldn't download wuatool (HTTP 404).); Settings can try again"]
    assert open(rommlib.LOG_PATH).read().count("couldn't be fetched") == 2


def test_compress_says_what_it_waits_for(tools, rb, monkeypatch):
    from rbui.screens.compress import needs_text, needs_toast
    app = _app(rb, monkeypatch)
    cand = {"mode": "wux", "setting": "Wii U to .wux"}
    assert needs_text(app, cand) == "wiiu-tools isn't installed yet: Settings → Wii U to .wux"
    app.wua_busy = True
    assert needs_toast(app, cand) == "wiiu-tools is downloading…"
    app.wua_busy = False
    app.cfg["wux"] = False
    assert needs_toast(app, cand) == "Turn on Wii U to .wux in Settings first"
    assert needs_text(app, {"mode": "createcd", "setting": "CHD"}) == "turn on CHD in Settings"


def test_a_download_waits_for_its_tool_then_converts(isolated, rb, tmp_path, monkeypatch):
    """Wii U to .wux on, wuatool not here yet: the download still queues its .wux;
    the worker keeps the task (Conversions and Compress say why) until the app has
    fetched the tool, then starts the worker, which converts it."""
    pytest.importorskip("cryptography")
    from test_wua import KEYS, TARBALL, _idle, mkdisc
    from test_rvz_overlap import _serve
    if not os.path.exists(TARBALL) or not rommlib.wua_supported():
        pytest.skip("wiiu-tools not built here")
    monkeypatch.setenv("ROMM_BROWSER_TOOLS", str(tmp_path / "tools"))
    rb_chd = _idle(monkeypatch)
    import rb_chd_plan
    from rbui.screens.box_data import conversion_rows
    disc = os.path.join(rommlib.ROMS_ROOT, "wiiu", "Lorem Kart (USA).wud")
    os.makedirs(os.path.dirname(disc))
    mkdisc.make_disc(disc, KEYS)
    cfg = dict(rommlib.DEFAULT_CONFIG, keep_free_gb=0)
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(cfg))
    d = rommlib.Downloader(None, rommlib.Library(), cfg, persist=False)
    d.es = {"wiiu": {".wud", ".wux", ".wua"}}
    j = rommlib.Job({"id": 9}, "wiiu")
    d._maybe_chd(j, "wiiu", [disc], [disc])
    assert j.chd_task and j.chd_mode == "wux"                   # queued though wuatool isn't here
    rb_chd.main()
    rb_chd.main()
    assert os.listdir(rommlib.CHD_QUEUE_DIR) == [j.chd_task] and os.path.exists(disc)
    log = open(rommlib.LOG_PATH).read()
    assert log.count("wiiu-tools isn't installed yet") == 2 and "SKIP" not in log     # once per run
    assert not os.path.exists(rommlib.chd_results_path())       # not failed, not skipped: waiting
    assert [r["tool"] for r in conversion_rows() if r["kind"] == "queued"] == ["wiiu-tools"]
    assert rommlib.conversions_waiting_for_tools() == {"wiiu-tools": 1}
    cands = rb_chd_plan.candidates(["wiiu"], cfg)               # Compress still lists it, needing the tool
    assert [(c["mode"], c["enabled"]) for c in cands] == [("wux", False)]

    app = _app(rb, monkeypatch)
    srv, url = _serve(open(TARBALL, "rb").read())
    real = rommlib.install_tool
    monkeypatch.setattr(rommlib, "install_tool", lambda spec, progress=None: real(spec, progress, url=url))
    started = []
    monkeypatch.setattr(app, "spawn_helper", lambda script: (started.append(script), rb_chd.main()))
    try:
        app.fetch_tools({"wiiu"})
    finally:
        srv.shutdown()
    assert started == ["rb_chd.py"]
    assert os.path.exists(disc[:-4] + ".wux") and not os.path.exists(disc)
    assert os.listdir(rommlib.CHD_QUEUE_DIR) == [] and rommlib.conversions_waiting_for_tools() == {}
