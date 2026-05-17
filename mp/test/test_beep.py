"""
PR10 BEEP Sound Reproduction - テストスクリプト

【ユニットテスト】
  モック machine を使用。ブザー非接続でも実行可能。
  Pico 2 REPL から:
    exec(open('/test/test_beep.py').read())
  または:
    from test.test_beep import run_unit_tests; run_unit_tests()

【ハードウェア統合テスト】
  GP14 にパッシブ圧電ブザーを接続した状態で実行。
    from test.test_beep import run_hw_test; run_hw_test()
"""

import time

# ------------------------------------------------------------------ #
# ヘルパー                                                             #
# ------------------------------------------------------------------ #

def _assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: got={actual!r}, expected={expected!r}")

def _assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)

def _assert_none(val, msg):
    if val is not None:
        raise AssertionError(f"{msg}: got={val!r}")


# ------------------------------------------------------------------ #
# モック machine                                                        #
# ------------------------------------------------------------------ #

class MockPin:
    def __init__(self, n, *args, **kwargs):
        self.n = n

class MockPWM:
    def __init__(self, pin):
        self.pin = pin
        self._freq = 0
        self._duty = 0
        self.freq_calls = []
        self.duty_calls = []

    def freq(self, f):
        self._freq = f
        self.freq_calls.append(f)

    def duty_u16(self, d):
        self._duty = d
        self.duty_calls.append(d)

class MockPWMFailing:
    """初期化で例外を投げるモック（不正 GPIO 番号のシミュレーション）"""
    def __init__(self, pin):
        raise ValueError(f"invalid GPIO: {pin.n}")

class _MockMachine:
    Pin = MockPin
    PWM = MockPWM

class _MockMachineFailing:
    Pin = MockPin
    PWM = MockPWMFailing


# ------------------------------------------------------------------ #
# テスト対象ロジック（pb1000.py の _beep_init / _beep_set と同一）     #
# ------------------------------------------------------------------ #

PD_BEEP_MASK = 0xC0  # pb1000.py の定数と同値であること

