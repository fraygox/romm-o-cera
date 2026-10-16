"""List + panel layout (rbui/widgets/layout.py) and Compress's size estimate."""
import json
import os

import pytest


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.bg = lambda fn, done=None, err=None: None
    return app


@pytest.mark.parametrize("width", [1216, 896])             # 1280 and 4:3 (960) less the margins
@pytest.mark.parametrize("need", [None, 0, 300, 560, 676, 900, 5000])
def test_list_and_panel_span_the_content_with_no_dead_strip(rb, width, need):
    import pygame
    content = pygame.Rect(32, 80, width, 576)
    lst, pan = rb.split(need, content)
    assert lst.left == content.left and pan.right == content.right
    assert pan.left - lst.right == rb.GAP
    assert lst.top == pan.top == content.top and lst.height == pan.height == content.height
    assert pan.width >= rb.PANEL_MIN
    assert lst.width <= content.width * rb.LIST_MAX
    assert lst.width >= min(content.width * rb.LIST_MIN, content.width - rb.GAP - rb.PANEL_MIN) - 1


def test_the_list_takes_what_its_rows_need_and_the_panel_the_rest(rb):
    w = rb.CONTENT.width
    assert rb.split(676)[0].width == 676                        # a game list: unchanged
    assert tuple(rb.split(676)[1]) == (728, 80, 520, 576)
    assert rb.split(None)[0].width == int(w * rb.LIST_MAX)      # as wide as allowed
    assert rb.split(10)[0].width == int(w * rb.LIST_MIN)        # short rows still read as a list
    narrow, wide = rb.split(600), rb.split(700)
    assert narrow[0].width < wide[0].width and narrow[1].width > wide[1].width


def test_need_width_grows_with_the_rows(app, rb):
    f = app.fonts
    short = rb.need_width(f, [("psx", "1 game, 1.0 GB", "to CHD")])
    long_ = rb.need_width(f, [("psx", "1 game, 1.0 GB", "to CHD"), ("A much longer label for a row", "", "")])
    assert short < long_ and rb.need_width(f, [("psx", "", "")], mark=True) > rb.need_width(f, [("psx", "", "")])


def test_a_row_keeps_its_status_inside_the_row_and_the_label_clear_of_it(app, rb, monkeypatch):
    import pygame
    drawn = []
    real = rb.draw.text

    def spy(surf, font, s, pos, color=None, anchor="topleft", maxw=None):
        r = real(surf, font, s, pos, color, anchor, maxw)
        drawn.append((s, r))
        return r
    rb.patch(monkeypatch, "text", spy)
    r = pygame.Rect(32, 124, 600, 52)
    rb.list_row(app.canvas, app.fonts, r, False, "A label long enough to run into the status " * 3, "sub", "to CHD")
    got = dict(drawn)
    st = got["to CHD"]
    assert st.right <= r.right - 19 and r.left < st.left
    label = next(rr for s, rr in drawn if s.startswith("A label"))
    assert label.right <= st.left - 20


def test_panel_footer_first_then_the_body_stays_above_it(app, rb):
    import pygame
    hits = []
    app.hit = lambda rect, fn: hits.append((pygame.Rect(rect), fn))
    p = rb.Panel(app.canvas, app.fonts, rb.split(None)[1], app=app)
    bottom = p.bottom
    p.actions([("A", "Games", lambda: None), ("X", "Compress all psx", None)])
    assert p.bottom < bottom and len(hits) == 1 and hits[0][0].bottom <= bottom
    p.title("psx", "2 games, 1.0 GB")
    for _ in range(40):
        p.fact("Size", "1.0 GB")
    assert p.y <= p.bottom + 42


def test_hint_actions_are_the_screens_own_buttons(app, rb):
    class S:
        acted = []

        def hints(self):
            return [("A", "Games"), ("X", "Compress all"), ("L", "Page up"), ("B", "Back")]

        def action(self, a):
            self.acted.append(a)
    s = S()
    got = rb.hint_actions(s)
    assert [(t, lab) for t, lab, _ in got] == [("A", "Games"), ("X", "Compress all")]
    for _, _, fn in got:
        fn()
    assert s.acted == ["ok", "x"]


# ---------------------------------------------------------------- Compress: now and after

