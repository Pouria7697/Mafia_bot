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
from telegram.ext import filters
group_filter = filters.ChatType.GROUPS
from datetime import datetime, timezone, timedelta  
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, Message
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from collections import defaultdict
# --- CALLBACK DATA CONSTANTS ---
BTN_GOD     = "register_god"    
BTN_PLAYER  = "player_name"    
BTN_DELETE  = "delete_seat"      
BTN_START   = "start_game"      
BTN_CALL = "call_players"     

GH_TOKEN = os.environ.get("GH_TOKEN")
GIST_ID = os.environ.get("GIST_ID")
GIST_FILENAME = "gistfile1.txt"
GIST_API_URL = f"https://api.github.com/gists/{GIST_ID}"


USERNAMES_FILENAME = "usernames.json" 
TOKEN = os.environ.get("TOKEN")
PERSIST_FILE = "mafia_data.pkl"
SEAT_EMOJI = "👤"; LOCKED_EMOJI = "🔒"; GOD_EMOJI = "👳🏻‍♂️"; START_EMOJI = "🚀"


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
        self.purchased_seat = None
        self.awaiting_purchase_number = False


class Store:
    def __init__(self, path=PERSIST_FILE):
        self.path = path
        self.scenarios: list[Scenario] = []
        self.games: dict[int, GameState] = {}
        self.group_stats: dict[int, dict] = {} 
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path, "rb") as f:
                obj = pickle.load(f)
                self.scenarios = obj.get("scenarios", [])
                self.games = obj.get("games", {})
                self.group_stats = obj.get("group_stats", {})
                for g in self.games.values():
                    if isinstance(g, GameState):
                        g.__post_init__()
        else:
            self.save()

    def save(self):
        with open(self.path, "wb") as f:
            pickle.dump({
                "scenarios": self.scenarios,
                "games": self.games,
                "group_stats": self.group_stats
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


def seat_keyboard(g: GameState) -> InlineKeyboardMarkup:
    rows = []

    rows.append([
        InlineKeyboardButton("✏️ ثبت نام راوی", callback_data="register_god"),
        InlineKeyboardButton("⏰ تغییر ساعت", callback_data="change_time")
    ])
    rows.append([
        
        InlineKeyboardButton("❌ حذف بازیکن", callback_data="delete_player")
    ])
    # 👇 دکمه جدید برای شروع بازی
    rows.append([
        InlineKeyboardButton("🚀 شروع بازی", callback_data="startgame")
    ])

    return InlineKeyboardMarkup(rows)



def text_seating_keyboard(g: GameState) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("✏️ ثبت نام راوی", callback_data=BTN_GOD),
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
        row = [
            InlineKeyboardButton("🔊 صدا زدن", callback_data=BTN_CALL),
            InlineKeyboardButton("🪄 تغییر سناریو", callback_data="change_scenario")
        ]
        if len(g.seats) == g.max_seats:
            row.insert(0, InlineKeyboardButton("▶️ شروع بازی", callback_data="startgame"))
        rows.append(row)

    return InlineKeyboardMarkup(rows)


# ─────────────────────────────────────────────────────────────
#  دکمه‌های کنترل راوی در حین بازی
# ─────────────────────────────────────────────────────────────
def control_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✂️ خط‌زدن",           callback_data="strike_out")],
        [InlineKeyboardButton("🗳 رأی‌گیری اولیه",     callback_data="init_vote")],
        [InlineKeyboardButton("🗳 رأی‌گیری نهایی",     callback_data="final_vote")],
        [InlineKeyboardButton("🏁 اتمام بازی",        callback_data="end_game")]
    ])

def striked_control_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔙 بازگشت", callback_data="strike_undo"),
            InlineKeyboardButton("✅ انجام شد", callback_data="strike_done")
        ]
    ])
# ─────── بالای فایل (یا کنار بقیهٔ ثوابت) ──────────────────
REG   = "register"   # نمایش دکمه‌های ثبت‌نامی
CTRL  = "controls"   # فقط دکمه‌های کنترلی