class _BeepStub:
    """PB1000System の BEEP サブシステムのみを切り出したスタブ。"""

    def __init__(self, config, machine_mod=None):
        self._config = config
        self._machine_mod = machine_mod if machine_mod is not None else _MockMachine()

    def init(self):
        self._beep_on = False
        self._beep_pwm = None
        cfg = (self._config or {}).get("beep", {})
        enabled = cfg.get("enable", "true").lower() in ("1", "true", "yes", "on")
        if not enabled:
            return
        try:
            gpio_pin = int(cfg.get("gpio_pin", "14"))
            freq_hz  = int(cfg.get("freq_hz",  "1000"))
            duty_pct = int(cfg.get("duty",     "50"))
        except (ValueError, TypeError):
            gpio_pin, freq_hz, duty_pct = 14, 1000, 50
        self._beep_duty = max(0, min(65535, duty_pct * 65535 // 100))
        m = self._machine_mod
        try:
            self._beep_pwm = m.PWM(m.Pin(gpio_pin))
            self._beep_pwm.freq(freq_hz)
            self._beep_pwm.duty_u16(0)
        except Exception as e:
            self._beep_pwm = None

    def set(self, on):
        if self._beep_on == on:
            return
        self._beep_on = on
        if self._beep_pwm is None:
            return
        self._beep_pwm.duty_u16(self._beep_duty if on else 0)

    def port_write(self, data):
        """_port_write の BEEP 検出ロジック（pb1000.py と同一）"""
        beep_bits = data & PD_BEEP_MASK
        if beep_bits == PD_BEEP_MASK:
            self.set(False)
        elif beep_bits == 0x40 or beep_bits == 0x80:
            self.set(True)


def _make_stub(cfg_dict=None, machine_mod=None):
    config = {"beep": cfg_dict} if cfg_dict is not None else {}
    stub = _BeepStub(config, machine_mod)
    stub.init()
    return stub


# ------------------------------------------------------------------ #
# ユニットテスト                                                        #
# ------------------------------------------------------------------ #

def test_pd_beep_mask_value():
    """PD_BEEP_MASK が 0xC0 であることを確認"""
    _assert_eq(PD_BEEP_MASK, 0xC0, "PD_BEEP_MASK")


def test_init_default_config():
    """デフォルト設定で PWM が正常に初期化されること"""
    stub = _make_stub({"enable": "true", "gpio_pin": "14", "freq_hz": "1000", "duty": "50"})
    _assert_true(stub._beep_pwm is not None, "PWM should be initialized")
    _assert_eq(stub._beep_pwm._freq, 1000, "freq")
    _assert_eq(stub._beep_pwm._duty, 0, "initial duty should be 0")
    _assert_eq(stub._beep_on, False, "initial beep_on should be False")
    expected_duty = 50 * 65535 // 100
    _assert_eq(stub._beep_duty, expected_duty, "beep_duty for 50%")


def test_init_disabled():
    """enable = false のとき _beep_pwm が None になること"""
    stub = _make_stub({"enable": "false"})
    _assert_none(stub._beep_pwm, "_beep_pwm should be None when disabled")
    _assert_eq(stub._beep_on, False, "beep_on should remain False")


def test_init_bad_gpio_no_crash():
    """不正な GPIO 番号で例外が発生しても起動が継続すること"""
    stub = _make_stub(
        {"enable": "true", "gpio_pin": "99", "freq_hz": "1000", "duty": "50"},
        machine_mod=_MockMachineFailing(),
    )
    _assert_none(stub._beep_pwm, "_beep_pwm should be None after init failure")
    _assert_eq(stub._beep_on, False, "beep_on should be False after init failure")


def test_duty_calculation():
    """デューティ比の変換が正しいこと"""
    for duty_pct, expected in [(0, 0), (50, 32767), (100, 65535)]:
        stub = _make_stub({"enable": "true", "gpio_pin": "14", "freq_hz": "1000", "duty": str(duty_pct)})
        _assert_eq(stub._beep_duty, expected, f"duty {duty_pct}%")


def test_port_write_0x40_starts_beep():
    """0x40 (bit6 High フェーズ) で BEEP が ON になること"""
    stub = _make_stub({"enable": "true", "gpio_pin": "14", "freq_hz": "1000", "duty": "50"})
    stub.port_write(0x40)
    _assert_eq(stub._beep_on, True, "beep_on after 0x40")
    _assert_true(stub._beep_pwm._duty > 0, "PWM duty should be > 0")


def test_port_write_0x80_starts_beep():
    """0x80 (bit7 High フェーズ) で BEEP が ON になること"""
    stub = _make_stub({"enable": "true", "gpio_pin": "14", "freq_hz": "1000", "duty": "50"})
    stub.port_write(0x80)
    _assert_eq(stub._beep_on, True, "beep_on after 0x80")
    _assert_true(stub._beep_pwm._duty > 0, "PWM duty should be > 0")


def test_port_write_0xc0_stops_beep():
    """0xC0 (ROM 終了通知) で BEEP が OFF になること"""
    stub = _make_stub({"enable": "true", "gpio_pin": "14", "freq_hz": "1000", "duty": "50"})
    stub.port_write(0x40)   # ON にしてから
    stub.port_write(0xC0)   # 終了通知
    _assert_eq(stub._beep_on, False, "beep_on after 0xC0")
    _assert_eq(stub._beep_pwm._duty, 0, "PWM duty should be 0 after stop")


def test_port_write_0x80_no_state_change_when_already_on():
    """既に BEEP ON のとき 0x80 を受けても duty_u16 が再呼び出しされないこと"""
    stub = _make_stub({"enable": "true", "gpio_pin": "14", "freq_hz": "1000", "duty": "50"})
    stub.port_write(0x40)
    duty_calls_before = len(stub._beep_pwm.duty_calls)
    stub.port_write(0x80)   # 状態変化なし → set() は何もしないはず
    _assert_eq(stub._beep_on, True, "beep_on should remain True")
    _assert_eq(len(stub._beep_pwm.duty_calls), duty_calls_before,
               "duty_u16 should not be called again when state unchanged")


def test_port_write_other_bits_ignored():
    """BEEP ビット以外のビットは BEEP 状態に影響しないこと"""
    stub = _make_stub({"enable": "true", "gpio_pin": "14", "freq_hz": "1000", "duty": "50"})
    # bit6,7 が両方 0（0x00, 0x3F など）→ 変化なし
    stub.port_write(0x00)
    _assert_eq(stub._beep_on, False, "0x00: beep should remain OFF")
    stub.port_write(0x3F)
    _assert_eq(stub._beep_on, False, "0x3F: beep should remain OFF")


def test_beep_off_when_disabled():
    """enable = false のとき port_write を呼んでも例外が発生しないこと"""
    stub = _make_stub({"enable": "false"})
    stub.port_write(0x40)   # 例外なく処理されること
    stub.port_write(0xC0)
    _assert_eq(stub._beep_on, False, "beep_on should stay False when disabled")


def test_full_beep_cycle():
    """0x40/0x80 交互 → 0xC0 の一連サイクルが正常動作すること"""
    stub = _make_stub({"enable": "true", "gpio_pin": "14", "freq_hz": "1000", "duty": "50"})
    stub.port_write(0x40)
    _assert_eq(stub._beep_on, True, "cycle: ON after 0x40")
    stub.port_write(0x80)
    _assert_eq(stub._beep_on, True, "cycle: ON after 0x80")
    stub.port_write(0x40)
    _assert_eq(stub._beep_on, True, "cycle: ON after 0x40 again")
    stub.port_write(0xC0)
    _assert_eq(stub._beep_on, False, "cycle: OFF after 0xC0")
    _assert_eq(stub._beep_pwm._duty, 0, "cycle: duty=0 after 0xC0")


# ------------------------------------------------------------------ #
# ユニットテストまとめ実行                                              #
# ------------------------------------------------------------------ #

_UNIT_TESTS = [
    ("PD_BEEP_MASK 値確認",             test_pd_beep_mask_value),
    ("デフォルト設定での初期化",          test_init_default_config),
    ("enable=false で PWM=None",        test_init_disabled),
    ("不正 GPIO で例外なく継続",          test_init_bad_gpio_no_crash),
    ("デューティ比変換",                  test_duty_calculation),
    ("port_write(0x40) → BEEP ON",     test_port_write_0x40_starts_beep),
    ("port_write(0x80) → BEEP ON",     test_port_write_0x80_starts_beep),
    ("port_write(0xC0) → BEEP OFF",    test_port_write_0xc0_stops_beep),
    ("ON 中の 0x80 は duty 再呼出なし",  test_port_write_0x80_no_state_change_when_already_on),
    ("BEEP 無関係ビットは無視",           test_port_write_other_bits_ignored),
    ("disabled 時は例外なし",            test_beep_off_when_disabled),
    ("0x40/0x80→0xC0 完全サイクル",     test_full_beep_cycle),
]


def run_unit_tests():
    passed = 0
    failed = 0
    for name, fn in _UNIT_TESTS:
        try:
            fn()
            print(f"[PASS] {name}")
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\nUnit tests: {passed}/{passed + failed} passed")
    return failed == 0


# ------------------------------------------------------------------ #
# ハードウェア統合テスト（GP14 + パッシブ圧電ブザー接続が必要）         #
# ------------------------------------------------------------------ #

def run_hw_test(gpio_pin=14, freq_hz=1000, duty_pct=50, beep_ms=300, pause_ms=200):
    """
    実機 GP14 で PWM を直接発振させて音が出ることを確認する。

    引数:
        gpio_pin  : 使用 GPIO 番号（デフォルト 14）
        freq_hz   : BEEP 周波数 Hz（デフォルト 1000）
        duty_pct  : デューティ比 % 0-100（デフォルト 50）
        beep_ms   : 1 回の発音時間 ms（デフォルト 300）
        pause_ms  : 発音間の無音時間 ms（デフォルト 200）
    """
    try:
        import machine
    except ImportError:
        print("[HW SKIP] machine モジュールが見つかりません（実機で実行してください）")
        return

    duty_u16 = max(0, min(65535, duty_pct * 65535 // 100))

    print(f"[HW] GP{gpio_pin} で PWM 発振テスト開始: {freq_hz}Hz duty={duty_pct}%")
    pwm = machine.PWM(machine.Pin(gpio_pin))
    pwm.freq(freq_hz)

    patterns = [
        ("BEEP 0 (1000Hz 標準)",  freq_hz,       beep_ms),
        ("BEEP 1 (2000Hz 高音)",  freq_hz * 2,   beep_ms),
        ("短音 x3",               freq_hz,       beep_ms // 3),
    ]

    for label, f, dur_ms in patterns:
        print(f"  → {label} ({dur_ms}ms × {'3' if '×3' in label else '1'})")
        if "×3" in label or "x3" in label:
            for _ in range(3):
                pwm.freq(f)
                pwm.duty_u16(duty_u16)
                time.sleep_ms(dur_ms)
                pwm.duty_u16(0)
                time.sleep_ms(pause_ms // 2)
        else:
            pwm.freq(f)
            pwm.duty_u16(duty_u16)
            time.sleep_ms(dur_ms)
            pwm.duty_u16(0)
        time.sleep_ms(pause_ms)

    pwm.deinit()
    print("[HW] テスト完了。音が聞こえましたか？")
    print("      聞こえた   → GP14 + ブザー配線 OK")
    print("      聞こえない → 配線・パッシブブザー種別・GPIO 番号を確認してください")


# ------------------------------------------------------------------ #
# エントリポイント                                                      #
# ------------------------------------------------------------------ #

def run_all():
    print("=" * 48)
    print("PR10 BEEP ユニットテスト")
    print("=" * 48)
    ok = run_unit_tests()
    print()
    print("ハードウェアテストを実行するには:")
    print("  from test.test_beep import run_hw_test")
    print("  run_hw_test()  # GP14 にブザーを接続して実行")
    return ok


if __name__ == "__main__":
    run_all()
