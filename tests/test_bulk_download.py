"""Downloading any number of games at once: no cap on a list's "Download every game", and
a whole platform's disc sets put together from its own listing, discs already here included."""
import pytest

import rommlib


def _disc(i, n, base="Lorem Saga (USA)"):
    return {"id": i, "name": "%s (Disc %d)" % (base, n), "fs_name_no_ext": "%s (Disc %d)" % (base, n),
            "fs_name": "%s (Disc %d).zip" % (base, n), "fs_size_bytes": 100, "platform_id": 6}


class _App:
    """What expand_sets / queue_bulk need of the app."""
    platform_by_id = {6: {"fs_slug": "ps1"}}
    api = None

    def __init__(self, rb, here=(), jobs=()):
        self.make_group = lambda rom, discs: rb.App.make_group(self, rom, discs)
        self.set_title = lambda rom: rb.App.set_title(self, rom)
        self.expand_sets = lambda items, pool=None: rb.App.expand_sets(self, items, pool)
        self.adopted, self.queued = [], []
        app, mine = self, list(jobs)

        class Lib:
            @staticmethod
            def is_installed(r, s):
                return r["id"] in here

            @staticmethod
            def adopt(r, s, g):
                app.adopted.append((r["id"], g["rom_ids"]))
                return True

        class Dl:
            jobs = mine

            @staticmethod
            def enqueue_many(entries, save=True):
                app.queued += entries
                return []

            @staticmethod
            def _finish_group(g, s):
                return "/r/%s.m3u" % g["base"]

        self.library, self.downloader = Lib, Dl


def test_a_whole_platform_puts_its_sets_together_from_its_listing(rb, monkeypatch):
    """Disc 1 is on the box already: the list download asks for discs 2 and 3, the set is all
    three (disc 1 joins it), and RomM isn't asked for any set."""
    monkeypatch.setattr(rommlib, "find_disc_set", lambda *a: pytest.fail("asked RomM for a set it had listed"))
    d1, d2, d3 = _disc(1, 1), _disc(2, 2), _disc(3, 3)
    solo = {"id": 9, "name": "Ipsum", "fs_name_no_ext": "Ipsum (USA)", "fs_name": "Ipsum (USA).zip",
            "platform_id": 6}
    app = _App(rb, here={1})
    pool = [(d, "psx") for d in (d1, d2, d3)]
    n, sets, unrecorded, made = rb.App.queue_bulk(app, [(d2, "psx"), (d3, "psx"), (solo, "psx")], pool)
    assert (n, sets, unrecorded) == (3, 1, None)
    assert [(r["id"], g and g["rom_ids"]) for r, _, g in app.queued] == [(2, [1, 2, 3]), (3, [1, 2, 3]), (9, None)]
    assert app.adopted == [(1, [1, 2, 3])]


def test_every_disc_already_here_makes_the_playlist(rb, monkeypatch):
    monkeypatch.setattr(rommlib, "find_disc_set", lambda *a: pytest.fail("asked RomM"))
    d1, d2 = _disc(1, 1), _disc(2, 2)
    app = _App(rb, here={1, 2})
    pool = [(d1, "psx"), (d2, "psx")]
    assert rb.App.queue_bulk(app, [(d1, "psx")], pool) == (0, 1, None, 1)


def test_picked_discs_still_find_their_set_in_romm(rb, monkeypatch):
    """Without a whole listing (picked games, a filtered list) the set comes from RomM, once a set."""
    d1, d2, d3 = _disc(1, 1), _disc(2, 2), _disc(3, 3)
    asked = []
    monkeypatch.setattr(rommlib, "find_disc_set", lambda api, r: (asked.append(r["id"]) or
                                                                  ([(1, d1), (2, d2), (3, d3)], [])))
    app = _App(rb)
    n, sets, _, _ = rb.App.queue_bulk(app, [(d2, "psx"), (d3, "psx")])
    assert asked == [2] and (n, sets) == (3, 1)


def test_two_copies_of_a_disc_ask_romm_which_set(rb, monkeypatch):
    """"Disc 01" and "Disc 1" of the same game in one listing: RomM's own pick (find_disc_set) decides."""
    a, b, c = _disc(1, 1), _disc(2, 2), dict(_disc(3, 1), fs_name_no_ext="Lorem Saga (USA) (Disc 01)")
    asked = []
    monkeypatch.setattr(rommlib, "find_disc_set", lambda api, r: (asked.append(r["id"]) or ([(1, a), (2, b)], [])))
    app = _App(rb)
    pool = [(x, "psx") for x in (a, b, c)]
    rb.App.queue_bulk(app, [(a, "psx"), (b, "psx"), (c, "psx")], pool)
    assert asked == [1]


def test_a_list_of_any_size_can_be_downloaded(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    monkeypatch.setattr(app, "bg", lambda fn, done=None, err=None: None)
    scr = rb.GameListScreen(app, "Lorem", platform={"id": 6, "_system": "psx", "display_name": "Lorem"})
    scr.server_total = 25000
    [entry] = [e for e in scr.list_actions() if e[0] == "Download every game in this list"]
    assert entry[2] is not None and "25,000 games" in entry[1]
