# sanoTTS-jp ドキュメント

arXiv:2608.21378 "sanoTTS" の蒸留レシピを日本語に適用し、**ESP32 上で動く
日本語 TTS** を作るプロジェクトの調査・設計・実測記録。

⚠️ **「まず動かしたい」なら、ここではなく [`../README.md`](../README.md) の「はじめかた」から。**
セットアップ（`uv sync` の前に piper-plus を向け直す）と、音を出すまでの最短手順がある。
実機に載せるのは [`../esp32/TESTING.md`](../esp32/TESTING.md)。

**現在地（2026-09-03）**: **速度の要件に届き**（満チャンク 1 pull の xRT **0.446**。M-90）、
**スタックチャン（M5 CoreS3）で漢字・カタカナ・ひらがなを喋る**ところまで来た。
**残っているのは聴取（G32）だけで、それは人を待っている。**
⚠️ **この音はまだ誰も聴いていない。** 指標（SCOREQ / DNSMOS）は**誤読もアクセント誤りも罰しない**。

## 読む順序

| # | ドキュメント | 内容 | 更新頻度 |
|---|---|---|---|
| 0 | [`../CLAUDE.md`](../CLAUDE.md) | 実装時の要点だけを抜き出した運用ルール。**コードを書く前に必ず読む** | 実測のたび |
| 0.5 | [`requirements.md`](requirements.md) | **要件定義書**。入力仕様・機能/非機能要件・受け入れ条件 | 仕様変更時 |
| 1 | [`decisions.md`](decisions.md) | 意思決定の記録 D-001〜D-048 と**訂正履歴 C-001〜C-056** | 決定のたび |
| 2 | [`measurements.md`](measurements.md) | **実測値の一次ソース** M-1〜M-91。全数値に再現コマンド付き | 実測のたび |
| 3 | [`plan/phase0-1-implementation-plan.md`](plan/phase0-1-implementation-plan.md) | 作業計画（かなトラック）。B-0〜B-12 の検証タスクと Phase 0〜D の状態。**§10 の P-1/P-2/E-1/E-2 は全部決着したので、いまはほぼ履歴** | 固定 |
| 2.5 | [`upstream-sanotts.md`](upstream-sanotts.md) | **公式実装 `Ampixa/sanoTTS` から得た事実**（GPL-3.0）。⚠️ すべて**上流の申告値で未再現**。ソースコードは読まない | 上流を見たとき |
| 4 | [`research/b0-g2p-footprint.md`](research/b0-g2p-footprint.md) | B-0 の結論レポート。辞書枝刈りが不成立と判定した根拠 | 固定 |
| 4.5 | [`research/k1-kanji-katakana-ondevice.md`](research/k1-kanji-katakana-ondevice.md) | **K-1 の結論レポート**。B-0 の否定的結論のうち 4 つが崩れた。辞書は mmap / TTS 専用バイナリで 1 エントリ 28.29 B / アクセント天井は 126 行。**§0 に「その後どうなったか」**（実装で分かったずれ 3 件） | 固定（§0 だけ追記） |
| 4.6 | [`plan/k1-kanji-implementation-plan.md`](plan/k1-kanji-implementation-plan.md) | **K トラックの実装計画**。K-0〜K-8 に目的・ゴール・受け入れ条件（G1〜G32。⚠️ G15/G16 は欠番）。**K-8 まで完了**（M-83 / M-90）。残りは **G32 聴取**と、エントリ数・接続行列の判断 | 固定（判断待ち 2 件） |
| 4.7 | [`research/s1-m5-cores3-speed.md`](research/s1-m5-cores3-speed.md) | **S-1: 実機で初めて速度が出た**（第三者の M5Stack CoreS3 報告 W8A8+PIE **1.55× RT**。⚠️ 未再現・S1 前）。1 step の内訳をホスト + QEMU で取り、**QUANT / GELU / LOOKUP / WCOPY が MAC と同等以上**と分かった（M-80）。⚠️ **§4 の仮説は半分が外れた**（C-054。§5 は「直し方」で、そちらは全部入った） | 固定 |
| 4.9 | [`plan/s2-fast-kanji-m5-plan.md`](plan/s2-fast-kanji-m5-plan.md) | **いちばん新しい計画**。⚠️ **§10 に S-1（M5Unified 対応 A-0〜A-5 / 速度 S1〜S5a）の前史**を畳んである（旧 `plan/s1-speed-implementation-plan.md` は削除）。T1（末尾 pull の早期終了）/ T2（S9）/ T3（S6）/ T4（arena）/ T5（GELU）/ 64 B 行 と、M5 への漢字搭載。**要件 RTF ≤ 0.5 を達成して完了**（M-88 → M-90）。残りは聴取 | 固定 |
| 5 | [`research/sanotts-jp-feasibility.md`](research/sanotts-jp-feasibility.md) | 初期調査。論文の全数値と piper-plus の資産棚卸し。⚠️ 結論の一部は更新済み | ほぼ固定 |

**数値が食い違ったら [`measurements.md`](measurements.md) が正**。
他のドキュメントはそこからの引用または解釈として扱う。

⚠️ 例外は [`upstream-sanotts.md`](upstream-sanotts.md)。**あれは上流の申告値であって、うちの実測ではない。** M-番号と混ぜないこと。

## 現在地（2026-09-03 時点）

