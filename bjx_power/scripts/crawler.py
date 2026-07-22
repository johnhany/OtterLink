#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
北极星电力网 每日增量抓取脚本
用法:
  python3 crawler.py run                 # 抓取20个栏目过去24小时增量, 生成当日Markdown简报
  python3 crawler.py mark --ids ID...    # 将手动补抓的文章ID标记为已抓
配套脚本:
  metrics.py run                         # 当日新增图片OCR预筛+索引拼图(图片表格解析第一步)
  pack.py export                         # 导出状态快照/全文包/更新运行时包到 $BJX_OUT（默认 /opt/bjx/backup）
  bootstrap.sh                           # 每日自举: 恢复运行时→校验→运行(见 REBUILD.md)
任务幂等: 已抓取的文章ID存于 state/seen.json, 重跑自动跳过。
"""
import os, sys, json, re, time, random, hashlib, argparse, datetime, subprocess

BASE = os.environ.get("BJX_BASE", "/opt/bjx/data")
CONF = os.path.join(BASE, "config", "columns.json")
STATE_DIR = os.path.join(BASE, "state")
SEEN_FILE = os.path.join(STATE_DIR, "seen.json")
PENDING_FILE = os.path.join(STATE_DIR, "pending_manual.json")
ART_DIR = os.path.join(BASE, "articles")
BRIEF_DIR = os.path.join(BASE, "briefing")
LOG_DIR = os.path.join(BASE, "logs")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
ART_RE = re.compile(r"https?://[^\s\"'<>]+/(\d{8})/(\d+)\.shtml")

# ---------- 时区与时间 ----------
def bj_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))

def today_str():
    return bj_now().strftime("%Y-%m-%d")

def cutoff_dates():
    """过去24小时窗口: 今天与昨天(按文章URL/列表日期)。"""
    t = bj_now().date()
    y = t - datetime.timedelta(days=1)
    return {t.strftime("%Y%m%d"), y.strftime("%Y%m%d")}, {t.isoformat(), y.isoformat()}

# ---------- 日志 ----------
_log_fp = None
def log(msg):
    global _log_fp
    line = "[%s] %s" % (bj_now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    if _log_fp is None:
        os.makedirs(LOG_DIR, exist_ok=True)
        _log_fp = open(os.path.join(LOG_DIR, today_str() + ".log"), "a", encoding="utf-8")
    _log_fp.write(line + "\n"); _log_fp.flush()

# ---------- 状态 ----------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)

# ---------- Playwright 抓取 ----------
_pw = None
_browser = None

def browser():
    global _pw, _browser
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    return _browser

def shutdown():
    global _pw, _browser
    try:
        if _browser: _browser.close()
        if _pw: _pw.stop()
    except Exception:
        pass
    _pw = _browser = None

def fetch_rendered(url, is_article, max_attempts=3, wait_s=16):
    """新上下文抓取渲染后的HTML. 校验通过返回html, 否则None."""
    for att in range(1, max_attempts + 1):
        ctx = None
        try:
            ctx = browser().new_context(
                user_agent=UA, viewport={"width": 1366, "height": 900}, locale="zh-CN")
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
            pg = ctx.new_page()
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            deadline = time.time() + wait_s
            while time.time() < deadline:
                try:
                    h = pg.content()
                except Exception:
                    time.sleep(1.0); continue
                if "aliyun_waf" in h[:3000]:
                    time.sleep(1.1); continue
                if is_article:
                    if re.search(r"<h1[^>]*>\s*[^<]", h) and "cc-article" in h:
                        return h
                else:
                    if len(ART_RE.findall(h)) >= 5 or "cc-list-content" in h:
                        return h
                time.sleep(1.1)
        except Exception as e:
            log("  渲染异常(%s att%d): %s" % (url[:60], att, str(e)[:60]))
        finally:
            try:
                if ctx: ctx.close()
            except Exception:
                pass
        time.sleep(random.uniform(0.8, 1.8))
    return None

def fetch_plain(url, referer=None):
    """图片等静态资源的直接下载。"""
    import requests
    hdr = {"User-Agent": UA}
    if referer: hdr["Referer"] = referer
    r = requests.get(url, headers=hdr, timeout=30)
    r.raise_for_status()
    return r.content

# ---------- 列表解析 ----------
def parse_main_list(html):
    """主站栏目列表: <li><a href title>..</a><span>YYYY-MM-DD</span></li>"""
    out = []
    for m in re.finditer(
            r'<li><a href="(https?://[^"]+/\d{8}/\d+\.shtml)"[^>]*title="([^"]*)"[^>]*>.*?</a><span>(\d{4}-\d{2}-\d{2})</span></li>',
            html, re.S):
        url, title, d = m.group(1), m.group(2).strip(), m.group(3)
        dm = ART_RE.search(url)
        if dm:
            out.append({"id": dm.group(2), "url": url, "title": title, "date": d})
    return out

def parse_portal(html, host, date8_set):
    """频道门户页: 按URL日期过滤当日/昨日文章链接。"""
    found = {}
    for m in re.finditer(
            r'<a[^>]+href="(https?://[^"]+/\d{8}/\d+\.shtml)"[^>]*?(?:title="([^"]*)")?[^>]*>(.*?)</a>',
            html, re.S):
        url, tattr, text = m.group(1), (m.group(2) or "").strip(), re.sub(r"<[^>]+>", "", m.group(3)).strip()
        dm = ART_RE.search(url)
        if not dm or dm.group(1) not in date8_set:
            continue
        if url.split("/")[2] == "ex.bjx.com.cn":
            continue
        title = tattr or text
        aid = dm.group(2)
        if aid not in found or (len(title) > len(found[aid]["title"])):
            found[aid] = {"id": aid, "url": url, "title": title,
                          "date": "%s-%s-%s" % (dm.group(1)[:4], dm.group(1)[4:6], dm.group(1)[6:])}
    return list(found.values())

# ---------- 详情页解析 ----------
def _clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

def parse_article(html):
    art = {}
    m = re.search(r'<div class="cc-headline">.*?<h1>(.*?)</h1>', html, re.S)
    art["title"] = _clean(re.sub(r"<[^>]+>", "", m.group(1))) if m else ""
    if not art["title"]:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        art["title"] = _clean(re.sub(r"<[^>]+>", "", m.group(1))) if m else ""
    head = html[html.find('class="cc-headline"'):html.find('class="cc-headline"') + 3000]
    m = re.search(r"<span>(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)</span>", head)
    art["datetime"] = m.group(1) if m else ""
    art["date"] = art["datetime"][:10] if art["datetime"] else ""
    m = re.search(r"<span>来源：([^<]*)</span>", head)
    art["source"] = _clean(m.group(1)) if m else ""
    m = re.search(r"<span>作者：([^<]*)</span>", head)
    art["author"] = _clean(m.group(1)) if m else ""
    kw = re.search(r'<span id="key_word">(.*?)</span>', head, re.S)
    art["keywords"] = re.findall(r">([^<>]+)</a>", kw.group(1)) if kw else []
    # 面包屑(频道归属)
    crumbs = []
    cm = re.search(r'<div class="cc-crumbs">(.*?)</div>\s*<div class="cc-headline">', html, re.S)
    if cm:
        crumbs = [_clean(re.sub(r"<[^>]+>", "", x)) for x in re.findall(r"<em>(.*?)</em>", cm.group(1), re.S)]
    art["breadcrumb"] = [c for c in crumbs if c and c != "正文"]
    # 正文
    body_m = re.search(r'<div class="cc-article">(.*?)<div class="cc-article-source-title', html, re.S)
    if not body_m:
        body_m = re.search(r'<div class="cc-article">(.*?)(?:<div class="cc-detail-contact|<div data-expose)', html, re.S)
    body = body_m.group(1) if body_m else ""
    return art, body

def body_to_markdown(body, aid, img_dir, img_rel, referer):
    """正文HTML转Markdown; 图片下载到img_dir, 返回(md文本, 图片清单)。"""
    parts, imgs, pos, seq = [], [], 0, 0
    for m in re.finditer(r"<img[^>]+src=\"([^\"]+)\"[^>]*>", body):
        pre = body[pos:m.start()]
        txt = html_to_text(pre)
        if txt: parts.append(txt)
        url = m.group(1)
        if url.startswith("//"): url = "https:" + url
        if url.startswith("http") and "mybjx.net" in url:
            seq += 1
            ext = os.path.splitext(url.split("?")[0])[1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"): ext = ".jpg"
            fn = "%s_%d%s" % (aid, seq, ext)
            try:
                data = fetch_plain(url, referer=referer)
                if len(data) >= 4096:
                    os.makedirs(img_dir, exist_ok=True)
                    with open(os.path.join(img_dir, fn), "wb") as f:
                        f.write(data)
                    parts.append("![](%s/%s)" % (img_rel, fn))
                    imgs.append({"file": fn, "url": url, "seq": seq})
                else:
                    log("  图片过小跳过: %s (%dB)" % (url[:60], len(data)))
            except Exception as e:
                log("  图片下载失败: %s (%s)" % (url[:60], str(e)[:50]))
        pos = m.end()
    tail = html_to_text(body[pos:])
    if tail: parts.append(tail)
    return "\n\n".join(p for p in parts if p), imgs

def html_to_text(frag):
    frag = re.sub(r"<script.*?</script>", "", frag, flags=re.S)
    frag = re.sub(r"<style.*?</style>", "", frag, flags=re.S)
    frag = re.sub(r"<br\s*/?>", "\n", frag)
    txt = re.sub(r"<[^>]+>", "", frag)
    import html as _h
    txt = _h.unescape(txt)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in txt.split("\n")]
    return "\n".join(ln for ln in lines if ln)

# ---------- 存档 ----------
def month_of(date_str):
    return date_str[:7] if date_str else bj_now().strftime("%Y-%m")

def article_md_path(aid, date_str):
    d = os.path.join(ART_DIR, month_of(date_str))
    return os.path.join(d, "%s.md" % aid)

def archive_article(item, art, body_md, imgs, column_id, column_name):
    date_str = art.get("date") or item.get("date") or today_str()
    path = article_md_path(item["id"], date_str)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fm = [
        "---",
        'id: "%s"' % item["id"],
        'title: "%s"' % (art.get("title") or item.get("title", "")).replace('"', "'"),
        'date: "%s"' % (art.get("datetime") or item.get("date", "")),
        'source: "%s"' % art.get("source", "").replace('"', "'"),
        'author: "%s"' % art.get("author", "").replace('"', "'"),
        'column: "%s"' % column_name,
        'column_id: "%s"' % column_id,
        'channel: "%s"' % (art["breadcrumb"][2] if len(art.get("breadcrumb", [])) > 2 else ""),
        'url: "%s"' % item["url"],
        'keywords: [%s]' % ", ".join('"%s"' % k.replace('"', "'") for k in art.get("keywords", [])),
        'images: %d' % len(imgs),
        'crawled_at: "%s"' % bj_now().isoformat(timespec="seconds"),
        "---",
        "",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(fm) + body_md + "\n")
    return path

# ---------- 候选地址 ----------
def candidate_urls(url):
    dm = ART_RE.search(url)
    cands = [url]
    if dm:
        d, aid = dm.group(1), dm.group(2)
        # 主站镜像候选
        mirror = "https://news.bjx.com.cn/html/%s/%s.shtml" % (d, aid)
        if mirror != url:
            cands.append(mirror)
    if url.startswith("https://"):
        cands.append("http://" + url[len("https://"):])
    seen, out = set(), []
    for u in cands:
        if u not in seen:
            seen.add(u); out.append(u)
    return out

# ---------- 主流程 ----------
def collect_items(columns, date8_set, dateiso_set):
    """汇总各栏目过去24小时文章条目(按ID去重)。"""
    items = {}
    for col in columns:
        got = []
        if col["type"] == "main_list":
            for page in (1, 2, 3):
                url = col["url"] if page == 1 else col["url"] + "%d/" % page
                html = fetch_rendered(url, is_article=False)
                if not html:
                    log("列表页失败: %s p%d" % (col["id"], page))
                    break
                lst = parse_main_list(html)
                if not lst:
                    if page == 1:
                        log("!! 解析为空告警: %s (%s) 页面结构可能已变化" % (col["id"], url))
                    break
                fresh = [x for x in lst if x["date"] in dateiso_set]
                got.extend(fresh)
                oldest = min(x["date"] for x in lst)
                if oldest < min(dateiso_set):
                    break  # 已翻出24小时窗口
                if len(lst) < 20:
                    break
        else:
            html = fetch_rendered(col["url"], is_article=False)
            if not html:
                log("列表页失败: %s (%s) —— 可能遭遇WAF, 该频道经主站交叉收录补充" % (col["id"], col["url"]))
            else:
                got = parse_portal(html, col.get("host", ""), date8_set)
                if not got:
                    log("提示: %s 门户页无24h内新文或结构变化" % col["id"])
        for x in got:
            if x["id"] not in items:
                x["also_in"] = []
                items[x["id"]] = x
            items[x["id"]]["also_in"].append(col["id"])
        log("栏目 %-10s 24h增量 %d 篇" % (col["id"], len(got)))
        time.sleep(random.uniform(0.5, 1.2))
    # 归属定级: 子站域名 > 主题栏目 > 频道门户 > 要闻
    thematic = ["zc", "sc", "sj", "xm", "mq", "pl", "gj", "dj", "js"]
    by_id = {c["id"]: c for c in columns}
    host2col = {c.get("host"): c for c in columns if c.get("host")}
    for x in items.values():
        host = x["url"].split("/")[2]
        xcol = host2col.get(host)
        if xcol is None:
            for t in thematic:
                if t in x["also_in"]:
                    xcol = by_id[t]; break
        if xcol is None:
            for c in columns:
                if c["type"] == "portal" and c["id"] in x["also_in"]:
                    xcol = c; break
        if xcol is None:
            xcol = by_id["yw"]
        x["column_id"], x["column_name"] = xcol["id"], xcol["name"]
        x["discovered_via"] = ",".join(x["also_in"])
    return items

NUM_PAT = re.compile(
    r"(\d[\d,\.]*\s*(?:万千瓦|兆瓦|MW|GW|kW|千瓦|亿千瓦时|万千瓦时|千瓦时|亿元|万元|元/千瓦时|元/吨|万吨|吨|亿元/年|%|个百分点))")

def extract_numbers(title, lead):
    out = []
    for seg in (title, lead):
        for m in NUM_PAT.finditer(seg or ""):
            s = _clean(seg[max(0, m.start() - 25):m.end() + 15])
            if s and s not in out:
                out.append(s)
    return out[:3]

def build_briefing(day, columns, seen, pending):
    """生成/重建当日简报; 保留人工追加区块。"""
    path = os.path.join(BRIEF_DIR, day + ".md")
    # 人工整理的"市场数值"内容存放于 state/briefing_manual.md, 注入时不再留任何标记
    manual = ""
    manual_path = os.path.join(STATE_DIR, "briefing_manual.md")
    if os.path.exists(manual_path):
        manual = open(manual_path, encoding="utf-8").read().strip("\n")
    dateiso_set = {day, (datetime.date.fromisoformat(day) - datetime.timedelta(days=1)).isoformat()}
    arts = [v for v in seen.values() if v.get("date", "")[:10] in dateiso_set and v.get("status") in ("ok", "manual")]
    arts.sort(key=lambda v: (v.get("date", ""), v.get("id", "")), reverse=True)
    col_order = [c["id"] for c in columns]
    col_name = {c["id"]: c["name"] for c in columns}
    by_col = {}
    for v in arts:
        by_col.setdefault(v.get("column_id", "?"), []).append(v)

    L = []
    L.append("# 北极星电力网每日简报（%s）" % day)
    L.append("")
    L.append("> 生成时间：%s（北京时间） ｜ 覆盖栏目：%d ｜ 收录文章：%d 篇 ｜ 待人工补抓：%d 篇"
             % (bj_now().strftime("%Y-%m-%d %H:%M"), len(columns), len(arts), len(pending)))
    L.append("")
    L.append("## 今日概览")
    L.append("")
    L.append("| 栏目 | 篇数 |")
    L.append("| --- | --- |")
    for cid in col_order:
        if cid in by_col:
            L.append("| %s | %d |" % (col_name[cid], len(by_col[cid])))
    L.append("")
    # 要闻精选
    L.append("## 要闻精选")
    L.append("")
    picks = [v for v in arts if v.get("column_id") in ("yw", "dj", "zc")]
    if len(picks) < 10:
        rest = [v for v in arts if v not in picks]
        picks += rest[:10 - len(picks)]
    picks = picks[:10]
    for v in picks:
        L.append("- **[%s](%s)**（%s ｜ %s）" % (v["title"], v["url"], col_name.get(v.get("column_id"), v.get("column_name", "")), v.get("date", "")[:16]))
        if v.get("lead"):
            L.append("  > %s" % v["lead"][:150])
    L.append("")
    # 市场数值
    L.append("## 市场数值")
    L.append("")
    if manual:
        L.append(manual)
    else:
        n = 0
        for v in arts:
            for s in v.get("numbers", []):
                L.append("- %s —— [%s](%s)" % (s, v["title"][:40], v["url"]))
                n += 1
                break
            if n >= 25: break
        if n == 0:
            L.append("（暂无数值类条目）")
    L.append("")
    # 各栏目清单
    L.append("## 各栏目文章清单")
    for cid in col_order:
        if cid not in by_col: continue
        L.append("")
        L.append("### %s（%d）" % (col_name[cid], len(by_col[cid])))
        L.append("")
        for v in by_col[cid]:
            L.append("- [%s](%s)（%s）" % (v["title"], v["url"], v.get("date", "")[:16]))
    L.append("")
    if pending:
        L.append("## 待人工补抓（详情页WAF）")
        L.append("")
        for aid, v in pending.items():
            L.append("- ID %s ｜ [%s](%s)" % (aid, v.get("title", ""), v.get("url", "")))
        L.append("")
    os.makedirs(BRIEF_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return path

def cmd_run():
    # 幂等保护: 全新沙箱中目录/配置缺失时给出明确提示
    if not os.path.exists(CONF):
        sys.stderr.write("ERROR: 配置不存在: %s\n请确认 columns.json 已部署到 $BJX_BASE/config/（服务器部署见 design.md 第4节）。\n" % CONF)
        sys.exit(2)
    for d in (STATE_DIR, ART_DIR, BRIEF_DIR, LOG_DIR):
        os.makedirs(d, exist_ok=True)
    columns = json.load(open(CONF, encoding="utf-8"))["columns"]
    seen = load_json(SEEN_FILE, {})
    pending = load_json(PENDING_FILE, {})
    date8_set, dateiso_set = cutoff_dates()
    log("=== 开始每日增量抓取: 窗口 %s ===" % "/".join(sorted(dateiso_set)))
    items = collect_items(columns, date8_set, dateiso_set)
    log("去重后候选文章 %d 篇" % len(items))
    # 已抓文章同步最新归属(幂等修正)
    ch_map = {"火电": "huodian", "风电": "fd", "光伏": "guangfu", "储能": "chuneng",
              "水电": "shuidian", "核电": "hedian", "输配电": "shupeidian",
              "电力市场": "shoudian", "氢能": "qn", "环保": "huanbao"}
    name_of = {c["id"]: c["name"] for c in columns}
    for x in items.values():
        if x["id"] in seen:
            v = seen[x["id"]]
            cid, cname = x["column_id"], x["column_name"]
            ch = v.get("channel", "")
            if ch in ch_map and x["url"].split("/")[2] == "news.bjx.com.cn":
                cid, cname = ch_map[ch], ch
            if v.get("column_id") != cid:
                v["column_id"], v["column_name"] = cid, name_of.get(cid, cname)
    save_json(SEEN_FILE, seen)
    todo = [x for x in items.values()
            if x["id"] not in seen and x["id"] not in pending]
    log("待抓详情 %d 篇（跳过已抓 %d 篇）" % (len(todo), len(items) - len(todo)))
    ok_n = fail_n = 0
    for i, x in enumerate(todo, 1):
        art_html = None
        for url in candidate_urls(x["url"]):
            art_html = fetch_rendered(url, is_article=True, max_attempts=2)
            if art_html:
                x["url"] = url
                break
        if not art_html:
            log("详情页失败(全部候选地址) id=%s url=%s title=%s" % (x["id"], x["url"], x.get("title", "")))
            pending[x["id"]] = {"url": x["url"], "title": x.get("title", ""),
                                "date": x.get("date", ""), "column_id": x["column_id"],
                                "column_name": x["column_name"]}
            save_json(PENDING_FILE, pending)
            fail_n += 1
            continue
        art, body = parse_article(art_html)
        # 面包屑频道归属修正(主站文章)
        ch_map = {"火电": "huodian", "风电": "fd", "光伏": "guangfu", "储能": "chuneng",
                  "水电": "shuidian", "核电": "hedian", "输配电": "shupeidian",
                  "电力市场": "shoudian", "氢能": "qn", "环保": "huanbao"}
        bc = art.get("breadcrumb", [])
        ch_hit = next((b for b in bc if b in ch_map), None)
        if ch_hit and x["url"].split("/")[2] == "news.bjx.com.cn":
            x["column_id"] = ch_map[ch_hit]
            x["column_name"] = ch_hit
        if not art.get("title"):
            art["title"] = x.get("title", "")
        if not art.get("date"):
            art["date"] = x.get("date", ""); art["datetime"] = x.get("date", "")
        mdir = month_of(art["date"])
        img_dir = os.path.join(ART_DIR, mdir, "images")
        body_md, imgs = body_to_markdown(body, x["id"], img_dir, "images", x["url"])
        path = archive_article(x, art, body_md, imgs, x["column_id"], x["column_name"])
        lead = ""
        for para in body_md.split("\n\n"):
            if para and not para.startswith("!["):
                lead = para[:160]; break
        seen[x["id"]] = {
            "status": "ok", "title": art["title"], "url": x["url"],
            "date": art.get("datetime") or x.get("date", ""),
            "column_id": x["column_id"], "column_name": x["column_name"],
            "channel": art["breadcrumb"][2] if len(art.get("breadcrumb", [])) > 2 else "",
            "file": os.path.relpath(path, BASE), "images": len(imgs),
            "lead": lead, "numbers": extract_numbers(art["title"], lead),
            "crawled_at": bj_now().isoformat(timespec="seconds")}
        save_json(SEEN_FILE, seen)
        ok_n += 1
        log("[%d/%d] %s %s" % (i, len(todo), x["id"], art["title"][:38]))
        time.sleep(random.uniform(0.4, 1.0))
    bp = build_briefing(today_str(), columns, seen, pending)
    log("=== 完成: 成功 %d, 失败 %d, 简报 %s ===" % (ok_n, fail_n, bp))
    shutdown()

def cmd_mark(ids):
    seen = load_json(SEEN_FILE, {})
    pending = load_json(PENDING_FILE, {})
    marked = 0
    for aid in ids:
        aid = str(aid).strip()
        if aid in pending:
            v = pending.pop(aid)
            v["status"] = "manual"
            v["crawled_at"] = bj_now().isoformat(timespec="seconds")
            # 补充文件位置(若已按格式手动存档)
            dstr = (v.get("date") or today_str())[:10]
            p = article_md_path(aid, dstr)
            if os.path.exists(p):
                v["file"] = os.path.relpath(p, BASE)
                try:
                    txt = open(p, encoding="utf-8").read()
                    if "lead" not in v:
                        paras = [x for x in txt.split("---", 2)[-1].split("\n\n") if x.strip() and not x.strip().startswith("![")]
                        v["lead"] = paras[0][:160] if paras else ""
                    v["numbers"] = extract_numbers(v.get("title", ""), v.get("lead", ""))
                except Exception:
                    pass
            seen[aid] = v
            marked += 1
            log("标记已抓(手动补抓): %s %s" % (aid, v.get("title", "")[:40]))
        elif aid in seen:
            log("已在库中: %s" % aid)
        else:
            seen[aid] = {"status": "manual", "title": "", "url": "", "date": today_str(),
                         "column_id": "", "column_name": "", "crawled_at": bj_now().isoformat(timespec="seconds")}
            marked += 1
            log("标记已抓(无元数据): %s" % aid)
    save_json(SEEN_FILE, seen)
    save_json(PENDING_FILE, pending)
    columns = json.load(open(CONF, encoding="utf-8"))["columns"]
    bp = build_briefing(today_str(), columns, seen, pending)
    log("已标记 %d 篇, 简报已更新: %s" % (marked, bp))

def main():
    ap = argparse.ArgumentParser(description="北极星电力网每日增量抓取")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    mp = sub.add_parser("mark")
    mp.add_argument("--ids", nargs="+", required=True)
    args = ap.parse_args()
    os.chdir(BASE)
    if args.cmd == "run":
        cmd_run()
    elif args.cmd == "mark":
        cmd_mark(args.ids)

if __name__ == "__main__":
    # 无显示环境时自动套 xvfb-run 重 exec（有头浏览器过WAF所需）
    if not os.environ.get("DISPLAY") and os.environ.get("BJX_NO_XVFB") != "1":
        os.execvp("xvfb-run", ["xvfb-run", "-a", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
    main()
