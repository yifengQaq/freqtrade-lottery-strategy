"""
V2ex-Agent Dashboard — FastAPI backend.

Reads existing result files (zero-intrusion on agent code) and serves a
real-time monitoring UI.

Run:
    cd /path/to/project
    source .venv/bin/activate
    uvicorn dashboard.app:app --reload --port 8501
"""

from __future__ import annotations

import difflib
import json
import os
import re
import signal
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "results"
COMPARISONS_DIR = RESULTS_DIR / "comparisons"
VERSIONS_DIR = RESULTS_DIR / "strategy_versions"
LOG_FILE = RESULTS_DIR / "continuous_run.log"
TARGET_GAP_FILE = COMPARISONS_DIR / "target_gap_history.jsonl"
COMPARISON_MATRIX_FILE = COMPARISONS_DIR / "comparison_matrix.json"
ITERATION_LOG_FILE = RESULTS_DIR / "iteration_log.json"
STRATEGY_FILE = PROJECT_ROOT / "strategies" / "LotteryMindsetStrategy.py"

app = FastAPI(title="V2ex-Agent Dashboard", version="1.0.0")

# Serve static frontend files
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_agent_pid() -> Optional[int]:
    """Find the PID of the running agent process."""
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "run_agent.py.*--continuous"],
            text=True,
        ).strip()
        pids = [int(p) for p in out.splitlines() if p.strip()]
        return pids[0] if pids else None
    except (subprocess.CalledProcessError, ValueError):
        return None


def _parse_log_events() -> dict:
    """Parse the continuous_run.log for key events."""
    if not LOG_FILE.exists():
        return {"epochs": [], "rounds": [], "auto_fixes": 0, "errors": 0}

    epochs: list[dict] = []
    rounds: list[dict] = []
    auto_fixes = 0
    errors = 0
    current_epoch = 1
    current_round = 0
    start_time: Optional[str] = None

    with open(LOG_FILE) as f:
        for line in f:
            # Extract timestamp
            ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
            ts = ts_match.group(1) if ts_match else ""

            if not start_time and ts:
                start_time = ts

            # Round markers
            m = re.search(r"Epoch (\d+) · Round (\d+)", line)
            if m:
                current_epoch = int(m.group(1))
                current_round = int(m.group(2))
                rounds.append({
                    "epoch": current_epoch,
                    "round": current_round,
                    "timestamp": ts,
                    "status": "running",
                    "auto_fix": False,
                    "description": "",
                })

            # Auto-fix events
            if "Auto-fixed LLM typos" in line:
                auto_fixes += 1
                if rounds:
                    rounds[-1]["auto_fix"] = True
                fix_match = re.search(r"Auto-fixed LLM typos.*?: ({.*})", line)
                if fix_match and rounds:
                    rounds[-1]["fix_details"] = fix_match.group(1)

            # Strategy updated (marks success)
            if "Strategy updated" in line and rounds:
                desc_match = re.search(r"Backup: .*?round_\d+_\d+__(.*?)\.py", line)
                if desc_match:
                    rounds[-1]["description"] = desc_match.group(1).replace("_", " ")
                rounds[-1]["status"] = "success"

            # Epoch reset
            if "Epoch" in line and "reset:" in line:
                reset_match = re.search(
                    r"Epoch (\d+) reset:.*scores: \[(.*?)\].*best round (\d+) \(score=([\d.]+)\)",
                    line,
                )
                if reset_match:
                    epochs.append({
                        "epoch": int(reset_match.group(1)),
                        "scores": reset_match.group(2),
                        "best_round": int(reset_match.group(3)),
                        "best_score": float(reset_match.group(4)),
                        "timestamp": ts,
                    })

            # Backtest failed
            if "Backtest failed" in line or "Patch rejected" in line:
                errors += 1
                if rounds:
                    rounds[-1]["status"] = "failed"

            # Retry events
            if "retry" in line.lower() and "Transient error" in line:
                if rounds:
                    rounds[-1]["retried"] = True

    return {
        "epochs": epochs,
        "rounds": rounds,
        "auto_fixes": auto_fixes,
        "errors": errors,
        "start_time": start_time,
        "current_epoch": current_epoch,
        "current_round": current_round,
    }


