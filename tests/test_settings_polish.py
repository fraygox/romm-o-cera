"""Settings: Low-memory mode back to Auto, the daily update check, the Auto
theme that follows the controller, and the documentation's QR code (romm/qr.py),
checked by a decoder written here from the standard, not from the encoder."""
import pytest

import rommlib

# ------------------------------------------------------------------ QR codes

EC = {  # version 1-6: (EC codewords per block, blocks), per level; total codewords (ISO/IEC 18004 table 9)
    "L": ((7, 1), (10, 1), (15, 1), (20, 1), (26, 1), (18, 2)),
    "M": ((10, 1), (16, 1), (26, 1), (18, 2), (24, 2), (16, 4)),
    "Q": ((13, 1), (22, 1), (18, 2), (26, 2), (18, 4), (24, 4)),
    "H": ((17, 1), (28, 1), (22, 2), (16, 4), (22, 4), (28, 4)),
}
TOTAL = (26, 44, 70, 100, 134, 172)
LEVEL = {1: "L", 0: "M", 3: "Q", 2: "H"}


def _gf_tables():
    exp, log, x = [0] * 512, [0] * 256, 1
    for i in range(255):
        exp[i], log[x] = x, i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


EXP, LOG = _gf_tables()


def _syndromes_zero(block):
    """A Reed-Solomon codeword (data + EC) evaluates to 0 at alpha^0 .. alpha^(n-1)."""
    nec = len(block) - block.ndata
    for i in range(nec):
        s = 0
        for c in block:
            s = (EXP[LOG[s] + i] if s else 0) ^ c
        if s:
            return False
    return True


class _Block(list):
    ndata = 0


def _format_word(level_bits, mask):
    d = level_bits << 3 | mask
    g, v = 0b10100110111, d << 10
    for i in range(14, 9, -1):
        if v >> i & 1:
            v ^= g << (i - 10)
    return (d << 10 | v) ^ 0b101010000010010


