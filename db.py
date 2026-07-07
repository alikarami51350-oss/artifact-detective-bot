"""
ماژول دیتابیس (SQLite) — پرونده‌ها، پلن‌های اشتراک، اعتبار تحلیل،
امتیاز وفاداری، سیستم دعوت دوستان، و آمار مدیریتی.

نکته‌ی مهم: روی پلن رایگان Render، فضای دیسک "موقتیه" — یعنی هر بار که
سرویس رو دوباره Deploy کنی، این فایل دیتابیس پاک می‌شه. برای نگه‌داری
همیشگی، در آینده باید از یه دیتابیس بیرونی (Postgres/Supabase) استفاده کرد.

تعریف «ماه» در کل ربات: نه ماه تقویمی، بلکه یک دوره‌ی ۳۰ روزه که از لحظه‌ی
عضویت هر کاربر (joined_at) شروع می‌شه و هر ۳۰ روز یک‌بار تکرار می‌شه.
"""

import random
import re
import sqlite3
import string
from contextlib import closing
from datetime import datetime, timedelta, timezone

DB_PATH = "cases.db"

CYCLE_DAYS = 30  # طول هر «ماه» بر اساس این ربات

REFERRAL_FRIEND_BONUS_CREDITS = 2
REFERRAL_REFERRER_BONUS_CREDITS = 3

# محدودیت‌های ضدسوءاستفاده
MAX_REWARDED_REFERRALS_PER_DAY = 5   # سقف روزانه‌ی دعوت‌های پاداش‌دار برای هر معرف
NEW_CASE_COOLDOWN_SECONDS = 60       # حداقل فاصله بین دو شروع «پرونده جدید»
MAX_CASES_PER_DAY = 20               # سقف مطلق تعداد پرونده در روز (فارغ از پلن)

