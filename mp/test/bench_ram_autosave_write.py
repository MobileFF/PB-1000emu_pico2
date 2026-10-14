"""
RAM オートセーブ機構（検討中）の判断材料として、ram0 単体 (8KB) および
ram0+ram1 相当 (40KB) のデータを内部フラッシュと SD カードそれぞれに
書き込んだときの所要時間を計測するベンチマークツール。

main.py や ROM 読み込みを伴わない単体スクリプト。SD カードが未搭載/
未マウントの場合は SD 側の計測を自動的にスキップする。SPI の配線・
設定は test_sd.py と同一 (SPI1, SCK=GP10, MOSI=GP11, MISO=GP12,
SD_CS=GP15)。

計測しているのは書き込み呼び出しの経過時間 (wall-clock) のみ。内部
フラッシュへの消去/書き込み中はハードウェア制約上 XIP 実行ができない
ため両コア停止・割り込み禁止になる一方、SD カードへの書き込みはそう
ではない、という割り込み面の違いまではこのスクリプトでは測れない
(別途の計測手段が必要)。

Usage:
    mpremote connect <port> run mp/test/bench_ram_autosave_write.py
"""
import gc
import os
import time

SIZES = [
    ("ram0のみ (8KB)", 8 * 1024),
    ("ram0+ram1 (40KB)", 40 * 1024),
]
TRIALS = 5

INTERNAL_PATH = "/_bench_tmp.bin"   # 内部フラッシュ (root vfs)
SD_PATH = "/sd/_bench_tmp.bin"


def _make_buf(size):
    """ベンチマーク用データ。全0/全1 (消去状態と一致しうる値) を避け、
    0x00-0xFF の繰り返しパターンにする。"""
    pat = bytes(range(256))
    buf = bytearray(size)
    mv = memoryview(buf)
    reps = size // 256
    rem = size - reps * 256
    for i in range(reps):
        mv[i * 256:(i + 1) * 256] = pat
    if rem:
        mv[reps * 256:] = pat[:rem]
    return buf


def _bench_write(path, buf, trials):
    times_us = []
    for _ in range(trials):
        gc.collect()  # GCの一時停止が測定ノイズになるのを避ける
        t0 = time.ticks_us()
        with open(path, "wb") as f:
            f.write(buf)
        t1 = time.ticks_us()
        times_us.append(time.ticks_diff(t1, t0))
    try:
        os.remove(path)
    except OSError:
        pass
    return times_us


def _report(label, times_us):
    ms = [t / 1000 for t in times_us]
    detail = ", ".join(f"{m:.1f}ms" for m in ms)
    print(f"    {label}: {detail}")
    print(f"      avg {sum(ms) / len(ms):.1f}ms  min {min(ms):.1f}ms  max {max(ms):.1f}ms")


def _try_mount_sd():
    """test_sd.py と同じ配線・SPI設定でSDをマウントする。失敗時はFalseを返す。"""
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
        print(f"  SD not available, skipping SD benchmark ({e})")
        return False


def run():
    print("=== RAM autosave write-latency benchmark ===")
    print(f"Trials per size: {TRIALS}\n")

    sd_ok = _try_mount_sd()
    print()

    for label, size in SIZES:
        print(f"[{label}]")
        buf = _make_buf(size)

        print("  Internal flash (root vfs):")
        _report("flash", _bench_write(INTERNAL_PATH, buf, TRIALS))

        if sd_ok:
            print("  SD card (/sd):")
            _report("sd   ", _bench_write(SD_PATH, buf, TRIALS))
        print()

    if sd_ok:
        try:
            os.umount("/sd")
        except OSError:
            pass

    print("=== Done ===")
    print("Note: wall-clock write time only. Does not measure whether")
    print("hardware interrupts are blocked during the write.")


if __name__ == "__main__":
    run()
