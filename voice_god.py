"""
🎙 گادِ صوتی — یک اکانتِ کاربریِ تلگرام که جمله‌های ثابت را در وویس‌چتِ گروه پخش می‌کند.

- بدونِ سه متغیرِ TG_API_ID / TG_API_HASH / TG_SESSION کاملاً خاموش است و بات
  دقیقاً مثلِ قبل کار می‌کند. هیچ خطایی از این ماژول به بیرون نشت نمی‌کند.
- جمله‌ها از قبل ساخته شده‌اند (voice/<صدا>/<کلید>.raw — PCM خام ۴۸kHz مونو)
  تا روی سرور نه ffmpeg لازم باشد نه سرویسِ TTS.
- صدای سفارشی (وویسِ آپلودشده در پیویِ سازنده) در voice/custom/<کلید>.raw می‌نشیند
  و بر صدای پیش‌فرض اولویت دارد. تبدیلش با ffmpegِ همراهِ imageio-ffmpeg انجام می‌شود.
- TG_VOICE = dilara (پیش‌فرض) | farid
- TG_VOLUME = بلندیِ اکانت در وویس‌چت، ۱ تا ۲۰۰ (پیش‌فرض ۱۵۰) — یک‌بار بعد از اولین پخش در هر گروه
"""
import os
import asyncio

TG_API_ID = os.environ.get("TG_API_ID", "").strip()
TG_API_HASH = os.environ.get("TG_API_HASH", "").strip()
TG_SESSION = os.environ.get("TG_SESSION", "").strip()
TG_VOICE = (os.environ.get("TG_VOICE", "dilara").strip() or "dilara").lower()
try:
    TG_VOLUME = max(1, min(200, int(os.environ.get("TG_VOLUME", "150"))))
except ValueError:
    TG_VOLUME = 150

VOICE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice")
CUSTOM_DIR = os.path.join(VOICE_DIR, "custom")
PCM_BYTES_PER_SEC = 48000 * 2          # s16le, mono

# جمله‌های موجود (کلید → فایل)
PHRASES = ("time_up", "day", "night", "temp_night", "temp_night_end")

_state = {"client": None, "calls": None, "ready": False}
_locks: dict[int, asyncio.Lock] = {}
_warned_no_call: set[int] = set()
_volume_set: set[int] = set()


def enabled() -> bool:
    return bool(TG_API_ID and TG_API_HASH and TG_SESSION)


def ready() -> bool:
    return bool(_state["ready"])


# ─── فایل‌های صدا ───────────────────────────────────────────
def custom_path(key: str) -> str:
    return os.path.join(CUSTOM_DIR, f"{key}.raw")


def has_custom(key: str) -> bool:
    p = custom_path(key)
    return os.path.isfile(p) and os.path.getsize(p) > 0


def phrase_path(key: str):
    """مسیرِ فایلِ جمله: سفارشی → صدای انتخابی → dilara."""
    if has_custom(key):
        return custom_path(key)
    for voice in (TG_VOICE, "dilara"):
        p = os.path.join(VOICE_DIR, voice, f"{key}.raw")
        if os.path.isfile(p):
            return p
    return None


def save_custom(key: str, raw: bytes) -> str:
    os.makedirs(CUSTOM_DIR, exist_ok=True)
    p = custom_path(key)
    tmp = p + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, p)
    return p


def remove_custom(key: str) -> bool:
    p = custom_path(key)
    try:
        os.remove(p)
        return True
    except FileNotFoundError:
        return False


def ffmpeg_exe():
    """مسیرِ ffmpeg — همراهِ بستهٔ imageio-ffmpeg (بدونِ نیاز به نصبِ سیستمی)."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        print("⚠️ گادِ صوتی: ffmpeg در دسترس نیست:", e)
        return None


async def convert_to_raw(data: bytes):
    """هر فایلِ صوتی (ogg/mp3/m4a/…) → PCM خام ۴۸kHz مونو، با نرمال‌سازیِ بلندی.
    خروجی bytes یا None. هرگز استثنا نمی‌اندازد."""
    ff = ffmpeg_exe()
    if not ff or not data:
        return None
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
           # 🔊 بلندیِ یکنواخت و نزدیکِ سقف + کمی سکوت در ابتدا/انتها تا لبه‌ها نیفتد
           "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,adelay=200|200,apad=pad_dur=0.3",
           "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "1", "pipe:1"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(input=data), timeout=60)
        if proc.returncode != 0 or len(out) < PCM_BYTES_PER_SEC // 4:
            print("⚠️ گادِ صوتی: ffmpeg:", (err or b"")[:300].decode("utf-8", "ignore"))
            return None
        return out
    except Exception as e:
        print("⚠️ گادِ صوتی: تبدیل ناموفق:", repr(e))
        return None


# ─── اتصال ──────────────────────────────────────────────────
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
        print(f"🎙 گادِ صوتی آماده: {me.first_name or ''} (@{me.username or '—'}) — "
              f"صدا: {TG_VOICE} | بلندی: {TG_VOLUME}"
              + (f" | ⚠️ فایلِ ناموجود: {missing}" if missing else ""))
        return True
    except Exception as e:
        print("⛔ گادِ صوتی بالا نیامد:", repr(e))
        return False


# ─── پخش ────────────────────────────────────────────────────
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
            # 🔊 بلندیِ اکانت در این وویس‌چت — فقط یک‌بار بعد از اولین پخشِ موفق
            if chat_id not in _volume_set and TG_VOLUME != 100:
                _volume_set.add(chat_id)
                try:
                    await _state["calls"].change_volume_call(chat_id, TG_VOLUME)
                except Exception as e:
                    print(f"⚠️ گادِ صوتی: تنظیمِ بلندی در {chat_id}: {type(e).__name__}: {e}")
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
    _volume_set.discard(int(chat_id))
