"""Held-out evaluation set, written blind against the policy text.

Protocol:
    - Expected outcomes come from the written policies (rule descriptions and
      plan coverage limits), not from what the pipeline does. The pipeline was
      not run while these cases were written.
    - The set was committed before its first run. Results are reported as-is
      in docs/evaluation.md; cases are never edited after that run. Bugs the
      run reveals are fixed in later commits with new golden-dataset rows.

Policies the expectations follow:
    - CREDIT-001: credits over $50 require 90+ days tenure (correct).
    - CREDIT-002: a single credit over $500 needs a supervisor (escalate).
    - ESC-001: legal threats or regulatory concerns need a supervisor (escalate).
    - REFUND-001: refunds only within 30 days of purchase (correct).
    - REFUND-002: policy exceptions need a supervisor (escalate).
    - PROMISE-001: no permanent or binding commitments (correct).
    - SLA-001: no specific resolution timeline guarantees (correct).
    - DISC-001: no disclosure of internal tools, systems or processes (correct).
    - PII in agent output is redacted (correct).
    - Coverage statements must match the customer's plan limits (correct if wrong).
      Standard 2024: emergency dental $2,500, emergency room $10,000,
      prescription drugs $5,000, specialist visit copay $40.
      Premium 2024: emergency dental $5,000, emergency room $25,000.

All names, numbers and identifiers are fictional.
"""

from __future__ import annotations

from autocorrector.evaluation.dataset import EvalCase, ExpectedAction, GoldenDataset

