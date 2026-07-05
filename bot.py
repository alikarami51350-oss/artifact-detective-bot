"""
ربات تلگرامی «کارآگاه باستانی» (ArchaeoLens)
مخاطب چند عکس از یک شی از زوایای مختلف می‌فرسته + با دکمه به چند سوال
جواب می‌ده، بعد ربات با استفاده از Gemini تحلیل می‌کنه که آیا این شی
احتمالاً ساخته‌ی دست بشره یا پدیده‌ی طبیعی، و نتیجه رو با یک شماره‌ی
پرونده‌ی یکتا ذخیره می‌کنه.
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
 
load_dotenv()
 
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
 
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)
 
gemini_client = genai.Client(api_key=GEMINI_API_KEY)
 
# ---------------------------------------------------------------------------
# مراحل مکالمه
# ---------------------------------------------------------------------------
COLLECTING_PHOTOS, ASK_ENVIRONMENT, ASK_SIZE, ASK_MATERIAL, ASK_NOTES = range(5)
 
DONE_BUTTON = "✅ عکس‌ها تمام شد"
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
# هندلرهای شروع و دریافت عکس
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["photos"] = []
    context.user_data["environment"] = None
    context.user_data["size"] = None
    context.user_data["material"] = None
    context.user_data["notes"] = None
 
    keyboard = ReplyKeyboardMarkup([[DONE_BUTTON]], resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text(
        "سلام! 🏛 من کارآگاه باستانی (ArchaeoLens) هستم.\n\n"
        "لطفاً چند عکس (حداقل ۲-۳ تا) از شیء مورد نظرتون از زاویه‌های مختلف "
        "برام بفرستید. سعی کنید نور کافی باشه و جزئیات سطح شی مشخص باشه.\n\n"
        "وقتی عکس‌ها تموم شد، دکمه‌ی زیر رو بزنید.",
        reply_markup=keyboard,
    )
    return COLLECTING_PHOTOS
 
 
async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo = update.message.photo[-1]  # بالاترین کیفیت
    file_path = f"/tmp/{update.effective_chat.id}_{len(context.user_data['photos'])}.jpg"
 
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
 
    context.user_data["photos"].append(file_path)
 
    count = len(context.user_data["photos"])
    await update.message.reply_text(f"عکس شماره {count} دریافت شد. 📸")
    return COLLECTING_PHOTOS
 
 
async def done_with_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photos = context.user_data.get("photos", [])
    if len(photos) < 2:
        await update.message.reply_text(
            "برای تحلیل بهتره حداقل ۲-۳ عکس از زوایای مختلف بفرستید. لطفاً چند عکس دیگه بفرستید."
        )
        return COLLECTING_PHOTOS
 
    await update.message.reply_text(
        f"عالی، {len(photos)} عکس دریافت شد. حالا چند تا سوال کوتاه ازتون می‌پرسم.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.message.reply_text(
        "📍 محیط کشف این شی کجا بود؟",
        reply_markup=build_inline_keyboard(ENVIRONMENT_OPTIONS, "env"),
    )
    return ASK_ENVIRONMENT
 
 
# ---------------------------------------------------------------------------
# هندلرهای دکمه‌ای (Callback Query)
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
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="ممنون! دارم عکس‌ها و اطلاعاتتون رو با دقت بررسی می‌کنم... 🔍🧠",
    )
    await analyze_and_reply(update, context)
    return ConversationHandler.END
 
 
async def receive_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["notes"] = update.message.text
    await update.message.reply_text(
        "ممنون! دارم عکس‌ها و اطلاعاتتون رو با دقت بررسی می‌کنم... 🔍🧠"
    )
    await analyze_and_reply(update, context)
    return ConversationHandler.END
 
 
# ---------------------------------------------------------------------------
# دستور «پرونده‌های من»
# ---------------------------------------------------------------------------
async def my_cases(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    cases = db.get_user_cases(user_id, limit=10)
 
    if not cases:
        await update.message.reply_text(
            "هنوز هیچ پرونده‌ای ثبت نشده. با /start یه تحلیل جدید شروع کن."
        )
        return
 
    lines = ["📂 آخرین پرونده‌های شما:\n"]
    for case in cases:
        date_str = case["created_at"][:10]
        lines.append(
            f"🔖 {case['case_number']} — {date_str}\n"
            f"   محیط: {case['environment']} | اندازه: {case['size']} | جنس: {case['material']}\n"
        )
 
    await update.message.reply_text("\n".join(lines))
 
 
# ---------------------------------------------------------------------------
# تحلیل نهایی با Gemini
# ---------------------------------------------------------------------------
async def analyze_and_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    photos = context.user_data.get("photos", [])
    environment = context.user_data.get("environment", "نامشخص")
    size = context.user_data.get("size", "نامشخص")
    material = context.user_data.get("material", "نامشخص")
    notes = context.user_data.get("notes", "—")
 
    qa_text = (
        f"- محیط کشف: {environment}\n"
        f"- اندازه‌ی تقریبی: {size}\n"
        f"- جنس احتمالی: {material}\n"
        f"- توضیح اضافه‌ی کاربر: {notes}\n"
    )
 
    system_prompt = (
        "تو یک باستان‌شناس باتجربه و تحلیلگر تصویر هستی. کاربر چند عکس از یک شی "
        "از زوایای مختلف به همراه چند اطلاعات زمینه‌ای درباره‌ی محل پیدا شدن آن شی "
        "فرستاده. وظیفه‌ی تو اینه که فقط بر اساس شواهد بصری واقعی موجود در عکس‌ها "
        "(شکل، تقارن، بافت سطح، الگوهای هندسی، آثار ابزار، فرسایش طبیعی و غیره) "
        "و اطلاعات زمینه‌ای که کاربر داده، یک تحلیل مستدل و شفاف ارائه بدی. "
        "پاسخ باید شامل این بخش‌ها باشه:\n"
        "۱. نتیجه‌گیری کلی (احتمال ساخته‌ی دست بشر بودن یا پدیده‌ی طبیعی بودن) با یک درصد اطمینان تقریبی.\n"
        "۲. دلایل مشخص و مبتنی بر شواهد بصری برای این نتیجه‌گیری.\n"
        "۳. اگر احتمال انسان‌ساخت بودن بالاست، حدس بزن این شی احتمالاً برای چه کاربردی ساخته شده و به چه دوره یا سبکی ممکنه تعلق داشته باشه.\n"
        "۴. توصیه‌ی نهایی: تاکید کن که این فقط یک تحلیل اولیه‌ی هوش مصنوعیه و برای تایید قطعی باید حتماً "
        "با یک باستان‌شناس یا سازمان میراث فرهنگی محلی تماس بگیرن، به‌خصوص قبل از هرگونه جابجایی یا حفاری بیشتر.\n\n"
        "لحن پاسخ باید حرفه‌ای، جذاب و قابل فهم برای عموم باشه، اما هرگز چیزی رو که در عکس‌ها قابل مشاهده نیست "
        "ادعا نکن و از قطعیت کاذب پرهیز کن. صادق و مبتنی بر شواهد باش."
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
                "لطفاً دوباره با /start شروع کن و عکس‌های واضح‌تری امتحان کن."
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("خطا در فراخوانی Gemini API")
        analysis = (
            "متاسفانه در حال حاضر امکان تحلیل وجود نداره. "
            f"جزئیات خطا برای دیباگ: {exc}"
        )
 
    # ذخیره‌ی پرونده در دیتابیس و ساخت شماره‌ی پرونده
    user_id = update.effective_user.id
    case_number = db.create_case(user_id, environment, size, material, notes, analysis)
 
    final_message = f"📁 شماره‌ی پرونده: {case_number}\n\n{analysis}"
 
    try:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=final_message)
    except Exception:  # noqa: BLE001
        logger.exception("ارسال پیام تحلیل به کاربر با خطا مواجه شد")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ در ارسال نتیجه‌ی تحلیل مشکلی پیش اومد. لطفاً دوباره با /start امتحان کن.",
        )
 
    # پاکسازی فایل‌های موقت
    for path in photos:
        try:
            os.remove(path)
        except OSError:
            pass
 
 
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "باشه، لغو شد. هر وقت خواستید دوباره با /start شروع کنید.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END
 
 
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
 
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            COLLECTING_PHOTOS: [
                MessageHandler(filters.PHOTO, receive_photo),
                MessageHandler(filters.Regex(f"^{DONE_BUTTON}$"), done_with_photos),
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
 
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("mycases", my_cases))
 
    # اگه این متغیر وجود داشته باشه یعنی روی Render (یا سرویس مشابه) اجرا می‌شیم
    # و باید از حالت Webhook استفاده کنیم. در غیر این صورت (روی کامپیوتر شخصی)
    # از همون Polling قبلی استفاده می‌کنیم.
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
