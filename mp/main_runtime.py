import time


def service_pio_uart_bridge(system, cpu_core):
    # Resolve hasattr checks once on first call; cache as tuple on system object.
    if not hasattr(system, '_ubr_cache'):
        system.uart_xon = True
        system._ubr_cache = (
            getattr(system, 'service_pio_uart', None),
            getattr(cpu_core, 'uart_tx_get', None),
            getattr(cpu_core, 'uart_signal_rx', None),
            getattr(cpu_core, 'uart_clear_rx_signal', None),
        )
    _svc, _tx_get, _signal_rx, _clear_rx = system._ubr_cache

    if _svc:
        _svc()

    pio = system.pio_uart
    if not pio or cpu_core is None:
        return

    # pio.write/service_tx/service_rx/any are bound-method accesses --
    # allocate a fresh bound-method object every time otherwise, and this
    # function runs once per main-loop iteration. pio_uart is a single
    # persistent object for the session (main.py assigns it once before the
    # loop starts; reset_emulator() clears its buffers, doesn't replace the
    # object), so caching by identity is safe -- the id() check re-caches
    # automatically in the unlikely event it's ever swapped. Was part of the
    # small residual "pio" bucket in mem_overlay.py logs after
    # clock/memovl/frame/input were fixed -- see
    # [[project_mem_churn_investigation]].
    _pc = getattr(system, '_pio_cache', None)
    if _pc is None or _pc[0] != id(pio):
        _pc = (id(pio), pio.write, pio.service_tx, pio.service_rx, pio.any)
        system._pio_cache = _pc
    _, _pio_write, _svc_tx, _svc_rx, _pio_any = _pc

    if _tx_get:
        for _ in range(32):
            tx_data = _tx_get()
            if tx_data is None:
                break
            if tx_data == 0x13:
                system.uart_xon = False
            elif tx_data == 0x11:
                system.uart_xon = True
            _pio_write(tx_data)

    _svc_tx()
    _svc_rx()

    # Bytes remain in the Python PIO buffer (_rx_buffer) for the MMIO
    # callback at 0x0C02 (IO read path) to serve. Keep INT1 level-triggered:
    # assert while data is available, deassert when buffer is empty.
    # Deasserting on every empty tick prevents stale INT1 between transfers
    # (e.g. after flush_rx() on BREAK without going through the MMIO read path).
    if _signal_rx:
        if system.uart_xon and _pio_any():
            _signal_rx()
        elif _clear_rx and not _pio_any():
            _clear_rx()


def step_with_input_service(system, steps, *, chunk=64, extra_svc=None):
    # Cache both bound methods on system, same pattern as
    # service_pio_uart_bridge()'s _ubr_cache -- this function runs once per
    # main-loop iteration (via run_cpu_slice()), and a plain `system.step`/
    # `getattr(system, 'service_pio_uart', ...)` attribute access allocates
    # a fresh bound-method object every single call otherwise. Was part of
    # the small residual "cpu" bucket in mem_overlay.py logs after
    # clock/memovl/frame/input were fixed -- see
    # [[project_mem_churn_investigation]].
    if not hasattr(system, '_sws_cache'):
        system._sws_cache = (
            getattr(system, 'service_pio_uart', None),
            system.step,
        )
    _svc, _step = system._sws_cache
    ran = 0

    while ran < steps:
        if _svc:
            _svc()
        if extra_svc:
            extra_svc()
        n = chunk
        remain = steps - ran
        if n > remain:
            n = remain
            executed = _step(n)
            if executed is None:
                executed = n
            ran += executed
        else:
            _step(n)
            ran += n
    return ran


def run_cpu_slice(system, *, active_steps, sleep_ms, step_chunk, extra_svc=None):
    if system.is_sleeping:
        # Even during sleep, call step_with_input_service so that
        # c_kb_service_input_lines() fires KEY_INT pulses and can clear
        # CPU_SLP (hd61700_set_input clears it when KEY_INT is asserted
        # with FLAG_SW set).  hd61700_execute gracefully burns cycles
        # when CPU_SLP is set, so this is safe.
        step_with_input_service(system, step_chunk, chunk=step_chunk, extra_svc=extra_svc)
        time.sleep_ms(sleep_ms)
        return 0
    return step_with_input_service(system, active_steps, chunk=step_chunk, extra_svc=extra_svc)


def update_frame_if_due(system, now, frame_time, *, frame_interval_ms):
    if time.ticks_diff(now, frame_time) >= frame_interval_ms:
        # Avoid rendering during active SPI transfers (SD Card)
        if system._fdd_active:
            return frame_time
        system.update_display()
        return now
    return frame_time


def service_timer_ticks(system, tick_step_accum, *, timer_tick_steps):
    while tick_step_accum >= timer_tick_steps:
        system.tick_timer()
        tick_step_accum -= timer_tick_steps
    return tick_step_accum


def service_timer_realtime(system, last_tick_ms, *, ms_per_tick):
    """Fire timer ticks based on real elapsed time, independent of CPU sleep state.

    The HD61700 hardware timer runs continuously regardless of whether the CPU
    is sleeping (SLP state). This function replicates that behavior by using
    wall-clock time rather than CPU step counts, so TIME$ advances correctly
    even while the BASIC prompt is waiting for input.
    """
    import hd61700 as _hd
    now = time.ticks_ms()
    elapsed = time.ticks_diff(now, last_tick_ms)
    if elapsed < ms_per_tick:
        return last_tick_ms
    ticks = elapsed // ms_per_tick
    # Cap burst to avoid cascading interrupts after a long pause
    if ticks > 60:
        ticks = 60
    tm_before = _hd.get_reg8(6)
    reg0_before = _hd.get_reg(0)
    for _ in range(ticks):
        system.tick_timer()
    tm_after = _hd.get_reg8(6)
    reg0_after = _hd.get_reg(0)
    #print(f"[TIMER] TM {tm_before}->{tm_after}  reg[0] {reg0_before}->{reg0_after}  elapsed={elapsed}ms")
    return time.ticks_add(last_tick_ms, ticks * ms_per_tick)
