#!/bin/bash
# 北极星电力网每日流程: 抓取 -> 简报 -> 静态站点
# 由用户级 systemd 定时器调用(systemctl --user, 见 deploy/), 也可手动执行。
set -uo pipefail
cd "$(dirname "$0")"

export BJX_BASE="${BJX_BASE:-$HOME/bjx/data}"
export BJX_SITE="${BJX_SITE:-$HOME/bjx/site}"

# venv 布局: 脚本同级 .venv(旧扁平布局)或上级目录 .venv(仓库布局 scripts/ 的上一级);
# Linux/macOS 为 bin/, Windows 为 Scripts/
PY=./.venv/bin/python
[ -x "$PY" ] || PY=../.venv/bin/python
[ -x "$PY" ] || PY=./.venv/Scripts/python
[ -x "$PY" ] || PY=python3

echo "[run_daily] $(date '+%F %T') start (BJX_BASE=$BJX_BASE)"

"$PY" crawler.py run          # 无 DISPLAY 时脚本内部自动 xvfb-run 重 exec
rc_crawl=$?
echo "[run_daily] crawler exit=$rc_crawl"

# 抓取失败也要把既有报告发布出去, 故不因上一步失败而中止
"$PY" site.py
rc_site=$?
echo "[run_daily] site exit=$rc_site"

# 可选备份(状态快照/全文包 -> $BJX_OUT): 取消下行注释
# "$PY" pack.py export

echo "[run_daily] $(date '+%F %T') done"
[ "$rc_crawl" -eq 0 ] && [ "$rc_site" -eq 0 ]
