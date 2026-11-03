# Case study: what broke, and what it taught

This project was built as a demo, then reworked into something that could be
checked. The rework started with an audit of the failure paths and ended with a blind
evaluation. This page lists the failures in roughly the order they were found:
what broke, how it was found, the fix, and the principle behind the fix. Commit ids
point at the full write-up.

Code review during the rework was done in rounds by a fresh-context AI reviewer
agent, and most logic fixes were checked by mutation: revert the fix on its own and
confirm a test fails. The numbers come from [evaluation.md](evaluation.md).

## 1. Non-English text was silently allowed

- **Broke:** with no API key, translation failed open and returned the original
  text. The English-only rules then matched nothing, so a Spanish reply offering a
  credit came out as ALLOW, with no policy check at all.
- **Found:** reading the pipeline's failure paths during the audit; the fix started
  from a failing test (Spanish credit offer, no key, expect ESCALATE).
- **Fix:** the translator reports whether it succeeded. If it did not, the pipeline
  still redacts PII and validates, then escalates with the tag
  `translation-unavailable`. The MCP `validate_action` tool also escalates
  non-English text. (`7ee1a40`)
- **Principle:** fail-open is right for an optional safety layer, but a failure that
  skips the policy check should not end in "allowed". One such path remains open: if
  the policy or fact check itself raises an exception, its rules are skipped and the
  reply can be allowed. It is listed in the README's limitations.

## 2. The demo called an escalation a correction

- **Broke:** the step-by-step demo printed `Action: CORRECT (workflow continues)` for
  any violation, including a critical $1,000 credit that the real pipeline escalates.
  The footer also printed hardcoded test and eval counts.
- **Found:** the audit: the action line was a hardcoded print, not the routed
  result.
- **Fix:** the demo prints the action the pipeline actually routed, the corrected
  text and each edit, and computes its footer counts. (`ae66062`, `af4a8f2`)
- **Principle:** a demo is a claim. Print what the system did, never what it is
  supposed to do.

## 3. A refused LLM answer could ship

- **Broke:** responses were read as `content[0].text`, which breaks once a response
  starts with a thinking block. With server-side refusal fallbacks there was a worse
  case: text streamed by the model that declined could be joined to the fallback
  model's answer and delivered.
- **Found:** review of the LLM parsing change (a blocker).
- **Fix:** one helper reads the answer text after any thinking blocks, discards the
  whole answer if the declining model produced any text before a fallback seam, and
  accepts only the `end_turn` and `stop_sequence` stop reasons. Everything else falls through to the deterministic tiers. Tests use real
  SDK message objects at every call site. (`41db93c`)
- **Principle:** allowlist the success cases. An unknown stop reason is not success.

## 4. A dependency sent telemetry by default

- **Broke:** with no config file present, Guardrails AI enables metrics and exports
  trace spans to the vendor's collector, so PII checks were traced by default.
- **Found:** the audit, by reading the library's settings defaults.
- **Fix:** guards are built through one helper that turns telemetry off before the
  validator is created and drops the process-wide tracer, so a guard built elsewhere
  cannot turn it back on. Opt-in via `AUTOCORRECTOR_GUARDRAILS_TELEMETRY`. Checked
  by running the whole suite under a tracer spy: 0 spans. (`7ee1a40`)
- **Principle:** check what your dependencies send out, and prove it with a test.

## 5. Fact checks compared every amount with every limit

- **Broke:** each dollar amount was compared with the limit of every coverage
  category mentioned anywhere in the text, so "Your $40 specialist copay and $2,500
  emergency dental limit are unchanged." raised two false errors. The correction step
  then used `str.replace`, rewriting every copy of the "wrong" figure, including
  correct ones.
- **Found:** the audit; the fix then took five review rounds, each finding phrasings
  the previous version misread.
- **Fix:** claim assignment (`knowledge_graph/claims.py`). Each amount in a clause
  belongs to its nearest category mention; amounts typed as other money (copays,
  bills, credits) are not coverage claims; ambiguous ones count only with limit
  language ("up to", "capped", "maximum"). Fact errors carry the claim's character
  span, and corrections replace only that span. (`ed1588a`)
