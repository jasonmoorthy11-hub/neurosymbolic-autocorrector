"""Custom NeMo Guardrails actions with quick heuristic checks."""

import re

# Heuristic patterns that indicate jailbreak attempts
JAILBREAK_PATTERNS = [
    r"ignore\s+(your|all|previous)\s+(instructions|rules|guidelines)",
    r"forget\s+(all|your|previous)\s+(instructions|rules)",
    r"you\s+are\s+now\s+(DAN|unrestricted|unfiltered)",
    r"pretend\s+(you|to)\s+(are|be|have)\s+(no|an?\s+unrestricted)",
    r"(system|initial)\s+prompt",
    r"bypass\s+(your|the|all)\s+(filters|safety|guidelines)",
    r"act\s+as\s+if\s+(you\s+have\s+no|there\s+are\s+no)",
]

# Quick check keywords for off-topic detection
OFF_TOPIC_KEYWORDS = [
    "election", "politics", "homework", "essay", "poem",
    "recipe", "meaning of life", "philosophy",
]

_compiled_jailbreak = [re.compile(p, re.IGNORECASE) for p in JAILBREAK_PATTERNS]


async def self_check_input(context: dict) -> str:
    """Quick heuristic jailbreak check before LLM classification."""
    user_input = context.get("user_message", "")
    for pattern in _compiled_jailbreak:
        if pattern.search(user_input):
            return "yes"
    return "no"


async def topic_check_input(context: dict) -> str:
    """Quick heuristic topic check before LLM classification."""
    user_input = context.get("user_message", "").lower()
    for keyword in OFF_TOPIC_KEYWORDS:
        if keyword in user_input:
            return "no"
    return "yes"
