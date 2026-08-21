"""
🔑 ساختِ TG_SESSION برای اکانتِ گادِ صوتی — فقط یک‌بار، روی کامپیوترِ خودت.

اجرا:
    pip install telethon
    python make_session.py

شماره را با کدِ کشور بده (مثلاً +98912…). کدِ تأیید به خودِ تلگرامِ همان اکانت می‌آید.
خروجی در فایلِ tg_session.txt ذخیره می‌شود → محتوایش را در رندر بگذار (Key: TG_SESSION)
و بعد فایل را پاک کن. ⚠️ این رشته معادلِ رمزِ اکانت است — به هیچ‌کس نده.
"""
import sys

try:
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
except ImportError:
    print("⛔ اول این را بزن:  pip install telethon")
    sys.exit(1)

print("— همان مقدارهایی که از my.telegram.org گرفتی —")
api_id = input("API_ID (عدد): ").strip()
api_hash = input("API_HASH: ").strip()
if not api_id.isdigit() or len(api_hash) < 20:
    print("⛔ مقدارها درست به نظر نمی‌رسند.")
    sys.exit(1)

print("\n— حالا شمارهٔ اکانتِ جدید را می‌پرسد، بعد کدی که به تلگرامِ همان اکانت می‌آید —")
with TelegramClient(StringSession(), int(api_id), api_hash) as client:
    me = client.get_me()
    sess = client.session.save()

with open("tg_session.txt", "w", encoding="utf-8") as f:
    f.write(sess)

print(f"\n✅ وارد شد: {me.first_name or ''} (@{me.username or '—'})")
print("📄 رشتهٔ session در فایلِ tg_session.txt ذخیره شد.")
print("   → محتوایش را کامل کپی کن و در رندر بگذار: Environment → TG_SESSION")
print("   → بعدش فایل را پاک کن.")
