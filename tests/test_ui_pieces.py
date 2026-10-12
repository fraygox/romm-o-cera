"""Behaviour of UI pieces that were only ever drawn by the screenshot
harness: the search keyboard, the menu of a game already on the box, the
download prompt, queue_info; and the polish around them: a paused download's
cancel prompt, toasts that repeat, games Compress can't convert."""
import json
import os

import pytest

import rommlib


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)

    def bg(fn, done=None, err=None):               # background work runs right away
        try:
            res = fn()
        except Exception as e:                     # noqa: BLE001 - handed to err, like App.bg
            if err:
                err(e)
            return
        if done:
            done(res)
    monkeypatch.setattr(app, "bg", bg)
    app.platforms = [{"id": 7, "name": "PlayStation", "_system": "psx"}]
    app.platform_by_id = {7: app.platforms[0]}
    return app


def _rom(rid=1, name="Lorem Quest (USA)", size=300 << 20, **kw):
    r = {"id": rid, "name": name, "fs_name": name + ".chd", "fs_name_no_ext": name, "fs_extension": "chd",
         "fs_size_bytes": size, "platform_id": 7}
    r.update(kw)
    return r


def _put(rel, data=b"x" * 1000):
    p = os.path.join(rommlib.ROMS_ROOT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _entries(menu):
    return [e for e in menu.entries if e]


# ------------------------------------------------------------ cancelling a paused download

def _paused_job(rb, app, part=b""):
    J = rommlib.Job
    j = J(_rom(5, "Sed Sprite (Europe)", size=700 << 20, fs_name="Sed Sprite (Europe).zip"), "psx")
    j.state = J.PAUSED
    app.downloader.jobs.append(j)
    if part:
        _put("psx/Sed Sprite (Europe).zip.part", part)
    return j


def test_cancelling_a_paused_download_asks_and_says_how_much_goes(app, rb):
    J = rommlib.Job
    j = _paused_job(rb, app, part=b"p" * (3 << 20))
    sc = rb.DownloadsScreen(app)
    app.push(sc)
    sc.menu(j)
    cancel = next(e for e in _entries(app.modal) if e[0] == "Cancel download")
    cancel[2]()
    dlg = app.modal
    assert isinstance(dlg, rb.Confirm) and dlg.danger and dlg.title == "Cancel download?"
    assert "the 3.0 MB downloaded so far (of 700.0 MB) is deleted" in dlg.body and "Back keeps it" in dlg.body
    part = os.path.join(rommlib.ROMS_ROOT, "psx", "Sed Sprite (Europe).zip.part")
    dlg.action("back")
    assert j.state == J.PAUSED and os.path.exists(part)        # Back: nothing lost
    sc.menu(j)
    next(e for e in _entries(app.modal) if e[0] == "Cancel download")[2]()
    app.modal.action("ok")
    assert j.state == J.CANCELLED and not os.path.exists(part)


def test_cancel_prompt_with_nothing_downloaded_yet(app, rb):
    j = _paused_job(rb, app)
    sc = rb.DownloadsScreen(app)
    sc.confirm_cancel(j)
    assert "nothing is downloaded yet" in app.modal.body


def test_part_bytes_counts_a_folder_downloads_parts(app):
    j = _paused_job(None, app)
    j.rom["fs_name"] = "Sed Sprite (Europe)"
    _put("psx/Sed Sprite (Europe)/a.bin.part", b"a" * 100)
    _put("psx/Sed Sprite (Europe)/b.bin.part", b"b" * 50)
    _put("psx/Sed Sprite (Europe)/c.bin", b"c" * 999)          # finished files aren't partial
    assert app.downloader.part_bytes(j) == 150


# ------------------------------------------------------------ toasts that repeat

def test_the_same_toast_again_is_one_toast_with_a_count(app, rb, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rb.time, "time", lambda: now[0])
    played = []
    monkeypatch.setattr(app.sfx, "play", lambda name, *a, **k: played.append(name))
    app.toast("Turn on RVZ in Settings first", rb.AMBER)
    now[0] += 2
    app.toast("Turn on RVZ in Settings first", rb.AMBER)
    assert len(app.toasts) == 1 and app.toasts[0][3] == 2 and app.toasts[0][2] == 1005.5   # timer renewed
    app.toast("Something else")
    app.toast("Turn on RVZ in Settings first", rb.AMBER)    # not the newest one: stacks as before
    assert [t[0] for t in app.toasts] == ["Turn on RVZ in Settings first", "Something else",
                                          "Turn on RVZ in Settings first"]
    app.toast("Something else", rb.RED)                     # same words, other colour: its own toast
    assert len(app.toasts) == 4
    now[0] += 10                                            # the last one has gone: a fresh one
    app.toast("Something else", rb.RED)
    assert app.toasts[-1][3] == 1


def test_a_burst_of_the_same_error_sounds_once(app, rb, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rb.time, "time", lambda: now[0])
    played = []
    monkeypatch.setattr(app.sfx, "play", lambda name, *a, **k: played.append(name))
    for _ in range(5):                                      # every load of a screen failing at once
        app.toast("Can't reach RomM", rb.RED)
    assert played == ["deny"] and app.toasts[-1][3] == 5
    now[0] += 1
    app.toast("Can't reach RomM", rb.RED)                   # pressed again a moment later: heard again
    assert played == ["deny", "deny"]


def test_a_repeated_toast_draws_its_count(app, rb, monkeypatch):
    seen = []
    real = rb.rich_text
    monkeypatch.setattr(rb, "rich_text", lambda c, fonts, font, s, *a, **k: (seen.append(s),
                                                                            real(c, fonts, font, s, *a, **k))[1])
    app.toast("Queued: Lorem")
    app.toast("Queued: Lorem")
    app.toast("Queued: Lorem")
    app.draw_toasts(app.canvas)
    assert seen == ["Queued: Lorem  ×3"]


# ------------------------------------------------------------ what Compress can't convert

def _encrypted_3ds(name):
    h = bytearray(0x400)
    h[0x100:0x104] = b"NCCH"                       # NCCH flags[7] without "no crypto": encrypted
    return _put("3ds/%s.3ds" % name, bytes(h))


def test_compress_lists_what_it_cant_convert_and_never_queues_it(app, rb, monkeypatch):
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, z3ds=True))
    enc = _encrypted_3ds("Lorem Cart (Encrypted)")
    _put("psx/Ipsum (USA)/Ipsum (USA).cue", b'FILE "Ipsum (USA).bin" BINARY\n  TRACK 01 MODE2/2352\n')
    _put("psx/Ipsum (USA)/Ipsum (USA).bin", b"x" * 5000)
    sc = rb.CompressScreen(app)
    app.push(sc)
    sc.enter()
    assert [r["system"] for r in sc.rows] == ["psx", "3ds"] and sc.rows[1]["cant"]
    assert [c["system"] for c in sc.cands] == ["psx"]          # nothing to queue from 3ds
    sc.draw(app.canvas)
    sc.sel = 1
    assert sc.hints()[0] == ("A", "Why")
    sc.action("x")
    assert app.modal is None and "can't be converted" in app.toasts[-1][0]
    sc.action("ok")
    why = app.stack[-1]
    assert isinstance(why, rb.CantConvertScreen) and why.items == [(enc, "an encrypted dump (Azahar runs "
                                                                         "decrypted ones only)")]
    why.draw(app.canvas)
    why.action("ok")
    assert app.stack[-1] is why and app.modal is None


def test_compress_with_only_games_it_cant_convert(app, rb, monkeypatch):
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, z3ds=True))
    _encrypted_3ds("Lorem Cart (Encrypted)")
    sc = rb.CompressScreen(app)
    app.push(sc)
    sc.enter()
    assert sc.cands == [] and [r["system"] for r in sc.rows] == ["3ds"]
    sc.draw(app.canvas)                             # "Nothing left to compress", then the row
    sc.action("ok")
    assert isinstance(app.stack[-1], rb.CantConvertScreen)


def test_a_failed_compress_scan_lists_nothing(app, rb, monkeypatch):
    sc = rb.CompressScreen(app)
    app.push(sc)
    sc._loaded([], [("3ds", "/r/3ds/x.3ds", "an encrypted dump")])
    assert sc.rows
    monkeypatch.setattr(rb, "compress_scan", lambda skipped=None: 1 / 0)
    sc.enter()
    assert sc.rows == [] and sc.load_error
    sc.action("ok")
    assert app.stack[-1] is sc
