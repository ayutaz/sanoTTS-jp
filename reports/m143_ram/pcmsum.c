/* ストリーミング経路で PCM の FNV-1a を出す。MEM-7 の前後で**同じ値**になること。
 * n_ids を伸ばすため golden の ids を巡回させる（arena_stress と同じ手口）。 */
#include "saanotts.h"
#include "saanotts_stream.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static void*slurp(const char*p,size_t*s){FILE*f=fopen(p,"rb");if(!f){perror(p);exit(1);}
 fseek(f,0,SEEK_END);long n=ftell(f);fseek(f,0,SEEK_SET);void*b=malloc(n);
 if(fread(b,1,n,f)!=(size_t)n){exit(1);}fclose(f);*s=n;return b;}
int main(int argc,char**argv){
  size_t ws,gs;void*wb=slurp(argv[1],&ws),*gb=slurp(argv[2],&gs);
  static saan_weights W,G;
  if(saan_weights_open(&W,wb,ws)!=SAAN_OK){fprintf(stderr,"重み NG\n");return 1;}
  if(saan_weights_open(&G,gb,gs)!=SAAN_OK){fprintf(stderr,"golden NG\n");return 1;}
  uint64_t nb=0;const float*idf=saan_tensor(&G,"in.ids",NULL,NULL,&nb);int bn=nb/4;
  static unsigned char buf[8u<<20];
  int NS[]={53,100,224,350};
  printf("| n_ids | frames | PCM FNV-1a | log_d FNV-1a | d_hat 和 | a.peak | peak() |\n");
  printf("|---:|---:|---|---|---:|---:|---:|\n");
  for(int q=0;q<4;++q){int n=NS[q];
    int32_t*ids=malloc(4*(size_t)n);for(int i=0;i<n;++i)ids[i]=(int32_t)idf[i%bn];
    saan_arena a;saan_arena_init(&a,buf,sizeof buf);
    saan_stream st;
    if(saan_stream_init(&st,&W,&a,ids,n,SAAN_S_V)!=SAAN_OK){printf("| %d | init NG |\n",n);continue;}
    uint64_t hp=1469598103934665603ull,hl=1469598103934665603ull;long dsum=0;
    for(int i=0;i<n;++i)dsum+=st.d_hat[i];
    for(int i=0;i<n;++i){const unsigned char*p=(const unsigned char*)&st.log_d[i];
      for(int k=0;k<4;++k){hl^=p[k];hl*=1099511628211ull;}}
    static float ch[64*SAAN_HOP];int32_t nh=0;long frames=0;
    for(;;){saan_status s=saan_stream_pull(&st,ch,&nh);
      if(s!=SAAN_OK||nh==0)break;frames+=nh;
      size_t ns=(size_t)nh*SAAN_HOP;
      for(size_t i=0;i<ns;++i){float v=ch[i];if(v>1.f)v=1.f;if(v<-1.f)v=-1.f;
        int32_t q16=(int32_t)(v*32767.f);int16_t o=(int16_t)q16;
        const unsigned char*p=(const unsigned char*)&o;
        for(int k=0;k<2;++k){hp^=p[k];hp*=1099511628211ull;}}}
    printf("| %d | %ld | 0x%016llx | 0x%016llx | %ld | %zu | %zu |\n",n,frames,
           (unsigned long long)hp,(unsigned long long)hl,dsum,a.peak,
           saan_stream_arena_peak(n));
    free(ids);}
  return 0;}
