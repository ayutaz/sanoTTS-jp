#include "saanotts_stream.h"
#include <stdio.h>
int main(void){
  for (int n = 1; n <= 350; n = (n==1?8:(n==8?53:(n==53?300:(n==300?350:351)))))
    printf("n_ids %3d : used %6zu / **peak %6zu**\n", n,
           saan_stream_arena_used(n), saan_stream_arena_peak(n));
  return 0;
}
