def dump_shutdown_state(system):
    print("dump 0x6000-0x6FFF")
    system.dump_mem_range(0x6000, 0x6FFF)
