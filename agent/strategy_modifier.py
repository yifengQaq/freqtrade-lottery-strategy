"""
Strategy Modifier — applies LLM-generated code patches to strategy files.

Safety features:
1. Validates Python syntax before writing
2. Keeps backup of every version
3. Checks forbidden modifications (WeeklyBudgetController, etc.)
4. Atomic writes (temp file + rename)
"""

import ast
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Patterns that should NEVER be removed from strategy code
REQUIRED_PATTERNS = [
    r"WeeklyBudgetController",
    r"can_open_trade",
    r"confirm_trade_entry",
]

# Patterns that should NEVER appear (dangerous modifications)
FORBIDDEN_PATTERNS = [
    r"leverage\s*=\s*(\d+)",  # Will be checked with max value
]

MAX_LEVERAGE = 20
MIN_STOPLOSS = -0.98  # Can't be wider than -98%


class StrategyModifier:
    """Safely apply LLM-generated strategy modifications."""

    def __init__(
        self,
        strategy_dir: str = "strategies",
        backup_dir: str = "results/strategy_versions",
        strategy_filename: str = "LotteryMindsetStrategy.py",
    ):
        self.strategy_dir = strategy_dir
        self.backup_dir = backup_dir
        self.strategy_filename = strategy_filename
        self.strategy_path = os.path.join(strategy_dir, strategy_filename)

        os.makedirs(backup_dir, exist_ok=True)

    def get_current_code(self) -> str:
        """Read current strategy code."""
        with open(self.strategy_path, "r") as f:
            return f.read()

    def apply_patch(
        self,
        new_code: str,
        round_num: int,
        changes_description: str = "",
    ) -> dict:
        """
        Apply a code patch to the strategy file.

        Returns:
            {
                "success": bool,
                "backup_path": str,
                "errors": list[str],
                "warnings": list[str],
            }
        """
        errors = []
        warnings = []

        # 1. Syntax validation
        syntax_ok, syntax_err = self._validate_syntax(new_code)
        if not syntax_ok:
            errors.append(f"Syntax error: {syntax_err}")
            return {
                "success": False,
                "backup_path": "",
                "errors": errors,
                "warnings": warnings,
            }

        # 2. Safety checks
        safety_errors, safety_warnings = self._safety_check(new_code)
        errors.extend(safety_errors)
        warnings.extend(safety_warnings)

        if errors:
            return {
                "success": False,
                "backup_path": "",
                "errors": errors,
                "warnings": warnings,
            }

        # 3. Backup current version
        backup_path = self._backup(round_num, changes_description)

        # 4. Write new code (atomic)
        try:
            self._atomic_write(self.strategy_path, new_code)
            logger.info(
                "Strategy updated (round %d). Backup: %s",
                round_num, backup_path,
            )
            return {
                "success": True,
                "backup_path": backup_path,
                "errors": [],
                "warnings": warnings,
            }
        except Exception as e:
            # Restore from backup
            logger.error("Write failed, restoring backup: %s", e)
            if backup_path and os.path.exists(backup_path):
                shutil.copy2(backup_path, self.strategy_path)
            errors.append(f"Write failed: {e}")
            return {
                "success": False,
                "backup_path": backup_path,
                "errors": errors,
                "warnings": warnings,
            }

    def apply_config_patch(
        self,
        config_path: str,
        config_changes: dict,
        round_num: int,
    ) -> dict:
        """Apply JSON config changes (merge, not replace)."""
        import json

        try:
            with open(config_path, "r") as f:
                config = json.load(f)

            # Backup
            backup = config_path + f".bak.r{round_num}"
            shutil.copy2(config_path, backup)

            # Deep merge
            self._deep_merge(config, config_changes)

            with open(config_path, "w") as f:
                json.dump(config, f, indent=4)

            return {"success": True, "backup": backup, "errors": []}

        except Exception as e:
            return {"success": False, "backup": "", "errors": [str(e)]}

    def rollback(self, round_num: int) -> bool:
        """Rollback to a specific round's backup."""
        pattern = f"round_{round_num:03d}_*.py"
        backups = list(Path(self.backup_dir).glob(pattern))
        if not backups:
            logger.error("No backup found for round %d", round_num)
            return False

        latest = max(backups, key=lambda p: p.stat().st_mtime)
        shutil.copy2(str(latest), self.strategy_path)
        logger.info("Rolled back to round %d: %s", round_num, latest)
        return True

    def list_versions(self) -> list[dict]:
        """List all saved strategy versions."""
        versions = []
        for f in sorted(Path(self.backup_dir).glob("round_*.py")):
            # Parse round number from filename
            match = re.search(r"round_(\d+)", f.name)
            if match:
                versions.append({
                    "round": int(match.group(1)),
                    "file": str(f),
                    "timestamp": datetime.fromtimestamp(
                        f.stat().st_mtime
                    ).isoformat(),
                })
        return versions

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_syntax(code: str) -> tuple[bool, str]:
        """Validate Python syntax."""
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as e:
            return False, f"Line {e.lineno}: {e.msg}"

    def _safety_check(self, code: str) -> tuple[list[str], list[str]]:
        """
        Check for forbidden modifications.

        Returns: (errors, warnings)
        """
        errors = []
        warnings = []

        # Required patterns must be present
        for pattern in REQUIRED_PATTERNS:
            if not re.search(pattern, code):
                errors.append(
                    f"Required pattern missing: {pattern} — "
                    "WeeklyBudgetController integration must be preserved"
                )

        # Leverage check
        lev_matches = re.findall(r"leverage\s*[=:]\s*(\d+)", code)
        for lev_str in lev_matches:
            lev = int(lev_str)
            if lev > MAX_LEVERAGE:
                errors.append(
                    f"Leverage {lev}x exceeds maximum {MAX_LEVERAGE}x"
                )
            elif lev > 10:
                warnings.append(
                    f"Leverage {lev}x is within allowed range but > 10x. "
                    "OP recommends 3-10x."
                )

        # Stoploss check
        sl_matches = re.findall(r"stoploss\s*=\s*(-?[\d.]+)", code)
        for sl_str in sl_matches:
            sl = float(sl_str)
            if sl < MIN_STOPLOSS:
                errors.append(
                    f"Stoploss {sl} is wider than minimum {MIN_STOPLOSS}"
                )

        # Check for compound/reinvest patterns (forbidden in OP strategy)
        compound_patterns = [
            r"compound",
            r"reinvest",
            r"stake_amount\s*=\s*.*balance",
        ]
        for p in compound_patterns:
            if re.search(p, code, re.IGNORECASE):
                warnings.append(
                    f"Possible compounding pattern detected: {p} — "
                    "OP strategy is non-compounding"
                )

        return errors, warnings

    def _backup(self, round_num: int, description: str = "") -> str:
        """Create a timestamped backup."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_desc = re.sub(r"[^\w]", "_", description)[:50] if description else ""
        filename = f"round_{round_num:03d}_{ts}_{safe_desc}.py"
        backup_path = os.path.join(self.backup_dir, filename)

        if os.path.exists(self.strategy_path):
            shutil.copy2(self.strategy_path, backup_path)

        return backup_path

    @staticmethod
    def _atomic_write(path: str, content: str):
        """Write file atomically via temp + rename."""
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(content)
        os.replace(tmp, path)

    @staticmethod
    def _deep_merge(base: dict, override: dict):
        """Deep merge override into base dict."""
        for key, value in override.items():
            if (
                key in base
                and isinstance(base[key], dict)
                and isinstance(value, dict)
            ):
                StrategyModifier._deep_merge(base[key], value)
            else:
                base[key] = value
