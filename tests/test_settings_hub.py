"""Settings' navigation: a hub of sections, each on its own screen with the
rows it always had; L/R go between sections, B back to the hub, Start Home."""
import pytest

import rommlib


@pytest.fixture
def ui(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.input.names = lambda: ["Xbox Wireless Controller"]
    app.push(rb.HomeScreen(app))
    st = rb.SettingsScreen(app)
    app.push(st)
    yield app, st
    rb.apply_theme("default")


def keys(st):
    return [it[0] for it in st.items()]


TITLES = ["RomM server", "Controller", "Downloads", "Metadata", "Disc compression", "This app", "Updates"]


def test_settings_opens_on_the_hub_with_a_row_a_section(ui, rb):
    app, st = ui
    assert st.current() is None and st.crumbs() == ["Settings"]
    assert [t for _, t, _ in st.sections()] == TITLES
    assert st.selectable() == [h for h, _, _ in st.sections()]
    assert st.hints() == [("A", "Open"), ("B", "Back")]
    assert set(st.SECTIONS) == set(TITLES)
    for h, title, rows in st.sections():                       # every row is in exactly one section
        assert rows and all(st.items()[i][0] != "§" for i in rows)
    assert sum(len(rows) for _, _, rows in st.sections()) == len([k for k in keys(st) if k != "§"])


def test_a_opens_the_section_and_b_comes_back_to_its_row(ui, rb):
    app, st = ui
    st.action("down")
    st.action("down")
    assert st.items()[st.sel][1] == "Downloads"
    st.action("ok")
    assert st.current()[1] == "Downloads" and keys(st)[st.sel] == "hours"
    assert st.crumbs() == ["Settings", "Downloads"] and st.selectable() == st.current()[2]
    assert ("L R", "Section") in st.hints() and ("A", "Change") in st.hints()
    st.action("down")
    assert keys(st)[st.sel] == "keepfree"
    st.action("back")
    assert st.current() is None and st.items()[st.sel][1] == "Downloads" and len(app.stack) == 2
    st.action("back")
    assert len(app.stack) == 1                                 # the hub's Back leaves Settings


def test_up_and_down_wrap_within_the_section_and_the_hub(ui, rb):
    app, st = ui
    st.action("up")
    assert st.items()[st.sel][1] == "Updates"
    st.action("ok")
    assert keys(st)[st.sel] == "channel"
    rows = st.current()[2]
    st.action("up")
    assert st.sel == rows[-1]
    st.action("down")
    assert st.sel == rows[0]


def test_shoulders_go_round_the_sections(ui, rb):
    app, st = ui
    st.action("ok")                                            # RomM server
    seen = [st.current()[1]]
    for _ in range(len(TITLES)):
        st.action("r1")
        seen.append(st.current()[1])
    assert seen == TITLES + ["RomM server"]
    assert keys(st)[st.sel] == "url" and st.scroll == 0
    st.action("l1")
    assert st.current()[1] == "Updates" and keys(st)[st.sel] == "channel"
    assert st.nav_key() == st.current()[0]
    st.action("back")
    st.action("r1")                                            # on the hub: nothing to page
    assert st.current() is None


def test_a_row_reached_directly_shows_its_section(ui, rb):
    """Tests and the screenshot harness put sel on a row: that row's section is open."""
    app, st = ui
    st.sel = keys(st).index("chd")
    assert st.current()[1] == "Disc compression" and st.crumbs() == ["Settings", "Disc compression"]
    app.draw()
    assert keys(st)[st.sel] == "chd"
    st.sel = 10 ** 6
    assert st.current() is not None and st.sel == len(st.items()) - 1


def test_start_goes_home_from_a_section(ui, rb):
    app, st = ui
    st.action("ok")
    app.dispatch("start")
    assert len(app.stack) == 1 and isinstance(app.stack[0], rb.HomeScreen)


def test_the_hub_says_each_sections_state(ui, rb):
    app, st = ui
    app.cfg.update(url="https://romm.lan", download_hours="22-6", es_image="boxart", theme="xbox")
    rb.apply_theme("xbox")
    by = {t: st.headline(t, rows) for _, t, rows in st.sections()}
    assert by["RomM server"] == "https://romm.lan" and by["Downloads"] == "10 pm – 6 am"
    assert by["Metadata"] == "Box art" and by["This app"] == "Xbox" and by["Controller"].startswith("Auto")
    assert by["Updates"] == "v" + rommlib.VERSION_LABEL
    app.update_tag = "v0.10.0+n20261013.2002.c7cb0e9"
    assert st.headline("Updates", []) == "Update available"
    on, n = by["Disc compression"].split(" of ")
    assert int(on) <= int(n.split()[0])


def test_the_hub_panel_lists_the_sections_values(ui, rb, monkeypatch):
    app, st = ui
    drawn = []
    rb.patch(monkeypatch, "text", lambda c, f, s, pos, *a, **k: drawn.append(s) or rb.pygame.Rect(0, 0, 10, 10))
    st.sel = keys(st).index("§", 1)                            # the second header: Controller
    app.draw()
    assert "Controller" in drawn and "Button labels" in drawn and "Confirm button" in drawn
    assert "SETTINGS" in drawn and not any(s.startswith("Now:") for s in drawn)
    drawn.clear()
    st.sel = [i for i, k in enumerate(keys(st)) if k == "§"][TITLES.index("Disc compression")]
    app.draw()
    assert any(s.startswith("and ") and s.endswith("more…") for s in drawn)    # more rows than fit: said


def test_the_hub_rows_open_on_tap_and_section_rows_still_act(ui, rb):
    app, st = ui
    app.draw()
    rows = sorted((r for r, fn in app.targets if r.left == 48 and r.width == 700), key=lambda r: r.top)
    assert len(rows) == len(TITLES)
    app.tap(rows[3].center)
    assert st.current()[1] == "Metadata" and keys(st)[st.sel] == "esimage"
    app.draw()
    rows = sorted((r for r, fn in app.targets if r.left == 48 and r.width == 700), key=lambda r: r.top)
    assert len(rows) == len(st.current()[2])
    app.tap(rows[1].center)                                    # Videos: cycles its choice
    assert keys(st)[st.sel] == "esvideo" and app.cfg["es_video_mb"] == 100


def test_fill_in_game_info_opens_from_metadata(ui, rb, monkeypatch):
    app, st = ui
    st.sel = keys(st).index("fillinfo")
    assert st.hints()[0] == ("A", "Open")
    app.platforms = [{"id": 2, "_system": "nes"}]
    started = []
    monkeypatch.setattr(rb.InfoDialog, "start", classmethod(lambda cls, a, systems: started.append(systems)))
    st.action("ok")
    assert started == [[("nes", [2])]]


def test_the_metadata_rows_cycle_their_choices(ui, rb):
    app, st = ui
    st.sel = keys(st).index("esimage")
    st.action("right")
    assert app.cfg["es_image"] == "boxart" and st.items()[st.sel][2] == "Box art"
    st.action("right")
    assert app.cfg["es_image"] == "screenshot"
    st.sel = keys(st).index("esvideo")
    assert st.items()[st.sel][2] == "Up to 50 MB"
    st.action("left")
    assert app.cfg["es_video_mb"] == 25
    st.action("left")
    assert app.cfg["es_video_mb"] == 0 and st.items()[st.sel][2] == "Off"
    for key, ck in (("esextra", "es_extra_art"), ("estags", "es_tags")):
        st.sel = keys(st).index(key)
        st.action("ok")
        assert app.cfg[ck] is False and st.items()[st.sel][2] == "Off"
    assert rommlib.load_config()["es_video_mb"] == 0                      # saved


def test_the_l_r_hint_token_draws_in_every_style(ui, rb):
    app, st = ui
    for style in ("xbox", "playstation", "nintendo"):
        rb.pads.PAD_STYLE["style"] = style
        assert rb.pads.pad_face("L R")[1] == rb.pads.OTHER[style]["L R"]
    rb.pads.PAD_STYLE["style"] = "xbox"
    assert rb.pads.TOKEN_ACTION["L R"] == "r1" and rb.pads.TOKEN_BACK["L R"] == "l1"
