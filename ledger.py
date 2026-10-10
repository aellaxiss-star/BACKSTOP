#!/usr/bin/env python3
"""
BACKSTOP Ledger: deterministic ingestion, reconciliation, unit economics, and traceability.

Usage:
  python ledger.py
  python ledger.py --data data/demo_nasi_lemak --out outputs/ledger

Dependency:
  python -m pip install openpyxl
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from openpyxl import load_workbook, Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError as exc:
    raise SystemExit("Missing dependency 'openpyxl'. Run: python -m pip install openpyxl") from exc


def sha_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:14].upper()


def parse_money(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).strip().replace(",", "")
    match = re.search(r"-?\d+(?:\.\d{1,2})?", text)
    return round(float(match.group()), 2) if match else None


def parse_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d %b %Y", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_receipts(data_dir: Path) -> tuple[list[dict], list[dict]]:
    transactions, exceptions = [], []
    receipt_dir = data_dir / "receipts_text"
    for file in sorted(receipt_dir.glob("*.txt")):
        raw = file.read_text(encoding="utf-8", errors="replace")
        rel = file.relative_to(data_dir.parent).as_posix()
        # Capture every currency-like amount, preserving source line references.
        amounts = []
        dates = []
        supplier = None
        for line_no, line in enumerate(raw.splitlines(), 1):
            if re.search(r"\b(total|grand total|amount)\b", line, re.I):
                value = parse_money(line)
                if value is not None:
                    amounts.append({"line": line_no, "value": value, "text": line.strip()})
            parsed = None
            for candidate in re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\b", line):
                parsed = parse_date(candidate)
                if parsed:
                    dates.append({"line": line_no, "value": parsed})
            if supplier is None and line.strip() and not re.search(r"receipt|date|item|total|paid|thank|ref:|category:", line, re.I):
                supplier = line.strip()
        unique_amounts = {x["value"] for x in amounts}
        status = "needs_review" if len(unique_amounts) != 1 or not amounts or not dates else "parsed_unverified"
        chosen_amount = amounts[0]["value"] if amounts else None
        if len(unique_amounts) > 1:
            exceptions.append({
                "exception_id": f"EXC-{len(exceptions)+1:04d}",
                "severity": "high",
                "type": "conflicting_amounts",
                "source_file": str(file),
                "source_rows": ";".join(str(x["line"]) for x in amounts),
                "details": f"Different amount candidates found: {sorted(unique_amounts)}; no value selected as verified.",
                "status": "open",
            })
            chosen_amount = None
        if not dates:
            exceptions.append({
                "exception_id": f"EXC-{len(exceptions)+1:04d}",
                "severity": "medium",
                "type": "missing_or_ambiguous_date",
                "source_file": str(file),
                "source_rows": "n/a",
                "details": "No unambiguous date parsed from receipt.",
                "status": "open",
            })
        transactions.append({
            "transaction_id": "REC-" + sha_id(file.name, raw),
            "source_type": "receipt",
            "source_file": str(file),
            "source_row": f"lines:{','.join(str(x['line']) for x in amounts) or 'none'}",
            "date": dates[0]["value"] if dates else None,
            "description": supplier or file.name,
            "amount_rm": chosen_amount,
            "currency": "MYR",
            "status": status,
            "confidence": "low" if status == "needs_review" else "medium",
            "raw_excerpt": " | ".join(raw.splitlines()[:4])[:240],
        })
    return transactions, exceptions


def read_whatsapp(data_dir: Path) -> tuple[list[dict], list[dict]]:
    path = data_dir / "whatsapp_orders.txt"
    if not path.exists():
        return [], []
    transactions, exceptions = [], []
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.startswith("["):
            continue
        match = re.match(r"\[(\d{2}/\d{2}/\d{4}),\s+(\d{2}:\d{2}\s+[AP]M)\]\s+(.*)", line)
        if not match:
            continue
        date_s = parse_date(match.group(1))
        message = match.group(3).strip()
        low = message.lower()
        if "cancel" in low or "correction" in low or "correction," in low:
            status = "excluded_or_reconcile"
        elif "reserve" in low or "pay later" in low or "enquiry" in low:
            status = "unconfirmed"
        elif "order" in low:
            status = "candidate_order_not_proof_of_payment"
        else:
            status = "needs_review"
        money_matches = re.findall(r"RM\s*(\d+(?:\.\d{1,2})?)", message, re.I)
        amount = float(money_matches[-1]) if money_matches else None
        if status in ("unconfirmed", "excluded_or_reconcile") or amount is None:
            exceptions.append({
                "exception_id": f"EXC-{len(exceptions)+1:04d}",
                "severity": "medium",
                "type": "chat_order_status_or_amount_unclear",
                "source_file": str(path),
                "source_rows": str(line_no),
                "details": f"Chat message is not automatically counted as realized revenue ({status}): {message[:180]}",
                "status": "open",
            })
        transactions.append({
            "transaction_id": "CHAT-" + sha_id(str(line_no), line),
            "source_type": "whatsapp",
            "source_file": str(path),
            "source_row": str(line_no),
            "date": date_s,
            "description": message,
            "amount_rm": amount,
            "currency": "MYR",
            "status": status,
            "confidence": "low",
            "raw_excerpt": message[:240],
        })
    return transactions, exceptions


def read_workbook(data_dir: Path) -> tuple[list[dict], list[dict], list[dict]]:
    path = data_dir / "broken_sales_and_costs.xlsx"
    if not path.exists():
        return [], [], []
    wb = load_workbook(path, data_only=False)
    transactions, exceptions, costs = [], [], []
    ws = wb["Daily Sales"]
    seen = Counter()
    for row_no in range(2, ws.max_row + 1):
        vals = [ws.cell(row_no, col).value for col in range(1, 7)]
        if all(v is None for v in vals):
            continue
        date_s = parse_date(vals[0])
        product = str(vals[1]).strip() if vals[1] is not None else ""
        qty = parse_money(vals[2])
        unit = parse_money(vals[3])
        total = parse_money(vals[4])
        note = str(vals[5] or "")
        issues = []
        if not date_s: issues.append("date_unparsed")
        if qty is None: issues.append("quantity_unparsed")
        if unit is None: issues.append("unit_price_unparsed")
        if isinstance(vals[4], str) and vals[4].startswith("="): issues.append("formula_unverified")
        if total is None: issues.append("total_missing_or_unparseable")
        if qty is not None and unit is not None and total is not None and abs(qty * unit - total) > 0.02:
            issues.append("line_total_mismatch")
        fingerprint = (date_s, product, qty, unit, total)
        seen[fingerprint] += 1
        if seen[fingerprint] > 1 or "duplicate" in note.lower():
            issues.append("possible_duplicate")
        status = "needs_review" if issues else "parsed_unverified"
        if issues:
            exceptions.append({
                "exception_id": f"EXC-{len(exceptions)+1:04d}",
                "severity": "high" if "formula_unverified" in issues or "possible_duplicate" in issues else "medium",
                "type": ";".join(issues),
                "source_file": str(path),
                "source_rows": f"Daily Sales!A{row_no}:F{row_no}",
                "details": f"Row not treated as verified: {', '.join(issues)}",
                "status": "open",
            })
        transactions.append({
            "transaction_id": "XLS-" + sha_id(str(row_no), repr(vals)),
            "source_type": "spreadsheet_sale",
            "source_file": str(path),
            "source_row": f"Daily Sales!A{row_no}:F{row_no}",
            "date": date_s,
            "description": product,
            "quantity": qty,
            "unit_price_rm": unit,
            "amount_rm": total if not (isinstance(vals[4], str) and vals[4].startswith("=")) else None,
            "currency": "MYR",
            "status": status,
            "confidence": "low" if issues else "medium",
            "raw_excerpt": " | ".join("" if v is None else str(v) for v in vals)[:240],
        })
    if "Costs" in wb.sheetnames:
        cws = wb["Costs"]
        for row_no in range(2, cws.max_row + 1):
            item, amount, unit, updated = [cws.cell(row_no, col).value for col in range(1, 5)]
            parsed = parse_money(amount)
            costs.append({
                "cost_id": "COST-" + sha_id(str(row_no), repr([item, amount, unit, updated])),
                "item": str(item or "").strip(),
                "amount_rm": parsed,
                "unit": str(unit or "").strip(),
                "updated_note": str(updated or "").strip(),
                "source_file": str(path),
                "source_row": f"Costs!A{row_no}:D{row_no}",
                "status": "needs_review" if parsed is None or "old" in str(updated).lower() or "?" in str(updated) else "unverified_estimate",
            })
    return transactions, exceptions, costs


def build_product_economics() -> list[dict]:
    # These are explicit synthetic recipe assumptions, not measured costs.
    recipe = {
        "Nasi Lemak Biasa": {"price": 3.50, "ingredients": {"rice": 0.42, "sambal": 0.28, "anchovy_peanut_cucumber": 0.35, "packaging": 0.18}},
        "Nasi Lemak Telur": {"price": 4.50, "ingredients": {"rice": 0.42, "sambal": 0.28, "anchovy_peanut_cucumber": 0.35, "egg": 0.55, "packaging": 0.18}},
        "Nasi Lemak Ayam": {"price": 7.50, "ingredients": {"rice": 0.48, "sambal": 0.32, "anchovy_peanut_cucumber": 0.35, "egg": 0.55, "chicken": 1.75, "packaging": 0.22}},
        "Teh Ais": {"price": 2.00, "ingredients": {"tea_sugar_milk_ice": 0.48, "cup": 0.16}},
    }
    out = []
    for name, item in recipe.items():
        variable = round(sum(item["ingredients"].values()), 2)
        contribution = round(item["price"] - variable, 2)
        out.append({
            "product": name,
            "selling_price_rm": item["price"],
            "estimated_variable_cost_rm": variable,
            "estimated_contribution_rm": contribution,
            "estimated_contribution_margin_pct": round(contribution / item["price"] * 100, 1) if item["price"] else None,
            "cost_basis": "synthetic recipe assumptions; owner verification required",
            "ingredient_cost_breakdown_json": json.dumps(item["ingredients"], ensure_ascii=False),
            "source_id": "ASSUMPTION-RECIPE-001",
        })
    return out


def build_break_even(product_economics: list[dict], costs: list[dict]) -> dict:
    # Monthly rent is the only confidently interpretable fixed-cost input in the sample.
    rent = next((c["amount_rm"] for c in costs if c["item"].lower() == "rent" and c["amount_rm"] is not None), 450.0)
    helper = next((c["amount_rm"] for c in costs if "helper" in c["item"].lower() and c["amount_rm"] is not None), None)
    electricity = next((c["amount_rm"] for c in costs if "electric" in c["item"].lower() and c["amount_rm"] is not None), None)
    fixed_base = rent
    # Explicitly do not mix daily allowance into monthly costs without days worked.
    # Weighted mix is unavailable, so show per-product break-even rather than pretending one mix.
    rows = []
    for p in product_economics:
        contrib = p["estimated_contribution_rm"]
        rows.append({
            "product": p["product"],
            "fixed_cost_base_rm_month": fixed_base,
            "contribution_per_unit_rm": contrib,
            "break_even_units_month_rent_only": round(fixed_base / contrib, 1) if contrib > 0 else None,
            "scope_note": "rent-only floor; excludes helper, electricity, gas, owner pay, wastage, platform fees and unknown costs",
        })
    return {
        "fixed_cost_inputs": {
            "rent_rm_month": rent,
            "helper_allowance_rm_day": helper,
            "electricity_rm_month": electricity,
            "fixed_cost_total_rm_month": None,
            "note": "A complete fixed-cost total is not asserted because helper days, gas, and other costs are unresolved.",
        },
        "per_product": rows,
    }


def make_workbook(out_path: Path, transactions: list[dict], products: list[dict], costs: list[dict], exceptions: list[dict]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Normalized Ledger"
    columns = ["transaction_id", "source_type", "source_file", "source_row", "date", "description", "quantity", "unit_price_rm", "amount_rm", "currency", "status", "confidence", "raw_excerpt"]
    ws.append(columns)
    for row in transactions:
        ws.append([row.get(k) for k in columns])
    prod = wb.create_sheet("Product Margins")
    pcols = list(products[0].keys()) if products else []
    prod.append(pcols)
    for row in products:
        prod.append([row.get(k) for k in pcols])
    cws = wb.create_sheet("Cost Inputs")
    ccols = list(costs[0].keys()) if costs else ["cost_id", "item", "amount_rm", "unit", "updated_note", "source_file", "source_row", "status"]
    cws.append(ccols)
    for row in costs:
        cws.append([row.get(k) for k in ccols])
    ex = wb.create_sheet("Exceptions")
    ecols = ["exception_id", "severity", "type", "source_file", "source_rows", "details", "status"]
    ex.append(ecols)
    for row in exceptions:
        ex.append([row.get(k) for k in ecols])
    for sheet in wb.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="243447")
        for col in sheet.columns:
            letter = get_column_letter(col[0].column)
            max_len = max((len(str(c.value)) if c.value is not None else 0 for c in col[:100]), default=10)
            sheet.column_dimensions[letter].width = min(max(max_len + 2, 12), 48)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/demo_nasi_lemak", help="Input dataset directory")
    parser.add_argument("--out", default="outputs/ledger", help="Output directory")
    args = parser.parse_args()
    data_dir, out_dir = Path(args.data), Path(args.out)
    if not data_dir.exists():
        raise SystemExit(f"Dataset not found: {data_dir.resolve()}. Run generate_dataset.py first.")
    out_dir.mkdir(parents=True, exist_ok=True)

    receipt_rows, receipt_ex = read_receipts(data_dir)
    chat_rows, chat_ex = read_whatsapp(data_dir)
    sheet_rows, sheet_ex, costs = read_workbook(data_dir)
    transactions = receipt_rows + chat_rows + sheet_rows
    exceptions = receipt_ex + chat_ex + sheet_ex
    # IDs for exceptions are renumbered to avoid duplicate IDs across parsers.
    for idx, item in enumerate(exceptions, 1):
        item["exception_id"] = f"EXC-{idx:04d}"
    products = build_product_economics()
    break_even = build_break_even(products, costs)

    write_csv(out_dir / "normalized_ledger.csv", transactions)
    write_csv(out_dir / "source_trace.csv", [
        {
            "transaction_id": r["transaction_id"],
            "source_type": r["source_type"],
            "source_file": r["source_file"],
            "source_row": r["source_row"],
            "date": r["date"],
            "amount_rm": r["amount_rm"],
            "status": r["status"],
            "raw_excerpt": r["raw_excerpt"],
        } for r in transactions
    ])
    write_csv(out_dir / "exceptions.csv", exceptions, ["exception_id", "severity", "type", "source_file", "source_rows", "details", "status"])
    write_csv(out_dir / "product_margins.csv", products)
    write_csv(out_dir / "cost_inputs.csv", costs)
    (out_dir / "break_even.json").write_text(json.dumps(break_even, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = {
        "business": "Fictional nasi lemak stall",
        "currency": "MYR",
        "generated_at_local": datetime.now().isoformat(timespec="seconds"),
        "input_dir": str(data_dir),
        "transaction_records": len(transactions),
        "receipt_records": len(receipt_rows),
        "whatsapp_records": len(chat_rows),
        "spreadsheet_records": len(sheet_rows),
        "open_exceptions": len(exceptions),
        "verified_revenue_rm": None,
        "note": "Verified revenue is intentionally null: raw inputs are synthetic and not fully reconciled. Do not sum candidate orders and unverified rows as confirmed revenue.",
        "product_margins_basis": "explicit synthetic recipe assumptions; not owner-verified costs",
        "files": ["normalized_ledger.csv", "source_trace.csv", "exceptions.csv", "product_margins.csv", "cost_inputs.csv", "break_even.json", "books.xlsx"],
    }
    (out_dir / "ledger_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    make_workbook(out_dir / "books.xlsx", transactions, products, costs, exceptions)
    print("BACKSTOP Ledger completed.")
    print(f"Input: {data_dir.resolve()}")
    print(f"Output: {out_dir.resolve()}")
    print(f"Source records: {len(transactions)} (receipts={len(receipt_rows)}, WhatsApp={len(chat_rows)}, spreadsheet={len(sheet_rows)})")
    print(f"Open exceptions: {len(exceptions)}")
    print("Verified revenue: NOT ASSERTED (inputs need reconciliation)")
    print("Created books.xlsx plus CSV/JSON audit outputs.")


if __name__ == "__main__":
    main()