# ─────── تابع اصلاح‌ شده ───────────────────────────────────
async def publish_seating(ctx, chat_id: int, g: GameState, mode: str = REG):
    today = jdatetime.date.today().strftime("%Y/%m/%d")

    emoji_numbers = ["⓿", "➊", "➋", "➌", "➍", "➎", "➏", "➐", "➑", "➒", "➓",
                     "⓫", "⓬", "⓭", "⓮", "⓯", "⓰", "⓱", "⓲", "⓳", "⓴"]

    # گرفتن آیدی یا لینک گروه
    group_id_or_link = f"🆔 {chat_id}"
    if ctx.bot.username and chat_id < 0:
        try:
            chat_obj = await ctx.bot.get_chat(chat_id)
            if chat_obj.username:
                group_id_or_link = f"🔗 <a href='https://t.me/{chat_obj.username}'>{chat_obj.title}</a>"
            else:
                group_id_or_link = f"🔒 {chat_obj.title}"
        except:
            pass

    # ساخت متن لیست صندلی‌ها
    lines = [
        f"{group_id_or_link}",
        "♚🎭 <b>رویداد مافیا</b>",
        f"♚📆 <b>تاریخ:</b> {today}",
        f"♚🕰 <b>زمان:</b> {g.event_time or '---'}",
        f"♚🎩 <b>راوی:</b> <a href='tg://user?id={g.god_id}'>{g.god_name or '❓'}</a>",
    ]

    if g.scenario:
        lines.append(f"♚📜 <b>سناریو:</b> {g.scenario.name} | 👥 {sum(g.scenario.roles.values())} نفر")

    lines.append("\n\n♚📂 <b>بازیکنان:</b>\n")

    for i in range(1, g.max_seats + 1):
        emoji_num = emoji_numbers[i] if i < len(emoji_numbers) else str(i)
        if i in g.seats:
            uid, name = g.seats[i]
            txt = f"<a href='tg://user?id={uid}'>{name}</a>"
            if i in g.striked:
                txt += " ❌☠️"
            line = f"♚{emoji_num}  {txt}"
        else:
            line = f"♚{emoji_num} ⬜ /{i}"
        lines.append(line)

    text = "\n".join(lines)
    kb = text_seating_keyboard(g) if mode == REG else control_keyboard()

    try:
        if g.last_seating_msg_id:
            await ctx.bot.edit_message_text(
                chat_id=chat_id,
                message_id=g.last_seating_msg_id,
                text=text,
                parse_mode="HTML",
                reply_markup=kb
            )
        else:
            msg = await ctx.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
            g.last_seating_msg_id = msg.message_id
            if chat_id < 0:
                await ctx.bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
    except:
        msg = await ctx.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
        g.last_seating_msg_id = msg.message_id

    # 🟡 نمایش لیست نقش‌ها فقط یکبار یا در صورت تغییر سناریو
    if g.scenario and mode == REG:
        if getattr(g, "last_roles_scenario_name", None) != g.scenario.name:
            role_lines = ["📜 <b>لیست نقش‌های سناریو:</b>\n"]
            for role, count in g.scenario.roles.items():
                for _ in range(count):
                    role_lines.append(f"🔸 {role}")
            role_text = "\n".join(role_lines)

            try:
                if hasattr(g, "last_roles_msg_id") and g.last_roles_msg_id:
                    await ctx.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=g.last_roles_msg_id,
                        text=role_text,
                        parse_mode="HTML"
                    )
                else:
                    role_msg = await ctx.bot.send_message(chat_id, role_text, parse_mode="HTML")
                    g.last_roles_msg_id = role_msg.message_id
            except:
                role_msg = await ctx.bot.send_message(chat_id, role_text, parse_mode="HTML")
                g.last_roles_msg_id = role_msg.message_id

            # به‌روزرسانی نام آخرین سناریوی ارسال‌شده
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
    god_name = g.god_name or "—"
    scenario_name = getattr(g.scenario, "name", "—")

    # لینک‌دار کردن گروه
    if chat.username:
        group_link = f"<a href='https://t.me/{chat.username}'>{group_title}</a>"
    else:
        group_link = group_title  # گروه خصوصی لینک‌نداره

    lines = [
        f"░⚜️🎮 گروه: {group_link}",
        f"░⚜️📅 تاریخ: {date_str}",
        f"░💡🔱 راوی: <a href='tg://user?id={g.god_id}'>{g.god_name or '❓'}</a>",
        f"░⚜️📃 سناریو: {scenario_name}",
        "",
        "░⚜️💫 لیست بازیکنان ⬇️",
        "",
    ]

    for seat in sorted(g.seats):
        uid, name = g.seats[seat]
        role = g.assigned_roles.get(seat, "—")
        if getattr(g, "purchased_seat", None) == seat:
            role_display = f"{role} / مافیاساده"
        else:
            role_display = role

        lines.append(f"░⚜️▪️{seat}- <a href='tg://user?id={uid}'>{name}</a> ⇦ {role_display}")



    lines.append("")

    result_line = f"🏆 نتیجه بازی: برد {g.winner_side}"
    if getattr(g, "clean_win", False):
        result_line += " (کلین‌شیت)"
    lines.append(result_line)

    g.phase = "ended"
    store.save()

    msg = await ctx.bot.send_message(
        chat.id,
        "\n".join(lines),
        parse_mode="HTML"  # لازم برای لینک
    )

    try:
        await ctx.bot.pin_chat_message(chat_id=chat.id, message_id=msg.message_id)
    except Exception as e:
        print("⚠️ خطا در پین کردن پیام:", e)

# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
#  CALL-BACK ROUTER – نسخهٔ کامل با فاصله‌گذاری درست
# ─────────────────────────────────────────────────────────────
async def callback_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return
    q = update.callback_query
    await q.answer()
    data = q.data
    chat = q.message.chat.id
    uid = q.from_user.id
    g = gs(chat)

    # ─── دکمه‌های پایین پیام صندلی‌ها ────────────────────────────
    if data == BTN_GOD:  # ✏️ ثبت نام راوی
        if g.god_id is None:  # هنوز راوی تعیین نشده
            g.god_id = uid
            g.waiting_god.add(uid)
            store.save()
            await ctx.bot.send_message(
                chat,
                "😎 نام راوی را بنویس:",
                reply_markup=ForceReply(selective=True)
            )
            return

    # ─── حذف بازیکن توسط گاد ────────────────────────────────────
    if data == BTN_DELETE:
        if uid != g.god_id:
            await q.answer("⚠️ فقط راوی می‌تواند حذف کند!", show_alert=True)
            return
        g.vote_type = "awaiting_delete"
        store.save()
        await ctx.bot.send_message(chat, "🔴 شمارهٔ صندلی برای حذف را ریپلای کنید:")
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
            await q.answer("❗ شما در لیست نیستید.", show_alert=True)
        return

    if data == "change_name":
        if uid not in [u for u, _ in g.seats.values()]:
            await q.answer("❗ شما هنوز ثبت‌نام نکرده‌اید.", show_alert=True)
            return

        g.waiting_name[uid] = [s for s in g.seats if g.seats[s][0] == uid][0]
        store.save()

        await ctx.bot.send_message(
            chat,
            "✏️ این پیام را ریپلای کنید و نام جدید خود را به فارسی وارد کنید:"
        )
        return


    # ─── صدا زدن همه قبلِ شروع ──────────────────────────────────
    if data == BTN_CALL:
        if uid != g.god_id:
            await q.answer("⚠️ فقط راوی می‌تواند این دکمه را بزند!", show_alert=True)
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
            await q.answer("⚠️ فقط راوی می‌تواند زمان را عوض کند!", show_alert=True)
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
            await q.answer("⚠️ ابتدا باید راوی ثبت نام کند!", show_alert=True)
            return

        if uid != g.god_id:
            await q.answer("⚠️ فقط راوی می‌تواند بازی را شروع کند!", show_alert=True)
            return

        if len(g.seats) != g.max_seats:
            await ctx.bot.send_message(chat, "⚠️ هنوز همهٔ صندلی‌ها پُر نشده!")
            return

        # ✅ اگر سناریو از قبل انتخاب شده → بپرس که آیا می‌خواهی صندلی‌ها رندوم بشن؟
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

        # ⛔ اگر سناریو انتخاب نشده → برو سراغ انتخاب سناریو
        g.awaiting_scenario = True
        g.from_startgame = False
        store.save()
        await show_scenario_selection(ctx, chat, g)
        return

    if data == "shuffle_yes":
        if not g.awaiting_shuffle_decision:
            return

        g.awaiting_shuffle_decision = False
        g.from_startgame = False
        store.save()

        # حذف پیام دکمه
        if hasattr(g, "shuffle_prompt_msg_id") and g.shuffle_prompt_msg_id:
            try:
                await ctx.bot.delete_message(chat, g.shuffle_prompt_msg_id)
            except:
                pass
            g.shuffle_prompt_msg_id = None

        await shuffle_and_assign(ctx, chat, g, shuffle_seats=True)
        return

    if data == "shuffle_no":
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

        await shuffle_and_assign(ctx, chat, g, shuffle_seats=False)
        return



    if data == "change_scenario":
        if g.god_id is None or uid != g.god_id:
            await q.answer("⚠️ فقط راوی می‌تواند سناریو را تغییر دهد!", show_alert=True)
            return

        g.awaiting_scenario = True
        g.from_startgame = True  # 🔁 این بار برای نمایش ساده است نه نقش دادن
        store.save()
        await show_scenario_selection(ctx, chat, g)
        return

    if data.startswith("sc_"):
        idx = int(data.split("_")[1])
        valid = [s for s in store.scenarios if sum(s.roles.values()) == g.max_seats]

        if idx < len(valid):
            g.scenario = valid[idx]
            g.awaiting_scenario = False
            store.save()

            # حذف پیام انتخاب سناریو
            if g.scenario_prompt_msg_id:
                try:
                    await ctx.bot.delete_message(chat, g.scenario_prompt_msg_id)
                except:
                    pass
                g.scenario_prompt_msg_id = None

            # ⛳ تشخیص اینکه از /newgame آمده یا از دکمه شروع بازی
            if g.from_startgame:
                g.from_startgame = False  # ریست
                await publish_seating(ctx, chat, g)
            else:
                if uid != g.god_id:
                    await q.answer("⚠️ فقط راوی می‌تواند سناریو را انتخاب کند!", show_alert=True)
                    return
                await shuffle_and_assign(ctx, chat, g)
        return

    # ─── پایان بازی و انتخاب برنده ──────────────────────────────
    if data == "end_game" and uid == g.god_id:

        now = datetime.now(timezone.utc).timestamp()
        store.group_stats.setdefault(chat, {
            "waiting_list": [],
            "started": [],
            "ended": []
        })
        store.group_stats[chat]["ended"].append(now)
        store.save()

        g.phase = "awaiting_winner"
        g.awaiting_winner = True
        store.save()
        await ctx.bot.send_message(
            chat,
            "🏁 بازی تمام! تیم برنده؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏙 شهر",          callback_data="winner_city")],
                [InlineKeyboardButton("😈 مافیا",         callback_data="winner_mafia")],
                [InlineKeyboardButton("🏙 کلین‌شیت شهر",   callback_data="clean_city")],
                [InlineKeyboardButton("😈 کلین‌شیت مافیا", callback_data="clean_mafia")],
                [InlineKeyboardButton("⬅️ بازگشت",        callback_data="back_endgame")],
            ])
        )
        return

    if data == "back_endgame" and uid == g.god_id:
        g.awaiting_winner = False
        g.phase = "playing"
        store.save()
        try:
            await ctx.bot.delete_message(chat, q.message.message_id)
        except:
            pass
        await ctx.bot.send_message(chat, "↩️ انتخاب برنده لغو شد.")
        return

    if data in {"winner_city", "winner_mafia", "clean_city", "clean_mafia"} and g.awaiting_winner:
        g.temp_winner = data  # 🆕 مرحله اول: ذخیره انتخاب موقت
        winner_txt = {
            "winner_city": "🏙 شهر",
            "winner_mafia": "😈 مافیا",
            "clean_city": "🏙 کلین‌شیت شهر",
            "clean_mafia": "😈 کلین‌شیت مافیا"
        }[data]

        if data in {"winner_city", "winner_mafia"}:
            g.ask_purchased = True  # 🆕 باید بپرسیم کسی خریداری شده یا نه
            store.save()
            await ctx.bot.send_message(
                chat,
                f"🛒 آیا کسی خریداری شده است؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ بله", callback_data="purchased_yes")],
                    [InlineKeyboardButton("❌ خیر", callback_data="purchased_no")],
                    [InlineKeyboardButton("↩️ بازگشت", callback_data="back_to_winner_select")]
                ])
            )
            return

        # برای کلین‌شیت نیازی به پرسش نیست
        await ctx.bot.send_message(
            chat,
            f"🔒 برنده انتخاب شد: <b>{winner_txt}</b>\nآیا تأیید می‌کنید؟",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأیید", callback_data="confirm_winner")],
                [InlineKeyboardButton("↩️ بازگشت", callback_data="back_to_winner_select")],
            ])
        )
        return


    if data == "purchased_yes" and g.awaiting_winner:
        g.awaiting_purchase_number = True
        await ctx.bot.send_message(chat, "✏️ لطفاً شماره صندلی بازیکن خریداری‌شده را وارد کنید:")
        return

    if data == "purchased_no" and g.awaiting_winner:
        g.purchased_seat = None

        # بازسازی متن برنده بر اساس temp_winner
        winner_txt = {
            "winner_city": "🏙 شهر",
            "winner_mafia": "😈 مافیا"
        }.get(g.temp_winner, "❓")

        await ctx.bot.send_message(
            chat,
            f"🔒 برنده انتخاب شد: <b>{winner_txt}</b>\nآیا تأیید می‌کنید؟",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ تأیید", callback_data="confirm_winner")
                ],
                [
                    InlineKeyboardButton("↩️ بازگشت", callback_data="back_to_winner_select")
                ],
            ])
        )
        store.save()
        return


    if data == "back_to_winner_select" and uid == g.god_id:
        g.temp_winner = None
        store.save()
        await ctx.bot.send_message(
            chat,
            "🔁 لطفاً دوباره تیم برنده را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏙 شهر",          callback_data="winner_city")],
                [InlineKeyboardButton("😈 مافیا",         callback_data="winner_mafia")],
                [InlineKeyboardButton("🏙 کلین‌شیت شهر",   callback_data="clean_city")],
                [InlineKeyboardButton("😈 کلین‌شیت مافیا", callback_data="clean_mafia")],
                [InlineKeyboardButton("⬅️ بازگشت",        callback_data="back_endgame")],
            ])
        )
        return

    if data == "confirm_winner" and uid == g.god_id and hasattr(g, "temp_winner") and g.temp_winner:
        g.awaiting_winner = False
        g.winner_side = "شهر" if "city" in g.temp_winner else "مافیا"
        g.clean_win = "clean" in g.temp_winner
        g.temp_winner = None  # 🧹 پاک‌سازی
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
            await q.answer("⚠️ فقط راوی می‌تونه این کار رو انجام بده!", show_alert=True)
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
    if data.startswith("seat_"):
        seat = int(data.split("_")[1])

        if uid in [u for u, _ in g.seats.values()] or uid == g.god_id or seat in g.seats:
            return

        if uid in g.user_names:
            g.seats[seat] = (uid, g.user_names[uid])
            store.save()
            await publish_seating(ctx, chat, g)
            return

        g.awaiting_name_input[uid] = seat
        sent_msg = await ctx.bot.send_message(
            chat,
            f"✏️ نام خود را برای صندلی {seat} وارد کنید:"
        )
        g.last_name_prompt_msg_id[uid] = sent_msg.message_id
        store.save()
        return


    if data.startswith("cancel_"):
        seat = int(data.split("_")[1])
        if seat in g.seats and (g.seats[seat][0] == uid or uid == g.god_id):
            del g.seats[seat]
            store.save()
            await q.edit_message_reply_markup(reply_markup=text_seating_keyboard(g))
        return

    if data == "strike_out" and uid == g.god_id:
        # حذف پیام‌های قبلی اگر وجود دارند
        if g.strike_list_msg_id:
            try:
                await ctx.bot.delete_message(chat, g.strike_list_msg_id)
            except:
                pass
        
        if g.strike_control_msg_id:
            try:
                await ctx.bot.delete_message(chat, g.strike_control_msg_id)
            except:
                pass
        
        # ارسال لیست بازیکنان برای خط زدن
        btns = [
            [InlineKeyboardButton(f"{s}. {g.seats[s][1]}", callback_data=f"do_strike_{s}")]
            for s in sorted(g.seats) if s not in g.striked
        ]
        
        list_msg = await ctx.bot.send_message(
            chat,
            "چه کسی خط بخورد؟",
            reply_markup=InlineKeyboardMarkup(btns)
        )
        
        g.strike_list_msg_id = list_msg.message_id
        g.strike_backup_seats = set(g.striked)  # ذخیره وضعیت فعلی
        store.save()
        return

    if data.startswith("do_strike_") and uid == g.god_id:
        seat = int(data.split("_")[2])
        
        if seat in g.seats and seat not in g.striked:
            g.striked.add(seat)
            store.save()
            
            # حذف پیام لیست بازیکنان
            if g.strike_list_msg_id:
                try:
                    await ctx.bot.delete_message(chat_id=chat, message_id=g.strike_list_msg_id)
                except:
                    pass
                g.strike_list_msg_id = None
            
            # ارسال دکمه‌های مدیریت
            btns = [
                [InlineKeyboardButton("🔙 بازگشت", callback_data="undo_strike")],
                [InlineKeyboardButton("✅ انجام شد", callback_data="strike_done")]
            ]
            
            ctrl_msg = await ctx.bot.send_message(
                chat,
                f"🔧 مدیریت خط زدن برای {seat}. {g.seats[seat][1]}:",
                reply_markup=InlineKeyboardMarkup(btns)
            )
            
            g.strike_control_msg_id = ctrl_msg.message_id
            await publish_seating(ctx, chat, g, mode=CTRL)
        return

    if data == "undo_strike" and uid == g.god_id:
        g.striked = set(g.strike_backup_seats)
        g.strike_backup_seats = {}
        store.save()
        
        # حذف پیام مدیریت
        if g.strike_control_msg_id:
            try:
                await ctx.bot.delete_message(chat, g.strike_control_msg_id)
            except:
                pass
            g.strike_control_msg_id = None
        
        await publish_seating(ctx, chat, g, mode=CTRL)
        return

    if data == "strike_done" and uid == g.god_id:
        # فقط پیام‌های مدیریت را حذف کن
        if g.strike_control_msg_id:
            try:
                await ctx.bot.delete_message(chat, g.strike_control_msg_id)
            except:
                pass
            g.strike_control_msg_id = None
        
        if g.strike_list_msg_id:
            try:
                await ctx.bot.delete_message(chat, g.strike_list_msg_id)
            except:
                pass
            g.strike_list_msg_id = None
        
        g.strike_backup_seats = {}  # پاک کردن نسخه پشتیبان
        store.save()
        return
    # ─── رأی‌گیری‌ها ────────────────────────────────────────────
    if data == "init_vote":
        if uid != g.god_id:
            await q.answer("⚠️ فقط راوی می‌تواند رأی‌گیری را شروع کند!", show_alert=True)
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
            await q.answer("⚠️ فقط راوی می‌تواند رأی‌گیری نهایی را شروع کند!", show_alert=True)
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
            await q.answer("⛔ فقط راوی می‌تواند رأی بدهد!", show_alert=True)
            return
        seat_str = data.split("_")[1]
        if seat_str.isdigit():
            await handle_vote(ctx, chat, g, int(seat_str))
        return



