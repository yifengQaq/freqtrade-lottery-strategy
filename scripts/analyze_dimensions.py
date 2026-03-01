#!/usr/bin/env python3
"""Analyze what dimensions the LLM actually modified across all rounds."""
import json

data = json.load(open("results/iteration_log.json"))

categories = {
    "指标/因子切换": 0,
    "入场条件参数": 0,
    "止损(stoploss)": 0,
    "止盈(ROI)": 0,
    "杠杆(leverage)": 0,
    "追踪止损(trailing)": 0,
    "出场信号": 0,
    "交易方向(多/空)": 0,
    "timeframe": 0,
}

for r in data:
    c = r.get("changes_made", "")
    if any(k in c for k in ["引入", "换用", "替代", "家族", "因子"]):
        categories["指标/因子切换"] += 1
    if any(k in c for k in ["阈值", "参数", "条件"]):
        categories["入场条件参数"] += 1
    if "stoploss" in c or "止损" in c:
        categories["止损(stoploss)"] += 1
    if "ROI" in c or "止盈" in c:
        categories["止盈(ROI)"] += 1
    if "杠杆" in c or "leverage" in c:
        categories["杠杆(leverage)"] += 1
    if "trailing" in c or "追踪" in c:
        categories["追踪止损(trailing)"] += 1
    if "出场" in c:
        categories["出场信号"] += 1
    if any(k in c for k in ["做多", "做空", "方向", "short"]):
        categories["交易方向(多/空)"] += 1
    if "timeframe" in c or "时间框架" in c:
        categories["timeframe"] += 1

total = len(data)
print(f"=== LLM 修改维度分布 ({total}轮) ===\n")
for k, v in sorted(categories.items(), key=lambda x: -x[1]):
    pct = v / total * 100
    bar = "#" * int(pct / 2)
    print(f"  {k:20s}: {v:3d}轮 ({pct:4.0f}%) {bar}")

# Also check actual stoploss / leverage / ROI values used
print("\n=== 关键风控参数变化范围 ===\n")
stoplosses = []
leverages = []
for r in data:
    m = r.get("backtest_metrics", {})
    # Try to extract from strategy code changes
    
print("从策略版本文件中提取实际参数...")
import glob, re

files = sorted(glob.glob("results/strategy_versions/*.py"))
for f in files[-10:]:  # Last 10 rounds
    code = open(f).read()
    sl = re.search(r"stoploss\s*=\s*(-?[\d.]+)", code)
    lev = re.search(r"leverage\s*=?\s*(\d+)", code) or re.search(r"return\s+(\d+).*leverage", code)
    roi = re.findall(r'"?(\d+)"?\s*:\s*([\d.]+)', code)
    trailing = re.search(r"trailing_stop\s*=\s*(True|False)", code)
    
    rnd = f.split("round_")[1].split("_")[0] if "round_" in f else "?"
    print(f"  R{rnd}: stoploss={sl.group(1) if sl else '?'}, "
          f"trailing={'Y' if trailing and trailing.group(1)=='True' else 'N'}, "
          f"ROI_entries={len(roi)}")
