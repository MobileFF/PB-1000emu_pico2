"""
SPI デバイス拡張モジュール サンプル

SPI1 (SCK=GP10, MOSI=GP11, MISO=GP12) を LCD/SD/タッチと共有し、
CSピン切り替えで独自デバイスを接続する例。

接続例: CS=GP28  (gpio_pin_map.md の空きピンを使用)

  GP28 は本プロジェクト唯一の汎用予備ピン。
  EXT_SD_CS_PIN も GP28 を使うため、MY_CS_PIN と EXT_SD_CS_PIN を
  同時に使う場合はどちらかをジョイスティック未使用ピン
  (GP18–GP21, GP26–GP27) に変更すること。

BASIC 使用例:
    CALL &5E30
    IF PEEK(&5F00)<>0 THEN PRINT "Error": END
    PRINT PEEK(&5F01)

Work area layout (CALL &5E30):
    [0x5F00]: 結果コード  0x00=OK / 0xFF=Error
    [0x5F01]: デバイスから読み取った1バイト

外付けSDカード一覧 (REPL から):
    from ext.spi_sample import list_ext_sd
    list_ext_sd()
"""

import machine
import os

CALL_ADDR      = 0x5E30
MY_CS_PIN      = 28          # 使用するCSピン番号 (GP28: 汎用予備ピン)
MY_BAUD        = 1_000_000   # デバイスのボーレート
LCD_BAUD       = 40_000_000  # LCDが使う40MHz に戻す値

# 外付けSDカードモジュール設定
# エミュレータの SD (CS=GP15) とは別のモジュールを GP28 に接続する前提
# GP28 は boot.py で PULL_UP 設定済み（フローティング誤アサート防止）
EXT_SD_CS_PIN  = 28
EXT_SD_BAUD    = 400_000
EXT_MOUNT_PATH = "/ext_sd"

# SPI1 共有バス定数 (pb1000.py と同じ)
_SPI_ID   = 1
_SCK_PIN  = 10
_MOSI_PIN = 11
_MISO_PIN = 12


def register(system):
    if system.spi is None:
        print("spi_sample: SPI バスが利用できません")
        return
    try:
        cs = machine.Pin(MY_CS_PIN, machine.Pin.OUT, value=1)
        system.register_call_hook(CALL_ADDR, lambda: _callback(system, cs))
        print(f"spi_sample: hook {CALL_ADDR:#06x} ready (CS=GP{MY_CS_PIN})")
    except Exception as e:
        print(f"spi_sample: init failed: {e}")


def list_ext_sd(cs_pin=EXT_SD_CS_PIN, mount_path=EXT_MOUNT_PATH):
    """外付けSDカードモジュールのルートをREPLに一覧表示する。

    SPI1バスを共有し、cs_pinに接続したSDカードをマウントしてファイル一覧を
    printする。エミュレータ非実行中（またはREPL割り込み後）に呼ぶこと。

    使用例:
        from ext.spi_sample import list_ext_sd
        list_ext_sd()              # CS=GP18, マウント先=/ext_sd
        list_ext_sd(cs_pin=19)     # 別ピンを使う場合
    """
    from sdcard import SDCard

    spi = machine.SPI(
        _SPI_ID,
        baudrate=LCD_BAUD,
        sck=machine.Pin(_SCK_PIN),
        mosi=machine.Pin(_MOSI_PIN),
        miso=machine.Pin(_MISO_PIN),
    )
    cs = machine.Pin(cs_pin, machine.Pin.OUT, value=1)

    mounted = False
    try:
        sd = SDCard(spi, cs, baudrate=EXT_SD_BAUD, restore_baudrate=LCD_BAUD)
        vfs = os.VfsFat(sd)
        os.mount(vfs, mount_path)
        mounted = True

        entries = os.listdir(mount_path)
        print(f"--- {mount_path}/ ({len(entries)} items) ---")
        for name in entries:
            try:
                stat = os.stat(mount_path + "/" + name)
                # stat[0] のビット14が立っていればディレクトリ
                is_dir = bool(stat[0] & 0x4000)
                size = stat[6]
                tag = "<DIR>" if is_dir else f"{size:>10} B"
            except OSError:
                tag = "      ?"
            print(f"  {tag}  {name}")
        print("---")

    except Exception as e:
        print(f"list_ext_sd: error: {e}")
    finally:
        if mounted:
            try:
                os.umount(mount_path)
            except Exception:
                pass
        cs.value(1)
        spi.init(baudrate=LCD_BAUD)


def _callback(system, cs):
    """CALL &5E30 ハンドラ: SPI デバイスから1バイト読み取る"""
    w = system._ext_work
    try:
        spi = system.spi
        spi.init(baudrate=MY_BAUD)
        cs.value(0)
        spi.write(b'\x00')          # デバイスに応じたコマンドに変更
        buf = bytearray(1)
        spi.readinto(buf, 0xFF)
        cs.value(1)
        spi.init(baudrate=LCD_BAUD) # LCD 用ボーレートに戻す

        w[0] = system.EXT_OK
        w[1] = buf[0]

    except Exception as e:
        print(f"spi_sample: error: {e}")
        try:
            cs.value(1)
            system.spi.init(baudrate=LCD_BAUD)
        except Exception:
            pass
        w[0] = system.EXT_ERR_GENERAL
