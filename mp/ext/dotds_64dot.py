"""
dotds_64dot.py — 64ドットモード時、DOTDS(&H022C)と1文字クイック表示(&H02BD)を
SCTOP非依存の全8行対応版に置き換える内部フィックス。

背景: DOTDS/02BD は本来 SCTOP 起点の4行ウィンドウでしか物理LCDへ転送しないため、
64ドットモード(LEDTP 8行分)では行4以降が反映されない。SCTOP を固定する方式は
rom0.src のオートスクロールループが無限ループするため不可（sctop_lock.py で廃止済み、
詳細はそちらを参照）。そのため CALL フックで両ルーチンを丸ごと置き換え、SCTOP を
無視して常に現在のページ数ぶんを扱う。

32ドットモードではこの置き換えは不要かつ有害（本来の SCTOP スクロールを壊す）ため、
register() 時に system._config から [display] lcd_height を直接読んで判定し、32ドット
モードでは DSPMD の値に関わらず常に call_hook を disable する。lcd_c.set_num_pages()
は register() より後（main_boot.py の create_system() 内）で呼ばれるため、その時点で
get_num_pages() を読んでも確定前の値になってしまう — config を直接読むことでこの
タイミング問題を回避している（DOTDS/02BD 本体のページ数計算は実行時に毎回
get_num_pages() を読み直すので、そちらはタイミング問題なし）。

パフォーマンス: 両ルーチンはホットパス（画面更新毎／PRINT の文字出力毎）のため、
実処理は独立ネイティブモジュール dotds64（src/moddotds64.c、hd61700 コアとは分離）
にC実装済み。register() は import dotds64 に成功すればそちらを call_hook として登録し、
失敗（未ビルドの旧ファームウェア）時のみ以下の Python 版にフォールバックする。
設計の詳細は doc/dev_guide.md「dotds64 モジュール」および doc/extension_api.md の
本モジュールの項を参照。
"""

import hd61700 as cpu_core
import lcd_c

_DOTDS_ADDR = 0x022C
_DSPMD_ADDR = 0x68D0   # references/rom1.src: DSPMD
# MENU表示モードは DSPMD==3 の完全一致で判定される(bit4-5マスクではない)。
_DSPMD_MENU_VALUE = 0x03

_LEDTP_ADDR = 0x6201   # references/rom0.src: LCD display dot buffer
_LEDTP_RAM_OFF = _LEDTP_ADDR - 0x6000   # get_ram_view() 内でのオフセット

_ram_mv = None    # memoryview キャッシュ (register() で一度だけ生成、Pythonフォールバック用)
_system = None
_is_64dot_mode = False  # register() で config から一度だけ判定 (実行時に変化しない)


def _dotds_override():
    """DOTDS (&H022C) の Python フォールバック実装。
    LEDTP 先頭から現在のページ数ぶんをモノクロVRAMへ一括転送する。
    blit_reversed() は lcd_write() と同じビット反転をして書き込む
    (単純な memcpy だと上下(ビット順)が逆になり文字が反転表示される)。"""
    length = lcd_c.get_num_pages() * 192   # 32dot=768B(4行) / 64dot=1536B(8行)
    lcd_c.blit_reversed(_ram_mv[_LEDTP_RAM_OFF:_LEDTP_RAM_OFF + length], 0)
    lcd_c.mark_dirty()

    # 本来の DOTDS は末尾で必ず LCD ON コマンド (&H14) を送る (rom0.src:570)。
    # 送らないと lcd->display_on が true にならず LCD-OFF 塗りつぶしのままになる。
    _system.lcd.lcd_ctrl(0xDF)   # OP=1 (コマンドモード), CE=3 (両チップ選択)
    _system.lcd.lcd_write(0x14)  # LCD ON
    _system.lcd.lcd_ctrl(0xDE)   # OP=0 (データモードへ戻す)


_CHAR_DISP_ADDR = 0x02BD   # references/rom0.src: 1文字クイック表示
_EDCSR_ADDR = 0x68C8


