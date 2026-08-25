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
├─ Display   (colors and resolution)
├─ System    (system info and reset-related actions)
└─ Exit      (close the menu)
```

Each row shows a **badge** on the right (`ON` / `OFF` / `N/A`, or a current setting value).

---

## 3. Toggles

| Item | What it does |
| :--- | :--- |
| **Beep** | Mute/unmute the beep sound |

Flips ON/OFF immediately on EXE; not saved to a config file. Kept because muting mid-session
without losing running state (which a reboot would cost) is a real need a boot-time setting
can't cover.

RS-232C (PIO), vFDD, Joystick, and Color VRAM (VDP) toggles were removed from this menu on
2026-08-22. Each corresponds 1:1 to a `pb1000.ini` setting (`[rs232c] enable` /
`[disk] enabled` / `[joystick] enable` / `[display] vdp_enable`) — change it in the F1
boot-time setup menu, Save & Exit (or F10), and the Pico reboots automatically to apply it.
None of these need to flip instantly mid-session, so this menu no longer needs its own separate
live-toggle implementation for each.

Serial Console (outputting LCD characters to a console over GP4/GP5, UART1) was itself **removed
entirely** on the same date, 2026-08-22 — not migrated to a setting. The UART keyboard input
feature (typing PB-1000 keys from a serial terminal), which shared the same UART1, was removed
at the same time. RS-232C (PIO UART, GP6/GP13) is unrelated and remains available.

---

## 4. Storage

| Item | What it does |
| :--- | :--- |
| **FD Swap** | Switch the virtual floppy disk's image file |
| **RAM Save** | Snapshot the current RAM contents and CPU state (PC/registers) to the SD card (`/sd/rams/`) |
| **VRAM Save** | Save the current screen contents (LCD VRAM) to a file — same as the PrintScreen key |
| **Full Capture** | Save the entire physical screen, including the bezel and function key bar, as a single image (PPM) |

**RAM Save** opens a screen for picking the destination folder name (when saving a new one, you
can type a name using letters, digits, and hyphens).

> [!NOTE]
> **RAM Load** (restoring from a previously saved snapshot) was removed from this menu on
> 2026-08-22. To reach a different profile's saved state — or an earlier save of the current
> profile — use **System > Reboot Emulator (MCU)** and pick it again at the boot-time profile
> picker (that picker already loads whichever profile's save you choose). There is no longer a
> way to switch to a different profile's saved state without a reboot.

---

## 5. Display

| Item | What it does |
| :--- | :--- |
| **Foreground Color** | Change the color of "lit" LCD pixels |
| **Background Color** | Change the color of "unlit" LCD pixels |
| **LCD Height** | Toggle the LCD's vertical resolution between 32-dot and 64-dot |

Selecting **Foreground Color** / **Background Color** opens a numeric entry screen (RGB332
value, 0–255) followed by a color preview screen. Confirming with EXE saves the value to
`pb1000.ini`, so it persists across reboots.

**LCD Height** flips between 32-dot and 64-dot immediately on each EXE press (no sub-screen).
Like the other toggle-style items, it is session-only and is NOT saved to `pb1000.ini` — it
reverts to the `[display] lcd_height` setting on the next boot. Right after switching to
64-dot mode, the newly-added rows (5-8) may still show whatever was previously sitting in VRAM
until the running program redraws them — this matches real PB-1000 hardware behavior and is
not a bug.

> [!NOTE]
> The **HDMI** mirror output ON/OFF toggle was removed from the EMULATOR MENU on 2026-08-24.
> Use the boot-time F1 setup menu, or `[hdmi] enable` in `pb1000.ini`, instead (changes take
> effect after saving and an MCU reboot). See [hardware_guide_en.md](hardware_guide_en.md) §9
> and [usage_guide_en.md](usage_guide_en.md) §11 for details.

---

## 6. System

| Item | What it does |
| :--- | :--- |
| **Hook Status** | A read-only screen listing which extensions (modules under `mp/ext/`, etc.) are currently active |
| **CPU Status** | A read-only screen showing the current CPU registers (PC, flags, UA, IA/IB/IE, IX-KY, $0-$31) plus a raw byte dump and disassembly around PC |
| **Reset** | Reset the **emulated PB-1000** on the spot — the same as pressing NumLock on a real keyboard. The Pico itself does not restart. |
| **NEW ALL (clear memory)** | Run the PB-1000's NEW ALL function, which erases all user memory |
| **Reboot Emulator (MCU)** | Hardware-reboot **the Pico itself** via `machine.reset()` — see the warning below |

> [!NOTE]
> **NEW ALL** doesn't simulate a real Win+F12 keypress through the key matrix. After
> confirmation, it jumps the CPU's PC directly to the ROM's own NEW ALL handler
> (`&H8D38`) instead. Before 2026-08-22 this queued a simulated key press, but that was
> sensitive to KEY_INT timing and the main loop resuming in time, so it was switched to
> this more direct and reliable approach.

> [!WARNING]
> **NEW ALL erases all of the user's memory** (saved programs and data). A confirmation screen
> (EXE: yes / BRK: no) is shown first — be careful not to trigger it by accident. This action
> cannot be undone.

> [!WARNING]
> **Reboot Emulator (MCU) is not the same as Reset — it actually reboots the Pico itself.** A
> confirmation screen is shown first, but **any progress not already written out via RAM Save
> will be lost** (the entire boot sequence re-runs from the profile picker onward). Use **RAM
> Save** beforehand if you need to keep your progress. This exists as a way to recover when the
> emulator has frozen or hung badly enough that nothing else works.

**Reset** runs immediately with no confirmation screen, and the EMULATOR MENU closes
automatically afterward (matching the real hardware's NumLock key). **Reboot Emulator (MCU)**
differs here — it goes through a confirmation screen (EXE: yes / BRK: no) first.

Inside the **Hook Status** / **CPU Status** screens, Up/Down scrolls the list; BREAK returns to
the System sub-menu.

---

## 7. Troubleshooting

- **Lost track of which menu level you're on?** Keep pressing BREAK — it always steps you back:
  sub-menu → top-level menu → menu closed.
- **Accidentally ran NEW ALL or Reboot Emulator (MCU)?** These take effect immediately and can't
  be undone. Get in the habit of using **RAM Save** regularly to keep backups.

---

## Related Documents

- `usage_guide_en.md` — General keyboard operation, serial features, joystick, etc.
- `hardware_guide_en.md` — Wiring and GPIO assignments (including the REPL UART wiring)
