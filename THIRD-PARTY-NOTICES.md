# Third-Party Notices

This project is licensed under the GNU General Public License v3.0 (see
[LICENSE](LICENSE)). It also incorporates or builds on the following
third-party components, each of which remains under its own license as
described below.

---

## HD61700 CPU core (`src/hd61700.c`, `src/hd61700.h`)

This project's HD61700 CPU emulation core is a C port of the HD61700 CPU
core from the [MAME](https://www.mamedev.org/) project
(`src/devices/cpu/hd61700/hd61700.cpp`), written by Sandro Ronco.
The original file is licensed under the **BSD-3-Clause** license (per its
own header: `// license:BSD-3-Clause` / `// copyright-holders:Sandro Ronco`).
That file's own header additionally credits Piotr Piatek and BLUE for
HD61700 *documentation* (not source code) consulted during MAME's
original implementation:

```
Hitachi HD61700 cpu core emulation.
by Sandro Ronco

This CPU core is based on documentations works done by:
- Piotr Piatek ( http://www.pisi.com.pl/piotr433/pb1000he.htm )
- BLUE ( http://www.geocities.jp/hd61700lab/ )
```

`src/hd61700.c` and `src/hd61700.h` in this repository are a mechanical
C-language port of that BSD-3-Clause file (register-access macros,
decode structure, and instruction semantics carried over; C++ member
access `m_foo` translated to C's `cpu->foo`), adapted for standalone
use with MicroPython on the RP2350. As required by the BSD-3-Clause
license, its full text and copyright notice are reproduced below.

```
Copyright (c) Sandro Ronco.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

Combining BSD-3-Clause (permissive) code into this GPL-3.0 project is
license-compatible; the obligation is solely to preserve the notice
above, which this file satisfies for the whole repository.

---

## Piotr Piatek's PB-1000 emulator (`pb1000es/*.pas`, reference material only)

Piotr Piatek's PB-1000 emulator source (bundled in this repository under
`pb1000es/` purely as historical reference material, and **not** built
or shipped as part of the firmware) carries no license file and no
license statement was found published alongside it. Its copyright
status should therefore be treated as "all rights reserved" by its
author. A comparison of this repository's `src/hd61700.c` against
`pb1000es/*.pas` found no evidence of direct code reuse — the two use
entirely different naming conventions and code structure, and MAME's
own header (which this project's core is a direct port of) states it
was informed by Piotr Piatek's *documentation*, not his source code.
No files in `src/` or `mp/` are derived from `pb1000es/`.

---

## Build-time dependencies (linked into `firmware.uf2`, not vendored in this repository)

The compiled firmware bundles the following components at build time.
Their source is not included in this repository (see
[Build Guide](doc/build_guide.md) for how to obtain them), but their
licenses apply to the resulting `firmware.uf2` binary:

| Component | License | Source |
| --- | --- | --- |
| [MicroPython](https://github.com/micropython/micropython) | MIT License | github.com/micropython/micropython |
| [Raspberry Pi Pico SDK](https://github.com/raspberrypi/pico-sdk) | BSD-3-Clause | github.com/raspberrypi/pico-sdk |
| [TinyUSB](https://github.com/hathach/tinyusb) | MIT License | github.com/hathach/tinyusb |

---

## PB-1000 ROM images

CASIO PB-1000 ROM images are **not** included in or distributed with
this repository. They remain copyrighted by CASIO COMPUTER CO., LTD.
Users must supply their own ROM dump obtained from their own hardware.
