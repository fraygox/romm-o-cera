"""Every UI name in one place, for tests and the screenshot tools: rb.App,
rb.BG, rb.SettingsScreen, ... read from rommbrowser.pygame and the rbui
package. A name is read from the module that defines it, at the moment it's
read, so rb.BG after a theme switch is the new colour.

Read-only: patching a name here would only change the facade, not where the
app looks it up. Patch the defining module (rb.theme.K, rb.draw.TEXT_BUDGET,
rb.input.sdlcontroller, ...), or, for a function or class other modules
import by name, rb.patch(monkeypatch, "text", fake): every module holding it.
"""
import ast
import importlib
import os
import pkgutil

import rbui
import rbui.screens
import rbui.widgets


def _modules():
    out = []
    for pkg in (rbui, rbui.screens, rbui.widgets):
        for info in pkgutil.iter_modules(pkg.__path__):
            if not info.ispkg:
                out.append(importlib.import_module(pkg.__name__ + "." + info.name))
    return [rbui] + out


def _defined(mod):
    """Names a module defines itself (not ones it imports)."""
    with open(mod.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)}
        elif isinstance(node, ast.Try):            # input.py's optional sdlcontroller import
            names |= {(a.asname or a.name) for n in ast.walk(node) if isinstance(n, ast.ImportFrom) for a in n.names}
            names |= {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    return names


class UI:
    def __init__(self, entry):
        mods = _modules()
        home = {}
        for m in mods:
            for n in _defined(m):
                home.setdefault(n, m)
        short = {m.__name__.rsplit(".", 1)[-1]: m for m in mods if m is not rbui}
        object.__setattr__(self, "_entry", entry)
        object.__setattr__(self, "_mods", mods + [entry])
        object.__setattr__(self, "_home", home)
        object.__setattr__(self, "_short", short)

    def where(self, name):
        """The module that defines name (or, for an imported module like time, one holding it)."""
        if name in self._home:
            return self._home[name]
        for m in self._mods:
            if name in vars(m):
                return m
        raise AttributeError(name)

    def __getattr__(self, name):
        if name in self._short:
            return self._short[name]
        return getattr(self.where(name), name)

    def __setattr__(self, name, value):
        raise AttributeError("rb is read-only: set %s on %s (or use rb.patch)" % (name, self.where(name).__name__))

    def patch(self, monkeypatch, name, value):
        """Replace a function or class everywhere it's looked up: the module
        that defines it and every module that imported it by name."""
        real = getattr(self.where(name), name)
        hit = [m for m in self._mods if vars(m).get(name) is real]
        for m in hit:
            monkeypatch.setattr(m, name, value)
        assert hit, name


def load(app_dir, module_name="rommbrowser"):
    """Run rommbrowser.pygame's imports (as Batocera would, minus main()) and return the facade."""
    import importlib.machinery
    entry = importlib.machinery.SourceFileLoader(module_name, os.path.join(app_dir, "rommbrowser.pygame")).load_module()
    return UI(entry)
