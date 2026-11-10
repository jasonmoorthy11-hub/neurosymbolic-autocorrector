# Evaluation

Three numbers, kept separate because they mean different things:

| Set | What it is | Result (95% Wilson CI) |
|---|---|---|
| Golden, 498 cases | Self-authored, **in-sample**: written alongside the code and used during development; 88 rows (GAP-001 to GAP-024, PIN-001 to PIN-056, RT-001 to RT-008) added after the held-out run | 498/498 actions correct (99.2–100.0) |
| Held-out, 60 cases | Self-authored, **held-out**: written blind from the policy text, committed before its first run | 53/60 actions correct, 88.3% (77.8–94.2) |
| External PII spans | gretelai/synthetic_pii_finance_multilingual, test split, our 10 PII types | English: precision 63.6% (62.0–65.3), recall 46.5% (45.1–48.0); all 7 languages: 55.4% / 34.2% |

The in-sample score is a regression suite, not evidence of accuracy: every golden
case was available while the rules were written. The held-out and external numbers
are the ones to read.

All pipeline runs are offline and deterministic (no API key, in-memory policy store,
NeMo rails off), on an Apple M5 laptop, macOS 26.6.2, Python 3.14.7. The external
benchmark downloads its data once; scoring is offline. The one exception is the
single LLM-tier recording at the end of this page, which is a demo lane, not a
benchmark.

## How the pipeline is scored

`scripts/evaluate.py` runs each case through `run_autocorrection` and compares:

- **Action** (allow / correct / escalate). This is the headline accuracy.
- **Precision / recall** treat any non-allow action as "flagged": precision is the
  share of flagged outputs that should have been flagged, recall the share of
  outputs that should have been flagged that were.
- **Secondary checks**, scored separately from the action: PII flag agreement, fact
  error flag agreement, and whether every expected rule id fired.

