#!/bin/bash
# 北极星电力网每日抓取 自举脚本
# 用法: bash bootstrap.sh [run]
#   无参: 仅恢复/校验运行时环境
#   run : 恢复后执行 python3 crawler.py run
# 设计目标: work目录不存在、或全新沙箱时, 一条命令恢复到可抓取状态。
set -u
BASE="${BJX_BASE:-/mnt/agents/work/bjx_daily}"
export BJX_BASE="$BASE"   # crawler.py 等脚本的路径基准(默认值改为 ~/bjx/data 后, 此处保持沙箱流程兼容)
OUT=/mnt/agents/output
PKG=$OUT/bjx_daily_runtime.zip
SNAP=$OUT/bjx_state_latest.json

echo "[bootstrap] $(date '+%F %T') start"
# 分别创建, 不用花括号展开(/bin/sh不支持)
mkdir -p "$BASE"
mkdir -p "$BASE/config"
mkdir -p "$BASE/articles"
mkdir -p "$BASE/briefing"
mkdir -p "$BASE/metrics"
mkdir -p "$BASE/logs"
mkdir -p "$BASE/state"
mkdir -p "$BASE/tools"

# 1. 恢复运行时代码(若缺失): 解压runtime.zip
if [ ! -f "$BASE/crawler.py" ] || [ ! -f "$BASE/config/columns.json" ]; then
  if [ -f "$PKG" ]; then
    echo "[bootstrap] 恢复运行时: $PKG"
    python3 -c "import zipfile; zipfile.ZipFile('$PKG').extractall('$BASE')"
  else
    echo "[bootstrap] FATAL: 未找到运行时包 ($PKG)"
    echo "[bootstrap] 请按 REBUILD.md 重建, 或将 bjx_daily_runtime.zip 放入 $OUT 后重跑"
    exit 3
  fi
else
  echo "[bootstrap] 运行时已存在, 跳过解压"
fi

# 2. 恢复状态快照: 包内嵌的(自洽恢复点) + output中最新的(每日更新)
#    两者取较大者写入 state/seen.json
python3 - "$BASE" "$SNAP" <<'PY'
import sys, json, os
base, out_snap = sys.argv[1], sys.argv[2]
def load_snap(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return {}
cands = []
for p in (os.path.join(base, "bjx_state_latest.json"), out_snap):
    d = load_snap(p)
    if d.get("seen"): cands.append((len(d["seen"]), d, p))
cur_seen_p = os.path.join(base, "state", "seen.json")
cur = {}
if os.path.exists(cur_seen_p):
    try: cur = json.load(open(cur_seen_p, encoding="utf-8"))
    except Exception: cur = {}
if cands:
    n, data, src = max(cands, key=lambda x: x[0])
    if n > len(cur):
        json.dump(data["seen"], open(cur_seen_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump(data.get("pending", {}), open(os.path.join(base, "state", "pending_manual.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[bootstrap] 快照恢复: %d 篇 (来自 %s)" % (n, os.path.basename(src)))
    else:
        print("[bootstrap] 本地状态更新(%d), 无需恢复快照(%d)" % (len(cur), n))
else:
    print("[bootstrap] 无可用快照, 从空库开始")
# 清理包内嵌快照(已写入state)
emb = os.path.join(base, "bjx_state_latest.json")
if os.path.exists(emb): os.remove(emb)
PY

# 3. 校验
python3 - <<'PY'
import json, os
base = "/mnt/agents/work/bjx_daily"
cols = json.load(open(os.path.join(base, "config", "columns.json"), encoding="utf-8"))["columns"]
seen_p = os.path.join(base, "state", "seen.json")
n = len(json.load(open(seen_p, encoding="utf-8"))) if os.path.exists(seen_p) else 0
print("[bootstrap] 校验: 栏目 %d 个, 已收录 %d 篇" % (len(cols), n))
assert len(cols) == 20, "栏目数异常"
PY

echo "[bootstrap] ready"
if [ "${1:-}" = "run" ]; then
  python3 "$BASE/crawler.py" run
fi
