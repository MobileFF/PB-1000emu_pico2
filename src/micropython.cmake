# To use this module, add the following to your MicroPython build command:
# USER_C_MODULES=../../../PB-1000_emu_AG2/src/micropython.cmake

# ------------------------------------------------------------------------
# Disable Bluetooth (BTstack + CYW43 BT integration).
#
# The RPI_PICO2_W board definition (boards/RPI_PICO2_W/mpconfigboard.cmake)
# enables MICROPY_PY_BLUETOOTH / MICROPY_BLUETOOTH_BTSTACK /
# MICROPY_PY_BLUETOOTH_CYW43 by default, but this project never uses
# Bluetooth (no `import bluetooth` anywhere in mp/). That still costs real
# RAM at runtime: BTstack's HCI/L2CAP buffers and run-loop state, plus the
# CYW43 driver's Bluetooth code paths, plus the combined WiFi+BT firmware
# blob (lib/cyw43-driver/firmware/wb43439A0_..._combined.h, ~44KB larger
# than the WiFi-only blob) versus the WiFi-only firmware selected once BT
# is off. WiFi/NTP (MICROPY_PY_NETWORK_CYW43 / MICROPY_PY_LWIP, used by
# mp/ntp_sync.py) is left untouched.
#
# This must be set here (via USER_C_MODULES, included from
# ports/rp2/CMakeLists.txt before the MICROPY_PY_BLUETOOTH checks are
# evaluated) rather than by editing the shared board file, so the override
# stays local to this project's build.
set(MICROPY_PY_BLUETOOTH OFF)
set(MICROPY_BLUETOOTH_BTSTACK OFF)
set(MICROPY_PY_BLUETOOTH_CYW43 OFF)
# ------------------------------------------------------------------------

add_compile_options(-Wno-error -Wno-error=implicit-function-declaration)
add_compile_definitions(CFG_TUH_HID_EP_BUFSIZE=64)

# globally disable pico_malloc panic so that C heap exhaustion doesn't
# terminate the program; USB host module will handle NULL returns gracefully
add_definitions(-DPICO_MALLOC_PANIC=0)

# Declared here too (also declared again, identically, further below next
# to usb_host_core_lib) so it's available for the device-mode block right
# below -- CMake's option() is idempotent, only setting the default once.
# Default ON: this is the project's default build as of 2026-10 (PIO-USB
# keyboard host + native USB-C CDC) -- see doc/hardware_guide.md. Pass
# -DUSE_PIO_USB=OFF to build the previous native-USB-host-only
# configuration (no USB-C CDC; use the UART0 REPL instead) if needed.
option(USE_PIO_USB "Use PIO-USB (GP4/GP5) instead of the native USB host controller" ON)

# USB host: CFG_TUH_ENABLED=1 must be global so MicroPython's build pulls
# in TinyUSB host utility symbols (tu_edpt_*, tusb_time_delay_ms_api)
# needed by usbh.c.
add_compile_definitions(CFG_TUH_ENABLED=1)
add_compile_definitions(CFG_TUSB_RHPORT1_MODE=0x0101)

if(USE_PIO_USB)
    # The USB keyboard host moved to PIO-USB (GP4/GP5, rhport1, see
    # USE_PIO_USB below) -- rhport0's native controller is free, so the
    # native USB-C port can run in device mode (CDC REPL) instead of
    # being left idle. CFG_TUSB_RHPORT0_MODE must be explicit: TinyUSB's
    # own tusb_option.h only defaults it to OPT_MODE_DEVICE when NEITHER
    # RHPORT0_MODE nor RHPORT1_MODE is defined, and this file already
    # defines RHPORT1_MODE above, so without this rhport0 would get no
    # mode at all and CDC would silently never come up (confirmed on
    # real hardware: flashed fine, enumerated as PIO-USB host correctly,
    # but USB-C CDC never appeared, not even at the OS USB-device level).
    add_compile_definitions(CFG_TUSB_RHPORT0_MODE=OPT_MODE_DEVICE)
