"""
/fkbar.raw（内部フラッシュ）と /sd/fkbar.raw（SDカードルート）の内容を
バイト単位で比較する調査用スクリプト。funckey_bar.py経由での表示が
SD読み込み時だけ崩れる、という報告の切り分け用 -- まずファイル内容
そのものが一致しているかを確認する（調査用/参照: funckey_bar.pyの
_blit_raw()周りのSPI共有処理は静的解析では問題が見つからなかったため、
ファイル破損の可能性を先に潰す）。

512バイトずつ両方読み込んで比較するだけで、どちらのファイルも一度に
全部メモリへ載せない（funckey_bar.py自身のチャンクサイズに合わせている）。

main.pyを経由しない単体スクリプトなので、/sdは自動マウントされていない
(SDマウントはmain_entry.pyのinit_display_only()経由でしか行われない) --
bench_ram_autosave_write.py/test_sd.pyと同じ配線・SPI設定で、このスクリプト
自身がマウントする。

Usage:
    mpremote connect <port> run mp/test/test_fkbar_raw_diff.py
"""
import os

FLASH_PATH = "/fkbar.raw"
SD_PATH = "/sd/fkbar.raw"
CHUNK = 512


def _try_mount_sd():
    """bench_ram_autosave_write.py/test_sd.pyと同じ配線・SPI設定でSDを
    マウントする。失敗時はFalseを返す。"""
    import machine
    from sdcard import SDCard

    SCK_PIN, MOSI_PIN, MISO_PIN = 10, 11, 12
    SD_CS_PIN, LCD_CS_PIN, T_CS_PIN = 15, 9, 16
    SPI_BAUDRATE = 40_000_000
    SD_INIT_BAUDRATE = 400_000

    try:
        spi = machine.SPI(
            1, baudrate=SPI_BAUDRATE,
            sck=machine.Pin(SCK_PIN), mosi=machine.Pin(MOSI_PIN), miso=machine.Pin(MISO_PIN),
        )
        # 他のSPIデバイス(LCD/タッチ)のCSをHIGHに保ち、バス競合を避ける
        machine.Pin(LCD_CS_PIN, machine.Pin.OUT, value=1)
        machine.Pin(T_CS_PIN, machine.Pin.OUT, value=1)
        sd_cs = machine.Pin(SD_CS_PIN, machine.Pin.OUT, value=1)
        sd = SDCard(spi, sd_cs, baudrate=SD_INIT_BAUDRATE, restore_baudrate=SPI_BAUDRATE)
        if "sd" not in os.listdir("/"):
            os.mkdir("/sd")
        os.mount(os.VfsFat(sd), "/sd")
        return True
    except Exception as e:
        print(f"  SD mount failed: {e}")
        return False


def _size(path):
    try:
        return os.stat(path)[6]
    except OSError as e:
        print(f"  stat failed: {path}: {e}")
        return None


def main():
    print("=" * 60)
    print("fkbar.raw diff: %s vs %s" % (FLASH_PATH, SD_PATH))
    print("=" * 60)

    if "sd" not in os.listdir("/"):
        print("/sd not mounted yet -- mounting...")
    if not _try_mount_sd():
        # Already-mounted (e.g. a previous run in the same REPL session
        # left it mounted) raises too -- os.listdir("/sd") is a cheap way
        # to tell "already mounted and fine" apart from "truly failed".
        try:
            os.listdir("/sd")
            print("  (/sd was already mounted -- continuing)")
        except OSError:
            print("RESULT: could not mount SD card -- cannot compare.")
            return

    size_flash = _size(FLASH_PATH)
    size_sd = _size(SD_PATH)
    print(f"flash size: {size_flash}")
    print(f"sd    size: {size_sd}")

    if size_flash is None or size_sd is None:
        print("RESULT: one or both files missing -- cannot compare.")
        return

    if size_flash != size_sd:
        print(f"RESULT: SIZE MISMATCH ({size_flash} vs {size_sd} bytes) "
              "-- files are not the same, stop here.")
        return

    buf_a = bytearray(CHUNK)
    buf_b = bytearray(CHUNK)
    total = size_flash
    offset = 0
    first_diff_offset = None
    diff_byte_count = 0
    diff_chunk_count = 0

    try:
        with open(FLASH_PATH, "rb") as fa, open(SD_PATH, "rb") as fb:
            while offset < total:
                na = fa.readinto(buf_a)
                nb = fb.readinto(buf_b)
                if na != nb:
                    print(f"RESULT: short-read mismatch at offset {offset} "
                          f"(flash read {na}B, sd read {nb}B)")
                    return
                if na == 0:
                    break
                chunk_diffs = 0
                for i in range(na):
                    if buf_a[i] != buf_b[i]:
                        chunk_diffs += 1
                        if first_diff_offset is None:
                            first_diff_offset = offset + i
                            print(f"  first difference at byte offset "
                                  f"{first_diff_offset}: "
                                  f"flash=0x{buf_a[i]:02X} sd=0x{buf_b[i]:02X}")
                if chunk_diffs:
                    diff_chunk_count += 1
                    diff_byte_count += chunk_diffs
                offset += na
    except OSError as e:
        print(f"RESULT: read error during comparison: {e}")
        return

    print("-" * 60)
    if diff_byte_count == 0:
        print(f"RESULT: IDENTICAL ({total} bytes compared, 0 differences).")
    else:
        print(f"RESULT: DIFFERENT -- {diff_byte_count} byte(s) differ across "
              f"{diff_chunk_count} of {(total + CHUNK - 1) // CHUNK} "
              f"{CHUNK}-byte chunk(s), out of {total} bytes total.")
        print(f"        first difference at offset {first_diff_offset}.")


if __name__ == "__main__":
    main()
