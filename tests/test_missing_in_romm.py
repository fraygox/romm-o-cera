"""A game whose file has left RomM's library (missing_from_fs): RomM still lists it, and a
download of it fails with "has no file to download"; the app never offers it."""
import rommlib


def _api(monkeypatch, answers):
    api = rommlib.Api("http://romm.invalid", "t")
    asked = []

    def get(path, params=None, **kw):
        asked.append((path, dict(params or {})))
        return answers(path, params or {})
    monkeypatch.setattr(api, "get", get)
    return api, asked


def test_they_stay_listed_but_are_never_queued(isolated, rb, monkeypatch):
    """Still listed (a game on the box whose RomM file went missing keeps its page, Compare and
    the rest; a RomM that scanned with its library drive away lists every game so), but a
    download says why instead of failing, and the bulk and disc-set paths skip them."""
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.platform_by_id = {1: {"id": 1, "_system": "gba", "fs_slug": "gba"}}
    gone = {"id": 5, "name": "Copper", "platform_id": 1, "fs_name": "Copper.gba", "missing_from_fs": True}
    here = {"id": 6, "name": "Kettle", "platform_id": 1, "fs_name": "Kettle.gba", "missing_from_fs": False}
    app.start_download(gone)
    assert not app.downloader.jobs and "no file for Copper" in app.toasts[-1][0]
    m = rb.modals.DownloadConfirm(app, gone)
    assert m.body == rb.modals.GONE_SAYS
    entries, _ = rb.App.set_entries(app.library, app.downloader, {"id": "g"}, [(gone, "gba"), (here, "gba")])
    assert [r["id"] for r, _, _ in entries] == [6]


def test_a_random_pick_never_lands_on_one(monkeypatch):
    picks = iter([{"id": 1, "missing_from_fs": True}, {"id": 2, "missing_from_fs": False}])
    api, _ = _api(monkeypatch, lambda p, q: next(picks))
    assert api.surprise(lambda o, n: {}, scope={"platform_ids": [1]})[0]["id"] == 2


def test_recommended_and_similar_leave_them_out(monkeypatch):
    def answers(p, q):
        if p == "/api/recommendations":
            return [{"rom": {"id": 1, "missing_from_fs": True}}, {"rom": {"id": 2}}]
        return [{"rom": {"id": 3, "missing_from_fs": True}}, {"rom": {"id": 4, "missing_from_fs": False}}]
    api, _ = _api(monkeypatch, answers)
    assert [x["rom"]["id"] for x in api.recommendations()] == [2]
    assert [r["id"] for r in api.similar(9)] == [4]
