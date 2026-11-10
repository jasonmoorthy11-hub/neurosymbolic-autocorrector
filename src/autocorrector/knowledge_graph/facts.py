"""Coverage fact checking: the amounts a reply claims against the plan's limits.

Both store paths (in-memory and Neo4j) shape their coverages the same way and
hand them to :func:`fact_errors`.
"""

from __future__ import annotations

from typing import Any

from autocorrector.knowledge_graph.claims import assign_amounts, nearest_plan
from autocorrector.models import FactCheckResult

# Category aliases for flexible fact matching
CATEGORY_ALIASES: dict[str, list[str]] = {
    "prescription drugs": [
        "prescription drug",
        "prescription medication",
        "prescription medications",
        "rx coverage",
    ],
    "emergency dental": ["dental emergency", "dental coverage"],
    "emergency room": ["er coverage", "emergency care", "er visit"],
    "specialist visit copay": ["specialist copay", "specialist copayment", "specialist visit"],
}


def plan_key(plan_type: str) -> str:
    """Fact-store key for a plan given either way ("Standard 2024" or "standard_2024")."""
    return plan_type.strip().lower().replace(" ", "_")


def mentions_for(key: str, plan_name: str) -> list[str]:
    """The phrases that name one plan: "premium 2024", its name, "premium plan"."""
    name = plan_name.lower()
    first = name.split()[0] if name.split() else key
    return list(dict.fromkeys([key.replace("_", " "), name, f"{first} plan"]))


def fact_errors(
    text: str,
    facts: list[dict[str, Any]],
    plan_type: str = "",
    others: dict[str, list[dict[str, Any]] | None] | None = None,
    plan_mentions: dict[str, list[str]] | None = None,
) -> list[FactCheckResult]:
    """Compare each amount with the limit of the category that claims it.

    ``facts`` holds ``category``, ``limit``, ``source_document`` and
    ``source_page`` per coverage of the customer's plan (``plan_type``);
    aliases come from ``CATEGORY_ALIASES``. An amount whose sentence
    names another plan is checked against that plan's entry in ``others``
    (a plan mapped to ``None`` has no coverages here, so its claims are
    left alone): "The Premium plan covers up to $5,000, while your Standard
    plan covers up to $2,500" checks each amount against its own plan.
    ``plan_mentions`` maps the plans the store knows to the phrases that name
    them; a plan missing from it is named from its key ("gold_2024" → "gold plan").
    """
    customer = plan_key(plan_type)
    by_plan: dict[str, dict[str, dict[str, Any]] | None] = {
        customer: {fact["category"].lower(): fact for fact in facts}
    }
    for key, plan_facts in (others or {}).items():
        by_plan[plan_key(key)] = (
            None if plan_facts is None else {fact["category"].lower(): fact for fact in plan_facts}
        )
    categories = {cat for by_category in by_plan.values() if by_category for cat in by_category}
    claims = assign_amounts(
        text, {cat: [cat, *CATEGORY_ALIASES.get(cat, [])] for cat in categories}
    )
    known = plan_mentions or {}
    mentions = {key: known.get(key) or mentions_for(key, key.replace("_", " ")) for key in by_plan}
    # "your plan" is the customer's, whatever it is called
    mentions[customer] = [*mentions.get(customer, []), "your plan", "your coverage"]
    results = []
    for cat, matches in claims.items():
        for match in matches:
            plan = nearest_plan(text, match, mentions) or customer
            by_category = by_plan.get(plan)
            fact = (by_category or {}).get(cat)
            if fact is None:
                continue
            limit = float(fact["limit"])
            if float(match.group(1).replace(",", "")) == limit:
                continue
            results.append(
                FactCheckResult(
                    claim=match.group(0),
                    is_accurate=False,
                    correct_value=f"${limit:,.0f}",
                    source_document=fact.get("source_document"),
                    source_page=fact.get("source_page"),
                    confidence=0.99,
                    claim_start=match.start(),
                    claim_end=match.end(),
                )
            )
    return sorted(results, key=lambda r: r.claim_start or 0)
