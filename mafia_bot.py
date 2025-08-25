from __future__ import annotations
from dataclasses import dataclass
import pickle, os, random, asyncio
import telegram.error
import jdatetime
import requests
import json, httpx
import sys
import re
import asyncio
import regex
import subprocess
from html import escape
from telegram.ext import filters
from telegram.error import BadRequest
group_filter = filters.ChatType.GROUPS
from datetime import datetime, timezone, timedelta  
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, Message
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from collections import defaultdict
# --- CALLBACK DATA CONSTANTS ---
BTN_PLAYER  = "player_name"    
BTN_DELETE  = "delete_seat"      
BTN_START   = "start_game"      
BTN_CALL = "call_players"   
BTN_REROLL = "reroll_roles"  

GH_TOKEN = os.environ.get("GH_TOKEN")
GIST_ID = os.environ.get("GIST_ID")
GIST_FILENAME = "gistfile1.txt"
GIST_API_URL = f"https://api.github.com/gists/{GIST_ID}"


USERNAMES_FILENAME = "usernames.json" 
TOKEN = os.environ.get("TOKEN")
PERSIST_FILE = "mafia_data.pkl"
SEAT_EMOJI = "👤"; LOCKED_EMOJI = "🔒"; GOD_EMOJI = "👳🏻‍♂️"; START_EMOJI = "🚀"

def load_active_groups() -> set[int]:
    try:
        if not GH_TOKEN or not GIST_ID:
            print("⚠️ GH_TOKEN/GIST_ID not set; load_active_groups -> empty set")
            return set()
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            print("❌ load_active_groups failed:", r.status_code, r.text)
            return set()
        data = r.json()
        content = data["files"].get("active_groups.json", {}).get("content", "[]")
        arr = json.loads(content) if content else []
        return set(int(x) for x in arr)
    except Exception as e:
        print("❌ load_active_groups error:", e)
        return set()

def save_active_groups(active_groups: set[int]) -> bool:
    try:
        if not GH_TOKEN or not GIST_ID:
            print("⚠️ GH_TOKEN/GIST_ID not set; save_active_groups skipped")
            return False
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}
        payload = {
            "files": {
                "active_groups.json": {
                    "content": json.dumps(sorted(list(active_groups)), ensure_ascii=False, indent=2)
                }
            }
        }
        r = requests.patch(url, headers=headers, json=payload, timeout=10)
        if r.status_code not in (200, 201):
            print("❌ save_active_groups failed:", r.status_code, r.text)
            return False
        return True
    except Exception as e:
        print("❌ save_active_groups error:", e)
        return False

@dataclass
class Scenario:
    name: str
    roles: dict[str, int]

@dataclass
class GameState:
    god_id: int | None = None
    god_name: str | None = None
    seats: dict[int, tuple[int, str]] | None = None
    event_time: str | None = None
    max_seats: int = 0
    scenario: Scenario | None = None
    phase: str = "idle"

    waiting_name: dict[int, int] | None = None
    waiting_name_proxy: dict[int, int] | None = None
    waiting_god: set[int] | None = None
    awaiting_scenario: bool = False

    assigned_roles: dict[int, str] | None = None
    striked: set[int] | None = None
    voting: dict[int, list[int]] | None = None
    current_vote_target: int | None = None
    vote_type: str | None = None
    vote_candidates: list[int] | None = None
    defense_seats: list[int] | None = None
    last_seating_msg_id: int | None = None
    last_roles_msg_id: int | None = None
    last_roles_scenario_name: str | None = None
    winner_side: str | None = None
    awaiting_winner: bool = False
    last_vote_msg_id: int | None = None
    defense_prompt_msg_id: int | None = None
    strike_control_msg_id = None
    strike_list_msg_id = None
    awaiting_players: set[int] | None = None
    awaiting_name_input: dict[int, int] = None
    last_name_prompt_msg_id: dict[int, int] = None
    from_startgame: bool = False
    awaiting_shuffle_decision: bool = False
    shuffle_prompt_msg_id: int | None = None
    purchased_seat: int | None = None
    awaiting_purchase_number: bool = False
    pending_strikes: set[int] | None = None 
    status_counts: dict[str, int] = None
    status_mode: bool = False 
    ui_hint: str | None = None
    warnings: dict[int, int] | None = None
    warning_mode: bool = False
    pending_warnings: dict[int, int] | None = None


    def __post_init__(self):
        self.seats = self.seats or {}
        self.waiting_name = self.waiting_name or {}
        self.waiting_name_proxy = self.waiting_name_proxy or {}
        self.waiting_god = self.waiting_god or set()
        self.assigned_roles = self.assigned_roles or {}
        self.striked = self.striked or set()
        self.strike_backup_seats = {}
        self.strike_control_msg_id = None
        self.voting = self.voting or {}
        self.vote_candidates = self.vote_candidates or []
        self.defense_seats = self.defense_seats or []
        self.awaiting_players = self.awaiting_players or set()
        self.defense_prompt_msg_id = self.defense_prompt_msg_id or None
        self.awaiting_seat = {}
        self.pending_name_msgs = {}
        self.awaiting_name_input = self.awaiting_name_input or {}
        self.last_name_prompt_msg_id = self.last_name_prompt_msg_id or {}
        self.user_names = {}
        self.selected_defense = []
        self.vote_messages: list = []
        self.last_roles_msg_id = None
        self.awaiting_shuffle_decision = False
        self.shuffle_prompt_msg_id = None
        self.awaiting_purchase_number = False
        self.pending_strikes = self.pending_strikes or set()
        self.status_counts = self.status_counts or {"citizen": 0, "mafia": 0}
        self.status_mode = False
        self.preview_uid_to_role = getattr(self, "preview_uid_to_role", None)
        self.shuffle_repeats = getattr(self, "shuffle_repeats", None) 
        self.chaos_mode = False        
        self.chaos_selected = set()       
        self.purchased_seat = None    
        self.pending_delete = getattr(self, "pending_delete", None) or set()  
        self.warnings = self.warnings or {}
        self.pending_warnings = self.pending_warnings or {}
        self.warning_mode = getattr(self, "warning_mode", False)

class Store:
    def __init__(self, path=PERSIST_FILE):
        self.path = path
        self.scenarios: list[Scenario] = []
        self.games: dict[int, GameState] = {}
        self.group_stats: dict[int, dict] = {}
        self.active_groups: set[int] = set()
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path, "rb") as f:
                obj = pickle.load(f)
                self.scenarios = obj.get("scenarios", [])
                self.games = obj.get("games", {})
                self.group_stats = obj.get("group_stats", {})
                # ⬇️ منبع حقیقت: Gist
                ag = load_active_groups()
                self.active_groups = ag if ag else set(obj.get("active_groups", []))
                for g in self.games.values():
                    if isinstance(g, GameState):
                        g.__post_init__()
        else:
          
            self.scenarios = []
            self.games = {}
            self.group_stats = {}
            self.active_groups = load_active_groups()  
            self.save()  # بعداً روی دیسک ذخیره کن

    def save(self):
        with open(self.path, "wb") as f:
            pickle.dump({
                "scenarios": self.scenarios,
                "games": self.games,
                "group_stats": self.group_stats,
                "active_groups": list(self.active_groups)
            }, f)


def save_scenarios_to_gist(scenarios):
    if not GH_TOKEN or not GIST_ID:
        return

    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    data = {
        "files": {
            GIST_FILENAME: {
                "content": json.dumps({"scenarios": [s.__dict__ for s in scenarios]}, ensure_ascii=False, indent=2)
            }
        }
    }

    try:
        httpx.patch(url, headers=headers, json=data)
    except Exception as e:
        print("❌ save_scenarios error:", e)

def load_scenarios_from_gist():
    if not GH_TOKEN or not GIST_ID:
        return []

    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    try:
        response = httpx.get(url, headers=headers)
        if response.status_code == 200:
            gist_data = response.json()
            content = gist_data["files"][GIST_FILENAME]["content"]
            data = json.loads(content)
            return [Scenario(name=s["name"], roles=s["roles"]) for s in data.get("scenarios", [])]
        else:
            print("❌ Gist fetch failed:", response.status_code)
            return []
    except Exception as e:
        print("❌ load_scenarios error:", e)
        return []
def load_usernames_from_gist():
    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
        }

        response = httpx.get(url, headers=headers)
        if response.status_code == 200:
            gist_data = response.json()
            content = gist_data["files"].get(USERNAMES_FILENAME, {}).get("content", "{}")
            data = json.loads(content) or {}
            return {int(k): v for k, v in data.items()}  # 👈 کلیدها رو تبدیل کن به عدد
        else:
            print("❌ user_names gist fetch failed:", response.status_code)
            return {}
    except Exception as e:
        print("❌ load_usernames error:", e)
        return {}

def save_usernames_to_gist(usernames: dict[int, str]):
    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        data = {
            "files": {
                USERNAMES_FILENAME: {
                    "content": json.dumps(usernames, ensure_ascii=False, indent=2)
                }
            }
        }
        httpx.patch(url, headers=headers, json=data)
    except Exception as e:
        print("❌ save_usernames error:", e)


store = Store()
store.scenarios = load_scenarios_from_gist()

# لود کردن نام‌های کاربران از Gist برای تمام گیم‌ها
usernames = load_usernames_from_gist()
for g in store.games.values():
    g.user_names = usernames



def gs(chat_id):
    g = store.games.setdefault(chat_id, GameState())
    if not g.user_names:
        g.user_names = load_usernames_from_gist()  # ← بارگذاری اسامی از Gist
    return g

def load_event_numbers():
    url = f"https://api.github.com/gists/{GIST_ID}"
    res = requests.get(url, headers={"Authorization": f"token {GH_TOKEN}"})
    data = res.json()
    content = data["files"]["event_numbers.json"]["content"]
    try:
        return json.loads(content)
    except:
        return {}

def save_event_numbers(event_numbers: dict) -> bool:
    try:
        if not GH_TOKEN or not GIST_ID:
            print("⚠️ GH_TOKEN/GIST_ID not set; save_event_numbers skipped")
            return False
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}
        payload = {
            "files": {
                "event_numbers.json": {
                    "content": json.dumps(event_numbers, ensure_ascii=False, indent=2)
                }
            }
        }
        res = requests.patch(url, headers=headers, json=payload, timeout=10)
        if res.status_code not in (200, 201):
            print("❌ save_event_numbers PATCH failed:", res.status_code, res.text)
            return False

        # ✅ کش را همزمان به‌روز کن
        global EVENT_NUMBERS_CACHE
        EVENT_NUMBERS_CACHE = event_numbers
        return True
    except Exception as e:
        print("❌ save_event_numbers error:", e)
        return False



def load_stickers():
    url = f"https://api.github.com/gists/{GIST_ID}"
    res = requests.get(url, headers={"Authorization": f"token {GH_TOKEN}"})
    data = res.json()
    content = data["files"]["stickers.json"]["content"]
    try:
        return json.loads(content)
    except:
        return {}

def save_stickers(stickers):
    url = f"https://api.github.com/gists/{GIST_ID}"
    files = {
        "stickers.json": {
            "content": json.dumps(stickers, ensure_ascii=False, indent=2)
        }
    }
    requests.patch(url, headers={"Authorization": f"token {GH_TOKEN}"}, json={"files": files})


def text_seating_keyboard(g: GameState) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("⏰ تغییر ساعت", callback_data="change_time")
        ],
        [
            InlineKeyboardButton("❌ حذف بازیکن", callback_data=BTN_DELETE),
            InlineKeyboardButton("🧹 پاکسازی ", callback_data="cleanup_below")
        ],
        [
            InlineKeyboardButton("↩️ لغو ثبت‌نام", callback_data="cancel_self"),
            InlineKeyboardButton("✏️ تغییر نام", callback_data="change_name")
        ]
    ]

    if g.god_id:
        # ردیف اول: صدا زدن + تغییر سناریو
        rows.append([
            InlineKeyboardButton("🔊 صدا زدن", callback_data=BTN_CALL),
            InlineKeyboardButton("🪄 تغییر سناریو", callback_data="change_scenario")
        ])

        # ردیف دوم: شروع بازی + رندوم نقش (فقط وقتی همه صندلیا پره)
        if len(g.seats) == g.max_seats:
            rows.append([
                InlineKeyboardButton("▶️ شروع بازی", callback_data="startgame"),
                InlineKeyboardButton("🎲 رندوم نقش", callback_data=BTN_REROLL)
            ])

    return InlineKeyboardMarkup(rows)



