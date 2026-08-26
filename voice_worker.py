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
  ← {"cmd":"rec_watch","chat":-100…,"title":"ایونت ۱۲","since":ts}   شروعِ بازی
  ← {"cmd":"rec_finish","chat":-100…,"channel":…,"title":…,"since":ts} اتمامِ بازی
  ← {"cmd":"rec_abort","chat":-100…}                                  ریستِ وسطِ بازی
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


# ─── 🎥 ضبطِ بازی ───────────────────────────────────────────
# ضبطِ خودِ تلگرام (نه ضبطِ محلی): چون این اکانت ضبط را «باز» می‌کند، فایلِ هر
# پارت — حتی وقتی نتِ گاد مایک را وسطِ بازی می‌بندد — به Saved Messages همین
# اکانت می‌رسد؛ بعد از اتمامِ بازی همه یک‌جا به چنلِ آرشیو فرستاده می‌شوند.
_rec: dict[int, dict] = {}       # chat → {"title","since","parts","task"}
_rec_raw_on = False


def _rec_hook_updates():
    """گوش‌دادن به بازشدنِ وویس‌چت — فقط برای گروه‌های زیرِ نظر، لحظه‌ای."""
    global _rec_raw_on
    if _rec_raw_on or _client is None:
        return
    from telethon import events, utils
    from telethon.tl import types

    async def _on_call_upd(u):
        try:
            if u.peer is None or not isinstance(u.call, types.GroupCall):
                return
            cid = utils.get_peer_id(u.peer)
            if cid in _rec:
                await asyncio.sleep(2)          # تا وضعِ کال سرِ جایش بنشیند
                await _rec_ensure(cid)
        except Exception as e:
            out(f"⚠️ آپدیتِ وویس‌چت: {type(e).__name__}: {e}")

    _client.add_event_handler(_on_call_upd, events.Raw(types.UpdateGroupCall))
    _rec_raw_on = True


async def _rec_call_info(chat_id: int):
    """(InputGroupCall, GroupCall) وویس‌چتِ فعالِ گروه — یا (None, None) اگر مایک بسته است."""
    from telethon.tl import types, functions
    ent = await _client.get_entity(chat_id)
    if isinstance(ent, types.Channel):
        full = await _client(functions.channels.GetFullChannelRequest(ent))
    else:
        full = await _client(functions.messages.GetFullChatRequest(ent.id))
    call = getattr(full.full_chat, "call", None)
    if call is None:
        return None, None
    try:
        gc = (await _client(functions.phone.GetGroupCallRequest(call=call, limit=1))).call
        if not isinstance(gc, types.GroupCall):
            return None, None                   # کال تمام‌شده (Discarded)
    except Exception:
        return None, None
    return call, gc


async def _rec_saved_parts(since: float):
    """فایل‌های ضبط که از لحظهٔ شروعِ بازی به Saved Messages رسیده‌اند — قدیمی → جدید."""
    parts = []
    async for m in _client.iter_messages("me", limit=60):
        if m.date.timestamp() < float(since) - 60:
            break
        if getattr(m, "audio", None) or getattr(m, "voice", None):
            parts.append(m)
    return list(reversed(parts))


async def _rec_ensure(chat_id: int):
    """اگر مایک باز است و ضبطی در جریان نیست → برو رویِ مایک و ضبط را روشن کن."""
    st = _rec.get(chat_id)
    if st is None:
        return
    lock = _locks.setdefault(chat_id, asyncio.Lock())
    async with lock:      # هم وسطِ پخشِ جمله نمی‌پریم، هم دو محرکِ هم‌زمان دوبار روشن نمی‌کنند
        if _rec.get(chat_id) is not st:
            return
        call, gc = await _rec_call_info(chat_id)
        if call is None:
            return                              # مایک بسته — منتظرِ بازشدن
        if getattr(gc, "record_start_date", None):
            return                              # همین حالا در حالِ ضبط است
        try:
            if chat_id not in await _calls.calls:
                await _calls.play(chat_id, None)    # 🎙 رویِ مایک، بی‌صدا
                _joined.add(chat_id)
        except Exception as e:
            out(f"⚠️ ضبط: رفتن رویِ مایکِ {chat_id}: {type(e).__name__}: {e}")
        # 🔴 پارتِ n+1 → «ادامه » × n + اسمِ ایونت (شمارشِ پارت‌ها از فایل‌های رسیده هم چک می‌شود)
        from telethon.tl.functions.phone import ToggleGroupCallRecordRequest
        try:
            n = max(st["parts"], len(await _rec_saved_parts(st["since"])))
            title = ("ادامه " * n) + st["title"]
            await _client(ToggleGroupCallRecordRequest(call=call, start=True, title=title))
            st["parts"] = n + 1
            out(f"🔴 ضبط روشن شد: «{title}»")
        except Exception as e:
            out(f"⚠️ ضبط روشن نشد ({chat_id}): {type(e).__name__}: {e}")


