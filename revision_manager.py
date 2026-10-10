#!/usr/bin/env python3
"""
BACKSTOP Revision Manager: versioned plan revisions with validation and rollback.

This script implements an explicit versioning system for the financial plan:

  v1 (immutable baseline) — the original ledger output, never overwritten.
  v2 (proposed revision) — a revised plan stored in a separate versioned directory.

The revision manager:
  1. Copies the v1 baseline to an immutable location.
  2. Applies a deterministic revision (e.g., ingredient costs +25% per the
     adversary's SHOCK-INGREDIENTS-25 revision request).
  3. Runs self_check.py against v2.
  4. If all mandatory checks pass → v2 is accepted as the active plan.
  5. If any mandatory check fails → v2 is rejected, v1 is restored as active,
     and the rollback is recorded in a machine-readable revision log.

Because the current project only generates revision REQUESTS (not actual AI
agent revisions), this script applies a deterministic rule-based revision
rather than pretending an independent agent revised the plan. The revision
is clearly labelled as "deterministic_rule_based" in the revision log.

Usage:
  python revision_manager.py --ledger outputs/ledger --self-check-dir outputs/self_check \
      --adversary outputs/adversary --out outputs/revisions --scenario ingredients_25pct

  python revision_manager.py --ledger outputs/ledger --self-check-dir outputs/self_check \
      --adversary outputs/adversary --out outputs/revisions --scenario ingredients_25pct \
      --force-fail-check  (deliberately corrupts v2 to test rollback)
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ------------------------------------------------------------------
# Revision scenarios
# ------------------------------------------------------------------

def apply_ingredients_25pct_revision(
    ledger_dir: Path,
    v2_dir: Path,
    force_fail: bool = False,
) -> dict[str, Any]:
    """Apply the +25% ingredient cost revision (from SHOCK-INGREDIENTS-25).

    Creates a revised product_margins.csv in v2_dir where:
    - Ingredient costs are multiplied by 1.25
    - Packaging costs are held constant
    - Variable cost, contribution, and margin% are recalculated
    - All other ledger files are copied unchanged from v1
    """
    v2_dir.mkdir(parents=True, exist_ok=True)

    # Copy all ledger files to v2 as the starting point
    for f in ledger_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, v2_dir / f.name)

    # Read and revise product_margins.csv
    pm_path = v2_dir / "product_margins.csv"
    products = read_csv(pm_path)

    packaging_terms = ("packaging", "package", "cup", "container", "wrapper", "bag", "napkin", "cutlery", "box")
    changes: list[dict] = []

    for p in products:
        original_price = float(p.get("selling_price_rm", "0"))
        original_cost = float(p.get("estimated_variable_cost_rm", "0"))
        original_contrib = float(p.get("estimated_contribution_rm", "0"))
        original_margin = float(p.get("estimated_contribution_margin_pct", "0"))

        breakdown_json = p.get("ingredient_cost_breakdown_json", "{}")
        try:
            breakdown = json.loads(breakdown_json)
        except (json.JSONDecodeError, TypeError):
            breakdown = {}

        # Revise: ingredient costs × 1.25, packaging held constant
        revised_breakdown = {}
        ingredient_cost_original = 0.0
        ingredient_cost_revised = 0.0
        packaging_cost = 0.0
        for k, v in breakdown.items():
            v = float(v)
            if any(term in k.lower() for term in packaging_terms):
                revised_breakdown[k] = round(v, 2)
                packaging_cost += v
            else:
                revised_breakdown[k] = round(v * 1.25, 2)
                ingredient_cost_original += v
                ingredient_cost_revised += v * 1.25

        revised_cost = round(ingredient_cost_revised + packaging_cost, 2)
        revised_contrib = round(original_price - revised_cost, 2)
        revised_margin = round(revised_contrib / original_price * 100, 1) if original_price else None

        if force_fail:
            # Deliberately corrupt the contribution to not match price - cost
            # This simulates a revision that introduces an arithmetic error
            revised_contrib = round(revised_contrib + 5.00, 2)  # off by RM 5.00
            revised_margin = round(revised_contrib / original_price * 100, 1) if original_price else None

        changes.append({
            "product": p.get("product"),
            "original_variable_cost_rm": original_cost,
            "revised_variable_cost_rm": revised_cost,
            "original_contribution_rm": original_contrib,
            "revised_contribution_rm": revised_contrib,
            "original_margin_pct": original_margin,
            "revised_margin_pct": revised_margin,
            "cost_change_rm": round(revised_cost - original_cost, 2),
            "margin_change_pct": round(revised_margin - original_margin, 1) if revised_margin and original_margin else None,
        })

        p["estimated_variable_cost_rm"] = revised_cost
        p["estimated_contribution_rm"] = revised_contrib
        p["estimated_contribution_margin_pct"] = revised_margin
        p["ingredient_cost_breakdown_json"] = json.dumps(revised_breakdown, ensure_ascii=False)
        p["cost_basis"] = "revised: synthetic recipe × 1.25 ingredient multiplier; owner verification required"
        p["source_id"] = "REVISION-V2-INGREDIENTS-25"

    write_csv(pm_path, products)

    # Update break_even.json with revised contributions
    be_path = v2_dir / "break_even.json"
    be = read_json(be_path)
    if be and "per_product" in be:
        rent = be.get("fixed_cost_inputs", {}).get("rent_rm_month", 450.0)
        for i, pp in enumerate(be["per_product"]):
            if i < len(changes):
                revised_contrib = changes[i]["revised_contribution_rm"]
                pp["contribution_per_unit_rm"] = revised_contrib
                pp["break_even_units_month_rent_only"] = round(rent / revised_contrib, 1) if revised_contrib > 0 else None
        be_path.write_text(json.dumps(be, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "scenario": "ingredients_25pct",
        "description": "Ingredient costs increased by 25%, packaging held constant. "
                       "Variable cost, contribution, and margin% recalculated.",
        "trigger": "SHOCK-INGREDIENTS-25 (adversary revision request REVREQ-001)",
        "changes_per_product": changes,
        "force_fail_mode": force_fail,
    }


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="BACKSTOP versioned revision manager.")
    parser.add_argument("--ledger", required=True, help="v1 baseline ledger directory")
    parser.add_argument("--self-check-dir", default=None, help="v1 self-check directory (for reference)")
    parser.add_argument("--adversary", default=None, help="Adversary output directory (for revision requests)")
    parser.add_argument("--out", required=True, help="Output directory for versioned revisions")
    parser.add_argument("--scenario", default="ingredients_25pct",
                        choices=["ingredients_25pct"],
                        help="Revision scenario to apply")
    parser.add_argument("--force-fail-check", action="store_true",
                        help="Deliberately corrupt v2 to test rollback (acceptance test 3)")
    parser.add_argument("--data", default=None,
                        help="Source data directory (for running self_check on v2)")
    parser.add_argument("--self-check-script", default=None,
                        help="Path to self_check.py (if not in same directory)")
    args = parser.parse_args()

    ledger_dir = Path(args.ledger)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Locate self_check.py
    script_dir = Path(__file__).resolve().parent
    self_check_script = Path(args.self_check_script) if args.self_check_script else (script_dir / "self_check.py")
    venv_python = script_dir / ".venv" / "Scripts" / "python.exe"
    python_exe = str(venv_python) if venv_python.is_file() else None

    # --- Step 1: Preserve v1 as immutable baseline ---
    v1_dir = out_dir / "v1_baseline"
    v1_dir.mkdir(parents=True, exist_ok=True)
    for f in ledger_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, v1_dir / f.name)

    # Copy v1 self-check report if available
    v1_self_check_path = None
    if args.self_check_dir:
        sc_path = Path(args.self_check_dir) / "self_check_report.json"
        if sc_path.exists():
            shutil.copy2(sc_path, v1_dir / "self_check_report.json")
            v1_self_check_path = v1_dir / "self_check_report.json"

    v1_report = read_json(v1_dir / "self_check_report.json") if v1_self_check_path else {}

    # --- Step 2: Create v2 with the revision applied ---
    v2_dir = out_dir / "v2_revised"
    if v2_dir.exists():
        shutil.rmtree(v2_dir)
    v2_dir.mkdir(parents=True, exist_ok=True)

    if args.scenario == "ingredients_25pct":
        revision_details = apply_ingredients_25pct_revision(
            v1_dir, v2_dir, force_fail=args.force_fail_check
        )
    else:
        raise SystemExit(f"Unknown scenario: {args.scenario}")

    # --- Step 3: Run self_check against v2 ---
    v2_self_check_dir = v2_dir / "self_check"
    v2_self_check_dir.mkdir(parents=True, exist_ok=True)

    v2_self_check_status = "not_run"
    v2_self_check_report = {}

    if python_exe and self_check_script.exists() and args.data:
        import subprocess
        result = subprocess.run(
            [
                python_exe,
                str(self_check_script),
                "--data", args.data,
                "--ledger", str(v2_dir),
                "--out", str(v2_self_check_dir),
                "--mode", "arithmetic",  # v2 only needs arithmetic consistency, not recipe comparison
            ],
            capture_output=True, text=True, check=False,
        )
        v2_sc_path = v2_self_check_dir / "self_check_report.json"
        if v2_sc_path.exists():
            v2_self_check_report = read_json(v2_sc_path)
            v2_self_check_status = v2_self_check_report.get("summary", {}).get("overall_status", "unknown")
        else:
            v2_self_check_status = "error"
            v2_self_check_report = {
                "error": "self_check.py did not produce a report",
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            }
    elif not args.data:
        v2_self_check_status = "skipped_no_data_dir"
        v2_self_check_report = {"note": "No --data argument provided; self_check could not run against v2."}
    else:
        v2_self_check_status = "skipped_no_self_check_script"
        v2_self_check_report = {"note": f"self_check.py not found at {self_check_script}"}

    # --- Step 4: Accept or reject v2 ---
    v2_accepted = v2_self_check_status == "pass"
    rollback_occurred = not v2_accepted and v2_self_check_status not in ("not_run", "skipped_no_data_dir", "skipped_no_self_check_script")

    if v2_accepted:
        active_version = "v2"
        active_dir = v2_dir
    else:
        active_version = "v1"
        active_dir = v1_dir

    # --- Step 5: Write revision log ---
    revision_log = {
        "generated_at_local": now_iso(),
        "v1_dir": str(v1_dir.resolve()),
        "v2_dir": str(v2_dir.resolve()),
        "active_version": active_version,
        "active_dir": str(active_dir.resolve()),
        "v2_accepted": v2_accepted,
        "rollback_occurred": rollback_occurred,
        "revision_details": revision_details,
        "v1_self_check_status": v1_report.get("summary", {}).get("overall_status", "not_available"),
        "v2_self_check_status": v2_self_check_status,
        "v2_self_check_summary": v2_self_check_report.get("summary", {}),
        "v2_self_check_failed_checks": [
            c for c in v2_self_check_report.get("checks", [])
            if c.get("status") == "fail"
        ] if v2_self_check_report else [],
        "note": (
            "v1 is preserved as an immutable baseline and is never overwritten. "
            "v2 is stored in a separate versioned directory. "
            "If v2 fails validation, v1 is restored as the active plan and the "
            "rollback is recorded here. "
            "The revision is deterministic and rule-based (not an AI agent revision). "
            "Revision requests from the adversary are queued proposals; this script "
            "applies one such proposal deterministically for testing."
        ),
    }

    log_path = out_dir / "revision_log.json"
    log_path.write_text(json.dumps(revision_log, indent=2, ensure_ascii=False), encoding="utf-8")

    # Write a summary status file
    status = {
        "active_version": active_version,
        "v2_accepted": v2_accepted,
        "rollback_occurred": rollback_occurred,
        "v2_self_check_status": v2_self_check_status,
        "revision_log": str(log_path.resolve()),
    }
    (out_dir / "revision_status.json").write_text(
        json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("BACKSTOP Revision Manager completed.")
    print(f"v1 (immutable baseline): {v1_dir.resolve()}")
    print(f"v2 (revised plan):       {v2_dir.resolve()}")
    print(f"Active version:          {active_version}")
    print(f"v2 accepted:             {v2_accepted}")
    print(f"Rollback occurred:       {rollback_occurred}")
    print(f"v2 self-check status:    {v2_self_check_status}")
    print(f"Revision log:            {log_path.resolve()}")


if __name__ == "__main__":
    main()