# ─────────────────────────────────────────────────────────────
#  دکمه‌های کنترل راوی در حین بازی
# ─────────────────────────────────────────────────────────────
def control_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ اخطار", callback_data="warn_mode")],
        [InlineKeyboardButton("✂️ خط‌زدن", callback_data="strike_out")],
        [InlineKeyboardButton("📊 استعلام وضعیت", callback_data="status_query")],
        [InlineKeyboardButton("🗳 رأی‌گیری اولیه", callback_data="init_vote")],
        [InlineKeyboardButton("🗳 رأی‌گیری نهایی", callback_data="final_vote")],
        [InlineKeyboardButton("🏁 اتمام بازی", callback_data="end_game")]
    ])

def warn_button_markup_plusminus(g: GameState) -> InlineKeyboardMarkup:
    # از dict بودن مطمئن شو
    pw = g.pending_warnings if isinstance(g.pending_warnings, dict) else {}
    w  = g.warnings          if isinstance(g.warnings, dict)          else {}

    rows = []
    # فقط زنده‌ها
    alive = [s for s in sorted(g.seats) if s not in g.striked]
    for s in alive:
        base = pw.get(s, w.get(s, 0))
        try:
            n = int(base)
        except Exception:
            n = 0
       
        n = max(0, n)
        icons = "❗️" * n if n > 0 else "(0)"
        label = f"{s} {icons}"

        rows.append([
            InlineKeyboardButton("➖", callback_data=f"warn_dec_{s}"),
            InlineKeyboardButton(label, callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"warn_inc_{s}"),
        ])

    rows.append([InlineKeyboardButton("✅ تأیید", callback_data="warn_confirm")])
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data="warn_back")])
    return InlineKeyboardMarkup(rows)




def kb_endgame_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏙 شهر", callback_data="winner_city")],
        [InlineKeyboardButton("😈 مافیا", callback_data="winner_mafia")],
        [InlineKeyboardButton("🏙 کلین‌شیت شهر", callback_data="clean_city")],
        [InlineKeyboardButton("😈 کلین‌شیت مافیا", callback_data="clean_mafia")],
        [InlineKeyboardButton("🏙 شهر (کی‌آس)", callback_data="winner_city_chaos")],
        [InlineKeyboardButton("😈 مافیا (کی‌آس)", callback_data="winner_mafia_chaos")],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data="back_endgame")]
    ])



def kb_purchase_yesno() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ بله", callback_data="purchased_yes")],
        [InlineKeyboardButton("❌ خیر", callback_data="purchased_no")],
        [InlineKeyboardButton("↩️ بازگشت", callback_data="back_to_winner_select")]
    ])


def kb_pick_single_seat(alive_seats: list[int], selected: int | None,
                        confirm_cb: str, back_cb: str, title: str = "انتخاب صندلی") -> InlineKeyboardMarkup:
    rows = []
    for s in alive_seats:
        label = f"{s} ✅" if selected == s else f"{s}"
        rows.append([InlineKeyboardButton(label, callback_data=f"pick_single_{s}")])
    rows.append([InlineKeyboardButton("✅ تأیید", callback_data=confirm_cb)])
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)


def kb_pick_multi_seats(alive_seats: list[int], selected: set[int],
                        max_count: int, confirm_cb: str, back_cb: str) -> InlineKeyboardMarkup:
    rows = []
    for s in alive_seats:
        label = f"{s} ✅" if s in selected else f"{s}"
        rows.append([InlineKeyboardButton(label, callback_data=f"toggle_multi_{s}")])
    rows.append([InlineKeyboardButton(f"✅ تأیید ({len(selected)}/{max_count})", callback_data=confirm_cb)])
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)

def delete_button_markup(g: GameState) -> InlineKeyboardMarkup:
    rows = []
    # فقط صندلی‌هایی که بازیکن دارند
    for seat in sorted(g.seats.keys()):
        label = f"{seat} ✅" if seat in g.pending_delete else f"{seat}"
        rows.append([InlineKeyboardButton(label, callback_data=f"delete_toggle_{seat}")])
    # کنترل‌ها
    rows.append([InlineKeyboardButton("✅ تأیید حذف", callback_data="delete_confirm")])
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data="delete_cancel")])
    return InlineKeyboardMarkup(rows)


# ─────── بالای فایل (یا کنار بقیهٔ ثوابت) ──────────────────
REG   = "register"   # نمایش دکمه‌های ثبت‌نامی
CTRL  = "controls"   # فقط دکمه‌های کنترلی

async def safe_q_answer(q, text=None, show_alert=False):
    try:
        await q.answer(text, show_alert=show_alert)
    except telegram.error.BadRequest:
        pass
    except Exception:
        pass
async def set_hint_and_kb(ctx, chat_id: int, g: GameState, hint: str | None, kb: InlineKeyboardMarkup, mode: str = CTRL):
    g.ui_hint = hint
    store.save()
    await publish_seating(ctx, chat_id, g, mode=mode, custom_kb=kb)

EVENT_NUMBERS_CACHE = None

def get_event_numbers():
    global EVENT_NUMBERS_CACHE
    if EVENT_NUMBERS_CACHE is None:
        EVENT_NUMBERS_CACHE = load_event_numbers() or {}
    return EVENT_NUMBERS_CACHE

# ─────── تابع اصلاح‌ شده ───────────────────────────────────
async def publish_seating(
    ctx,
    chat_id: int,
    g: GameState,
    mode: str = REG,
    custom_kb: InlineKeyboardMarkup | None = None,
):
    # اگر بازی هنوز با /newgame راه‌اندازی نشده
    if not g.max_seats or g.max_seats <= 0:
        await ctx.bot.send_message(chat_id, "برای شروع، ادمین باید /newgame <seats> بزند.")
        return

    today = jdatetime.date.today().strftime("%Y/%m/%d")
    emoji_numbers = [
        "⓿", "➊", "➋", "➌", "➍", "➎", "➏", "➐", "➑", "➒",
        "➓", "⓫", "⓬", "⓭", "⓮", "⓯", "⓰", "⓱", "⓲", "⓳", "⓴"
    ]

    # آیدی/لینک گروه
    group_id_or_link = f"🆔 {chat_id}"
    if ctx.bot.username and chat_id < 0:
        try:
            chat_obj = await ctx.bot.get_chat(chat_id)
            if getattr(chat_obj, "username", None):
                group_id_or_link = f"🔗 <a href='https://t.me/{chat_obj.username}'>{chat_obj.title}</a>"
            else:
                group_id_or_link = f"🔒 {chat_obj.title}"
        except:
            pass

    # بدنه متن
    lines = [
        f"{group_id_or_link}",
        "♚🎭 <b>رویداد مافیا</b>",
        f"♚📆 <b>تاریخ:</b> {today}",
        f"♚🕰 <b>زمان:</b> {g.event_time or '---'}",
        f"♚🎩 <b>راوی:</b> <a href='tg://user?id={g.god_id}'>{g.god_name or '❓'}</a>",
    ]

    # شماره رویداد (همیشه مقدار فعلی را از گیت به‌روز بخوان)
    
    event_num = int(get_event_numbers().get(str(chat_id), 1))
    lines.insert(1, f"♚🎯 <b>شماره رویداد:</b> {event_num}")

    # سناریو
    if g.scenario:
        lines.append(f"♚📜 <b>سناریو:</b> {g.scenario.name} | 👥 {sum(g.scenario.roles.values())} نفر")

    lines.append("\n\n♚📂 <b>بازیکنان:</b>\n")

    # لیست صندلی‌ها
    for i in range(1, g.max_seats + 1):
        emoji_num = emoji_numbers[i] if i < len(emoji_numbers) else str(i)

        if i in g.seats:
            uid, name = g.seats[i]
            safe_name = escape(name, quote=False)
            txt = f"<a href='tg://user?id={uid}'>{safe_name}</a>"

            
            wn = 0
            if isinstance(getattr(g, "warnings", None), dict):
                wn = g.warnings.get(i, 0)
            try:
                wn = int(wn)
            except Exception:
                wn = 0
            wn = max(0, wn)
            if wn > 0:
                txt += " " + ("❗️" * wn)

            # ☠️ خط‌خورده‌ها
            if i in g.striked:
                txt += " ❌☠️"

            line = f"♚{emoji_num}  {txt}"
        else:
            line = f"♚{emoji_num} ⬜ /{i}"

        lines.append(line)




    # گزارش کوتاه استعلام وضعیت (اختیاری)
    if g.status_counts.get("citizen", 0) > 0 or g.status_counts.get("mafia", 0) > 0:
        c = g.status_counts.get("citizen", 0)
        m = g.status_counts.get("mafia", 0)
        lines.append(f"\n🧾 <i>استعلام وضعیت: {c} شهروند و {m} مافیا</i>")

    # راهنمای مرحله (در همان پیام)
    if getattr(g, "ui_hint", None):
        lines.append("")
        lines.append(f"ℹ️ <i>{g.ui_hint}</i>")

    text = "\n".join(lines)

    # انتخاب کیبورد
    if custom_kb is not None:
        kb = custom_kb
    else:
        if mode == REG:
            kb = text_seating_keyboard(g)
        elif mode == "strike":
            kb = strike_button_markup(g)
        elif mode == "status":
            kb = status_button_markup(g)
        elif mode == "delete":
            kb = warn_button_markup_plusminus(g)
        elif mode == "warn":                         
            kb = warn_button_markup(g)
        else:
            kb = control_keyboard()

    # ارسال/ویرایش پیام لیست
    from telegram.error import BadRequest

    try:
        if g.last_seating_msg_id:
            try:
                # تلاش برای ادیت متن + کیبورد
                await ctx.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=g.last_seating_msg_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except BadRequest as e:
                s = str(e)
                if "message is not modified" in s:
                    # متن تغییری نکرده؛ شاید فقط کیبورد باید عوض شود
                    try:
                        await ctx.bot.edit_message_reply_markup(
                            chat_id=chat_id,
                            message_id=g.last_seating_msg_id,
                            reply_markup=kb
                        )
                    except BadRequest as e2:
                        # اگر این هم تغییری نداشت، کاری لازم نیست
                        if "message is not modified" in str(e2):
                            pass
                        else:
                            raise
                else:
                    # خطای دیگری بود → اجازه بده شاخه‌ی بیرونی پیام جدید بسازد
                    raise
        else:
            # هنوز پیامی نداریم → ارسال پیام جدید و پین
            msg = await ctx.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
            g.last_seating_msg_id = msg.message_id
            if chat_id < 0:
                try:
                    await ctx.bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
                except Exception:
                    pass
    except Exception:
        # اگر ادیت به هر دلیل ممکن نشد → پیام جدید بساز
        old_msg_id = g.last_seating_msg_id
        msg = await ctx.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
        g.last_seating_msg_id = msg.message_id

        # پین پیام جدید
        if chat_id < 0:
            try:
                await ctx.bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
            except Exception:
                pass
        # (اختیاری) حذف پیام قدیمی برای جلوگیری از دو لیست
        # if old_msg_id:
        #     try:
        #         await ctx.bot.delete_message(chat_id, old_msg_id)
        #     except Exception:
        #         pass

    # نمایش یک‌باره لیست نقش‌ها (وقتی سناریو عوض شود)
    if g.scenario and mode == REG:
        if getattr(g, "last_roles_scenario_name", None) != g.scenario.name:
            role_lines = ["📜 <b>لیست نقش‌های سناریو:</b>\n"]
            for role, count in g.scenario.roles.items():
                for _ in range(count):
                    role_lines.append(f"🔸 {role}")
            role_text = "\n".join(role_lines)

            try:
                if getattr(g, "last_roles_msg_id", None):
                    try:
                        await ctx.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=g.last_roles_msg_id,
                            text=role_text,
                            parse_mode="HTML",
                        )
                    except BadRequest as e:
                        if "message is not modified" in str(e):
                            # نیازی به پیام جدید نیست
                            pass
                        else:
                            raise
                else:
                    role_msg = await ctx.bot.send_message(chat_id, role_text, parse_mode="HTML")
                    g.last_roles_msg_id = role_msg.message_id
            except Exception:
                role_msg = await ctx.bot.send_message(chat_id, role_text, parse_mode="HTML")
                g.last_roles_msg_id = role_msg.message_id

            g.last_roles_scenario_name = g.scenario.name

    store.save()


