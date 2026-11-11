# Changelog

Every stable release. Nightly builds: the commits on main since the last one.
Releases up to v0.8.0: [GitHub Releases](https://github.com/fraygox/romm-o-cera/releases).

## v0.13.0 (2026-11-11)

A sweep: safer deletes and taps, clearer connection errors, and a quieter idle

### Features
- **feat:** A still screen redraws a few times a second instead of sixty, so the app uses far less battery while you're not touching it. Any input wakes it at once.
- **feat:** Dates read the same on every screen: "3 Oct 2026", with the time where it matters.
- **feat:** On a keyboard, F2 stands in for Y while a text field is open, so Welcome's Advanced can be reached without a controller.
- **feat:** Tapping the last cover in view on a shelf brings the next one into view, so a finger reaches a whole shelf.
- **feat:** Compress disc games to CHD says "Not available" on a box whose chdman is missing or won't run, and disc games there stay as they downloaded and play.

### Fixes
- **fix:** Free up space, Download again and Games ES can't see no longer delete a game's art, video and manual.
- **fix:** Starting RomM-o-Cera a second time while it's running no longer wrecks the download in progress.
- **fix:** A double tap never confirms a delete or changes a setting on the screen the first tap opened.
- **fix:** A game converted under a new name no longer writes its art over a file already there under that name.
- **fix:** The delete confirm in Games from RomM no longer says art and video will be removed.
- **fix:** A server address that answers something other than RomM no longer crashes the app at every start.
- **fix:** Updates says it couldn't ask GitHub when it couldn't, instead of "You have the latest version".
- **fix:** A plain-http RomM typed as https is told to use `http://`, and the `http://` key replaces the `https://` already typed.
- **fix:** A RomM that has moved to another address or wants https says so, instead of blaming the token.
- **fix:** Welcome says why it came back when RomM turned the saved token away.
- **fix:** The header stops saying Online once RomM can't be reached or rejects the token, and Test connection brings it back.
- **fix:** A search that fails says so instead of showing "Searching…" forever, and a game list that fails shows the whole reason.
- **fix:** A library that grows while it's being listed no longer shows a game twice.
- **fix:** Delete and other coloured buttons have readable labels in every theme.
- **fix:** Activity no longer gives a finish time while a download is paused or waiting for space.
- **fix:** Conversions and On this box count the queue the same way.
- **fix:** An empty leftover folder is no longer shown as "0 B of leftovers", and the Space panel's rule no longer runs through a row.
- **fix:** Tapping the logo on a text field no longer saves what's typed.
- **fix:** Home, PgUp and PgDn work while typing, and a held Backspace keeps deleting.
- **fix:** What's new shows a command from the release notes as written.
- **fix:** An interrupted tool download no longer leaves a partial file behind.
- **fix:** A CA bundle that isn't there is named in the error.

### Docs
- **docs:** First-launch steps in the README and installer describe Welcome.
- **docs:** [Controls](https://github.com/fraygox/romm-o-cera/blob/main/docs/controls.md), Browsing and [Settings](https://github.com/fraygox/romm-o-cera/blob/main/docs/settings.md) match the app: Activity's name, Backlog's place, the Pair button, the picking menu, and the settings rows that had no description.

## v0.12.3 (2026-11-10)

Clearer touch and reporting steps, and a log that says its version

### Fixes
- **fix:** The log's start line says the app's version, so a log sent with a report says which build wrote it.
- **fix:** What's new shows a command from the release notes without its code-fence line.

### Docs
- **docs:** [Reporting a problem](https://github.com/fraygox/romm-o-cera/blob/main/docs/reporting-a-problem.md) points at where the app's version and Batocera's are shown, and [Controls](https://github.com/fraygox/romm-o-cera/blob/main/docs/controls.md) says which things open on one tap and which on two.

### Upgrading
v0.12.1 made controllers use Batocera's own mapping (the Steam Deck and other handhelds). If your controller can't drive the app, you can't reach Settings → Updates with it. Any of these gets you this version; your settings, queue and downloaded games are kept.

- **Touch screen or keyboard** (a Steam Deck's screen works): open the app and tap **Settings** on Home, then **Updates**, then the **Updates** row that names the new version, then **Update and restart**. One tap each. If the app opens on Welcome because no RomM server is set yet, tap **Later** in the bar at the bottom first. On a keyboard: arrows, Enter, and Esc to go back.
- **Over SSH** (user `root`, password `linux` unless you changed it): run the installer again.
  ```
  curl -fsSL https://github.com/fraygox/romm-o-cera/releases/latest/download/install.sh | sh
  ```
- **No SSH:** download `romm-o-cera.zip` from this release and copy its `rommbrowser` folder over the one in `\\BATOCERA\share\roms\pygame\`, replacing the files.

Then start RomM-o-Cera again. If a button still does the wrong thing, copy `romm-browser.log` from `\\BATOCERA\share\system\romm-browser\` and attach it to an issue: its `pad:` lines say how the app reads your controller.

## v0.12.2 (2026-11-10)

The log says how each controller is read, and the docs say how to report a problem

### Fixes
- **fix:** The log names each controller, its ID and whether Batocera had a mapping for it, so a report about buttons can be answered from one file.

### Docs
- **docs:** [Reporting a problem](https://github.com/fraygox/romm-o-cera/blob/main/docs/reporting-a-problem.md) says what to send with a report: the version, how to get the log off the box, and what the controller lines in it mean.

### Upgrading
v0.12.1 made controllers use Batocera's own mapping (the Steam Deck and other handhelds). If your controller can't drive the app, you can't reach Settings → Updates with it. Any of these gets you this version; your settings, queue and downloaded games are kept.

- **Touch screen or keyboard** (a Steam Deck's screen works): open the app and tap **Settings** on Home, then **Updates**, then the **Updates** row that names the new version, then **Update and restart**. One tap each. On a keyboard: arrows, Enter, and Esc to go back.
- **Over SSH** (user `root`, password `linux` unless you changed it): run the installer again.
  ```
  curl -fsSL https://github.com/fraygox/romm-o-cera/releases/latest/download/install.sh | sh
  ```
- **No SSH:** download `romm-o-cera.zip` from this release and copy its `rommbrowser` folder over the one in `\\BATOCERA\share\roms\pygame\`, replacing the files.

Then start RomM-o-Cera again. If a button still does the wrong thing, copy `romm-browser.log` from `\\BATOCERA\share\system\romm-browser\` and attach it to an issue: its `pad:` lines say how the app reads your controller.

## v0.12.1 (2026-11-10)

Controllers use Batocera's own mapping, so the Steam Deck and other handhelds work

### Fixes
- **fix:** Controllers use the mapping Batocera has for them: the one EmulationStation ships for the pad, or the one you made in Controller & Bluetooth settings → Controller mapping. Before this the app guessed a pad's buttons by number, which only suited Xbox-style and plain USB pads. On a Steam Deck the buttons did the wrong things and the D-pad did nothing, and other handhelds and pads with their own button order were off too. There's nothing to map in the app.

### Upgrading
If your controller can't drive the app, you can't reach Settings → Updates with it. Any of these gets you this version; your settings, queue and downloaded games are kept.

- **Touch screen or keyboard** (a Steam Deck's screen works): open the app and tap **Settings** on Home, then **Updates**, then the **Updates** row that names the new version, then **Update and restart**. One tap each. On a keyboard: arrows, Enter, and Esc to go back.
- **Over SSH** (user `root`, password `linux` unless you changed it): run the installer again.
  ```
  curl -fsSL https://github.com/fraygox/romm-o-cera/releases/latest/download/install.sh | sh
  ```
- **No SSH:** download `romm-o-cera.zip` from this release and copy its `rommbrowser` folder over the one in `\\BATOCERA\share\roms\pygame\`, replacing the files.

Then start RomM-o-Cera again. If a button still does the wrong thing, it's wrong in Batocera's mapping for the pad: map it again in EmulationStation (Main menu → Controller & Bluetooth settings → Controller mapping) and start the app again.

## v0.12.0 (2026-11-06)

Your games and files are safe: the app changes only what it downloaded or what a confirm lists, and Check games and On this box show what needs a look at a glance

### Features
- **feat:** Check games lists the last check's findings beside a panel that says what's wrong and what downloading again does. Y checks again, X Actions has a quick and a full check, and Games ES can't see sits under the findings.
- **feat:** On this box opens with one line about the whole box over its space bar. Each section shows its state at a glance: a value on the right, and a coloured line and edge when it needs a look.
- **feat:** Y searches in every list of games (a collection's filter moved to the pills and X Actions). Home's and Game night's panels say where a game stands; A opens its page, where Play now or Download is. Section labels are in sentence case.

### Your files are safe
- **fix:** Each download records the files it wrote, so deleting one of RomM-o-Cera's games takes only those. A patch, cheat, translation, save or note you add to its folder stays. A file of the app's that something else changed or replaced since is treated like one you put there.
- **fix:** Games you put on the box yourself can still be deleted, freed up, downloaded again, repaired by Check games and compressed. The confirm says the game wasn't downloaded by RomM-o-Cera and lists exactly the files that go or get replaced: the game file and the tracks its sheet names (in a folder named like the game, the files named like it), or a playlist of yours alone. Your notes, saves, patches, cheats, other games beside it, and tracks or discs another sheet or playlist uses are never touched. Download all damaged again says how many weren't downloaded by RomM-o-Cera.
- **fix:** A confirm acts only on the files it showed: a file swapped while it's open, or before a queued download runs, is left alone.
- **fix:** Nothing automatic writes over a file the app didn't write. A download unpacks beside your files in a game's folder, never over them, and compresses only what it brought.
- **fix:** Art, videos and manuals are never deleted or written over. Deleting a game leaves them. Downloads and Fill in game info add art only where nothing is there yet under EmulationStation's name for it, and never write through a link.
- **fix:** A folder you made that's named like a RomM game is that game only when a game file in it is named like it too, so Delete, Check games and Compare never take your cheats, patches, saves or other games in it for RomM's. Patches and cheats (`.ips`, `.bps`, `.ppf`, `.cht`…) are never taken for a game.
- **fix:** Compressing never replaces or deletes a file it didn't make (unless that file is certainly cut short and a fresh copy of the game came down beside it). A game whose compressed name is already taken is listed under "can't convert" with what to do. A game in its own folder keeps the folder when it holds anything of yours, and a playlist of yours moves beside the compressed discs. A disc still being unpacked or copied isn't offered yet.
- **fix:** Clean up leftovers offers a converted game's originals only when the converted file checks out and nothing still uses them. It never offers a zip holding anything besides the disc, a `.7z` kept beside a CHD, another program's partial download, or a partial or temporary file changed in the last hour.
- **fix:** Games ES can't see › Unpack keeps the Keep free on disk reserve, and refuses an archive named like a game ES already lists beside it. Memory cards and other saves are no longer listed there.

### Fixes
- **fix:** Cancelled, interrupted and repaired downloads, disc compression, updates and background hand-overs no longer leave a broken or half-deleted game or app behind. Closing the app or updating stops an unpack in progress; it starts again later, with nothing half-unpacked left behind.
- **fix:** A disc image missing a quarter or more of its disc counts as cut short: Check games marks it damaged and it's never compressed (and its original deleted) as if it were whole.
- **fix:** Compare says "Differs from RomM's" for a copy that isn't the same as RomM's (a CHD cut short included), and "Newer in RomM" only when RomM's file changed since the app downloaded it. A compressed copy the app didn't make matches by name only (Check games' Full check catches a damaged one), and a folder holding only your own files is no longer a match.
- **fix:** A game whose file was changed since it was downloaded says so, a game whose later download failed still shows as on the box with Play now and Delete, and a retried download is compressed as soon as it's in.
- **fix:** Fill in game info no longer lists a game again and again for art it won't write over, and its confirm says plainly what it adds.
- **fix:** Game night starts over when you change who's playing or Only what's on this box, so a roll that finds nothing no longer leaves a game that doesn't fit on screen.
- **fix:** Changing a setting when the disk is full says it couldn't be saved instead of closing the app.

### Upgrading
- A game downloaded before this version has no list of its files, so deleting it still takes the game files in its folder (never your notes, saves, patches or cheats). Games downloaded from now on are deleted file by file.
- If you downloaded a game again with v0.11.2 or earlier and got "already on the box under that name", delete the leftover `.zip` beside its CHD.

### Known limits
- Your art under the names EmulationStation gives a game's art (`images/<game>-image.png`…) that no gamelist entry links stays on disk but isn't linked, so that game shows no picture of that kind; link it in EmulationStation's metadata editor. Older downloads whose image is only the box art again keep it.
- Downloading again over a game you copied in yourself gives it RomM's name and art, not the name and art links you had set (your files stay). Downloading into a folder that also holds a near-same-named sheet of yours (a translation) can still give RomM's name to your sheet.
- A converted disc is checked against its own checksums and RomM's size, not RomM's contents: a different disc of exactly the same size put in place of a game's CHD passes Full check, and Compare goes by names.
- Compare can show a folder named like a game, holding only a sheet of yours whose tracks are missing, as a match (Check games flags it).
- A PS3 disc left partly decrypted by another tool is caught in most layouts but not all (see the PS3 page).

## v0.11.2 (2026-10-17)

Faster game info away from home, and clearer words, counts and buttons on every screen

### Performance
- **perf:** Game info and media reach EmulationStation up to 7 times faster when RomM is far away: they're fetched several at a time, as many as the box's CPU and the connection allow. A small box stays at two at a time.

### Fixes
- **fix:** Compressing shows what happened in words: "to CHD · 22.0 MB, now 147 KB", or the reason it was skipped, never the worker's log line; a queued disc shows its real size, tracks included.
- **fix:** Activity counts the queue the way the header does, says how many are paused, and downloads held by Pause all say On hold.
- **fix:** Counts of one read as one ("Delete 1 game?"), a one-game collection on the box says On this box, platform tiles always say games, and a shelf's position reads 4 / 46 so it isn't mistaken for what's on the box.
- **fix:** Deleting asks Keep or Delete; Leftovers, Free up space and Conversions show where they are (On this box › Space › …); the address and token editors are named as their Settings rows; Settings only shows "Now:" for rows that hold a setting.
- **fix:** Picking several labels X with what it does (Act on 2), Game night only offers another roll when one could find a game, the Search keyboard has no keys twice, game facts have room between them, and no screen draws a character the font lacks.

## v0.11.1 (2026-10-16)

Safer disc compression (multi-disc playlists, LibCrypt .sbi files, exFAT drives), update checks that retry, and systems called by their names

### Fixes
- **fix:** Compressing games already here is safer with disc games: two different games in one folder are no longer joined into one playlist, a conversion that fails on a later disc removes the discs it had already made (so EmulationStation doesn't list the game twice), and a multi-disc game lying straight in the system folder gets its playlist under its full name.
- **fix:** A PS1 game with a LibCrypt .sbi file keeps working after CHD compression: the .sbi moves beside the CHD.
- **fix:** Compression that waits for space no longer keeps the box busy retrying; it starts again once space is freed.
- **fix:** Compress games already here works on a box whose userdata drive is exFAT or FAT32, and one game whose cue sheet can't be read is listed with the reason instead of emptying the list.
- **fix:** A compressed disc game's art and info go to EmulationStation once, not once for every track file, and Fill in game info's first big run is quicker on SD cards.
- **fix:** A daily update check that couldn't reach GitHub tries again within the hour instead of the next day, and Updates says GitHub can't be reached after 6 seconds, not 20.
- **fix:** Compress, Space, Free up space and Games from RomM call each system by its name (PlayStation 2, not ps2); the breadcrumb at the top no longer runs into the download count, and Space no longer draws over its bars.
- **fix:** Game night's Y is called Roll again everywhere, and Leftovers shows its buttons in its panel like the other lists.

## v0.11.0 (2026-10-14)

Settings in sections, and a Metadata section: EmulationStation gets RomM's screenshots, logos, videos and more

### Features
- **feat:** Settings opens on a list of its sections, each showing its current state; A opens one and L/R jump between them.
- **feat:** A new Metadata section in Settings picks what EmulationStation gets for a game: its screenshot or box art as the image, videos up to a size or none, extra art, region, language and series, and manuals; Fill in game info is one press away.
- **feat:** Downloads and Fill in game info give EmulationStation everything RomM has stored for a game: its screenshot as the image, the box art, logo, title screen, 3D box, box back, fan art, cartridge and video, and its region, language and series. Games downloaded with an earlier version get their screenshot with Fill in game info; a picture of your own is never replaced.

### Fixes
- **fix:** Fill in game info no longer lists games it has just filled in, also when run again before leaving RomM-o-Cera.
- **fix:** A game deleted and downloaded again gets its box art and the rest of its media again.
- **fix:** A disc game converted to CHD keeps its media, also when EmulationStation hadn't saved it yet, and its alternative box art follows it.

### Documentation
- **docs:** The README asks you to use RomM-o-Cera with care, and says it provides no games, BIOS, firmware or keys: you bring your own.

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

