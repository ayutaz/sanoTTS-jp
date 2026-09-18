/* olw を「周期 256 の表」に置き換えると PCM が変わるか。
 * 実装は out[i] = ola[j] / olw[j] で、olw はフレームごとに win[i]^2 を足す**累積和**。
 * 定常部の位置 p への寄与は i = p, p-256, p-512, p-768（フレーム順 = i の降順）。
 * 表を作るときに i の昇順で足すと**順序が変わる**ので、bit 一致するかは実測しかない。 */
#include <stdio.h>
#include <math.h>
#include <string.h>
#define NFFT 1024
#define HOP  256
int main(void){
  static float win[NFFT];
  for (int i=0;i<NFFT;++i) win[i]=0.5f-0.5f*cosf(2.0f*(float)M_PI*(float)i/(float)NFFT);
  int diff_desc_asc=0, worst_i=-1; double worst=0;
  for (int p=0;p<HOP;++p){
    /* (a) 実装と同じ順序: i の降順（フレームが来る順） */
    float a=0.0f;
    for (int i=p+3*HOP; i>=p; i-=HOP) a += win[i]*win[i];
    /* (b) 表を素直に作る順序: i の昇順 */
    float b=0.0f;
    for (int i=p; i<NFFT; i+=HOP) b += win[i]*win[i];
    if (memcmp(&a,&b,sizeof(float))!=0){ ++diff_desc_asc;
      double d=fabs((double)a-(double)b); if(d>worst){worst=d;worst_i=p;} }
  }
  printf("== olw を表にするときの加算順 ==\n");
  printf("降順（実装と同じ）と昇順が違う位置: %d / %d\n", diff_desc_asc, HOP);
  if (diff_desc_asc) printf("  最大 |Δ| %.6g @ p=%d\n", worst, worst_i);
  printf("-> 表は **実装と同じ降順で作らないと PCM が変わる**: %s\n",
         diff_desc_asc ? "はい（順序が効く）" : "いいえ（順序は効かない）");
  /* さらに: 定常部の値は p mod HOP だけで決まるか（= 表 256 個で足りるか） */
  int nonperiodic=0;
  for (int p=0;p<HOP;++p){
    float a=0.0f; for (int i=p+3*HOP;i>=p;i-=HOP) a+=win[i]*win[i];
    float c=0.0f; int q=p+HOP; /* 次の周期の同じ位相 */
    for (int i=q+3*HOP;i>=q;i-=HOP) if(i<NFFT) c+=win[i]*win[i];
    (void)c;
    if (a<=0.0f) ++nonperiodic;
  }
  printf("olw の定常値が 0 以下になる位相: %d / %d（0 なら割り算のガードに掛からない）\n",
         nonperiodic, HOP);
  return 0;}
