# 北极星电力网每日资讯 部署技术方案

目标环境：腾讯云香港 CVM（Ubuntu 24.04），域名 `bjx.geekbit.org`（GoDaddy 购买）。
目标效果：工作日早上定时抓取北极星电力网 20 个栏目的 24 小时增量文章，自动生成每日资讯报告，并以网页形式对外展示。

本文档包含三部分：**项目工作流程**、**现有代码和脚本的调整修改**、**部署流程说明**。

---

## 1. 现状分析

`bjx_power/` 是一套已在沙箱环境验证过的抓取体系（2026-07 实测），核心资产：

| 文件 | 作用 | 在新方案中的定位 |
| --- | --- | --- |
| `scripts/crawler.py` | 抓取 20 栏目 24h 增量 + 生成 Markdown 简报 | **核心保留**，改路径配置 |
| `scripts/metrics.py` | 正文图片 OCR 预筛 + 索引拼图 | 保留为可选工具 |
| `scripts/pack.py` | 状态快照/全文包导出（面向沙箱 output 目录） | 改造为备份工具（可选） |
| `scripts/bootstrap.sh` | 沙箱自举恢复 | **不使用**（服务器磁盘持久，无需每日恢复） |
| `references/columns.json` | 20 栏目配置 | 原样使用 |
| `references/FORMAT.md` | 数据格式权威定义 | 原样遵循 |
| `references/REBUILD.md` | 全灭重建手册（含 WAF 绕过参数） | 保留作灾备参考 |
| `references/daily_task.md` | 每日执行手册（含人工/Agent 介入步骤） | **不照搬**，每日流程重新定义为全自动化 |
| `SKILL.md` | 沙箱技能恢复点 | 与新方案无关，保留 |

关键技术约束（来自 REBUILD.md 实测结论，必须继承）：

- 北极星全站有阿里云 WAF，纯 requests/curl_cffi 过不去，**必须 Playwright + Chromium 有头模式（`headless=False`）+ `xvfb-run`**，且每个 URL 用全新 browser context；
- 详情页可能出现滑块验证，策略是放弃滑块、重开上下文重试；
- 图片 CDN（`*.mybjx.net`）无防护，可直接 requests 下载；
- 任务幂等：`state/seen.json` 去重，重跑自动跳过。

`daily_task.md` 中以下步骤依赖人工/Agent 介入，**无人值守部署中裁掉或降级**：

- Step 2 失败补抓（人工过 WAF）→ 失败文章留在 `pending_manual.json`，仅在简报中列出；
- Step 3 图片表格视觉转录 → 服务器上无人读图，只保留 OCR 预筛（可选执行）；
- Step 5 KIMI_REF 呈现 → 由静态网站取代。

## 2. 总体架构与项目工作流程

### 2.1 架构

```
                        ┌──────────────────────────── 腾讯云香港 CVM ───────────────────────────┐
                        │                                                                       │
  北极星电力网 ──抓取──▶ │  bjx-daily.timer (systemd, 周一至周五 08:30 北京时间)                  │
  (bjx.com.cn, WAF)     │       │                                                               │
                        │       ▼                                                               │
                        │  bjx-daily.service → run_daily.sh                                     │
                        │       1. crawler.py run   (xvfb-run 自动包裹, 过WAF抓20栏目)            │
                        │       2. site.py          (Markdown 简报 → 静态HTML)                   │
                        │       3. pack.py export   (可选: 状态备份)                              │
                        │       │                                                               │
                        │       ▼                                                               │
                        │  /opt/bjx/site/  ◀── Nginx (443, HTTPS) ──▶ https://bjx.geekbit.org   │
                        │                                                                       │
                        └───────────────────────────────────────────────────────────────────────┘
```

选型说明：

- **静态站点 + Nginx**，不引入 Flask/FastAPI 等常驻应用服务。每日报告是天然的静态内容，生成式渲染最可靠、零运行时故障面，Nginx 直接出流。
- **systemd timer** 而非 cron：日志走 journalctl，支持 `Persistent=true`（停机错过时点会补跑），失败状态可查。
- HTTPS 用 certbot + Let's Encrypt 自动签发续期。

