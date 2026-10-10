#!/usr/bin/env python3
"""BACKSTOP pipeline runner — executes the full demo pipeline as a background job.

Stages (in order):
  1. generate_dataset.py  — create synthetic messy demo data
  2. ledger.py             — normalise records, compute margins, detect exceptions
  3. adversary.py          — stress-test with 5 shocks, queue revision requests
  4. self_check.py         — automated ledger reconciliation + margin verification
  5. revision_manager.py   — versioned plan revision with validation and rollback

Each run uses a unique output directory under outputs/pipeline_jobs/<job_id>/
so existing outputs are never overwritten.

NOTE: The planted-error benchmark (planted_error_fixture.py) runs SEPARATELY
from this pipeline.  It is not a pipeline stage.  The final report includes a
pointer to the most recent benchmark report if one exists.

status.json is written atomically (write-to-temp-then-rename) at every stage
transition, so WorkBuddy can poll safely without reading partial files.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
JOBS_DIR = ROOT / "outputs" / "pipeline_jobs"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_status_atomic(job_dir: Path, status: dict[str, Any]) -> None:
    """Write status.json atomically: write to temp, then rename."""
    job_dir.mkdir(parents=True, exist_ok=True)
    tmp = job_dir / "status.json.tmp"
    final = job_dir / "status.json"
    tmp.write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # os.replace is atomic on the same filesystem (POSIX rename, Windows MoveFileEx)
    os.replace(str(tmp), str(final))


def run_stage(
    job_dir: Path,
    stage_name: str,
    stage_num: int,
    total_stages: int,
    command: list[str],
    started_at_iso: str,
) -> tuple[bool, int, str]:
    """Run one pipeline stage.  Returns (success, exit_code, error_msg)."""
    log_path = job_dir / f"stage_{stage_num}_{stage_name}.log"
    err_path = job_dir / f"stage_{stage_num}_{stage_name}_stderr.log"

    stage_start = time.time()
    stage_start_iso = now_iso()

    # Mark stage as running
    write_status_atomic(job_dir, {
        "job_id": job_dir.name,
        "status": "running",
        "current_stage": stage_name,
        "stage_number": stage_num,
        "total_stages": total_stages,
        "stage_started_at": stage_start_iso,
        "pipeline_started_at": started_at_iso,
        "updated_at": stage_start_iso,
    })

    try:
        with open(log_path, "w", encoding="utf-8") as lf, \
             open(err_path, "w", encoding="utf-8") as ef:
            result = subprocess.run(
                command,
                cwd=str(ROOT),
                stdout=lf,
                stderr=ef,
                text=True,
                check=False,
            )
    except Exception as exc:
        elapsed = round(time.time() - stage_start, 2)
        write_status_atomic(job_dir, {
            "job_id": job_dir.name,
            "status": "failed",
            "current_stage": stage_name,
            "stage_number": stage_num,
            "total_stages": total_stages,
            "stage_started_at": stage_start_iso,
            "stage_finished_at": now_iso(),
            "stage_elapsed_seconds": elapsed,
            "pipeline_started_at": started_at_iso,
            "stage_exit_code": -1,
            "error": f"Exception while running {stage_name}: {exc}",
            "updated_at": now_iso(),
        })
        return False, -1, str(exc)

    elapsed = round(time.time() - stage_start, 2)
    success = result.returncode == 0

    if not success:
        # Read last 3000 chars of stderr for context
        error_snippet = ""
        try:
            error_snippet = err_path.read_text(
                encoding="utf-8", errors="replace"
            )[-3000:]
        except Exception:
            pass

        write_status_atomic(job_dir, {
            "job_id": job_dir.name,
            "status": "failed",
            "current_stage": stage_name,
            "stage_number": stage_num,
            "total_stages": total_stages,
            "stage_started_at": stage_start_iso,
            "stage_finished_at": now_iso(),
            "stage_elapsed_seconds": elapsed,
            "pipeline_started_at": started_at_iso,
            "stage_exit_code": result.returncode,
            "error": f"Stage '{stage_name}' exited with code {result.returncode}.",
            "stderr_tail": error_snippet,
            "updated_at": now_iso(),
        })
        return False, result.returncode, f"Exit code {result.returncode}"

    # Stage succeeded
    write_status_atomic(job_dir, {
        "job_id": job_dir.name,
        "status": "running",
        "current_stage": stage_name,
        "stage_number": stage_num,
        "total_stages": total_stages,
        "stage_started_at": stage_start_iso,
        "stage_finished_at": now_iso(),
        "stage_elapsed_seconds": elapsed,
        "pipeline_started_at": started_at_iso,
        "stage_exit_code": 0,
        "updated_at": now_iso(),
    })
    return True, 0, ""


# ------------------------------------------------------------------
# Pipeline definition
# ------------------------------------------------------------------

def build_stages(job_dir: Path) -> list[dict[str, Any]]:
    """Build the list of pipeline stages with their commands and output dirs."""

    data_dir = job_dir / "data"
    ledger_dir = job_dir / "ledger"
    adversary_dir = job_dir / "adversary"
    self_check_dir = job_dir / "self_check"
    revisions_dir = job_dir / "revisions"

    # Use the project venv Python if available, else fall back to sys.executable
    venv_python = str(VENV_PYTHON) if VENV_PYTHON.is_file() else sys.executable

    return [
        {
            "name": "generate_dataset",
            "command": [
                venv_python,
                str(ROOT / "generate_dataset.py"),
                "--out", str(data_dir),
                "--seed", "42",
            ],
            "output_dir": str(data_dir),
        },
        {
            "name": "ledger",
            "command": [
                venv_python,
                str(ROOT / "ledger.py"),
                "--data", str(data_dir),
                "--out", str(ledger_dir),
            ],
            "output_dir": str(ledger_dir),
        },
        {
            "name": "adversary",
            "command": [
                venv_python,
                str(ROOT / "adversary.py"),
                "--ledger", str(ledger_dir),
                "--out", str(adversary_dir),
                "--threshold", "45",
                "--max-revisions", "2",
            ],
            "output_dir": str(adversary_dir),
        },
        {
            "name": "self_check",
            "command": [
                venv_python,
                str(ROOT / "self_check.py"),
                "--data", str(data_dir),
                "--ledger", str(ledger_dir),
                "--out", str(self_check_dir),
                "--mode", "full",
            ],
            "output_dir": str(self_check_dir),
        },
        {
            "name": "revision_manager",
            "command": [
                venv_python,
                str(ROOT / "revision_manager.py"),
                "--ledger", str(ledger_dir),
                "--self-check-dir", str(self_check_dir),
                "--adversary", str(adversary_dir),
                "--out", str(revisions_dir),
                "--scenario", "ingredients_25pct",
                "--data", str(data_dir),
            ],
            "output_dir": str(revisions_dir),
        },
    ]


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the BACKSTOP demo pipeline as a background job."
    )
    parser.add_argument(
        "--job-id",
        default=None,
        help="Job ID (auto-generated if omitted)",
    )
    args = parser.parse_args()

    job_id = args.job_id or "job_" + uuid.uuid4().hex[:12]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    stages = build_stages(job_dir)
    total = len(stages)
    started_at = now_iso()

    # Write initial status
    write_status_atomic(job_dir, {
        "job_id": job_id,
        "status": "running",
        "current_stage": "init",
        "stage_number": 0,
        "total_stages": total,
        "started_at": started_at,
        "updated_at": started_at,
        "pipeline": [s["name"] for s in stages],
        "output_dirs": {
            s["name"]: s["output_dir"] for s in stages
        },
    })

    print(f"[pipeline_runner] Job {job_id} started at {started_at}", flush=True)
    print(f"[pipeline_runner] Job directory: {job_dir}", flush=True)
    print(f"[pipeline_runner] {total} stages: {[s['name'] for s in stages]}", flush=True)

    # Run each stage sequentially
    for i, stage in enumerate(stages, start=1):
        print(f"\n[pipeline_runner] --- Stage {i}/{total}: {stage['name']} ---", flush=True)
        print(f"[pipeline_runner] Command: {' '.join(stage['command'])}", flush=True)

        success, exit_code, error = run_stage(
            job_dir,
            stage["name"],
            i,
            total,
            stage["command"],
            started_at,
        )

        if not success:
            print(
                f"\n[pipeline_runner] Pipeline FAILED at stage {i} "
                f"({stage['name']}): {error}",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)

        print(f"[pipeline_runner] Stage {i} ({stage['name']}) completed.", flush=True)

    # All stages succeeded — assemble final status
    output_files: list[str] = []
    for stage in stages:
        out_dir = Path(stage["output_dir"])
        if out_dir.is_dir():
            for f in sorted(out_dir.rglob("*")):
                if f.is_file():
                    output_files.append(str(f.relative_to(job_dir)))

    # Read key summary outputs for quick reporting
    summary: dict[str, Any] = {}

    ledger_summary_path = Path(stages[1]["output_dir"]) / "ledger_summary.json"
    if ledger_summary_path.is_file():
        try:
            ls = json.loads(
                ledger_summary_path.read_text(encoding="utf-8-sig")
            )
            summary["ledger_transaction_records"] = ls.get("transaction_records")
            summary["ledger_open_exceptions"] = ls.get("open_exceptions")
            summary["ledger_verified_revenue_rm"] = ls.get("verified_revenue_rm")
            summary["ledger_generated_at"] = ls.get("generated_at_local")
        except Exception:
            summary["ledger_summary"] = "error reading summary"

    adversary_report_path = Path(stages[2]["output_dir"]) / "adversary_report.json"
    if adversary_report_path.is_file():
        try:
            adv = json.loads(
                adversary_report_path.read_text(encoding="utf-8-sig")
            )
            summary["adversary_shock_count"] = len(adv.get("shocks", []))
            summary["adversary_revision_request_count"] = len(
                adv.get("revision_requests", [])
            )
            summary["adversary_revision_rounds_completed"] = adv.get(
                "revision_rounds_completed"
            )
            summary["adversary_revision_status"] = adv.get("revision_status")
        except Exception:
            summary["adversary_report"] = "error reading report"

    # Read self_check summary
    self_check_report_path = Path(stages[3]["output_dir"]) / "self_check_report.json"
    if self_check_report_path.is_file():
        try:
            sc = json.loads(
                self_check_report_path.read_text(encoding="utf-8-sig")
            )
            summary["self_check_total_checks"] = sc.get("summary", {}).get("total_checks")
            summary["self_check_passed"] = sc.get("summary", {}).get("passed")
            summary["self_check_failed"] = sc.get("summary", {}).get("failed")
            summary["self_check_inconclusive"] = sc.get("summary", {}).get("inconclusive")
            summary["self_check_overall_status"] = sc.get("summary", {}).get("overall_status")
        except Exception:
            summary["self_check"] = "error reading report"

    # Read revision manager status
    revision_status_path = Path(stages[4]["output_dir"]) / "revision_status.json"
    if revision_status_path.is_file():
        try:
            rev = json.loads(
                revision_status_path.read_text(encoding="utf-8-sig")
            )
            summary["revision_active_version"] = rev.get("active_version")
            summary["revision_v2_accepted"] = rev.get("v2_accepted")
            summary["revision_rollback_occurred"] = rev.get("rollback_occurred")
            summary["revision_v2_self_check_status"] = rev.get("v2_self_check_status")
        except Exception:
            summary["revision"] = "error reading status"

    # Planted-error benchmark — runs SEPARATELY from the pipeline, not as a stage.
    # Include a pointer to the most recent benchmark report if one exists.
    benchmark_dir = ROOT / "outputs" / "planted_error_test"
    benchmark_rerun_dir = ROOT / "outputs" / "planted_error_test_rerun"
    benchmark_report = None
    for candidate in [
        benchmark_rerun_dir / "planted_error_report.json",
        benchmark_dir / "planted_error_report.json",
    ]:
        if candidate.is_file():
            benchmark_report = str(candidate.relative_to(ROOT))
            break
    summary["planted_error_benchmark"] = {
        "part_of_pipeline": False,
        "runs_separately": True,
        "script": "planted_error_fixture.py",
        "latest_report": benchmark_report,
        "note": (
            "The 10-error benchmark is NOT executed as a pipeline stage. "
            "It runs separately via planted_error_fixture.py against an "
            "isolated copy of the data. See the report path above if a "
            "benchmark has been run."
        ),
    }

    finish_iso = now_iso()
    final_status = {
        "job_id": job_id,
        "status": "completed",
        "current_stage": "done",
        "stage_number": total,
        "total_stages": total,
        "started_at": started_at,
        "finished_at": finish_iso,
        "updated_at": finish_iso,
        "output_dirs": {
            s["name"]: s["output_dir"] for s in stages
        },
        "output_files": output_files,
        "summary": summary,
    }
    write_status_atomic(job_dir, final_status)

    print(f"\n[pipeline_runner] Pipeline COMPLETED. Job ID: {job_id}", flush=True)
    print(f"[pipeline_runner] Total output files: {len(output_files)}", flush=True)
    print(f"[pipeline_runner] Status: {json.dumps(summary, indent=2)}", flush=True)


if __name__ == "__main__":
    main()
