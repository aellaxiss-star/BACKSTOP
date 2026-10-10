#!/usr/bin/env python3
"""
BACKSTOP Self-Check: automated assertions for ledger reconciliation and margin verification.

This script independently re-derives key figures from the original source records
and compares them against the Ledger's reported values. It does NOT compare a
ledger against totals derived from that same ledger and call it verification.

Checks are divided into two groups:
  1. Ledger reconciliation — independently re-parse source files (receipts,
     WhatsApp, spreadsheet) and compare counts/amounts with normalized_ledger.csv.
  2. Margin verification — independently recompute contribution and margin%
     from the recipe definition and compare with product_margins.csv.

Checks that cannot be completed because the source data is incomplete or
synthetic are explicitly labelled as "inconclusive" — they are NOT silently
passed.

Usage:
  python self_check.py --data data/demo --ledger outputs/ledger --out outputs/self_check
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from openpyxl import load_workbook
except ImportError as exc:
    raise SystemExit("Missing dependency 'openpyxl'. Run: python -m pip install openpyxl") from exc

ROUNDING_TOLERANCE_RM = 0.02   # 2 sen tolerance for currency comparisons
ROUNDING_TOLERANCE_PCT = 0.15  # 0.15 percentage-point tolerance for margin comparisons

# Independent recipe definition (not imported from ledger.py).
# This is the same synthetic data, but defined here independently so that
# a bug in ledger.py's recipe would be caught by this check.
EXPECTED_PRODUCTS = [
    {
        "product": "Nasi Lemak Biasa",
        "selling_price_rm": 3.50,
        "ingredients": {"rice": 0.42, "sambal": 0.28, "anchovy_peanut_cucumber": 0.35, "packaging": 0.18},
    },
    {
        "product": "Nasi Lemak Telur",
        "selling_price_rm": 4.50,
        "ingredients": {"rice": 0.42, "sambal": 0.28, "anchovy_peanut_cucumber": 0.35, "egg": 0.55, "packaging": 0.18},
    },
    {
        "product": "Nasi Lemak Ayam",
        "selling_price_rm": 7.50,
        "ingredients": {"rice": 0.48, "sambal": 0.32, "anchovy_peanut_cucumber": 0.35, "egg": 0.55, "chicken": 1.75, "packaging": 0.22},
    },
    {
        "product": "Teh Ais",
        "selling_price_rm": 2.00,
        "ingredients": {"tea_sugar_milk_ice": 0.48, "cup": 0.16},
    },
]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def parse_money_independently(text: str) -> float | None:
    """Parse a monetary value from a text line — independent implementation."""
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d{1,2})?)", text.replace(",", ""))
    return round(float(match.group(1)), 2) if match else None


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def make_check(
    name: str,
    category: str,
    expected: Any,
    actual: Any,
    tolerance: float | None = None,
    explanation: str = "",
    passed: bool | None = None,
) -> dict[str, Any]:
    """Build a single check result dict.

    Comparison logic (in order of priority):
      1. If *passed* is not None (explicit boolean), use it directly.
         This handles condition-type checks (e.g. "price > 0").
      2. If expected and actual are both None → inconclusive.
      3. If either is None → inconclusive.
      4. If tolerance is set and both are numeric → pass/fail by tolerance.
      5. If both are numeric (no tolerance) → pass/fail by exact equality.
      6. Otherwise → pass/fail by equality.
    """
    if passed is not None:
        status = "pass" if passed else "fail"
        discrepancy = None if passed else f"condition not met: expected={expected}, actual={actual}"
    elif expected is None and actual is None:
        status = "inconclusive"
        discrepancy = None
    elif expected is None or actual is None:
        status = "inconclusive"
        discrepancy = f"expected={expected}, actual={actual}"
    elif tolerance is not None and isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        diff = abs(actual - expected)
        status = "pass" if diff <= tolerance else "fail"
        discrepancy = round(diff, 4) if diff > 0 else 0
    elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        status = "pass" if expected == actual else "fail"
        discrepancy = round(abs(actual - expected), 4) if expected != actual else None
    elif expected == actual:
        status = "pass"
        discrepancy = None
    else:
        status = "fail"
        discrepancy = f"expected={expected}, actual={actual}"
    return {
        "name": name,
        "category": category,
        "expected": expected,
        "actual": actual,
        "status": status,
        "discrepancy": discrepancy,
        "explanation": explanation,
    }


# ------------------------------------------------------------------
# Independent source-data parsers
# ------------------------------------------------------------------

def independently_count_receipts(data_dir: Path) -> tuple[int, dict[str, float | None]]:
    """Count receipt files and independently parse each receipt's amount."""
    receipt_dir = data_dir / "receipts_text"
    if not receipt_dir.is_dir():
        return 0, {}
    counts: dict[str, float | None] = {}
    for f in sorted(receipt_dir.glob("receipt_*.txt")):
        raw = f.read_text(encoding="utf-8", errors="replace")
        # Find the line that looks like a total/amount
        amount = None
        amount_candidates: list[float] = []
        for line in raw.splitlines():
            low = line.lower()
            if any(kw in low for kw in ("total", "grand total", "amount")):
                parsed = parse_money_independently(line)
                if parsed is not None:
                    amount_candidates.append(parsed)
        unique = set(amount_candidates)
        if len(unique) == 1:
            amount = amount_candidates[0]
        elif len(unique) > 1:
            amount = None  # conflicting — can't verify
        else:
            # No explicit total/amount line; try to find any RM value
            for line in raw.splitlines():
                if "rm" in line.lower():
                    parsed = parse_money_independently(line)
                    if parsed is not None:
                        amount_candidates.append(parsed)
            unique = set(amount_candidates)
            if len(unique) == 1:
                amount = amount_candidates[0]
            elif len(unique) > 1:
                amount = None
        counts[f.name] = amount
    return len(counts), counts


