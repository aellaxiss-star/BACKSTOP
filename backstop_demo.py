from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
PLAN_PATH = OUTPUTS / "rescue_plan.json"
SENTINEL_PATH = OUTPUTS / "sentinel_report.json"

def money(value):
    if value is None:
        return "UNKNOWN"
    return f"RM {value:,.2f}"

def line():
    print("-" * 68)

def read_json(path):
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)

def main():
    # Improve Windows terminal handling of Unicode.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not PLAN_PATH.is_file():
        print(f"ERROR: Missing {PLAN_PATH}")
        return 1

    try:
        plan = read_json(PLAN_PATH)
        sentinel = read_json(SENTINEL_PATH) if SENTINEL_PATH.is_file() else None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: Could not read report: {exc}")
        return 1

    baseline = plan.get("business_baseline", {})
    costs = baseline.get("fixed_costs", {})
    products = baseline.get("products", [])
    findings = plan.get("key_findings", [])
    warnings = plan.get("financial_warnings", [])
    risks = plan.get("supply_risks", [])
    options = plan.get("rescue_options", plan.get("options", []))

    print()
    print("=" * 68)
    print("  BACKSTOP | BUSINESS RESCUE BRIEFING")
    print("=" * 68)
    print(f"  Business : {baseline.get('business_name', 'Unknown')}")
    print(f"  Period   : {baseline.get('data_period', 'Unknown')}")
    print(f"  Currency : {baseline.get('currency', 'Unknown')}")
    print()
    print("  01 | FINANCIAL POSITION")
    line()
    print(f"  Verified revenue       : {money(baseline.get('verified_revenue_rm'))}")
    print(f"  Transaction records    : {baseline.get('transaction_records', 'Unknown')}")
    print(f"  Open data exceptions   : {baseline.get('open_exceptions', 'Unknown')}")
    print(f"  Cash balance           : {money(baseline.get('cash_balance'))}")
    print(f"  Outstanding debts      : {money(baseline.get('outstanding_debts'))}")
    print(f"  Monthly rent           : {money(costs.get('rent_rm_month'))}")
    print(f"  Monthly electricity    : {money(costs.get('electricity_rm_month'))}")
    print(f"  Monthly gas cost       : {money(costs.get('gas_rm_month'))}")
    print(f"  Total monthly costs    : {money(costs.get('fixed_cost_total_rm_month'))}")
    print()
    print("  IMPORTANT: Unknown values are not zero.")
    print("  Revenue and total monthly costs have not been verified.")
    print()
    print("  02 | PRODUCT CONTRIBUTION ESTIMATES")
    line()
    print(f"  {'Product':<25} {'Price':>12} {'Contribution':>15} {'Margin':>10}")
    line()
    for product in products:
        name = str(product.get("name", "Unknown"))[:25]
        price = money(product.get("price_rm"))
        contribution = money(product.get("estimated_contribution_rm"))
        margin = product.get("margin_pct")
        margin_text = f"{margin:.1f}%" if isinstance(margin, (int, float)) else "UNKNOWN"
        print(f"  {name:<25} {price:>12} {contribution:>15} {margin_text:>10}")
    print()
    print("  These are estimates based on synthetic recipe assumptions,")
    print("  not verified profit figures.")
    print()
    print("  03 | KEY FINDINGS")
    line()
    for index, finding in enumerate(findings[:8], start=1):
        if isinstance(finding, dict):
            text = finding.get("finding", str(finding))
            classification = finding.get("classification", "Unclassified")
        else:
            text = str(finding)
            classification = "Unclassified"
        print(f"  {index}. {text}")
        print(f"     Classification: {classification}")
    print()
    print("  04 | FINANCIAL WARNINGS")
    line()
    for index, warning in enumerate(warnings[:8], start=1):
        print(f"  {index}. {warning}")
    print()
    print("  05 | SUPPLY RISKS")
    line()
    if risks:
        for index, risk in enumerate(risks[:6], start=1):
            if isinstance(risk, dict):
                description = risk.get("risk", risk.get("finding", str(risk)))
            else:
                description = str(risk)
            print(f"  {index}. {description}")
    else:
        print("  No structured supply-risk list found in the rescue plan.")
        print("  Consult outputs/supply_report.json for the full assessment.")
    print()
    print("  06 | RECOMMENDATION")
    line()
    print("  Establish a 14-day verified financial baseline before making")
    print("  major pricing, borrowing, or menu-discontinuation decisions.")
    print("  Reconcile disputed transactions and verify current supplier costs.")
    print()
    print("  07 | VALIDATION")
    line()
    if sentinel is not None:
        print("  Sentinel report exists and was parsed.")
        print("  This display does not independently rerun Sentinel validation.")
        print("  Inspect outputs/sentinel_report.json for its actual findings.")
    else:
        print("  Sentinel report missing. Validation status is unavailable.")
    print()
    print("  DATA NOTICE")
    print("  This is synthetic demonstration data, not a verified real business.")
    print("  This briefing reads saved outputs; it does not execute the engines.")
    print("=" * 68)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