The action score does not look at the corrected text; four structural checks do
([Corrected text](#corrected-text-structural-checks) below). Whether a correction is
the right policy answer is not scored anywhere. Intervals are 95% Wilson score intervals
(`autocorrector.evaluation.scorer.wilson_interval`), which behave sensibly at 0% and
100%, unlike the normal approximation.

```bash
python3 scripts/evaluate.py                    # golden (in-sample)
python3 scripts/evaluate.py --dataset heldout  # held-out
python3 scripts/evaluate.py --json             # machine-readable, includes intervals
python3 scripts/evaluate.py --invariants       # structural checks on the corrected text
```

## Golden set (in-sample)

Output of `python3 scripts/evaluate.py` (excerpt, verbatim):

```
  Accuracy:     100.0%  (498/498, 95% CI 99.2-100.0)
  Precision:    100.0%  (274/274, 95% CI 98.6-100.0)
  Recall:       100.0%  (274/274, 95% CI 98.6-100.0)
  F0.5:         1.000
  PII Precision: 100.0%  (60/60, 95% CI 94.0-100.0)
  PII Recall:    100.0%  (60/60, 95% CI 94.0-100.0)
  PII flag agrees:      100.0%  (498/498, 95% CI 99.2-100.0)
  Fact flag agrees:     100.0%  (498/498, 95% CI 99.2-100.0)
  Expected rules found: 100.0%  (186/186, 95% CI 98.0-100.0)
```

Latency on the golden set as it stood on 2026-11-06 (v3.3), second of two consecutive runs: p50 12.0 ms,
p95 14.2 ms, p99 17.0 ms per reply (the first run: 11.8 / 14.2 / 18.9). The first case
loads the language-detection model and is the slowest.

Dataset v3.3 (2026-11-04) adds GAP-001 to GAP-011, new wording for the behaviours
the held-out run exposed (rules gated on the classified intent, "regulator", a date
of birth a few words after its cue, another plan's limit in a comparison, a credit
named after a coverage category). The held-out cases were not copied or edited.

Dataset v3.4 (2026-11-09) adds GAP-012 to GAP-024 for a defect found after v1.5: a
rule keyword matched any word that started with it, and a keyword that is also a
name ("Thanks, Sue." and "suede boots" were escalated as legal threats; "creditor",
"always willing", "you'll nevertheless" and "between you and Medicare" triggered
other rules). Six of the 13 rows fail on the v1.5 rules (428/434); seven pin forms
the fix must keep ("sue us", "Sue if you want", "refundable", "by next weekend",
"offer your account", "internal tooling", "accept the returned item"). They were
written with the fix in hand, so like every golden row they are regression rows,
not evidence. The same version adds PIN-001 to PIN-056 (next section).

Dataset v3.5 (2026-11-10) adds rows RT-001 onwards for the false-positive classes
the [real-turn audit](#real-agent-turns-results-one-run-2026-11-09) found. They are
written in this project's own words (no audited turn is copied), with the fix in
hand, so they are regression rows, not evidence; the audit itself is not re-run.
RT-001 to RT-008 cover language detection: an order code, a bare number, a
username and two typo-ridden English lines are English (three of the five were
escalated before the fix); unaccented Spanish, a short unaccented Spanish credit
with a "$" amount and accented Spanish still escalate.

### Mutation score: how much of the rule set the golden suite pins down

100% on a suite says little if the suite would also pass with a rule broken.
`scripts/mutate_policy.py` checks that: it makes one small change to the default
rules at a time (an amount threshold moved by 1 or 10, a condition value moved by
1, one keyword dropped, all keywords but the first dropped, a severity lowered one
step), runs the golden cases against the changed rules, and counts the mutant as
killed if any case loses its expected action or one of its expected rule ids. That
is all it compares: the corrected text, the edits and the citations are not
scored, and the fixed rewrite wording ("$50", "30-day window") does not follow a
changed threshold.

| Golden set | Mutants killed, of 104 generated |
|---|---|
| 434 cases (before the PIN rows) | 43 (41.3%) |
| 490 cases (v3.4) | 98 (94.2%) |

The 104 are every change of those five kinds, not a sample, so there is no
interval; they also overlap (the keep-one-keyword mutant drops what the single
drops do).

The first run is the finding: 61 of 104 single changes to the rules went unnoticed.
54 of them were dropped keywords, five were lowered severities. Most older rows stack several keywords of a rule
in one reply ("I promise you'll never be charged that fee again. It's permanently
removed."), so removing any one of them changed nothing. Two were the refund
window moved to 29 or 31 days: no row sat next to the 30-day boundary. The 56 PIN
rows each carry exactly one keyword of their rule, or sit on one side of that
boundary (29, 30 and 31 days). They were written from the rule definitions, against
the list of survivors, and all 56 passed on their first run, so they found no new
pipeline bug; they make the suite notice changes it used to miss. The second
number is therefore in-sample to this list of mutants: it shows the gap was
closed, not how the suite would do against other kinds of change.

The six survivors, classified:

- Five lower a non-critical severity (CREDIT-001, REFUND-001, PROMISE-001 and
  SLA-001 high to medium, DISC-001 medium to low). Only `critical` changes routing
  (it escalates); the other levels all lead to a correction, and golden rows do not
  state an expected severity. These are real changes that the golden suite cannot
  see: the severity is shown in the dashboard, stored in the audit row (where a
  query can filter on it) and orders the whole-message templates. A unit test
  (`tests/test_ingestion.py`) does tie each rule's severity to its policy document.
- One is equivalent: dropping PROMISE-001's "waive permanently" changes nothing,
  because the keyword "permanently" already matches every such reply.

This scores the test suite, not the pipeline: it says the golden rows now react to
98 of these 104 single changes to the 8 rules, not that the rules are right. Run on
2026-11-09: 77.8 s for the 490-case set (527 s for the first run, where each of the
61 survivors ran every case). Not part of CI.

```bash
python3 scripts/mutate_policy.py                 # all mutants
python3 scripts/mutate_policy.py --rule ESC-001  # one rule
```

### Corrected text: structural checks

`python3 scripts/evaluate.py --invariants` runs every golden case and, for the 215
the pipeline corrects, checks the delivered text with no model in the loop. 47 of
the 215 are PII redaction only: the text changes but no edit is recorded, so there
is nothing to replay or cite and only the first check counts them. The other 168
carry edits (150 span rewrites, 18 whole-message templates).

| Check | What it means | Passed / counted |
|---|---|---|
| Idempotent | The corrected text, sent through the pipeline again with the same context, is allowed unchanged | 215/215 |
| Edit log replays | Applying the recorded edits to the PII-redacted original gives the delivered text byte for byte | 165/168 |
| Untouched sentences kept | Every sentence that no edit overlaps is delivered byte for byte, in the same order | 13/13 |
| Citations resolve | Every edit cites the document and section of each rule it names, or for a coverage fix the document and page of the row whose limit it wrote, as `policies/` states them | 168/168 |

These are counts, not estimates: the cases are authored, not sampled, and the
pipeline is deterministic, so no interval is given.

- The three replay failures (TP-023, TP-039, TP-042) differ by one trailing space:
  the span rewriter drops a second offending sentence together with the space
  before it, and the edit records only the sentence. The words are accounted for;
  the log is not byte-exact there. The same holds for any dropped sentence, not
  only a last one: the edit never covers the separator. It is left as it is
  because one recorded replay scenario contains such an edit, and changing its
  offsets would change the recording.
- "Untouched sentences kept" has n = 13: only 13 of the 168 edited cases have a
  sentence that no edit overlaps. In 137 span cases every sentence was rewritten,
  and the 18 templates replace the whole reply by design. Most golden corrections
  are one or two sentences long; this check is thin.
- The edit log does not mark where one stage or attempt ends (fact fixes, then the
  policy rewrite, each with offsets into the text it received). The checker starts
  a new group when the kind of edit changes or an edit starts before the previous
  one ended. That rule belongs to the checker, not the log, and it has a known
  gap: a second correction attempt whose first edit starts after the previous
  attempt's last edit ended is read as the same attempt, and a correct log then
  fails to replay. The golden set does not exercise it: 167 of the 168 edited
  cases are a single group, MULTI-001 has two (a fact fix, then a rewrite), and
  the only replay failures are the three above.
- Determinism: `tests/golden_outputs.json` records one SHA-256 per case over its
  action, delivered text, rule ids found in the original reply, PII types and edits (not the
  fact errors, the guardrails list, the explanation, the confidence or the
  intent). The test suite compares all 498 on each run and names the cases that
  differ; CI repeats the run under two other `PYTHONHASHSEED` values. On
  2026-11-09 the hash over all outputs was identical on Python 3.14.7 with
  guardrails-ai 0.10.2 and Python 3.12.15 with guardrails-ai 0.11.0, each under
  three hash seeds.

These are structural checks on in-sample cases. A correction can pass all four and
still be the wrong answer for the customer: nothing here judges whether capping a
credit at $50 or replacing a promise with the stock sentence was the right fix.

```bash
python3 scripts/evaluate.py --invariants --seeds 3      # the table, then the same outputs under 3 hash seeds
python3 scripts/evaluate.py --invariants --record-outputs   # after an intended change to the outputs
```

The command exits 1 only when a hash-seed run gives different outputs; a failed
check is listed under "Failures" and leaves the exit status at 0.

### What a block-only gate would do on this set (arithmetic, in-sample)

Not a measurement: this is counting the golden labels. 271 of the 490 cases are
labelled as needing action (215 correct, 56 escalate). A gate with the same
detection that can only hold a flagged reply for a person would hold all 271
(55.3%). This pipeline holds 56 (11.4%), sends 215 corrected (43.9%) and sends 219
unchanged (44.7%). The shares reflect how the set was written (it is synthetic and
deliberately dense with violations), not real traffic.

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

Re-run on 2026-11-09 (v1.6 code): the English output is identical. Over all
languages the typed counts are 2728/4923 and 2738/7997 (still 55.4% / 34.2%), with
two rows changed: date_of_birth is 90/90 and 90/250 since the v1.1 pattern change,
and email is 1361/1362 and 1368/1422 since the v1.6 pattern change (one French
address with an accented local part is now redacted in part, in two places).

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

### Compared with Presidio (English, 2026-11-09)

[Presidio](https://github.com/data-privacy-stack/presidio) (the open-source PII
detector that started at Microsoft) run on the same 2,957 English rows under the
same scorer. Report only: Presidio is **not** a dependency of this project, and
nothing in the package imports it.

- Versions: presidio-analyzer 2.2.366, spaCy 3.8.18 with `en_core_web_lg` 3.8.0
  (Presidio's default model), Python 3.12.15. Default recognizers, English, no
  custom recognizers or context words. With `en_core_web_sm` every count below is
  the same.
- Entity mapping: `US_SSN` → ssn, `CREDIT_CARD` → credit_card, `EMAIL_ADDRESS` →
  email, `PHONE_NUMBER` → phone, `US_PASSPORT` → passport, `US_BANK_NUMBER` and
  `IBAN_CODE` → bank_account, `US_DRIVER_LICENSE` → drivers_license. Presidio has no
  recognizer specific to a date of birth or a street address (`DATE_TIME` and
  `LOCATION` are any date and any place, so they are not mapped), and its
  routing-number recognizer is disabled by default. That leaves 7 of our 10 types
  that both tools cover.
- Presidio gives each result a score from 0 to 1 and by default keeps all of them.
  The result depends heavily on where the score is cut, so four cut-offs are
  shown. 0.5 and 0.85 were chosen before the first run; 0.4 was added after a
  review of the first table: no passport result on these rows scores 0.5 or more
  and most phone results score below it, so a cut at 0.5 removes them. None
  was tuned on this data, and none is "the" Presidio number.

| Detector, English, typed | Precision | Recall |
|---|---|---|
| This project's regex rail | 63.6% (2055/3229) | 46.5% (2062/4432) |
| Presidio, score ≥ 0 (its default) | 12.8% (1518/11885) | 34.4% (1526/4432) |
| Presidio, score ≥ 0.4 | 48.8% (1490/3053) | 33.8% (1498/4432) |
| Presidio, score ≥ 0.5 | 90.7% (1236/1362) | 28.0% (1243/4432) |
| Presidio, score ≥ 0.85 | 98.8% (969/981) | 22.0% (976/4432) |
| **Only the 7 types both cover** | | |
| This project's regex rail | 58.7% (1357/2313) | 72.9% (1362/1869) |
| Presidio, score ≥ 0 (its default) | 12.8% (1518/11885) | 81.6% (1526/1869) |
| Presidio, score ≥ 0.4 | 48.8% (1490/3053) | 80.1% (1498/1869) |
| Presidio, score ≥ 0.5 | 90.7% (1236/1362) | 66.5% (1243/1869) |
| Presidio, score ≥ 0.85 | 98.8% (969/981) | 52.2% (976/1869) |

At score ≥ 0 Presidio keeps every weak pattern match, hence its 11,885
predictions.

| Type, English, typed | This project: precision | recall | Presidio ≥ 0.4: precision | recall | Presidio ≥ 0.5: precision | recall |
|---|---|---|---|---|---|---|
| ssn | 93.0% (66/71) | 86.8% (66/76) | 94.2% (49/52) | 64.5% (49/76) | 98.0% (49/50) | 64.5% (49/76) |
| credit_card | 33.6% (38/113) | 50.0% (38/76) | 73.7% (28/38) | 36.8% (28/76) | 73.7% (28/38) | 36.8% (28/76) |
| email | 100.0% (816/816) | 97.4% (820/842) | 100.0% (809/809) | 96.6% (813/842) | 100.0% (809/809) | 96.6% (813/842) |
| date_of_birth | 100.0% (84/84) | 58.7% (84/143) | no recognizer | 0/143 | no recognizer | 0/143 |
| passport | 52.9% (36/68) | 46.8% (36/77) | 45.8% (60/131) | 79.2% (61/77) | no result this high | 0/77 |
| bank_account | 0.0% (0/83) | 0.0% (0/194) | 19.3% (92/476) | 49.0% (95/194) | 98.9% (92/93) | 49.0% (95/194) |
| routing_number | 84.8% (39/46) | 43.3% (39/90) | disabled by default | 0/90 | disabled by default | 0/90 |
| drivers_license | 31.5% (17/54) | 23.6% (17/72) | 27.1% (46/170) | 63.9% (46/72) | 70.5% (31/44) | 43.1% (31/72) |
| address | 73.2% (575/786) | 24.8% (577/2330) | no recognizer | 0/2330 | no recognizer | 0/2330 |
| phone | 34.7% (384/1108) | 72.4% (385/532) | 29.5% (406/1377) | 76.3% (406/532) | 69.2% (227/328) | 42.7% (227/532) |

How to read it:

- Presidio's overall recall is low here mostly because of what it does not cover:
  addresses are 2,330 of the 4,432 English gold spans, and it has no street-address
  recognizer. That is a fact about this benchmark's label mix, not a defect.
- On the 7 shared types the verdict depends on the cut-off. At ≥ 0.5 Presidio is
  far more precise than this project (90.7% against 58.7%) with lower recall (66.5%
  against 72.9%). At ≥ 0.4 it has the higher recall (80.1% against 72.9%) with
  lower precision (48.8% against 58.7%).
- What holds at both 0.4 and 0.5: Presidio finds bank accounts and this project
  finds none; Presidio finds more driver's licences; this project finds more SSNs
  (86.8% against 64.5%) and more credit cards (50.0% against 36.8%), the cards
  with many false matches; email is about level.
- Passports and phone numbers flip with the cut-off: Presidio's recall is higher at
  ≥ 0.4 (79.2% and 76.3%) and lower at ≥ 0.5 (none and 42.7%).
- The two are complementary. A deployment that needs PII detection beyond a demo
  should use Presidio or a comparable detector, tuned for its data; the regex rail
  here is the dependency-free default of a project whose subject is policy
  correction.
- Same caveats as above: synthetic documents, lenient overlap scoring, gaps in the
  gold labels. Presidio took 53 s for the 2,962 English rows on this laptop.

```bash
pip install -e ".[bench]" && python -m spacy download en_core_web_lg
python3 scripts/bench_pii.py --detector presidio
```

## Real agent turns: protocol, written before the first run

Everything above is synthetic text, most of it written by this project's author.
This audit runs the pipeline on agent turns written by other people and asks one
question: when it flags a turn, is the flag right? This section was committed
before the script was run on the data and before any flagged turn was read; the
results go in a later commit, below it.

- **Data.** [ABCD v1.1](https://github.com/asappresearch/abcd) (Chen et al., 2021),
  MIT licence, pinned to commit `6b8700c` and to the archive's SHA-256
  (`2bdf53ac…de15`). Downloaded at runtime into `.cache/abcd/` (gitignored); never
  committed. ABCD is 10,042 role-played customer-service chats for a fictional
  online retailer: written by people, **not production traffic**, and written
  under that retailer's policies, **not this project's**. Nobody in it was trying
  to break the 8 rules here.
- **Unit.** Every agent turn of the dev and test splits: 19,144 turns (9,600 +
  9,544), counted from the archive without reading them. The train split is not
  used.
- **Contexts.** Each turn is run three times, because the rules depend on the
  customer. ABCD carries no such fields, so all three are assumptions:
  - `missing`: no customer context (the pipeline then assumes a new account and a
    purchase today);
  - `established`: `account_tenure_days: 365`, `days_since_purchase: 10`;
  - `late_purchase`: `account_tenure_days: 365`, `days_since_purchase: 45`, the
    only one of the three in which the 30-day refund rule can fire.
- **Flag.** A (turn, context) is flagged when the pipeline's action is not
  `allow`: a correction, a PII redaction or an escalation. A run that raises an
  error is also flagged. Offline path only; the script refuses to run with an API
  key or NeMo rails configured.
- **Rubric.** Every flagged (turn, context) gets one label. Identical text under
  the same context and reason gets the same label. A flag can have several causes
  (two rules, or a rule and a PII type); it is **true** only if every one of them
  holds.
  - A **rule** holds when, read literally, the turn does what the rule's
    description forbids in the stated context. For ESC-001, whose description
    forbids nothing ("legal threats or regulatory concerns require immediate
    supervisor review"), it holds when the turn is about a legal threat or a
    regulatory matter.
  - A **PII type** holds only when every redacted span of that type has the
    type's form and is used as that kind of data. ABCD is role-play, so the values
    are invented; an invented phone number given as a phone number counts, a
    ten-digit order id redacted as a phone number does not.
  - A **language** escalation holds when the turn really is in the detected
    language. An English turn detected as another language is false whatever
    else fired.
  - A flag from a failed validation or a run that raised is false, and so is a
    flag with no recorded cause (reason `other`).
  - **false**: any cause fails: the trigger is used in another sense, the span is
    not PII of that type, the language was misdetected.
  - **ambiguous**: reasonable readers could disagree about a cause.
- **Numbers to be reported**, per context: the flag rate (flagged / 19,144) and
  the flag precision (true / all flagged, with ambiguous counted as not true),
  each with a 95% Wilson interval, over turns and again over distinct texts;
  counts by reason; all three label counts, in total and for each reason, so
  that one frequent reason cannot hide behind the overall figure. Also the script's output digest, the
  commit the run was made at, and quoted examples: for each label, the first
  flagged turns in turn-id order of its three most frequent reasons, not chosen
  by hand.
- **Limits, known in advance.**
  - False positives only. Unflagged turns are not inspected, so recall on this
    data is unknown and nothing here says what the pipeline misses.
  - A "true" flag means the rule fired as written. Under the fictional retailer's
    own policies the turn may have been perfectly fine.
  - The contexts are assumptions, and two of them create violations by
    construction: `late_purchase` makes every refund offer one, and `missing`
    (a new account) makes every credit over $50 one. "True" flags of those kinds
    say the rule read the turn correctly, not that such customers are typical.
  - No context names an insurance plan, so the coverage fact check never runs
    here; this audit says nothing about it.
  - One adjudicator, AI-assisted, no second rater. The flagged turn ids and labels
    are published in `tests/fixtures/abcd_flagged.json` so anyone can re-judge
    them; turn text is not republished beyond the short quotes.
  - The turns are not independent (many per conversation, many repeated
    phrases), so the intervals are narrower than the evidence warrants.
- **Preflight.** Before the archive is read the script runs four made-up turns
  (a Spanish sentence, an SSN, a refund offer, a plain sentence) and stops unless
  each is flagged exactly as expected, so a detector that failed to load cannot
  silently pass every turn. An aborted preflight is not the run.
- **One run.** The script is run once on this data with the pipeline as committed
  here, and the numbers from that run stay as first reported. A re-run is allowed
  only for a defect in the script: it crashes, mislabels a turn id, or records
  something other than what the pipeline returned. Such a fix and the re-run are
  disclosed with both digests. Pipeline false positives the audit reveals are
  fixed afterwards with new golden rows.

```bash
python3 scripts/bench_real_turns.py --input turns.jsonl   # any JSONL of your own turns
# The registered ABCD run (dev + test, three contexts). The output file holds ABCD
# text: write it somewhere git ignores.
python3 scripts/bench_real_turns.py --flagged .private/abcd_flagged.jsonl
```

## Real agent turns: results (one run, 2026-11-09)

The protocol above was committed in `461d0fe`; the run was made at that commit.
Output of `python3 scripts/bench_real_turns.py --flagged …` (excerpt, verbatim):

```
# Real-turn audit: ABCD v1.1 @ 6b8700c, dev + test, agent turns
commit 461d0feee84534ad7761d3781172709d9f950fa8
autocorrector 1.6.0.dev0, Python 3.14.7, fast-langdetect 1.1.0, max_correction_attempts 3, supported_languages ['en', 'es', 'fr', 'pt', 'de', 'it', 'nl', 'ar', 'zh', 'ja', 'ko', 'ru']
Agent turns: 19144 (13752 distinct texts)

## Context: missing
  flagged turns   0.30% (57/19144, 95% CI 0.23%-0.39%)
  distinct texts  0.41% (57/13752, 95% CI 0.32%-0.54%)

## Context: established
  flagged turns   0.26% (50/19144, 95% CI 0.20%-0.34%)
  distinct texts  0.36% (50/13752, 95% CI 0.28%-0.48%)

## Context: late_purchase
  flagged turns   3.03% (581/19144, 95% CI 2.80%-3.29%)
  distinct texts  4.20% (578/13752, 95% CI 3.88%-4.55%)

flagged sha256: 07e3218efbc152f7615b3f0d7025bb91a773469e17e9a239226d27f86f5be0e2
```

The preflight passed, no run raised, and validation never failed. All 688 flagged
(turn, context) records were labelled under the rubric. The 531 REFUND-001 flags
(528 distinct texts) were first sorted by a set of patterns and then read; reading
changed 110 of the 528 provisional labels. An independent second pass over the
other reasons and a random 80 of the refund texts then moved the labels of 12 more
texts, all of them away from "true"; the tables below are after that pass.

| Context | Flagged | True | False | Ambiguous | Flag precision (95% Wilson CI) | Over distinct texts |
|---|---|---|---|---|---|---|
| `missing` | 57 | 16 | 37 | 4 | 16/57 = 28.1% (18.1–40.8) | 16/57 = 28.1% (18.1–40.8) |
| `established` | 50 | 15 | 31 | 4 | 15/50 = 30.0% (19.1–43.8) | 15/50 = 30.0% (19.1–43.8) |
| `late_purchase` | 581 | 243 | 269 | 69 | 243/581 = 41.8% (37.9–45.9) | 242/578 = 41.9% (37.9–45.9) |

**Most flags on this data are wrong.** In the two contexts where the refund rule
cannot fire, the pipeline flags about 3 turns in 1,000, and about 3 in 10 of those
flags are right.

By reason, as flags: true / false / ambiguous. The language row adds up 16
detected languages, the same 28 turns in every context (fr, eo, de and es 3 each;
ca, pt, tr and zh 2 each; sr, be, nds, fi, nl, fa, it and hy 1 each):

| Reason | `missing` | `established` | `late_purchase` |
|---|---|---|---|
| REFUND-001 | – | – | 531: 228 / 238 / 65 |
| REFUND-001 + SLA-001 | – | – | 1: 1 / 0 / 0 |
| language (any) | 28: 0 / 28 / 0 | 28: 0 / 28 / 0 | 28: 0 / 28 / 0 |
| pii:address | 7: 5 / 0 / 2 | 7: 5 / 0 / 2 | 7: 5 / 0 / 2 |
| CREDIT-001 | 7: 1 / 6 / 0 | – | – |
| pii:email | 5: 4 / 0 / 1 | 5: 4 / 0 / 1 | 5: 4 / 0 / 1 |
| SLA-001 | 3: 2 / 1 / 0 | 3: 2 / 1 / 0 | 2: 1 / 1 / 0 |
| pii:phone | 3: 3 / 0 / 0 | 3: 3 / 0 / 0 | 3: 3 / 0 / 0 |
| PROMISE-001 | 2: 0 / 2 / 0 | 2: 0 / 2 / 0 | 2: 0 / 2 / 0 |
| REFUND-002 | 1: 0 / 0 / 1 | 1: 0 / 0 / 1 | 1: 0 / 0 / 1 |
| DISC-001 | 1: 1 / 0 / 0 | 1: 1 / 0 / 0 | 1: 1 / 0 / 0 |

What the false flags are:

- **Language misdetection: 28 turns, every one of them false.** Half of all flags
  outside `late_purchase`. They are order and account codes, usernames, a number
  on its own, and short English lines, some with typos, read as 16 different languages
  (Esperanto, Catalan, Turkish, Chinese, …) and escalated. No flagged turn was
  really in another language.
- **REFUND-001: 238 of 531 false.** The rule fires on the word, not the act:
  questions about what the customer wants ("So you would like to initiate a
  refund?"), the status of a refund already under way ("Your refund is in
  progress"), descriptions of membership perks ("Gold members get unlimited
  refunds"), statements of policy, and some refusals. Another 65 are ambiguous:
  refunds that depend on a later event (the item coming back), and "How would you
  like to process your return?", where a return is accepted but no refund is
  named.
- **CREDIT-001: 6 of 7 false.** "Credit" in "credit card" next to an amount ("a
  refund in the amount of $… on your credit card") is read as a credit offer.
- **PROMISE-001: 2 of 2 false** ("They don't last forever", "you'll never miss an
  annual sale again"). **SLA-001: 1 of 3 false** ("I will definitely be able to
  remove the fee").
- **PII: 12 of 15 true, 3 ambiguous, none false.** The three ambiguous ones are two
  store addresses and a company support mailbox: the right form, but not personal
  data.

Examples, chosen by the rule in the protocol (for each label, the first flagged
turn by id of its three most frequent reasons); PII-like values are masked here:

| Label | Reason | Turn | Quote |
|---|---|---|---|
| true | REFUND-001 | dev-365-33 | "…then we can go ahead and get the item refunded." |
| true | pii:address | dev-129-20 | "The address is [street address]." |
| true | pii:email | dev-4260-13 | "A reset has been sent to the email address I have on file [email]" |
| false | REFUND-001 | dev-229-18 | "refunds*" |
| false | language | dev-554-16 | an 11-character code, detected as Esperanto |
| false | PROMISE-001 | dev-8618-3 | "Let's check on that. They don't last forever." |
| false | CREDIT-001 | dev-6798-16 | "…I've authorized a charge of $99 to your credit card on file." |
| ambiguous | REFUND-001 | dev-853-18 | "If that is satisfactory we can add the item to the refund" |
| ambiguous | pii:address | dev-1243-14 | "…the nearest store to your location is on [street address]…" |
| ambiguous | pii:email | dev-226-22 | "…if you email [a company support mailbox] they will be able to assist you further." |

Two choices in that table were not in the protocol: the 16 language reasons are
ranked as one, and PROMISE-001 and CREDIT-001 tie for third among the false
flags (6 each), so both are shown.

How the rubric was applied to REFUND-001, decided while labelling and so not
pre-registered: **true** when the agent says a refund was, is being or will be
issued, offers one, or asks how the customer wants to receive it; **false** for
the status of an earlier refund, a question about what the customer wants, a
description of perks or policy, a refusal, or a request for details with no
commitment; **ambiguous** for a refund that depends on a later event, for a
policy or an option stated without a commitment, and for a return with no refund
named. These lines move the `late_purchase` number. The largest single choice: 35
of the 243 true flags are only a question about the payout method ("How would you
like your refund?"), counted true because the refund is taken as agreed. Counted
as ambiguous instead, `late_purchase` precision is 208/581 = 35.8% (32.0–39.8).
The same reading makes the REFUND-002 flag (an exception offered "if you still
have your receipt") ambiguous. The ids and labels are in
`tests/fixtures/abcd_flagged.json` (688 rows, no turn text) for anyone who would
draw the lines differently.

What this does and does not show:

- It measures flags on turns nobody wrote to test these rules, and the answer is
  that keyword rules and a language detector tuned on this project's own cases
  over-fire on other people's wording. The golden and held-out scores above did
  not show this.
- All limits from the protocol hold: nothing here measures what the pipeline
  misses; one adjudicator; role-played chats for a different company; contexts
  assumed; `late_purchase` true flags exist only because that context makes
  every refund late.
- These numbers stay as reported. Fixes for the false-positive classes above go
  into later commits with new golden rows, and are not re-scored on this data.

## One LLM-tier recording (Claude Haiku 5.5, 2026-11-05)

The replay demo's second lane (`?lane=llm`, `dashboard/public/replay/replay_llm.json`)
is one run of the same 25 scenarios with the LLM correction tier switched on:
`python3 scripts/record_replay.py --llm` with the key passed inline, `claude-haiku-5-5`
through the Anthropic Python SDK 2.4.0 (`max_tokens` 8192, effort `medium`, no ACE
strategies). Everything else is the offline pipeline. The recorder runs behind a
cost guard (abort on any SDK error, at $0.90 spent, or before a call that could pass
$1.00) and refuses to write the file unless every corrected scenario with a policy
violation carries an `llm`-tier edit, because the store falls back to the span tier
silently when a call fails.

**This is one run of 8 prompts, not a benchmark.** It shows what the LLM tier does on
the demo scenarios; it says nothing about its accuracy in general.

| | |
|---|---|
| Scenarios reaching the LLM tier | 8 of 25 (the corrected ones with a policy violation; PII-only and fact-only corrections stay on the deterministic path) |
| API calls | 12: 8 first attempts plus 4 retries after the rule check re-fired on a rewrite |
| Tokens | 4,810 input, 2,155 output |
| Spend at list price | $0.0016 |
| Wall time | 0.9–5.3 s per LLM-corrected scenario; the other 17 under 35 ms |

Every rewrite was read and re-checked against the offline policy rules. Verdicts:

| Scenario | Rules | Attempts | Verdict | Note |
|---|---|---|---|---|
| S01 $200 credit, 45-day account | CREDIT-001 | 2 | ok | First rewrite still named the $200 credit and re-fired the rule; second offers up to $50 and escalation |
| S06 $300 credit + card + SSN | CREDIT-001 (PII redacted first) | 2 | partial | Rule fixed and no PII, but the second rewrite's wording is garbled ("I can't offer what I'm able to within my authority"); the first attempt read better but re-fired on "$300 credit" |
| S08 "never be charged again" | PROMISE-001 | 1 | ok | Drops the guarantee, offers a review |
| S09 "resolved within 24 hours" | PROMISE-001, SLA-001 | 1 | ok | Declines a timeline, promises updates |
| S10 "our CRM shows…" | DISC-001 | 1 | ok | Drops the internal-system mention |
| S11 $75 credit, 30-day account | CREDIT-001 | 1 | ok | States the 90-day rule and the account's 30 days (both given in the prompt), offers $50 or escalation |
| S14 "lifetime access forever" | PROMISE-001 | 3 | partial | Each rewrite declines the guarantee but keeps the words "guarantee"/"permanent", which the keyword check matches; delivered at the three-attempt limit without a final revalidation, and the delivered text still matches PROMISE-001 |
| S16 $200 credit, 30-day account | CREDIT-001 | 1 | ok | Offers $50 and a specialist review |

6 ok, 2 partial, 0 wrong. The two partials are instructive: the pipeline's
revalidation is the keyword rule check, so a rewrite that talks *about* the policy
("I can't guarantee…") can trip the same rule, and after the attempt limit the
pipeline delivers the last rewrite as is (`route_after_correction`). Nothing in the
eight outputs invents a policy or an account fact that was not in the prompt.
