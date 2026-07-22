#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日收尾导出: 状态快照 + 当日全文包 + 运行时包(内容变化时) -> $BJX_OUT（默认 /opt/bjx/backup）
用法:
  python3 pack.py export [--date YYYY-MM-DD]
  python3 pack.py runtime          # 仅重建运行时包
"""
import os, sys, json, zipfile, hashlib, argparse, datetime, shutil, subprocess

BASE = os.environ.get("BJX_BASE", "/opt/bjx/data")
OUT = os.environ.get("BJX_OUT", "/opt/bjx/backup")
STATE_DIR = os.path.join(BASE, "state")
SEEN_FILE = os.path.join(STATE_DIR, "seen.json")
PENDING_FILE = os.path.join(STATE_DIR, "pending_manual.json")
RUNTIME = os.path.join(OUT, "bjx_daily_runtime.zip")
RUNTIME_FILES = ["crawler.py", "metrics.py", "pack.py", "site.py", "run_daily.sh",
                 "bootstrap.sh", "FORMAT.md", "REBUILD.md", "daily_task.md", "config/columns.json"]
MAX_OUT = 95 * 1024 * 1024  # 输出目录单文件上限(实测约100MB), 留余量

def bj_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))

# ---------- 1. 状态快照 ----------
def export_state(date):
    seen = json.load(open(SEEN_FILE, encoding="utf-8")) if os.path.exists(SEEN_FILE) else {}
    pending = json.load(open(PENDING_FILE, encoding="utf-8")) if os.path.exists(PENDING_FILE) else {}
    today = datetime.date.fromisoformat(date)
    slim = {}
    for aid, v in seen.items():
        try:
            d = datetime.date.fromisoformat(v.get("date", "")[:10])
        except Exception:
            continue
        age = (today - d).days
        if age > 30:
            continue
        entry = {"title": v.get("title", ""), "url": v.get("url", ""),
                 "date": v.get("date", ""), "column_id": v.get("column_id", ""),
                 "column_name": v.get("column_name", ""), "status": v.get("status", "ok")}
        if age <= 7:  # 近7天保留简报重建所需的完整字段
            for k in ("channel", "file", "images", "lead", "numbers"):
                if k in v: entry[k] = v[k]
        slim[aid] = entry
    snap = {"exported_at": bj_now().isoformat(timespec="seconds"),
            "date": date, "seen": slim, "pending": pending}
    for name in ("bjx_state_%s.json" % date, "bjx_state_latest.json"):
        p = os.path.join(OUT, name)
        json.dump(snap, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("快照: %s (%d篇, %.1fKB)" % (name, len(slim), os.path.getsize(p) / 1024))
    # 恢复可读性自检
    back = json.load(open(os.path.join(OUT, "bjx_state_latest.json"), encoding="utf-8"))
    assert back["seen"] and len(back["seen"]) == len(slim), "快照读回校验失败"
    print("快照读回校验 OK")
    return snap

# ---------- 2. 当日全文包 ----------
def export_fulltext(date):
    month = date[:7]
    src = os.path.join(BASE, "articles", month)
    if not os.path.isdir(src):
        print("无当月文章目录, 跳过全文包")
        return
    seen = json.load(open(SEEN_FILE, encoding="utf-8")) if os.path.exists(SEEN_FILE) else {}
    day_ids = {aid for aid, v in seen.items() if v.get("date", "")[:10] == date}
    md_files, img_files = [], []
    for f in sorted(os.listdir(src)):
        if f.endswith(".md") and f[:-3] in day_ids:
            md_files.append(f)
    img_dir = os.path.join(src, "images")
    if os.path.isdir(img_dir):
        for f in sorted(os.listdir(img_dir)):
            if f.rsplit("_", 1)[0] in day_ids:
                img_files.append(f)
    name_md = "北极星电力网文章全文-%s.zip" % date
    zf = os.path.join("/tmp", name_md)
    with zipfile.ZipFile(zf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in md_files:
            z.write(os.path.join(src, f), arcname=f)
    shutil.copy(zf, os.path.join(OUT, name_md))
    print("全文包(纯文本): %s (%d篇, %.1fKB)" % (name_md, len(md_files), os.path.getsize(zf) / 1024))
    # 含图包(分卷)
    if img_files:
        name_img = "北极星电力网文章全文含图-%s.zip" % date
        zf2 = os.path.join("/tmp", name_img)
        with zipfile.ZipFile(zf2, "w", zipfile.ZIP_DEFLATED) as z:
            for f in md_files:
                z.write(os.path.join(src, f), arcname=f)
            for f in img_files:
                z.write(os.path.join(img_dir, f), arcname="images/" + f)
        sz = os.path.getsize(zf2)
        if sz <= MAX_OUT:
            shutil.copy(zf2, os.path.join(OUT, name_img))
            print("全文包(含图): %s (%d图, %.1fMB)" % (name_img, len(img_files), sz / 1048576))
        else:
            n = 0
            with open(zf2, "rb") as fh:
                while True:
                    chunk = fh.read(MAX_OUT)
                    if not chunk: break
                    part = os.path.join(OUT, "%s.part%02d" % (name_img, n))
                    with open(part, "wb") as pf: pf.write(chunk)
                    n += 1
            print("全文包(含图): %s 分%d卷 (%d图, %.1fMB)" % (name_img, n, len(img_files), sz / 1048576))
    # 清理旧日期包, 避免堆积
    for f in os.listdir(OUT):
        if f.startswith("北极星电力网文章全文") and date not in f:
            try: os.remove(os.path.join(OUT, f))
            except Exception: pass

# ---------- 3. 运行时包 ----------
def runtime_hash():
    h = hashlib.md5()
    for rel in RUNTIME_FILES:
        p = os.path.join(BASE, rel)
        h.update(rel.encode())
        if os.path.exists(p):
            h.update(open(p, "rb").read())
    return h.hexdigest()

def export_runtime(force=False):
    sig = os.path.join(OUT, "bjx_daily_runtime.md5")
    cur = runtime_hash()
    if not force and os.path.exists(RUNTIME) and os.path.exists(sig):
        if open(sig).read().strip() == cur:
            print("运行时包无变化, 跳过")
            return
    zf = "/tmp/bjx_daily_runtime.zip"
    with zipfile.ZipFile(zf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in RUNTIME_FILES:
            p = os.path.join(BASE, rel)
            if os.path.exists(p):
                z.write(p, arcname=rel)
        # 快照一并打入, 形成自洽恢复点
        latest = os.path.join(OUT, "bjx_state_latest.json")
        if os.path.exists(latest):
            z.write(latest, arcname="bjx_state_latest.json")
        for rel in ("state/briefing_manual.md",):
            p = os.path.join(BASE, rel)
            if os.path.exists(p):
                z.write(p, arcname=rel)
    shutil.copy(zf, RUNTIME)
    open(sig, "w").write(cur)
    print("运行时包: bjx_daily_runtime.zip (%.1fKB)" % (os.path.getsize(RUNTIME) / 1024))

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    ep = sub.add_parser("export")
    ep.add_argument("--date", default=bj_now().strftime("%Y-%m-%d"))
    sub.add_parser("runtime")
    args = ap.parse_args()
    if args.cmd == "export":
        os.makedirs(OUT, exist_ok=True)
        export_state(args.date)
        export_fulltext(args.date)
        export_runtime()
    elif args.cmd == "runtime":
        os.makedirs(OUT, exist_ok=True)
        export_runtime(force=True)

if __name__ == "__main__":
    main()
