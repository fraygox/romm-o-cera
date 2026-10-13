# Browsing and downloading

## Finding games

- **Platforms**: only those with a Batocera system. Each card shows how many
  games RomM has and how many of that system's games are already on the box
  (green). **Y** searches the platform under the cursor, **X** picks a random
  game from it.
- **Collections**: your own, smart collections, and RomM's virtual ones
  (genre, franchise, series, company, game mode). Like the platform cards,
  each shows how many of its games are already on the box (a green ✓ and a
  count, next to RomM's total), including games you copied over yourself.
- **Recently added**, on the home screen and as a full list.
- **Recommended for you** (RomM 5.4 and newer), on the home screen: games
  RomM picks from what you play, ranked from its similarity graph and your
  play history. Focus it and the home screen's shelf
  shows them; open it for the list, where each game's detail panel says why
  ("Because you played …", a series, genre or decade, "Highly rated"). Until
  you've played a few, RomM suggests the library's best-rated games. When
  RomM has nothing to suggest yet (or is older than 5.4), the entry doesn't
  show.

  ![Recommended for you on the home screen](../screenshots/recommended.png)

- **Search** (on the home screen) looks through every platform, with an
  on-screen keyboard and the top matches shown as you type (or just type on
  a keyboard). Move right past the keyboard to open a match straight away
  with the pad or arrow keys, or press Start for the full list of results
  ([Controls](controls.md#on-each-screen)). **Y** in a platform's game list
  searches just that platform.
- In any list, **L2/R2** jump letter by letter and **L/R** or left/right
  page ([Controls](controls.md#lists)).

### Game night

Can't decide? Game night rolls a random game and shows it big: cover, name,
platform, year, genre, how many can play and its description. **A** plays it
when it's on the box or downloads it when it isn't, **Y** rolls another, **B**
goes back. The rolls you pass on stay down the left (the last four, until you
leave): **up/down** brings one back.

Across the top: who's playing tonight (**Anyone, 2, 3 or 4 players**, changed
with **left/right** or a tap) and **Only what's on this box**, switched in
**X Actions**. Both roll again when they change and stay set until you exit.
The line at the bottom left says what it's rolling from ("Rolling from 1,234
games in PlayStation, for 2+ players").

Where to find it:

- **Home: Y.** From your whole library.
- **Platforms: X** on a platform: from that platform.
- **Collections: X › Game night** on a collection.
- **A game list: X › Game night.** From the list as it's filtered right now
  (region, hidden prototypes...); Game night's own players and on-the-box
  choices replace the list's.

Only games with a Batocera system come up. RomM 5.4 picks the game itself;
with an older RomM the app picks from the list's pages.

### Backlog

**Collections › Your RomM lists › Backlog** lists the games you marked
Backlog in RomM, in RomM's order: a cover, on-the-box tick or size, and
**Waits for space** in amber for those that won't fit yet. The panel sums up
**Download my backlog** (**Y**): the total, how many aren't here yet, what
fits now and what waits (against the space left once the queue has landed,
keeping Settings' keep-free space), and a line per platform. Everything is
queued in that order (up to 1,000); the ones that don't fit wait for room.
**X Actions** has Free up space. **A** plays the selected game or downloads it.

## The detail panel

Beside every list: cover, description, players, release date and genre, how
many RetroAchievements the game has, whether its manual comes along, and for
disc games the format and what it becomes on the box, for example
"2 discs, CUE/BIN (becomes CHD)", with the size once unpacked (exact when RomM
lists the archive's contents, "~" when estimated). It also tells you when
another region or revision of the game is already on the box.

## The game page

**A** on a game in a list (or in Games from RomM) opens its page: the cover
over a soft backdrop, its facts and description, and your side of RomM in
one strip: your status (Playing, On your backlog, Finished…), your rating,
your newest note, and your RetroAchievements progress. Up to three
screenshots sit under it, and a QR code opens the game's page in RomM on
your phone. Anything RomM doesn't have is left out.

- **A** is the game's one action: **Download** (A again in the prompt
  downloads it), or **Play now** when it's on the box. Then the facts set the
  box's copy beside RomM's ("On this box: CHD · 257 MB · roms/psx", "In RomM:
  zip · 515 MB") and the strip says when you last played it here.
- **X** opens the game's Actions: More like this, Surprise me (from the list
  it came from), and for a game on the box Delete and Download again.
- **Left/right** go to the previous or next game of the list; **B** goes back.

While picking several games, A still picks.

## The Actions menu (X)

Press **X** in a game list for its Actions menu. Filters change the list in place
and the menu stays open; the actions close it.

- **More like this**: games similar to the selected one.
- **Game night**: roll a game from the list (see [Game night](#game-night)).
- **Filters**: hide games already on the box, pick a region (USA + World,
  Europe + World, Japan), hide prototypes/demos/hacks, show only
  checksum-verified dumps; in a platform's list and Recently added, sort by
  name or newest first.
- **Players** (the party filter): only games for 2+, 3+ or 4+ players, going
  by the player counts in RomM's game info (games without one are left out).
  The list's header shows it while it's on ("2+ players, 312 games"), next
  to the region. Like the other filters it applies to that list until you
  leave it.
- **Pick several games**: **A** then picks games, and **Download … picked**
  in this menu downloads them together (see
  [Controls](controls.md#picking-several)).
- **Download every game in this list** (up to 1,000).
- **Fill in game info** for this system's games already on the box (see
  [On this box](on-this-box.md#fill-in-game-info)).
- **Get BIOS for** the platform, when the box is missing BIOS files that
  your RomM has (checksum-verified; all systems: [On this box](on-this-box.md#bios-files)).

**Download a whole collection** straight from Collections: X on a collection,
then Download every game (up to 1,000 games; a bigger one: open it and
filter first).

Bulk downloads estimate the space they need once extracted and offer to
download only what fits.

## Downloading

- The download prompt says what you're getting: the download size, the size
  once unpacked ("about" when it's an estimate, "up to" for a Wii U zip whose
  contents aren't known yet), whether it'll be compressed in the background, and the free
  space left afterwards.
- **Multi-disc games** download as a set: one entry in EmulationStation (an
  `.m3u` playlist, the single discs hidden), and discs you already have are
  reused. "Only this disc" is there too.
- Downloads are **resumable**, **checked against RomM's checksums** (zips
  member by member) and retried on network errors. When RomM (or the proxy
  in front of it) says it's busy, the app sends it fewer requests at once
  and tries again after the wait it asks for, for covers, lists and
  downloads alike. A cover that didn't come is asked for again a little
  later, so a hiccup doesn't leave "no cover" for the rest of the session. One that grows past the
  file's size RomM gave stops at once ("The server sent more than the file's
  size"), so a broken server can't fill the drive.
- They **keep going after you exit** and while you play (optionally
  speed-capped while a game runs), with a popup in EmulationStation when
  they're done. If they're stuck on something only you can fix (the drive
  is full, or RomM no longer accepts the box's token), EmulationStation
  shows a message box once you're back in its menus.
- **Download while extracting** (Settings, on by default): the next game
  downloads while the last one unpacks. Turn it off for slow storage (SD
  cards, USB sticks).
- Cartridge zips stay zipped when the system reads them; disc archives are
  extracted, and disc games can be compressed in the background (see
  [Disc compression](disc-compression.md)).
- An archive never unpacks into another game's folder. If its folder name is
  already taken by a different game, the download stops with "… is already on
  the box under that name" instead of mixing the two. Archives that would
  unpack over EmulationStation's files, and 7z/rar archives that contain
  links, are refused.
- Manuals (PDF) come along from RomM and open from the game's menu in
  EmulationStation.

### Play now

A game you've just downloaded can start straight away: **Play now** is in
the game's page (A on a game on the box), in its row's menu on the Downloads
screen, and the "Ready" toast says so (tapping the toast plays it).
RomM-o-Cera closes and EmulationStation starts the game a few seconds later.
If other downloads are still running it asks first: they keep going in the
background while you play, or, with **Keep downloading after exit** off,
pause and resume next time you open RomM-o-Cera.

It starts what EmulationStation lists for the game: the `.m3u` playlist of a
multi-disc game (once every disc is here), a PS3 `.ps3` folder, or the
converted file (CHD, RVZ, .zar, .wua…). A game that's waiting to be
compressed, or being compressed, can't start until that's done, and the menu
says so. A new download isn't in EmulationStation's lists until RomM-o-Cera
hands it over after closing, so the game starts once EmulationStation lists
it and its game info and images are written (up to 10 minutes on Batocera
43.1, which reloads every list for it); starting another game meanwhile
cancels it, and a popup says where to find the game if it isn't ready by
then. Where EmulationStation asks
before starting a game (a system with missing BIOS files), it asks then too.

### The Downloads screen

**Select** opens it from anywhere. The queue comes first, in the order it
runs, then finished downloads, newest first. **A** on a download gives its
options: move it to the top, up or down, Download now (outside the download
hours), pause, resume, cancel, retry, or remove it from the list. The **X**
menu pauses or resumes everything and clears finished ones (so does **Y**).
Pick several (X › Pick several) to pause, resume, cancel, retry or remove
them at once; cancelling (one or several, active or paused) asks first, since it
deletes the partial files. Each game shows what it's being compressed to (CHD,
RVZ, squashfs, .zcci, .zar, .wux, .wua, a `.ps3` folder) and when that's done. A
finished game's menu has [Play now](#play-now). L2/R2 opens
**Conversions** (background compression,
[On this box](on-this-box.md#conversions)).

### Space

**Keep free on disk** (Settings, default 20 GB): downloads and conversions
never leave less than this free. A download that doesn't fit waits ("Waiting
for space") and starts once there's room; smaller ones behind it keep going.
Downloads shows what the whole queue still needs against what's free, and
adding a game the queue can't fit warns you. An archive counts what it
really unpacks to: RomM's list of its contents, or, when RomM has none (Wii U,
for one), the zip's own list, read from RomM in the background before the
download starts (a few small requests, not the download). Until it's read, or
when it can't be (a 7z, or a server that won't send part of a file), a zipped
Wii U game counts as a whole disc image ("up to 23.3 GB", the size of every
`.wud`) and anything else about 2.2 times the download. A zip is deleted
once it's unpacked, so the queue counts only the biggest zip beside its
contents, plus what every game unpacks to. Conversions waiting for room
start as soon as you free some.

### Download hours

Settings, off by default: only download in a window, e.g. 1–7 am. A transfer
running when the window closes pauses and picks up the next night.

## Game info in EmulationStation

Name, description, date, genre, developer, players, rating and cover are
handed to EmulationStation through its own API once it's idle — never by
editing `gamelist.xml` behind its back, which EmulationStation could overwrite.
New downloads are added to EmulationStation's lists on their own, so it
doesn't reload every system for them. Batocera 43.1's EmulationStation can't
add games this way (a bug fixed after it), so there it reloads its lists as
before, as it does everywhere after games are compressed or deleted.

RomM-o-Cera stays out of EmulationStation's way: game info waits until ES is
idle, and background compression pauses while a game runs and while ES
scrapes, installs, computes RetroAchievements hashes or updates Batocera
([Disc compression](disc-compression.md)).

## Updates

**Settings → Updates** checks GitHub, then downloads, verifies and installs a
new release in place, keeping the previous version for one-step rollback.
The app closes and opens again by itself on the new version; downloads
carry on where they were.
The swap is a single step where the drive allows it, so a power cut mid-update
never leaves the box without the app. **Update channel** picks what it
offers: *Stable* releases, or *Nightly* builds of the newest changes as they
land. A daily check flags a newer version on your channel (turn it off with
**Check for updates daily**; Updates still checks when you select it).
Details in the [main README](../README.md#updating).
