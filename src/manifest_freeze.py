# mp/*.py のフリーズ定義 (2026-10-17)
#
# 目的: 起動時のコンパイル(import)に必要な連続ヒープ確保が、GCヒープの断片化
# で失敗する問題への対策。フリーズ済みモジュールはビルド時にバイトコード化
# されてフラッシュに焼き込まれるため、import時にコンパイルが走らず(＝その場
# での大きな連続確保が要らない)、かつコード自体もGCヒープを一切消費しない。
# 詳細な検討経緯は 調査用/mp_freeze_tradeoffs.md 参照。
#
# 【使い方】このファイルは Google Drive 側 (このリポジトリ) が正本。
# ports/rp2/boards/manifest.py から
#   include("$(PORT_DIR)/boards/manifest_freeze.py")
# として読み込ませる (1行の追加はチェックアウト側にのみ存在し、git管理外な
# ので、micropython.pb1000 チェックアウトを作り直した場合はこの1行を
# 手動で追加し直す必要がある -- doc/build_guide.md 参照)。
#
# 【除外しているもの】
# - boot.py / main.py: pyexec_file_if_exists()はフリーズ済みモジュールを
#   フラッシュ上のファイルより先に探すため(通常のimportの優先順位とは逆)。
#   main.pyは2026-10-17にランチャー化(mp/main_entry.pyへ本体を分離)した上で
#   このファイル自体は非フリーズのまま維持している -- フリーズすると、
#   デバイスのファイルシステムにmain.pyが存在しなくても毎回自動起動してしまい
#   (開発時に電源再投入のたびにエミュレータが起動してmpremoteがシリアルポートを
#   掴めなくなる、このプロジェクト自身の開発運用を妨げる)、本体
#   (main_entry.py、下記リストに含む)だけをフリーズすることで、その問題を
#   起こさずにヒープ削減効果(約10.4KB)だけを得ている。
#   boot.pyも同じ優先順位の非対称性があるため、一貫して非フリーズのまま。
# - ext/*.py: プロファイル別の拡張モジュール機構(pb1000.pyの
#   _ext_load_modules())で、SDカード上に後から追加されることを前提とした
#   ファイルなので、ビルド時に固定するのは筋が違う。
# - mp/配下のデータファイル(*.json, *.map, *.ini, *.raw)はそもそも対象外
#   (freeze()は.py/.mpyのみを扱う)。

freeze(
    "/home/flex/projects/hd61700/mp",
    [
        "boot_session.py",
        "boot_status.py",
        "clock_overlay.py",
        "config.py",
        "debug.py",
        "disk_select_ui.py",
        "display_init.py",
        "draw_text.py",
        "emulator_menu.py",
        "emulator_menu_capture.py",
        "emulator_menu_debug.py",
        "emulator_menu_display_actions.py",
        "emulator_menu_ram.py",
        "emulator_menu_system_actions.py",
        "fdd_protocol.py",
        "fdd_storage.py",
        "funckey_bar.py",
        "hdmi_menu_mirror.py",
        "ili9341.py",
        "keymap.py",
        "lcd_controller_c.py",
        "main_actions.py",
        "main_boot.py",
        "main_cleanup.py",
        "main_entry.py",
        "main_input_cursor.py",
        "main_input_joystick.py",
        "main_input_keyboard.py",
        "main_input_touch.py",
        "main_runtime.py",
        "md100_dos.py",
        "mem_overlay.py",
        "ntp_sync.py",
        "pb1000.py",
        "pb1000_fdd.py",
        "pb1000_state_io.py",
        "pio_uart.py",
        "png_writer.py",
        "ram_autosave.py",
        "sdcard.py",
        "setup_menu.py",
        "st7796.py",
        "xpt2046.py",
    ],
)
