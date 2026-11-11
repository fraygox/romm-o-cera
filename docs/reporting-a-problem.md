# Reporting a problem

File it at <https://github.com/fraygox/romm-o-cera/issues>. Three things make
a report answerable: what you did and what happened, the version, and the
app's log.

## The version

**Settings** shows it in the About line at the bottom of its right panel
(a nightly build shows its date and commit too). If you can't reach Settings,
the log's `start` lines are enough.

## The log

The app and its download and compression workers write one file on the box:

```
/userdata/system/romm-browser/romm-browser.log
```

Older lines are in `romm-browser.log.1` … `.9` beside it (a new file starts
every 10 MB). To get it off the box, pick one:

- **Network share:** open `\\BATOCERA\share\system\romm-browser\` from a PC
  (on a Mac or Linux: `smb://batocera/share/system/romm-browser/`) and copy
  `romm-browser.log`.
- **SSH** (user `root`, password `linux` unless you changed it):
  ```sh
  scp root@batocera:/userdata/system/romm-browser/romm-browser.log .
  ```

Make the problem happen first, then copy the file, so the log holds it. Read
it over before you post it: it names your games, and its error lines can
name your RomM server's address. Remove what you don't want public.

**Never post `config.json`** from that folder: it holds your RomM token.

## A controller that does the wrong thing

Start the app with the controller connected, press the buttons that
misbehave, leave the app (or copy the log while it's open) and send the log.
Each start, and each time a controller is plugged in or taken away, writes
lines like these:

```
pads: Batocera has mappings for 257
pad: Steam Deck [03000000de2800000512000011010000] 24 buttons, 10 axes, 0 hats: mapped
```

- **`mapped`**: the app reads the pad through Batocera's mapping for it. A
  wrong button is wrong in that mapping: map the pad again in EmulationStation
  (**Main menu → Controller & Bluetooth settings → Controller mapping**) and
  start the app again. If it's right in EmulationStation and wrong here, say
  which buttons do what.
- **`no mapping, guessed as …`**: Batocera has no mapping under that ID, so
  the app guessed the buttons by number. Mapping the pad in EmulationStation
  gives it one.
- **`pads: Batocera has mappings for 0`**: the app couldn't read
  EmulationStation's `es_input.cfg` at all.

Say also what the pad is (make and model, USB or Bluetooth, or the handheld
it's built into) and your Batocera version (**Main menu → System settings → Information**).
A keyboard and the touch screen keep working while a controller doesn't
([Controls](controls.md)).

## A download, a conversion or a game that won't start

Send the log from after it went wrong, with the game's system and file type
(`.zip`, `.chd`, `.iso`…), and what Activity or the game's page said. For a
game that downloads but won't start in its emulator, say whether it starts
when you copy the same file to the box by hand.

## A screen that looks wrong

A photo or screenshot, your screen's resolution, and the theme
(**Settings → This app → Theme**).
