"""The format test cases: one per (converter, system) in rommlib.CONVERTERS, plus the
two other steps that change game files (Make Xbox images bootable, PS3 decryption).

Built from the converter table, so a converter or system added there gets a case
here by itself (tests/test_format_cases.py checks it). Each case names the source
files whose change should make it run again (`areas`); cases_touched() maps a
list of changed paths to the cases to run, and tools/release.py's gate uses it.

Real games are never named here or in anything the harness writes: a case is a
neutral key ("createcd-psx"), its input lives on the test box as fmt-<key>.<ext>,
and which library file that is lives only in local/formats/inputs.json (git-ignored).
"""
import hashlib
import os
import sys
from dataclasses import dataclass, field

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
import rommlib  # noqa: E402

# every converter runs through the library planner and the worker, and its row in the table
COMMON = ("rb_chd.py", "rb_chd_plan.py", "romm/convert.py")
# the modules each converter's own code lives in, besides COMMON
MODE_AREAS = {
    "createcd": ("romm/discs.py",),          # cue/gdi sheets, cut-short checks
    "createdvd": ("romm/discs.py",),
    "cso": ("romm/discs.py",),               # the CSO decoder
    "rvz": ("romm/tools.py",),               # dolphin-tool's pin and installer
    "squashfs": ("romm/discs.py",),          # make_xiso runs first
    "z3ds": (),
    "zar": ("romm/tools.py",),
    "wua": ("romm/wiiu.py", "romm/tools.py"),
    "wux": ("romm/wiiu.py", "romm/tools.py"),
    "ps3": ("romm/ps3.py",),
}
# settings a converter needs besides its own switch
NEEDS = {"squashfs": {"xbox_bootable": True}, "ps3": {"ps3_decrypt": True}}
# a source the library may only have in another form: derived on the box from the
# owner's own file with the app's tool (never downloaded). mode -> (from ext, how, to ext)
DERIVE = {"rvz": (".rvz", "dolphin-iso", ".iso"), "wux": (".wux", "unwux", ".wud")}
# keys the case needs on the box, from the owner's BIOS folder (romm/ps3.py, romm/wiiu.py)
KEYS = {"ps3": "ps3keys", "wua": "wiiukeys"}


@dataclass(frozen=True)
class Case:
    key: str                  # "createcd-psx": the only name a case ever has
    mode: str                 # the converter's mode, or the extra step's ("xiso", "ps3decrypt")
    system: str               # Batocera's roms folder
    sources: tuple            # input extensions it takes
    outputs: tuple            # what it makes (same extension for xiso: the image, cut)
    settings: dict            # config.json switches turned on for it (everything else off)
    areas: tuple              # repo paths whose change makes it run again
    tool: str = ""            # the ToolSpec slug the app downloads for it
    keys: str = ""            # the keys folder it needs, if any
    derive: tuple = ()        # (from ext, how, to ext) when the input may be made from another form
    library: tuple = field(default=())   # RomM platform folders holding the system's games
    zipped: bool = False      # the input goes on the box as a zip (the form RomM keeps it in), not unpacked

    def areas_hash(self, root=REPO):
        """SHA-256 over the content of every area file (a missing one counts as empty)."""
        h = hashlib.sha256()
        for rel in sorted(self.areas):
            h.update(rel.encode() + b"\0")
            try:
                with open(os.path.join(root, rel), "rb") as f:
                    h.update(hashlib.sha256(f.read()).digest())
            except OSError:
                h.update(b"-")
        return h.hexdigest()


def _library(system):
    return tuple(sorted(slug for slug, s in rommlib.PLATFORM_MAP.items() if s == system))


def _all_off():
    """Every converter switch off: a case turns on only what it tests."""
    out = {c.setting: False for c in rommlib.CONVERTERS}
    out.update({k: False for need in NEEDS.values() for k in need})
    return out


def build():
    cases = []
    for c in rommlib.CONVERTERS:
        for system in sorted(c.systems):
            settings = _all_off()
            settings[c.setting] = True
            settings.update(NEEDS.get(c.mode, {}))
            cases.append(Case(
                "%s-%s" % (c.mode, system), c.mode, system, c.sources, c.outputs, settings,
                tuple(dict.fromkeys(COMMON + MODE_AREAS.get(c.mode, ()))),
                tool=c.tool.slug if c.tool else "", keys=KEYS.get(c.mode, ""),
                derive=DERIVE.get(c.mode, ()), library=_library(system),
                zipped=".zip" in c.sources))
    # Make Xbox images bootable: the planner cuts a full disc image to its game partition
    # (and renames .xiso to .iso) right away, Xbox to squashfs off
    settings = _all_off()
    settings["xbox_bootable"] = True
    cases.append(Case("xiso-xbox", "xiso", "xbox", (".iso", ".xiso"), (".iso",), settings,
                      ("rb_chd_plan.py", "rb_chd.py", "romm/discs.py"), library=_library("xbox")))
    # PS3 decryption: an encrypted disc image, loose, with its key in bios/ps3keys (the
    # ps3 converter case takes RomM's zip of it)
    settings = _all_off()
    settings.update({"ps3_folders": True, "ps3_decrypt": True})
    cases.append(Case("ps3decrypt-ps3", "ps3decrypt", "ps3", (".iso",), (".ps3",), settings,
                      tuple(dict.fromkeys(COMMON + ("romm/ps3.py",))), keys="ps3keys",
                      library=_library("ps3")))
    return cases


CASES = build()
BY_KEY = {c.key: c for c in CASES}


def _norm(p):
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def cases_touched(changed_paths, cases=None):
    """The cases whose areas hold one of the changed paths (repo-relative). An area
    ending in "/" is a folder: anything under it counts."""
    changed = {_norm(p) for p in changed_paths}
    out = []
    for c in (CASES if cases is None else cases):
        for a in c.areas:
            a = _norm(a)
            if a in changed or (a.endswith("/") and any(p.startswith(a) for p in changed)):
                out.append(c)
                break
    return out


def all_areas():
    return sorted({a for c in CASES for a in c.areas})
