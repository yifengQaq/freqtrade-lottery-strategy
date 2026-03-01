#!/usr/bin/env python3
"""Diagnose why scoring doesn't correlate with profitability."""
import json

data = json.load(open("results/iteration_log.json"))

print(f"Total rounds: {len(data)}\n")

# Show R4 vs R8 comparison
for idx in [3, 7]:
    r = data[idx]
    ev = r.get("eval_result", {})
    sb = ev.get("score_breakdown", {})
    m = r.get("backtest_metrics", {})
    print(f'=== R{r["round"]} (profit={m.get("total_profit_pct", 0):.1f}%, score={ev.get("score", 0)}) ===')
    print(f'  Score breakdown: {json.dumps(sb, indent=4)}')
    print(f'  weekly_target_hit_rate: {m.get("weekly_target_hit_rate")}')
    print(f'  monthly_net_profit_avg: {m.get("monthly_net_profit_avg")}')
    print(f'  max_monthly_loss: {m.get("max_monthly_loss")}')
    print(f'  avg_trade_duration_hours: {m.get("avg_trade_duration_hours")}')
    print(f'  total_trades: {m.get("total_trades")}')
    print(f'  gate_failures: {ev.get("gate_failures", [])}')
    print()

# Summary stats
print("=" * 60)
print("ALL ROUNDS SUMMARY:")
print("=" * 60)
for r in data:
    ev = r.get("eval_result", {})
    m = r.get("backtest_metrics", {})
    profit = m.get("total_profit_pct", 0)
    score = ev.get("score", 0)
    wtr = m.get("weekly_target_hit_rate", "?")
    monthly = m.get("monthly_net_profit_avg", "?")
    trades = m.get("total_trades", 0)
    duration = m.get("avg_trade_duration_hours", "?")
    print(f'R{r["round"]:2d}: profit={profit:8.1f}% score={score:6.2f} wtr={wtr} monthly={monthly} trades={trades} duration_h={duration}')