```
[完了] 論文の仕様抽出        論文 PDF から全数値を抽出
[完了] 教師 ckpt の特定       HF private 61 repo を調査 → 1 件に確定
[完了] 教師の動作確認         v2.0 に missing 0 / unexpected 0 でロード、決定的推論が bit 再現
[完了] 音素化経路の確定       canonical 経路を特定、発話速度 8.4 mora/s を確認
[完了] スコープ確定           ESP32 のみ。ブラウザは対象外
[完了] B-0 G2P フットプリント  辞書路線は不成立と判明（40 MiB 必要）
[完了] 入力仕様の確定         ひらがな + アクセント記号 + 無声化マーク → 端末 877 B
[完了] 変換器の実装           scripts/kana_g2p.py。往復 100%、教師出力と bit 一致
[完了] 要件定義              requirements.md
[完了] ESP32 メモリ収支      I2S 逐次出力で 96 KB / SRAM 残 416 KB。中止材料なし
[完了] B-5 教師品質ベースライン UTMOS 1.748 / 実人間 2.305 → 比 0.758。指標の較正ずれを補正
[完了] Phase A              ラベル生成の設計確定（入力経路 / prosody / パック形式）
[完了] Phase B              パイプライン実装 + 本番実行（手元の CPU で 47 分）
[完了] Phase C              生徒 4 段の学習ループ。スモークテスト全通過
[完了] B-4/6/7/8/9/10/12    検証タスク完走。D-016〜D-031 として設計値を凍結
[完了] SCOREQ 導入           論文の主指標。教師 2.0488 / 実人間 2.4983 → 比 0.820
[完了] 本番ラベルパック      train 20,790 / heldout 2,314 発話。SHA-256 固定（M-35）
[完了] 本学習 v1/v2         **SCOREQ 教師比 0.611（論文の英語比 0.5427 を超過）**（M-36 / M-37）
[完了] CER                  かな CER 教師 0.135 / 生徒 0.178（M-38）
[完了] int8 量子化           blob 624,692 B（論文比 −8.1%）（M-39）
[完了] β スイープ            β=0 と 2 が候補（M-40）→ **聴取で β=0 に確定、式7 は不要**（M-60 / D-038）。
                            ⚠️ **聴取者 1 名**
[完了] Phase D-1            C99 コア。Pearson 1.000000 / SNR 117.5 dB（M-41）
[完了] Phase D-2            **ストリーミング化。1,258→196.9 KB で SRAM に載った**（M-42）
[完了] Phase D-3a/b/c       FFT 1,435 倍 / 手元 **0.023× RT** / int8 カーネル（M-43）
[完了] Phase D-3c'-1/2      **int8 end-to-end**。fp32 比 平均 25.88 dB / ブロブ −71.4%（M-45）
[完了] Phase D-3c'-4        ESP-IDF 雛形。M-46 の時点では未ビルド → ✅ **M-56 でビルド成功**（267,968 B）/
                            **M-62 で QEMU 完走** / **M-82 以降で実機**（CoreS3）
[完了] D-4                  アクセント型ミニマルペア。符号一致 35/36 で**再現している**（M-44 / v2）
                            ⚠️ v3 では **37/37**（M-59）。下の「v3 に差し替え」を見ること
[完了] B-12                 教師の事前学習との重複検査。**看板の 24 文は汚染ゼロ**（M-47）
[完了] 敵対的検証            空虚に通るゲート 2 件 + silent failure 1 件を修正（M-48）
[完了] E-1 DNSMOS           生徒/教師 **0.7727**（M-50 / v2）。上流の主張は**再現せず**
                            ⚠️ v3 では **0.7969**（M-61。旧記録 0.8385 は誤り）
                            ⚠️ **陽性対照 G6 は FAIL**。DNSMOS が下がらない＝劣化が無い、ではない（M-50 / D-034）
[完了] E-2 decoder 教師初期化 **定義できない**（27/28 は形状一致だが意味が対応せず、hout は候補ゼロ）
                            代わりにギャップを分解: decoder 0.395 / acoustic 0.283 / duration 0.052（M-49 / D-033）
[完了] E-2b 幅スイープ        **`Gγ` は容量律速ではない**。params +43% で Δ +0.0006（CI が 0 を含む）
                            効くのは学習量: 20k→40k で −0.1090（ノイズ床 0.0812 超え）（M-52）
[完了] ライセンス再調査        一次ソースで**記録 3 件を訂正**。CV は CC0 確定、つくよみちゃんは
                            **モデル配布を明示的に許可**。**継承の障害は最初から無かった**（D-035 / C-029〜031）
[完了] ESP-IDF 導入           **「toolchain 待ち」は誤りだった**。v5.5 を導入し、C99 コア 5 ファイルが
                            厳密 `-std=c99` で ESP32-S3 向けに通過。⚠️ `M_PI` の移植性バグを 1 件発見（M-54 / C-033）
[中止] E-2c W=56 が無料か     結果がどちらでも打つ手が変わらないため中止（D-036）
[完了] W8A8 の知覚評価         **知覚的に無料**。SCOREQ 差 +0.0049（CI が 0 を含む）。
                            ⚠️ SNR ゲートは落ちる（23.24 dB）が知覚に対応していない。陽性対照つき（M-55）
[完了] QEMU で PIE 検証        **`ee.vmulas.s8.accx` が正しく動く**（乱数 200 回で不一致 0、陰性対照つき）。
                            ESP-IDF 雛形も**初めてビルド成功**（267,968 B）（M-56）
[完了] 成果物を v3 に差し替え  **Stage 3 を 40k → 80k にしただけで 5 指標すべて改善**（M-59 / D-037）
                            SCOREQ 比 0.6113→**0.6444** / DNSMOS 比 0.7727→**0.7969**
                            アクセント 35/36→**37/37** / int8 最小 SNR 23.27→**25.72 dB**
                            ⚠️ かな CER は +0.0425→+0.0320 で**改善を検出できない**（M-61 で訂正）
                            ⚠️ **160k は無駄**（最終品質は v3 と有意差なし）。**80k が最適点**
[完了] P-1 PIE カーネル       `saan_conv1d_i8a` の内積を `ee.vmulas.s8.accx` で書き直した。
                            **QEMU で bit 完全一致**（陰性対照つき）（M-57）
[完了] PIE を全層へ拡張        活性化ストライドを align16(cin) にパディング。**69.0% → 99.40%**（M-58）
                            出力は 24/24 で bit 一致、arena 増加 0 B。⚠️ 0.60%（depthwise）は原理的に不可
                            ✅ **出荷ファームの既定にした**（D-048。ESP32-S3 ではフラグ無しで有効。
                            W8A32 は xRT 4.28〜4.62 で間に合わない）
                            ✅ 速度は自分で測った: CoreS3 で W8A8+PIE **0.926× RT**（M-82。下の 2026-09-02 の行を見ること）
[完了] P-2 β の聴取決定      **β=0 で確定。式7 は不要**（M-60 / D-038）。⚠️ 上流（英語）は 6.0
                            ⚠️ **聴取者 1 名**。v2↔v3 も 7 試行で聴き分けられず（C-037）
[完了] QEMU で出荷ファーム完走 起動 → 重み mmap(183 tensors) → G2P → 合成 → int16 まで通り、
                            **PIE が全経路で bit 一致**（27,136 sample・陰性対照つき）（M-62）
                            ⚠️ ホストとターゲットは bit 一致しない。それは正常（float の丸め）
[完了] 端末でかなの自由入力    UART / USB-JTAG から 1 行受けて合成。QEMU の UART で実測（M-63 / D-040）
                            ⚠️ **音では気づけない欠陥を 2 件出した**（CRLF で空行 / 行頭が
                            エコーされない）。どちらも呼び出し側の読み違い。G9 / G10 で固定
                            ⚠️ 起動時の 1 発話はやめた（既定 SAAN_BOOT_SPEAK=0。C-039）
[完了] 外の人が動かせる状態に  **piper-plus も教師も無しで合成できる**（M-64 / M-65 / D-041）。
                            mora テーブルを csrc/g2p_table.json に凍結（SHA-256 検証つき）
                            ⚠️ **リリース v0.1.0 の手順は動かなかった**（C-040）
                            ⚠️ 「piper-plus 無しで通った」と**誤って観測**した（C-041）
[完了] **v0.3.0 リリース**      **スタックチャン（M5 CoreS3）の配布イメージ**を追加。焼いた実機で確認済み（M-90 / M-91）。
                            ⚠️ **モデルは v0.1.0 から bit 同一**（再学習していない）。変わったのは端末のコード。
                            ✅ **v0.2.0 の欠陥 2 件を直した**: int8 blob が v1 で現行コアに拒まれる（→ **v2 / 654,032 B**）／
                            コンソールが UART0 のみで native USB の板を操作できない（→ **USB Serial/JTAG 版を併配**）
[完了] **v0.2.0 リリース**      **端末で漢字かな交じり文を扱う firmware**を追加（M-76 / M-79）。
                            ⚠️ **モデルは v0.1.1 と bit 同一**（再学習していない）。
                            ✅ **CoreS3 で実機確認**（M-83。v0.2.0 コードで checksum が QEMU と一致）。
                            ⚠️ **配布イメージは UART0 入力**で native USB だけの板では操作できない
                            ⚠️ 出荷構成では DRAM が 19,304 B 溢れた
                            （`LABEL_IDS_MAX_TOKENS` 2048 → 640 で解決。M-79）
[完了] v0.1.1 リリース        手順書を新規 clone でなぞって欠陥 2 件を修正（M-66）。
                            **焼くだけの ESP32 firmware 2 種**を追加（M-67）。
                            ⚠️ モデルは v0.1.0 と bit 同一（再学習していない）
[報告] 実機の速度（第三者 2 件） CoreS3 で **W8A8+PIE 1.554× RT / W8A32 4.834× RT**、AtomS3 で **1.718× RT**（2026-09-01〜02、
                            **私は未再現・S1 前の値**）。checksum は M-62 と一致。実時間に**間に合っていなかった**
[完了] 段別プロファイラ        `csrc/saan_prof.h`。QEMU + ホストで **QUANT/GELU/LOOKUP/WCOPY ≥ MAC**（M-80）。
                            実機の内訳は `idf.py -DSAAN_PROFILE=1` で取れる
[完了] M5Unified 対応          `esp32/boards/m5unified/`（CoreS3 / Core2）。音声出力を saan_audio API に、
                            重みを .rodata に埋める経路、SAAN_BUFFERED、タッチ再生（A-1〜A-3 / A-5）
                            ⚠️ ESP32（Core2）は arena が PSRAM に行く（遅い）
[完了] 速度 S1〜S5a            検索を init で 1 回に / 量子化を逆数乗算 + round.s / GELU を Hermite 表 /
                            blob v2（事前整列）/ PIE のロード併合。**QEMU 命令数比で 1 step −49%**（M-81 / D-046）
                            ⚠️ S3 で基準 checksum が変わった（W8A8+PIE `0xa69a7ebbb5ccb05f` / W8A32 `0xe4b645c30835d42d`）
                            ⚠️ テストの潜在バグ 2 件が表面化（int8_test の作業領域。C-053）
[完了] A-0 板の同定            ユーザーのスタックチャンは **ESP32-S3 / 16 MB / native USB**（CoreS3 系。**D-047**）。
                            焼く前に 16 MB を丸ごと backup した
[完了] Phase D-3d / A-4 実機   CoreS3 で W8A8+PIE **0.926× RT**（M-82）。⚠️ この時点で目標 RTF ≤ 0.5 は未達
[完了] 実機で仮説を検算        **M-82 §4 の 4 つのうち半分が外れた**（C-054）。64 B キャッシュ行で **−7.0%**（M-84）、
                            `SPIRAM_RODATA` は **−0.9% しか効かない**。pie_probe D/E 節で
                            **dot の固定費 78 cyc/dot** と **flash 259 cyc/32 B 行 = 38.5%**、
                            **GELU は表の配置ではなく非インライン化が原因**（M-85）
[完了] S2 計画 T1〜T5          T1（末尾 pull の早期終了）/ T2（S9 = 捨てる出力を計算しない）/ T3（S6 = token のパイプ化）/
                            T4（arena の詰め）/ T5（GELU のコード生成）。**1 step 18.38 M → 11.66 M cyc**
                            ⚠️ T5 で **GELU が 2 倍遅くなったまま通りかけた**（C-055 / M-87）
[完了] **要件 RTF ≤ 0.5 達成**   満チャンク 1 pull の xRT **0.497**、アンダーラン **0**、鳴らし始め 434 ms（M-88。かな構成）。
                                 ⚠️ **出荷構成（M5 + 漢字 + S5b）は xRT 0.446 / 鳴らし始め 384 ms**（M-90）。
                            T4 で内部 DRAM の空きが **+36,420 B**（M-89）
                            ⚠️ **発話全体で見ると 0.550〜0.693** でまだ 0.5 超（分母は未決 = D-049）
[完了] D-048                  **ESP32-S3 では W8A8 + PIE を既定にする**（フラグ無しで有効）
[完了] **スタックチャンで喋った** 辞書 + 漢字 + W8A8/PIE + T1〜T5 + S5b を 1 本のファームに載せ、
                            **漢字・カタカナ・ひらがなを M5.Speaker へ**（M-90）。xRT **0.446** /
                            内部 DRAM の空き 132,039 B / 辞書 13.7 MB を `esp_mmu_map` で貼る
                            ⚠️ **音は聴いていない**（G32）
```