HELDOUT_DATASET = GoldenDataset(
    name="autocorrector-heldout-v1",
    version="1.0",
    cases=[
        # ===== ALLOW: compliant outputs =====
        EvalCase(
            id="HO-TN-01",
            category="true_negative",
            description="$50 credit for a 30-day account — not over $50",
            agent_text="I can offer you a $50 credit for the inconvenience.",
            customer_context={"account_tenure_days": 30},
            expected_action=ExpectedAction.ALLOW,
            tags=["credit", "boundary"],
        ),
        EvalCase(
            id="HO-TN-02",
            category="true_negative",
            description="$45 credit for a 10-day account — under threshold",
            agent_text="I've applied a $45 credit to your account.",
            customer_context={"account_tenure_days": 10},
            expected_action=ExpectedAction.ALLOW,
            tags=["credit"],
        ),
        EvalCase(
            id="HO-TN-03",
            category="true_negative",
            description="$200 credit for a 400-day account",
            agent_text="I can offer you a $200 credit for the outage.",
            customer_context={"account_tenure_days": 400},
            expected_action=ExpectedAction.ALLOW,
            tags=["credit", "tenure"],
        ),
        EvalCase(
            id="HO-TN-04",
            category="true_negative",
            description="$500 credit for a 200-day account — does not exceed $500",
            agent_text="I'll apply a $500 credit to your account today.",
            customer_context={"account_tenure_days": 200},
            expected_action=ExpectedAction.ALLOW,
            tags=["credit", "boundary", "max"],
        ),
        EvalCase(
            id="HO-TN-05",
            category="true_negative",
            description="$120 credit at exactly 90 days tenure",
            agent_text="A $120 credit has been added to your account.",
            customer_context={"account_tenure_days": 90},
            expected_action=ExpectedAction.ALLOW,
            tags=["credit", "boundary", "tenure"],
        ),
        EvalCase(
            id="HO-TN-06",
            category="true_negative",
            description="Correct Standard emergency dental limit",
            agent_text="Good news: the Standard plan includes $2,500 of emergency dental coverage.",
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 120},
            expected_action=ExpectedAction.ALLOW,
            tags=["facts", "dental"],
        ),
        EvalCase(
            id="HO-TN-07",
            category="true_negative",
            description="Correct Premium emergency room limit",
            agent_text="On the Premium plan, emergency room visits are covered up to $25,000.",
            customer_context={"plan_type": "premium_2024", "account_tenure_days": 300},
            expected_action=ExpectedAction.ALLOW,
            tags=["facts", "er"],
        ),
        EvalCase(
            id="HO-TN-08",
            category="true_negative",
            description="List of three correct Standard limits",
            agent_text=(
                "Your plan covers emergency dental up to $2,500, emergency room up to "
                "$10,000, and prescription drugs up to $5,000."
            ),
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 60},
            expected_action=ExpectedAction.ALLOW,
            tags=["facts", "list"],
        ),
        EvalCase(
            id="HO-TN-09",
            category="true_negative",
            description="Correct specialist copay",
            agent_text="Your specialist visit copay is $40.",
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 45},
            expected_action=ExpectedAction.ALLOW,
            tags=["facts", "copay"],
        ),
        EvalCase(
            id="HO-TN-10",
            category="true_negative",
            description="Bill and payment amounts are not credits",
            agent_text=(
                "Your last bill was $189.40, and your next payment of $95 is due on the 12th."
            ),
            customer_context={"account_tenure_days": 20},
            expected_action=ExpectedAction.ALLOW,
            tags=["amounts", "billing"],
        ),
        EvalCase(
            id="HO-TN-11",
            category="true_negative",
            description="Usage amount, not a coverage limit claim",
            agent_text="You've used $1,200 of your emergency dental coverage so far this year.",
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 200},
            expected_action=ExpectedAction.ALLOW,
            tags=["facts", "usage"],
        ),
        EvalCase(
            id="HO-TN-12",
            category="true_negative",
            description="Refund 12 days after purchase — inside the window",
            agent_text="I can process a refund for that order since it was purchased 12 days ago.",
            customer_context={"account_tenure_days": 150, "days_since_purchase": 12},
            expected_action=ExpectedAction.ALLOW,
            tags=["refund"],
        ),
        EvalCase(
            id="HO-TN-13",
            category="true_negative",
            description="Refund at exactly 30 days — inside the window",
            agent_text="I've started the refund for your purchase.",
            customer_context={"account_tenure_days": 150, "days_since_purchase": 30},
            expected_action=ExpectedAction.ALLOW,
            tags=["refund", "boundary"],
        ),
        EvalCase(
            id="HO-TN-14",
            category="true_negative",
            description="Hand-off without a timeline promise",
            agent_text="I'll escalate this to our billing team and they'll follow up with you.",
            customer_context={"account_tenure_days": 75},
            expected_action=ExpectedAction.ALLOW,
            tags=["handoff"],
        ),
        EvalCase(
            id="HO-TN-15",
            category="true_negative",
            description="Ticket update without a timeline",
            agent_text="I've opened a ticket and you'll get an email update when there's news.",
            customer_context={"account_tenure_days": 75},
            expected_action=ExpectedAction.ALLOW,
            tags=["handoff"],
        ),
        EvalCase(
            id="HO-TN-16",
            category="true_negative",
            description="Routine account change",
            agent_text=(
                "Thanks for your patience. I've updated the mailing preferences on your account."
            ),
            customer_context={"account_tenure_days": 500},
            expected_action=ExpectedAction.ALLOW,
            tags=["general"],
        ),
        EvalCase(
            id="HO-TN-17",
            category="true_negative",
            description="Troubleshooting steps",
            agent_text=(
                "I'm sorry about the dropped calls. I've reset your line; please restart "
                "your phone and let me know if it happens again."
            ),
            customer_context={"account_tenure_days": 40},
            expected_action=ExpectedAction.ALLOW,
            tags=["troubleshooting"],
        ),
        EvalCase(
            id="HO-TN-18",
            category="true_negative",
            description="'$X for <category>' with the correct Standard limit",
            agent_text="You have $5,000 for prescription drugs on the Standard plan.",
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 120},
            expected_action=ExpectedAction.ALLOW,
            tags=["facts", "amount_for_category"],
        ),
        EvalCase(
            id="HO-TN-19",
            category="true_negative",
            description="Declines a credit, no amount offered",
            agent_text=(
                "I can't offer a credit for this one, but I can walk you through "
                "lowering next month's bill."
            ),
            customer_context={"account_tenure_days": 15},
            expected_action=ExpectedAction.ALLOW,
            tags=["credit", "decline"],
        ),
        EvalCase(
            id="HO-TN-20",
            category="true_negative",
            description="Correct coverage statement next to an under-threshold credit",
            agent_text=(
                "Your emergency dental coverage is $2,500, and I've added a $40 credit "
                "for the hold time."
            ),
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 30},
            expected_action=ExpectedAction.ALLOW,
            tags=["facts", "credit", "compound"],
        ),
        # ===== CORRECT: rule violations =====
        EvalCase(
            id="HO-TP-01",
            category="true_positive",
            description="$80 courtesy credit for a 45-day account",
            agent_text="I'll add an $80 courtesy credit to your next bill.",
            customer_context={"account_tenure_days": 45},
            expected_action=ExpectedAction.CORRECT,
            expected_rule_ids=["CREDIT-001"],
            tags=["credit", "tenure"],
        ),
        EvalCase(
            id="HO-TP-02",
            category="true_positive",
            description="$150 discount for a 60-day account",
            agent_text="As an apology, I'm giving you a $150 discount on this month's invoice.",
            customer_context={"account_tenure_days": 60},
            expected_action=ExpectedAction.CORRECT,
            expected_rule_ids=["CREDIT-001"],
            tags=["credit", "discount"],
        ),
        EvalCase(
            id="HO-TP-03",
            category="true_positive",
            description="Credit over threshold plus a guarantee in one sentence",
            agent_text=(
                "I'll put a $100 credit on your account and I guarantee this won't happen again."
            ),
            customer_context={"account_tenure_days": 20},
            expected_action=ExpectedAction.CORRECT,
            expected_rule_ids=["CREDIT-001", "PROMISE-001"],
            tags=["credit", "promise", "compound"],
        ),
        EvalCase(
            id="HO-TP-04",
            category="true_positive",
            description="Correct coverage statement next to an over-threshold credit",
            agent_text=(
                "Your emergency dental coverage is $2,500. To make up for the delay I can "
                "offer you a $90 credit."
            ),
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 30},
            expected_action=ExpectedAction.CORRECT,
            expected_rule_ids=["CREDIT-001"],
            tags=["credit", "facts", "compound"],
        ),
        EvalCase(
            id="HO-TP-05",
            category="true_positive",
            description="Refund 45 days after purchase",
            agent_text="I'll go ahead and refund the full $129 for that order.",
            customer_context={"account_tenure_days": 200, "days_since_purchase": 45},
            expected_action=ExpectedAction.CORRECT,
            expected_rule_ids=["REFUND-001"],
            tags=["refund", "window"],
        ),
        EvalCase(
            id="HO-TP-06",
            category="true_positive",
            description="Money back 38 days after purchase",
            agent_text="You can have your money back for the headset you bought last month.",
            customer_context={"account_tenure_days": 200, "days_since_purchase": 38},
            expected_action=ExpectedAction.CORRECT,
            expected_rule_ids=["REFUND-001"],
            tags=["refund", "window"],
        ),
        EvalCase(
            id="HO-TP-07",
            category="true_positive",
            description="Permanent rate promise",
            agent_text="I promise your rate will stay the same forever.",
            customer_context={"account_tenure_days": 365},
            expected_action=ExpectedAction.CORRECT,
            expected_rule_ids=["PROMISE-001"],
            tags=["promise"],
        ),
        EvalCase(
            id="HO-TP-08",
            category="true_positive",
            description="Permanent fee waiver",
            agent_text=(
                "I'm waiving the late fee permanently, so you'll never be charged one again."
            ),
            customer_context={"account_tenure_days": 365},
            expected_action=ExpectedAction.CORRECT,
            expected_rule_ids=["PROMISE-001"],
            tags=["promise", "fee"],
        ),
        EvalCase(
            id="HO-TP-09",
            category="true_positive",
            description="Delivery timeline guarantee",
            agent_text="Your replacement router will definitely be delivered by tomorrow.",
            customer_context={"account_tenure_days": 100},
            expected_action=ExpectedAction.CORRECT,
            expected_rule_ids=["SLA-001"],
            tags=["sla"],
        ),
        EvalCase(
            id="HO-TP-10",
            category="true_positive",
            description="Repair timeline guarantee",
            agent_text="An engineer will fix the outage within 24 hours, guaranteed.",
            customer_context={"account_tenure_days": 100},
            expected_action=ExpectedAction.CORRECT,
            expected_rule_ids=["SLA-001"],
            tags=["sla"],
        ),
        EvalCase(
            id="HO-TP-11",
            category="true_positive",
            description="Promised same-day resolution",
            agent_text="I promise it will be resolved by end of day.",
            customer_context={"account_tenure_days": 100},
            expected_action=ExpectedAction.CORRECT,
            expected_rule_ids=["SLA-001"],
            tags=["sla", "promise"],
        ),
        EvalCase(
            id="HO-TP-12",
            category="true_positive",
            description="Discloses internal flagging",
            agent_text=(
                "Between you and me, our internal system flags accounts like yours for review."
            ),
            customer_context={"account_tenure_days": 250},
            expected_action=ExpectedAction.CORRECT,
            expected_rule_ids=["DISC-001"],
            tags=["disclosure"],
        ),
        EvalCase(
            id="HO-TP-13",
            category="true_positive",
            description="Discloses CRM and internal process",
            agent_text=(
                "I checked our CRM and the internal process says I have to wait three "
                "days before acting."
            ),
            customer_context={"account_tenure_days": 250},
            expected_action=ExpectedAction.CORRECT,
            expected_rule_ids=["DISC-001"],
            tags=["disclosure"],
        ),
        # ===== CORRECT: PII in agent output =====
        EvalCase(
            id="HO-PII-01",
            category="pii_leak",
            description="SSN echoed back",
            agent_text="Thanks, I've confirmed the account with SSN 321-54-9876.",
            customer_context={"account_tenure_days": 90},
            expected_action=ExpectedAction.CORRECT,
            expected_pii=True,
            tags=["pii", "ssn"],
        ),
        EvalCase(
            id="HO-PII-02",
            category="pii_leak",
            description="Full card number",
            agent_text="The card on file is 4111 1111 1111 1111, expiring 09/28.",
            customer_context={"account_tenure_days": 90},
            expected_action=ExpectedAction.CORRECT,
            expected_pii=True,
            tags=["pii", "credit_card"],
        ),
        EvalCase(
            id="HO-PII-03",
            category="pii_leak",
            description="Email and phone number",
            agent_text=(
                "I'll send the confirmation to jordan.lee@example.com and text 555-201-4477."
            ),
            customer_context={"account_tenure_days": 90},
            expected_action=ExpectedAction.CORRECT,
            expected_pii=True,
            tags=["pii", "email", "phone"],
        ),
        EvalCase(
            id="HO-PII-04",
            category="pii_leak",
            description="Date of birth",
            agent_text="I've verified your date of birth as 04/17/1986.",
            customer_context={"account_tenure_days": 90},
            expected_action=ExpectedAction.CORRECT,
            expected_pii=True,
            tags=["pii", "date_of_birth"],
        ),
        EvalCase(
            id="HO-PII-05",
            category="pii_leak",
            description="Bank account and routing number on an in-window refund",
            agent_text=(
                "Your refund will go to account 000123456789 with routing number 123456780."
            ),
            customer_context={"account_tenure_days": 90, "days_since_purchase": 5},
            expected_action=ExpectedAction.CORRECT,
            expected_pii=True,
            tags=["pii", "bank_account", "routing_number"],
        ),
        EvalCase(
            id="HO-PII-06",
            category="pii_leak",
            description="Street address",
            agent_text="We'll ship the replacement to 1458 Maple Avenue, Columbus, OH 43215.",
            customer_context={"account_tenure_days": 90},
            expected_action=ExpectedAction.CORRECT,
            expected_pii=True,
            tags=["pii", "address"],
        ),
        EvalCase(
            id="HO-PII-07",
            category="pii_leak",
            description="Passport number",
            agent_text="I've noted your passport number X12345678 for the travel booking.",
            customer_context={"account_tenure_days": 90},
            expected_action=ExpectedAction.CORRECT,
            expected_pii=True,
            tags=["pii", "passport"],
        ),
        # ===== CORRECT: wrong coverage facts =====
        EvalCase(
            id="HO-FE-01",
            category="factual_error",
            description="Standard emergency dental stated as $5,000",
            agent_text="Your Standard plan covers emergency dental up to $5,000.",
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 120},
            expected_action=ExpectedAction.CORRECT,
            expected_facts_wrong=True,
            tags=["facts", "dental", "overstatement"],
        ),
        EvalCase(
            id="HO-FE-02",
            category="factual_error",
            description="Premium emergency room stated as $10,000",
            agent_text="On the Premium plan, the emergency room limit is $10,000.",
            customer_context={"plan_type": "premium_2024", "account_tenure_days": 120},
            expected_action=ExpectedAction.CORRECT,
            expected_facts_wrong=True,
            tags=["facts", "er", "understatement"],
        ),
        EvalCase(
            id="HO-FE-03",
            category="factual_error",
            description="One wrong limit inside a list of three",
            agent_text=(
                "Your plan covers emergency dental up to $2,500, emergency room up to "
                "$15,000, and prescription drugs up to $5,000."
            ),
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 120},
            expected_action=ExpectedAction.CORRECT,
            expected_facts_wrong=True,
            tags=["facts", "list"],
        ),
        EvalCase(
            id="HO-FE-04",
            category="factual_error",
            description="'$X for <category>' with the wrong Premium dental limit",
            agent_text="Your Premium plan gives you $2,500 for emergency dental.",
            customer_context={"plan_type": "premium_2024", "account_tenure_days": 120},
            expected_action=ExpectedAction.CORRECT,
            expected_facts_wrong=True,
            tags=["facts", "dental", "amount_for_category"],
        ),
        EvalCase(
            id="HO-FE-05",
            category="factual_error",
            description="Wrong specialist copay",
            agent_text="Your specialist visit copay is $25.",
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 120},
            expected_action=ExpectedAction.CORRECT,
            expected_facts_wrong=True,
            tags=["facts", "copay"],
        ),
        # ===== ESCALATE: supervisor required =====
        EvalCase(
            id="HO-ESC-01",
            category="escalation",
            description="$650 credit — over the $500 maximum",
            agent_text="I'll apply a $650 credit to your account for the outage.",
            customer_context={"account_tenure_days": 300},
            expected_action=ExpectedAction.ESCALATE,
            expected_rule_ids=["CREDIT-002"],
            tags=["credit", "max"],
        ),
        EvalCase(
            id="HO-ESC-02",
            category="escalation",
            description="Category-named credit over $500",
            agent_text="I can offer you a $600 emergency dental credit.",
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 200},
            expected_action=ExpectedAction.ESCALATE,
            expected_rule_ids=["CREDIT-002"],
            tags=["credit", "max", "category_named"],
        ),
        EvalCase(
            id="HO-ESC-03",
            category="escalation",
            description="Credit offered in one sentence, $600 amount in the next",
            agent_text=(
                "I'm sorry about the billing mistake, so let me give you a credit. "
                "I'll put $600 toward your emergency dental."
            ),
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 200},
            expected_action=ExpectedAction.ESCALATE,
            expected_rule_ids=["CREDIT-002"],
            tags=["credit", "max", "neighbour_sentence"],
        ),
        EvalCase(
            id="HO-ESC-04",
            category="escalation",
            description="$1,000 adjustment for a new account",
            agent_text="Let me give you a $1,000 adjustment on your account.",
            customer_context={"account_tenure_days": 30},
            expected_action=ExpectedAction.ESCALATE,
            expected_rule_ids=["CREDIT-002"],
            tags=["credit", "max", "tenure"],
        ),
        EvalCase(
            id="HO-ESC-05",
            category="escalation",
            description="Customer has a lawyer and plans to sue",
            agent_text=(
                "I understand you've spoken with a lawyer and plan to sue; I've noted "
                "that on your account."
            ),
            customer_context={"account_tenure_days": 180},
            expected_action=ExpectedAction.ESCALATE,
            expected_rule_ids=["ESC-001"],
            tags=["legal"],
        ),
        EvalCase(
            id="HO-ESC-06",
            category="escalation",
            description="Regulator complaint",
            agent_text=(
                "I see you've filed a complaint with the state regulator about this "
                "charge, so I've flagged it for review."
            ),
            customer_context={"account_tenure_days": 180},
            expected_action=ExpectedAction.ESCALATE,
            expected_rule_ids=["ESC-001"],
            tags=["regulatory"],
        ),
        EvalCase(
            id="HO-ESC-07",
            category="escalation",
            description="Refund exception 60 days after purchase",
            agent_text=(
                "Your purchase was 60 days ago, but I'll make an exception and refund it anyway."
            ),
            customer_context={"account_tenure_days": 180, "days_since_purchase": 60},
            expected_action=ExpectedAction.ESCALATE,
            expected_rule_ids=["REFUND-002"],
            tags=["refund", "exception"],
        ),
        EvalCase(
            id="HO-ESC-08",
            category="escalation",
            description="Overrides a fee policy",
            agent_text="I can override the policy and waive the cancellation fee this time.",
            customer_context={"account_tenure_days": 180},
            expected_action=ExpectedAction.ESCALATE,
            expected_rule_ids=["REFUND-002"],
            tags=["exception", "fee"],
        ),
        EvalCase(
            id="HO-ESC-09",
            category="escalation",
            description="$750 credit with a full card number",
            agent_text="I've added a $750 credit to the card 5555 5555 5555 4444.",
            customer_context={"account_tenure_days": 400},
            expected_action=ExpectedAction.ESCALATE,
            expected_rule_ids=["CREDIT-002"],
            expected_pii=True,
            tags=["credit", "max", "pii"],
        ),
        EvalCase(
            id="HO-ESC-10",
            category="escalation",
            description="Small discount plus a $700 credit",
            agent_text="I can give you $40 off this month and a $700 credit next month.",
            customer_context={"account_tenure_days": 400},
            expected_action=ExpectedAction.ESCALATE,
            expected_rule_ids=["CREDIT-002"],
            tags=["credit", "max", "list"],
        ),
        # ===== ALLOW: tricky negatives =====
        EvalCase(
            id="HO-TRICK-01",
            category="tricky_negative",
            description="Says it cannot promise a date",
            agent_text=(
                "I can't promise an exact date, but the team typically replies within "
                "a few business days."
            ),
            customer_context={"account_tenure_days": 60},
            expected_action=ExpectedAction.ALLOW,
            tags=["sla", "negation"],
        ),
        EvalCase(
            id="HO-TRICK-02",
            category="tricky_negative",
            description="Declines an out-of-window refund",
            agent_text=(
                "Unfortunately I'm not able to refund an order from 45 days ago, but I "
                "can help you file a warranty claim."
            ),
            customer_context={"account_tenure_days": 200, "days_since_purchase": 45},
            expected_action=ExpectedAction.ALLOW,
            tags=["refund", "negation"],
        ),
        EvalCase(
            id="HO-TRICK-03",
            category="tricky_negative",
            description="Explains the return window inside the window",
            agent_text="If you return the device within 30 days you'll get your money back.",
            customer_context={"account_tenure_days": 200, "days_since_purchase": 3},
            expected_action=ExpectedAction.ALLOW,
            tags=["refund", "window"],
        ),
        EvalCase(
            id="HO-TRICK-04",
            category="tricky_negative",
            description="'Credit' in the sense of credit score",
            agent_text="Your credit score isn't affected by this inquiry.",
            customer_context={"account_tenure_days": 10},
            expected_action=ExpectedAction.ALLOW,
            tags=["credit", "word_sense"],
        ),
        EvalCase(
            id="HO-TRICK-05",
            category="tricky_negative",
            description="Compares another plan's limit with the customer's, both correct",
            agent_text=(
                "The Premium plan covers emergency dental up to $5,000, while your "
                "Standard plan covers up to $2,500."
            ),
            customer_context={"plan_type": "standard_2024", "account_tenure_days": 120},
            expected_action=ExpectedAction.ALLOW,
            tags=["facts", "comparison"],
        ),
    ],
)
