from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from email_security import is_valid_email_address
from notification_models import NotificationRecipient, NotificationRule


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class NotificationStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def list_rules(self, event_type: str | None = None) -> list[NotificationRule]:
        with self._connect() as conn:
            if event_type is None:
                rows = conn.execute(
                    """
                    SELECT id, event_type, label, active, subject_template, body_template,
                           trigger_timing, date_field, offset_days, created_at, updated_at
                    FROM notification_rules
                    ORDER BY event_type, label, id
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, event_type, label, active, subject_template, body_template,
                           trigger_timing, date_field, offset_days, created_at, updated_at
                    FROM notification_rules
                    WHERE event_type = ?
                    ORDER BY label, id
                    """,
                    (str(event_type).strip(),),
                ).fetchall()
            return [self._rule_from_row(conn, row) for row in rows]

    def ensure_default_rules(self) -> None:
        defaults = [
            NotificationRule(
                event_type="staffing.assignment.need_now",
                label="Hiring manager: position needed now",
                subject_template="Position needed now: {position_name}",
                body_template="{school} needs {position_name} for {classroom}.",
                active=False,
            ),
            NotificationRule(
                event_type="offer.accepted",
                label="Leadership: offer accepted",
                subject_template="Offer accepted: {candidate_name}",
                body_template="{candidate_name} accepted the {position} offer for {school}.",
                active=False,
            ),
        ]
        existing = {rule.event_type for rule in self.list_rules()}
        for rule in defaults:
            if rule.event_type not in existing:
                self.save_rule(rule)

    def save_rule(self, rule: NotificationRule) -> NotificationRule:
        event_type = str(rule.event_type or "").strip()
        label = str(rule.label or "").strip()
        if not event_type:
            raise ValueError("Notification event type is required.")
        if not label:
            raise ValueError("Notification label is required.")
        trigger_timing = str(rule.trigger_timing or "event").strip() or "event"
        date_field = str(rule.date_field or "").strip()
        offset_days = int(rule.offset_days)
        if trigger_timing not in {"event", "date_offset"}:
            raise ValueError("Notification trigger timing must be event or date_offset.")
        if trigger_timing == "date_offset" and not date_field:
            raise ValueError("Date-offset notification requires a date field.")
        for recipient in rule.recipients:
            email = str(recipient.email or "").strip()
            if not is_valid_email_address(email):
                raise ValueError("Invalid recipient email.")

        now = utc_now_iso()
        with self._connect() as conn:
            with conn:
                if rule.id is None:
                    cursor = conn.execute(
                        """
                        INSERT INTO notification_rules
                            (event_type, label, active, subject_template, body_template,
                             trigger_timing, date_field, offset_days, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_type,
                            label,
                            1 if rule.active else 0,
                            str(rule.subject_template or ""),
                            str(rule.body_template or ""),
                            trigger_timing,
                            date_field,
                            offset_days,
                            now,
                            now,
                        ),
                    )
                    rule_id = int(cursor.lastrowid)
                else:
                    rule_id = int(rule.id)
                    conn.execute(
                        """
                        UPDATE notification_rules
                        SET event_type = ?, label = ?, active = ?, subject_template = ?, body_template = ?,
                            trigger_timing = ?, date_field = ?, offset_days = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            event_type,
                            label,
                            1 if rule.active else 0,
                            str(rule.subject_template or ""),
                            str(rule.body_template or ""),
                            trigger_timing,
                            date_field,
                            offset_days,
                            now,
                            rule_id,
                        ),
                    )
                    conn.execute("DELETE FROM notification_recipients WHERE rule_id = ?", (rule_id,))

                for recipient in rule.recipients:
                    conn.execute(
                        """
                        INSERT INTO notification_recipients
                            (rule_id, name, email, role_label, active, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rule_id,
                            str(recipient.name or "").strip(),
                            str(recipient.email or "").strip(),
                            str(recipient.role_label or "").strip(),
                            1 if recipient.active else 0,
                            now,
                            now,
                        ),
                    )
        return self.get_rule(rule_id)

    def get_rule(self, rule_id: int) -> NotificationRule:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, event_type, label, active, subject_template, body_template,
                       trigger_timing, date_field, offset_days, created_at, updated_at
                FROM notification_rules
                WHERE id = ?
                """,
                (int(rule_id),),
            ).fetchone()
            if row is None:
                raise ValueError("Notification rule not found.")
            return self._rule_from_row(conn, row)

    def delete_rule(self, rule_id: int) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute("DELETE FROM notification_recipients WHERE rule_id = ?", (int(rule_id),))
                conn.execute("DELETE FROM notification_rules WHERE id = ?", (int(rule_id),))

    def set_rule_active(self, rule_id: int, active: bool) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    "UPDATE notification_rules SET active = ?, updated_at = ? WHERE id = ?",
                    (1 if active else 0, utc_now_iso(), int(rule_id)),
                )

    def has_send_attempt(self, rule_id: int, idempotency_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM notification_audit
                WHERE rule_id = ? AND idempotency_key = ?
                LIMIT 1
                """,
                (int(rule_id), str(idempotency_key)),
            ).fetchone()
            return row is not None

    def record_send_attempt(
        self,
        *,
        event_type: str,
        rule_id: int | None,
        idempotency_key: str,
        recipient_count: int,
        status: str,
        error: str = "",
    ) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO notification_audit
                        (event_type, rule_id, idempotency_key, recipient_count, status, error, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(event_type).strip(),
                        rule_id,
                        str(idempotency_key),
                        int(recipient_count),
                        str(status).strip(),
                        str(error or "").strip(),
                        utc_now_iso(),
                    ),
                )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notification_rules (
                        id INTEGER PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        label TEXT NOT NULL,
                        active INTEGER NOT NULL DEFAULT 1,
                        subject_template TEXT NOT NULL DEFAULT '',
                        body_template TEXT NOT NULL DEFAULT '',
                        trigger_timing TEXT NOT NULL DEFAULT 'event',
                        date_field TEXT NOT NULL DEFAULT '',
                        offset_days INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                _ensure_column(conn, "notification_rules", "trigger_timing", "TEXT NOT NULL DEFAULT 'event'")
                _ensure_column(conn, "notification_rules", "date_field", "TEXT NOT NULL DEFAULT ''")
                _ensure_column(conn, "notification_rules", "offset_days", "INTEGER NOT NULL DEFAULT 0")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notification_recipients (
                        id INTEGER PRIMARY KEY,
                        rule_id INTEGER NOT NULL REFERENCES notification_rules(id) ON DELETE CASCADE,
                        name TEXT NOT NULL DEFAULT '',
                        email TEXT NOT NULL,
                        role_label TEXT NOT NULL DEFAULT '',
                        active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notification_audit (
                        id INTEGER PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        rule_id INTEGER,
                        idempotency_key TEXT NOT NULL,
                        recipient_count INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL,
                        error TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_notification_rules_event_type ON notification_rules(event_type)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_notification_recipients_rule_id ON notification_recipients(rule_id)")
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_audit_dedupe
                    ON notification_audit(rule_id, idempotency_key)
                    WHERE rule_id IS NOT NULL
                    """
                )

    def _rule_from_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> NotificationRule:
        recipients = [
            NotificationRecipient(
                id=int(recipient["id"]),
                name=str(recipient["name"]),
                email=str(recipient["email"]),
                role_label=str(recipient["role_label"]),
                active=bool(recipient["active"]),
            )
            for recipient in conn.execute(
                """
                SELECT id, name, email, role_label, active
                FROM notification_recipients
                WHERE rule_id = ?
                ORDER BY id
                """,
                (int(row["id"]),),
            ).fetchall()
        ]
        return NotificationRule(
            id=int(row["id"]),
            event_type=str(row["event_type"]),
            label=str(row["label"]),
            active=bool(row["active"]),
            subject_template=str(row["subject_template"]),
            body_template=str(row["body_template"]),
            trigger_timing=str(row["trigger_timing"]),
            date_field=str(row["date_field"]),
            offset_days=int(row["offset_days"]),
            recipients=recipients,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