**(1) の残りは深い聴取だけ。** ざっとした聴取は済んだ（M-91。ユーザーが「音は正しかった」。⚠️ 1 名・対照なし）。
残るのは **`reports/k8_listen/` の 12 組**（枝刈りで読みが変わるペア）と
**アクセントの過剰強調** `magnitude_ratio` 1.193（`reports/d4_accent/`）の確認。
実機・S5b・D-048 は片づいた。計画は [`plan/s2-fast-kanji-m5-plan.md`](plan/s2-fast-kanji-m5-plan.md)
（前段の S-1 計画は同文書 **§10「前史」**に畳んだ）。

---

## 残っているタスク（**2026-09-03 更新。対照つきの聴取だけ**）

| # | 何 | トラック | 種類 | ゲート |
|---|---|---|---|---|
| **1** | **対照つきの聴取** — ざっとは聴かれた（M-91。⚠️ 1 名・対照なし・盲検なし）が、**`reports/k8_listen/` の 12 組**と**アクセントのミニマルペア**は未聴取 | 両方 | **人が要る** | **G32** |
| 2 | RTF の分母（満チャンク 1 pull = 0.446 で達成 / 発話全体 = 0.54〜0.71 で未達） | かな | 判断 | **未決。D-049 で決める**（[`requirements.md`](requirements.md) §6.2） |
| ~~3~~ | ~~リリース資産の blob を v1 → v2 に上げる~~ | かな | ✅ **v0.3.0** | `scripts/check_release_assets.py` |
| ~~4~~ | ~~配布イメージを USB Serial/JTAG 入力でも配る~~ | 両方 | ✅ **v0.3.0** | `esp32/TESTING.md` |
| 5 | エントリ数・接続行列（今は 438,750 / int16） | K | 判断 | D-044 を見直すか |

