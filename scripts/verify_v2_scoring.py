#!/usr/bin/env python3
"""Verify new v2 scoring formula on historical data."""
import json
import sys
sys.path.insert(0, ".")
from agent.evaluator import Evaluator

evaluator = Evaluator()
data = json.load(open("results/iteration_log.json"))

print("V2 评分公式验证 — 利润应与分数正相关\n")
print(f"{'轮次':>4} {'总收益%':>10} {'旧分':>8} {'新分':>8} {'方向':>4}")
print("-" * 44)

for r in data:
    m = r.get("backtest_metrics", {})
    old_score = r.get("eval_result", {}).get("score", 0)
    
    if not m or m.get("total_trades", 0) == 0:
        continue
    
    new_result = evaluator.evaluate(m)
    profit = m.get("total_profit_pct", 0)
    
    # Check if profit and new score have same sign
    direction = "✓" if (profit > 0 and new_result.score > 0) or (profit < 0 and new_result.score < 0) else "✗"
    
    print(f"R{r['round']:>3}: {profit:>9.1f}% {old_score:>7.2f} {new_result.score:>7.2f} {direction}")

print("\n--- 新评分 Top 5 ---")
scored = []
for r in data:
    m = r.get("backtest_metrics", {})
    if not m or m.get("total_trades", 0) == 0:
        continue
    new_result = evaluator.evaluate(m)
    scored.append((r["round"], m.get("total_profit_pct", 0), new_result.score, new_result.passed))

scored.sort(key=lambda x: x[2], reverse=True)
for rnd, profit, score, passed in scored[:5]:
    print(f"  R{rnd}: profit={profit:+.1f}%  score={score:.2f}  passed={passed}")

print("\n--- 门控通过率 ---")
passed_count = sum(1 for _, _, _, p in scored if p)
print(f"  {passed_count}/{len(scored)} ({passed_count/len(scored)*100:.0f}%) 通过门控")
