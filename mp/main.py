"""
PB-1000 Emulator - Normal Run Script
"""
import machine
#import machine.freq(150000000)
import sys
import time
from main_boot import (
    configure_c_keyboard,
    configure_usb_keyboard_routing,
    create_console_uart,
    initialize_system,
    initialize_usb_host_and_pio,
    load_default_roms,
)
from main_input import KeyboardInputManager, TouchInputManager
from main_runtime import (
    run_cpu_slice,
    service_pio_uart_bridge,
    service_timer_ticks,
    update_frame_if_due,
)
from main_actions import handle_key_status_and_capture, handle_save_state_request
from main_cleanup import dump_shutdown_state

KEY_HOLD_MS = 120
KEY_RELEASE_HARD_TIMEOUT_MS = 1200
INTER_KEY_GAP_MS = 80
STEP_TIMER_TICK_STEPS = 40000
FRAME_INTERVAL_MS = 33
AUTO_EXE_ON_ENTER = False
ON_INT_PULSE_MS = 30
ACTIVE_STEP_COUNT = 4000
SLEEP_POLL_MS = 10
LOOP_IDLE_MS = 1

# Input Configuration
ENABLE_USB_KBD = True
ENABLE_UART_KBD = False
UART_BAUDRATE = 115200
UART_TX_PIN = 4   # UART1 TX (Console output)
UART_RX_PIN = 5   # UART1 RX (Keyboard input)

# Initialize UART1 for keyboard input + console output
_uart_kbd, _console_uart = create_console_uart(
    machine,
    enable_uart_kbd=ENABLE_UART_KBD,
    baudrate=UART_BAUDRATE,
    tx_pin=UART_TX_PIN,
    rx_pin=UART_RX_PIN,
)


def _create_input_managers():
    keyboard_input = KeyboardInputManager(
        uart_kbd=_uart_kbd,
        enable_uart_kbd=ENABLE_UART_KBD,
        auto_exe_on_enter=AUTO_EXE_ON_ENTER,
        key_hold_ms=KEY_HOLD_MS,
        key_release_hard_timeout_ms=KEY_RELEASE_HARD_TIMEOUT_MS,
        inter_key_gap_ms=INTER_KEY_GAP_MS,
        on_int_pulse_ms=ON_INT_PULSE_MS,
    )
    touch_input = TouchInputManager()
    return keyboard_input, touch_input


def main():
    print("PB-1000 Emulator Starting...")

    system = initialize_system(console_uart=_console_uart)

    keyboard_input, touch_input = _create_input_managers()

    load_default_roms(system)
    initialize_usb_host_and_pio(system, enable_usb_kbd=ENABLE_USB_KBD)
    cpu_core = configure_c_keyboard(system, enable_usb_kbd=ENABLE_USB_KBD)
    configure_usb_keyboard_routing()

    system.power_on()

    print(f"System initialized. PC={system.pc:#06x}")
    print("Interactive Mode: Type in REPL to send keys (ESC for MENU).")

    tick_step_accum = 0
    frame_time = time.ticks_ms()

    try:
        while True:
            service_pio_uart_bridge(system, cpu_core)

            if not system.is_sleeping:
                tick_step_accum += run_cpu_slice(
                    system,
                    active_steps=ACTIVE_STEP_COUNT,
                    sleep_ms=SLEEP_POLL_MS,
                    step_chunk=64,
                )

            now = time.ticks_ms()
            keyboard_input.poll(system)
            touch_input.poll(system)

            handle_key_status_and_capture(system)
            handle_save_state_request(system, enable_usb_kbd=ENABLE_USB_KBD)

            frame_time = update_frame_if_due(
                system,
                now,
                frame_time,
                frame_interval_ms=FRAME_INTERVAL_MS,
            )

            tick_step_accum = service_timer_ticks(
                system,
                tick_step_accum,
                timer_tick_steps=STEP_TIMER_TICK_STEPS,
            )

            time.sleep_ms(LOOP_IDLE_MS)

    except KeyboardInterrupt:
        print("\nEmulator stopped by user.")
    except Exception as e:
        print(f"\n*** MAIN LOOP EXCEPTION: {type(e).__name__}: {e}")
        sys.print_exception(e)
    finally:
        dump_shutdown_state(system)


if __name__ == '__main__':
    main()