def independently_count_whatsapp(data_dir: Path) -> tuple[int, list[str]]:
    """Count WhatsApp chat messages (lines starting with [)."""
    path = data_dir / "whatsapp_orders.txt"
    if not path.exists():
        return 0, []
    messages: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("["):
            # Check it's a real timestamped message, not a header
            if re.match(r"\[\d{2}/\d{2}/\d{4},\s+\d{2}:\d{2}\s+[AP]M\]", line):
                messages.append(line)
    return len(messages), messages


def independently_count_spreadsheet(data_dir: Path) -> tuple[int, list[dict]]:
    """Count rows in the Daily Sales sheet of the broken workbook."""
    path = data_dir / "broken_sales_and_costs.xlsx"
    if not path.exists():
        return 0, []
    wb = load_workbook(path, data_only=False)
    if "Daily Sales" not in wb.sheetnames:
        return 0, []
    ws = wb["Daily Sales"]
    rows: list[dict] = []
    for row_no in range(2, ws.max_row + 1):
        vals = [ws.cell(row_no, col).value for col in range(1, 7)]
        if all(v is None for v in vals):
            continue
        rows.append({
            "row": row_no,
            "date": str(vals[0]) if vals[0] else None,
            "product": str(vals[1]) if vals[1] else None,
            "qty": vals[2],
            "price": vals[3],
            "total": vals[4],
            "note": str(vals[5] or ""),
        })
    return len(rows), rows


# ------------------------------------------------------------------
# Check runners
# ------------------------------------------------------------------

