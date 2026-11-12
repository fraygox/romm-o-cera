# Music

**Music** (on Home, once your RomM has soundtracks) makes EmulationStation play
RomM's soundtracks as its menu music. You choose what plays; RomM-o-Cera keeps
the tracks on the box and in step with RomM.

RomM calls this its jukebox: the audio files in a game's `soundtrack/` folder
(checked with RomM 5.4). A RomM without any shows no Music on Home.

## Turning it on

Open **Music**, switch **Menu music** on, then switch on what should play.
The tracks are downloaded to `music/romm-o-cera/` in the box's music folder
(`/userdata/music/romm-o-cera/`), and EmulationStation picks from there at
random, as it does with any music in that folder.

- While any track is there, Batocera's own songs don't play. Turn Menu music
  off and they return: the app asks first, then removes the tracks it placed.
- Music of your own in the music folder keeps playing alongside, and is never
  touched: the app removes only files it placed itself.
- EmulationStation plays `.mp3`, `.ogg`, `.flac`, `.wav` and `.opus`. Tracks in
  other formats stay in RomM, and the card at the top says how many.
- With EmulationStation's **Only play system-specific music** on, it skips this
  folder: leave that setting off.

## What plays

Each source is a switch, and what's switched on adds up. A track two sources
ask for is kept once.

| Source | What it brings |
|---|---|
| **My RomM favourites** | every track you starred in RomM's jukebox |
| **Games on this box** | the soundtracks of the games you've downloaded; a game you delete takes its tracks along |
| a **playlist** | a playlist from RomM: yours, or one shared with everyone |
| a **game** | one game's whole soundtrack, added here (**Add a game's soundtrack**) or from the game's page |
| a **mix** | every track in RomM that fits what you picked (below) |

The card at the top shows how many tracks are in the menus and the space they
take, and what the last sync did: up to date, still fetching, how many tracks
were left out, or why RomM couldn't be asked. When RomM can't be reached,
what's on the box stays and keeps playing.

The box follows RomM when you change a source, each time the app opens, and
when you press **Sync now** (X).

### Favourites and playlists need a token that may read them

Favourites and playlists belong to a RomM account, and a token only reads them
when it was made with the `playlists.read` scope. With a token that wasn't,
those two rows say **Not available with this token**; mixes, single games and
the games on this box work as they are. Make a token with that scope for the
account whose favourites you want, and enter it in Settings.

## Mixes

**New mix** builds a source from RomM's own tags: game genres, music genres,
artists, platforms, decades and a longest track length. Each row opens its
values with a track count beside each; tick as many as you like.

- A track fits when it matches one pick in every row that has any. A row with
  nothing ticked doesn't narrow the mix.
- The card at the top counts what fits as you go.
- A mix is a question, not a list: soundtracks added to RomM later that fit
  arrive by themselves.
- A mix is named after what you picked unless you name it. On the Music
  screen, **Y** on a mix changes or deletes it.

Mixes are saved on the box, not in RomM.

## Space

**Space for menu music** is the most room the tracks take: 1, 2, 5, 10 (the
default), 20 or 50 GB, or no limit. When what you chose is bigger, every album
keeps some of its tracks and the card says how many were left out. Downloads
also stop before the space to keep free on the drive
([Settings](settings.md)) is touched.

RomM doesn't say how big a track is before it's downloaded, so the plan goes
by each track's length; it settles once a few tracks are on the box.

## From a game's page

A game with a soundtrack in RomM shows a **Soundtrack** line (tracks and
length). Its Actions (X) offer **Add soundtrack to menu music**, and **Remove
soundtrack from menu music** once it's in.
