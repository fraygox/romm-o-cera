# PS3 games

RomM-o-Cera turns PS3 disc images into **`.ps3` game folders**, the layout
RPCS3 boots from EmulationStation and installs game updates and DLC for. It
runs in the background like disc compression, and is paused while a game runs.

- **A download** of a zipped disc image (`Game.zip` holding `Game.iso`) is
  read straight from the zip, so there's no room needed for an unzipped copy.
  It needs space for the finished folder only. A loose `Game.iso` works the
  same way.
- **Games already on the box:** a PS3 zip shows up in
  [Games ES can't see](on-this-box.md#games-es-cant-see), where **Unpack**
  turns it into a game folder. Loose `.iso` files and PS3 zips are also
  listed under [Compress games already here](on-this-box.md#compress-games-already-here).
- **Checked before anything is deleted:** every file is written from the
  image, the game's start files (`EBOOT.BIN`, `PARAM.SFO`) are checked, and a
  zip's own checksum is checked by reading it to the end. Only then does the
  image go.

[Check games](on-this-box.md#check-games) matches a `Game.ps3` folder with
RomM's `Game.zip` or `Game.iso` and checks the files RPCS3 boots it by; a full
check reads every file.

A disc that can't become a folder (encrypted with no key, damaged, or one with
no game to boot) is left exactly as it is, and ES doesn't list it. Compress
games already here lists it as **can't convert**, with the reason.

Setting: **PS3 games as folders**, on by default.

## Encrypted discs and disc keys

Many PS3 disc images, including Redump's, are encrypted. Each disc has its own
key, and the image has to be decrypted with it before RPCS3 can boot it.

- **Decrypt PS3 discs** (Settings → Disc compression) is **off by default**.
- It uses **disc keys for games you own**, which you provide. RomM-o-Cera
  comes with no keys and downloads them from nowhere but your own RomM server.
- An image that's already decrypted needs no key and no setting.

### Where disc keys go

Put them in this folder on the box (Batocera's network share:
`\\BATOCERA\share\bios\ps3keys`):

```
/userdata/bios/ps3keys/
├── Game Name (USA).key        16-byte key file
├── Other Game (Europe).dkey   the same key as 32 hex digits (text)
└── keys.zip                   any number of .key/.dkey files, or zips of them
```

- **File names:** name each key after its disc, the same as the image's file
  name without the extension. `Game Name (USA).zip` uses
  `Game Name (USA).key`.
- **Zips** are read in place and never unpacked (see the layouts below).
- **Anything else** in the folder, or a file that isn't a valid key, is
  ignored.

### What goes inside a keys zip

A zip in `ps3keys/` can have any name ending in `.zip`. Inside it, either
layout works, and the two can be mixed:

```
keys.zip                          key files directly inside
├── Game One (USA).key
├── Game Two (Europe).dkey
└── more/Game Three (Japan).key   folders inside the zip are fine

keys.zip                          one small zip per disc
├── Game One (USA).zip
│   └── Game One (USA).key
└── Game Two (Europe).zip
    └── Game Two (Europe).dkey
```

- **The key's name** comes from the key file's own name, without `.key` or
  `.dkey`. The names of the folders and per-disc zips around it don't count.
- **One level of zips inside a zip.** A zip inside a per-disc zip is ignored.
- **Formats:** a `.key` holds the 16-byte key; a `.dkey` holds the same key as
  32 hex digits (a trailing newline is fine).
- **Two keys with the same name:** the first one read is used.
- **Limits:** a key file over 4 KB, or a zip over 256 MB, is skipped.

### Keeping disc keys in RomM

You can keep your keys in RomM instead, and every box gets them:

1. Upload the key files, or one zip of them (its name must contain "key",
   e.g. `keys.zip`), to RomM as **firmware** for the **PlayStation 3**
   platform. Or put them in RomM's library at `bios/ps3/` and rescan PS3.
2. On the box, turn on **Decrypt PS3 discs**. RomM-o-Cera copies them into
   `bios/ps3keys` and says how many keys are ready.

Turn the setting off and on again to pick up keys added to RomM later.

### How a key is matched

1. **By name:** a key with exactly the disc's name (any capitalisation) is
   tried first.
2. **By likeness:** then the keys whose names are most alike, by words and
   letters, which catches renamed files.
3. **Every other key.**

Each key is tested on the disc itself, on files whose first bytes are known,
so a wrong key is never used. The finished folder is checked again before the
image is deleted. Trying thousands of keys takes well under a second.

## Space

A zipped disc needs the zip plus the finished folder for a while, about twice
the game's size; afterwards only the folder remains. The conversion waits
until there's room, keeping your reserve free (`keep_free_gb`).
