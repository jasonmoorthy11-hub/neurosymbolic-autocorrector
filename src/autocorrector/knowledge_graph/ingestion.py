"""Policy document ingestion pipeline.

Extracts structured policy information from markdown documents with regular
expressions and can load it into the Neo4j knowledge graph.

Experimental: the correction pipeline does not import it (its rules come from
``defaults.py`` and the Neo4j seed in ``schema.py``). Its parser is what
``tests/test_ingestion.py`` uses to check that every citation in ``defaults.py``
resolves to a document in ``policies/``.

Supports ingesting:
- Policy rules (with rule IDs, severity, keywords, thresholds)
- Coverage details (plan type, category, limits, source pages)
- General policy metadata (name, version, effective date)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExtractedRule:
    """A policy rule extracted from a document."""

    rule_id: str
    name: str
    description: str = ""
    severity: str = ""
    section: str = ""
    keywords: list[str] = field(default_factory=list)
    amount_threshold: float | None = None
    conditions: list[dict[str, Any]] = field(default_factory=list)
    correction_template: str = ""
    policy_name: str = ""


@dataclass
class ExtractedCoverage:
    """A coverage item extracted from a document."""

    plan_name: str
    category: str
    limit: float
    source_document: str = ""
    source_page: int = 0


@dataclass
class ExtractedPolicy:
    """A complete policy document's extracted content."""

    name: str
    version: str
    effective_date: str
    category: str
    rules: list[ExtractedRule] = field(default_factory=list)
    coverages: list[ExtractedCoverage] = field(default_factory=list)


def extract_from_markdown(content: str, filename: str = "") -> ExtractedPolicy:
    """Extract policy data from a markdown document.

    Uses regex patterns to find:
    - Policy metadata (name, version, date) from the header
    - Rules with IDs like CREDIT-001, REFUND-001
    - Coverage tables and limit amounts
    """
    policy = ExtractedPolicy(
        name=_extract_policy_name(content, filename),
        version=_extract_field(content, "version", "1.0"),
        effective_date=_extract_field(content, "effective date", ""),
        category=_extract_field(content, "category", "general"),
    )

    policy.rules = _extract_rules(content, policy.name)
    policy.coverages = _extract_coverages(content, filename)

    logger.info(
        f"Extracted from {filename}: {len(policy.rules)} rules, {len(policy.coverages)} coverages"
    )
    return policy


def _extract_policy_name(content: str, filename: str) -> str:
    """Extract policy name from the first heading or filename."""
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if match:
        return match.group(1).strip().replace(" ", "_")
    return Path(filename).stem if filename else "Unknown_Policy"


