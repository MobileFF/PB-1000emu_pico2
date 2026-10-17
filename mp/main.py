"""
PB-1000 Emulator - entry-point launcher.

The actual ~1000-line body lives in main_entry.py now (frozen into
firmware -- see that module's docstring and 調査用/mp_freeze_tradeoffs.md
for why the split exists). This file stays deliberately tiny and is
never frozen itself, so it keeps behaving exactly as before:

- `mpremote cp main.py :main.py` + power-cycle -> auto-boots the emulator
  (real/production use).
- Left off the device filesystem during development (this project's own
  workflow -- see [[project_dev_workflow_main_py_not_deployed]]) so a
  bare power-cycle does NOT auto-start the emulator and tie up the
  serial port; run on demand instead via `mpremote run main.py`, which
  sends this file's content directly regardless of what's deployed.

`from main_entry import main` resolves main_entry.py the normal way (the
device's own filesystem copy, if any, shadows the frozen one -- same as
any other `import`): put a filesystem copy there during active
development on main_entry.py's own content, same as the other 41 frozen
mp/*.py modules.
"""
from main_entry import main

main()
