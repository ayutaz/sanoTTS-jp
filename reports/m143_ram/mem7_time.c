#define _POSIX_C_SOURCE 199309L
#include "saanotts.h"
#include "saanotts_internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static double ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
  return t.tv_sec*1e3+t.tv_nsec/1e6;}
static void*slurp(const char*p,size_t*s){FILE*f=fopen(p,"rb");fseek(f,0,SEEK_END);
  long n=ftell(f);fseek(f,0,SEEK_SET);void*b=malloc(n);fread(b,1,n,f);fclose(f);*s=n;return b;}
#define WD SAAN_DUR_W
static void zo(float*x,int Tw,int lo,int n){for(int j=0;j<Tw;++j){int t=lo+j;
  if(t>=0&&t<n)continue;for(int c=0;c<WD;++c)x[(size_t)c*Tw+j]=0.f;}}
static saan_status dw(const saan_weights*w,saan_arena*a,const int32_t*ids,int n,float*ld,int K){
  const float*emb=saan_tf(w,"duration.emb.weight");const saan_wref pw=saan_w(w,"duration.proj.weight");
  const float*pb=saan_tf(w,"duration.proj.bias");
  for(int s=0;s<n;s+=K){int kk=(n-s<K)?(n-s):K;int lo=s-12,Tw=kk+24;size_t mk=a->used;
    float*h=saan_alloc(a,sizeof(float)*WD*Tw),*t1=saan_alloc(a,sizeof(float)*WD*Tw);
    if(!h||!t1)return SAAN_ERR_ARENA;
    for(int j=0;j<Tw;++j){int t=lo+j;if(t<0||t>=n){for(int c=0;c<WD;++c)h[(size_t)c*Tw+j]=0.f;continue;}
      for(int c=0;c<WD;++c)h[(size_t)c*Tw+j]=emb[(size_t)ids[t]*WD+c];}
    for(int bi=0;bi<3;++bi){
      const saan_wref c1=saan_w(w,"duration.blocks.%d.c1.weight",bi);
      const float*c1b=saan_tf(w,"duration.blocks.%d.c1.bias",bi);
      const saan_wref c2=saan_w(w,"duration.blocks.%d.c2.weight",bi);
      const float*c2b=saan_tf(w,"duration.blocks.%d.c2.bias",bi);
      const float*ng=saan_tf(w,"duration.blocks.%d.norm.weight",bi);
      const float*nb=saan_tf(w,"duration.blocks.%d.norm.bias",bi);
      const float*gm=saan_tf(w,"duration.blocks.%d.gamma",bi);
      SAAN_TRY(saan_conv1d_w(t1,h,c1,c1b,WD,WD,5,Tw,a));saan_relu(t1,(size_t)WD*Tw);zo(t1,Tw,lo,n);
      float*t2=saan_alloc(a,sizeof(float)*WD*Tw);if(!t2)return SAAN_ERR_ARENA;
      SAAN_TRY(saan_conv1d_w(t2,t1,c2,c2b,WD,WD,5,Tw,a));saan_layernorm_c(t2,ng,nb,WD,Tw);
      for(size_t i=0;i<(size_t)WD*Tw;++i)h[i]+=gm[0]*t2[i];zo(h,Tw,lo,n);
      a->used-=SAAN_ALIGN16(sizeof(float)*(size_t)WD*Tw);}
    SAAN_TRY(saan_conv1d_wr(ld+s,h,pw,pb,WD,1,1,Tw,12,12+kk,a));a->used=mk;}
  return SAAN_OK;}
int main(int c,char**v){size_t ws,gs;void*wb=slurp(v[1],&ws),*gb=slurp(v[2],&gs);
  static saan_weights W,G;saan_weights_open(&W,wb,ws);saan_weights_open(&G,gb,gs);
  uint64_t nb=0;const float*idf=saan_tensor(&G,"in.ids",NULL,NULL,&nb);int bn=nb/4;
  static unsigned char buf[4u<<20];
  printf("| n_ids | 一括版 ms | K=128 ms | K=32 ms | K=128/一括 | K=32/一括 |\n|---:|---:|---:|---:|---:|---:|\n");
  int NS[]={53,224,350};
  for(int q=0;q<3;++q){int n=NS[q];
    int32_t*ids=malloc(4*n);for(int i=0;i<n;++i)ids[i]=(int32_t)idf[i%bn];
    float*o=malloc(4*n);const int R=200;double t0,ta,tb,tc;
    saan_arena a;
    saan_arena_init(&a,buf,sizeof buf);t0=ms();
    for(int r=0;r<R;++r){a.used=0;saan_run_duration(&W,&a,ids,n,o);}ta=ms()-t0;
    saan_arena_init(&a,buf,sizeof buf);t0=ms();
    for(int r=0;r<R;++r){a.used=0;dw(&W,&a,ids,n,o,128);}tb=ms()-t0;
    saan_arena_init(&a,buf,sizeof buf);t0=ms();
    for(int r=0;r<R;++r){a.used=0;dw(&W,&a,ids,n,o,32);}tc=ms()-t0;
    printf("| %d | %.3f | %.3f | %.3f | %.2fx | %.2fx |\n",n,ta/R,tb/R,tc/R,tb/ta,tc/ta);
    free(ids);free(o);}
  return 0;}
