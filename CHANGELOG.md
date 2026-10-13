# Changelog

Every stable release. Nightly builds: the commits on main since the last one.
Releases up to v0.8.0: [GitHub Releases](https://github.com/fraygox/romm-o-cera/releases).

## v0.10.0 (2026-10-13)

a new Home with shelves, Collections as shelves of covers, a game page, Activity for downloads and compressing, a first-run Welcome, Game night, your RomM backlog, a live theme picker with 26 themes, and Wii U compression

### Features
- **feat:** Home shows shelves of what you're playing, recommended, new in RomM, your backlog and collections beside a rail of destinations, with the focused game's cover and Play now or Download.
- **feat:** A game page shows a game's facts, your RomM status, rating, note and achievements, screenshots and a QR code to open it in RomM; for a game on the box it compares this box with RomM.
- **feat:** Activity shows downloads and on-box compressing side by side, says when everything is done, and pauses, resumes or stops the running conversion.
- **feat:** A first-run Welcome walks a new box through the server address (TLS options under Advanced), pairing with RomM and its first platform.
- **feat:** Collections are shelves of cover mosaics by kind, each tile showing how much of it is on this box; Y shows a whole kind as a wall, and a collection's games filter by what's here.
- **feat:** Settings, Collections tile size shows more or fewer collections at once (Small, Medium, Large).
- **feat:** A on any game opens its page, Home's shelves included; Play now or Download is the page's big button.
- **feat:** Game night rolls a game for tonight's players, from your whole library or only what's on this box, and keeps the rolls you passed on.
- **feat:** Backlog lists your RomM backlog and downloads it in one go, what fits first.
- **feat:** Compare with RomM, inside a platform or collection, shows which games match RomM, are newer in RomM or aren't in RomM, and gets the newer copy.
- **feat:** What's new shows the release notes once after an update.
- **feat:** One on-screen keyboard everywhere, with a digit row, a symbols layer and keys made for each field.
- **feat:** Compress, Leftovers, Conversions, Credits, BIOS, Free up space and Games ES can't see show a list with a detail panel; Compress says how much smaller each system gets.
- **feat:** Settings → Theme opens a picker with every theme as a card: moving previews it across the whole app, A keeps it, Y switches a card to OLED.
- **feat:** Twenty new themes, from Brick and Red Visor OLED to Synthwave, Nord and Paper; the Nintendo themes are now called Kyoto.
- **feat:** A Custom theme lets you pick your own colours (accent, background tint, tint strength and dark, OLED or light), and it always stays easy to read.
- **feat:** Match theme to controller (off by default) picks PlayStation, Xbox or Kyoto colours to match your pad.
- **feat:** Wii U compression: Wii U to .wux (on by default) shrinks disc images losslessly with no keys, often by 90%; Decrypt Wii U discs makes .wua for the games whose disc key you have, with the disc's own update and DLC inside, so Cemu starts them updated and needs no key to play. The tool installs itself when there are Wii U games to compress.
- **feat:** Online in the header and Settings says which RomM version the server runs, and Test connection names it.
- **feat:** Play now starts a game you just downloaded: RomM-o-Cera closes and EmulationStation starts it as soon as it's listed.
- **feat:** The Players filter (2+, 3+, 4+) shows only games for that many players, for a game night.
- **feat:** Recommended for you on the home screen lists the games RomM picks from what you play, each with why.
- **feat:** X on a theme in the picker starts your Custom theme from it.
- **feat:** Updating or going back restarts RomM-o-Cera by itself: downloads pause and carry on when it's back, and nothing of the old version keeps running.
- **feat:** Every list picks several rows the same way, jumps with L2/R2 and pages with left/right, and Games from RomM gets covers, details, a sort and the game's menu.
- **feat:** Search's top matches work with the controller and keyboard, and open a game's menu if it's here.
- **feat:** Check games checks RVZ, squashfs and PS3 game folders too, and names any tool it's missing.
- **feat:** Compress lists the games it can't convert (an encrypted dump, a disc without its key, a damaged .cso) with the reason.
- **feat:** Disc compression also waits while EmulationStation scrapes, installs, hashes or updates.
- **feat:** EmulationStation shows a message box when background downloads are stuck on a full drive or a refused token.
- **feat:** Settings, Documentation shows a QR code that opens the documentation on your phone.
- **feat:** Settings can turn off the daily update check; Updates still checks when you select it, and turning it back on checks straight away.
- **feat:** Controllers without an SDL mapping get L2 and R2.
- **feat:** A tap left of a choice's value steps back, in Settings, menus and the hint bar.

### Performance
- **perf:** New downloads show up in EmulationStation without it reloading every system.

### Fixes
- **fix:** A zipped game is unpacked when the system's emulator can't open zips, so N64 games start with Batocera's default emulator.
- **fix:** X is labelled Actions on every pad, so Xbox no longer shows two Menu buttons.
- **fix:** The smallest text is a little bigger, and tags, counts and never-played lines use the readable grey.
- **fix:** Saving settings on a box configured by ROMM_URL/ROMM_TOKEN no longer writes them into config.json.
- **fix:** A click or tap acts on exactly the row under it, on scrolled lists too.
- **fix:** A menu taller than the screen scrolls to the selected row, and Reset settings' hint no longer offers left/right.
- **fix:** Resume all right after Pause all no longer leaves a download paused.
- **fix:** Cancelling a paused download asks first, and says how much of it is deleted.
- **fix:** A key tapped after a tapped search match types instead of reopening the match.
- **fix:** The same message shown again renews its toast with a count instead of stacking copies.
- **fix:** Low-memory mode can go back to Auto in Settings, and the Settings page documents every row.
- **fix:** Compress's can't-convert list names the fix for that system only.
- **fix:** A finished conversion and the compress command line show sizes in the app's units.
- **fix:** A stopped disc compression leaves no half-written file behind.
- **fix:** romm-browser.log starts a new file every 10 MB and keeps the last nine.
- **fix:** Space estimates read what a zip really holds from RomM, and a queue counts each zip only until it's unpacked, so Downloads and "the N that fit" ask for the room they really need.
- **fix:** A setting's help uses the panel's room, so the longer ones (Xbox 360 to .zar, Decrypt PS3 discs) show whole.

### Under the hood
- **refactor:** The app's screens, drawing, input and theme live in their own modules instead of one 7,600-line file.
- **refactor:** Every call to EmulationStation, from the app and the background helpers, goes through one small client with the same timeouts and errors.
- **refactor:** The background workers share one lock helper, and the library planner takes its system order from the converter table.
- **refactor:** The library hands out a snapshot of its records; the conversion screens read the worker's state through one function.
- **refactor:** dolphin-tool and the Xbox 360 tools are described once, and installed and offered by one path.
- **refactor:** deploy.sh swaps the whole app folder in one step, as the in-app updater does.

### Upgrading
- **Close RomM-o-Cera and open it again once this update is installed**, before compressing or downloading
  anything (the update's last screen has the button). Until you do, the old version still running can
  stop with an error on the Compress screen, and from v0.7.x it can't copy a game's saves to its new file
  name when a download replaces a differently named copy (the saves stay under the old name). From this
  version on, updating closes the app by itself.

## v0.9.0 (2026-10-12)

update channels, half the memory at 4K, and a busy RomM no longer breaks covers or downloads

### Features
- **feat:** *Update channel* (Settings → Updates): **Stable** gets releases; **Nightly** also gets builds of the newest changes as they land: fixes sooner, less tested. Updates finds the newest one straight away, and back on Stable you keep the nightly until a release is newer. From the shell: `curl -fsSL https://github.com/fraygox/romm-o-cera/releases/latest/download/install.sh | VERSION=nightly sh`.

### Performance
- **perf:** Memory stays bounded however long you browse. Scrolling a long list at 4K used to grow the app past 1.1 GB; it now levels off around 450 MB, and boxes with under 3 GB of RAM keep half as much again. Covers are kept at the size they're drawn, and long lists keep only the pages near the cursor.
- **perf:** Check games, BIOS lookups and long game lists ask RomM several things at once: a full check of a large library takes about 40% less time, and keeps only the games on the box in memory.
- **perf:** Covers load on a fixed set of threads, newest first, so the screen you're looking at fills before the rows you scrolled past.

### Fixes
- **fix:** When RomM (or the proxy in front of it) is busy, the app sends it fewer requests at once and tries again after the wait it asks for, for covers, lists and downloads alike, instead of every request failing. Connections are kept open and shared, including after an error.
- **fix:** A cover that didn't load is fetched again a little later, instead of showing "no cover" for the rest of the session.
- **fix:** Korean titles, Greek letters and symbols such as Ⅱ and ‼ draw instead of showing as boxes.
- **fix:** Batocera's screenshot hotkey no longer hides the app behind a white screen.
- **fix:** A quick keyboard tap moves the selection once instead of running on by itself.
- **fix:** A screen that can't reach RomM says so in red, instead of showing an empty or all-clear list.
- **fix:** Compress can make Xbox images bootable even when Xbox to squashfs is off.
- **fix:** "Convert next" and "Remove from the queue" leave alone a conversion that has already started or finished.
- **fix:** Deleting a multi-disc set frees its space for conversions waiting on it.
- **fix:** Multi-disc playlists are written all at once, so a crash can't leave half of one.
- **fix:** Without chdman, Check games reports the CHDs it couldn't verify, instead of calling them damaged.
- **fix:** The app starts on a full disk instead of crashing.
- **fix:** BIOS and PS3 key downloads are written to disk in full before they're put in place.

### Under the hood
- **ci:** Every change is now published to GitHub as it's made; GitHub Actions test each one, build the nightly, and publish releases and the docs site.

