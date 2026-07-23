#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
静态站点生成: 将每日简报 Markdown 渲染为 HTML, 并生成首页索引。
用法:
  python3 site.py
环境变量:
  BJX_BASE  数据目录(默认 ~/bjx/data), 读取 briefing/ 与 logs/, 摘要缓存写入 summaries/
  BJX_SITE  站点输出目录(默认 ~/bjx/site), 即 Nginx root
  BJX_LLM_BASE_URL / BJX_LLM_API_KEY / BJX_LLM_MODEL
            OpenAI 兼容接口(第三方平台)配置, 三者配齐才为首页生成日报摘要,
            结果按日缓存于 summaries/YYYY-MM-DD.json, 每日仅对缺失缓存的简报调用一次
幂等: 每次全量重渲染; 未配置 LLM 时跳过摘要, 秒级完成。部署与定时调度见 design.md。
"""
import os, sys, re, glob, json, html, shutil, datetime

BASE = os.environ.get("BJX_BASE") or os.path.expanduser(os.path.join("~", "bjx", "data"))
SITE = os.environ.get("BJX_SITE") or os.path.expanduser(os.path.join("~", "bjx", "site"))
BRIEF_DIR = os.path.join(BASE, "briefing")
LOG_DIR = os.path.join(BASE, "logs")
SUM_DIR = os.path.join(BASE, "summaries")

LLM_BASE = os.environ.get("BJX_LLM_BASE_URL", "").rstrip("/")
LLM_KEY = os.environ.get("BJX_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("BJX_LLM_MODEL", "")

DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
HDR_RE = re.compile(r"生成时间：(.+?)（北京时间）.*?收录文章：(\d+) 篇.*?待人工补抓：(\d+) 篇", re.S)
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

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

CSS = """:root {
  --bg: #f3f5f9; --card: #ffffff; --text: #1f2430; --muted: #64748b;
  --line: #e6e9f0; --brand: #2563eb; --brand-dark: #1e40af;
  --radius: 12px; --shadow: 0 1px 2px rgba(16, 24, 40, .06), 0 1px 3px rgba(16, 24, 40, .08);
}
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
       color: var(--text); background: var(--bg); line-height: 1.75; }
header.top { background: linear-gradient(120deg, var(--brand-dark), var(--brand));
             padding: 16px 24px; box-shadow: 0 1px 4px rgba(15, 23, 42, .25); }
header.top a { color: #fff; text-decoration: none; font-size: 19px; font-weight: 600; letter-spacing: .5px; }
main { max-width: 920px; margin: 28px auto 48px; padding: 0 16px; }
article, .card { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius);
                 box-shadow: var(--shadow); padding: 28px 32px; }
h1 { font-size: 25px; margin: 4px 0 14px; letter-spacing: .3px; }
h2 { font-size: 20px; margin-top: 36px; padding-left: 12px; border-left: 4px solid var(--brand); line-height: 1.35; }
h3 { font-size: 17px; }
a { color: var(--brand); text-decoration: none; }
a:hover { text-decoration: underline; }
img { max-width: 100%; }
table { border-collapse: collapse; margin: 16px 0; border-radius: 10px; overflow: hidden;
        box-shadow: 0 0 0 1px var(--line); }
