"""Knowledge Graph Store — Neo4j-backed policy knowledge graph.

Provides:
- Connection management for Neo4j
- Policy compliance checking via Cypher queries
- Fact verification against stored knowledge
- Compliant alternative generation using KG-grounded context
- Policy document ingestion (via neo4j-graphrag pipeline)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from cachetools import TTLCache

from autocorrector.llm_response import (
    SERVER_SIDE_FALLBACK_BETA,
    completed_text,
    supports_server_side_fallback,
)
from autocorrector.models import FactCheckResult, PolicyViolation, Severity

logger = logging.getLogger(__name__)

# Default model for LLM-powered corrections
DEFAULT_CORRECTION_LLM = "claude-opus-5-5"


class KnowledgeGraphStore:
    """Interface to the Neo4j knowledge graph for policy compliance and fact verification.

    In production, this connects to Neo4j and runs Cypher queries against
    the policy knowledge graph. For the MVP, it uses an in-memory rule store
    that demonstrates the same interface and correction patterns.
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

        # In-memory policy store for MVP (replaced by Neo4j queries in production)
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

    async def connect(self) -> None:
        """Connect to Neo4j. Falls back to in-memory store if no URI configured."""
        if self._uri:
            try:
                from neo4j import AsyncGraphDatabase

                self._driver = AsyncGraphDatabase.driver(
                    self._uri, auth=(self._user, self._password)
                )
                await self._driver.verify_connectivity()
                self._connected = True
                logger.info(f"Connected to Neo4j at {self._uri}")
            except Exception as e:
                logger.warning(f"Neo4j connection failed ({e}), using in-memory store")
                self._connected = False
        else:
            logger.info("No Neo4j URI configured, using in-memory policy store")
            self._connected = False

    async def close(self) -> None:
        """Close Neo4j connection."""
        if self._driver:
            await self._driver.close()
            logger.info("Neo4j connection closed")

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
        """Check text against policy rules for the given intent type.

        In production, runs Cypher queries like:
            MATCH (p:Policy)-[:HAS_RULE]->(r:Rule)-[:TRIGGERS]->(a:Action)
            WHERE r.intent_type = $intent_type
            RETURN r, a
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
        """Query Neo4j for applicable rules and check compliance.

        Fetches rules matching the intent type, evaluates amount thresholds
        and conditions, and returns any violations found.
        """
        violations = []
        text_lower = text.lower()

        async with self._driver.session() as session:
            # Get rules with their conditions
            result = await session.run(
                """
                MATCH (p:Policy)-[:HAS_RULE]->(r:Rule)
                WHERE r.intent_type = $intent_type AND r.active = true
                OPTIONAL MATCH (r)-[:HAS_CONDITION]->(c:Condition)
                RETURN r.rule_id AS rule_id, r.name AS name, r.description AS description,
                       r.severity AS severity, p.name AS source_document,
                       r.section AS source_section, r.amount_threshold AS amount_threshold,
                       r.keywords AS keywords,
                       collect(CASE WHEN c IS NOT NULL
                           THEN {field: c.field, operator: c.operator, value: c.value}
                       END) AS conditions
                """,
                intent_type=intent_type,
            )
            rules = [record.data() async for record in result]

        for rule in rules:
            # Step 1: Check keyword triggers
            keywords = rule.get("keywords") or []
            has_keyword = any(kw in text_lower for kw in keywords)
            if not has_keyword:
                continue

            # Step 2: Check amount threshold
            amount_threshold = rule.get("amount_threshold")
            if amount_threshold is not None:
                amounts = re.findall(r"\$(\d+(?:,\d{3})*(?:\.\d{2})?)", text)
                amount_exceeded = any(float(a.replace(",", "")) > amount_threshold for a in amounts)
                if not amount_exceeded:
                    continue

            # Step 3: Check conditions
            conditions = [c for c in (rule.get("conditions") or []) if c is not None]
            if conditions:
                all_met = True
                for cond in conditions:
                    actual = customer_context.get(cond["field"])
                    if actual is None:
                        all_met = False
                        break
                    op = cond["operator"]
                    val = cond["value"]
                    if (
                        op == "lt"
                        and not (actual < val)
                        or op == "gt"
                        and not (actual > val)
                        or op == "eq"
                        and actual != val
                    ):
                        all_met = False
                        break
                if not all_met:
                    continue

            violations.append(
                PolicyViolation(
                    rule_id=rule["rule_id"],
                    rule_name=rule["name"],
                    description=rule["description"],
                    severity=Severity(rule.get("severity", "medium")),
                    source_document=rule.get("source_document"),
                    source_section=rule.get("source_section"),
                )
            )
        return violations

    # Patterns indicating the agent is NOT actively making an offer
    _NON_OFFER_PATTERNS = [
        r"(?:unable|cannot|can'?t|won'?t|wouldn'?t|couldn'?t)\s+(?:to\s+)?(?:offer|provide|give|apply|issue)",
        r"\bcompetitor",
        r"(?:already|previously)\s+(?:applied|issued|given|credited|processed)",
        r"(?:was|were|has been|had been)\s+(?:applied|issued|given|credited)",
        r"(?:if\s+(?:you were|approved|eligible))|(?:could\s+be\s+eligible)",
        r"(?:policy|policies|credits?)\s+(?:allows?|states?|limits?|requires?)",
    ]

    # Patterns indicating hedged/non-guarantee timeline references (skip SLA-001)
    _NON_GUARANTEE_PATTERNS = [
        r"\bshould\b",
        r"\btypically\b",
        r"\busually\b",
        r"\bmay\b",
        r"\bmight\b",
        r"\bcould\b",
        r"\bhappy to have\b",
        r"\bestimate[ds]?\b",
        r"\bapproximately\b",
        r"\bgenerally\b",
        r"\bnormally\b",
        r"\bcan'?t guarantee\b",
        r"\bno guarantee\b",
    ]

    # Word-to-number mapping for amounts expressed in words
    _WORD_AMOUNTS = {
        "fifty": 50,
        "sixty": 60,
        "seventy": 70,
        "eighty": 80,
        "ninety": 90,
        "seventy five": 75,
        "seventy-five": 75,
        "one hundred": 100,
        "two hundred": 200,
        "three hundred": 300,
        "four hundred": 400,
        "five hundred": 500,
        "one thousand": 1000,
    }

    def _check_compliance_inmemory(
        self, text: str, intent_type: str, customer_context: dict[str, Any]
    ) -> list[PolicyViolation]:
        """Check compliance against in-memory rule store.

        A rule triggers only when ALL of these are true:
        1. At least one keyword matches (and text is not a non-offer context for credit rules)
        2. The extracted dollar amount exceeds the amount_threshold (if defined)
        3. All conditions are met (e.g., tenure < 90 days)
        If a rule has no conditions, only keyword + amount_threshold are checked.
        """
        violations = []
        text_lower = text.lower()

        # Detect non-offer contexts (agent describing, not offering)
        is_non_offer = any(re.search(pat, text_lower) for pat in self._NON_OFFER_PATTERNS)

        # Detect hedged/non-guarantee timeline references
        is_hedged = any(re.search(pat, text_lower) for pat in self._NON_GUARANTEE_PATTERNS)

        # Check intent-specific rules + universal rules that apply to all intents
        rules = list(self._policy_rules.get(intent_type, []))
        for universal_rule in self._policy_rules.get("_universal", []):
            if not any(r["rule_id"] == universal_rule["rule_id"] for r in rules):
                rules.append(universal_rule)

        for rule in rules:
            # Step 1: Check keyword triggers
            has_keyword = any(kw in text_lower for kw in rule.get("keywords", []))
            if not has_keyword:
                continue

            # Step 1b: Skip credit rules if non-offer context detected
            if is_non_offer and rule.get("rule_id", "").startswith("CREDIT"):
                continue

            # Step 1c: Skip SLA rules if hedged/non-guarantee language detected
            if is_hedged and rule.get("rule_id", "").startswith("SLA"):
                continue

            # Step 2: Check amount threshold (if defined)
            if "amount_threshold" in rule:
                # Parse $X amounts
                amounts = re.findall(r"\$(\d+(?:,\d{3})*(?:\.\d{2})?)", text)
                # Parse bare "N dollar(s)" or "N credit" without $ prefix
                bare = re.findall(
                    r"(?:^|\s)(\d+(?:,\d{3})*(?:\.\d{2})?)\s+(?:dollar|credit|compensation|adjustment)",
                    text_lower,
                )
                amounts.extend(bare)

                parsed = [float(a.replace(",", "")) for a in amounts]

                # Check word amounts
                for word, val in self._WORD_AMOUNTS.items():
                    if word in text_lower:
                        parsed.append(val)

                amount_exceeded = any(a > rule["amount_threshold"] for a in parsed)

                # Check sum of multiple amounts (e.g., $30 + $40 = $70)
                # Only for lower thresholds — avoids mixing credit and non-credit amounts
                if not amount_exceeded and len(parsed) >= 2 and rule["amount_threshold"] < 500:
                    amount_exceeded = sum(parsed) > rule["amount_threshold"]

                if not amount_exceeded:
                    continue

            # Step 3: Check all conditions (must ALL be true)
            conditions = rule.get("conditions", [])
            if conditions:
                all_conditions_met = True
                for condition in conditions:
                    field = condition["field"]
                    operator = condition["operator"]
                    threshold = condition["value"]
                    actual_value = customer_context.get(field)

                    # Missing context defaults to 0 (conservative: unknown = new/recent)
                    if actual_value is None:
                        actual_value = 0

                    if (
                        operator == "lt"
                        and not (actual_value < threshold)
                        or operator == "gt"
                        and not (actual_value > threshold)
                        or operator == "eq"
                        and actual_value != threshold
                    ):
                        all_conditions_met = False
                        break

                if not all_conditions_met:
                    continue

            # All checks passed — this is a violation
            violations.append(
                PolicyViolation(
                    rule_id=rule["rule_id"],
                    rule_name=rule["rule_name"],
                    description=rule["description"],
                    severity=Severity(rule["severity"]),
                    source_document=rule.get("source_document"),
                    source_section=rule.get("source_section"),
                )
            )

        return violations

    # --- Fact Verification ---

    async def verify_facts(
        self,
        text: str,
        customer_context: dict[str, Any],
    ) -> list[FactCheckResult]:
        """Verify factual claims in the text against the knowledge graph.

        In production, extracts claims, queries Neo4j for ground truth,
        and returns mismatches.
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
        results = []
        plan_type = customer_context.get("plan_type")

        if not plan_type:
            return results

        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (plan:Product {name: $plan_type})-[:HAS_COVERAGE]->(cov:Coverage)
                RETURN cov.category AS category, cov.limit AS coverage_limit,
                       cov.source_document AS source_doc, cov.source_page AS source_page
                """,
                plan_type=plan_type,
            )
            coverages = [record.data() async for record in result]

        amount_pattern = r"\$(\d+(?:,\d{3})*(?:\.\d{2})?)"
        text_lower = text.lower()

        for cov in coverages:
            category_lower = cov["category"].lower()
            if category_lower in text_lower:
                matches = re.findall(amount_pattern, text)
                for match in matches:
                    claimed = float(match.replace(",", ""))
                    actual = float(cov["coverage_limit"])
                    if claimed != actual:
                        results.append(
                            FactCheckResult(
                                claim=f"${match}",
                                is_accurate=False,
                                correct_value=f"${actual:,.0f}",
                                source_document=cov.get("source_doc"),
                                source_page=cov.get("source_page"),
                                confidence=0.99,
                            )
                        )

        return results

    # Category aliases for flexible fact matching
    _CATEGORY_ALIASES = {
        "prescription drugs": [
            "prescription drug",
            "prescription medication",
            "prescription medications",
            "rx coverage",
        ],
        "emergency dental": ["dental emergency", "dental coverage"],
        "emergency room": ["er coverage", "emergency care", "er visit"],
        "specialist visit copay": ["specialist copay", "specialist visit"],
    }

    def _verify_facts_inmemory(
        self, text: str, customer_context: dict[str, Any]
    ) -> list[FactCheckResult]:
        """Verify facts against in-memory fact store."""
        results = []
        plan_type = customer_context.get("plan_type", "").lower()

        if not plan_type or plan_type not in self._fact_store:
            return results

        plan_facts = self._fact_store[plan_type]

        amount_pattern = r"\$(\d+(?:,\d{3})*(?:\.\d{2})?)"
        text_lower = text.lower()

        for fact in plan_facts.get("coverages", []):
            category_lower = fact["category"].lower()
            variants = [category_lower] + self._CATEGORY_ALIASES.get(category_lower, [])

            if any(v in text_lower for v in variants):
                matches = re.findall(amount_pattern, text)
                for match in matches:
                    claimed = float(match.replace(",", ""))
                    actual = fact["limit"]
                    if claimed != actual:
                        results.append(
                            FactCheckResult(
                                claim=f"${match}",
                                is_accurate=False,
                                correct_value=f"${actual:,.0f}",
                                source_document=fact.get("source_document"),
                                source_page=fact.get("source_page"),
                                confidence=0.99,
                            )
                        )

        return results

    # --- Correction Generation ---

    async def generate_compliant_alternative(
        self,
        original_text: str,
        violations: list[dict[str, Any]],
        intent_type: str,
        customer_context: dict[str, Any],
        strategies: list[dict[str, Any]] | None = None,
    ) -> str:
        """Generate a policy-compliant alternative to the original text.

        Uses KG context to produce a corrected version that:
        - Addresses all violations
        - Preserves conversational tone
        - Offers appropriate alternatives (escalation, lower amount, etc.)
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

        corrected = original_text

        # Tier 1: Try Neo4j correction templates (per-violation)
        if self._connected:
            for violation in violations:
                rule_id = violation.get("rule_id", "")
                correction = await self._get_correction_from_neo4j(rule_id)
                if correction:
                    corrected = correction
            if corrected != original_text:
                self._correction_cache[key] = corrected
                return corrected

        # Tier 2: Try LLM-powered correction (all violations at once for coherent rewrite)
        llm_correction = await self._get_llm_correction(
            original_text, violations, intent_type, customer_context, strategies=strategies
        )
        if llm_correction:
            self._correction_cache[key] = llm_correction
            return llm_correction

        # Tier 3: Hardcoded template fallback (per-violation)
        for violation in violations:
            rule_id = violation.get("rule_id", "")
            correction = self._get_template_correction(rule_id, customer_context, original_text)
            if correction:
                corrected = correction

        self._correction_cache[key] = corrected
        return corrected

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
        """Get a template-based correction for a rule violation."""

        corrections = {
            "CREDIT-001": lambda: (
                "I understand your frustration. Based on your account status, "
                "I can offer you a $50 credit today. For additional compensation, "
                "I'd be happy to escalate this to a supervisor who can review your case further."
            ),
            "CREDIT-002": lambda: (
                "I'd like to help resolve this for you. Let me connect you with a supervisor "
                "who has the authorization to review credits of this amount. They'll be able "
                "to assist you right away."
            ),
            "REFUND-001": lambda: (
                "I understand you'd like a refund. Let me check the details of your purchase "
                "and our current refund policy to find the best option for you."
            ),
            "PROMISE-001": lambda: (
                "I want to help resolve this for you. While I can't make guarantees about "
                "future policies, I can ensure your current concern is addressed and escalate "
                "for further review if needed."
            ),
            "SLA-001": lambda: (
                "I'll prioritize getting this resolved for you as quickly as possible. "
                "While I can't guarantee a specific timeline, I'll make sure your case "
                "receives prompt attention."
            ),
            "DISC-001": lambda: (
                "Let me look into that for you. I'll work on getting this resolved "
                "and follow up with the next steps."
            ),
        }

        correction_fn = corrections.get(rule_id)
        return correction_fn() if correction_fn else None

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
                """,
                domain=domain,
            )
            rules = [record.data() async for record in result]
        return rules

    def _get_rules_inmemory(self, domain: str) -> list[dict[str, Any]]:
        """Get rules from the in-memory store for a domain."""
        raw_rules = self._policy_rules.get(domain, [])
        return [
            {
                "id": r["rule_id"],
                "name": r["rule_name"],
                "description": r["description"],
                "severity": r["severity"],
            }
            for r in raw_rules
        ]

    # --- Default data for MVP ---

    @staticmethod
    def _load_default_rules() -> dict[str, list[dict[str, Any]]]:
        """Load default policy rules for the MVP in-memory store.

        These mirror the canonical scenarios:
        - Credit approval requires 90+ days tenure for amounts > $50
        - Maximum single credit is $500 without supervisor approval
        """
        return {
            "financial_compensation": [
                {
                    "rule_id": "CREDIT-001",
                    "rule_name": "Credit Authorization Threshold",
                    "description": "Credits over $50 require 90+ days account tenure",
                    "severity": "high",
                    "source_document": "Credit_Policy_2024_v3.2",
                    "source_section": "Section 4.1",
                    "keywords": [
                        "credit",
                        "compensation",
                        "offer you",
                        "adjustment",
                        "complimentary",
                        "discount",
                    ],
                    "amount_threshold": 50,
                    "conditions": [{"field": "account_tenure_days", "operator": "lt", "value": 90}],
                },
                {
                    "rule_id": "CREDIT-002",
                    "rule_name": "Maximum Credit Amount",
                    "description": "Single credit cannot exceed $500 without supervisor approval",
                    "severity": "critical",
                    "source_document": "Credit_Policy_2024_v3.2",
                    "source_section": "Section 4.3",
                    "keywords": [
                        "credit",
                        "compensation",
                        "offer you",
                        "adjustment",
                        "complimentary",
                        "discount",
                    ],
                    "amount_threshold": 500,
                    "conditions": [],
                },
            ],
            "escalation_request": [
                {
                    "rule_id": "ESC-001",
                    "rule_name": "Legal Threat Escalation",
                    "description": (
                        "Legal threats or regulatory concerns require immediate supervisor review"
                    ),
                    "severity": "critical",
                    "source_document": "Escalation_Policy_2024_v1.0",
                    "source_section": "Section 1.1",
                    "keywords": [
                        "legal action",
                        "attorney",
                        "lawyer",
                        "lawsuit",
                        "litigation",
                        "regulatory",
                        "sue",
                    ],
                    "conditions": [],
                },
            ],
            "refund_processing": [
                {
                    "rule_id": "REFUND-001",
                    "rule_name": "Refund Window",
                    "description": "Refunds must be processed within 30 days of purchase",
                    "severity": "high",
                    "source_document": "Refund_Policy_2024_v2.1",
                    "source_section": "Section 2.1",
                    "keywords": ["refund", "money back", "return"],
                    "conditions": [{"field": "days_since_purchase", "operator": "gt", "value": 30}],
                },
                {
                    "rule_id": "REFUND-002",
                    "rule_name": "Policy Exception Authorization",
                    "description": "Policy exceptions require supervisor approval",
                    "severity": "critical",
                    "source_document": "Refund_Policy_2024_v2.1",
                    "source_section": "Section 3.1",
                    "keywords": [
                        "make an exception",
                        "policy exception",
                        "override",
                        "outside the window",
                        "outside the 30",
                    ],
                    "conditions": [],
                },
            ],
            "_universal": [
                {
                    "rule_id": "PROMISE-001",
                    "rule_name": "Unauthorized Commitment",
                    "description": (
                        "Agents cannot make permanent or binding commitments without authorization"
                    ),
                    "severity": "high",
                    "source_document": "Agent_Conduct_Policy_2024_v1.0",
                    "source_section": "Section 2.3",
                    "keywords": [
                        "i promise",
                        "i guarantee",
                        "permanently",
                        "forever",
                        "never again",
                        "lifetime",
                        "waive permanently",
                        "always will",
                        "you'll never",
                        "won't ever",
                    ],
                    "conditions": [],
                },
                {
                    "rule_id": "SLA-001",
                    "rule_name": "Unauthorized Timeline Guarantee",
                    "description": (
                        "Agents cannot promise specific resolution timelines without authorization"
                    ),
                    "severity": "high",
                    "source_document": "SLA_Policy_2024_v1.0",
                    "source_section": "Section 1.2",
                    "keywords": [
                        "within 24 hours",
                        "by tomorrow",
                        "within the hour",
                        "by end of day",
                        "guaranteed by",
                        "i promise it will be",
                        "will definitely be",
                        "no later than",
                        "within 48 hours",
                        "by next week",
                    ],
                    "conditions": [],
                },
                {
                    "rule_id": "DISC-001",
                    "rule_name": "Internal Process Disclosure",
                    "description": (
                        "Agents must not disclose internal tools, systems, or process details"
                    ),
                    "severity": "medium",
                    "source_document": "Agent_Conduct_Policy_2024_v1.0",
                    "source_section": "Section 5.1",
                    "keywords": [
                        "our internal system",
                        "internal tool",
                        "internal database",
                        "our crm",
                        "our salesforce",
                        "internal process",
                        "internal policy",
                        "between you and me",
                        "not supposed to tell",
                        "off the record",
                    ],
                    "conditions": [],
                },
            ],
        }

    @staticmethod
    def _load_default_facts() -> dict[str, dict[str, Any]]:
        """Load default fact store for the MVP.

        These mirror the canonical scenario:
        - Standard plan: $2,500 emergency dental (not $5,000)
        """
        return {
            "standard_2024": {
                "plan_name": "Standard 2024",
                "coverages": [
                    {
                        "category": "emergency dental",
                        "limit": 2500,
                        "source_document": "Policy_Document_Standard_2024.pdf",
                        "source_page": 12,
                    },
                    {
                        "category": "emergency room",
                        "limit": 10000,
                        "source_document": "Policy_Document_Standard_2024.pdf",
                        "source_page": 8,
                    },
                    {
                        "category": "prescription drugs",
                        "limit": 5000,
                        "source_document": "Policy_Document_Standard_2024.pdf",
                        "source_page": 15,
                    },
                    {
                        "category": "specialist visit copay",
                        "limit": 40,
                        "source_document": "Policy_Document_Standard_2024.pdf",
                        "source_page": 22,
                    },
                ],
            },
            "premium_2024": {
                "plan_name": "Premium 2024",
                "coverages": [
                    {
                        "category": "emergency dental",
                        "limit": 5000,
                        "source_document": "Policy_Document_Premium_2024.pdf",
                        "source_page": 12,
                    },
                    {
                        "category": "emergency room",
                        "limit": 25000,
                        "source_document": "Policy_Document_Premium_2024.pdf",
                        "source_page": 8,
                    },
                ],
            },
        }