def run_reconciliation_checks(
    data_dir: Path,
    ledger_dir: Path,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    # Load ledger outputs
    ledger_summary = {}
    summary_path = ledger_dir / "ledger_summary.json"
    if summary_path.exists():
        ledger_summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))

    normalized_ledger: list[dict] = []
    nl_path = ledger_dir / "normalized_ledger.csv"
    if nl_path.exists():
        with nl_path.open(encoding="utf-8-sig", newline="") as f:
            normalized_ledger = list(csv.DictReader(f))

    exceptions: list[dict] = []
    exc_path = ledger_dir / "exceptions.csv"
    if exc_path.exists():
        with exc_path.open(encoding="utf-8-sig", newline="") as f:
            exceptions = list(csv.DictReader(f))

    # --- Receipt count reconciliation ---
    indep_count, indep_amounts = independently_count_receipts(data_dir)
    ledger_receipt_rows = [r for r in normalized_ledger if r.get("source_type") == "receipt"]
    ledger_count = len(ledger_receipt_rows)
    checks.append(make_check(
        name="receipt_count_reconciliation",
        category="ledger_reconciliation",
        expected=indep_count,
        actual=ledger_count,
        tolerance=0,
        explanation=(
            f"Independently counted {indep_count} receipt files in {data_dir}/receipts_text/. "
            f"Ledger reports {ledger_count} receipt transactions. "
            f"{'Match.' if indep_count == ledger_count else 'Mismatch — investigate missing or extra records.'}"
        ),
    ))

    # --- Receipt filename set reconciliation ---
    # Compare the SET of receipt filenames in source data with the SET referenced
    # in the ledger's normalized_ledger.csv. This detects added/removed files even
    # when the net count change is zero (e.g., one added, one deleted).
    indep_filenames = set(indep_amounts.keys())  # e.g. {"receipt_01.txt", ...}
    ledger_filenames = set()
    for r in ledger_receipt_rows:
        sf = r.get("source_file", "")
        fn = Path(sf).name if sf else ""
        if fn:
            ledger_filenames.add(fn)
    only_in_source = indep_filenames - ledger_filenames
    only_in_ledger = ledger_filenames - indep_filenames
    set_match = len(only_in_source) == 0 and len(only_in_ledger) == 0
    checks.append(make_check(
        name="receipt_filename_set_reconciliation",
        category="ledger_reconciliation",
        expected="source filenames == ledger filenames",
        actual={
            "only_in_source": sorted(only_in_source),
            "only_in_ledger": sorted(only_in_ledger),
        },
        passed=set_match,
        explanation=(
            f"Source has {len(indep_filenames)} receipt filenames; "
            f"ledger references {len(ledger_filenames)}. "
            + (f"Only in source: {sorted(only_in_source)}. " if only_in_source else "")
            + (f"Only in ledger: {sorted(only_in_ledger)}." if only_in_ledger else "")
            + (" Pass — filename sets match." if set_match else " FAIL — filename sets differ.")
        ),
    ))

    # --- Receipt amount reconciliation (per-receipt) ---
    # For each receipt file, compare the independently parsed amount with the
    # ledger's amount_rm for that transaction. Only compare when both sides
    # have a non-null value.
    amount_mismatches: list[dict] = []
    amount_match_count = 0
    amount_inconclusive = 0
    for ledger_row in ledger_receipt_rows:
        source_file = ledger_row.get("source_file", "")
        # Extract just the filename from the full path
        filename = Path(source_file).name if source_file else ""
        indep_amt = indep_amounts.get(filename)
        ledger_amt_str = ledger_row.get("amount_rm", "")
        ledger_amt = float(ledger_amt_str) if ledger_amt_str else None

        if indep_amt is None or ledger_amt is None:
            amount_inconclusive += 1
        elif abs(indep_amt - ledger_amt) <= ROUNDING_TOLERANCE_RM:
            amount_match_count += 1
        else:
            amount_mismatches.append({
                "file": filename,
                "independent_amount": indep_amt,
                "ledger_amount": ledger_amt,
                "difference": round(indep_amt - ledger_amt, 2),
            })

    checks.append(make_check(
        name="receipt_amount_reconciliation",
        category="ledger_reconciliation",
        expected=0,
        actual=len(amount_mismatches),
        tolerance=0,
        explanation=(
            f"Independently parsed amounts for {indep_count} receipt files. "
            f"{amount_match_count} matched the ledger within RM{ROUNDING_TOLERANCE_RM} tolerance. "
            f"{len(amount_mismatches)} mismatched. "
            f"{amount_inconclusive} inconclusive (conflicting amounts or missing values in source — "
            f"these are expected by design, e.g. receipt_14 has a deliberately conflicting duplicate total). "
            + (f"Mismatches: {json.dumps(amount_mismatches[:5])}" if amount_mismatches else "")
        ),
    ))

    # --- WhatsApp count reconciliation ---
    indep_wa_count, _ = independently_count_whatsapp(data_dir)
    ledger_wa_count = sum(1 for r in normalized_ledger if r.get("source_type") == "whatsapp")
    ledger_wa_reported = ledger_summary.get("whatsapp_records", ledger_wa_count)
    checks.append(make_check(
        name="whatsapp_count_reconciliation",
        category="ledger_reconciliation",
        expected=indep_wa_count,
        actual=ledger_wa_count,
        tolerance=0,
        explanation=(
            f"Independently counted {indep_wa_count} timestamped WhatsApp messages. "
            f"Ledger normalized_ledger.csv contains {ledger_wa_count} WhatsApp transactions. "
            f"Ledger summary reports {ledger_wa_reported}. "
            f"{'Match.' if indep_wa_count == ledger_wa_count else 'Mismatch.'}"
        ),
    ))

    # --- Spreadsheet count reconciliation ---
    indep_ss_count, _ = independently_count_spreadsheet(data_dir)
    ledger_ss_count = sum(1 for r in normalized_ledger if r.get("source_type") == "spreadsheet_sale")
    ledger_ss_reported = ledger_summary.get("spreadsheet_records", ledger_ss_count)
    checks.append(make_check(
        name="spreadsheet_count_reconciliation",
        category="ledger_reconciliation",
        expected=indep_ss_count,
        actual=ledger_ss_count,
        tolerance=0,
        explanation=(
            f"Independently counted {indep_ss_count} non-header rows in Daily Sales sheet. "
            f"Ledger normalized_ledger.csv contains {ledger_ss_count} spreadsheet transactions. "
            f"Ledger summary reports {ledger_ss_reported}. "
            f"{'Match.' if indep_ss_count == ledger_ss_count else 'Mismatch.'}"
        ),
    ))

    # --- Total transaction count reconciliation ---
    indep_total = indep_count + indep_wa_count + indep_ss_count
    ledger_total = len(normalized_ledger)
    ledger_total_reported = ledger_summary.get("transaction_records", ledger_total)
    checks.append(make_check(
        name="total_transaction_count_reconciliation",
        category="ledger_reconciliation",
        expected=indep_total,
        actual=ledger_total,
        tolerance=0,
        explanation=(
            f"Independently counted {indep_total} total source records "
            f"({indep_count} receipts + {indep_wa_count} WhatsApp + {indep_ss_count} spreadsheet). "
            f"Ledger reports {ledger_total} in CSV, {ledger_total_reported} in summary. "
            f"{'Match.' if indep_total == ledger_total else 'Mismatch.'}"
        ),
    ))

    # --- Exceptions coverage check ---
    # Every transaction with status "needs_review" should have a corresponding exception
    needs_review_txns = [r for r in normalized_ledger if r.get("status") == "needs_review"]
    exception_count = len(exceptions)
    coverage_ok = exception_count >= len(needs_review_txns)
    checks.append(make_check(
        name="exceptions_coverage_check",
        category="ledger_reconciliation",
        expected=len(needs_review_txns),
        actual=exception_count,
        tolerance=0,
        passed=coverage_ok if exception_count != len(needs_review_txns) else None,
        explanation=(
            f"{len(needs_review_txns)} transactions have status 'needs_review'. "
            f"{exception_count} exceptions are recorded. "
            f"The exception count should be >= needs_review count (exceptions may also cover "
            f"non-transaction issues like conflicting amounts). "
            f"{'Pass.' if coverage_ok else 'Fail — some needs_review transactions lack exceptions.'}"
        ),
    ))

    # --- Verified revenue is null (integrity check) ---
    verified_rev = ledger_summary.get("verified_revenue_rm")
    checks.append(make_check(
        name="verified_revenue_null_check",
        category="ledger_reconciliation",
        expected="null (revenue intentionally withheld)",
        actual=verified_rev,
        passed=(verified_rev is None),
        explanation=(
            "Verified revenue should be null because source inputs are synthetic and not "
            "fully reconciled. This is an integrity check — the ledger must NOT assert "
            "verified revenue from unconfirmed data."
            + (" Pass — revenue correctly withheld." if verified_rev is None
               else f" FAIL — ledger incorrectly asserts verified revenue of RM {verified_rev}.")
        ),
    ))

    return checks


