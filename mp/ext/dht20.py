"""
DHT20 温湿度センサー 拡張モジュール

接続: I2C1  GP2=SDA  GP3=SCL  (gpio_pin_map.md の PR6 予約ピン)
回路: VDD=3.3V, GND, SDA/SCL に 4.7kΩ プルアップ

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

import machine
import time

CALL_ADDR  = 0x5E10
_I2C_ADDR  = 0x38
_MEAS_CMD  = bytes([0xAC, 0x33, 0x00])
_MEAS_WAIT = 80   # ms


def register(system):
    """pb1000.py の _ext_load_modules() から呼ばれる。"""
    try:
        i2c = machine.I2C(1, sda=machine.Pin(2), scl=machine.Pin(3), freq=100_000)
        _check_sensor(i2c)
        system.register_call_hook(CALL_ADDR, lambda: _read(system, i2c))
        print(f"dht20: hook {CALL_ADDR:#06x} ready (I2C1 GP2/GP3)")
    except Exception as e:
        print(f"dht20: init failed: {e}")


def _check_sensor(i2c):
    """起動時にセンサーの存在を確認する。応答がなければ例外を送出。"""
    i2c.readfrom(_I2C_ADDR, 1)


def _read(system, i2c):
    """CALL &5E10 ハンドラ: DHT20 から温湿度を読み取りワークエリアに書く。"""
    print("CALL &5E10 ハンドラ: DHT20 から温湿度を読み取りワークエリアに書く。")
    w = system._ext_work
    try:
        i2c.writeto(_I2C_ADDR, _MEAS_CMD)
        time.sleep_ms(_MEAS_WAIT)
        buf = i2c.readfrom(_I2C_ADDR, 6)

        if buf[0] & 0x80:   # bit7=1: 測定未完了
            print("測定未完了")
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
        print(f"dht20: read error: {e}")
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