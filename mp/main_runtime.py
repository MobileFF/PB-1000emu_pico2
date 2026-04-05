def service_pio_uart_bridge(system, cpu_core):
    if hasattr(system, "service_pio_uart"):
        system.service_pio_uart()

    if not system.pio_uart or cpu_core is None:
        return

    if not hasattr(system, 'uart_xon'):
        system.uart_xon = True

    if hasattr(cpu_core, 'uart_tx_get'):
        for _ in range(32):
            tx_data = cpu_core.uart_tx_get()
            if tx_data is None:
                break
            if tx_data == 0x13:
                system.uart_xon = False
            elif tx_data == 0x11:
                system.uart_xon = True
            system.pio_uart.write(tx_data)

    system.pio_uart.service_tx()
    system.pio_uart.service_rx()

    if hasattr(cpu_core, 'uart_rx_put') and system.uart_xon:
        for _ in range(8):
            if not system.pio_uart.any():
                break
            data = system.pio_uart.read(1)
            if not data:
                break
            cpu_core.uart_rx_put(data[0])



def step_with_input_service(system, steps, *, chunk=64):
    ran = 0
    while ran < steps:
        if hasattr(system, "service_pio_uart"):
            system.service_pio_uart()

        n = chunk
        remain = steps - ran
        if n > remain:
            n = remain
            executed = system.step(n)
            if executed is None:
                executed = n
            ran += executed
        else:
            system.step(n)
            ran += n
    return ran


def run_cpu_slice(system, *, active_steps, sleep_ms, step_chunk):
    if system.is_sleeping:
        import time
        time.sleep_ms(sleep_ms)
        return 0
    return step_with_input_service(system, active_steps, chunk=step_chunk)


def update_frame_if_due(system, now, frame_time, *, frame_interval_ms):
    import time
    if time.ticks_diff(now, frame_time) >= frame_interval_ms:
        system.update_display(x_offset=16, y_offset=40)
        return now
    return frame_time


def service_timer_ticks(system, tick_step_accum, *, timer_tick_steps):
    while tick_step_accum >= timer_tick_steps:
        system.tick_timer()
        tick_step_accum -= timer_tick_steps
    return tick_step_accum
