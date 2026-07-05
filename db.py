"""
ماژول دیتابیس ساده (SQLite) برای ذخیره‌ی پرونده‌های تحلیل‌شده.

نکته‌ی مهم: روی پلن رایگان Render، فضای دیسک "موقتیه" — یعنی هر بار که
سرویس رو دوباره Deploy کنی، این فایل دیتابیس پاک می‌شه و پرونده‌های قبلی
از بین می‌رن. برای نگه‌داری همیشگی، در آینده باید از یه دیتابیس بیرونی
(مثل Postgres رایگان Render یا Supabase) استفاده کرد.
"""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone

DB_PATH = "cases.db"


def init_db() -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_number TEXT UNIQUE,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                environment TEXT,
                size TEXT,
                material TEXT,
                notes TEXT,
                analysis TEXT
            )
            """
        )
        conn.commit()


def create_case(
    user_id: int,
    environment: str,
    size: str,
    material: str,
    notes: str,
    analysis: str,
) -> str:
    """یک پرونده‌ی جدید می‌سازه و شماره‌ی پرونده (مثل AL-2026-000123) رو برمی‌گردونه."""
    now = datetime.now(timezone.utc)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO cases
                (case_number, user_id, created_at, environment, size, material, notes, analysis)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("", user_id, now.isoformat(), environment, size, material, notes, analysis),
        )
        case_id = cursor.lastrowid
        case_number = f"AL-{now.year}-{case_id:06d}"
        conn.execute(
            "UPDATE cases SET case_number = ? WHERE id = ?", (case_number, case_id)
        )
        conn.commit()
    return case_number


def get_user_cases(user_id: int, limit: int = 10):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT case_number, created_at, environment, size, material
            FROM cases
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return rows


def get_case_by_number(case_number: str, user_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM cases WHERE case_number = ? AND user_id = ?",
            (case_number, user_id),
        ).fetchone()
    return row