async def shuffle_and_assign(ctx, chat_id: int, g: GameState, shuffle_seats: bool = True):
    if shuffle_seats:
        shuffled = list(g.seats.items())
        random.shuffle(shuffled)
        g.seats = {i + 1: p[1] for i, p in enumerate(shuffled)}

    # رندوم‌سازی نقش‌ها
    pool = [r for r, n in g.scenario.roles.items() for _ in range(n)]
    random.shuffle(pool)
    g.assigned_roles = {seat: pool[i] for i, seat in enumerate(g.seats)}

    log, unreachable = [], []
    for seat in sorted(g.seats):  # ← صندلی‌ها به ترتیب عددی
        uid, name = g.seats[seat]
        role = g.assigned_roles[seat]
        try:
            await ctx.bot.send_message(uid, f"🎭 نقش شما: {role}")
        except telegram.error.Forbidden:
            unreachable.append(name)
        log.append(f"{seat:>2}. {name} → {role}")  # ← فاصله‌گذاری مرتب دو رقمی

    # ارسال خلاصه برای گاد
    if g.god_id:
        text = "👑 خلاصهٔ نقش‌ها:\n" + "\n".join(log)
        if unreachable:
            text += "\n⚠️ نشد برای این افراد پیام خصوصی بفرستم: " + ", ".join(unreachable)
        await ctx.bot.send_message(g.god_id, text)


    g.phase = "playing"
    store.save()
    await publish_seating(ctx, chat_id, g, mode=CTRL)



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

    if uid in g.user_names:
        print(f"🟢 Found stored name: {g.user_names[uid]}", file=sys.stdout)
        g.seats[seat_no] = (uid, g.user_names[uid])
        store.save()
        await publish_seating(ctx, chat_id, g)
        return

    # اگر اسم ذخیره نشده بود، ازش بخواه وارد کنه
    g.awaiting_name_input[uid] = seat_no
    sent_msg = await ctx.bot.send_message(
        chat_id,
        f"✏️ نام خود را برای صندلی {seat_no} (بدون ریپلای کردن این پیام!) وارد کنید:"
    )
    g.last_name_prompt_msg_id[uid] = sent_msg.message_id  # ذخیره آیدی پیام
    store.save()



