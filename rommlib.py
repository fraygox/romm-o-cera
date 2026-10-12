"""RomM Browser backend: RomM API client, platform map, download queue and
post-processing (extract / CHD hand-off / gamelist hand-off) for Batocera.

The engine lives in the romm/ package (romm/core.py, romm/config.py, ...),
imported in one go from here. rommlib is its front: rommlib.X reads and sets
each name in the romm module that defines it (see the end of this file), so
code, tests and the workers use rommlib.X as before. VERSION, BUILD and
APP_DIR stay here as literals: older updaters read VERSION from this file's
text, deploy.sh stamps BUILD here, and APP_DIR is this file's folder.

No pygame imports here, so it can be exercised from a shell on the box.
Python 3.9+ (dev venvs) and 3.12 (Batocera 43).
"""
# The stdlib imports stay although rommlib itself uses few of them: tests reach
# rommlib.shutil, rommlib.requests and the like.
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
import types
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from typing import Callable, Optional

import requests

VERSION = "0.9.0"            # changes only for a real GitHub release
BUILD = ""                   # internal builds: deploy.sh stamps the git commit here (empty in releases)
VERSION_LABEL = VERSION + ("+" + BUILD if BUILD else "")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
RELEASE_DATE = "2026-10-12"   # bump with VERSION (tools/build-release.sh refuses a stale one)
AUTHOR = "fraygox"
PROJECT_URL = "https://github.com/fraygox/romm-o-cera"

# after the names above: the romm modules read them through rommlib
from romm import (core as _core, config as _config, discs as _discs, es as _es, covers as _covers,
                  api as _api, tools as _tools, bios as _bios, ps3 as _ps3, convert as _convert,
                  library as _library, leftovers as _leftovers, update as _update, downloader as _downloader,
                  audit as _audit, unseen as _unseen, net as _net)  # noqa: E402


# ---------------------------------------------------------- the names everyone imports
# Every name the romm/ modules define is read and set through rommlib, as before
# the split: rommlib.X reads romm.<owner>.X, and setting rommlib.X (tests, the
# screenshot harness) sets it in its owner, where its code looks it up.
_MODULES = (_core, _config, _discs, _es, _covers, _api, _tools, _bios, _ps3, _convert, _library, _leftovers,
            _update, _downloader, _audit, _unseen, _net)
_FOREIGN = {"dataclass", "Callable", "Optional"}
_OWNER = {}
for _m in _MODULES:
    for _n, _v in vars(_m).items():
        if _n.startswith("__") or _n in _FOREIGN or isinstance(_v, types.ModuleType):
            continue
        if _n in _OWNER or _n in globals():
            raise ImportError("rommlib: %s is defined twice (%s)" % (_n, _m.__name__))
        _OWNER[_n] = _m


class _Rommlib(types.ModuleType):
    def __getattr__(self, name):
        try:
            return getattr(_OWNER[name], name)
        except KeyError:
            raise AttributeError("module 'rommlib' has no attribute %r" % name) from None

    def __setattr__(self, name, value):
        if name in _OWNER:
            setattr(_OWNER[name], name, value)
        else:
            super().__setattr__(name, value)

    def __delattr__(self, name):
        if name in _OWNER:
            delattr(_OWNER[name], name)
        else:
            super().__delattr__(name)

    def __dir__(self):
        return sorted(set(super().__dir__()) | set(_OWNER))


sys.modules[__name__].__class__ = _Rommlib