# ─────────────────────────────────────────────────────────────
#  رأی‌گیری (همان نسخهٔ قبلی؛ فقط دست نزدیم)
# ─────────────────────────────────────────────────────────────
async def start_vote(ctx, chat_id: int, g: GameState, stage: str):
    g.vote_stage = stage
    g.tally = {}
    g.current_target = None
    g.collecting = False

    candidates = g.defense_seats if stage == "final" else list(g.seats.keys())

    if stage == "final":
        g.vote_candidates = [s for s in candidates if s not in g.striked]
    else:
        g.vote_candidates = sorted([s for s in candidates if s not in g.striked])
    btns = []
    for s in g.vote_candidates:
        name = g.seats[s][1]
        if hasattr(g, "voted_targets") and s in g.voted_targets:
            label = f"✅ {s}. {name}"
        else:
            label = f"{s}. {name}"
        btns.append([InlineKeyboardButton(label, callback_data=f"vote_{s}")])

    btns.append([InlineKeyboardButton("✅ پایان رأی‌گیری", callback_data="vote_done")])

    back_code = "back_vote_init" if stage == "initial_vote" else "back_vote_final"
    btns.append([InlineKeyboardButton("⬅️ بازگشت", callback_data=back_code)])

    title = "🗳 رأی‌گیری اولیه – انتخاب هدف:" \
            if stage == "initial_vote" else \
            "🗳 رأی‌گیری نهایی – انتخاب حذف:"

    msg = await ctx.bot.send_message(chat_id, title, reply_markup=InlineKeyboardMarkup(btns))
    g.last_vote_msg_id = msg.message_id  # 🧹 ذخیره پیام رأی‌گیری
    store.save()

async def update_vote_buttons(ctx, chat_id: int, g: GameState):
    btns = []
    for s in g.vote_candidates:
        name = g.seats[s][1]
        label = f"✅ {s}. {name}" if hasattr(g, "voted_targets") and s in g.voted_targets else f"{s}. {name}"
        btns.append([InlineKeyboardButton(label, callback_data=f"vote_{s}")])

    btns.append([InlineKeyboardButton("✅ پایان رأی‌گیری", callback_data="vote_done")])
    btns.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="back_vote_init")])

    try:
        await ctx.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=g.last_vote_msg_id,
            reply_markup=InlineKeyboardMarkup(btns)
        )
    except:
        pass


async def handle_vote(ctx, chat_id: int, g: GameState, target_seat: int):
    g.current_vote_target = target_seat

    await ctx.bot.send_message(
        chat_id,
        f"⏳ رأی‌گیری برای <b>{target_seat}. {g.seats[target_seat][1]}</b>",
        parse_mode="HTML"
    )

    await asyncio.sleep(5)

    await ctx.bot.send_message(
        chat_id,
        f"🛑 تمام",
        parse_mode="HTML"
    )

    # ✅ علامت‌گذاری اینکه این صندلی رأی‌گیری شده
    if not hasattr(g, "voted_targets"):
        g.voted_targets = set()
    g.voted_targets.add(target_seat)

    # 🔁 آپدیت دکمه‌ها
    await update_vote_buttons(ctx, chat_id, g)

    store.save()

import jdatetime


async def announce_winner(ctx, update, g: GameState):
    chat = update.effective_chat
    group_title = chat.title or "—"
    date_str = jdatetime.date.today().strftime("%Y/%m/%d")
    scenario_name = getattr(g.scenario, "name", "—")

    # ← فقط از کش
    nums = get_event_numbers()
    key = str(chat.id)
    event_num = int(nums.get(key, 1))  # نمایش عدد فعلی

    # لینک‌دار کردن گروه
    if chat.username:
        group_link = f"<a href='https://t.me/{chat.username}'>{group_title}</a>"
    else:
        group_link = group_title

    lines = [
        f"░⚜️🎮 گروه: {group_link}",
        f"░⚜️📅 تاریخ: {date_str}",
        f"░⚜️🎯 شماره رویداد:{event_num}",
        f"░💡🔱 راوی: <a href='tg://user?id={g.god_id}'>{g.god_name or '❓'}</a>",
        f"░⚜️📃 سناریو: {scenario_name}",
        "",
        "░⚜️💫 لیست بازیکنان ⬇️",
        "",
    ]

    for seat in sorted(g.seats):
        uid, name = g.seats[seat]
        role = g.assigned_roles.get(seat, "—")
        role_display = f"{role} / مافیاساده" if getattr(g, "purchased_seat", None) == seat else role
        chaos_mark = " 🟢" if getattr(g, "chaos_selected", set()) and seat in g.chaos_selected else ""
        lines.append(f"░⚜️▪️{seat}- <a href='tg://user?id={uid}'>{name}</a> ⇦ {role_display}{chaos_mark}")

    lines.append("")
    result_line = f"🏆 نتیجه بازی: برد {g.winner_side}"
    if getattr(g, "clean_win", False): result_line += " (کلین‌شیت)"
    if getattr(g, "chaos_mode", False): result_line += " (کی‌آس)"
    lines.append(result_line)

    # ✅ افزایش شماره ایونت (کش + Gist)
    nums[key] = event_num + 1
    ok = save_event_numbers(nums)
    if not ok:
        print(f"⚠️ save_event_numbers failed for chat {key}")

    g.phase = "ended"
    store.save()

    msg = await ctx.bot.send_message(chat.id, "\n".join(lines), parse_mode="HTML")
    try:
        await ctx.bot.pin_chat_message(chat_id=chat.id, message_id=msg.message_id)
    except Exception as e:
        print("⚠️ خطا در پین کردن پیام:", e)



def _apply_size_and_scenario(g: GameState, new_size: int, new_scenario: Scenario):
    # اگر کم می‌کنیم: صندلی‌های بالای ظرفیت جدید حذف شوند
    if new_size < g.max_seats:
        for seat in sorted(list(g.seats.keys())):
            if seat > new_size:
                g.seats.pop(seat, None)
        # خط‌خورده‌ها و دفاع و… هم تمیز شوند
        g.striked = {s for s in g.striked if s <= new_size}
        g.defense_seats = [s for s in g.defense_seats if s <= new_size]
    # اگر زیاد می‌کنیم: فقط ظرفیت بالا برود؛ نفرات قبلی سر جایشان
    g.max_seats = new_size
    g.scenario = new_scenario
    g.last_roles_scenario_name = None  # تا لیست نقش‌ها دوباره چاپ شود
    # هرچیزی که مربوط به نقش‌های قبلی بوده پاک؛ چون هنوز بازی شروع نشده
    g.assigned_roles = {}
    g.phase = "idle"
    g.awaiting_scenario = False
    # فلگ‌های مربوط به تغییر سناریو
    g.awaiting_scenario_change = False
    g.pending_size = None

def _scenario_sizes_available() -> list[int]:
    sizes = sorted({sum(s.roles.values()) for s in store.scenarios})
    return sizes

def kb_choose_sizes() -> InlineKeyboardMarkup:
    sizes = _scenario_sizes_available()
    rows, row = [], []
    for i, n in enumerate(sizes, 1):
        row.append(InlineKeyboardButton(str(n), callback_data=f"scsize_{n}"))
        if i % 4 == 0:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data="scchange_back")])
    return InlineKeyboardMarkup(rows)

def kb_choose_scenarios_for(size: int) -> InlineKeyboardMarkup:
    options = [s for s in store.scenarios if sum(s.roles.values()) == size]
    # هر سناریو یک دکمه
    rows = [[InlineKeyboardButton(s.name, callback_data=f"scpick_{size}_{i}")]
            for i, s in enumerate(options)]
    rows.append([InlineKeyboardButton("⬅️ انتخاب ظرفیت دیگر", callback_data="scchange_again")])
    return InlineKeyboardMarkup(rows)


# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
#  CALL-BACK ROUTER – نسخهٔ کامل با فاصله‌گذاری درست
# ─────────────────────────────────────────────────────────────
async def callback_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return
    q = update.callback_query
    await safe_q_answer(q)
    data = q.data
    chat = q.message.chat.id
    uid = q.from_user.id
    g = gs(chat)


    # ─── حذف بازیکن توسط گاد ────────────────────────────────────
    if data == BTN_DELETE:
        if uid != g.god_id:
            await ctx.bot.send_message(chat, "⚠️ فقط راوی می‌تواند حذف کند!")
            return
        g.pending_delete = set()
        store.save()
        await set_hint_and_kb(
            ctx, chat, g,
            "صندلی‌های دارای بازیکن را انتخاب کنید و در پایان «تأیید حذف» را بزنید.",
            delete_button_markup(g),
            mode="delete"
        )
        return
    if data.startswith("delete_toggle_") and uid == g.god_id:
        try:
            seat = int(data.split("_")[2])
        except:
            return
        # فقط اگر صندلی پُر است اجازهٔ انتخاب بده
        if seat in g.seats:
            if seat in g.pending_delete:
                g.pending_delete.remove(seat)
            else:
                g.pending_delete.add(seat)
            store.save()
        await publish_seating(ctx, chat, g, mode="delete")
        return

    if data == "delete_confirm" and uid == g.god_id:
        # حذف همهٔ صندلی‌های انتخاب‌شده
        for seat in sorted(list(g.pending_delete)):
            g.seats.pop(seat, None)
        g.pending_delete = set()
        g.ui_hint = None 
        store.save()
        await publish_seating(ctx, chat, g, mode=REG)
        return

    if data == "delete_cancel" and uid == g.god_id:
        g.pending_delete = set()
        store.save()
        await publish_seating(ctx, chat, g, mode=REG)
        return


    # ─── لغو ثبت‌نام توسط خودِ بازیکن ───────────────────────────
    if data == "cancel_self":
        for seat, (player_uid, _) in g.seats.items():
            if player_uid == uid:
                del g.seats[seat]
                store.save()
                await ctx.bot.send_message(chat, "❎ ثبت‌نام شما با موفقیت لغو شد.")
                await publish_seating(ctx, chat, g)
                break
        else:
            await ctx.bot.send_message(chat,"❗ شما در لیست نیستید.")
        return

    if data == "change_name":
        if uid not in [u for u, _ in g.seats.values()]:
            await ctx.bot.send_message(chat, "❗ شما هنوز ثبت‌نام نکرده‌اید.")
            return

        seat_no = [s for s in g.seats if g.seats[s][0] == uid][0]
        g.waiting_name[uid] = seat_no
        store.save()

        await ctx.bot.send_message(
            chat,
            f"✏️ این پیام را ریپلای کنید و نام جدید خود را برای صندلی {seat_no} به فارسی وارد کنید:"
        )
        return



    # ─── صدا زدن همه قبلِ شروع ──────────────────────────────────
    if data == BTN_CALL:
        if uid != g.god_id:
            await ctx.bot.send_message(chat,"⚠️ فقط راوی می‌تواند این دکمه را بزند!")
            return

        mentions = [
            f"<a href='tg://user?id={u_id}'>{name}</a>"
            for _, (u_id, name) in sorted(g.seats.items())
        ]
        text = (
            "🎙 سلاطین تشریف بیارید، "
            "بقیه رو علاف نکنید!\n" + " | ".join(mentions)
        )
        await ctx.bot.send_message(chat, text, parse_mode="HTML")
        return

    # ─── تغییر ساعت شروع ───────────────────────────────────────
    if data == "change_time":
        if uid != g.god_id:
            await ctx.bot.send_message(chat,"⚠️ فقط راوی می‌تواند زمان را عوض کند!")
            return
        g.vote_type = "awaiting_time"
        store.save()
        await ctx.bot.send_message(
            chat,
            "🕒 ساعت شروع را بنویس (مثال: 22:30):",
            reply_markup=ForceReply(selective=True)
        )
        return

    # ─── شروع بازی (انتخاب سناریو) ─────────────────────────────
    if data == "startgame":
        if g.god_id is None:
            await ctx.bot.send_message(chat,"⚠️ ابتدا باید راوی ثبت نام کند!")
            return

        if uid != g.god_id:
            await ctx.bot.send_message(chat,"⚠️ فقط راوی می‌تواند بازی را شروع کند!")
            return

        if not getattr(g, "preview_uid_to_role", None):
            await ctx.bot.send_message(
                chat,
                "🎲 قبل از شروع بازی، چند بار روی «رندوم نقش» بزنید تا نقش‌ها شافل شوند."
            )
            return

        if len(g.seats) != g.max_seats:
            await ctx.bot.send_message(chat, "⚠️ هنوز همهٔ صندلی‌ها پُر نشده!")
            return

     
        now = datetime.now(timezone.utc).timestamp()
        store.group_stats.setdefault(chat, {
            "waiting_list": [],
            "started": [],
            "ended": []
        })
        store.group_stats[chat]["started"].append(now)
        store.save()
        if g.scenario:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ بله", callback_data="shuffle_yes"),
                    InlineKeyboardButton("❌ خیر", callback_data="shuffle_no"),
                ]
            ])
            msg = await ctx.bot.send_message(
                chat,
                "❓ آیا مایل هستید صندلی‌ها رندوم بشن؟",
                reply_markup=keyboard
            )
            g.shuffle_prompt_msg_id = msg.message_id
            g.awaiting_shuffle_decision = True
            store.save()
            return

 
        g.awaiting_scenario = True
        g.from_startgame = False
        store.save()
        await show_scenario_selection(ctx, chat, g)
        return

    if data == "shuffle_yes":
        if uid != g.god_id:
            await ctx.bot.send_message(chat,"⚠️ فقط راوی می‌تواند بازی را شروع کند!")
            return

        if not g.awaiting_shuffle_decision:
            return

        g.awaiting_shuffle_decision = False
        g.from_startgame = False
        store.save()

        if hasattr(g, "shuffle_prompt_msg_id") and g.shuffle_prompt_msg_id:
            try:
                await ctx.bot.delete_message(chat, g.shuffle_prompt_msg_id)
            except:
                pass
            g.shuffle_prompt_msg_id = None

        repeats = getattr(g, "shuffle_repeats", None) or 1

        await shuffle_and_assign(
            ctx,
            chat,
            g,
            shuffle_seats=True,                         # صندلی‌ها جابجا بشن
            uid_to_role=g.preview_uid_to_role or None,  # اگر ریرول زده بود، همون نقش‌ها
            notify_players=True,                        # این بار برای پلیرها هم بفرست
            preview_mode=False,
            role_shuffle_repeats=repeats,               # ✨ تعداد دفعات شافل سیت مطابق ریرول
        )

        g.preview_uid_to_role = None
        g.shuffle_repeats = None
        store.save()
        return


    
    if data == "shuffle_no":
        if uid != g.god_id:
            await ctx.bot.send_message(chat,"⚠️ فقط راوی می‌تواند بازی را شروع کند!")
            return

        if not g.awaiting_shuffle_decision:
            return

        g.awaiting_shuffle_decision = False
        g.from_startgame = False
        store.save()

        if hasattr(g, "shuffle_prompt_msg_id") and g.shuffle_prompt_msg_id:
            try:
                await ctx.bot.delete_message(chat, g.shuffle_prompt_msg_id)
            except:
                pass
            g.shuffle_prompt_msg_id = None

        repeats = getattr(g, "shuffle_repeats", None) or 1

        await shuffle_and_assign(
            ctx,
            chat,
            g,
            shuffle_seats=False,                        # صندلی‌ها ثابت بمانند
            uid_to_role=g.preview_uid_to_role or None,  # اگر ریرول زده بود، همان نقش‌ها
            notify_players=True,                        # این بار برای پلیرها هم بفرست
            preview_mode=False,
            role_shuffle_repeats=repeats,               # (برای سازگاری پاس می‌دهیم)
        )

        g.preview_uid_to_role = None
        g.shuffle_repeats = None
        store.save()
        return

    # اخطار

    # ورود به حالت اخطار
    if data == "warn_mode":
        if uid != g.god_id:
            await ctx.bot.send_message(chat, "⚠️ فقط راوی می‌تواند اخطار بدهد!")
            return
        if not isinstance(g.warnings, dict):
            g.warnings = {}
        g.warning_mode = True
        g.pending_warnings = dict(g.warnings)  # ویرایش روی کپی
        store.save()
        await publish_seating(ctx, chat, g, mode="warn")
        return

    # افزایش اخطار
    if data.startswith("warn_inc_") and g.warning_mode and uid == g.god_id:
        try:
            seat = int(data.split("_")[2])
        except Exception:
            return
        if seat in g.seats and seat not in g.striked:
            if not isinstance(g.pending_warnings, dict):
                g.pending_warnings = {}
            cur = g.pending_warnings.get(seat, g.warnings.get(seat, 0))
            try:
                cur = int(cur)
            except Exception:
                cur = 0
            
            nxt = cur + 1
            g.pending_warnings[seat] = nxt
            store.save()
            await publish_seating(ctx, chat, g, mode="warn")
        return

    # کاهش اخطار
    if data.startswith("warn_dec_") and g.warning_mode and uid == g.god_id:
        try:
            seat = int(data.split("_")[2])
        except Exception:
            return
        if seat in g.seats and seat not in g.striked:
            if not isinstance(g.pending_warnings, dict):
                g.pending_warnings = {}
            cur = g.pending_warnings.get(seat, g.warnings.get(seat, 0))
            try:
                cur = int(cur)
            except Exception:
                cur = 0
            
            nxt = max(cur - 1, 0) 
            
            g.pending_warnings[seat] = nxt
            store.save()
            await publish_seating(ctx, chat, g, mode="warn")
        return

    # تأیید اخطارها
    if data == "warn_confirm" and g.warning_mode and uid == g.god_id:
        if not isinstance(g.pending_warnings, dict):
            g.pending_warnings = {}
        # فقط مقادیر >0 ذخیره شوند
        g.warnings = {
            int(k): int(v)
            for k, v in g.pending_warnings.items()
            if isinstance(v, int) and v > 0
        }
        g.warning_mode = False
        g.pending_warnings = {}
        store.save()
        await publish_seating(ctx, chat, g, mode=CTRL)
        return

    # بازگشت بدون اعمال
    if data == "warn_back" and g.warning_mode and uid == g.god_id:
        g.warning_mode = False
        g.pending_warnings = {}
        store.save()
        await publish_seating(ctx, chat, g, mode=CTRL)
        return

    # نادیده گرفتن برچسب
    if data == "noop":
        return

    # شروع «تغییر سناریو/ظرفیت»
    if data == "change_scenario":
        if g.god_id is None or uid != g.god_id:
            await safe_q_answer(q, "⚠️ فقط راوی می‌تواند سناریو را تغییر دهد!", show_alert=True)
            return
        g.awaiting_scenario_change = True
        g.pending_size = None
        store.save()
        await set_hint_and_kb(ctx, chat, g, "ابتدا ظرفیت را انتخاب کنید:", kb_choose_sizes(), mode=REG if g.phase=="idle" else CTRL)
        return

    # برگشت از انتخاب ظرفیت/سناریو
    if data == "scchange_back":
        g.awaiting_scenario_change = False
        g.pending_size = None
        g.ui_hint = None
        store.save()
        await publish_seating(ctx, chat, g, mode=REG if g.phase=="idle" else CTRL)
        return

    # تغییر ظرفیت → نمایش لیست سناریوهای همان ظرفیت
    if data.startswith("scsize_") and getattr(g, "awaiting_scenario_change", False):
        try:
            size = int(data.split("_")[1])
        except:
            return
        g.pending_size = size
        store.save()
        await set_hint_and_kb(ctx, chat, g,
                              f"سناریوی {size}نفره را انتخاب کنید:",
                              kb_choose_scenarios_for(size),
                              mode=REG if g.phase=="idle" else CTRL)
        return

    # انتخاب سناریو نهایی و اعمال تغییر
    if data.startswith("scpick_") and getattr(g, "awaiting_scenario_change", False):
        parts = data.split("_")
        if len(parts) != 3:
            return
        try:
            size = int(parts[1])
            idx = int(parts[2])
        except:
            return

        options = [s for s in store.scenarios if sum(s.roles.values()) == size]
        if not (0 <= idx < len(options)):
            await safe_q_answer(q, "سناریوی نامعتبر.", show_alert=True)
            return

        chosen = options[idx]

        # ⛔ اگر تغییری نیست، کاری نکن
        if g.scenario and g.scenario.name == chosen.name and g.max_seats == size:
            await safe_q_answer(q, "سناریو تغییری نکرد.", show_alert=False)
            return

        _apply_size_and_scenario(g, size, chosen)
        # خروج از مود تغییر سناریو و پاک کردن hint
        g.awaiting_scenario_change = False
        g.pending_size = None
        g.ui_hint = None
        store.save()

        # نمایش لیست با ظرفیت/سناریوی جدید
        await set_hint_and_kb(
            ctx, chat, g,
            None,
            text_seating_keyboard(g),
            mode=REG if g.phase == "idle" else CTRL
        )
        return

    # اگر وسط انتخاب سناریو بود و گفت «ظرفیت دیگر»
    if data == "scchange_again" and getattr(g, "awaiting_scenario_change", False):
        g.pending_size = None
        store.save()
        await set_hint_and_kb(ctx, chat, g, "ظرفیت را انتخاب کنید:", kb_choose_sizes(), mode=REG if g.phase=="idle" else CTRL)
        return

 

    # ─── پایان بازی و انتخاب برنده ──────────────────────────────
    if data == "end_game" and uid == g.god_id:
        now = datetime.now(timezone.utc).timestamp()
        store.group_stats.setdefault(chat, {"waiting_list": [], "started": [], "ended": []})
        store.group_stats[chat]["ended"].append(now)

        g.phase = "awaiting_winner"
        g.awaiting_winner = True
        g.temp_winner = None
        g.chaos_mode = False
        g.chaos_selected = set()
        store.save()

        await set_hint_and_kb(ctx, chat, g, "برنده را انتخاب کنید.", kb_endgame_root())
        return


    if data == "back_endgame" and uid == g.god_id:
        g.awaiting_winner = False
        g.phase = "playing"
        g.temp_winner = None
        g.chaos_mode = False
        g.chaos_selected = set()
        g.ui_hint = None  # 👈 برای اینکه متن راهنما روی لیست اصلی باقی نمونه
        store.save()
        await publish_seating(ctx, chat, g, mode=CTRL)
        return


    if data in {
        "winner_city", "winner_mafia", "clean_city", "clean_mafia",
        "winner_city_chaos", "winner_mafia_chaos"
    } and g.awaiting_winner:
        g.temp_winner = data
        g.chaos_mode = data.endswith("_chaos")
        store.save()

        # کلین‌شیت → مستقیماً تأیید نهایی
        if data in {"clean_city", "clean_mafia"}:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأیید", callback_data="confirm_winner")],
                [InlineKeyboardButton("↩️ بازگشت", callback_data="back_to_winner_select")],
            ])
            await set_hint_and_kb(ctx, chat, g, "نتیجه را بررسی کنید و «تأیید» را بزنید.", kb)
            return

        # شهر/مافیا (معمولی یا کی‌آس) → اول بپرس «خریداری؟»
        await set_hint_and_kb(ctx, chat, g, "آیا بازیکنی خریداری شده است؟", kb_purchase_yesno())
        return





    if data == "purchased_yes" and g.awaiting_winner:
        g.awaiting_purchase_number = True
        alive = [s for s in sorted(g.seats) if s not in g.striked]
        kb = kb_pick_single_seat(alive_seats=alive,
                                 selected=g.purchased_seat,
                                 confirm_cb="purchased_confirm",
                                 back_cb="back_to_winner_select")
        await set_hint_and_kb(ctx, chat, g,"شماره صندلی بازیکنِ خریداری‌شده را انتخاب کنید و سپس «تأیید» را بزنید.", kb)
        return

    if data == "purchased_no" and g.awaiting_winner:
        g.purchased_seat = None
        store.save()

        if not g.chaos_mode:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأیید", callback_data="confirm_winner")],
                [InlineKeyboardButton("↩️ بازگشت", callback_data="back_to_winner_select")],
            ])
            await set_hint_and_kb(
                ctx, chat, g,
                "🔒 برنده مشخص شد. اگر مطمئن هستید «تأیید» را بزنید.",
                kb
            )
            return

        # کی‌آس → انتخاب ۳ نفر زنده
        alive = [s for s in sorted(g.seats) if s not in g.striked]
        g.chaos_selected = set()
        kb = kb_pick_multi_seats(
            alive, g.chaos_selected, 3,
            confirm_cb="chaos_confirm",
            back_cb="back_to_winner_select"
        )
        await set_hint_and_kb(
            ctx, chat, g,
            "🌀 حالت کی‌آس: ۳ نفر از بازیکنان زنده را انتخاب کنید و سپس «تأیید» را بزنید.",
            kb
        )
        return


    if data.startswith("pick_single_") and g.awaiting_winner:
        try:
            s = int(data.split("_")[2])
        except:
            return

        if s in g.seats and s not in g.striked:
            g.purchased_seat = s
            store.save()

        alive = [x for x in sorted(g.seats) if x not in g.striked]
        kb = kb_pick_single_seat(
            alive, g.purchased_seat,
            confirm_cb="purchased_confirm",
            back_cb="back_to_winner_select"
        )
        await set_hint_and_kb(
            ctx, chat, g,
            "🛒 صندلی خریداری‌شده را انتخاب کنید و سپس «تأیید» را بزنید.",
            kb
        )
        return


    if data == "purchased_confirm" and g.awaiting_winner:
        if not g.chaos_mode:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأیید", callback_data="confirm_winner")],
                [InlineKeyboardButton("↩️ بازگشت", callback_data="back_to_winner_select")],
            ])
            await set_hint_and_kb(
                ctx, chat, g,
                "🔒 برنده و صندلی خریداری‌شده ثبت شد. برای نهایی‌سازی «تأیید» را بزنید.",
                kb
            )
            return

        # کی‌آس بعد از خریداری
        alive = [s for s in sorted(g.seats) if s not in g.striked]
        g.chaos_selected = set()
        kb = kb_pick_multi_seats(
            alive, g.chaos_selected, 3,
            confirm_cb="chaos_confirm",
            back_cb="back_to_winner_select"
        )
        await set_hint_and_kb(
            ctx, chat, g,
            "🌀 حالت کی‌آس: ۳ نفر از بازیکنان زنده را انتخاب کنید و سپس «تأیید» را بزنید.",
            kb
        )
        return


    if data.startswith("toggle_multi_") and g.awaiting_winner and g.chaos_mode:
        try:
            s = int(data.split("_")[2])
        except:
            return

        alive = [x for x in sorted(g.seats) if x not in g.striked]
        if s in alive:
            if s in g.chaos_selected:
                g.chaos_selected.remove(s)
            else:
                if len(g.chaos_selected) >= 3:
                    await safe_q_answer(q, "حداکثر ۳ نفر!", show_alert=True)
                else:
                    g.chaos_selected.add(s)
            store.save()

        kb = kb_pick_multi_seats(
            alive, g.chaos_selected, 3,
            confirm_cb="chaos_confirm",
            back_cb="back_to_winner_select"
        )
        await set_hint_and_kb(
            ctx, chat, g,
            f"🌀 حالت کی‌آس: {len(g.chaos_selected)}/3 نفر انتخاب شده‌اند. ادامه دهید و «تأیید» را بزنید.",
            kb
        )
        return


    if data == "chaos_confirm" and g.awaiting_winner and g.chaos_mode:
        if len(g.chaos_selected) != 3:
            await safe_q_answer(q, "باید دقیقاً ۳ نفر را انتخاب کنی.", show_alert=True)
            return

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأیید", callback_data="confirm_winner")],
            [InlineKeyboardButton("↩️ بازگشت", callback_data="back_to_winner_select")],
        ])
        await set_hint_and_kb(
            ctx, chat, g,
            "🔒 انتخاب‌ها ثبت شد. برای نهایی‌سازی نتیجه «تأیید» را بزنید.",
            kb
        )
        return


    if data == "back_to_winner_select" and uid == g.god_id and g.awaiting_winner:
        await set_hint_and_kb(
            ctx, chat, g,
            "برنده را انتخاب کنید:",
            kb_endgame_root()
        )
        return


    if data == "confirm_winner" and uid == g.god_id and getattr(g, "temp_winner", None):
        g.awaiting_winner = False
        g.winner_side = "شهر" if "city" in g.temp_winner else "مافیا"
        g.clean_win = "clean" in g.temp_winner
        # در صورت حالت کی‌آس، g.chaos_selected قبلاً تنظیم شده
        g.temp_winner = None
        store.save()

        await announce_winner(ctx, update, g)
        await reset_game(update=update)
        return



    # ─── اگر بازی پایان یافته، دیگر ادامه نده ────────────────────
    if g.phase == "ended":
        return

    if data == "vote_done" and uid == g.god_id:
        # 🧹 حذف پیام رأی‌گیری (اگر هنوز هست)
        if g.last_vote_msg_id:
            try:
                await ctx.bot.delete_message(chat_id=chat, message_id=g.last_vote_msg_id)
            except:
                pass
           # print("Trying to delete vote message:", g.last_vote_msg_id)  # ✅ اینجا بذار
            g.last_vote_msg_id = None

        await ctx.bot.send_message(chat, "✅ رأی‌گیری تمام شد.")
        store.save()
        return


    if data == "cleanup_below":
        if uid != g.god_id:
            await ctx.bot.send_message(chat,"⚠️ فقط راوی می‌تونه این کار رو انجام بده!")
            return

        try:
            deleted = 0
            # 🔄 پیام‌هایی که بعد از لیست ارسال شدن رو حذف می‌کنیم (حداکثر 100 عدد)
            for msg_id in range(g.last_seating_msg_id + 1, g.last_seating_msg_id + 100):
                try:
                    await ctx.bot.delete_message(chat_id=chat, message_id=msg_id)
                    deleted += 1
                except:
                    pass

            #await ctx.bot.send_message(chat, f"✅ {deleted} پیام زیر لیست پاک شد.")
        except Exception as e:
            await ctx.bot.send_message(chat, f"❌ خطا در پاکسازی: {e}")
        return


    # ────────────────────────────────────────────────────────────
    #  بخش‌های قدیمی (seat_ / cancel_ / strike_out / …)
    # ────────────────────────────────────────────────────────────

    if data == "strike_out" and uid == g.god_id:
        g.pending_strikes = set(g.striked)
        store.save()
        await publish_seating(ctx, chat, g, mode="strike")
        return

    if data == "strike_toggle_done" and uid == g.god_id:
        g.striked = set(g.pending_strikes)
        g.pending_strikes = set()
        store.save()
        await publish_seating(ctx, chat, g, mode=CTRL)
        return

    if data.startswith("strike_toggle_") and uid == g.god_id:
        seat = int(data.split("_")[2])
        if seat in g.pending_strikes:
            g.pending_strikes.remove(seat)
        else:
            g.pending_strikes.add(seat)
        store.save()
        await publish_seating(ctx, chat, g, mode="strike")
        return

    if data == BTN_REROLL:
        if uid != g.god_id:
            await ctx.bot.send_message(chat,"⚠️ فقط راوی می‌تواند نقش‌ها را رندوم کند!")
            return

        if not g.scenario or len(g.seats) != g.max_seats:
            await ctx.bot.send_message(chat,"⚠️ ابتدا سناریو انتخاب و همه صندلی‌ها پُر شوند.")
            return

        repeats = random.randint(1, 30)
        g.shuffle_repeats = repeats 

        try:
            await shuffle_and_assign(
                ctx,
                chat,
                g,
                shuffle_seats=False,
                uid_to_role=None,
                notify_players=False,
                preview_mode=True,
                role_shuffle_repeats=repeats,  
            )
            await ctx.bot.send_message(chat, f"🎲 نقش‌ها {repeats} بار رندوم شد.")
           
        except Exception:
            await ctx.bot.send_message(chat,"⚠️ خطا در رندوم نقش.")

        store.save()
        return



    # ─── رأی‌گیری‌ها ────────────────────────────────────────────
    if data == "init_vote":
        if uid != g.god_id:
            await ctx.bot.send_message(chat,"⚠️ فقط راوی می‌تواند رأی‌گیری را شروع کند!")
            return

        g.voted_targets = set()  # 🧹 ریست تیک‌های قبلی هنگام شروع رأی‌گیری جدید
        await start_vote(ctx, chat, g, "initial_vote")
        return


    if data == "back_vote_init" and uid == g.god_id:
        g.phase = "voting_selection"
        g.voted_targets = set()  # 🧹 پاک کردن صندلی‌های قبلاً رأی‌گیری‌شده
        store.save()
        await ctx.bot.send_message(chat, "↩️ مجدداً کاندید رأی‌گیری را انتخاب کنید.")
        await start_vote(ctx, chat, g, "initial_vote")
        return


    if data == "final_vote" and uid == g.god_id:
        if uid != g.god_id:
            await ctx.bot.send_message(chat,"⚠️ فقط راوی می‌تواند رأی‌گیری نهایی را شروع کند!")
            return

        g.vote_type = "awaiting_defense"
        g.voted_targets = set()  # 🧹 پاک‌سازی لیست تیک‌ها برای رأی‌گیری نهایی
        store.save()

        msg = await ctx.bot.send_message(
            chat,
            "📢 صندلی‌های دفاع را وارد کنید (مثال: 1 3 5):",
            reply_markup=ForceReply(selective=True)
        )
        g.defense_prompt_msg_id = msg.message_id
        store.save()
        return

    if data == "status_query" and uid == g.god_id:
        g.status_mode = True
        await publish_seating(ctx, chat, g, mode="status")
        return

    if g.status_mode:
        changed = False

        if data == "inc_citizen":
            g.status_counts["citizen"] += 1
            changed = True

        elif data == "dec_citizen":
            if g.status_counts["citizen"] == 0:
                
                await safe_q_answer(q, "از صفر کمتر نمیشه.", show_alert=True)
                warn = await ctx.bot.send_message(chat, "⚠️  کمتر از صفر نمی‌شود.")
               
                async def _cleanup(msg_id: int):
                    await asyncio.sleep(2)
                    try:
                        await ctx.bot.delete_message(chat_id=chat, message_id=msg_id)
                    except Exception:
                        pass
                asyncio.create_task(_cleanup(warn.message_id))
                # UI را همان حالت status نگه دار
                await publish_seating(ctx, chat, g, mode="status")
                return
            g.status_counts["citizen"] -= 1
            changed = True

        elif data == "inc_mafia":
            g.status_counts["mafia"] += 1
            changed = True

        elif data == "dec_mafia":
            if g.status_counts["mafia"] == 0:
                await safe_q_answer(q, "از صفر کمتر نمیشه.", show_alert=True)
                warn = await ctx.bot.send_message(chat, "⚠️  کمتر از صفر نمی‌شود.")
                async def _cleanup(msg_id: int):
                    await asyncio.sleep(2)
                    try:
                        await ctx.bot.delete_message(chat_id=chat, message_id=msg_id)
                    except Exception:
                        pass
                asyncio.create_task(_cleanup(warn.message_id))
                await publish_seating(ctx, chat, g, mode="status")
                return
            g.status_counts["mafia"] -= 1
            changed = True

        elif data == "confirm_status":
            g.status_mode = False
            store.save()

            c = g.status_counts.get("citizen", 0)
            m = g.status_counts.get("mafia", 0)

            await ctx.bot.send_message(
                chat,
                f"📢 استعلام وضعیت :\n {c} شهروند\n {m} مافیا"
            )
            await publish_seating(ctx, chat, g, mode=CTRL)
            return

        if changed:
            store.save()
            await publish_seating(ctx, chat, g, mode="status")
        return



    if data == "back_vote_final" and uid == g.god_id:
        g.phase = "defense_selection"
        g.vote_type = "awaiting_defense"
        g.voted_targets = set()  # 🧹 پاک‌سازی لیست تیک‌ها هنگام برگشت
        store.save()

        msg = await ctx.bot.send_message(
            chat,
            "↩️ دوباره صندلی‌های دفاع را وارد کنید:",
            reply_markup=ForceReply(selective=True)
        )
        g.defense_prompt_msg_id = msg.message_id
        store.save()
        return

    if data.startswith("vote_"):
        if uid != g.god_id:
            await ctx.bot.send_message(chat,"⛔ فقط راوی می‌تواند رأی بدهد!")
            return
        seat_str = data.split("_")[1]
        if seat_str.isdigit():
            await handle_vote(ctx, chat, g, int(seat_str))
        return

