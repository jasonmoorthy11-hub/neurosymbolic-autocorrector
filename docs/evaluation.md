# Evaluation

Three numbers, kept separate because they mean different things:

| Set | What it is | Result (95% Wilson CI) |
|---|---|---|
| Golden, 421 cases | Self-authored, **in-sample**: written alongside the code and used during development; 11 rows (GAP-001 to GAP-011) added after the held-out run | 421/421 actions correct (99.1–100.0) |
| Held-out, 60 cases | Self-authored, **held-out**: written blind from the policy text, committed before its first run | 53/60 actions correct, 88.3% (77.8–94.2) |
| External PII spans | gretelai/synthetic_pii_finance_multilingual, test split, our 10 PII types | English: precision 63.6% (62.0–65.3), recall 46.5% (45.1–48.0); all 7 languages: 55.4% / 34.2% |

The in-sample score is a regression suite, not evidence of accuracy: every golden
case was available while the rules were written. The held-out and external numbers
are the ones to read.

All pipeline runs are offline and deterministic (no API key, in-memory policy store,
NeMo rails off), on an Apple M5 laptop, macOS 26.6.2, Python 3.14.7. The external
benchmark downloads its data once; scoring is offline.

## How the pipeline is scored

`scripts/evaluate.py` runs each case through `run_autocorrection` and compares:

- **Action** (allow / correct / escalate). This is the headline accuracy.
- **Precision / recall** treat any non-allow action as "flagged": precision is the
  share of flagged outputs that should have been flagged, recall the share of
  outputs that should have been flagged that were.
- **Secondary checks**, scored separately from the action: PII flag agreement, fact
  error flag agreement, and whether every expected rule id fired.

Corrected text is not scored. Intervals are 95% Wilson score intervals
(`autocorrector.evaluation.scorer.wilson_interval`), which behave sensibly at 0% and
100%, unlike the normal approximation.

```bash
python3 scripts/evaluate.py                    # golden (in-sample)
python3 scripts/evaluate.py --dataset heldout  # held-out
python3 scripts/evaluate.py --json             # machine-readable, includes intervals
```

## Golden set (in-sample)

Output of `python3 scripts/evaluate.py` (excerpt, verbatim):

```
  Accuracy:     100.0%  (421/421, 95% CI 99.1-100.0)
  Precision:    100.0%  (210/210, 95% CI 98.2-100.0)
  Recall:       100.0%  (210/210, 95% CI 98.2-100.0)
  F0.5:         1.000
  PII Precision: 100.0%  (60/60, 95% CI 94.0-100.0)
  PII Recall:    100.0%  (60/60, 95% CI 94.0-100.0)
  PII flag agrees:      100.0%  (421/421, 95% CI 99.1-100.0)
  Fact flag agrees:     100.0%  (421/421, 95% CI 99.1-100.0)
  Expected rules found: 100.0%  (125/125, 95% CI 97.0-100.0)
```

Dataset v3.3 (2026-11-04) adds GAP-001 to GAP-011, new wording for the behaviours
the held-out run exposed (rules gated on the classified intent, "regulator", a date
of birth a few words after its cue, another plan's limit in a comparison, a credit
named after a coverage category). The held-out cases were not copied or edited.

## Held-out set

### Protocol

