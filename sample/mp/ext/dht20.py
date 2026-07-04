"""
DHT20 温湿度センサー 拡張モジュール

接続: I2C1  GP2=SDA  GP3=SCL  (gpio_pin_map.md の PR6 予約ピン)
回路: VDD=3.3V, GND, SDA/SCL に 4.7kΩ プルアップ

I2C 初期化はフック呼び出し時に遅延実行される。
エミュレータ起動後にセンサーを接続しても認識される。
初期化済みの場合はそのまま測定を行う。
エラー時は初期化状態をリセットし、次回呼び出し時に再試行する。

BASIC 使用例:
    CALL &5E10
    IF PEEK(&5F00)<>0 THEN PRINT "Error": END
    T=PEEK(&5F01)*256+PEEK(&5F02)
    IF T>32767 THEN T=T-65536
    H=PEEK(&5F03)*256+PEEK(&5F04)
    PRINT "Temp:";T/10;"C  Hum:";H/10;"%"

Work area layout (CALL &5E10):
    [0x5F00]: 結果コード  0x00=OK / 0xFF=Error
    [0x5F01]: 温度×10 上位バイト (signed int16 big-endian)
    [0x5F02]: 温度×10 下位バイト  例: 0x00EB=235 → 23.5°C
    [0x5F03]: 湿度×10 上位バイト (uint16 big-endian)
    [0x5F04]: 湿度×10 下位バイト  例: 0x025D=605 → 60.5%
"""

import time

CALL_ADDR  = 0x5E10
_I2C_ADDR  = 0x38
_MEAS_CMD  = bytes([0xAC, 0x33, 0x00])
_MEAS_WAIT = 80   # ms

_i2c = None  # 遅延初期化。None の間は未接続扱い。


def register(system):
    """pb1000.py の _ext_load_modules() から呼ばれる。"""
    system.register_call_hook(CALL_ADDR, lambda: _read(system))
    print(f"dht20: hook {CALL_ADDR:#06x} ready (I2C1 GP2/GP3, lazy init)")


def _ensure_i2c():
    """I2C を初期化してセンサー応答を確認する。失敗したら例外を送出。"""
    global _i2c
    import machine as _machine
    i2c = _machine.I2C(1, sda=_machine.Pin(2), scl=_machine.Pin(3), freq=100_000)
    i2c.readfrom(_I2C_ADDR, 1)  # センサー不在なら OSError
    _i2c = i2c
    print("dht20: I2C1 initialized (GP2=SDA GP3=SCL)")


def _read(system):
    """CALL &5E10 ハンドラ: 必要なら初期化してから DHT20 を読み取る。"""
    global _i2c
    w = system._ext_work
    try:
        if _i2c is None:
            _ensure_i2c()

        _i2c.writeto(_I2C_ADDR, _MEAS_CMD)
        time.sleep_ms(_MEAS_WAIT)
        buf = _i2c.readfrom(_I2C_ADDR, 6)

        if buf[0] & 0x80:   # bit7=1: 測定未完了
            print("dht20: measurement not ready")
            w[0] = system.EXT_ERR_GENERAL
            return

        # 湿度 (20-bit raw → ×10 整数, 0–1000)
        raw_hum  = (buf[1] << 12) | (buf[2] << 4) | (buf[3] >> 4)
        hum_x10  = raw_hum * 1000 // (1 << 20)

        # 温度 (20-bit raw → ×10 整数, -50°C オフセット込み, -500–1550)
        raw_temp = ((buf[3] & 0x0F) << 16) | (buf[4] << 8) | buf[5]
        temp_x10 = raw_temp * 2000 // (1 << 20) - 500

        w[0] = system.EXT_OK
        w[1] = (temp_x10 >> 8) & 0xFF
        w[2] =  temp_x10       & 0xFF
        w[3] = (hum_x10  >> 8) & 0xFF
        w[4] =  hum_x10        & 0xFF

    except Exception as e:
        _i2c = None  # 次回呼び出し時に再初期化を試みる
        print(f"dht20: error: {e}")
        w[0] = system.EXT_ERR_GENERAL

def main():
    i2c = machine.I2C(1, sda=machine.Pin(2), scl=machine.Pin(3), freq=100_000)
    while True:
        i2c.writeto(_I2C_ADDR, _MEAS_CMD)
        time.sleep_ms(_MEAS_WAIT)
        buf = i2c.readfrom(_I2C_ADDR, 6)

        if buf[0] & 0x80:   # bit7=1: 測定未完了
            print("測定未完了")
            continue

        # 湿度 (20-bit raw → ×10 整数, 0–1000)
        raw_hum  = (buf[1] << 12) | (buf[2] << 4) | (buf[3] >> 4)
        hum_x10  = raw_hum * 1000 // (1 << 20)

        # 温度 (20-bit raw → ×10 整数, -50°C オフセット込み, -500–1550)
        raw_temp = ((buf[3] & 0x0F) << 16) | (buf[4] << 8) | buf[5]
        temp_x10 = raw_temp * 2000 // (1 << 20) - 500

        print(f"temp {temp_x10/10}")
        print(f"humi {hum_x10/10}")
        

if __name__ == '__main__':
    main()