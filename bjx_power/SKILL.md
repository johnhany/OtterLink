---
name: beijixing-energy-news
description: 北极星电力资讯——北极星电力网(bjx.com.cn)每日增量抓取体系的运行时技能。当用户要求执行北极星每日抓取任务、恢复bjx_daily抓取环境、或定时任务提到"北极星电力网每日增量抓取"时触发。提供完整运行时(crawler.py等脚本、20栏目配置、格式规范FORMAT.md、重建手册REBUILD.md、每日执行手册daily_task.md)，可将 /mnt/agents/work/bjx_daily/ 一键恢复到可抓取状态。
---

# 北极星电力资讯（beijixing-energy-news）

> 中文称呼：北极星电力资讯

本技能是 bjx_daily 抓取体系的自包含恢复点。工作目录 `/mnt/agents/work/bjx_daily/` 缺失时按本文件恢复。

## 恢复步骤（优先用本技能，而非 /mnt/agents/output 的 runtime.zip）

1. 找到本技能的 .skill 文件（本质是 zip）并解压到工作目录：

```bash
python3 - <<'PY'
import zipfile, glob, os
cands = glob.glob('/app/.user/skills/beijixing-energy-news*') + glob.glob('/mnt/agents/output/*.skill')
print("候选:", cands)
# 若 cands 为空, 改从 /mnt/agents/output/bjx_daily_runtime.zip 恢复
PY
```

2. 标准恢复命令（runtime.zip 与技能包内部结构一致）：

```bash
python3 -c "import zipfile; zipfile.ZipFile('<包路径>').extractall('/mnt/agents/work/bjx_daily')"
bash /mnt/agents/work/bjx_daily/bootstrap.sh
```

3. bootstrap.sh 会自动：恢复 scripts 到工作目录根（见下"目录映射"）→ 恢复快照到 state/ → 校验20栏目 → 就绪。

## 目录映射（技能包内 → /mnt/agents/work/bjx_daily/）

| 技能包内 | 恢复后位置 | 说明 |
| --- | --- | --- |
| scripts/crawler.py | crawler.py | 抓取+简报主程序 |
| scripts/metrics.py | metrics.py | 图片OCR预筛+索引拼图 |
| scripts/pack.py | pack.py | 每日收尾导出 |
| scripts/site.py | site.py | 静态站点生成（简报→HTML，服务器部署用，见 design.md） |
| scripts/run_daily.sh | run_daily.sh | 服务器每日流程编排（服务器部署用） |
| scripts/bootstrap.sh | bootstrap.sh | 自举脚本 |
| references/FORMAT.md | FORMAT.md | 数据格式权威定义 |
| references/REBUILD.md | REBUILD.md | 全灭重建手册 |
| references/daily_task.md | daily_task.md | 每日执行手册 |
| references/columns.json | config/columns.json | 20栏目配置 |

注意：解压后 scripts/ 与 references/ 下的文件需平移到工作目录根（bootstrap.sh 已处理 config/columns.json 的归位；若手动恢复，按上表移动）。

## 每日执行

恢复完成后，严格按 `daily_task.md`（references/daily_task.md）执行 Step 1–5。格式问题查 FORMAT.md；站点结构大变导致解析为空时，改完代码必须重新打包（`python3 pack.py runtime`）。

## 兜底

技能包与 runtime.zip 都丢失时，按 references/REBUILD.md 确定性重放重建（含2026-07实测的WAF绕过参数），禁止即兴发明格式。
