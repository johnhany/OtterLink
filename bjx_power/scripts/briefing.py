#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日简报构造(纯本地计算, 不访问网络)。

本模块是"构造日报"逻辑的唯一来源:
  - 以库的方式被 crawler.py import(抓取完成后构造当日简报);
  - 以脚本方式独立运行, 根据已有爬取结果重建简报, 不触发任何抓取:
      python3 briefing.py                    # 重建当天简报
      python3 briefing.py --date YYYY-MM-DD  # 重建指定日简报
      python3 briefing.py --all              # 重建 seen.json 覆盖的所有日期

数据流: config/columns.json + state/seen.json + state/pending_manual.json
        (+ 可选 state/briefing_manual.md) -> briefing/YYYY-MM-DD.md
简报格式定义见 FORMAT.md 第5节; 重建网站 HTML 请随后运行 site.py。
"""
import os, sys, json, argparse, datetime

BASE = os.environ.get("BJX_BASE") or os.path.expanduser(os.path.join("~", "bjx", "data"))
CONF = os.path.join(BASE, "config", "columns.json")
STATE_DIR = os.path.join(BASE, "state")
SEEN_FILE = os.path.join(STATE_DIR, "seen.json")
PENDING_FILE = os.path.join(STATE_DIR, "pending_manual.json")
BRIEF_DIR = os.path.join(BASE, "briefing")

def bj_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))

def today_str():
    return bj_now().strftime("%Y-%m-%d")

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def load_inputs():
    """读取构造简报所需的三份输入。"""
    columns = json.load(open(CONF, encoding="utf-8"))["columns"]
    return columns, load_json(SEEN_FILE, {}), load_json(PENDING_FILE, {})

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

def main():
    ap = argparse.ArgumentParser(description="由爬取结果重建每日简报(纯本地, 不联网)")
    ap.add_argument("--date", help="重建指定日简报(YYYY-MM-DD), 默认今天(北京时间)")
    ap.add_argument("--all", action="store_true", help="重建 seen.json 覆盖的所有日期")
    args = ap.parse_args()
    if not os.path.exists(CONF):
        sys.stderr.write("ERROR: 配置不存在: %s\n请先部署 config/columns.json 到 $BJX_BASE/config/\n" % CONF)
        sys.exit(2)
    columns, seen, pending = load_inputs()
    if args.all:
        days = sorted({v.get("date", "")[:10] for v in seen.values()} - {""})
    else:
        days = [args.date or today_str()]
    for d in days:
        print(build_briefing(d, columns, seen, pending), flush=True)
    print("共重建 %d 份简报" % len(days))

if __name__ == "__main__":
    main()
