# Wii U games

Cemu, Batocera's Wii U emulator, opens three kinds of game file. RomM-o-Cera
can turn a disc image into either of the smaller two, in the background like
the rest of [disc compression](disc-compression.md), paused while a game runs.

| File | What it is | Size | Key to make it | Key to play it | Back to the original disc? |
|---|---|---|---|---|---|
| `.wud` | the whole disc, byte for byte, encrypted | always 25 GB | — | the disc key | it *is* the original |
| `.wux` | the same disc, with each repeated or empty 32 KB sector stored once | usually a few GB | none | the disc key | yes, exactly |
| `.wua` | the game's files, decrypted, in Cemu's own archive | usually the smallest | the disc key, once | none | no |

Most Wii U collections, RomM's included, already hold `.wux` files.

## Which switch does what

- **Wii U to .wux** (on by default): every `.wud` becomes a `.wux`. Nothing is
  lost, no key is needed, and the `.wux` still matches RomM's checksums once
  expanded, so [Check games](on-this-box.md#check-games) can check it.
- **Decrypt Wii U discs** (off by default): a `.wud` or `.wux` whose disc key
  you have becomes a `.wua`. It's the tidiest result: smaller still, and Cemu
  needs no key to play it. It isn't reversible, so it's your choice to make.

With both on, a disc you have the key for becomes a `.wua`, and the rest become
`.wux`. Turning Decrypt on later brings your `.wux` files into
[Compress games already here](on-this-box.md#compress-games-already-here) as
`.wua`. The details, and what's checked before an image is deleted, are under
[Disc compression](disc-compression.md#wii-u-to-wux-on-by-default).

Both use **wuatool**, a small tool (about 3 MB, x86_64 and 64-bit ARM) built
from Cemu's own disc code. The app fetches it by itself once there are Wii U
games to compress; a box without Wii U games downloads nothing.

## Disc keys

Each Wii U disc has its own key. RomM-o-Cera comes with none: it uses the keys
for games you own, from any of these places:

- **Cemu's `keys.txt`**, `/userdata/bios/cemu/keys.txt`: one 32-digit key per
  line (anything after `#` is a comment). It's the file Cemu reads to play
  `.wud` and `.wux` discs, so a key there serves both.
- **`/userdata/bios/wiiukeys/`**: `.key` files (16 bytes each), key lists in
  the `keys.txt` style, or zips of either.
- **Your RomM**, as Wii U firmware: turning on Decrypt Wii U discs copies
  them into `bios/wiiukeys/`.

Every key is tested against the disc itself, so a wrong one is never used. A
disc with no key stays a `.wux`. Compress lists it as can't decrypt, with the
reason.

## Updates and DLC

Cemu keeps game updates and DLC outside the game file, in its own storage,
matched to the game by its title ID. They work with any of the three formats,
and converting a disc to `.wua` leaves installed updates and DLC working.

Some later disc printings carry the game's update, and sometimes DLC, on the
disc itself. Cemu ignores those when it plays a `.wud` or `.wux`. When such a
disc becomes a `.wua`, they go into the same `.wua`, each in its own folder as
Cemu's own "Convert to .wua" packs them, so the game starts already updated.

Not handled yet: eShop titles and separately downloaded updates or DLC (NUS
packages). Install those through Cemu.

## Where Cemu runs

Batocera ships Cemu for x86_64 PCs, and on recent Batocera builds for
Snapdragon handhelds too. wuatool is built for both. The app never offers
Wii U compression on a box whose CPU it isn't built for.
