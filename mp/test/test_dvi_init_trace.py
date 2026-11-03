# Run with: mpremote run test/test_dvi_init_trace.py  (right after a reset,
# without main.py). Prints before/after dvi_display.init() and then the DVI
# counters once a second, to see whether init returns and core1 keeps up.
import time
import dvi_display

print("before init")
ok = dvi_display.init()
print("after init:", ok)
for _ in range(5):
    hb, late = dvi_display.debug_info()
    resync, pump_max = dvi_display.timing_info()
    print("hb", hb, "late", late, "resync", resync, "pump_max_us", pump_max,
          "fdebug", hex(dvi_display.pio_debug_info()))
    time.sleep(1)
