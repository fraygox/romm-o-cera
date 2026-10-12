# Browsing and downloading

## Finding games

- **Platforms**: only those with a Batocera system. Each card shows how many
  of that system's games are already on the box.
- **Collections**: your own, smart collections, and RomM's virtual ones
  (genre, franchise, series, company, game mode). Like the platform cards,
  each shows how many of its games are already on the box (a green ✓ and a
  count, next to RomM's total), including games you copied over yourself.
- **Recently added**, on the home screen and as a full list.
- **Search** with an on-screen keyboard and live top matches (or just type on
  a keyboard).
- **L2/R2** jump letter by letter; **L/R** page.

## The detail panel

Beside every list: cover, description, players, release date and genre, how
many RetroAchievements the game has, whether its manual comes along, and for
disc games the format and what it becomes on the box, for example
"2 discs, CUE/BIN (becomes CHD)", with the size once unpacked (exact when RomM
lists the archive's contents, "~" when estimated). It also tells you when
another region or revision of the game is already on the box.

## The list menu (X)

- **More like this**: games similar to the selected one.
- **Filters**: hide games already on the box, pick a region, hide
  prototypes/demos/hacks, show only checksum-verified dumps; sort.
- **Pick games to download**: mark several, then download them together.
- **Download every game in this list** (up to 1,000).
- **Fill in game info** for this system's games already on the box (see
  [On this box](on-this-box.md#fill-in-game-info)).

**Download a whole collection** straight from Collections: X on a collection
(up to 1,000 games).

Bulk downloads estimate the space they need once extracted and offer to
download only what fits.

## Downloading

- The download prompt says what you're getting: the download size, the size
  once unpacked, whether it'll be compressed in the background, and the free
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
  they're done.
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

### The Downloads screen

Reorder, pause, cancel, pause/resume everything, clear finished. Each game
shows what it's being compressed to (CHD, RVZ, squashfs, .zcci, .zar) and
when that's done. L2/R2 opens **Conversions** (background compression). "Download now" starts one game
outside the download hours.

### Space

**Keep free on disk** (Settings, default 20 GB): downloads and conversions
never leave less than this free. A download that doesn't fit waits ("Waiting
for space") and starts once there's room; smaller ones behind it keep going.
Downloads shows what the whole queue still needs against what's free, and
adding a game the queue can't fit warns you. Conversions waiting for room
start as soon as you free some.

### Download hours

Settings, off by default: only download in a window, e.g. 1–7 am. A transfer
running when the window closes pauses and picks up the next night.

## Game info in EmulationStation

Name, description, date, genre, developer, players, rating and cover are
handed to EmulationStation through its own API once it's idle — never by
editing `gamelist.xml` behind its back, which EmulationStation could overwrite.

## Updates

**Settings → Updates** checks GitHub, then downloads, verifies and installs a
new release in place, keeping the previous version for one-step rollback.
The swap is a single step where the drive allows it, so a power cut mid-update
never leaves the box without the app. A
daily check flags new releases (turn it off with **Check for updates daily**;
Updates still checks when you select it).
Details in the [main README](../README.md#updating).