else()
    # Native-host build (default): USB-C port is the keyboard host itself
    # (rhport0), so device mode must stay off.
    add_compile_definitions(CFG_TUD_ENABLED=0)
    add_compile_definitions(MICROPY_HW_ENABLE_USBDEV=0)
    add_compile_definitions(MICROPY_HW_USB_CDC=0)
endif()
add_compile_definitions(MICROPY_HW_USB_MSC=0)
add_compile_definitions(MICROPY_HW_USB_HID=0)

add_compile_definitions(MICROPY_PY_PIO_USB=1)

# Enable UART REPL on GP0/GP1 (UART0) so mpremote can connect
add_compile_definitions(MICROPY_HW_ENABLE_UART_REPL=1)
add_compile_definitions(PICO_DEFAULT_UART=0)
add_compile_definitions(PICO_DEFAULT_UART_TX_PIN=0)
add_compile_definitions(PICO_DEFAULT_UART_RX_PIN=1)

# ============================================================
# 1) HD61700 + MicroPython wrapper modules (INTERFACE library)
#    These are compiled as part of the main firmware and can
#    freely include MicroPython headers.
# ============================================================
add_library(hd61700_lib INTERFACE)

target_sources(hd61700_lib INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/hd61700.c
    ${CMAKE_CURRENT_LIST_DIR}/modhd61700.c
    ${CMAKE_CURRENT_LIST_DIR}/lcd_controller.c
    ${CMAKE_CURRENT_LIST_DIR}/modlcd_controller.c
    ${CMAKE_CURRENT_LIST_DIR}/modusb_host.c
    ${CMAKE_CURRENT_LIST_DIR}/moddotds64.c
)

target_include_directories(hd61700_lib INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
)

# debugging aid: normally we want the real initialization, but
# for earlier testing we added a compile-time define.  make it
# optional so the default build actually executes tuh_init().

option(USB_HOST_SKIP_INIT "Skip USB host core init for debugging" OFF)
if(USB_HOST_SKIP_INIT)
  target_compile_definitions(hd61700_lib INTERFACE DEBUG_SKIP_CORE_INIT)
endif()
# to enable skipping, append "-DUSB_HOST_SKIP_INIT=ON" to the cmake
# invocation or set the option in your build script.

# ============================================================
# 2) USB Host core (STATIC library, isolated from MicroPython)
#    This library uses our own tusb_config.h (host-only) and
#    must NOT leak its compile definitions into the firmware.
#    Using a STATIC (not INTERFACE) library ensures PRIVATE
#    settings stay private.
# ============================================================

# USE_PIO_USB: build the USB keyboard host on PIO-USB (GP4/GP5) instead
# of the native RP2350 USB controller (hcd_rp2040.c). Default ON (see the
# option() call near the top of this file) -- this frees the native
# USB-C port to run as a CDC device (mpremote) instead of sitting idle.
# Pass -DUSE_PIO_USB=OFF to build the previous native-USB-host-only
# configuration instead. See src/usb_host/pio_usb/PROVENANCE.md and
# src/usb_host/hcd_pio_usb_pb1000.c for the full rationale; this is a
# from-scratch re-implementation (ported from a sibling RP2350
# MicroPython project's own validated code) of what an earlier, abandoned
# attempt at this ("Pico-PIO-USB removed for Native Host mode", below)
# never finished — src/usb_host/hcd_pio_usb_custom.c is that old attempt,
# kept only as historical background, not built by either setting.
option(USE_PIO_USB "Use PIO-USB (GP4/GP5) instead of the native USB host controller" ON)