def status_button_markup(g: GameState) -> InlineKeyboardMarkup:
    c = g.status_counts.get("citizen", 0)
    m = g.status_counts.get("mafia", 0)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"⚪ {c}", callback_data="noop"),
            InlineKeyboardButton("⬆️", callback_data="inc_citizen"),
            InlineKeyboardButton("⬇️", callback_data="dec_citizen")
        ],
        [
            InlineKeyboardButton(f"⚫ {m}", callback_data="noop"),
            InlineKeyboardButton("⬆️", callback_data="inc_mafia"),
            InlineKeyboardButton("⬇️", callback_data="dec_mafia")
        ],
        [
            InlineKeyboardButton("✅ تأیید", callback_data="confirm_status")
        ]
    ])

def strike_button_markup(g: GameState) -> InlineKeyboardMarkup:
    rows = []

    for i in range(1, g.max_seats + 1):
        if i in g.pending_strikes:
            label = f"{i} ❌"
        else:
            label = f"{i} ✅"
        rows.append([InlineKeyboardButton(label, callback_data=f"strike_toggle_{i}")])

    # دکمه تایید نهایی
    rows.append([InlineKeyboardButton("✅ تایید خط‌زدن", callback_data="strike_toggle_done")])

    return InlineKeyboardMarkup(rows)


