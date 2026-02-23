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
from telegram.error import RetryAfter, TimedOut, BadRequest
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
MAFIA_FILENAME = "mafia.json"
 

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
    votes_cast: dict[int, set[int]] | None = None
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
    remaining_cards: dict[str, list[str]] = None
    purchased_player: int | None = None
    purchase_pm_msg_id: int | None = None


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
        self.remaining_cards = self.remaining_cards or {}
        self.votes_cast = self.votes_cast or {}
        self.purchased_player = getattr(self, "purchased_player", None)
        self.purchase_pm_msg_id = getattr(self, "purchase_pm_msg_id", None)
        self.awaiting_rerandom_decision = getattr(self, "awaiting_rerandom_decision", False)
        self.rerandom_prompt_msg_id = getattr(self, "rerandom_prompt_msg_id", None)


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


def load_mafia_roles() -> set[str]:
    try:
        if not GH_TOKEN or not GIST_ID:
            print("⚠️ GH_TOKEN/GIST_ID not set; load_mafia_roles -> empty set")
            return set()
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            print("❌ load_mafia_roles failed:", r.status_code, r.text)
            return set()
        data = r.json()
        content = data["files"].get(MAFIA_FILENAME, {}).get("content", "[]")
        arr = json.loads(content) if content else []
        # رشته‌های خالی رو حذف کن
        clean = [x.strip() for x in arr if isinstance(x, str) and x.strip()]
        return set(clean)
    except Exception as e:
        print("❌ load_mafia_roles error:", e)
        return set()

def save_mafia_roles(roles: set[str]) -> bool:
    try:
        if not GH_TOKEN or not GIST_ID:
            print("⚠️ GH_TOKEN/GIST_ID not set; save_mafia_roles skipped")
            return False
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}
        payload = {
            "files": {
                MAFIA_FILENAME: {
                    "content": json.dumps(sorted(list(roles)), ensure_ascii=False, indent=2)
                }
            }
        }
        r = requests.patch(url, headers=headers, json=payload, timeout=10)
        if r.status_code not in (200, 201):
            print("❌ save_mafia_roles failed:", r.status_code, r.text)
            return False
        return True
    except Exception as e:
        print("❌ save_mafia_roles error:", e)
        return False


INDEP_FILENAME = "indep_roles.json"

def load_indep_roles() -> dict[str, list[str]]:
    try:
        if not GH_TOKEN or not GIST_ID:
            print("⚠️ GH_TOKEN/GIST_ID not set; load_indep_roles -> empty dict")
            return {}
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            print("❌ load_indep_roles failed:", r.status_code, r.text)
            return {}
        data = r.json()
        content = data["files"].get("indep_roles.json", {}).get("content", "{}")
        roles = json.loads(content) if content else {}
        return roles  # ← حالا خروجی مثل جیستت هست
    except Exception as e:
        print("❌ load_indep_roles error:", e)
        return {}


def save_indep_roles(indep: dict[str, list[str]]) -> bool:
    try:
        if not GH_TOKEN or not GIST_ID:
            return False
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}
        payload = {
            "files": {
                INDEP_FILENAME: {"content": json.dumps(indep, ensure_ascii=False, indent=2)}
            }
        }
        r = requests.patch(url, headers=headers, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print("❌ save_indep_roles error:", e)
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
            InlineKeyboardButton("❌ حذف ", callback_data=BTN_DELETE),
            InlineKeyboardButton("⏰ تغییر ساعت", callback_data="change_time"),   
        
        ],
        [
            InlineKeyboardButton("🧹 پاکسازی ", callback_data="cleanup"),
            InlineKeyboardButton("⚙️ تنظیمات", callback_data="settings_menu")
        ],
        [
            InlineKeyboardButton("↩️ لغو", callback_data="cancel_self"),
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

def settings_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("➕ سناریو جدید", callback_data="add_scenario")],
        [InlineKeyboardButton("⬅️ برگشت", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(rows)


CARDS_FILENAME = "cards.json"

def load_cards() -> dict[str, list[str]]:
    try:
        if not GH_TOKEN or not GIST_ID:
            print("⚠️ GH_TOKEN/GIST_ID not set; load_cards -> empty dict")
            return {}
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            print("❌ load_cards failed:", r.status_code, r.text)
            return {}
        data = r.json()
        content = data["files"].get(CARDS_FILENAME, {}).get("content", "{}")
        return json.loads(content) if content else {}
    except Exception as e:
        print("❌ load_cards error:", e)
        return {}

def save_cards(cards: dict[str, list[str]]) -> bool:
    try:
        if not GH_TOKEN or not GIST_ID:
            print("⚠️ GH_TOKEN/GIST_ID not set; save_cards skipped")
            return False
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}
        payload = {
            "files": {
                CARDS_FILENAME: {
                    "content": json.dumps(cards, ensure_ascii=False, indent=2)
                }
            }
        }
        r = requests.patch(url, headers=headers, json=payload, timeout=10)
        return r.status_code in (200, 201)
    except Exception as e:
        print("❌ save_cards error:", e)
        return False


# ─────────────────────────────────────────────────────────────
#  دکمه‌های کنترل راوی در حین بازی
# ─────────────────────────────────────────────────────────────
def control_keyboard(g: GameState) -> InlineKeyboardMarkup:
    rows = []

    # اگر کارت برای این سناریو وجود داشت → اول بیاد
    cards = load_cards()
    if g.scenario and g.scenario.name in cards and cards[g.scenario.name]:
        rows.append([InlineKeyboardButton("🃏 شافل کارت", callback_data="shuffle_card")])

    # بعد بقیه دکمه‌ها
    rows.extend([
        [
            InlineKeyboardButton("⚠️ اخطار", callback_data="warn_mode"),
            InlineKeyboardButton("✂️ خط‌زدن", callback_data="strike_out"),
        ],
        [
            InlineKeyboardButton("📊 وضعیت (اتومات)", callback_data="status_auto"),
            InlineKeyboardButton("📊 وضعیت (دستی)", callback_data="status_query"),
        ],
        [
            InlineKeyboardButton("🗳 رأی اولیه", callback_data="init_vote"),
            InlineKeyboardButton("🗳 رأی نهایی", callback_data="final_vote"),
        ],
        [
            InlineKeyboardButton("🛒 خریداری", callback_data="purchase_menu"),
            InlineKeyboardButton("🔁 رندوم مجدد", callback_data="rerandom_roles_confirm"),
        ],
        # Keep "end game" alone (safer)
        [InlineKeyboardButton("🏁 اتمام بازی", callback_data="end_game")],
    ])

    return InlineKeyboardMarkup(rows)

async def _delete_rerandom_prompt_after(ctx, chat_id: int, g: GameState, msg_id: int, seconds: int = 30):
    await asyncio.sleep(seconds)
    if getattr(g, "awaiting_rerandom_decision", False) and getattr(g, "rerandom_prompt_msg_id", None) == msg_id:
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
        g.awaiting_rerandom_decision = False
        g.rerandom_prompt_msg_id = None
        store.save()

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




def kb_endgame_root(g: GameState) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🏙 شهر", callback_data="winner_city")],
        [InlineKeyboardButton("😈 مافیا", callback_data="winner_mafia")],
        [InlineKeyboardButton("🏙 کلین‌شیت شهر", callback_data="clean_city")],
        [InlineKeyboardButton("😈 کلین‌شیت مافیا", callback_data="clean_mafia")],
        [InlineKeyboardButton("🏙 شهر (کی‌آس)", callback_data="winner_city_chaos")],
        [InlineKeyboardButton("😈 مافیا (کی‌آس)", callback_data="winner_mafia_chaos")],
    ]

    indep_roles = load_indep_roles()
    if g.scenario and g.scenario.name in indep_roles and indep_roles[g.scenario.name]:
        rows.append([InlineKeyboardButton("♦️ مستقل", callback_data="winner_indep")])

    rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="back_endgame")])
    return InlineKeyboardMarkup(rows)



