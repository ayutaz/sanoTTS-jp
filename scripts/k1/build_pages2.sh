#!/bin/sh
K1_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
set -e
SP=${K1_WORK:-$K1_ROOT/.k1work}
cd "$SP/oj"
clang++ -std=c++11 -O2 -w \
  -DCHARSET_UTF_8 -DDIC_VERSION=102 -DHAVE_CONFIG_H -DMECAB_CHARSET=utf-8 \
  '-DMECAB_DEFAULT_RC="dummy"' -DMECAB_UTF8_USE_ONLY -DMECAB_WITHOUT_SHARE_DIC \
  '-DPACKAGE="open_jtalk"' '-DVERSION="1.11"' \
  -I. -Imecab/src -Itext2mecab -c saan_pages2.cpp -o obj/saan_pages2.o
OBJS=`ls obj/*.o | grep -v saan_probe.o | grep -v saan_pages2.o`
clang++ -o "$SP/saan_pages2" obj/saan_pages2.o $OBJS -liconv
echo "built $SP/saan_pages2"
