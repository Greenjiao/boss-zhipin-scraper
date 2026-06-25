#!/usr/bin/env python3
"""临时脚本：批量清洗已有数据文件的 JD 噪音"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from boss_cdp_raw import clean_jd_text

RESULT_DIR = os.path.expanduser("~/.boss-zhipin-scraper/job-result")

if not os.path.isdir(RESULT_DIR):
    print(f"目录不存在: {RESULT_DIR}")
    sys.exit(1)

files = sorted(
    [f for f in os.listdir(RESULT_DIR)
     if f.endswith(".json") and (f.startswith("boss_details_") or f.startswith("boss_screened_"))],
    reverse=True,
)

if not files:
    print("未找到需要清洗的文件")
    sys.exit(0)

print(f"找到 {len(files)} 个文件\n")

total_changed = 0
total_jobs = 0

for fname in files:
    path = os.path.join(RESULT_DIR, fname)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    jobs = data if isinstance(data, list) else data.get("jobs", [])
    if not jobs:
        print(f"{fname}: 无岗位数据，跳过")
        continue

    changed = 0
    for j in jobs:
        old = j.get("jd", "")
        if not old:
            continue
        new = clean_jd_text(old)
        if new != old:
            j["jd"] = new
            changed += 1

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✓ {fname}: 清洗 {changed}/{len(jobs)} 条")
    else:
        print(f"  {fname}: 无需清洗 ({len(jobs)} 条)")

    total_changed += changed
    total_jobs += len(jobs)

print(f"\n完成: 清洗 {total_changed}/{total_jobs} 条")
