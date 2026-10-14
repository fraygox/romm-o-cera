# RomM-o-Cera — a RomM client for Batocera

A controller-driven app that runs **on a Batocera box**: browse and search a
[RomM](https://github.com/rommapp/romm) library from the couch and download
games straight into the right `/userdata/roms/<system>/` folder — extracted,
optionally CHD-compressed, with EmulationStation metadata and cover art.

Built for **Batocera 43** (pygame 2.6, Python 3.12) and tested against
**RomM 5.4** (5.3 works too). Draws natively at any resolution, 720p to 4K.

> **Use it with care.** RomM-o-Cera is a hobby project with one author and few
> users so far. It downloads, unpacks, converts and deletes files on your box:
> keep a backup of anything you can't replace, and please file issues.
>
> **Your own games only.** RomM-o-Cera does not include, link to or provide any
> games, BIOS files, firmware or keys. It only talks to the RomM server you run,
> with the games you put on it. Anything a system needs to run — BIOS files,
> firmware, disc or title keys — you must provide yourself, from hardware and
> games you own.

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

- **Find something to play from the couch.** Browse your RomM library by
  platform, collection, recently added or **Recommended for you**, search it
  with an on-screen keyboard, filter a list to games for **2+, 3+ or 4+
  players**, or let **Game night** roll one for tonight's players (and roll
  again if you don't fancy it). Your RomM **Backlog** downloads in one go,
  what fits first.
  → [Browsing and downloading](docs/browsing-and-downloading.md)
- **Download and play.** A game lands in the right `/userdata/roms/<system>/`
  folder, unpacked, checked against RomM's checksums, with its cover,
  description and manual in EmulationStation. **Play now** starts it as soon
  as it's ready. Multi-disc games become one playlist entry.
- **Queue as much as you like.** Whole lists and collections at once, picked
  several at a time in any list. Downloads resume, keep going after you exit
  and while you play, wait for space instead of filling the drive (counting
  what each zip really unpacks to), and can be kept to overnight hours.
- **Keep the box tidy.** One menu shows how the box is doing: delete games
  (saves are kept), see where the space went and free some up, clean up
  leftovers, fetch missing BIOS files from RomM, and check for damaged or
  cut-short games and replace them. → [On this box](docs/on-this-box.md)
- **Save space without losing anything.** Disc games are compressed in the
  background, lossless and verified: CHD for CD systems, PS2 and PSP; RVZ
  for GameCube and Wii; squashfs for Xbox; .zcci for 3DS; .zar for Xbox
  360; .wux for Wii U, or .wua with your own disc keys. Games already on the
  box can be compressed too. → [Disc compression](docs/disc-compression.md),
  [Wii U games](docs/wiiu.md)
- **PS3 games as game folders** RPCS3 boots and updates; encrypted discs
  use disc keys for games you own, which you provide (off by default).
  → [PS3 games](docs/ps3.md)
- **Make it yours.** Button labels match your controller (Xbox, PlayStation,
  Nintendo) or keyboard; 26 colour themes in a picker that previews them
  live, OLED versions, a Custom theme of your own colours, or a theme that
  follows your controller. Touch screens and mice work too.
  → [Themes](docs/themes.md), [Controls](docs/controls.md)
- **Looks after itself.** Settings explain themselves and a QR code opens
  this documentation on your phone; updates install from the box (Stable or
  Nightly), restart the app by themselves and can be undone in one step.
  → [Settings](docs/settings.md)

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

On the box:

1. **Main Menu → Game Settings → Update Gamelists**, so RomM-o-Cera shows up
   under **Pygame**.
2. Open **Pygame → RomM-o-Cera**, then **Settings → Server address**, and
   enter your RomM's address.
3. **Settings → Pair with RomM**: in RomM press **Pair** on the token, and
   type the code it shows (or type the token itself under **API token**).

Back on the home screen, your platforms load. **Hotkey + Start** exits.

Plain HTTP, self-signed certificates and auth proxies:
[Connecting to RomM](docs/connecting.md).

### Updating

On the box: **Settings → Updates**. It downloads the latest release from
GitHub, checks it against the release's SHA256SUMS, confirms every file stays
inside the app folder and compiles, then swaps it in (in one step where the
drive allows it, so a power cut can't leave you without the app) and keeps the
previous version ("Go back to the previous version" appears below Updates).
The app then closes and opens again on the new version by itself; downloads
pause for that moment and carry on.
Re-running the install one-liner works too, and puts back an app folder that
went missing. Your config, queue and logs are never touched.

**Update channel** (Settings, Updates): *Stable* gets releases. *Nightly* also
gets builds of the newest changes as they land: fixes sooner, less tested.
Press Updates to get the newest one now. Back on Stable you keep the nightly
until a release is newer. From the shell, `… install.sh | VERSION=nightly sh`
installs the newest nightly and sets the channel to Nightly, so the box keeps
following it.

## Documentation

Easiest to read on the **[documentation site](https://fraygox.github.io/romm-o-cera/)**, with search.

| | |
|---|---|
| [Browsing and downloading](docs/browsing-and-downloading.md) | finding games, Game night, Backlog, filters, Play now, the queue, space, download hours, game info |
| [On this box](docs/on-this-box.md) | installed games, space, compressing what's here, BIOS, Check games, conversions, game info |
| [Disc compression](docs/disc-compression.md) | CHD, RVZ, Xbox, 3DS, Xbox 360, Wii U, converting a library you already have |
| [PS3 games](docs/ps3.md) | `.ps3` game folders, encrypted discs, where disc keys go (`bios/ps3keys`), keys in RomM |
| [Wii U games](docs/wiiu.md) | `.wud`, `.wux` and `.wua`, the two Wii U switches, disc keys, updates and DLC |
| [Settings](docs/settings.md) | every setting and `config.json` key, resetting settings |
| [Themes](docs/themes.md) | the theme picker, every theme, your own Custom theme, matching your controller |
| [Connecting to RomM](docs/connecting.md) | HTTPS, certificates, network share, auth proxies |
| [Controls](docs/controls.md) | controller, keyboard, touch and mouse, every screen's buttons |
| [Development](docs/development.md) | no-box development, screenshots, the docs site, releasing, tool builds, tests, files |

## Credits

RomM-o-Cera uses or downloads: Dolphin's **dolphin-tool** (GPL-2.0-or-later,
optional download; its license, the licenses of code it bundles and its
exact source are on the same GitHub release), **extract-xiso** (BSD-style license;
this product includes software developed by in <in@fishtank.com>) and
**ZArchive**'s `zarchive` (MIT-0, with **zstd**, BSD-3-Clause), an optional download
for Xbox 360 to .zar whose licenses and source are on the `xbox360-tools` release,
**wuatool**, built on **Cemu**'s disc code (MPL-2.0) with ZArchive, zstd and OpenSSL
(Apache-2.0), an optional download for Wii U to .wux and Decrypt Wii U discs whose licenses and source are
on the `wiiu-tools` release,
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
