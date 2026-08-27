"""
🎙 گادِ صوتی — پلِ باتِ اصلی به کارگرِ صوتی (voice_worker.py) که در «پروسهٔ جدا» اجرا می‌شود.

- بدونِ سه متغیرِ TG_API_ID / TG_API_HASH / TG_SESSION کاملاً خاموش است و بات
  دقیقاً مثلِ قبل کار می‌کند. هیچ خطایی از این ماژول به بیرون نشت نمی‌کند.
- کارگر در پروسهٔ جدا اجرا می‌شود: کرش، هنگ یا قطعیِ اکانت فقط «صدا» را می‌برد،
  نه بات را؛ و همین‌جا خودکار دوباره بالا می‌آید (با سقفِ تلاش).
- جمله‌ها از قبل ساخته شده‌اند (voice/<صدا>/<کلید>.raw — PCM خام ۴۸kHz مونو).
- صدای سفارشی (وویسِ آپلودشده در پیویِ سازنده) در voice/custom/<کلید>.raw می‌نشیند
  و بر صدای پیش‌فرض اولویت دارد. تبدیلش با ffmpegِ همراهِ imageio-ffmpeg است.
- TG_VOICE = dilara (پیش‌فرض) | farid
- TG_VOLUME = بلندیِ اکانت در وویس‌چت، ۱ تا ۲۰۰ (پیش‌فرض ۱۵۰)
"""
import os
import re
import sys
import json
import time
import asyncio

TG_API_ID = os.environ.get("TG_API_ID", "").strip()
TG_API_HASH = os.environ.get("TG_API_HASH", "").strip()
TG_SESSION = os.environ.get("TG_SESSION", "").strip()
TG_VOICE = (os.environ.get("TG_VOICE", "dilara").strip() or "dilara").lower()
try:
    TG_VOLUME = max(1, min(200, int(os.environ.get("TG_VOLUME", "150"))))
except ValueError:
    TG_VOLUME = 150

_HERE = os.path.dirname(os.path.abspath(__file__))
VOICE_DIR = os.path.join(_HERE, "voice")
CUSTOM_DIR = os.path.join(VOICE_DIR, "custom")
CACHE_DIR = os.path.join(VOICE_DIR, "cache")     # جمله‌های سرِهم‌شده (قابلِ بازسازی)
WORKER_PATH = os.path.join(_HERE, "voice_worker.py")
PCM_BYTES_PER_SEC = 48000 * 2          # s16le, mono

# جمله‌های موجود (کلید → فایل)
PHRASES = ("time_up", "day", "night", "temp_night", "temp_night_end",
           "yakuza", "nato", "jalad", "maarefe", "mine_on")

# 🗳 «رأی‌گیری برای صندلیِ N» — کلیدِ vote_N
#    سفارشیِ کامل (vote_N) → وگرنه پیشوند (vote_prefix، سفارشی یا پیش‌فرض) + شمارهٔ پیش‌فرض (num_N)
VOTE_SEAT_MAX = 20
_VOTE_RE = re.compile(r"^vote_(\d{1,2})$")

# ── نگهداریِ پروسهٔ کارگر ──
RESTART_DELAY = 10          # ثانیه صبر قبل از بالا آوردنِ دوباره
RESTART_MAX = 6             # حداکثر تلاش در هر پنجره
RESTART_WINDOW = 3600       # طولِ پنجره (ثانیه)
PING_EVERY = 90             # هر چند ثانیه یک پینگ
PING_TIMEOUT = 30           # بی‌جوابی بیش از این → کارگر هنگ کرده → کشته و دوباره ساخته می‌شود

_state = {
    "proc": None, "ready": False, "info": {}, "quitting": False,
    "ready_evt": None, "restarts": [], "ping_id": 0, "pong_id": 0,
}


def enabled() -> bool:
    return bool(TG_API_ID and TG_API_HASH and TG_SESSION)


def ready() -> bool:
    return bool(_state["ready"])


# ─── فایل‌های صدا (مشترک با کارگر) ─────────────────────────────
def custom_path(key: str) -> str:
    return os.path.join(CUSTOM_DIR, f"{key}.raw")


