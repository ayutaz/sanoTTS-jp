/* arena ストレス — ESP32 に静的 arena を切る前の受け入れ条件（c'-4 の G6/G7）
 *
 * **なぜ要るか。** ESP32 では arena を `static uint8_t g_arena[N]` で
 * 固定的に切る。`saan_stream_arena_needed()` は緩い上限なので、実際に
 * どこまで小さくできるかと、**足りないときに安全に失敗するか**を
 * 手元で測っておかないと、実機では「ログも出ずに再起動する」に化ける。
 *
 * ⚠️ 子プロセスに fork して SEGV を観測する。**ホスト専用のテストコード**で、
 *    デバイスには載らない（csrc の core 3 ファイルには何も足していない）。
 *
 *   make -C csrc arena
 */
/* ⚠️ **移植性。** `clock_gettime` / `CLOCK_MONOTONIC` は POSIX で、
 * 厳密 `-std=c99` だと glibc が隠す。**どのヘッダより先に**立てる。
 * ⚠️ これを立てると macOS 側で `M_PI` が隠れるので、下でガードしている。 */
#define _POSIX_C_SOURCE 199309L
#include "saanotts.h"
#include "saanotts_stream.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/wait.h>
#include <unistd.h>

static void *slurp(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "開けない: %s\n", path); exit(1); }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    void *b = malloc((size_t)n);
    if (!b || fread(b, 1, (size_t)n, f) != (size_t)n) {
        fprintf(stderr, "読めない: %s\n", path); exit(1);
    }
    fclose(f); *size = (size_t)n; return b;
}

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e3 + (double)ts.tv_nsec / 1e6;
}

/* 子プロセスの終了コード。0=完走 / 10=init が clean fail / それ以外は異常 */
#define RC_OK        0
#define RC_INITFAIL 10
#define RC_PULLFAIL 11

typedef struct { int rc; int crashed; int sig; int frames; size_t used; } run_t;

static const saan_weights *g_W;
static const int32_t *g_ids_base;
static int g_base_n;

/* n_ids 個の id を作る（golden の 53 個を巡回させる） */
static int32_t *make_ids(int n) {
    int32_t *p = (int32_t *)malloc(sizeof(int32_t) * (size_t)n);
    for (int i = 0; i < n; ++i) p[i] = g_ids_base[i % g_base_n];
    return p;
}

/* 子プロセスの中身。共有メモリを使わないので frames/used は返さない */
static int child_run(int n_ids, size_t arena_bytes) {
    int32_t *ids = make_ids(n_ids);
    void *buf = malloc(arena_bytes);
    if (!buf) return 99;
    saan_arena A; saan_arena_init(&A, buf, arena_bytes);
    saan_stream st;
    if (saan_stream_init(&st, g_W, &A, ids, n_ids, SAAN_S_V) != SAAN_OK)
        return RC_INITFAIL;
    static float chunk[SAAN_CHUNK * SAAN_HOP];
    int32_t n = 0;
    for (;;) {
        saan_status s = saan_stream_pull(&st, chunk, &n);
        if (s != SAAN_OK) return RC_PULLFAIL;
        if (n <= 0) break;
    }
    free(buf); free(ids);
    return RC_OK;
}

static run_t run_isolated(int n_ids, size_t arena_bytes) {
    run_t r; memset(&r, 0, sizeof r);
    fflush(NULL);
    pid_t pid = fork();
    if (pid == 0) _exit(child_run(n_ids, arena_bytes));
    int status = 0;
    waitpid(pid, &status, 0);
    if (WIFSIGNALED(status)) { r.crashed = 1; r.sig = WTERMSIG(status); r.rc = -1; }
    else r.rc = WEXITSTATUS(status);
    return r;
}

static const char *verdict(run_t r) {
    if (r.crashed) return "CRASH";
    switch (r.rc) {
        case RC_OK:       return "ok";
        case RC_INITFAIL: return "init-fail";
        case RC_PULLFAIL: return "pull-fail";
        default:          return "?";
    }
}

/* `saan_stream_arena_used(n)`（コアが確保一覧から計算する「init 後の a.used」）と実測の判定。
 * 雛形（esp32/main/main.c）はこの関数の値と a.used が一致しないと発話を拒否するので、
 * 関数と実際の確保がずれていれば**実機は 1 発話も喋らない**。判定関数を分けてあるのは
 * 下で陽性対照（±16 B = saan_alloc の最小粒度）を通すため。戻り値 0 = OK / 1 = NG */
