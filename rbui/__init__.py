"""RomM-o-Cera's UI, run by rommbrowser.pygame (Batocera's pygame system starts that file).

  theme     scaling (logical units -> screen pixels), the colour palette and its themes
  draw      fonts, drawing, text, prose with controller glyphs
  pads      controller styles: what each hint token shows
  sfx       the UI's sounds
  input     keyboard, controllers and joysticks -> actions
  app       the App: screen stack, modal, toasts, main loop, covers
  screens/  the screens, a module per family
  modals    menus and dialogs
  startup   start-up chores

State that changes while the app runs lives in one module and is read through
it, never copied out by `from ... import`: theme.BG and the rest of the palette
(apply_theme), theme.K (the scale), draw.TEXT_BUDGET / draw.COVER_BUDGET,
draw.FONTS, pads.INPUT_MODE / pads.PAD_STYLE. The background workers (rb_*.py)
never import this package.
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the app folder (rommbrowser.pygame's)
APP_NAME = "RomM-o-Cera"
