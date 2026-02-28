#!/usr/bin/env python3
"""
run_agent.py — CLI entry-point for the V2ex LLM + Backtest iteration agent.

Usage examples:
    python scripts/run_agent.py                         # run with defaults from config
    python scripts/run_agent.py --rounds 5              # limit to 5 rounds
    python scripts/run_agent.py --walk-forward           # enable walk-forward validation
    python scripts/run_agent.py --list-versions          # list saved strategy versions
    python scripts/run_agent.py --rollback 3             # rollback to round 3
    python scripts/run_agent.py --dry-run                # print plan and exit
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import yaml

# Ensure project root is on sys.path so ``agent`` package is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.orchestrator import Orchestrator  # noqa: E402
from agent.strategy_modifier import StrategyModifier  # noqa: E402

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "agent_config.yaml")

logger = logging.getLogger("v2ex_agent")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _load_config(path: str = CONFIG_PATH) -> dict:
    """Load and return the ``agent:`` section of agent_config.yaml."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    cfg = raw.get("agent", {})

    # Resolve env-var placeholders like ${FREQTRADE_DIR}
    for key, val in cfg.items():
        if isinstance(val, str) and val.startswith("${") and val.endswith("}"):
            env_name = val[2:-1]
            cfg[key] = os.environ.get(env_name, val)

    return cfg


def _setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt)


# ------------------------------------------------------------------
# Sub-commands
# ------------------------------------------------------------------

def cmd_run(args, config: dict):
    """Run the iteration loop."""
    if args.walk_forward:
        config["enable_walk_forward"] = True
    if args.auto_repair:
        config["enable_auto_repair"] = True
    if args.repair_max_retries is not None:
        config["repair_max_retries"] = args.repair_max_retries
    if args.enable_factor_lab:
        config["enable_factor_lab"] = True
    if args.factor_candidates is not None:
        config["factor_candidates"] = args.factor_candidates

    orch = Orchestrator(config)

    max_rounds = args.rounds or config.get("max_rounds", 20)
    logger.info("Starting iteration loop (max_rounds=%d)", max_rounds)

    rounds = orch.run_iteration_loop(max_rounds=max_rounds)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"Completed {len(rounds)} rounds")
    for r in rounds:
        status_icon = {
            "success": "✓",
            "failed": "✗",
            "overfitting": "⚠",
        }.get(r["status"], "?")
        print(
            f"  Round {r['round']:>2d} [{status_icon} {r['status']:>11s}] "
            f"score={r['score']:>7.2f}  {r['changes_made'][:60]}"
        )
    print(f"{'=' * 60}")


def cmd_dry_run(args, config: dict):
    """Print plan without executing."""
    max_rounds = args.rounds or config.get("max_rounds", 20)
    print("=== Dry-run plan ===")
    print(f"  Strategy:       {config.get('strategy_name')}")
    print(f"  Max rounds:     {max_rounds}")
    print(f"  IS timerange:   {config.get('timerange_is')}")
    print(f"  OOS timerange:  {config.get('timerange_oos')}")
    print(f"  Walk-forward:   {args.walk_forward or config.get('enable_walk_forward')}")
    print(f"  Stale limit:    {config.get('stale_rounds_limit')}")
    print(f"  Config file:    {CONFIG_PATH}")
    print("No changes will be made.")


def cmd_list_versions(config: dict):
    """List all saved strategy versions."""
    modifier = StrategyModifier(
        strategy_dir=config.get("strategy_dir", "strategies"),
        backup_dir=config.get("backup_dir", "results/strategy_versions"),
    )
    versions = modifier.list_versions()
    if not versions:
        print("No saved versions found.")
        return
    print(f"{'Round':>6s}  {'Timestamp':<26s}  File")
    print("-" * 70)
    for v in versions:
        print(f"  {v['round']:>4d}  {v['timestamp']:<26s}  {v['file']}")


def cmd_rollback(args, config: dict):
    """Rollback to a specified round."""
    modifier = StrategyModifier(
        strategy_dir=config.get("strategy_dir", "strategies"),
        backup_dir=config.get("backup_dir", "results/strategy_versions"),
    )
    ok = modifier.rollback(args.rollback)
    if ok:
        print(f"Successfully rolled back to round {args.rollback}.")
    else:
        print(f"Rollback to round {args.rollback} failed — no backup found.")
        sys.exit(1)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="V2ex LLM + Backtest iteration agent",
    )
    parser.add_argument(
        "--rounds", "-n",
        type=int,
        default=None,
        help="Maximum iteration rounds (default: from config)",
    )
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        default=False,
        help="Enable walk-forward validation",
    )
    parser.add_argument(
        "--list-versions",
        action="store_true",
        default=False,
        help="List all saved strategy versions and exit",
    )
    parser.add_argument(
        "--rollback",
        type=int,
        default=None,
        metavar="N",
        help="Rollback strategy to round N and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print plan without executing",
    )
    parser.add_argument(
        "--auto-repair",
        action="store_true",
        default=False,
        help="Enable automatic error recovery on backtest failure",
    )
    parser.add_argument(
        "--repair-max-retries",
        type=int,
        default=None,
        metavar="N",
        help="Maximum repair attempts per failure (default: 3)",
    )
    parser.add_argument(
        "--enable-factor-lab",
        action="store_true",
        default=False,
        help="Enable factor generation and experimentation",
    )
    parser.add_argument(
        "--factor-candidates",
        type=int,
        default=None,
        metavar="N",
        help="Number of factor candidates to generate per round (default: 5)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=CONFIG_PATH,
        help="Path to agent_config.yaml",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Enable debug logging",
    )
    return parser


def main(argv: list[str] | None = None):
    parser = build_parser()
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    config = _load_config(args.config)

    if args.list_versions:
        cmd_list_versions(config)
    elif args.rollback is not None:
        cmd_rollback(args, config)
    elif args.dry_run:
        cmd_dry_run(args, config)
    else:
        cmd_run(args, config)


if __name__ == "__main__":
    main()
