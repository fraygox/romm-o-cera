# Music

**Music** (on Home, once your RomM has soundtracks) does two things with
RomM's soundtracks: it plays them [in the app](#listening-in-the-app), and it
makes EmulationStation play them as its [menu music](#turning-it-on). The two
don't depend on each other.

RomM calls this its jukebox: the audio files in a game's `soundtrack/` folder
(checked with RomM 5.4). A RomM without any shows no Music on Home.

## Listening in the app

**Play music**, the first row of the Music screen, lists what there is to
listen to:

- **Everything, shuffled**: every track in RomM, in a new order each time
- **My RomM favourites** and your **playlists** (with a token that may read
  them, [below](#favourites-and-playlists-need-a-token-that-may-read-them))
- the **mixes** you saved
- every **game** RomM has a soundtrack for

**A** opens a row's tracks; **X** plays it at once. In a list of tracks:

| Button | What it does |
|---|---|
| **A** | plays from the selected track, and the list plays on from there; on the track that's playing, pauses or resumes |
| **X** | pauses or resumes whatever is playing; with nothing playing, plays the list from the top |
| **Y** | shuffles, or goes back to the list's order |
| **L2 / R2** | previous and next track |

The panel beside the list shows what's playing, how far in it is, and what
comes next.

The music goes on while you look around the app, and the top bar names the
track (tap it to get back to its list). It stops when the app closes or a game
starts. While something plays, **Play music** has it as its first row.

Tracks are fetched from RomM as they come up, the next one while the current
plays. Up to 256 MB of them stay on the box so they start at once the next
time; a track that's already in the menu music is played from there. The app
plays `.mp3`, `.ogg`, `.flac`, `.wav` and `.opus`; a track in another format
says it can't be played here.

## Menu music

EmulationStation can play RomM's soundtracks in its menus. You choose what
plays; RomM-o-Cera keeps the tracks on the box and in step with RomM.

### Turning it on

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

### What plays

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

#### Favourites and playlists need a token that may read them

Favourites and playlists belong to a RomM account, and a token only reads them
when it was made with the `playlists.read` scope. With a token that wasn't,
those two rows say **Not available with this token**; mixes, single games and
the games on this box work as they are. Make a token with that scope for the
account whose favourites you want, and enter it in Settings.

### Mixes

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

### Space

**Space for menu music** is the most room the tracks take: 1, 2, 5, 10 (the
default), 20 or 50 GB, or no limit. When what you chose is bigger, every album
keeps some of its tracks and the card says how many were left out. Downloads
also stop before the space to keep free on the drive
([Settings](settings.md)) is touched.

RomM doesn't say how big a track is before it's downloaded, so the plan goes
by each track's length; it settles once a few tracks are on the box.

## From a game's page

A game with a soundtrack in RomM shows a **Soundtrack** line (tracks and
length). Its Actions (X) offer **Play soundtrack**, which plays it in the app
from its first track, and **Add soundtrack to menu music** (**Remove
soundtrack from menu music** once it's in).
