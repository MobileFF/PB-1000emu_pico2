import os
import time
import subprocess
import pygetwindow as gw
import pyautogui
import pydirectinput
import pytesseract
from PIL import Image

# ================= 設定セクション =================
# スクリプト自身の場所を基準にパスを固定します
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

EMU_DIR = r"C:\Users\user\Downloads\pb1000em"
EMU_EXE_NAME = "pb1000.exe"

# 画像ファイルのパスを絶対パスに変更
RUN_BUTTON_IMG = os.path.join(SCRIPT_DIR, 'run_btn.png')

TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
LOG_FILE = os.path.join(SCRIPT_DIR, "trace_golden.log") # ログもスクリプトと同じ場所へ

pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
OCR_CONFIG = '--psm 6 -c tessedit_char_whitelist=0123456789ABCDEF:ZNCUZF'
# =================================================

# ================= 設定セクション =================
# 1. 各種パスの設定
# EMU_DIR = r"C:\Users\user\Downloads\pb1000em"
# EMU_EXE = "pb1000.exe"
# EXE_PATH = r"C:\Users\user\Downloads\pb1000em\pb1000.exe"  # エミュレータの場所を書き換えてください
# TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe" # パスを確認してください
# LOG_FILE = "trace_golden.log"

# # 2. OCR設定
# pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
# OCR_CONFIG = '--psm 6 -c tessedit_char_whitelist=0123456789ABCDEF:ZNCUZF' # 16進数とフラグに限定

# # 3. ボタン画像
# RUN_BUTTON_IMG = 'run_btn.png'
# =================================================

def setup_emulator():
    print("--- 起動プロセス開始 ---")
    try:
        os.chdir(EMU_DIR)
        subprocess.Popen(os.path.join(".", EMU_EXE_NAME), shell=True)
        time.sleep(3)
    except Exception as e:
        print(f"起動エラー: {e}")
        return None

    # メインウィンドウとデバッガの制御
    try:
        titles = gw.getAllTitles()
        target_title = [t for t in titles if 'PB-1000 Emulator' in t][0]
        main_win = gw.getWindowsWithTitle(target_title)[0]
        main_win.activate()
        
        # 1. まずF3でデバッガを起動（ここでエミュレーションが一時停止する）
        pydirectinput.press('f3')
        print("F3送信: デバッガを起動し、実行を一時停止しました。")
        time.sleep(2)
        
        # 2. デバッガウィンドウを配置固定
        debug_win = gw.getWindowsWithTitle('Debug Window')[0]
        debug_win.restore()
        debug_win.moveTo(0, 0)
        debug_win.activate()
        print("デバッガを(0,0)に配置しました。")
        time.sleep(1)

        # 3. デバッガが開いた状態で「本体のリセット」を押す
        # これにより、デバッガがリセット後の先頭命令を指した状態で待機する
        print("リセット操作を実行中...")
        main_win.activate() # 一旦本体をアクティブに
        time.sleep(0.5)
        #reset_pos = pyautogui.locateCenterOnScreen(RESET_BUTTON_IMG, confidence=0.8)
        pyautogui.click(1023,617)
        # if reset_pos:
        #     pyautogui.click(reset_pos)
        #     print("RESETをクリックしました。")
        #     time.sleep(1)
        # else:
        #     print("警告: RESETボタンが見つかりません。座標指定で試行します。")
        #     # 本体の中心付近を仮クリック（画像認識失敗時の保険）
        #     pyautogui.click(main_win.left + (main_win.width // 2), main_win.top + (main_win.height // 2))

        # 再びデバッガを前面へ
        debug_win.activate()
        return debug_win
        
    except Exception as e:
        print(f"セットアップエラー: {e}")
        return None

def capture_registers(debug_win):
    # OCR範囲 (Debug Windowが0,0にある時の Registers表示エリア)
    # 以前の画像に基づき、右上レジスタ部分を重点的に切り出し
    reg_box = (250, 40, 500, 240) 
    
    screenshot = pyautogui.screenshot(region=(debug_win.left, debug_win.top, debug_win.width, debug_win.height))
    reg_img = screenshot.crop(reg_box).convert('L')
    # 3倍拡大して鮮明化
    reg_img = reg_img.resize((reg_img.width * 3, reg_img.height * 3), Image.Resampling.LANCZOS)
    
    return pytesseract.image_to_string(reg_img, config=OCR_CONFIG).strip()

def capture_mnemonic(debug_win):
    # OCR範囲 (Debug Windowが0,0にある時の Registers表示エリア)
    # 以前の画像に基づき、右上レジスタ部分を重点的に切り出し
    reg_box = (20,54, 289, 70) 
    
    screenshot = pyautogui.screenshot(region=(debug_win.left, debug_win.top, debug_win.width, debug_win.height))
    reg_img = screenshot.crop(reg_box).convert('L')
    # 3倍拡大して鮮明化
    reg_img = reg_img.resize((reg_img.width * 3, reg_img.height * 3), Image.Resampling.LANCZOS)
    
    return pytesseract.image_to_string(reg_img, config=OCR_CONFIG).strip()

def run_automation(debug_win, total_steps=100):
    print(f"--- 自動トレース開始 ({total_steps}ステップ) ---")
    
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        for i in range(total_steps):
            # 現在（実行前）のレジスタ状態を記録
            # reg_data = capture_registers(debug_win)
            # f.write(f"STEP: {i:04d}\n{reg_data}\n" + "-"*30 + "\n")

            mnemonic = capture_mnemonic(debug_win)
            f.write(f"{mnemonic}\n")

            f.flush()

            # Runボタン（Single step）をクリックして1命令進める
            try:
                # btn_pos = pyautogui.locateCenterOnScreen(RUN_BUTTON_IMG, confidence=0.8)
                # if btn_pos:
                #   pyautogui.click(btn_pos)
                # else:
                #     pyautogui.click(debug_win.left + 425, debug_win.top + 65)
                pyautogui.click(579,65)
            except Exception as e:
                print(f"クリックエラー: {e}")
            
            # 命令実行と描画の反映待ち
            time.sleep(0.4)

    print(f"トレース完了: {LOG_FILE}")

if __name__ == "__main__":
    debug_w = setup_emulator()
    if debug_w:
        # ステップ数は必要に応じて増やしてください
        run_automation(debug_w, total_steps=100)
