import pio_usb
import time

host = pio_usb.Host(pin_dp=2)

print("キーボードのキーを押してください（Ctrl+Cで終了）")

while True:
    for device in host.get_devices():
        if device.is_hid():
            # HIDレポート（8バイトのデータ）を取得
            report = device.get_report()
            
            # 何かキーが押されている場合（report[2]以降が0以外）
            if report and any(report[2:]):
                # report[2] にメインで押されたキーのスキャンコードが入る
                print(f"検出されたスキャンコード: {hex(report[2])}")
                
                # チャタリング防止（簡易）
                while device.get_report():
                    time.sleep(0.01)
                    
    time.sleep(0.01)