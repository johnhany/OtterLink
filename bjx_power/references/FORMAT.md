# 北极星电力网抓取体系 数据格式规范

本文件是"既有格式"的唯一权威定义。任何恢复、重建、格式调整都必须与本文件保持一致。
修改格式时：先改本文件 → 改 crawler.py → 版本号 +1。

格式版本：v1.2（2026-07-18）

---

## 1. 目录结构

```
/mnt/agents/work/bjx_daily/
├── crawler.py            # 抓取+简报生成（主程序）
├── metrics.py            # 图片OCR预筛+索引拼图
├── pack.py               # 每日收尾导出（快照/全文包/运行时包）
├── site.py               # 静态站点生成（服务器部署用，见 design.md）
├── run_daily.sh          # 服务器每日流程编排（服务器部署用）
├── bootstrap.sh          # 每日自举恢复脚本
├── FORMAT.md             # 本文件
├── REBUILD.md            # 全灭重建手册
├── daily_task.md         # 定时任务执行手册（每日照此执行）
├── config/columns.json   # 20个栏目清单
├── articles/YYYY-MM/     # 文章Markdown全文（文件名=文章ID.md）
│   └── images/           # 正文图片（文件名=文章ID_序号.扩展名）
├── briefing/YYYY-MM-DD.md  # 每日简报
├── metrics/metrics-YYYY-MM.jsonl  # 结构化数值指标
├── logs/YYYY-MM-DD.log   # 运行日志
└── state/
    ├── seen.json             # 已抓文章库（增量去重的核心状态）
    ├── pending_manual.json   # 待人工补抓队列
    ├── briefing_manual.md    # 人工整理的"市场数值"区块（注入简报用）
    └── img_analysis-YYYY-MM-DD.json  # 当日图片预筛结果
```

## 2. 文章 Markdown 格式（articles/YYYY-MM/{文章ID}.md）

文章ID：北极星原文URL中的数字编号（如 `https://news.bjx.com.cn/html/20260717/1504789.shtml` → ID `1504789`）。全站唯一，跨栏目共享。

文件以 YAML front matter 开头（三个短横线包围），字段顺序与含义固定：

```markdown
---
id: "1504789"                # 文章ID（字符串）
title: "标题"
date: "2026-07-17 18:42"     # 发布时间（分钟精度）
source: "北极星输配电网"      # 来源
author: "Bing"               # 作者（可空）
column: "要闻"               # 归属栏目名（见 columns.json）
column_id: "yw"              # 归属栏目ID
channel: "输配电"            # 面包屑频道（可空）
url: "https://..."           # 原文URL
keywords: ["特高压", "换流阀"] # 关键词数组
images: 3                    # 正文图片数
crawled_at: "2026-07-18T14:17:44+08:00"  # 抓取时间(ISO8601)
---

正文Markdown……图片以 ![](images/1504789_1.png) 引用。

## 附：图片表格解析        ← 仅当正文图片含表格/图表时由人工解析后追加

**图1（表格）**：表名说明

| 列1 | 列2 |
| --- | --- |
| … | … |
```

正文图片存于 `articles/YYYY-MM/images/`，文件名 `{文章ID}_{序号}.{扩展名}`（序号从1起）。

## 3. 栏目清单（config/columns.json）

20个栏目，两种类型：
- `main_list`：主站列表页（news.bjx.com.cn/xx/），逐条带日期，翻页抓24h窗口；
- `portal`：频道门户页（xx.bjx.com.cn），按文章URL中的日期（/YYYYMMDD/ID.shtml）过滤24h窗口，URL主机决定栏目归属。

栏目ID与中文名固定：yw要闻 zc政策 sc市场 sj数据 xm项目 mq企业 pl评论 gj国际 dj独家 js技术 huodian火电 fd风电 guangfu光伏 chuneng储能 shuidian水电 hedian核电 shupeidian输配电 shoudian电力市场 qn氢能 huanbao环保。

增删栏目只改 columns.json，不改代码。

## 4. metrics JSONL（metrics/metrics-YYYY-MM.jsonl）

每行一个 JSON 对象，字段固定：

```json
{"date": "2026-07-17", "article_id": "1504642", "column": "光伏", "kind": "图片表格",
 "metric": "指标名", "value": 156.0104, "unit": "MW", "note": "备注(可空串)"}
```

- `kind` 目前恒为 `"图片表格"`（图片转录来源标注），后续如加正文抽取可用其他取值；
- `value` 为数字（不带千分位）；`unit` 用原文量纲（万元/元/W/MW/%/万千瓦时/吨…）；
- 追加写入前须按 (article_id, metric, kind) 去重，保证幂等。

## 5. 每日简报（briefing/YYYY-MM-DD.md）

章节顺序固定：

1. `# 北极星电力网每日简报（YYYY-MM-DD）` + 生成时间/栏目数/篇数/待补抓数
2. `## 今日概览`：栏目×篇数表格
3. `## 要闻精选`：≤10条，每条 `- **[标题](原文URL)**（栏目 ｜ 时间）` + 引文摘要
4. `## 市场数值`：优先采用图片表格转录成果（由 state/briefing_manual.md 注入；无人工内容时回退为自动抽取）。同质数据一律用 Markdown 表格展示，**正文中不得出现 MANUAL:START/END 等注释标记**
5. `## 各栏目文章清单`：按栏目分组的 `- [标题](URL)（时间）` 列表
6. `## 待人工补抓（详情页WAF）`：仅当有失败时出现

## 6. 状态快照（/mnt/agents/output/bjx_state_latest.json）

每日导出的恢复用状态：

```json
{"exported_at": "...", "date": "YYYY-MM-DD",
 "seen": {"1504789": {"title","url","date","column_id","column_name","status",
                       "channel","file","images","lead","numbers"(近7天保留)}},
 "pending": {...}}
```

- 保留窗口：去重ID近30天，简报重建字段近7天；
- bootstrap.sh 恢复时把它写回 state/seen.json + state/pending_manual.json。

## 7. 交付物（/mnt/agents/output/）

| 文件 | 内容 | 更新频率 |
| --- | --- | --- |
| `北极星电力网每日简报-YYYY-MM-DD.md` | 当日简报 | 每日 |
| `北极星电力网文章全文-YYYY-MM-DD.zip` | 当日新增文章Markdown（纯文本） | 每日 |
| `北极星电力网文章全文含图-YYYY-MM-DD.zip[.partNN]` | 全文+图片（>95MB时分卷） | 每日 |
| `bjx_state_latest.json` / `bjx_state_YYYY-MM-DD.json` | 状态快照 | 每日 |
| `bjx_daily_runtime.zip` (+.md5) | 运行时包（代码+配置+文档+快照） | 内容变化时 |
| `bjx-daily-crawler.skill` | 同上的技能封装 | 内容变化时 |
