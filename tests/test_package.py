"""rommlib is a front for the romm/ package: every name keeps working through
rommlib, and setting rommlib.X (monkeypatch, the screenshot harness) reaches
the code that uses X, wherever it lives."""
import ast
import builtins
import glob
import os
import re
import symtable
import sys
import types

import rommlib

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(APP, "romm")
SOURCES = sorted(glob.glob(os.path.join(PKG, "*.py"))) + [os.path.join(APP, "rommlib.py")]


def test_every_romm_module_is_loaded_by_rommlib():
    """rommlib imports the whole package at once: a running worker never loads a
    module later, from a folder an update may have swapped in meanwhile."""
    names = {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(PKG, "*.py"))} - {"__init__"}
    assert names == {m.__name__.split(".")[1] for m in rommlib._MODULES}
    assert all("romm." + n in sys.modules for n in names)


def test_one_owner_per_name_and_no_copies():
    """No module holds a copy of another's name (from .x import y): setting it
    through rommlib would miss the copy."""
    for m in rommlib._MODULES:
        for n, v in vars(m).items():
            if n.startswith("__") or n in rommlib._FOREIGN or isinstance(v, types.ModuleType):
                continue
            assert rommlib._OWNER[n] is m, (m.__name__, n)
    assert not set(rommlib._OWNER) & set(vars(rommlib)), "rommlib keeps its own copy"


def test_setting_through_rommlib_reaches_the_owner(monkeypatch):
    for n, m in rommlib._OWNER.items():
        orig = getattr(m, n)
        assert getattr(rommlib, n) is orig
        mark = object()
        monkeypatch.setattr(rommlib, n, mark)
        assert getattr(m, n) is mark and getattr(rommlib, n) is mark
        monkeypatch.undo()
        assert getattr(m, n) is orig


def _imports_ok(path):
    """Siblings only as `from . import x as _x`, at the top; nothing imported by
    name from romm/rommlib (that would be a copy); no romm import inside a
    function (see the first test)."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    top = set(map(id, tree.body))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.level or (node.module or "").split(".")[0] in ("romm", "rommlib")):
            assert id(node) in top, (path, node.lineno)
            if os.path.basename(path) == "rommlib.py":
                assert node.module == "romm" and not node.level, (path, node.lineno)
            else:
                assert node.level == 1 and node.module is None, (path, node.lineno)
            assert all(a.asname == "_" + a.name for a in node.names), (path, node.lineno)
        elif isinstance(node, ast.Import) and any(a.name.split(".")[0] in ("romm", "rommlib") for a in node.names):
            assert id(node) in top, (path, node.lineno)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # never a worker: it would load later, maybe from a folder an update swapped meanwhile
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
            assert not any(n.startswith("rb_") for n in names), (path, node.lineno)


def test_imports_are_whole_modules():
    for p in SOURCES:
        _imports_ok(p)


def _global_refs(table):
    for s in table.get_symbols():
        if s.is_referenced() and (s.is_global() or (table.get_type() == "module" and not s.is_assigned()
                                                    and not s.is_imported())):
            yield s.get_name()
    for c in table.get_children():
        yield from _global_refs(c)


def test_no_undefined_globals():
    """A cross-module name left unqualified would only fail when that line runs."""
    for p in SOURCES:
        src = open(p, encoding="utf-8").read()
        table = symtable.symtable(src, p, "exec")
        defined = {s.get_name() for s in table.get_symbols() if s.is_assigned() or s.is_imported()}
        defined |= set(dir(builtins)) | {"__file__", "__name__"}
        missing = sorted(set(_global_refs(table)) - defined)
        assert not missing, (p, missing)


def test_version_stays_a_literal_in_rommlib_py():
    """Older updaters read VERSION from rommlib.py's text; deploy.sh stamps BUILD there."""
    text = open(os.path.join(APP, "rommlib.py"), encoding="utf-8").read()
    assert re.search(r'^VERSION = "([^"]+)"', text, re.M).group(1) == rommlib.VERSION
    assert re.search(r'^BUILD = ""', text, re.M)
    assert rommlib.APP_DIR == APP


def test_qualified_names_exist():
    """_config.load_config and the like name something that module defines."""
    for p in SOURCES:
        for node in ast.walk(ast.parse(open(p, encoding="utf-8").read())):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                base = node.value.id
                if base.startswith("_") and "romm." + base[1:] in sys.modules:
                    assert node.attr in vars(sys.modules["romm." + base[1:]]), (p, node.lineno, base, node.attr)
                elif base == "rommlib" and os.path.dirname(p) == PKG:
                    assert node.attr in vars(rommlib), (p, node.lineno, node.attr)


