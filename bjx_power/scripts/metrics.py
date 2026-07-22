#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片表格解析第一步: 对当日新增正文图片做本地OCR预筛 + 生成索引拼图,
将 400+ 张图压缩为少量"疑似表格/图表"候选, 供视觉判读。
用法:
  python3 metrics.py run [--date YYYY-MM-DD] [--all]
输出:
  state/img_analysis-<date>.json    每张图的分类与OCR摘要
  /tmp/bjx_sheets/sheet_XX.jpg      索引拼图(文件名标注在图上)
"""
import os, sys, json, re, argparse, datetime, subprocess, math, tempfile

BASE = os.environ.get("BJX_BASE", "/opt/bjx/data")
ART_DIR = os.path.join(BASE, "articles")
STATE_DIR = os.path.join(BASE, "state")
SEEN_FILE = os.path.join(STATE_DIR, "seen.json")

def bj_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))

# ---------- 当日新增图片清单 ----------
def day_images(date):
    seen = json.load(open(SEEN_FILE, encoding="utf-8")) if os.path.exists(SEEN_FILE) else {}
    day8 = date.replace("-", "")
    out = []
    month = date[:7]
    img_dir = os.path.join(ART_DIR, month, "images")
    for aid, v in seen.items():
        if v.get("date", "")[:10] != date:
            continue
        n = v.get("images", 0)
        for i in range(1, n + 1):
            for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                fn = "%s_%d%s" % (aid, i, ext)
                p = os.path.join(img_dir, fn)
                if os.path.exists(p):
                    out.append({"article_id": aid, "seq": i, "file": fn, "path": p,
                                "title": v.get("title", ""), "column": v.get("column_name", "")})
                    break
    out.sort(key=lambda x: (x["article_id"], x["seq"]))
    return out

# ---------- 本地预筛 ----------
def ocr_text(path):
    try:
        r = subprocess.run(["tesseract", path, "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                           capture_output=True, text=True, timeout=60)
        return r.stdout or ""
    except Exception:
        return ""

GRID_HINT = re.compile(r"[\d]{2,}")
def classify(path, text):
    from PIL import Image
    try:
        im = Image.open(path)
        w, h = im.size
    except Exception:
        return "unreadable", ""
    if w < 220 or h < 140:
        return "skip_small", ""
    digits = len(re.findall(r"\d", text))
    han = len(re.findall(r"[一-鿿]", text))
    pct = len(re.findall(r"%|％", text))
    # 数值密度高且带百分比/单位 -> 疑似表格
    if digits > 60 and (pct >= 2 or digits > 120) and han > 30:
        return "candidate_table", ""
    # 含轴/图例词且文字较少 -> 疑似图表
    if re.search(r"单位|图例|时间|月份|年份|MW|kW|万千瓦|亿元|万千瓦时", text) and han > 15:
        return "candidate_chart", ""
    if han < 12 and digits < 8:
        return "skip_photo", ""
    return "review", ""  # 文字页/海报/难以判定, 列入拼图复核

def make_sheets(cands, out_dir):
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(out_dir, exist_ok=True)
    for old in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, old))
    COLS, ROWS, CW, CH, LAB = 5, 6, 380, 300, 26
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    per = COLS * ROWS
    sheets = []
    for s in range(max(1, math.ceil(len(cands) / per))):
        batch = cands[s * per:(s + 1) * per]
        if not batch:
            break
        sheet = Image.new("RGB", (COLS * CW, ROWS * (CH + LAB)), "white")
        dr = ImageDraw.Draw(sheet)
        for i, c in enumerate(batch):
            r, cc = divmod(i, COLS)
            x, y = cc * CW, r * (CH + LAB)
            try:
                im = Image.open(c["path"]).convert("RGB")
                im.thumbnail((CW - 8, CH - 8))
                sheet.paste(im, (x + 4, y + 4))
            except Exception:
                pass
            dr.text((x + 6, y + CH + 2), c["file"], fill="black", font=font)
        fn = os.path.join(out_dir, "sheet_%02d.jpg" % s)
        sheet.save(fn, quality=82)
        sheets.append(fn)
    return sheets

def cmd_run(date, use_all=False):
    imgs = day_images(date)
    print("当日图片 %d 张" % len(imgs), flush=True)
    results = []
    for i, im in enumerate(imgs, 1):
        text = "" if im["file"].lower().endswith(".gif") else ocr_text(im["path"])
        cls, note = classify(im["path"], text)
        im2 = dict(im)
        im2["class"] = cls
        im2["ocr_len"] = len(text)
        results.append(im2)
        if i % 25 == 0:
            print("  预筛 %d/%d" % (i, len(imgs)), flush=True)
    out = os.path.join(STATE_DIR, "img_analysis-%s.json" % date)
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(results, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cand = [r for r in results if r["class"] in ("candidate_table", "candidate_chart", "review")]
    sheets = make_sheets(cand, "/tmp/bjx_sheets")
    from collections import Counter
    print("分类统计:", dict(Counter(r["class"] for r in results)))
    print("候选 %d 张 -> 拼图 %d 张于 /tmp/bjx_sheets/" % (len(cand), len(sheets)))
    print("分析结果: %s" % out)

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("run")
    rp.add_argument("--date", default=bj_now().strftime("%Y-%m-%d"))
    rp.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if args.cmd == "run":
        cmd_run(args.date, args.all)

if __name__ == "__main__":
    main()