### 2.2 每日工作流程（全自动，无人介入）

1. **触发**：systemd timer 于北京时间周一至周五 08:30 触发 `bjx-daily.service`。
   说明：systemd/cron 无法感知中国法定节假日，周一至周五全量执行即可——假日照常抓取无害（当天没新闻就产出空简报），比维护节假日历简单可靠。
2. **抓取**（`crawler.py run`，约 5–10 分钟）：
   - 无 DISPLAY 环境，脚本自动 `xvfb-run` 重 exec（现有逻辑直接可用）；
   - 扫 20 个栏目列表页，取 24h 窗口文章，`seen.json` 去重后逐篇抓详情页（每篇新 context 过 WAF）；
   - 文章全文转 Markdown 存 `articles/YYYY-MM/`，图片存 `articles/YYYY-MM/images/`；
   - 失败文章进 `pending_manual.json`；
   - 生成当日简报 `briefing/YYYY-MM-DD.md`（"市场数值"区块自动回退为数值抽取模式，不依赖人工 `briefing_manual.md`）。
3. **站点生成**（新增 `site.py`，秒级）：
   - 读取 `briefing/*.md`，渲染为 HTML 报告页；
   - 生成首页索引（按日期倒序，含篇数/栏目统计、最近运行状态横幅）。
4. **对外服务**：Nginx 静态服务 `/opt/bjx/site/`，报告生成即上线，无需重启任何服务。
5. **收尾（可选）**：`pack.py export` 导出状态快照作本地备份。

### 2.3 服务器目录规划

```
/opt/bjx/                     # 项目根（代码，建议 git 管理）
├── crawler.py  metrics.py  pack.py  site.py  run_daily.sh
├── requirements.txt
├── config/columns.json
├── FORMAT.md  REBUILD.md
├── deploy/                   # nginx 配置、systemd unit 模板
├── .venv/                    # Python 虚拟环境
├── data/                     # $BJX_BASE：运行数据（持久，需备份的就是这里）
│   ├── config/columns.json   # 首次部署时从代码目录复制
│   ├── articles/YYYY-MM/     # 文章全文 + images/
│   ├── briefing/YYYY-MM-DD.md
│   ├── metrics/  logs/  state/
└── site/                     # $BJX_SITE：生成的静态站点（Nginx root）
    ├── index.html
    ├── report/YYYY-MM-DD.html
    └── assets/style.css
```

## 3. 现有代码和脚本的调整修改

原则：**最小改动**。抓取与解析逻辑一行不动，只把沙箱环境的路径假设参数化，并新增站点生成与调度件。

### 3.1 `crawler.py`（2 处改动）

1. **BASE 路径环境变量化**（第 16 行）：

   ```python
   BASE = os.environ.get("BJX_BASE", "/opt/bjx/data")
   ```

   `CONF/STATE_DIR/ART_DIR/BRIEF_DIR/LOG_DIR` 均由 BASE 派生，无需逐个改。`main()` 里的 `os.chdir(BASE)` 保持不变。

2. **配置缺失时的报错文案**（`cmd_run` 开头）：把"请先运行 bootstrap.sh"改为"请确认 config/columns.json 已部署到 $BJX_BASE/config/"，去掉沙箱指引。

保留不动的关键逻辑：

- `__main__` 中无 DISPLAY 自动 `xvfb-run` 重 exec——服务器上正是无头环境，这段是方案成立的支点；
- 有头 Chromium + 每 URL 新 context + `webdriver` 隐藏 + WAF 轮询（`fetch_rendered`）；
- 简报生成 `build_briefing`：无 `state/briefing_manual.md` 时自动用 `numbers` 抽取兜底，正好匹配无人值守场景。

注意：`crawler.py` 内部 `import requests`、`from playwright.sync_api import ...`，依赖见 3.6。

