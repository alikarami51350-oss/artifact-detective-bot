"""
بررسی ساده و واقعی کیفیت عکس‌ها با استفاده از PIL:
- وضوح: بر اساس تعداد مگاپیکسل تصویر
- نور: بر اساس میانگین روشنایی تصویر (نه خیلی تاریک، نه خیلی سوخته)
هیچ عددی ساختگی نیست؛ همه بر اساس محتوای واقعی فایل عکس محاسبه می‌شه.
"""

from PIL import Image, ImageStat


def _resolution_stars(width: int, height: int) -> int:
    megapixels = (width * height) / 1_000_000
    if megapixels >= 2.0:
        return 5
    if megapixels >= 1.0:
        return 4
    if megapixels >= 0.5:
        return 3
    if megapixels >= 0.2:
        return 2
    return 1


def _brightness_stars(mean_brightness: float) -> int:
    if 90 <= mean_brightness <= 190:
        return 5
    if 70 <= mean_brightness <= 210:
        return 4
    if 50 <= mean_brightness <= 230:
        return 3
    if 30 <= mean_brightness <= 245:
        return 2
    return 1


def analyze_photo(path: str) -> dict:
    with Image.open(path) as img:
        width, height = img.size
        gray = img.convert("L")
        mean_brightness = ImageStat.Stat(gray).mean[0]

    return {
        "resolution_stars": _resolution_stars(width, height),
        "brightness_stars": _brightness_stars(mean_brightness),
    }


def analyze_photos(paths: list) -> dict:
    """میانگین کیفیت روی چند عکس رو برمی‌گردونه."""
    if not paths:
        return {"resolution_stars": 0, "brightness_stars": 0, "overall": "نامشخص"}

    results = [analyze_photo(p) for p in paths]
    avg_resolution = round(sum(r["resolution_stars"] for r in results) / len(results))
    avg_brightness = round(sum(r["brightness_stars"] for r in results) / len(results))

    if avg_resolution >= 4 and avg_brightness >= 4:
        overall = "عالی"
    elif avg_resolution >= 3 and avg_brightness >= 3:
        overall = "خوب"
    else:
        overall = "ضعیف"

    return {
        "resolution_stars": avg_resolution,
        "brightness_stars": avg_brightness,
        "overall": overall,
    }


def stars_to_text(count: int) -> str:
    count = max(0, min(5, count))
    return "★" * count + "☆" * (5 - count)
