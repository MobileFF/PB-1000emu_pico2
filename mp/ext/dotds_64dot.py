"""
dotds_64dot.py — 64ドットモード時、DOTDS(&H022C)の転送範囲と、
1文字クイック表示(&H02BD)の描画位置を、常に全8行(行0~7)基準に
強制する拡張モジュール
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【DOTDS (&H022C) について】
DOTDS (references/rom0.src:553-570) 自体は常に「行0～3(or 4)」しか
物理LCDへ転送しないため、BASIC の LOCATE で Y座標4～7 を指定して書いた
内容は LEDTP バッファ (&H6201～&H6800, 8行分=1536B) には正しく書かれて
いても画面には反映されない。system.register_call_hook(&H022C, ...) で
DOTDS 呼び出し自体を横取りし、「LEDTP 先頭から (64ドットモードなら
8行=1536B、32ドットモードなら4行=768B) をモノクロVRAMへ一括転送」する
処理に置き換える。転送元オフセットは常に LEDTP 先頭 (offset 0) 固定。

【1文字クイック表示 (&H02BD) について】
PRINT で1文字書くたびに呼ばれる &H041B→&H02BD は、DOTDSの一括転送とは
別に「今書いた1文字だけ」を即座に物理LCDへ直接書く高速パスだが、この
ルーチンは EDCSR ではなく (EDCSR - SCTOP) を物理行として使う
(references/rom0.src:667-683、027Cのコメント "in range 0..127" が示す
通り、SCTOP起点の4行ぶんウィンドウが前提)。通常表示中、行4以降へ移動
すると rom0.src:0478 の CR/LF ハンドラがオーバーフローを検出して SCTOP
を自動的に繰り上げる (0580, 059A/059C) ため、(EDCSR-SCTOP) は常に
0~3 に収まり、02BD は行4以降の内容を常に物理行3(見た目4行目)へ描いて
しまう。直後に CR/LF や次の LOCATE が DOTDS (&H022C) を呼べば、上の
_dotds_override が SCTOP を無視して正しい行へ描き直すため大抵は一瞬で
修正されるが、そのフレームの入力が「最後の1文字」だった場合(直後に
別の LOCATE が来ない場合)は誤った位置の描画が残り続ける。
このモジュールは &H02BD もフックし、02BDの呼び出し元が積んだソース
アドレス(レジスタ$2:$3、下位:上位)から実際の6バイト(文字ドット
パターンまたはカーソル点滅パターン)を読み、SCTOPを無視した生の
EDCSR行・列でVRAMへ直接書き込む。

【SCTOP (実画面先頭アドレス) を固定していない理由】
当初 sctop_lock.py で SCTOP を 0 に固定し、DOTDS 本来の
「LEDTP + 6*SCTOP」計算を無効化しようとしたが、references/rom0.src の
EDCSR変更時オートスクロール処理 (0589-0596) は SCTOP への書き込みが
実際に反映されることを前提にしたループになっており、書き込みをキャンセル
すると無限ループ (ハング) を起こすことが判明したため sctop_lock.py は
廃止した (詳細は sctop_lock.py 参照)。
LOCATE/PRINT の文字データ自体(LEDTP書き込み先アドレス)は SCTOP を
一切参照していない (rom0.src 全体で SCTOP を読むのは DOTDS 本来の
転送元計算・オートスクロールループ・02BDの表示範囲判定の3箇所のみ) ため、
SCTOP がオートスクロールで自由に変化しても、DOTDSと02BDの両方を
SCTOP非依存に上書きしている限り表示内容には影響しない。

【64ドットかどうかの判定タイミングに関する注意】
lcd_c.set_num_pages() は system 初期化時 (System.__init__ → _ext_init() →
このモジュールの register()) より後、main_boot.py の create_system() /
initialize_system() 内で ini 設定に応じて呼ばれる。したがって
register() の時点ではまだ 64/32 ドットのどちらか確定していないため、
「64ドットかどうか」は register() 時にキャッシュせず、DOTDS が実際に
呼ばれる _dotds_override() の中で毎回 lcd_c.get_num_pages() を読み直す。
(register() 呼び出し時点で判定・キャッシュしてしまうと、常に初期値の
32ドット相当と誤判定されるバグになる。)

CALL フックは全置換で「今回だけ元の処理に戻す」という部分フォールバック
ができない(セッションログ参照)ため、PF/MENU 表示モードでは ROM 本来の
DOTDS/02BD をそのまま動かしたい(MENU表示は SCTOP ウィンドウを前提に
動いているため、こちらを上書きすると壊れる)。DSPMD (&H68D0) への
書き込みを mem_write_hook で監視し、通常表示モードの間だけ両方の
call_hook を enable、それ以外は disable する方式で「必要な時だけ」
上書きを有効化する。
"""

import hd61700 as cpu_core
import lcd_c