### 3.2 `metrics.py`（1 处改动，定位调整为可选工具）

- 同样改 `BASE = os.environ.get("BJX_BASE", "/opt/bjx/data")`。
- 无人值守时"索引拼图供视觉判读"没有执行者，**不进每日主流程**；保留命令行手动执行能力（`python3 metrics.py run --date ...`），供日后需要时做图片 OCR 预筛。
- 若执行，需系统包 `tesseract-ocr`、`tesseract-ocr-chi-sim` 和 Pillow。

### 3.3 `pack.py`（1 处改动，定位调整为备份工具）

- `OUT = os.environ.get("BJX_OUT", "/opt/bjx/backup")`。
- 每日流程**不依赖**它（服务器磁盘持久，不存在沙箱销毁问题）；可作为每日/每周备份手段：`pack.py export` 生成状态快照与当日全文包到 `$BJX_OUT`，再按需 rsync 到异地或腾讯云 COS。
- `MAX_OUT` 的 95MB 分卷限制是沙箱产物，服务器上无意义，但保留无害，不改。
- `RUNTIME_FILES` 清单同步加入 `site.py`、`run_daily.sh`，保持运行时恢复包与技能包结构一致。

### 3.4 `bootstrap.sh` / `daily_task.md` / `SKILL.md` / `FORMAT.md`

- 均为沙箱环境专用，**不进入部署流程**，仓库保留作灾备与语义存档。服务器重建按第 4 节部署流程重放即可；代码全灭时按 `REBUILD.md` + 本文件重建。
- 因 BASE 默认值改为 `/opt/bjx/data`，做了保持沙箱流程可用的兼容性改动，不涉及逻辑变化：`bootstrap.sh` 开头导出 `BJX_BASE`（默认仍为沙箱路径）；`daily_task.md` Step 0 补充环境变量说明；`SKILL.md` 映射表与 `FORMAT.md` 目录清单登记新增的 `site.py`、`run_daily.sh`。

### 3.5 `config/columns.json`、`FORMAT.md`

- 零改动。栏目增删仍只改 `columns.json`。

### 3.6 新增文件

1. **`requirements.txt`**

   ```
   playwright
   requests
   Pillow
   markdown
   ```

   （Pillow 仅 metrics.py 需要；`markdown` 供 site.py 渲染，用 `tables` 扩展渲染简报中的表格。）

2. **`site.py`（核心新增，约 150 行）**——静态站点生成器：

   - 输入：`$BJX_BASE/briefing/*.md`；
   - 用 `markdown` 库（`extensions=["tables", "toc"]`）把每份简报渲染为 `site/report/YYYY-MM-DD.html`（套用统一 HTML 模板 + `assets/style.css`，简报内的原文链接保持外链到北极星）；
   - 生成 `site/index.html`：报告按日期倒序列表，每条展示日期、收录篇数、待补抓数（从简报的引用行正则解析，或渲染时顺手从 `seen.json` 统计）；顶部横幅展示最近一次抓取时间与成功/失败状态（读取当日日志尾部 `=== 完成` 行判断），便于一眼巡检；
   - 幂等：每次全量重渲染，秒级完成。
   - 不渲染文章全文页（简报已含原文外链）。如需站内全文阅读，二期再扩展 `articles/` 渲染 + 图片复制，静态生成成本同样很低。

3. **`run_daily.sh`**——每日流程编排（systemd service 的 ExecStart）：

   ```bash
   #!/bin/bash
   set -uo pipefail
   export BJX_BASE=/opt/bjx/data
   export BJX_SITE=/opt/bjx/site
   cd /opt/bjx
   .venv/bin/python crawler.py run        # 内部自动 xvfb-run
   .venv/bin/python site.py
   # 可选: .venv/bin/python pack.py export
   ```

   注意 crawler.py 失败（非零退出）也要继续跑 site.py 把既有报告发布出去，因此不用 `set -e`，逐命令判状态写日志。

