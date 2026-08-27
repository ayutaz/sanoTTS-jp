#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <time.h>
#define N 256
#define REP 4000000L
#define TRIALS 7
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+1e-9*t.tv_nsec;}
static float af[N],bf[N]; static int16_t a16[N],b16[N]; static int8_t a8[N],b8[N];
__attribute__((noinline)) static float dot_f32(const float*a,const float*b,int n){float s=0;for(int i=0;i<n;i++)s+=a[i]*b[i];return s;}
__attribute__((noinline)) static int32_t dot_s16(const int16_t*a,const int16_t*b,int n){int32_t s=0;for(int i=0;i<n;i++)s+=(int32_t)a[i]*(int32_t)b[i];return s;}
__attribute__((noinline)) static int32_t dot_s8(const int8_t*a,const int8_t*b,int n){int32_t s=0;for(int i=0;i<n;i++)s+=(int32_t)a[i]*(int32_t)b[i];return s;}
static int cmpd(const void*x,const void*y){double a=*(const double*)x,b=*(const double*)y;return a<b?-1:a>b;}
int main(void){
  for(int i=0;i<N;i++){af[i]=(float)((i%7)-3)*0.25f;bf[i]=(float)((i%5)-2)*0.5f;
    a16[i]=(int16_t)((i%17)-8);b16[i]=(int16_t)((i%13)-6);a8[i]=(int8_t)((i%9)-4);b8[i]=(int8_t)((i%11)-5);}
  double macs=(double)REP*N, r[3][TRIALS]; volatile double sink=0;
  for(int t=0;t<TRIALS;t++){
    double s,acc=0; s=now(); for(long i=0;i<REP;i++){af[i&(N-1)]+=1e-9f;__asm__ __volatile__("":::"memory");acc+=dot_f32(af,bf,N);} r[0][t]=macs/(now()-s)/1e6; sink+=acc;
    long a1=0; s=now(); for(long i=0;i<REP;i++){a16[i&(N-1)]^=1;__asm__ __volatile__("":::"memory");a1+=dot_s16(a16,b16,N);} r[1][t]=macs/(now()-s)/1e6; sink+=a1;
    long a2=0; s=now(); for(long i=0;i<REP;i++){a8[i&(N-1)]^=1;__asm__ __volatile__("":::"memory");a2+=dot_s8(a8,b8,N);} r[2][t]=macs/(now()-s)/1e6; sink+=a2;
  }
  const char*nm[3]={"f32","s16","s8 "};
  for(int k=0;k<3;k++){ qsort(r[k],TRIALS,sizeof(double),cmpd);
    printf("%s median %9.1f MMAC/s   min %9.1f  max %9.1f  (n=%d)\n",nm[k],r[k][TRIALS/2],r[k][0],r[k][TRIALS-1],TRIALS);}
  printf("sink %g\n",(double)sink); return 0;}
