"""HD61700 CPU Test Common Infrastructure
Shared by all per-opcode-range test files.
"""
import hd61700
import gc

TB = 0x7000          # Test Base address
_A = []              # Anchor list for GC protection

# Singleton T instance - reused across all test files
_shared_t = None

class T:
    def __init__(self):
        _A.append(hd61700._init_anchor())
        # Use C-side memory (no Python RAM buffers needed)
        hd61700.use_c_memory(True)
        # LCD dummy
        self.lcd_buf = []
        self._lr = self._lcd_read
        self._lw = self._lcd_write
        self._lc = self._lcd_ctrl
        _A.extend([self._lr, self._lw, self._lc])
        hd61700.set_lcd_callbacks(self._lr, self._lw, self._lc)
        # Port dummy
        self.port_out = 0
        self._pr = self._port_read
        self._pw = self._port_write
        _A.extend([self._pr, self._pw])
        hd61700.set_port_callbacks(self._pr, self._pw)
        self.rst()

    def rst(self):
        hd61700.reset(False)
        hd61700.use_c_memory(True)
        hd61700.set_debug(False)
        hd61700.set_key_debug(False)
        hd61700.set_lcd_debug(False)
        for i in range(32): hd61700.set_reg(i, 0)
        for i in range(3): hd61700.set_sreg(i, i)
        for i in range(8): hd61700.set_reg16(i, 0)
        self.lcd_buf = []
        self.port_out = 0

    def _lcd_read(self):
        if self.lcd_buf:
            return self.lcd_buf.pop(0)
        return 0xEE

    def _lcd_write(self, d):
        self.lcd_buf.append(d & 0xFF)

    def _lcd_ctrl(self, d):
        self.port_out = d

    def _port_read(self):
        return self.port_out & 0xFF

    def _port_write(self, d):
        self.port_out = d & 0xFF

    def lc(self, addr, code):
        for i, b in enumerate(code):
            hd61700.write_mem(addr + i, b)

    def ex(self, code, setup=None, pc=TB, stop=None):
        self.rst()
        self.lc(pc, code)
        hd61700.set_pc(pc)
        if setup: setup()
        sp = stop if stop else pc + len(code)
        hd61700.execute(500, sp)


def get_t():
    """Get or create the shared T instance."""
    global _shared_t
    if _shared_t is None:
        _shared_t = T()
    return _shared_t


def R(n): return hd61700.get_reg(n)
def F(): return hd61700.get_flags()
def M(a): return hd61700.read_mem(a)
def PC(): return hd61700.get_pc()
def R8(n): return hd61700.get_reg8(n)
def R16(n): return hd61700.get_reg16(n)
def SR(n): return hd61700.get_sreg(n)
def SLEEP(): return hd61700.is_sleeping()

def cr(r, e):
    return lambda: (R(r)==e, "R%d=%02X!=%02X" % (r, R(r), e))
def cr16(r, e):
    return lambda: (hd61700.get_reg16(r)==e, "R16_%d=%04X!=%04X" % (r, hd61700.get_reg16(r), e))
def cm(a, e):
    return lambda: (M(a)==e, "[%04X]=%02X!=%02X" % (a, M(a), e))
def cf(z=None, c=None, lz=None, uz=None):
    def _ck():
        f = F(); e = []
        if z is not None and bool(f&0x80)!=z: e.append("Z=%s" % bool(f&0x80))
        if c is not None and bool(f&0x40)!=c: e.append("C=%s" % bool(f&0x40))
        if lz is not None and bool(f&0x20)!=lz: e.append("LZ=%s" % bool(f&0x20))
        if uz is not None and bool(f&0x10)!=uz: e.append("UZ=%s" % bool(f&0x10))
        return (len(e)==0, ",".join(e)) if e else (True,"")
    return _ck
def cp(e):
    return lambda: (PC()==e, "PC=%04X!=%04X" % (PC(), e))
def cr8(idx, e):
    return lambda: (R8(idx)==e, "REG8[%d]=%02X!=%02X" % (idx, R8(idx), e))
def csr(idx, e):
    return lambda: (SR(idx)==e, "SR%d=%d!=%d" % (idx, SR(idx), e))
def cslp(e):
    return lambda: (SLEEP()==e, "SLP=%s!=%s" % (SLEEP(), e))


def run_group(t, label, tests_fn):
    print("\n[%s]" % label)
    total_p = 0; total_f = 0

    def check(name, code, setup, checks, stop=None):
        nonlocal total_p, total_f
        try:
            t.ex(code, setup, stop=stop)
            ok = True; msg = ""
            for c in checks:
                r = c()
                if r is not None and not r[0]:
                    ok = False; msg = r[1]; break
            if ok:
                print("  %-40s OK" % name)
                total_p += 1
            else:
                print("  %-40s NG (%s)" % (name, msg))
                total_f += 1
        except Exception as e:
            print("  %-40s EX (%s)" % (name, e))
            total_f += 1

    tests_fn(t, check)
    gc.collect()
    return total_p, total_f