async def shuffle_and_assign(
    ctx,
    chat_id: int,
    g: GameState,
    shuffle_seats: bool = True,
    uid_to_role: dict[int, str] | None = None,
    notify_players: bool = True,
    preview_mode: bool = False,
    role_shuffle_repeats: int = 1,
):

    # 1) بازیکن‌ها بر اساس ترتیب فعلی صندلی‌ها
    players = [g.seats[i] for i in sorted(g.seats)]
    uids = [uid for uid, _ in players]

    # 2) اگر نگاشت نقش→بازیکن داده نشده، اینجا بساز (مستقل از شماره صندلی)
    if uid_to_role is None:
        pool = [r for r, n in g.scenario.roles.items() for _ in range(n)]
        uids_for_roles = uids[:]
        reps = max(1, int(role_shuffle_repeats))
        for _ in range(reps):
            random.shuffle(pool)
            random.shuffle(uids_for_roles)
        uid_to_role = {uid_: pool[i] for i, uid_ in enumerate(uids_for_roles)}

    # 3) حالت پیش‌نمایش: فقط نگاشت را ذخیره کن و خارج شو (هیچ پیام/تغییری اعمال نکن)
    if preview_mode:
        g.preview_uid_to_role = uid_to_role
        store.save()
        return uid_to_role

    # 4) نهایی‌سازی: در صورت نیاز، صندلی‌ها را به تعداد مشخص شافل کن
    if shuffle_seats:
        reps = max(1, int(role_shuffle_repeats))
        for _ in range(reps):
            random.shuffle(players)

    g.seats = {i + 1: (uid, name) for i, (uid, name) in enumerate(players)}

    # 5) نسبت‌دادن نقش‌ها به صندلی‌ها از روی uid
    g.assigned_roles = {
        seat: uid_to_role[g.seats[seat][0]]
        for seat in g.seats
    }

    # 6) ارسال نقش‌ها به بازیکن‌ها (اختیاری) و ساخت لاگ برای گاد
    log, unreachable = [], []
    stickers = load_stickers()
    if notify_players:
        for seat in sorted(g.seats):
            uid, name = g.seats[seat]
            role = g.assigned_roles[seat]
            if role in stickers:
                try:
                    await ctx.bot.send_sticker(uid, stickers[role])
                except:
                    pass
            try:
                await ctx.bot.send_message(uid, f"🎭 نقش شما: {role}")
            except telegram.error.Forbidden:
                unreachable.append(name)

    for seat in sorted(g.seats):
        uid, name = g.seats[seat]
        role = g.assigned_roles[seat]
        log.append(f"{seat:>2}. {name} → {role}")

    if g.god_id:
        text = "👑 خلاصهٔ نقش‌ها:\n" + "\n".join(log)
        if unreachable:
            text += "\n⚠️ نشد برای این افراد پیام بفرستم: " + ", ".join(unreachable)
        try:
            await ctx.bot.send_message(g.god_id, text)
        except:
            pass

    # 7) به‌روزرسانی فاز و UI
    g.phase = "playing"
    store.save()
    await publish_seating(ctx, chat_id, g, mode=CTRL)

    return uid_to_role





async def handle_simple_seat_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    chat_id = msg.chat.id
    uid = msg.from_user.id
    g = gs(chat_id)

    # ⛔ فقط در حالت "idle" اجازه ثبت‌نام با /عدد هست
    if g.phase != "idle":
        return

    if not hasattr(g, 'user_names') or g.user_names is None:
        g.user_names = load_usernames_from_gist()

    command_text = msg.text.split('@')[0]
    try:
        seat_no = int(command_text[1:])
    except:
        return

    if seat_no in g.seats:
        await ctx.bot.send_message(chat_id, f"❗ صندلی {seat_no} قبلاً پُر شده.")
        return

    if uid in [u for u, _ in g.seats.values()]:
        await ctx.bot.send_message(chat_id, "❗ شما قبلاً ثبت‌نام کرده‌اید.")
        return

    name = g.user_names.get(uid, "ناشناس")
    g.seats[seat_no] = (uid, name)
    store.save()
    await publish_seating(ctx, chat_id, g)
    await ctx.bot.send_message(chat_id, f"✅ ثبت‌نام برای صندلی {seat_no} با نام «{name}» انجام شد.")


async def name_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    text = msg.text.strip()
    uid = msg.from_user.id
    chat_id = msg.chat.id
    g = gs(chat_id)

    # ─────────────────────────────────────────────────────────────
    # 1) تغییر ساعت شروع (فقط توسط گاد) – با یا بدون ریپلای
    # ─────────────────────────────────────────────────────────────
    if g.vote_type == "awaiting_time" and uid == g.god_id:

        g.event_time = text
        g.vote_type = None
        store.save()
        await publish_seating(ctx, chat_id, g)
        await ctx.bot.send_message(chat_id, f"✅ ساعت رویداد روی {text} تنظیم شد.")
        return

    # ─────────────────────────────────────────────────────────────
    # 2) ثبت‌نام/جابجایی با ریپلای به لیست: کاربر شماره صندلی می‌نویسد
    #    - اگر قبلاً نشسته بود، جابه‌جا می‌شود
    #    - اگر اسم ذخیره نباشد، «ناشناس»
    # ─────────────────────────────────────────────────────────────
    if (
        msg.reply_to_message
        and g.last_seating_msg_id
        and msg.reply_to_message.message_id == g.last_seating_msg_id
    ):
        if text.isdigit():
            seat_no = int(text)

            if not (1 <= seat_no <= g.max_seats):
                await ctx.bot.send_message(chat_id, "❌ شمارهٔ صندلی معتبر نیست.")
                return

            if seat_no in g.seats:
                await ctx.bot.send_message(chat_id, f"❌ صندلی {seat_no} قبلاً پُر شده.")
                return

            # نام ترجیحی
            preferred_name = g.user_names.get(uid, None)

            # آیا کاربر قبلاً روی صندلی‌ای نشسته؟
            existing_seat = None
            existing_name = None
            for s, (u, n) in g.seats.items():
                if u == uid:
                    existing_seat = s
                    existing_name = n
                    break

            final_name = preferred_name or existing_name or "ناشناس"

            if existing_seat is not None:
                # جابجایی
                del g.seats[existing_seat]
                g.seats[seat_no] = (uid, final_name)
                store.save()
                await publish_seating(ctx, chat_id, g)
                await ctx.bot.send_message(
                    chat_id,
                    f"↪️ «{final_name}» از صندلی {existing_seat} به صندلی {seat_no} منتقل شد."
                )
                return

            # ثبت‌نام جدید
            g.seats[seat_no] = (uid, final_name)
            store.save()
            await publish_seating(ctx, chat_id, g)
            await ctx.bot.send_message(
                chat_id,
                f"✅ ثبت‌نام برای صندلی {seat_no} با نام «{final_name}» انجام شد."
            )
            return

    # ─────────────────────────────────────────────────────────────
    # 3) تغییر نام کاربر (فقط وقتی از دکمه «✏️ تغییر نام» وارد شده)
    #    g.waiting_name[uid] = seat_no
    # ─────────────────────────────────────────────────────────────
    if uid in g.waiting_name:
        target_seat = g.waiting_name[uid]  # فلگ را فعلاً پاک نکنیم

        import re
        if not re.match(r'^[\u0600-\u06FF\s]+$', text):
            await ctx.bot.send_message(
                chat_id,
                "❗ لطفاً نام را فقط با حروف فارسی وارد کنید. دوباره امتحان کنید:"
            )
            return

        # ورودی معتبر شد → فلگ را پاک کن
        g.waiting_name.pop(uid, None)

        # ذخیره نام جدید در دفترچه
        g.user_names[uid] = text

        # اگر هنوز روی همان صندلی است، همان را آپدیت کن
        if target_seat in g.seats and g.seats[target_seat][0] == uid:
            g.seats[target_seat] = (uid, text)
            changed_seat = target_seat
        else:
            # اگر جای دیگری نشسته، صندلی فعلی‌اش را پیدا و آپدیت کن
            changed_seat = None
            for s, (u, n) in list(g.seats.items()):
                if u == uid:
                    g.seats[s] = (uid, text)
                    changed_seat = s
                    break

        store.save()
        await publish_seating(ctx, chat_id, g)

        # پیام تأیید
        if changed_seat:
            await ctx.bot.send_message(chat_id, f"✅ نام صندلی {changed_seat} به «{text}» تغییر کرد.")
        else:
            await ctx.bot.send_message(chat_id, f"✅ نام شما به «{text}» تغییر کرد.")

        # نوشتن روی Gist بعد از UI (برای جلوگیری از کندی)
        try:
            save_usernames_to_gist(g.user_names)
        except Exception:
            pass

        return





