"""
サンプル 拡張モジュール

BASIC 使用例:
    CALL &5E20

Work area layout (CALL &5E20):
    [0x5F00]: PB-1000から受け取った値
    [0x5F01]: 拡張モジュールからの応答
"""

CALL_ADDR  = 0x5E20

def register(system):
    """pb1000.py の _ext_load_modules() から呼ばれる。"""
    try:
        system.register_call_hook(CALL_ADDR, lambda: _callback(system))
        print(f"sample ext: hook {CALL_ADDR:#06x} ready")
    except Exception as e:
        print(f"sample ext: init failed: {e}")

def _callback(system):
    """CALL &5E20 ハンドラ: サンプル拡張モジュール"""
    for i in range(100):
        print(f"{i} CALL &5E20 ハンドラ: サンプル拡張モジュール")
    print(f"PB-1000の0x5F00={system._ext_work[0]}")
    system._ext_work[1] = system._ext_work[0]+1    
    print(f"PB-1000の0x5F01={system._ext_work[1]}")
    for i in range(100):
        print(f"{9-i} CALL &5E20 ハンドラ: サンプル拡張モジュール")
    