set(_usb_host_core_lib_sources
    ${CMAKE_CURRENT_LIST_DIR}/usb_host_core.c
    ${PICO_SDK_PATH}/lib/tinyusb/src/host/usbh.c
    ${PICO_SDK_PATH}/lib/tinyusb/src/host/hub.c
    ${PICO_SDK_PATH}/lib/tinyusb/src/class/hid/hid_host.c
)
if(USE_PIO_USB)
    list(APPEND _usb_host_core_lib_sources
        ${CMAKE_CURRENT_LIST_DIR}/usb_host/hcd_pio_usb_pb1000.c
        ${CMAKE_CURRENT_LIST_DIR}/usb_host/pio_usb/pio_usb.c
        ${CMAKE_CURRENT_LIST_DIR}/usb_host/pio_usb/pio_usb_host.c
        ${CMAKE_CURRENT_LIST_DIR}/usb_host/pio_usb/usb_crc.c
        # pio_usb_device.c (upstream's DEVICE-mode implementation) is not
        # even vendored here — this project only ever uses pio_usb as a
        # HOST (keyboard). See PROVENANCE.md.
    )
else()
    list(APPEND _usb_host_core_lib_sources
        ${PICO_SDK_PATH}/lib/tinyusb/src/portable/raspberrypi/rp2040/hcd_rp2040.c
    )
endif()

add_library(usb_host_core_lib STATIC ${_usb_host_core_lib_sources})

if(USE_PIO_USB)
    # usb_tx.pio.h/usb_rx.pio.h are upstream's own pre-generated (pioasm)
    # headers, vendored as plain static files rather than regenerated via
    # pico_generate_pio_header() at build time -- MicroPython's separate
    # QSTR-extraction pre-pass runs a raw preprocessor invocation over
    # every USER_C_MODULES source file before that custom command's
    # dependency graph guarantees the header would exist yet. See
    # src/usb_host/pio_usb/PROVENANCE.md.
    target_include_directories(usb_host_core_lib PRIVATE
        ${CMAKE_CURRENT_LIST_DIR}/usb_host/pio_usb
    )
    target_compile_definitions(usb_host_core_lib PRIVATE
        CFG_TUH_RPI_PIO_USB=1
        PB1000_USE_PIO_USB=1
    )
    # NOTE: raising CFG_TUSB_DEBUG/CFG_TUH_LOG_LEVEL here was tried for
    # bring-up tracing and reverted -- usbh.c's debug paths call
    # tu_print_mem(), which only exists in TinyUSB's own src/tusb.c (not
    # built here; this project calls tuh_init() directly instead of the
    # generic tusb_init()), so enabling them is a link error
    # ("undefined reference to `tu_print_mem'") rather than something
    # fixable by just adding compile definitions. Use
    # usb_host_core_debug_pio_usb() (usb_host.debug() in Python) and/or
    # prints added directly in hcd_pio_usb_pb1000.c's event handler
    # instead.
    target_link_libraries(usb_host_core_lib PRIVATE
        hardware_pio
        hardware_dma
        hardware_timer
    )
endif()

# PRIVATE link/include: these do NOT propagate to the main firmware
target_link_libraries(usb_host_core_lib PRIVATE
    pico_stdlib
    pico_rand
)

target_include_directories(usb_host_core_lib PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/usb_host
    ${PICO_SDK_PATH}/lib/tinyusb/src
    ${PICO_SDK_PATH}/lib/tinyusb/src/common
    ${PICO_SDK_PATH}/lib/tinyusb/hw
    # PICOSDK hardware headers needed by mpconfigport.h
    ${PICO_SDK_PATH}/src/rp2_common/hardware_flash/include
    ${PICO_SDK_PATH}/src/rp2_common/hardware_base/include
    ${PICO_SDK_PATH}/src/rp2_common/hardware/include
    ${PICO_SDK_PATH}/src/rp2_common/hardware_pio/include
    ${PICO_SDK_PATH}/src/rp2_common/hardware_dma/include
    ${PICO_SDK_PATH}/src/rp2_common/hardware_spi/include
    # Allow usb_host_core.c to use MicroPython headers (mp_printf etc.)
    # CMAKE_SOURCE_DIR is ports/rp2/ in the MicroPython build, so ../../ = micropython root
    ${CMAKE_SOURCE_DIR}/../..
    # explicit path from this module into the micropython repo
    # (hd61700/src/../../micropython/ports/rp2/boards/<board>)
    #${CMAKE_CURRENT_LIST_DIR}/../../pico/micropython/ports/rp2/boards/${BOARD}
    #${CMAKE_SOURCE_DIR}/boards/${BOARD}  # board-specific config header
    ${CMAKE_SOURCE_DIR}/boards/${MICROPY_BOARD}  # board-specific config header
    ${CMAKE_SOURCE_DIR}/ports/rp2      # also include rp2 tree just in case
    ${CMAKE_SOURCE_DIR}                # micropython root
    ${CMAKE_SOURCE_DIR}/boards/${BOARD}  # board-specific config header
    ${CMAKE_SOURCE_DIR}/boards/${PICO_BOARD}  # board-specific config header
    ${CMAKE_BINARY_DIR}
)

