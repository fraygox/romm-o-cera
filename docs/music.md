# Music

**Music** (on Home, once your RomM has soundtracks) does two things with
RomM's soundtracks: it plays them [in the app](#listening-in-the-app), and it
makes EmulationStation play them as its [menu music](#turning-it-on). The two
don't depend on each other.

RomM calls this its jukebox: the audio files in a game's `soundtrack/` folder
(checked with RomM 5.4). A RomM without any shows no Music on Home.

## Listening in the app

Music opens on what there is to listen to, top to bottom:

- **Pills:** **Shuffle everything** (every track in RomM, in a new order each
  time), **Search**, and **Menu music**, which says whether it's on and opens
  [its screen](#menu-music). While something plays, **Now playing** comes
  first.
- **Browse by:** how the shelves below are grouped. **Systems**, **Game
  genres**, **Composers**, **Decades** or **A–Z**. Left and right on that row
  change it; **L** and **R** change it from anywhere on the screen.
- **Your lists:** your RomM favourites and playlists (with a token that may
  read them, [below](#favourites-and-playlists-need-a-token-that-may-read-them))
  and the mixes you saved, each shown by the covers its tracks are from.
- **Recently added** (once RomM has more soundtracks than fit on one shelf) and
  **On this box**, the soundtracks of the games you've downloaded.
- One shelf for each group, covers side by side, with how many it holds. A game
  sits under each of its genres; a composer gets a shelf when two or more
  soundtracks are mostly theirs.

The panel on the right shows what's focused: its cover, who it's by, how long
it is and its first tracks. **A** opens it and **X** plays it at once.

**Y** on a shelf shows all of it as a wall. There **Y** changes the order (by
name, newest in RomM, longest, oldest game), **L2** and **R2** jump to the
next letter while it's in name order, and **L** and **R** go a screenful.

**Search** finds soundtracks by the game's name, composers, and tracks by
their title, artist or album. A soundtrack opens, a composer shows everything
of theirs as a wall, and a track plays, with the other tracks found playing
on after it.

Grouping by system works as soon as Music opens. The other groupings, the
sort orders and search by name need to know more about each soundtrack than
RomM's list gives, so the first time the app reads RomM's whole track list in
the background, which takes a moment. It keeps what it
learned on the box and reads again only when RomM's soundtracks have changed.

In a list of tracks:

| Button | What it does |
|---|---|
| **A** | plays from the selected track, and the list plays on from there; on the track that's playing, pauses or resumes |
| **X** | pauses or resumes whatever is playing; with nothing playing, plays the list from the top |
| **Y** | shuffles, or goes back to the list's order |
| **L2 / R2** | previous and next track |

A soundtrack lists its tracks by number. While something from the list plays,
the panel beside it shows the track, how far in it is and what's up next;
otherwise it describes the list.

The music goes on while you look around the app, and the top bar names the
track and the soundtrack it's from (tap it to get back to its list). It stops
when the app closes or a game starts. The cover that's playing carries a play
mark on every shelf it's on.

Tracks are fetched from RomM as they come up, the next one while the current
plays. Up to 256 MB of them stay on the box so they start at once the next
time; a track that's already in the menu music is played from there. The app
plays `.mp3`, `.ogg`, `.flac`, `.wav` and `.opus`; a track in another format
says it can't be played here.

## Menu music

EmulationStation can play RomM's soundtracks in its menus. You choose what
plays; RomM-o-Cera keeps the tracks on the box and in step with RomM.

### Turning it on

Open **Music**, choose the **Menu music** pill, switch **Menu music** on (the
card at the top), then switch on what should play.
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

**Space for menu music**, the last row of the Menu music screen, is the most
room the tracks take: 1, 2, 5, 10 (the
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
