BACKSTOP OWNER-FIRST WORKFLOW REDESIGN
======================================

Files in this patch
- backstop_case.py

Expected project layout:
BACKSTOP/
  backstop_case.py
  ledger.py
  adversary.py
  data/
    demo_nasi_lemak/
  outputs/

Run from PowerShell:
  cd "$HOME\Desktop\BACKSTOP"
  python backstop_case.py
Then open:
  http://127.0.0.1:8765

Workflow implemented:
Business owner intake -> CONDUCTOR -> SCOUT -> LEDGER + SUPPLY -> SPARK ->
ADVERSARY -> bounded local revision loop (maximum 2 rounds) -> SENTINEL ->
CONDUCTOR consolidation -> owner review -> final report.

Important execution-integrity note:
This version runs ledger.py and adversary.py as actual local subprocesses, performs
SCOUT/SUPPLY/SPARK/SENTINEL and coordination using local rule-based workflow code,
and automatically resolves adversary revision requests with a bounded local
revision controller. It does NOT call Tencent WorkBuddy agents: the Singapore
WorkBuddy client has not been connected to a supported API/connector in this
project. The UI and report explicitly disclose that limitation instead of
claiming that WorkBuddy agents ran.

Owner uploads:
- TXT, CSV and JSON up to 2 MB each are accepted and saved locally under
  outputs/current_case/uploads.
- Text receipts and pasted notes are passed to the existing Ledger text parser.
- The existing Ledger parser does not ingest arbitrary CSV/JSON uploads into its
  numeric calculations yet; those files are inventoried for evidence review and
  the report flags this limitation.
- The existing product-economics model still contains synthetic reference costs;
  do not treat them as owner-verified values.

Human review:
The report is not approved by default. The owner must explicitly approve or
return it. Approval records the decision only; no external action is performed.

Smoke test performed in a temporary copy:
- Python syntax checks passed.
- Inline JavaScript syntax check passed.
- Local HTTP flow tested: intake, Ledger execution, Adversary execution,
  revision handling, report generation, and owner approval.
