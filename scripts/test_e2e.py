#!/usr/bin/env python3
"""End-to-end test: BacktestRunner with real freqtrade."""
import os
from dotenv import load_dotenv
load_dotenv()

from agent.backtest_runner import BacktestRunner

runner = BacktestRunner(
    freqtrade_dir=os.environ["FREQTRADE_DIR"],
    user_data="user_data",
    freqtrade_bin=os.environ["FREQTRADE_BIN"],
    config_path="config/config_backtest.json",
    timerange="20251001-20251231",
)

result = runner.run()
print("=== BacktestRunner E2E ===")
print(f"Success: {result['success']}")

if result["success"]:
    m = result["metrics"]
    print(f"Total Profit: {m['total_profit_pct']:.2f}% ({m['total_profit_abs']:.2f} USDT)")
    print(f"Max Drawdown: {m['max_drawdown_pct']:.2f}% ({m['max_drawdown_abs']:.2f} USDT)")
    print(f"Sharpe: {m['sharpe_ratio']:.2f}  Sortino: {m['sortino_ratio']:.2f}")
    print(f"Win Rate: {m['win_rate']:.2%}  Total Trades: {m['total_trades']}")
    print(f"Long/Short: {m['trade_count_long']}/{m['trade_count_short']}")
    print(f"Profit Factor: {m['profit_factor']:.2f}")
    print(f"Avg Duration: {m['avg_trade_duration']}")
    print(f"Backtest Days: {m['backtest_days']}  Market Change: {m['market_change']:.2%}")
else:
    print(f"Error: {result['error'][:500]}")