def _read_target_gap_history() -> list[dict]:
    """Read the target gap history JSONL for the CURRENT run (last R1 onward)."""
    if not TARGET_GAP_FILE.exists():
        return []

    all_records: list[dict] = []
    with open(TARGET_GAP_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    all_records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # Find the last occurrence of round=1 to isolate the current run
    last_r1_idx = 0
    for i, r in enumerate(all_records):
        if r.get("round") == 1:
            last_r1_idx = i

    return all_records[last_r1_idx:]


def _read_iteration_log() -> list[dict]:
    """Read the iteration_log.json for evaluator scores."""
    if not ITERATION_LOG_FILE.exists():
        return []
    try:
        with open(ITERATION_LOG_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _read_comparison_matrix() -> Optional[dict]:
    """Read the latest comparison matrix."""
    if not COMPARISON_MATRIX_FILE.exists():
        return None
    try:
        with open(COMPARISON_MATRIX_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _list_strategy_versions() -> list[dict]:
    """List all strategy version files with parsed metadata."""
    if not VERSIONS_DIR.exists():
        return []

    versions = []
    for f in sorted(VERSIONS_DIR.glob("round_*.py")):
        # Parse: round_008_20260301_144057__因子家族扩展__...py
        m = re.match(r"round_(\d+)_(\d{8}_\d{6})__(.*?)\.py", f.name)
        if m:
            versions.append({
                "filename": f.name,
                "round": int(m.group(1)),
                "timestamp": m.group(2),
                "description": m.group(3).replace("_", " "),
                "size": f.stat().st_size,
            })
        else:
            versions.append({
                "filename": f.name,
                "round": 0,
                "timestamp": "",
                "description": f.name,
                "size": f.stat().st_size,
            })

    return versions


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the dashboard HTML."""
    index_html = STATIC_DIR / "index.html"
    if not index_html.exists():
        return HTMLResponse("<h1>Dashboard not found</h1>", status_code=404)
    return HTMLResponse(index_html.read_text(encoding="utf-8"))


@app.get("/api/status")
async def get_status():
    """Agent running status, current epoch/round, uptime."""
    pid = _find_agent_pid()
    log_events = _parse_log_events()
    iter_log = _read_iteration_log()

    # Calculate best score from iteration log (evaluator score, higher=better)
    best_score = 0.0
    best_round = 0
    success_rounds = [r for r in iter_log if r.get("status") == "success"]
    if success_rounds:
        best_rec = max(success_rounds, key=lambda x: x.get("score", 0))
        best_score = best_rec.get("score", 0)
        best_round = best_rec.get("round", 0)

    # Uptime calculation
    uptime_str = ""
    if log_events.get("start_time"):
        try:
            start = datetime.strptime(log_events["start_time"], "%Y-%m-%d %H:%M:%S")
            delta = datetime.now() - start
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            uptime_str = f"{hours}h {minutes}m"
        except ValueError:
            pass

    return {
        "running": pid is not None,
        "pid": pid,
        "current_epoch": log_events.get("current_epoch", 0),
        "current_round": log_events.get("current_round", 0),
        "total_rounds": len(log_events.get("rounds", [])),
        "auto_fixes": log_events.get("auto_fixes", 0),
        "errors": log_events.get("errors", 0),
        "best_score": best_score,
        "best_round": best_round,
        "epoch_resets": len(log_events.get("epochs", [])),
        "uptime": uptime_str,
        "success_rate": _calc_success_rate(log_events.get("rounds", [])),
    }


def _calc_success_rate(rounds: list[dict]) -> str:
    """Calculate success rate as 'N/M (X%)'."""
    if not rounds:
        return "0/0"
    success = sum(1 for r in rounds if r.get("status") == "success")
    total = len(rounds)
    pct = success / total * 100 if total else 0
    return f"{success}/{total} ({pct:.0f}%)"


@app.get("/api/rounds")
async def get_rounds():
    """All rounds with scores, status, changes description."""
    log_events = _parse_log_events()
    gap_history = _read_target_gap_history()
    iter_log = _read_iteration_log()

    rounds = log_events.get("rounds", [])

    # Merge gap history (weighted_norm) into rounds
    score_map = {r["round"]: r for r in gap_history}
    for rd in rounds:
        gap = score_map.get(rd["round"])
        if gap:
            rd["weighted_norm"] = gap.get("weighted_norm", 0)
            rd["deltas"] = gap.get("deltas", {})
            rd["mode"] = gap.get("mode", "")

    # Merge evaluator scores + backtest metrics from iteration_log.json
    eval_map = {r["round"]: r for r in iter_log}
    for rd in rounds:
        ev = eval_map.get(rd["round"])
        if ev:
            rd["eval_score"] = ev.get("score", 0)
            rd["eval_status"] = ev.get("status", "")
            if not rd.get("description") and ev.get("changes_made"):
                rd["description"] = ev["changes_made"]
            # Attach key backtest metrics
            bm = ev.get("backtest_metrics", {})
            if bm:
                rd["profit_pct"] = bm.get("total_profit_pct")
                rd["max_dd_pct"] = bm.get("max_drawdown_pct")
                rd["total_trades"] = bm.get("total_trades")
                rd["win_rate"] = bm.get("win_rate")
                rd["sharpe"] = bm.get("sharpe_ratio")
                rd["profit_factor"] = bm.get("profit_factor")
                rd["avg_profit"] = bm.get("avg_profit_per_trade_pct")

    return {"rounds": rounds, "epoch_resets": log_events.get("epochs", [])}


@app.get("/api/metrics")
async def get_metrics():
    """Latest comparison matrix with per-window metrics."""
    matrix = _read_comparison_matrix()
    if matrix is None:
        return {"matrix": None, "windows": []}

    return {
        "matrix": matrix,
        "windows": matrix.get("windows", []),
        "round": matrix.get("round", 0),
    }


@app.get("/api/history")
async def get_history():
    """Score history — merges evaluator scores with gap data."""
    gap_records = _read_target_gap_history()
    iter_log = _read_iteration_log()

    # Build eval score map
    eval_map = {r["round"]: r.get("score", 0) for r in iter_log}

    # Merge eval scores into gap records
    for r in gap_records:
        r["eval_score"] = eval_map.get(r["round"], 0)

    return {"records": gap_records, "iter_log_count": len(iter_log)}


@app.get("/api/versions")
async def get_versions():
    """List all saved strategy versions."""
    return {"versions": _list_strategy_versions()}


@app.get("/api/version/{filename}")
async def get_version_code(filename: str):
    """Get the source code of a specific strategy version."""
    # Security: only allow files from versions dir
    filepath = VERSIONS_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="Version not found")
    if not str(filepath.resolve()).startswith(str(VERSIONS_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    return {"filename": filename, "code": filepath.read_text(encoding="utf-8")}


@app.get("/api/diff")
async def get_diff(
    a: str = Query(..., description="First version filename"),
    b: str = Query(..., description="Second version filename"),
):
    """Generate a unified diff between two strategy versions."""
    path_a = VERSIONS_DIR / a
    path_b = VERSIONS_DIR / b

    if not path_a.exists():
        raise HTTPException(status_code=404, detail=f"Version '{a}' not found")
    if not path_b.exists():
        raise HTTPException(status_code=404, detail=f"Version '{b}' not found")

    code_a = path_a.read_text(encoding="utf-8").splitlines()
    code_b = path_b.read_text(encoding="utf-8").splitlines()

    diff = list(difflib.unified_diff(
        code_a, code_b,
        fromfile=a, tofile=b,
        lineterm="",
    ))

    # Also produce an HTML side-by-side diff
    html_diff = difflib.HtmlDiff(wrapcolumn=80).make_table(
        code_a, code_b,
        fromdesc=a, todesc=b,
        context=True, numlines=3,
    )

    return {"diff_text": "\n".join(diff), "diff_html": html_diff}


@app.get("/api/log")
async def get_log(
    tail: int = Query(200, ge=1, le=5000, description="Number of lines to tail"),
):
    """Tail the continuous_run.log."""
    if not LOG_FILE.exists():
        return {"lines": [], "total_lines": 0}

    with open(LOG_FILE) as f:
        all_lines = f.readlines()

    total = len(all_lines)
    lines = [l.rstrip() for l in all_lines[-tail:]]

    return {"lines": lines, "total_lines": total}


@app.post("/api/control/stop")
async def stop_agent():
    """Gracefully stop the agent process (SIGINT)."""
    pid = _find_agent_pid()
    if pid is None:
        raise HTTPException(status_code=404, detail="Agent process not found")
    try:
        os.kill(pid, signal.SIGINT)
        return {"message": f"Sent SIGINT to PID {pid}", "pid": pid}
    except ProcessLookupError:
        raise HTTPException(status_code=404, detail="Process already exited")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")


@app.post("/api/control/start")
async def start_agent():
    """Start the agent in continuous mode."""
    pid = _find_agent_pid()
    if pid is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Agent already running (PID {pid})",
        )

    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    script = PROJECT_ROOT / "scripts" / "run_agent.py"
    log_path = LOG_FILE

    proc = subprocess.Popen(
        [str(venv_python), str(script), "--continuous"],
        cwd=str(PROJECT_ROOT),
        stdout=open(str(log_path), "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    return {"message": f"Agent started with PID {proc.pid}", "pid": proc.pid}
