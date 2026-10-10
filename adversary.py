#!/usr/bin/env python3
"""
BACKSTOP Adversary: deterministic stress tests and bounded revision-loop controller.

This script scores risks using explicit assumptions, records evidence and unknowns,
and emits structured revision requests. It does NOT claim qualitative shocks have
precise financial impacts unless the input data supports those calculations.

Usage:
  python adversary.py
  python adversary.py --ledger outputs/ledger --out outputs/adversary
  python adversary.py --threshold 60 --max-revisions 2

Dependency: standard library only.
"""
from __future__ import annotations

import argparse
import json
import csv
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_SHOCKS = [
    {
        "id": "SHOCK-INGREDIENTS-25",
        "name": "Ingredient costs rise 25%",
        "category": "financial",
        "owner": "Ledger",
        "likelihood": 3,
        "impact": 5,
        "detectability": 3,
        "question": "Does the plan remain viable if ingredient costs rise by 25%?",
        "required_response": "Recalculate variable cost, contribution margin, break-even and recommended price/promo changes.",
    },
    {
        "id": "SHOCK-SUPPLIER-LOSS",
        "name": "Primary supplier unavailable",
        "category": "operations",
        "owner": "Supply",
        "likelihood": 3,
        "impact": 4,
        "detectability": 3,
        "question": "Can the stall source equivalent ingredients quickly without compromising food safety or margin?",
        "required_response": "Identify backup suppliers, substitution checks, lead times and an owner action.",
    },
    {
        "id": "SHOCK-RENT-RISE",
        "name": "Monthly rent rises 20%",
        "category": "financial",
        "owner": "Ledger",
        "likelihood": 2,
        "impact": 4,
        "detectability": 2,
        "question": "How does a 20% rent increase affect the fixed-cost floor and break-even?",
        "required_response": "Recalculate rent-only and known-fixed-cost scenarios; state which costs remain unknown.",
    },
    {
        "id": "SHOCK-VIRAL-REVIEW",
        "name": "Damaging viral review",
        "category": "reputation",
        "owner": "Spark",
        "likelihood": 2,
        "impact": 5,
        "detectability": 4,
        "question": "Can the business respond quickly and responsibly to a widely shared negative review?",
        "required_response": "Draft a calm response, service-recovery steps and a 48-hour communications plan; do not fabricate facts.",
    },
    {
        "id": "SHOCK-DEMAND-DROP-30",
        "name": "Demand falls 30%",
        "category": "financial",
        "owner": "Ledger",
        "likelihood": 3,
        "impact": 5,
        "detectability": 3,
        "question": "Does contribution cover the known fixed-cost floor if unit sales fall 30%?",
        "required_response": "Calculate a scenario only where a valid baseline volume/mix exists; otherwise identify the missing inputs and provide the formula.",
    },
]


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def risk_score(likelihood: int, impact: int, detectability: int) -> int:
    # Transparent 1–5 scales. Higher detectability number means harder to detect.
    return likelihood * impact * detectability


