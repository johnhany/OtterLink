# 北极星电力网抓取体系 全灭重建手册

仅当以下三者**全部失效**时才需要本手册：
1. `/mnt/agents/output/bjx_daily_runtime.zip`（运行时包）
2. `bjx-daily-crawler` 用户技能
3. 用户本地留存的 runtime.zip 备份

只要任何一个还在：把 runtime.zip 放到 `/mnt/agents/output/`，运行 `bash bootstrap.sh` 即完成恢复，**不需要本手册**。

重建是把已验证的步骤确定性重放，不是重新设计。全程约40~60分钟。

---

## 第0步：确认真的需要重建

```bash
ls /mnt/agents/output/bjx_daily_runtime.zip
ls /mnt/agents/output/bjx_state_latest.json
```

- runtime.zip 在 → 解压到 `/mnt/agents/work/bjx_daily/` 即可，跳到第6步校验；
- 只有 bjx_state_latest.json 在 → 代码需重建，但去重状态可保留（恢复后无需重抓历史）。

## 第1步：环境验证（北极星反爬现状，2026-07 实测）

- 全站（列表页+详情页）有阿里云WAF：直接 HTTP 请求返回约11KB挑战页；
- **有效解法**：Playwright + Chromium 有头模式（`xvfb-run`），**每个URL用全新 browser context**，轮询页面内容最多约16秒，WAF JS挑战自动通过（详情页有时出现滑块验证，自动拖拽通过率不稳定，放弃滑块、重开上下文重试即可，实测100%通过）；
- 每次会话（context）通过的页面数有限，故必须"每篇新上下文"；
- 图片CDN（img01.mybjx.net 等）无防护，可直接 requests 下载；
- 详情页候选地址规律：子站文章 `https://{sub}.bjx.com.cn/news/YYYYMMDD/ID.shtml` 往往也可用主站镜像 `https://news.bjx.com.cn/html/YYYYMMDD/ID.shtml` 访问，脚本按此顺序尝试候选。

验证命令（应当打印 PASS）：
```bash
xvfb-run -a python3 -c "
from playwright.sync_api import sync_playwright
import re,time
with sync_playwright() as p:
    b=p.chromium.launch(headless=False,args=['--no-sandbox','--disable-blink-features=AutomationControlled'])
    ctx=b.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',viewport={'width':1366,'height':900},locale='zh-CN')
    ctx.add_init_script(\"Object.defineProperty(navigator,'webdriver',{get:()=>undefined});\")
    pg=ctx.new_page()
    pg.goto('https://news.bjx.com.cn/',wait_until='domcontentloaded',timeout=45000)
    pg.wait_for_timeout(6000)
    print('PASS' if 'Verification' not in pg.title() else 'FAIL')
    b.close()"
```

## 第2步：建目录与栏目配置

```bash
mkdir -p /mnt/agents/work/bjx_daily/{config,articles,briefing,metrics,logs,state,tools}
```

把 FORMAT.md 第3节的20个栏目写入 `config/columns.json`（结构：`{"columns":[{"id","name","type":main_list|portal,"url","host"(portal必填)}]}`）。

## 第3步：编写 crawler.py

按 FORMAT.md 实现，核心组件：
- `fetch_rendered(url, is_article, max_attempts, wait_s)`：每次新建 context + init_script 隐藏 webdriver，轮询直到拿到真实内容（列表页特征 `cc-list-content` 或≥5个文章链接；详情页特征 `<h1>` 非空 + `cc-article`）；
- 列表解析：主站 `<li><a href title>..</a><span>YYYY-MM-DD</span></li>`；门户页按URL日期过滤；
- 详情解析：标题 `div.cc-headline h1`，时间/来源在 headline 区 span，正文 `div.cc-article`，面包屑 `div.cc-crumbs`；
- 归属规则：子站域名 > 主题栏目(zc/sc/sj/xm/mq/pl/gj/dj/js) > 频道门户 > 要闻；主站文章按面包屑频道名修正（火电/风电/光伏/储能/水电/核电/输配电/电力市场/氢能/环保）；
- 图片下载命名 `{ID}_{序号}`，正文转 Markdown；
- seen.json 增量去重（幂等）；失败文章进 pending_manual.json；
- 简报生成按 FORMAT.md 第5节模板。

## 第4步：首跑验证

```bash
cp /mnt/agents/work/bjx_daily/crawler.py /tmp/crawler.py
python3 /tmp/crawler.py run
```

预期：20个栏目输出增量统计，163±50 篇详情成功，失败≈0，生成当日简报。

## 第5步：恢复历史状态（可选但推荐）

若 `bjx_state_latest.json` 存在：
```bash
python3 -c "
import json
d=json.load(open('/mnt/agents/output/bjx_state_latest.json'))
json.dump(d['seen'],open('/mnt/agents/work/bjx_daily/state/seen.json','w'),ensure_ascii=False,indent=1)
json.dump(d.get('pending',{}),open('/mnt/agents/work/bjx_daily/state/pending_manual.json','w'),ensure_ascii=False,indent=1)"
```
这样历史文章无需重抓。

## 第6步：补齐配套件并打包

编写 metrics.py / pack.py / bootstrap.sh（职责见 FORMAT.md 第1节），然后：
```bash
python3 /mnt/agents/work/bjx_daily/pack.py runtime   # 生成 runtime.zip 到 output
```
并按 daily_task.md 注册为用户技能。

## 第7步：演练

```bash
mv /mnt/agents/work/bjx_daily /tmp/bjx_backup
bash /tmp/bjx_backup/bootstrap.sh run    # 应从runtime.zip恢复并完成当日抓取
diff <(ls /tmp/bjx_backup/articles/*/) <(ls /mnt/agents/work/bjx_daily/articles/*/)  # 应一致
```

## 已知坑（2026-07 实测）

1. 共享 context 抓详情页只有首篇能过WAF——必须每篇新 context；
2. Playwright headless=True 过不了列表页WAF——必须 headless=False + xvfb-run；
3. curl_cffi/纯requests 无法过详情页——只能浏览器渲染；
4. 输出目录单文件上限约100MB——大包必须分卷（pack.py 已处理）；
5. 首次 mkdir 用 `{a,b,c}` 花括号展开在 sh 下不生效——用 bash 或分开写；
6. /mnt/agents/work 跨会话**不保证保留**——每日必须以 bootstrap.sh 自举开始。