✅ **「音の測定」は済んだ**（M-78）。実機は要らなかった — 端末の ids はホストの
生徒モデルにそのまま入るので、**端末の音とホストの音を直接比べられる**。
**SCOREQ の差は −0.0019（全体）/ −0.0127（違う 44 文だけ）で、どちらも CI が 0 を跨ぐ。**
⚠️ **枝刈りは音を汚していない。変えるのは読みだけ。**
⚠️ ただし **SCOREQ は誤読を罰しない**ので、読み違いの重大さは**聴取でしか分からない**。

**聴く素材**: `reports/k8_listen/`（端末 vs ホスト 12 組）/ `reports/d4_accent/{student,teacher}`（各 64 本）
／ `reports/student_wav_v3/`（held-out 24 文）。
⚠️ **実機のスピーカーでしか分からないものが残る**（G32 の本体）: **途切れ・音量・実サンプルレートの誤差**。
checksum が一致しても M5.Speaker の DMA の実挙動は別（M-90 §5）。

⚠️ **これから板を買うなら N16R8。** 8 MB では K トラックの辞書（13.7 MB）が入らない。
16 MB なら**かなトラックの構成もそのまま焼ける**。**別々に取りに行くと 2 回焼き直しになる。**
実測に使っているのはユーザーの M5 CoreS3（16 MB / PSRAM 8 MB Quad。**D-047**）。

**実装として書くものは残っていない。** 1〜5 は「聴く」「決める」「上げ直す」。

⚠️ **聴取が決定を覆す可能性がある**: D-044（動作点 438,750）は
**「音素の 0.32%」だけで決めた**。

---

## 現在地 (2) — K トラック（端末で漢字を扱う）

B-0 / D-009 の「G2P は端末に載らない」を測り直したら**4 つの根拠が崩れた**。
結論は [`research/k1-kanji-katakana-ondevice.md`](research/k1-kanji-katakana-ondevice.md)、
計画は [`plan/k1-kanji-implementation-plan.md`](plan/k1-kanji-implementation-plan.md)。

```
[完了] K-1 調査              辞書は **mmap**（98.4 MB マップ / 常駐 1,040 KB）。
                            TTS 専用バイナリで 1 エントリ **130 B → 28.29 B**。
                            アクセントの天井 76.22% は語彙資源ではなく**外部資源ゼロの 126 行**
                            （SudachiDict 217 MB と nani ONNX の寄与は合計 0.13pt）
[完了] K-0 前提の凍結        **D-042**: N16R8 / OTA 無し / 辞書 SHA-256 を凍結
                            ⚠️ **エントリ数は D-044 で 438,750 に引き上げた** /
                            辞書 SHA-256 を凍結（`scripts/k1/dict_manifest.json`）
                            ESP-IDF 5.5.0 のヘッダで MMU 窓 32 MiB を PSRAM と共有と確認。
                            **フル辞書は現行の調達可能なボードに載らない**
[完了] K-1 エンコーダ        blob **13,702,320 B**（438,750 entries・行列 3.79 MB / char / unk 込み）。
                            G1〜G5 通過（11 フィールド往復 370,863/370,863、陰性対照つき）
                            ⚠️ **かつて 7,967,364 B と書いていたのは辞書本体だけ**の値。
                            端末に置くものを全部入れると 1.53 倍になる（C-047 と同じ形）
[完了] K-2 C リーダ+Viterbi  **MeCab と 1,918/1,918 文一致**（token 31,412 件）。G6〜G8 通過
                            ⚠️ ゲートが実バグを捕まえた（白熊 シロクマ/ハグマ の同点）
                            ⚠️ 半角→全角正規化は K-2 の対象外。端末側の前処理として別途要る
[完了] K-3 未知語ノード生成   **経路なし 0 / 1,977 文**。MeCab と **1,977/1,977 一致**、
                            未知語を含む文も **59/59**（G9〜G11）
[完了] K-4 アクセント規則移植 126 行を C へ。**Python 版と 2,333/2,333 一致**（G12/G13）
                            **外部資源は かな表 76 件だけ**
                            ⚠️ 「段の順序は入れ替え不可」は**未検証**（corpus が区別しない）
                            ⚠️ **この 4 段はホスト既定 7 段のうちの 4 つ**（C-049）。
                            残り 3 段（ONNX / Sudachi / 踊り字）は端末に載らない
[完了] K-4b NJD チェーン統合  Open JTalk **34 ファイル**を vendoring（修正 BSD）。
                            ホストと **635/635 文・NJD ノード 10,206 件**一致（M-69）
                            ⚠️ **取り込み元は素の pyopenjtalk ではなく pyopenjtalk-plus**
                            （22/34 ファイルが違う。取り違えると 75/600 で止まる。C-048）
                            ⚠️ 素の Open JTalk に無い段が chaining の前にもう 1 つあった
                            （`apply_original_rule_before_chaining` の 12 規則）
[完了] K-5 メモリの詰め      `MAXBUFLEN` 1024 → 256。1 文ピーク **268,941 → 104,589 B**（M-71）
                            G24 はラベルが**ホストと 34,997 本一致**（陽性対照つき）
                            ⚠️ 改変は `k4b_vendor.py` の `PATCHES` に登録し `--check` で守る
[完了] K-6 ホストとの一致    **素性が一致した 244 文でラベル差 0 件**（陰性対照 44 件。M-74）
                            食い違いは**全部が辞書の枝刈り**で、移植の誤りではない
                            ⚠️ **G17 の「0.60% 以下」は取り下げた**（C-050）。あれは
                            同形異音語 14 文の数で、枝刈りの代償ではなかった（実測 12〜18%）
[完了] K-7 ESP32 / QEMU     **漢字文から合成まで完走**（M-76）。app 359,584 B / 2 MB、
                            dict **13,702,320 B** / 13,828,096 B（**枠は D-042 の予算と一致**）
                            **3 経路が bit 一致** `0x78c209af06affc01`
                            ⚠️ DRAM が 2 回溢れた（.bss で 419 KB / heap の空き 20,964 B）。
                            大きい 2 配列を**合成用 arena から切り出して**解決
                            ⚠️ **PSRAM は QEMU が持っていない**ので使えなかった
[完了] K-8 実機（G28〜G31）  **CoreS3 で実機確認**（M-83）。v0.2.0 コード `0x78c209af06affc01` / 現行 `0xe4b645c30835d42d`、
                            漢字 G2P 27.85〜66.30 ms（音声長の 1.7〜2.3%。⚠️ 目安 1% 未達）、xRT 4.28〜4.62（W8A32・DIO）
                            ⚠️ 配布イメージは UART0 入力で native USB の板では操作不能
[完了] 漢字 + W8A8/PIE        DevKit 構成で **checksum が かな PIE 構成と bit 一致**（M-86）。
                            xRT は **DIO 1.090 → QIO 0.922 → QIO+64 B 行 0.858**
                            ⚠️ **DevKit の既定が DIO だった**（`sdkconfig.defaults` に QIO と 64 B 行を入れた）。
                            ⚠️ `write_flash --flash_mode qio` を明示するとブートループする
[完了] K-A/K-B M5 への搭載    **スタックチャンで漢字・カタカナ・ひらがなを喋った**（M-90）。
                            **`!` の印は要らない** — 端末が 3 値（かな / 辞書 / 拒否）で決め、
                            ホストの `to_intermediate.py` と **596/596 一致**。
                            **かな行と漢字行から同じ PCM が出る**（bit 一致）。
                            辞書 13.7 MB は **`esp_mmu_map`**（ROM 実装の 128 ページ制限の回避）
[未]   K-8 G32 聴取           人が 1 回聴く（WAV は `reports/k8_listen/` に用意済み）
```

