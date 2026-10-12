# Settings

On the box: **Pygame → RomM-o-Cera → Settings**. Settings are grouped
(RomM server, Controller, Downloads, Disc compression, This app, Updates), and
the panel beside the list explains the selected one. Everything is saved to
`/userdata/system/romm-browser/config.json` straight away.

**Pair with RomM** (in RomM server) signs the box in with the short code
RomM shows when you press Pair on a client API token, instead of typing the
token: see [Connecting](connecting.md#signing-in-pair-with-a-code). It stores
the token in `token`.

**Theme** (in This app) sets the colours of the whole app: Default (RomM
violet), PlayStation, Xbox or Nintendo, each also as an **OLED** version with
a pure black background, plus **High contrast** (black and white with a
yellow highlight) and **High contrast light** (black on white). Left and
right change it, and the app repaints
straight away. Every theme keeps text at least as readable as Default, and
warnings never share a colour with the highlight (in the Nintendo themes,
warnings are coral, not red).

| | |
|---|---|
| ![PlayStation OLED theme](../screenshots/themes/playstation-oled.png) | ![Xbox theme](../screenshots/themes/xbox.png) |
| ![Nintendo theme](../screenshots/themes/nintendo.png) | ![High contrast light theme](../screenshots/themes/high-contrast-light.png) |

**Reset settings** (in This app) puts every setting back to its default,
including the server address and token. The old file is kept beside it as
`config.json.bad-<date>`, so nothing is lost.

## `config.json`

| Key | Default | |
|---|---|---|
| `url` | — | RomM base URL |
| `token` | — | client API token |
| `verify_tls` | `true` | `false` or a CA-bundle path for self-signed certs ([Connecting](connecting.md)) |
| `swap_ab` | `false` | `false`: south button confirms (Batocera's default) |
| `button_style` | `auto` | `auto`, `xbox`, `playstation` or `nintendo` button labels |
| `check_updates` | `true` | daily check of GitHub for a newer release (sends no data beyond the request) |
| `background_downloads` | `true` | keep downloading after the app exits |
| `speed_while_playing` | `0` | MB/s cap for background downloads while a game runs (`0` = none) |
| `download_hours` | `any` | `any`, or a local-time window like `1-7` / `22-6` |
| `keep_free_gb` | `20` | downloads and conversions leave at least this much free (GB); a download that doesn't fit waits for space (`0` = no reserve beyond a small margin) |
| `extract_parallel` | `true` | start the next download while the last one is extracted (`false` for slow storage) |
| `notify` | `true` | EmulationStation popup when background downloads finish or fail |
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