def kb_pick_defense(g: GameState) -> InlineKeyboardMarkup:
    rows = []

    # فقط بازیکنان زنده (یعنی کسانی که در g.striked نیستن)
    alive_seats = [s for s in sorted(g.seats.keys()) if s not in g.striked]

    for s in alive_seats:
        uid, name = g.seats[s]
        label = f"{s}. {name}"  # شماره + نام بازیکن

        # اگر بازیکن انتخاب شده، ترتیب انتخاب را هم نشان بده
        if s in g.defense_selection:
            order = g.defense_selection.index(s) + 1
            label = f"{s}. {name} ({order}) ✅"

        rows.append([InlineKeyboardButton(label, callback_data=f"def_pick_{s}")])

    # دکمه‌های پایانی
    rows.append([InlineKeyboardButton("✅ تأیید", callback_data="def_confirm")])
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data="def_back")])

    return InlineKeyboardMarkup(rows)

def kb_purchase_yesno() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ بله", callback_data="purchased_yes")],
        [InlineKeyboardButton("❌ خیر", callback_data="purchased_no")],
        [InlineKeyboardButton("↩️ بازگشت", callback_data="back_to_winner_select")]
    ])

def kb_pick_purchase(alive_seats, selected=None):
    rows = []
    for s in alive_seats:
        label = f"{s} ✅" if selected == s else str(s)
        rows.append([InlineKeyboardButton(label, callback_data=f"purchase_pick_{s}")])
    rows.append([InlineKeyboardButton("✅ تأیید", callback_data="purchase_confirm")])
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data="purchase_back")])
    return InlineKeyboardMarkup(rows)

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


# ---- Concurrency / Debounce / Retry helpers ----
DEBOUNCE_EDIT_SEC = 0.15
SAVE_DEBOUNCE_SEC = 0.30

CHAT_LOCKS: dict[int, asyncio.Lock] = {}
def get_chat_lock(chat_id: int) -> asyncio.Lock:
    lock = CHAT_LOCKS.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        CHAT_LOCKS[chat_id] = lock
    return lock


_SAVE_TASK: asyncio.Task | None = None
def save_debounced():
    global _SAVE_TASK
    if _SAVE_TASK and not _SAVE_TASK.done():
        return
    async def _do():
        await asyncio.sleep(SAVE_DEBOUNCE_SEC)
        try:
            store.save()
        except Exception as e:
            print("save_debounced error:", e)
    _SAVE_TASK = asyncio.create_task(_do())

# Retry wrapper for Telegram rate limits
async def _retry(coro):
    try:
        return await coro
    except RetryAfter as e:
        await asyncio.sleep(float(getattr(e, "retry_after", 1.0)) + 0.1)
        return await coro


# ─────── تابع اصلاح‌ شده ───────────────────────────────────
async def publish_seating(
    ctx,
    chat_id: int,
    g: GameState,
    mode: str = REG,
    custom_kb: InlineKeyboardMarkup | None = None,
):
    lock = get_chat_lock(chat_id)
    async with lock:
        await asyncio.sleep(DEBOUNCE_EDIT_SEC)

        if not g.max_seats or g.max_seats <= 0:
            await _retry(ctx.bot.send_message(chat_id, "برای شروع، ادمین باید /newgame <seats> بزند."))
            return

        today = jdatetime.date.today().strftime("%Y/%m/%d")
        emoji_numbers = [
            "⓿", "➊", "➋", "➌", "➍", "➎", "➏", "➐", "➑", "➒",
            "➓", "⓫", "⓬", "⓭", "⓮", "⓯", "⓰", "⓱", "⓲", "⓳", "⓴"
        ]

        # آیدی/لینک گروه
        if not hasattr(g, "_chat_cache"):
            g._chat_cache = {}
        group_id_or_link = f"🆔 {chat_id}"
        if ctx.bot.username and chat_id < 0:
            try:
                if "username" in g._chat_cache and "title" in g._chat_cache:
                    username = g._chat_cache["username"]
                    title = g._chat_cache["title"]
                else:
                    chat_obj = await _retry(ctx.bot.get_chat(chat_id))
                    username = getattr(chat_obj, "username", None)
                    title = getattr(chat_obj, "title", None)
                    g._chat_cache["username"] = username
                    g._chat_cache["title"] = title

                if username:
                    group_id_or_link = f"🔗 <a href='https://t.me/{username}'>{title}</a>"
                elif title:
                    group_id_or_link = f"🔒 {title}"
            except Exception:
                pass

        # متن اصلی
        lines = [
            f"{group_id_or_link}",
            "♚🎭 <b>رویداد مافیا</b>",
            f"♚📆 <b>تاریخ:</b> {today}",
            f"♚🕰 <b>زمان:</b> {g.event_time or '---'}",
            f"♚🎩 <b>راوی:</b> <a href='tg://user?id={g.god_id}'>{g.god_name or '❓'}</a>",
        ]

        event_num = int(get_event_numbers().get(str(chat_id), 1))
        lines.insert(1, f"♚🎯 <b>شماره رویداد:</b> {event_num}")

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

                if i in g.striked:
                    txt += " ❌☠️"

                line = f"♚{i}  {txt}"
            else:
                line = f"♚{i} ⬜ /{i}"
            lines.append(line)

        # استعلام وضعیت
        if g.status_counts.get("citizen", 0) > 0 or g.status_counts.get("mafia", 0) > 0:
            c = g.status_counts.get("citizen", 0)
            m = g.status_counts.get("mafia", 0)
            lines.append(f"\n🧾 <i>استعلام وضعیت: {c} شهروند و {m} مافیا</i>")

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
                kb = delete_button_markup(g)
            elif mode == "warn":
                kb = warn_button_markup_plusminus(g)
            else:
                kb = control_keyboard(g)

        # --- ذخیره اسنپ‌شات آخرین لیست برای بازیابی با /lists ---
        try:
            g.last_snapshot = {
                "text": text,
                "kb": kb.to_dict(),  # کیبورد رو به dict ذخیره می‌کنیم
            }
            store.save()
        except Exception as e:
            print("⚠️ snapshot save error:", e)
        # پیام لیست
        try:
            if g.last_seating_msg_id:
                try:
                    await _retry(ctx.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=g.last_seating_msg_id,
                        text=text,
                        parse_mode="HTML",
                        reply_markup=kb,
                    ))
                except BadRequest as e:
                    s = str(e)
                    if "message is not modified" in s:
                        try:
                            await _retry(ctx.bot.edit_message_reply_markup(
                                chat_id=chat_id,
                                message_id=g.last_seating_msg_id,
                                reply_markup=kb
                            ))
                        except BadRequest as e2:
                            if "message is not modified" in str(e2):
                                pass
                            else:
                                raise
                    else:
                        raise
            else:
                msg = await _retry(ctx.bot.send_message(
                    chat_id,
                    text,
                    parse_mode="HTML",
                    reply_markup=kb
                ))
                g.last_seating_msg_id = msg.message_id
                if chat_id < 0:
                    try:
                        await _retry(ctx.bot.pin_chat_message(
                            chat_id,
                            msg.message_id,
                            disable_notification=True
                        ))
                    except Exception:
                        pass
        except Exception:
            old_msg_id = g.last_seating_msg_id
            msg = await _retry(ctx.bot.send_message(
                chat_id,
                text,
                parse_mode="HTML",
                reply_markup=kb
            ))
            g.last_seating_msg_id = msg.message_id

            if chat_id < 0:
                try:
                    await _retry(ctx.bot.pin_chat_message(
                        chat_id,
                        msg.message_id,
                        disable_notification=True
                    ))
                except Exception:
                    pass

            if old_msg_id:
                try:
                    await ctx.bot.delete_message(chat_id, old_msg_id)
                except Exception:
                    pass

  
        # لیست نقش‌ها
        if g.scenario and mode == REG:
            if getattr(g, "last_roles_scenario_name", None) != g.scenario.name:
                mafia_roles = load_mafia_roles()
                indep_roles = load_indep_roles()
                indep_for_this = indep_roles.get(g.scenario.name, [])
                mafia_lines = ["<b>نقش‌های مافیا:</b>"]
                citizen_lines = ["<b>نقش‌های شهروند:</b>"]
                indep_lines = ["<b>نقش‌های مستقل:</b>"]

                for role, count in g.scenario.roles.items():
                    for _ in range(count):
                        if role in mafia_roles:
                            mafia_lines.append(f"♠️ {role}")
                        elif role in indep_for_this:
                            indep_lines.append(f"♦️ {role}")
                        else:
                            citizen_lines.append(f"♥️ {role}")

                role_lines = ["📜 <b>لیست نقش‌های سناریو:</b>\n"]
                role_lines.extend(mafia_lines)
                role_lines.append("")
                role_lines.extend(citizen_lines)
                if len(indep_lines) > 1:  # یعنی حداقل یک نقش مستقل هست
                    role_lines.append("")
                    role_lines.extend(indep_lines)

                role_text = "\n".join(role_lines)

                try:
                    if getattr(g, "last_roles_msg_id", None):
                        try:
                            await _retry(ctx.bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=g.last_roles_msg_id,
                                text=role_text,
                                parse_mode="HTML",
                            ))
                        except BadRequest as e:
                            if "message is not modified" in str(e):
                                pass
                            else:
                                raise
                    else:
                        role_msg = await _retry(
                            ctx.bot.send_message(chat_id, role_text, parse_mode="HTML")
                        )
                        g.last_roles_msg_id = role_msg.message_id
                except Exception:
                    role_msg = await _retry(
                        ctx.bot.send_message(chat_id, role_text, parse_mode="HTML")
                    )
                    g.last_roles_msg_id = role_msg.message_id

                g.last_roles_scenario_name = g.scenario.name


        save_debounced()


