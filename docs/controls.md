# Controls

Button labels follow your controller: Xbox, PlayStation (✕ ○ □ △) or
Nintendo, picked from the controller's name or set in **Settings → Button
labels**, and they switch to key names while you use a keyboard. Every
control on screen (hint bar, menus, dialogs, messages) is drawn the same
way, so what you see always matches the buttons in your hand. The hint bar
at the bottom always says what each button does on the screen you're on.

These pages name the buttons by position, the Xbox way: **A** is the bottom
face button (confirm), **B** the right one (back), **X** the left one and
**Y** the top one. **Settings → Confirm button** swaps confirm and back.

## Controller

**A** confirms · **B** goes back · **Select** opens Downloads · **Start**
goes Home · **Hotkey + Start exits**. What X, Y, L/R and L2/R2 do depends on
the screen ([below](#on-each-screen)).

`rommbrowser.pygame.keys` turns off Batocera's pad-to-keyboard mapping for
this app, so the buttons reach it directly. A pad SDL has no mapping for
still works: with 10 or more buttons, buttons 6 and 7 are L2/R2 and 8 and 9
Select/Start (with fewer, 6 and 7 are Select/Start), and axes that rest
fully back are L2 then R2.

## Keyboard

Arrows move · **Enter** or **Space** confirm · **Esc** or **Backspace** back ·
**X** and **Y** as on a pad · **PgUp/PgDn** as L/R · `[` `]` as L2/R2 ·
**Tab** Downloads · **Home** home · **Alt+F4** exits. In Search and text
fields you just type: Enter runs the search or saves, Backspace deletes, Esc
goes back. A keyboard and a pad work side by side.

## Touch and mouse

- Tap a row to select it, and tap it again to open it. While picking, one
  tap picks or unpicks it.
- Tap menu entries, keyboard keys, dialog buttons and tiles to use them
  straight away.
- Tap a button in the hint bar to press it, including **Home** and
  **Downloads** at its right end.
- Tap the logo for Home, and the breadcrumbs beside it to go back a screen.
- On a setting or menu row with a choice, a tap left of the value's middle
  (where ‹ is) steps back and anywhere else steps on. The hint bar's
  left/right and L2/R2 glyphs work both ways too.
- Drag a finger or turn the mouse wheel to move through a list. A mouse
  click never scrolls.
- A tap or click acts on what was under it when you pressed, if you let go
  on the same thing.
- Tap outside a dialog to close it.
- Tap a "Ready" message to play the game it names
  ([Play now](browsing-and-downloading.md#play-now)).
- In the theme picker, tap a card to try it and tap it again to keep it, and
  tap its OLED pill to switch it. On the Custom screen, tap a colour, a
  strength step or a mode.

## Lists

Every list moves the same way:

- **Up/down** move a row; **L/R** (PgUp/PgDn) and **left/right** move a
  page. In Collections, left/right go to the previous or next group instead;
  on the tiles above Space's and Check games' lists they move between the
  tiles; and the platform cards and theme cards are grids, where left/right
  move one card.
- **L2/R2** (`[` `]`) jump to the previous or next letter in a list sorted
  by name, or to the previous or next system in a list grouped by system.
  Where you land goes to the top of the list. Lists you can sort (Free up
  space, Games from RomM) jump only while sorted by name.
- The hint bar shows L/R only when the list is longer than a page, and L2/R2
  only when there's somewhere to jump.
- **X** opens the list's Actions menu: what you can do with the whole list, its
  sort and filters, and picking. A few lists have just one such action, and
  X does it straight away (the hint bar names it).

### Picking several

Every list where you might act on more than one row picks them the same
way: game lists, Games from RomM, Free up space, Leftovers, a system's games
in Compress, Downloads, Check games' findings and Games ES can't see.

1. **X** › **Pick several …** (or **Pick all**) starts picking.
2. While picking, **A** picks or unpicks the row and moves to the next one.
3. **X** opens the menu with what you can do with the picks at the top
   ("Download 3 picked", "Delete 3 picked", "Cancel 2"…), then Pick all,
   Clear picks and Stop picking.
4. **B** stops picking. The picks stay ticked (and in the menu) until you
   use or clear them.

Deleting or cancelling picks always asks first. Free up space is only for
picking, so there A picks from the start and B goes back.

## Menus and dialogs

In a menu, **A** runs the selected entry. An entry with a switch or a
‹ choice › changes in place with A or left/right, and the menu stays open.
**B** (or X) closes the menu.

A question shows its buttons: **A** for what it offers ("Download",
"Delete 3", "Play now"), **B** to go back without doing it. A Surprise me
pick adds **Y Another**, to roll again.

## On each screen

| Screen | A | X | Y | L2/R2 |
|---|---|---|---|---|
| Home | Open | | Surprise me | |
| Platforms | Open | Surprise me from this platform | Search this platform | |
| Collections | Open | A collection's menu: open, surprise me, download every game | | A–Z (in a single list) |
| Game lists | Download (a game on the box: its menu) | Actions: more like this, surprise me, download all, filters, sort, pick several | Search | A–Z |
| Search keyboard | Type the key | Delete | Space | |
| Downloads | That download's options (a finished game: Play now) | Actions: pause or resume all, clear finished, pick several | Clear finished | Conversions |
| Conversions | Options: convert next, remove from the queue, try again | Start now | Clear finished | |
| On this box | Open | | | |
| Games from RomM | The game's menu: play now, delete, download again, more like this | Actions: sort, pick several | Sort | A–Z (by name) |
| Space | Open / free up space in that system | | | |
| Free up space | Pick | Actions: delete picked, pick enough to free 10–100 GB, sort | Sort | A–Z (by name) |
| Leftovers | Delete it | Actions: delete all, pick several | Delete all | System |
| Compress | That system's games (a can't-convert row: why) | Compress the whole system | | |
| Compress › a system | Compress this one | Actions: compress all, pick several | | A–Z |
| BIOS files | Get from RomM | Get all from RomM | | A–Z |
| Check games | Start a check / look / download again | Actions: download all damaged again, pick several | Download all damaged again | System |
| Games ES can't see | Its options: unpack, move, delete | Actions: pick several, pick all | | System |
| Settings | Change / open | | | |
| Theme picker | Keep this theme (Custom: open it) | Edit a copy | OLED on/off | |
| Custom theme | Keep and use it | | | |

While picking, A picks on every list that can, and X's menu starts with what
to do with the picks.

A few screens use more buttons:

- **Search keyboard**: **Start** (or R) searches. Move right past the end of
  a keyboard row (or left past its start) to reach the top matches beside
  it; there up/down pick one, A opens it (a game already on the box opens
  its menu: play now, delete or download again), and B or left/right go
  back to the keyboard. On a keyboard, just type, and Enter searches.
- **Text fields** (server address, API token, pairing code): A types the
  key, X deletes, Y switches to capitals and symbols, **Start** saves and B
  cancels.
- **Settings**: left/right step through a setting's choices, L/R jump six
  rows.
- **Theme picker**: the D-pad moves round the cards, L/R go to the previous
  or next group. See [Themes](themes.md#the-theme-picker).
- **Custom theme**: up/down pick a choice, left/right change it, L/R take
  bigger steps. See [Themes](themes.md#custom).
