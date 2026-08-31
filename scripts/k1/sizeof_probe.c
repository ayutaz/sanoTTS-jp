/* ESP32-S3 (xtensa, ILP32) 上での構造体サイズを実測する。
 * 各 sizeof をグローバル配列の長さにして、nm/objdump でシンボルサイズを読む。
 * 実行はしない（クロスコンパイルするだけ）。
 */
#include <stddef.h>

/* mecab.h の mecab_node_t をそのまま写す（フィールド順・型を変えない） */
struct mecab_path_t;
typedef struct mecab_node_t {
  struct mecab_node_t  *prev;
  struct mecab_node_t  *next;
  struct mecab_node_t  *enext;
  struct mecab_node_t  *bnext;
  struct mecab_path_t  *rpath;
  struct mecab_path_t  *lpath;
  const char           *surface;
  const char           *feature;
  unsigned int          id;
  unsigned short        length;
  unsigned short        rlength;
  unsigned short        rcAttr;
  unsigned short        lcAttr;
  unsigned short        posid;
  unsigned char         char_type;
  unsigned char         stat;
  unsigned char         isbest;
  float                 alpha;
  float                 beta;
  float                 prob;
  short                 wcost;
  long                  cost;
} mecab_node_t;

typedef struct mecab_path_t {
  struct mecab_node_t* rnode;
  struct mecab_path_t* rnext;
  struct mecab_node_t* lnode;
  struct mecab_path_t* lnext;
  int                  cost;
  float                prob;
} mecab_path_t;

/* dictionary.h の Token */
typedef struct {
  unsigned short lcAttr;
  unsigned short rcAttr;
  unsigned short posid;
  short wcost;
  unsigned int feature;
  unsigned int compound;
} Token;

/* njd.h の NJDNode */
typedef struct _NJDNode {
  char *string;
  char *pos;
  char *pos_group1;
  char *pos_group2;
  char *pos_group3;
  char *ctype;
  char *cform;
  char *orig;
  char *read;
  char *pron;
  int acc;
  int mora_size;
  char *chain_rule;
  int chain_flag;
  struct _NJDNode *prev;
  struct _NJDNode *next;
} NJDNode;

/* jpcommon.h の JPCommonNode */
typedef struct _JPCommonNode {
  char *pron;
  char *pos;
  char *ctype;
  char *cform;
  int acc;
  int chain_flag;
  struct _JPCommonNode *prev;
  struct _JPCommonNode *next;
} JPCommonNode;

char saan_sizeof_mecab_node_t[sizeof(mecab_node_t)];
char saan_sizeof_mecab_path_t[sizeof(mecab_path_t)];
char saan_sizeof_Token[sizeof(Token)];
char saan_sizeof_NJDNode[sizeof(NJDNode)];
char saan_sizeof_JPCommonNode[sizeof(JPCommonNode)];
char saan_sizeof_ptr[sizeof(void *)];
char saan_sizeof_long[sizeof(long)];
