# Architecture

How a reply moves through the system, what each part does when something fails,
and how the replay demo is built. Paths are relative to `src/autocorrector/`.

## Pipeline

`orchestrator/pipeline.py` builds a LangGraph state graph (`build_pipeline`);
`run_autocorrection(text, customer_context, ...)` runs it and returns a
`CorrectionResult`.

| Node | What it does | When it fails or is off |
|---|---|---|
| `detect_and_translate` | fast-langdetect (`multilingual/detector.py`); non-English text is translated to English by the LLM (`multilingual/translator.py`) | No key, unsupported language or failed translation sets `translation_failed`; the reply is escalated after PII redaction, never allowed |
| `nemo_input_check` | NeMo Guardrails input rails (jailbreak, topic) | Off by default; not installed or erroring = pass through (fail-open). A block escalates |
| `classify_intent` | Rule-based classifier (`intent_classifier/classifier.py`), 9 intents, regex patterns plus keyword scores; below the 0.3 floor the intent is `general` | — |
| `guardrails_check` | PII rail (`guardrails/validators.py`, 10 regex types) through a Guardrails AI guard built with telemetry off (`guardrails/telemetry.py`); matched PII is replaced with tokens and the redacted text is used from here on | — |
| `validate` | Policy rules and coverage facts from the knowledge-graph store | If the policy check or the fact check raises, it is logged, `validation-unavailable` is added to the guardrails applied and the reply is escalated (fail-closed); what the other check found is kept |
| `correct` | Fact edits at the claimed spans, then the correction tiers | The policy rewrite raises → logged; the text keeps only its fact edits |
| `allow` / `deliver` | Return the text (translated back if needed) | — |
| `escalate` | Returns no text: held for a supervisor; runs the PII rail first if an early exit (a NeMo block) skipped it | — |

Routing after `validate` (`route_after_validation`): translation failed or a check
raised → escalate; compliant → allow; any critical violation → escalate; otherwise correct. After
`correct`, the corrected text goes back through `validate`, so it can be allowed,
corrected again or escalated. After `AUTOCORRECTOR_MAX_CORRECTION_ATTEMPTS` rounds
(default 3) the text is delivered without another check. A changed text is reported
as CORRECT even when it leaves through `allow` (for example PII-only redaction).

## Policy store

`knowledge_graph/store.py` (`KnowledgeGraphStore`) holds the rules and plan facts.
Without Neo4j, which is the default, both are in memory:

- 8 rules, grouped by domain: CREDIT-001 and CREDIT-002 (`financial_compensation`),
  ESC-001 (`escalation_request`), REFUND-001 and REFUND-002 (`refund_processing`),
  and the universal PROMISE-001, SLA-001, DISC-001. Every rule is checked on every
  reply by its own triggers; the classified intent only puts its domain's rules
  first. Keywords match at a word start ("sue" does not match "issue"). Each rule
  carries a severity, keywords, conditions on the customer context (for example
  account tenure) and its source document and section. A missing context field
  counts as 0.
- Coverage facts for two plans (Standard 2024, Premium 2024), each limit with its
  source document and page.

Fact checks use `knowledge_graph/claims.py`: every dollar amount in a clause is
assigned to the nearest coverage-category mention, amounts typed as other money
("a $45 copay", "the bill was $1,200") are not coverage claims, and ambiguous ones
count only with limit language ("up to", "capped", "maximum"). Each fact error
records the character span of its claim.

The credit-threshold rules leave out an amount only when it is verifiably a
coverage statement (assigned to a category, equal to that plan's limit, in a
sentence shaped like a statement of coverage with no transfer verb and no rule
keyword). Everything else counts as an offer. This fails closed: an unclear amount
is treated as money being offered.

With Neo4j connected (`AUTOCORRECTOR_NEO4J_*`, `scripts/seed_neo4j.py`), rules,
facts and correction templates are read with parameterized Cypher. The seed
(`knowledge_graph/schema.py`) is generated from the in-memory rules, templates and
coverage facts, so both paths check the same eight rules and both plans (the rule
query reads every active rule and only orders by intent; the fact query reads every
plan, so an amount attributed to another plan is checked against that plan). The
span tier and the coverage exemption still use the in-memory rules and facts.

## Correction tiers

`KnowledgeGraphStore.generate_compliant_alternative_with_edits`; the first tier that
applies wins:

1. **Neo4j template**, only when connected; cited like tier 4 (every rule, the
   highest-severity rule's template).
2. **LLM rewrite**, only with an Anthropic key (`AUTOCORRECTOR_CORRECTION_LLM`). The
   response parser keeps only text after any thinking blocks and accepts only the
   `end_turn` and `stop_sequence` stop reasons; anything else falls through.
3. **Span rewrite** (`knowledge_graph/rewriter.py`): only the flagged sentences
   change. CREDIT-001 caps the amount at $50 and offers supervisor review;
   REFUND-001, PROMISE-001 and SLA-001 replace the sentence with fixed wording;
   DISC-001 drops it. Fixes for several rules in one sentence are composed into one
   replacement. A rule counts as resolved only if the rewritten text no longer
   triggers it.
4. **Whole-message template** for any rule the span tier left unresolved. The
   template replaces the whole reply, including any span edits, so its one edit
   cites every rule that fired (ids and sources joined, highest severity first); the
   wording is the template of the highest-severity unresolved rule that has one.

Policy and fact changes are recorded as `CorrectionEdit`s (`models.py`): `rule_id`,
`before`, `after`, `start`, `end`, `source_document`, `source_section`, `tier`. Fact
corrections are edits with rule `FACT-CHECK` (the source is the policy page). Tiers 1,
2 and 4 produce one whole-message edit citing one rule (the LLM tier's edit lists all
rule ids but no source). PII redaction is not an edit: the types found are in `pii_types`.
Escalations carry no edits and no text.

## Result

`CorrectionResult` (`models.py`) carries the routed action, the original and
corrected text, the edits (empty on escalation), the validation (violations, fact
errors, severity), PII types, intent, detected language, guardrails applied and an
explanation (`explainability/engine.py`). On escalation nothing is sent; the
corrected-text field holds the PII-redacted input, also after a NeMo block. The confidence
is a fixed heuristic: `AUTOCORRECTOR_CORRECTION_CONFIDENCE_SCORE` (0.92) when the
text changed, otherwise 1.0. It is not a calibrated probability.

## Audit store

`audit/store.py`, SQLite through aiosqlite:

- `correction_events`: append-only, one row per logged reply.
- `supervisor_decisions`: approve/reject decisions, kept separate so events are never
  updated.
- `correction_strategies`: lessons from the ACE-style supervisor-feedback loop
  (after Agentic Context Engineering, Zhang et al., 2025, arXiv 2510.04618;
  `ace/reflector.py` turns a rejected correction into a lesson with the LLM;
  `ace/curator.py` deduplicates and scores them). The top lessons for an intent are
  passed to the LLM tier only.

Events are written by the callers: the dashboard API and the MCP
`log_correction_event` tool. Both store the original and corrected text as the PII
rail redacts them (the dashboard redacts the untranslated input itself, since the
pipeline's flag comes from the translated text), so the rows the dashboard serves on
`/api/events`, `/api/queue` and the WebSocket (none of which the dashboard API key
protects) carry no PII the regex rail detects. Rows written before v1.1 are unchanged.

## MCP server

`mcp_server/server.py` (FastMCP), started by the `autocorrector` console script:
stdio with `--stdio`, otherwise streamable HTTP on `AUTOCORRECTOR_PORT` with an
optional API key (`AUTOCORRECTOR_MCP_API_KEY`, compared in constant time).

- Tools: `validate_action` (PII rail, then policy and fact checks on the redacted
  text; returns `pii_types` and `redacted_text`), `get_correction` (PII rail, fact
  corrections and the correction tiers), `log_correction_event` (stores redacted
  text).
- Resources: `rules://compliance/{domain}`, `metrics://corrections/summary`,
  `policies://active`.

The tools do not run the LangGraph pipeline: there is no translation (non-English
text always escalates) and no revalidation.
`scripts/test_mcp_client.py` starts the server over stdio, calls each tool and reads
the rules resource.

## Dashboard

`dashboard/api.py` is a FastAPI app: REST endpoints for metrics, events, the
supervisor queue (escalations plus high and critical violations, newest first, each
labelled with its highest severity), approve/reject, export, analytics and demo runs,
and a WebSocket that pushes new events. Rate limiting, and an optional API key on the
approve/reject, export and demo endpoints.

The React app in `dashboard/` builds in one of two modes (`VITE_REPLAY`):

- **Live**: talks to the FastAPI backend.
- **Replay**: a static site. `scripts/record_replay.py` runs 25 scenarios through
  the offline pipeline and writes `dashboard/public/replay/replay.json`; the data
  hooks are swapped for replay hooks that read only that file. Pure functions in
  `dashboard/src/replay/derive.ts` compute the dashboard's numbers from it with the
  backend's formulas. The recorder refuses to run with an API key set or NeMo on,
  and `--check` (run in CI) fails if the file no longer matches the pipeline.
  `.github/workflows/pages.yml` builds this mode and publishes it to GitHub Pages.

## Evaluation

`evaluation/`: `dataset.py` (421 golden cases, in-sample), `heldout.py` (60 blind
cases, run once), `scorer.py` (action accuracy, precision/recall, F0.5, secondary
checks, 95% Wilson intervals, latency percentiles), `runner.py`, `registry.py`
(`--dataset golden|heldout`), `llm_judge.py` (optional, needs a key).
`scripts/bench_pii.py` scores the PII rail against an external span-labelled
benchmark using `find_pii_spans`, an exact replay of the validator that returns
character spans. Results and protocol: [evaluation.md](evaluation.md).

## Failure behaviour, in one place

| Situation | Behaviour |
|---|---|
| NeMo missing or erroring | pass through |
| Policy or fact check raises | logged; all of its rules skipped, may allow |
| No translation for non-English text | escalate (PII still redacted) |
| Policy rewrite raises | logged; text keeps only its fact edits |
| LLM refuses, truncates or stops early | next tier |
| Unclear amount next to a credit | counted as an offer (may escalate) |
| Critical rule | escalate, nothing sent |
