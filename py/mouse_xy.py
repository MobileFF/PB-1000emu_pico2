import pyautogui
import sys

print("--- マウス座標確認ツール ---")
print("Ctrl + C で終了します。")
print("ボタンの上にマウスを置いて、数値をメモしてください。\n")

try:
    while True:
        # マウスの現在位置を取得
        x, y = pyautogui.position()
        # 画面上の色も取得（ボタンの特定に便利）
        pixel_color = pyautogui.screenshot().getpixel((x, y))
        
        # 上書き表示
        position_str = f"X: {x:>4} Y: {y:>4}  |  RGB: {pixel_color}"
        print(position_str, end="")
        print("\b" * len(position_str), end="", flush=True)
except KeyboardInterrupt:
    print("\n終了しました。")