def _result(mode, msg=None, result="ok", **kw):
    return dict({"result": result, "mode": mode, "message": msg or ""}, **kw)


def test_ratios_come_from_this_boxs_finished_conversions(rb):
    rl = rb.rommlib
    res = [_result("createcd", "OK a.cue -> a.chd  (400.0 MB -> 200.0 MB)"),
           _result("createcd", in_size=600 << 20, out_size=360 << 20),
           _result("rvz", "OK b.iso -> b.rvz (1.0 GB -> 0.25 GB)"),
           _result("rvz", "Misplaced.iso: not a disc", result="failed"),
           _result("createdvd", "OK c.iso -> c.chd")]                 # says no sizes: left out
    got = rl.conversion_ratios(res)
    assert set(got) == {"createcd", "rvz"}
    assert got["createcd"] == pytest.approx(560 / 1000)
    assert got["rvz"] == pytest.approx(0.25)


def test_estimate_uses_this_box_else_the_typical_ratio(rb):
    rl = rb.rommlib
    cands = [{"mode": "createcd", "size": 1000}, {"mode": "rvz", "size": 1000}]
    after, measured = rl.estimate_after(cands, {"createcd": 0.5, "rvz": 0.4})
    assert (after, measured) == (900, True)
    after, measured = rl.estimate_after(cands, {"createcd": 0.5})
    assert after == 500 + int(1000 * rl.TYPICAL_RATIO["rvz"]) and measured is False
    assert rl.estimate_after([], {}) == (0, False)


def test_the_worker_records_the_sizes_it_ended_with(rb, isolated, monkeypatch):
    import rb_chd
    os.makedirs(os.path.dirname(rb.rommlib.chd_results_path()), exist_ok=True)
    monkeypatch.setitem(rb_chd._state, "last", "OK a -> b")
    monkeypatch.setitem(rb_chd._state, "sizes", (1000, 600))
    rb_chd.record("1.json", {"mode": "createcd", "sources": ["a.cue"]}, "ok")
    rb_chd.record("2.json", {"mode": "createcd", "sources": ["b.cue"]}, "failed")
    lines = [json.loads(l) for l in open(rb.rommlib.chd_results_path())]
    assert (lines[0]["in_size"], lines[0]["out_size"]) == (1000, 600)
    assert "in_size" not in lines[1]
    assert rb.rommlib.conversion_ratios(lines) == {"createcd": 0.6}


def test_compress_panel_says_now_and_after(app, rb, monkeypatch):
    said = []
    real = rb.layout.text
    monkeypatch.setattr(rb.layout, "text", lambda surf, font, s, *a, **k: (said.append(s), real(surf, font, s, *a, **k))[1])
    scr = rb.CompressScreen(app)
    app.push(scr)
    cands = [{"system": "psx", "root": "/r/psx/G.cue", "files": ["/r/psx/G.cue"], "name": "G", "mode": "createcd",
              "label": "CHD", "size": 1000 << 20, "enabled": True, "setting": "x", "xiso": False, "cut": 0}]
    scr._loaded(cands, [], {})
    scr.draw(app.canvas)
    assert "After, about" in said and "1000.0 MB" in said
    said.clear()
    scr._loaded(cands, [], {"createcd": 0.5})
    scr.draw(app.canvas)
    assert "After" in said and "500.0 MB, saves 500.0 MB" in said


def test_a_row_tag_sits_left_of_the_status(app, rb, monkeypatch):
    import pygame
    tags = []
    real = rb.chips.tag

    def spy(c, font, label, pos, anchor="topleft", warn=False, color=None):
        r = real(c, font, label, pos, anchor, warn, color)
        tags.append((label, r, color))
        return r
    monkeypatch.setattr(rb.layout, "draw_tag", spy)
    r = pygame.Rect(32, 124, 600, 52)
    rb.list_row(app.canvas, app.fonts, r, True, "Dolor Dash (USA).chd", "Smaller than RomM's copy", "2.1 GB",
                tag="psx", tag_color=rb.GREEN)
    (label, tr, col), = tags
    assert label == "psx" and col == rb.GREEN and r.left + 200 < tr.left and tr.right < r.right - 20 - 40


