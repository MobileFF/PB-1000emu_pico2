"""
PIO UART Hardware Loopback Test (GP6 -> GP13)
実際に GP6 (TX) と GP13 (RX) をジャンパ線で結んでテストしてください。
"""
import time
from pio_uart import PioUart

def test_hardware_loopback():
    print("PIO UART Hardware Test Starting...")
    print("Make sure GP6 (TX) and GP13 (RX) are connected together.")
    
    # Initialize UART (GP6/TX, GP13/RX, 9600bps)
    uart = PioUart(tx_pin=6, rx_pin=13, baudrate=9600)
    
    test_message = b"PB-1000 PIO UART Loopback Test! ABCDEFG 12345"
    print(f"Sending: {test_message}")
    
    # 1. データを送信バッファに書き込む
    uart.write(test_message)
    
    # 2. メインループのシミュレート: service_tx() を呼び出して実際に送信
    start_ms = time.ticks_ms()
    received_data = bytearray()
    
    # 送信と受信を並行して行う（タイムアウト30秒: 手入力用）
    timeout_ms = 10000
    while time.ticks_diff(time.ticks_ms(), start_ms) < timeout_ms:
        # 送信処理
        uart.service_tx()
        # 受信処理（内部で service_rx が呼ばれます）
        if uart.any():
            data = uart.read(uart.any())
            if data:
                received_data.extend(data)
                print(f"Current Buffer ({len(received_data)}/{len(test_message)}): {received_data.decode('ascii', 'replace')}")
                # 全データ受信したら終了
                if len(received_data) >= len(test_message):
                    break
        
        # 非常に短いスリープでポーリング頻度を上げる（FIFO溢れ防止）
        time.sleep_ms(1)
    
    print(f"Received: {received_data}")
    
    if received_data == test_message:
        print("\nSUCCESS: Hardware Loopback Verified!")
    else:
        print("\nFAILED: Received data does not match.")
        print(f"Expected length: {len(test_message)}, Received length: {len(received_data)}")

if __name__ == "__main__":
    try:
        test_hardware_loopback()
    except Exception as e:
        print(f"Error during test: {e}")
