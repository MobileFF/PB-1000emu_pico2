import gc, sys
for name in ("display","system","_spoll"):
    if name in globals():
        del globals()[name]
gc.collect()
print('free', gc.mem_free(), 'alloc', gc.mem_alloc())
