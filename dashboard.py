"""تولید صفحه‌ی HTML داشبورد مدیریتی (فقط نمایشی، بدون امکان ویرایش)."""

import html


def _escape(value) -> str:
    return html.escape(str(value)) if value is not None else "—"


def render_dashboard_html(stats: dict, cases: list, users: list) -> str:
    plan_rows = "".join(
        f"<tr><td>{_escape(plan_id)}</td><td>{count}</td></tr>"
        for plan_id, count in stats["users_by_plan"].items()
    )

    case_rows = "".join(
        f"<tr>"
        f"<td>{_escape(c['case_number'])}</td>"
        f"<td>{_escape(c['user_id'])}</td>"
        f"<td>{_escape(c['created_at'])[:16]}</td>"
        f"<td>{_escape(c['environment'])}</td>"
        f"<td>{_escape(c['size'])}</td>"
        f"<td>{_escape(c['material'])}</td>"
        f"<td>{'✅' if c['used_credit'] else ''}</td>"
        f"</tr>"
        for c in cases
    )

    user_rows = "".join(
        f"<tr>"
        f"<td>{_escape(u['user_id'])}</td>"
        f"<td>{_escape(u['plan'])}</td>"
        f"<td>{_escape(u['analysis_credits'])}</td>"
        f"<td>{_escape(u['wallet_toman'])}</td>"
        f"<td>{_escape(u['successful_referrals'])}</td>"
        f"<td>{_escape(u['loyalty_points'])}</td>"
        f"<td>{_escape(u['joined_at'])[:16]}</td>"
        f"</tr>"
        for u in users
    )

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>داشبورد مدیریتی ArchaeoLens</title>
<style>
  body {{ font-family: Tahoma, Arial, sans-serif; background:#f5f5f7; margin:0; padding:24px; color:#1f2937; }}
  h1 {{ font-size:22px; margin-bottom:4px; }}
  h2 {{ font-size:16px; margin-top:32px; color:#374151; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:12px; margin-top:16px; }}
  .card {{ background:#fff; border:1px solid #e5e7eb; border-radius:12px; padding:16px 20px; min-width:160px; }}
  .card .label {{ font-size:12px; color:#6b7280; }}
  .card .value {{ font-size:22px; font-weight:bold; margin-top:4px; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; border-radius:8px; overflow:hidden; margin-top:8px; }}
  th, td {{ padding:8px 10px; text-align:right; font-size:13px; border-bottom:1px solid #eee; }}
  th {{ background:#f0f0f3; color:#374151; }}
  tr:hover {{ background:#fafafa; }}
  .muted {{ color:#9ca3af; font-size:12px; margin-top:24px; }}
</style>
</head>
<body>
  <h1>📊 داشبورد مدیریتی ArchaeoLens</h1>
  <div class="muted">این صفحه فقط نمایشیه (Read-only). برای تایید/رد موارد از خود ربات تلگرام استفاده کن.</div>

  <div class="cards">
    <div class="card"><div class="label">کل کاربران</div><div class="value">{stats['total_users']}</div></div>
    <div class="card"><div class="label">کل پرونده‌ها</div><div class="value">{stats['total_cases']}</div></div>
    <div class="card"><div class="label">پرونده‌های امروز</div><div class="value">{stats['cases_today']}</div></div>
    <div class="card"><div class="label">پرونده‌های ۳۰ روز اخیر</div><div class="value">{stats['cases_last_30_days']}</div></div>
    <div class="card"><div class="label">کل دعوت‌های پاداش‌دار</div><div class="value">{stats['total_referrals']}</div></div>
    <div class="card"><div class="label">کل درآمد</div><div class="value">{stats['total_revenue']:,} ت</div></div>
    <div class="card"><div class="label">درآمد ۳۰ روز اخیر</div><div class="value">{stats['revenue_last_30_days']:,} ت</div></div>
    <div class="card"><div class="label">تعداد خرید تاییدشده</div><div class="value">{stats['total_purchases']}</div></div>
  </div>

  <h2>کاربران به تفکیک پلن</h2>
  <table>
    <tr><th>پلن</th><th>تعداد کاربر</th></tr>
    {plan_rows}
  </table>

  <h2>آخرین پرونده‌ها (حداکثر ۵۰ مورد)</h2>
  <table>
    <tr><th>شماره پرونده</th><th>آیدی کاربر</th><th>تاریخ</th><th>محیط</th><th>اندازه</th><th>جنس</th><th>با اعتبار</th></tr>
    {case_rows}
  </table>

  <h2>کاربران (حداکثر ۲۰۰ نفر، جدیدترین اول)</h2>
  <table>
    <tr><th>آیدی</th><th>پلن</th><th>اعتبار</th><th>کیف پول (تومان)</th><th>دعوت موفق</th><th>امتیاز</th><th>تاریخ عضویت</th></tr>
    {user_rows}
  </table>
</body>
</html>"""