async def name_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    text = msg.text.strip()
    uid = msg.from_user.id
    chat = msg.chat.id
    g = gs(chat)

    # اگر در حال رأی‌گیری هستیم، پیام را ثبت کن
    #if g.vote_type == "counting":
       # g.vote_messages.append({
        #    "uid": msg.from_user.id,
         #   "text": (msg.text or "").strip()
        #})
        #store.save()

        #await ctx.bot.send_message(
         #   chat,
          #  f"📝 رأی دریافت شد از {msg.from_user.first_name} | متنی: {(msg.text or '').strip()}"
        #)
        #return


    # ─────────────────────────────────────────────────────────────
    # 1) راوی نام خود را وارد می‌کند
    # ─────────────────────────────────────────────────────────────
    if g.vote_type == "awaiting_god_name" and uid == g.god_id:
        g.god_name  = text
        g.vote_type = None
        store.save()
        await publish_seating(ctx, chat, g)
        return

    # ─────────────────────────────────────────────────────────────
    # 2) ثبت‌نام بازیکن با ریپلای صندلی
    # ─────────────────────────────────────────────────────────────
    if msg.reply_to_message and msg.reply_to_message.message_id == g.last_seating_msg_id:
        if text.isdigit():
            seat_no = int(text)

            if not (1 <= seat_no <= g.max_seats):
                await ctx.bot.send_message(chat, f"❌ شمارهٔ صندلی معتبر نیست.")
                return

            # اگر صندلی پر باشه
            if seat_no in g.seats:
                await ctx.bot.send_message(chat, f"❌ صندلی {seat_no} قبلاً پُر شده.")
                return

            # اگر بازیکن قبلاً ثبت‌نام کرده، جابه‌جایی کن
            existing_seat = None
            for s, (u, n) in g.seats.items():
                if u == uid:
                    existing_seat = s
                    existing_name = n
                    break

            if existing_seat is not None:
                del g.seats[existing_seat]
                g.seats[seat_no] = (uid, existing_name)
                store.save()
                await publish_seating(ctx, chat, g)
                return

            # اگر اسمش تو Gist ذخیره شده بود → ثبت فوری
            if uid in g.user_names:
                g.seats[seat_no] = (uid, g.user_names[uid])
                store.save()
                await publish_seating(ctx, chat, g)
                return

            # اگر اسم نداشت → درخواست نام
            g.waiting_name[uid] = seat_no
            msg = await ctx.bot.send_message(chat, f"👤 لطفاً نام خود را برای صندلی {seat_no} وارد کنید:")
            g.pending_name_msgs[uid] = msg.message_id
            store.save()
            return


    # ─────────────────────────────────────────────────────────────
    # 3) راوی صندلی‌ای را خالی می‌کند
    # ─────────────────────────────────────────────────────────────
    if g.vote_type == "awaiting_delete" and uid == g.god_id:
        if not text.isdigit():
            await ctx.bot.send_message(chat, "❌ فقط شمارهٔ صندلی را بنویسید.")
            return
        seat_no = int(text)
        if seat_no in g.seats:
            del g.seats[seat_no]
        g.vote_type = None
        store.save()
        await publish_seating(ctx, chat, g)
        return


    # -------------- defense threshold by God ------------------
    #if g.vote_type == "awaiting_defense_threshold" and uid == g.god_id:
        #try:
         #   threshold = int(text.strip())
        #except:
        #    await ctx.bot.send_message(chat, "❗ فقط عدد بنویسید (مثلاً: 4)")
            #return

        #qualified = [s for s, votes in g.tally.items() if len(set(votes)) >= threshold]

        #if not qualified:
        #    await ctx.bot.send_message(chat, f"❗ هیچکس {threshold} رأی یا بیشتر نیاورده.")
           # return

        # 🧹 حذف پیام سوال رأی لازم برای دفاع
            #try:
           #     await ctx.bot.delete_message(chat_id=chat, message_id=g.defense_prompt_msg_id)
          #  except:
         #       pass
        #    g.defense_prompt_msg_id = None

       # g.defense_seats = qualified
      #  g.selected_defense = []
     #   g.vote_type = None

    #    await ctx.bot.send_message(
   #         chat,
  #          f"🛡 صندلی‌هایی با {threshold} رأی: {', '.join(map(str, qualified))}"
 #       )
