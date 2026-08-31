#!/bin/sh
K1_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# saan_probe をビルドする。DEFS は pyopenjtalk の
# lib/open_jtalk/src/build/CMakeFiles/openjtalk.dir/flags.make と同じものを使う。
# .c は C として、.cpp は C++ として別々にコンパイルする（open_jtalk 本来のビルドと同じ）。
set -e
SP=${K1_WORK:-$K1_ROOT/.k1work}
cd "$SP/oj"
mkdir -p obj

DEFS='-DCHARSET_UTF_8 -DDIC_VERSION=102 -DHAVE_CONFIG_H -DMECAB_CHARSET=utf-8 -DMECAB_UTF8_USE_ONLY -DMECAB_WITHOUT_SHARE_DIC'
INC='-I. -Imecab/src -Itext2mecab -Injd -Ijpcommon -Imecab2njd -Injd2jpcommon -Injd_set_pronunciation -Injd_set_digit -Injd_set_accent_phrase -Injd_set_accent_type -Injd_set_unvoiced_vowel -Injd_set_long_vowel'

CFILES="mecab/src/saan_touch.c text2mecab/text2mecab.c njd/njd.c njd/njd_node.c
        jpcommon/jpcommon.c jpcommon/jpcommon_label.c jpcommon/jpcommon_node.c
        mecab2njd/mecab2njd.c njd2jpcommon/njd2jpcommon.c
        njd_set_pronunciation/njd_set_pronunciation.c njd_set_digit/njd_set_digit.c
        njd_set_accent_phrase/njd_set_accent_phrase.c njd_set_accent_type/njd_set_accent_type.c
        njd_set_unvoiced_vowel/njd_set_unvoiced_vowel.c njd_set_long_vowel/njd_set_long_vowel.c"

CPPFILES="mecab/src/char_property.cpp mecab/src/connector.cpp mecab/src/context_id.cpp
          mecab/src/dictionary.cpp mecab/src/dictionary_rewriter.cpp
          mecab/src/feature_index.cpp mecab/src/iconv_utils.cpp mecab/src/lbfgs.cpp
          mecab/src/learner_tagger.cpp mecab/src/libmecab.cpp mecab/src/nbest_generator.cpp
          mecab/src/param.cpp mecab/src/string_buffer.cpp mecab/src/tagger.cpp
          mecab/src/tokenizer.cpp mecab/src/utils.cpp mecab/src/viterbi.cpp
          mecab/src/writer.cpp mecab/src/dictionary_compiler.cpp mecab/src/dictionary_generator.cpp"

OBJS=""
for f in $CFILES; do
  o="obj/$(echo "$f" | tr '/' '_').o"
  clang -std=c99 -O2 -w $DEFS $INC -c "$f" -o "$o"
  OBJS="$OBJS $o"
done
for f in $CPPFILES; do
  o="obj/$(echo "$f" | tr '/' '_').o"
  clang++ -std=c++11 -O2 -w $DEFS '-DMECAB_DEFAULT_RC="dummy"' '-DPACKAGE="open_jtalk"' '-DVERSION="1.11"' $INC -c "$f" -o "$o"
  OBJS="$OBJS $o"
done
clang++ -std=c++11 -O2 -w $DEFS '-DMECAB_DEFAULT_RC="dummy"' '-DPACKAGE="open_jtalk"' '-DVERSION="1.11"' $INC -c saan_probe.cpp -o obj/saan_probe.o
clang++ -o "$SP/saan_probe" obj/saan_probe.o $OBJS -liconv
echo "built $SP/saan_probe"