_DOTDS_ADDR = 0x022C
_DSPMD_ADDR = 0x68D0   # references/rom1.src (patch.src): DSPMD
# references/rom0.src の &H03D3/&H03E8/&H03F6 に同一の
# `sbc $2,&H03 ;MENU display mode?` があり、MENU表示モードは
# DSPMD==3(完全一致)で判定されている。bit4-5マスクではない。
# (以前は _DSPMD_MASK=0x30 のビットマスク判定だったが、DSPMD=3の場合
# 3 & 0x30 == 0 となり誤って「通常表示」と判定してしまうバグがあった。)
_DSPMD_MENU_VALUE = 0x03

_LEDTP_ADDR = 0x6201   # references/rom0.src: LCD display dot buffer
_LEDTP_RAM_OFF = _LEDTP_ADDR - 0x6000   # get_ram_view() 内でのオフセット

_ram_mv = None    # memoryview キャッシュ (register() で一度だけ生成)
_system = None


def _dotds_override():
    """DOTDS (&H022C) の置き換え実装。
    LEDTP 先頭から (現在のページ数分の行) をモノクロVRAMへ一括転送する。
    lcd_c.blit_reversed() は本来の lcd_write()(LCDC_CMD_DRAW_BITIMAGE)が
    行うのと同じビット反転をしながら書き込む(単純な memcpy だと
    1バイトごとの上下(ビット順)が逆になり、文字が上下反転して表示される)。
    ページ数は毎回読み直す(理由はモジュール docstring 参照)。"""
    length = lcd_c.get_num_pages() * 192   # 32dot=768B(4行) / 64dot=1536B(8行)
    lcd_c.blit_reversed(_ram_mv[_LEDTP_RAM_OFF:_LEDTP_RAM_OFF + length], 0)
    lcd_c.mark_dirty()

    # 本来の DOTDS は末尾で必ず LCD ON コマンド (&H14) を送る
    # (references/rom0.src:570 "023E: ld $2,&H14,jr &H0278 ;LCD on")。
    # これを送らないと lcd->display_on が true にならず、
    # lcd_render_to_display() が常にグレーの LCD-OFF 塗りつぶし
    # (src/lcd_controller.c の `if (!lcd->display_on)` 分岐) を取り続ける。
    # system.power_on() (pb1000.py) と同じシーケンスで再現する。
    _system.lcd.lcd_ctrl(0xDF)   # OP=1 (コマンドモード), CE=3 (両チップ選択)
    _system.lcd.lcd_write(0x14)  # LCD ON
    _system.lcd.lcd_ctrl(0xDE)   # OP=0 (データモードへ戻す)


_CHAR_DISP_ADDR = 0x02BD   # references/rom0.src: 1文字クイック表示
_EDCSR_ADDR = 0x68C8


def _char_display_override():
    """&H02BD (1文字クイック表示) の置き換え実装。
    呼び出し元 (041B: 通常の文字表示 / 02BA-02BC: カーソル点滅) は、
    描画すべき6バイトのソースアドレスを呼び出し前にレジスタ$2(下位),
    $3(上位) に積んでいる(references/rom0.src:665-680)。ROM本来の
    02BDはこの6バイトを (EDCSR-SCTOP) から求めた物理行(常に0~3)へ
    書くが、ここでは代わりに生の EDCSR (SCTOPを無視) から行・列を
    求めて VRAM の対応ページへ直接書き込む。
    文字表示・カーソル点滅のどちらの呼び出し元でも、描画対象は常に
    現在のカーソル位置(EDCSR)なので、ソースの6バイトさえ正しく
    読めればこの行・列計算は両ケースで共通に使える。"""
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
    # rom0.src の MENU display mode 判定 (&H03D3等) は DSPMD==3 の完全一致
    # (bit4-5マスクではない)。DSPMD==0 が通常表示であることは元コメント
    # 通りなので、ここでは非0を「通常表示ではない」とみなす。
    normal_mode = (dspmd_value == 0)
    if normal_mode:
        _system.enable_call_hook(_DOTDS_ADDR)
        _system.enable_call_hook(_CHAR_DISP_ADDR)
    else:
        _system.disable_call_hook(_DOTDS_ADDR)
        _system.disable_call_hook(_CHAR_DISP_ADDR)


def _on_dspmd_write(addr, data, bank):
    """DSPMD (&H68D0) への書き込み監視コールバック。
    書き込みはキャンセルせず、新しい値に応じて DOTDS フックの有効/無効を
    同期するためだけに使う。"""
    _sync_hook_enabled(data)
    return False


def register(system):
    global _ram_mv, _system
    _system = system
    _ram_mv = memoryview(cpu_core.get_ram_view())

    system.register_call_hook(_DOTDS_ADDR, _dotds_override)
    system.register_call_hook(_CHAR_DISP_ADDR, _char_display_override)
    _sync_hook_enabled(cpu_core.read_mem(_DSPMD_ADDR))  # 現在の状態に同期
    system.register_mem_write_hook(_DSPMD_ADDR, _on_dspmd_write)
    print("dotds_64dot: DOTDS full-row override armed "
          "(page count re-checked on every call)")
