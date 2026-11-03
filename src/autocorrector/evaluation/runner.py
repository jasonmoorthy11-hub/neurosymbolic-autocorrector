"""Evaluation runner — executes test cases through the pipeline and collects results."""

from __future__ import annotations

import logging
import time

from autocorrector.evaluation.dataset import EvalCase, GoldenDataset
from autocorrector.evaluation.scorer import CaseResult, EvaluationReport, compute_metrics
from autocorrector.knowledge_graph.store import KnowledgeGraphStore
from autocorrector.orchestrator.pipeline import run_autocorrection

logger = logging.getLogger(__name__)


async def evaluate_case(
    case: EvalCase,
    kg_store: KnowledgeGraphStore,
) -> CaseResult:
    """Run a single test case through the pipeline and evaluate the result."""
    try:
        start = time.monotonic()
        result = await run_autocorrection(
            text=case.agent_text,
            customer_context=case.customer_context,
            kg_store=kg_store,
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        actual_action = result.action_taken.value

        # Check if the action matches expected
        is_correct = actual_action == case.expected_action.value

        # Check PII detection
        actual_pii = result.pii_detected
        pii_correct = case.expected_pii == actual_pii

        # Check fact errors
        actual_facts_wrong = len(result.validation.fact_errors) > 0
        facts_correct = case.expected_facts_wrong == actual_facts_wrong

        # Check rule IDs (if expected)
        actual_rule_ids = [v.rule_id for v in result.validation.violations]
        if case.expected_rule_ids:
            rules_correct = all(rid in actual_rule_ids for rid in case.expected_rule_ids)
        else:
            rules_correct = True

        return CaseResult(
            case_id=case.id,
            category=case.category,
            expected_action=case.expected_action,
            actual_action=actual_action,
            is_correct=is_correct,
            expected_pii=case.expected_pii,
            actual_pii=actual_pii,
            pii_correct=pii_correct,
            expected_facts_wrong=case.expected_facts_wrong,
            actual_facts_wrong=actual_facts_wrong,
            facts_correct=facts_correct,
            expected_rule_ids=case.expected_rule_ids,
            actual_rule_ids=actual_rule_ids,
            rules_correct=rules_correct,
            latency_ms=elapsed_ms,
        )

    except Exception as e:
        logger.error(f"Error evaluating case {case.id}: {e}")
        return CaseResult(
            case_id=case.id,
            category=case.category,
            expected_action=case.expected_action,
            actual_action="error",
            is_correct=False,
            expected_pii=case.expected_pii,
            expected_facts_wrong=case.expected_facts_wrong,
            expected_rule_ids=case.expected_rule_ids,
            error=str(e),
        )


async def run_evaluation(
    dataset: GoldenDataset,
    kg_store: KnowledgeGraphStore | None = None,
) -> EvaluationReport:
    """Run all test cases in a dataset and produce an evaluation report."""
    if kg_store is None:
        kg_store = KnowledgeGraphStore()
        await kg_store.connect()

    results: list[CaseResult] = []
    total = len(dataset.cases)

    for i, case in enumerate(dataset.cases, 1):
        logger.info(f"[{i}/{total}] Evaluating {case.id}: {case.description}")
        case_result = await evaluate_case(case, kg_store)
        results.append(case_result)

        status = "PASS" if case_result.is_correct else "FAIL"
        logger.info(
            f"  {status}: expected={case_result.expected_action.value}, "
            f"actual={case_result.actual_action} ({case_result.latency_ms:.1f}ms)"
        )

    return compute_metrics(
        results,
        dataset_name=dataset.name,
        dataset_version=dataset.version,
    )
