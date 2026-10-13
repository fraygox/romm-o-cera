# Controls

Button labels follow your controller — Xbox, PlayStation (✕ ○ □ △) or
Nintendo — picked from the controller's name or set in Settings, and switch to
key names while you use a keyboard. Every control on screen (hint bar, menus,
dialogs, messages) is drawn by one helper, so they always match. The hint bar
always says what each button does on the screen you're on.

**Controller:** south button confirms (switch in Settings) · east back ·
Select downloads · Start home · **Hotkey + Start exits**. What X and Y do
depends on the screen (see [Lists](#lists)). `rommbrowser.pygame.keys` turns
off Batocera's pad-to-keyboard mapping for this app. A pad SDL has no
mapping for still works: with 10 or more buttons, buttons 6 and 7 are L2/R2
and 8 and 9 Select/Start (with fewer, 6 and 7 are Select/Start), and axes that
rest fully back are L2 then R2.

**Keyboard:** arrows move · Enter confirm · Esc back · X and Y as on a pad ·
PgUp/PgDn page · `[` `]` as L2/R2 · Tab downloads · Home home. Search and text
fields take typing directly (Enter runs the search / saves). A keyboard and a
pad work side by side.

**Touch / mouse (basic):** tap a list row to select it, tap again to open (while picking, one tap picks it); tap
menu entries, keys, buttons and the hint bar; tap the logo for Home and the
breadcrumbs to go back; drag a finger or scroll the wheel to move through
lists (a mouse click never scrolls). A click acts on what was under it when
pressed, if let go on the same thing. On a setting or menu row with a choice,
a tap left of the value's middle (where ‹ is) steps back, anywhere else steps
on; the hint bar's left/right and L2/R2 glyphs work both ways too.

**Search's top matches:** move right past the end of a keyboard row (or left
past its start) to reach them; up/down picks one, A opens it, B or left/right
returns to the keyboard. A game already on the box opens its menu (play now,
delete or download again), as in a game list; tapping a match does the same.

## Lists

Every list moves the same way:

- **Up/down** a row; **L/R** (PgUp/PgDn) and **left/right** a page. In
  Collections, left/right go to the previous or next group instead, and on
  the tiles above Space's and Check games' lists they move between the tiles.
- **L2/R2** (`[` `]`) jump to the previous or next letter in a list sorted by
  name, or to the previous or next system in a list grouped by system. Where
  you land goes to the top of the list. Lists you can sort (Free up space,
  Games from RomM) jump only while sorted by name.
- The hint bar shows L/R only when the list is longer than a page, and L2/R2
  only when there's somewhere to jump.
- **X** opens the list's menu: what you can do with the whole list, its sort
  and filters, and picking. A few lists have just one such action, and X
  does it straight away (the hint bar names it).

### Picking several

Every list where you might act on more than one row picks them the same way:
game lists, Games from RomM, Free up space, Leftovers, a system's games in
Compress, Downloads, Check games' findings and Games ES can't see.

1. **X** › **Pick several …** (or **Pick all**) starts picking.
2. While picking, **A** picks or unpicks the row and moves to the next one.
3. **X** opens the menu with what you can do with the picks at the top
   ("Download 3 picked", "Delete 3 picked", "Cancel 2"…), then Pick all,
   Clear picks and Stop picking.
4. **B** stops picking. The picks stay ticked (and in the menu) until you use
   or clear them.

Deleting or cancelling picks always asks first. Free up space is only for
picking, so there A picks from the start and B goes back.

### What A, X and Y do

| Screen | A | X | Y | L2/R2 |
|---|---|---|---|---|
| Home | Open | | Surprise me: a random game | |
| Game lists | Download (a game on the box: its menu) | Menu: more like this, surprise me, download all, filters (region, players), sort, pick several | Search | Letter |
| Platforms | Open | Surprise me: a random game from it | Search this platform | |
| Collections | Open | Menu: open, surprise me, download every game | | Letter (one list) |
| Downloads | That download's options (a finished game: Play now) | Menu: pause or resume all, clear finished, pick several | Clear finished | Conversions |
| Conversions | Options | Start now | Clear finished | |
| Games from RomM | The game's menu: play now, delete, download again, more like this | Menu: sort, pick several | Sort | Letter (by name) |
| Free up space | Pick | Menu: delete picked, pick enough to free 10–100 GB, sort | Sort | Letter (by name) |
| Leftovers | Delete it | Menu: delete all, pick several | Delete all | System |
| Compress | That system's games | Compress the whole system | | |
| Compress › a system | Compress this one | Menu: compress all, pick several | | Letter |
| BIOS files | Get from RomM | Get all from RomM | | Letter |
| Check games, a check's results | Start / look / download again | Menu: download all damaged again, pick several | Download all damaged again | System |
| Games ES can't see | Its options | Menu: pick several, pick all | | System |
| Space | Open / free up space here | | | |

While picking, A picks on every one of these lists, and X's menu starts with
what to do with the picks.
