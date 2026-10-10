
from __future__ import annotations

import csv
import json
import math
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("BACKSTOP")

# ============================================================
# CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent

CASE_FILE = ROOT / "backstop_case.py"
LEDGER_DIR = ROOT / "outputs" / "current_case" / "ledger"
SUMMARY_FILE = LEDGER_DIR / "ledger_summary.json"
MARGINS_FILE = LEDGER_DIR / "product_margins.csv"

ADVERSARY_SCRIPT = ROOT / "adversary.py"
ADVERSARY_OUTPUT_DIR = (
    ROOT / "outputs" / "current_case" / "adversary_demo"
)

MAX_REVISION_ROUNDS = 2
ADVERSARY_TIMEOUT_SECONDS = 300

# --- Pipeline background-job configuration ---
PIPELINE_RUNNER_SCRIPT = ROOT / "pipeline_runner.py"
JOBS_DIR = ROOT / "outputs" / "pipeline_jobs"
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

EXPECTED_COLUMNS = {
    "product",
    "selling_price_rm",
    "estimated_variable_cost_rm",
    "estimated_contribution_rm",
    "estimated_contribution_margin_pct",
    "cost_basis",
    "ingredient_cost_breakdown_json",
    "source_id",
}


# ============================================================
# INTERNAL HELPERS
# ============================================================

def error_result(message: str) -> dict[str, Any]:
    return {
        "system": "BACKSTOP",
        "success": False,
        "error": message,
    }