# make sure the TinyUSB sources are compiled with our malloc override
# header so they use the C library allocator instead of MicroPython's.
target_compile_options(usb_host_core_lib PRIVATE
    "-include${CMAKE_CURRENT_LIST_DIR}/usb_host/malloc_override.h"
    "-include${CMAKE_CURRENT_LIST_DIR}/usb_host/tusb_config.h"
)

# Additionally, redefine the allocation functions when compiling the host
# library so that TinyUSB calls go through our custom tu_* wrappers.  These
# wrappers allocate from the Pico SDK heap via pico_malloc, keeping the
# MicroPython GC heap pristine.
target_compile_definitions(usb_host_core_lib PRIVATE
    malloc=tu_malloc
    free=tu_free
    calloc=tu_calloc
    realloc=tu_realloc
    PICO_MALLOC_PANIC=0
    CFG_TUH_HID_EP_BUFSIZE=64
    CFG_TUH_HID=4
    CFG_TUH_HID_EPIN_BUFSIZE=64
    CFG_TUH_HID_EPOUT_BUFSIZE=64
)

# The USB initialization code is mostly confined to usb_host_core_lib
# which already has its own malloc overrides.  Adding the same macros to
# broad, widely-used interface libraries like pico_stdlib causes them to be
# visible during unrelated builds (e.g. C++ runtime setup) and breaks
# compilation.  Therefore we limit the overrides to usb_host_core_lib only.

# If pico_pio_usb ever allocates via malloc outside of usb_host_core_lib, then
# consider adding overrides specifically to that target's sources instead of
# globally.

# ============================================================
# 3) Link everything into the usermod
# ============================================================
target_link_libraries(usermod INTERFACE
    hd61700_lib
    usb_host_core_lib
)

# ------------------------------------------------------------------------
# Restore the pico/cyw43_driver.h include path lost by disabling Bluetooth
# above.
#
# In an unmodified (Bluetooth-enabled) build, ports/rp2/CMakeLists.txt only
# ever links the WiFi-only target `cyw43_driver_picow` (which compiles
# cyw43_bus_pio_spi.c, needed purely for WiFi) directly into the firmware.
# That target's own INTERFACE include dirs do NOT contain
# pico_cyw43_driver/include/ (where pico/cyw43_driver.h lives) — the SDK
# only adds that path via `pico_btstack_hci_transport_cyw43`, a Bluetooth
# target that happens to share the same include directory. With
# MICROPY_BLUETOOTH_BTSTACK off, that target is never linked, so
# cyw43_bus_pio_spi.c fails with "pico/cyw43_driver.h: No such file or
# directory" even though the missing header has nothing to do with
# Bluetooth.
#
# Link only `pico_cyw43_driver_headers` (the SDK's convention for a
# headers-only INTERFACE target: include dirs/compile defs, no sources) —
# NOT the full `pico_cyw43_driver` target, which also carries
# cyw43_driver.c (an async_context integration layer MicroPython's own
# WiFi glue doesn't use and was never compiled in the original Bluetooth-
# enabled build either). Pulling in the full target instead of just its
# headers would newly require pico/async_context.h and an async_context
# implementation neither this project nor stock MicroPython links here.
if (MICROPY_PY_NETWORK_CYW43 AND NOT MICROPY_BLUETOOTH_BTSTACK)
    target_link_libraries(hd61700_lib INTERFACE pico_cyw43_driver_headers)
endif()