def _char_display_override():
    """&H02BD (1文字クイック表示) の Python フォールバック実装。
    呼び出し元(041B: 通常表示 / 02BA-02BC: カーソル点滅)がレジスタ$2:$3に
    積んだ6バイトのソースアドレスを読み、ROM本来の (EDCSR-SCTOP) ではなく
    生の EDCSR から行・列を求めて VRAM へ直接書き込む。"""
    src_addr = cpu_core.get_reg(2) | (cpu_core.get_reg(3) << 8)
    src = bytes(cpu_core.read_mem(src_addr + i) for i in range(6))

    edcsr = cpu_core.read_mem(_EDCSR_ADDR)
    row = edcsr >> 5
    col = edcsr & 0x1F
    dst_off = row * 192 + col * 6
    if dst_off + 6 <= lcd_c.get_num_pages() * 192:
        lcd_c.blit_reversed(src, dst_off)
        lcd_c.mark_dirty()


def _sync_hook_enabled(dspmd_value):
    # 32ドットモードでは DSPMD の値に関わらず常に disable（モジュール docstring 参照）。
    normal_mode = _is_64dot_mode and (dspmd_value == 0)
    if normal_mode:
        _system.enable_call_hook(_DOTDS_ADDR)
        _system.enable_call_hook(_CHAR_DISP_ADDR)
    else:
        _system.disable_call_hook(_DOTDS_ADDR)
        _system.disable_call_hook(_CHAR_DISP_ADDR)


def _on_dspmd_write(addr, data, bank):
    """DSPMD (&H68D0) への書き込み監視コールバック。
    書き込みはキャンセルせず、新しい値に応じて DOTDS フックの有効/無効を同期する。"""
    _sync_hook_enabled(data)
    return False


def set_mode(is_64dot):
    """Runtime mode switch, called by the emulator menu's LCD Height toggle
    (GUI+F7 -> Display -> LCD Height). Unlike the register()-time detection
    above (which must read config directly due to an ordering constraint —
    see module docstring), this can safely re-check the DSPMD value via
    cpu_core, since it always runs well after register()."""
    global _is_64dot_mode
    _is_64dot_mode = bool(is_64dot)
    if _system is not None:
        _sync_hook_enabled(cpu_core.read_mem(_DSPMD_ADDR))


def register(system):
    global _ram_mv, _system, _is_64dot_mode
    _system = system
    _ram_mv = memoryview(cpu_core.get_ram_view())

    disp_cfg = (getattr(system, "_config", None) or {}).get("display", {})
    try:
        _is_64dot_mode = (int(disp_cfg.get("lcd_height", "32")) == 64)
    except (ValueError, TypeError):
        _is_64dot_mode = False

    # ホットパスのため、ネイティブモジュール dotds64 (src/moddotds64.c) があれば
    # そちらを優先し、無ければ (旧ファームウェア) Python 版にフォールバックする。
    try:
        import dotds64
        dotds_fn = dotds64.dotds_hook
        char_fn = dotds64.char_hook
        _native = True
    except ImportError:
        dotds_fn = _dotds_override
        char_fn = _char_display_override
        _native = False

    system.register_call_hook(_DOTDS_ADDR, dotds_fn, owner="dotds_64dot")
    system.register_call_hook(_CHAR_DISP_ADDR, char_fn, owner="dotds_64dot")
    _sync_hook_enabled(cpu_core.read_mem(_DSPMD_ADDR))  # 現在の状態に同期
    system.register_mem_write_hook(_DSPMD_ADDR, _on_dspmd_write, owner="dotds_64dot")
    if _is_64dot_mode:
        print("dotds_64dot: 64-dot mode - DOTDS full-row override armed "
              "(%s, page count re-checked on every call)"
              % ("native C hooks" if _native else "Python hooks"))
    else:
        print("dotds_64dot: 32-dot mode - override left disabled "
              "(ROM native DOTDS/02BD used)")
