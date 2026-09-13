# Buy or Wait? — AI-Powered Financial Decision Agent

**HackerRank Orchestrate (September 2026) Submission**

---

## Overview

This system decides whether a user can safely afford a requested expense given their full financial picture: recurring income and expenses, pending obligations, minimum balance requirements, payment preferences, and unstructured evidence from messages and receipt images.

For every request, it outputs:
- `amount_safe_to_pay` — maximum safely payable today
- `affordability_status` — one of: `affordable_now`, `affordable_with_plan`, `affordable_later`, `not_affordable`
- `recommended_payment_method` — `full_payment`, `partial_payment`, `installments`, `wait`, or `not_recommended`
- `payment_plan` — chronological payment schedule
- `earliest_date_for_full_payment` — first conservative date for full payment
- `spending_changes_needed` — up to 3 `stop`/`reduce_to` actions on flexible events
- `decision_explanation` — grounded natural language explanation

---

## Architecture

```
dataset/ CSV files
    │
    ▼
data_loader.py          ← Loads, normalises, and currency-converts all datasets.
                           OCR-derived amounts for 16 blank-amount image receipts
                           are hard-coded after extraction with Apple Vision.
    │
    ▼
forecaster.py           ← Deterministic 90-day daily cash-flow forecaster:
                           1. Pending debits (reserved on due date)
                           2. Scheduled events
                           3. Confirmed inflows from message signals
                           4. Recurring monthly streams (salary dedup logic to
                              avoid phantom recurring from retroactive payslips)
                           5. Variable streams (groceries, transport) with
                              outlier-filtered interval/average projection
    │
    ▼
decision_engine.py      ← Constraint-based plan selection:
                           • Computes amount_safe_to_pay and earliest_date
                           • Generates candidate plans: full_payment, partial_payment,
                             installments (per supplier options), wait
                           • Tries spending-change combinations (stop/reduce)
                           • Ranks plans by 6-tier tie-breaking per problem spec
                           • Generates grounded, template-bound explanations
    │
    ▼
output.csv              ← 250 predictions (one per request)
```

---

## Setup

### Requirements

- Python 3.9+
- No external packages required (standard library only: `csv`, `calendar`, `math`, `datetime`, `collections`, `re`)

### Quick Start

```bash
# Clone / navigate to repo
cd hackerrank-orchestrate-september26

# Run predictions (writes output.csv to repo root)
python3 code/main.py

# Run benchmark against 25 public sample requests
python3 code/main.py --test
```

**Expected benchmark output (25 sample requests):**

```
Affordability Status : 23/25 (92.0%)
Payment Method       : 25/25 (100.0%)
Earliest Date        : 22/25 (88.0%)
Spending Changes     : 23/25 (92.0%)
Payment Plan         : 23/25 (92.0%)
```

---

## Key Design Decisions

### 1. Deterministic-First Architecture
The core engine contains zero stochastic LLM calls at inference time. Every number in the output is traceable to a computation on the input data. This guarantees reproducibility and eliminates hallucination.

### 2. Conservative Cash-Flow Forecasting
- **Pending debits** are reserved (worst-case timing)
- **Pending credits** (commissions, bonuses, refunds) are NOT counted until settled — strictly per rules
- **Variable expenses** (groceries, transport) use outlier-filtered historical averages at observed intervals
- **Salary deduplication**: When a user has both a regular payroll record and a retroactive net-salary document for the same month (e.g. `event_253`), only the recurring stream is projected forward to avoid phantom income double-counting

### 3. OCR Receipt Amounts (Image Evidence)
16 financial events had blank `amount` fields referencing `dataset/media/images/`. Amounts were extracted using **Apple Vision Framework** (`VNRecognizeTextRequestRevision3`) and verified against visible currency symbols and totals. These are baked into `data_loader.py` as `IMAGE_AMOUNTS` for deterministic, reproducible results.

### 4. Message Parsing
`data_loader.py` parses `messages.csv` for:
- Salary termination signals (`employment has ended`, etc.)
- Rent increase percentages
- Confirmed client invoice inflows
- Confirmed base salary overrides from payroll communications

### 5. Plan Ranking (6-Tier Tie-Breaking)
Candidate plans are ranked by:
1. Completes full request by `desired_completion_date` (preferred)
2. No spending changes required (preferred)
3. Minimize total amount paid
4. Earlier start date
5. Fewer payments
6. Lowest `payment_option_id`

### 6. Spending Changes
Only non-protected, `reducible`/`stoppable` events in categories the user permits are considered. At most 3 changes are applied. `stop:event_id` halts a future projected expense; `reduce_to:event_id:amount` caps it at its `minimum_allowed_amount`.

---

## File Structure

```
.
├── code/
│   ├── main.py                  ← Entry point (--test for benchmark, default for full run)
│   ├── data_loader.py           ← CSV loading, currency conversion, OCR amounts, message parsing
│   ├── forecaster.py            ← 90-day cash-flow builder + simulate() function
│   ├── decision_engine.py       ← Plan selection, ranking, validation, explanation generation
│   └── evaluation/
│       ├── main.py              ← Detailed per-field accuracy evaluator
│       └── usage_report.md      ← Token usage & cost report (required by §6.5)
├── dataset/                     ← Input CSVs + media/images/
├── evaluation/
│   └── usage_report.md          ← Token usage report (required §6.5 artifact)
├── output.csv                   ← 250 predictions (generated by code/main.py)
├── log.txt                      ← Agent session log
└── README.md                    ← This file
```

---

## Evaluation / Token Usage

See [`evaluation/usage_report.md`](evaluation/usage_report.md) for full details.

**Summary:** The system uses **zero cloud API tokens**. The entire pipeline runs deterministically on local compute using only Python's standard library and Apple Vision for OCR (offline). Total wall-clock time: ~0.24 seconds for 250 requests.

---

## Submission Artifacts

| Artifact | Description |
|---|---|
| `code.zip` | Source code (`code/` + `evaluation/usage_report.md` + `README.md`) |
| `output.csv` | 250 predictions for `dataset/requests.csv` |
| `log.txt` | Agent session transcript / chat log |
