"""Knowledge Graph Store — Neo4j-backed policy knowledge graph.

Provides:
- Connection management for Neo4j
- Policy compliance checking via Cypher queries
- Fact verification against stored knowledge
- Compliant alternative generation using KG-grounded context

Rule evaluation, fact checking, the whole-message templates and the default
data live in ``rules.py``, ``facts.py``, ``templates.py`` and ``defaults.py``;
ingestion is in ``ingestion.py``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from cachetools import TTLCache

if TYPE_CHECKING:
    from autocorrector.config import Settings

from autocorrector.knowledge_graph.defaults import load_default_facts, load_default_rules
from autocorrector.knowledge_graph.facts import (
    CATEGORY_ALIASES,
    fact_errors,
    mentions_for,
    plan_key,
)
from autocorrector.knowledge_graph.rewriter import RewriteResult, rewrite
from autocorrector.knowledge_graph.rules import offer_amounts, violations_for
from autocorrector.knowledge_graph.templates import (
    CORRECTION_TEMPLATES,
    by_severity,
    cite_all,
    whole_text_result,
)
from autocorrector.llm_response import (
    SERVER_SIDE_FALLBACK_BETA,
    completed_text,
    supports_server_side_fallback,
)
from autocorrector.models import (
    FactCheckResult,
    PolicyViolation,
)

logger = logging.getLogger(__name__)

# Default model for LLM-powered corrections
DEFAULT_CORRECTION_LLM = "claude-opus-5-5"


class KnowledgeGraphStore:
    """Policy compliance, fact verification and corrections over one rule set.

    Without a Neo4j URI (the default, and the offline path the demo, evaluation
    and replay scripts use) the rules and facts come from ``defaults.py`` in memory. With
    a URI and a reachable server, rules, facts and correction templates are read
    from the graph and evaluated by the same code.
    """

    def __init__(
        self,
        neo4j_uri: str | None = None,
        neo4j_user: str | None = None,
        neo4j_password: str | None = None,
        anthropic_api_key: str | None = None,
        correction_llm: str | None = None,
    ):
        self._uri = neo4j_uri
        self._user = neo4j_user
        self._password = neo4j_password
        self._driver = None
        self._connected = False

        # LLM correction settings
        self._anthropic_api_key = anthropic_api_key or ""
        self._correction_llm = correction_llm or DEFAULT_CORRECTION_LLM
        self._anthropic_client: Any = None

        # In-memory rules and facts: the whole store offline, and with Neo4j still
        # what the span rewriter and the coverage exemption read
        self._policy_rules = self._load_default_rules()
        self._fact_store = self._load_default_facts()

        # TTL caches to reduce Neo4j round trips
        self._rule_cache: TTLCache = TTLCache(maxsize=128, ttl=3600)  # 1 hour
        self._fact_cache: TTLCache = TTLCache(maxsize=256, ttl=86400)  # 24 hours
        self._correction_cache: TTLCache = TTLCache(maxsize=64, ttl=3600)  # 1 hour

    @property
    def is_connected(self) -> bool:
        """Whether the store is connected to Neo4j."""
        return self._connected

    @property
    def is_configured(self) -> bool:
        """Whether a Neo4j URI has been set."""
        return bool(self._uri)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> KnowledgeGraphStore:
        """The store a long-running service uses: Neo4j and LLM settings from the
        environment (``AUTOCORRECTOR_NEO4J_*``, ``AUTOCORRECTOR_ANTHROPIC_API_KEY``).

        Scripts that must stay offline (demo, evaluate, record_replay) construct
        ``KnowledgeGraphStore()`` directly instead.
        """
        from autocorrector.config import get_settings

        s = settings or get_settings()
        return cls(
            neo4j_uri=s.neo4j_uri or None,
            neo4j_user=s.neo4j_user,
            neo4j_password=s.neo4j_password,
            anthropic_api_key=s.anthropic_api_key,
            correction_llm=s.correction_llm,
        )

    async def connect(self) -> None:
        """Connect to Neo4j once; falls back to the in-memory store without a URI
        or when the server cannot be reached. Safe to call again: a connected
        store keeps its driver."""
        if self._connected:
            return
        if self._uri:
            driver = None
            try:
                from neo4j import AsyncGraphDatabase

                driver = AsyncGraphDatabase.driver(self._uri, auth=(self._user, self._password))
                await driver.verify_connectivity()
                self._driver = driver
                self._connected = True
                logger.info(f"Connected to Neo4j at {self._uri}")
            except Exception as e:
                logger.warning(f"Neo4j connection failed ({e}), using in-memory store")
                self._connected = False
                if driver is not None:
                    await driver.close()
        else:
            logger.info("No Neo4j URI configured, using in-memory policy store")
            self._connected = False

    async def close(self) -> None:
        """Close the Neo4j connection; a later ``connect()`` opens a new driver."""
        if self._driver:
            await self._driver.close()
            logger.info("Neo4j connection closed")
        self._driver = None
        self._connected = False

    @staticmethod
    def _cache_key(*args: Any) -> str:
        """Build a deterministic cache key from arguments."""
        raw = json.dumps(args, sort_keys=True, default=str)
        return hashlib.md5(raw.encode()).hexdigest()

    def clear_cache(self) -> None:
        """Clear all TTL caches."""
        self._rule_cache.clear()
        self._fact_cache.clear()
        self._correction_cache.clear()

    # --- Policy Compliance ---

    async def check_policy_compliance(
        self,
        text: str,
        intent_type: str,
        customer_context: dict[str, Any],
    ) -> list[PolicyViolation]:
        """Check text against every active policy rule.

        Each rule runs on its own triggers and keywords; ``intent_type`` only
        orders the classified domain's rules first (it stopped gating them in v1.1).
        """
        key = self._cache_key("compliance", text, intent_type, customer_context)
        if key in self._rule_cache:
            logger.debug("Cache hit: check_policy_compliance")
            return self._rule_cache[key]

        if self._connected:
            result = await self._check_compliance_neo4j(text, intent_type, customer_context)
        else:
            result = self._check_compliance_inmemory(text, intent_type, customer_context)

        self._rule_cache[key] = result
        return result

    async def _check_compliance_neo4j(
        self, text: str, intent_type: str, customer_context: dict[str, Any]
    ) -> list[PolicyViolation]:
        """Query Neo4j for every active rule and check compliance.

        The rules are evaluated by the same code as the in-memory ones, so the
        two paths trigger identically for the same rule set.
        """
        async with self._driver.session() as session:
            # Every active rule, with its conditions; as in memory, the classified
            # intent only puts its domain's rules first
            result = await session.run(
                """
                MATCH (p:Policy)-[:HAS_RULE]->(r:Rule)
                WHERE r.active = true
                OPTIONAL MATCH (r)-[:HAS_CONDITION]->(c:Condition)
                RETURN r.rule_id AS rule_id, r.name AS name, r.description AS description,
                       r.severity AS severity, p.name AS source_document,
                       r.section AS source_section, r.amount_threshold AS amount_threshold,
                       r.keywords AS keywords, r.intent_type AS intent_type,
                       collect(CASE WHEN c IS NOT NULL
                           THEN {field: c.field, operator: c.operator, value: c.value}
                       END) AS conditions
                ORDER BY CASE WHEN r.intent_type = $intent_type THEN 0 ELSE 1 END, r.rule_id
                """,
                intent_type=intent_type,
            )
            rules = [record.data() async for record in result]

        # Same shape as the in-memory rules, so one evaluator (with the non-offer,
        # hedged, bare and word amount handling) serves both paths
        shaped = [
            {
                "rule_id": rule["rule_id"],
                "rule_name": rule["name"],
                "description": rule["description"],
                "severity": rule.get("severity") or "medium",
                "source_document": rule.get("source_document"),
                "source_section": rule.get("source_section"),
                "keywords": list(rule.get("keywords") or []),
                "conditions": [c for c in (rule.get("conditions") or []) if c is not None],
                **(
                    {"amount_threshold": rule["amount_threshold"]}
                    if rule.get("amount_threshold") is not None
                    else {}
                ),
            }
            for rule in rules
        ]
        return self._violations_for(shaped, text, customer_context)

    def _offer_amounts(
        self, text: str, keywords: list[str], customer_context: dict[str, Any]
    ) -> list[str]:
        """The ``$`` amounts a threshold rule with these keywords may count.

        Verified coverage statements of the customer's plan are left out
        (see :func:`rules.offer_amounts`); every other amount counts.
        """
        plan = self._fact_store.get(plan_key(str(customer_context.get("plan_type", ""))), {})
        limits = {cov["category"].lower(): float(cov["limit"]) for cov in plan.get("coverages", [])}
        return offer_amounts(text, keywords, limits, self.coverage_mentions())

    def coverage_mentions(self) -> dict[str, list[str]]:
        """Map each known coverage category to the phrases that mention it."""
        categories = {
            cov["category"].lower()
            for plan in self._fact_store.values()
            for cov in plan.get("coverages", [])
        }
        return {cat: [cat, *CATEGORY_ALIASES.get(cat, [])] for cat in categories}

    def _check_compliance_inmemory(
        self, text: str, intent_type: str, customer_context: dict[str, Any]
    ) -> list[PolicyViolation]:
        """Check compliance against in-memory rule store.

        Every rule is checked by its own triggers; the classified intent only
        puts its domain's rules first. (Gating rules on the intent dropped real
        violations whenever the classifier fell back to ``general``.)

        A rule triggers only when ALL of these are true:
        1. At least one keyword matches (and text is not a non-offer context for credit rules)
        2. The extracted dollar amount exceeds the amount_threshold (if defined)
        3. All conditions are met (e.g., tenure < 90 days)
        If a rule has no conditions, only keyword + amount_threshold are checked.
        """
        rules = list(self._policy_rules.get(intent_type, []))
        for domain, domain_rules in self._policy_rules.items():
            if domain == intent_type:
                continue
            for rule in domain_rules:
                if not any(r["rule_id"] == rule["rule_id"] for r in rules):
                    rules.append(rule)
        return self._violations_for(rules, text, customer_context)

    def _violations_for(
        self, rules: list[dict[str, Any]], text: str, customer_context: dict[str, Any]
    ) -> list[PolicyViolation]:
        """Evaluate in-memory-shaped rules against the text (both store paths)."""
        return violations_for(
            rules,
            text,
            customer_context,
            lambda keywords: self._offer_amounts(text, keywords, customer_context),
        )

    # --- Fact Verification ---

    async def verify_facts(
        self,
        text: str,
        customer_context: dict[str, Any],
    ) -> list[FactCheckResult]:
        """Verify factual claims in the text against the plan facts.

        Extracts coverage claims and returns the ones that differ from the stored
        limits (from Neo4j when connected, otherwise from memory).
        """
        key = self._cache_key("facts", text, customer_context)
        if key in self._fact_cache:
            logger.debug("Cache hit: verify_facts")
            return self._fact_cache[key]

        if self._connected:
            result = await self._verify_facts_neo4j(text, customer_context)
        else:
            result = self._verify_facts_inmemory(text, customer_context)

        self._fact_cache[key] = result
        return result

    async def _verify_facts_neo4j(
        self, text: str, customer_context: dict[str, Any]
    ) -> list[FactCheckResult]:
        """Verify facts against Neo4j knowledge graph.

        Queries the Product→Coverage relationships to check if dollar
        amounts mentioned in the text match the actual coverage limits.
        """
        plan_type = customer_context.get("plan_type")

        if not plan_type:
            return []

        # Every plan with its coverages (a plan without any is still known, so a
        # claim naming it is skipped rather than checked against the customer's
        # plan), as on the in-memory path
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (plan:Product)
                OPTIONAL MATCH (plan)-[:HAS_COVERAGE]->(cov:Coverage)
                RETURN plan.name AS plan, cov.category AS category,
                       cov.limit AS coverage_limit,
                       cov.source_document AS source_doc, cov.source_page AS source_page
                """
            )
            coverages = [record.data() async for record in result]

        by_plan: dict[str, list[dict[str, Any]]] = {}
        for cov in coverages:
            facts = by_plan.setdefault(plan_key(str(cov.get("plan", ""))), [])
            if cov.get("category") is None:
                continue
            facts.append(
                {
                    "category": cov["category"],
                    "limit": cov["coverage_limit"],
                    "source_document": cov.get("source_doc"),
                    "source_page": cov.get("source_page"),
                }
            )
        customer = plan_key(str(plan_type))
        if customer not in by_plan:
            return []
        others = {key: facts for key, facts in by_plan.items() if key != customer}
        return self._fact_errors(text, by_plan[customer], plan_type, others)

    # Category aliases for flexible fact matching
    _CATEGORY_ALIASES = CATEGORY_ALIASES

    def _verify_facts_inmemory(
        self, text: str, customer_context: dict[str, Any]
    ) -> list[FactCheckResult]:
        """Verify facts against in-memory fact store."""
        plan_type = plan_key(str(customer_context.get("plan_type", "")))

        if not plan_type or plan_type not in self._fact_store:
            return []

        others = {
            key: plan.get("coverages", [])
            for key, plan in self._fact_store.items()
            if key != plan_type
        }
        return self._fact_errors(
            text, self._fact_store[plan_type].get("coverages", []), plan_type, others
        )

    _mentions_for = staticmethod(mentions_for)

    def plan_mentions(self) -> dict[str, list[str]]:
        """Map each known plan key to the phrases that name it ("premium plan")."""
        return {
            key: self._mentions_for(key, str(plan.get("plan_name", key)))
            for key, plan in self._fact_store.items()
        }

    def _fact_errors(
        self,
        text: str,
        facts: list[dict[str, Any]],
        plan_type: str = "",
        others: dict[str, list[dict[str, Any]] | None] | None = None,
    ) -> list[FactCheckResult]:
        """Compare each amount with its category's limit (see :func:`facts.fact_errors`).

        A plan only Neo4j knows is named from its key ("gold_2024" → "gold plan").
        """
        return fact_errors(text, facts, plan_type, others, self.plan_mentions())

    # --- Correction Generation ---

    def rule_index(self) -> dict[str, dict[str, Any]]:
        """Map each rule_id to its definition (first occurrence across intents)."""
        index: dict[str, dict[str, Any]] = {}
        for rules in self._policy_rules.values():
            for rule in rules:
                index.setdefault(rule["rule_id"], rule)
        return index

    async def generate_compliant_alternative(
        self,
        original_text: str,
        violations: list[dict[str, Any]],
        intent_type: str,
        customer_context: dict[str, Any],
        strategies: list[dict[str, Any]] | None = None,
    ) -> str:
        """Generate a policy-compliant alternative to the original text."""
        result = await self.generate_compliant_alternative_with_edits(
            original_text, violations, intent_type, customer_context, strategies=strategies
        )
        return result.text

    async def generate_compliant_alternative_with_edits(
        self,
        original_text: str,
        violations: list[dict[str, Any]],
        intent_type: str,
        customer_context: dict[str, Any],
        strategies: list[dict[str, Any]] | None = None,
    ) -> RewriteResult:
        """Generate a policy-compliant alternative plus the edits that produced it.

        Tiers, first that applies wins: Neo4j correction templates (connected
        only) → LLM rewrite (API key only) → span-level rewrite of the flagged
        sentences → whole-message template for rules the span tier could not fix.
        """
        key = self._cache_key(
            "correction",
            original_text,
            violations,
            intent_type,
            customer_context,
        )
        if key in self._correction_cache:
            logger.debug("Cache hit: generate_compliant_alternative")
            return self._correction_cache[key]

        result = await self._tiered_correction(
            original_text, violations, intent_type, customer_context, strategies
        )
        self._correction_cache[key] = result
        return result

    async def _tiered_correction(
        self,
        original_text: str,
        violations: list[dict[str, Any]],
        intent_type: str,
        customer_context: dict[str, Any],
        strategies: list[dict[str, Any]] | None,
    ) -> RewriteResult:
        # Tier 1: Neo4j correction templates. As in tier 4, the whole reply is
        # replaced, so the edit cites every rule (highest severity first) and the
        # wording is the first rule's template in that order.
        if self._connected:
            ordered = by_severity(violations)
            for violation in ordered:
                correction = await self._get_correction_from_neo4j(violation.get("rule_id", ""))
                if correction and correction != original_text:
                    return whole_text_result(original_text, correction, cite_all(ordered), "neo4j")

        # Tier 2: LLM-powered correction (all violations at once for coherent rewrite)
        llm_correction = await self._get_llm_correction(
            original_text, violations, intent_type, customer_context, strategies=strategies
        )
        if llm_correction:
            cited = cite_all(by_severity(violations))
            return whole_text_result(original_text, llm_correction, cited, "llm")

        # Tier 3: span-level rewrite of only the offending sentences
        span = rewrite(original_text, violations, self.rule_index(), self.coverage_mentions())
        unresolved = set(span.unresolved)
        if span.edits:
            # A rule counts as resolved only if the rewrite no longer triggers it.
            # New violations in the rewrite are left to the pipeline's revalidation.
            still = await self.check_policy_compliance(span.text, intent_type, customer_context)
            unresolved |= {v.rule_id for v in still} & {v.get("rule_id") for v in violations}

        # Tier 4: whole-message template for rules the span tier left unresolved.
        # It replaces the whole reply, so its one edit cites every rule that fired,
        # highest severity first; the template is the first unresolved rule's in that
        # order that has one.
        ordered = by_severity(violations)
        template = next(
            (
                t
                for v in ordered
                if v.get("rule_id", "") in unresolved
                and (
                    t := self._get_template_correction(
                        v.get("rule_id", ""), customer_context, original_text
                    )
                )
            ),
            None,
        )
        if template is None:
            return span
        return whole_text_result(original_text, template, cite_all(ordered), "template")

    async def _get_correction_from_neo4j(self, rule_id: str) -> str | None:
        """Fetch a correction template from Neo4j for a given rule."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (r:Rule {rule_id: $rule_id})-[:HAS_CORRECTION]->(ct:CorrectionTemplate)
                RETURN ct.template AS template
                """,
                rule_id=rule_id,
            )
            record = await result.single()
            if record:
                return record["template"]
        return None

    def _get_anthropic_client(self) -> Any:
        """Lazily initialize the Anthropic client. Returns None if no API key."""
        if not self._anthropic_api_key:
            return None
        if self._anthropic_client is None:
            try:
                import anthropic

                self._anthropic_client = anthropic.AsyncAnthropic(api_key=self._anthropic_api_key)
            except Exception as e:
                logger.warning(f"Failed to initialize Anthropic client: {e}")
                return None
        return self._anthropic_client

    async def _get_llm_correction(
        self,
        original_text: str,
        violations: list[dict[str, Any]],
        intent_type: str,
        customer_context: dict[str, Any],
        strategies: list[dict[str, Any]] | None = None,
    ) -> str | None:
        """Generate a corrected version using Anthropic Claude. Returns None on any failure."""
        client = self._get_anthropic_client()
        if client is None:
            return None

        try:
            system_prompt = self._build_correction_system_prompt(strategies=strategies)
            user_prompt = self._build_correction_user_prompt(
                original_text, violations, intent_type, customer_context
            )

            request: dict[str, Any] = {
                "model": self._correction_llm,
                "max_tokens": 8192,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
                "output_config": {"effort": "medium"},
            }
            if supports_server_side_fallback(self._correction_llm):
                response = await client.beta.messages.create(
                    **request,
                    betas=[SERVER_SIDE_FALLBACK_BETA],
                    fallbacks="default",
                )
            else:
                response = await client.messages.create(**request)

            corrected = completed_text(response).strip()
            if not corrected:
                logger.warning("LLM returned empty correction")
                return None

            logger.info("LLM correction generated successfully")
            return corrected

        except Exception as e:
            logger.warning(f"LLM correction failed: {e}")
            return None

    @staticmethod
    def _build_correction_system_prompt(strategies: list[dict[str, Any]] | None = None) -> str:
        """Build the system prompt for the correction LLM, optionally with ACE strategies."""
        base = (
            "You are a compliance correction engine for a customer service AI system. "
            "Your job is to rewrite agent responses to fix ALL policy violations while:\n"
            "- Preserving the original conversational tone and helpfulness\n"
            "- Never fabricating information or making promises the agent cannot keep\n"
            "- Offering appropriate alternatives (escalation, lower amounts, etc.)\n"
            "- Keeping the response concise and natural\n\n"
            "Output ONLY the corrected text. Do not include explanations, prefixes, or quotes."
        )
        if strategies:
            lessons = "\n".join(f"- {s['lesson']}" for s in strategies[:10])
            base += "\n\nLEARNED STRATEGIES (from supervisor feedback — follow these):\n" + lessons
        return base

    @staticmethod
    def _build_correction_user_prompt(
        original_text: str,
        violations: list[dict[str, Any]],
        intent_type: str,
        customer_context: dict[str, Any],
    ) -> str:
        """Build the user prompt with structured violation details."""
        parts = [f"ORIGINAL TEXT:\n{original_text}\n"]
        parts.append(f"INTENT TYPE: {intent_type}\n")

        if customer_context:
            ctx_lines = [f"  {k}: {v}" for k, v in customer_context.items()]
            parts.append("CUSTOMER CONTEXT:\n" + "\n".join(ctx_lines) + "\n")

        if violations:
            parts.append("POLICY VIOLATIONS:")
            for i, v in enumerate(violations, 1):
                rule_id = v.get("rule_id", "UNKNOWN")
                severity = v.get("severity", "medium")
                desc = v.get("description", "")
                source = v.get("source_document", "")
                line = f"  {i}. [{rule_id}] (severity: {severity}) {desc}"
                if source:
                    line += f" — Source: {source}"
                parts.append(line)
            parts.append("")

        parts.append(
            "Rewrite the original text to fix all violations listed above. "
            "Maintain a helpful, empathetic tone."
        )
        return "\n".join(parts)

    def _get_template_correction(
        self, rule_id: str, customer_context: dict[str, Any], original_text: str
    ) -> str | None:
        """The whole-message template for a rule, or None.

        Templates are fixed per rule; the context and text parameters are kept
        for callers that may pass them (``guardrails/validators.py``).
        """
        return CORRECTION_TEMPLATES.get(rule_id)

    # --- Rules query ---

    async def get_rules_by_domain(self, domain: str) -> list[dict[str, Any]]:
        """Get policy rules for a specific domain/intent type.

        Queries Neo4j if connected, otherwise returns from the in-memory store.
        """
        if self._connected:
            return await self._get_rules_from_neo4j(domain)
        return self._get_rules_inmemory(domain)

    async def _get_rules_from_neo4j(self, domain: str) -> list[dict[str, Any]]:
        """Fetch rules from Neo4j for a given domain."""
        rules = []
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (p:Policy)-[:HAS_RULE]->(r:Rule)
                WHERE r.intent_type = $domain AND r.active = true
                RETURN r.rule_id AS id, r.name AS name, r.description AS description,
                       r.severity AS severity, p.name AS source_document,
                       r.section AS source_section
                ORDER BY r.rule_id
                """,
                domain=domain,
            )
            rules = [record.data() async for record in result]
        return rules

    def _get_rules_inmemory(self, domain: str) -> list[dict[str, Any]]:
        """Rules from the in-memory store for a domain, in the Neo4j query's shape and order."""
        raw_rules = self._policy_rules.get(domain, [])
        return [
            {
                "id": r["rule_id"],
                "name": r["rule_name"],
                "description": r["description"],
                "severity": r["severity"],
                "source_document": r["source_document"],
                "source_section": r["source_section"],
            }
            for r in sorted(raw_rules, key=lambda rule: rule["rule_id"])
        ]

    # --- Active policies ---

    async def get_active_policies(self) -> dict[str, Any]:
        """The policy documents behind the active rules, with their rule ids, and
        the number of coverage facts: from Neo4j when connected, else from memory."""
        if self._connected:
            by_document, total_facts = await self._active_policies_from_neo4j()
        else:
            by_document = {}
            for rules in self._policy_rules.values():
                for rule in rules:
                    by_document.setdefault(rule["source_document"], []).append(rule["rule_id"])
            total_facts = sum(len(plan["coverages"]) for plan in self._fact_store.values())
        policies = [
            {"name": name, "rules": sorted(rule_ids)}
            for name, rule_ids in sorted(by_document.items())
        ]
        return {
            "policies": policies,
            "total_rules": sum(len(p["rules"]) for p in policies),
            "total_facts": total_facts,
            "store": "neo4j" if self._connected else "memory",
        }

    async def _active_policies_from_neo4j(self) -> tuple[dict[str, list[str]], int]:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (p:Policy)-[:HAS_RULE]->(r:Rule)
                WHERE r.active = true
                RETURN p.name AS name, collect(r.rule_id) AS rules
                """
            )
            by_document = {record["name"]: list(record["rules"]) async for record in result}
            result = await session.run(
                "MATCH (:Product)-[:HAS_COVERAGE]->(c:Coverage) RETURN count(c) AS total"
            )
            record = await result.single()
        return by_document, int(record["total"]) if record else 0

    # --- Default data ---

    _load_default_rules = staticmethod(load_default_rules)
    _load_default_facts = staticmethod(load_default_facts)