def _now_iso() -> str:
    """Return current timestamp in ISO-8601 with local timezone."""
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_ledger() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read and validate generated current-case Ledger files."""

    if not SUMMARY_FILE.is_file():
        raise FileNotFoundError(
            f"Ledger summary not found: {SUMMARY_FILE}"
        )

    if not MARGINS_FILE.is_file():
        raise FileNotFoundError(
            f"Product margins CSV not found: {MARGINS_FILE}"
        )

    with SUMMARY_FILE.open("r", encoding="utf-8-sig") as file:
        summary = json.load(file)

    if not isinstance(summary, dict):
        raise ValueError("Ledger summary must be a JSON object.")

    menu: list[dict[str, Any]] = []

    with MARGINS_FILE.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        reader = csv.DictReader(file)

        if not reader.fieldnames:
            raise ValueError("Product margins CSV has no header.")

        missing = EXPECTED_COLUMNS - set(reader.fieldnames)

        if missing:
            raise ValueError(
                "Missing CSV columns: " + ", ".join(sorted(missing))
            )

        for line_number, row in enumerate(reader, start=2):
            try:
                item = row["product"].strip()
                price = float(row["selling_price_rm"])
                cost = float(row["estimated_variable_cost_rm"])
                contribution = float(row["estimated_contribution_rm"])
                margin = float(
                    row["estimated_contribution_margin_pct"]
                )
                ingredients = json.loads(
                    row["ingredient_cost_breakdown_json"]
                )
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(
                    f"Invalid CSV data on line {line_number}: {exc}"
                ) from exc

            values = [price, cost, contribution, margin]

            if not item:
                raise ValueError(
                    f"Missing product name on line {line_number}."
                )

            if not all(math.isfinite(value) for value in values):
                raise ValueError(
                    f"Non-finite numeric value on line {line_number}."
                )

            if price <= 0 or cost < 0:
                raise ValueError(
                    f"Invalid price or cost on line {line_number}."
                )

            if not isinstance(ingredients, dict):
                raise ValueError(
                    f"Invalid ingredient JSON on line {line_number}."
                )

            if abs(contribution - (price - cost)) > 0.02:
                raise ValueError(
                    f"Contribution mismatch on line {line_number}."
                )

            menu.append({
                "item": item,
                "price": price,
                "cost": cost,
                "contribution": contribution,
                "margin_percent": margin,
                "cost_basis": row["cost_basis"],
                "source_id": row["source_id"],
                "ingredient_cost_breakdown": ingredients,
            })

    if not menu:
        raise ValueError("No menu items found in the Ledger CSV.")

    return summary, menu


# ============================================================
# TOOL 1: CASE STATUS
# ============================================================

@mcp.tool()
def get_case_status() -> dict:
    """Return current-case Ledger status and evidence limitations.
    Read-only. Does not run or modify project files.
    """
    try:
        summary, menu = load_ledger()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return error_result(str(exc))

    verified_revenue = summary.get("verified_revenue_rm")

    return {
        "system": "BACKSTOP",
        "case": summary.get("business"),
        "currency": summary.get("currency", "MYR"),
        "dashboard_file_exists": CASE_FILE.is_file(),
        "ledger_generated_at_local": summary.get("generated_at_local"),
        "ledger_source": str(LEDGER_DIR),
        "transaction_records": summary.get("transaction_records"),
        "receipt_records": summary.get("receipt_records"),
        "whatsapp_records": summary.get("whatsapp_records"),
        "spreadsheet_records": summary.get("spreadsheet_records"),
        "open_exceptions": summary.get("open_exceptions"),
        "verified_revenue_rm": verified_revenue,
        "revenue_status": (
            "WITHHELD: not reconciled"
            if verified_revenue is None
            else "A value exists; inspect supporting reconciliation evidence."
        ),
        "menu_items_loaded": len(menu),
        "product_margins_basis": summary.get("product_margins_basis"),
        "fixed_costs": "Unknown unless independently verified.",
        "sales_mix": "Unknown unless independently verified.",
        "execution_status": (
            "Ledger integration active; Adversary can be invoked separately. "
            "Full multi-agent orchestration is not implemented."
        ),
        "files_modified": False,
    }


# ============================================================
# TOOL 2: MENU BASELINE
# ============================================================

@mcp.tool()
def get_menu_baseline() -> dict:
    """Read menu prices, estimated costs and margins from Ledger output."""
    try:
        summary, menu = load_ledger()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return error_result(str(exc))

    return {
        "success": True,
        "source_file": str(MARGINS_FILE),
        "ledger_generated_at_local": summary.get("generated_at_local"),
        "basis": summary.get("product_margins_basis"),
        "results": [
            {
                "item": row["item"],
                "price_rm": round(row["price"], 2),
                "estimated_variable_cost_rm": round(row["cost"], 2),
                "contribution_rm": round(row["contribution"], 2),
                "margin_percent": round(row["margin_percent"], 1),
                "cost_basis": row["cost_basis"],
                "source_id": row["source_id"],
                "ingredient_cost_breakdown": (
                    row["ingredient_cost_breakdown"]
                ),
            }
            for row in menu
        ],
        "limitations": [
            "Recipe costs are synthetic assumptions, not owner-verified.",
            "Contribution margin is not the same as net profit.",
            "Revenue remains unverified when the Ledger reports null.",
            "Fixed costs and sales mix are unknown.",
        ],
    }


# ============================================================
# TOOL 3: HYPOTHETICAL COST SHOCK
# ============================================================

@mcp.tool()
def calculate_cost_shock(shock_percent: float = 25.0) -> dict:
    """Model a hypothetical increase in estimated variable costs.
    Valid range: 0–200%. Scenario only; no source data is changed.
    """
    if (
        not isinstance(shock_percent, (int, float))
        or isinstance(shock_percent, bool)
        or not math.isfinite(shock_percent)
        or not 0 <= shock_percent <= 200
    ):
        return error_result(
            "shock_percent must be a finite number between 0 and 200."
        )

    try:
        summary, menu = load_ledger()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return error_result(str(exc))

    results = []

    for row in menu:
        price = row["price"]
        baseline_cost = row["cost"]
        stressed_cost = baseline_cost * (1 + shock_percent / 100)
        stressed_contribution = price - stressed_cost
        stressed_margin = stressed_contribution / price * 100

        results.append({
            "item": row["item"],
            "price_rm": round(price, 2),
            "baseline_estimated_cost_rm": round(baseline_cost, 2),
            "stressed_estimated_cost_rm": round(stressed_cost, 2),
            "stressed_contribution_rm": round(stressed_contribution, 2),
            "stressed_margin_percent": round(stressed_margin, 1),
            "below_40_percent_margin": stressed_margin < 40,
            "source_id": row["source_id"],
        })

    return {
        "success": True,
        "shock_percent": shock_percent,
        "scenario_only": True,
        "not_a_forecast": True,
        "revenue_used": False,
        "ledger_generated_at_local": summary.get("generated_at_local"),
        "results": results,
        "limitations": [
            "Uses estimated variable costs, not verified actual costs.",
            "Does not forecast demand or sales volume.",
            "Does not include fixed costs or sales mix.",
            "Does not modify prices or business records.",
        ],
    }


# ============================================================
# TOOL 4: RUN EXISTING ADVERSARY ENGINE
# ============================================================

@mcp.tool()
def run_adversary_demo() -> dict:
    """Run the existing Adversary script against current-case Ledger data.
    Uses a dedicated output directory and a maximum of two revision rounds.
    """
    try:
        load_ledger()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return error_result(
            f"Current-case Ledger validation failed: {exc}"
        )

    if not ADVERSARY_SCRIPT.is_file():
        return error_result(
            f"Adversary script not found: {ADVERSARY_SCRIPT}"
        )

    # Avoid running concurrently or overwriting previous demo outputs.
    if ADVERSARY_OUTPUT_DIR.exists() and any(
        ADVERSARY_OUTPUT_DIR.iterdir()
    ):
        return {
            "success": False,
            "error": (
                "Adversary demo output directory already contains files. "
                "Review or rename it before running again."
            ),
            "output_dir": str(ADVERSARY_OUTPUT_DIR),
        }

    ADVERSARY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        str(ADVERSARY_SCRIPT),
        "--ledger", str(LEDGER_DIR),
        "--out", str(ADVERSARY_OUTPUT_DIR),
        "--max-revisions", str(MAX_REVISION_ROUNDS),
    ]

    try:
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=ADVERSARY_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": (
                "Adversary exceeded the "
                f"{ADVERSARY_TIMEOUT_SECONDS}-second time limit."
            ),
            "output_dir": str(ADVERSARY_OUTPUT_DIR),
        }
    except OSError as exc:
        return error_result(f"Could not launch Adversary: {exc}")

    output_files = [
        str(path.relative_to(ROOT))
        for path in ADVERSARY_OUTPUT_DIR.rglob("*")
        if path.is_file()
    ]

    return {
        "success": result.returncode == 0,
        "return_code": result.returncode,
        "max_revision_rounds": MAX_REVISION_ROUNDS,
        "stdout": result.stdout[-10000:],
        "stderr": result.stderr[-5000:],
        "output_dir": str(ADVERSARY_OUTPUT_DIR),
        "output_files": output_files,
        "note": (
            "Reports the result of the existing Adversary script. "
            "This does not execute the complete multi-agent workflow."
        ),
    }


# ============================================================
# TOOL 5: START PIPELINE (async background launch)
# ============================================================

def validate_job_id(job_id: str) -> Path:
    """Validate a job ID and return the resolved job directory.

    Raises ValueError if the ID is malformed or resolves outside JOBS_DIR.
    """
    if not isinstance(job_id, str) or not JOB_ID_PATTERN.match(job_id):
        raise ValueError(
            "job_id must be 1-64 chars of [A-Za-z0-9_-] only."
        )
    job_dir = (JOBS_DIR / job_id).resolve()
    try:
        job_dir.relative_to(JOBS_DIR.resolve())
    except ValueError as exc:
        raise ValueError("job_id resolves outside the jobs directory.") from exc
    return job_dir


@mcp.tool()
def start_pipeline() -> dict:
    """Start the full BACKSTOP demo pipeline as a background process.

    Returns immediately with a job ID and job directory.  The pipeline
    continues running after this tool returns.  Use get_pipeline_status
    to poll for completion.

    Stages: generate_dataset -> ledger -> adversary -> self_check -> revision_manager.
    Each run uses a unique output directory; existing outputs are preserved.
    """
    if not PIPELINE_RUNNER_SCRIPT.is_file():
        return error_result(
            f"Pipeline runner script not found: {PIPELINE_RUNNER_SCRIPT}"
        )

    job_id = "job_" + uuid.uuid4().hex[:12]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Write an initial status so polling immediately finds a valid state.
    initial_status = {
        "job_id": job_id,
        "status": "running",
        "current_stage": "starting",
        "stage_number": 0,
        "total_stages": 5,
        "started_at": None,  # runner will fill this in
        "updated_at": None,
        "pipeline": [
            "generate_dataset", "ledger", "adversary",
            "self_check", "revision_manager",
        ],
        "note": "Background process launching; status not yet written by runner.",
    }
    status_path = job_dir / "status.json"
    status_path.write_text(
        json.dumps(initial_status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Log files for the runner process itself (separate from stage logs).
    runner_log = job_dir / "runner.log"
    runner_err = job_dir / "runner_stderr.log"

    command = [
        sys.executable,
        str(PIPELINE_RUNNER_SCRIPT),
        "--job-id", job_id,
    ]

    # On Windows, CREATE_NO_WINDOW prevents a console pop-up;
    # CREATE_NEW_PROCESS_GROUP detaches the child so it survives
    # even if the MCP server's tool-call scope ends.
    creationflags = 0
    if sys.platform == "win32":
        creationflags |= getattr(
            subprocess, "CREATE_NO_WINDOW", 0x08000000
        )
        creationflags |= getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )

    try:
        runner_log_fp = open(runner_log, "w", encoding="utf-8")
        runner_err_fp = open(runner_err, "w", encoding="utf-8")
        proc = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdout=runner_log_fp,
            stderr=runner_err_fp,
            creationflags=creationflags,
            # Do NOT close fds — we manage the log files ourselves.
            close_fds=False,
        )
        # We intentionally do NOT call proc.wait().  The child process
        # continues running in the background.  The file handles will be
        # kept open by the OS until the child exits.
    except OSError as exc:
        runner_log_fp.close()
        runner_err_fp.close()
        # Overwrite status to reflect failure
        initial_status["status"] = "failed"
        initial_status["error"] = f"Could not launch pipeline: {exc}"
        initial_status["updated_at"] = _now_iso()
        status_path.write_text(
            json.dumps(initial_status, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return error_result(f"Could not launch pipeline: {exc}")

    return {
        "success": True,
        "job_id": job_id,
        "job_dir": str(job_dir),
        "status_file": str(status_path),
        "pipeline": [
            "generate_dataset", "ledger", "adversary",
            "self_check", "revision_manager",
        ],
        "instruction": (
            "Pipeline is running in the background. "
            "Poll get_pipeline_status with this job_id to check progress."
        ),
    }


# ============================================================
# TOOL 6: GET PIPELINE STATUS (non-blocking poll)
# ============================================================

@mcp.tool()
def get_pipeline_status(job_id: str) -> dict:
    """Read the current status of a pipeline job.

    Accepts a job ID returned by start_pipeline.
    Returns the actual current stage, status, and recent log lines.
    Does not block or wait.
    """
    try:
        job_dir = validate_job_id(job_id)
    except ValueError as exc:
        return error_result(str(exc))

    status_path = job_dir / "status.json"
    if not status_path.is_file():
        return error_result(
            f"No status file found for job '{job_id}'. "
            "The job may not have started yet."
        )

    try:
        status = json.loads(
            status_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        return error_result(f"Could not read status file: {exc}")

    # Append recent log lines for convenience (last 1500 chars of latest stage log)
    recent_logs = {}
    stage_num = status.get("stage_number", 0)
    if stage_num and isinstance(stage_num, int) and stage_num > 0:
        current_stage = status.get("current_stage", "")
        if current_stage:
            log_name = f"stage_{stage_num}_{current_stage}.log"
            log_path = job_dir / log_name
            if log_path.is_file():
                try:
                    content = log_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    recent_logs["current_stage_log_tail"] = content[-1500:]
                except OSError:
                    pass

            err_name = f"stage_{stage_num}_{current_stage}_stderr.log"
            err_path = job_dir / err_name
            if err_path.is_file():
                try:
                    content = err_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    if content.strip():
                        recent_logs["current_stage_stderr_tail"] = (
                            content[-1500:]
                        )
                except OSError:
                    pass

    result = dict(status)
    if recent_logs:
        result["recent_logs"] = recent_logs

    return result


# ============================================================
# TOOL 7: GET PIPELINE REPORT (final report for completed job)
# ============================================================

@mcp.tool()
def get_pipeline_report(job_id: str) -> dict:
    """Read the final report, logs, and output file list for a completed job.

    Accepts a job ID returned by start_pipeline.
    Returns an error if the job has not completed or has failed.
    """
    try:
        job_dir = validate_job_id(job_id)
    except ValueError as exc:
        return error_result(str(exc))

    status_path = job_dir / "status.json"
    if not status_path.is_file():
        return error_result(
            f"No status file found for job '{job_id}'."
        )

    try:
        status = json.loads(
            status_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        return error_result(f"Could not read status file: {exc}")

    job_status = status.get("status", "unknown")
    if job_status != "completed":
        return {
            "success": False,
            "job_id": job_id,
            "status": job_status,
            "error": (
                f"Job has not completed (current status: {job_status}). "
                "Use get_pipeline_status to monitor progress."
            ),
        }

    # Gather all stage logs (stages 1-5)
    stage_logs = {}
    for i in range(1, 6):
        for suffix in [".log", "_stderr.log"]:
            pattern = f"stage_{i}_*{suffix}"
            for log_path in job_dir.glob(pattern):
                try:
                    content = log_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    stage_logs[log_path.name] = content[-3000:]
                except OSError:
                    pass

    # Read key output files for the report
    report_data: dict[str, Any] = {
        "job_id": job_id,
        "success": True,
        "status": status,
        "stage_logs": stage_logs,
    }

    # Read ledger summary
    ledger_summary_path = job_dir / "ledger" / "ledger_summary.json"
    if ledger_summary_path.is_file():
        try:
            report_data["ledger_summary"] = json.loads(
                ledger_summary_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, json.JSONDecodeError):
            report_data["ledger_summary"] = "error reading file"

    # Read product margins
    margins_path = job_dir / "ledger" / "product_margins.csv"
    if margins_path.is_file():
        try:
            report_data["product_margins_csv"] = margins_path.read_text(
                encoding="utf-8-sig"
            )
        except OSError:
            pass

    # Read adversary report summary
    adv_path = job_dir / "adversary" / "adversary_report.json"
    if adv_path.is_file():
        try:
            adv = json.loads(
                adv_path.read_text(encoding="utf-8-sig")
            )
            report_data["adversary_report_summary"] = {
                "shock_count": len(adv.get("shocks", [])),
                "revision_request_count": len(
                    adv.get("revision_requests", [])
                ),
                "revision_rounds_completed": adv.get(
                    "revision_rounds_completed"
                ),
                "revision_status": adv.get("revision_status"),
                "shocks": [
                    {
                        "id": s.get("id"),
                        "name": s.get("name"),
                        "score": s.get("score"),
                        "triggers_revision": s.get("score", 0) >= 45,
                    }
                    for s in adv.get("shocks", [])
                ],
            }
        except (OSError, json.JSONDecodeError):
            report_data["adversary_report_summary"] = "error reading file"

    # Read self-check report
    sc_path = job_dir / "self_check" / "self_check_report.json"
    if sc_path.is_file():
        try:
            sc = json.loads(
                sc_path.read_text(encoding="utf-8-sig")
            )
            report_data["self_check_summary"] = {
                "total_checks": sc.get("summary", {}).get("total_checks"),
                "passed": sc.get("summary", {}).get("passed"),
                "failed": sc.get("summary", {}).get("failed"),
                "inconclusive": sc.get("summary", {}).get("inconclusive"),
                "overall_status": sc.get("summary", {}).get("overall_status"),
                "mode": sc.get("mode"),
            }
        except (OSError, json.JSONDecodeError):
            report_data["self_check_summary"] = "error reading file"

    # Read revision manager status
    rev_status_path = job_dir / "revisions" / "revision_status.json"
    if rev_status_path.is_file():
        try:
            report_data["revision_status"] = json.loads(
                rev_status_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, json.JSONDecodeError):
            report_data["revision_status"] = "error reading file"

    # Planted-error benchmark — runs SEPARATELY, not as a pipeline stage
    benchmark_dir = ROOT / "outputs" / "planted_error_test"
    benchmark_rerun_dir = ROOT / "outputs" / "planted_error_test_rerun"
    benchmark_report_path = None
    for candidate in [
        benchmark_rerun_dir / "planted_error_report.json",
        benchmark_dir / "planted_error_report.json",
    ]:
        if candidate.is_file():
            benchmark_report_path = str(candidate)
            break
    report_data["planted_error_benchmark"] = {
        "part_of_pipeline": False,
        "runs_separately": True,
        "script": "planted_error_fixture.py",
        "latest_report_path": benchmark_report_path,
        "note": (
            "The 10-error benchmark is NOT a pipeline stage. "
            "It runs separately via planted_error_fixture.py against "
            "an isolated copy of the data. If a report path is shown, "
            "a benchmark has been run; otherwise it has not."
        ),
    }

    return report_data


# ============================================================
# SERVER ENTRY POINT
# ============================================================

if __name__ == "__main__":
    mcp.run(transport="stdio")