def test_panel_facts_fill_columns_and_move_down(app, rb):
    p = rb.Panel(app.canvas, app.fonts, rb.split(None)[1], app=app)
    y = p.y
    p.facts([("Size", "1.0 GB"), ("Format", "CHD"), ("Region", "USA")], cols=2)
    assert p.y == y + 2 * 42 + 6
    p.fact("Where", "")                                     # nothing to say: nothing drawn
    assert p.y == y + 2 * 42 + 6


def test_a_row_with_an_aside_puts_its_status_on_the_label_line_and_a_bar_under_it(app, rb, monkeypatch):
    """aside: the status goes up beside the label, a progress bar under it, and the label and
    grey line stop short of that column; mark_w makes room for a cover thumb."""
    import pygame
    drawn, bars, marks = [], [], []
    real = rb.draw.text

    def spy(surf, font, s, pos, color=None, anchor="topleft", maxw=None):
        r = real(surf, font, s, pos, color, anchor, maxw)
        drawn.append((s, r))
        return r
    rb.patch(monkeypatch, "text", spy)
    monkeypatch.setattr(rb.layout, "progress_bar", lambda c, rect, frac, color=None: bars.append((rect, frac, color)))
    r = pygame.Rect(32, 216, 700, 58)
    tx = rb.list_row(app.canvas, app.fonts, r, False, "A label long enough to reach the bar " * 3, "roms/psx",
                     "38.0 MB/s", rb.BLUE, aside=(0.6, rb.BLUE), mark=lambda ctr: marks.append(ctr) or True,
                     mark_w=62, box=rb.PANEL)
    st = dict(drawn)["38.0 MB/s"]
    (bx, by, bw, bh), frac, col = bars[0]
    assert tx == r.left + 62 and marks == [(r.left + 29, r.centery)]
    assert st.right <= r.right - 19 and st.bottom <= by and frac == 0.6 and col == rb.BLUE
    assert bx + bw == r.right - 20 and by + bh <= r.bottom
    label = next(rr for s, rr in drawn if s.startswith("A label"))
    assert label.right <= bx - 20


def test_panel_cards_stack_down_and_footer_cards_sit_at_the_bottom(app, rb):
    import pygame
    hits = []
    app.hit = lambda rect, fn: hits.append(pygame.Rect(rect))
    pan = rb.split(None)[1]
    p = rb.Panel(app.canvas, app.fonts, pan, app=app)
    foot, _ = p.card("Compress games…", "Pick games", bottom=True, chevron=True, tap=lambda: None)
    assert foot.bottom == pan.bottom - p.PAD and p.bottom == foot.top - 12
    p.head("Compressing", "pauses while you play")
    a, room = p.card("Lorem (USA)", "to CHD", on=True, h=62, more=52, tap=lambda: None)
    b, _ = p.card("Ipsum (USA)", "to RVZ", tap=lambda: None)
    assert a.bottom + 8 == b.top and room.top == a.top + 62 and room.height == 52
    assert hits == [foot, a, b] and a.left == b.left == foot.left and a.width == pan.width - 2 * p.PAD + 24


def test_breadcrumbs_never_run_into_the_queue_status(app, rb, monkeypatch):
    """A deep trail with long names while downloads run: the crumbs stop short of
    "5 in queue, …" (earliest ones give way to "…"), and the current screen's stays."""
    drawn = []
    real = rb.draw.text

    def spy(surf, font, s, pos, color=None, anchor="topleft", maxw=None):
        r = real(surf, font, s, pos, color, anchor, maxw)
        drawn.append((s, r))
        return r
    rb.patch(monkeypatch, "text", spy)

    class Deep(rb.Screen):
        def crumbs(self):
            return ["Platforms", "Nintendo Entertainment System", "A Rather Long Game Title (USA) (Rev 2)"]

        def draw(self, c):
            pass
    app.stack.append(Deep(app))
    monkeypatch.setattr(app.downloader, "active_count", lambda: 5)
    app.draw()
    queue = next(r for s, r in drawn if s.startswith("5 in queue"))
    names = Deep(app).crumbs()
    crumbs = [(s, r) for s, r in drawn if r.top < 64 and (s == "…" or any(n.startswith(s.rstrip("…")) for n in names))]
    assert len(crumbs) == 3 and all(r.right <= queue.left - 12 for _, r in crumbs)
    assert crumbs[-1][0].startswith("A Rather")               # the current screen's name keeps its place