# ─────────────────────────────────────────────────────────────
#  رأی‌گیری (همان نسخهٔ قبلی؛ فقط دست نزدیم)
# ─────────────────────────────────────────────────────────────
async def start_vote(ctx, chat_id: int, g: GameState, stage: str):
    g.vote_stage = stage
    g.tally = {}
    g.current_target = None
    g.collecting = False

    candidates = g.defense_seats if stage == "final" else list(g.seats.keys())
    g.vote_candidates = [s for s in candidates if s not in g.striked]
    if stage != "final":
        g.vote_candidates = sorted(g.vote_candidates)

    btns = []
    for s in g.vote_candidates:
        name = g.seats[s][1]
        label = f"✅ {s}. {name}" if s in getattr(g, "voted_targets", set()) else f"{s}. {name}"
        btns.append([InlineKeyboardButton(label, callback_data=f"vote_{s}")])

    if stage == "initial_vote":
        btns.append([InlineKeyboardButton("🧹 پاک کردن رأی‌گیری", callback_data="clear_vote_initial")])
        btns.append([InlineKeyboardButton("✅ پایان رأی‌گیری", callback_data="vote_done_initial")])
    else:  # final
        btns.append([InlineKeyboardButton("🧹 پاک کردن رأی‌گیری", callback_data="clear_vote_final")])
        btns.append([InlineKeyboardButton("✅ پایان رأی‌گیری", callback_data="vote_done_final")])

    back_code = "back_vote_init" if stage == "initial_vote" else "back_vote_final"
    btns.append([InlineKeyboardButton("⬅️ بازگشت", callback_data=back_code)])

    title = "🗳 رأی‌گیری اولیه – انتخاب هدف:" if stage == "initial_vote" else "🗳 رأی‌گیری نهایی – انتخاب حذف:"
    msg = await ctx.bot.send_message(chat_id, title, reply_markup=InlineKeyboardMarkup(btns))

    g.vote_msg_id = msg.message_id

    if stage == "initial_vote":
        g.first_vote_msg_id_initial = msg.message_id
        g.last_vote_msg_id_initial = msg.message_id
    elif stage == "final":
        g.first_vote_msg_id_final = msg.message_id
        g.last_vote_msg_id_final = msg.message_id

    store.save()


async def update_vote_buttons(ctx, chat_id: int, g: GameState):
    btns = []
    for s in g.vote_candidates:
        name = g.seats[s][1]
        label = f"✅ {s}. {name}" if s in getattr(g, "voted_targets", set()) else f"{s}. {name}"
        btns.append([InlineKeyboardButton(label, callback_data=f"vote_{s}")])

    if g.vote_stage == "initial_vote":
        btns.append([InlineKeyboardButton("🧹 پاک کردن رأی‌گیری", callback_data="clear_vote_initial")])
        btns.append([InlineKeyboardButton("✅ پایان رأی‌گیری", callback_data="vote_done_initial")])
        btns.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="back_vote_init")])
    elif g.vote_stage == "final":
        btns.append([InlineKeyboardButton("🧹 پاک کردن رأی‌گیری", callback_data="clear_vote_final")])
        btns.append([InlineKeyboardButton("✅ پایان رأی‌گیری", callback_data="vote_done_final")])
        btns.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="back_vote_final")])

    try:
        await ctx.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=g.vote_msg_id,  # 📌 فقط روی پیام دکمه‌های اصلی
            reply_markup=InlineKeyboardMarkup(btns)
        )
    except:
        pass


async def handle_vote(ctx, chat_id: int, g: GameState, target_seat: int):
    g.current_vote_target = target_seat

    start_time = datetime.now().timestamp()
    end_time = start_time + 4.3
    g.vote_window = (start_time, end_time, target_seat)

    g.vote_collecting = True
    g.votes_cast.setdefault(target_seat, set())
    g.vote_logs.setdefault(target_seat, [])

    if not hasattr(g, "vote_order"):
        g.vote_order = []
    g.vote_order.append(target_seat)

    if not hasattr(g, "vote_cleanup_ids"):
        g.vote_cleanup_ids = []

    store.save()

    msg = await ctx.bot.send_message(
        chat_id,
        f"⏳ رأی‌گیری برای <b>{target_seat}. {g.seats[target_seat][1]}</b>",
        parse_mode="HTML"
    )
    g.vote_cleanup_ids.append(msg.message_id)

    await asyncio.sleep(4)

    g.vote_collecting = False
    end_msg = await ctx.bot.send_message(chat_id, "🛑 تمام", parse_mode="HTML")

    if g.vote_stage == "initial_vote":
        g.last_vote_msg_id_initial = end_msg.message_id
    elif g.vote_stage == "final":
        g.last_vote_msg_id_final = end_msg.message_id

    g.voted_targets.add(target_seat)
    await update_vote_buttons(ctx, chat_id, g)
    store.save()









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

    mafia_roles = load_mafia_roles()
    indep_roles = load_indep_roles()
    indep_for_this = indep_roles.get(g.scenario.name, [])

    for seat in sorted(g.seats):
        uid, name = g.seats[seat]
        role = g.assigned_roles.get(seat, "—")

        # انتخاب مارکر بر اساس نقش
        if getattr(g, "purchased_seat", None) == seat or getattr(g, "purchased_player", None) == seat:
            role_display = f"{role} / مافیا"
            marker = "◾️"  # خریداری شده → مافیا
        elif role in mafia_roles:
            marker = "◾️"  # مافیا
            role_display = role
        elif role in indep_for_this:
            marker = "♦️"  # مستقل
            role_display = role
                
        else:
            marker = "◽️"  # شهروند
            role_display = role

        chaos_mark = " 🔸" if getattr(g, "chaos_selected", set()) and seat in g.chaos_selected else ""

        lines.append(
            f"░⚜️{marker}{seat}- <a href='tg://user?id={uid}'>{name}</a> ⇦ {role_display}{chaos_mark}"
        )

    lines.append("")
    result_line = f"🏆 نتیجه بازی: برد {g.winner_side}"
    if getattr(g, "clean_win", False):
        result_line += " (کلین‌شیت)"
    if getattr(g, "chaos_mode", False):
        result_line += " (کی‌آس)"
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



async def cleanup_after(ctx, chat_id: int, from_message_id: int, stop_message_id: int | None = None):

    try:
        
        if stop_message_id:
            limit = stop_message_id
        else:
            
            limit = from_message_id + 5000

        batch = []
        for msg_id in range(from_message_id + 1, limit):
            batch.append(msg_id)
            if len(batch) == 100:  # هر 100 تا
                for mid in batch:
                    try:
                        await ctx.bot.delete_message(chat_id, mid)
                    except Exception:
                        pass
                batch = []
                await asyncio.sleep(1)  # جلوگیری از FloodLimit

        # باقی‌مانده
        for mid in batch:
            try:
                await ctx.bot.delete_message(chat_id, mid)
            except Exception:
                pass

    except Exception as e:
        print(f"⚠️ cleanup_after error: {e}")




# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
#  CALL-BACK ROUTER – نسخهٔ کامل با فاصله‌گذاری درست
# ─────────────────────────────────────────────────────────────
async def callback_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # 🔹 جلوگیری از اجرای کال‌بک‌ها در پی‌وی مگر برای راوی در حالت خریداری
    if update.effective_chat.type == "private":
        q = update.callback_query
        data = q.data if q else None
        uid = q.from_user.id

        # 🟢 پیدا کردن گیمی که راوی‌اش همین uid است
        g = None
        chat = None
        for chat_id, game in store.games.items():
            if game.god_id == uid and game.phase in ("playing", "awaiting_winner"):
                g = game
                chat = chat_id
                break

        # ❌ اگر بازی پیدا نشد یا دکمه مربوط به خریداری نیست → خروج
        if not (g and data and data.startswith("purchase_")):
            return
    else:
        # 🟢 در گروه‌ها (غیر پی‌وی)
        q = update.callback_query
        data = q.data
        chat = q.message.chat.id
        uid = q.from_user.id
        g = gs(chat)

    await safe_q_answer(q)

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


    if data == "settings_menu" and uid == g.god_id:
        await ctx.bot.edit_message_reply_markup(
            chat_id=chat,
            message_id=g.last_seating_msg_id,
            reply_markup=settings_keyboard()
        )
        return

    if data == "back_to_main" and uid == g.god_id:
        await ctx.bot.edit_message_reply_markup(
            chat_id=chat,
            message_id=g.last_seating_msg_id,
            reply_markup=text_seating_keyboard(g)
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
            await ctx.bot.send_message(chat, "⚠️ فقط راوی می‌تواند بازی را شروع کند!")
            return

        if not g.awaiting_shuffle_decision:
            return

        g.awaiting_shuffle_decision = False
        g.from_startgame = False
        store.save()

        prompt_id = g.shuffle_prompt_msg_id 
        if prompt_id:
            try:
                await ctx.bot.delete_message(chat, prompt_id)
            except:
                pass
            g.shuffle_prompt_msg_id = None

        repeats = getattr(g, "shuffle_repeats", None) or 1

        # اول نقش‌ها داده شود
        await shuffle_and_assign(
            ctx,
            chat,
            g,
            shuffle_seats=True,
            uid_to_role=g.preview_uid_to_role or None,
            notify_players=True,
            preview_mode=False,
            role_shuffle_repeats=repeats,
        )


        g.preview_uid_to_role = None
        g.shuffle_repeats = None
        store.save()
        return


    if data == "shuffle_no":
        if uid != g.god_id:
            await ctx.bot.send_message(chat, "⚠️ فقط راوی می‌تواند بازی را شروع کند!")
            return

        if not g.awaiting_shuffle_decision:
            return

        g.awaiting_shuffle_decision = False
        g.from_startgame = False
        store.save()

        prompt_id = g.shuffle_prompt_msg_id  
        if prompt_id:
            try:
                await ctx.bot.delete_message(chat, prompt_id)
            except:
                pass
            g.shuffle_prompt_msg_id = None

        repeats = getattr(g, "shuffle_repeats", None) or 1

        # اول نقش‌ها داده شود
        await shuffle_and_assign(
            ctx,
            chat,
            g,
            shuffle_seats=False,
            uid_to_role=g.preview_uid_to_role or None,
            notify_players=True,
            preview_mode=False,
            role_shuffle_repeats=repeats,
        )

        g.preview_uid_to_role = None
        g.shuffle_repeats = None
        store.save()
        return

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

 


    # ─── خریداری – شروع در پی‌وی گاد ─────────────────────────────
    if data == "purchase_menu" and uid == g.god_id:
        alive = [s for s in sorted(g.seats) if s not in g.striked]
        g.purchased_player = None
        store.save()

        kb = kb_pick_purchase(alive, None)

        try:
            msg = await ctx.bot.send_message(
                g.god_id,
                "🛍 بازیکنی را که خریداری شده انتخاب کن:",
                reply_markup=kb
            )
            g.purchase_pm_msg_id = msg.message_id
            store.save()
        except Exception:
            await ctx.bot.send_message(
                chat,
                "⚠️ بات نمی‌تواند به پی‌وی راوی پیام بفرستد. ابتدا بات را استارت کن."
            )
        return
    # ─── انتخاب بازیکن خریداری‌شده (در پی‌وی گاد) ───────────────
    if data.startswith("purchase_pick_") and uid == g.god_id:
        try:
            s = int(data.split("_")[2])
        except:
            return

        alive = [x for x in sorted(g.seats) if x not in g.striked]
        if s not in alive:
            await ctx.bot.send_message(uid, "⚠️ بازیکن انتخاب‌شده زنده نیست.")
            return

        g.purchased_player = s
        store.save()

        try:
            await ctx.bot.edit_message_reply_markup(
                chat_id=uid,
                message_id=g.purchase_pm_msg_id,
                reply_markup=kb_pick_purchase(alive, s)
            )
        except Exception:
            pass
        return

    # ─── تأیید خریداری ─────────────────────────────────────────
    if data == "purchase_confirm" and uid == g.god_id:
        if not g.purchased_player:
            await ctx.bot.send_message(uid, "⚠️ هنوز بازیکنی انتخاب نشده است.")
            return

        seat = g.purchased_player
        uid_target, name_target = g.seats[seat]

        try:
            await ctx.bot.send_message(uid_target, "💰 شما خریداری شده‌اید!")
            await ctx.bot.send_message(uid, f"✅ {seat}. {name_target} خریداری شد.")
        except Exception:
            await ctx.bot.send_message(
                uid,
                f"⚠️ {seat}. {name_target} هنوز بات را استارت نکرده یا پیام دریافت نمی‌کند."
            )

        # پاک کردن پیام انتخاب در پی‌وی گاد
        try:
            if g.purchase_pm_msg_id:
                await ctx.bot.delete_message(uid, g.purchase_pm_msg_id)
                g.purchase_pm_msg_id = None
        except Exception:
            pass

        store.save()
        return

    # ─── بازگشت از خریداری ─────────────────────────────────────
    if data == "purchase_back" and uid == g.god_id:
        try:
            if g.purchase_pm_msg_id:
                await ctx.bot.delete_message(uid, g.purchase_pm_msg_id)
                g.purchase_pm_msg_id = None
        except Exception:
            pass

        g.purchased_player = None
        store.save()
        await ctx.bot.send_message(uid, "↩️ عملیات خریداری لغو شد.")
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

        await set_hint_and_kb(ctx, chat, g, "برنده را انتخاب کنید.", kb_endgame_root(g))
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
        "winner_city_chaos", "winner_mafia_chaos", "winner_indep"
    } and g.awaiting_winner:
        g.temp_winner = data
        g.chaos_mode = data.endswith("_chaos")
        store.save()

        if data == "winner_indep":
            # مستقل → مستقیم تأیید (بدون خریداری یا کی‌آس)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأیید", callback_data="confirm_winner")],
                [InlineKeyboardButton("↩️ بازگشت", callback_data="back_to_winner_select")],
            ])
            await set_hint_and_kb(
                ctx, chat, g,
                "🔒 نقش مستقل انتخاب شد. برای نهایی‌سازی «تأیید» را بزنید.",
                kb
            )
            return

        # کلین‌شیت → مستقیماً تأیید نهایی
        if data in {"clean_city", "clean_mafia"}:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأیید", callback_data="confirm_winner")],
                [InlineKeyboardButton("↩️ بازگشت", callback_data="back_to_winner_select")],
            ])
            await set_hint_and_kb(
                ctx, chat, g,
                "نتیجه را بررسی کنید و «تأیید» را بزنید.",
                kb
            )
            return

        # حالت معمولی (بدون chaos)
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

        # ────────────────────────────────────────────────
        # 🔹 حالت کی‌آس: انتخاب ۳ بازیکن زنده
        # ────────────────────────────────────────────────
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
            kb_endgame_root(g)
        )
        return


    if data == "confirm_winner" and uid == g.god_id and getattr(g, "temp_winner", None):
        g.awaiting_winner = False

        if g.temp_winner == "winner_indep":
            g.winner_side = "مستقل"
            g.clean_win = False
        else:
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
    if data == "vote_done_initial" and uid == g.god_id:
        await ctx.bot.send_message(chat, "✅ رأی‌گیری اولیه تمام شد.")
        g.votes_cast = {}
        g.vote_logs = {}
        g.current_vote_target = None
        g.vote_has_ended_initial = True
        g.vote_order = []
        store.save()
        return

    if data == "vote_done_final" and uid == g.god_id:
        await ctx.bot.send_message(chat, "✅ رأی‌گیری نهایی تمام شد.")
        g.votes_cast = {}
        g.vote_logs = {}
        g.current_vote_target = None
        g.vote_has_ended_final = True
        g.vote_order = []
        store.save()
        return


    if data == "clear_vote_initial" and uid == g.god_id:
        if not getattr(g, "vote_has_ended_initial", False):
            await ctx.bot.send_message(chat, "⚠️ ابتدا باید رأی‌گیری پایان یابد.")
            return
        first_id = getattr(g, "first_vote_msg_id_initial", None)
        last_id  = getattr(g, "last_vote_msg_id_initial", None)
        if first_id and last_id:
            for mid in range(first_id, last_id + 1):
                try:
                    await ctx.bot.delete_message(chat_id=chat, message_id=mid)
                except:
                    pass
        await ctx.bot.send_message(chat, "🧹 رأی‌گیری اولیه پاک شد.")
        return
    if data == "clear_vote_final" and uid == g.god_id:
        if not getattr(g, "vote_has_ended_final", False):
            await ctx.bot.send_message(chat, "⚠️ ابتدا باید رأی‌گیری پایان یابد.")
            return
        first_id = getattr(g, "first_vote_msg_id_final", None)
        last_id  = getattr(g, "last_vote_msg_id_final", None)
        if first_id and last_id:
            for mid in range(first_id, last_id + 1):
                try:
                    await ctx.bot.delete_message(chat_id=chat, message_id=mid)
                except:
                    pass
        await ctx.bot.send_message(chat, "🧹 رأی‌گیری نهایی پاک شد.")
        return
    # ────────────────────────────────────────────────────────────
    #  کارت
    # ────────────────────────────────────────────────────────────

    if data == "shuffle_card":
        if uid != g.god_id:
            await ctx.bot.send_message(chat, "⛔ فقط راوی می‌تواند کارت بکشد!")
            return

        cards = load_cards()
        scn = g.scenario.name if g.scenario else None
        if not scn or scn not in cards:
            await ctx.bot.send_message(chat, "❌ برای این سناریو کارتی تعریف نشده.")
            return

        deck = g.remaining_cards.get(scn, cards[scn].copy())

        if not deck:
            await ctx.bot.send_message(chat, "🃏 همه کارت‌ها مصرف شدند.")
            return

        choice = random.choice(deck)
        deck.remove(choice)
        g.remaining_cards[scn] = deck
        store.save()

        await ctx.bot.send_message(chat, f"🃏 کارت انتخاب‌شده:\n<b>{choice}</b>", parse_mode="HTML")
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

    if data == "cleanup" and uid == g.god_id:
        if g.last_seating_msg_id:
            stop_id = g.shuffle_prompt_msg_id or None
            asyncio.create_task(
                cleanup_after(ctx, chat, g.last_seating_msg_id, stop_id)
            )
            await ctx.bot.send_message(chat, "🧹 درحال پاکسازی پیام‌ها (در پس‌زمینه)...")
        else:
            await ctx.bot.send_message(chat, "⚠️ لیست بازیکنان مشخص نیست، پاکسازی انجام نشد.")
        return


    if data == "add_scenario" and (uid == g.god_id or uid in g.admins):
        g.adding_scenario_step = "name"
        g.adding_scenario_data = {}
        g.adding_scenario_last = datetime.now()
        store.save()
        await ctx.bot.send_message(chat, "📝 نام سناریوی جدید را بفرستید (۵ دقیقه فرصت دارید).")
        return

    # ─── رأی‌گیری‌ها ────────────────────────────────────────────
    if data == "init_vote":
        if uid != g.god_id:
            await ctx.bot.send_message(chat, "⚠️ فقط راوی می‌تواند رأی‌گیری را شروع کند!")
            return

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗳 پل", callback_data="init_vote_poll")],
            [InlineKeyboardButton("🗳 تک تک", callback_data="init_vote_classic")],
            [InlineKeyboardButton("↩️ بازگشت", callback_data="back_to_controls")],
        ])
        await set_hint_and_kb(ctx, chat, g, "روش رأی‌گیری اولیه را انتخاب کنید:", kb)
        return

    if data == "init_vote_classic" and uid == g.god_id:
        g.votes_cast = {}
        g.vote_logs = {}
        g.current_vote_target = None
        g.voted_targets = set()
        store.save()

        await set_hint_and_kb(ctx, chat, g, None, control_keyboard(g), mode=CTRL)
        await start_vote(ctx, chat, g, "initial_vote")
        return

    if data == "init_vote_poll" and uid == g.god_id:
        await set_hint_and_kb(ctx, chat, g, None, control_keyboard(g), mode=CTRL)

        alive = [s for s in sorted(g.seats) if s not in g.striked]
        options = [f"{s}. {g.seats[s][1]}" for s in alive]
        max_per_poll = 9  # حداکثر بازیکن در هر poll (۱۰مین گزینه برای "دیدن نتایج")

        # تقسیم گزینه‌ها به چند poll هر 9 نفر
        chunks = [options[i:i + max_per_poll] for i in range(0, len(options), max_per_poll)]

        total_polls = len(chunks)
        if total_polls == 0:
            await ctx.bot.send_message(chat, "⚠️ هیچ بازیکنی برای رأی‌گیری وجود ندارد.")
            return

        poll_ids = []

        # --- مرحله ۱: ارسال همه pollها پشت‌سر‌هم ---
        for idx, chunk in enumerate(chunks, start=1):
            # افزودن گزینه‌ی نتایج برای هر poll
            chunk.append(f"📊 دیدن نتایج ({idx}/{total_polls})")

            try:
                poll_msg = await ctx.bot.send_poll(
                    chat_id=chat,
                    question=f"🗳 رأی‌گیری اولیه – بخش {idx}/{total_polls}",
                    options=chunk,
                    is_anonymous=False,
                    allows_multiple_answers=True
                )
                poll_ids.append(poll_msg.message_id)
                g.last_poll_ids = getattr(g, "last_poll_ids", []) + [poll_msg.message_id]
                store.save()

            except Exception as e:
                print(f"❌ poll send error (part {idx}):", e)

        # --- مرحله ۲: مکث برای رأی دادن، سپس بستن همه pollها ---
        await asyncio.sleep(10)

        for idx, poll_id in enumerate(poll_ids, start=1):
            try:
                await ctx.bot.stop_poll(chat_id=chat, message_id=poll_id)
            except Exception as e:
                print(f"⚠️ stop_poll error (part {idx}):", e)

        await ctx.bot.send_message(chat, f"✅ {total_polls} رأی‌گیری بسته شد.")
        return


    if data == "back_to_controls" and uid == g.god_id:
        await set_hint_and_kb(ctx, chat, g, None, control_keyboard(g), mode=CTRL)
        return

        return


    if data == "back_vote_init" and uid == g.god_id:
        g.phase = "voting_selection"
        g.voted_targets = set()  # 🧹 پاک کردن صندلی‌های قبلاً رأی‌گیری‌شده
        store.save()
        await ctx.bot.send_message(chat, "↩️ مجدداً کاندید رأی‌گیری را انتخاب کنید.")
        await start_vote(ctx, chat, g, "initial_vote")
        return


    # ─── رأی‌گیری نهایی: انتخاب دفاع با دکمه ─────────────────────────────
    if data == "final_vote" and uid == g.god_id:
        g.votes_cast = {}
        g.vote_logs = {}
        g.current_vote_target = None
        g.voted_targets = set()
        store.save()

        # آماده‌سازی مرحله انتخاب دفاع
        g.vote_type = "awaiting_defense"
        g.defense_selection = []  # ترتیب انتخاب ذخیره میشه
        store.save()

        await set_hint_and_kb(
            ctx, chat, g,
            "🧍 صندلی‌های دفاع را انتخاب کنید و سپس «تأیید» را بزنید:",
            kb_pick_defense(g)
        )
        return

    # ─── انتخاب صندلی دفاع ────────────────────────────────────────────────
    if data.startswith("def_pick_") and uid == g.god_id and g.vote_type == "awaiting_defense":
        try:
            seat = int(data.split("_")[2])
        except Exception:
            return

        # انتخاب/حذف صندلی با حفظ ترتیب
        if seat in g.defense_selection:
            g.defense_selection.remove(seat)
        else:
            g.defense_selection.append(seat)

        store.save()
        await set_hint_and_kb(
            ctx, chat, g,
            "🧍 صندلی‌های دفاع را انتخاب کنید و سپس «تأیید» را بزنید:",
            kb_pick_defense(g)
        )
        return

    # ─── تأیید انتخاب صندلی‌های دفاع ─────────────────────────────────────
    if data == "def_confirm" and uid == g.god_id and g.vote_type == "awaiting_defense":
        if not g.defense_selection:
            await safe_q_answer(q, "حداقل یک صندلی را انتخاب کن!", show_alert=True)
            return

        g.defense_seats = list(g.defense_selection)
        g.vote_type = "defense_selected"
        store.save()

        await ctx.bot.send_message(
            chat,
            f"🛡 صندلی‌های دفاع: {'، '.join(map(str, g.defense_seats))}"
        )

        # رفتن به مرحله رأی‌گیری نهایی (به‌ترتیب انتخاب گاد)
        await start_vote(ctx, chat, g, "final")
        await publish_seating(ctx, chat, g, mode=CTRL)
        return

    # ─── بازگشت از انتخاب دفاع ───────────────────────────────────────────
    if data == "def_back" and uid == g.god_id and g.vote_type == "awaiting_defense":
        g.vote_type = None
        g.defense_selection = []
        store.save()
        await publish_seating(ctx, chat, g, mode=CTRL)
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

    if data == "status_auto":
        if uid != g.god_id:
            await ctx.bot.send_message(chat, "⚠️ فقط راوی می‌تواند استعلام وضعیت بگیرد!")
            return

        mafia_roles = load_mafia_roles()
        dead_seats = [s for s in g.striked]  
        mafia_count = 0
        citizen_count = 0

        for s in dead_seats:
            role = g.assigned_roles.get(s)
            if role and role in mafia_roles:
                mafia_count += 1
            else:
                citizen_count += 1

        # ذخیره برای نمایش در لیست
        g.status_counts = {"citizen": citizen_count, "mafia": mafia_count}
        g.status_mode = False
        store.save()

        await ctx.bot.send_message(
            chat,
            f"📢 استعلام وضعیت :\n {citizen_count} شهروند\n {mafia_count} مافیا"
        )
        await publish_seating(ctx, chat, g, mode=CTRL)
        return

    if data == "back_vote_final" and uid == g.god_id:
        # 🔹 حذف دکمه‌های فعلی از پیام رأی‌گیری نهایی
        try:
            if hasattr(g, "last_vote_msg_id_final") and g.last_vote_msg_id_final:
                await ctx.bot.edit_message_reply_markup(
                    chat_id=chat,
                    message_id=g.last_vote_msg_id_final,
                    reply_markup=None
                )
                g.last_vote_msg_id_final = None
        except Exception as e:
            print(f"⚠️ error clearing final vote buttons: {e}")

        # 🔹 ارسال پیام ساده برای اطلاع
        await ctx.bot.send_message(
            chat,
            "↩️ دکمه‌های رأی‌گیری نهایی حذف شدند. راوی می‌تواند دوباره صندلی‌های دفاع را انتخاب کند."
        )

        # 🔹 پاک کردن حالت رأی‌گیری از حافظه
        g.phase = "defense_selection"
        g.vote_type = None
        g.voted_targets = set()
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
    
    if data == "rerandom_roles_confirm":
        if uid != g.god_id:
        #    await ctx.bot.send_message(chat, "⚠️ فقط راوی می‌تواند رندوم مجدد انجام دهد!")
            return

        #if not g.scenario or len(g.seats) != g.max_seats:
        #    await ctx.bot.send_message(chat, "⚠️ ابتدا سناریو انتخاب و همه صندلی‌ها پُر شوند.")
        #    return

        # اگر وسط انتخاب برنده هستی، بهتره اجازه ندی (اختیاری ولی منطقیه)
        if g.phase == "awaiting_winner":
            await ctx.bot.send_message(chat, "⚠️ در حالت انتخاب برنده نمی‌شود رندوم مجدد کرد.")
            return

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ بله", callback_data="rerandom_roles_yes"),
            InlineKeyboardButton("❌ خیر", callback_data="rerandom_roles_no"),
        ]])

        msg = await ctx.bot.send_message(chat, "❓ آیا تمایل به رندوم مجدد دارید؟", reply_markup=kb)

        g.awaiting_rerandom_decision = True
        g.rerandom_prompt_msg_id = msg.message_id
        store.save()

        asyncio.create_task(_delete_rerandom_prompt_after(ctx, chat, g, msg.message_id, 30))
        return
    if data == "rerandom_roles_no":
        if uid != g.god_id:
            return
        if not getattr(g, "awaiting_rerandom_decision", False):
            return

        prompt_id = getattr(g, "rerandom_prompt_msg_id", None)
        if prompt_id:
            try:
                await ctx.bot.delete_message(chat, prompt_id)
            except Exception:
                pass

        g.awaiting_rerandom_decision = False
        g.rerandom_prompt_msg_id = None
        store.save()
        return
    if data == "rerandom_roles_yes":
        if uid != g.god_id:
            return
        if not getattr(g, "awaiting_rerandom_decision", False):
            return

        prompt_id = getattr(g, "rerandom_prompt_msg_id", None)
        if prompt_id:
            try:
                await ctx.bot.delete_message(chat, prompt_id)
            except Exception:
                pass

        g.awaiting_rerandom_decision = False
        g.rerandom_prompt_msg_id = None
        store.save()

        # ✅ رندوم مجدد نقش‌ها بدون شافل صندلی‌ها
        await shuffle_and_assign(
            ctx,
            chat,
            g,
            shuffle_seats=False,
            uid_to_role=None,
            notify_players=True,
            preview_mode=False,
            role_shuffle_repeats=5,
        )

        # برای اینکه UI هم آپدیت بماند (اختیاری ولی بهتر):
        await publish_seating(ctx, chat, g, mode=CTRL)
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
        log.append(f"{seat:>2}. <a href='tg://user?id={uid}'>{name}</a> → {role}")


    if g.god_id:
        text = "👑 خلاصهٔ نقش‌ها:\n" + "\n".join(log)
        if unreachable:
            text += "\n⚠️ نشد برای این افراد پیام بفرستم: " + ", ".join(unreachable)
        try:
            await ctx.bot.send_message(g.god_id, text, parse_mode="HTML")
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

    if g.phase == "idle" and text.strip() == "کنسل":
        for seat, (player_uid, _) in list(g.seats.items()):
            if player_uid == uid:
                del g.seats[seat]
                store.save()
                await ctx.bot.send_message(chat_id, "❎ ثبت‌نام شما با موفقیت لغو شد.")
                await publish_seating(ctx, chat_id, g)
                break
        else:
            await ctx.bot.send_message(chat_id, "❗ شما در لیست نیستید.")
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
        mode = CTRL if g.phase != "idle" else REG
        await publish_seating(ctx, chat_id, g, mode=mode)
 

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

