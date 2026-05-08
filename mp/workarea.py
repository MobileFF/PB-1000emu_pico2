WORKAREA_DIC = {
    # name :(addr,len)
    "KYSTA":(0x68D2,1),
    "IOBF" :(0x692F,2),
    "SSTOP":(0x6931,2),
    "SBOT" :(0x6933,2),
    "FORSK":(0x6935,2),
    "GOSSK":(0x6937,2),
    "TONDT":(0x6939,2),
    "DTTB" :(0x693B,2),
    "TOSDT":(0x693D,2),
    "PTSDT":(0x693F,2),
    "HIMEM":(0x6941,2),
    "BASEN":(0x6943,2),
    "MEMEN":(0x6945,2),
    "DATDI":(0x6947,2),
    "BASDI":(0x6949,2),
    "DIREN":(0x694B,2),
    "ACJMP":(0x694D,2),
    "OPTCD":(0x6BFA,1),
}

def peek_workarea(system,name):
    wa_addr = WORKAREA_DIC[name][0]
    wa_len  = WORKAREA_DIC[name][1]
    result = bytearray(wa_len)
    for i in range(wa_len):
        result[i] = system._mem_read_impl(0, wa_addr+i)
    return result
    
def print_workarea(system,name):
    print(f"{name}({WORKAREA_DIC[name][0]:04X}) = ",end="")
    ba = peek_workarea(system,name)
    if (len(ba)==2):
        print(f"{ba[1]:02X}{ba[0]:02X}({ba})")
    else:
        for i in ba:
            print(f"{i:02X} ",end="")
        print()

def print_all_workarea(system):
    for i in WORKAREA_DIC:
        print_workarea(system,i)
