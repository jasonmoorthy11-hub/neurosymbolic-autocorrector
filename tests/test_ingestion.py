"""Tests for the policy document ingestion pipeline."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autocorrector.knowledge_graph.defaults import load_default_facts, load_default_rules
from autocorrector.knowledge_graph.ingestion import (
    _extract_coverages,
    _extract_field,
    _extract_policy_name,
    _extract_rules,
    extract_from_markdown,
    ingest_directory,
)

POLICIES_DIR = Path(__file__).resolve().parents[1] / "policies"

# --- Extraction Helper Tests ---


class TestExtractionHelpers:
    """Test individual extraction functions."""

    def test_extract_policy_name_from_heading(self):
        content = "# Credit Policy 2024 v3.2\n\nSome text."
        assert _extract_policy_name(content, "credit.md") == "Credit_Policy_2024_v3.2"

    def test_extract_policy_name_from_filename(self):
        content = "Some text without a heading."
        assert _extract_policy_name(content, "my_policy.md") == "my_policy"

    def test_extract_policy_name_default(self):
        content = "No heading here."
        assert _extract_policy_name(content, "") == "Unknown_Policy"

    def test_extract_field_found(self):
        content = "**Effective Date:** March 1, 2024\n**Category:** Financial"
        assert _extract_field(content, "effective date", "") == "March 1, 2024"
        assert _extract_field(content, "category", "general") == "Financial"

    def test_extract_field_default(self):
        content = "No fields here."
        assert _extract_field(content, "version", "1.0") == "1.0"


# --- Rule Extraction Tests ---


class TestRuleExtraction:
    """Test policy rule extraction from markdown."""

    SAMPLE_RULES = """
## Section 4: Credit Rules

### Section 4.1 — Credit Authorization Threshold (CREDIT-001)
**Severity:** High
**Rule:** Credits over $50 require 90+ days account tenure.
**Keywords:** credit, compensation, offer you
**Corrective Action:** Reduce credit to $50 maximum.

### Section 4.3 — Maximum Credit Amount (CREDIT-002)
**Severity:** Critical
**Rule:** Single credit cannot exceed $500 without supervisor approval.
**Keywords:** credit, compensation, offer you
**Corrective Action:** Escalate to supervisor immediately.
"""

    def test_extracts_two_rules(self):
        rules = _extract_rules(self.SAMPLE_RULES, "Test_Policy")
        assert len(rules) == 2

    def test_rule_ids(self):
        rules = _extract_rules(self.SAMPLE_RULES, "Test_Policy")
        ids = [r.rule_id for r in rules]
        assert "CREDIT-001" in ids
        assert "CREDIT-002" in ids

    def test_rule_severity(self):
        rules = _extract_rules(self.SAMPLE_RULES, "Test_Policy")
        by_id = {r.rule_id: r for r in rules}
        assert by_id["CREDIT-001"].severity == "high"
        assert by_id["CREDIT-002"].severity == "critical"

    def test_rule_description(self):
        rules = _extract_rules(self.SAMPLE_RULES, "Test_Policy")
        by_id = {r.rule_id: r for r in rules}
        assert "$50" in by_id["CREDIT-001"].description
        assert "90+" in by_id["CREDIT-001"].description

    def test_rule_keywords(self):
        rules = _extract_rules(self.SAMPLE_RULES, "Test_Policy")
        by_id = {r.rule_id: r for r in rules}
        assert "credit" in by_id["CREDIT-001"].keywords
        assert "compensation" in by_id["CREDIT-001"].keywords

    def test_rule_amount_threshold(self):
        rules = _extract_rules(self.SAMPLE_RULES, "Test_Policy")
        by_id = {r.rule_id: r for r in rules}
        assert by_id["CREDIT-001"].amount_threshold == 50.0
        assert by_id["CREDIT-002"].amount_threshold == 500.0

    def test_rule_correction_template(self):
        rules = _extract_rules(self.SAMPLE_RULES, "Test_Policy")
        by_id = {r.rule_id: r for r in rules}
        assert "Reduce credit" in by_id["CREDIT-001"].correction_template
        assert "Escalate" in by_id["CREDIT-002"].correction_template

    def test_rule_section(self):
        rules = _extract_rules(self.SAMPLE_RULES, "Test_Policy")
        by_id = {r.rule_id: r for r in rules}
        assert by_id["CREDIT-001"].section == "Section 4.1"

    def test_skips_sections_without_rule_id(self):
        content = """
### Section 1.0 — Overview
This is just an overview section with no rule ID.
"""
        rules = _extract_rules(content, "Test_Policy")
        assert len(rules) == 0


# --- Coverage Extraction Tests ---


class TestCoverageExtraction:
    """Test coverage detail extraction from markdown."""

    TABLE_CONTENT = """# Standard Plan 2024 — Coverage Details

**Product:** Standard 2024

## Coverage Summary

