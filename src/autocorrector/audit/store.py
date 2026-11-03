"""SQLite-backed audit store for correction events."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from autocorrector.models import (
    CorrectionAction,
    CorrectionEvent,
    DetectedIntent,
    IntentType,
    PolicyViolation,
)

logger = logging.getLogger(__name__)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS correction_events (
    event_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    agent_id TEXT,
    session_id TEXT,
    original_text TEXT NOT NULL,
    corrected_text TEXT NOT NULL,
    was_modified INTEGER NOT NULL,
    action_taken TEXT NOT NULL,
    intent_type TEXT NOT NULL,
    intent_confidence REAL NOT NULL,
    violations TEXT NOT NULL,
    confidence REAL NOT NULL,
    latency_ms REAL
)
"""

REJECTION_REASONS = [
    "tone_wrong",
    "incomplete_fix",
    "over_corrected",
    "factually_wrong",
    "context_lost",
]

CREATE_DECISIONS_TABLE = """
CREATE TABLE IF NOT EXISTS supervisor_decisions (
    event_id TEXT PRIMARY KEY,
    decision TEXT NOT NULL,
    reviewer TEXT,
    comment TEXT,
    rejection_reason TEXT,
    decided_at TEXT NOT NULL
)
"""

CREATE_STRATEGIES_TABLE = """
CREATE TABLE IF NOT EXISTS correction_strategies (
    strategy_id TEXT PRIMARY KEY,
    lesson TEXT NOT NULL,
    rule_id TEXT,
    intent_type TEXT,
    source_event_id TEXT,
    applied_count INTEGER DEFAULT 0,
    helpful_count INTEGER DEFAULT 0,
    harmful_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
)
"""


