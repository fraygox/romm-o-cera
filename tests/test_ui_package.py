"""The UI lives in the rbui/ package, run by rommbrowser.pygame: every module
loads at start-up, state that changes at run time has one home that the rest
read through, and the package ships wherever the app does."""
import builtins
import glob
import os
import subprocess
import symtable
import sys

import pytest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = sorted(glob.glob(os.path.join(APP, "rbui", "*.py")) + glob.glob(os.path.join(APP, "rbui", "screens", "*.py")))
# rebound or changed in place while the app runs (apply_theme, the scale, memory
# budgets, the last input used, the button style), so read as theme.BG etc.
LIVE = {"theme": ("K", "BG", "BG2", "PANEL", "PANEL_HI", "LINE", "TEXT", "DIM", "FAINT", "ACCENT", "ACCENT_HI",
                  "GREEN", "AMBER", "RED", "BLUE", "SYSTEM_COL", "SPACE_PARTS"),
        "draw": ("TEXT_BUDGET", "COVER_BUDGET", "FONTS", "NOW"),
        "pads": ("INPUT_MODE", "PAD_STYLE")}


def _name(path):
    rel = os.path.relpath(path, APP)[:-3].replace(os.sep, ".")
    return rel[:-len(".__init__")] if rel.endswith(".__init__") else rel


def test_every_ui_module_is_loaded_at_start(rb):
    """Nothing of the UI loads later, from a folder an update may have swapped in meanwhile."""
    assert all(_name(p) in sys.modules for p in SOURCES)


def test_live_state_has_one_home(rb):
    """No module keeps its own copy of a name that changes at run time: it would
    keep the old theme's colour, the old scale, the old budget."""
    for p in SOURCES:
        mod = sys.modules[_name(p)]
        for home, names in LIVE.items():
            if mod is getattr(rb, home):
                assert all(n in vars(mod) for n in names)
                continue
            assert not set(names) & set(vars(mod)), (mod.__name__, set(names) & set(vars(mod)))


def _global_refs(table):
    for s in table.get_symbols():
        if s.is_referenced() and (s.is_global() or (table.get_type() == "module" and not s.is_assigned()
                                                    and not s.is_imported())):
            yield s.get_name()
    for c in table.get_children():
        yield from _global_refs(c)


@pytest.mark.parametrize("path", SOURCES + [os.path.join(APP, "rommbrowser.pygame")],
                         ids=lambda p: os.path.relpath(p, APP))
def test_no_undefined_globals(path):
    """A name another module defines but this one doesn't import would only fail when that line runs."""
    src = open(path, encoding="utf-8").read()
    table = symtable.symtable(src, path, "exec")
    defined = {s.get_name() for s in table.get_symbols() if s.is_assigned() or s.is_imported()}
    defined |= set(dir(builtins)) | {"__file__", "__name__"}
    missing = sorted(set(_global_refs(table)) - defined)
    assert not missing, missing


def test_the_app_file_set_carries_the_ui(tmp_path):
    """tools/app-files.sh (the release zip, deploy.sh) copies every UI module, and
    the copy starts from its own folder."""
    dest = tmp_path / "rommbrowser"
    subprocess.run(["bash", "-c", '. "$1/tools/app-files.sh" && copy_app_files "$1" "$2"', "_", APP, str(dest)],
                   check=True)
    for p in SOURCES:
        assert (dest / os.path.relpath(p, APP)).is_file(), p
    pytest.importorskip("pygame")
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-c", "import importlib.machinery as m; "
                        "m.SourceFileLoader('rb', 'rommbrowser.pygame').load_module(); "
                        "import rbui.app, rommlib; print(rbui.app.__file__)"],
                       cwd=str(dest), env=env, check=True, capture_output=True, text=True)
    assert r.stdout.strip().splitlines()[-1] == str(dest / "rbui" / "app.py")
