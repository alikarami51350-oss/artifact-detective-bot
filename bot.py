"""
ربات تلگرامی «کارآگاه باستانی» (ArchaeoLens) — نسخه‌ی کاربرپسند با منوی اصلی

ساختار:
- منوی اصلی: پرونده جدید / پرونده‌های من / دانشنامه / حساب من
- جریان «پرونده جدید»: عکس‌ها → کنترل کیفیت واقعی → سوالات دکمه‌ای → تحلیل با Gemini
- ذخیره‌ی هر پرونده با شماره‌ی یکتا در دیتابیس محلی
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from google import genai
from PIL import Image
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import db
import plans
import quality
from encyclopedia import ENCYCLOPEDIA_TOPICS

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_admin_chat_id_raw = os.getenv("ADMIN_CHAT_ID", "").strip()
ADMIN_CHAT_ID = int(_admin_chat_id_raw) if _admin_chat_id_raw else None

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

def get_quota_status(user_id: int) -> dict:
    """پلن فعلی، سهمیه‌ی باقی‌مانده، و اعتبار کاربر رو محاسبه می‌کنه."""
    user = db.get_user(user_id)
    plan_id = user["plan"] if user else "explorer"
    plan = plans.PLANS.get(plan_id, plans.PLANS["explorer"])

    # اگه پلن پولی منقضی شده باشه، به‌صورت موقت به‌عنوان explorer حساب می‌شه
    if plan_id != "explorer" and user and user["plan_expires_at"]:
        try:
            expires = datetime.fromisoformat(user["plan_expires_at"])
            if expires < datetime.now(timezone.utc):
                plan_id = "explorer"
                plan = plans.PLANS["explorer"]
        except ValueError:
            pass

    used = db.get_current_cycle_case_count(user_id)
    remaining = plan["monthly_quota"] - used

    credits = user["analysis_credits"] if user else 0
    remaining = max(0, remaining)

    return {
        "plan_id": plan_id,
        "plan_label": plan["label"],
        "remaining": remaining,
        "credits": credits,
        "can_proceed": remaining > 0 or credits > 0,
        "will_use_credit": remaining <= 0 and credits > 0,
    }


gemini_client = genai.Client(api_key=GEMINI_API_KEY)

TELEGRAM_SAFE_MESSAGE_LENGTH = 3500


async def send_long_message(bot, chat_id: int, text: str) -> None:
    """پیام‌های طولانی‌تر از سقف مجاز تلگرام رو به چند پیام تقسیم می‌کنه.

    این رفع‌کننده‌ی باگی است که وقتی کاربر توضیح اضافه (نکات) وارد می‌کرد و
    مجموع طول گزارش از سقف ۴۰۹۶ کاراکتری تلگرام رد می‌شد، باعث می‌شد ارسال
    پیام با خطا مواجه بشه و کاربر هیچ جوابی نگیره.
    """
    if len(text) <= TELEGRAM_SAFE_MESSAGE_LENGTH:
        await bot.send_message(chat_id=chat_id, text=text)
        return

    remaining = text
    while remaining:
        if len(remaining) <= TELEGRAM_SAFE_MESSAGE_LENGTH:
            chunk = remaining
            remaining = ""
        else:
            split_at = remaining.rfind("\n\n", 0, TELEGRAM_SAFE_MESSAGE_LENGTH)
            if split_at <= 0:
                split_at = remaining.rfind("\n", 0, TELEGRAM_SAFE_MESSAGE_LENGTH)
            if split_at <= 0:
                split_at = TELEGRAM_SAFE_MESSAGE_LENGTH
            chunk = remaining[:split_at]
            remaining = remaining[split_at:].lstrip("\n")
        await bot.send_message(chat_id=chat_id, text=chunk)

# ---------------------------------------------------------------------------
# منوی اصلی
# ---------------------------------------------------------------------------
BTN_NEW_CASE = "📂 پرونده جدید"
BTN_MY_CASES = "📁 پرونده‌های من"
BTN_PHOTO_GUIDE = "📷 راهنمای عکس گرفتن"
BTN_ACCOUNT = "⚙️ حساب من"
BTN_SUBSCRIPTION = "💎 اشتراک حرفه‌ای"
BTN_INVITE = "🎁 دعوت دوستان"
BTN_SUPPORT = "🆘 پشتیبانی"

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_NEW_CASE, BTN_MY_CASES],
        [BTN_PHOTO_GUIDE, BTN_ACCOUNT],
        [BTN_SUBSCRIPTION, BTN_INVITE],
        [BTN_SUPPORT],
    ],
    resize_keyboard=True,
)

CHANNEL_USERNAME = os.getenv("REQUIRED_CHANNEL", "@archaeolens")
CARD_NUMBER = os.getenv("CARD_NUMBER", "6037-0000-0000-0000")
CARD_HOLDER_NAME = os.getenv("CARD_HOLDER_NAME", "نام صاحب حساب")
REVIEW_TIMEOUT_MINUTES = int(os.getenv("REVIEW_TIMEOUT_MINUTES", "20"))

# ---------------------------------------------------------------------------
# مراحل مکالمه‌ی «پرونده جدید»
# ---------------------------------------------------------------------------
COLLECTING_PHOTOS, ASK_ENVIRONMENT, ASK_SIZE, ASK_MATERIAL, ASK_NOTES = range(5)

DONE_PHOTOS_BUTTON = "✅ عکس‌ها تمام شد"
SKIP_NOTES_CALLBACK = "notes|skip"

ENVIRONMENT_OPTIONS = [
    ("⛰️ کوهستان", "کوهستان"),
    ("🕳️ غار", "غار"),
    ("🏜️ دشت", "دشت"),
    ("🌊 کنار رودخانه", "کنار رودخانه"),
    ("🏛️ بنای تاریخی", "بنای تاریخی"),
    ("❓ سایر", "سایر"),
]

SIZE_OPTIONS = [
    ("کوچک (کف دست)", "کوچک"),
    ("متوسط (یک وجب)", "متوسط"),
    ("بزرگ (بزرگ‌تر از یک وجب)", "بزرگ"),
    ("نامشخص", "نامشخص"),
]

MATERIAL_OPTIONS = [
    ("آهکی", "آهکی"),
    ("گرانیت", "گرانیت"),
    ("بازالت", "بازالت"),
    ("سایر", "سایر"),
    ("نمی‌دانم", "نمی‌دانم"),
]


def build_inline_keyboard(options, prefix):
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"{prefix}|{value}")]
        for label, value in options
    ]
    return InlineKeyboardMarkup(buttons)


# ---------------------------------------------------------------------------
# دستورات و منوی سطح‌بالا
# ---------------------------------------------------------------------------
async def check_channel_membership(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status not in ("left", "kicked")
    except Exception:  # noqa: BLE001
        logger.exception("خطا در بررسی عضویت کانال (احتمالاً ربات ادمین کانال نیست)")
        return False


def build_join_gate_keyboard() -> InlineKeyboardMarkup:
    channel_link = f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 عضویت در کانال", url=channel_link)],
            [InlineKeyboardButton("✅ عضو شدم، بررسی کن", callback_data="joincheck")],
        ]
    )


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    if context.args:
        context.user_data["pending_referral_code"] = context.args[0]

    is_member = await check_channel_membership(context, user.id)
    if not is_member:
        await update.message.reply_text(
            "👋 سلام! برای استفاده از ArchaeoLens، اول باید عضو کانال ما بشی:\n\n"
            f"📢 {CHANNEL_USERNAME}\n\n"
            "بعد از عضویت، دکمه‌ی «✅ عضو شدم» رو بزن.",
            reply_markup=build_join_gate_keyboard(),
        )
        return

    await complete_start(update.effective_chat.id, user, context)


async def on_join_check_clicked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user

    is_member = await check_channel_membership(context, user.id)
    if not is_member:
        await query.answer("هنوز عضو کانال نشدی 🙁 اول عضو شو، بعد دوباره بزن.", show_alert=True)
        return

    await query.answer("✅ عضویت تأیید شد!")
    await query.edit_message_text("✅ عضویت شما تأیید شد! خوش اومدی 🎉")
    await complete_start(update.effective_chat.id, user, context)


async def complete_start(chat_id: int, user, context: ContextTypes.DEFAULT_TYPE) -> None:
    referral_code = context.user_data.pop("pending_referral_code", None)

    db.get_or_create_user(
        user.id, first_name=user.first_name or "", referral_code=referral_code
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🏛 <b>ArchaeoLens</b> — دستیار هوشمند تحلیل آثار تاریخی\n\n"
            "👋 خوش اومدی! از منوی زیر انتخاب کن:"
        ),
        parse_mode="HTML",
        reply_markup=MAIN_MENU_KEYBOARD,
    )


async def my_cases(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    cases = db.get_user_cases(user_id, limit=10)

    if not cases:
        await update.message.reply_text(
            "📁 هنوز هیچ پرونده‌ای ثبت نشده.\n"
            f"با «{BTN_NEW_CASE}» یه تحلیل جدید شروع کن."
        )
        return

    lines = ["📁 <b>آخرین پرونده‌های شما:</b>\n"]
    for case in cases:
        date_str = case["created_at"][:10]
        lines.append(
            f"🔖 <b>{case['case_number']}</b> — {date_str}\n"
            f"   محیط: {case['environment']} | اندازه: {case['size']} | جنس: {case['material']}\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["awaiting_support_message"] = True
    await update.message.reply_text(
        "🆘 <b>پشتیبانی</b>\n\n"
        "پیامت رو بنویس و بفرست؛ مستقیم برای تیم پشتیبانی ارسال می‌شه و "
        "به‌زودی جواب می‌گیری.",
        parse_mode="HTML",
    )


async def relay_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get("awaiting_support_message"):
        return  # این پیام مربوط به درخواست پشتیبانی نیست

    context.user_data["awaiting_support_message"] = False
    user = update.effective_user

    await update.message.reply_text(
        "✅ پیامت ارسال شد. به‌زودی جواب می‌گیری."
    )

    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    "🆘 <b>پیام پشتیبانی جدید</b>\n"
                    f"از طرف: {user.full_name} (آیدی: {user.id})\n\n"
                    f"{update.message.text}"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("✍️ پاسخ", callback_data=f"support|reply|{user.id}")]]
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("ارسال پیام پشتیبانی به ادمین با خطا مواجه شد")


async def on_support_reply_clicked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, _, user_id_str = query.data.split("|", 2)
    user_id = int(user_id_str)

    support_replies = context.bot_data.setdefault("awaiting_support_reply", {})
    support_replies[update.effective_chat.id] = user_id

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✍️ پاسخت رو بنویس؛ مستقیم برای کاربر ارسال می‌شه:",
    )


async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ADMIN_CHAT_ID or update.effective_chat.id != ADMIN_CHAT_ID:
        return  # این دستور فقط برای ادمین کار می‌کنه

    stats = db.get_admin_stats()
    pending_reviews = len(context.bot_data.get("pending_reviews", {}))
    pending_purchases = len(context.bot_data.get("pending_purchases", {}))

    plan_lines = "\n".join(
        f"  • {plans.PLANS.get(plan_id, {'label': plan_id})['label']}: {count}"
        for plan_id, count in stats["users_by_plan"].items()
    )

    text = (
        "📊 <b>داشبورد آماری ArchaeoLens</b>\n\n"
        f"👥 کل کاربران: {stats['total_users']}\n"
        f"{plan_lines}\n\n"
        f"📂 کل پرونده‌ها: {stats['total_cases']}\n"
        f"📅 پرونده‌های امروز: {stats['cases_today']}\n"
        f"📅 پرونده‌های ۳۰ روز اخیر: {stats['cases_last_30_days']}\n\n"
        f"🎁 کل دعوت‌های پاداش‌دار: {stats['total_referrals']}\n\n"
        f"💰 کل درآمد تاییدشده: {stats['total_revenue']:,} تومان\n"
        f"💰 درآمد ۳۰ روز اخیر: {stats['revenue_last_30_days']:,} تومان\n"
        f"🧾 تعداد خریدهای تاییدشده: {stats['total_purchases']}\n\n"
        f"⏳ در انتظار تایید تحلیل: {pending_reviews}\n"
        f"⏳ در انتظار تایید پرداخت: {pending_purchases}\n"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def show_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db.get_or_create_user(user.id, first_name=user.first_name or "")

    user_row = db.get_user(user.id)
    status = get_quota_status(user.id)
    case_count = db.count_user_cases(user.id)

    plan = plans.PLANS[status["plan_id"]]
    if status["plan_id"] == "explorer":
        quota_line = f"📊 سهمیه‌ی باقی‌مانده: {status['remaining']} پرونده (مجموع)"
    else:
        quota_line = f"📊 سهمیه‌ی این ماه: {status['remaining']} پرونده باقی‌مانده"
        expires = user_row["plan_expires_at"]
        if expires:
            quota_line += f"\n📅 انقضای پلن: {expires[:10]}"

    keyboard = None
    if user_row["loyalty_points"] >= db.LOYALTY_POINTS_PER_CREDIT:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏆 تبدیل امتیاز به اعتبار", callback_data="loyalty|redeem")]]
        )

    await update.message.reply_text(
        "⚙️ <b>حساب من</b>\n\n"
        f"👤 نام: {user.full_name}\n"
        f"🔖 کد دعوت شما: <code>{user_row['referral_code']}</code>\n"
        f"💎 پلن فعلی: {plan['label']}\n"
        f"{quota_line}\n"
        f"💳 اعتبار اضافه: {status['credits']} تحلیل\n"
        f"📂 تعداد کل پرونده‌ها: {case_count}\n"
        f"🎁 دوستان دعوت‌شده‌ی موفق: {user_row['successful_referrals']}\n"
        f"🏆 امتیاز وفاداری: {user_row['loyalty_points']}\n",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def on_loyalty_redeem_clicked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    credits_granted = db.redeem_loyalty_points(user_id)
    if credits_granted > 0:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ {credits_granted} امتیاز وفاداری به اعتبار تحلیل تبدیل شد! 🎉",
        )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"برای تبدیل، حداقل {db.LOYALTY_POINTS_PER_CREDIT} امتیاز نیاز داری.",
        )


async def show_invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db.get_or_create_user(user.id, first_name=user.first_name or "")
    user_row = db.get_user(user.id)

    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start={user_row['referral_code']}"

    await update.message.reply_text(
        "🎁 <b>دعوت از دوستان</b>\n\n"
        f"کد اختصاصی شما: <code>{user_row['referral_code']}</code>\n"
        f"🔗 {link}\n\n"
        f"وقتی دوستت با این لینک وارد بشه و <b>اولین پرونده‌ش رو کامل کنه</b>:\n"
        f"• خودش {db.REFERRAL_FRIEND_BONUS_CREDITS} اعتبار تحلیل رایگان می‌گیره\n"
        f"• تو {db.REFERRAL_REFERRER_BONUS_CREDITS} اعتبار تحلیل می‌گیری\n\n"
        "🏆 <b>پاداش‌های پله‌ای</b> (بر اساس تعداد دعوت موفق):\n"
        "• ۱ نفر → ۳ اعتبار اضافه\n"
        "• ۵ نفر → ۲۰ اعتبار اضافه\n"
        "• ۱۰ نفر → ۱ ماه پلن برنزی رایگان\n"
        "• ۲۵ نفر → ۱ ماه پلن نقره‌ای رایگان\n"
        "• ۵۰ نفر → ۳ ماه پلن نقره‌ای رایگان\n"
        "• ۱۰۰ نفر → ۱ سال پلن نقره‌ای رایگان 🎉\n\n"
        f"🎁 دوستان دعوت‌شده‌ی موفق شما تا الان: {user_row['successful_referrals']}\n"
        f"💳 اعتبار فعلی شما: {user_row['analysis_credits']} تحلیل",
        parse_mode="HTML",
    )


async def show_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    db.get_or_create_user(user_id)

    lines = ["💎 <b>پلن‌های اشتراک ArchaeoLens</b>\n"]

    for plan_id in plans.PLAN_ORDER:
        plan = plans.PLANS[plan_id]
        lines.append(f"\n<b>{plan['label']}</b>")
        if plan_id == "explorer":
            lines.append("رایگان")
        else:
            lines.append(
                f"{plan['price_month']:,} تومان/ماه — یا {plan['price_year']:,} تومان/سال"
            )
        for feature in plan["features"]:
            lines.append(f"✔ {feature}")

    lines.append("\n\n💳 <b>بسته‌های اعتباری</b> (بدون نیاز به اشتراک ماهانه):")
    for pack in plans.CREDIT_PACKS:
        lines.append(f"• {pack['count']} پرونده — {pack['price']:,} تومان")

    if db.is_within_first_purchase_window(user_id):
        offer_plan = plans.PLANS[plans.FIRST_PURCHASE_PLAN]
        discounted = round(
            offer_plan["price_month"] * (100 - plans.FIRST_PURCHASE_DISCOUNT_PERCENT) / 100
        )
        lines.append(
            f"\n\n🔥 <b>پیشنهاد ویژه‌ی خوش‌آمدگویی!</b>\n"
            f"تا ۲۴ ساعت پس از عضویت، پلن {offer_plan['label']} رو با "
            f"{plans.FIRST_PURCHASE_DISCOUNT_PERCENT}٪ تخفیف بگیر: "
            f"فقط {discounted:,} تومان (به‌جای {offer_plan['price_month']:,} تومان) برای ماه اول!"
        )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛒 خرید اشتراک / بسته", callback_data="sub|buy")]]
    )
    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=keyboard
    )


async def on_subscription_buy_clicked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    buttons = []
    for plan_id in plans.PLAN_ORDER:
        if plan_id == "explorer":
            continue
        plan = plans.PLANS[plan_id]
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{plan['label']} — ماهانه ({plan['price_month']:,} ت)",
                    callback_data=f"buy|plan|{plan_id}|month",
                )
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{plan['label']} — سالانه ({plan['price_year']:,} ت)",
                    callback_data=f"buy|plan|{plan_id}|year",
                )
            ]
        )
    for idx, pack in enumerate(plans.CREDIT_PACKS):
        buttons.append(
            [
                InlineKeyboardButton(
                    f"💳 بسته‌ی {pack['count']} پرونده — {pack['price']:,} ت",
                    callback_data=f"buy|credit|{idx}",
                )
            ]
        )

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="کدوم رو می‌خوای بخری؟",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def on_purchase_item_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|")
    user_id = update.effective_user.id

    if parts[1] == "plan":
        plan_id, period = parts[2], parts[3]
        plan = plans.PLANS[plan_id]
        price = plan["price_month"] if period == "month" else plan["price_year"]
        days = 30 if period == "month" else 365

        if (
            plan_id == plans.FIRST_PURCHASE_PLAN
            and period == "month"
            and db.is_within_first_purchase_window(user_id)
        ):
            price = round(price * (100 - plans.FIRST_PURCHASE_DISCOUNT_PERCENT) / 100)

        period_label = "ماهانه" if period == "month" else "سالانه"
        description = f"پلن {plan['label']} ({period_label})"
        target = {"type": "plan", "plan_id": plan_id, "days": days}
    else:
        idx = int(parts[2])
        pack = plans.CREDIT_PACKS[idx]
        price = pack["price"]
        description = f"بسته‌ی {pack['count']} پرونده"
        target = {"type": "credit", "count": pack["count"]}

    pending_purchases = context.bot_data.setdefault("pending_purchases", {})
    pending_purchases[user_id] = {"description": description, "amount": price, "target": target}

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            f"🧾 خرید: {description}\n"
            f"💰 مبلغ قابل پرداخت: {price:,} تومان\n\n"
            "لطفاً این مبلغ رو به شماره کارت زیر واریز کن:\n"
            f"💳 {CARD_NUMBER}\n"
            f"👤 به نام: {CARD_HOLDER_NAME}\n\n"
            "بعد از واریز، عکس رسید/فیش واریزی رو همینجا (در همین چت) بفرست تا "
            "برای تایید نهایی ارسال بشه."
        ),
    )


async def receive_payment_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    pending_purchases = context.bot_data.get("pending_purchases", {})
    purchase = pending_purchases.get(user_id)
    if not purchase:
        return  # این عکس ربطی به رسید پرداخت نداره؛ بی‌خیالش می‌شیم

    photo = update.message.photo[-1]
    receipt_path = f"/tmp/receipt_{user_id}.jpg"
    try:
        file = await photo.get_file()
        await file.download_to_drive(receipt_path)
    except Exception:  # noqa: BLE001
        logger.exception("دانلود رسید پرداخت با خطا مواجه شد")
        await update.message.reply_text("⚠️ دریافت عکس رسید با مشکل مواجه شد. لطفاً دوباره بفرست.")
        return

    await update.message.reply_text(
        "✅ رسید دریافت شد و برای تایید نهایی ارسال شد. به‌زودی نتیجه رو اطلاع می‌دیم."
    )

    if ADMIN_CHAT_ID:
        try:
            with open(receipt_path, "rb") as f:
                await context.bot.send_photo(
                    chat_id=ADMIN_CHAT_ID,
                    photo=f,
                    caption=(
                        "🧾 <b>درخواست خرید جدید</b>\n"
                        f"کاربر: {update.effective_user.full_name} (آیدی: {user_id})\n"
                        f"مورد: {purchase['description']}\n"
                        f"مبلغ: {purchase['amount']:,} تومان"
                    ),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "✅ تایید و فعال‌سازی",
                                    callback_data=f"purchase|approve|{user_id}",
                                ),
                                InlineKeyboardButton(
                                    "❌ رد کردن", callback_data=f"purchase|reject|{user_id}"
                                ),
                            ]
                        ]
                    ),
                )
        except Exception:  # noqa: BLE001
            logger.exception("ارسال رسید خرید به ادمین با خطا مواجه شد")

    try:
        os.remove(receipt_path)
    except OSError:
        pass


async def on_purchase_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, action, user_id_str = query.data.split("|", 2)
    user_id = int(user_id_str)

    pending_purchases = context.bot_data.get("pending_purchases", {})
    purchase = pending_purchases.get(user_id)
    if not purchase:
        await query.edit_message_caption(caption="⚠️ این درخواست قبلاً پردازش شده یا پیدا نشد.")
        return

    if action == "approve":
        target = purchase["target"]
        if target["type"] == "plan":
            db.set_plan(user_id, target["plan_id"], target["days"])
        else:
            db.add_credits(user_id, target["count"])
        db.add_loyalty_points(user_id, 100)
        db.log_purchase(user_id, purchase["description"], purchase["amount"])
        del pending_purchases[user_id]

        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ پرداخت شما تایید شد! «{purchase['description']}» با موفقیت فعال شد. 🎉",
        )
        await query.edit_message_caption(caption="✅ تایید شد و برای کاربر فعال شد.")
    else:
        del pending_purchases[user_id]
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "❌ متاسفانه پرداخت شما تایید نشد. اگه فکر می‌کنید اشتباهی رخ داده، "
                "دوباره تلاش کنید یا رسید واضح‌تری بفرستید."
            ),
        )
        await query.edit_message_caption(caption="❌ رد شد.")


async def show_photo_guide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    guide_text = ENCYCLOPEDIA_TOPICS["photo_tips"]["text"]
    await update.message.reply_text(guide_text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# جریان «پرونده جدید» — دریافت عکس
# ---------------------------------------------------------------------------
async def start_new_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    db.get_or_create_user(user_id, first_name=update.effective_user.first_name or "")

    count_today, last_time = db.get_recent_case_stats(user_id)
    if count_today >= db.MAX_CASES_PER_DAY:
        await update.message.reply_text(
            "⚠️ به سقف تعداد پرونده‌ی امروز رسیدی. لطفاً فردا دوباره تلاش کن.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return ConversationHandler.END

    if last_time:
        seconds_since_last = (datetime.now(timezone.utc) - last_time).total_seconds()
        if seconds_since_last < db.NEW_CASE_COOLDOWN_SECONDS:
            wait_seconds = int(db.NEW_CASE_COOLDOWN_SECONDS - seconds_since_last)
            await update.message.reply_text(
                f"⏳ کمی صبر کن ({wait_seconds} ثانیه‌ی دیگه) و دوباره امتحان کن.",
                reply_markup=MAIN_MENU_KEYBOARD,
            )
            return ConversationHandler.END

    status = get_quota_status(user_id)
    if not status["can_proceed"]:
        await update.message.reply_text(
            "⚠️ سهمیه‌ی پلن رایگان شما تموم شده و اعتبار اضافه‌ای هم نداری.\n\n"
            "برای ادامه:\n"
            f"• از «{BTN_SUBSCRIPTION}» یکی از پلن‌ها یا بسته‌های اعتباری رو ببین، یا\n"
            f"• از «{BTN_INVITE}» دوستانت رو دعوت کن تا اعتبار رایگان بگیری.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return ConversationHandler.END

    context.user_data["will_use_credit"] = status["will_use_credit"]
    context.user_data["photos"] = []
    context.user_data["environment"] = None
    context.user_data["size"] = None
    context.user_data["material"] = None
    context.user_data["notes"] = None

    keyboard = ReplyKeyboardMarkup(
        [[DONE_PHOTOS_BUTTON]], resize_keyboard=True, one_time_keyboard=False
    )
    credit_note = ""
    if status["will_use_credit"]:
        credit_note = (
            f"\n\n💳 توجه: سهمیه‌ی پلن فعلیت تموم شده؛ این تحلیل از اعتبار اضافه‌ت "
            f"کم می‌شه ({status['credits']} اعتبار باقی‌مانده)."
        )
    await update.message.reply_text(
        "📂 <b>پرونده جدید</b>\n\n"
        "برای بهترین نتیجه:\n"
        "✓ حداقل ۲-۳ عکس، حداکثر ۸ عکس\n"
        "✓ نور طبیعی (نه فلاش مستقیم)\n"
        "✓ چند زاویه‌ی مختلف\n"
        "✓ تصاویر واضح و بدون فیلتر\n\n"
        f"📷 عکس‌ها رو بفرست:{credit_note}",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    return COLLECTING_PHOTOS


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photos = context.user_data.setdefault("photos", [])
    if len(photos) >= 8:
        await update.message.reply_text(
            "به سقف ۸ عکس رسیدی. اگه کافیه، دکمه‌ی "
            f"«{DONE_PHOTOS_BUTTON}» رو بزن."
        )
        return COLLECTING_PHOTOS

    photo = update.message.photo[-1]  # بالاترین کیفیت
    file_path = f"/tmp/{update.effective_chat.id}_{len(photos)}.jpg"

    max_attempts = 5
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            file = await photo.get_file()
            await file.download_to_drive(file_path)
            last_error = None
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("تلاش %s برای دانلود عکس ناموفق بود: %s", attempt, exc)
            if attempt < max_attempts:
                await update.message.reply_text(
                    f"⏳ اتصال ضعیفه، دوباره تلاش می‌کنم... ({attempt}/{max_attempts})"
                )
                await asyncio.sleep(15)

    if last_error is not None:
        logger.exception("دانلود عکس بعد از چند تلاش ناموفق بود", exc_info=last_error)
        await update.message.reply_text(
            "⚠️ بعد از چند تلاش هم نشد این عکس رو دانلود کنم. لطفاً دوباره امتحان کن."
        )
        return COLLECTING_PHOTOS

    photos.append(file_path)
    await update.message.reply_text(
        f"✔ تصویر {len(photos)} دریافت شد.\n"
        f"({len(photos)}/۸، حداقل ۲ عکس لازمه)"
    )
    return COLLECTING_PHOTOS


async def done_with_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photos = context.user_data.get("photos", [])
    if len(photos) < 2:
        await update.message.reply_text(
            "برای تحلیل بهتره حداقل ۲-۳ عکس از زوایای مختلف بفرستید. لطفاً چند عکس دیگه بفرستید."
        )
        return COLLECTING_PHOTOS

    await update.message.reply_text("🔍 در حال بررسی کیفیت تصاویر...", reply_markup=ReplyKeyboardRemove())

    quality_result = quality.analyze_photos(photos)
    quality_message = (
        "🔍 <b>بررسی کیفیت</b>\n\n"
        f"وضوح: {quality.stars_to_text(quality_result['resolution_stars'])}\n"
        f"نور: {quality.stars_to_text(quality_result['brightness_stars'])}\n\n"
        f"نتیجه: <b>{quality_result['overall']}</b>"
    )
    await update.message.reply_text(quality_message, parse_mode="HTML")

    if quality_result["overall"] == "ضعیف":
        for path in photos:
            try:
                os.remove(path)
            except OSError:
                pass
        context.user_data["photos"] = []
        keyboard = ReplyKeyboardMarkup(
            [[DONE_PHOTOS_BUTTON]], resize_keyboard=True, one_time_keyboard=False
        )
        await update.message.reply_text(
            "⚠️ تصاویر برای تحلیل دقیق کافی نیستن (احتمالاً نور کم یا وضوح "
            "پایینه). لطفاً با نور بهتر و از فاصله‌ی نزدیک‌تر دوباره عکس "
            "بگیر و بفرست.",
            reply_markup=keyboard,
        )
        return COLLECTING_PHOTOS

    await update.message.reply_text(
        "📍 محیط کشف این شی کجا بود؟",
        reply_markup=build_inline_keyboard(ENVIRONMENT_OPTIONS, "env"),
    )
    return ASK_ENVIRONMENT


# ---------------------------------------------------------------------------
# سوالات دکمه‌ای
# ---------------------------------------------------------------------------
async def on_environment_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    value = query.data.split("|", 1)[1]
    context.user_data["environment"] = value

    await query.edit_message_text(f"📍 محیط کشف: {value} ✅")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📏 اندازه‌ی تقریبی شی چقدره؟",
        reply_markup=build_inline_keyboard(SIZE_OPTIONS, "size"),
    )
    return ASK_SIZE


async def on_size_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    value = query.data.split("|", 1)[1]
    context.user_data["size"] = value

    await query.edit_message_text(f"📏 اندازه: {value} ✅")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🪨 جنس احتمالی شی به نظرتون چیه؟",
        reply_markup=build_inline_keyboard(MATERIAL_OPTIONS, "material"),
    )
    return ASK_MATERIAL


async def on_material_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    value = query.data.split("|", 1)[1]
    context.user_data["material"] = value

    await query.edit_message_text(f"🪨 جنس: {value} ✅")

    skip_keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("رد کردن (توضیح ندارم)", callback_data=SKIP_NOTES_CALLBACK)]]
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "📝 اگه توضیح اضافه‌ای دارید (مثلاً نشونه‌ی کنده‌کاری، اشیای اطراف، "
            "یا هر چیز دیگه)، تایپ کنید. اگه چیزی ندارید، دکمه‌ی زیر رو بزنید."
        ),
        reply_markup=skip_keyboard,
    )
    return ASK_NOTES


async def on_notes_skipped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["notes"] = "—"
    await query.edit_message_text("📝 توضیح اضافه: (بدون توضیح) ✅")
    await analyze_and_reply(update.effective_chat.id, update.effective_user.id, context)
    return ConversationHandler.END


async def receive_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["notes"] = update.message.text
    await analyze_and_reply(update.effective_chat.id, update.effective_user.id, context)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# تحلیل نهایی با Gemini + نمایش پیشرفت
# ---------------------------------------------------------------------------
async def analyze_and_reply(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    photos = context.user_data.get("photos", [])
    environment = context.user_data.get("environment", "نامشخص")
    size = context.user_data.get("size", "نامشخص")
    material = context.user_data.get("material", "نامشخص")
    notes = context.user_data.get("notes", "—")

    progress_message = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🧠 <b>در حال تحلیل پرونده</b>\n\n"
            "✓ دریافت تصاویر\n"
            "✓ کنترل کیفیت\n"
            "⏳ استخراج ویژگی‌ها و تحلیل ساختار...\n"
        ),
        parse_mode="HTML",
    )

    qa_text = (
        f"- محیط کشف: {environment}\n"
        f"- اندازه‌ی تقریبی: {size}\n"
        f"- جنس احتمالی: {material}\n"
        f"- توضیح اضافه‌ی کاربر: {notes}\n"
    )

    system_prompt = (
        "تو یک باستان‌شناس میدانی باتجربه هستی که سال‌ها روی شناسایی آثار کار "
        "کرده‌ای. کاربر چند عکس از یک شی از زوایای مختلف به همراه چند اطلاعات "
        "زمینه‌ای درباره‌ی محل پیدا شدن آن شی فرستاده. مثل یک کارشناس واقعی "
        "صحبت کن: جایی که شواهد بصری قوی و روشنه، با اطمینان و قاطعیت نظر بده؛ "
        "جایی که شواهد ناکافیه، صادقانه بگو که نمی‌شه مطمئن بود — نه بیشتر و نه "
        "کمتر از چیزی که واقعاً از عکس‌ها قابل استنتاجه.\n\n"
        "پاسخ رو دقیقاً با همین ۵ بخش و همین سرتیترها (بدون تغییر) بنویس:\n\n"
        "📄 خلاصه:\n"
        "(۲-۳ جمله خلاصه‌ی نتیجه‌گیری کلی، شامل احتمال ساخته‌ی دست بشر بودن یا "
        "پدیده‌ی طبیعی بودن، با یک درصد اطمینان تقریبی)\n\n"
        "🔍 شواهد:\n"
        "(لیستی از شواهد بصری مشخص که از عکس‌ها استخراج کردی)\n\n"
        "🏺 فرضیه‌ها:\n"
        "(اگر احتمال انسان‌ساخت بودن بالاست، حدس بزن این شی احتمالاً برای چه "
        "کاربردی ساخته شده و به چه دوره/سبکی ممکنه تعلق داشته باشه)\n\n"
        "⚠ محدودیت‌ها:\n"
        "(چه چیزهایی رو نمی‌شه فقط از روی عکس با قطعیت گفت)\n\n"
        "📌 پیشنهاد:\n"
        "(تاکید کن این فقط تحلیل اولیه‌ی هوش مصنوعیه و برای تایید قطعی باید حتماً "
        "با یک باستان‌شناس یا سازمان میراث فرهنگی محلی تماس بگیرن)\n\n"
        "قوانین سخت‌گیرانه:\n"
        "- هرگز چیزی رو که در عکس‌ها قابل مشاهده نیست ادعا نکن.\n"
        "- هرگز برای ایجاد هیجان کاذب یا القای ارزش/اصالت بالاتر از چیزی که "
        "شواهد نشون می‌ده، اغراق نکن. صداقت علمی مهم‌تر از جذاب بودن پاسخه.\n"
        "- از ستاره یا نشانه‌های Markdown استفاده نکن (فقط متن ساده با همین "
        "ایموجی‌ها)."
    )

    request_parts = [
        system_prompt,
        f"اطلاعات زمینه‌ای که کاربر داده:\n{qa_text}\n\nلطفاً عکس‌های زیر رو تحلیل کن.",
    ]
    for path in photos:
        request_parts.append(Image.open(path))

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=request_parts,
        )
        analysis = (response.text or "").strip()
        if not analysis:
            logger.error("Gemini پاسخ خالی برگردوند. جزئیات پاسخ: %s", response)
            analysis = (
                "⚠️ متاسفانه هوش مصنوعی نتونست تحلیلی برای این عکس‌ها تولید کنه "
                "(ممکنه به‌خاطر فیلترهای ایمنی مدل یا کیفیت/محتوای عکس‌ها باشه). "
                "لطفاً دوباره با عکس‌های واضح‌تر امتحان کن."
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("خطا در فراخوانی Gemini API")
        analysis = (
            "⚠️ متاسفانه در حال حاضر امکان تحلیل وجود نداره. "
            f"جزئیات خطا برای دیباگ: {exc}"
        )

    used_credit = context.user_data.get("will_use_credit", False)
    if used_credit:
        db.consume_credit(user_id)

    case_number = db.create_case(
        user_id, environment, size, material, notes, analysis, used_credit=used_credit
    )

    is_first_case = db.mark_first_case_done(user_id)
    if is_first_case:
        referrer_id = db.grant_referral_reward_if_pending(user_id)
        if referrer_id:
            try:
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=(
                        "🎉 خبر خوب! یکی از دوستانی که دعوت کرده بودی اولین "
                        "پرونده‌ش رو کامل کرد.\n"
                        f"💳 {db.REFERRAL_REFERRER_BONUS_CREDITS} اعتبار تحلیل به حسابت اضافه شد."
                    ),
                )
                milestone_msg = db.check_and_grant_milestones(referrer_id)
                if milestone_msg:
                    await context.bot.send_message(chat_id=referrer_id, text=milestone_msg)
            except Exception:  # noqa: BLE001
                logger.exception("ارسال پیام پاداش معرفی به معرف با خطا مواجه شد")

    if ADMIN_CHAT_ID:
        await send_for_admin_review(
            context=context,
            case_number=case_number,
            user_chat_id=chat_id,
            environment=environment,
            size=size,
            material=material,
            notes=notes,
            analysis=analysis,
            photos=photos,
            progress_message=progress_message,
        )
        for path in photos:
            try:
                os.remove(path)
            except OSError:
                pass
        return

    try:
        await progress_message.edit_text(
            "🧠 <b>در حال تحلیل پرونده</b>\n\n"
            "✓ دریافت تصاویر\n"
            "✓ کنترل کیفیت\n"
            "✓ استخراج ویژگی‌ها و تحلیل ساختار\n"
            "✓ تولید گزارش\n",
            parse_mode="HTML",
        )
    except Exception:  # noqa: BLE001
        pass

    final_message = f"📁 شماره‌ی پرونده: {case_number}\n\n{analysis}"

    try:
        await send_long_message(context.bot, chat_id, final_message)
    except Exception:  # noqa: BLE001
        logger.exception("ارسال پیام تحلیل به کاربر با خطا مواجه شد")
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ در ارسال نتیجه‌ی تحلیل مشکلی پیش اومد. لطفاً دوباره امتحان کن.",
        )

    await context.bot.send_message(
        chat_id=chat_id,
        text="برای شروع یه تحلیل جدید یا مشاهده‌ی پرونده‌ها، از منو انتخاب کن:",
        reply_markup=MAIN_MENU_KEYBOARD,
    )

    for path in photos:
        try:
            os.remove(path)
        except OSError:
            pass


async def send_for_admin_review(
    context: ContextTypes.DEFAULT_TYPE,
    case_number: str,
    user_chat_id: int,
    environment: str,
    size: str,
    material: str,
    notes: str,
    analysis: str,
    photos: list,
    progress_message,
) -> None:
    """گزارش رو برای تایید/ویرایش ادمین می‌فرسته، به‌جای ارسال مستقیم به کاربر."""
    pending = context.bot_data.setdefault("pending_reviews", {})
    pending[case_number] = {"user_chat_id": user_chat_id, "draft": analysis}

    try:
        if photos:
            media = [InputMediaPhoto(open(p, "rb")) for p in photos]
            await context.bot.send_media_group(chat_id=ADMIN_CHAT_ID, media=media)
    except Exception:  # noqa: BLE001
        logger.exception("ارسال عکس‌ها برای بازبینی ادمین با خطا مواجه شد")

    review_header = (
        "🕵️ <b>پرونده‌ی جدید در انتظار تایید</b>\n\n"
        f"شماره: {case_number}\n"
        f"محیط: {environment} | اندازه: {size} | جنس: {material}\n"
        f"توضیح کاربر: {notes}\n"
    )
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=review_header, parse_mode="HTML")
    await send_long_message(context.bot, ADMIN_CHAT_ID, analysis)

    review_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ تایید و ارسال", callback_data=f"review|approve|{case_number}"
                ),
                InlineKeyboardButton("✏️ ویرایش", callback_data=f"review|edit|{case_number}"),
            ]
        ]
    )
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID, text="تصمیم شما؟", reply_markup=review_keyboard
    )

    try:
        await progress_message.edit_text(
            "🧠 <b>در حال تحلیل پرونده</b>\n\n"
            "✓ دریافت تصاویر\n"
            "✓ کنترل کیفیت\n"
            "✓ استخراج ویژگی‌ها و تحلیل ساختار\n"
            "✓ تولید گزارش\n"
            "⏳ در انتظار تایید نهایی کارشناس...\n",
            parse_mode="HTML",
        )
    except Exception:  # noqa: BLE001
        pass

    await context.bot.send_message(
        chat_id=user_chat_id,
        text=(
            f"📁 پرونده‌ی شما ثبت شد (شماره: {case_number}).\n"
            "گزارش در حال بررسی نهاییه و به‌زودی نتیجه رو دریافت می‌کنید. ممنون از صبرتون! 🙏"
        ),
        reply_markup=MAIN_MENU_KEYBOARD,
    )

    if context.job_queue is not None:
        context.job_queue.run_once(
            auto_send_after_timeout,
            when=REVIEW_TIMEOUT_MINUTES * 60,
            data=case_number,
            name=f"auto_send_{case_number}",
        )


async def auto_send_after_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    """اگه ادمین ظرف مهلت مشخص تصمیم نگیره، گزارش خودکار (بدون تغییر) برای
    کاربر ارسال می‌شه تا کاربر برای همیشه معطل نمونه."""
    case_number = context.job.data
    pending = context.bot_data.get("pending_reviews", {})
    review = pending.get(case_number)
    if not review:
        return  # قبلاً تایید/ویرایش/رد شده

    final_text = f"📁 شماره‌ی پرونده: {case_number}\n\n{review['draft']}"
    await send_long_message(context.bot, review["user_chat_id"], final_text)
    await context.bot.send_message(
        chat_id=review["user_chat_id"],
        text="برای شروع یه تحلیل جدید از منو انتخاب کن:",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    db.update_case_analysis(case_number, review["draft"])
    del pending[case_number]

    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"⏰ چون ظرف {REVIEW_TIMEOUT_MINUTES} دقیقه پاسخی ندادی، پرونده‌ی "
                    f"{case_number} خودکار (بدون تغییر) برای کاربر ارسال شد."
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("اطلاع‌رسانی ارسال خودکار به ادمین با خطا مواجه شد")


def cancel_auto_send_job(context: ContextTypes.DEFAULT_TYPE, case_number: str) -> None:
    if context.job_queue is None:
        return
    for job in context.job_queue.get_jobs_by_name(f"auto_send_{case_number}"):
        job.schedule_removal()


async def on_review_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, action, case_number = query.data.split("|", 2)

    pending = context.bot_data.get("pending_reviews", {})
    review = pending.get(case_number)

    if not review:
        await query.edit_message_text("⚠️ این پرونده قبلاً پردازش شده یا پیدا نشد.")
        return

    cancel_auto_send_job(context, case_number)

    if action == "approve":
        final_text = f"📁 شماره‌ی پرونده: {case_number}\n\n{review['draft']}"
        await send_long_message(context.bot, review["user_chat_id"], final_text)
        await context.bot.send_message(
            chat_id=review["user_chat_id"],
            text="برای شروع یه تحلیل جدید از منو انتخاب کن:",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        db.update_case_analysis(case_number, review["draft"])
        del pending[case_number]
        await query.edit_message_text(f"✅ پرونده‌ی {case_number} تایید و برای کاربر ارسال شد.")

    elif action == "edit":
        editing = context.bot_data.setdefault("editing_case", {})
        editing[update.effective_chat.id] = case_number
        await query.edit_message_text(
            f"✏️ در حال ویرایش پرونده‌ی {case_number}.\n"
            "متن فعلی رو در پیام بعدی می‌فرستم — کپی کن، تغییرات لازم رو بده، "
            "و نسخه‌ی نهایی رو به‌صورت یک پیام کامل برام بفرست:"
        )
        await send_long_message(context.bot, update.effective_chat.id, review["draft"])


async def admin_edit_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_chat_id = update.effective_chat.id

    support_replies = context.bot_data.get("awaiting_support_reply", {})
    support_user_id = support_replies.get(admin_chat_id)
    if support_user_id:
        await context.bot.send_message(
            chat_id=support_user_id,
            text=f"📩 <b>پاسخ پشتیبانی:</b>\n\n{update.message.text}",
            parse_mode="HTML",
        )
        support_replies.pop(admin_chat_id, None)
        await update.message.reply_text("✅ پاسخ برای کاربر ارسال شد.")
        return

    editing = context.bot_data.get("editing_case", {})
    case_number = editing.get(admin_chat_id)
    if not case_number:
        return  # ادمین در حالت ویرایش/پاسخ نیست؛ این پیام مربوط به چیز دیگه‌ایه

    pending = context.bot_data.get("pending_reviews", {})
    review = pending.get(case_number)
    if not review:
        await update.message.reply_text("⚠️ این پرونده دیگه در انتظار تایید نیست.")
        editing.pop(admin_chat_id, None)
        return

    final_text = f"📁 شماره‌ی پرونده: {case_number}\n\n{update.message.text}"
    await send_long_message(context.bot, review["user_chat_id"], final_text)
    await context.bot.send_message(
        chat_id=review["user_chat_id"],
        text="برای شروع یه تحلیل جدید از منو انتخاب کن:",
        reply_markup=MAIN_MENU_KEYBOARD,
    )

    db.update_case_analysis(case_number, update.message.text)
    del pending[case_number]
    editing.pop(admin_chat_id, None)
    await update.message.reply_text(
        f"✅ نسخه‌ی ویرایش‌شده برای کاربر ارسال شد (پرونده {case_number})."
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "باشه، لغو شد.",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# راه‌اندازی برنامه
# ---------------------------------------------------------------------------
def main() -> None:
    if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
        raise RuntimeError(
            "لطفاً TELEGRAM_BOT_TOKEN و GEMINI_API_KEY رو در فایل .env تنظیم کنید."
        )

    db.init_db()

    # رفع یک ناسازگاری شناخته‌شده بین کتابخونه‌ی تلگرام و نسخه‌های خیلی جدید
    # پایتون (مثل 3.14): مطمئن می‌شیم قبل از اجرا، یک event loop روی همین
    # ترد اصلی از قبل ست شده باشه.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(60)
        .read_timeout(150)
        .write_timeout(150)
        .pool_timeout(60)
        .build()
    )

    new_case_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{BTN_NEW_CASE}$"), start_new_case)],
        states={
            COLLECTING_PHOTOS: [
                MessageHandler(filters.PHOTO, receive_photo),
                MessageHandler(filters.Regex(f"^{DONE_PHOTOS_BUTTON}$"), done_with_photos),
            ],
            ASK_ENVIRONMENT: [
                CallbackQueryHandler(on_environment_selected, pattern=r"^env\|"),
            ],
            ASK_SIZE: [
                CallbackQueryHandler(on_size_selected, pattern=r"^size\|"),
            ],
            ASK_MATERIAL: [
                CallbackQueryHandler(on_material_selected, pattern=r"^material\|"),
            ],
            ASK_NOTES: [
                CallbackQueryHandler(on_notes_skipped, pattern=f"^{SKIP_NOTES_CALLBACK}$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_notes),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", show_main_menu))
    application.add_handler(new_case_conv)
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_MY_CASES}$"), my_cases))
    application.add_handler(
        MessageHandler(filters.Regex(f"^{BTN_PHOTO_GUIDE}$"), show_photo_guide)
    )
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_ACCOUNT}$"), show_account))
    application.add_handler(
        MessageHandler(filters.Regex(f"^{BTN_SUBSCRIPTION}$"), show_subscription)
    )
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_INVITE}$"), show_invite))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_SUPPORT}$"), show_support))
    application.add_handler(CommandHandler("stats", show_admin_stats))
    application.add_handler(CallbackQueryHandler(on_subscription_buy_clicked, pattern=r"^sub\|buy$"))
    application.add_handler(CallbackQueryHandler(on_purchase_item_selected, pattern=r"^buy\|"))
    application.add_handler(CallbackQueryHandler(on_purchase_decision, pattern=r"^purchase\|"))
    application.add_handler(CallbackQueryHandler(on_loyalty_redeem_clicked, pattern=r"^loyalty\|redeem$"))
    application.add_handler(CallbackQueryHandler(on_join_check_clicked, pattern=r"^joincheck$"))
    application.add_handler(CallbackQueryHandler(on_support_reply_clicked, pattern=r"^support\|reply\|"))
    application.add_handler(CommandHandler("mycases", my_cases))
    application.add_handler(MessageHandler(filters.PHOTO, receive_payment_receipt))

    application.add_handler(CallbackQueryHandler(on_review_decision, pattern=r"^review\|"))
    if ADMIN_CHAT_ID:
        application.add_handler(
            MessageHandler(
                filters.Chat(chat_id=ADMIN_CHAT_ID) & filters.TEXT & ~filters.COMMAND,
                admin_edit_text_handler,
            )
        )
    else:
        logger.warning(
            "ADMIN_CHAT_ID تنظیم نشده؛ تحلیل‌ها بدون تایید مستقیماً برای کاربر ارسال می‌شن."
        )

    # این هندلر باید بعد از هندلر مخصوص ادمین ثبت بشه، وگرنه پیام‌های ادمین
    # (ویرایش تحلیل/پاسخ پشتیبانی) رو زودتر می‌قاپه.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, relay_support_message))

    external_hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")

    if external_hostname:
        port = int(os.getenv("PORT", "10000"))
        webhook_url = f"https://{external_hostname}/{TELEGRAM_TOKEN}"
        logger.info("ربات در حالت Webhook روی پورت %s اجرا می‌شه...", port)
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=TELEGRAM_TOKEN,
            webhook_url=webhook_url,
        )
    else:
        logger.info("ربات در حالت Polling (تست محلی) اجرا می‌شه...")
        application.run_polling()


if __name__ == "__main__":
    main()
