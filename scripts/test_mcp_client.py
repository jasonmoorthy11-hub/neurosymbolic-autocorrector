"""Test the MCP server by connecting as a client via stdio transport.

Runs validate_action, get_correction, and log_correction_event tools
and verifies the results match expected behavior.
"""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    print("=" * 60)
    print("MCP Autocorrector — Client Integration Test")
    print("=" * 60)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "autocorrector.mcp_server.server", "--stdio"],
        cwd="/Users/jasonmoorthy/neurosymbolic-autocorrector",
        env={"PYTHONPATH": "/Users/jasonmoorthy/neurosymbolic-autocorrector/src"},
    )

    print("\n[1] Connecting to MCP server via stdio...")
    async with (
        stdio_client(server_params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        print("    Connected and initialized!")

        # List available tools
        print("\n[2] Listing tools...")
        tools = await session.list_tools()
        for tool in tools.tools:
            print(f"    - {tool.name}: {tool.description[:80]}...")

        # List resources
        print("\n[3] Listing resources...")
        resources = await session.list_resources()
        for res in resources.resources:
            print(f"    - {res.uri}: {res.name}")

        # List prompts
        print("\n[4] Listing prompts...")
        prompts = await session.list_prompts()
        for prompt in prompts.prompts:
            print(
                f"    - {prompt.name}: "
                f"{prompt.description[:80] if prompt.description else 'N/A'}..."
            )

        # --- Test 1: Validate compliant output ---
        print("\n" + "=" * 60)
        print("[TEST 1] Validate compliant output (should ALLOW)")
        print("=" * 60)
        result = await session.call_tool(
            "validate_action",
            arguments={
                "text": "I can offer you a $25 credit for the inconvenience.",
                "action_type": "financial_compensation",
                "customer_context": {
                    "customer_id": "C-1234",
                    "plan_type": "standard_2024",
                    "account_tenure_days": 120,
                },
            },
        )
        content = result.content[0].text if result.content else "No content"
        parsed = json.loads(content)
        print(f"    Result: is_compliant={parsed['is_compliant']}, action={parsed['action']}")
        assert parsed["is_compliant"] is True, f"Expected compliant, got {parsed}"
        assert parsed["action"] == "allow"
        print("    PASSED!")

        # --- Test 2: Validate unauthorized credit (canonical scenario 1) ---
        print("\n" + "=" * 60)
        print("[TEST 2] Unauthorized credit — new account (should CORRECT)")
        print("=" * 60)
        result = await session.call_tool(
            "validate_action",
            arguments={
                "text": "I'll go ahead and issue you a $75 credit right now.",
                "action_type": "financial_compensation",
                "customer_context": {
                    "customer_id": "C-5678",
                    "plan_type": "standard_2024",
                    "account_tenure_days": 30,
                },
            },
        )
        content = result.content[0].text if result.content else "No content"
        parsed = json.loads(content)
        print(f"    Result: is_compliant={parsed['is_compliant']}, action={parsed['action']}")
        print(f"    Violations: {len(parsed['violations'])}")
        for v in parsed["violations"]:
            print(f"      - [{v['severity']}] {v['rule_id']}: {v['description']}")
        assert parsed["is_compliant"] is False
        assert parsed["action"] == "correct"
        print("    PASSED!")

        # --- Test 3: Get correction for the unauthorized credit ---
        print("\n" + "=" * 60)
        print("[TEST 3] Get correction for unauthorized credit")
        print("=" * 60)
        result = await session.call_tool(
            "get_correction",
            arguments={
                "text": "I'll go ahead and issue you a $75 credit right now.",
                "violations": parsed["violations"],
                "customer_context": {
                    "customer_id": "C-5678",
                    "plan_type": "standard_2024",
                    "account_tenure_days": 30,
                },
                "intent_type": "financial_compensation",
            },
        )
        content = result.content[0].text if result.content else "No content"
        correction = json.loads(content)
        print(f"    Was modified: {correction['was_modified']}")
        print(f"    Original:  {correction['original_text']}")
        print(f"    Corrected: {correction['corrected_text']}")
        print(f"    Confidence: {correction['confidence']}")
        assert correction["was_modified"] is True
        print("    PASSED!")

        # --- Test 4: Validate factual error (canonical scenario 2) ---
        print("\n" + "=" * 60)
        print("[TEST 4] Factual error — wrong coverage amount (should CORRECT)")
        print("=" * 60)
        result = await session.call_tool(
            "validate_action",
            arguments={
                "text": "Your Standard plan covers up to $5,000 in emergency dental procedures.",
                "action_type": "policy_explanation",
                "customer_context": {
                    "customer_id": "C-9012",
                    "plan_type": "standard_2024",
                    "account_tenure_days": 365,
                },
            },
        )
        content = result.content[0].text if result.content else "No content"
        parsed = json.loads(content)
        print(f"    Result: is_compliant={parsed['is_compliant']}, action={parsed['action']}")
        print(f"    Fact errors: {len(parsed['fact_errors'])}")
        for f in parsed["fact_errors"]:
            print(f"      - Claim: {f['claim']} → Correct: {f['correct_value']}")
        assert parsed["is_compliant"] is False
        assert len(parsed["fact_errors"]) > 0
        print("    PASSED!")

        # --- Test 5: Get correction for factual error ---
        print("\n" + "=" * 60)
        print("[TEST 5] Get correction for factual error")
        print("=" * 60)
        result = await session.call_tool(
            "get_correction",
            arguments={
                "text": "Your Standard plan covers up to $5,000 in emergency dental procedures.",
                "fact_errors": parsed["fact_errors"],
                "customer_context": {
                    "customer_id": "C-9012",
                    "plan_type": "standard_2024",
                    "account_tenure_days": 365,
                },
                "intent_type": "policy_explanation",
            },
        )
        content = result.content[0].text if result.content else "No content"
        correction = json.loads(content)
        print(f"    Was modified: {correction['was_modified']}")
        print(f"    Original:  {correction['original_text']}")
        print(f"    Corrected: {correction['corrected_text']}")
        assert correction["was_modified"] is True
        assert "$2,500" in correction["corrected_text"]
        print("    PASSED!")

        # --- Test 6: Log correction event ---
        print("\n" + "=" * 60)
        print("[TEST 6] Log correction event")
        print("=" * 60)
        result = await session.call_tool(
            "log_correction_event",
            arguments={
                "original_text": "I'll go ahead and issue you a $75 credit right now.",
                "corrected_text": (
                    "I can offer you a $50 credit today."
                    " For additional compensation, I'd be happy to escalate."
                ),
                "was_modified": True,
                "action_taken": "correct",
                "intent_type": "financial_compensation",
                "violations": [
                    {
                        "rule_id": "CREDIT-001",
                        "rule_name": "Credit Authorization Threshold",
                        "description": "Credits over $50 require 90+ days account tenure",
                        "severity": "high",
                    }
                ],
                "confidence": 0.92,
                "agent_id": "agent-007",
                "session_id": "session-abc",
                "latency_ms": 45.2,
            },
        )
        content = result.content[0].text if result.content else "No content"
        log_result = json.loads(content)
        print(f"    Event ID: {log_result['event_id']}")
        print(f"    Status: {log_result['status']}")
        assert log_result["status"] == "logged"
        print("    PASSED!")

        # --- Test 7: Critical severity escalation ---
        print("\n" + "=" * 60)
        print("[TEST 7] Critical violation — over $500 (should ESCALATE)")
        print("=" * 60)
        result = await session.call_tool(
            "validate_action",
            arguments={
                "text": "I'll issue you a $750 credit to make up for everything.",
                "action_type": "financial_compensation",
                "customer_context": {
                    "customer_id": "C-3456",
                    "plan_type": "standard_2024",
                    "account_tenure_days": 200,
                },
            },
        )
        content = result.content[0].text if result.content else "No content"
        parsed = json.loads(content)
        print(
            f"    Result: is_compliant={parsed['is_compliant']}, "
            f"action={parsed['action']}, severity={parsed['severity']}"
        )
        for v in parsed["violations"]:
            print(f"      - [{v['severity']}] {v['rule_id']}: {v['description']}")
        assert parsed["is_compliant"] is False
        assert parsed["action"] == "escalate"
        assert parsed["severity"] == "critical"
        print("    PASSED!")

        # --- Test 8: Read a resource ---
        print("\n" + "=" * 60)
        print("[TEST 8] Read compliance rules resource")
        print("=" * 60)
        result = await session.read_resource("rules://compliance/financial_compensation")
        content = result.contents[0].text if result.contents else "No content"
        rules_data = json.loads(content)
        print(f"    Domain: {rules_data.get('domain')}")
        print(f"    Rules: {len(rules_data.get('rules', []))}")
        for r in rules_data.get("rules", []):
            print(f"      - {r['id']}: {r['name']}")
        assert len(rules_data["rules"]) >= 2
        print("    PASSED!")

        print("\n" + "=" * 60)
        print("ALL 8 TESTS PASSED!")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
