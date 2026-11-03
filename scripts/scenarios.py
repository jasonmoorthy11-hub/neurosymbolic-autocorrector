"""Demo scenarios shared by scripts/demo.py and scripts/record_replay.py.

STEP_SCENARIOS run step by step (intent -> PII -> policy/fact checks -> routing);
PIPELINE_SCENARIOS run through the full LangGraph pipeline only.
"""

from __future__ import annotations

from typing import Any, NamedTuple


class Scenario(NamedTuple):
    name: str
    text: str
    context: dict[str, Any]


STEP_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "Scenario 1: Credit Approval (Financial Exposure Control)",
        "I can offer you a $200 credit for the inconvenience.",
        {"account_tenure_days": 45, "customer_id": "CUST-001", "account_tier": "standard"},
    ),
    Scenario(
        "Scenario 2: Policy Explanation (Hallucination Prevention)",
        "Your plan covers up to $5,000 in emergency dental work.",
        {"plan_type": "standard_2024", "customer_id": "CUST-002"},
    ),
    Scenario(
        "Scenario 3: Compliant Output (No Correction Needed)",
        "I understand your concern. Let me look into this for you right away.",
        {"account_tenure_days": 200, "plan_type": "premium_2024"},
    ),
    Scenario(
        "Scenario 4: Critical Violation (Excessive Credit)",
        "I'll apply a $1,000 credit to your account immediately.",
        {"account_tenure_days": 30},
    ),
    Scenario(
        "Scenario 5: PII Detection (Guardrails AI Safety Rail)",
        "I've confirmed your identity. Your SSN 412-55-7890 and email"
        " sarah.jones@gmail.com are on file.",
        {"customer_id": "CUST-003", "account_tenure_days": 365},
    ),
    Scenario(
        "Scenario 6: PII + Policy Violation (Combined Guardrails)",
        "I'll issue a $300 credit to card 4532-8765-1234-9999."
        " Your SSN 123-45-6789 confirms identity.",
        {"customer_id": "CUST-004", "account_tenure_days": 15},
    ),
    Scenario(
        "Scenario 7: Expanded PII Detection (Passport + Bank Account)",
        "For the wire transfer: passport number AB1234567, account number: 55667788990.",
        {"customer_id": "CUST-005", "account_tenure_days": 100},
    ),
    Scenario(
        "Scenario 8: Unauthorized Promise (PROMISE-001)",
        "I promise you'll never be charged that fee again. It's permanently removed.",
        {"customer_id": "CUST-006", "account_tenure_days": 90},
    ),
    Scenario(
        "Scenario 9: Unauthorized Timeline (SLA-001)",
        "I guarantee this will be resolved within 24 hours.",
        {"customer_id": "CUST-007", "account_tenure_days": 120},
    ),
    Scenario(
        "Scenario 10: Internal Disclosure (DISC-001)",
        "Between you and me, our CRM shows your case was flagged as priority.",
        {"customer_id": "CUST-008", "account_tenure_days": 60},
    ),
)

PIPELINE_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "Pipeline: Unauthorized Credit",
        "I'll go ahead and issue you a $75 credit right now.",
        {"customer_id": "C-5678", "plan_type": "standard_2024", "account_tenure_days": 30},
    ),
    Scenario(
        "Pipeline: PII Redaction (SSN + Address)",
        "Your SSN: 987-65-4321 and address is 123 Main St, Springfield, IL 62704.",
        {"customer_id": "C-9999", "account_tenure_days": 200},
    ),
    Scenario(
        "Pipeline: Factual Error",
        "Your Standard plan covers up to $5,000 in emergency dental procedures.",
        {"customer_id": "C-9012", "plan_type": "standard_2024", "account_tenure_days": 365},
    ),
    Scenario(
        "Pipeline: Unauthorized Promise",
        "I guarantee you'll have lifetime access to this feature forever.",
        {"customer_id": "C-2222", "account_tenure_days": 200},
    ),
    Scenario(
        "Pipeline: Clean Output",
        "I'd be happy to help you with that. Let me check your account details.",
        {"customer_id": "C-1111", "account_tenure_days": 365},
    ),
)
