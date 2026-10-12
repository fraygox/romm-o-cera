# Development

## Running without a box or a RomM

```sh
python3 -m venv .venv && .venv/bin/pip install pygame==2.6.1 requests pillow
.venv/bin/python tools/mock_romm.py --port 8765 &
ROMM_URL=http://127.0.0.1:8765 ROMM_TOKEN=demo \
ROMM_BROWSER_DATA=/tmp/rb/data ROMM_BROWSER_ROMS=/tmp/rb/roms \
  .venv/bin/python tools/shoot.py /tmp/shots "wait:3,shot:home,act:down,act:ok,wait:2,shot:platforms"
```

`shoot.py` steps: `wait:<s>`, `act:<up|down|left|right|ok|back|x|y|l1|r1|start|select>`,
`type:<text>` (search/edit fields), `shot:<name>`. Add `ROMM_BROWSER_SCALE=3`
to render at 4K. On a Batocera box (its SDL has no headless driver) run it
with `SDL_VIDEODRIVER=x11 DISPLAY=:0`. With `SHOOT_SETTLE=1` a `wait:<s>` ends
as soon as the app has settled (no background load or download running; `<s>`
stays the cap), and `SHOOT_TRACE=<file>` logs the app's state before each step.

The mock library is freely distributed homebrew / open-source games plus
invented "lorem ipsum" titles, with generated covers — keep it that way: no
commercial game names in screenshots, tests or fixtures.

## The screenshot harness

The whole UI at once — every screen and dialog, in the Xbox, PlayStation,
Nintendo and keyboard styles, with a contact sheet (`index.html`). It starts
its own mock RomM and fake EmulationStation, and fails on a hint that
bypasses the glyph helper:

```sh
.venv/bin/python tools/shots.py /tmp/shots [--styles xbox,kb] [--scenes settings,downloads]
.venv/bin/python tools/shots.py /tmp/shots --readme     # also refresh the README's screenshots/
```

Each scene is drawn once the app's background loads have landed (at most 50
frames); `--fixed-wait` always waits the full 50. `--theme xbox_oled` (any
Settings theme) draws them in that theme; `screenshots/themes/` comes from it.

## The documentation site

<https://fraygox.github.io/romm-o-cera/> is built from README.md and docs/ by
`.github/workflows/docs.yml` on every push to main. `tools/docs_site.py`
reshapes the GitHub Markdown into pages (README.md becomes Home, Install and
Credits), and `mkdocs.yml` lists them. Edit README.md and docs/, never
build/. To preview:

```sh
python3 tools/docs_site.py && pip install -r tools/docs-requirements.txt && mkdocs serve
```

`mkdocs build --strict` fails on a broken link or anchor, so the site never
publishes one.

## dolphin-tool

Built in a container from the Dolphin commit Batocera ships:
`tools/dolphin-tool/make.sh [x86_64|aarch64]` then
`tools/dolphin-tool/package.sh [x86_64|aarch64]`. The ARM build is a
cross-build; test it on real ARM hardware — under qemu-user it hangs at
startup (Dolphin's udev hot-plug listener waits on a real-time signal
qemu-user uses itself).

## Tests

```sh
pip install -r tests/requirements.txt
python -m pytest tests                       # logic + downloads/UI vs the mock RomM
ROMM_CONTRACT_URL=https://romm.example.com ROMM_CONTRACT_TOKEN=rmm_… \
  python -m pytest tests/test_contract.py    # your RomM still has the API the app uses
python tests/contract/disposable.py --tag 5.4.0   # same, against a throwaway RomM (needs docker)
```

The suite takes about 55 s (it took over 5 minutes before v0.7.0). It also runs in parallel if you install the
optional `pytest-xdist` (`python -m pytest tests -n 8`, about 17 s, bound by the
screenshot harness). The tests are independent of order and of each other,
and each xdist worker starts its own mock RomM. Without xdist nothing changes.

The contract tests are read-only and download only the smallest file they
find. `disposable.py` is handy before upgrading RomM: run it with the new tag
first.

## Adding a setting

Every `config.json` key is one `Setting` in `rommlib.SETTINGS` (`romm/config.py`), in the order
the file is written in: its default, and for a Settings row its row id, label,
grey line and help. A plain switch (`kind="onoff"`) or a list to cycle
(`kind="choice"`, with `choices`) needs nothing more than its place in
`SettingsScreen.items()`; `SettingsScreen.SHOW`, `NEEDS` and `EFFECTS` hold a
choice's value text, what the box must have, and what a change re-wires. Any
other row is written by hand in `items()` and `action()`. Add the key to the
table in [Settings](settings.md) too (a test checks). Renaming a key: add
`old: new` to `rommlib.RENAMED`, so the user's value moves over.

## The romm package

The engine is the `romm/` package; `rommlib.py` is its front, and the UI,
the workers, the tests and the screenshot harness use `rommlib.X` only. The
modules, in import order (each uses only the ones above it):