def _extract_field(content: str, field_name: str, default: str) -> str:
    """Extract a metadata field value."""
    pattern = rf"\*\*{re.escape(field_name)}:\*\*\s*(.+)"
    match = re.search(pattern, content, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return default


def _extract_rules(content: str, policy_name: str) -> list[ExtractedRule]:
    """Extract policy rules from the document."""
    rules = []

    # Find rule sections: "### Section X.Y — Rule Name (RULE-NNN)"
    # or "### Section X.Y — Rule Name" followed by rule_id in content
    rule_pattern = re.compile(
        r"###\s+(?:Section\s+)?([\d.]+)\s*[—–-]\s*(.+?)(?:\(([A-Z]+-\d+)\))?\s*$",
        re.MULTILINE,
    )

    for match in rule_pattern.finditer(content):
        section = f"Section {match.group(1)}"
        name = match.group(2).strip()
        rule_id = match.group(3)

        # Get the text block after this heading until next heading
        start = match.end()
        next_heading = re.search(r"^#{1,3}\s", content[start:], re.MULTILINE)
        block = content[start : start + next_heading.start()] if next_heading else content[start:]

        # If no rule_id in heading, look in the block
        if not rule_id:
            id_match = re.search(r"\(([A-Z]+-\d+)\)", block)
            if id_match:
                rule_id = id_match.group(1)
            else:
                continue  # Skip sections without rule IDs

        rule = ExtractedRule(
            rule_id=rule_id,
            name=name,
            section=section,
            policy_name=policy_name,
        )

        # Extract severity
        sev_match = re.search(r"\*\*Severity:\*\*\s*(\w+)", block, re.IGNORECASE)
        if sev_match:
            rule.severity = sev_match.group(1).lower()

        # Extract description
        desc_match = re.search(r"\*\*Rule:\*\*\s*(.+?)(?:\n|$)", block)
        if desc_match:
            rule.description = desc_match.group(1).strip()

        # Extract keywords
        kw_match = re.search(r"\*\*Keywords:\*\*\s*(.+?)(?:\n|$)", block)
        if kw_match:
            rule.keywords = [k.strip() for k in kw_match.group(1).split(",")]

        # Extract amount threshold
        amt_match = re.search(r"\$(\d+(?:,\d{3})*)", rule.description)
        if amt_match:
            rule.amount_threshold = float(amt_match.group(1).replace(",", ""))

        # Extract correction template
        corr_match = re.search(
            r"\*\*Corrective Action:\*\*\s*(.+?)(?:\n\n|\n\*\*|$)",
            block,
            re.DOTALL,
        )
        if corr_match:
            rule.correction_template = corr_match.group(1).strip()

        rules.append(rule)

    return rules


def _extract_coverages(content: str, filename: str) -> list[ExtractedCoverage]:
    """Extract coverage items from tables and sections."""
    coverages = []

    # Extract plan name from header
    plan_match = re.search(r"(?:Product|Plan):\*\*\s*(.+?)$", content, re.MULTILINE | re.IGNORECASE)
    plan_name = plan_match.group(1).strip().lower().replace(" ", "_") if plan_match else ""

    # Try markdown table format: | Category | Limit | Page |
    table_pattern = re.compile(r"\|\s*([^|]+?)\s*\|\s*\$?([\d,]+)\s*\|\s*(?:Page\s*)?(\d+)\s*\|")
    for match in table_pattern.finditer(content):
        category = match.group(1).strip()
        if category.lower() in ("coverage category", "---", ""):
            continue
        limit = float(match.group(2).replace(",", ""))
        page = int(match.group(3))

        coverages.append(
            ExtractedCoverage(
                plan_name=plan_name,
                category=category.lower(),
                limit=limit,
                source_document=filename,
                source_page=page,
            )
        )

    # Also try section-based format: "**Annual Limit:** $X,XXX"
    if not coverages:
        section_pattern = re.compile(
            r"##\s+(.+?Coverage)\s*\n.*?"
            r"\*\*Annual Limit:\*\*\s*\$?([\d,]+).*?"
            r"(?:\*\*Source.*?Page\s*(\d+))?",
            re.DOTALL,
        )
        for match in section_pattern.finditer(content):
            category = match.group(1).strip().lower()
            limit = float(match.group(2).replace(",", ""))
            page = int(match.group(3)) if match.group(3) else 0

            coverages.append(
                ExtractedCoverage(
                    plan_name=plan_name,
                    category=category,
                    limit=limit,
                    source_document=filename,
                    source_page=page,
                )
            )

    return coverages


async def ingest_directory(
    directory: str | Path,
    kg_store: Any = None,
    openai_api_key: str = "",
) -> list[ExtractedPolicy]:
    """Ingest all markdown files from a directory.

    If kg_store is provided and connected to Neo4j, loads extracted data
    into the graph. Otherwise, returns extracted policies for inspection.

    If openai_api_key is provided, generates vector embeddings after loading.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    policies = []
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith("."):
            continue
        content = path.read_text()
        policy = extract_from_markdown(content, path.name)
        policies.append(policy)

    logger.info(f"Ingested {len(policies)} policy documents from {directory}")

    # Load into Neo4j if available
    if kg_store and kg_store.is_connected:
        await _load_to_neo4j(policies, kg_store)

        if openai_api_key:
            from autocorrector.knowledge_graph.schema import (
                create_vector_indexes,
                generate_embeddings,
            )

            driver = kg_store._driver
            await create_vector_indexes(driver)
            count = await generate_embeddings(driver, openai_api_key)
            logger.info(f"Generated embeddings for {count} ingested nodes")

    return policies


async def _load_to_neo4j(
    policies: list[ExtractedPolicy],
    kg_store: Any,
) -> None:
    """Load extracted policies into Neo4j."""
    driver = kg_store._driver
    if not driver:
        return

    async with driver.session() as session:
        for policy in policies:
            # Create Policy node
            await session.run(
                """
                MERGE (p:Policy {name: $name})
                SET p.version = $version,
                    p.effective_date = $effective_date,
                    p.category = $category
                """,
                name=policy.name,
                version=policy.version,
                effective_date=policy.effective_date,
                category=policy.category,
            )

            # Create Rule nodes
            for rule in policy.rules:
                await session.run(
                    """
                    MATCH (p:Policy {name: $policy_name})
                    MERGE (r:Rule {rule_id: $rule_id})
                    SET r.name = $name,
                        r.description = $description,
                        r.severity = $severity,
                        r.section = $section,
                        r.keywords = $keywords,
                        r.amount_threshold = $amount_threshold,
                        r.active = true
                    MERGE (p)-[:HAS_RULE]->(r)
                    """,
                    policy_name=policy.name,
                    rule_id=rule.rule_id,
                    name=rule.name,
                    description=rule.description,
                    severity=rule.severity,
                    section=rule.section,
                    keywords=rule.keywords,
                    amount_threshold=rule.amount_threshold,
                )

                if rule.correction_template:
                    await session.run(
                        """
                        MATCH (r:Rule {rule_id: $rule_id})
                        MERGE (ct:CorrectionTemplate {rule_id: $rule_id})
                        SET ct.template = $template
                        MERGE (r)-[:HAS_CORRECTION]->(ct)
                        """,
                        rule_id=rule.rule_id,
                        template=rule.correction_template,
                    )

            # Create Product/Coverage nodes
            for cov in policy.coverages:
                if cov.plan_name:
                    await session.run(
                        """
                        MERGE (p:Product {name: $plan_name})
                        MERGE (c:Coverage {product: $plan_name, category: $category})
                        SET c.limit = $limit,
                            c.source_document = $source_doc,
                            c.source_page = $source_page
                        MERGE (p)-[:HAS_COVERAGE]->(c)
                        """,
                        plan_name=cov.plan_name,
                        category=cov.category,
                        limit=cov.limit,
                        source_doc=cov.source_document,
                        source_page=cov.source_page,
                    )

    logger.info(f"Loaded {len(policies)} policies into Neo4j")
