# bjx_power

北极星电力网（bjx.com.cn）每日资讯自动抓取与展示系统：工作日早上定时抓取 20 个栏目的 24 小时增量文章，生成每日资讯报告（Markdown 简报），并以静态网站形式对外展示。

- 所属仓库：[OtterLink](../README.md)（个人工具项目合集，本项目为其子项目之一）

- 生产环境：腾讯云香港 CVM（Ubuntu 24.04）+ Nginx + systemd 用户级 timer
- 线上地址：<https://bjx.geekbit.org>
- 路径原则：**所有文件读写都在用户目录（`~/bjx/`）下进行**，不依赖 `/opt`、`/tmp`，避免文件权限问题
- 本地开发同时支持 Windows 与 macOS
- 完整技术方案（架构、改动清单、风险与运维）见 [design.md](design.md)

## 项目说明

### 每日工作流程

1. systemd 用户级 timer 于北京时间周一至周五 08:30 触发 `run_daily.sh`；
2. `crawler.py run`：Playwright 有头 Chromium + `xvfb-run` 绕过阿里云 WAF（每篇文章用全新 browser context），抓取 20 个栏目的 24h 增量文章，全文转 Markdown 存档，并生成当日简报；
3. `site.py`：把简报渲染为静态 HTML——报告页 + 首页索引 + 运行状态横幅；配置 `BJX_LLM_*` 后，首页每份日报附 LLM 生成的摘要与 3 条推荐链接（按日缓存，见“环境变量”一节）；
4. Nginx 静态服务站点目录，报告生成即上线，无需重启任何服务。

任务幂等：`state/seen.json` 记录已抓文章 ID，重跑自动跳过；失败文章进入 `pending_manual.json`，次日自然重试。

### 目录结构

```
bjx_power/
├── scripts/
│   ├── crawler.py      # 抓取 + 简报生成（主程序）
│   ├── site.py         # 静态站点生成（简报 → HTML）
│   ├── run_daily.sh    # 每日流程编排（由 systemd 用户级 timer 调用，可手动执行）
│   ├── metrics.py      # 正文图片 OCR 预筛（可选工具）
│   ├── pack.py         # 状态快照/全文包导出（备份工具）
│   └── bootstrap.sh    # 沙箱自举恢复（灾备参考，部署不使用）
├── references/
│   ├── columns.json    # 20 栏目配置（增删栏目只改此文件）
│   ├── FORMAT.md       # 数据格式权威定义
│   ├── REBUILD.md      # 全灭重建手册（含 WAF 绕过实测参数）
│   └── daily_task.md   # 沙箱每日执行手册
├── deploy/             # Nginx 配置与 systemd 用户级 unit 模板
├── design.md           # 部署技术方案
├── requirements.txt
└── SKILL.md            # 沙箱技能恢复点
```

### 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BJX_BASE` | `~/bjx/data` | 运行数据目录（config / articles / briefing / state / logs） |
| `BJX_SITE` | `~/bjx/site` | 静态站点输出目录（Nginx root） |
| `BJX_OUT` | `~/bjx/backup` | `pack.py` 备份导出目录 |
| `BJX_LLM_BASE_URL` | 未设置 | OpenAI 兼容接口地址（第三方平台，如 `https://api.example.com/v1`） |
| `BJX_LLM_API_KEY` | 未设置 | 上述接口的 API Key |
| `BJX_LLM_MODEL` | 未设置 | 上述接口的模型名 |
| `BJX_NO_XVFB` | 未设置 | 设为 `1` 时跳过 crawler 的 `xvfb-run` 自动包裹（Windows / macOS / 桌面环境本地调试用） |