**v0.2.0 で焼くだけの 16 MB イメージを配布した**（`esp32s3-firmware-kanji-16mb.bin`）。
⚠️ **配布した時点では QEMU でしか動かしていなかった。** その後 CoreS3 で焼いて起動と辞書 OK を確認したが、
**UART0 入力でビルドされていて native USB だけの板では `かな>` に届かない**（M-83 §1）。
⚠️ **モデルは v0.1.1 と bit 同一**（再学習していない）。**blob はまだ v1** で、S4 以降のコアは拒む。

**2026-09-01、v0.2.0 の資産を 8 本 → 15 本にした**（D-045）。
当初「モデルは v0.1.1 と bit 同一だから再配布しない」としたが、
**`releases/latest` が v0.2.0 に移った瞬間に README のダウンロード 5 本が壊れた**（C-052）。
`scripts/check_release_assets.py` が**README の表を読んで**在るかを CI で検査する。

**CI を入れた**（[`.github/workflows/ci.yml`](../.github/workflows/ci.yml)、4 job）。
⚠️ **新規 clone だけで通るゲートに限ってある** — 品質・速度・音は 1 つも見ていない。
範囲と「入れていない理由」は [`.github/workflows/README.md`](../.github/workflows/README.md)。

⚠️ **QEMU の `xRT 0.661` は使えない数字**（サイクル精度ではない）。実機は
**W8A32 で 4.28〜4.62**（DIO。M-83）、**W8A8+PIE で 0.446**（M-90）。
⚠️ 一致はすべて「OpenJTalk と同じ出力か」であって正しさではなく、**聴取はゼロ**。

✅ **動作点は決まった**（D-044 / M-77）: **438,750 entries / 接続行列は int16 のまま。**

| entries | 行列 | blob | 枠の余り | **音素の誤り** |
|---:|---|---:|---:|---:|
| 370,863（旧 D-042） | int16 | 12,153,280 | 1,674,816 | 0.55% |
| **438,750 ← これ** | **int16** | **13,702,320** | **125,776** | **0.32%** |
| 528,750 | uint8 | 13,781,959 | 46,137 | 0.22% |

**決め手は「文が一致するか」から「どれだけ違うか」へ測り直したこと**（M-77）。
⚠️ **音としては測っていない。** 0.32% は「ホストと違う音素の割合」。

ゲート: `uv run python scripts/test_k1_dict.py` / `uv run python scripts/k1/k0_verify_dict.py`
／ `make -C csrc jdict`（G6〜G11）／ `accent`（G12/G13）／ `njd-rules`（G14a〜c）／ `oj-heap`（G22〜G24）
／ `kanji-e2e`（G17）／ `label-ids`（G25〜G27 + **G25b/G25c**: 表を arena に置いても同じ列か）
／ `kb-parity`（**K-B の経路判定**がホストと一致するか。596/596）
／ `uv run python scripts/k1/k4b_vendor.py --sdist <tgz> --check`（取り込んだ C の同一性）
⚠️ **jdict / accent / njd-rules / oj-heap / kanji-e2e / label-ids / kb-parity は `all-test` に入れていない**
（辞書と pyopenjtalk が要る。g2p-corpus と同じ扱い）
⚠️ **`β` の聴取は M-60 / D-038 で決着済み**（β=0）。詳細は
[`plan/phase0-1-implementation-plan.md`](plan/phase0-1-implementation-plan.md) §10。

⚠️ **「金属的な尾が出たら E-2 の仮説 (a) の証拠になる」は撤回**（C-026）。
尾が出ても言えるのは「うちの decoder が不十分」までで、逆算の当否は別問題。

⚠️ **「待ち」に分類するときは、待っている相手を名指しできるか確かめること**（C-033）。
名指しできないなら待ちではなく**未着手**。実機ボードは名指しできたが toolchain は違った。
（その板も 2026-09-02 に同定して測り終えた = **D-047**。いま名指しできる相手は**聴く人**だけ。）

### 凍結した設計値（2026-08-28 に凍結。⚠️ 実機で動いた行は 2026-09-03 に更新した）

| 項目 | 値 | 記録 |
|---|---|---|
| デプロイ語彙 | **57**（英語版は 157）→ 合計 **559,008 params** / int8 blob 624,692 B | D-016 / M-26 / M-39 |
| 長さフィルタ | `max_spec_length=700` で 4.31% 除外 | D-017 / M-23 |
| `_` PAD | **フレームの 53.76%**。特別扱いしない | D-018 / M-25 |
| `s_v` | **1.2187**（丸め規約の差の吸収） | D-019 / M-24 |
| 評価の主指標 | SCOREQ synthetic/nr + UTMOS 併記。**DNSMOS は合否にせず併記プローブ** | D-020 / **D-034** |
| ギャップの帰属 | decoder 0.395 / acoustic 0.283 / duration 0.052。⚠️ 定義依存で順序が逆転する | **D-033** / M-49 |
| λ | `λ_n`/`λ₂` は実行時算出、`λ_Δ=0.86` / `λ_s=0.19` / `λ_T=0.27` | D-021 / M-30 |
| iSTFT | `center=True` + `length=T*256` | D-022 / M-32 |
| `yT` | EMA 適用版 | D-023 / M-33 |
| 判別器 | 94,755 params（学習専用） | D-025 / M-31 |
| 平坦度プローブ | `n_fft=1024 / guard=0 / power=1` | M-27 |
| ストリーミング | ステート保持 / CHUNK=8。**196.9 KB で一括版と bit 一致**（当時 fp32） | D-029 / M-42 |
| アクセント | 記号 `[ ] #` のみ。A1/A2/A3 は**足さない**（v2 35/36 → **v3 37/37**） | **D-030** / M-44 / M-59 |
| ESP32 の配置 | 重みは flash の `model` パーティション（M5 構成は app の `.rodata`）、arena は **176 KB を静的確保**（208 → 176。T4。実測 used 157,360 B） | **D-031** / M-46 / **M-89** |
| ESP32-S3 の既定 | **W8A8 + PIE**（フラグ無しで有効）+ **QIO** + **D-cache 64 B 行** | **D-048** / M-84 / M-86 |
| 逆 FFT | radix-2 自前 / float。naive の 1,435 倍 / SNR 138.7 dB | M-43 |
| int8 | **W8A8 + PIE**（ESP32-S3 の既定。D-048）。W8A32 は `-DSAAN_ENABLE_PIE=0` | M-43 / M-55 / **D-048** |
| blob | **v2**（int8 conv 重みを `[cout][k][align16(cin)]` で事前整列）。payload 624,692 B / ファイル 654,032 B | **D-046** / M-81 |
| 実行環境 | 手元の M4 Max（ラベル生成 CPU / 学習 MPS） | D-027 |
| `Eρ` | Stage 2 で凍結、Stage 3 で decoder と学習 | D-028 |
| CER | **かな CER**（表記 CER は符号が逆転する） | C-023 |