static int used_verdict(size_t measured, size_t expected) { return measured != expected; }

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s student.bin golden.bin\n", argv[0]);
        return 2;
    }
    size_t wsz, gsz;
    void *wbuf = slurp(argv[1], &wsz), *gbuf = slurp(argv[2], &gsz);
    static saan_weights W, G;
    if (saan_weights_open(&W, wbuf, wsz) != SAAN_OK) { fprintf(stderr, "重みが読めない\n"); return 1; }
    if (saan_weights_open(&G, gbuf, gsz) != SAAN_OK) { fprintf(stderr, "golden が読めない\n"); return 1; }
    g_W = &W;
    uint64_t nb = 0;
    const float *ids_f = (const float *)saan_tensor(&G, "in.ids", NULL, NULL, &nb);
    if (!ids_f) { fprintf(stderr, "golden に in.ids が無い\n"); return 1; }
    g_base_n = (int)(nb / sizeof(float));
    int32_t *base = (int32_t *)malloc(sizeof(int32_t) * (size_t)g_base_n);
    for (int i = 0; i < g_base_n; ++i) base[i] = (int32_t)ids_f[i];
    g_ids_base = base;

    int bad = 0;
    const size_t ARENA = 176u * 1024u;   /* ESP32 の雛形が静的確保する値（esp32/main/main.c の
                                          * SAAN_ARENA_BYTES。T4 で 208 → 176 KB） */
    const int DESIGN_IDS = 350;          /* D-017 の max_spec_length=700 相当 */

    printf("sanoTTS-jp arena ストレス（c'-4 の受け入れ条件）\n");
    printf("  weights : %s (%zu B)\n", argv[1], wsz);
    printf("  base ids: %d 個を巡回させて任意長を作る\n\n", g_base_n);

    /* ---- 1+3. arena を 1 KB 刻みで走査する（二分探索は使わない） ----
     * ⚠️ 二分探索だと「途中で CRASH した」ときに探索木が壊れ、最小値が
     * 信用できなくなる。**全点を舐める**ほうが遅いが結論が壊れない。 */
    printf("== 1. arena を 1 KB 刻みで走査（n_ids=%d・設計上限） ==\n", DESIGN_IDS);
    size_t needed_design = saan_stream_arena_needed(DESIGN_IDS);
    printf("  saan_stream_arena_needed(%d) = %zu B (%.1f KB)  ← **緩い上限**\n",
           DESIGN_IDS, needed_design, (double)needed_design / 1024.0);
    size_t min_ok = 0, first_ok = 0;
    int c3 = 0, tested = 0, holes = 0;
    size_t crash_lo = 0, crash_hi = 0;
    for (size_t kb = 150; kb <= 260; ++kb) {
        run_t r = run_isolated(DESIGN_IDS, kb * 1024u);
        ++tested;
        if (r.crashed) {
            if (!crash_lo) crash_lo = kb;
            crash_hi = kb;
            ++c3; ++bad;
        } else if (r.rc == RC_OK) {
            if (!first_ok) first_ok = kb;
        } else if (first_ok) {
            /* 一度 ok になった後に fail に戻るのは単調性の破れ */
            ++holes;
        }
    }
    min_ok = first_ok * 1024u;
    printf("  実測: init も pull も通る最小 arena = %zu B (%zu KB)\n", min_ok, first_ok);
    printf("  needed() / 実測 = %.2f 倍。**needed() をそのまま静的確保しないこと**\n",
           (double)needed_design / (double)min_ok);
    printf("  %s **init が SAAN_OK を返した後に落ちた** サイズ: %d / %d 点",
           c3 == 0 ? "OK " : "NG!", c3, tested);
    if (c3) printf("（%zu〜%zu KB の範囲）", crash_lo, crash_hi);
    printf("\n");
    if (holes) { printf("  NG! 単調性の破れ %d 件\n", holes); ++bad; }
    printf("  ⚠️ これが c\'-4 の主ゲート。saan_alloc は失敗しても used を進めないので、\n");
    printf("     大きい確保だけ失敗して後続の小さい確保が成功すると init が OK を返す。\n");
    printf("  雛形の既定 %zu B (%.0f KB) は実測に対し %+lld B\n\n",
           ARENA, (double)ARENA / 1024.0, (long long)ARENA - (long long)min_ok);

    /* ---- 2. arena 固定で n_ids を振る（クラッシュ 0 が条件） ---- */
    printf("== 2. arena %zu B 固定で n_ids を振る（**クラッシュ 0** が条件） ==\n", ARENA);
    static const int ns[] = {1, 8, 16, 32, 53, 80, 120, 160, 200, 250, 300, 350,
                             400, 450, 496, 520, 560, 600, 700, 800, 848, 900, 1000};
    const int NN = (int)(sizeof ns / sizeof ns[0]);
    int crashes = 0, last_ok = 0, first_fail = 0;
    printf("  %8s  %-12s\n", "n_ids", "結果");
    for (int i = 0; i < NN; ++i) {
        run_t r = run_isolated(ns[i], ARENA);
        printf("  %8d  %-12s%s\n", ns[i], verdict(r),
               r.crashed ? "  ← **これが起きてはいけない**" : "");
        if (r.crashed) { ++crashes; ++bad; }
        else if (r.rc == RC_OK) last_ok = ns[i];
        else if (!first_fail) first_fail = ns[i];
    }
    printf("  %s クラッシュ %d 件 / 通った最大 n_ids = %d / 最初に clean fail した n_ids = %d\n\n",
           crashes == 0 ? "OK " : "NG!", crashes, last_ok, first_fail);

    /* ---- 4. pull ごとのレイテンシと「フレーム数 vs サンプル数」 ---- */
    printf("== 4. pull のプロファイル（n_ids=%d / arena %zu B・このホスト） ==\n",
           DESIGN_IDS, ARENA);
    {
        int32_t *ids = make_ids(DESIGN_IDS);
        void *buf = malloc(ARENA);
        saan_arena A; saan_arena_init(&A, buf, ARENA);
        saan_stream st;
        saan_status s = saan_stream_init(&st, &W, &A, ids, DESIGN_IDS, SAAN_S_V);
        if (s != SAAN_OK) { printf("  NG! init: %s\n", saan_strerror(s)); ++bad; }
        else {
            static float chunk[SAAN_CHUNK * SAAN_HOP];
            double first = 0, sum_rest = 0, worst_rest = 0;
            int npull = 0, frames = 0, last_n = 0, n_short = 0;
            for (;;) {
                int32_t n = 0;
                double t0 = now_ms();
                s = saan_stream_pull(&st, chunk, &n);
                double dt = now_ms() - t0;
                if (s != SAAN_OK) { printf("  NG! pull: %s\n", saan_strerror(s)); ++bad; break; }
                if (n <= 0) break;
                if (npull == 0) first = dt;
                else { sum_rest += dt; if (dt > worst_rest) worst_rest = dt; }
                if (n < SAAN_CHUNK) ++n_short;
                last_n = n; frames += n; ++npull;
            }
            double mean_rest = npull > 1 ? sum_rest / (npull - 1) : 0.0;
            double chunk_ms = (double)SAAN_CHUNK * SAAN_HOP * 1e3 / SAAN_SR;
            double audio_s = (double)frames * SAAN_HOP / SAAN_SR;
            printf("  pull 回数 %d / 合計 %d frames = %d sample (%.3f s の音声)\n",
                   npull, frames, frames * SAAN_HOP, audio_s);
            printf("  ⚠️ pull が返すのは **フレーム数**（最大 %d）。サンプル数は n*%d\n",
                   SAAN_CHUNK, SAAN_HOP);
            printf("  最終 pull の n = %d / n < %d だった回数 = %d\n",
                   last_n, SAAN_CHUNK, n_short);
            printf("  満チャンク 1 個 = %d sample = %.2f ms の音声\n",
                   SAAN_CHUNK * SAAN_HOP, chunk_ms);
            printf("  初回 pull %.2f ms / 2 回目以降 mean %.2f ms (worst %.2f) → 初回は %.1f 倍\n",
                   first, mean_rest, worst_rest, mean_rest > 0 ? first / mean_rest : 0.0);
            printf("  定常 pull の xRT = %.4f（このホスト。ESP32 ではない）\n",
                   mean_rest / chunk_ms);
            printf("  算法遅延 = %d frames = %.3f s（受容野 %d + iSTFT 2）\n",
                   SAAN_LATENCY + 2, (double)(SAAN_LATENCY + 2) * SAAN_HOP / SAAN_SR,
                   SAAN_LATENCY);
            int g7 = mean_rest > 0 && first / mean_rest >= 5.0;
            printf("  %s G7 初回 pull が定常の 5 倍以上（I2S を enable する前にプリロールが要る根拠）\n",
                   g7 ? "OK " : "NG!");
            if (!g7) ++bad;
            printf("  arena: st.peak_used %zu B / a.peak %zu B / a.used %zu B / 確保 %zu B\n",
                   st.peak_used, A.peak, A.used, ARENA);
            printf("  %s st.peak_used == a.peak（duration net の一時確保を巻き戻しても"
                   " 高水位は変わらない）\n", st.peak_used == A.peak ? "OK " : "NG!");
            if (st.peak_used != A.peak) ++bad;
            printf("  ⚠️ ただし**高水位は「確保すべき量」ではない**: %zu B (%.1f KB) しか"
                   " 使わないのに、\n", A.peak, (double)A.peak / 1024.0);
            printf("     init が通る最小 arena は %zu B。ALIGN16 の切り上げと"
                   " 確保順の差でずれる。\n", min_ok);
        }
        free(buf); free(ids);
    }

    /* ---- 5. init 後の a.used == saan_stream_arena_used(n_ids)（T2 で機械化） ----
     * 以前はここに「正しい a.used の最小 194,640 B / 黙って失敗した最大 191,280 B / 中点 192,960 B」を
     * **文字列で直書き**し、雛形はその定数を下限にしていた。T2a（obuf +2 hop）で既に 196,576 B に
     * ずれ、T2（S9）で 180,064 B まで下がったのに文字列は誰も更新しない。しかも定数はホストで
     * 測った値で、ターゲット（32 bit ポインタ）では impl 構造体が 768 B 小さく **一致しない**
     * （QEMU で実際に拒否された）。そこで雛形は定数をやめてコアの `saan_stream_arena_used()` と
     * 突き合わせる形にし、ここではその関数が**実際の確保と bit で一致する**ことを複数の n_ids で
     * 確かめる（確保を変えて関数を直し忘れると、実機は 1 発話も喋らない）。 */
    printf("== 5. init 後の a.used が saan_stream_arena_used(n_ids) と一致するか（雛形の二重防御の前提） ==\n");
    {
        static const int nn[] = {1, 8, 53, 350, 520};
        int n_ng = 0, ctl_ok = 1;
        for (int i = 0; i < (int)(sizeof nn / sizeof nn[0]); ++i) {
            int32_t *ids = make_ids(nn[i]);
            /* ⚠️ ここで見るのは「関数 == 実測」であって大きさではない（大きさは §1 / §2）。
             * 静的 arena（176 KB）では 520 ids の duration 一時領域（384 B/id）が入らないので、
             * 緩い上限 saan_stream_arena_needed() の大きさで確保する（T4 で 208 → 176 KB にした際に
             * 520 が init-fail になったため） */
            const size_t cap = saan_stream_arena_needed(nn[i]);
            void *buf = malloc(cap);
            saan_arena A; saan_arena_init(&A, buf, cap);
            saan_stream st;
            saan_status s = saan_stream_init(&st, &W, &A, ids, nn[i], SAAN_S_V);
            if (s != SAAN_OK) { printf("  NG! init(n_ids=%d): %s\n", nn[i], saan_strerror(s)); ++n_ng; }
            else {
                const size_t expect = saan_stream_arena_used(nn[i]);
                const int ng = used_verdict(A.used, expect);
                printf("  %s n_ids %4d : a.used %zu B / saan_stream_arena_used %zu B%s\n",
                       ng ? "NG!" : "OK ", nn[i], A.used, expect,
                       ng ? "  ← 確保一覧と関数がずれている。実機は init 後に拒否する" : "");
                n_ng += ng;
                /* 陽性対照: ±16 B（saan_alloc の最小粒度）ずらすと必ず NG */
                if (!(used_verdict(A.used, expect + 16) && used_verdict(A.used, expect - 16))) ctl_ok = 0;
            }
            free(buf); free(ids);
        }
        printf("  %s 陽性対照: 期待値を ±16 B ずらすと NG になる\n", ctl_ok ? "OK " : "NG!");
        printf("  ⚠️ この値はホストのもの。ターゲットは sizeof(impl) がポインタ幅で変わるので同じ式で自分で計算する\n");
        bad += n_ng + !ctl_ok;
    }

    if (c3) {
        printf("\n⚠️ **既知の未修正欠陥**（このタスクでは直していない — csrc/saanotts.c と\n");
        printf("   csrc/saanotts_stream.c は c'-1/c'-2 が並行で編集中のため触っていない）:\n");
        printf("   `saan_alloc` が失敗しても `used` を進めないので、大きい確保だけ失敗して\n");
        printf("   後続の小さい確保が成功する。`saan_stream_init` は 25 回の確保のうち\n");
        printf("   各グループの最後の 1 個しか NULL 検査していないため、**init が SAAN_OK を\n");
        printf("   返した後に pull の中で NULL 書き込み**になる。\n");
        printf("   修正案: `saan_arena` に粘着フラグ `failed` を足し、`saan_alloc` の\n");
        printf("   先頭で `if (a->failed) return NULL;`、失敗時に `a->failed = 1`。\n");
        printf("   `saan_arena_init` / `saan_arena_reset` で 0 に戻す。1 箇所で済む。\n");
        printf("   ⚠️ 発生するのは **arena が最小値 %zu B を下回るときだけ**なので、\n", min_ok);
        printf("      正しく大きさを取った雛形では踏まない。**それでも直すべき** —\n");
        printf("      ESP32 では「ログを出さずに再起動」に化ける。\n");
    }
    printf("\n%s\n", bad ? "NG: 条件を満たしていない項目がある"
                         : "arena ストレス: すべての条件を満たした");
    free(base); free(wbuf); free(gbuf);
    return bad ? 1 : 0;
}