th { background: #f1f5f9; font-weight: 600; color: #334155; text-align: left; }
th, td { border: none; border-bottom: 1px solid var(--line); padding: 9px 18px; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:nth-child(even) { background: #f8fafc; }
tbody tr:hover { background: #eff6ff; }
blockquote { margin: 10px 0 18px; padding: 10px 16px; color: var(--muted); font-size: 14.5px;
             border-left: 3px solid #c7d2fe; background: #f8fafc; border-radius: 0 8px 8px 0; }
article > blockquote:first-of-type { background: #eef4ff; border-left-color: var(--brand);
             color: #3b4b6b; border-radius: 8px; padding: 12px 18px; font-size: 14px; }
article ul { padding-left: 22px; }
article li { margin-bottom: 14px; }
article li strong a { font-size: 16px; }
.status { border: 1px solid; border-radius: var(--radius); padding: 13px 18px; margin: 0 0 18px;
          font-size: 15px; box-shadow: var(--shadow); }
.status.ok   { background: #ecfdf3; border-color: #a6e9c1; color: #067647; }
.status.warn { background: #fffaeb; border-color: #fedf89; color: #b54708; }
.status.ok::before   { content: "✓ "; font-weight: 700; }
.status.warn::before { content: "⚠ "; font-weight: 700; }
p.dl { text-align: right; margin: 0 0 12px; font-size: 14px; }
ul.reports { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }
ul.reports li { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius);
                box-shadow: var(--shadow); margin: 0; padding: 14px 20px;
                transition: transform .15s ease, box-shadow .15s ease; }
ul.reports li:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(16, 24, 40, .10); }
ul.reports .row { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
ul.reports .row > a { font-size: 17px; font-weight: 600; text-decoration: none; }
ul.reports .meta { color: var(--muted); font-size: 13.5px; background: #f1f5f9;
                   border-radius: 999px; padding: 3px 12px; }
ul.reports .summary { margin: 8px 0 0; font-size: 14.5px; color: #414c5e; }
ul.reports .picks { margin: 6px 0 0; font-size: 13.5px; color: var(--muted); }
footer { text-align: center; color: var(--muted); font-size: 13px; padding: 28px 16px;
         border-top: 1px solid var(--line); }
@media (max-width: 640px) {
  article, .card { padding: 20px; }
  h1 { font-size: 21px; }
}
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

def llm_ready():
    return bool(LLM_BASE and LLM_KEY and LLM_MODEL)

def gen_summary(day, text):
    """调用 OpenAI 兼容接口生成 摘要+3条推荐, 成功返回 dict, 失败返回 None(不写缓存, 下次重试)。"""
    links = LINK_RE.findall(text)
    if not links:
        return None
    prompt = (
        "你是电力行业资讯编辑。以下是北极星电力网 %s 的每日简报（Markdown 格式）。\n"
        "请完成两件事：\n"
        "1. 用 80~120 字中文概括当日电力行业动态要点；\n"
        "2. 从简报中的链接里选出 3 条最有价值的资讯（政策、市场、重大项目优先）。\n"
        "只输出 JSON，不要输出其他内容：\n"
        '{"summary": "概括文字", "picks": [{"title": "原文标题", "url": "原文链接"}, ...]}\n'
        "要求：picks 恰好 3 条，url 必须逐字取自简报中的链接，不得编造。\n\n"
        "简报全文：\n%s"
    ) % (day, text[:24000])
    try:
        import requests
        r = requests.post(LLM_BASE + "/chat/completions",
                          headers={"Authorization": "Bearer " + LLM_KEY},
                          json={"model": LLM_MODEL, "temperature": 0.3,
                                "messages": [{"role": "user", "content": prompt}]},
                          timeout=90)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        sys.stderr.write("[site] LLM 调用失败(%s): %s\n" % (day, e))
        return None
    m = re.search(r"\{.*\}", content, re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except Exception:
        data = None
    if not isinstance(data, dict):
        sys.stderr.write("[site] LLM 返回非 JSON(%s), 跳过\n" % day)
        return None
    summary = str(data.get("summary") or "").strip()
    title_of = {u: t for t, u in links}
    picks = []
    for p in data.get("picks") or []:
        if not isinstance(p, dict):
            continue
        u = str(p.get("url") or "").strip()
        if u in title_of:  # 只接受简报中真实存在的链接, 防止幻觉
            picks.append({"title": str(p.get("title") or "").strip() or title_of[u], "url": u})
        if len(picks) == 3:
            break
    if not summary or not picks:
        sys.stderr.write("[site] LLM 结果校验失败(%s): 摘要为空或无有效推荐链接\n" % day)
        return None
    return {"summary": summary, "picks": picks}

def load_summary(day, text):
    """读取缓存的日报摘要; 无缓存且已配置 LLM 时生成并写缓存。"""
    path = os.path.join(SUM_DIR, day + ".json")
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            return None
    if not llm_ready():
        return None
    os.makedirs(SUM_DIR, exist_ok=True)
    s = gen_summary(day, text)
    if s:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
    return s

def main():
    try:
        import markdown  # noqa: F401
    except ImportError:
        sys.stderr.write("缺少依赖 markdown, 请先: pip install markdown\n")
        sys.exit(2)
    if (LLM_BASE or LLM_KEY or LLM_MODEL) and not llm_ready():
        sys.stderr.write("[site] 提示: BJX_LLM_BASE_URL/BJX_LLM_API_KEY/BJX_LLM_MODEL 未配齐, 跳过日报摘要\n")
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
        info["sum"] = load_summary(day, text)
        # 原始 Markdown 随 HTML 一并发布, 供页面提供下载
        shutil.copyfile(path, os.path.join(out_dir, day + ".md"))
        page = PAGE.format(title="北极星电力网每日简报（%s）" % day,
                           css_href="../assets/style.css",
                           home_href="../index.html",
                           body='<p class="dl"><a href="%s.md" download>下载 Markdown 原文</a></p>'
                                "<article>%s</article>" % (day, render_md(text)),
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
        row = ('<div class="row"><a href="report/%s.html">%s</a>'
               '<span class="meta">收录 %s 篇 ｜ 待补抓 %s 篇 ｜ '
               '<a href="report/%s.md" download>Markdown</a></span></div>'
               % (day, day, info["articles"], info["pending"], day))
        extra = ""
        s = info.get("sum")
        if s:
            picks = " ｜ ".join('<a href="%s">%s</a>' % (html.escape(p["url"], quote=True),
                                                        html.escape(p["title"]))
                               for p in s["picks"])
            extra = ('<p class="summary">%s</p><p class="picks">推荐：%s</p>'
                     % (html.escape(s["summary"]), picks))
        items.append("<li>%s%s</li>" % (row, extra))
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