async def play_alarm_sound(ctx, chat_id: int):
    try:
        msg = await ctx.bot.send_voice(
            chat_id,
            voice="https://files.catbox.moe/4f8tem.ogg"
        )

        await ctx.bot.send_message(
            chat_id,
            "پخش",
            reply_to_message_id=msg.message_id
        )

    except Exception as e:
        print("⚠️ play_alarm_sound error:", e)


async def dynamic_timer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    uid = update.effective_user.id
    g = gs(chat)

    # جلوگیری از اجرای تایمر روی پیام‌های قدیمی
    if (datetime.now(timezone.utc) - update.message.date).total_seconds() > 10:
        return  

    if uid != g.god_id:
        await update.message.reply_text("⛔ فقط گاد می‌تونه تایمر بزنه.")
        return

    cmd = update.message.text.strip().lstrip("/")
    if not cmd.endswith("s") or not cmd[:-1].isdigit():
        await update.message.reply_text("❗ دستور درست نیست. مثال: /20s")
        return

    seconds = int(cmd[:-1])
    await update.message.reply_text(f"⏳ تایمر {seconds} ثانیه‌ای شروع شد...")

    
    asyncio.create_task(run_timer(ctx, chat, seconds))


async def run_timer(ctx, chat: int, seconds: int):
    try:
        await asyncio.sleep(seconds)
        await ctx.bot.send_message(chat, "⏰ تایم تمام شد")
