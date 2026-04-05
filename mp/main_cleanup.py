def dump_shutdown_state(system):
    from workarea import print_all_workarea

    print_all_workarea(system)
    print("dump 0x6000-0x7FFF")
    system.dump_mem_range(0x6000, 0x7FFF)
