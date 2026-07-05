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

from dotenv import load_dotenv
from google import genai
from PIL import Image
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
import quality
from encyclopedia import ENCYCLOPEDIA_TOPICS

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

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
BTN_ENCYCLOPEDIA = "📚 دانشنامه"
BTN_ACCOUNT = "⚙️ حساب من"
BTN_SUBSCRIPTION = "💎 اشتراک حرفه‌ای"

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_NEW_CASE, BTN_MY_CASES],
        [BTN_ENCYCLOPEDIA, BTN_ACCOUNT],
        [BTN_SUBSCRIPTION],
    ],
    resize_keyboard=True,
)

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
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🏛 <b>ArchaeoLens</b> — دستیار هوشمند تحلیل آثار تاریخی\n\n"
        "👋 خوش اومدی! از منوی زیر انتخاب کن:",
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


async def show_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    case_count = db.count_user_cases(user.id)

    await update.message.reply_text(
        "⚙️ <b>حساب من</b>\n\n"
        f"👤 نام: {user.full_name}\n"
        f"📂 تعداد پرونده‌ها: {case_count}\n"
        f"💎 سطح اشتراک: نسخه‌ی رایگان\n\n"
        "این یک نسخه‌ی آزمایشی و رایگانه؛ در حال حاضر امکانات پولی/اشتراک "
        "فعال نیست.",
        parse_mode="HTML",
    )


async def show_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "💎 <b>نسخه‌ی حرفه‌ای</b>\n\n"
        "مزایای نسخه‌ی حرفه‌ای نسبت به نسخه‌ی رایگان:\n\n"
        "✔ تحلیل نامحدود (بدون محدودیت تعداد در ماه)\n"
        "✔ خروجی PDF از هر گزارش، آماده برای چاپ یا اشتراک‌گذاری\n"
        "✔ آرشیو کامل و بدون محدودیت پرونده‌ها\n"
        "✔ اولویت در پردازش (پاسخ سریع‌تر در ساعات شلوغ)\n"
        "✔ دسترسی زودهنگام به قابلیت‌های جدید\n\n"
        "قیمت: به‌زودی اعلام می‌شه.\n"
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛒 خرید اشتراک", callback_data="sub|buy")]]
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def on_subscription_buy_clicked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "🚧 پرداخت آنلاین هنوز فعال نشده (به‌زودی از طریق درگاه داخلی "
            "زرین‌پال اضافه می‌شه). فعلاً نسخه‌ی رایگان بدون محدودیت در دسترسه."
        ),
    )


async def show_encyclopedia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    buttons = [
        [InlineKeyboardButton(topic["title"], callback_data=f"enc|{key}")]
        for key, topic in ENCYCLOPEDIA_TOPICS.items()
    ]
    await update.message.reply_text(
        "📚 <b>دانشنامه</b>\n\nیکی از موضوعات زیر رو انتخاب کن:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def on_encyclopedia_topic_selected(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    key = query.data.split("|", 1)[1]
    topic = ENCYCLOPEDIA_TOPICS.get(key)
    if topic:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=topic["text"], parse_mode="HTML"
        )


# ---------------------------------------------------------------------------
# جریان «پرونده جدید» — دریافت عکس
# ---------------------------------------------------------------------------
async def start_new_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["photos"] = []
    context.user_data["environment"] = None
    context.user_data["size"] = None
    context.user_data["material"] = None
    context.user_data["notes"] = None

    keyboard = ReplyKeyboardMarkup(
        [[DONE_PHOTOS_BUTTON]], resize_keyboard=True, one_time_keyboard=False
    )
    await update.message.reply_text(
        "📂 <b>پرونده جدید</b>\n\n"
        "برای بهترین نتیجه:\n"
        "✓ حداقل ۲-۳ عکس، حداکثر ۸ عکس\n"
        "✓ نور طبیعی (نه فلاش مستقیم)\n"
        "✓ چند زاویه‌ی مختلف\n"
        "✓ تصاویر واضح و بدون فیلتر\n\n"
        "📷 عکس‌ها رو بفرست:",
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

    case_number = db.create_case(user_id, environment, size, material, notes, analysis)

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
        MessageHandler(filters.Regex(f"^{BTN_ENCYCLOPEDIA}$"), show_encyclopedia)
    )
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_ACCOUNT}$"), show_account))
    application.add_handler(
        MessageHandler(filters.Regex(f"^{BTN_SUBSCRIPTION}$"), show_subscription)
    )
    application.add_handler(CallbackQueryHandler(on_subscription_buy_clicked, pattern=r"^sub\|buy$"))
    application.add_handler(CallbackQueryHandler(on_encyclopedia_topic_selected, pattern=r"^enc\|"))
    application.add_handler(CommandHandler("mycases", my_cases))

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