- **Principle:** when a heuristic keeps growing special cases, require positive
  evidence (a limit cue) before acting, and edit only what you can locate exactly.
  Remaining misses are listed in the README.

## 6. Credit limits counted coverage figures as offers

- **Broke:** the credit-threshold rules counted every dollar amount, so the compliant
  "Your emergency dental is $2,500. I can offer you a $50 credit." escalated as a
  critical over-limit credit.
- **Found:** span rewrites flipped a golden case from CORRECT to ESCALATE. The first
  fix then let a "$600 emergency dental credit" through, and it took four review
  rounds to close the gaps.
- **Fix:** an amount is left out only when it is verifiably a coverage statement:
  assigned to a category, equal to that plan's true limit, in a sentence shaped like
  a coverage statement, with no transfer verb and no credit keyword. Everything else
  still counts. (`af4a8f2`)
- **Principle:** fail closed. An exemption must be proved, and a missed exemption
  only sends a reply to a supervisor.

## 7. Corrections replaced the whole message

- **Broke:** offline, every correction replaced the agent's entire reply with a canned
  per-rule template, losing everything else the agent had said.
- **Found:** the audit, tracing which correction tier actually runs without a key
  or Neo4j: only the template tier.
- **Fix:** a span tier rewrites only the flagged sentences and records each change as
  an edit with the rule, before/after text, offsets and policy section. Rules hitting
  the same sentence are composed so one fix cannot undo another. A rule counts as
  fixed only if the rewritten text no longer triggers it; otherwise the template is
  still the fallback. (`af4a8f2`)
- **Principle:** make the smallest change that fixes the problem, and make every
  change explain itself.

## 8. The supervisor queue labelled critical items "high"

- **Broke:** each queue item's severity was `max()` over its violations' severity
  strings, and alphabetically "high" beats "critical", so an item with both showed
  as "high".
- **Found:** writing the replay dashboard's queue logic to match the backend; the
  test was written first.
- **Fix:** take the highest by the severity enum's order. (`c9ce8f6`)
- **Principle:** never compare labels as strings when they have an order.

## 9. Replay charts never finished animating

- **Broke:** the replay context handed React a new value object on every clock tick,
  so every chart re-rendered and restarted its animation.
- **Found:** browser testing of the local replay build.
- **Fix:** the context value is memoized and changes only when something visible
  changes. (`c9ce8f6`)
- **Principle:** keep the identity of shared state stable; check UI work by looking at
  it.

## 10. The PII span finder was not quite the validator

- **Broke:** the external benchmark needs character spans, so a span finder was
  written next to the redacting validator. Review produced an input where the two
  disagreed: the validator substitutes each pattern on the already-redacted text, and
  the finder did not.
- **Found:** a review counterexample. (One other counterexample from the same review
  did not reproduce; each was checked before anything was changed.)
- **Fix:** the finder replays the validator exactly (same patterns, same order, same
  gate on the original text, substitution on the redacted text) and maps spans back.
  Property tests, a 3,000-string fuzz test and a per-row check on the benchmark (0 of
  5,586 rows differ) confirm it. (`9118f7f`)
- **Principle:** a measurement tool must measure the real thing. Mirror it exactly,
  or don't call the result its score.

## 11. The blind held-out set found real bugs

- **What happened:** 60 cases were written from the policy text without running the
  pipeline, committed and pushed (`fa71907`), then run once: 53/60 = 88.3% (95% CI
  77.8–94.2), against 410/410 on the in-sample golden set.
- **What it found:** 5 bugs, 1 ambiguous case and 1 deliberate fail-closed
  limitation. Four of the misses involve domain gating: credit, refund and
  escalation rules are checked only for the classified intent, and a diluted keyword
  score sends a refund reply to `general`. Three of those four are escalations that
  were allowed through. One miss is a harmful correction: a
  correct comparison with another plan's limit was "fixed" into a false statement.
- **What was done:** reported as-is, every miss classified (`37c8c79`). The cases are
  not edited and the set is not re-run; fixes go into new golden rows, and any
  post-fix accuracy claim needs a new blind set.
- **Principle:** a perfect in-sample score is a regression suite, not evidence. Write
  the test before you look, run it once, and publish what it says.
