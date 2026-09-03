"""実機（USB Serial/JTAG）を reset し、`かな>` プロンプトに行を送ってログを保存する。

    uv run --no-project --with pyserial python scripts/dev_run.py --out /tmp/demo.log \
        --line '今日は良い天気ですね。' \
        --line 'きょ][おわよ][いて][んきです°ね'

各行の後、`----- 結果 -----` のブロックが出て `かな>` が戻るまで待つ（最大 --settle 秒）。
最後に checksum / xRT / 段別プロファイルの行を抜き出して標準出力へ出す。

⚠️ **`--port` の既定は `/dev/cu.usbmodem2101`**（手元の CoreS3）。自分の板に合わせること。
⚠️ **1 行を 64 B ずつに切って送る。** USB Serial/JTAG ドライバの RX リングは既定 256 B で、
   一気に書くと溢れて行が欠ける（実機で踏んだ）。⚠️ **UART0 のビルドには使えない**
   （リセットの叩き方が違う）。
⚠️ **`!` の前置は要らない**（v0.3.0 以降。経路は端末の `saan_g2p_classify()` が決める）。

依存は pyserial だけ。**リポジトリの環境（uv sync）は要らない。**
"""
import argparse, re, sys, time
import serial

ap = argparse.ArgumentParser()
ap.add_argument("--port", default="/dev/cu.usbmodem2101")
ap.add_argument("--baud", type=int, default=115200)
ap.add_argument("--out", required=True)
ap.add_argument("--line", action="append", default=[])
ap.add_argument("--repeat", type=int, default=1)
ap.add_argument("--boot-wait", type=float, default=12.0, help="reset 後、プロンプトを待つ最大秒")
ap.add_argument("--settle", type=float, default=40.0, help="1 行送ってから結果を待つ最大秒")
ap.add_argument("--no-reset", action="store_true")
args = ap.parse_args()

ANSI = re.compile(r"\x1b\[[0-9;]*m")
buf = bytearray()
log = open(args.out, "w", encoding="utf-8")
t0 = time.time()

def pump(ser, until=None, timeout=10.0):
    """until（正規表現）がバッファの末尾側に現れるか timeout まで読み続け、読んだテキストを返す"""
    global buf
    deadline = time.time() + timeout
    seen = ""
    while time.time() < deadline:
        n = ser.in_waiting
        chunk = ser.read(n if n else 1)
        if chunk:
            buf += chunk
            try:
                text = buf.decode("utf-8")
                buf = bytearray()
            except UnicodeDecodeError:
                # 途中で切れたマルチバイト。次回に持ち越す
                cut = len(buf)
                while cut > 0 and (buf[cut - 1] & 0xC0) == 0x80: cut -= 1
                text = buf[: max(cut - 1, 0)].decode("utf-8", "replace")
                buf = buf[max(cut - 1, 0):]
            text = ANSI.sub("", text).replace("\r", "")
            seen += text
            log.write(text); log.flush()
            if until and re.search(until, seen[-4000:]):
                return seen
        else:
            time.sleep(0.02)
    return seen

ser = serial.Serial(args.port, args.baud, timeout=0.05)
if not args.no_reset:
    # USB Serial/JTAG の自動リセット: RTS=1 & DTR=0 → 解放
    ser.dtr = False; ser.rts = True; time.sleep(0.15); ser.rts = False
    time.sleep(0.05)
boot = pump(ser, until=r"かな> ?$", timeout=args.boot_wait)
print(f"[boot] {len(boot)} chars, prompt={'yes' if re.search(r'かな> ?$', boot[-200:]) else 'NO'}", file=sys.stderr)

results = []
for rep in range(args.repeat):
    for line in args.line:
        data = (line + "\n").encode("utf-8")
        for i in range(0, len(data), 64):      # USB-JTAG ドライバの RX リングは既定 256 B。バーストで溢れるので 64 B ずつ
            ser.write(data[i:i + 64]); ser.flush(); time.sleep(0.03)
        out = pump(ser, until=r"かな> ?$", timeout=args.settle)
        results.append((rep, line, out))
        print(f"[{rep}] {line[:24]!r} → {len(out)} chars", file=sys.stderr)
ser.close(); log.close()

pat = re.compile(r"(0x[0-9a-f]{16}|\|max\||Σx²|xRT|pull \d+|アンダーラン|内部 DRAM|free|GELU|MAC|TOKEN|STEP|区間|漢字 G2P|経路|辞書|PSRAM|arena)")
for rep, line, out in results:
    print(f"===== [{rep}] {line}")
    for l in out.splitlines():
        if pat.search(l): print("  " + l.strip()[:200])
print(f"[done] log={args.out} elapsed={time.time()-t0:.1f}s", file=sys.stderr)