def assess_shocks(ledger_dir: Path) -> list[dict[str, Any]]:
    summary = read_json(ledger_dir / "ledger_summary.json")
    break_even = read_json(ledger_dir / "break_even.json")
    products = []
    product_path = ledger_dir / "product_margins.csv"
    if product_path.exists():
        import csv
        with product_path.open(encoding="utf-8-sig", newline="") as f:
            products = list(csv.DictReader(f))

    results = []
    for shock in DEFAULT_SHOCKS:
        item = dict(shock)
        item["score"] = risk_score(item["likelihood"], item["impact"], item["detectability"])
        item["score_scale"] = "likelihood(1-5) × impact(1-5) × detectability(1-5), range 1-125"
        item["financial_impact_rm"] = None
        item["evidence"] = []
        item["unknowns"] = []
        item["recommended_mitigation"] = []

        if item["id"] == "SHOCK-INGREDIENTS-25":
            if products:
                item["evidence"].append("Product margin inputs found in product_margins.csv.")
                item["unknowns"].append("Recipe costs are synthetic assumptions and require owner verification.")
                item["recommended_mitigation"] = [
                    "Verify current ingredient quantities and supplier prices before relying on the scenario.",
                    "Prioritize low-contribution products and review promotions before changing prices.",
                    "Request at least two supplier quotes for high-cost ingredients.",
                ]
                scenario_rows = []
                packaging_terms = ("packaging", "package", "cup", "container", "wrapper", "bag", "napkin", "cutlery", "box")
                for row in products:
                    try:
                        price = float(row.get("selling_price_rm", ""))
                        declared_cost = float(row.get("estimated_variable_cost_rm", ""))
                        breakdown = json.loads(row.get("ingredient_cost_breakdown_json") or "{}")
                        if not isinstance(breakdown, dict) or not breakdown:
                            raise ValueError("missing itemised cost breakdown")
                        parsed = {str(k): float(v) for k, v in breakdown.items()}
                        if any(v < 0 for v in parsed.values()):
                            raise ValueError("negative cost component")
                        ingredient_cost = sum(v for k, v in parsed.items() if not any(term in k.lower() for term in packaging_terms))
                        packaging_cost = sum(v for k, v in parsed.items() if any(term in k.lower() for term in packaging_terms))
                        itemised_total = ingredient_cost + packaging_cost
                        if abs(itemised_total - declared_cost) > 0.02:
                            scenario_rows.append({"product": row.get("product", "Unknown product"), "status": "withheld_cost_breakdown_mismatch", "source_id": row.get("source_id"), "declared_variable_cost_rm": round(declared_cost, 2), "itemised_cost_rm": round(itemised_total, 2), "note": "Itemised components do not reconcile to declared variable cost; no stressed margin calculated."})
                            continue
                        stressed_ingredients = ingredient_cost * 1.25
                        stressed_total = stressed_ingredients + packaging_cost
                        baseline_contribution = price - declared_cost
                        stressed_contribution = price - stressed_total
                        scenario_rows.append({
                            "product": row.get("product", "Unknown product"),
                            "status": "calculated_from_itemised_assumption",
                            "source_file": "outputs/ledger/product_margins.csv",
                            "source_id": row.get("source_id"),
                            "cost_basis": row.get("cost_basis", "Not supplied"),
                            "selling_price_rm": round(price, 2),
                            "baseline_ingredient_cost_rm": round(ingredient_cost, 2),
                            "packaging_and_non_ingredient_cost_rm": round(packaging_cost, 2),
                            "baseline_variable_cost_rm": round(declared_cost, 2),
                            "stressed_ingredient_cost_rm": round(stressed_ingredients, 2),
                            "stressed_variable_cost_rm": round(stressed_total, 2),
                            "baseline_contribution_rm": round(baseline_contribution, 2),
                            "stressed_contribution_rm": round(stressed_contribution, 2),
                            "baseline_margin_pct": round(baseline_contribution / price * 100, 1) if price else None,
                            "stressed_margin_pct": round(stressed_contribution / price * 100, 1) if price else None,
                            "ingredient_cost_increase_per_unit_rm": round(stressed_total - declared_cost, 2),
                            "formula": "stressed variable cost = baseline ingredient cost × 1.25 + packaging/non-ingredient cost; contribution = selling price − stressed variable cost",
                        })
                    except (TypeError, ValueError, json.JSONDecodeError):
                        scenario_rows.append({"product": row.get("product", "Unknown product"), "status": "withheld_missing_or_invalid_cost_breakdown", "source_id": row.get("source_id"), "note": "No reliable itemised cost breakdown; financial impact withheld."})
                item["per_unit_scenario"] = scenario_rows
                item["financial_impact_rm"] = None
                item["financial_impact_note"] = "Per-unit scenarios only. Total business impact is withheld because confirmed units sold and product mix are unavailable."
                item["evidence"].append("Deterministic +25% ingredient-cost scenario written per product; packaging/non-ingredient components are held constant.")
                item["unknowns"].append("Total RM impact cannot be calculated without reconciled product-level sales volumes and mix.")
            else:
                item["unknowns"].append("No product margin data found; financial impact cannot be calculated.")
        elif item["id"] == "SHOCK-SUPPLIER-LOSS":
            item["unknowns"] = [
                "No verified supplier concentration, lead-time or stock-cover data is available.",
                "No supplier prices or food-safety equivalence data are verified.",
            ]
            item["recommended_mitigation"] = [
                "List two qualified backup suppliers for each critical ingredient.",
                "Record contact, minimum order, lead time, price and substitution constraints.",
                "Do not substitute ingredients until food safety and customer disclosure requirements are checked.",
            ]
        elif item["id"] == "SHOCK-RENT-RISE":
            rent = break_even.get("fixed_cost_inputs", {}).get("rent_rm_month")
            if isinstance(rent, (int, float)):
                item["evidence"].append(f"Current rent input: RM {rent:.2f}/month, sourced from demo cost sheet.")
                item["scenario"] = {
                    "baseline_rent_rm_month": round(rent, 2),
                    "rent_after_20pct_rise_rm_month": round(rent * 1.2, 2),
                    "increase_rm_month": round(rent * 0.2, 2),
                }
                item["recommended_mitigation"] = [
                    "Confirm lease terms and notice period.",
                    "Recalculate break-even with all fixed costs before accepting a new rent commitment.",
                ]
            else:
                item["unknowns"].append("Rent input unavailable; cannot calculate rent shock.")
        elif item["id"] == "SHOCK-VIRAL-REVIEW":
            item["unknowns"] = [
                "No verified review incident or sentiment data is supplied.",
                "Revenue loss from reputational damage cannot be responsibly quantified from this dataset.",
            ]
            item["recommended_mitigation"] = [
                "Acknowledge the concern without arguing or exposing customer information.",
                "Check the order and service facts internally before making claims.",
                "Offer an appropriate private resolution and track follow-up within 48 hours.",
            ]
        elif item["id"] == "SHOCK-DEMAND-DROP-30":
            item["unknowns"] = [
                "No reconciled baseline unit volume or product mix is available.",
                "Verified revenue is unset; do not infer a 30% loss from unconfirmed chat orders.",
            ]
            item["recommended_mitigation"] = [
                "Collect at least 14 days of confirmed daily units by product.",
                "Calculate stressed contribution as baseline contribution × 0.70, holding mix constant only if that assumption is justified.",
                "Review purchasing and prep quantities to reduce waste while preserving food safety.",
            ]
        if summary.get("verified_revenue_rm") is None:
            item["evidence"].append("Ledger explicitly does not assert verified revenue.")
        results.append(item)
    return results