class AuditStore:
    """Persistent audit store backed by SQLite via aiosqlite."""

    def __init__(self, db_path: str = "autocorrector_audit.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open the database and create tables if they don't exist."""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute(CREATE_TABLE)
        await self._db.execute(CREATE_DECISIONS_TABLE)
        await self._db.execute(CREATE_STRATEGIES_TABLE)
        await self._db.commit()
        logger.info(f"Audit store connected: {self._db_path}")

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def log_event(self, event: CorrectionEvent) -> None:
        """Insert a correction event into the database."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")

        violations_json = json.dumps([v.model_dump(mode="json") for v in event.violations])

        await self._db.execute(
            """
            INSERT INTO correction_events
                (event_id, timestamp, agent_id, session_id, original_text,
                 corrected_text, was_modified, action_taken, intent_type,
                 intent_confidence, violations, confidence, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.timestamp.isoformat(),
                event.agent_id,
                event.session_id,
                event.original_text,
                event.corrected_text,
                int(event.was_modified),
                event.action_taken.value,
                event.intent.intent_type.value,
                event.intent.confidence,
                violations_json,
                event.confidence,
                event.latency_ms,
            ),
        )
        await self._db.commit()

    async def event_exists(self, event_id: str) -> bool:
        """Check if an event exists in the database."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")
        cursor = await self._db.execute(
            "SELECT 1 FROM correction_events WHERE event_id = ?", (event_id,)
        )
        return await cursor.fetchone() is not None

    async def get_event_by_id(self, event_id: str) -> CorrectionEvent | None:
        """Retrieve a single event by ID."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")
        cursor = await self._db.execute(
            "SELECT * FROM correction_events WHERE event_id = ?", (event_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_event(row)

    async def get_events(self, limit: int = 50, offset: int = 0) -> list[CorrectionEvent]:
        """Retrieve events ordered by timestamp descending."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")

        cursor = await self._db.execute(
            "SELECT * FROM correction_events ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]

    async def get_metrics(self) -> dict[str, Any]:
        """Compute aggregated metrics from all stored events."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")

        cursor = await self._db.execute("SELECT COUNT(*) FROM correction_events")
        total = (await cursor.fetchone())[0]

        if total == 0:
            return {
                "total_validations": 0,
                "total_corrections": 0,
                "corrections_by_type": {},
                "average_confidence": 0.0,
                "average_latency_ms": 0.0,
            }

        cursor = await self._db.execute(
            "SELECT action_taken, COUNT(*) FROM correction_events GROUP BY action_taken"
        )
        action_counts = dict(await cursor.fetchall())

        cursor = await self._db.execute(
            "SELECT AVG(confidence), AVG(latency_ms) FROM correction_events"
        )
        avg_conf, avg_lat = await cursor.fetchone()

        return {
            "total_validations": total,
            "total_corrections": action_counts.get("correct", 0),
            "corrections_by_type": action_counts,
            "average_confidence": round(avg_conf or 0.0, 4),
            "average_latency_ms": round(avg_lat or 0.0, 2),
        }

    async def get_queue(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Retrieve events needing supervisor review with decision status."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")

        cursor = await self._db.execute(
            """
            SELECT ce.*, sd.decision, sd.reviewer, sd.comment, sd.rejection_reason, sd.decided_at
            FROM correction_events ce
            LEFT JOIN supervisor_decisions sd ON ce.event_id = sd.event_id
            WHERE ce.action_taken = 'escalate'
               OR ce.violations LIKE '%"severity": "critical"%'
               OR ce.violations LIKE '%"severity": "high"%'
            ORDER BY ce.timestamp DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            event = self._row_to_event(row[:13])
            decision_info = {
                "decision": row[13],
                "reviewer": row[14],
                "comment": row[15],
                "rejection_reason": row[16],
                "decided_at": row[17],
            }
            results.append({"event": event, **decision_info})
        return results

    async def record_decision(
        self,
        event_id: str,
        decision: str,
        reviewer: str | None = None,
        comment: str | None = None,
        rejection_reason: str | None = None,
    ) -> bool:
        """Record a supervisor decision for an event. Returns False if already decided."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")

        # Check event exists
        cursor = await self._db.execute(
            "SELECT event_id FROM correction_events WHERE event_id = ?",
            (event_id,),
        )
        if not await cursor.fetchone():
            return False

        # Check not already decided
        cursor = await self._db.execute(
            "SELECT event_id FROM supervisor_decisions WHERE event_id = ?",
            (event_id,),
        )
        if await cursor.fetchone():
            return False

        await self._db.execute(
            """
            INSERT INTO supervisor_decisions
                (event_id, decision, reviewer, comment, rejection_reason, decided_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                decision,
                reviewer,
                comment,
                rejection_reason,
                datetime.now(UTC).isoformat(),
            ),
        )
        await self._db.commit()
        return True

    async def get_decision(self, event_id: str) -> dict[str, Any] | None:
        """Get the supervisor decision for an event, or None if not decided."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")

        cursor = await self._db.execute(
            "SELECT event_id, decision, reviewer, comment, rejection_reason, decided_at "
            "FROM supervisor_decisions WHERE event_id = ?",
            (event_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "event_id": row[0],
            "decision": row[1],
            "reviewer": row[2],
            "comment": row[3],
            "rejection_reason": row[4],
            "decided_at": row[5],
        }

    async def get_events_filtered(
        self,
        limit: int = 50,
        offset: int = 0,
        date_from: str | None = None,
        date_to: str | None = None,
        intent_type: str | None = None,
        action: str | None = None,
        severity: str | None = None,
        agent_id: str | None = None,
    ) -> tuple[list[CorrectionEvent], int]:
        """Retrieve events with filters. Returns (events, total_count)."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")

        conditions: list[str] = []
        params: list[Any] = []

        if date_from:
            conditions.append("timestamp >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("timestamp <= ?")
            params.append(date_to)
        if intent_type:
            conditions.append("intent_type = ?")
            params.append(intent_type)
        if action:
            conditions.append("action_taken = ?")
            params.append(action)
        if severity:
            conditions.append("violations LIKE ?")
            params.append(f'%"severity": "{severity}"%')
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        # Get total count
        cursor = await self._db.execute(
            f"SELECT COUNT(*) FROM correction_events {where}",
            params,
        )
        total = (await cursor.fetchone())[0]

        # Get paginated results
        cursor = await self._db.execute(
            f"SELECT * FROM correction_events {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = await cursor.fetchall()
        events = [self._row_to_event(row) for row in rows]
        return events, total

    async def get_approval_metrics(self) -> dict[str, Any]:
        """Compute approval/rejection rates by intent type and rule."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")

        # Overall rates
        cursor = await self._db.execute(
            "SELECT decision, COUNT(*) FROM supervisor_decisions GROUP BY decision"
        )
        decision_counts = dict(await cursor.fetchall())
        total_decisions = sum(decision_counts.values())

        # Rates by intent type
        cursor = await self._db.execute(
            """
            SELECT ce.intent_type, sd.decision, COUNT(*)
            FROM supervisor_decisions sd
            JOIN correction_events ce ON sd.event_id = ce.event_id
            GROUP BY ce.intent_type, sd.decision
            """
        )
        by_intent: dict[str, dict[str, int]] = {}
        for intent, decision, cnt in await cursor.fetchall():
            by_intent.setdefault(intent, {})
            by_intent[intent][decision] = cnt

        # Rejection reasons breakdown
        cursor = await self._db.execute(
            """
            SELECT rejection_reason, COUNT(*)
            FROM supervisor_decisions
            WHERE rejection_reason IS NOT NULL
            GROUP BY rejection_reason
            """
        )
        rejection_reasons = dict(await cursor.fetchall())

        # Rates by violated rule
        cursor = await self._db.execute(
            """
            SELECT ce.violations, sd.decision
            FROM supervisor_decisions sd
            JOIN correction_events ce ON sd.event_id = ce.event_id
            """
        )
        by_rule: dict[str, dict[str, int]] = {}
        for violations_json, decision in await cursor.fetchall():
            violations = json.loads(violations_json)
            for v in violations:
                rule_id = v.get("rule_id", "unknown")
                by_rule.setdefault(rule_id, {})
                by_rule[rule_id][decision] = by_rule[rule_id].get(decision, 0) + 1

        return {
            "total_decisions": total_decisions,
            "approved": decision_counts.get("approved", 0),
            "rejected": decision_counts.get("rejected", 0),
            "approval_rate": (
                decision_counts.get("approved", 0) / total_decisions if total_decisions > 0 else 0.0
            ),
            "by_intent": {
                intent: {
                    "approved": counts.get("approved", 0),
                    "rejected": counts.get("rejected", 0),
                    "approval_rate": (
                        counts.get("approved", 0)
                        / (counts.get("approved", 0) + counts.get("rejected", 0))
                        if (counts.get("approved", 0) + counts.get("rejected", 0)) > 0
                        else 0.0
                    ),
                }
                for intent, counts in by_intent.items()
            },
            "by_rule": {
                rule_id: {
                    "approved": counts.get("approved", 0),
                    "rejected": counts.get("rejected", 0),
                    "approval_rate": (
                        counts.get("approved", 0)
                        / (counts.get("approved", 0) + counts.get("rejected", 0))
                        if (counts.get("approved", 0) + counts.get("rejected", 0)) > 0
                        else 0.0
                    ),
                }
                for rule_id, counts in by_rule.items()
            },
            "rejection_reasons": rejection_reasons,
        }

    async def get_analytics_trends(
        self, hours: int = 24, bucket_minutes: int = 60
    ) -> list[dict[str, Any]]:
        """Return event counts grouped into time buckets of `bucket_minutes` size."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")

        bucket_seconds = bucket_minutes * 60
        cursor = await self._db.execute(
            """
            SELECT
                strftime('%Y-%m-%dT%H:%M',
                    (strftime('%s', timestamp) / ? * ?) , 'unixepoch'
                ) AS bucket,
                COUNT(*) AS total,
                SUM(CASE WHEN action_taken = 'correct' THEN 1 ELSE 0 END) AS corrections,
                SUM(CASE WHEN action_taken = 'escalate' THEN 1 ELSE 0 END) AS escalations,
                SUM(CASE WHEN action_taken = 'allow' THEN 1 ELSE 0 END) AS allowed,
                AVG(latency_ms) AS avg_latency_ms
            FROM correction_events
            WHERE timestamp >= datetime('now', ?)
            GROUP BY bucket
            ORDER BY bucket ASC
            """,
            (bucket_seconds, bucket_seconds, f"-{hours} hours"),
        )
        rows = await cursor.fetchall()
        return [
            {
                "timestamp": row[0],
                "total": row[1],
                "corrections": row[2],
                "escalations": row[3],
                "allowed": row[4],
                "avg_latency_ms": round(row[5] or 0.0, 2),
            }
            for row in rows
        ]

    async def get_analytics_summary(self) -> dict[str, Any]:
        """Compute executive summary stats across all events."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")

        cursor = await self._db.execute("SELECT COUNT(*) FROM correction_events")
        total = (await cursor.fetchone())[0]

        if total == 0:
            return {
                "total_events": 0,
                "corrections": 0,
                "escalations": 0,
                "allowed": 0,
                "unique_agents": 0,
                "unique_sessions": 0,
                "top_rules": [],
                "top_intents": [],
                "avg_latency_ms": 0.0,
                "p50_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "time_range": {"first": None, "last": None},
            }

        cursor = await self._db.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN action_taken = 'correct' THEN 1 ELSE 0 END),
                SUM(CASE WHEN action_taken = 'escalate' THEN 1 ELSE 0 END),
                SUM(CASE WHEN action_taken = 'allow' THEN 1 ELSE 0 END),
                COUNT(DISTINCT agent_id),
                COUNT(DISTINCT session_id),
                AVG(latency_ms),
                MIN(timestamp),
                MAX(timestamp)
            FROM correction_events
            """
        )
        row = await cursor.fetchone()
        total, corrections, escalations, allowed = row[0], row[1], row[2], row[3]
        unique_agents, unique_sessions = row[4], row[5]
        avg_latency = row[6] or 0.0
        first_ts, last_ts = row[7], row[8]

        # Top violated rules (extract from JSON)
        cursor = await self._db.execute(
            "SELECT violations FROM correction_events WHERE violations != '[]'"
        )
        rule_counts: dict[str, int] = {}
        for (violations_json,) in await cursor.fetchall():
            for v in json.loads(violations_json):
                rid = v.get("rule_id", "unknown")
                rule_counts[rid] = rule_counts.get(rid, 0) + 1
        top_rules = sorted(rule_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        # Top intents
        cursor = await self._db.execute(
            "SELECT intent_type, COUNT(*) FROM correction_events "
            "GROUP BY intent_type ORDER BY COUNT(*) DESC LIMIT 5"
        )
        top_intents = await cursor.fetchall()

        # Latency percentiles (fetch all latencies, compute in Python)
        cursor = await self._db.execute(
            "SELECT latency_ms FROM correction_events "
            "WHERE latency_ms IS NOT NULL ORDER BY latency_ms"
        )
        latencies = [r[0] for r in await cursor.fetchall()]
        p50 = latencies[len(latencies) // 2] if latencies else 0.0
        p95_idx = int(len(latencies) * 0.95) if latencies else 0
        p95 = latencies[min(p95_idx, len(latencies) - 1)] if latencies else 0.0

        return {
            "total_events": total,
            "corrections": corrections,
            "escalations": escalations,
            "allowed": allowed,
            "unique_agents": unique_agents,
            "unique_sessions": unique_sessions,
            "top_rules": [{"rule_id": r, "count": c} for r, c in top_rules],
            "top_intents": [{"intent": i, "count": c} for i, c in top_intents],
            "avg_latency_ms": round(avg_latency, 2),
            "p50_latency_ms": round(p50, 2),
            "p95_latency_ms": round(p95, 2),
            "time_range": {"first": first_ts, "last": last_ts},
        }

    # --- Correction Strategies (ACE) ---

    async def add_strategy(
        self,
        strategy_id: str,
        lesson: str,
        rule_id: str | None = None,
        intent_type: str | None = None,
        source_event_id: str | None = None,
    ) -> None:
        """Insert a new correction strategy learned from supervisor feedback."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")
        await self._db.execute(
            """
            INSERT INTO correction_strategies
                (strategy_id, lesson, rule_id, intent_type, source_event_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                lesson,
                rule_id,
                intent_type,
                source_event_id,
                datetime.now(UTC).isoformat(),
            ),
        )
        await self._db.commit()

    async def get_top_strategies(
        self, intent_type: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Get top strategies ranked by (helpful - harmful) score."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")

        if intent_type:
            cursor = await self._db.execute(
                """
                SELECT strategy_id, lesson, rule_id, intent_type,
                       helpful_count, harmful_count, applied_count
                FROM correction_strategies
                WHERE intent_type = ? OR intent_type IS NULL
                ORDER BY (helpful_count - harmful_count) DESC, created_at DESC
                LIMIT ?
                """,
                (intent_type, limit),
            )
        else:
            cursor = await self._db.execute(
                """
                SELECT strategy_id, lesson, rule_id, intent_type,
                       helpful_count, harmful_count, applied_count
                FROM correction_strategies
                ORDER BY (helpful_count - harmful_count) DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = await cursor.fetchall()
        return [
            {
                "strategy_id": r[0],
                "lesson": r[1],
                "rule_id": r[2],
                "intent_type": r[3],
                "helpful_count": r[4],
                "harmful_count": r[5],
                "applied_count": r[6],
                "score": r[4] - r[5],
            }
            for r in rows
        ]

    async def increment_strategy_helpful(self, strategy_id: str) -> None:
        if not self._db:
            raise RuntimeError("AuditStore not connected")
        await self._db.execute(
            "UPDATE correction_strategies "
            "SET helpful_count = helpful_count + 1 WHERE strategy_id = ?",
            (strategy_id,),
        )
        await self._db.commit()

    async def increment_strategy_harmful(self, strategy_id: str) -> None:
        if not self._db:
            raise RuntimeError("AuditStore not connected")
        await self._db.execute(
            "UPDATE correction_strategies "
            "SET harmful_count = harmful_count + 1 WHERE strategy_id = ?",
            (strategy_id,),
        )
        await self._db.commit()

    async def increment_strategy_applied(self, strategy_id: str) -> None:
        if not self._db:
            raise RuntimeError("AuditStore not connected")
        await self._db.execute(
            "UPDATE correction_strategies "
            "SET applied_count = applied_count + 1 WHERE strategy_id = ?",
            (strategy_id,),
        )
        await self._db.commit()

    async def find_similar_strategy(
        self, lesson: str, rule_id: str | None = None
    ) -> dict[str, Any] | None:
        """Find an existing strategy with the same rule_id and similar lesson text."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")
        if rule_id:
            cursor = await self._db.execute(
                "SELECT strategy_id, lesson FROM correction_strategies WHERE rule_id = ?",
                (rule_id,),
            )
        else:
            cursor = await self._db.execute("SELECT strategy_id, lesson FROM correction_strategies")
        for row in await cursor.fetchall():
            existing_lesson = row[1].lower()
            new_lesson = lesson.lower()
            # Simple word overlap check — if >60% of words match, consider duplicate
            existing_words = set(existing_lesson.split())
            new_words = set(new_lesson.split())
            if not existing_words or not new_words:
                continue
            overlap = len(existing_words & new_words) / max(len(existing_words), len(new_words))
            if overlap > 0.6:
                return {"strategy_id": row[0], "lesson": row[1]}
        return None

    async def get_all_strategies(self) -> list[dict[str, Any]]:
        """Get all strategies for the API endpoint."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")
        cursor = await self._db.execute(
            """
            SELECT strategy_id, lesson, rule_id, intent_type,
                   helpful_count, harmful_count, applied_count, created_at
            FROM correction_strategies
            ORDER BY (helpful_count - harmful_count) DESC, created_at DESC
            """
        )
        rows = await cursor.fetchall()
        return [
            {
                "strategy_id": r[0],
                "lesson": r[1],
                "rule_id": r[2],
                "intent_type": r[3],
                "helpful_count": r[4],
                "harmful_count": r[5],
                "applied_count": r[6],
                "created_at": r[7],
                "score": r[4] - r[5],
            }
            for r in rows
        ]

    async def count(self) -> int:
        """Return the total number of events."""
        if not self._db:
            raise RuntimeError("AuditStore not connected")

        cursor = await self._db.execute("SELECT COUNT(*) FROM correction_events")
        return (await cursor.fetchone())[0]

    def _row_to_event(self, row: tuple) -> CorrectionEvent:
        """Convert a database row to a CorrectionEvent."""
        (
            event_id,
            timestamp,
            agent_id,
            session_id,
            original_text,
            corrected_text,
            was_modified,
            action_taken,
            intent_type,
            intent_confidence,
            violations_json,
            confidence,
            latency_ms,
        ) = row

        violations_data = json.loads(violations_json)
        violations = [PolicyViolation(**v) for v in violations_data]

        try:
            it = IntentType(intent_type)
        except ValueError:
            it = IntentType.GENERAL

        try:
            action = CorrectionAction(action_taken)
        except ValueError:
            action = CorrectionAction.ALLOW

        return CorrectionEvent(
            event_id=event_id,
            timestamp=datetime.fromisoformat(timestamp),
            agent_id=agent_id,
            session_id=session_id,
            original_text=original_text,
            corrected_text=corrected_text,
            was_modified=bool(was_modified),
            action_taken=action,
            intent=DetectedIntent(intent_type=it, confidence=intent_confidence),
            violations=violations,
            confidence=confidence,
            latency_ms=latency_ms,
        )