三个 `BJX_LLM_*` 变量配齐后，`site.py` 会为每份日报生成首页摘要与 3 条推荐链接，结果缓存于 `$BJX_BASE/summaries/YYYY-MM-DD.json`，每日仅对缺失缓存的简报调用一次 LLM；未配置或调用失败则跳过，不影响站点生成。
注意：服务器上定时任务由 systemd 用户 service 触发，不读取 shell 环境变量，需在 `~/.config/systemd/user/bjx-daily.service` 的 `[Service]` 段追加 `Environment=BJX_LLM_BASE_URL=...` 等三行（含 API Key，勿提交到仓库）。

`~` 在各平台自动解析：Linux/macOS 为 `$HOME`，Windows 为 `C:\Users\<用户>`。

## 本地部署（Windows / macOS）

前置：安装 [uv](https://docs.astral.sh/uv/)，Python 版本固定用 3.12。Windows 下命令均在 Git Bash 中执行。

### 1. 创建环境并安装依赖（两系统相同）

```bash
cd bjx_power
uv venv --python 3.12 .venv
uv pip install -r requirements.txt        # 自动装入 ./.venv
```

### 2. 安装 Chromium

```bash
# Windows:
.venv/Scripts/python -m playwright install chromium
# macOS:
.venv/bin/python -m playwright install chromium
```

### 3. 准备数据目录并运行

```bash
mkdir -p local_data/config
cp references/columns.json local_data/config/
export BJX_BASE=$PWD/local_data
export BJX_SITE=$PWD/local_site
export BJX_NO_XVFB=1        # Windows 与 macOS 都没有 xvfb；本地有显示器，直接跑有头 Chromium

# Windows:
.venv/Scripts/python scripts/crawler.py run     # 抓取 + 生成当日简报
.venv/Scripts/python scripts/site.py            # 渲染静态站点
# macOS:
.venv/bin/python scripts/crawler.py run
.venv/bin/python scripts/site.py
```

说明：

- Linux 桌面环境命令同 macOS，同样设 `BJX_NO_XVFB=1`；Linux 无头环境安装 `xvfb` 后无需该变量（脚本自动包裹）；
- 抓取受北极星 WAF 影响，本地网络与服务器表现可能不同，属正常现象；重跑幂等。

### 4. 查看站点

浏览器直接打开 `local_site/index.html`，或起本地服务：

```bash
# Windows:
.venv/Scripts/python -m http.server 8000 -d local_site    # http://localhost:8000
# macOS:
.venv/bin/python -m http.server 8000 -d local_site
```

## 腾讯云部署（生产）

> 以下为部署要点，完整说明见 [design.md](design.md) 第 4 节。所有文件都在登录用户家目录（`~/bjx/`），仅标注 sudo 的步骤需要 root。

### 前置条件

- 腾讯云香港 CVM：Ubuntu 24.04，建议 2C4G、60GB+ 磁盘；安全组放行 22（建议限源 IP）/80/443；
- GoDaddy DNS：`geekbit.org` 添加 A 记录，主机名 `bjx`，值为服务器公网 IP，`dig +short bjx.geekbit.org` 生效后再签证书。

### 1. 服务器初始化

```bash
sudo apt update && sudo apt -y upgrade
sudo timedatectl set-timezone Asia/Shanghai    # 定时口径=北京时间
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable
```

### 2. 系统依赖

```bash
sudo apt install -y git python3 python3-venv python3-pip \
    xvfb xauth \
    tesseract-ocr tesseract-ocr-chi-sim \
    fonts-dejavu-core fonts-noto-cjk \
    nginx
```

### 3. 部署代码与 Python 环境

代码通过 GitHub 同步：服务器克隆本仓库到 `~/bjx/repo/`，后续更新只需 `git pull`。

```bash
# 服务器执行：全部在用户目录，无需 sudo
git clone https://github.com/johnhany/OtterLink.git ~/bjx/repo
# 若仓库为私有，需先在服务器配置 GitHub SSH key / deploy key，
# 并改用 SSH 地址：git clone git@github.com:johnhany/OtterLink.git ~/bjx/repo

# 目录约定：代码 ~/bjx/repo/bjx_power/，数据 ~/bjx/data/，站点 ~/bjx/site/，备份 ~/bjx/backup/
mkdir -p ~/bjx/data/{config,articles,briefing,metrics,logs,state} ~/bjx/site ~/bjx/backup
cp ~/bjx/repo/bjx_power/references/columns.json ~/bjx/data/config/
chmod +x ~/bjx/repo/bjx_power/scripts/run_daily.sh
cd ~/bjx/repo/bjx_power
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
sudo .venv/bin/playwright install-deps chromium    # Chromium 系统级依赖（仅这步需 sudo）
```

### 4. 首跑验证（关键：确认香港 IP 能过 WAF）

```bash
cd ~/bjx/repo/bjx_power && .venv/bin/python scripts/crawler.py run
cd ~/bjx/repo/bjx_power && .venv/bin/python scripts/site.py
```

预期：20 个栏目输出增量统计，详情成功率接近 100%，生成 `~/bjx/data/briefing/$(date +%F).md` 与 `~/bjx/site/index.html`。
若大面积 `详情页失败`，按 design.md 4.5 节预案处理（境内出口代理或改用境内机房）。

### 5. Nginx + HTTPS

```bash
chmod o+x "$HOME"      # nginx(www-data) 需能进入用户目录
sed "s/<USER>/$USER/g" ~/bjx/repo/bjx_power/deploy/bjx.nginx.conf | sudo tee /etc/nginx/sites-available/bjx
sudo ln -s /etc/nginx/sites-available/bjx /etc/nginx/sites-enabled/bjx
sudo nginx -t && sudo systemctl reload nginx

sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d bjx.geekbit.org --agree-tos -m <邮箱> --redirect
sudo systemctl enable --now certbot.timer     # 自动续期
```

Nginx root 为 `~/bjx/site/`（即 `BJX_SITE` 默认值），与代码仓库位置无关，无需改动模板中的路径。

### 6. 定时任务（systemd 用户级 timer）

```bash
mkdir -p ~/.config/systemd/user
cp ~/bjx/repo/bjx_power/deploy/bjx-daily.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now bjx-daily.timer
sudo loginctl enable-linger "$USER"           # 登出后定时器照常触发（仅这步需 sudo）
systemctl --user list-timers bjx-daily.timer  # 确认下次触发时间
```

service 模板中 `ExecStart=%h/bjx/repo/bjx_power/scripts/run_daily.sh`，与上述克隆路径一致；`run_daily.sh` 自动使用同级或上级目录的 `.venv`（即 `~/bjx/repo/bjx_power/.venv`）。

### 7. 代码更新

```bash
cd ~/bjx/repo && git pull                     # 拉取最新代码
# 依赖变更时：cd ~/bjx/repo/bjx_power && .venv/bin/pip install -r requirements.txt
# 仅样式/模板变更（site.py）想立即生效，可手动重渲染（次日定时任务也会自动渲染）：
~/bjx/repo/bjx_power/.venv/bin/python ~/bjx/repo/bjx_power/scripts/site.py
```

### 8. 验收与日常运维

```bash
systemctl --user start bjx-daily.service       # 手动触发一次完整流程
journalctl --user -u bjx-daily.service -e      # 查运行日志
tail ~/bjx/data/logs/$(date +%F).log           # 应见 "=== 完成 ==="
```

浏览器访问 <https://bjx.geekbit.org>，首页出现当日报告即验收通过。日常巡检看首页顶部"最近抓取"状态横幅即可。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [design.md](design.md) | 部署技术方案：架构、工作流程、代码改动清单、风险与运维 |
| [references/FORMAT.md](references/FORMAT.md) | 数据格式权威定义（文章/简报/状态/栏目） |
| [references/REBUILD.md](references/REBUILD.md) | 全灭重建手册（含 2026-07 实测 WAF 绕过参数） |