#       await play_alarm_sound(ctx, chat)

    except Exception as e:
        print("⚠️ run_timer error:", e)


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

    if getattr(g, "vote_collecting", False) and g.current_vote_target:
        start, end, target = g.vote_window
        now = datetime.now().timestamp()
        voter_seat = next((s for s,(u,_) in g.seats.items() if u == uid), None)

        if voter_seat and voter_seat != target and start <= now <= end:
            # ثبت رأی یکتا
            g.votes_cast.setdefault(target, set())
            g.votes_cast[target].add(uid)

            # 🕒 ذخیره لاگ رأی‌ها با زمان نسبی
            if not hasattr(g, "vote_logs"):
                g.vote_logs = {}
            g.vote_logs.setdefault(target, [])
            rel_time = now - start  # زمان از شروع بازه
            g.vote_logs[target].append((uid, rel_time))
            if not hasattr(g, "vote_cleanup_ids"):
                g.vote_cleanup_ids = []
            g.vote_cleanup_ids.append(msg.message_id)


    # -------------- defense seats by God ------------------
    # if g.vote_type == "awaiting_defense" and uid == g.god_id:
     #    nums = [int(n) for n in text.split() if n.isdigit() and int(n) in g.seats]

        # اگر ورودی معتبر نبود، پیام خطا بده و برگرد
       #  if not nums:
       #      await ctx.bot.send_message(chat_id, "❌ شماره صندلی معتبر وارد نشد. دوباره تلاش کنید (مثال: 1 3 5).")
           #  return

       #  g.defense_seats = nums
       #  g.vote_type = None  # ✅ غیرفعال کردن حالت وارد کردن صندلی دفاع

        # 🧹 حذف پیام درخواست صندلی‌های دفاع
       #  if g.defense_prompt_msg_id:
          #   try:
          #       await ctx.bot.delete_message(chat_id=chat_id, message_id=g.defense_prompt_msg_id)
          #   except:
          #       pass
          #   g.defense_prompt_msg_id = None

        # store.save()
         #await ctx.bot.send_message(chat_id, f"✅ صندلی‌های دفاع: {', '.join(map(str, nums))}")
        # await start_vote(ctx, chat_id, g, "final")
        # return

    if g.phase == "idle" and text.strip() == "کنسل":
        for seat, (player_uid, _) in list(g.seats.items()):
            if player_uid == uid:
                del g.seats[seat]
                store.save()
                await ctx.bot.send_message(chat_id, "❎ ثبت‌نام شما با موفقیت لغو شد.")
                await publish_seating(ctx, chat_id, g)
                break
        else:
            await ctx.bot.send_message(chat_id, "❗ شما در لیست نیستید.")
        return

    if hasattr(g, "adding_scenario_step") and g.adding_scenario_step:

        if uid != g.god_id:
            return


        if (datetime.now() - g.adding_scenario_last).total_seconds() > 300:
            g.adding_scenario_step = None
            g.adding_scenario_data = {}
            store.save()
            await ctx.bot.send_message(chat_id, "⏱ زمان شما تمام شد. اضافه کردن سناریو لغو شد.")
            return

        text = msg.text.strip()

        # مرحله ۱: نام سناریو
        if g.adding_scenario_step == "name":
            g.adding_scenario_data["name"] = text
            g.adding_scenario_step = "mafia"
            g.adding_scenario_last = datetime.now()
            store.save()
            await ctx.bot.send_message(chat_id, " ♠️ آیا نقش مافیا دارد؟ اگر بله، لیست را بفرستید (نقش ها را با / از هم جدا کنید). اگر نه، «خیر».")
            return

        # مرحله ۲: نقش مافیا
        if g.adding_scenario_step == "mafia":
            if text != "خیر":
                g.adding_scenario_data["mafia"] = [r.strip() for r in text.split("/") if r.strip()]
            else:
                g.adding_scenario_data["mafia"] = []
            g.adding_scenario_step = "citizen"
            g.adding_scenario_last = datetime.now()
            store.save()
            await ctx.bot.send_message(chat_id, "♥️ آیا نقش شهروند دارد؟ اگر بله، لیست را بفرستید (نقش ها را با / از هم جدا کنید). اگر نه، «خیر».")
            return

        # مرحله ۳: نقش شهروند
        if g.adding_scenario_step == "citizen":
            if text != "خیر":
                g.adding_scenario_data["citizen"] = [r.strip() for r in text.split("/") if r.strip()]
            else:
                g.adding_scenario_data["citizen"] = []
            g.adding_scenario_step = "indep"
            g.adding_scenario_last = datetime.now()
            store.save()
            await ctx.bot.send_message(chat_id, "♦️ آیا نقش مستقل دارد؟ اگر بله، لیست را بفرستید. اگر نه، «خیر».")
            return

        # مرحله ۴: نقش مستقل
        if g.adding_scenario_step == "indep":
            if text != "خیر":
                g.adding_scenario_data["indep"] = [r.strip() for r in text.split("/") if r.strip()]
            else:
                g.adding_scenario_data["indep"] = []
            g.adding_scenario_step = "cards"
            g.adding_scenario_last = datetime.now()
            store.save()
            await ctx.bot.send_message(chat_id, "♥️ آیا کارت دارد؟ اگر بله، لیست را بفرستید (نقش ها را با / از هم جدا کنید). اگر نه، «خیر».")
            return

        # مرحله ۵: کارت‌ها
        if g.adding_scenario_step == "cards":
            if text != "خیر":
                g.adding_scenario_data["cards"] = [r.strip() for r in text.split("/") if r.strip()]
            else:
                g.adding_scenario_data["cards"] = []

            # ✅ ذخیره در Gist
            name = g.adding_scenario_data["name"]
            mafia_roles   = g.adding_scenario_data["mafia"]
            citizen_roles = g.adding_scenario_data["citizen"]
            indep_roles   = g.adding_scenario_data["indep"]
            cards         = g.adding_scenario_data["cards"]

            # --- مافیا ---
            mafia_set = load_mafia_roles() or set()
            mafia_set |= set(mafia_roles)
            save_mafia_roles(mafia_set)

            # --- مستقل ---
            indep_map = load_indep_roles() or {}
            cur_indep = set(indep_map.get(name, []))
            cur_indep |= set(indep_roles)
            if cur_indep:
                indep_map[name] = sorted(cur_indep)
            save_indep_roles(indep_map)

            # --- کارت‌ها ---
            cards_map = load_cards() or {}
            cur_cards = set(cards_map.get(name, []))
            cur_cards |= set(cards)
            if cur_cards:
                cards_map[name] = sorted(cur_cards)
            save_cards(cards_map)

            # --- سناریو ---
            def list_to_counts(role_list):
                counts = {}
                for r in role_list:
                    counts[r] = counts.get(r, 0) + 1
                return counts

            mafia_counts   = list_to_counts(mafia_roles)
            citizen_counts = list_to_counts(citizen_roles)
            indep_counts   = list_to_counts(indep_roles)

            roles = {}
            roles.update(mafia_counts)
            roles.update(citizen_counts)
            roles.update(indep_counts)

            new_scenario = Scenario(name, roles)
            store.scenarios.append(new_scenario)
            store.save()
            save_scenarios_to_gist(store.scenarios)

            # پاکسازی وضعیت
            g.adding_scenario_step = None
            g.adding_scenario_data = {}
            store.save()

            await ctx.bot.send_message(chat_id, f"✅ سناریوی «{name}» با موفقیت ذخیره شد.")
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

