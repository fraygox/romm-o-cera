"""The format harness's case table (tools/formats/cases.py): one case per converter
and system, the right cases for a change, the release gate, and nothing private in
the harness or the tests."""
import glob
import json
import os
import re
import sys

import pytest

import rommlib

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP, "tools", "formats"))
sys.path.insert(0, os.path.join(APP, "tools"))
import cases  # noqa: E402


def keys(found):
    return sorted(c.key for c in found)


def test_every_converter_and_system_has_a_case():
    for conv in rommlib.CONVERTERS:
        for system in conv.systems:
            c = cases.BY_KEY["%s-%s" % (conv.mode, system)]
            assert (c.mode, c.system, c.sources) == (conv.mode, system, conv.sources)
            assert c.settings[conv.setting] is True
            # every other converter is off, so the planner queues only this one
            assert all(not v for k, v in c.settings.items()
                       if k != conv.setting and k not in cases.NEEDS.get(conv.mode, {}))
            assert (c.tool or None) == (conv.tool.slug if conv.tool else None)
    n = sum(len(conv.systems) for conv in rommlib.CONVERTERS)
    assert len(cases.CASES) == n + 2 and len(cases.BY_KEY) == len(cases.CASES)


def test_the_two_other_steps_have_cases():
    x, p = cases.BY_KEY["xiso-xbox"], cases.BY_KEY["ps3decrypt-ps3"]
    assert x.settings["xbox_bootable"] and not x.settings["squashfs"]
    assert p.settings["ps3_folders"] and p.settings["ps3_decrypt"] and p.keys == "ps3keys"
    assert cases.BY_KEY["squashfs-xbox"].settings["xbox_bootable"]     # squashfs needs a bootable image


def test_areas_exist_and_cover_the_converter_code():
    for c in cases.CASES:
        assert c.areas, c.key
        for a in c.areas:
            assert os.path.exists(os.path.join(APP, a)), (c.key, a)
    assert set(cases.all_areas()) >= {"rb_chd.py", "rb_chd_plan.py", "romm/convert.py", "romm/discs.py",
                                      "romm/ps3.py", "romm/wiiu.py", "romm/tools.py"}


def test_every_system_has_a_library_folder_name():
    for c in cases.CASES:
        assert all(rommlib.PLATFORM_MAP[s] == c.system for s in c.library), c.key
    assert cases.BY_KEY["createcd-psx"].library == ("ps1",)


@pytest.mark.parametrize("changed, want", [
    (["romm/wiiu.py"], ["wua-wiiu", "wux-wiiu"]),
    (["./romm/ps3.py"], ["ps3-ps3", "ps3decrypt-ps3"]),
    (["README.md", "rbui/app.py", "tests/test_logic.py"], []),
    (["romm/tools.py"], ["rvz-gamecube", "rvz-triforce", "rvz-wii", "wua-wiiu", "wux-wiiu", "zar-xbox360"]),
])
def test_cases_touched(changed, want):
    assert keys(cases.cases_touched(changed)) == want


def test_shared_code_touches_every_case_that_runs_it():
    assert keys(cases.cases_touched(["rb_chd.py"])) == keys(cases.CASES)
    discs = keys(cases.cases_touched(["romm/discs.py"]))
    assert "xiso-xbox" in discs and "squashfs-xbox" in discs and "cso-psp" in discs
    assert all(k in discs for k in cases.BY_KEY if k.startswith(("createcd-", "createdvd-")))
    assert "z3ds-3ds" not in discs and "ps3-ps3" not in discs
    conv = keys(cases.cases_touched(["romm/convert.py"]))
    assert "xiso-xbox" not in conv and len(conv) == len(cases.CASES) - 1


def test_folder_areas_match_what_is_under_them():
    c = cases.Case("k", "m", "s", (), (), {}, ("tools/xbox360-tools/",))
    assert cases.cases_touched(["tools/xbox360-tools/make.sh"], [c]) == [c]
    assert cases.cases_touched(["tools/xbox360-toolsX"], [c]) == []


