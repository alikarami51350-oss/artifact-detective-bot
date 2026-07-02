
"""
ربات تلگرامی «کارآگاه باستانی»
مخاطب چند عکس از یک شی از زوایای مختلف می‌فرسته + به چند سوال جواب می‌ده،
بعد ربات با استفاده از Gemini (Google AI, رایگان و بدون نیاز به کارت بانکی)
تحلیل می‌کنه که آیا این شی احتمالاً ساخته‌ی دست بشره یا پدیده‌ی طبیعی، و
در صورت انسان‌ساخت بودن، حدس می‌زنه برای چه هدفی ساخته شده.
"""
 
import asyncio
import logging
import os
 
from dotenv import load_dotenv
from google import genai
from PIL import Image
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
 
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
COLLECTING_PHOTOS, ASKING_QUESTIONS = range(2)
 
# سوالاتی که به ترتیب از کاربر پرسیده می‌شه
QUESTIONS = [
    "این شی رو دقیقاً کجا پیدا کردید؟ (مثلاً زیر خاک، کنار رودخانه، داخل غار، سطح زمین و غیره)",
    "به نظر شما جنس این شی چیه؟ (سنگ، فلز، سفال، چوب، استخوان، شیشه و ...)",
    "اندازه و وزن تقریبی‌ش چقدره؟",
    "آیا روی سطح شی نشونه‌ای از کنده‌کاری، الگوی هندسی منظم، یا علامت خاصی دیده می‌شه؟",
    "در اطراف محل پیدا شدنش، چیز دیگه‌ای هم بود؟ (مثل خرده‌سفال، استخوان، بقایای دیگه)",
    "حدس شخصی خودتون چیه؟ فکر می‌کنید ساخته‌ی دست بشره یا یه پدیده‌ی طبیعیه؟ چرا؟",
]
 
DONE_BUTTON = "✅ عکس‌ها تمام شد"
 
 
# ---------------------------------------------------------------------------
# هندلرها
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["photos"] = []
    context.user_data["answers"] = []
    context.user_data["question_index"] = 0
 
    keyboard = ReplyKeyboardMarkup([[DONE_BUTTON]], resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text(
        "سلام! 🏺 من کارآگاه باستانی هستم.\n\n"
        "لطفاً چند عکس (حداقل ۳ تا) از شیء مورد نظرتون از زاویه‌های مختلف "
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
            "⚠️ بعد از چند تلاش هم نشد این عکس رو دانلود کنم. اتصال اینترنتت "
            "(فیلترشکن) خیلی ضعیفه. لطفاً یه سرور دیگه توی Psiphon امتحان کن و "
            "بعد دوباره همین عکس رو بفرست."
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
        f"عالی، {len(photos)} عکس دریافت شد. حالا چند تا سوال ازتون می‌پرسم.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.message.reply_text(QUESTIONS[0])
    return ASKING_QUESTIONS
 
 
async def receive_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["answers"].append(update.message.text)
    context.user_data["question_index"] += 1
    idx = context.user_data["question_index"]
 
    if idx < len(QUESTIONS):
        await update.message.reply_text(QUESTIONS[idx])
        return ASKING_QUESTIONS
 
    await update.message.reply_text(
        "ممنون! دارم عکس‌ها و جواب‌هاتون رو با دقت بررسی می‌کنم... 🔍🧠"
    )
    await analyze_and_reply(update, context)
    return ConversationHandler.END
 
 
async def analyze_and_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    photos = context.user_data.get("photos", [])
    answers = context.user_data.get("answers", [])
 
    qa_text = "\n".join(
        f"- {q}\n  پاسخ کاربر: {a}" for q, a in zip(QUESTIONS, answers)
    )
 
    system_prompt = (
        "تو یک باستان‌شناس باتجربه و تحلیلگر تصویر هستی. کاربر چند عکس از یک شی "
        "از زوایای مختلف به همراه چند پاسخ درباره‌ی زمینه‌ی پیدا شدن آن شی فرستاده. "
        "وظیفه‌ی تو اینه که فقط بر اساس شواهد بصری واقعی موجود در عکس‌ها "
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
 
    try:
        await update.message.reply_text(analysis)
    except Exception:  # noqa: BLE001
        logger.exception("ارسال پیام تحلیل به کاربر با خطا مواجه شد")
        await update.message.reply_text(
            "⚠️ در ارسال نتیجه‌ی تحلیل مشکلی پیش اومد. لطفاً دوباره با /start امتحان کن."
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
            ASKING_QUESTIONS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
 
    application.add_handler(conv_handler)
 
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
 