async def cmd_addmafia(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in {"group", "supergroup"}:
        await update.message.reply_text("این دستور فقط در گروه قابل استفاده است.")
        return

    # چک ادمین بودن
    try:
        member = await ctx.bot.get_chat_member(chat.id, update.effective_user.id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text("⛔ فقط ادمین‌های گروه می‌توانند نقش مافیایی اضافه کنند.")
            return
    except Exception:
        await update.message.reply_text("⛔ خطا در بررسی ادمین بودن.")
        return

    role = " ".join(ctx.args).strip() if ctx.args else ""
    if not role:
        await update.message.reply_text("فرمت درست: /addmafia نام_نقش\nمثال: /addmafia گادفادر")
        return

    roles = load_mafia_roles()
    if role in roles:
        await update.message.reply_text(f"ℹ️ «{role}» از قبل در لیست مافیا هست.")
        return

    roles.add(role)
    ok = save_mafia_roles(roles)
    if ok:
        await update.message.reply_text(f"✅ نقش «{role}» به لیست مافیا اضافه شد.")
    else:
        await update.message.reply_text("❌ ذخیره‌سازی در Gist ناموفق بود.")


async def cmd_listmafia(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in {"group", "supergroup"}:
        await update.message.reply_text("این دستور فقط در گروه قابل استفاده است.")
        return

    # این یکی نیاز به ادمین‌بودن نداره → همه می‌تونن ببینن
    roles = sorted(list(load_mafia_roles()))
    if not roles:
        await update.message.reply_text("لیست نقش‌های مافیایی خالی است.")
        return

    txt = "🕶 لیست نقش‌های مافیایی ثبت‌شده:\n" + "\n".join(f"• {r}" for r in roles)
    await update.message.reply_text(txt)




async def add_card(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user_id = update.effective_user.id

    # فقط در گروه
    if chat.type not in {"group", "supergroup"}:
        await update.message.reply_text("⛔ این دستور فقط در گروه قابل استفاده است.")
        return

    # فقط ادمین‌ها
    member = await ctx.bot.get_chat_member(chat.id, user_id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("⛔ فقط ادمین‌های گروه می‌توانند کارت اضافه کنند.")
        return

    if len(ctx.args) < 2:
        await update.message.reply_text("❗ فرمت درست: /addcard <سناریو> <متن کارت>")
        return

    scn = ctx.args[0]
    card_text = " ".join(ctx.args[1:])

    cards = load_cards()
    cards.setdefault(scn, [])
    if card_text in cards[scn]:
        await update.message.reply_text("⚠️ این کارت قبلاً اضافه شده است.")
        return

    cards[scn].append(card_text)
    save_cards(cards)
    await update.message.reply_text(f"✅ کارت «{card_text}» به سناریو {scn} اضافه شد.")


async def list_cards(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user_id = update.effective_user.id

    # فقط در گروه
    if chat.type not in {"group", "supergroup"}:
        await update.message.reply_text("⛔ این دستور فقط در گروه قابل استفاده است.")
        return

    # فقط ادمین‌ها
    member = await ctx.bot.get_chat_member(chat.id, user_id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("⛔ فقط ادمین‌های گروه می‌توانند کارت‌ها را ببینند.")
        return

    if not ctx.args:
        await update.message.reply_text("❗ فرمت درست: /listcards <سناریو>")
        return

    scn = ctx.args[0]
    cards = load_cards().get(scn, [])

    if not cards:
        await update.message.reply_text(f"❌ برای سناریو {scn} کارتی ثبت نشده.")
        return

    msg = f"🃏 کارت‌های سناریو {scn}:\n" + "\n".join([f"- {c}" for c in cards])
    await update.message.reply_text(msg)


async def add_indep_role(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    uid = update.effective_user.id

    # فقط ادمین‌های گروه
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("❗ این دستور فقط در گروه قابل استفاده است.")
        return
    member = await ctx.bot.get_chat_member(chat.id, uid)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("⛔ فقط ادمین‌ها می‌توانند نقش مستقل اضافه کنند.")
        return

    if len(ctx.args) < 2:
        await update.message.reply_text("❗ فرمت درست: /addindep <سناریو> <نقش>")
        return

    scn = ctx.args[0]
    role = " ".join(ctx.args[1:])

    indep = load_indep_roles()
    indep.setdefault(scn, [])
    if role in indep[scn]:
        await update.message.reply_text("⚠️ این نقش قبلاً اضافه شده است.")
        return

    indep[scn].append(role)
    save_indep_roles(indep)
    await update.message.reply_text(f"✅ نقش مستقل «{role}» به سناریو {scn} اضافه شد.")


async def list_indep_roles(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("❗ فرمت درست: /listindep <سناریو>")
        return

    scn = ctx.args[0]
    roles = load_indep_roles().get(scn, [])

    if not roles:
        await update.message.reply_text(f"❌ برای سناریو {scn} نقش مستقلی ثبت نشده.")
        return

    msg = f"♦️ نقش‌های مستقل سناریو {scn}:\n" + "\n".join([f"- {r}" for r in roles])
    await update.message.reply_text(msg)

async def sub_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    g = gs(chat_id)


    if update.effective_user.id != g.god_id:
        await update.message.reply_text("⚠️ فقط راوی می‌تواند جایگزین کند.")
        return


    if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        await update.message.reply_text("⚠️ لطفاً روی پیام بازیکن جدید ریپلای کنید.")
        return

    new_uid = update.message.reply_to_message.from_user.id
    new_name = g.user_names.get(new_uid, "ناشناس")


    parts = update.message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await update.message.reply_text("⚠️ فرمت درست: /sub <شماره صندلی>")
        return

    seat_no = int(parts[1])
    if seat_no not in g.seats:
        await update.message.reply_text(f"⚠️ صندلی {seat_no} وجود ندارد.")
        return


    role = g.assigned_roles.get(seat_no)

    g.seats[seat_no] = (new_uid, new_name)
    store.save()
    await publish_seating(ctx, chat_id, g, mode=CTRL)


    stickers = load_stickers()
    if role in stickers:
        try:
            await ctx.bot.send_sticker(new_uid, stickers[role])
        except:
            pass
    try:
        await ctx.bot.send_message(new_uid, f"🎭 نقش شما: {role}")
    except telegram.error.Forbidden:
        await update.message.reply_text("⚠️ نتونستم نقش رو به پیوی بفرستم (پی‌وی بسته است).")


    if new_name == "ناشناس":
        g.waiting_name[new_uid] = seat_no
        store.save()
        await ctx.bot.send_message(
            chat_id,
            f"✏️ این پیام را ریپلای کنید و نام جدید خود را برای صندلی {seat_no} به فارسی وارد کنید:"
        )

    await update.message.reply_text(f"✅ بازیکن جدید جایگزین صندلی {seat_no} شد.")

async def cmd_lists(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    uid = update.effective_user.id
    g = gs(chat.id)

    # فقط ادمین‌های گروه اجازه داشته باشن
    try:
        member = await ctx.bot.get_chat_member(chat.id, uid)
        if member.status not in ("administrator", "creator"):
            await ctx.bot.send_message(chat.id, "⚠️ فقط ادمین‌ها می‌توانند این دستور را بزنند.")
            return
    except Exception:
        pass

    if not hasattr(g, "last_snapshot"):
        await ctx.bot.send_message(chat.id, "❌ لیست قبلی ذخیره نشده است.")
        return

    try:
        kb = InlineKeyboardMarkup.de_json(g.last_snapshot["kb"], ctx.bot)
    except Exception:
        kb = None

    # 📜 ارسال لیست بازیابی‌شده
    msg = await ctx.bot.send_message(
        chat.id,
        g.last_snapshot["text"],
        parse_mode="HTML",
        reply_markup=kb
    )

    # ✅ به‌روزرسانی آیدی پیام فعال
    g.last_seating_msg_id = msg.message_id
    store.save()

    # 📌 پین کردن پیام (اختیاری ولی پیشنهاد می‌شود)
    try:
        await ctx.bot.pin_chat_message(
            chat_id=chat.id,
            message_id=msg.message_id,
            disable_notification=True
        )
    except Exception as e:
        print(f"⚠️ خطا در پین کردن لیست بازیابی‌شده: {e}")



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
    app.add_handler(CommandHandler("addmafia", cmd_addmafia, filters=group_filter))
    app.add_handler(CommandHandler("listmafia", cmd_listmafia, filters=group_filter))
    app.add_handler(CommandHandler("list", cmd_lists, filters=group_filter))
    app.add_handler(CommandHandler("addcard", add_card))
    app.add_handler(CommandHandler("listcard", list_cards))
    app.add_handler(CommandHandler("addindep", add_indep_role))
    app.add_handler(CommandHandler("listindep", list_indep_roles))
    app.add_handler(CommandHandler("add", add_seat_cmd, filters=group_filter))
    app.add_handler(CommandHandler("god", transfer_god_cmd, filters=group_filter))
    app.add_handler(CommandHandler("sub", sub_command, filters=group_filter))
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

