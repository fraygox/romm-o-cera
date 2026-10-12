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