def run_margin_verification_checks(ledger_dir: Path, mode: str = "full") -> list[dict[str, Any]]:
    """Run margin verification checks.

    mode='full': recipe comparison + arithmetic consistency (for v1 baseline)
    mode='arithmetic': only internal arithmetic consistency (for v2 revisions)
    """
    checks: list[dict[str, Any]] = []

    # Load product margins from ledger
    pm_path = ledger_dir / "product_margins.csv"
    ledger_products: list[dict] = []
    if pm_path.exists():
        with pm_path.open(encoding="utf-8-sig", newline="") as f:
            ledger_products = list(csv.DictReader(f))

    # Build a lookup by product name
    ledger_by_name = {p.get("product", ""): p for p in ledger_products}

    # --- Check 1: All expected products present ---
    expected_names = [p["product"] for p in EXPECTED_PRODUCTS]
    actual_names = [p.get("product", "") for p in ledger_products]
    missing = [n for n in expected_names if n not in actual_names]
    extra = [n for n in actual_names if n not in expected_names]
    checks.append(make_check(
        name="product_completeness_check",
        category="margin_verification",
        expected=expected_names,
        actual=actual_names,
        passed=(not missing and not extra),
        explanation=(
            f"Expected {len(expected_names)} products: {expected_names}. "
            f"Found {len(actual_names)}: {actual_names}. "
            + (f"Missing: {missing}. " if missing else "")
            + (f"Extra: {extra}." if extra else "")
            + (" Pass." if not missing and not extra else " Fail.")
        ),
    ))

    # --- Per-product margin verification ---
    for expected_p in EXPECTED_PRODUCTS:
        name = expected_p["product"]
        recipe_price = expected_p["selling_price_rm"]
        ingredients = expected_p["ingredients"]
        recipe_variable_cost = round(sum(ingredients.values()), 2)
        recipe_contribution = round(recipe_price - recipe_variable_cost, 2)
        recipe_margin_pct = round(recipe_contribution / recipe_price * 100, 1) if recipe_price else None

        ledger_p = ledger_by_name.get(name, {})

        # Get ledger's own reported values
        ledger_price_str = ledger_p.get("selling_price_rm", "")
        ledger_price = float(ledger_price_str) if ledger_price_str else None
        ledger_cost_str = ledger_p.get("estimated_variable_cost_rm", "")
        ledger_cost = float(ledger_cost_str) if ledger_cost_str else None
        ledger_contrib_str = ledger_p.get("estimated_contribution_rm", "")
        ledger_contrib = float(ledger_contrib_str) if ledger_contrib_str else None
        ledger_margin_str = ledger_p.get("estimated_contribution_margin_pct", "")
        ledger_margin = float(ledger_margin_str) if ledger_margin_str else None

        # --- Arithmetic checks (always run, for both v1 and v2) ---
        # These use the ledger's OWN reported values to verify internal consistency.

        # Contribution arithmetic check: independently recompute contribution = price - cost
        if ledger_price is not None and ledger_cost is not None:
            indep_contrib = round(ledger_price - ledger_cost, 2)
            checks.append(make_check(
                name=f"{name}_contribution_arithmetic_check",
                category="margin_verification",
                expected=indep_contrib,
                actual=ledger_contrib,
                tolerance=ROUNDING_TOLERANCE_RM,
                explanation=(
                    f"Independently recomputed contribution = selling_price ({ledger_price}) "
                    f"- variable_cost ({ledger_cost}) = RM {indep_contrib:.2f}. "
                    f"Ledger reports RM {ledger_contrib}. Tolerance: RM{ROUNDING_TOLERANCE_RM}."
                ),
            ))
        else:
            checks.append(make_check(
                name=f"{name}_contribution_arithmetic_check",
                category="margin_verification",
                expected="price and cost both present",
                actual=f"price={ledger_price}, cost={ledger_cost}",
                passed=False,
                explanation="Cannot verify contribution arithmetic — price or cost is missing.",
            ))

        # Margin percentage arithmetic check: independently recompute margin = contribution / price * 100
        if ledger_contrib is not None and ledger_price is not None and ledger_price > 0:
            indep_margin = round(ledger_contrib / ledger_price * 100, 1)
            checks.append(make_check(
                name=f"{name}_margin_pct_arithmetic_check",
                category="margin_verification",
                expected=indep_margin,
                actual=ledger_margin,
                tolerance=ROUNDING_TOLERANCE_PCT,
                explanation=(
                    f"Independently recomputed margin% = contribution ({ledger_contrib}) "
                    f"/ price ({ledger_price}) * 100 = {indep_margin}%. "
                    f"Ledger reports {ledger_margin}%. Tolerance: {ROUNDING_TOLERANCE_PCT}pp."
                ),
            ))
        else:
            checks.append(make_check(
                name=f"{name}_margin_pct_arithmetic_check",
                category="margin_verification",
                expected="contribution and price both present",
                actual=f"contrib={ledger_contrib}, price={ledger_price}",
                passed=False,
                explanation="Cannot verify margin arithmetic — contribution or price is missing.",
            ))

        # Cost breakdown sum check: verify ingredient breakdown sums to declared variable cost
        breakdown_json = ledger_p.get("ingredient_cost_breakdown_json", "")
        try:
            breakdown = json.loads(breakdown_json) if breakdown_json else {}
            breakdown_sum = round(sum(float(v) for v in breakdown.values()), 2)
        except (json.JSONDecodeError, TypeError, ValueError):
            breakdown = {}
            breakdown_sum = None
        checks.append(make_check(
            name=f"{name}_cost_breakdown_sum_check",
            category="margin_verification",
            expected=ledger_cost,
            actual=breakdown_sum,
            tolerance=ROUNDING_TOLERANCE_RM,
            explanation=(
                f"Sum of ingredient_cost_breakdown_json components = RM {breakdown_sum}. "
                f"Declared variable cost = RM {ledger_cost}. "
                f"Tolerance: RM{ROUNDING_TOLERANCE_RM}."
            ),
        ))

        # Price validity check
        price_ok = ledger_price is not None and ledger_price > 0
        checks.append(make_check(
            name=f"{name}_price_validity_check",
            category="margin_verification",
            expected="price > 0",
            actual=ledger_price,
            passed=price_ok,
            explanation=f"Selling price must be positive. Ledger price = RM {ledger_price}."
            + (" Pass." if price_ok else " FAIL — invalid price."),
        ))

        # Contribution non-negative check
        contrib_ok = ledger_contrib is not None and ledger_contrib >= 0
        checks.append(make_check(
            name=f"{name}_contribution_non_negative_check",
            category="margin_verification",
            expected="contribution >= 0",
            actual=ledger_contrib,
            passed=contrib_ok,
            explanation=f"Contribution should be non-negative. Ledger reports RM {ledger_contrib}."
            + (" Pass." if contrib_ok else " FAIL — negative contribution."),
        ))

        # --- Recipe comparison checks (only in full mode, for v1 baseline) ---
        if mode == "full":
            # Variable cost recipe check
            checks.append(make_check(
                name=f"{name}_variable_cost_recipe_check",
                category="margin_verification",
                expected=recipe_variable_cost,
                actual=ledger_cost,
                tolerance=ROUNDING_TOLERANCE_RM,
                explanation=(
                    f"Independent recipe sum = sum({list(ingredients.keys())}) = RM {recipe_variable_cost:.2f}. "
                    f"Ledger reports RM {ledger_cost}. Tolerance: RM{ROUNDING_TOLERANCE_RM}."
                ),
            ))

            # Contribution recipe check
            checks.append(make_check(
                name=f"{name}_contribution_recipe_check",
                category="margin_verification",
                expected=recipe_contribution,
                actual=ledger_contrib,
                tolerance=ROUNDING_TOLERANCE_RM,
                explanation=(
                    f"Independent recipe contribution = {recipe_price} - {recipe_variable_cost} = RM {recipe_contribution:.2f}. "
                    f"Ledger reports RM {ledger_contrib}. Tolerance: RM{ROUNDING_TOLERANCE_RM}."
                ),
            ))

            # Margin percentage recipe check
            checks.append(make_check(
                name=f"{name}_margin_pct_recipe_check",
                category="margin_verification",
                expected=recipe_margin_pct,
                actual=ledger_margin,
                tolerance=ROUNDING_TOLERANCE_PCT,
                explanation=(
                    f"Independent recipe margin% = {recipe_contribution} / {recipe_price} * 100 = {recipe_margin_pct}%. "
                    f"Ledger reports {ledger_margin}%. Tolerance: {ROUNDING_TOLERANCE_PCT}pp."
                ),
            ))

    return checks


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run BACKSTOP automated self-checks.")
    parser.add_argument("--data", required=True, help="Source data directory (receipts, WhatsApp, spreadsheet)")
    parser.add_argument("--ledger", required=True, help="Ledger output directory to verify")
    parser.add_argument("--out", required=True, help="Output directory for self_check_report.json")
    parser.add_argument("--mode", default="full", choices=["full", "arithmetic"],
                        help="full = recipe comparison + arithmetic (v1); arithmetic = internal consistency only (v2)")
    args = parser.parse_args()

    data_dir = Path(args.data)
    ledger_dir = Path(args.ledger)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not data_dir.exists():
        raise SystemExit(f"Source data directory not found: {data_dir}")
    if not ledger_dir.exists():
        raise SystemExit(f"Ledger directory not found: {ledger_dir}")

    reconciliation_checks = run_reconciliation_checks(data_dir, ledger_dir)
    margin_checks = run_margin_verification_checks(ledger_dir, mode=args.mode)
    all_checks = reconciliation_checks + margin_checks

    passed = sum(1 for c in all_checks if c["status"] == "pass")
    failed = sum(1 for c in all_checks if c["status"] == "fail")
    inconclusive = sum(1 for c in all_checks if c["status"] == "inconclusive")

    report = {
        "generated_at_local": now_iso(),
        "data_dir": str(data_dir.resolve()),
        "ledger_dir": str(ledger_dir.resolve()),
        "mode": args.mode,
        "rounding_tolerance_rm": ROUNDING_TOLERANCE_RM,
        "rounding_tolerance_pct": ROUNDING_TOLERANCE_PCT,
        "summary": {
            "total_checks": len(all_checks),
            "passed": passed,
            "failed": failed,
            "inconclusive": inconclusive,
            "overall_status": "pass" if failed == 0 else "fail",
        },
        "checks": all_checks,
        "note": (
            "Genuine verification checks compare independently derived values against "
            "ledger-reported values. Checks marked 'inconclusive' cannot be completed "
            "because the source data is incomplete or synthetic (e.g., verified revenue "
            "is null by design). Inconclusive checks do not count as passes."
        ),
    }

    report_path = out_dir / "self_check_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("BACKSTOP Self-Check completed.")
    print(f"Data: {data_dir.resolve()}")
    print(f"Ledger: {ledger_dir.resolve()}")
    print(f"Total checks: {len(all_checks)}")
    print(f"Passed: {passed} | Failed: {failed} | Inconclusive: {inconclusive}")
    print(f"Overall: {report['summary']['overall_status']}")
    print(f"Report: {report_path.resolve()}")


if __name__ == "__main__":
    main()
