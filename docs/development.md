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
`.github/workflows/docs.yml` for each stable release (it describes what Stable
users have), or now with `tools/release.py docs`. `tools/docs_site.py`
reshapes the GitHub Markdown into pages (README.md becomes Home, Install and
Credits), and `mkdocs.yml` lists them. Edit README.md and docs/, never
build/. To preview:

```sh
python3 tools/docs_site.py && pip install -r tools/docs-requirements.txt && mkdocs serve
```

`mkdocs build --strict` fails on a broken link or anchor, so the site never
publishes one.

## Releasing

Every commit goes to GitHub's `main`; GitHub Actions builds from there, and
`tools/release.py` drives it. There are two channels, picked on the box in
Settings → Update channel:

- **Nightly** follows `main`. Each push that changes the app is tested and
  built as `VERSION+nYYYYMMDD.HHMM.<commit>` (UTC) and replaces the rolling
  `nightly` prerelease (`.github/workflows/nightly.yml`). It's never GitHub's
  "latest", so Stable installs and older versions don't see it.
- **Stable** is a tagged release, `vX.Y.Z`, with its notes in `CHANGELOG.md`
  (`.github/workflows/release.yml`; the docs site updates with it).

A nightly sorts after the release it was built on and before the next:
0.8.1 < 0.8.1+n20261012.1903.ab12cd3 < 0.8.2. Back on Stable, a box keeps
its nightly until a release is newer (never a downgrade).

```sh
tools/release.py setup                    # once per clone (git hooks; see below)
tools/release.py push                     # ship new commits; the nightly builds itself
tools/release.py nightly --wait           # build the nightly now and watch it
tools/release.py changelog                # what the next release's notes would say
tools/release.py stable --dry-run         # preview: version, title, notes
tools/release.py stable [--bump minor] [--notes notes.md] [--summary "…"] [--no-screenshots] [--wait]
tools/release.py screenshots              # refresh the README screenshots (commits them)
tools/release.py docs                     # publish main's docs site now
```

Notes no commit subject carries (upgrade advice, known issues) go under
`## Unreleased` in CHANGELOG.md: nightly notes show them, and the next
`stable` folds them into its section.

`stable` bumps `VERSION` and `RELEASE_DATE`, adds the CHANGELOG section
(written from the commits since the last release, grouped by Conventional
Commit type, unless `--notes` gives it), refreshes the README screenshots,
commits `chore: vX.Y.Z`, tags it and pushes. The release workflow tests the
tag, builds it and publishes the release; nothing is published if a test
fails. Screenshots need Python with pygame: `ROMM_PY` names it (for example
a docker image). Commit subjects become the notes, so write them for users:
`fix: covers that didn't load are fetched again`.

On a box, `VERSION=nightly` installs the nightly from the shell:
`curl -fsSL https://github.com/fraygox/romm-o-cera/releases/latest/download/install.sh | VERSION=nightly sh`.

### Public commits

Commits are public once pushed. `tools/release.py setup` turns on the hooks in
`tools/hooks/`, which run `tools/check_public.py` before each commit and push:
the author and committer must be `public.email`, and no message or added line
may match a `public.deny` pattern. Both are set per clone in git config, never
in the repo:

