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
frames); `--fixed-wait` always waits the full 50.

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

## Files

| File | Role |
|---|---|
| `rommbrowser.pygame` | the UI (Batocera's pygame system runs it) |
| `rommbrowser.pygame.keys` | evmapy override: only Hotkey+Start (exit) |
| `rommlib.py` | RomM API client, platform map, download/extract pipeline, config |
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
