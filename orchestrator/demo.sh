#!/usr/bin/env bash
# One-shot end-to-end demo of the Moira orchestrator v0.1 spike.
# Proves: happy path, human gate (pause/inbox/approve), reject->rework, audit trail.
set -euo pipefail
cd "$(dirname "$0")"

REPO="../../ai-sdlc"
SPEC="FUNC-MOIRA-audit-record"
PY=python3

echo "############################################################"
echo "# Moira orchestrator v0.1 — end-to-end demo"
echo "############################################################"
rm -rf .moira

echo; echo "### 1) Tests"
$PY -m unittest discover -s tests 2>&1 | tail -4

echo; echo "### 2) Happy path (hybrid impl gate auto-accepts on high confidence)"
$PY moira_cli.py run "$SPEC" --repo "$REPO"
RID1=$($PY moira_cli.py runs | head -1 | awk '{print $1}')

echo; echo "### 3) Audit record for the happy-path run"
$PY moira_cli.py audit "$RID1" | head -20

echo; echo "### 4) Human gate — run pauses, lands in Inbox"
$PY moira_cli.py run "$SPEC" --repo "$REPO" --analysis-gate human
$PY moira_cli.py inbox
RID2=$($PY moira_cli.py runs | head -1 | awk '{print $1}')

echo; echo "### 5) Approve the gate (capturing WHAT was confirmed) -> run resumes"
$PY moira_cli.py approve "$RID2" --by tomasz.skonieczny --confirm "analiza kompletna i zgodna z intencją"
$PY moira_cli.py inbox

echo; echo "### Done. 'show <run-id>' for the full activity log."
