# Disc compression

Disc games can be compressed in the background after they download. All of
it is lossless, runs only while no game is playing (a conversion pauses when
you start one, and also while EmulationStation scrapes, installs from the
content store, computes RetroAchievements hashes or updates Batocera), and checks the result against the original before the
original is deleted. Settings → **Disc compression**. Games already on the
box can be compressed too (see
[Converting a library you already have](#converting-a-library-you-already-have)).

Progress: **Activity** (Select: pause, resume or stop the running one), **On this box → Conversions**
(or L2/R2 in Activity), and
`/userdata/system/romm-browser/romm-browser.log` (older lines in
`romm-browser.log.1` … `.9`; a new file starts every 10 MB).

## CHD (CD systems, PS2, PSP) — on by default

**Compress disc games to CHD**: PlayStation, Saturn, Dreamcast, Mega CD, PC
Engine CD, Neo Geo CD, 3DO and the other CD systems, plus PS2 and PSP (PSP
with 2048-byte hunks, which PPSSPP reads fastest). PS2 and PSP games that
come as CD images (cue/bin) become CHD too, not only DVD `.iso` files. chdman
verifies each CHD before the original goes.

- A folder holding two releases of a game (say the original and a "Dual Shock
  Ver.") becomes one playlist per release.
- DVD images (PS2, PSP) skip chdman's FLAC codec, which leaks memory in some
  chdman builds. **Low-memory mode** (Auto: on under 4 GB RAM; or On, or Off)
  skips it for CDs too; their audio tracks come out a bit bigger.
- Any conversion stops if chdman takes more than half the RAM or free memory
  runs low; the originals are kept and it's tried again later.
- **Compression threads**: auto leaves some for the menus.
- A disc image that's cut short (an interrupted copy) is never converted —
  Check games can download it again. (Wii U's `.wux` is the exception: a
  `.wud` of the wrong size is compressed as it is, losslessly, with a note in
  the log; Check games compares the `.wux` with RomM's `.wud`, and a fresh
  download replaces it.)

### PSP `.cso` → CHD

PSP games in CSO ("CISO", compressed ISO) form become CHD too, under the same
switch. Batocera has no CSO tool, so RomM-o-Cera decodes the `.cso` itself to
a temporary ISO next to it (`Game.cso-iso.part`, which EmulationStation
doesn't list), and chdman packs that like a PSP `.iso` (2048-byte hunks, no
FLAC). The original goes only after `chdman verify` passes **and** the CHD's
data SHA-1 (`chdman info`, "Data SHA1") matches the SHA-1 of the decoded ISO.
The temporary ISO is deleted whatever happens; one left by a power cut shows
up in **Clean up leftovers**. While it works, it needs room for the decoded
ISO plus the CHD.

- Read: CSO version 1 (the common one, deflate), and version 2 files that
  use only deflate.
- Refused, the `.cso` stays as it is (PPSSPP plays it): CSO v2 files with LZ4
  blocks and ZSO files ("CSO v2/ZSO isn't supported (needs LZ4)" — Batocera's
  Python has no LZ4), anything damaged or cut short (a bad header or block
  index, a block that doesn't decode to exactly its size), and an image that
  isn't ISO9660 once decoded.

## GameCube and Wii to RVZ — off by default

Turning it on downloads Dolphin's `dolphin-tool` once (about 8 MB, checked by
SHA-256; x86_64 and 64-bit ARM such as the Pi 4/5), built from the same Dolphin
commit Batocera 43.1 uses. Each image is verified by dolphin-tool first
(anything that isn't a GameCube/Wii disc is left alone) and the RVZ must hash
to the original's CRC32 before the original is deleted. Usually much smaller.

Check games reads an RVZ's headers (cut short, or made from a different image
than RomM's); a full check has dolphin-tool read the whole disc and compares
its CRC32 and SHA-1 with RomM's image. When RomM keeps the game as `.wbfs`
or another compressed form, its checksums aren't the disc's: a full check
then only shows what dolphin-tool itself finds (data it can't read, Wii
blocks whose hashes are wrong).

## Make Xbox images bootable — on by default

xemu boots only an Xbox disc's game partition (an "XISO"). A full disc image,
the way Redump keeps them, shows "Please insert an Xbox disc". So Xbox
downloads keep just the game partition — in place where the drive allows
(instant, no extra space), otherwise through a copy. Files named `.xiso` are
renamed `.iso`, because EmulationStation's Xbox list doesn't include `.xiso`.
Images that are already bootable are left alone, so a RomM library of XISOs
costs nothing. Xbox games already on the box get the same treatment from
[Compress games already here](on-this-box.md#compress-games-already-here),
listed as "to bootable .iso" while Xbox to squashfs is off.

Check games knows a game partition cut here is exactly one partition smaller
than RomM's copy and doesn't flag it.

## Xbox to squashfs — off by default

Packs each Xbox game into `Game.iso.squashfs`: a compressed, read-only
filesystem holding the image (zstd, 1 MB blocks; usually 15–45% smaller).
Nothing is unpacked: when you start the game, Batocera mounts that one file
read-only, hands xemu the image inside, and unmounts it when you quit. The
image inside is mounted and checksummed against the original before the
original is deleted. Packing takes a few minutes of CPU per game.

Inside, next to the image, a `.ROM` link points at it. Batocera uses either
that link or the `Game.iso.squashfs` naming to find the image; without one
of them it would hand xemu the folder and the game wouldn't start.

Check games reads the squashfs header (cut short) and lists what's inside
with `unsquashfs`: the image must be there, the size of RomM's copy or of its
game partition. A full check reads the image out whole and, when it wasn't
cut, compares its CRC32 with RomM's. Without `unsquashfs` (4.5 or later for
a full check) the game counts as not verified.

**zstd, not xz**: xz packs about 5% smaller but reads about 20× slower (each
read decompresses a whole 1 MB block), which shows up as loading time.

Needs **Make Xbox images bootable** for full disc images (a packed image xemu
can't boot would be no use).

## 3DS to .zcci — on by default

Compresses 3DS games (`.3ds`, `.cci`; `.cxi` becomes `.zcxi`) into Azahar's own
compressed format, the one its "Compress ROM File" menu makes. Azahar opens
`.zcci` directly, and saves stay where they are (Azahar keeps them by the game's
title ID, not its file name). Typically 40–65% smaller, about a second per GB.

Nothing to download: it uses the zstd library Batocera already has. After
writing, every block is read back the way Azahar reads it and the result must
hash to the original's CRC32 before the original is deleted.

- Only decrypted dumps (the kind Azahar runs). An encrypted one is left as it is
  (Conversions shows it as skipped, and Compress games already here as can't
  convert, with the reason) and Check games flags it.
- `.cia` installers stay as they are for now.
- Check games compares a `.zcci` with RomM's original: a quick check catches a
  cut-short file or one packed from a different dump; a full check decodes the
  whole file and compares checksums.

## Xbox 360 to .zar — off by default

Turns each Xbox 360 disc image into `Game.zar`, a compressed archive that
xenia-canary (Batocera's default Xbox 360 emulator) opens directly. Often 40–60%
smaller: a 7.8 GB image became 3.2 GB in under a minute on a Ryzen 5600X.

A `.zar` holds the game's files, not the whole disc (the video partition and
padding are left out), so it can't be turned back into the original image and
won't match RomM's checksums. Nothing the game needs is lost.

Turning it on downloads two small tools once (under 1 MB, checked by SHA-256,
x86_64 only like Batocera's Xbox 360 emulators): **extract-xiso** and
**zarchive**, built from their published source; licenses and source are on
RomM-o-Cera's `xbox360-tools` release. This product includes software developed
by in <in@fishtank.com>. Before the image is deleted:

- the extracted files must match the disc's own file list (a cut-short image
  is refused, even though extract-xiso doesn't complain about it);
- the archive's own SHA-256 must check out;
- the archive is unpacked again and every file's CRC32 compared.

The old `xenia` emulator (not canary) can't open `.zar`. Check games checks a
`.zar`'s footer (quick) or its whole SHA-256 (full).

## Wii U to .wux — on by default

Compresses each Wii U disc image (`.wud`) into `Game.wux`, the lossless
container Cemu opens directly: a disc is mostly empty space, and a `.wux`
stores each distinct 32 KB sector once (a 25 GB `.wud` became a 1.5 GB `.wux`,
byte for byte the `.wux` Redump's set has for it). It needs no
keys, and the `.wux` expands back to the exact `.wud`, so Check games can still
compare it with RomM's `.wud` checksums.

Before the `.wud` is deleted, wuatool expands the `.wux` through Cemu's own
disc reader and compares every byte with the `.wud` (the log gets its CRC32 and
MD5). A `.wux` carries no checksum of its own: Check games checks its layout
(quick: header, sector index, every sector it points at present) or expands it
whole and compares it with RomM's `.wud` (full).

It needs **wuatool** (about 3 MB, checked by SHA-256, x86_64 and 64-bit ARM),
downloaded once. The app fetches it by itself, in the background, once there
are Wii U disc images on the box (or a Wii U game finishes downloading) and
whenever a newer version replaces the one installed (the old one is removed);
a box without Wii U games downloads nothing. Selecting the switch in Settings
fetches it too. A game that finishes downloading before the tool is here is
queued all the same and converted once the tool arrives (Conversions shows it
as "Waiting for wiiu-tools"; if the tool can't be fetched, Compress games
already here still lists it). Decrypt Wii U discs uses the same download. Any
converter tool whose switch is on is updated the same way. wuatool is built from
Cemu's own disc code and the ZArchive library Cemu reads `.wua` with, from
their published source; licenses and source are on RomM-o-Cera's `wiiu-tools`
release. Its `.wux` writer is RomM-o-Cera's own, written to the layout Cemu's
reader expects (Exzap's WudCompress, which introduced the format, carries no
license).

## Decrypt Wii U discs — off by default

Wii U discs are encrypted. With its **disc key**, for games you own, a disc
(`.wud` or `.wux`) becomes `Game.wua`, the compressed archive Cemu opens
directly, usually smaller still than a `.wux` (it keeps only what the game
uses: a 1.1 GB `.wux` became 127 MB in 6 seconds, checks included).

A `.wua` holds the disc's game partition, decrypted (the files Cemu plays, in
the layout Cemu's own "Convert to .wua" writes), not the whole disc, so it
can't be turned back into the original image and won't match RomM's checksums.
Some discs also carry the game's update and DLC as partitions of their own:
they go into the same `.wua`, each title in its own folder, as Cemu's "Convert
to .wua" packs a game with its update and DLC, and Cemu starts the game with
that update when you launch the `.wua`. (From a `.wud` or `.wux`, Cemu reads
only the disc's first game partition, so a disc's own update goes unused there.)
The console-firmware partition is left out.

Put the disc keys in Cemu's `keys.txt` (`/userdata/bios/cemu/keys.txt`, one
32-digit key per line, the file Batocera's Cemu reads) or in
`/userdata/bios/wiiukeys/` (`.key` files, key lists, or zips of them). Turning
the switch on copies the keys in your RomM's Wii U firmware (a `keys.txt`,
`.key` files, or a zip with "key" in its name) into `bios/wiiukeys/`.
RomM-o-Cera comes with no disc keys. A key is checked against the disc itself,
so a wrong one is never used.

How the two switches combine:

- a `.wud` whose key is here becomes a `.wua`;
- a `.wud` without its key (or with this switch off) becomes a `.wux`, when
  Wii U to .wux is on; Compress games already here lists it as can't decrypt,
  with the reason, and still offers it as `.wux`;
- a `.wux` becomes a `.wua` once this is on and its key is here (turning it on
  later brings the `.wux` files you have into Compress as `.wua`).

The choice is made again when a queued disc's turn comes, by the switches and
keys then. Before the image is deleted:

- every hashed block read from the disc must match its hash, and a cut-short
  image is refused (like CHD and `.zar`, damage in a part of the disc that
  carries no hash is packed as it is: Check games tells you whether the image
  matched RomM's before you compress it);
- the archive's own SHA-256 must check out;
- every file in the archive is compared with the disc again.

Check games checks a `.wua`'s footer (quick) or its whole SHA-256 (full).
Titles from the eShop (NUS format: `.app`/`.tmd` folders) aren't handled yet.

## Safety

- The new file is flushed to disk before any original is deleted, so a power
  cut can't leave you with neither.
- If a game's files change or disappear while it's being converted (a fresh
  download, a delete), the conversion is dropped and the files are left as
  they are.
- A conversion cut off by a shutdown just after its new file was written is
  finished on the next try, instead of leaving both copies listed.
- A `.cue` or `.gdi` naming a file outside the game's folder is refused.

## Converting a library you already have

**On this box → Compress games already here** lists, system by system, the
disc games on the box that could still be compressed. Compress one game or a
whole system (a whole system can take hours). CHD works even with its switch
off; RVZ, squashfs, .zcci, .zar, .wux, .wua and PS3 game folders need theirs on. Games it
can't convert (an encrypted 3DS dump, a PS3 or Wii U disc without its key, a
damaged `.cso`) are listed apart with the reason, never queued. Details in
[On this box](on-this-box.md#compress-games-already-here).

Over SSH, `rb_chd_plan.py` does the same:

```sh
ssh root@<box>
cd /userdata/roms/pygame/rommbrowser
python3 rb_chd_plan.py --dry-run                         # what would be done
python3 rb_chd_plan.py --systems psx --only "Some Game"  # try one first
python3 rb_chd_plan.py                                   # queue the rest
setsid nohup python3 rb_chd.py >/dev/null 2>&1 &
```

It queues CD/DVD games for CHD (even with CHD turned off), and GameCube/Wii
images for RVZ, 3DS games for `.zcci`, Xbox 360 images for `.zar`, Wii U
discs for `.wua` (or `.wux`, see above) and PS3 disc images for `.ps3` folders when those are on. What it can't convert
(encrypted 3DS dumps, PS3 or Wii U discs without a key, damaged `.cso` files) is
listed as skipped, with the reason. Games already queued are left out.
Xbox: with Make Xbox images bootable on, full disc images are cut to their
game partition and `.xiso` files renamed right away (even with Xbox to
squashfs off); with Xbox to squashfs on, the images are then queued for
packing.

## PS3 game folders

Not compression: PS3 disc images become `.ps3` game folders RPCS3 boots and
updates. Encrypted discs use disc keys for games you own. See
[PS3 games](ps3.md).

## Not offered (yet)

- **Wii U eShop titles** (NUS format, `.app`/`.tmd` folders): not converted yet.
- **PS3 squashfs**: measured about 2% smaller; Redump's PS3 images barely
  compress. PS3 discs become game folders instead ([PS3 games](ps3.md)).
