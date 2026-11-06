# On this box

**On this box** (home menu) opens with one line about the whole box ("1 game
needs you", or "Everything here looks fine") over the drive's space bar, then
its seven sections, always in the same order. Each section shows how it is
right now: a short value on the right, and a line under its name that is only
coloured, with a mark at its edge, when something needs a look (red: a damaged
game; amber or violet: something to fetch or fix; blue: compressing now). The
panel on the right previews the selected section; press A to open it.

## Games from RomM

Everything RomM-o-Cera downloaded, biggest first (**Y** sorts by name or
newest first instead), with the cover and details from RomM beside the list,
as in a game list. **A** opens the same menu as a game on the box in a game list:
**Play now** ([Browsing and downloading](browsing-and-downloading.md#play-now)), **Delete**, **Download again** and **More like this** (while RomM's record
can't be fetched, only Delete). To delete several at once, pick them (X ›
Pick several, see [Controls](controls.md#picking-several)). Any game it
downloaded can also be deleted from its game list. A game put on the box
another way (copied over by hand, say) is marked ✓ and plays too, and so is
one the app downloaded whose file something else replaced, cut short or
rewrote since (its size and time changed). Its menu offers **Delete from this box** ("Not downloaded by
RomM-o-Cera, or changed since: frees 1.2 GB (3 files)") and **Download
again** ("Replaces these files with RomM's copy"), and nothing happens until
you confirm. The confirm says the game wasn't
downloaded by RomM-o-Cera and lists the exact files that go or get replaced:
the game file and the tracks its `.cue`/`.gdi` names; in a plain folder named
like the game, the files named like the folder and their tracks; a playlist
(`.m3u`) of yours alone, never the discs it lists; a folder game
(`Game.ps3`) whole. Never anything else in or beside the folder (notes, saves,
states, patches, cheats, other games), and never a file one of the app's own
downloads holds. Saves and states are always kept, and so are art, videos and
manuals (`images/`, `videos/`, `manuals/`): deleting a game never deletes them,
whoever put them there, and the app never writes RomM's art over a file that's
already there. A game folder that also holds your own files (notes, a
manual, pictures, saves, patches, cheats, anything you added) keeps them, and
the folder stays: only the files the download wrote go. A playlist (`.m3u`) of
your own that lists the game is never deleted: the confirm names it, and ES goes
on listing it with nothing to start.

## Space

The whole drive as one bar: games from RomM-o-Cera, games added another way,
media (covers, videos, manuals), and Batocera itself with your saves. The
empty part is free space; its striped end is the **Keep free on disk**
reserve (Settings) that downloads never use, so you can see how much is left
for downloads.

- **Free up space**: every game that EmulationStation
  lists, the app's and yours put here another way, least played first (**Y** sorts by size or name
  instead). **A** picks games, or the X Actions menu picks the least played, biggest
  ones until 10, 20, 50 or 100 GB would be freed; check the ticks, then X
  deletes them in one go, after asking: for a game the app downloaded, only the
  files its download wrote; for one put here another way, the same files
  Delete would list for it (a playlist alone, never the discs it lists), and
  the confirm names them. Saves and states are kept.
- **Clean up leftovers**: partial files and empty folders (empty, or holding
  only files the system makes by itself, like `Thumbs.db` or `.DS_Store`; a
  folder with a manual, cover, save or any other file of yours is never one) that interrupted
  downloads or conversions left behind (including a half-made `.ps3` game
  folder, or a game left part-unpacked when the app closed or the power
  went: **Unfinished unpack**; a partial download only when it's one of the app's own downloads, and
  any partial file only once it's been unchanged for an hour), and **copies of games already here
  converted**, whatever put them here: an archive, image
  or loose tracks next to the same game's `.chd`, `.rvz`, `.iso.squashfs`,
  `.zcci`, `.zar`, `.wux`, `.wua` or `.ps3` folder (say `Game.7z` beside `Game.rvz`). Each
  copy names the converted game it duplicates. A copy is only offered when
  the converted game is proven whole and the same game: a `.chd` must be a
  standalone CHD v5 whose header, hunk map and metadata all fit in the file,
  whose tracks (or DVD image) hold the same number of bytes as the copy's
  (its `.cue`/`.gdi` tracks, loose tracks, `.iso`, the ISO a `.cso` decodes
  to, or the tracks in a `.zip`; within a sector per track), and that passes
  `chdman verify`. An `.rvz`, `.zcci`, `.wux` or `.iso.squashfs` must pass
  Check games' quick check and hold an image the size of the copy's (a plain
  `.iso`/`.gcm`/`.wud`/cartridge, or the one in a `.zip`), so a `.7z` or `.rar`
  beside one isn't offered. A `.zar` or `.wua` must pass its footer check, a
  `.ps3` folder what RPCS3 boots it by. A file an `.m3u` playlist or another
  `.cue`/`.gdi`/`.ccd` still points at is never offered (point the playlist at
  the converted files first), nor a `.sbi` or `.sub`, nor a `.zip` holding
  anything besides the disc (a memory card, a walkthrough). Never while a conversion runs, never for a
  file a queued conversion will use, and not on arcade systems (where
  `game.zip` and `game.chd` belong together). Partial downloads still queued
  are kept so they can resume. **A** deletes one, **Y** all of them, and the
  X Actions menu the ones you pick; each asks first.
- **By system**: where the space went in each system, in the same colours as
  the bar. Pick a system to free up space in just that one.

## Compress games already here

Disc games already on the box that could still be compressed: ones copied
over by hand, or downloaded while a format was off. The scan goes through
every system and lists each one with its game count, size and the format it
would become (CHD, RVZ, squashfs, .zcci, .zar, .wux, .wua, a `.ps3` game folder),
biggest first.

- **A** on a system lists its games: **A** compresses one; the **X** menu
  compresses all of them, or the ones you pick.
- **X** on a system compresses the whole system. You're asked first: a big
  system can take hours.

Games a converter applies to but can't convert come last, as **can't convert**
rows per system: an encrypted 3DS dump, an encrypted PS3 disc without its key
(or with **Decrypt PS3 discs** off), a damaged or LZ4 `.cso`, a file named like the new
one already here ("a file named X.chd is already here (another copy of it, or
another disc or game)": keep the one that plays and delete the other, or rename
one if they're different discs or games), a game still being written (try again in a minute),
another game here that would also become the same file, two of its discs that
would become the same file, a missing track. **A** lists them
with the reason for each. They're left as they are and never queued; fix or
replace the file and it shows up with the others.

It all runs in the background like any other conversion (see
[Conversions](#conversions)), pauses while you play, and each original is
deleted only after its copy checks out. CHD works even with **Compress disc
games to CHD** off (asking for it here is the point); the other formats show
dimmed with "needs a setting" until their switch in Settings → Disc
compression is on. Xbox images that aren't bootable yet are the exception:
with **Make Xbox images bootable** on and Xbox to squashfs off, they're
listed as "to bootable .iso" and only made bootable. Games already queued
don't show up again. Over SSH,
`rb_chd_plan.py` does the same
([Disc compression](disc-compression.md#converting-a-library-you-already-have)).

## BIOS files

Also in Settings. Every system with games on the box: what's complete, what's
missing that your RomM has, what's missing everywhere, and what was fetched
when. **A** gets the missing files of one system from RomM, **X** all of
them (after asking). Each file is matched and checked against Batocera's
checksum; a wrong file already there is kept beside it as `.bak-romm`.
A platform's game list offers the same for its system (X › Get BIOS).

## Check games

The last check's findings, damaged ones first, beside a panel that says what's
wrong with the focused game and what downloading it again does. **Y** runs a
quick check; **X** Actions has **Quick check**, **Full check** and
**Download all damaged again**, which includes games RomM-o-Cera didn't
download and says how many. Under the findings,
[Games ES can't see](#games-es-cant-see) opens with A. Before the first check,
the list starts with **Check now**. B stops a check that's running; when it
finishes, Check games shows what it found. If the drive stops answering or is
swapped mid-check, the check stops and saves nothing. **Download again** on a
finding of a game RomM-o-Cera downloaded replaces the file it shows, even when
RomM's copy comes under another name or format (a zipped cue/bin for a
damaged `.chd`); its row says **downloaded** once RomM's copy is in, and the
next check looks at it. A finding in a copy put here another way can be
downloaded again too: the confirm says it wasn't downloaded by RomM-o-Cera and
lists the files RomM's copy replaces.

**Quick check** looks for missing tracks, damaged zips, disc images cut short
(an interrupted copy: shorter than the disc's own filesystem says) and files
whose size doesn't match RomM. **Full check** also checksums every file and
verifies CHDs — slow, hours for a big library.

Games converted on the box are checked against RomM's copy of what they were
made from:

| Format | Quick check | Full check also |
|---|---|---|
| CHD | sectors against RomM's image | `chdman verify` |
| RVZ | its headers, cut short, disc size against RomM's image | `dolphin-tool` reads the whole disc; its CRC32/SHA-1 must be RomM's |
| `.iso.squashfs` | its superblock, cut short, the image inside and its size (an Xbox game partition cut here counts as right) | the image read out whole; uncut, its CRC32 must be RomM's |
| `.zcci` | its header, cut short, size against RomM's original | every block decoded; CRC32 against RomM's |
| `.zar` | its footer, cut short | its whole SHA-256 |
| `.wux` | its header and sector index, cut short, its disc's size against RomM's `.wud` | expanded whole by wuatool; CRC32 and SHA-1 against RomM's `.wud` |
| `.wua` | its footer, cut short | its whole SHA-256 |
| `.ps3` folder | `EBOOT.BIN`, `PARAM.SFO` (with the game's ID), `PS3_DISC.SFB` | every file read to its end |

A check that needs a tool the box doesn't have (`chdman`; `dolphin-tool`,
which turning on GameCube and Wii to RVZ installs; `unsquashfs` 4.5 or later)
counts the game as not verified, and the screen names the tool, rather than
flagging it. A `.ps3` folder is matched with RomM's disc image (`Game.zip`
or `Game.iso` for `Game.ps3`).

If RomM can't be reached, or a list can't be read, the screen says so in red
instead of showing an empty, all-clear list. Go back and open it again to retry.

Games that weren't fully verified (say `chdman verify` didn't finish) or
aren't in RomM under the same file name are listed under **Not fully checked**;
its panel names the games that aren't in RomM. Even for those, a quick check
reads every CHD's own header: a file that only has a `.chd` name, or a CHD cut
short (its index past the end of the file), is **Damaged**.
A file the box can't read shows as **Can't be read** and is left out of
**Download all damaged again**. A quick check keeps the last full check's
findings for files that haven't changed.

The last check's results stay here until you check again. A game whose file
has changed since (replaced or deleted) drops off the list.

Damaged games can be downloaded again, one at a time (**A**) or all at once
(X › **Download all damaged again**); a fresh copy replaces a cut-short CHD. To download several findings
again, amber ones included, pick them (X › Pick several) and choose
**Download … again** from the X Actions menu. A damaged game RomM names differently is found
by its RetroAchievements hash, else by the matching title, disc and region;
saves move to the new name and playlists follow. A game downloaded again
replaces the copy the app put on the box (say the CHD it compressed the game
to), not just the file named in the finding. Its row says **queued** while the
download waits or runs; if the download fails, the row says **failed** and the
panel says why, and it can be downloaded again.

A folder with no game file in it (only a manual, notes or pictures) isn't a
game: Check games doesn't flag it and the app doesn't count it as on the box.
That's the folder compressing keeps for your own files when it puts the game's
CHD beside it ([Disc compression](disc-compression.md)). Downloading the game
again unpacks into that folder beside your files, and never writes over one of
them: if the download has a file of the same name, it stops and says so. A
folder of that name with no game file named like it in it (another game, a
cheat, a patch, a save, a translation or another release) is never taken for
this game: it isn't counted as on the box, checked or deleted as it. A download
of the game unpacks beside what's in it, never over any of it, and compresses
only the files it brought.

Red findings are certain (a part the game needs to boot is missing); amber
ones are worth a look (some complete discs list dummy files past their end;
a trimmed image is smaller than RomM's copy).

### Games ES can't see

The row under the findings. EmulationStation only lists files whose extension the system
takes (its list in `es_systems.cfg`: PS2 and PSP take no `.zip`, N64 takes no
`.rvz`…) and silently skips the rest. This looks through every system folder
— no RomM needed, nothing is checksummed — and lists, by system:

- **Archives the system doesn't take** (a `.zip`/`.7z`/`.rar` in `ps2/`, say).
  **Unpack** shows what's inside, then unpacks it exactly as after a download:
  same safety checks (no paths or links out of the folder, never into a game
  that's already there under that name, nor beside a file of the same name ES
  lists, like a `.chd`: it says to delete the archive if that's the same game,
  or rename one of them if not), converted to CHD/RVZ/… if that's on,
  and the archive is deleted once it's out.
- **Games in the wrong system folder** (a GameCube image in `n64/`): a loose
  file whose extension one to three other systems take. **Move to …** offers
  each of them (a plain GameCube/Wii `.iso` is told apart by its disc header). The game's
  tracks or playlist discs move with it; nothing is ever moved over a file
  that's already there.
- **Files ES can't list here**: no system takes the extension, only launchers
  like Ports do, so many do that it says nothing (`.bin`, `.iso`), or the file
  sits inside a folder (a BIOS chip set, say). Listed so you know.

Every file also has **Delete**, for a leftover you don't want (an archive of a
game that's already here, say). It asks first, naming the size and any tracks
a `.cue`/`.gdi` (or discs an `.m3u`) names, which go with it; saves and states
stay. If RomM-o-Cera installed the file, it forgets it.

To do several at once, pick them as in any list (X › **Pick several files**
or **Pick all**, then **A** picks; see
[Controls](controls.md#picking-several)). The X Actions menu then offers **Unpack**
for every picked archive or **Delete** for all of them, each after one
confirmation. They're done one after another; one that can't be (a damaged
archive, say) is left as it is and named at the end, and the rest go on.

Skipped: media folders, `gamelist.xml`, hidden and unfinished files, notes,
pictures, saves and memory cards, patches and cheats (`.ips`, `.bps`, `.ppf`,
`.cht`…), backups and parked files (`.bak…`, `.disabled`,
`.torrent`, numbered copies), the tracks a `.cue`/`.gdi` (or the discs an `.m3u`)
names, and the other files of a game folder ES lists a game in. Nothing is
deleted unless you choose Delete and confirm. ES shows unpacked or moved games after its next reload (when
you leave RomM-o-Cera); game info RomM-o-Cera added follows a moved game.

## Conversions

Also in Downloads (L2/R2). The conversion running now, with its progress and
time left, then the queue and recent results. **A** on a queued one offers
**Convert next** or **Remove from the queue** (the original stays as it is);
on a failed one, **Try again**. **X** starts the queue now (when nothing is
playing and there's room), and **Y** clears the finished results. One that
waits for room says *Waiting for space*, with what it needs; it starts by
itself once there's room.
Conversions run only while no game is playing, and pause while
EmulationStation is busy. See [Disc compression](disc-compression.md).

## Fill in game info

Game info and media from RomM for games already on the box, matched by file
name: descriptions and details, region, language, screenshot, box art, logo,
title screen, video, manual… ([what goes where](browsing-and-downloading.md#game-info-in-emulationstation)).
Only what's empty is filled — your own scrapes and edits are never replaced,
and art is added only where there's no file yet under the name EmulationStation
gives it (`images/<game>-image.png`…): a file, a link or a folder already there,
whoever put it there, is never written over, so an image that is only the box
art again (what v0.10 and earlier left) stays. Art of yours under those names
that no gamelist entry links isn't linked for you either: link it in
EmulationStation's metadata editor. What the game has is read from EmulationStation
itself as well as `gamelist.xml`, which it writes only now and then, so a game
just filled isn't listed again; a media path to a file that is gone (a game
deleted and downloaded again gets its old entry back) counts as missing. A game counts as complete when it has
everything RomM has for it that [Settings → Metadata](settings.md) asks for:
one RomM has no video or logo for isn't listed for lacking them, nor is one
whose video is over the size limit or whose file RomM couldn't serve (noted
in `media-skipped.json` the first time; a new file at RomM, or a limit it
fits under, is tried again). This does every system; to do just one, use
that platform's list menu, or Settings → Metadata → Fill in game info.

## Game info lost after CHD conversion (v0.6.3 and earlier)

Up to v0.6.3, a game converted to CHD could lose its EmulationStation info
(description, cover, play count) because EmulationStation saved its own lists
over ours. Since v0.6.4 the app hands all game info to EmulationStation
through its API instead, and ships a one-time repair that finds the lost info
in the gamelist backups and gives it back (only empty fields are filled; play
history is merged; your gamelists are backed up first):

```sh
ssh root@<box>
cd /userdata/roms/pygame/rommbrowser
python3 rb_repair.py --dry-run     # what would be restored
python3 rb_repair.py
```