async def show_scenario_selection(ctx, chat_id: int, g: GameState):
    """نمایش لیست سناریوهای موجود برای انتخاب"""
    valid_scenarios = [s for s in store.scenarios if sum(s.roles.values()) == g.max_seats]
    
    if not valid_scenarios:
        await ctx.bot.send_message(chat_id, "❗ سناریوی مناسب برای این تعداد بازیکن پیدا نشد.")
        return
    
    # ایجاد دکمه‌های سناریو
    btns = [
        [InlineKeyboardButton(f"{s.name} ({sum(s.roles.values())} نفر)", callback_data=f"sc_{i}")]
        for i, s in enumerate(valid_scenarios)
    ]
    
    # اضافه کردن دکمه بازگشت اگر بازی در حال انجام است
    if g.phase != "idle":
        btns.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="back_to_game")])
    
    # ارسال پیام انتخاب سناریو
    scenario_msg = await ctx.bot.send_message(
        chat_id,
        "🎭 لطفاً یک سناریو انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(btns)
    )
    # ذخیره message_id برای حذف بعدی
    g.scenario_prompt_msg_id = scenario_msg.message_id
    g.awaiting_scenario = True
    store.save()


async def newgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    uid = update.effective_user.id

    # فقط گروه‌های فعال
    if chat not in store.active_groups:
        await update.message.reply_text("⛔ این گروه هنوز فعال نشده. ادمین اصلی باید /active بزند.")
        return

    # فقط در گروه‌ها
    if update.effective_chat.type not in {"group", "supergroup"}:
        await update.message.reply_text("این دستور فقط در گروه‌ها قابل استفاده است.")
        return

    # فقط ادمین گروه
    member = await ctx.bot.get_chat_member(chat, uid)
    if member.status not in {"administrator", "creator"}:
        await update.message.reply_text("فقط ادمین‌های گروه می‌تونن بازی جدید شروع کنن.")
        return

    # اگر آرگومان نداد → پیش‌فرض ۱۰
    seats = 10
    if ctx.args and ctx.args[0].isdigit():
        seats = int(ctx.args[0])

    # ساخت گیم جدید
    store.games[chat] = GameState(max_seats=seats)
    g = gs(chat)

    # بارگذاری/ذخیره نام‌ها در Gist
    g.user_names = load_usernames_from_gist()
    save_usernames_to_gist(g.user_names)

    # گاد پیش‌فرض = اجراکنندهٔ /newgame
    god_name = g.user_names.get(uid) or (update.effective_user.full_name or "—")
    g.god_id = uid
    g.god_name = god_name

    # سناریوی تصادفی با ظرفیت seats
    candidates = [s for s in store.scenarios if sum(s.roles.values()) == seats]
    if candidates:
        import random
        g.scenario = random.choice(candidates)
        g.last_roles_scenario_name = None  # تا لیست نقش‌ها دوباره چاپ شود
        g.awaiting_scenario = False
    else:
        g.scenario = None
        g.awaiting_scenario = True

    # آمار «waiting_list»
    now = datetime.now(timezone.utc).timestamp()
    store.group_stats.setdefault(chat, {"waiting_list": [], "started": [], "ended": []})
    store.group_stats[chat]["waiting_list"].append(now)
    store.save()

    # انتشار لیست اولیه
    await publish_seating(ctx, chat, g, mode=REG)
    # اگر سناریو پیدا نشد، انتخاب سناریو را باز کن
    if g.awaiting_scenario:
        g.from_startgame = True
        store.save()
        await show_scenario_selection(ctx, chat, g)



async def reset_game(ctx: ContextTypes.DEFAULT_TYPE = None, update: Update = None, chat_id: int = None):
    """ریست بازی با حفظ نام‌ها – هم قابل استفاده برای /resetgame و هم از داخل بات"""
    if update:
        chat_id = update.effective_chat.id
    elif not chat_id:
        raise ValueError("chat_id باید مشخص شود اگر update وجود ندارد")

    # 🔄 بارگذاری نام‌ها
    usernames = load_usernames_from_gist()

    store.games[chat_id] = GameState()
    g = store.games[chat_id]
    g.user_names = usernames
    save_usernames_to_gist(g.user_names)
    store.save()

    # اگر از طریق دستور اومده، پیام بفرست
    if update and update.message:
        await update.message.reply_text("🔁 بازی با حفظ نام‌ها ریست شد.")

async def resetgame_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    # بررسی اینکه فقط در گروه‌ها قابل اجرا باشه
    if chat.type not in {"group", "supergroup"}:
        await update.message.reply_text("این دستور فقط در گروه‌ها قابل استفاده است.")
        return

    # بررسی اینکه کاربر ادمین هست یا نه
    try:
        admins = await ctx.bot.get_chat_administrators(chat.id)
        admin_ids = [admin.user.id for admin in admins]
        if user.id not in admin_ids:
            await update.message.reply_text("فقط ادمین‌ها می‌تونن این دستور رو اجرا کنن.")
            return
    except:
        await update.message.reply_text("خطا در بررسی ادمین‌ها.")
        return

    # اجرای ریست بازی
    await reset_game(ctx=ctx, update=update)



async def add_seat_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Usage: /add <seat>")
        return

    seat = int(ctx.args[0])
    uid = update.effective_user.id
    chat = update.effective_chat.id
    g = gs(chat)

    if uid != g.god_id:
        return

    if seat in g.seats:
        await update.message.reply_text("❌ Seat already taken.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Use this command by replying to a message from the user you want to add.")
        return

    target_uid = update.message.reply_to_message.from_user.id

    # 🧠 بررسی نام ذخیره‌شده در gist
    name = g.user_names.get(target_uid, "ناشناس")
    g.seats[seat] = (target_uid, name)
    store.save()

    await update.message.reply_text(f"✅ صندلی {seat} با نام '{name}' به لیست اضافه شد.")

    # 🖥 به‌روزرسانی لیست صندلی‌ها
    await publish_seating(ctx, chat, g)

async def addscenario(update: Update, ctx):
    """/addscenario <name> role1:n1 role2:n2 ..."""

    if update.effective_chat.id not in store.active_groups:
        return  # گروه غیرمجاز

    # فقط توی گروه‌ها بررسی می‌کنیم
    if update.message.chat.type in ["group", "supergroup"]:
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        member = await ctx.bot.get_chat_member(chat_id, user_id)

        if member.status not in ["administrator", "creator"]:
            await update.message.reply_text("⚠️ فقط ادمین‌های گروه می‌تونن سناریو اضافه کنن.")
            return

    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /addscenario <name> role1:n1 role2:n2 ...")
        return

    name = ctx.args[0]
    roles: dict[str, int] = {}
    for pair in ctx.args[1:]:
        if ":" in pair:
            r, n = pair.split(":")
            roles[r.strip()] = int(n)

    new_scenario = Scenario(name, roles)
    store.scenarios.append(new_scenario)
    store.save()
    save_scenarios_to_gist(store.scenarios)

    await update.message.reply_text(f"✅ سناریو '{name}' اضافه شد با نقش‌ها: {roles}")



async def list_scenarios(update: Update, ctx):
    store.scenarios = load_scenarios_from_gist()  # 👈 بارگذاری از Gist

    if not store.scenarios:
        await update.message.reply_text("❌ No scenarios found.")
        return

    lines = ["📋 لیست سناریوها:"]
    for i, s in enumerate(store.scenarios, 1):
        role_summary = ", ".join(f"{role}: {count}" for role, count in s.roles.items())
        lines.append(f"{i}. {s.name} ({role_summary})")

    await update.message.reply_text("\n".join(lines))


