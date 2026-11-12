"""esptool wrapper that paces USB writes.

Why: this board's CH343 USB-serial chip on macOS (Apple's built-in CDC-ACM
driver) drops bytes when the host sends big bursts, so normal esptool writes
fail with "0105: The format of the received message is invalid" / stub
"Checksum error". Sending 64-byte bursts with ~1 ms gaps at 921600 baud and
using the ROM loader (--no-stub) is reliable (~300 kbit/s, MD5-verified).

Usage: python paced_esptool.py <normal esptool args>
Tunables via env: CHUNK (bytes per burst, default 64), GAP (seconds, default 0.001)
"""
import os, time, serial, esptool

CHUNK = int(os.environ.get("CHUNK", "64"))
GAP = float(os.environ.get("GAP", "0.001"))
_orig_write = serial.Serial.write

def paced_write(self, data):
    n = 0
    for i in range(0, len(data), CHUNK):
        n += _orig_write(self, data[i:i + CHUNK])
        self.flush()
        time.sleep(GAP)
    return n

serial.Serial.write = paced_write

if __name__ == "__main__":
    esptool.main()
