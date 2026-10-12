# Settings

On the box: **Pygame → RomM-o-Cera → Settings**. Settings are grouped
(RomM server, Controller, Downloads, Disc compression, This app, Updates), and
the panel beside the list explains the selected one. Everything is saved to
`/userdata/system/romm-browser/config.json` straight away.

The rows that aren't a setting do something when you press confirm:

**RomM server**

- **Pair with RomM** signs the box in with the short code RomM shows when
  you press Pair on a client API token, instead of typing the token: see
  [Connecting](connecting.md#signing-in-pair-with-a-code). It stores the token
  in `token`.
- **Test connection** asks RomM for its platform list right now and says how
  many platforms it found, or why it couldn't.

**This app**

- **Clear cover cache** deletes every cached cover (the row shows how much
  they take); they download again as you browse.
- **BIOS files** opens the BIOS check: what each system needs, what's
  missing, and what your RomM can fill in.
- **Credits and licenses** lists the software RomM-o-Cera is built on or
  downloads, and its licenses.
- **Documentation** shows a QR code for this documentation in the panel
  beside the list; point your phone's camera at it to open the site. Confirm
  shows the code full size, for a far couch.
- **Reset settings** puts every setting back to its default, including the
  server address and token. The old file is kept beside it as
  `config.json.bad-<date>`, so nothing is lost.

**Updates**

- **Update channel** picks Stable (tested releases) or Nightly (also builds
  of the newest changes); switching asks GitHub again straight away
  ([Updating](../README.md#updating)).
- **Check for updates daily**: once a day the app asks GitHub whether a
  newer version is out on your channel, and says so. Off, there's no daily
  check, but **Updates** still checks whenever you select it.
- **Updates** checks GitHub now. When a newer version is out, the row names
  it, and confirm downloads it and checks it, then closes the app to install
  it, keeping the version you had. Downloads in progress pause and carry on
  when you open the app again; a disc being compressed stops (its originals
  stay) and starts over later. Nothing of the old version keeps running in
  the background. EmulationStation then says the new version is ready: open
  it again from Pygame.
- **Go back to the previous version** (only after an update or a `deploy.sh`) swaps the
  version you had back in and closes the app the same way. The one you leave
  is kept, so this can be undone too.

**Low-memory mode** (in Disc compression) is Auto, On or Off: Auto turns it
on for boxes with less than 4 GB of RAM, and the row says which it is and
why ("Auto (Off, 8.0 GB RAM)").

**Theme** (in This app) sets the colours of the whole app: Default (RomM
violet), PlayStation, Xbox or Nintendo, each also as an **OLED** version with
a pure black background, plus **High contrast** (black and white with a
yellow highlight) and **High contrast light** (black on white). Left
and right change the theme, and the app repaints straight away. Every theme
keeps text at least as readable as Default, and warnings never share a
colour with the highlight (in the Nintendo themes, warnings are coral, not
red). A new install starts on Default.

**Match theme to controller** (off by default) uses the colours of the
controller the app sees instead of your Theme: PlayStation, Xbox or Nintendo,
in its OLED version if your Theme is an OLED one. With a keyboard, or a
controller whose name doesn't say who made it, your Theme stays. When Button
labels is set by hand, it follows that instead. High contrast themes are never
replaced. It changes as soon as a controller connects or disconnects, and the
Theme row shows what's on screen ("Default (now PlayStation)").

| | |
|---|---|
| ![PlayStation OLED theme](../screenshots/themes/playstation-oled.png) | ![Xbox theme](../screenshots/themes/xbox.png) |
| ![Nintendo theme](../screenshots/themes/nintendo.png) | ![High contrast light theme](../screenshots/themes/high-contrast-light.png) |

## `config.json`

| Key | Default | |
|---|---|---|
| `url` | — | RomM base URL |
| `token` | — | client API token |
| `verify_tls` | `true` | `false` or a CA-bundle path for self-signed certs ([Connecting](connecting.md)) |
| `verify_tls_ca` | — | written by the app: the CA-bundle path `verify_tls` last held, so the Settings row can cycle back to it after On or Off |
| `swap_ab` | `false` | `false`: south button confirms (Batocera's default) |
| `button_style` | `auto` | `auto`, `xbox`, `playstation` or `nintendo` button labels |
| `check_updates` | `true` | daily check of GitHub for a newer version (sends no data beyond the request); `false`: only when you select Updates |
| `update_channel` | `stable` | `stable`: releases; `nightly`: also builds of the newest changes ([Updating](../README.md#updating)) |
| `background_downloads` | `true` | keep downloading after the app exits |
| `speed_while_playing` | `0` | MB/s cap for background downloads while a game runs (`0` = none) |
| `download_hours` | `any` | `any`, or a local-time window like `1-7` / `22-6` |
| `keep_free_gb` | `20` | downloads and conversions leave at least this much free (GB); a download that doesn't fit waits for space (`0` = no reserve beyond a small margin) |
| `extract_parallel` | `true` | start the next download while the last one is extracted (`false` for slow storage) |
| `notify` | `true` | EmulationStation popup when background downloads finish or fail (a full drive or a refused token gets a message box either way) |
| `manuals` | `true` | fetch the game's manual PDF from RomM into `roms/<system>/manuals/` |
| `chd` | `true` | background CHD compression of extracted disc games |
| `chd_threads` | `auto` | conversion threads: `auto` (N−2 with 6+ threads, N−1 with 3–5) or a number |
| `chd_low_memory` | `auto` | compress CDs without chdman's FLAC codec, which leaks up to ~1.3 GB per CD in some chdman builds: `auto` (on with less than 4 GB RAM), `true`, `false`. CD audio tracks come out a bit bigger. DVDs never use FLAC |
| `rvz` | `false` | convert GameCube/Wii images to RVZ (turning it on in Settings downloads dolphin-tool) |
| `xbox_bootable` | `true` | Xbox: keep only the game partition xemu boots; rename `.xiso` to `.iso` |
| `squashfs` | `false` | pack Xbox images into zstd squashfs (Batocera mounts them at launch) |
| `z3ds` | `true` | compress decrypted 3DS images to Azahar's `.zcci` |
| `ps3_folders` | `true` | PS3 disc images (loose or zipped `.iso`) become `.ps3` game folders ([PS3 games](ps3.md)) |
| `ps3_decrypt` | `false` | decrypt encrypted PS3 discs with disc keys for games you own, from `/userdata/bios/ps3keys` ([where disc keys go](ps3.md#where-disc-keys-go)) |
| `zar` | `false` | convert Xbox 360 images to `.zar` for xenia-canary (x86_64; turning it on in Settings downloads two small tools) |
| `theme` | `default` | colours: `default`, `playstation`, `xbox`, `nintendo`, or any of them with `_oled` (pure black background), e.g. `xbox_oled`; `high_contrast`, `high_contrast_light` |
| `theme_auto` | `false` | `true`: a PlayStation, Xbox or Nintendo controller picks its maker's theme (Match theme to controller) |
| `sounds` | `normal` | UI sounds: `normal`, `quiet` or `off` |
| `cache_limit_mb` | `1024` | cover cache cap in MB; least recently used covers are removed first (`0` = no cap) |
| `platform_map` | `{}` | override the target system per RomM platform slug, e.g. `{"arcade": "mame", "msx": null}` (`null` hides it) |
| `config_version` | `2` | written by the app, last; leave it alone |

A value the app can't use, such as text where a number belongs
(`"keep_free_gb": "lots"`), counts as that key's default: the app writes a line
about it to `romm-browser.log` and saves the default the next time a setting
changes. On/off keys take any value as before: `false`, `0` and `null` are off,
anything else is on (so `"false"` in quotes is on).

### If `config.json` can't be read

A typo from editing it by hand doesn't cost you your settings. The app starts
on defaults, keeps a copy of the file as `config.json.bad-<date>`, and says so
at start. Until you fix it, nothing is saved over it: Settings changes still
apply, but only until you exit. Either fix the file over SSH and restart, or
pick **Reset settings** (in that message, or in Settings) and enter the
server and token again.

## `deploy.sh` (installing from a clone)

`deploy.sh <box>` ships the same files as the release zip and swaps the whole
app folder in one step, as an in-app update does: it uploads the new copy to
`/userdata/system/romm-browser/app-staging`, checks that it compiles on the
box, and only then swaps it in; the version it replaces is kept for **Go back
to the previous version**. A copy that fails the check changes nothing. It
won't deploy while a game (or the app) is running unless `FORCE=1`, and never
restarts anything.

| Variable | |
|---|---|
| `ROMM_URL` | RomM base URL, `https://…` or `http://…`. Unset = keep the box's current value. |
| `ROMM_TOKEN` | client API token. Unset = keep the box's current value. |
| `ROMM_VERIFY_TLS` | `false` for a self-signed certificate, or a path (on the box) to a CA bundle. |
| `SSH_KEY`, `SSH_USER` | SSH identity / user (default: your ssh config, `root`). |

## Environment

The app also reads `ROMM_URL`, `ROMM_TOKEN` and `ROMM_VERIFY_TLS` from its
environment (they win over `config.json`), and these paths:
`ROMM_BROWSER_DATA` (default `/userdata/system/romm-browser`),
`ROMM_BROWSER_ROMS` (`/userdata/roms`), `ROMM_BROWSER_SAVES`
(`/userdata/saves`), `ROMM_BROWSER_ES_SYSTEMS` (path to an
`es_systems.cfg`), `ROMM_BROWSER_ES` (ES API, `http://127.0.0.1:1234`),
`ROMM_BROWSER_SCALE` (headless render scale).
