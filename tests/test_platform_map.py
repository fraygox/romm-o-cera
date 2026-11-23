"""Which roms/<system> a RomM platform's games go to (rommlib.platform_system)."""
import rommlib


def _es(tmp_path, monkeypatch, names):
    cfg = tmp_path / "es_systems.cfg"
    cfg.write_text("<systemList>%s</systemList>" % "".join(
        "<system><name>%s</name><path>/userdata/roms/%s</path><extension>.zip</extension></system>" % (n, n)
        for n in names))
    monkeypatch.setenv("ROMM_BROWSER_ES_SYSTEMS", str(cfg))


def test_a_library_laid_out_like_a_batocera_share_keeps_its_platforms(tmp_path, monkeypatch):
    """RomM takes Batocera's folder names as they are (psx, megadrive, pcengine, fbneo...): those
    platforms were left out of the app altogether."""
    _es(tmp_path, monkeypatch, ["psx", "megadrive", "pcengine", "fbneo", "mame", "neogeo", "amstradcpc"])
    for fs, slug in (("psx", "psx"), ("megadrive", "genesis"), ("pcengine", "tg16"), ("fbneo", "arcade"),
                     ("mame", "arcade"), ("neogeo", "neogeoaes"), ("amstradcpc", "acpc")):
        assert rommlib.platform_system({"fs_slug": fs, "slug": slug}) == fs


def test_romms_own_names_map_as_before_and_overrides_win(tmp_path, monkeypatch):
    _es(tmp_path, monkeypatch, ["psx", "megadrive", "fbneo", "mame"])
    assert rommlib.platform_system({"fs_slug": "ps1", "slug": "psx"}) == "psx"
    assert rommlib.platform_system({"fs_slug": "arcade", "slug": "arcade"}) == "fbneo"
    assert rommlib.platform_system({"fs_slug": "arcade", "slug": "arcade"}, {"arcade": "mame"}) == "mame"
    assert rommlib.platform_system({"fs_slug": "psx", "slug": "psx"}, {"psx": None}) is None      # hidden


def test_a_folder_bound_in_romms_config_goes_by_romms_slug(tmp_path, monkeypatch):
    _es(tmp_path, monkeypatch, ["megadrive", "psx"])
    assert rommlib.platform_system({"fs_slug": "my-md-games", "slug": "genesis"}) == "megadrive"
    assert rommlib.platform_system({"fs_slug": "playstation-stuff", "slug": "psx"}) == "psx"


def test_a_folder_batocera_has_no_system_for_stays_hidden(tmp_path, monkeypatch):
    """A folder of the owner's own (cps2) isn't a system ES lists: its games would never show."""
    _es(tmp_path, monkeypatch, ["psx"])
    assert rommlib.platform_system({"fs_slug": "cps2", "slug": "cps2"}) is None
    assert rommlib.platform_system({"fs_slug": "notes", "slug": None}) is None


def test_romms_own_slugs_for_systems_batocera_names_otherwise(tmp_path, monkeypatch):
    """A RomM folder named by RomM's slug (ngc, tg16, acpc, neogeoaes...): it had no system."""
    _es(tmp_path, monkeypatch, [])
    for slug, system in (("ngc", "gamecube"), ("tg16", "pcengine"), ("acpc", "amstradcpc"), ("dc", "dreamcast"),
                         ("neogeoaes", "neogeo"), ("sms", "mastersystem"), ("atari8bit", "atari800"), ("rpg-maker", "easyrpg")):
        assert rommlib.platform_system({"fs_slug": slug, "slug": slug}) == system


def test_a_platform_map_saved_as_a_list_of_pairs_still_counts(tmp_path, monkeypatch):
    _es(tmp_path, monkeypatch, ["psx", "fbneo", "mame"])
    assert rommlib.platform_system({"fs_slug": "arcade", "slug": "arcade"}, [["arcade", "mame"]]) == "mame"
    assert rommlib.platform_system({"fs_slug": "ps1", "slug": "psx"}, [["ps1", None]]) is None
    assert rommlib.platform_system({"fs_slug": "ps1", "slug": "psx"}, "junk") == "psx"


def test_an_override_by_romms_slug_reaches_a_bound_folder(tmp_path, monkeypatch):
    """Settings' Arcade games go to (platform_map "arcade") for a folder bound to RomM's arcade."""
    _es(tmp_path, monkeypatch, ["fbneo", "mame"])
    assert rommlib.platform_system({"fs_slug": "my-arcade", "slug": "arcade"}, {"arcade": "mame"}) == "mame"
    assert rommlib.platform_system({"fs_slug": "fbneo", "slug": "arcade"}, {"arcade": "mame"}) == "fbneo"