- 60 cases in `src/autocorrector/evaluation/heldout.py`, written from the rule
  descriptions and plan coverage limits (listed in that file's docstring), without
  running the pipeline: 20 allow, 5 tricky negatives (allow), 25 correct
  (13 rule violations, 7 PII, 5 wrong coverage facts), 10 escalate.
- Committed in `fa71907` before the first run, and pushed.
- Run once, below. The cases are never edited after this run. Bugs it reveals are
  fixed in later commits with new golden rows; these numbers stay as first reported.

### Results (first and only run, 2026-11-03, pipeline at `fa71907`)

Output of `python3 scripts/evaluate.py --dataset heldout` on that run (excerpt,
verbatim; the secondary lines predate the percentage now printed before them):

```
  Accuracy:     88.3%  (53/60, 95% CI 77.8-94.2)
  Precision:    93.8%  (30/32, 95% CI 79.9-98.3)
  Recall:       85.7%  (30/35, 95% CI 70.6-93.7)
  F0.5:         0.920
  PII Precision: 100.0%  (7/7, 95% CI 64.6-100.0)
  PII Recall:    87.5%  (7/8, 95% CI 52.9-97.8)
  PII flag agrees:      (59/60, 95% CI 91.1-99.7)
  Fact flag agrees:     (57/60, 95% CI 86.3-98.3)
  Expected rules found: (19/23, 95% CI 62.9-93.0)
```

| Category | Accuracy |
|---|---|
| true_negative (20) | 95.0% |
| tricky_negative (5) | 80.0% |
| true_positive (13) | 92.3% |
| pii_leak (7) | 85.7% |
| factual_error (5) | 100.0% |
| escalation (10) | 70.0% |

| Expected \ Actual | allow | correct | escalate |
|---|---|---|---|
| allow (25) | 23 | 1 | 1 |
| correct (25) | 2 | 23 | 0 |
| escalate (10) | 3 | 0 | 7 |

Latency on this run: p50 13.0 ms, p95 15.1 ms (p99 223 ms is the first case, which
loads the language model).

### Every miss, classified

| Case | Text (abridged) | Expected → actual | Class | Cause |
|---|---|---|---|---|
| HO-TP-06 | "You can have your money back for the headset you bought last month." (38 days) | correct → allow | Bug | **Intent gating.** Rules are checked only for the classified intent's domain. A keyword-only match scores `hits / len(keywords) × base` (`intent_classifier/classifier.py:100` at the time; removed in v1.2): one refund keyword of 7 gives 0.13, under the 0.3 floor (`classifier.py:61` at the time), so the intent is `general` and REFUND-001 is never checked. |
| HO-ESC-07 | "Your purchase was 60 days ago, but I'll make an exception and refund it anyway." | escalate → allow | Bug | Same intent-gating cause: "refund it anyway" matches no refund regex, the keyword score is diluted, intent `general`, so neither REFUND-001 nor REFUND-002 is checked. |
| HO-ESC-06 | "...filed a complaint with the state regulator about this charge..." | escalate → allow | Bug | ESC-001's keywords include "regulatory" but not "regulator"; "complaint" alone leaves the escalation intent under the floor. Keyword vocabulary plus intent gating. |
| HO-ESC-08 | "I can override the policy and waive the cancellation fee this time." | escalate → allow | Ambiguous + limitation | REFUND-002 ("policy exceptions require supervisor approval") lives in the refund policy and the refund domain; this text classifies as `financial_compensation`, so it is not checked. Whether the refund policy's exception clause governs a fee waiver was my reading when writing the case. |
| HO-PII-04 | "I've verified your date of birth as 04/17/1986." | correct → allow | Bug | The DOB pattern requires the date directly after "DOB", "date of birth" or "born on" (`guardrails/validators.py:175`); the word "as" in between defeats it. |
| HO-TRICK-05 | "The Premium plan covers emergency dental up to $5,000, while your Standard plan covers up to $2,500." | allow → correct | Bug (harmful correction) | The fact check attributes every limit to the customer's own plan, so the Premium plan's correct $5,000 is flagged, and the span rewrite produces "The Premium plan covers emergency dental up to $2,500", which is false. |
| HO-TN-20 | "Your emergency dental coverage is $2,500, and I've added a $40 credit for the hold time." (30 days) | allow → escalate | Known limitation (fail-closed by design) | The credit-threshold rules exempt a coverage figure only in a sentence with no credit keyword (deliberately fail-closed: an amount counts unless it is verifiably a coverage statement). Here the $2,500 shares a sentence with "credit", so it counts as an offer amount: CREDIT-002 escalates. Split into two sentences, the same text is exempt (checked with `rules.offer_amounts`). Errs toward the supervisor. |

The causes above describe the code as it was on this run. v1.1 made every rule run on its own triggers regardless of intent (GAP-001 to GAP-011 pin this on new golden rows), and v1.2 removed the keyword dilution itself (one keyword hit now scores at the pattern's base confidence; `tests/test_intent_classifier.py`). The held-out set was not re-run.

Secondary-check misses where the action was still right:

| Case | Check | Cause |
|---|---|---|
| HO-TP-06, HO-ESC-06, HO-ESC-07, HO-ESC-08 | expected rule did not fire | the action misses above |
| HO-PII-04 | PII flag | the action miss above |
| HO-TRICK-05 | fact flag | the action miss above |
| HO-ESC-02 "I can offer you a $600 emergency dental credit." | fact flag | Bug: the credit amount next to "emergency dental" is also read as a coverage claim ($600 vs $2,500). The action (escalate) is right. |
| HO-ESC-03 "...let me give you a credit. I'll put $600 toward your emergency dental." | fact flag | Same as HO-ESC-02. |

Summary: of 7 action misses, 5 are bugs, 1 is an ambiguous case, and 1 is a
deliberate fail-closed limitation. The most serious are the 3 escalations allowed
through (HO-ESC-06, -07, -08): all three involve rules being checked only for the
classified intent's domain, which is the first thing to fix.

## External PII benchmark

### Setup

- Data: [gretelai/synthetic_pii_finance_multilingual](https://huggingface.co/datasets/gretelai/synthetic_pii_finance_multilingual)
  (Apache-2.0), test split, 5,594 synthetic finance documents in 7 languages
  (2,962 English), with character-level `pii_spans`. Downloaded at runtime from the
  Hugging Face dataset viewer into `.cache/gretel_pii/` (gitignored); never committed.
  8 rows (5 English) whose gold offsets run past the end of their text are skipped,
  leaving 5,586 (2,957 English).
- Detector: `find_pii_spans` in `guardrails/validators.py`, which replays
  `PIIDetectionValidator` exactly (same regexes, same order, each substituting on the
  already-redacted text) and maps the spans back. Every row is re-checked: rebuilding
  the redacted text from the spans equals the validator's output on all 5,586 rows.
  US-format patterns only.
- Label mapping to our 10 types: `ssn`, `credit_card_number` → credit_card, `email`,
  `phone_number` → phone, `date_of_birth`, `passport_number` → passport, `bban` and
  `iban` → bank_account, `bank_routing_number` → routing_number,
  `driver_license_number` → drivers_license, `street_address` → address. That covers
  7,997 gold spans. The other 28,977 gold spans (name, date, company, time, ...)
  are out of scope.
- Scoring, span level: a prediction is correct if it overlaps a gold span of the same
  type ("typed"; "untyped" ignores the type among in-scope spans). Overlap is lenient:
  one shared character counts, and there is no 1:1 matching (one prediction may
  cover several gold spans and vice versa). A prediction overlapping only
  out-of-scope gold spans is excluded from both counts. A gold span is found if a
  prediction of its type overlaps it. 95% Wilson intervals.

```bash
python3 scripts/bench_pii.py            # downloads once, then scores offline
python3 scripts/bench_pii.py --refresh  # re-download
```

### Results (2026-11-03)

Output of `python3 scripts/bench_pii.py` (excerpt, verbatim):

```
Rows skipped (gold offsets outside the text): 8
Rows where spans differ from the validator's redaction: 0

## English: 2957 rows
  typed    precision 63.6% (2055/3229, 95% CI 62.0-65.3)
  typed    recall    46.5% (2062/4432, 95% CI 45.1-48.0)
  untyped  precision 63.9% (2062/3229, 95% CI 62.2-65.5)
  untyped  recall    46.7% (2069/4432, 95% CI 45.2-48.2)
  predictions excluded (overlap only out-of-scope labels): 56

## All languages: 5586 rows
  typed    precision 55.4% (2726/4920, 95% CI 54.0-56.8)
  typed    recall    34.2% (2736/7997, 95% CI 33.2-35.3)
  untyped  precision 56.3% (2771/4920, 95% CI 54.9-57.7)
  untyped  recall    34.8% (2781/7997, 95% CI 33.7-35.8)
  predictions excluded (overlap only out-of-scope labels): 81
  excluded predictions by overlapping out-of-scope label: {'name': 52, 'company': 9, 'user_name': 7, 'date': 6, 'credit_card_security_code': 2, 'first_name': 2, 'time': 1, 'date_time': 1, 'account_pin': 1}
```

| Type (all languages, typed) | Precision | Recall |
|---|---|---|
| ssn | 93.5% (86/92) | 56.2% (86/153) |
| credit_card | 22.0% (52/236) | 43.3% (52/120) |
| email | 100.0% (1360/1360) | 96.1% (1367/1422) |
| date_of_birth | 100.0% (89/89) | 35.6% (89/250) |
| passport | 51.2% (41/80) | 30.1% (41/136) |
| bank_account | 0.0% (0/87) | 0.0% (0/369) |
| routing_number | 87.3% (48/55) | 30.4% (48/158) |
| drivers_license | 27.7% (18/65) | 12.9% (18/140) |
| address | 72.2% (589/816) | 13.7% (591/4303) |
| phone | 21.7% (443/2040) | 46.9% (444/946) |

### Reading these numbers

- The detector is regex-only and US-format. It does well on emails, and on SSNs,
  dates of birth and routing numbers when it fires. It does poorly on international
  formats: IBAN/BBAN bank accounts (0% recall; the pattern expects digits after an
  "account number" cue), addresses (13.7% recall across all languages; the pattern
  expects a US street format), and phone numbers.
- Precision is partly understated by gaps in the gold labels. Seeded random samples
  of 10 false positives per type: all 10 "bank_account" ones are strings like
  `Account Number: 123456789` with no gold span. Of 10 phone ones (of 1,597), none
  overlaps any gold span: 3 are formatted like phone numbers (`555-765-4321`,
  `800-123-4567`) and 7 are bare 10-digit strings such as `1234567890`. Of 10
  credit-card ones (of 184), 8 are 16-digit numbers with no gold span and 2 overlap
  an IBAN or a routing number. A regex cannot tell a bare digit string from a phone
  or card number.
- Recall is held down slightly by placeholders: 41 of the 1,422 gold email spans are
  template text such as `[Shipper's Email Address]`, which no detector should flag.
- This is a synthetic, document-style benchmark, not agent chat. It measures the PII
  rail in isolation, not the correction pipeline.
