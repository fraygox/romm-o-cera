# RomM-o-Cera — a RomM client for Batocera

A controller-driven app that runs **on a Batocera box**: browse and search a
[RomM](https://github.com/rommapp/romm) library from the couch and download
games straight into the right `/userdata/roms/<system>/` folder — extracted,
optionally CHD-compressed, with EmulationStation metadata and cover art.

**Early software (v0.9.0).** It works on the author's box every day, but it
has one author and few users so far — expect rough edges, and please file
issues. Built for **Batocera 43** (pygame 2.6, Python 3.12) and tested
against **RomM 5.4** (5.3 works too). Draws natively at any resolution, 720p
to 4K.

![Home: recently added shelf](screenshots/home.png)

| | |
|---|---|
| ![Platforms](screenshots/platforms.png) | ![A platform's games](screenshots/game-list.png) |
| ![List menu: filters, pick, download all](screenshots/menu.png) | ![Search](screenshots/search.png) |
| ![Collections: download a whole collection](screenshots/collections.png) | ![Downloads: queue, unpacking, waiting for space](screenshots/downloads.png) |
| ![Conversions: CHD and RVZ in the background](screenshots/conversions.png) | ![Settings in sections, with help](screenshots/settings.png) |
| ![On this box](screenshots/on-this-box.png) | ![Manage a game on the box](screenshots/manage.png) |
| ![Space: the whole drive, then each system](screenshots/storage.png) | ![Free up space](screenshots/free-up.png) |
| ![Compress games already on the box, one system at a time](screenshots/compress.png) | ![Credits and licenses](screenshots/credits.png) |

<sub>Screenshots come from `tools/mock_romm.py`: freely distributed homebrew /
open-source games plus invented "lorem ipsum" titles, with generated covers.</sub>

## What it does

- **Browse and search** your RomM library from the couch: platforms,
  collections, recently added, an on-screen keyboard, filters, "more like
  this". → [Browsing and downloading](docs/browsing-and-downloading.md)
- **Download** single games, multi-disc sets (one playlist entry), whole lists
  or whole collections into the right `/userdata/roms/<system>/` folder —
  resumable, checked against RomM's checksums, extracted, with cover art,
  descriptions and manuals handed to EmulationStation. Downloads keep going
  after you exit, wait for space instead of filling the drive, and can be kept
  to overnight hours.
- **Manage the box** from one menu that shows its state at a glance: delete
  games (saves are kept), see where the space went, free up space, clean up
  leftovers, compress games already on the box, fetch missing BIOS files from
  RomM, check for damaged or cut-short games and replace them.
  → [On this box](docs/on-this-box.md)
- **Compress discs in the background**, lossless and verified: CHD for CD
  systems, PS2 and PSP; RVZ for GameCube and Wii; squashfs for Xbox; .zcci for 3DS; .zar for Xbox 360. Xbox
  downloads are made bootable for xemu. Games already on the box can be
  compressed too, one or a whole system at a time. → [Disc compression](docs/disc-compression.md)
- **PS3 games as game folders**: disc images become `.ps3` folders RPCS3 boots
  and updates, read straight from the zip. Encrypted discs use disc keys for
  games you own, from `bios/ps3keys` or your RomM (off by default; none are
  included). → [PS3 games](docs/ps3.md#where-disc-keys-go)
- **Feels native**: button labels match your controller (Xbox, PlayStation,
  Nintendo) or keyboard, colour themes to match (with pure-black OLED
  and high-contrast versions), settings explain themselves, updates install from the
  box with one-step rollback. → [Controls](docs/controls.md),
  [Settings](docs/settings.md)

All the details are on the **[documentation site](https://fraygox.github.io/romm-o-cera/)**
(the same pages are in [docs/](docs/README.md)).

## Install

### 1. Make a RomM token

In RomM, log in as the user whose library the box should see (a Viewer-level
user is enough). Go to **Profile → Client API tokens → Create** with these
scopes, and copy the token (`rmm_…`). The app only ever reads from RomM.

```
me.read  roms.read  platforms.read  assets.read  collections.read  firmware.read  roms.user.read
```

### 2. Put the app on the box

Pick one:

- **One line over SSH** (root password is `linux` unless you changed it):
  ```sh
  curl -fsSL https://github.com/fraygox/romm-o-cera/releases/latest/download/install.sh | sh
  ```
- **No SSH:** download `romm-o-cera.zip` from the
  [latest release](https://github.com/fraygox/romm-o-cera/releases/latest) and
  copy its `rommbrowser` folder to `\\BATOCERA\share\roms\pygame\` (the
  box's network share).
- **From a clone** (developers): `ROMM_URL=… ROMM_TOKEN=… ./deploy.sh <box-ip>`
  also writes the config for you ([variables](docs/settings.md#deploysh-installing-from-a-clone)).

### 3. First launch

On the box: **Main Menu → Game Settings → Update Gamelists**, then open
**Pygame → RomM-o-Cera → Settings** enter your RomM address, then
**Pair with RomM** and type the code RomM shows when you press **Pair** on the
token (or type the token itself under API token).
**Hotkey + Start** exits.

Plain HTTP, self-signed certificates and auth proxies:
[Connecting to RomM](docs/connecting.md).

### Updating

On the box: **Settings → Updates**. It downloads the latest release from
GitHub, checks it against the release's SHA256SUMS, confirms every file stays
inside the app folder and compiles, then swaps it in (in one step where the
drive allows it, so a power cut can't leave you without the app) and keeps the
previous version ("Go back to the previous version" appears below Updates).
Re-running the install one-liner works too, and puts back an app folder that
went missing. Your config, queue and logs are never touched.

**Update channel** (Settings, Updates): *Stable* gets releases. *Nightly* also
gets builds of the newest changes as they land: fixes sooner, less tested.
Press Updates to get the newest one now. Back on Stable you keep the nightly
until a release is newer. From the shell: `… install.sh | VERSION=nightly sh`.

## Documentation

Easiest to read on the **[documentation site](https://fraygox.github.io/romm-o-cera/)**, with search.

| | |
|---|---|
| [Browsing and downloading](docs/browsing-and-downloading.md) | finding games, the queue, space, download hours, game info |
| [On this box](docs/on-this-box.md) | installed games, space, compressing what's here, BIOS, Check games, conversions, game info |
| [Disc compression](docs/disc-compression.md) | CHD, RVZ, Xbox, 3DS, Xbox 360, converting a library you already have |
| [PS3 games](docs/ps3.md) | `.ps3` game folders, encrypted discs, where disc keys go (`bios/ps3keys`), keys in RomM |
| [Settings](docs/settings.md) | every setting and `config.json` key, resetting settings |
| [Connecting to RomM](docs/connecting.md) | HTTPS, certificates, network share, auth proxies |
| [Controls](docs/controls.md) | controller, keyboard, touch |
| [Development](docs/development.md) | no-box development, screenshots, tests, files |

## Credits

RomM-o-Cera uses or downloads: Dolphin's **dolphin-tool** (GPL-2.0-or-later,
optional download; its license, the licenses of code it bundles and its
exact source are on the same GitHub release), **extract-xiso** (BSD-style license;
this product includes software developed by in <in@fishtank.com>) and
**ZArchive**'s `zarchive` (MIT-0, with **zstd**, BSD-3-Clause), an optional download
for Xbox 360 to .zar whose licenses and source are on the `xbox360-tools` release,
**chdman** from MAME (BSD-3-Clause /
GPL-2.0-or-later), **pygame**/**SDL** (LGPL-2.1 / zlib), **Requests**
(Apache-2.0) and **7-Zip** (LGPL-2.1) as shipped by Batocera, and the
**Rubik** font (OFL-1.1). It runs on **Batocera.linux** and talks to **RomM**
(AGPL-3.0). The same list is in Settings → Credits and licenses.

## License

GPL-3.0-or-later — see [LICENSE](LICENSE). The bundled Rubik font is © The
Rubik Project Authors, under the SIL Open Font License
([fonts/OFL-Rubik.txt](fonts/OFL-Rubik.txt)). Game names in the demo library are
freely distributed homebrew / open-source titles credited to their authors
(descriptions written for the mock) or invented placeholders.
