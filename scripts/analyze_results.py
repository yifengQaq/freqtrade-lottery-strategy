import json
import yaml

# 1. Check backtest config
cfg = json.load(open('config/config_backtest.json'))
print('=== 回测配置 ===')
print(f'Exchange: {cfg.get("exchange",{}).get("name","?")}')
print(f'Stake currency: {cfg.get("stake_currency","?")}')
print(f'Stake amount: {cfg.get("stake_amount","?")}')
print(f'Dry run wallet: {cfg.get("dry_run_wallet","?")}')
print(f'Trading mode: {cfg.get("trading_mode","?")}')
print(f'Margin mode: {cfg.get("margin_mode","?")}')
pairs = cfg.get('exchange',{}).get('pair_whitelist',[])
print(f'Pairs: {len(pairs)} - {pairs[:5]}...')

# 2. Check agent config for timeranges
agent_cfg = yaml.safe_load(open('config/agent_config.yaml'))['agent']
print(f'\n=== Agent配置 ===')
print(f'timerange_is: {agent_cfg.get("timerange_is","?")}')
print(f'timerange_oos: {agent_cfg.get("timerange_oos","?")}')
print('comparison_windows:')
for name, tr in agent_cfg.get('comparison_windows',{}).items():
    print(f'  {name}: {tr}')

# 3. Evaluator config
print(f'\n=== 评估配置 ===')
print(f'target_profile: {agent_cfg.get("target_profile",{})}')

# 4. Iteration log stats
log = json.load(open('results/iteration_log.json'))
success = [r for r in log if r['status']=='success' and r.get('backtest_metrics',{}).get('total_profit_pct') is not None]
print(f'\n=== 收益统计 ({len(success)} 成功轮) ===')
profits = [r['backtest_metrics']['total_profit_pct'] for r in success]
print(f'平均收益: {sum(profits)/len(profits):.2f}%')
print(f'最高收益: {max(profits):.2f}%')
print(f'最低收益: {min(profits):.2f}%')
positive = [p for p in profits if p > 0]
print(f'正收益轮数: {len(positive)}/{len(profits)} ({len(positive)/len(profits)*100:.0f}%)')

# Best 5
best = sorted(success, key=lambda r: r['backtest_metrics']['total_profit_pct'], reverse=True)[:5]
print(f'\n=== TOP 5 ===')
for r in best:
    m = r['backtest_metrics']
    print(f'R{r["round"]:>2} profit={m["total_profit_pct"]:>8.2f}% dd={m["max_drawdown_pct"]:>5.1f}% trades={m["total_trades"]} wr={m["win_rate"]*100:.1f}% sharpe={m["sharpe_ratio"]:.2f}')

# Worst 5
worst = sorted(success, key=lambda r: r['backtest_metrics']['total_profit_pct'])[:5]
print(f'\n=== WORST 5 ===')
for r in worst:
    m = r['backtest_metrics']
    print(f'R{r["round"]:>2} profit={m["total_profit_pct"]:>8.2f}% dd={m["max_drawdown_pct"]:>5.1f}% trades={m["total_trades"]} wr={m["win_rate"]*100:.1f}%')
