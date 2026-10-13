# On this box

**On this box** (home menu) opens a menu of seven sections. The line under each
one shows its state right now, so you can see how the box is doing without
opening anything. It's only coloured when something needs a look. The panel
on the right previews the selected section; press A to open it.

## Games from RomM

Everything RomM-o-Cera downloaded, biggest first (**Y** sorts by name or
newest first instead), with the cover and details from RomM beside the list,
as in a game list. **A** opens the same menu as a game on the box in a game list:
**Play now** ([Browsing and downloading](browsing-and-downloading.md#play-now)), **Delete**, **Download again** and **More like this** (while RomM's record
can't be fetched, only Delete). To delete several at once, pick them (X ›
Pick several, see [Controls](controls.md#picking-several)). Any game marked ✓
in a list (including ones you copied over before) can also be deleted from
its game list. Saves and states are always kept.

## Space

The whole drive as one bar: games from RomM-o-Cera, games added another way,
media (covers, videos, manuals), and Batocera itself with your saves. The
empty part is free space; its striped end is the **Keep free on disk**
reserve (Settings) that downloads never use, so you can see how much is left
for downloads.

- **Free up space**: every game EmulationStation lists, including ones copied
  over another way, least played first (**Y** sorts by size or name
  instead). **A** picks games, or the X Actions menu picks the least played, biggest
  ones until 10, 20, 50 or 100 GB would be freed; check the ticks, then X
  deletes them in one go, after asking. Saves and states are kept.
- **Clean up leftovers**: partial files and empty folders that interrupted
  downloads or conversions left behind (including a half-made `.ps3` game
  folder), and **copies of games already here converted**: an archive, image
  or loose tracks next to the same game's `.chd`, `.rvz`, `.iso.squashfs`,
  `.zcci`, `.zar`, `.wux`, `.wua` or `.ps3` folder (say `Game.7z` beside `Game.rvz`). Each
  copy names the converted game it duplicates. A copy is only offered when
  the converted game looks whole, never while a conversion runs, never for a
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
(or with **Decrypt PS3 discs** off), a damaged or LZ4 `.cso`. **A** lists them
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

Three tiles at the top: **Quick check**, **Full check** and
[Games ES can't see](#games-es-cant-see); A starts or opens one, and B stops
a check that's running. Below them are the last check's results.

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

The last check's results stay here until you check again. A game whose file
has changed since (replaced or deleted) drops off the list.

Damaged games can be downloaded again, one at a time (**A**) or all at once
(**Y**); a fresh copy replaces a cut-short CHD. To download several findings
again, amber ones included, pick them (X › Pick several) and choose
**Download … again** from the X Actions menu. A damaged game RomM names differently is found
by its RetroAchievements hash, else by the matching title, disc and region;
saves move to the new name and playlists follow.

Red findings are certain (a part the game needs to boot is missing); amber
ones are worth a look (some complete discs list dummy files past their end;
a trimmed image is smaller than RomM's copy).

### Games ES can't see

The third tile. EmulationStation only lists files whose extension the system
takes (its list in `es_systems.cfg`: PS2 and PSP take no `.zip`, N64 takes no
`.rvz`…) and silently skips the rest. This looks through every system folder
— no RomM needed, nothing is checksummed — and lists, by system:

- **Archives the system doesn't take** (a `.zip`/`.7z`/`.rar` in `ps2/`, say).
  **Unpack** shows what's inside, then unpacks it exactly as after a download:
  same safety checks (no paths or links out of the folder, never into a game
  that's already there under that name), converted to CHD/RVZ/… if that's on,
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
pictures and saves, backups and parked files (`.bak…`, `.disabled`,
`.torrent`, numbered copies), the tracks a `.cue`/`.gdi` (or the discs an `.m3u`)
names, and the other files of a game folder ES lists a game in. Nothing is
deleted unless you choose Delete and confirm. ES shows unpacked or moved games after its next reload (when
you leave RomM-o-Cera); game info RomM-o-Cera added follows a moved game.

## Conversions

Also in Downloads (L2/R2). The conversion running now, with its progress and
time left, then the queue and recent results. **A** on a queued one offers
**Convert next** or **Remove from the queue** (the original stays as it is);
on a failed one, **Try again**. **X** starts the queue now (when nothing is
playing and there's room), and **Y** clears the finished results.
Conversions run only while no game is playing, and pause while
EmulationStation is busy. See [Disc compression](disc-compression.md).

## Fill in game info

Names, descriptions, covers and manuals from RomM for games already on the
box, matched by file name, added only where missing — your own scrapes and
edits are never replaced. This does every system; to do just one, use that
platform's list menu.

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
