"""
NTP時刻同期の「ズレ」切り分け用診断スクリプト（2026-10-18、調査用）。

main_entry.py が使う ntp_sync.ntp_sync_and_set() の処理を、ズレの発生源ごとに
切り分けられるよう個別の段階に分解して実行する:

  0. 設定値の確認（[wifi]/[ntp] セクション）
  1. 同期前の Pico RTC の値（基準点）
  2. WiFi接続
  3. 設定済みNTPサーバーからの生UTC epoch取得（タイムゾーン適用前）
  4. 別のNTPサーバー（time.google.com 等）からも取得し、設定済みサーバー側の
     応答が疑わしくないかをクロスチェック
  5. タイムゾーン適用後のローカル時刻を計算し、Pico RTCへ設定→直後に読み戻す

ここでは意図的に PB-1000 側 (DATE$/TIME$/REG_TM) への書き込み確認は行わない
-- それには main.py が実機 ROM を起動済みの状態が必要で、このスクリプト単体
では CPU が初期化されていないため (hd61700.read_mem() 等は呼べても、意味の
あるROM/RAM状態が存在しない)。PB-1000側の値を見る・ズレを追跡するには、
main.py 実行中に別のシリアル接続（REPL）から下記を呼ぶ:

    import ntp_sync
    print(ntp_sync.read_pb1000_clock())

これを数分おきに手動で呼んで実時刻と比較すれば、「初回同期時点でズレている
(0.〜5.側の問題)」のか「初回は合っているが徐々にズレる (ROM側タイマーの
進み方の問題)」のかが分かる。

Usage:
    mpremote connect <port> run mp/test/test_ntp_timesync.py
"""
import time
import machine

import config
import ntp_sync


def _hdr(title):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def _fmt_epoch(epoch, label):
    if epoch is None:
        print(f"  {label}: 取得失敗")
        return
    tm = time.gmtime(epoch)
    print(f"  {label}: epoch={epoch}  = {tm[0]:04d}-{tm[1]:02d}-{tm[2]:02d} "
          f"{tm[3]:02d}:{tm[4]:02d}:{tm[5]:02d} UTC")


def main():
    cfg = config.load_config()
    ssid = config.get_str(cfg, "wifi", "ssid")
    password = config.get_str(cfg, "wifi", "password")
    ntp_server = config.get_str(cfg, "ntp", "server") or "pool.ntp.org"
    tz_offset_h = config.get_int(cfg, "ntp", "tz_offset_h")
    timeout_ms = config.get_int(cfg, "ntp", "timeout_ms") or 15000

    _hdr("0. 設定値 (/pb1000.ini, /sd/pb1000.ini のみ -- プロファイル固有の"
         "pb1000.iniは見ていません)")
    print(f"  wifi.ssid    = {ssid!r}")
    print(f"  ntp.server   = {ntp_server!r}")
    print(f"  ntp.tz_offset_h = {tz_offset_h!r}")
    print(f"  ntp.timeout_ms  = {timeout_ms!r}")

    _hdr("1. 同期前の Pico RTC (基準点)")
    try:
        print("  ", machine.RTC().datetime())
    except Exception as e:
        print("  Pico RTC read failed:", e)

    if not ssid:
        print("\n[wifi] ssid が空です。WiFi/NTP関連の確認はここで終了します。")
        return

    if not ntp_sync.is_wifi_supported():
        print("\nこのボードはWiFi非搭載（無印 Pico 2 等）です。終了します。")
        return

    _hdr("2. WiFi接続")
    if not ntp_sync._wifi_connect(ssid, password, timeout_ms):
        print("WiFi接続に失敗しました。ここで終了します。")
        return

    try:
        _hdr("3. 設定済みNTPサーバーからの生UTC取得")
        t0 = time.ticks_ms()
        utc_epoch = ntp_sync._ntp_request(ntp_server)
        elapsed_ms = time.ticks_diff(time.ticks_ms(), t0)
        print(f"  server={ntp_server!r}  ({elapsed_ms}ms)")
        _fmt_epoch(utc_epoch, "結果")

        _hdr("4. 別サーバーとのクロスチェック")
        for alt_server in ("time.google.com", "time.cloudflare.com"):
            if alt_server == ntp_server:
                continue
            t0 = time.ticks_ms()
            alt_epoch = ntp_sync._ntp_request(alt_server)
            elapsed_ms = time.ticks_diff(time.ticks_ms(), t0)
            print(f"  server={alt_server!r}  ({elapsed_ms}ms)")
            _fmt_epoch(alt_epoch, "結果")
            if utc_epoch is not None and alt_epoch is not None:
                diff = alt_epoch - utc_epoch
                print(f"  -> 設定サーバーとの差: {diff:+d}秒")

        if utc_epoch is None:
            print("\n設定済みNTPサーバーからの取得に失敗したため、"
                  "5.以降は省略します。")
            return

        _hdr("5. タイムゾーン適用 + Pico RTC設定")
        local_epoch = utc_epoch + tz_offset_h * 3600
        tm = time.gmtime(local_epoch)
        year, month, day, hour, minute, second = tm[0], tm[1], tm[2], tm[3], tm[4], tm[5]
        print(f"  tz_offset_h={tz_offset_h:+d} -> ローカル時刻 "
              f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}")

        try:
            rtc = machine.RTC()
            weekday = tm[6]
            rtc.datetime((year, month, day, weekday, hour, minute, second, 0))
            print("  Pico RTC設定直後の読み戻し:", rtc.datetime())
        except Exception as e:
            print("  Pico RTC set/readback failed:", e)
    finally:
        ntp_sync._wifi_disconnect()

    _hdr("まとめ -- 切り分けの見方")
    print("  3.と4.のepoch差が数秒以上あれば、設定済みNTPサーバー側の応答/")
    print("  ネットワーク経路を疑ってください（プール系サーバーは個体により")
    print("  数百ms程度ずれることがあるが、数秒以上は通常ない）。")
    print("  5.のPico RTC読み戻しが設定した値と異なれば、machine.RTCの")
    print("  挙動自体（設定直後に正しく保持されるか）を疑ってください。")
    print()
    print("  ここまでが合っているのに実機の表示時刻がズレる場合は、PB-1000側")
    print("  の書き込み（ntp_sync.set_pb1000_time()のBCD変換/アドレス）か、")
    print("  ROM側タイマー(REG_TM)の進み方が原因です。main.py実行中に別の")
    print("  シリアル接続から次を数分おきに呼び、実時刻とのズレが時間ととも")
    print("  に拡大するか確認してください:")
    print("      import ntp_sync; print(ntp_sync.read_pb1000_clock())")


if __name__ == "__main__":
    main()