async def remove_scenario(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if chat.id not in store.active_groups:
        return  # گروه غیرمجاز

    # 🔐 فقط ادمین‌ها اجازه دارند سناریو حذف کنند
    if chat.type != "private":
        member = await ctx.bot.get_chat_member(chat.id, user.id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text("⚠️ فقط ادمین‌های گروه می‌تونن سناریو حذف کنن.")
            return

    if not ctx.args:
        await update.message.reply_text("❌ نحوه استفاده: /removescenario <نام سناریو>")
        return

    name = " ".join(ctx.args).strip()
    before = len(store.scenarios)
    store.scenarios = [s for s in store.scenarios if s.name != name]
    after = len(store.scenarios)

    if before == after:
        await update.message.reply_text(f"⚠️ سناریویی با نام «{name}» پیدا نشد.")
    else:
        store.save()
        save_scenarios_to_gist(store.scenarios)
        await update.message.reply_text(f"🗑️ سناریوی «{name}» با موفقیت حذف شد.")

 
async def dynamic_timer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    uid = update.effective_user.id
    g = gs(chat)

    # ❗ جلوگیری از اجرای تایمر روی پیام‌های قدیمی (مثلاً در لحظهٔ ری‌استارت بات)
    if (datetime.now(timezone.utc) - update.message.date).total_seconds() > 10:
        return  # اگر پیام خیلی قدیمیه، هیچی نکن

    if uid != g.god_id:
        await update.message.reply_text("⛔ فقط گاد می‌تونه تایمر بزنه.")
        return

    cmd = update.message.text.strip().lstrip("/")
    if not cmd.endswith("s") or not cmd[:-1].isdigit():
        await update.message.reply_text("❗ دستور درست نیست. مثال: /20s")
        return

    seconds = int(cmd[:-1])
    await update.message.reply_text(f"⏳ تایمر {seconds} ثانیه‌ای شروع شد...")
    await asyncio.sleep(seconds)
    await ctx.bot.send_message(chat, "⏰ تایم تمام")


async def transfer_god_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id

    # 1) فقط در گروه‌های فعال
    if chat not in store.active_groups:
        await update.message.reply_text("⛔ این گروه هنوز فعال نشده. اول /active را بزن.")
        return

    g = gs(chat)

    # 2) فقط بعد از ساخت بازی
    if not g.max_seats or g.max_seats <= 0:
        await update.message.reply_text("⚠️ اول با /newgame <seats> بازی بساز، بعد /god بزن.")
        return

    # ✅ فقط ادمین‌ها یا گاد فعلی اجازه تغییر گاد دارند
    admins = await ctx.bot.get_chat_administrators(chat)
    admin_ids = {admin.user.id for admin in admins}
    is_current_god = update.effective_user.id == g.god_id
    if update.effective_user.id not in admin_ids and not is_current_god:
        await update.message.reply_text("❌ فقط ادمین‌های گروه یا گاد فعلی می‌تونن گاد رو عوض کنن.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ لطفاً روی پیام کسی ریپلای کنید و بعد /god را بزنید.")
        return

    target = update.message.reply_to_message.from_user
    if g.god_id == target.id:
        await update.message.reply_text("ℹ️ همین حالا هم گاد هست.")
        return
    # نام ترجیحی: از gist اگر موجود، وگرنه نام تلگرام
    new_name = g.user_names.get(target.id, target.full_name)

    g.god_id = target.id
    g.god_name = new_name
    store.save()

    await update.message.reply_text(f"✅ حالا گاد جدید بازیه {new_name}.")

    mode = CTRL if g.phase != "idle" else REG
    await publish_seating(ctx, chat, g, mode=mode)

    if g.phase != "idle":
        log = []
        for seat in sorted(g.assigned_roles):
            role = g.assigned_roles.get(seat, "—")
            name = g.seats[seat][1]
            log.append(f"{name} ⇦ {role}")
        try:
            await ctx.bot.send_message(
                target.id,
                "👑 شما به عنوان گاد جدید انتخاب شدید.\n\n🧾 لیست نقش‌ها:\n" + "\n".join(log)
            )
        except telegram.error.Forbidden:
            await update.message.reply_text("⚠️ نتونستم نقش‌ها رو به پیوی گاد جدید بفرستم.")



async def handle_direct_name_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    chat_id = msg.chat.id
    uid = msg.from_user.id
    g = gs(chat_id)
    text = msg.text.strip()

    if g.vote_type == "awaiting_time" and uid == g.god_id:
        g.event_time = text
        g.vote_type = None
        store.save()
        await publish_seating(ctx, chat_id, g)
        await ctx.bot.send_message(chat_id, f"✅ ساعت رویداد روی {text} تنظیم شد.")
        return


    # ثبت نام برای صندلی
    if uid in g.awaiting_name_input:
        seat_no = g.awaiting_name_input.pop(uid)

        import re
        if not re.match(r'^[\u0600-\u06FF\s]+$', text):
            await ctx.bot.send_message(chat_id, "❗ لطفاً نام را فقط با حروف فارسی وارد کنید.")
            return

        g.seats[seat_no] = (uid, text)
        g.user_names[uid] = text
        save_usernames_to_gist(g.user_names)
        store.save()

        if uid in g.last_name_prompt_msg_id:
            try:
                await ctx.bot.delete_message(
                    chat_id=chat_id,
                    message_id=g.last_name_prompt_msg_id[uid]
                )
            except:
                pass
            del g.last_name_prompt_msg_id[uid]

        await publish_seating(ctx, chat_id, g)
        return  

    # -------------- defense seats by God ------------------
    if g.vote_type == "awaiting_defense" and uid == g.god_id:
        nums = [int(n) for n in text.split() if n.isdigit() and int(n) in g.seats]

        # اگر ورودی معتبر نبود، پیام خطا بده و برگرد
        if not nums:
            await ctx.bot.send_message(chat_id, "❌ شماره صندلی معتبر وارد نشد. دوباره تلاش کنید (مثال: 1 3 5).")
            return

        g.defense_seats = nums
        g.vote_type = None  # ✅ غیرفعال کردن حالت وارد کردن صندلی دفاع

        # 🧹 حذف پیام درخواست صندلی‌های دفاع
        if g.defense_prompt_msg_id:
            try:
                await ctx.bot.delete_message(chat_id=chat_id, message_id=g.defense_prompt_msg_id)
            except:
                pass
            g.defense_prompt_msg_id = None

        store.save()
        await ctx.bot.send_message(chat_id, f"✅ صندلی‌های دفاع: {', '.join(map(str, nums))}")
        await start_vote(ctx, chat_id, g, "final")
        return


async def handle_stats_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(timezone.utc).timestamp()
    day_ago = now - 86400  # 24 ساعت گذشته

    msg_lines = []
    running_groups = []
    recruiting_groups = []

    for gid, g in store.games.items():
        g: GameState
        stats = store.group_stats.get(gid, {})
        started = sum(1 for t in stats.get("started", []) if t > day_ago)
        ended = sum(1 for t in stats.get("ended", []) if t > day_ago)

        try:
            chat = await ctx.bot.get_chat(gid)
            if chat.username:
                name = f"<a href='https://t.me/{chat.username}'>{chat.title or chat.username}</a> (<code>{gid}</code>)"
                is_private = False
            else:
                name = f"{chat.title or 'گروه خصوصی'}  <code>{gid}</code>"
                is_private = True
        except:
            name = f"(گروه ناشناس) <code>{gid}</code>"
            is_private = True


        # وضعیت فعلی
        if g.phase == "playing":
            running_groups.append(name)
        elif (
            g.scenario and
            g.god_id and
            len(g.seats) < g.max_seats and
            g.phase != "playing"
        ):
            recruiting_groups.append(name)

        msg_lines.append(f"👥 {name}:\n⏺ {started} شروع\n⏹ {ended} پایان\n")

    final_msg = "\n".join(msg_lines)
    final_msg += "\n\n🎮 <b>گروه‌هایی که بازی فعال دارن:</b>\n" + ", ".join(running_groups or ["—"])
    final_msg += "\n\n🪑 <b>گروه‌هایی که در حال عضوگیری هستن:</b>\n" + ", ".join(recruiting_groups or ["—"])

    await ctx.bot.send_message(
        update.effective_chat.id,
        final_msg,
        parse_mode="HTML",
        disable_web_page_preview=True
    )

async def leave_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != 99347107:
        await update.message.reply_text("⛔ فقط مدیر اصلی بات اجازه دارد این دستور را اجرا کند.")
        return

    if not ctx.args:
        await update.message.reply_text("لطفاً Chat ID گروه را وارد کنید.")
        return

    try:
        chat_id = int(ctx.args[0])
        await ctx.bot.leave_chat(chat_id)
        await update.message.reply_text(f"✅ بات از گروه {chat_id} خارج شد.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در خروج از گروه: {e}")

OWNER_IDS = {99347107, 449916967, 7501892705,5904091398}


async def activate_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in {"group", "supergroup"}:
        await update.message.reply_text("این دستور فقط در گروه‌ها قابل استفاده است.")
        return

    user_id = update.effective_user.id
    if user_id not in OWNER_IDS:
        await update.message.reply_text("⛔ فقط ادمین‌های اصلی می‌تونن گروه رو فعال کنن.")
        return

    store.active_groups.add(chat.id)
    store.save()
    ok = save_active_groups(store.active_groups)
    if not ok:
        await update.message.reply_text("⚠️ گروه فعال شد، اما ذخیره در Gist ناموفق بود.")
        return

    await update.message.reply_text("✅ این گروه با موفقیت فعال شد.")


async def deactivate_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in {"group", "supergroup"}:
        await update.message.reply_text("این دستور فقط در گروه‌ها قابل استفاده است.")
        return

    user_id = update.effective_user.id
    if user_id not in OWNER_IDS:
        await update.message.reply_text("⛔ فقط ادمین‌های اصلی می‌تونن گروه رو غیرفعال کنن.")
        return

    if chat.id in store.active_groups:
        store.active_groups.remove(chat.id)
        store.save()
        ok = save_active_groups(store.active_groups)
        if not ok:
            await update.message.reply_text("⚠️ گروه از لیست محلی حذف شد، ولی ذخیره در Gist ناموفق بود.")
            return
        await update.message.reply_text("🛑 این گروه غیرفعال شد و از Gist هم پاک شد.")
    else:
        await update.message.reply_text("ℹ️ این گروه از قبل فعال نبود.")


async def set_event_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id_int = update.effective_chat.id
    chat_id = str(chat_id_int)
    g = gs(chat_id_int)

    if update.effective_user.id != g.god_id:
        await update.message.reply_text("❌ فقط راوی می‌تواند شماره ایونت را تغییر دهد.")
        return

    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("⚠️ استفاده صحیح: /setevent <شماره>")
        return

    num = int(ctx.args[0])

    # ✅ به جای load/save خام، از کش استفاده کن و همون رو به‌روز کن
    nums = get_event_numbers()             # ← از کش می‌خوانیم
    nums[chat_id] = num                    # ← کش را بلافاصله به‌روز می‌کنیم
    save_event_numbers(nums)               # ← سپس یک PATCH به Gist

    # حالا لیست را ادیت کن؛ چون کش به‌روز شده، متن جدید می‌شود
    try:
        mode = CTRL if g.phase != "idle" else REG
        await publish_seating(ctx, chat_id_int, g, mode=mode)
    except Exception:
        pass

    await update.message.reply_text(f"✅ شماره ایونت برای این گروه روی {num} تنظیم شد.")


MY_ID = 99347107 

async def add_sticker_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # فقط آیدی تو
    if update.effective_user.id != MY_ID:
        await update.message.reply_text("⛔ فقط ادمین اصلی می‌تواند استیکر اضافه کند.")
        return

    # چک کن روی استیکر ریپلای شده یا نه
    if not update.message.reply_to_message or not update.message.reply_to_message.sticker:
        await update.message.reply_text("⚠️ باید روی پیام استیکر ریپلای کنید.")
        return

    if not ctx.args:
        await update.message.reply_text("⚠️ استفاده صحیح: /addsticker <نام نقش>")
        return

    role_name = " ".join(ctx.args).strip()
    file_id = update.message.reply_to_message.sticker.file_id

    stickers = load_stickers()
    stickers[role_name] = file_id
    save_stickers(stickers)

    await update.message.reply_text(f"✅ استیکر برای نقش «{role_name}» ذخیره شد.")

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    # BadRequest های بی‌اهمیت رو نادیده بگیر
    if isinstance(err, BadRequest) and ("Query is too old" in str(err) or "query id is invalid" in str(err)):
        return
    try:
        chat_id = update.effective_chat.id if update and hasattr(update, "effective_chat") else None
        print(f"[ERROR] chat={chat_id} err={err}")
    except Exception:
        pass
async def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("active", activate_group))
    app.add_handler(CommandHandler("deactivate", deactivate_group))
    # 👉 اضافه کردن هندلرها
    app.add_handler(CommandHandler("newgame", newgame, filters=group_filter))
    app.add_handler(CommandHandler("leave", leave_group, filters=filters.ChatType.PRIVATE & filters.User(99347107)))
    # 🪑 انتخاب صندلی با دستور مثل /3
    app.add_handler(
        MessageHandler(
            filters.Regex(r"^/\d+(@PouriaMafiaBot)?$") & filters.ChatType.GROUPS,
            handle_simple_seat_command
        )
    )
    app.add_handler(CommandHandler("resetgame", resetgame_cmd, filters=group_filter))
    app.add_handler(CommandHandler("addscenario", addscenario, filters=group_filter))
    app.add_handler(CommandHandler("listscenarios", list_scenarios, filters=group_filter))
    app.add_handler(CommandHandler("removescenario", remove_scenario, filters=group_filter))
    app.add_handler(CommandHandler("add", add_seat_cmd, filters=group_filter))
    app.add_handler(CommandHandler("god", transfer_god_cmd, filters=group_filter))
    app.add_handler(CommandHandler("setevent", set_event_cmd, filters=group_filter))
    app.add_handler(CommandHandler("addsticker", add_sticker_cmd, filters=filters.ChatType.PRIVATE))
    # ⏱ تایمر پویا مثل /3s
    app.add_handler(
        MessageHandler(
            filters.COMMAND & filters.Regex(r"^/\d+s$"),
            dynamic_timer
        )
    )

    # 👥 هندلر ریپلای‌های متنی (اول name_reply باشه)
    app.add_handler(
        MessageHandler(
            group_filter & filters.REPLY & filters.TEXT,
            name_reply
        )
    )

    # 🧑‍💻 ریپلای‌های مستقیم بدون ریپلای
    app.add_handler(
        MessageHandler(
            group_filter & filters.TEXT & ~filters.REPLY,
            handle_direct_name_input
        )
    )

    # 🎮 دکمه‌ها و رای‌گیری
    app.add_handler(CallbackQueryHandler(callback_router))

    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & filters.User(99347107)
            & filters.TEXT
            & filters.Regex(r"^/stats$"),
            handle_stats_request
        )
    )

      
    # ✅ initialize application
    await app.initialize()

    # 🌐 ساخت aiohttp برای وب‌هوک
    from aiohttp import web
    import os

    aio_app = web.Application()
    aio_app.router.add_get("/", lambda req: web.Response(text="OK"))

    async def webhook_handler(request):
        data = await request.json()
        update = Update.de_json(data, app.bot)
        await app.process_update(update)
        return web.Response()

    aio_app.router.add_post(f"/{TOKEN}", webhook_handler)

    # 📡 تنظیم آدرس وب‌هوک
    webhook_url = f"https://mafia-bot-259u.onrender.com/{TOKEN}"
    await app.bot.set_webhook(webhook_url)

    # 🟢 اجرای سرور aiohttp
    runner = web.AppRunner(aio_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080)))
    await site.start()
    print("✅ Webhook server is running...")

    # ▶️ اجرای اپلیکیشن
    await app.start()

    # ⏳ جلوگیری از خاموشی برنامه
    await asyncio.Event().wait()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