| Coverage Category | Annual Limit | Source Page |
|---|---|---|
| Emergency Dental | $2,500 | Page 12 |
| Emergency Room | $10,000 | Page 8 |
| Prescription Drugs | $5,000 | Page 15 |
"""

    def test_extracts_table_coverages(self):
        coverages = _extract_coverages(self.TABLE_CONTENT, "standard.md")
        assert len(coverages) == 3

    def test_coverage_amounts(self):
        coverages = _extract_coverages(self.TABLE_CONTENT, "standard.md")
        amounts = {c.category: c.limit for c in coverages}
        assert amounts["emergency dental"] == 2500.0
        assert amounts["emergency room"] == 10000.0
        assert amounts["prescription drugs"] == 5000.0

    def test_coverage_source_pages(self):
        coverages = _extract_coverages(self.TABLE_CONTENT, "standard.md")
        pages = {c.category: c.source_page for c in coverages}
        assert pages["emergency dental"] == 12
        assert pages["emergency room"] == 8

    def test_coverage_plan_name(self):
        coverages = _extract_coverages(self.TABLE_CONTENT, "standard.md")
        assert all(c.plan_name == "standard_2024" for c in coverages)

    def test_coverage_source_document(self):
        coverages = _extract_coverages(self.TABLE_CONTENT, "standard.md")
        assert all(c.source_document == "standard.md" for c in coverages)


# --- Full Document Extraction Tests ---


class TestFullExtraction:
    """Test end-to-end document extraction."""

    def test_extract_credit_policy(self):
        content = (POLICIES_DIR / "credit_policy_2024_v3.2.md").read_text()
        policy = extract_from_markdown(content, "credit_policy_2024_v3.2.md")

        assert policy.name == "Credit_Policy_2024_v3.2"
        assert policy.category == "Financial"
        assert len(policy.rules) == 2
        assert policy.rules[0].rule_id in ("CREDIT-001", "CREDIT-002")

    def test_extract_refund_policy(self):
        content = (POLICIES_DIR / "refund_policy_2024_v2.1.md").read_text()
        policy = extract_from_markdown(content, "refund_policy_2024_v2.1.md")

        assert "Refund" in policy.name
        assert len(policy.rules) >= 1
        assert any(r.rule_id == "REFUND-001" for r in policy.rules)

    def test_extract_standard_coverage(self):
        content = (POLICIES_DIR / "coverage_standard_2024.md").read_text()
        policy = extract_from_markdown(content, "coverage_standard_2024.md")

        assert len(policy.coverages) >= 3
        categories = [c.category for c in policy.coverages]
        assert any("dental" in cat for cat in categories)
        assert any("room" in cat for cat in categories)

    def test_extract_premium_coverage(self):
        content = (POLICIES_DIR / "coverage_premium_2024.md").read_text()
        policy = extract_from_markdown(content, "coverage_premium_2024.md")

        assert len(policy.coverages) >= 2
        limits = {c.category: c.limit for c in policy.coverages}
        # Premium plan has higher limits
        dental = [v for k, v in limits.items() if "dental" in k]
        assert dental and dental[0] == 5000.0


# --- Directory Ingestion Tests ---


class TestDirectoryIngestion:
    """Test batch ingestion from a directory."""

    async def test_ingest_policies_directory(self):
        policies = await ingest_directory(str(POLICIES_DIR))
        assert len(policies) >= 4  # credit, refund, standard, premium

    async def test_ingest_extracts_rules_and_coverages(self):
        policies = await ingest_directory(str(POLICIES_DIR))
        total_rules = sum(len(p.rules) for p in policies)
        total_coverages = sum(len(p.coverages) for p in policies)
        assert total_rules >= 3  # CREDIT-001, CREDIT-002, REFUND-001
        assert total_coverages >= 5  # standard has 3, premium has 2

    async def test_ingest_nonexistent_directory_raises(self):
        with pytest.raises(FileNotFoundError):
            await ingest_directory("/nonexistent/path")


# --- Citations Resolve ---


def _default_rules() -> list[dict]:
    return [rule for rules in load_default_rules().values() for rule in rules]


def _default_coverages() -> list[tuple[str, dict]]:
    return [
        (plan, coverage)
        for plan, facts in load_default_facts().items()
        for coverage in facts["coverages"]
    ]


class TestDefaultCitationsResolve:
    """Every correction cites a document and a section or page. Each citation in
    `defaults.py` must lead to that text in `policies/`, read with the same parser
    the ingestion pipeline uses."""

    @pytest.fixture
    async def policies(self):
        return await ingest_directory(str(POLICIES_DIR))

    @pytest.mark.parametrize("rule", _default_rules(), ids=lambda r: r["rule_id"])
    async def test_citation_rule_resolves_to_its_policy_section(self, policies, rule):
        cited = [
            found
            for policy in policies
            if policy.name == rule["source_document"]
            for found in policy.rules
            if found.rule_id == rule["rule_id"]
        ]
        assert len(cited) == 1, f"{rule['rule_id']} not found in {rule['source_document']}"
        assert cited[0].section == rule["source_section"]
        assert cited[0].name == rule["rule_name"]
        assert cited[0].severity == rule["severity"]
        assert cited[0].description.startswith(rule["description"])
        assert cited[0].keywords
        assert set(cited[0].keywords) <= set(rule["keywords"])

    @pytest.mark.parametrize(
        "plan,coverage",
        _default_coverages(),
        ids=lambda v: v if isinstance(v, str) else v["category"],
    )
    async def test_citation_fact_resolves_to_its_coverage_page(self, policies, plan, coverage):
        rows = [
            found
            for policy in policies
            for found in policy.coverages
            if found.plan_name == plan and found.category == coverage["category"]
        ]
        assert len(rows) == 1, f"{plan} / {coverage['category']} not in policies/"
        assert rows[0].limit == coverage["limit"]
        assert rows[0].source_page == coverage["source_page"]
        text = (POLICIES_DIR / rows[0].source_document).read_text(encoding="utf-8")
        cited = re.escape(f"{coverage['source_document']}, Page {coverage['source_page']}")
        assert re.search(cited + r"\b", text)
