"""Memory stays bounded however long you browse: rendered text and decoded
covers are kept by size (not count), covers are shrunk to what's drawn, and
whole-platform listings keep only the games on the box."""
import os
import time

import pytest

import rommlib


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    a = rb.App(None, headless=True)
    a.cover_loader.WORKERS = 0
    return a


def _run(app):
    while app.cover_loader.run_one(wait=False):
        pass
    for fn in app.events_from_threads:
        fn()
    app.events_from_threads.clear()


def test_text_is_kept_by_size_and_the_newest_stays(app, rb, monkeypatch):
    rb._TEXT_CACHE.clear()
    rb._TEXT_BYTES[0] = 0
    monkeypatch.setattr(rb, "TEXT_BUDGET", 300 << 10)
    f = app.fonts.body
    first = rb.render_cached(f, "kept on screen", (255, 255, 255))
    for i in range(800):
        rb.render_cached(f, "Lorem ipsum dolor %d" % i, (255, 255, 255))
        rb.render_cached(f, "kept on screen", (255, 255, 255))       # drawn every frame: never dropped
    assert rb._TEXT_BYTES[0] <= rb.TEXT_BUDGET
    assert rb._TEXT_BYTES[0] == sum(rb.surface_bytes(s) for s in rb._TEXT_CACHE.values())
    assert rb.render_cached(f, "kept on screen", (255, 255, 255)) is first
    assert len(rb._TEXT_CACHE) < 800


def test_a_cover_is_kept_at_the_size_it_is_drawn(app, rb):
    """Shrunk from RomM's large covers, enlarged once from small ones: the
    game panel then draws it as it is, with no second copy."""
    import pygame
    pw, ph = rb.SR(pygame.Rect((0, 0), rb.COVER_BOX)).size
    for size in ((3000, 4000), (100, 130), (800, 300)):
        w, h = rb.cover_fit(pygame.Surface(size, pygame.SRCALPHA, 32)).get_size()
        assert w <= pw and h <= ph and (w == pw or h == ph), size
    fitted = pygame.Surface((pw, ph), pygame.SRCALPHA, 32)
    assert rb.cover_fit(fitted) is fitted


def test_covers_stay_in_budget_and_what_is_drawn_stays(app, rb, monkeypatch):
    import pygame
    path = os.path.join(rommlib.DATA_DIR, "c.png")
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    pygame.image.save(pygame.Surface((150, 200)), path)
    app.api.fetch_cover = lambda rom, large=False: path
    one = 150 * 200 * 4
    monkeypatch.setattr(rb, "COVER_BUDGET", 5 * one)
    for rid in range(40):
        app.cover({"id": rid})
        app.cover({"id": 0})                                   # on screen the whole time
        for r in range(rid + 1):
            app.cover_loader.seen[r] = time.monotonic()
        _run(app)
        app.cover({"id": 0})
    assert app.cover_bytes <= rb.COVER_BUDGET
    assert app.cover_bytes == sum(rb.surface_bytes(v) for v in app.covers.values())
    assert isinstance(app.covers.get(0), pygame.Surface)        # the one still being drawn
    assert set(app.covers) == set(app.cover_order)
    app.forget_covers()
    assert app.cover_bytes == 0


def test_a_listing_keeps_only_what_is_wanted_page_by_page(isolated):
    class Api(rommlib.Api):
        def __init__(self, total):
            super().__init__("http://x", "t")
            self.total, self.calls = total, []

        def roms(self, offset=0, limit=60, **kw):
            self.calls.append(offset)
            n = max(0, min(limit, self.total - offset))
            return {"total": self.total, "items": [{"id": offset + i, "fs_name": "g%d.zip" % (offset + i)}
                                                   for i in range(n)]}
    api = Api(2300)
    got = api.roms_all(limit=500, keep=lambda r: r["id"] % 100 == 0)
    assert [r["id"] for r in got] == list(range(0, 2300, 100))
    assert sorted(api.calls) == [0, 500, 1000, 1500, 2000]      # every page read, none twice
    assert len(api.roms_all(limit=500)) == 2300                 # no filter: everything, as before


def test_check_games_keeps_only_the_games_on_the_box(isolated, monkeypatch):
    """A platform's whole listing (thousands of 16 KB records) was kept for
    a check of the few games here."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "nes")
    os.makedirs(sysdir)
    open(os.path.join(sysdir, "Lorem (USA).zip"), "wb").close()
    kept = []

    class Api:
        def roms_all(self, keep=None, **kw):
            listed = [{"id": i, "fs_name": "Game %d.zip" % i, "fs_name_no_ext": "Game %d" % i} for i in range(3000)]
            listed.append({"id": 9999, "fs_name": "Lorem (USA).zip", "fs_name_no_ext": "Lorem (USA)"})
            out = [r for r in listed if keep is None or keep(r)]
            kept.append(len(out))
            return out

        def rom(self, rid):
            return {"id": rid, "files": []}
    rommlib.audit(Api(), {"nes": [1]})
    assert kept == [1]


def test_a_small_box_keeps_half_as_much(rb, monkeypatch):
    monkeypatch.setattr(rb, "TEXT_BUDGET", 64 << 20)
    monkeypatch.setattr(rb, "COVER_BUDGET", 64 << 20)
    monkeypatch.setattr(rommlib, "total_ram", lambda: 8 << 30)
    rb.fit_memory_budgets()
    assert rb.TEXT_BUDGET == rb.COVER_BUDGET == 64 << 20
    monkeypatch.setattr(rommlib, "total_ram", lambda: 2 << 30)
    rb.fit_memory_budgets()
    assert rb.TEXT_BUDGET == rb.COVER_BUDGET == 32 << 20
