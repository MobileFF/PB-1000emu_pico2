# To use this module, add the following to your MicroPython build command:
# USER_C_MODULES=../../../PB-1000_emu_AG2/src/micropython.cmake

add_compile_options(-Wno-error)
add_compile_definitions(CFG_TUH_HID_EP_BUFSIZE=64)

# globally disable pico_malloc panic so that C heap exhaustion doesn't
# terminate the program; USB host module will handle NULL returns gracefully
add_definitions(-DPICO_MALLOC_PANIC=0)
add_compile_definitions(MICROPY_HW_USB_CDC=0)
add_compile_definitions(MICROPY_HW_USB_MSC=0)

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
add_library(usb_host_core_lib STATIC
    ${CMAKE_CURRENT_LIST_DIR}/usb_host_core.c
    ${PICO_SDK_PATH}/lib/tinyusb/src/host/usbh.c
    ${PICO_SDK_PATH}/lib/tinyusb/src/host/hub.c
    ${PICO_SDK_PATH}/lib/tinyusb/src/class/hid/hid_host.c
    ${PICO_SDK_PATH}/lib/tinyusb/src/portable/raspberrypi/rp2040/hcd_rp2040.c
)

# Pico-PIO-USB removed for Native Host mode

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
    ${CMAKE_SOURCE_DIR}/boards/RPI_PICO2  # board-specific config header
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
