"""R-3: 論文の blob2 = 399,544 B を制約にして Gγ の逆算を再検証する。

量子化スキーム（論文の記述。うちは M-39 で実装済み）:
  重み int8 / per-output-channel の fp32 scale / embedding・norm affine・1-D は fp32
  blob = W_int8 + 4*B_fp32 + 4*C_scale,  W + B = P
  ⇒ blob = P + 3B + 4C          ... (★)
"""
P2, BLOB2 = 331308, 399544
CIN, OUT = 40, 1539
NEED = BLOB2 - P2
print(f"(★) 必要条件:  3B + 4C = {NEED:,}")

W_o, B_o, C_o = 327300, 16032 // 4, 17532 // 4
print(f"うちの案C: W={W_o:,} B={B_o:,} C={C_o:,}"
      f"  → 3B+4C = {3*B_o+4*C_o:,}  (不足 {NEED-(3*B_o+4*C_o):,} B)")
print(f"           blob2 実測 = {W_o+4*B_o+4*C_o:,} B  (M-39 の 360,864 B と一致)\n")

# --- 論文が明示している構成: width 76 / kernel-7 block ×5 / pointwise 拡張 304 / rank-12 ---
W, E, K, R, NB = 76, 304, 7, 12, 5
base_C = W + NB*(W + E + W + R + W)          # 量子化層の出力ch（head を除く）
base_B = W + NB*(E + W) + NB*(R + W)         # bias（head・LayerScale・norm を除く）
print(f"論文明示の W=76/E=304/K=7/R=12/5ブロック 固定時:  base_B={base_B:,} base_C={base_C:,}")
print(f"  → 3*base_B + 4*base_C = {3*base_B+4*base_C:,}")
print(f"  head + LayerScale + norm affine で埋めるべき残り = {NEED-(3*base_B+4*base_C):,}\n")

rows = []
for naff in (0, 1, 2, 3):                    # ブロックあたりの正規化層数（affine 2W）
    for gper in (0, 1):                      # LayerScale: スカラ / チャネルごと
        g = NB*(W if gper else 1)
        for head, rmax in (("dense", 0), ("fact", 4000)):
            rs = [0] if head == "dense" else range(1, rmax)
            for r in rs:
                hb = OUT if head == "dense" else r + OUT
                hc = OUT if head == "dense" else r + OUT
                B = base_B + g + naff*NB*2*W + hb
                C = base_C + hc
                if 3*B + 4*C == NEED:
                    rows.append((naff, gper, head, r, B, C))
print(f"3B+4C == {NEED} を満たす (naff, gamma粒度, head, r): {len(rows)} 件")
for naff, gper, head, r, B, C in rows[:20]:
    # そのときの総パラメータ数
    P = CIN*W*3 + W + NB*(W*K) + NB*(W*E+E) + NB*(E*W+W) + NB*(CIN*R+R) + NB*(R*W+W)
    P += NB*(W if gper else 1) + naff*NB*2*W
    P += (W*OUT + OUT) if head == "dense" else (W*r + r + r*OUT + OUT)
    print(f"  naff={naff} gamma_per_ch={gper} head={head} r={r}"
          f"  → B={B:,} C={C:,}  総params={P:,} (Table I {P2:,} との差 {P-P2:+,})")
print("\n→ どれも Table I の 331,308 を満たさない。"
      " head rank / norm affine / LayerScale 粒度では 38,680 B を埋められない。")

# 全モデルでの検算
print("\n=== 参考: モデル全体での同じ検算 ===")
P_all, BLOB_all = 567008, 679832
B_ja = (8848 + 33984 + 16032)//4; C_ja = (772 + 3232 + 17532)//4
P_ja, blob_ja = 559008, 624692
print(f"うち(V=57):  P={P_ja:,} B={B_ja:,} C={C_ja:,} blob={blob_ja:,}")
B_en = B_ja + (157-57)*(32+48)               # 埋め込みは fp32 のまま増える
blob_en = (P_all - B_en) + 4*B_en + 4*C_ja
print(f"うちを V=157 に戻すと: P={P_all:,} B={B_en:,} blob={blob_en:,} B"
      f"   論文 {BLOB_all:,} B との差 {blob_en-BLOB_all:+,} B")
print(f"  内訳: blob1 {263828+ (157-57)*(32+48)*4:,} B (論文 280,288 / 差 "
      f"{263828+(157-57)*(32+48)*4-280288:+,})   blob2 360,864 B (論文 399,544 / 差 -38,680)")
