#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
静态站点生成: 将每日简报 Markdown 渲染为 HTML, 并生成首页索引。
用法:
  python3 site.py
环境变量:
  BJX_BASE  数据目录(默认 /opt/bjx/data), 读取 briefing/ 与 logs/
  BJX_SITE  站点输出目录(默认 /opt/bjx/site), 即 Nginx root
幂等: 每次全量重渲染, 秒级完成。部署与定时调度见 design.md。
"""
import os, sys, re, glob, datetime

BASE = os.environ.get("BJX_BASE", "/opt/bjx/data")
SITE = os.environ.get("BJX_SITE", "/opt/bjx/site")
BRIEF_DIR = os.path.join(BASE, "briefing")
LOG_DIR = os.path.join(BASE, "logs")

DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
HDR_RE = re.compile(r"生成时间：(.+?)（北京时间）.*?收录文章：(\d+) 篇.*?待人工补抓：(\d+) 篇", re.S)

def bj_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="{css_href}">
</head>
<body>
<header class="top"><a href="{home_href}">北极星电力网每日资讯</a></header>
<main>
{body}
</main>
<footer>数据来源：<a href="https://www.bjx.com.cn/">北极星电力网</a> ｜ 页面生成于 {gen}（北京时间）</footer>
</body>
</html>
"""

CSS = """* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; color: #24292f; background: #f6f8fa; line-height: 1.7; }
header.top { background: #1f6feb; padding: 14px 20px; }
header.top a { color: #fff; text-decoration: none; font-size: 18px; font-weight: 600; }
main { max-width: 880px; margin: 24px auto; padding: 0 16px; }
article, .card { background: #fff; border: 1px solid #d0d7de; border-radius: 8px; padding: 20px 28px; }
h1 { font-size: 24px; }
h2 { font-size: 20px; border-bottom: 1px solid #d0d7de; padding-bottom: 6px; margin-top: 32px; }
h3 { font-size: 17px; }
a { color: #1f6feb; }
table { border-collapse: collapse; margin: 12px 0; }
th, td { border: 1px solid #d0d7de; padding: 6px 14px; }
blockquote { margin: 10px 0; padding: 4px 14px; color: #57606a; border-left: 4px solid #d0d7de; background: #f6f8fa; }
.status { border-radius: 8px; padding: 12px 18px; margin-bottom: 16px; font-size: 15px; }
.status.ok { background: #dafbe1; border: 1px solid #2da44e; }
.status.warn { background: #fff8c5; border: 1px solid #d4a72c; }
ul.reports { list-style: none; padding: 0; }
ul.reports li { background: #fff; border: 1px solid #d0d7de; border-radius: 8px; margin: 10px 0; padding: 12px 18px; display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
ul.reports a { font-size: 17px; font-weight: 600; text-decoration: none; }
ul.reports .meta { color: #57606a; font-size: 14px; }
footer { text-align: center; color: #57606a; font-size: 13px; padding: 24px; }
"""

def render_md(text):
    import markdown
    return markdown.markdown(text, extensions=["tables"], output_format="html5")

def parse_header(text):
    """从简报引用行解析 生成时间/收录篇数/待补抓数(格式见 FORMAT.md 第5节)。"""
    m = HDR_RE.search(text)
    if not m:
        return {"gen": "", "articles": "?", "pending": "?"}
    return {"gen": m.group(1), "articles": m.group(2), "pending": m.group(3)}

def last_run_status():
    """最新一份日志中是否有完成标记, 用于首页状态横幅。"""
    logs = sorted(glob.glob(os.path.join(LOG_DIR, "*.log")))
    if not logs:
        return None
    day = os.path.basename(logs[-1])[:-4]
    try:
        txt = open(logs[-1], encoding="utf-8").read()
    except Exception:
        return None
    return (day, "=== 完成" in txt)

def main():
    try:
        import markdown  # noqa: F401
    except ImportError:
        sys.stderr.write("缺少依赖 markdown, 请先: pip install markdown\n")
        sys.exit(2)
    reports = []
    out_dir = os.path.join(SITE, "report")
    os.makedirs(out_dir, exist_ok=True)
    for path in sorted(glob.glob(os.path.join(BRIEF_DIR, "*.md")), reverse=True):
        m = DAY_RE.match(os.path.basename(path))
        if not m:
            continue
        day = m.group(1)
        text = open(path, encoding="utf-8").read()
        info = parse_header(text)
        page = PAGE.format(title="北极星电力网每日简报（%s）" % day,
                           css_href="../assets/style.css",
                           home_href="../index.html",
                           body="<article>%s</article>" % render_md(text),
                           gen=bj_now().strftime("%Y-%m-%d %H:%M"))
        with open(os.path.join(out_dir, day + ".html"), "w", encoding="utf-8") as f:
            f.write(page)
        reports.append((day, info))

    assets = os.path.join(SITE, "assets")
    os.makedirs(assets, exist_ok=True)
    with open(os.path.join(assets, "style.css"), "w", encoding="utf-8") as f:
        f.write(CSS)

    # 首页: 状态横幅 + 报告索引(日期倒序)
    st = last_run_status()
    if st is None:
        banner = '<p class="status warn">暂无抓取日志。</p>'
    elif st[1]:
        banner = '<p class="status ok">最近抓取（%s）已完成。</p>' % st[0]
    else:
        banner = '<p class="status warn">最近抓取（%s）未见完成标记，请检查服务器日志。</p>' % st[0]
    items = []
    for day, info in reports:
        items.append('<li><a href="report/%s.html">%s</a>'
                     '<span class="meta">收录 %s 篇 ｜ 待补抓 %s 篇</span></li>'
                     % (day, day, info["articles"], info["pending"]))
    body = ["<h1>每日资讯报告</h1>", banner]
    if items:
        body.append('<ul class="reports">%s</ul>' % "\n".join(items))
    else:
        body.append("<p>暂无报告。</p>")
    page = PAGE.format(title="北极星电力网每日资讯",
                       css_href="assets/style.css",
                       home_href="index.html",
                       body="\n".join(body),
                       gen=bj_now().strftime("%Y-%m-%d %H:%M"))
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print("站点已生成: %s (报告 %d 份)" % (SITE, len(reports)))

if __name__ == "__main__":
    main()