#
  #      await start_vote(ctx, chat, g, "final")
 #       store.save()
#        return


    # -------------- normal seat assignment ----------------
    if uid in g.waiting_name:
        seat = g.waiting_name.pop(uid)

        import re
        if not re.match(r'^[\u0600-\u06FF\s]+$', text):
            await ctx.bot.send_message(chat, "❗ لطفاً نام را فقط با حروف فارسی وارد کنید.")
            return

        g.seats[seat] = (uid, text)
        g.user_names[uid] = text  # ✅ ذخیره نام بازیکن برای استفاده‌های بعدی
        save_usernames_to_gist(g.user_names)  # 👈 ذخیره در Gist
        store.save()
        await publish_seating(ctx, chat, g)


        # حذف پیام قبلی "نام خود را وارد کنید"
        if uid in g.pending_name_msgs:
            try:
                await ctx.bot.delete_message(chat_id=chat, message_id=g.pending_name_msgs[uid])
            except:
                pass
            del g.pending_name_msgs[uid]
        return

    # -------------- God sets his own name (روش قدیمی) -----
    if uid in g.waiting_god:
        g.waiting_god.remove(uid)
        g.god_id   = uid
        g.god_name = text
        store.save()
        await publish_seating(ctx, chat, g)
        return

    # -------------- تنظیم ساعت شروع -----------------------
    if g.vote_type == "awaiting_time" and uid == g.god_id:
        g.event_time = text
        g.vote_type  = None
        store.save()
        await publish_seating(ctx, chat, g)
        return
    # -------------- اگر کاربر بدون ریپلای ولی در لیست انتظار اسم است
    if uid in g.awaiting_name_input:
        seat_no = g.awaiting_name_input.pop(uid)

        import re
        if not re.match(r'^[\u0600-\u06FF\s]+$', text):
            await ctx.bot.send_message(chat, "❗ لطفاً نام را فقط با حروف فارسی وارد کنید.")
            return

        g.seats[seat_no] = (uid, text)
        g.user_names[uid] = text  # ✅ ذخیره نام برای استفاده بعدی
        save_usernames_to_gist(g.user_names)  # ✅ ذخیره در Gist
        store.save()

        # حذف پیام راهنمای قبلی (اگه هست)
        if uid in g.last_name_prompt_msg_id:
            try:
                await ctx.bot.delete_message(
                    chat_id=chat,
                    message_id=g.last_name_prompt_msg_id[uid]
                )
            except:
                pass
            del g.last_name_prompt_msg_id[uid]

        await publish_seating(ctx, chat, g)
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


