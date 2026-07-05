"""تعریف پلن‌های اشتراک و بسته‌های اعتباری."""

PLANS = {
    "explorer": {
        "label": "🎁 کاوشگر (رایگان)",
        "monthly_quota": None,       # نامحدود در ماه ندارد؛ به‌جاش سهمیه‌ی کل داره
        "lifetime_quota": 3,
        "price_month": 0,
        "price_year": 0,
        "features": [
            "۳ پرونده تحلیل رایگان (مجموع)",
            "گزارش خلاصه",
            "تاریخچه‌ی ۷ روزه",
            "دسترسی به دانشنامه",
        ],
    },
    "basic": {
        "label": "🥉 برنزی (Basic)",
        "monthly_quota": 30,
        "lifetime_quota": None,
        "price_month": 149_000,
        "price_year": 1_490_000,
        "features": [
            "۳۰ پرونده در ماه",
            "گزارش کامل",
            "دانلود PDF",
            "آرشیو دائمی",
        ],
    },
    "pro": {
        "label": "🥈 نقره‌ای (Pro) ⭐ محبوب‌ترین",
        "monthly_quota": 100,
        "lifetime_quota": None,
        "price_month": 349_000,
        "price_year": 3_490_000,
        "features": [
            "۱۰۰ پرونده در ماه",
            "گزارش کامل + PDF حرفه‌ای",
            "اولویت در پردازش",
            "مقایسه با تحلیل‌های قبلی",
            "دسترسی زودهنگام به قابلیت‌های جدید",
        ],
    },
    "expert": {
        "label": "🥇 طلایی (Expert)",
        "monthly_quota": 300,
        "lifetime_quota": None,
        "price_month": 699_000,
        "price_year": 6_990_000,
        "features": [
            "۳۰۰ پرونده در ماه",
            "سریع‌ترین پردازش",
            "همه‌ی امکانات پلن Pro",
            "پشتیبانی ویژه",
        ],
    },
}

PLAN_ORDER = ["explorer", "basic", "pro", "expert"]

CREDIT_PACKS = [
    {"count": 10, "price": 79_000},
    {"count": 25, "price": 169_000},
    {"count": 50, "price": 299_000},
    {"count": 100, "price": 549_000},
]

FIRST_PURCHASE_DISCOUNT_PERCENT = 30
FIRST_PURCHASE_PLAN = "pro"
