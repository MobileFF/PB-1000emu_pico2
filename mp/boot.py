import machine

# GP28: 外部SPI CS ピン。ファームウェア起動前にフローティングで誤アサートしないよう
# PULL_UP を設定してHIGH（非選択）状態を保つ。
machine.Pin(28, machine.Pin.IN, machine.Pin.PULL_UP)

# UART REPL は firmware の MICROPY_HW_ENABLE_UART_REPL=1 で GP0/GP1 に設定済み。
# machine.UART(0, ...) を呼ぶと UART0 ハードウェアがリセットされて C レベルの
# 受信 IRQ が消え、かつ os.dupterm との二重書き込みで mpremote が失敗するため
# ここでは呼ばない。
