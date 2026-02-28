# Quickstart: LLM Agent + 回测闭环自动迭代系统

## 前置条件

1. **Python 3.11+**
2. **Freqtrade** 已安装并可运行 `freqtrade backtesting`
3. **DeepSeek API Key** — 从 https://platform.deepseek.com 获取
4. **交易数据** 已下载（至少 2023-01-01 至 2025-12-31）

## 安装

```bash
# 克隆项目
git clone <repo-url>
cd V2ex-Agent-Dev

# 安装依赖
pip install httpx pyyaml

# 设置 API Key
export DEEPSEEK_API_KEY="your-api-key-here"
```

## 配置

1. 编辑 `config/agent_config.yaml` 设置 freqtrade 路径和时间范围
2. 确认 `strategies/LotteryMindsetStrategy.py` 是初始策略
3. 确认 `config/config_backtest.json` 指向正确的数据目录

## 运行

### 单轮迭代（测试）
```bash
python scripts/run_agent.py --rounds 1
```

### 多轮自动迭代
```bash
python scripts/run_agent.py --rounds 20
```

### 带 walk-forward 验证
```bash
python scripts/run_agent.py --rounds 20 --walk-forward
```

### 查看历史版本
```bash
python scripts/run_agent.py --list-versions
```

### 回滚到某轮
```bash
python scripts/run_agent.py --rollback 3
```

## 输出

- `results/iteration_log.json` — 每轮迭代记录
- `results/strategy_versions/` — 每轮策略快照
- 终端输出每轮的得分和变更摘要

## 验证测试

```bash
# 运行单元测试
pytest tests/ -v

# 用模拟数据测试单轮
python scripts/run_agent.py --rounds 1 --dry-run
```
