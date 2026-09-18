#include "saanotts_stream.h"
#include <stdio.h>
int main(void){
  printf("| n_ids | arena_used | arena_peak | arena_needed |\n|---:|---:|---:|---:|\n");
  int NS[]={1,53,224,350};
  for(int i=0;i<4;++i) printf("| %d | %zu | %zu | %zu |\n", NS[i],
      saan_stream_arena_used(NS[i]), saan_stream_arena_peak(NS[i]),
      saan_stream_arena_needed(NS[i]));
  return 0;}