# پاداش‌های پله‌ای بر اساس تعداد دعوت موفق (تجمعی)
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
    "purchase": 100,
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
                referral_rewarded INTEGER NOT NULL DEFAULT 0,
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_reward_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                rewarded_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS purchases_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                description TEXT,
                amount INTEGER NOT NULL,
                approved_at TEXT NOT NULL
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
    """اگه کاربر وجود نداشته باشه می‌سازدش و رابطه‌ی معرف/دعوت‌شده رو ثبت
    می‌کنه. توجه: هیچ پاداشی همین‌جا داده نمی‌شه — پاداش دعوت فقط بعد از
    تکمیل اولین پرونده‌ی کاربر جدید فعال می‌شه (ضدسوءاستفاده با اکانت الکی).

    خروجی: user_row (اگه از قبل وجود داشته یا تازه ساخته شده، فرقی نداره)
    """
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is not None:
            return row

        referrer_row = None
        if referral_code:
            referrer_row = conn.execute(
                "SELECT * FROM users WHERE referral_code = ?",
                (referral_code.strip().upper(),),
            ).fetchone()
            if referrer_row is not None and referrer_row["user_id"] == user_id:
                referrer_row = None  # جلوگیری از دعوت خود فرد از خودش

        new_code = _generate_unique_referral_code(conn, first_name)
        signup_points = LOYALTY_POINTS["signup"]

        conn.execute(
            """
            INSERT INTO users
                (user_id, referred_by, referral_code, loyalty_points, joined_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                referrer_row["user_id"] if referrer_row else None,
                new_code,
                signup_points,
                now,
            ),
        )
        conn.commit()

        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row


def _count_referrer_rewards_today(conn, referrer_id: int) -> int:
    day_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) FROM referral_reward_log WHERE referrer_id = ? AND rewarded_at >= ?",
        (referrer_id, day_start),
    ).fetchone()
    return row[0] if row else 0


def grant_referral_reward_if_pending(user_id: int):
    """وقتی کاربر اولین پرونده‌ش رو کامل می‌کنه صدا زده می‌شه. اگه با یک کد
    دعوت معتبر ثبت‌نام کرده و هنوز پاداشی نگرفته، الان پاداش‌ها اعمال
    می‌شن. خروجی: referrer_id (اگه پاداش معرف واقعاً اعمال شده باشه)، وگرنه
    None."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT referred_by, referral_rewarded FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row or not row[0] or row[1]:
            return None

        referrer_id = row[0]

        conn.execute(
            "UPDATE users SET analysis_credits = analysis_credits + ?, "
            "referral_rewarded = 1 WHERE user_id = ?",
            (REFERRAL_FRIEND_BONUS_CREDITS, user_id),
        )

        rewards_today = _count_referrer_rewards_today(conn, referrer_id)
        if rewards_today >= MAX_REWARDED_REFERRALS_PER_DAY:
            conn.commit()
            return None  # سقف روزانه‌ی پاداش معرف پر شده؛ فقط دوست جدید پاداش گرفت

        conn.execute(
            "UPDATE users SET analysis_credits = analysis_credits + ?, "
            "successful_referrals = successful_referrals + 1, "
            "loyalty_points = loyalty_points + ? WHERE user_id = ?",
            (REFERRAL_REFERRER_BONUS_CREDITS, LOYALTY_POINTS["referral_success"], referrer_id),
        )
        conn.execute(
            "INSERT INTO referral_reward_log (referrer_id, rewarded_at) VALUES (?, ?)",
            (referrer_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return referrer_id


def check_and_grant_milestones(user_id: int):
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
# پرونده‌ها و محاسبه‌ی سهمیه بر اساس چرخه‌ی ۳۰ روزه‌ی عضویت
# ---------------------------------------------------------------------------
def _current_cycle_start(joined_at_iso: str) -> datetime:
    joined = datetime.fromisoformat(joined_at_iso)
    now = datetime.now(timezone.utc)
    days_passed = (now - joined).days
    cycle_number = days_passed // CYCLE_DAYS
    return joined + timedelta(days=cycle_number * CYCLE_DAYS)


def get_current_cycle_case_count(user_id: int) -> int:
    """تعداد پرونده‌های ساخته‌شده در «ماه» جاری (۳۰ روز اخیر از عضویت، نه
    ماه تقویمی) رو برمی‌گردونه."""
    user = get_user(user_id)
    if not user:
        return 0
    cycle_start = _current_cycle_start(user["joined_at"]).isoformat()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE user_id = ? AND created_at >= ?",
            (user_id, cycle_start),
        ).fetchone()
    return row[0] if row else 0


def count_user_cases(user_id: int) -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row[0] if row else 0


def get_recent_case_stats(user_id: int):
    """برای محدودیت ضدسوءاستفاده: (تعداد پرونده‌ی امروز، زمان آخرین پرونده)."""
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        count_today = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE user_id = ? AND created_at >= ?",
            (user_id, day_start),
        ).fetchone()[0]
        last_row = conn.execute(
            "SELECT created_at FROM cases WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    last_time = datetime.fromisoformat(last_row[0]) if last_row else None
    return count_today, last_time


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


# ---------------------------------------------------------------------------
# لاگ خرید و آمار مدیریتی
# ---------------------------------------------------------------------------
def log_purchase(user_id: int, description: str, amount: int) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO purchases_log (user_id, description, amount, approved_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, description, amount, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def get_admin_stats() -> dict:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    last_30_days = (now - timedelta(days=30)).isoformat()

    with closing(sqlite3.connect(DB_PATH)) as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_cases = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        cases_today = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE created_at >= ?", (today_start,)
        ).fetchone()[0]
        cases_last_30_days = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE created_at >= ?", (last_30_days,)
        ).fetchone()[0]

        plan_rows = conn.execute(
            "SELECT plan, COUNT(*) FROM users GROUP BY plan"
        ).fetchall()
        users_by_plan = {row[0]: row[1] for row in plan_rows}

        total_referrals = conn.execute(
            "SELECT COUNT(*) FROM referral_reward_log"
        ).fetchone()[0]

        total_revenue = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM purchases_log"
        ).fetchone()[0]
        revenue_last_30_days = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM purchases_log WHERE approved_at >= ?",
            (last_30_days,),
        ).fetchone()[0]
        total_purchases = conn.execute(
            "SELECT COUNT(*) FROM purchases_log"
        ).fetchone()[0]

    return {
        "total_users": total_users,
        "total_cases": total_cases,
        "cases_today": cases_today,
        "cases_last_30_days": cases_last_30_days,
        "users_by_plan": users_by_plan,
        "total_referrals": total_referrals,
        "total_revenue": total_revenue,
        "revenue_last_30_days": revenue_last_30_days,
        "total_purchases": total_purchases,
    }
