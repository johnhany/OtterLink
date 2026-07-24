# 北极星电力网每日抓取 执行手册（定时任务照此执行）

每日一次（北京时间8:30）。**流程固定，禁止即兴改动**；确需调整时改完必须重新打包 runtime.zip。

## Step 0 自举（必做，<1分钟）

```bash
bash /mnt/agents/work/bjx_daily/bootstrap.sh 2>/dev/null || \
bash <(python3 -c "import zipfile; zipfile.ZipFile('/mnt/agents/output/bjx_daily_runtime.zip').extractall('/mnt/agents/work/bjx_daily'); print('/mnt/agents/work/bjx_daily/bootstrap.sh')")
```

- 环境变量：脚本路径基准已参数化为 `BJX_BASE`（默认 `~/bjx/data`，服务器部署见 design.md）。沙箱内手动执行 Step 1–5 前先 `export BJX_BASE=/mnt/agents/work/bjx_daily`（`bootstrap.sh` 已自动导出）；
- work 目录在 → 校验后直接就绪；
- work 目录不在 → 自动从 `/mnt/agents/output/bjx_daily_runtime.zip` + `bjx_state_latest.json` 恢复；
- runtime.zip 也不在 → 找 `bjx-daily-crawler` 技能的 .skill 文件（zip格式）解压恢复；仍没有 → 按 REBUILD.md 重建，**不得自行发明格式**。

## Step 1 抓取（5–10分钟）

```bash
cd /mnt/agents/work/bjx_daily
nohup python3 crawler.py run > logs/run_$(date +%F).out 2>&1 &
```

轮询 `tail /mnt/agents/work/bjx_daily/logs/$(date +%F).log` 直到出现"=== 完成 ==="。
若异常中断：看日志定位 → 修复 → 重跑（幂等安全）。

## Step 2 失败补抓（通常为0篇）

日志中 `详情页失败(全部候选地址)` 的文章：用浏览器工具访问原文URL，按 FORMAT.md 第2节格式手动存档到 `articles/当月/{ID}.md`，然后：

```bash
python3 crawler.py mark --ids <ID列表>
```

## Step 3 图片表格解析

```bash
python3 /mnt/agents/work/bjx_daily/metrics.py run
```

得到 `metrics/sheets/sheet_XX.jpg` 索引拼图（候选图已按 OCR 预筛压缩，位于 `$BJX_BASE` 下）。逐张读拼图：
- 含表格 → 读原图（`articles/当月/images/`），转录为 Markdown 表格，追加到对应文章末尾 `## 附：图片表格解析`（多张表按图序，标明图号）；带量纲关键数值按 FORMAT.md 第4节 schema 追加到 `metrics/metrics-当月.jsonl`（kind="图片表格"，写入前去重）；
- 折线/柱状图 → 一句趋势描述追加到该节；
- 照片/海报/二维码/纯文字扫描页 → 跳过；
- 模糊不可辨 → 标注"图片清晰度不足，待人工核对"，**不臆造数字**。

## Step 4 简报定稿

若当日有新的表格转录成果，把精选数值写入 `state/briefing_manual.md`（Markdown 表格优先，不出现任何注释标记），然后重建简报：

```bash
python3 briefing.py            # 重建当天简报; 指定日期: python3 briefing.py --date YYYY-MM-DD
```

## Step 5 呈现 + 导出收尾

1. 读取 `briefing/$(date +%F).md`，全文呈现在对话中，复制到 `/mnt/agents/output/北极星电力网每日简报-$(date +%F).md` 并附 KIMI_REF；
2. 导出快照与全文包：

```bash
python3 /mnt/agents/work/bjx_daily/pack.py export
```

3. 回复中列出当日产物清单（简报/全文包/含图包或分卷）。

## 完成标志

- 简报已呈现且 KIMI_REF 已附；
- `bjx_state_latest.json` 的 exported_at 为当天；
- output 中有当日全文包。
