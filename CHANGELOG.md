# Changelog

One entry per release tag (`vX.Y`; the version's patch number stays 0); each line
under a release is that tag's own message.
The version number lives in `src/autocorrector/__init__.py` (`__version__`), which
the package metadata, the dashboard API and `/health` read.

## [Unreleased]

Known weaknesses found since v1.5, each fixed here:

- A rule keyword matched any word that started with it, and a keyword that is also a
  name: "Thanks, Sue. Your order shipped this morning." and "Those suede boots are
  back in stock." were escalated as legal threats (ESC-001); "creditor", "always
  willing", "you'll nevertheless" and "between you and Medicare" triggered other
  rules. Keywords now match the word or phrase itself plus regular inflections.
  "Sue" is read as a name only in a greeting ("Thanks, Sue.", "Hi Sue, …");
  anywhere else it still escalates, so "Sue will call you back" is still held. Wordings the prefix match used to catch are now keywords of their own
  ("refundable", "by next weekend", "offer your", "internal tooling", "internal
  processing", "accept/process the returned"); other derived forms no longer
  match. `scripts/audit_keywords.py` lists what each keyword matches in a
  dictionary. v1.5 gets 428 of the first 434 cases in golden set v3.4 right; the 13
  new rows (GAP-012 to GAP-024) are in-sample regression rows. Coverage categories
  in fact checks are matched the same way ("specialist copayment" is now an
  alias so it is still checked). A seeded Neo4j
  database needs `scripts/seed_neo4j.py` run again to get the new keywords.
- The email pattern was quadratic on long text with dots or dashes and no "@": a
  40,000-character reply took about 3.7 s in the PII rail, now about 5 ms. A
  slowdown, not a hang. A redacted address now includes punctuation attached to
  its front (".john@example.com"). On the external PII benchmark the English
  output is identical; over all languages one French address is now redacted in
  part instead of missed (two more predictions, one of them correct), which leaves
  every headline percentage as it was.
- `docs/case-study.md` quoted the golden set as 410/410 without saying that was the
  set at the time of the held-out run; a test now checks the places that state the
  current golden-set size against the dataset.
- The golden suite noticed only 43 of 104 single changes to the rules: most rows
  stack several keywords of a rule, and no row sat next to the 30-day refund
  boundary. `scripts/mutate_policy.py` measures this; 56 rows (PIN-001 to PIN-056),
  one per keyword and boundary, written against the survivors, bring it to 98 of
  104. The six left are five severity changes the suite cannot see and one
  redundant keyword (docs/evaluation.md).

False-positive classes found by the real-turn audit (below), each fixed with new
golden rows (RT-001 onwards, written for this project, not copied from the audited
turns). The audit is not re-run; its numbers predate these fixes:

- Language detection escalated order codes, bare numbers, usernames and typo-ridden
  English as 16 different languages. Short plain-ASCII text is now English unless
  it carries an amount (a "$" or "%", or a number standing on its own next to two
  or more words), so a digit inside a code or username no longer sends it to the
  detector; text with no letters is English; plain-ASCII text is never a language
  written in another script; and plain-ASCII text with two or more different
  common English words that Spanish, French, Portuguese, German, Italian and
  Dutch neither share nor borrow, making up a fifth of its words, is English
  unless it also has two function words of one of those languages. Curly quotes,
  dashes and an ellipsis no longer count as foreign letters. What this costs and
  what it leaves:
  - New loss: a foreign line of under four unaccented words with a number but
    one word ("Rabatt 20"), or with a curly apostrophe or dash ("J’ai
    rembourse", "Danke – erledigt"), used to reach the detector and is now
    checked as English, like any short unaccented line ("Gracias por esperar.")
    before it.
  - New loss: a foreign line that carries English words and fewer than two of
    its own function words ("Garantiert: your new Erstattung today") is checked
    as English and passes.
  - Still escalated: typo-ridden English with fewer than two of the English
    words ("ok im looking now", "im sorry abot that"), and a line of several
    codes with four or more letter runs ("order C3EDQM ref EVXRVC").
- "credit card" counted as a mention of a credit, so "The $89 renewal went through on
  your credit card this morning" was rewritten as an over-limit credit offer for
  a new account (CREDIT-001). The keyword "credit" no longer matches in "credit
  card(s)", "credit-card" or "credit or debit card"; a credit named elsewhere in
  the reply still does, and so does crediting a card, known by the word before
  it ("I'll credit card ending 4417", "Let me credit card ending ...") or by
  what follows ("gladly credit card ending 4417 with $90") (RT-009 to RT-013). What this gives up: "As an apology I'm
  sending $600 to your credit card" used to escalate only because of that
  misreading and is now allowed; no keyword names a payment that is not called a
  credit. A capped credit now reads "a $50 credit" where the reply said "an $80
  credit" (it kept "an").
- The PII rail redacted "Your refund of $72 is on its way back to your credit
  card" as a street address ("72 is on its way"; "Way" is a street type and the
  pattern ignores case). A street-name word can no longer be a function word or
  a unit of time or count; lowercase addresses ("123 main st") are still found
  (RT-037). On the external PII benchmark this removes twelve address
  predictions, two of them correct: English precision 63.6% to 63.8%
  (2053/3218), recall 2062 to 2060 of 4,432 (still 46.5%).
- REFUND-001 fired on the word "refund", not on granting one: 45 days after
  purchase, "So you'd like a refund for the headphones?", "That refund is still
  pending", "Premium members get unlimited refunds" and "I'm sorry, I can't refund
  this order" were all rewritten. A reply is now excused when every part of it is
  a refusal, a question that neither proposes the refund nor takes it as settled,
  or a neutral remark (a greeting, thanks, an apology, looking something up), so
  the first and the last of those four are allowed. Everything else fires,
  including a question that takes the refund as settled or proposes it ("How
  should we send your refund, to your card or as store credit?", "Would you like
  your money back?") (RT-014 to RT-030). What this does not fix: a status report
  and a statement of policy are still rewritten, the second and third of the
  four. Excusing those as well was tried first and withdrawn: four reviews each
  found late refunds it let through ("You'd like a refund? Sure thing.", "I
  can't refund the shipping fee, just the $80 for the item itself."), so the rule
  now fails toward the supervisor on any wording it does not know.
- PROMISE-001 and SLA-001 fired on three idioms: "Promo codes don't last forever",
  "you'll never miss a clearance event" (a sale, an event, an offer; "never miss
  a delivery" is still a commitment) and "I will definitely be able to ..." where
  the sentence names no time or number ("By Friday we will ...", "... this
  afternoon" and "... at 9am" still fire). Those wordings are blanked before the rule's keywords are matched;
  the same keyword elsewhere in the reply still fires (RT-031 to RT-036). Only
  these three are covered: "This will definitely be sorted" with no date still
  counts as a timeline guarantee, and any other idiom with "forever", "never
  again" or "lifetime" still counts as a commitment.

New measurements:

- `scripts/bench_pii.py --detector presidio` (new `[bench]` extra, not in `[all]`)
  scores Presidio on the English rows of the external PII benchmark under the same
  scorer. On the 7 types both cover, Presidio has precision 90.7% and recall 66.5%
  at score ≥ 0.5, and 48.8% and 80.1% at ≥ 0.4, against this project's 58.7% and
  72.9%. Report only: Presidio
  is not a dependency. The README's comparison is now a table, dated 2026-11-09.
- `scripts/bench_real_turns.py` runs the pipeline over human-written agent turns
  (ABCD v1.1, MIT, downloaded at runtime) or any JSONL of turns. The audit's
  protocol and rubric are in docs/evaluation.md, committed before the first run.
  Result of the one run (19,144 turns, three assumed customer contexts): about
  0.3% of turns flagged where the refund rule cannot fire, and 16 of 57 (28.1%)
  and 15 of 50 (30.0%) of those flags are right; 243 of 581 (41.8%) where every
  refund is late by assumption. Half the flags outside that context are
  language misdetections on codes and usernames. Ids and labels are in
  `tests/fixtures/abcd_flagged.json`.
- docs/evaluation.md states what a block-only gate would do on the golden labels
  (hold 289 of 527 against 59 here); arithmetic, in-sample.

- `scripts/evaluate.py --invariants` checks the corrected text of the 230 cases
  of the golden set that the pipeline corrects: idempotent 230/230; of the 183 with
  recorded edits, citations resolve 183/183 and the edit log replays byte for byte
  180/183 (three differ by a trailing space); untouched sentences kept 13/13 where
  there is one. One hash per golden output is recorded in
  `tests/golden_outputs.json`, compared on each test run (a difference names its
  cases) and under other hash seeds in CI.

## [1.5] - 2026-11-09

Audit round: things a reader following the docs could trip over.

- `.env.example` is safe to copy as it is: every line is commented out and names a
  real setting.
- The Neo4j integration tests, which wipe the database, run only with
  `AUTOCORRECTOR_NEO4J_TEST_WIPE=1` and no longer read `.env`.
- Dashboard rate limits apply to every HTTP route, including demo stop and step; the
  demo interval is bounded; clients behind a trusted proxy get their own bucket
  (`AUTOCORRECTOR_TRUSTED_PROXIES`).
- Every document, section and page cited by a default rule or fact exists in
  `policies/`, and a test keeps it that way.
- MCP resources: `policies://active` returns the store's documents and rule ids
  (it was an empty stub, and its `last_updated` field is gone);
  `metrics://corrections/summary` can be read (every read failed with "Unknown
  resource"); `rules://compliance/{domain}` no longer invents `FACT-001` and
  `FACT-002` for a domain without rules, and each rule carries its source.
- The `[redis]` extra is removed: nothing imported it.
- Local servers bind loopback by default: the MCP HTTP server
  (`AUTOCORRECTOR_HOST`, was every interface) and `scripts/start_dashboard.sh`.
  `autocorrector --help` prints usage instead of starting a server.
- The dashboard API's CORS policy names what it serves: GET and POST, and the
  headers `Content-Type`, `Authorization`, `X-API-Key`, `X-Request-ID` (it was `*`).
- `AUTOCORRECTOR_NEO4J_DATABASE` reaches the driver, so sessions now open the
  database it names (default `neo4j`) instead of the server's home database; the settings nothing read
  (`DEBUG`, `EMBEDDING_*`, `DEFAULT_LANGUAGE`) are gone.
- Installs are refused on Python 3.15 (a dependency does not build there yet);
  three unused `langchain-*` packages left the `[langgraph]` extra and `httpx`
  moved to `[dev]`.
- Docker stack: published ports are bound to `127.0.0.1`; the API image runs as an
  unprivileged user and no longer ships `tests/`; the audit log is on an
  `audit_data` volume; the unused APOC plugin is gone. The image build now fails
  if the language model is not baked in (a failed download used to pass silently
  and the first request then fetched 125 MB).
- CI runs the Neo4j integration tests against a disposable service, builds both
  Docker images, and builds the replay bundle under a sub-path.
- One version source, and this changelog.

## [1.4] - 2026-11-09

Verification round (live-mode fixes, phone layout, ARIA, Docker stack with Neo4j).

## [1.3] - 2026-11-05

One recorded LLM-tier run (Claude Haiku 5.5) with a lane switch.

## [1.2] - 2026-11-05

Intent scoring fixed, modules split, theme-aware charts.

## [1.1] - 2026-11-05

Rules by their triggers, fail-closed validation, redaction everywhere, full Neo4j seed.

## [1.0] - 2026-11-04

MIT, replay demo, honest eval.