def test_areas_hash_follows_the_content(tmp_path):
    (tmp_path / "a.py").write_text("one")
    c = cases.Case("k", "m", "s", (), (), {}, ("a.py", "missing.py"))
    h = c.areas_hash(str(tmp_path))
    assert h == c.areas_hash(str(tmp_path))
    (tmp_path / "a.py").write_text("two")
    assert c.areas_hash(str(tmp_path)) != h


def test_release_gate(tmp_path, monkeypatch, capsys):
    import release
    passed = tmp_path / "passed.json"
    monkeypatch.setattr(release, "FORMAT_PASSED", str(passed))
    monkeypatch.setattr(release, "last_tag", lambda: "v1.0.0")
    monkeypatch.setattr(release, "git", lambda *a, **k: "romm/wiiu.py\nREADME.md")
    with pytest.raises(SystemExit) as e:
        release.format_gate(False)
    assert "wux-wiiu on 43.1 (never run)" in str(e.value)
    release.format_gate(True)                   # --skip-format-tests says what it skipped
    assert "wua-wiiu on 44" in capsys.readouterr().out
    rec = {b: {k: {"status": "pass", "areas_hash": cases.BY_KEY[k].areas_hash()} for k in ("wua-wiiu", "wux-wiiu")}
           for b in release.FORMAT_BASES}
    passed.write_text(json.dumps(rec))
    release.format_gate(False)
    rec["44"]["wux-wiiu"]["status"] = "fail"
    passed.write_text(json.dumps(rec))
    with pytest.raises(SystemExit):
        release.format_gate(False)
    rec["44"]["wux-wiiu"] = {"status": "pass", "areas_hash": "other code"}
    passed.write_text(json.dumps(rec))
    with pytest.raises(SystemExit) as e:
        release.format_gate(False)
    assert "not run for this code" in str(e.value)
    monkeypatch.setattr(release, "git", lambda *a, **k: "README.md")
    release.format_gate(False)                  # nothing a case covers changed


# ------------------------------------------------------------------ nothing private

def _scanned():
    files = glob.glob(os.path.join(APP, "tools", "formats", "**", "*"), recursive=True)
    files += glob.glob(os.path.join(APP, "tests", "**", "*.py"), recursive=True)
    return [f for f in files if os.path.isfile(f) and "__pycache__" not in f]


# an absolute path into a home or a mounted share: never in the harness or the tests
LOCAL_PATH = re.compile(r"/(?:mnt|media|home|srv|nfs)/[A-Za-z0-9_.-]+/")


def _private_strings():
    """What local/formats/inputs.json knows about the owner's library: its folders,
    and each input's file name and the name of the game in it (not the lab's homebrew)."""
    path = os.path.join(APP, "local", "formats", "inputs.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    out = set()
    roots = data.get("_roots") or {}
    for v in [roots.get("bios")] + list(roots.get("library") or []) + list(roots.get("lab") or []):
        if v:
            out.add(v.rstrip("/"))
    lab = tuple(r.rstrip("/") + "/" for r in roots.get("lab") or [])
    for e in (data.get("cases") or {}).values():
        if lab and (e.get("path") or "").startswith(lab):
            continue                # open homebrew from the lab folder (the mock library uses some too)
        for v in (e.get("path"), e.get("member"), (e.get("key") or {}).get("member")):
            if not v:
                continue
            base = os.path.basename(v)
            stem = os.path.splitext(base)[0]
            out |= {base, stem, re.split(r"\s*[(\[]", stem)[0].strip()}
    return {s for s in out if len(s) >= 5}


def test_no_library_path_or_input_name_in_the_harness_or_tests():
    private = _private_strings()
    me = os.path.abspath(__file__)
    for f in _scanned():
        with open(f, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if os.path.abspath(f) != me:            # this file names the pattern itself
            m = LOCAL_PATH.search(text)
            assert not m, "%s holds a local path (%s)" % (os.path.relpath(f, APP), m.group(0))
        low = text.lower()
        for s in private:
            assert s.lower() not in low, "%s holds a name from local/formats/inputs.json" % os.path.relpath(f, APP)
