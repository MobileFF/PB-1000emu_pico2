# EMULATOR MENU User Guide

The PB-1000 emulator includes an **EMULATOR MENU** that gathers settings and features specific
to the emulator itself — things that don't exist on the real PB-1000 hardware. This guide covers
how to open it, how to navigate it, and what each item does.

> This guide is written for end users. For internal implementation details (source layout, etc.),
> see `dev_guide_en.md`.

---

## 1. Opening and Closing

| Key | Action |
| :--- | :--- |
| **Win (GUI) + F7** | Open the EMULATOR MENU |
| **Up / Down** | Move the cursor (only active inside this menu) |
| **EXE** | Select / activate the highlighted item |
| **BREAK** | Go back one screen (inside a sub-menu) / close the menu (at the top level) |

While the menu is open, the PB-1000's own execution (CPU stepping) is paused. Closing the menu
resumes whatever program was running from exactly where it left off. Changes take effect
immediately; unless a section explicitly says it's written to `pb1000.ini`, the setting reverts
to its default on the next boot.

---

## 2. Menu Layout

The EMULATOR MENU has four categories plus **Exit**. Selecting a category opens its own
sub-menu.

```
== EMULATOR MENU ==
├─ Toggles   (turn various features on/off)
├─ Storage   (save/load disk, RAM, and screen state)
├─ Display   (change on-screen colors)
├─ System    (system info and reset-related actions)
└─ Exit      (close the menu)
```

Each row shows a **badge** on the right (`ON` / `OFF` / `N/A`, or a current setting value).

---

## 3. Toggles

| Item | What it does |
| :--- | :--- |
| **Serial Console** | Toggle real-time output of on-screen LCD characters to the console UART (GP4/GP5) |
| **RS-232C (PIO)** | Toggle the PB-1000's RS-232C interface (virtual serial communication) |
| **vFDD** | Toggle the virtual floppy drive. **Cannot be turned off while the FDD is mid-transfer** (a message is shown if you try) |
| **Beep** | Mute/unmute the beep sound |
| **Joystick** | Toggle joystick input |
| **Color VRAM (VDP)** | Switch the screen between plain two-color monochrome and per-pixel color rendering |

Every item here flips ON/OFF immediately on EXE; none of these are saved to a config file.

---

## 4. Storage

| Item | What it does |
| :--- | :--- |
| **FD Swap** | Switch the virtual floppy disk's image file |
| **RAM Save** | Snapshot the current RAM contents to the SD card (`/sd/rams/`) |
| **RAM Load** | Restore RAM from a previously saved snapshot |
| **VRAM Save** | Save the current screen contents (LCD VRAM) to a file — same as the PrintScreen key |
| **Full Capture** | Save the entire physical screen, including the bezel and function key bar, as a single image (PPM) |

> [!WARNING]
> **Running RAM Load immediately triggers a reset and reboot, and the EMULATOR MENU closes
> automatically.** Whatever program is currently running is discarded, so use RAM Save first if
> you need to keep it.

Both **RAM Save** and **RAM Load** open a screen for picking the destination/source folder name
(when saving a new one, you can type a name using letters, digits, and hyphens).

---

## 5. Display

| Item | What it does |
| :--- | :--- |
| **Foreground Color** | Change the color of "lit" LCD pixels |
| **Background Color** | Change the color of "unlit" LCD pixels |

Selecting either opens a numeric entry screen (RGB332 value, 0–255) followed by a color preview
screen. Confirming with EXE saves the value to `pb1000.ini`, so it persists across reboots.

---

## 6. System

| Item | What it does |
| :--- | :--- |
| **Hook Status** | A read-only screen listing which extensions (modules under `mp/ext/`, etc.) are currently active |
| **Reset** | Perform a hardware-style reset on the spot — the same as pressing NumLock on a real keyboard |
| **NEW ALL (clear memory)** | Run the PB-1000's NEW ALL function, which erases all user memory |

> [!WARNING]
> **NEW ALL erases all of the user's memory** (saved programs and data). A confirmation screen
> (EXE: yes / BRK: no) is shown first — be careful not to trigger it by accident. This action
> cannot be undone.

**Reset** runs immediately with no confirmation screen, and the EMULATOR MENU closes
automatically afterward (matching the real hardware's NumLock key).

Inside the **Hook Status** screen, Up/Down scrolls the list; BREAK returns to the System
sub-menu.

---

## 7. Troubleshooting

- **Lost track of which menu level you're on?** Keep pressing BREAK — it always steps you back:
  sub-menu → top-level menu → menu closed.
- **Accidentally toggled Color VRAM (or another toggle)?** Select the same item again with EXE
  to flip it back.
- **Accidentally ran RAM Load or NEW ALL?** These take effect immediately and can't be undone.
  Get in the habit of using **RAM Save** regularly to keep backups.

---

## Related Documents

- `usage_guide_en.md` — General keyboard operation, serial features, joystick, etc.
- `hardware_guide_en.md` — Wiring and GPIO assignments (including the REPL UART wiring)
