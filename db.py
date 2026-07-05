"""
ماژول دیتابیس (SQLite) — پرونده‌ها، پلن‌های اشتراک، اعتبار تحلیل،
امتیاز وفاداری، و سیستم دعوت دوستان.

نکته‌ی مهم: روی پلن رایگان Render، فضای دیسک "موقتیه" — یعنی هر بار که
سرویس رو دوباره Deploy کنی، این فایل دیتابیس پاک می‌شه. برای نگه‌داری
همیشگی، در آینده باید از یه دیتابیس بیرونی (Postgres/Supabase) استفاده کرد.
"""

import random
import re
import sqlite3
import string
from contextlib import closing
from datetime import datetime, timedelta, timezone

DB_PATH = "cases.db"

REFERRAL_FRIEND_BONUS_CREDITS = 2
REFERRAL_REFERRER_BONUS_CREDITS = 3

# پاداش‌های پله‌ای بر اساس تعداد دعوت موفق (تجمعی)
# مقدار عددی = تعداد اعتبار تحلیل، رشته = یک پلن رایگان به مدت مشخص
REFERRAL_MILESTONES = {
    1: {"credits": 3},
    5: {"credits": 20},
    10: {"plan": "basic", "days": 30},
    25: {"plan": "pro", "days": 30},
    50: {"plan": "pro", "days": 90},
    100: {"plan": "pro", "days": 365},
}

LOYALTY_POINTS = {
    "signup": 20,
    "first_case": 10,
    "referral_success": 50,
    "feedback": 20,
}
LOYALTY_POINTS_PER_CREDIT = 10  # نرخ تبدیل امتیاز به اعتبار تحلیل

FIRST_PURCHASE_OFFER_HOURS = 24


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
                analysis TEXT,
                used_credit INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                referred_by INTEGER,
                referral_code TEXT UNIQUE,
                plan TEXT NOT NULL DEFAULT 'explorer',
                plan_expires_at TEXT,
                analysis_credits INTEGER NOT NULL DEFAULT 0,
                loyalty_points INTEGER NOT NULL DEFAULT 0,
                successful_referrals INTEGER NOT NULL DEFAULT 0,
                milestones_claimed TEXT NOT NULL DEFAULT '',
                first_case_done INTEGER NOT NULL DEFAULT 0,
                joined_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


# ---------------------------------------------------------------------------
# ساخت کاربر و کد دعوت
# ---------------------------------------------------------------------------
def _slugify(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]", "", name or "")
    return (name[:4] or "USER").upper()


def _generate_unique_referral_code(conn, first_name: str) -> str:
    base = _slugify(first_name)
    while True:
        suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        code = f"{base}-{suffix}"
        exists = conn.execute(
            "SELECT 1 FROM users WHERE referral_code = ?", (code,)
        ).fetchone()
        if not exists:
            return code


def get_user(user_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()


def get_user_by_referral_code(code: str):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM users WHERE referral_code = ?", (code.strip().upper(),)
        ).fetchone()


def get_or_create_user(user_id: int, first_name: str = "", referral_code: str = None):
    """اگه کاربر وجود نداشته باشه می‌سازدش. اگه با یک کد دعوتِ معتبر ساخته بشه،
    پاداش‌های فوری ثبت‌نام (به خودش و به معرفش) اعمال می‌شه.

    خروجی: (user_row, referrer_id_or_None) — referrer_id فقط وقتی که همین
    الان برای اولین بار با یک کد معتبر ساخته شده باشه پر می‌شه (برای اطلاع‌رسانی).
    """
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is not None:
            return row, None

        referrer_row = None
        if referral_code:
            referrer_row = conn.execute(
                "SELECT * FROM users WHERE referral_code = ?",
                (referral_code.strip().upper(),),
            ).fetchone()
            if referrer_row is not None and referrer_row["user_id"] == user_id:
                referrer_row = None  # جلوگیری از دعوت خود فرد از خودش

        new_code = _generate_unique_referral_code(conn, first_name)
        signup_credits = REFERRAL_FRIEND_BONUS_CREDITS if referrer_row else 0
        signup_points = LOYALTY_POINTS["signup"]

        conn.execute(
            """
            INSERT INTO users
                (user_id, referred_by, referral_code, plan, analysis_credits,
                 loyalty_points, joined_at)
            VALUES (?, ?, ?, 'explorer', ?, ?, ?)
            """,
            (
                user_id,
                referrer_row["user_id"] if referrer_row else None,
                new_code,
                signup_credits,
                signup_points,
                now,
            ),
        )
        conn.commit()

        referrer_id = None
        if referrer_row is not None:
            referrer_id = referrer_row["user_id"]
            _grant_referral_rewards(conn, referrer_id)

        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row, referrer_id


def _grant_referral_rewards(conn, referrer_id: int) -> None:
    """اعتبار فوری معرف + بررسی پاداش‌های پله‌ای رو انجام می‌ده. باید داخل یک
    اتصال دیتابیس باز (conn) صدا زده بشه."""
    conn.execute(
        "UPDATE users SET analysis_credits = analysis_credits + ?, "
        "successful_referrals = successful_referrals + 1, "
        "loyalty_points = loyalty_points + ? WHERE user_id = ?",
        (REFERRAL_REFERRER_BONUS_CREDITS, LOYALTY_POINTS["referral_success"], referrer_id),
    )
    conn.commit()


