# Changelog

Every stable release. Nightly builds: the commits on main since the last one.
Releases up to v0.8.0: [GitHub Releases](https://github.com/fraygox/romm-o-cera/releases).

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

