# On this box

**On this box** (home menu) opens a menu of six sections. The line under each
one shows its state right now, so you can see how the box is doing without
opening anything. It's only coloured when something needs a look. The panel
on the right previews the selected section; press A to open it.

## Games from RomM

Everything RomM-o-Cera downloaded, biggest first. Delete a game from here, or
any game marked ✓ in a list (including ones you copied over before). Saves and
states are always kept.

## Space

The whole drive as one bar: games from RomM-o-Cera, games added another way,
media (covers, videos, manuals), and Batocera itself with your saves. The
empty part is free space; its striped end is the **Keep free on disk**
reserve (Settings) that downloads never use, so you can see how much is left
for downloads.

- **Free up space**: every game EmulationStation lists, including ones copied
  over another way, least played and biggest first. Mark several, or let it
  pick enough to free 10–100 GB, and delete them in one go. Saves and states
  are kept.
- **Clean up leftovers**: partial files and empty folders that interrupted
  downloads or conversions left behind. Partial downloads still queued are
  kept so they can resume.
- **By system**: where the space went in each system, in the same colours as
  the bar. Pick a system to free up space in just that one.

## BIOS files

Also in Settings. Every system with games on the box: what's complete, what's
missing that your RomM has (one press to fetch, or all at once — matched and
verified by checksum), what's missing everywhere, and what was fetched when.

## Check games

**Quick check** looks for missing tracks, damaged zips, disc images cut short
(an interrupted copy: shorter than the disc's own filesystem says) and files
whose size doesn't match RomM. **Full check** also checksums every file and
verifies CHDs — slow, hours for a big library.

The last check's results stay here until you check again. A game whose file
has changed since (replaced or deleted) drops off the list.

Damaged games can be downloaded again, one at a time or all at once; a fresh
copy replaces a cut-short CHD. A damaged game RomM names differently is found
by its RetroAchievements hash, else by the matching title, disc and region;
saves move to the new name and playlists follow.

Red findings are certain (a part the game needs to boot is missing); amber
ones are worth a look (some complete discs list dummy files past their end;
a trimmed image is smaller than RomM's copy).

## Conversions

Also in Downloads (L2/R2). The conversion running now with time left, the
queue, and recent results; retry a failed one. See
[Disc compression](disc-compression.md).

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