def decode(m):
    """The text in a QR matrix (versions 1-6, byte mode), checking everything on the way."""
    n = len(m)
    version = (n - 17) // 4
    assert n == 17 + 4 * version and 1 <= version <= 6 and all(len(r) == n for r in m)
    for cx, cy in ((0, 0), (n - 7, 0), (0, n - 7)):                 # finder patterns
        for y in range(7):
            for x in range(7):
                ring = max(abs(x - 3), abs(y - 3))
                assert m[cy + y][cx + x] == (ring != 2), (cx, cy, x, y)
    for i in range(8, n - 8):                                       # timing
        assert m[6][i] == m[i][6] == (i % 2 == 0)
    assert m[n - 8][8]                                              # the dark module
    # format: both copies, BCH-valid
    a = [m[i][8] for i in range(6)] + [m[7][8], m[8][8], m[8][7]] + [m[8][14 - i] for i in range(9, 15)]
    b = [m[8][n - 1 - i] for i in range(8)] + [m[n - 15 + i][8] for i in range(8, 15)]
    assert a == b
    word = sum(1 << i for i, v in enumerate(a) if v)
    found = [(lv, mk) for lv in range(4) for mk in range(8) if _format_word(lv, mk) == word]
    assert len(found) == 1
    level, mask = LEVEL[found[0][0]], found[0][1]
    # what isn't data
    fn = [[False] * n for _ in range(n)]
    for cx, cy in ((0, 0), (n - 8, 0), (0, n - 8)):
        for y in range(8):
            for x in range(8):
                fn[cy + y][cx + x] = True
    for i in range(n):
        fn[6][i] = fn[i][6] = True
    for i in range(9):
        fn[8][i] = fn[i][8] = True
    for i in range(8):
        fn[8][n - 1 - i] = fn[n - 1 - i][8] = True
    if version > 1:
        for y in range(n - 9, n - 4):
            for x in range(n - 9, n - 4):
                fn[y][x] = True
                ring = max(abs(x - (n - 7)), abs(y - (n - 7)))
                assert m[y][x] == (ring != 1)
    cond = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
            lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
            lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0][mask]
    bits, col, upward = [], n - 1, True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(n - 1, -1, -1) if upward else range(n)
        for y in rows:
            for x in (col, col - 1):
                if not fn[y][x]:
                    bits.append(int(m[y][x] ^ cond(x, y)))
        upward = not upward
        col -= 2
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, TOTAL[version - 1] * 8, 8)]
    nec, nb = EC[level][version - 1]
    ndata = TOTAL[version - 1] - nec * nb
    sizes = [ndata // nb + (i >= nb - ndata % nb) for i in range(nb)]
    blocks = [_Block() for _ in range(nb)]
    k = 0
    for i in range(max(sizes)):
        for j in range(nb):
            if i < sizes[j]:
                blocks[j].append(words[k])
                k += 1
    for i in range(nec):
        for j in range(nb):
            blocks[j].append(words[k])
            k += 1
    data = []
    for blk, size in zip(blocks, sizes):
        blk.ndata = size
        assert _syndromes_zero(blk), "Reed-Solomon check failed"
        data += blk[:size]
    stream = "".join(format(w, "08b") for w in data)
    assert stream[:4] == "0100"                                     # byte mode
    count = int(stream[4:12], 2)
    out = bytes(int(stream[12 + 8 * i:20 + 8 * i], 2) for i in range(count))
    return out.decode("utf-8"), version, level


# the docs site's code, decoded by OpenCV from the 720p and 4K screenshots
DOCS_QR = (
    "#######.##.####...#.#.#######",
    "#.....#.#.#..##..#.##.#.....#",
    "#.###.#.####.##.#.#...#.###.#",
    "#.###.#..##..##..#..#.#.###.#",
    "#.###.#.###.###.#.##..#.###.#",
    "#.....#...##.##..##.#.#.....#",
    "#######.#.#.#.#.#.#.#.#######",
    ".............#..#..#.........",
    "#..######...##.###.###..#.###",
    ".##.#...#.#.####.###...##.##.",
    "#.#.###...#.##.##...#...#.#..",
    "#..#.#..#....#.###...#.#.#..#",
    "#..####.#.#.##.##.#.#.#.....#",
    "...###.....########...#######",
    "..#.#####.##.#.#.#.#...##.#.#",
    "#..###..####.........#..#.#.#",
    ".##...#.##....#........#.#...",
    "##.###..#.########.###..#.##.",
    "##...####.#.#..#..##.#.#.#..#",
    "##.#.#.#...#..###.###...###..",
    "###.#.#.####.##.##..########.",
    "........#..##.##.##.#...##...",
    "#######.###...###..##.#.##...",
    "#.....#.##.##.#..#.##...#....",
    "#.###.#.#...###.#..#######..#",
    "#.###.#.##...#.#.##....#....#",
    "#.###.#..#####.#.....#.##.###",
    "#.....#...##..#......#...##.#",
    "#######.#..........####.#....",
)


def test_the_docs_code_decodes_to_the_docs_site_and_is_stable():
    m = rommlib.qr_matrix(rommlib.DOCS_URL)
    assert decode(m) == ("https://fraygox.github.io/romm-o-cera/", 3, "M")
    assert ["".join("#" if v else "." for v in r) for r in m] == list(DOCS_QR)


@pytest.mark.parametrize("text", ["A", "hello, world", "x" * 50, "ünïcödé ✓", "y" * 106,
                                  "https://fraygox.github.io/romm-o-cera/settings/#config-json"])
def test_every_size_level_and_mask_decodes(text):
    for level in "LMQH":
        try:
            rommlib.qr_matrix(text, level)
        except ValueError:
            continue
        for mask in range(8):
            got, version, lv = decode(rommlib.qr_matrix(text, level, mask))
            assert got == text
            assert "LMQH".index(lv) >= "LMQH".index(level)          # raised only when it fits the same size


def test_known_vectors_from_the_standard():
    # "HELLO WORLD", 1-M: data codewords and their EC codewords (the worked example)
    data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
    assert rommlib._qr_ec_bytes(data, 10) == [196, 35, 39, 119, 235, 215, 231, 226, 93, 23]
    # format strings (ISO/IEC 18004 Annex C), mask 0
    assert rommlib._qr_format_bits("L", 0) == 0b111011111000100
    assert rommlib._qr_format_bits("M", 0) == 0b101010000010010
    assert rommlib._qr_format_bits("Q", 0) == 0b011010101011111
    assert rommlib._qr_format_bits("H", 0) == 0b001011010001001
    assert rommlib.qr_capacity(1, "L") == 17 and rommlib.qr_capacity(6, "M") == 106
    with pytest.raises(ValueError):
        rommlib.qr_matrix("z" * 107)


@pytest.mark.parametrize("k", [1.0, 1.5, 3.0])
def test_the_code_is_drawn_crisp_with_a_quiet_zone(rb, monkeypatch, k):
    import pygame
    monkeypatch.setattr(rb.theme, "K", k)
    c = pygame.Surface((rb.S(rb.W), rb.S(rb.H)))
    c.fill((40, 30, 60))
    m = rb.docs_qr()
    box = rb.draw_qr(c, m, pygame.Rect(100, 100, 300, 250))
    n = len(m) + 8
    px = box.width // n
    assert box.width == box.height == px * n and px >= 6 * k - 1     # whole pixels per module, big enough
    assert pygame.Rect(rb.SR((100, 100, 300, 250))).contains(box)
    for y in range(n):
        for x in range(n):
            dark = 4 <= x < n - 4 and 4 <= y < n - 4 and m[y - 4][x - 4]
            want = (0, 0, 0) if dark else (255, 255, 255)
            if x in (0, n - 1) and y in (0, n - 1):
                continue                                              # rounded tile corners
            for dx, dy in ((0, 0), (px - 1, px - 1)):                 # each module fills its square
                assert tuple(c.get_at((box.left + x * px + dx, box.top + y * px + dy)))[:3] == want


# ------------------------------------------------------------------ Settings

@pytest.fixture
def ui(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    names = ["Xbox Wireless Controller"]
    app.input.names = lambda: list(names)
    st = rb.SettingsScreen(app)
    app.push(st)
    yield app, st, names
    rb.apply_theme("default")


def _at(st, key):
    st.sel = [it[0] for it in st.items()].index(key)


def _value(st, key):
    return next(it[2] for it in st.items() if it[0] == key)


def test_low_memory_mode_cycles_auto_on_off(ui, monkeypatch):
    app, st, _ = ui
    monkeypatch.setattr(rommlib, "total_ram", lambda: 2 << 30)
    _at(st, "chdlowmem")
    assert app.cfg["chd_low_memory"] == "auto" and _value(st, "chdlowmem") == "Auto (On, 2.0 GB RAM)"
    seen = []
    for _ in range(3):
        st.action("right")
        seen.append((app.cfg["chd_low_memory"], _value(st, "chdlowmem"), rommlib.load_config()["chd_low_memory"]))
    assert seen == [(True, "On", True), (False, "Off", False), ("auto", "Auto (On, 2.0 GB RAM)", "auto")]
    st.action("left")
    assert app.cfg["chd_low_memory"] is False
    monkeypatch.setattr(rommlib, "total_ram", lambda: 0)
    app.cfg["chd_low_memory"] = "auto"
    assert _value(st, "chdlowmem").startswith("Auto (")


def test_the_daily_update_check_has_a_row_and_updates_still_checks_on_demand(ui, monkeypatch):
    app, st, _ = ui
    asked = []
    monkeypatch.setattr(rommlib, "newer_release", lambda cfg, force=False: asked.append(
        (cfg["check_updates"], force)) or None)
    app.bg = lambda fn, done=None, err=None: done and done(fn())
    keys = [it[0] for it in st.items()]
    assert keys.index("channel") < keys.index("checkupdates") < keys.index("update")
    _at(st, "checkupdates")
    assert _value(st, "checkupdates") == "On"
    st.action("ok")
    assert app.cfg["check_updates"] is False and rommlib.load_config()["check_updates"] is False
    assert _value(st, "checkupdates") == "Off"
    _at(st, "update")
    st.action("ok")
    assert asked == [(True, True)]                    # the button asks GitHub even with the daily check off


def test_theme_for_follows_the_controller_only_when_asked(rb):
    t = rb.theme_for
    ps = ["Sony Interactive Entertainment Wireless Controller"]
    assert t("default", False, "auto", ps) == "default"                         # off (the default)
    assert t("default", True, "auto", ps) == "playstation"
    assert t("default_oled", True, "auto", ["DualSense Wireless Controller"]) == "playstation_oled"
    assert t("xbox", True, "auto", ["Nintendo Switch Pro Controller"]) == "nintendo"
    assert t("default", True, "auto", ["Microsoft X-Box 360 pad"]) == "xbox"
    assert t("nintendo_oled", True, "auto", []) == "nintendo_oled"              # keyboard: your Theme
    assert t("default", True, "auto", ["Generic USB Gamepad"]) == "default"     # unknown pad: your Theme
    assert t("default", True, "nintendo", ["Generic USB Gamepad"]) == "nintendo"   # labels set by hand
    assert t("high_contrast", True, "auto", ps) == "high_contrast"             # never replaced


def test_match_theme_to_controller_is_its_own_switch(ui, rb):
    app, st, names = ui
    assert app.cfg["theme_auto"] is False and app.cfg["theme"] == "default"
    _at(st, "theme")
    st.action("left")                                               # the Theme row lists themes only
    assert app.cfg["theme"] == list(rb.THEMES)[-1]
    st.action("right")
    assert app.cfg["theme"] == "default"
    names[:] = ["DualSense Wireless Controller"]
    app.dispatch("padchange")
    assert rb.ACCENT == rb._DEFAULT_PALETTE["ACCENT"]                # off: a pad changes nothing
    _at(st, "themeauto")
    st.action("ok")
    assert app.cfg["theme_auto"] is True and rommlib.load_config()["theme_auto"] is True
    assert rb.ACCENT == rb.THEMES["playstation"][1]["ACCENT"]
    assert _value(st, "theme") == "Default (now PlayStation)"
    names[:] = []                                                   # the pad goes: your Theme again
    app.dispatch("padchange")
    assert rb.ACCENT == rb._DEFAULT_PALETTE["ACCENT"] and _value(st, "theme") == "Default"
    _at(st, "themeauto")
    st.action("ok")
    names[:] = ["Xbox Series X Controller"]
    app.dispatch("padchange")
    assert app.cfg["theme_auto"] is False and rb.ACCENT == rb._DEFAULT_PALETTE["ACCENT"]


def test_matched_theme_applies_at_start(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    pygame.init()
    rommlib.save_config(dict(rommlib.DEFAULT_CONFIG, theme="default_oled", theme_auto=True, button_style="nintendo"))
    try:
        rb.App(None, headless=True)
        assert rb.BG == (0, 0, 0) and rb.ACCENT == rb.THEMES["nintendo_oled"][1]["ACCENT"]
    finally:
        rb.apply_theme("default")


def test_documentation_row_shows_the_code_and_opens_it_big(ui, rb):
    app, st, _ = ui
    _at(st, "docs")
    assert st.hints()[0] == ("A", "Open")
    st.draw(app.canvas)
    st.action("ok")
    assert isinstance(app.stack[-1], rb.DocsScreen)
    assert app.stack[-1].crumbs() == ["Settings", "Documentation"]
    app.stack[-1].draw(app.canvas)
    app.stack[-1].action("back")
    assert app.stack[-1] is st


def test_turning_the_daily_check_back_on_checks_now(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.bg = lambda fn, done=None, err=None: done(fn()) if done else fn()
    asked = []
    monkeypatch.setattr(rommlib, "newer_release", lambda cfg, force=False: asked.append(force) or "v9.9.9")
    scr = rb.SettingsScreen(app)
    app.cfg["check_updates"] = True
    scr.EFFECTS["check_updates"](scr, app)
    assert asked == [True] and app.update_tag == "v9.9.9"
    app.cfg["check_updates"] = False
    scr.EFFECTS["check_updates"](scr, app)
    assert app.update_tag is None and asked == [True]
