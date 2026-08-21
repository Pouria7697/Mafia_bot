"""
🎙 گادِ صوتی — یک اکانتِ کاربریِ تلگرام که جمله‌های ثابت را در وویس‌چتِ گروه پخش می‌کند.

- بدونِ سه متغیرِ TG_API_ID / TG_API_HASH / TG_SESSION کاملاً خاموش است و بات
  دقیقاً مثلِ قبل کار می‌کند. هیچ خطایی از این ماژول به بیرون نشت نمی‌کند.
- جمله‌ها از قبل ساخته شده‌اند (voice/<صدا>/<کلید>.raw — PCM خام ۴۸kHz مونو)
  تا روی سرور نه ffmpeg لازم باشد نه سرویسِ TTS.
- TG_VOICE = dilara (پیش‌فرض) | farid
"""
import os
import asyncio

TG_API_ID = os.environ.get("TG_API_ID", "").strip()
TG_API_HASH = os.environ.get("TG_API_HASH", "").strip()
TG_SESSION = os.environ.get("TG_SESSION", "").strip()
TG_VOICE = (os.environ.get("TG_VOICE", "dilara").strip() or "dilara").lower()

VOICE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice")
PCM_BYTES_PER_SEC = 48000 * 2          # s16le, mono

# جمله‌های موجود (کلید → فایل)
PHRASES = ("time_up", "day", "night", "temp_night", "temp_night_end")

_state = {"client": None, "calls": None, "ready": False}
_locks: dict[int, asyncio.Lock] = {}
_warned_no_call: set[int] = set()


def enabled() -> bool:
    return bool(TG_API_ID and TG_API_HASH and TG_SESSION)


def ready() -> bool:
    return bool(_state["ready"])


def phrase_path(key: str):
    """مسیرِ فایلِ جمله — اگر صدای انتخابی آن را ندارد، از dilara."""
    for voice in (TG_VOICE, "dilara"):
        p = os.path.join(VOICE_DIR, voice, f"{key}.raw")
        if os.path.isfile(p):
            return p
    return None


async def start() -> bool:
    """اتصالِ اکانت + موتورِ وویس. خروجی: آماده شد یا نه. هرگز استثنا نمی‌اندازد."""
    if not enabled():
        print("🎙 گادِ صوتی خاموش — TG_API_ID/TG_API_HASH/TG_SESSION تنظیم نشده.")
        return False
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from pytgcalls import PyTgCalls
    except Exception as e:
        print("🎙 گادِ صوتی خاموش — کتابخانه نصب نیست:", e)
        return False
    try:
        client = TelegramClient(StringSession(TG_SESSION), int(TG_API_ID), TG_API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            print("⛔ گادِ صوتی: TG_SESSION معتبر نیست — با make_session.py دوباره بساز.")
            await client.disconnect()
            return False
        me = await client.get_me()
        # 📇 کشِ چت‌ها تا play(chat_id) بتواند گروه را پیدا کند
        try:
            await client.get_dialogs(limit=200)
        except Exception as e:
            print("⚠️ گادِ صوتی: get_dialogs:", e)
        calls = PyTgCalls(client)
        await calls.start()
        _state.update(client=client, calls=calls, ready=True)
        missing = [k for k in PHRASES if not phrase_path(k)]
        print(f"🎙 گادِ صوتی آماده: {me.first_name or ''} (@{me.username or '—'}) — صدا: {TG_VOICE}"
              + (f" | ⚠️ فایلِ ناموجود: {missing}" if missing else ""))
        return True
    except Exception as e:
        print("⛔ گادِ صوتی بالا نیامد:", repr(e))
        return False


def say(chat_id: int, key: str):
    """🔊 پخشِ یک جمله در وویس‌چتِ این گروه — غیرِمسدودکننده، بی‌خطا.
    اگر وویس‌چت باز نباشد یا ماژول آماده نباشد، بی‌صدا رد می‌شود."""
    if not _state["ready"]:
        return
    try:
        asyncio.get_running_loop().create_task(_say(int(chat_id), key))
    except RuntimeError:
        pass


async def _say(chat_id: int, key: str):
    path = phrase_path(key)
    if not path:
        print(f"⚠️ گادِ صوتی: فایلِ «{key}» نیست.")
        return
    lock = _locks.setdefault(chat_id, asyncio.Lock())
    async with lock:                     # جمله‌ها پشتِ هم، نه روی هم
        try:
            from pytgcalls.types.raw import Stream, AudioStream, AudioParameters
            from ntgcalls import MediaSource
            stream = Stream(microphone=AudioStream(
                MediaSource.FILE, path, AudioParameters(48000, 1)))
            await _state["calls"].play(chat_id, stream)
            _warned_no_call.discard(chat_id)
            # ⏳ تا تمام‌شدنِ همین جمله صبر کن تا جملهٔ بعدی رویش نیفتد
            await asyncio.sleep(os.path.getsize(path) / PCM_BYTES_PER_SEC + 0.4)
        except Exception as e:
            name = type(e).__name__
            if name == "NoActiveGroupCall":
                if chat_id not in _warned_no_call:
                    _warned_no_call.add(chat_id)
                    print(f"🎙 گادِ صوتی: وویس‌چتِ {chat_id} باز نیست — «{key}» پخش نشد.")
            else:
                print(f"⚠️ گادِ صوتی ({key} در {chat_id}): {name}: {e}")


async def leave(chat_id: int):
    """🚪 خروج از وویس‌چتِ این گروه (پایانِ بازی). بی‌خطا."""
    if not _state["ready"]:
        return
    try:
        await _state["calls"].leave_call(int(chat_id))
    except Exception:
        pass
    _warned_no_call.discard(int(chat_id))
