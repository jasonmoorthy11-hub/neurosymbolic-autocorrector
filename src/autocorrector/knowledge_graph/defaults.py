"""Default policy rules and coverage facts for the in-memory knowledge graph.

The Neo4j seed (``schema.py``) is generated from these, so both store paths
evaluate the same rules and facts.
"""

from __future__ import annotations

from typing import Any


def load_default_rules() -> dict[str, list[dict[str, Any]]]:
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
                    "regulator",
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
                # Granting language only: a bare "return" also means a call or a result
                "keywords": [
                    "refund",
                    "money back",
                    "accept the return",
                    "accept your return",
                    "accept a return",
                    "process the return",
                    "process your return",
                    "process a return",
                ],
                "conditions": [{"field": "days_since_purchase", "operator": "gt", "value": 30}],
            },
            {
                "rule_id": "REFUND-002",
                "rule_name": "Policy Exception Authorization",
                "description": "Policy exceptions require supervisor approval",
                "severity": "critical",
                "source_document": "Refund_Policy_2024_v2.1",
                "source_section": "Section 3.1",
                # "override" alone also describes settings and systems
                "keywords": [
                    "make an exception",
                    "policy exception",
                    "override the policy",
                    "override our policy",
                    "override policy",
                    "policy override",
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


def load_default_facts() -> dict[str, dict[str, Any]]:
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