| Module | Holds |
|---|---|
| `romm/core.py` | data paths, platform constants, errors, log, atomic writes, queue locking, small helpers (sizes, names, free space, spawning) |
| `romm/config.py` | `config.json`: the `Setting` table, load/save/migrate, URL and verify helpers, download hours, conversion threads, keep-free space |
| `romm/discs.py` | incomplete-image checks, XISO, .cso, cue/gdi sheets |
| `romm/es.py` | EmulationStation: its API, notify, m3u, game files, delete, systems, hashes and counts |
| `romm/covers.py` | the cover cache |
| `romm/api.py` | the RomM API client and pairing |
| `romm/tools.py` | the dolphin-tool and Xbox 360 tool installers |
| `romm/bios.py` | BIOS checks and fetching |
| `romm/ps3.py` | PS3 discs |
| `romm/convert.py` | conversions: the Converter table, Z3DS, .zar checks, the conversion queue |
| `romm/library.py` | installed.json (`Library`), multi-disc sets, gamelist entries |
| `romm/leftovers.py` | files a download or conversion left behind |
| `romm/update.py` | release checks and the in-app updater |
| `romm/downloader.py` | downloads: the queue, the workers, space planning |
| `romm/audit.py` | Check games, and game info for games already here |
| `romm/unseen.py` | games EmulationStation can't see |

The rules (`tests/test_package.py` checks them):

- A module reaches its siblings only as `from . import core as _core, ...` at
  the top, and names theirs qualified: `_config.load_config()`,
  `_core.DATA_DIR`. Its own names stay bare.
- No `from .x import name` (nor `from romm.x import ...`) of romm names: a copy
  would not see a value set through rommlib. Each name is defined in one module.
- rommlib's owner table forwards gets and sets: `rommlib.X` reads, and
  `rommlib.X = v` (or `monkeypatch.setattr(rommlib, "X", v)`) sets, the name in
  the module that defines it, which is where that module's code looks it up.
  A new name needs nothing more than its place in the right module; a new
  module goes into rommlib's import and `_MODULES`.
- Everything is imported at startup, through `import rommlib`: no romm import
  inside a function, and no worker (rb_*) import at all: workers import the
  package, never the other way round. VERSION,
  BUILD, VERSION_LABEL, APP_DIR, RELEASE_DATE, AUTHOR and PROJECT_URL stay as
  literals in `rommlib.py`, and romm modules read them as `rommlib.VERSION`:
  every shipped updater reads `VERSION = "..."` from rommlib.py's text,
  deploy.sh stamps `BUILD = ""` there, and APP_DIR is that file's folder.

Worker files an OLD process loads lazily after an in-app update swapped the
folder must only use rommlib names the previous release has: v0.7.1's
`rb_dl.py` imports rb_chd (and with it rb_finalize) for migrate_saves and
fix_m3us, which is why rb_chd keeps its own copies of those two for one
release (they live in romm/library.py now), and a UI left
open imports rb_chd_plan on the Compress screen. Read settings there as
`cfg.get(key, rommlib.DEFAULT_CONFIG[key])`, not `config_value`.
`tests/test_package.py` pins the names they may use.
`romm` is a generic name: it is found first because every entry point is a
script in the app folder (sys.path[0]), and install.sh's version check puts
the app folder first too.

## Files

| File | Role |
|---|---|
| `rommbrowser.pygame` | the UI (Batocera's pygame system runs it) |
| `rommbrowser.pygame.keys` | evmapy override: only Hotkey+Start (exit) |
| `rommlib.py` | the engine's front: VERSION, BUILD and APP_DIR, then imports the `romm/` package and makes every name in it readable and settable as `rommlib.X` |
| `romm/` | the engine (see [The romm package](#the-romm-package)) |
| `rb_finalize.py` | hands game info to EmulationStation through its API once ES is idle |
| `rb_repair.py` | one-time restore of game info lost after CHD conversion (≤ v0.6.3) |
| `rb_chd.py` | background conversion worker (CHD, RVZ, squashfs, .zcci, .zar) |
| `rb_chd_plan.py` | finds games already on the box that could be compressed and queues them (On this box → Compress games already here, or its command line); makes Xbox images bootable |
| `rb_dl.py` | background downloader (finishes the queue after exit) |
| `fonts/` | Rubik (SIL Open Font License) |
| `deploy.sh` | copy to a box + write config |
| `tools/mock_romm.py` | fake RomM API with a demo library, for development |
| `tools/shoot.py` | headless screenshot driver (one scripted path) |
| `tools/shots.py` | screenshot harness: every screen and dialog in every control style + contact sheet |
| `tools/dolphin-tool/` | builds and packages dolphin-tool for Batocera x86_64 and aarch64 (`make.sh`, `package.sh`) |
| `tools/xbox360-tools/` | builds and packages extract-xiso and zarchive for Batocera x86_64 (`make.sh`, `package.sh`) |
| `tools/settle.py` | tells the screenshot drivers when the app has settled (`SHOOT_SETTLE`) |
| `tools/build-release.sh`, `tools/install.sh` | build the release zip / the on-box installer |
| `tools/publish.sh` | maintainer: publish this folder from a monorepo (history replay, or `--snapshot` one commit on top of the remote) |
| `docs/` | these pages |
