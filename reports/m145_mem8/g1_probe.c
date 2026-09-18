/* stream_test の G1 と同じ計算を、held-out コーパス無しで再現する
 * （G1 は ids_heldout.bin が要るが、G1 自身は 350 ids を巡回させるだけ）。 */
#include "saanotts_stream.h"
#include <stdio.h>
#include <stdlib.h>
static void*slurp(const char*p,size_t*s){FILE*f=fopen(p,"rb");if(!f){perror(p);exit(1);}
 fseek(f,0,SEEK_END);long n=ftell(f);fseek(f,0,SEEK_SET);void*b=malloc(n);
 if(fread(b,1,n,f)!=(size_t)n)exit(1);fclose(f);*s=n;return b;}
int main(int argc,char**argv){
  size_t ws,gs;void*wb=slurp(argv[1],&ws),*gb=slurp(argv[2],&gs);
  static saan_weights W,G;saan_weights_open(&W,wb,ws);saan_weights_open(&G,gb,gs);
  uint64_t nb=0;const float*idf=saan_tensor(&G,"in.ids",NULL,NULL,&nb);int bn=nb/4;
  const int G1_IDS=350;
  int32_t*ids=malloc(4*(size_t)G1_IDS);
  for(int i=0;i<G1_IDS;++i)ids[i]=(int32_t)idf[i%bn];
  static unsigned char buf[8u<<20];
  saan_arena a;saan_arena_init(&a,buf,sizeof buf);
  saan_stream st;
  if(saan_stream_init(&st,&W,&a,ids,G1_IDS,SAAN_S_V)!=SAAN_OK){puts("init NG");return 1;}
  static float tmp[SAAN_CHUNK*SAAN_HOP];int32_t k;
  while(saan_stream_pull(&st,tmp,&k)==SAAN_OK&&k>0){}
  const size_t FFT_STACK=4224, total=st.peak_used+FFT_STACK;
  printf("W8A8=%d: peak_used %zu + FFT stack %zu = **%zu B（%.1f KB）**\n",
#ifdef SAAN_INT8_ACT
    SAAN_INT8_ACT,
#else
    0,
#endif
    st.peak_used,FFT_STACK,total,(double)total/1024.0);
  int ks[]={116,118,119,148,200};
  for(int i=0;i<5;++i)
    printf("  --g1-kb %3d（%d B）-> %s\n",ks[i],ks[i]*1024,
           total < (size_t)ks[i]*1024u ? "OK" : "**NG（落ちる）**");
  return 0;}
