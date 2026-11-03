"""
FOREX_PB.EXE INKEY 直接呼び出しバグの補正パッチ

FOREX_PB.txt の BOSS_DEFEAT_PH4 (MODEL=PB1000 分岐, ソース2003-2004行目)
だけが、他8箇所の `LDW $28,INKEY / CAL IOCS_CAL` という正規手順を使わず
`CAL INKEY`(=`CAL &H9E3B`)を直接呼んでいる。IOCS_CAL(実アドレス0x723A)は
`PST UA,&H50` でROM(bank0)へ切り替えてから呼び出し、戻ったら`PST UA,&H51`
でゲーム自身のRAMバンクへ復帰する規約になっているが、この1箇所だけその
UA切り替えが抜けている。実行時UAは常にゲーム自身のバンク(0x51)のままの
ため、`CAL &H9E3B`はROMのINKEYルーチンではなく、たまたま同じアドレスに
あるFOREX_PB自身のRAM上のバイト列(CPRINTのC_PRINTLOOP_REPL末尾と一致する
内容)を実行してしまう。この末尾ループは$12(本来は無関係な、その時々の
ゲーム内ワークレジスタ値)を8バイトずつ減算しながら厳密に0になるまで
回るため、$12が8の倍数でない値のときは理論上絶対に終了しない無限ループ
となり、最終的にIXが16bitアドレス空間を一周してループ自身のJR命令を
自己書き換えし、TRPに落ちてクラッシュする。詳細は
調査用/FOREX_PB/investigation_notes.md (2026-08-13の節) を参照。

このモジュールは呼び出し元アドレス(0x7D3E)を register_call_hook でフック
し、フック発火時に「LDW $28,INKEY / CAL IOCS_CAL」を実行したのと等価な
状態になるよう手動でスタックを組み立てる:

  hd61700.c の汎用フック(命令フェッチ直前、PC==フック対象アドレスなら
  常に発火)は、フック関数を呼んだ直後に必ず「SSから2バイトpop→
  そのアドレス+1へジャンプ」を自動的に行う(BASICのpush+JP CALL規約を
  想定した設計)。これは無条件でPythonの戻り値を見ないため、
  「介入するかどうか」をPython側で選ぶことはできない。

  そこでこの自動pop+jumpを逆手にとり、フック関数の中で手動で2組の
  16bit値をスタックにpushしておく:
    1. 元のCALの戻り先(0x7D41)  ― IOCS_CALが最後にRTNしたときに
       ポップされるべき、本当の最終リターン先
    2. IOCS_CAL(0x723A)のアドレス-1 ― 直後にCディスパッチャ自身が
       自動popしてジャンプ先として使う値(pop後+1されるのでちょうど
       0x723Aに着地する)
  ついでに $28:$29 = INKEY(0x9E3B) をLDW $28,INKEY相当にセットする。

  これにより制御はIOCS_CALへ渡り、実際にUA=0x50へ切り替えたうえで本物の
  ROM INKEYが実行され、RTN後にUA=0x51へ復帰し、最終的に0x7D41(元のCAL
  命令の直後)へ正しく戻る — ソースコードが本来書くべきだった
  `LDW $28,INKEY / CAL IOCS_CAL` と完全に等価な結果になる。

  スタックへの書き込みは system.ram (RAM_START=0x6000 起点の
  memoryview、副作用なし) を直接使う。SS は実測で常に0x6000-0x7FFF
  (バンク非依存の標準RAM) の範囲に収まっている前提 — もし範囲外なら
  安全のため何もしない。
"""

_CALLER_PC = 0x7D3E     # BOSS_DEFEAT_PH4 の "CAL &H9E3B" (MODEL=PB1000限定)
_RETURN_PC = 0x7D41     # _CALLER_PC + 3 (CAL IM16 命令長)
_INKEY_ADDR = 0x9E3B    # FOREX_PB.txt: INKEY:EQU &H9E3B
_IOCS_CAL_ADDR = 0x723A # FOREX_PB.txt: IOCS_CAL: (実測アドレス)


def register(system):
    profile = getattr(system, 'profile_dir', '') or ''
    if 'FOREX_PB' not in profile:
        return
    try:
        system.register_call_hook(_CALLER_PC, lambda: _patch(system),
                                   owner="forex_pb_inkey_patch")
        print("forex_pb_inkey_patch: CALL &H%04X -> UA-safe IOCS_CAL(INKEY) wrapper installed" % _CALLER_PC)
    except Exception as e:
        print(f"forex_pb_inkey_patch: init failed: {e}")


def _push_word(ram, ram_base, ss, value):
    """hd61700.c push() を模倣: SS-- してから上位バイト、続けてSS--して下位バイトの順。"""
    ss = (ss - 1) & 0xFFFF
    ram[ss - ram_base] = (value >> 8) & 0xFF
    ss = (ss - 1) & 0xFFFF
    ram[ss - ram_base] = value & 0xFF
    return ss


def _patch(system):
    cpu = system.cpu
    ram = getattr(system, 'ram', None)
    ram_base = getattr(system, 'RAM_START', 0x6000)
    ss = cpu.get_reg16(4)  # REG_SS
    if ram is None or not (ram_base <= ss <= ram_base + len(ram)):
        print("forex_pb_inkey_patch: SS=&H%04X out of expected RAM range, skipping patch" % ss)
        return
    # 1) 最終的な戻り先(元のCALの直後)を先に積む — IOCS_CALのRTNが最後に消費する
    ss = _push_word(ram, ram_base, ss, _RETURN_PC)
    # 2) Cディスパッチャの自動pop+jumpが直後に消費する分 (IOCS_CAL-1)
    ss = _push_word(ram, ram_base, ss, (_IOCS_CAL_ADDR - 1) & 0xFFFF)
    cpu.set_reg16(4, ss)
    # $28:$29 = INKEY (LDW $28,INKEY 相当。REG_GET16: $28=下位, $29=上位)
    cpu.set_reg(28, _INKEY_ADDR & 0xFF)
    cpu.set_reg(29, (_INKEY_ADDR >> 8) & 0xFF)