def make_revision_requests(shocks: list[dict], threshold: int) -> list[dict]:
    requests = []
    for shock in shocks:
        if shock["score"] >= threshold:
            requests.append({
                "request_id": f"REVREQ-{len(requests)+1:03d}",
                "trigger_shock_id": shock["id"],
                "risk_score": shock["score"],
                "target_skill": shock["owner"],
                "priority": "high" if shock["score"] >= 80 else "medium",
                "instruction": shock["required_response"],
                "required_evidence": [
                    "List changed assumptions.",
                    "Cite source file and row for factual inputs.",
                    "Separate calculated results from estimates and unknowns.",
                    "State whether the revised plan addresses the triggering weakness.",
                ],
                "status": "pending_agent_revision",
            })
    return requests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", default="outputs/ledger", help="Ledger output directory")
    parser.add_argument("--out", default="outputs/adversary", help="Adversary output directory")
    parser.add_argument("--threshold", type=int, default=45, help="Scores >= threshold trigger revision requests")
    parser.add_argument("--max-revisions", type=int, default=2, help="Maximum full revision rounds")
    args = parser.parse_args()
    if not 1 <= args.threshold <= 125:
        raise SystemExit("--threshold must be between 1 and 125")
    if not 0 <= args.max_revisions <= 2:
        raise SystemExit("--max-revisions must be between 0 and 2")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    shocks = assess_shocks(Path(args.ledger))
    requests = make_revision_requests(shocks, args.threshold)
    report = {
        "generated_at_local": datetime.now().isoformat(timespec="seconds"),
        "method": "Transparent risk score = likelihood × impact × difficulty of detection; each factor 1–5.",
        "threshold": args.threshold,
        "max_revision_rounds": args.max_revisions,
        "revision_rounds_completed": 0,
        "revision_status": "pending_agent_revision" if requests and args.max_revisions else "no_revision_requested",
        "warning": "This script generates stress tests and revision requests. It does not execute WorkBuddy agents or claim revisions occurred.",
        "shocks": shocks,
        "revision_requests": requests,
        "revision_log": [],
    }
    (out_dir / "adversary_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    import csv
    with (out_dir / "attack_log.csv").open("w", newline="", encoding="utf-8-sig") as f:
        fields = ["id", "name", "category", "owner", "score", "threshold", "triggers_revision", "financial_impact_rm", "evidence", "unknowns", "recommended_mitigation"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for s in shocks:
            writer.writerow({
                "id": s["id"], "name": s["name"], "category": s["category"], "owner": s["owner"],
                "score": s["score"], "threshold": args.threshold, "triggers_revision": s["score"] >= args.threshold,
                "financial_impact_rm": s.get("financial_impact_rm"),
                "evidence": " | ".join(s["evidence"]), "unknowns": " | ".join(s["unknowns"]),
                "recommended_mitigation": " | ".join(s["recommended_mitigation"]),
            })
    print("BACKSTOP Adversary completed.")
    print(f"Stress tests: {len(shocks)}")
    print(f"Revision requests generated: {len(requests)}")
    print(f"Threshold: {args.threshold}/125; maximum revision rounds: {args.max_revisions}")
    print(f"Output: {out_dir.resolve()}")
    print("Note: requests are queued for WorkBuddy; no agent revision is claimed by this script.")


if __name__ == "__main__":
    main()
