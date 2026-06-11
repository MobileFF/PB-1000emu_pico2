"""
BANK2/3 RAM 動作確認 拡張モジュール

--- Python 側から一括テスト ---
    CALL &H5E71         全テスト実行 (Python 側 R/W)
    PRINT PEEK &H5F00   結果 (0=全OK, 1=失敗あり)
    PRINT PEEK &H5F01   合格テスト数
    PRINT PEEK &H5F02   失敗テスト数

--- BASIC プログラム用バンク切替 ---
    CALL &H5E41         データバンク → BANK2  (UA bit[5:4]=10)
    CALL &H5E51         データバンク → BANK3  (UA bit[5:4]=11)
    CALL &H5E61         データバンク → 復元   (UA bit[5:4]=00)

    切替後は POKE/PEEK で &H8000-&HFFFF がそのバンクに届く。
    BASIC プログラムは ram_test.bas を参照。

Python テスト内容:
    Test1  キーアドレスへの多パターン書き込み/読み返し
    Test2  先頭256バイト連番パターン
    Test3  末尾256バイト逆順パターン
    Test4  BANK2/BANK3 独立性（同アドレスに別値）
"""

CALL_ADDR        = 0x5E71
BANK_SEL2_ADDR   = 0x5E41  # CALL &H5E41 → data bank 2
BANK_SEL3_ADDR   = 0x5E51  # CALL &H5E51 → data bank 3
BANK_RESTORE_ADDR = 0x5E61 # CALL &H5E61 → data bank 0 (restore)
BANK_SIZE = 0x8000  # 32 KB

# 書き込み/読み返しを行うバンク内オフセット一覧
_TEST_OFFSETS = [
    0x0000, 0x0001,          # 先頭
    0x00FE, 0x00FF,          # 先頭ページ末尾
    0x0100,                  # 第2ページ先頭
    0x3FFF,                  # 前半末尾
    0x4000, 0x4001,          # 後半先頭
    0x7FFE, 0x7FFF,          # 末尾
]

# 使用するビットパターン
_PATTERNS = [0x00, 0xFF, 0xAA, 0x55, 0xA5, 0x5A]


def register(system):
    try:
        system.register_call_hook(CALL_ADDR,
                                  lambda: _run_test(system))
        system.register_call_hook(BANK_SEL2_ADDR,
                                  lambda: _select_bank(system, 2))
        system.register_call_hook(BANK_SEL3_ADDR,
                                  lambda: _select_bank(system, 3))
        system.register_call_hook(BANK_RESTORE_ADDR,
                                  lambda: _restore_bank(system))
        print(f"ram_test: CALL &H{CALL_ADDR:04X}  -> run all tests")
        print(f"ram_test: CALL &H{BANK_SEL2_ADDR:04X}  -> select BANK2 data")
        print(f"ram_test: CALL &H{BANK_SEL3_ADDR:04X}  -> select BANK3 data")
        print(f"ram_test: CALL &H{BANK_RESTORE_ADDR:04X}  -> restore bank")
    except Exception as e:
        print(f"ram_test: init failed: {e}")


def _select_bank(system, bank):
    """UA bit[5:4] をバンク番号に設定する。他のビットは保持。"""
    system.ua = (system.ua & 0xCF) | ((bank & 0x03) << 4)
    print(f"ram_test: data bank={bank}  UA={system.ua:#04x}")


def _restore_bank(system):
    """UA bit[5:4] をクリアしてバンク0(ROM)に戻す。"""
    system.ua = system.ua & 0xCF
    print(f"ram_test: data bank restored  UA={system.ua:#04x}")


# ---------------------------------------------------------------------------

def _verify(label, buf, addr, expected):
    """1バイト検証。不一致なら詳細を出力して False を返す。"""
    got = buf[addr]
    if got == expected:
        return True
    print(f"    FAIL [{label}] off={addr:#06x} exp={expected:#04x} got={got:#04x}")
    return False


