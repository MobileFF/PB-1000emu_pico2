# Boot script for PB-1000 Emulator
# This runs automatically on power-up

import gc
import machine
import time

# Disable debug output for production
# machine.freq(133000000)  # Set CPU frequency if needed

# Print banner
print("=" * 40)
print("PB-1000 Emulator for Raspberry Pi Pico 2")
print("=" * 40)

# Free up memory
gc.collect()
print(f"Free memory: {gc.mem_free()} bytes")

# Wait a moment for serial to stabilize
time.sleep_ms(100)

# Import and run main
try:
    import main
    main.main()
except Exception as e:
    print(f"Error starting emulator: {e}")
    import sys
    sys.print_exception(e)
