#!/usr/bin/env python3
"""External PII benchmark: span-level precision/recall of the PII detector.

Dataset: gretelai/synthetic_pii_finance_multilingual (Apache-2.0), test split,
downloaded at runtime as JSON from the Hugging Face dataset viewer into
.cache/gretel_pii/ (gitignored; the data is never committed).

Scoring (spans from find_pii_spans, i.e. exactly what the validator redacts;
every row is re-checked against PIIDetectionValidator and mismatches are counted):
    - Gold labels are mapped to our 10 PII types via LABEL_MAP; every other
      label is out of scope and excluded from numerator and denominator.
    - Overlap means sharing at least one character; there is no 1:1 matching
      (one prediction may cover several gold spans and vice versa).
    - typed: a prediction is a TP if it overlaps a gold span of the same type;
      it is an FP if it overlaps no in-scope gold span or only ones of another
      type; it is excluded if it overlaps only out-of-scope gold spans.
      A gold span is found if a prediction of its type overlaps it.
    - untyped: the same, ignoring types among in-scope spans.
    - Rows whose gold offsets fall outside the text are skipped and counted.
    - 95% Wilson intervals on every proportion.

Usage:
    python3 scripts/bench_pii.py                      # English subset + all languages
    python3 scripts/bench_pii.py --refresh            # re-download the cache
    python3 scripts/bench_pii.py --detector presidio  # English rows: this detector next to
                                                      # Presidio (needs the [bench] extra and
                                                      # spaCy's en_core_web_lg)
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autocorrector.evaluation.scorer import wilson_interval  # noqa: E402
from autocorrector.guardrails.validators import (  # noqa: E402
    PII_TYPES,
    PIIDetectionValidator,
    find_pii_spans,
    redact_spans,
)

DATASET = "gretelai/synthetic_pii_finance_multilingual"
ROWS_URL = "https://datasets-server.huggingface.co/rows"
CACHE = Path(__file__).resolve().parent.parent / ".cache/gretel_pii/test.jsonl"
PAGE = 100

# gretel label -> our PII type. Unlisted labels (name, date, company, ...) are out of scope.
LABEL_MAP = {
    "ssn": "ssn",
    "credit_card_number": "credit_card",
    "email": "email",
    "phone_number": "phone",
    "date_of_birth": "date_of_birth",
    "passport_number": "passport",
    "bban": "bank_account",
    "iban": "bank_account",
    "bank_routing_number": "routing_number",
    "driver_license_number": "drivers_license",
    "street_address": "address",
}

# Presidio entity -> our PII type. Presidio has no recognizer specific to a date of
# birth or a street address (DATE_TIME and LOCATION are any date and any place), so
# those two types have no Presidio prediction here. An entity that the installed
# Presidio's default registry does not load (ABA_ROUTING_NUMBER ships disabled in
# 2.2.366) is reported and its type left out of the shared-types comparison.
PRESIDIO_ENTITIES = {
    "US_SSN": "ssn",
    "CREDIT_CARD": "credit_card",
    "EMAIL_ADDRESS": "email",
    "PHONE_NUMBER": "phone",
    "US_PASSPORT": "passport",
    "US_BANK_NUMBER": "bank_account",
    "IBAN_CODE": "bank_account",
    "ABA_ROUTING_NUMBER": "routing_number",
    "US_DRIVER_LICENSE": "drivers_license",
}
# Presidio scores each result 0-1 and by default keeps all of them (threshold 0).
# 0.5 and 0.85 were picked before the first run as round reference points. 0.4 was
# added after a review of the first table: no US_PASSPORT result on these rows
# reaches 0.5 and most phone results score below it. None is tuned.
PRESIDIO_THRESHOLDS = (0.0, 0.4, 0.5, 0.85)
PER_TYPE_THRESHOLDS = (0.4, 0.5)
# Presidio's own default model (conf/default.yaml); lemmas feed its context boost
PRESIDIO_SPACY_MODEL = "en_core_web_lg"

Span = tuple[str, int, int]
Detector = Callable[[str], list[Span]]


@dataclass
class Counts:
    pred_tp: int = 0
    pred_fp: int = 0
    pred_excluded: int = 0
    gold_found: int = 0
    gold_missed: int = 0


@dataclass
class Tally:
    rows: int = 0
    skipped_rows: int = 0  # gold offsets outside the text: the row is unreliable
    mirror_mismatches: int = 0  # rows where spans do not rebuild the validator's output
    typed: Counts = field(default_factory=Counts)
    untyped: Counts = field(default_factory=Counts)
    per_type: dict[str, Counts] = field(default_factory=lambda: {t: Counts() for t in PII_TYPES})
    excluded_by_label: dict[str, int] = field(default_factory=dict)


class SpanOffsetError(ValueError):
    """A well-formed span whose offsets do not fit its text (the row is skipped)."""


def parse_spans(raw: str, text_len: int) -> list[Span]:
    """Parse a row's pii_spans JSON string.

    Raises SpanOffsetError unless 0 <= start < end <= text_len, and plain
    ValueError for anything structurally malformed.
    """
    try:
        items = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as e:
        raise ValueError(f"pii_spans is not JSON: {e}") from e
    if not isinstance(items, list):
        raise ValueError("pii_spans is not a list")
    spans = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("span is not an object")
        label, start, end = item.get("label"), item.get("start"), item.get("end")
        if not isinstance(label, str) or type(start) is not int or type(end) is not int:
            raise ValueError(f"bad span fields: {item!r}")
        if not 0 <= start < end <= text_len:
            raise SpanOffsetError(f"bad span offsets for a {text_len}-char text: {item!r}")
        spans.append((label, start, end))
    return spans


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _score(preds: list[Span], gold_in: list[Span], gold_out: list[Span], tally: Tally) -> None:
    for ptype, ps, pe in preds:
        hits = [g for g in gold_in if _overlaps(ps, pe, g[1], g[2])]
        typed_hit = any(g[0] == ptype for g in hits)
        if hits:
            tally.untyped.pred_tp += 1
        if typed_hit:
            tally.typed.pred_tp += 1
            tally.per_type[ptype].pred_tp += 1
        elif hits or not any(_overlaps(ps, pe, g[1], g[2]) for g in gold_out):
            tally.typed.pred_fp += 1
            tally.per_type[ptype].pred_fp += 1
            if not hits:
                tally.untyped.pred_fp += 1
        else:
            tally.typed.pred_excluded += 1
            tally.untyped.pred_excluded += 1
            for label in {g[0] for g in gold_out if _overlaps(ps, pe, g[1], g[2])}:
                tally.excluded_by_label[label] = tally.excluded_by_label.get(label, 0) + 1
    for gtype, gs, ge in gold_in:
        hits = [p for p in preds if _overlaps(gs, ge, p[1], p[2])]
        if any(p[0] == gtype for p in hits):
            tally.typed.gold_found += 1
            tally.per_type[gtype].gold_found += 1
        else:
            tally.typed.gold_missed += 1
            tally.per_type[gtype].gold_missed += 1
        if hits:
            tally.untyped.gold_found += 1
        else:
            tally.untyped.gold_missed += 1


def score_rows(
    rows: Iterable[dict], language: str | None = None, detector: Detector | None = None
) -> Tally:
    """Span-level counts over rows ({text, language, pii_spans}), optionally one language.

    ``detector`` replaces this project's ``find_pii_spans`` (and skips the check
    that the spans rebuild the validator's output, which only applies to it).
    """
    tally = Tally()
    for row in rows:
        if language is not None and row["language"] != language:
            continue
        text = row["text"]
        try:
            spans = parse_spans(row["pii_spans"], len(text))
        except SpanOffsetError:
            tally.skipped_rows += 1
            continue
        tally.rows += 1
        gold_in = [(LABEL_MAP[lb], s, e) for lb, s, e in spans if lb in LABEL_MAP]
        gold_out = [(lb, s, e) for lb, s, e in spans if lb not in LABEL_MAP]
        if detector is None:
            preds = find_pii_spans(text)
            if redact_spans(text, preds) != _validator_output(text):
                tally.mirror_mismatches += 1
        else:
            preds = detector(text)
        _score(preds, gold_in, gold_out, tally)
    return tally


def _validator_output(text: str) -> str:
    result = PIIDetectionValidator().validate(text)
    return getattr(result, "fix_value", None) or text


_TRANSIENT = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    http.client.IncompleteRead,
    json.JSONDecodeError,
)


def _get(url: str, retries: int = 8) -> dict:
    """GET JSON, retrying transient failures with backoff (honours Retry-After).

    Client errors other than 429 are permanent and raise at once.
    """
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                return json.load(resp)
        except _TRANSIENT as e:
            permanent = isinstance(e, urllib.error.HTTPError) and e.code != 429 and e.code < 500
            if permanent or attempt == retries - 1:
                raise
            wait = min(2 ** (attempt + 1), 120)
            if isinstance(e, urllib.error.HTTPError):
                retry_after = (e.headers or {}).get("Retry-After", "")
                if retry_after.isdigit():
                    wait = min(int(retry_after), 300)
            print(f"  retry in {wait}s ({e!r})", file=sys.stderr)
            time.sleep(wait)
    raise AssertionError("unreachable")


def fetch(cache: Path) -> int:
    """Download the test split as JSON lines into ``cache``; returns the row count.

    Writes to a .partial file and renames it only when every row arrived intact.
    """
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".partial")
    query = {"dataset": DATASET, "config": "default", "split": "test", "length": PAGE}
    offset, total = 0, None
    try:
        with tmp.open("w") as out:
            while total is None or offset < total:
                page = _get(f"{ROWS_URL}?{urllib.parse.urlencode({**query, 'offset': offset})}")
                page_total = int(page["num_rows_total"])
                if total is not None and page_total != total:
                    raise ValueError(f"row total changed from {total} to {page_total}")
                total = page_total
                if not page["rows"] and offset < total:
                    raise ValueError(f"empty page at offset {offset} of {total}")
                for item in page["rows"]:
                    if item.get("truncated_cells"):
                        raise ValueError(f"truncated cells near offset {offset}")
                    r = item["row"]
                    text, lang, spans = r["generated_text"], r["language"], r["pii_spans"]
                    if not all(isinstance(v, str) for v in (text, lang, spans)):
                        raise ValueError(f"unexpected row types near offset {offset}")
                    out.write(
                        json.dumps({"text": text, "language": lang, "pii_spans": spans}) + "\n"
                    )
                offset += len(page["rows"])
                time.sleep(1.0)
        if offset != total:
            raise ValueError(f"wrote {offset} rows, expected {total}")
        tmp.replace(cache)
    finally:
        tmp.unlink(missing_ok=True)
    return total


def _pct(k: int, n: int) -> str:
    if n == 0:
        return "n/a (0/0)"
    low, high = wilson_interval(k, n)
    return f"{k / n:.1%} ({k}/{n}, 95% CI {low * 100:.1f}-{high * 100:.1f})"


def _print_tally(title: str, t: Tally) -> None:
    print(f"\n## {title}: {t.rows} rows")
    for name, c in (("typed", t.typed), ("untyped", t.untyped)):
        print(f"  {name:8s} precision {_pct(c.pred_tp, c.pred_tp + c.pred_fp)}")
        print(f"  {name:8s} recall    {_pct(c.gold_found, c.gold_found + c.gold_missed)}")
    print(f"  predictions excluded (overlap only out-of-scope labels): {t.typed.pred_excluded}")


def _print_per_type(title: str, t: Tally) -> None:
    print(f"\n## {title}")
    print(f"  {'type':16s} {'precision':>36s}   {'recall':>36s}")
    for ptype, c in t.per_type.items():
        prec = _pct(c.pred_tp, c.pred_tp + c.pred_fp)
        rec = _pct(c.gold_found, c.gold_found + c.gold_missed)
        print(f"  {ptype:16s} {prec:>36s}   {rec:>36s}")


def _print_shared_types(title: str, t: Tally, types: Iterable[str]) -> None:
    """Typed precision and recall over ``types`` (those Presidio has a recognizer for)."""
    shared = [t.per_type[ptype] for ptype in types]
    tp = sum(c.pred_tp for c in shared)
    fp = sum(c.pred_fp for c in shared)
    found = sum(c.gold_found for c in shared)
    missed = sum(c.gold_missed for c in shared)
    print(f"  {title}: precision {_pct(tp, tp + fp)}, recall {_pct(found, found + missed)}")


def shared_types(entities: Iterable[str]) -> list[str]:
    """Our PII types that the given Presidio entities map to, sorted."""
    return sorted({PRESIDIO_ENTITIES[entity] for entity in entities})


def presidio_results(
    texts: Iterable[str], model: str = PRESIDIO_SPACY_MODEL
) -> tuple[dict[str, list[tuple[str, int, int, float]]], list[str]]:
    """Presidio's (type, start, end, score) results per text, and the entities it ran.

    Default recognizers, spaCy ``model``, English, no score threshold, and only the
    entities of PRESIDIO_ENTITIES that the default registry loads (the second value).
    """
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
    except ImportError as exc:
        raise SystemExit(
            "Presidio is not installed. Run:\n"
            '  pip install -e ".[bench]"\n'
            f"  python -m spacy download {model}"
        ) from exc
    nlp = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": model}],
        }
    ).create_engine()
    analyzer = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
    supported = set(analyzer.get_supported_entities("en"))
    entities = [entity for entity in PRESIDIO_ENTITIES if entity in supported]
    results = {
        text: sorted(
            (PRESIDIO_ENTITIES[r.entity_type], r.start, r.end, r.score)
            for r in analyzer.analyze(text=text, language="en", entities=entities)
        )
        for text in texts
    }
    return results, entities


def compare_with_presidio(rows: list[dict], model: str = PRESIDIO_SPACY_MODEL) -> None:
    """English rows only: this detector and Presidio under the same scorer."""
    english = [row for row in rows if row["language"] == "English"]
    ours = score_rows(english)
    _print_tally("English, this project's regex rail", ours)
    _print_per_type("Per type, English, this project's regex rail (typed)", ours)

    started = time.monotonic()
    results, entities = presidio_results((row["text"] for row in english), model)
    seconds = time.monotonic() - started
    shared = shared_types(entities)
    missing = [entity for entity in PRESIDIO_ENTITIES if entity not in entities]
    print(
        f"\nPresidio: default recognizers, spaCy {model}, {len(entities)} entities"
        f" mapped to {len(shared)} of our {len(PII_TYPES)} types;"
        f" {seconds:.0f} s for {len(english)} rows"
    )
    if missing:
        print(f"  not loaded by the default registry: {', '.join(missing)}")
    tallies: dict[float, Tally] = {}
    for threshold in PRESIDIO_THRESHOLDS:

        def detect(text: str, threshold: float = threshold) -> list[Span]:
            return [(t, s, e) for t, s, e, score in results[text] if score >= threshold]

        tally = tallies[threshold] = score_rows(english, detector=detect)
        _print_tally(f"English, Presidio, score >= {threshold}", tally)
        if threshold in PER_TYPE_THRESHOLDS:
            _print_per_type(f"Per type, English, Presidio at score >= {threshold} (typed)", tally)

    print(f"\n## English, only the {len(shared)} types both cover ({', '.join(shared)})")
    _print_shared_types("this project's regex rail", ours, shared)
    for threshold, tally in tallies.items():
        _print_shared_types(f"Presidio at score >= {threshold:<4} ", tally, shared)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="re-download the dataset")
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument(
        "--detector",
        choices=["regex", "presidio"],
        default="regex",
        help="regex: this project's rail (default); presidio: compare with Presidio on English",
    )
    parser.add_argument(
        "--spacy-model",
        default=PRESIDIO_SPACY_MODEL,
        help=f"with --detector presidio: the spaCy model (default {PRESIDIO_SPACY_MODEL}, "
        "Presidio's own default)",
    )
    args = parser.parse_args(argv)

    if args.refresh or not args.cache.exists():
        print(f"Downloading {DATASET} (test split) to {args.cache} ...")
        fetch(args.cache)
    with args.cache.open() as f:
        rows = [json.loads(line) for line in f]

    print(f"# PII span benchmark: {DATASET}, test split, {len(rows)} rows")
    labels: dict[str, int] = {}
    for row in rows:
        try:
            spans = parse_spans(row["pii_spans"], len(row["text"]))
        except SpanOffsetError:
            continue
        for lb, _, _ in spans:
            labels[lb] = labels.get(lb, 0) + 1
    in_scope = {lb: n for lb, n in labels.items() if lb in LABEL_MAP}
    print(f"In-scope gold spans by label: {dict(sorted(in_scope.items()))}")
    print(f"Out-of-scope gold spans: {sum(labels.values()) - sum(in_scope.values())}")

    if args.detector == "presidio":
        compare_with_presidio(rows, args.spacy_model)
        return 0

    english = score_rows(rows, language="English")
    everything = score_rows(rows)
    print(f"Rows skipped (gold offsets outside the text): {everything.skipped_rows}")
    print(f"Rows where spans differ from the validator's redaction: {everything.mirror_mismatches}")
    _print_tally("English", english)
    _print_tally("All languages", everything)
    excluded = sorted(everything.excluded_by_label.items(), key=lambda kv: -kv[1])
    print(f"  excluded predictions by overlapping out-of-scope label: {dict(excluded)}")

    _print_per_type("Per type, all languages (typed)", everything)
    return 0


if __name__ == "__main__":
    sys.exit(main())
