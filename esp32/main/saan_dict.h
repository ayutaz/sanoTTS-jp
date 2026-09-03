/* K-7: 辞書パーティションを mmap して K-1 の blob を開く。
 *
 * ⚠️ **`model` と同じ理由で 64 KB 境界が要る**（partitions_16mb.csv）。
 * ⚠️ 辞書は 12 MB 以上ある。**RAM には読み込まない** — flash から直接引く。
 */
#ifndef SAAN_DICT_H
#define SAAN_DICT_H

#include <stdbool.h>
#include "jdict.h"

/* dict パーティションを mmap して開く。失敗したら false。 */
bool saan_dict_open(jdict_t *d);
void saan_dict_close(void);

#endif /* SAAN_DICT_H */