def _test_bank(slot, buf):
    """1バンク分のテストを実行して (passed, failed) を返す。"""
    passed = 0
    failed = 0

    # ------------------------------------------------------------------
    # Test 1: キーアドレス × 多パターン R/W
    # ------------------------------------------------------------------
    print(f"  Test1: Key-addr R/W  ({len(_TEST_OFFSETS)} addr x {len(_PATTERNS)} patterns)")
    saved = [buf[a] for a in _TEST_OFFSETS]
    ok = True
    for pat in _PATTERNS:
        for addr in _TEST_OFFSETS:
            buf[addr] = pat
        for addr in _TEST_OFFSETS:
            if not _verify(f"B{slot}T1 pat={pat:#04x}", buf, addr, pat):
                ok = False
    for i, addr in enumerate(_TEST_OFFSETS):
        buf[addr] = saved[i]
    if ok:
        print(f"    OK")
        passed += 1
    else:
        failed += 1

    # ------------------------------------------------------------------
    # Test 2: 先頭 256 バイト連番 (0x00..0xFF)
    # ------------------------------------------------------------------
    print(f"  Test2: Head-256 sequential  (off 0x0000-0x00FF)")
    saved2 = [buf[i] for i in range(256)]
    ok = True
    for i in range(256):
        buf[i] = i & 0xFF
    for i in range(256):
        if not _verify(f"B{slot}T2", buf, i, i & 0xFF):
            ok = False
    for i in range(256):
        buf[i] = saved2[i]
    if ok:
        print(f"    OK")
        passed += 1
    else:
        failed += 1

    # ------------------------------------------------------------------
    # Test 3: 末尾 256 バイト逆順 (0xFF..0x00)
    # ------------------------------------------------------------------
    base = BANK_SIZE - 256
    print(f"  Test3: Tail-256 reverse     (off {base:#06x}-{base+255:#06x})")
    saved3 = [buf[base + i] for i in range(256)]
    ok = True
    for i in range(256):
        buf[base + i] = (255 - i) & 0xFF
    for i in range(256):
        if not _verify(f"B{slot}T3", buf, base + i, (255 - i) & 0xFF):
            ok = False
    for i in range(256):
        buf[base + i] = saved3[i]
    if ok:
        print(f"    OK")
        passed += 1
    else:
        failed += 1

    return passed, failed


def _run_test(system):
    total_passed = 0
    total_failed = 0

    print("\n" + "=" * 44)
    print("  BANK2/3 RAM Test")
    print("=" * 44)

    for slot in (2, 3):
        if not system.has_bank[slot]:
            print(f"\n[BANK{slot}] Not present — SKIP")
            continue
        buf = system._bank_ram[slot]
        if not buf:
            print(f"\n[BANK{slot}] Buffer empty — SKIP")
            continue

        print(f"\n[BANK{slot}]  buf_type={type(buf).__name__}")
        p, f = _test_bank(slot, buf)
        total_passed += p
        total_failed += f

    # ------------------------------------------------------------------
    # Test 4: BANK2/3 独立性
    # ------------------------------------------------------------------
    if system.has_bank[2] and system.has_bank[3] and \
       system._bank_ram[2] and system._bank_ram[3]:
        print(f"\n[Isolation] BANK2 vs BANK3")
        b2 = system._bank_ram[2]
        b3 = system._bank_ram[3]
        checks = [
            (0x0000, 0xB2, 0xB3),
            (0x4000, 0x2B, 0x3B),
            (0x7FFF, 0xC2, 0xC3),
        ]
        s2 = {a: b2[a] for a, _, _ in checks}
        s3 = {a: b3[a] for a, _, _ in checks}
        ok = True
        for addr, v2, v3 in checks:
            b2[addr] = v2
            b3[addr] = v3
        for addr, v2, v3 in checks:
            if not _verify("Iso B2", b2, addr, v2):
                ok = False
            if not _verify("Iso B3", b3, addr, v3):
                ok = False
        for addr, _, _ in checks:
            b2[addr] = s2[addr]
            b3[addr] = s3[addr]
        if ok:
            print(f"  OK")
            total_passed += 1
        else:
            total_failed += 1

    # ------------------------------------------------------------------
    # 結果サマリ
    # ------------------------------------------------------------------
    total = total_passed + total_failed
    status = "ALL PASSED" if total_failed == 0 else f"{total_failed} FAILED"
    print("\n" + "=" * 44)
    print(f"  Result: {total_passed}/{total}  {status}")
    print("=" * 44)

    system._ext_work[0] = 0x00 if total_failed == 0 else 0x01
    system._ext_work[1] = total_passed & 0xFF
    system._ext_work[2] = total_failed & 0xFF
