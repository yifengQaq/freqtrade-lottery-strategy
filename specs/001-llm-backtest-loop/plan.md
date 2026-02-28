# Implementation Plan: LLM Agent + 回测闭环自动迭代系统

**Branch**: `001-llm-backtest-loop` | **Date**: 2026-02-28 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/001-llm-backtest-loop/spec.md`

## Summary

构建一个"DeepSeek LLM Agent + Freqtrade Backtest"闭环系统，自动完成：策略代码分析 → 参数/逻辑修改 → 回测执行 → 结果评估 → 再迭代。核心是一个 Python CLI 工具，以 Agent 模块调用 DeepSeek API，以 Runner 模块执行 freqtrade 回测，以 Evaluator 模块打分筛选，以 Modifier 模块安全写入代码，循环最多 20 轮或提前终止。

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: httpx (DeepSeek API), freqtrade (回测引擎, 外部), pydantic (数据模型, 可选)  
**Storage**: 文件系统 (JSON 结果 + .py 策略版本)  
**Testing**: pytest  
**Target Platform**: macOS / Linux (CLI)  
**Project Type**: CLI 工具  
**Performance Goals**: 单轮迭代 < 15 分钟（含 LLM 调用 + 回测）  
**Constraints**: DeepSeek API 需要有效 API Key；Freqtrade 需独立安装并配置好交易数据  
**Scale/Scope**: 单用户本地运行，20 轮迭代为上限

## Constitution Check

*GATE: 项目尚未定义 constitution，使用默认原则*

- [x] 简单优先（YAGNI）：不引入不必要的框架
- [x] 可测试：每个模块独立可测
- [x] 安全约束：Agent 修改受安全门控限制
- [x] 版本管理：每轮迭代有备份和回滚能力

## Project Structure

### Documentation (this feature)

```text
specs/001-llm-backtest-loop/
├── plan.md              # 本文件
├── spec.md              # 功能规格
├── research.md          # 技术调研
├── data-model.md        # 数据模型
├── quickstart.md        # 快速启动指南
├── contracts/           # 接口约定
│   └── agent-output.json
└── tasks.md             # 任务列表（/speckit.tasks 生成）
```

### Source Code (repository root)

```text
agent/                           # Agent 核心模块
├── __init__.py
├── deepseek_client.py           # DeepSeek API 封装
├── backtest_runner.py           # freqtrade 回测执行器
├── evaluator.py                 # 结果评估与评分
├── strategy_modifier.py         # 策略代码安全写入
├── orchestrator.py              # 主循环编排器
└── prompts/
    └── system_prompt.md         # Agent 系统提示词

strategies/                      # Freqtrade 策略文件
└── LotteryMindsetStrategy.py    # 彩票心态策略（Agent 修改目标）

controllers/                     # 资金管理控制器（不可修改）
└── weekly_budget_controller.py

config/                          # 配置文件
├── config_backtest.json         # freqtrade 回测配置
├── agent_config.yaml            # Agent 运行参数
└── iteration_rules.yaml         # 迭代规则（门控/评分/终止）

scripts/                         # 入口脚本
└── run_agent.py                 # CLI 入口

results/                         # 运行结果（git ignored）
├── strategy_versions/           # 每轮策略快照
├── backtest_outputs/            # 回测结果 JSON
└── iteration_log.json           # 全局迭代日志

tests/                           # 测试
├── unit/
│   ├── test_deepseek_client.py
│   ├── test_evaluator.py
│   ├── test_strategy_modifier.py
│   └── test_backtest_runner.py
└── integration/
    └── test_orchestrator.py

Input/                           # 原始参考资料（只读）
└── ...
```

**Structure Decision**: 采用单项目 flat 结构。`agent/` 为核心库，`scripts/` 为 CLI 入口，`strategies/` + `controllers/` 为 freqtrade 策略侧代码，`config/` 为配置，`tests/` 为测试。