def has_custom(key: str) -> bool:
    p = custom_path(key)
    return os.path.isfile(p) and os.path.getsize(p) > 0


def _default_path(key: str):
    """فایلِ پیش‌فرضِ یک کلید: صدای انتخابی → dilara."""
    for voice in (TG_VOICE, "dilara"):
        p = os.path.join(VOICE_DIR, voice, f"{key}.raw")
        if os.path.isfile(p):
            return p
    return None


def _vote_path(n: int):
    """🗳 «رأی‌گیری برای صندلیِ n»: سفارشیِ کامل، وگرنه پیشوند + شماره را سرِ هم می‌کند."""
    if has_custom(f"vote_{n}"):
        return custom_path(f"vote_{n}")
    prefix = custom_path("vote_prefix") if has_custom("vote_prefix") else _default_path("vote_prefix")
    num = _default_path(f"num_{n}")
    if not prefix or not num:
        return None
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        # 🔑 نامِ کش از «هویتِ» منابع ساخته می‌شود (سفارشی/پیش‌فرض + زمان + اندازه) —
        #    پس با حذف یا تعویضِ پیشوندِ سفارشی، خودبه‌خود کشِ تازه ساخته می‌شود
        sig = "_".join(f"{'c' if p.startswith(CUSTOM_DIR) else 'd'}{int(os.path.getmtime(p))}-{os.path.getsize(p)}"
                       for p in (prefix, num))
        out = os.path.join(CACHE_DIR, f"vote_{n}__{sig}.raw")
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return out
        with open(prefix, "rb") as a, open(num, "rb") as b:
            data = a.read() + b.read()
        tmp = out + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, out)
        # 🧹 کش‌های قدیمیِ همین صندلی
        for fn in os.listdir(CACHE_DIR):
            if fn.startswith(f"vote_{n}__") and fn != os.path.basename(out):
                try:
                    os.remove(os.path.join(CACHE_DIR, fn))
                except Exception:
                    pass
        return out
    except Exception as e:
        print("⚠️ گادِ صوتی: ساختِ جملهٔ رأی‌گیری:", repr(e))
        return None


def phrase_path(key: str):
    """مسیرِ فایلِ جمله: سفارشی → صدای انتخابی → dilara. (vote_N سرِ هم می‌شود)"""
    m = _VOTE_RE.match(key or "")
    if m:
        n = int(m.group(1))
        return _vote_path(n) if 1 <= n <= VOTE_SEAT_MAX else None
    if has_custom(key):
        return custom_path(key)
    return _default_path(key)


def save_custom(key: str, raw: bytes) -> str:
    os.makedirs(CUSTOM_DIR, exist_ok=True)
    p = custom_path(key)
    tmp = p + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, p)
    return p


def remove_custom(key: str) -> bool:
    try:
        os.remove(custom_path(key))
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
    # ✂️ سکوتِ اول و آخر بریده می‌شود (TTS و ضبطِ گوشی هر دو سکوتِ اضافه دارند)،
    #    بعد بلندیِ یکنواخت، بعد فقط ۰٫۱۵s سکوت جلو و ۰٫۲۵s عقب — تا جمله‌های
    #    سرِهم‌شده (پیشوند + شماره) بدونِ مکثِ عجیب و لبه‌ها بدونِ بریدگی باشند.
    trim = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.08"
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
           "-af", f"{trim},areverse,{trim},areverse,"
                  "loudnorm=I=-14:TP=-1.5:LRA=11,adelay=150|150,apad=pad_dur=0.25",
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


# ─── پروسهٔ کارگر ───────────────────────────────────────────
async def start() -> bool:
    """بالا آوردنِ کارگر و انتظار برای آماده‌شدنش. هرگز استثنا نمی‌اندازد."""
    if not enabled():
        print("🎙 گادِ صوتی خاموش — TG_API_ID/TG_API_HASH/TG_SESSION تنظیم نشده.")
        return False
    if not os.path.isfile(WORKER_PATH):
        print("🎙 گادِ صوتی خاموش — voice_worker.py پیدا نشد.")
        return False
    _state["quitting"] = False
    ok = await _spawn()
    if ok:
        asyncio.get_running_loop().create_task(_pinger())
    return ok


