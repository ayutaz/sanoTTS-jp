/* 長い文章を**文ごとに貯めて鳴らす**（M5.Speaker）。
 *
 * ストリーミング（`SanoTTSSpeakerM5`）が途切れる構成向け。典型は **Wi-Fi と併用して
 * arena を PSRAM に置いた**（`-DSANOTTS_ARENA_HEAP=1`）とき。
 *
 *   - 文を `。` `！` `？` と改行で区切り、1 文ずつ `say()` する
 *   - `say()` は合成し終わったら**鳴り終わる前に戻る**ので、**前の文を鳴らしている間に
 *     次の文を合成する**
 *   - 最後に `spk.wait()` で鳴り終わりを待つ
 *
 * ⚠️ **1 文目は合成し終わるまで鳴らない**（ストリーミングより鳴り出しが遅い）。
 * ⚠️ **区切る場所は文末だけにしてある。** 読点で切ると文の途中で抑揚が切れる。
 * ⚠️ **M5 の実機では鳴らしていない**（ライブラリの README「何が確かめてあるか」）。
 *
 * 要るもの: M5Unified / 16 MB の辞書（漢字を読むため。README「漢字を喋らせる」）
 */
#include <M5Unified.h>
#include <SanoTTS.h>
#include <SanoTTSSpeakerM5Buffered.h>

SanoTTS tts;
SanoTTSSpeakerM5Buffered spk;

static const char kText[] =
    "今日は良い天気ですね。"
    "午後から少し風が強くなるそうです。"
    "洗濯物は早めに取り込みましょう。";

/* 文末（。！？ と改行）で区切って 1 文ずつ喋る。 */
static void sayBySentence(const char* text) {
    static const char* const kEnds[] = {"。", "！", "？", "\n"};
    const char* p = text;
    while (*p) {
        const char* cut = nullptr;
        size_t cutLen = 0;
        for (const char* e : kEnds) {
            const char* q = strstr(p, e);
            if (q && (!cut || q < cut)) { cut = q; cutLen = strlen(e); }
        }
        const size_t n = cut ? (size_t)(cut - p) + cutLen : strlen(p);
        /* 改行は喋る列に含めない（句点は含める = 文末の抑揚が付く） */
        const size_t nSay = (cut && *cut == '\n') ? (size_t)(cut - p) : n;
        if (nSay > 0 && !tts.say(p, nSay)) Serial.printf("say: %s\n", tts.lastError());
        p += n;
    }
}

void setup() {
    M5.begin();
    Serial.begin(115200);
    tts.setSpeaker(&spk);
    if (!tts.begin()) { Serial.println(tts.lastError()); return; }
    if (!tts.kanjiReady()) Serial.println("⚠️ 辞書が無いので漢字は読めない（README「漢字を喋らせる」）");

    sayBySentence(kText);
    spk.wait();
    Serial.println("鳴り終わった");
}

void loop() { M5.update(); }