async def newgame(update: Update, ctx):
    chat = update.effective_chat.id

    if update.effective_chat.type not in {"group", "supergroup"}:
        await update.message.reply_text("این دستور فقط در گروه‌ها قابل استفاده است.")
        return


    member = await ctx.bot.get_chat_member(chat, update.effective_user.id)
    if member.status not in {"administrator", "creator"}:
        await update.message.reply_text("فقط ادمین‌های گروه می‌تونن بازی جدید شروع کنن.")
        return

    if not ctx.args:
        await update.message.reply_text("Usage: /newgame <seats>")
        return

    store.games[chat] = GameState(max_seats=int(ctx.args[0]))
    g = gs(chat)

    # 🔄 این خط رو اضافه کنید تا نام‌ها همیشه تازه باشند
    g.user_names = load_usernames_from_gist()  # بارگذاری نام‌ها از Gist
    save_usernames_to_gist(g.user_names)  # ذخیره مجدد برای اطمینان

    g.from_startgame = True
    g.awaiting_scenario = True
    g.phase = "seating"

    now = datetime.now(timezone.utc).timestamp()
    store.group_stats.setdefault(chat, {
        "waiting_list": [],
        "started": [],
        "ended": []
    })
    store.group_stats[chat]["waiting_list"].append(now)

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

