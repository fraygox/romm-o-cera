"""Extensions ES lists for a system but its chosen emulator and core can't open (es_systems'
incompatible_extensions): a zip there is unpacked, as N64's default mupen64plus can't open one."""
import os
import zipfile

import rommlib
from romm import es

ES_SYSTEMS = """<systemList>
 <system><name>n64</name><path>/userdata/roms/n64</path><extension>.z64 .n64 .v64 .zip .7z</extension>
  <emulators>
   <emulator name="libretro"><cores><core>mupen64plus-next</core><core>parallel_n64</core></cores></emulator>
   <emulator name="mupen64plus"><cores>
    <core default="true" incompatible_extensions=".zip .7z">glide64mk2</core>
    <core incompatible_extensions=".zip .7z">gliden64</core></cores></emulator>
  </emulators></system>
 <system><name>gba</name><path>/userdata/roms/gba</path><extension>.gba .zip</extension>
  <emulators><emulator name="libretro"><cores><core default="true">mgba</core></cores></emulator></emulators>
 </system>
</systemList>
"""


def _es(tmp_path, monkeypatch):
    p = tmp_path / "es_systems.cfg"
    p.write_text(ES_SYSTEMS)
    monkeypatch.setenv("ROMM_BROWSER_ES_SYSTEMS", str(p))


def test_the_default_or_chosen_core_decides(isolated, tmp_path, monkeypatch):
    _es(tmp_path, monkeypatch)
    assert es.load_es_unopenable({}) == {"n64": {".zip", ".7z"}}
    assert es.load_es_unopenable({"n64.emulator": "libretro"}) == {}
    assert es.load_es_unopenable({"n64.core": "parallel_n64"}) == {}
    assert es.load_es_unopenable({"n64.emulator": "mupen64plus", "n64.core": "gliden64"}) == \
        {"n64": {".zip", ".7z"}}
    conf = tmp_path / "system" / "batocera.conf"
    conf.parent.mkdir()
    conf.write_text("# a comment\nn64.emulator=libretro\nn64.core=mupen64plus-next\n")
    assert es.batocera_conf()["n64.core"] == "mupen64plus-next"
    assert es.load_es_unopenable() == {}


def _zipped(system, name):
    d = os.path.join(rommlib.ROMS_ROOT, system)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, os.path.splitext(name)[0] + ".zip")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(name, b"\x80\x37\x12\x40" + b"\0" * 4096)
    return path


def test_an_n64_zip_is_unpacked_unless_the_core_opens_zips(isolated, tmp_path, monkeypatch):
    _es(tmp_path, monkeypatch)
    cfg = dict(rommlib.DEFAULT_CONFIG)
    d = rommlib.Downloader(rommlib.Api("http://127.0.0.1:9", "x"), rommlib.Library(), cfg)
    d.es = rommlib.load_es_systems()
    assert d.unopenable == {"n64": {".zip", ".7z"}}
    path = _zipped("n64", "Lorem (USA).z64")
    out = d._postprocess_file(rommlib.Job({"id": 1, "name": "Lorem"}, "n64"), path)
    assert [os.path.basename(p) for p in out] == ["Lorem (USA).z64"] and not os.path.exists(path)
    gba = _zipped("gba", "Ipsum (USA).gba")
    assert d._postprocess_file(rommlib.Job({"id": 2, "name": "Ipsum"}, "gba"), gba) == [gba]   # mgba reads zips
    d.unopenable = es.load_es_unopenable({"n64.emulator": "libretro"})
    path = _zipped("n64", "Dolor (USA).z64")
    assert d._postprocess_file(rommlib.Job({"id": 3, "name": "Dolor"}, "n64"), path) == [path]
