# Disc compression

Disc games can be compressed in the background after they download. All of
it is lossless, runs only while no game is playing (a conversion pauses when
you start one), and checks the result against the original before the
original is deleted. Settings → **Disc compression**.

Progress: **On this box → X → Conversions** (or L2/R2 in Downloads), and
`/userdata/system/romm-browser/romm-browser.log`.

## CHD (CD systems, PS2, PSP) — on by default

**Compress disc games to CHD**: PlayStation, Saturn, Dreamcast, Mega CD, PC
Engine CD, Neo Geo CD, 3DO and the other CD systems, plus PS2 and PSP (PSP
with 2048-byte hunks, which PPSSPP reads fastest). chdman verifies each CHD
before the original goes.

- A folder holding two releases of a game (say the original and a "Dual Shock
  Ver.") becomes one playlist per release.
- DVD images (PS2, PSP) skip chdman's FLAC codec, which leaks memory in some
  chdman builds. **Low-memory mode** (on automatically under 4 GB RAM) skips it
  for CDs too; their audio tracks come out a bit bigger.
- Any conversion stops if chdman takes more than half the RAM or free memory
  runs low; the originals are kept and it's tried again later.
- **Compression threads**: auto leaves some for the menus.
- A disc image that's cut short (an interrupted copy) is never converted —
  Check games can download it again.

## GameCube and Wii to RVZ — off by default

Turning it on downloads Dolphin's `dolphin-tool` once (about 8 MB, checked by
SHA-256; x86_64 and 64-bit ARM such as the Pi 4/5), built from the same Dolphin
commit Batocera 43.1 uses. Each image is verified by dolphin-tool first
(anything that isn't a GameCube/Wii disc is left alone) and the RVZ must hash
to the original's CRC32 before the original is deleted. Usually much smaller.

## Make Xbox images bootable — on by default

xemu boots only an Xbox disc's game partition (an "XISO"). A full disc image,
the way Redump keeps them, shows "Please insert an Xbox disc". So Xbox
downloads keep just the game partition — in place where the drive allows
(instant, no extra space), otherwise through a copy. Files named `.xiso` are
renamed `.iso`, because EmulationStation's Xbox list doesn't include `.xiso`.
Images that are already bootable are left alone, so a RomM library of XISOs
costs nothing.

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

**zstd, not xz**: xz packs about 5% smaller but reads about 20× slower (each
read decompresses a whole 1 MB block), which shows up as loading time.

Needs **Make Xbox images bootable** for full disc images (a packed image xemu
can't boot would be no use).

## Converting a library you already have

```sh
ssh root@<box>
cd /userdata/roms/pygame/rommbrowser
python3 rb_chd_plan.py --dry-run                         # what would be done
python3 rb_chd_plan.py --systems psx --only "Some Game"  # try one first
python3 rb_chd_plan.py                                   # queue the rest
setsid nohup python3 rb_chd.py >/dev/null 2>&1 &
```

It queues CD/DVD games for CHD, and GameCube/Wii images for RVZ when that's on.
Xbox: with Make Xbox images bootable on, full disc images are cut to their
game partition and `.xiso` files renamed right away; with Xbox to squashfs on,
the images are then queued for packing.

## Not offered (yet)

- **PS3**: measured about 2% smaller as squashfs; Redump's PS3 images are
  encrypted and barely compress.
- **3DS** (`.zcci`), **Wii U** (`.wua`), **Xbox 360** (`.zar`): Batocera
  reads these compressed formats, but making them needs tools Batocera
  doesn't ship.