### 何が確実で、何が未知か

**確実（実測済み）**
- 教師は手元にあり、決定的にラベル `(dT, zT, yT)` を吐く
- 潜在インターフェースが論文と一致（192ch / hop 256 / 86.13 fps）
- 蒸留に音声データは不要。テキスト 23,271 行を調達可能
- ⚠️ かつてここに「45 MMAC/s は言語非依存なので論文の 0.22× RT はそのまま日本語に移る**見込み**」と
  書いていたが、**見込みを実測の欄に置いていた**。実測は **0.446**（M-90。CoreS3 / W8A8+PIE / 満チャンク 1 pull）で、
  論文の 0.22 には届いていない
- **日本語のデプロイ語彙は 57**（変換器の閉包）。論文の英語 157 より小さい

- **オンデバイス G2P は 877 B で成立する**（往復 100%、教師出力と bit 一致）

**未知（プロジェクトの成否を左右する順）**

1. **音として通用するか** — ⚠️ **誰も聴いていない**（G32）。SCOREQ も DNSMOS も
   **誤読を罰せず**（M-78）、DNSMOS は**アクセント誤りに無反応**（M-50）。
   ここだけは指標では埋まらない
2. ~~ESP32 で実時間に間に合うか~~ — ✅ **満チャンク 1 pull の xRT 0.446**（M-90。CoreS3 / W8A8+PIE）。
   ⚠️ **発話全体では 0.54〜0.71**（M-88 / M-90）で、**分母は未決**（D-049 で決める）。
   ⚠️ **「PIE の int8 カーネルが必須」は当たった**（同じ板で W8A32 は 0.92〜1.09。M-86）が、
   **M-43 の外挿 0.088× RT は実機の 18 倍外れた**（M-80）
3. ~~ESP32 に載るか~~ — ✅ **arena 176 KB / 実測 used 157,360 B**、内部 DRAM の空き 132 KB
   （漢字 + 辞書込み。M-89 / M-90）
4. ~~`β`（式7）~~ — ✅ **β=0 で確定、式7 は不要**（M-60 / D-038）。⚠️ **聴取者 1 名**
5. ~~アクセント型の再現性~~ — ✅ **v3 は 37/37 で符号一致**（M-59 / D-030、
   ミニマルペア 15 群 32 語 64 文）。**記号 `[` `]` `#` だけで足りており、
   duration net への A1/A2/A3 追加は不要**。
   ⚠️ 聴取は未実施 / 2 型の下降核は n=13〜16 でしか測れていない /
   `magnitude_ratio` **1.193**（生徒の起伏が 19% 大きい）は**聴かないと判断できない**

**品質は目標に届いた**（SCOREQ 教師比 **0.6444** > 論文の英語比 0.5427。M-61 / v3）。
⚠️ ただし **n=24** で、絶対値は日本語で較正されていない。
**速度もメモリも要件に届き、音も 1 度は聴かれた（M-91）。残るのは対照つきの聴取だけ。**

## リポジトリ構成

