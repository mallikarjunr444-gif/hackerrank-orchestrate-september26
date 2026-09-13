#!/usr/bin/env python3
"""
evaluate.py — Score predictions against sample_requests.csv ground truth.

Usage:
    python3 evaluate.py                         # scores output.csv vs sample_requests.csv
    python3 evaluate.py --pred my_output.csv    # custom prediction file
    python3 evaluate.py --verbose               # show full row-by-row diff

Returns non-zero exit code if any hard-constraint violations are found.
"""

import csv
import sys
import re
import argparse
from pathlib import Path

REPO = Path(__file__).resolve().parent
DATASET = REPO / "dataset"


def load_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def check_amount_close(a, b, tol=2.0):
    try:
        return abs(float(a) - float(b)) <= tol
    except Exception:
        return str(a).strip() == str(b).strip()


def score_field(pred_val, gt_val, field):
    """Return 1.0 if match, 0.0 otherwise. Amount fields use tolerance."""
    p = str(pred_val).strip()
    g = str(gt_val).strip()
    if field == "amount_safe_to_pay":
        return 1.0 if check_amount_close(p, g, tol=1.0) else 0.0
    return 1.0 if p == g else 0.0


SCORED_FIELDS = [
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
]

VALID_STATUSES = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
VALID_METHODS  = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}


def validate_hard_constraints(row, req, gt_row=None):
    """Check mandatory hard constraints. Returns list of violation strings."""
    violations = []
    rid = row["request_id"]
    gt_row = gt_row or {}

    # 1. amount_safe_to_pay in [0, requested_amount]
    try:
        safe = float(row["amount_safe_to_pay"])
        req_amt_str = (req.get("requested_amount") or gt_row.get("requested_amount", "")).strip()
        if req_amt_str:
            req_amt = float(req_amt_str)
            if not (-1e-4 <= safe <= req_amt + 1e-4):
                violations.append(f"amount_safe_to_pay={safe} out of [0, {req_amt}]")
    except Exception as e:
        violations.append(f"amount_safe_to_pay parse error: {e}")

    # 2. affordability_status valid
    if row["affordability_status"] not in VALID_STATUSES:
        violations.append(f"invalid affordability_status={row['affordability_status']!r}")

    # 3. recommended_payment_method valid
    if row["recommended_payment_method"] not in VALID_METHODS:
        violations.append(f"invalid recommended_payment_method={row['recommended_payment_method']!r}")

    # 4. affordable_now => earliest_date == request_date
    if row["affordability_status"] == "affordable_now":
        req_date = req.get("request_date") or gt_row.get("request_date", "")
        if req_date and row["earliest_date_for_full_payment"] != req_date:
            violations.append(
                f"affordable_now but earliest={row['earliest_date_for_full_payment']!r} != request_date={req_date!r}"
            )

    # 5. payment_plan format
    plan = row["payment_plan"]
    if plan and plan != "none":
        for item in plan.split("|"):
            if not re.match(r"^\d{4}-\d{2}-\d{2}:[\d.]+$", item):
                violations.append(f"malformed payment_plan item {item!r}")

    # 6. spending_changes_needed format (max 3, valid actions)
    changes = row["spending_changes_needed"]
    if changes and changes != "none":
        parts = changes.split("|")
        if len(parts) > 3:
            violations.append(f"too many spending_changes ({len(parts)} > 3)")
        seen = set()
        for p in parts:
            pp = p.split(":")
            if pp[0] not in ("stop", "reduce_to"):
                violations.append(f"invalid change action {pp[0]!r}")
            eid = pp[1] if len(pp) > 1 else ""
            if eid in seen:
                violations.append(f"duplicate event_id in changes: {eid!r}")
            seen.add(eid)

    return violations


def main():
    parser = argparse.ArgumentParser(description="Score Buy-or-Wait predictions")
    parser.add_argument("--pred", default=None,
                        help="Path to predictions CSV (default: auto-generate from sample_requests.csv)")
    parser.add_argument("--gt", default=str(DATASET / "sample_requests.csv"),
                        help="Path to ground truth CSV (default: dataset/sample_requests.csv)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print row-by-row field comparison")
    args = parser.parse_args()

    # If no pred file given, generate predictions for sample_requests on the fly
    if args.pred is None:
        import subprocess, tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False, dir=REPO)
        tmp.close()
        try:
            result = subprocess.run(
                [sys.executable, str(REPO / "code" / "main.py"),
                 "--sample-out", tmp.name],
                capture_output=True, text=True, cwd=str(REPO)
            )
            if result.returncode != 0:
                # Fallback: use output.csv if it exists
                if (REPO / "output.csv").exists():
                    args.pred = str(REPO / "output.csv")
                else:
                    print("ERROR: Could not generate predictions.", file=sys.stderr)
                    print(result.stderr, file=sys.stderr)
                    return 1
            else:
                args.pred = tmp.name
        except Exception as e:
            print(f"ERROR auto-generating predictions: {e}", file=sys.stderr)
            return 1

    preds = {r["request_id"]: r for r in load_csv(args.pred)}
    gts   = {r["request_id"]: r for r in load_csv(args.gt)}
    reqs  = {r["request_id"]: r for r in load_csv(str(DATASET / "requests.csv"))}

    total = len(gts)
    field_scores = {f: 0 for f in SCORED_FIELDS}
    total_violations = 0
    missing = []

    if args.verbose:
        header = f"{'ID':12} | " + " | ".join(f"{f[:14]:14}" for f in SCORED_FIELDS)
        print(header)
        print("-" * len(header))

    for rid, gt in sorted(gts.items()):
        if rid not in preds:
            missing.append(rid)
            continue

        pred = preds[rid]
        req  = reqs.get(rid, {})

        violations = validate_hard_constraints(pred, req)
        if violations:
            total_violations += len(violations)
            for v in violations:
                print(f"VIOLATION {rid}: {v}", file=sys.stderr)

        row_scores = {}
        for f in SCORED_FIELDS:
            s = score_field(pred.get(f, ""), gt.get(f, ""), f)
            field_scores[f] += s
            row_scores[f] = s

        if args.verbose:
            cells = " | ".join(
                f"{'✓' if row_scores[f] else '✗':14}" for f in SCORED_FIELDS
            )
            print(f"{rid:12} | {cells}")
            if not all(row_scores.values()):
                for f in SCORED_FIELDS:
                    if not row_scores[f]:
                        print(f"  {f}: pred={pred.get(f,'')!r}  gt={gt.get(f,'')!r}")

    print()
    print(f"{'='*60}")
    print(f"Benchmark Results on {total} samples ({len(missing)} missing):")
    print(f"{'='*60}")
    for f, score in field_scores.items():
        pct = 100 * score / total if total else 0
        bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
        print(f"  {f:<35} {score:3.0f}/{total}  ({pct:5.1f}%)  {bar}")
    print(f"{'='*60}")
    print(f"  Hard-constraint violations: {total_violations}")
    if missing:
        print(f"  Missing predictions: {missing}")

    overall = sum(field_scores.values()) / (len(SCORED_FIELDS) * total) if total else 0
    print(f"  Overall field accuracy:     {overall*100:.1f}%")

    return 1 if (total_violations > 0 or missing) else 0


if __name__ == "__main__":
    sys.exit(main())