4. **`deploy/bjx.nginx.conf`**（见 4.6）与 **`deploy/bjx-daily.service` / `bjx-daily.timer`**（见 4.7）。

## 4. 部署流程说明

### 4.0 前置条件

- 腾讯云香港 CVM：建议 **2C4G、60GB+ 系统盘**（Chromium 渲染并发多页，2G 内存偏紧；文章+图片日积月累，留足磁盘），Ubuntu 24.04 LTS 镜像；
- 腾讯云安全组放行：22（SSH，建议限源 IP）、80、443；
- 已知服务器公网 IP。

### 4.1 DNS 配置（GoDaddy）

1. 登录 GoDaddy → 域名 `geekbit.org` → DNS 管理；
2. 添加记录：类型 `A`，主机名 `bjx`，值 = CVM 公网 IP，TTL 默认（600s）；
3. 生效验证（本地执行）：`dig +short bjx.geekbit.org` 返回服务器 IP 后再进行 4.6 的证书签发。

### 4.2 服务器初始化

```bash
sudo apt update && sudo apt -y upgrade
sudo timedatectl set-timezone Asia/Shanghai   # 关键: 定时与"北京时间"口径一致(香港同为UTC+8, 显式设置更稳)
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable

# 专用运行用户与目录
sudo useradd -r -s /bin/bash bjx || true
sudo mkdir -p /opt/bjx/{data/{config,articles,briefing,metrics,logs,state},site,backup}
```

### 4.3 安装系统依赖

```bash
sudo apt install -y python3 python3-venv python3-pip \
    xvfb xauth \
    tesseract-ocr tesseract-ocr-chi-sim \
    fonts-dejavu-core fonts-noto-cjk \
    nginx
```

- `xvfb`：有头 Chromium 过 WAF 的必需品；
- `tesseract` + 中文语言包：仅 metrics.py 可选流程用；
- `fonts-noto-cjk`：避免 Chromium 渲染中文页面出方块字（影响页面解析稳定性与截图）。

### 4.4 部署代码与 Python 环境

```bash
# 本地（开发机）执行: 上传仓库 bjx_power 目录
rsync -avz bjx_power/ user@<server>:/tmp/bjx_power/

# 服务器执行: 按部署目录结构归位(scripts/ 平铺到 /opt/bjx/)
sudo mkdir -p /opt/bjx/config /opt/bjx/deploy
sudo cp /tmp/bjx_power/scripts/{crawler.py,metrics.py,pack.py,site.py,run_daily.sh,bootstrap.sh} /opt/bjx/
sudo cp /tmp/bjx_power/requirements.txt /opt/bjx/
sudo cp /tmp/bjx_power/references/columns.json /opt/bjx/config/
sudo cp /tmp/bjx_power/references/{FORMAT.md,REBUILD.md} /opt/bjx/
sudo cp /tmp/bjx_power/deploy/* /opt/bjx/deploy/
sudo cp /opt/bjx/config/columns.json /opt/bjx/data/config/columns.json
sudo chmod +x /opt/bjx/run_daily.sh
cd /opt/bjx
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo .venv/bin/playwright install chromium
sudo .venv/bin/playwright install-deps chromium   # 装 Chromium 系统级依赖
sudo chown -R bjx:bjx /opt/bjx
```

### 4.5 首跑验证（关键里程碑）

```bash
sudo -u bjx -H bash -c 'cd /opt/bjx && BJX_BASE=/opt/bjx/data .venv/bin/python crawler.py run'
```

预期（对照 REBUILD.md 第4步）：20 个栏目输出增量统计，详情成功率接近 100%，生成 `data/briefing/$(date +%F).md`。

**重点验证香港 IP 能否过 WAF**：北极星 WAF 对海外 IP 的策略可能比境内更严。若首跑大面积 `详情页失败`：