```
sanoTTS-jp/
├── CLAUDE.md                              運用ルール（実装前に読む）
├── docs/
│   ├── README.md                          このファイル
│   ├── requirements.md                    要件定義書
│   ├── decisions.md                       決定記録 D-001〜D-048 + 訂正履歴 C-001〜C-056
│   ├── measurements.md                    実測値の一次ソース M-1〜M-91
│   ├── upstream-sanotts.md                公式実装から得た事実（⚠️ 上流申告値・未再現）
│   ├── release-notes/                     各リリースの変更点（**訂正も残す**）
│   ├── plan/phase0-1-implementation-plan.md
│   ├── plan/k1-kanji-implementation-plan.md  K トラックの実装計画（K-0〜K-8）
│   ├── plan/s2-fast-kanji-m5-plan.md        **S2（T1〜T5 / 64 B 行 / M5 への漢字搭載）+ §10 に S-1 の前史**
│   └── research/
│       ├── b0-g2p-footprint.md            B-0 の結論
│       ├── k1-kanji-katakana-ondevice.md  K-1 の結論（B-0 を測り直した）
│       ├── s1-m5-cores3-speed.md          S-1（第三者報告 + 1 step の内訳。⚠️ §4 の仮説は半分外れた）
│       └── sanotts-jp-feasibility.md     初期調査
├── scripts/k1/                            K トラックの測定・ビルド（README.md あり）
│   ├── k0_verify_dict.py                  使う辞書が D-042 の凍結物か（陰性対照 2 種）
│   ├── k1_build_dict.py                   本番の辞書 blob を組んで G1〜G5 を通す
│   ├── k2_gen_vectors.py                  K-2/K-3 の参照ベクタ（参照は MeCab）
│   ├── k4_gen_vectors.py                  K-4 の参照ベクタ（参照は Python 版の 4 段）
│   ├── k4b_gen_vectors.py                 K-4b の参照ベクタ（参照は NJD チェーン）
│   ├── k4b_vendor.py                      **取り込みと --check**（上流 + PATCHES と突き合わせ）
│   ├── k5_gen_labels.py                   K-5 の basis（ホストのフルコンテキストラベル）
│   └── k6_gen_vectors.py                  K-6/K-7 の参照ベクタ（ラベル + ids の 2 基準）
├── src/saanotts_jp/                       ライブラリ（scripts から import する）
│   ├── _param_reference.py                論文 Table I を再現する層構成
│   ├── losses.py                          式2 / 3 / 5 / 6 / 7
│   ├── discriminator.py                   一次差分判別器（学習専用）
│   ├── vocab.py                           ⚠️ デプロイ語彙 57 と教師IDの写像（重みと一緒に凍結）
│   ├── labelpack.py                       ラベルパックの読み書き + 13 ゲート
│   ├── k1_dict.py                         **K-1: TTS 専用の辞書バイナリ形式**（TDD で実装）
│   ├── durations.py                       全行 duration の読み込み
│   ├── flatness.py                        音素クラス別 SFM（設定と教師ベースラインを凍結）
│   ├── accent.py                          アクセント型ミニマルペア評価（設定を凍結・D-030）
│   ├── scoreq_metric.py                   SCOREQ ラッパ（torchcodec 回避）
│   └── teacher_identity.py                piper-plus のコミット / ソース SHA-256 のピン留め
├── csrc/                                  **C99 推論コア（Phase D）**
│   ├── saanotts.h / saanotts.c            一括版。依存は libm のみ。malloc を呼ばず arena を使う
│   ├── saanotts_stream.h / .c             **ストリーミング版**（196.9 KB / SRAM に載る）
│   ├── saanotts_internal.h                両版で共有するカーネル（**2 回書かない**）
│   ├── fft.h / fft.c                      radix-2 逆実 FFT（naive の 1,435 倍）
│   ├── saanotts_int8.h / .c               int8 カーネル + **PIE（ESP32-S3 の整数 SIMD）**
│   ├── g2p.h / g2p.c / g2p_table.h        端末側 G2P（中間表現 → 生徒インデックス。表 877 B）
│   │                                        + **`saan_g2p_classify`**（K-B: かな / 辞書 / 拒否の 3 値）
│   ├── saan_prof.h                        段別プロファイラ（`SAAN_PROFILE=1` で有効。回数と要素数）
│   ├── erf_table.h                        GELU の erf 近似の節点表（**機械生成**。S3）
│   ├── oj_heap_psram.h                    Open JTalk の一時ヒープを PSRAM に落とす（M-90）
│   ├── jdict.h / jdict.c                **K-2/K-3/K-6: 辞書 blob リーダ + LOUDS + Viterbi
│   │                                        + 未知語 + 素性の復元 + 読みの推測**
│   ├── accent.h / accent.c          **K-4: アクセント規則 4 段（126 行）** ← 新規性の中核
│   ├── njd_rules.h / njd_rules.c              **K-4b: chaining 前の 12 規則**（フォークが足した段）
│   ├── label_ids.h / label_ids.c    **K-7: ラベル → 生徒インデックス**
│   ├── token_table.h / dan_table.h              語彙 57 / `_DAN_MAP` 76（**どちらも機械生成**）
│   ├── oj_heap_probe.h / oj_heap_probe.c            K-5 の追跡アロケータ（取り込んだ C を改変せず測る）
│   ├── openjtalk/                         **取り込んだ Open JTalk 34 ファイル**（修正 BSD）
│   │                                        ⚠️ PROVENANCE.md / 改変は PATCHES の 1 件だけ
│   ├── jdict_test.c / accent_test.c              K-2〜K-4 の受け入れ（どちらも陰性対照つき）
│   ├── njd_rules_test.c / oj_heap_test.c         K-4b / K-5 の受け入れ
│   ├── kanji_e2e_test.c / label_ids_test.c              K-6 / K-7 の受け入れ
│   ├── line.h / line.c                    端末の行編集（UTF-8 / BS / CRLF / ESC。369 B）
│   ├── golden_test.c                      参照実装との一致（Pearson >= 0.98）
│   ├── stream_test.c                      受け入れ条件 G1〜G4（**stack 込みで判定**）
│   ├── fft_test.c / int8_test.c           各カーネルの単体検証
│   ├── g2p_test.c / line_test.c           G2P と行編集（**どちらも陽性対照つき**。line_test に **G11 = 経路判定**）
│   ├── range_test.c                       **S9 の範囲版カーネル**が `[0,T)` 版と bit 一致か（陽性対照つき）
│   ├── erf_test.c                         erf 近似 vs libm（線形補間の陽性対照つき）
│   ├── prof_test.c                        段別プロファイラの駆動 + `--expect-*` ゲート（S1 / T1〜T3）
│   ├── route_tool.c                       経路判定をホストから駆動する棒（`kb-parity` が使う）
│   ├── bench.c                            レイテンシ測定（段別の内訳）
│   └── Makefile                           `make all-test` / `make run-bench` / `make prof`
├── esp32/                                 **ESP-IDF アプリ**（CoreS3 で実機確認 M-82〜M-90。⚠️ DevKit の I2S 直叩きは未検証）
│   ├── main/main.c                        arena / 合成ループ / 計測ログ / 対話ループ
│   ├── main/saan_console.{h,c}            シリアルからの 1 行入力（UART0 / USB Serial/JTAG）
│   ├── main/saan_dict.{h,c}               dict パーティションの mmap（**`esp_mmu_map`**。M-90 §4）
│   ├── main/saan_kanji.{h,c}              **K-7: 漢字文 → 生徒インデックス**（端末の全段）
│   ├── partitions.csv                     8 MB（かな入力だけの出荷構成）
│   ├── partitions_16mb.csv                **16 MB + dict 13,828,096 B**（漢字対応）
│   ├── sdkconfig.defaults                 **QIO + D-cache 64 B 行**（M-84 / M-86）。PIE は S3 で既定（D-048）
│   ├── sdkconfig.kanji / .qemu / .usb_serial_jtag   漢字 / QEMU（DIO に戻す）/ USB-JTAG コンソール
│   ├── boards/m5unified/                  **M5Stack（スタックチャン）構成**。重みは app の `.rodata`、
│   │                                        `partitions.csv` に **dict 0x2D0000（DevKit と同じ offset）**、
│   │                                        `main/saan_audio_m5.cpp` が M5.Speaker、`saan_ui_m5.cpp` が画面
│   ├── pie_probe/                         PIE の bit 一致（A/B/C 節）+ 重みの置き場所 / GELU（D/E 節。M-85）
│   ├── host_stub/                         IDF API の偽ヘッダ。**デバイスには載らない**
│   ├── TESTING.md                         **実機を持っている人向けの手順**
│   └── README.md                          ビルドと設計判断
├── deploy/                                リモート実行の材料（⚠️ 手順書は削除済み。D-027 の追記）
│   ├── vastai_bootstrap.sh                setup → parity → labels → train
│   └── retarget_sources.py                path 依存をインスタンスのパスに向け直す
├── pyproject.toml / uv.lock               uv 環境定義
├── .claude/
│   ├── settings.json                      permissions.deny + PreToolUse hook
│   ├── hooks/guard_bash.py                piper-plus 保護 / uv 強制 / 本番パック保護（94 ケース + commit ガードのテスト付き）
│   └── skills/                            recording-measurements / teacher-inference /
│                                           student-training / evaluating-quality /
│                                           verifying-reports / writing-gates
├── reports/                               一次データ (JSON)。⚠️ 全行ダンプは追跡しない
└── scripts/
    ├── phase0_verify_teacher.py           教師の決定的推論を検証（6 チェック）
    ├── kana_g2p.py                        中間表現 ⇄ 音素列の変換器 + 入力正規化
    ├── to_intermediate.py                 漢字文 → 端末に貼る 1 行（往復一致を検査）
    ├── gen_teacher_labels.py              中間表現 → 教師 → ラベルパック
    ├── b_durations_all.py                 全行の duration だけを取る（B-4/7/8 の土台）
    ├── train_student.py                   生徒 4 段の蒸留学習
    ├── test_losses.py / test_labelpack.py / test_discriminator.py
    ├── b4_device_parity.py                CPU/GPU のラベル一致検証（★ 本番前のゲート）
    ├── b4_length_hist.py                  長さ分布と符号化の関係式
    ├── b5_teacher_baseline.py / b5_measure_mos.py / b5_scoreq_baseline.py
    ├── b6_build_evalset.py                クラス別 n を確保した評価セットを作る
    ├── b6_flatness_grid.py                SFM の窓設計グリッド（定数がズレたら exit 1）
    ├── b7_sv_calibration.py               length scale s_v の較正
    ├── b8_pad_duration.py                 `_` PAD のフレーム占有率
    ├── b9_vocab_closure.py                デプロイ語彙の閉包
    ├── b9_add_question_eos.py             疑問 EOS 4 種の文を追加
    ├── b10_overlap.py / b10_write_exclusions.py
    ├── c1_lambda_balance.py               λ の勾配整合
    ├── d5_istft_framing.py / d6_ema_ablation.py
    ├── eval_metrics.py                    UTMOS + SCOREQ 4 設定を並べて出す
    ├── esp32_memory_budget.py             ESP32-S3 のメモリ収支見積もり
    ├── train_student.py                   生徒 4 段の蒸留学習（段間で重みを引き継ぐ）
    ├── synthesize_student.py              **生徒だけで音声を作る**（教師を呼ばない）
    ├── eval_student.py                    教師比 + 音素クラス別 SFM/RMS
    ├── measure_cer.py                     かな CER（表記 CER も参考で併記）
    ├── quantize_student.py                int8 PTQ シミュレーションと blob サイズ
    ├── b_beta_sweep.py                    β の候補絞り込み（決定は聴取）
    ├── build_listening_set.py             A/B 聴取セット（順序・左右ランダム / 2 反復）
    ├── score_listening.py                 二項 CI と内的一貫性で判定
    ├── export_c_weights.py                重みと golden を SAAN 形式で書き出す
    └── b0/                                B-0 の測定スクリプト（記録用）

（`data/pack*` `runs/` `reports/eval_*` `csrc/*.bin` は .gitignore。再生成できる）
```

## 実行方法

⚠️ **以下は「フルセットアップ」（piper-plus + 教師）が前提。**
重みだけで音を出す / ゲートを回すだけなら
[`../README.md`](../README.md) の「最小セットアップ」（`uv venv` + 3 パッケージ）で足りる。

```bash
uv sync --extra eval                             # 環境構築（初回・依存変更時）

# 健全性チェック（ここが通らないなら先に進まない）
uv run python scripts/phase0_verify_teacher.py   # 教師の疎通（6 チェック）
uv run python scripts/kana_g2p.py                # 中間表現変換器（10 ケース）
uv run python scripts/to_intermediate.py "今日は良い天気ですね。"   # 端末に貼る 1 行
uv run python scripts/test_losses.py             # 損失の性質（26 項目）
uv run python scripts/test_labelpack.py          # パック往復 + ゲート発火
uv run python scripts/test_discriminator.py      # 判別器（23 チェック）
uv run python .claude/hooks/test_guard_bash.py   # hook の回帰（94 ケース + commit ガード）
uv run python src/saanotts_jp/_param_reference.py  # 論文 Table I の再現 + V=57
uv run python scripts/check_doc_counters.py      # 索引の M/D/C 番号 + 引用アンカー
uv run python scripts/check_doc_links.py         # md の相対リンクが実在するか
uv run python scripts/check_release_assets.py    # 表の資産がリリースに在るか（要ネットワーク）
uv run python scripts/test_k1_dict.py            # K-1 辞書エンコーダ（G1〜G5。陰性対照つき）
uv run python scripts/k1/k0_verify_dict.py       # 使う辞書が D-042 の凍結物か

# C99 推論コア（Phase D）
uv run python scripts/export_c_weights.py --ckpt runs/v3/stage4.pt
make -C csrc all-test                            # golden / stream（held-out 24 文 × 3 レーン）/ fft /
                                                 #   int8 / int8-golden / int8-e2e / arena /
                                                 #   g2p / pad / line / erf / range
make -C csrc prof                                # 段別プロファイラ + --expect-* ゲート（S1 / T1〜T3）
make -C csrc kb-parity                           # 経路の 3 値判定がホストと一致するか（K-B）
bash scripts/check_esp32_template.sh             # esp32/ 雛形をホストで検査（§10 = arena の余白）
```

⚠️ **`make prof` のホストの時間は実機の内訳ではない**（C-055）。速度の判断は
**実機の `idf.py -DSAAN_PROFILE=1` の表**でだけ行い、**マージ直後に前の版と 1 行ずつ並べる**。

一から作り直す場合:

```bash
uv run python scripts/gen_teacher_labels.py --split train   --out data/pack     # 47 分
uv run python scripts/gen_teacher_labels.py --split heldout --out data/pack_heldout
uv run python scripts/train_student.py --run runs/v3 --stage 1 --steps 20000 --accum 8
uv run python scripts/train_student.py --run runs/v3 --stage 2 --steps 60000 --accum 8
uv run python scripts/train_student.py --run runs/v3 --stage 3 --steps 80000 --accum 8
uv run python scripts/train_student.py --run runs/v3 --stage 4 --steps 60000 --accum 8
uv run --extra eval python scripts/eval_student.py --ckpt runs/v3/stage4.pt --n 24 \
    --out reports/eval_v3
```

⚠️ **Stage 3 は 80,000 step**（D-037）。40,000 は v2 の値で、80k にすると 5 指標すべて
改善する（M-59）。⚠️ **160k は無駄**（80k と有意差なし）。
⚠️ **ラベルは一度だけ生成する**（D-015）。hook が `data/pack` の破棄と再生成を deny する。

**Python は必ず `uv` 経由**（`pip install` を使わない）。
⚠️ **学習もラベル生成も手元の M4 Max で完結する**（[D-027](decisions.md)。D-012 の実行環境部分は撤回済み）。
かつてここに「学習は vast.ai」と書いてあったが**古い**。手順書も 2026-09-03 に削除した（D-027 の追記）。
⚠️ 本番パック `data/pack` を破棄・再生成しようとすると hook が止める（[D-015](decisions.md)）。

## 外部依存

| 対象 | 場所 | 備考 |
|---|---|---|
| 教師モデル | `ayousanz/piper-plus-zero-shot-tsukuyomi` (HF private) | `epoch=499-step=22000.ckpt` 927 MB |
| piper-plus | `~/Documents/piper-plus` | v2.0.0 HEAD。**読み取り専用で使う** |
| Python 環境 | 本リポジトリの `uv`（`pyproject.toml`） | Python 3.14.0 / torch 2.13.0。教師ラベルは piper-plus venv と bit 一致 |
| 学習環境 | **手元の M4 Max**（D-027） | ラベル生成 CPU 40 分 / 学習 MPS 約 1.3 時間。⚠️ **リモートの手順書は削除した**（D-027 の追記。CUDA parity ゲートは未通過のまま） |
| 評価指標 | `scoreq==1.0.1`（PyPI） | `uv sync --extra eval`。ラッパは `src/saanotts_jp/scoreq_metric.py` |

⚠️ **piper-plus のコミットは `src/saanotts_jp/teacher_identity.py` にピン留めしてある。**
別マシンで教師を動かす前に `verify()` を通すこと（コードが違えばラベルは静かに別物になる）。