async def _spawn() -> bool:
    evt = asyncio.Event()
    _state.update(ready=False, ready_evt=evt, info={})
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", WORKER_PATH,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=None,                        # stderr مستقیم به لاگِ رندر
            cwd=_HERE)
    except Exception as e:
        print("⛔ گادِ صوتی: کارگر اجرا نشد:", repr(e))
        return False
    _state["proc"] = proc
    asyncio.get_running_loop().create_task(_pump(proc))
    try:
        await asyncio.wait_for(evt.wait(), timeout=60)
    except asyncio.TimeoutError:
        print("⚠️ گادِ صوتی: کارگر در ۶۰ ثانیه آماده نشد.")
    if _state["ready"]:
        info = _state["info"]
        print(f"🎙 گادِ صوتی آماده: {info.get('name', '')} (@{info.get('username') or '—'}) — "
              f"صدا: {TG_VOICE} | بلندی: {TG_VOLUME} | پروسهٔ جدا pid={proc.pid}")
    return bool(_state["ready"])


async def _pump(proc):
    """خواندنِ stdoutِ کارگر: خط‌های @@ پروتکل‌اند، بقیه لاگ. با پایانِ پروسه → راه‌اندازیِ دوباره."""
    try:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "ignore").rstrip("\n")
            if line.startswith("@@READY"):
                try:
                    _state["info"] = json.loads(line[len("@@READY"):].strip() or "{}")
                except Exception:
                    _state["info"] = {}
                _state["ready"] = True
                # 🎥 کارگرِ تازه از ضبط‌های در جریان خبر ندارد — دوباره بگو
                for _c, _st in _rec_games.items():
                    _send({"cmd": "rec_watch", "chat": _c, **_st})
                while _rec_finishes:
                    _send(_rec_finishes.pop(0))
                if _state["ready_evt"]:
                    _state["ready_evt"].set()
            elif line.startswith("@@FAILED"):
                print("⛔ گادِ صوتی:", line[len("@@FAILED"):].strip())
                if _state["ready_evt"]:
                    _state["ready_evt"].set()
            elif line.startswith("@@PONG"):
                try:
                    _state["pong_id"] = int(line.split()[1])
                except Exception:
                    pass
            elif line:
                print("🎙│", line)
    except Exception as e:
        print("⚠️ گادِ صوتی: خواندنِ خروجیِ کارگر:", repr(e))
    finally:
        code = None
        try:
            code = await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            pass
        if _state["proc"] is proc:
            _state["ready"] = False
            _state["proc"] = None
            if _state["ready_evt"]:
                _state["ready_evt"].set()
        if not _state["quitting"]:
            print(f"⚠️ گادِ صوتی: کارگر خارج شد (code={code}) — بات سالم است؛ فقط صدا قطع شد.")
            asyncio.get_running_loop().create_task(_restart_later())


async def _restart_later():
    now = time.time()
    _state["restarts"] = [t for t in _state["restarts"] if now - t < RESTART_WINDOW]
    if len(_state["restarts"]) >= RESTART_MAX:
        print(f"⛔ گادِ صوتی: {RESTART_MAX} بار در یک ساعت افتاد — دیگر تلاش نمی‌کنم "
              f"(با دیپلوی/ری‌استارت دوباره امتحان می‌شود).")
        return
    _state["restarts"].append(now)
    await asyncio.sleep(RESTART_DELAY)
    if _state["quitting"] or _state["proc"] is not None:
        return
    print("🔄 گادِ صوتی: بالا آوردنِ دوبارهٔ کارگر…")
    await _spawn()


async def _pinger():
    """هر چند ثانیه یک پینگ؛ اگر کارگر جواب نداد، هنگ کرده → کشته می‌شود (و _pump دوباره می‌سازد)."""
    while not _state["quitting"]:
        await asyncio.sleep(PING_EVERY)
        proc = _state["proc"]
        if proc is None or not _state["ready"]:
            continue
        _state["ping_id"] += 1
        pid = _state["ping_id"]
        _send({"cmd": "ping", "id": pid})
        await asyncio.sleep(PING_TIMEOUT)
        if _state["proc"] is proc and _state["pong_id"] < pid:
            print("⚠️ گادِ صوتی: کارگر به پینگ جواب نداد — هنگ کرده؛ کشته می‌شود.")
            try:
                proc.kill()
            except Exception:
                pass


