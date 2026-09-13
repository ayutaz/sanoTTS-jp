/* sanoTTS-jp — ESP32-S3 で実時間に間に合う日本語ニューラル TTS（567 K params）。
 *
 *   #include <SanoTTS.h>
 *   #include <SanoTTSSpeakerM5.h>
 *
 *   SanoTTS tts;
 *   SanoTTSSpeakerM5 spk;
 *
 *   void setup() {
 *     M5.begin();
 *     tts.setSpeaker(&spk);
 *     if (!tts.begin()) { Serial.println(tts.lastError()); return; }
 *     tts.say("今日は良い天気ですね。");
 *   }
 *
 * ## 入力
 *
 * **漢字かな交じり文でも、かな中間表現でもよい。** どちらかは端末が自分で決める
 * （`saan_g2p_classify()` の 3 値判定）。**同じ文をかなで書いても漢字で書いても
 * PCM が bit 一致する。**
 *
 *   漢字かな交じり  今日は良い天気ですね。      ← 辞書（13.7 MB）が要る
 *   かな中間表現    きょ][おわよ][いて][んきです°ね
 *                   [ 上昇 / ] 下降核 / # 句境界 / ° 無声化
 *
 * ⚠️ **混ぜると拒否する。** 中間表現の記号が混じった漢字文は喋らない
 *    （読めない文字が黙って落ちるのを防ぐため）。
 *
 * ## ライセンス
 *
 * このコードは **MIT**。⚠️ **重み（SanoTTS-jp-voice-* ライブラリ）は MIT ではない** —
 * `LicenseRef-sanoTTS-jp-Model-1.0`。帰属表示の義務と、**出力の用途制限 4 項目**と
 * コピーレフトがある。製品に組み込む側は 4 項目を自社の利用規約に書く義務がある。
 * 同梱の `LICENSE-MODEL.md` を読むこと。
 */
#ifndef SANOTTS_H
#define SANOTTS_H

#include <stddef.h>
#include <stdint.h>

#include "sanotts_config.h"
#include "SanoTTSSpeaker.h"

/* ⚠️ **PlatformIO の LDF にこの依存を見せるために、ここで include する。**
 *    LDF（既定の `chain` モード）はプリプロセッサの条件を評価せず `#include` を
 *    テキストで拾うので、この 3 行があると重みライブラリを依存として拾ってくれる。
 *    無い環境では `__has_include` が false になって何も起きない。 */
#if defined(__has_include)
#  if __has_include(<saanotts_jp_voice.h>)
#    include <saanotts_jp_voice.h>
#  endif
#endif

class SanoTTS {
public:
    /* PCM は **22,050 Hz / モノラル / int16 / インターリーブ無し**。
     * ⚠️ コールバックが返るまで次のチャンクは作られない（= ここで待つと xRT が悪化する）。 */
    typedef void (*PcmCallback)(const int16_t* pcm, size_t nSamples, void* user);

    /* 重み（と、有効なら辞書）を開く。
     * ⚠️ **辞書が開けなくても true を返す。** 漢字経路だけ無効にして
     *    かな専用で続く（D-063）。`kanjiReady()` で確かめられる。 */
    bool begin();

    bool ready()      const { return m_ready; }
    bool kanjiReady() const { return m_kanjiReady; }

    /* 合成して PCM をチャンクで返す。スピーカーは要らない。 */
    bool synthesize(const char* text, PcmCallback cb, void* user = nullptr);
    bool synthesize(const char* text, size_t nbytes, PcmCallback cb, void* user = nullptr);

    /* 合成して `setSpeaker()` された先へ流す。プリロールも面倒を見る。 */
    bool say(const char* text);
    bool say(const char* text, size_t nbytes);

    void setSpeaker(SanoTTSSpeaker* s) { m_spk = s; }

    /* 発話の速さ。⚠️ **既定 1.2187 は D-019 の実測値**（言語ごとに較正されている）。
     *    1.0 にすると教師と違う長さになる。変えるのは意図があるときだけ。 */
    void  setLengthScale(float s) { m_sv = s; }
    float lengthScale() const { return m_sv; }

    /* 直前の発話で出た int16 サンプル全部の FNV-1a 64。**移植の検証用**。
     * ⚠️ 同じターゲット上の 2 構成を比べるときだけ意味がある
     *    （ホストと ESP32 は float の丸めが違うので bit 一致しない。それは正常）。 */
    uint64_t checksum() const;
    uint32_t samples()  const;
    int32_t  absmax()   const;

    const char* lastError() const { return m_err; }

    static const uint32_t kSampleRate = 22050;
    /* ⚠️ **arena の限界ではなく学習分布の上限**（D-017 の max_spec_length=700 相当）。
     *    超える入力は**喋らずに拒否する** — 分布外の音を黙って出すより良い。 */
    static const int32_t  kMaxIds = 350;

private:
    bool toIds(const char* text, size_t nbytes, int32_t* nIds);
    bool openStream(int32_t nIds);

    SanoTTSSpeaker* m_spk = nullptr;
    bool  m_ready = false;
    bool  m_kanjiReady = false;
    float m_sv = 1.2187f;            /* SAAN_S_V（D-019） */
    const char* m_err = "";
};

#endif /* SANOTTS_H */
