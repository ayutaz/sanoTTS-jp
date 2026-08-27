/* effective clock via a 1-cycle-latency dependent integer add chain */
#include <stdio.h>
#include <time.h>
#include <stdint.h>
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+1e-9*t.tv_nsec;}
int main(void){
  volatile uint64_t sink=0;
  for(int trial=0;trial<5;trial++){
    uint64_t x=1; const long n=2000000000L;
    double t=now();
    for(long i=0;i<n;i++){ asm volatile("add %0, %0, #1":"+r"(x)::); }
    double d=now()-t; sink+=x;
    printf("trial %d: %.4f s for %ld dependent adds -> %.3f GHz\n",trial,d,n,n/d/1e9);
  }
  printf("sink %llu\n",(unsigned long long)sink); return 0;}