async def _rec_stop(chat_id: int):
    """⏹ اگر ضبطی در جریان است، ببند. برمی‌گرداند: بسته شد؟"""
    from telethon.tl.functions.phone import ToggleGroupCallRecordRequest
    try:
        call, gc = await _rec_call_info(chat_id)
        if call is not None and getattr(gc, "record_start_date", None):
            await _client(ToggleGroupCallRecordRequest(call=call, start=False))
            return True
    except Exception as e:
        out(f"⚠️ بستنِ ضبط ({chat_id}): {type(e).__name__}: {e}")
    return False


async def _rec_loop(chat_id: int):
    """پشتیبانِ آپدیتِ لحظه‌ای: هر ۲۵ ثانیه چک — مایک باز شده و ضبط نمی‌شود؟ روشن کن."""
    while chat_id in _rec:
        try:
            await _rec_ensure(chat_id)
        except Exception as e:
            out(f"⚠️ ضبط‌بان ({chat_id}): {type(e).__name__}: {e}")
        await asyncio.sleep(25)


async def _rec_watch(chat_id: int, title: str, since: float):
    st = _rec.get(chat_id)
    if st is None:
        st = _rec[chat_id] = {"title": title, "since": float(since), "parts": 0, "task": None}
    else:                                       # پخشِ مجدد وسطِ بازی / تکرارِ فرمان
        st["title"] = title
        st["since"] = min(st["since"], float(since))
    _rec_hook_updates()
    if st["task"] is None or st["task"].done():
        st["task"] = asyncio.create_task(_rec_loop(chat_id))


async def _rec_finish(chat_id: int, channel, title: str, since: float):
    """🏁 اتمامِ بازی: ضبط بسته، از مایک پایین، صبر تا فایلِ آخر برسد، همه → چنل."""
    st = _rec.pop(chat_id, None)
    if st and st.get("task"):
        st["task"].cancel()
    parts_before = []
    try:
        parts_before = await _rec_saved_parts(since)
    except Exception as e:
        out(f"⚠️ خواندنِ Saved Messages: {type(e).__name__}: {e}")
    was_rec = await _rec_stop(chat_id)
    await _leave(chat_id)                       # 🚪 از مایک پایین
    # ⏳ فایلِ پارتِ آخر چند ثانیه بعد از بستنِ ضبط ساخته می‌شود
    parts = parts_before
    deadline = asyncio.get_event_loop().time() + (180 if was_rec else 40)
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(6)
        try:
            parts = await _rec_saved_parts(since)
        except Exception:
            continue
        if len(parts) > len(parts_before):
            await asyncio.sleep(6)              # شاید فایلِ دیگری هم در راه باشد
            try:
                parts = await _rec_saved_parts(since)
            except Exception:
                pass
            break
    if not parts:
        out(f"🎙 ضبط: برای «{title}» هیچ فایلی نرسید (مایک باز نشده بود؟).")
        return
    if not channel:
        out("⚠️ چنلِ آرشیو تنظیم نیست — فایل‌های ضبط در Saved Messages اکانت ماندند.")
        return
    try:
        if isinstance(channel, str) and channel.strip().lstrip("-").isdigit():
            channel = int(channel.strip())
        ent = await _client.get_entity(channel)
        for i in range(0, len(parts), 10):      # آلبوم‌های حداکثر ۱۰تایی
            chunk = parts[i:i + 10]
            await _client.send_file(ent, [m.media for m in chunk],
                                    caption=(f"🎙 {title}" if i == 0 else None))
        out(f"📤 {len(parts)} پارتِ ضبطِ «{title}» به چنل رفت.")
    except Exception as e:
        out(f"⚠️ ارسالِ ضبط‌ها به چنل: {type(e).__name__}: {e} — فایل‌ها در Saved Messages هستند.")


async def _rec_abort(chat_id: int):
    """🚮 ریستِ وسطِ بازی: ضبط بسته و از مایک پایین — چیزی به چنل نمی‌رود."""
    st = _rec.pop(chat_id, None)
    if st and st.get("task"):
        st["task"].cancel()
    await _rec_stop(chat_id)
    await _leave(chat_id)


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
            elif c == "rec_watch":
                asyncio.create_task(_rec_watch(int(cmd["chat"]), str(cmd["title"]),
                                               float(cmd["since"])))
            elif c == "rec_finish":
                asyncio.create_task(_rec_finish(int(cmd["chat"]), cmd.get("channel"),
                                                str(cmd["title"]), float(cmd["since"])))
            elif c == "rec_abort":
                asyncio.create_task(_rec_abort(int(cmd["chat"])))
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
