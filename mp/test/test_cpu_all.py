"""HD61700 CPU Test: ALL opcodes 0x00-0xFF
Thonnyからこのファイルを実行すると全テストが一括実行されます。
"""
import gc
import sys

# Ensure test_cpu_common is loaded first (shared singleton)
from test_cpu_common import get_t
get_t()
gc.collect()

files = [
    "test_cpu_00_1f.py",
    "test_cpu_20_3f.py",
    "test_cpu_40_5f.py",
    "test_cpu_60_7f.py",
    "test_cpu_80_9f.py",
    "test_cpu_a0_bf.py",
    "test_cpu_c0_df.py",
    "test_cpu_e0_ff.py",
]

for name in files:
    print("\n" + "#" * 60)
    print("# %s" % name)
    print("#" * 60)
    gc.collect()
    try:
        # exec() runs code without keeping a persistent module
        exec(open(name).read())
    except Exception as e:
        print("!!! FAILED to run %s: %s" % (name, e))
    # Remove any cached test modules to free memory
    for k in list(sys.modules.keys()):
        if k.startswith("test_cpu_") and k != "test_cpu_common":
            del sys.modules[k]
    gc.collect()
    print("  [free mem: %d bytes]" % gc.mem_free())

print("\n" + "=" * 60)
print("ALL TESTS COMPLETE")
print("=" * 60)
