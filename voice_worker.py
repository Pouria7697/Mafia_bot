"""
🎙 کارگرِ گادِ صوتی — پروسهٔ جدا از باتِ اصلی.

چرا جدا؟ موتورِ وویس (ntgcalls) کدِ نیتیو است؛ اگر بترکد کلِ پروسه را می‌برد.
با جدا بودن، هر اتفاقی اینجا بیفتد — کرش، قطعیِ اکانت، پیامِ غریبه به اکانت،
هنگ — باتِ اصلی چیزی حس نمی‌کند و فقط «بی‌صدا» می‌شود؛ باتِ اصلی هم
این پروسه را خودکار دوباره بالا می‌آورد.

این پروسه:
- هیچ هندلری برای پیام ندارد → هر پیامی به اکانت برسد، نادیده گرفته می‌شود.
- فقط از stdin دستور می‌گیرد (JSON در هر خط) و در stdout گزارش می‌دهد.
- اگر stdin بسته شود (باتِ اصلی رفت)، از همهٔ وویس‌چت‌ها بیرون می‌آید و تمام می‌کند.

پروتکل (هر خط یک JSON):
  ← {"cmd":"say","chat":-100…,"key":"time_up"}
  ← {"cmd":"leave","chat":-100…}
  ← {"cmd":"ping","id":7}
  ← {"cmd":"quit"}
  → @@READY {"name":…}   |  @@FAILED <دلیل>  |  @@PONG 7  |  هر خطِ دیگر = لاگ
"""
import os
import sys
import json
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice_god as VG   # فقط برای مسیرِ فایل‌ها و تنظیمات — هیچ پروسه‌ای نمی‌سازد

_calls = None
_client = None
_locks: dict[int, asyncio.Lock] = {}
_warned_no_call: set[int] = set()
_volume_set: set[int] = set()
_joined: set[int] = set()


def out(line: str):
    """خطِ خروجی برای باتِ اصلی — همیشه flush، چون stdout یک pipe است."""
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except Exception:
        pass


async def _start() -> bool:
    global _calls, _client
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from pytgcalls import PyTgCalls
    except Exception as e:
        out(f"@@FAILED کتابخانه نصب نیست: {e}")
        return False
    try:
        client = TelegramClient(StringSession(VG.TG_SESSION), int(VG.TG_API_ID), VG.TG_API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            out("@@FAILED TG_SESSION معتبر نیست — با make_session.py دوباره بساز.")
            await client.disconnect()
            return False
        me = await client.get_me()
        try:
            await client.get_dialogs(limit=200)     # 📇 کشِ چت‌ها برای play(chat_id)
        except Exception as e:
            out(f"⚠️ get_dialogs: {e}")
        calls = PyTgCalls(client)
        await calls.start()
        _client, _calls = client, calls
        out("@@READY " + json.dumps({"name": me.first_name or "", "username": me.username or ""},
                                    ensure_ascii=False))
        return True
    except Exception as e:
        out(f"@@FAILED {type(e).__name__}: {e}")
        return False


async def _say(chat_id: int, key: str):
    path = VG.phrase_path(key)
    if not path:
        out(f"⚠️ فایلِ «{key}» نیست.")
        return
    lock = _locks.setdefault(chat_id, asyncio.Lock())
    async with lock:                     # جمله‌ها پشتِ هم، نه روی هم
        try:
            from pytgcalls.types.raw import Stream, AudioStream, AudioParameters
            from ntgcalls import MediaSource
            stream = Stream(microphone=AudioStream(
                MediaSource.FILE, path, AudioParameters(48000, 1)))
            await _calls.play(chat_id, stream)
            _joined.add(chat_id)
            _warned_no_call.discard(chat_id)
            # 🔊 بلندیِ اکانت در این وویس‌چت — یک‌بار بعد از اولین پخشِ موفق
            if chat_id not in _volume_set and VG.TG_VOLUME != 100:
                _volume_set.add(chat_id)
                try:
                    await _calls.change_volume_call(chat_id, VG.TG_VOLUME)
                except Exception as e:
                    out(f"⚠️ تنظیمِ بلندی در {chat_id}: {type(e).__name__}: {e}")
            await asyncio.sleep(os.path.getsize(path) / VG.PCM_BYTES_PER_SEC + 0.4)
        except Exception as e:
            name = type(e).__name__
            if name == "NoActiveGroupCall":
                if chat_id not in _warned_no_call:
                    _warned_no_call.add(chat_id)
                    out(f"وویس‌چتِ {chat_id} باز نیست — «{key}» پخش نشد.")
            else:
                out(f"⚠️ ({key} در {chat_id}): {name}: {e}")


async def _leave(chat_id: int):
    try:
        await _calls.leave_call(chat_id)
    except Exception:
        pass
    _joined.discard(chat_id)
    _warned_no_call.discard(chat_id)
    _volume_set.discard(chat_id)


async def _shutdown():
    for cid in list(_joined):
        await _leave(cid)
    try:
        if _client is not None:
            await _client.disconnect()
    except Exception:
        pass


async def main():
    if not VG.enabled():
        out("@@FAILED TG_API_ID/TG_API_HASH/TG_SESSION تنظیم نشده.")
        return
    if not await _start():
        return
    # 📥 دستورها از stdin — در ترد، که روی همهٔ سیستم‌عامل‌ها کار کند
    while True:
        line = await asyncio.to_thread(sys.stdin.buffer.readline)
        if not line:
            out("stdin بسته شد — باتِ اصلی رفت؛ خروج.")
            break
        try:
            cmd = json.loads(line.decode("utf-8", "ignore").strip() or "{}")
        except Exception:
            continue
        c = cmd.get("cmd")
        try:
            if c == "say":
                asyncio.create_task(_say(int(cmd["chat"]), str(cmd["key"])))
            elif c == "leave":
                asyncio.create_task(_leave(int(cmd["chat"])))
            elif c == "ping":
                out(f"@@PONG {cmd.get('id', 0)}")
            elif c == "quit":
                break
        except Exception as e:
            out(f"⚠️ دستورِ {c}: {e}")
    await _shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