def _send(obj: dict):
    proc = _state["proc"]
    if proc is None or proc.stdin is None:
        return
    try:
        proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        asyncio.get_running_loop().create_task(_drain(proc))
    except Exception as e:
        print("⚠️ گادِ صوتی: ارسال به کارگر:", repr(e))


async def _drain(proc):
    try:
        await proc.stdin.drain()
    except Exception:
        pass


# ─── API برای بات ───────────────────────────────────────────
def say(chat_id: int, key: str):
    """🔊 پخشِ یک جمله در وویس‌چتِ این گروه — غیرِمسدودکننده، بی‌خطا.
    اگر کارگر آماده نباشد یا وویس‌چت باز نباشد، بی‌صدا رد می‌شود."""
    if not _state["ready"]:
        return
    _send({"cmd": "say", "chat": int(chat_id), "key": str(key)})


async def leave(chat_id: int):
    """🚪 خروج از وویس‌چتِ این گروه (پایانِ بازی). بی‌خطا."""
    if not _state["ready"]:
        return
    _send({"cmd": "leave", "chat": int(chat_id)})


# ─── 🎥 ضبطِ بازی ───────────────────────────────────────────
# اکانتِ صوتی خودش ضبطِ تلگرام را باز و بسته می‌کند؛ چون بازکنندهٔ ضبط است،
# فایلِ همهٔ پارت‌ها (حتی وقتی نتِ گاد مایک را می‌بندد) به Saved Messages
# خودش می‌رسد و بعد از اتمامِ بازی یک‌جا زیرِ لیستِ چنلِ آرشیو فرستاده می‌شود.
_rec_games: dict[int, dict] = {}     # chat → {"title","since"} بازی‌های زیرِ نظر
_rec_finishes: list[dict] = []       # پایان‌هایی که موقعِ نبودنِ کارگر صف شدند


def record_watch(chat_id: int, title: str, since=None):
    """🎥 شروعِ بازی: برو رویِ مایک، ضبط را روشن کن و تا «اتمام بازی» هوایِ
    باز/بسته‌شدنِ مایک را داشته باش (هر بازشدن = پارتِ تازه با «ادامه …»)."""
    if not enabled():
        return
    chat_id = int(chat_id)
    # پخشِ مجددِ نقش‌ها وسطِ همان بازی → قدیمی‌ترین لحظهٔ شروع معتبر می‌ماند
    cand = [float(since)] if since else []
    prev = _rec_games.get(chat_id)
    if prev:
        cand.append(prev["since"])
    st = {"title": str(title), "since": min(cand) if cand else time.time()}
    _rec_games[chat_id] = st
    if _state["ready"]:
        _send({"cmd": "rec_watch", "chat": chat_id, **st})


def record_finish(chat_id: int, channel):
    """🏁 اتمامِ بازی: ضبط بسته، از مایک پایین، همهٔ پارت‌ها → چنل (زیرِ لیست)."""
    st = _rec_games.pop(int(chat_id), None)
    if st is None:
        return
    cmd = {"cmd": "rec_finish", "chat": int(chat_id), "channel": channel, **st}
    if _state["ready"]:
        _send(cmd)
    else:
        _rec_finishes.append(cmd)    # کارگر که برگشت، فرستاده می‌شود


def record_abort(chat_id: int):
    """🚮 ریستِ وسطِ بازی: ضبط بسته و از مایک پایین — بدونِ ارسال به چنل."""
    if _rec_games.pop(int(chat_id), None) is not None and _state["ready"]:
        _send({"cmd": "rec_abort", "chat": int(chat_id)})


async def stop():
    """خاموش‌کردنِ کارگر (هنگامِ پایانِ بات)."""
    _state["quitting"] = True
    proc = _state["proc"]
    if proc is None:
        return
    _send({"cmd": "quit"})
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
