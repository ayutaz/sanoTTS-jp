#!/usr/bin/env python3
"""実機（M5 CoreS3）をリセットして起動ログを取り、文を打って応答を取る。

使い方: serial_probe.py <out.log> [文 ...]
"""
import sys, time, serial

PORT = "/dev/cu.usbmodem2101"
BAUD = 115200

def main():
    out = sys.argv[1]
    texts = sys.argv[2:]
    s = serial.Serial(PORT, BAUD, timeout=0.2)
    # リセット（USB-serial-JTAG / CP210x どちらでも効く形）
    s.setDTR(False); s.setRTS(True); time.sleep(0.15)
    s.setRTS(False); time.sleep(0.05)
    s.reset_input_buffer()
    buf = bytearray()

    def drain(sec):
        t0 = time.time()
        while time.time() - t0 < sec:
            d = s.read(4096)
            if d:
                buf.extend(d)
                sys.stdout.write(d.decode("utf-8", "replace"))
                sys.stdout.flush()
            else:
                time.sleep(0.02)

    drain(20.0)                     # 起動 + 辞書 mmap + 起動時の 1 発話
    for t in texts:
        s.write((t + "\r\n").encode("utf-8"))
        s.flush()
        drain(18.0)
    s.close()
    with open(out, "wb") as f:
        f.write(bytes(buf))
    print(f"\n--- {len(buf)} B を {out} に保存 ---")

main()