def check_and_grant_milestones(user_id: int):
    """بعد از هر دعوت موفق صدا زده می‌شه. اگه به یکی از آستانه‌های پله‌ای
    رسیده باشه و قبلاً گرفته نشده، پاداششو می‌ده و توضیح متنی پاداش رو
    برمی‌گردونه (برای اطلاع‌رسانی به کاربر)، وگرنه None."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT successful_referrals, milestones_claimed FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return None

        count = row["successful_referrals"]
        claimed = set(row["milestones_claimed"].split(",")) if row["milestones_claimed"] else set()

        if count not in REFERRAL_MILESTONES or str(count) in claimed:
            return None

        reward = REFERRAL_MILESTONES[count]
        description = None

        if "credits" in reward:
            conn.execute(
                "UPDATE users SET analysis_credits = analysis_credits + ? WHERE user_id = ?",
                (reward["credits"], user_id),
            )
            description = f"🎁 به‌خاطر رسیدن به {count} دعوت موفق، {reward['credits']} اعتبار تحلیل گرفتی!"
        elif "plan" in reward:
            _extend_plan(conn, user_id, reward["plan"], reward["days"])
            description = (
                f"🎁 به‌خاطر رسیدن به {count} دعوت موفق، {reward['days']} روز پلن "
                f"{reward['plan']} برات فعال شد!"
            )

        claimed.add(str(count))
        conn.execute(
            "UPDATE users SET milestones_claimed = ? WHERE user_id = ?",
            (",".join(sorted(claimed, key=int)), user_id),
        )
        conn.commit()
        return description


def _extend_plan(conn, user_id: int, plan: str, days: int) -> None:
    row = conn.execute(
        "SELECT plan, plan_expires_at FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    now = datetime.now(timezone.utc)
    current_expiry = None
    if row and row[1]:
        try:
            current_expiry = datetime.fromisoformat(row[1])
        except ValueError:
            current_expiry = None

    base = current_expiry if (current_expiry and current_expiry > now) else now
    new_expiry = base + timedelta(days=days)
    conn.execute(
        "UPDATE users SET plan = ?, plan_expires_at = ? WHERE user_id = ?",
        (plan, new_expiry.isoformat(), user_id),
    )


def set_plan(user_id: int, plan: str, days: int) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        _extend_plan(conn, user_id, plan, days)
        conn.commit()


def add_credits(user_id: int, amount: int) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "UPDATE users SET analysis_credits = analysis_credits + ? WHERE user_id = ?",
            (amount, user_id),
        )
        conn.commit()


def consume_credit(user_id: int) -> bool:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT analysis_credits FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row or row[0] <= 0:
            return False
        conn.execute(
            "UPDATE users SET analysis_credits = analysis_credits - 1 WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()
        return True


def add_loyalty_points(user_id: int, amount: int) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "UPDATE users SET loyalty_points = loyalty_points + ? WHERE user_id = ?",
            (amount, user_id),
        )
        conn.commit()


def redeem_loyalty_points(user_id: int) -> int:
    """تمام امتیازهای قابل‌تبدیل کاربر رو به اعتبار تبدیل می‌کنه و تعداد
    اعتبار حاصل‌شده رو برمی‌گردونه (اگه امتیاز کافی نبود، 0)."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT loyalty_points FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return 0
        points = row[0]
        credits_to_grant = points // LOYALTY_POINTS_PER_CREDIT
        if credits_to_grant <= 0:
            return 0
        points_used = credits_to_grant * LOYALTY_POINTS_PER_CREDIT
        conn.execute(
            "UPDATE users SET loyalty_points = loyalty_points - ?, "
            "analysis_credits = analysis_credits + ? WHERE user_id = ?",
            (points_used, credits_to_grant, user_id),
        )
        conn.commit()
        return credits_to_grant


def mark_first_case_done(user_id: int) -> bool:
    """اگه این اولین پرونده‌ی کاربر باشه، امتیاز مربوطه رو می‌ده و True
    برمی‌گردونه؛ وگرنه False."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT first_case_done FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None or row[0]:
            return False
        conn.execute(
            "UPDATE users SET first_case_done = 1, loyalty_points = loyalty_points + ? "
            "WHERE user_id = ?",
            (LOYALTY_POINTS["first_case"], user_id),
        )
        conn.commit()
        return True


def is_within_first_purchase_window(user_id: int) -> bool:
    user = get_user(user_id)
    if not user:
        return False
    joined = datetime.fromisoformat(user["joined_at"])
    return datetime.now(timezone.utc) - joined <= timedelta(hours=FIRST_PURCHASE_OFFER_HOURS)


# ---------------------------------------------------------------------------
# پرونده‌ها و محاسبه‌ی سهمیه‌ی ماهانه
# ---------------------------------------------------------------------------
def get_monthly_case_count(user_id: int) -> int:
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE user_id = ? AND created_at >= ?",
            (user_id, month_start),
        ).fetchone()
    return row[0] if row else 0


def count_user_cases(user_id: int) -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row[0] if row else 0


def create_case(
    user_id: int,
    environment: str,
    size: str,
    material: str,
    notes: str,
    analysis: str,
    used_credit: bool = False,
) -> str:
    """یک پرونده‌ی جدید می‌سازه و شماره‌ی پرونده (مثل AL-2026-000123) رو برمی‌گردونه."""
    now = datetime.now(timezone.utc)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO cases
                (case_number, user_id, created_at, environment, size, material,
                 notes, analysis, used_credit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "",
                user_id,
                now.isoformat(),
                environment,
                size,
                material,
                notes,
                analysis,
                1 if used_credit else 0,
            ),
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


def update_case_analysis(case_number: str, analysis: str) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "UPDATE cases SET analysis = ? WHERE case_number = ?", (analysis, case_number)
        )
        conn.commit()