# rommlib names the lazily loaded workers use, pinned to v0.11.0, the release
# before the one being cut. An old process reaches the NEW worker files after an
# in-app update swapped the folder under it: a still-open UI imports rb_chd_plan
# on the Compress screen (which imports rb_chd) and rb_dl to take over
# downloads, and rb_dl imports rb_finalize. All of it runs against the OLD
# rommlib already in memory. Add a name here only after checking v0.11.0
# defines it (`git show v0.11.0:romm/<module>.py`); a name new since waits for
# the next release. Bump the tag after each stable release.
PREVIOUS_RELEASE_NAMES = frozenset((
    "Api", "CHD_QUEUE_DIR", "CHECK_ERRORS", "CONVERTER", "CONVERTERS", "CSO_TEMP_SUFFIX", "CsoError",
    "DATA_DIR", "DEFAULT_CONFIG", "ES_API", "EsClient", "GAMELIST_MEDIA_DIRS", "GAMELIST_QUEUE", "Library",
    "MEDIA_DIRS", "NoSpace", "Ps3Error", "ROMS_ROOT", "RVZ_ARGS", "SQUASH_ARGS", "SQUASH_SYSTEMS",
    "Z3dsError", "append_gamelist", "atomic_write_json", "atomic_write_text", "chd_current_path",
    "chd_lock_path", "chd_low_memory", "chd_results_path", "chd_threads", "conversion_for",
    "conversion_output", "converters_for", "cso_decode", "cso_info", "disc_incomplete", "disc_info",
    "dolphin_tool_path", "es_add_games", "es_busy", "es_running_game", "fix_m3us", "free_bytes",
    "human_size", "keep_free_bytes", "load_config", "load_es_systems", "log", "make_xiso",
    "migrate_saves", "path_size", "planner_order", "primary_path", "ps3_to_folder", "publish_queue_task",
    "queue_lock", "sheet_sources", "spawn_detached", "sync_file", "tmp_dir", "try_lock", "write_png", "x360_tools",
    "xbox_partition", "xiso_listing", "xiso_to_iso", "z3ds_compress", "z3ds_info", "z3ds_verify",
    "zar_check",
    "DL_LOCK", "DL_STATUS", "DL_STOP", "Downloader", "Job", "es_messagebox", "es_notify",
    "start_conversions",   # rb_dl
    "wiiu_key_file", "wuatool_path",   # rb_chd (Wii U; only a "wua" task reaches them)
    "settle",       # rb_chd, rb_chd_plan: only a converter with a fallback (Wii U, newer than v0.10.0) reaches it
))


def test_lazily_loaded_workers_only_use_names_the_previous_release_has():
    for fn in ("rb_chd.py", "rb_chd_plan.py", "rb_finalize.py", "rb_dl.py"):
        tree = ast.parse(open(os.path.join(APP, fn), encoding="utf-8").read())
        for node in ast.walk(tree):
            assert not (isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in ("romm", "rommlib")), (fn, node.lineno)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "rommlib":
                assert node.attr in PREVIOUS_RELEASE_NAMES, (fn, node.lineno, node.attr)


def test_isolated_redirects_every_data_path(isolated):
    """Paths joined from DATA_DIR at import (UPDATE_CACHE, TOOLS_DIR...) are
    patched too, and those joined at call time (rb_finalize's lock and deferred
    entries...) follow DATA_DIR: a test must never write into a real data
    folder (pytest run on the box itself)."""
    import rb_chd
    import rb_chd_plan
    import rb_dl
    import rb_finalize
    import rb_repair
    real = os.environ.get("ROMM_BROWSER_DATA", "/userdata/system/romm-browser")
    for m in list(rommlib._MODULES) + [rb_chd, rb_chd_plan, rb_dl, rb_finalize, rb_repair]:
        for n, v in vars(m).items():
            assert not (isinstance(v, str) and v.startswith(real)), (m.__name__, n, v)
    for fn in (rommlib.chd_lock_path, rommlib.chd_current_path, rommlib.chd_results_path,
               rommlib.audit_path, rb_finalize.lock_path, rb_finalize.deferred_path):
        assert fn().startswith(rommlib.DATA_DIR + os.sep), (fn.__name__, fn())
