"""Fill in game info against what EmulationStation holds, as found on a lab RomM with
full ScreenScraper media and Batocera 43.1 VMs (2026-10-13):

- ES keeps new game info in its journal and writes gamelist.xml only now and then
  (quitting, a reload): a game just given everything must not be listed again."""
import os

import rommlib
from romm import audit
from test_es_media import game_here, rom_54
from test_metadata_settings import ListApi


def cfg(**kw):
    return dict(rommlib.DEFAULT_CONFIG, **kw)


def lorem():
    return rom_54(fs_name="Lorem Mages.zip", fs_name_no_ext="Lorem Mages", languages=["English"])


def give_es(es, path, tags, **files):
    """ES holds these tags for the game (in memory only: no gamelist.xml written)."""
    with es.lock:
        md = es.mem["nes"][path]
        for t in tags:
            md[t] = "./images/x.png" if t in audit.OFFER_MEDIA else "x"
        md.update(files)


def test_fill_in_counts_what_es_holds_before_it_writes_gamelist_xml(isolated, fake_es, monkeypatch):
    rom = lorem()
    path, _ = game_here()
    es = fake_es({"nes": {".zip"}})
    give_es(es, path, audit.offered(rommlib.gamelist_entry(rom, "nes", [path])))
    assert not os.path.exists(os.path.join(os.path.dirname(path), "gamelist.xml"))
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())
    assert entries == [] and st["complete"] == 1
    monkeypatch.setattr(rommlib, "ES_API", "http://127.0.0.1:9")          # ES can't be asked: the file is all
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())
    assert len(entries) == 1