```sh
git config public.email you@users.noreply.github.com
git config user.email you@users.noreply.github.com
git config --add public.deny 'your-name|your-domain\.example'
```

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
docker build -t rb-test:local tests/         # or in a container (tests/Dockerfile):
docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -v $PWD:$PWD -w $PWD rb-test:local \
  python -m pytest -q -p no:cacheprovider tests
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
| `romm/core.py` | data paths, platform constants, errors, log, atomic writes, queue locking, `try_lock` (a lock file taken only if free), small helpers (sizes, names, a rom's download path `rom_dest`, free space, spawning) |
| `romm/config.py` | `config.json`: the `Setting` table, load/save/migrate, URL and verify helpers, download hours, conversion threads, keep-free space |
| `romm/discs.py` | incomplete-image checks, XISO, .cso, cue/gdi sheets |
| `romm/es.py` | EmulationStation: its API client (`EsClient`), notify, m3u, game files, delete, systems, hashes and counts |
| `romm/covers.py` | the cover cache |
| `romm/net.py` | the HTTP session every request goes through: shared connections, a per-server limit that backs off when the server is busy, retries |
| `romm/api.py` | the RomM API client and pairing |
| `romm/tools.py` | the tools a converter downloads (dolphin-tool, the Xbox 360 tools), each described once as a `ToolSpec` (pinned archives, Settings row state, credits' commits) that its Converter names (`Converter.tool`), and their one installer |
| `romm/bios.py` | BIOS checks and fetching |
| `romm/ps3.py` | PS3 discs |
| `romm/convert.py` | conversions: the Converter table, Z3DS, .zar checks, the conversion queue (the screens read what the worker publishes through `conversions_state()`, never its files) |
| `romm/library.py` | installed.json (`Library`: everything outside reads its records through `snapshot()`, never its lock or fields), multi-disc sets, gamelist entries |
| `romm/leftovers.py` | files a download or conversion left behind |
| `romm/update.py` | release checks and the in-app updater |
| `romm/downloader.py` | downloads: the queue, the workers, space planning |
| `romm/audit.py` | Check games, and game info for games already here |
| `romm/unseen.py` | games EmulationStation can't see |
| `romm/qr.py` | a small QR code encoder (byte mode, versions 1-6) for the Documentation row |

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
folder must only use rommlib names the previous release has: a UI left open
imports rb_chd_plan on the Compress screen (and with it rb_chd and
rb_finalize) and rb_dl to take over downloads. A name new in this release
reaches the workers in the next one. Read settings there as
`cfg.get(key, rommlib.DEFAULT_CONFIG[key])`, not `config_value`.
`tests/test_package.py` pins the names they may use (`PREVIOUS_RELEASE_NAMES`,
checked against the previous release's tag; bump it with each release).
`romm` is a generic name: it is found first because every entry point is a
script in the app folder (sys.path[0]), and install.sh's version check puts
the app folder first too.

### Memory

At 4K one rendered line of text is ~180 KB and a cover several MB, so what
the app keeps is bounded by size, never by count. Rendered text
(`TEXT_BUDGET`) and decoded covers (`COVER_BUDGET`) are least-recently-drawn
caches; each budget is halved on a box with under 3 GB of RAM. A cover is
scaled once, when it loads, to the game panel's box (`COVER_BOX`, the largest
any screen draws), so no full-size copy and no second copy for the panel is
kept. At most two covers decode at once. glibc gets two heaps instead of one
per thread, and freed memory goes back to the system every 30 s. Whole-platform
listings (Check games, Fill in game info) keep only the games on the box,
filtered page by page (`roms_all(keep=…)`). Measured at 4K on a 2 GB VM
against a 64,000-game RomM: 48 pages of search results peak at ~480 MB
(v0.8.0: 1.17 GB, leaving the box 25 MB).

### HTTP

Every request to RomM goes through `Api.s`, and every request to GitHub
through `plain_get()`; both are `net.http_session()`s. Don't make a new
`requests.get` or `Session`. The session:

- keeps connections open and shares them across threads, so a TLS
  handshake is paid once per connection, not per request. Read a response
  you're done with off with `discard(r)` rather than `r.close()`: closing an
  unread body closes its connection too;
- gives each server a `HostGate`: starts at `HTTP_POOL` requests at once,
  halves what was in flight on a 429/502/503/504 or a timeout (once per
  round of requests, never below `GATE_FLOOR`), pauses everyone for a
  `Retry-After`, and grows back by one per round of answers;
- sends a GET (never a POST) up to `NET_TRIES` times after a busy answer,
  waiting what the server asked for, else 0.5 s, 1 s, 2 s (at most
  `NET_WAIT_MAX` each), and once more after a refused or dropped
  connection. A timeout isn't repeated (a wrong address would make the
  user wait it out four times), and a `Retry-After` longer than
  `NET_WAIT_MAX` goes back to the caller.

Only the request and the response head hold a place at the gate: a long
download's body doesn't. A cover that couldn't be fetched (`CoverError`)
shows "…" and is asked for again when drawn, after 5 s, 15 s, then every
minute (logged once); one RomM has no file for, or refuses this token or
certificate for, is "no cover". The ES API on localhost and
the single-use pairing POST don't go through it.

### Logs

`rommlib.log()` appends to `romm-browser.log` in the data folder; the app and
every worker (rb_dl, rb_chd, rb_chd_plan, rb_finalize) write it at once. Each
line is one `O_APPEND` write, so lines never mix. The write that takes the
file past `LOG_MAX` (10 MB) rotates it under `romm-browser.log.lock`:
`.1` is the newest old log, `.9` the oldest (`LOG_KEEP`), so the logs never
take more than about 100 MB. A writer that finds a fresh file once it has the
lock leaves it, so it's rotated once. Every other file the data folder keeps
is bounded too: `chd-results.jsonl` keeps its last entries, the cover cache
its size limit, and queue lines rb_finalize can't read (`*.bad`) their
newest 1 MB.

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
| `deploy.sh` | install a checkout on a box over SSH + write config: stages the release's file set with BUILD stamped, then `tools/deploy_swap.py` checks and swaps it in on the box (the in-app updater's one-step swap; the old app becomes `app-backup`) |
| `tools/app-files.sh` | the app's file set, shared by `deploy.sh` and `tools/build-release.sh` |
| `tools/deploy_swap.py` | deploy.sh's on-box step (stdlib only, run as `python3 -` over ssh); `tests/test_deploy.py` runs it and deploy.sh locally |
| `tools/mock_romm.py` | fake RomM API with a demo library, for development |
| `tools/shoot.py` | headless screenshot driver (one scripted path) |
| `tools/shots.py` | screenshot harness: every screen and dialog in every control style + contact sheet |
| `tools/dolphin-tool/` | builds and packages dolphin-tool for Batocera x86_64 and aarch64 (`make.sh`, `package.sh`) |
| `tools/xbox360-tools/` | builds and packages extract-xiso and zarchive for Batocera x86_64 (`make.sh`, `package.sh`) |
| `tools/settle.py` | tells the screenshot drivers when the app has settled (`SHOOT_SETTLE`) |
| `tools/build-release.sh`, `tools/install.sh` | build the release zip (`NIGHTLY=…` for a nightly) / the on-box installer |
| `tools/release.py` | push, nightly and stable releases, screenshots, docs ([Releasing](#releasing)) |
| `tools/check_public.py`, `tools/hooks/` | keep private details out of public commits (git hooks) |
| `CHANGELOG.md` | every stable release's notes (the GitHub release shows its section) |
| `docs/` | these pages |