# برای هندلر دستور /resetgame
async def resetgame_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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






from datetime import datetime, timezone, timedelta  # بالای فایل مطمئن شو اینا ایمپورت شدن

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
    g = gs(chat)

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

    new_god = update.message.reply_to_message.from_user
    g.god_id = new_god.id
    g.god_name = new_god.full_name
    store.save()

    await update.message.reply_text(f"✅ حالا گاد جدید بازیه {new_god.full_name}.")

    # 📢 نمایش لیست صندلی‌های به‌روز شده (با حالت مناسب)
    mode = CTRL if g.phase != "idle" else REG
    await publish_seating(ctx, chat, g, mode=mode)

    # 🔒 فقط وقتی بازی شروع شده پیام خصوصی بفرست
    if g.phase != "idle":
        log = []
        for seat in sorted(g.assigned_roles):
            role = g.assigned_roles.get(seat, "—")
            name = g.seats[seat][1]
            log.append(f"{name} ⇦ {role}")
        try:
            await ctx.bot.send_message(
                new_god.id,
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
        return  # 👈 چون کار ثبت‌نام انجام شده، بقیه اجرا نشه

    if g.awaiting_purchase_number:
        try:
            seat_no = int(text.strip())
            if seat_no not in g.seats:
                await ctx.bot.send_message(chat_id, "❌ شماره صندلی معتبر نیست.")
                return

            g.purchased_seat = seat_no
            g.awaiting_purchase_number = False

            # بر اساس temp_winner، متن برنده رو بساز
            winner_txt = {
                "winner_city": "🏙 شهر",
                "winner_mafia": "😈 مافیا"
            }.get(g.temp_winner, "❓")

            await ctx.bot.send_message(
                chat_id,
                f"🎯 صندلی {seat_no} به عنوان خریداری‌شده ثبت شد.\n🔒 برنده انتخاب شد: <b>{winner_txt}</b>\nآیا تأیید می‌کنید؟",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ تأیید", callback_data="confirm_winner")],
                    [InlineKeyboardButton("↩️ بازگشت", callback_data="back_to_winner_select")]
                ])
            )
            store.save()
        except:
            await ctx.bot.send_message(chat_id, "❌ لطفاً فقط عدد شماره صندلی را وارد کنید.")
        return



    # ثبت رأی در حالت counting
   # if g.vote_type == "counting" and g.current_vote_target and hasattr(g, "vote_start_time"):
       # delta = (datetime.now(timezone.utc) - g.vote_start_time).total_seconds()
       # if 0 <= delta <= 5:  # فقط رأی‌هایی که بین 0 تا 5 ثانیه بعد از شروع رأی‌گیری هستن
        #    g.vote_messages_by_seat.setdefault(g.current_vote_target, []).append({
       #         "uid": uid,
      #          "text": text,
     #           "target": g.current_vote_target
    #        })
   #         store.save()
  #      return  # چه ثبت بشه چه نه، کاری نکن دیگه

    # -------------- defense seats by God ------------------
    if g.vote_type == "awaiting_defense" and uid == g.god_id:
        nums = [int(n) for n in text.split() if n.isdigit() and int(n) in g.seats]
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

        # لینک یا آیدی یا "خصوصی"
        try:
            chat = await ctx.bot.get_chat(gid)
            if chat.username:
                name = f"<a href='https://t.me/{chat.username}'>{chat.title or chat.username}</a>"
            else:
                name = f"{chat.title or 'گروه خصوصی'} (<code>{gid}</code>)"
        except:
            name = f"<code>{gid}</code>"

        # وضعیت فعلی
        if g.phase == "playing":
            running_groups.append(name)
        elif g.phase == "seating":
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


async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # 👉 اضافه کردن هندلرها
    app.add_handler(CommandHandler("newgame", newgame, filters=group_filter))
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