- 先看重试后成功率（脚本自带每篇最多 2 次 × 多候选地址）；
- 持续失败则考虑：给爬虫配置境内出口代理（Playwright launch 加 `proxy` 参数，改动集中在 `browser()`）；或改用腾讯云境内 CVM（注意境内服务器绑定域名需 ICP 备案，香港则无此要求——这也是选香港的主要原因）。

验证通过后手动跑一次站点生成：`sudo -u bjx .venv/bin/python site.py`，确认 `/opt/bjx/site/index.html` 产出。

### 4.6 Nginx + HTTPS

`/etc/nginx/sites-available/bjx`（模板入 `deploy/bjx.nginx.conf`）：

```nginx
server {
    listen 80;
    server_name bjx.geekbit.org;
    root /opt/bjx/site;
    index index.html;
    location / { try_files $uri $uri/ =404; }
    location ~* \.(css|js|png|jpg|jpeg|gif|webp)$ { expires 7d; }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/bjx /etc/nginx/sites-enabled/bjx
sudo nginx -t && sudo systemctl reload nginx
# DNS 已生效后签证书, 自动改 443 配置与跳转
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d bjx.geekbit.org --agree-tos -m <邮箱> --redirect
sudo systemctl enable --now certbot.timer   # 自动续期
```

### 4.7 定时任务（systemd timer）

`deploy/bjx-daily.service`：

```ini
[Unit]
Description=BJX daily crawl + briefing + site render
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=bjx
Environment=BJX_BASE=/opt/bjx/data BJX_SITE=/opt/bjx/site
ExecStart=/opt/bjx/run_daily.sh
```

`deploy/bjx-daily.timer`：

```ini
[Unit]
Description=BJX daily crawl timer (weekday mornings, Asia/Shanghai)

[Timer]
OnCalendar=Mon..Fri 08:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo cp /opt/bjx/deploy/bjx-daily.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bjx-daily.timer
systemctl list-timers bjx-daily.timer   # 确认下次触发时间
```

### 4.8 端到端验收

```bash
sudo systemctl start bjx-daily.service                 # 手动触发一次完整流程
journalctl -u bjx-daily.service -e                     # 查日志
tail /opt/bjx/data/logs/$(date +%F).log                # 应见 "=== 完成 ==="
```

浏览器访问 `https://bjx.geekbit.org`：首页出现当日报告条目，点进报告页内容完整、表格正常渲染，验收通过。

## 5. 运维与风险

| 事项 | 说明与对策 |
| --- | --- |
| WAF/海外 IP 风险 | 最大不确定项，4.5 首跑即验证；失败按 4.5 的代理/境内机房预案处理。 |
| 站点改版 | 北极星改页面结构会导致解析为空，`crawler.py` 日志有 `解析为空告警`；巡检看首页"最近运行状态"横幅 + `data/logs/`。修复后格式按 FORMAT.md 演进。 |
| 失败补抓 | 无人值守不做人工补抓；`pending_manual.json` 中的失败文章次日重跑会自然再试（不在 seen 中），WAF 抖动可自愈。 |
| 磁盘增长 | 每日约百篇文章+图片，年增数 GB；需要时加 retention 脚本（如保留 12 个月 articles、90 天日志）或挂数据盘。 |
| 备份 | 真正不可再生的状态只有 `data/state/seen.json` 与 `data/articles/`；用 `pack.py export`（已改 `$BJX_OUT`）+ rsync/COS 做周期备份。 |
| 节假日 | timer 周一至周五全跑，节假日产出空/少简报，无害。 |
| 告警（可选二期） | service 失败时 `OnFailure=` 触发邮件/ webhook 通知；或首页状态横幅人工巡检。 |

## 6. 工作量汇总

| 项 | 改动量 |
| --- | --- |
| crawler.py / metrics.py / pack.py | 各 1–2 行路径参数化 + 文案 |
| site.py | 新增（约 150 行） |
| run_daily.sh / requirements.txt / deploy 模板 | 新增小件 |
| 服务器部署与首跑验证 | 约 1–2 小时（含 WAF 验证） |
