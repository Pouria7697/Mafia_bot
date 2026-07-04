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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, Message, ChatPermissions
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
    event_title: str | None = None
    awaiting_event_title: bool = False
    max_seats: int = 0
    scenario: Scenario | None = None
    phase: str = "idle"

    waiting_name: dict[int, int] | None = None
    waiting_name_token: dict[int, float] | None = None
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
        self.waiting_name_token = self.waiting_name_token or {}
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
        self.pending_defense = getattr(self, "pending_defense", []) or []
        self.purchased_player = getattr(self, "purchased_player", None)
        self.purchase_pm_msg_id = getattr(self, "purchase_pm_msg_id", None)
        self.seat_sides = getattr(self, "seat_sides", {})
        # ── حالت شبِ خودکار (سناریو مذاکره) ──
        self.night_active = getattr(self, "night_active", False)
        self.maarefe_active = getattr(self, "maarefe_active", False)
        self.night_number = getattr(self, "night_number", 0)
        self.night_stage = getattr(self, "night_stage", None)
        self.night_is_negotiation = getattr(self, "night_is_negotiation", False)
        self.night_negotiation_target = getattr(self, "night_negotiation_target", None)
        self.night_shot_target = getattr(self, "night_shot_target", None)
        self.night_decider_seat = getattr(self, "night_decider_seat", None)
        self.night_can_negotiate = getattr(self, "night_can_negotiate", False)
        self.night_alive_at_start = getattr(self, "night_alive_at_start", 0)
        self.night_doc_need = getattr(self, "night_doc_need", 1)
        self.night_done = getattr(self, "night_done", set()) or set()
        self.night_sel = getattr(self, "night_sel", {}) or {}
        self.night_doc_sel = getattr(self, "night_doc_sel", {}) or {}
        self.night_pm_msgs = getattr(self, "night_pm_msgs", {}) or {}
        self.night_log = getattr(self, "night_log", []) or []
        self.negotiation_used = getattr(self, "negotiation_used", False)
        self.negotiated_seats = getattr(self, "negotiated_seats", set()) or set()
        self.sniper_used = getattr(self, "sniper_used", False)
        self.doctor_self_saves = getattr(self, "doctor_self_saves", 0)
        # ── حالت شبِ خودکار (سناریو بازپرس) ──
        self.yakuza_used = getattr(self, "yakuza_used", False)
        self.baazpors_used = getattr(self, "baazpors_used", False)
        self.night_doctor_blocked = getattr(self, "night_doctor_blocked", False)
        self.night_rest_opened = getattr(self, "night_rest_opened", False)
        self.night_shiad_guess = getattr(self, "night_shiad_guess", None)
        self.night_nato_seat = getattr(self, "night_nato_seat", None)
        self.night_nato_target = getattr(self, "night_nato_target", None)
        self.night_yakuza_sacrifice = getattr(self, "night_yakuza_sacrifice", None)
        self.night_hunter_target = getattr(self, "night_hunter_target", None)
        self.night_baz_sel = getattr(self, "night_baz_sel", {}) or {}
        self.night_baz_targets = getattr(self, "night_baz_targets", []) or []
        self.bzp_decider_seat = getattr(self, "bzp_decider_seat", None)
        # ── حالت شبِ خودکار (سناریو نماینده) ──
        self.mine_seat = getattr(self, "mine_seat", None)            # محل مین (تا آخر بازی)
        self.defuse_used = getattr(self, "defuse_used", False)        # خنثی‌سازی یکبار در بازی
        self.lawyer_used = getattr(self, "lawyer_used", False)        # وکالت یکبار در بازی
        self.nato_immune = getattr(self, "nato_immune", set()) or set()  # صندلی‌های مصون از ناتویی
        self.guide_last_target = getattr(self, "guide_last_target", None)
        self.night_hacker_actor = getattr(self, "night_hacker_actor", None)
        self.night_hacker_target = getattr(self, "night_hacker_target", None)
        self.night_don_defuse = getattr(self, "night_don_defuse", False)
        self.night_don_act = getattr(self, "night_don_act", None)     # "shot" / "nato"
        self.night_guide_target = getattr(self, "night_guide_target", None)
        self.night_guide_recipient_inv = getattr(self, "night_guide_recipient_inv", None)
        self.night_nato_correct = getattr(self, "night_nato_correct", False)
        self.night_lawyer_target = getattr(self, "night_lawyer_target", None)
        self.nem_decider_seat = getattr(self, "nem_decider_seat", None)
        # ── محاسبهٔ خودکار مرگ ──
        self.night_doc_saved = getattr(self, "night_doc_saved", []) or []
        self.night_sniper_target = getattr(self, "night_sniper_target", None)
        self.night_awaiting_sacrifice = getattr(self, "night_awaiting_sacrifice", False)
        self.night_pending_dead = getattr(self, "night_pending_dead", []) or []
        self.night_pending_reasons = getattr(self, "night_pending_reasons", {}) or {}
        self.night_god_notified = getattr(self, "night_god_notified", False)
        self.night_kick_seat = getattr(self, "night_kick_seat", None)
        self.vote_prev_window = getattr(self, "vote_prev_window", None)
        self.night_mine_handled = getattr(self, "night_mine_handled", False)
        self.night_mine_sacrifice = getattr(self, "night_mine_sacrifice", None)
        # ── حالت شبِ خودکار (سناریو تکاور) ──
        self.night_shield = getattr(self, "night_shield", False)
        self.night_guard_seats = getattr(self, "night_guard_seats", []) or []
        self.night_guard_sel = getattr(self, "night_guard_sel", {}) or {}
        self.tk_guard_need = getattr(self, "tk_guard_need", 1)
        self.night_hostage_seat = getattr(self, "night_hostage_seat", None)
        self.hostage_last_target = getattr(self, "hostage_last_target", None)
        self.night_commando_target = getattr(self, "night_commando_target", None)
        self.war_gun_used = getattr(self, "war_gun_used", False)
        self.war_gun_holder = getattr(self, "war_gun_holder", None)
        self.night_gun1_target = getattr(self, "night_gun1_target", None)
        self.night_gun1_type = getattr(self, "night_gun1_type", None)
        self.night_gun2_target = getattr(self, "night_gun2_target", None)
        self.night_gun2_type = getattr(self, "night_gun2_type", None)
        self.tk_decider_seat = getattr(self, "tk_decider_seat", None)
        # ── حالت شبِ خودکار (سناریو کاپو) ──
        self.jalad_used = getattr(self, "jalad_used", False)
        self.night_jalad_target = getattr(self, "night_jalad_target", None)
        self.night_jalad_seat = getattr(self, "night_jalad_seat", None)
        self.night_jalad_correct = getattr(self, "night_jalad_correct", False)
        self.night_witch_target = getattr(self, "night_witch_target", None)
        self.night_attar_poison_target = getattr(self, "night_attar_poison_target", None)
        self.attar_poisoned_seat = getattr(self, "attar_poisoned_seat", None)
        self.attar_poison_used = getattr(self, "attar_poison_used", False)
        self.poison_phase = getattr(self, "poison_phase", False)
        self.antidote_votes = getattr(self, "antidote_votes", {}) or {}
        self.antidote_expected = getattr(self, "antidote_expected", []) or []
        self.heir_seat = getattr(self, "heir_seat", None)
        self.heir_target = getattr(self, "heir_target", None)
        self.heir_inherited = getattr(self, "heir_inherited", False)
        self.heir_no_yakuza = getattr(self, "heir_no_yakuza", False)
        self.kp_decider_seat = getattr(self, "kp_decider_seat", None)
        # ── حالت شبِ خودکار (سناریو گیمر) ──
        self.gm_don_sentence = getattr(self, "gm_don_sentence", None)
        self.gm_awaiting_don_sentence = getattr(self, "gm_awaiting_don_sentence", False)
        self.gm_holmes_uses = getattr(self, "gm_holmes_uses", 0)
        self.gm_moriarty_uses = getattr(self, "gm_moriarty_uses", 0)
        self.gm_robin_uses = getattr(self, "gm_robin_uses", 0)
        self.gm_james_uses = getattr(self, "gm_james_uses", 0)
        self.gm_bomb_seat = getattr(self, "gm_bomb_seat", None)
        self.gm_bomb_fuses = getattr(self, "gm_bomb_fuses", {}) or {}
        self.gm_robbed_seat = getattr(self, "gm_robbed_seat", None)
        self.gm_gift_to = getattr(self, "gm_gift_to", None)
        self.gm_gift_accepted = getattr(self, "gm_gift_accepted", False)
        self.gm_gift_pending = getattr(self, "gm_gift_pending", False)
        self.gm_holmes_correct = getattr(self, "gm_holmes_correct", False)
        self.gm_holmes_despair = getattr(self, "gm_holmes_despair", None)
        self.gm_moriarty_correct = getattr(self, "gm_moriarty_correct", False)
        self.gm_moriarty_despair = getattr(self, "gm_moriarty_despair", False)
        self.gm_eliot_protect = getattr(self, "gm_eliot_protect", None)
        self.gm_rick_target = getattr(self, "gm_rick_target", None)
        self.gm_expected = getattr(self, "gm_expected", set()) or set()
        self.gm_james_nums = getattr(self, "gm_james_nums", []) or []
        self.gm_james_target = getattr(self, "gm_james_target", None)
        self.gm_james_waiting_don = getattr(self, "gm_james_waiting_don", False)
        self.gm_james_dice_val = getattr(self, "gm_james_dice_val", None)
        self.gm_tf_target = getattr(self, "gm_tf_target", None)
        self.gm_tf_map = getattr(self, "gm_tf_map", {}) or {}
        self.gm_robin_steal_from = getattr(self, "gm_robin_steal_from", None)
        # ── اتاق چت مافیا ──
        self.mafia_room_id = getattr(self, "mafia_room_id", None)
        self.mafia_room_link = getattr(self, "mafia_room_link", None)
        self.mafia_room_members = getattr(self, "mafia_room_members", set()) or set()
        self.mafia_room_pending_link = getattr(self, "mafia_room_pending_link", []) or []
        self.mafia_room_kicked = getattr(self, "mafia_room_kicked", set()) or set()
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


# ─── آمار بازیکنان (برد/باخت بر اساس ساید) ──────────────────────
STATS_FILENAME = "player_stats.json"

def load_player_stats() -> dict:
    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        response = httpx.get(url, headers=headers)
        if response.status_code == 200:
            gist_data = response.json()
            content = gist_data["files"].get(STATS_FILENAME, {}).get("content", "{}")
            return json.loads(content) or {}
        else:
            print("❌ player_stats gist fetch failed:", response.status_code)
            return {}
    except Exception as e:
        print("❌ load_player_stats error:", e)
        return {}

def save_player_stats(stats: dict):
    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        data = {
            "files": {
                STATS_FILENAME: {
                    "content": json.dumps(stats, ensure_ascii=False, indent=2)
                }
            }
        }
        httpx.patch(url, headers=headers, json=data)
    except Exception as e:
        print("❌ save_player_stats error:", e)


def update_player_stats(g: GameState, mafia_roles, indep_for_this):
    """بعد از پایان بازی، آمار هر بازیکن را بر اساس ساید و نتیجه به‌روز می‌کند."""
    try:
        stats = load_player_stats()

        for seat in sorted(g.seats):
            uid, name = g.seats[seat]
            role = g.assigned_roles.get(seat, "—")

            # تعیین ساید بازیکن
            # اولویت اول: خریداری یا جذب با مذاکره (شهروند → مافیا)
            if (getattr(g, "purchased_seat", None) == seat
                    or getattr(g, "purchased_player", None) == seat
                    or seat in getattr(g, "negotiated_seats", set())):
                side = "مافیا"
            # اولویت دوم: ساید کَش‌شده هنگام تخصیص نقش (قابل اعتماد‌ترین روش)
            elif getattr(g, "seat_sides", None) and seat in g.seat_sides:
                side = g.seat_sides[seat]
            # fallback: تشخیص لحظه‌ای (اگر کَش وجود نداشت) — با نرمالایز عربی/فارسی
            elif _nz(role) in {_nz(x) for x in mafia_roles}:
                side = "مافیا"
            elif _nz(role) in {_nz(x) for x in indep_for_this}:
                side = "مستقل"
            else:
                side = "شهر"

            # برد فقط بر اساس ساید واقعی هر بازیکن (شهر/مافیا/مستقل) تعیین می‌شود
            # کی‌آس هیچ تأثیری روی برد ندارد؛ مافیای داخل کی‌آس هم اگر مافیا ببرد برنده است
            won = (side == g.winner_side)

            key = str(uid)
            p = stats.get(key, {
                "name": name,
                "games": 0, "wins": 0,
                "citizen_games": 0, "citizen_wins": 0,
                "mafia_games": 0, "mafia_wins": 0,
                "indep_games": 0, "indep_wins": 0,
            })
            p["name"] = name  # نام را به‌روز نگه دار
            p["games"] = p.get("games", 0) + 1
            if won:
                p["wins"] = p.get("wins", 0) + 1

            if side == "مافیا":
                p["mafia_games"] = p.get("mafia_games", 0) + 1
                if won:
                    p["mafia_wins"] = p.get("mafia_wins", 0) + 1
            elif side == "مستقل":
                p["indep_games"] = p.get("indep_games", 0) + 1
                if won:
                    p["indep_wins"] = p.get("indep_wins", 0) + 1
            else:
                p["citizen_games"] = p.get("citizen_games", 0) + 1
                if won:
                    p["citizen_wins"] = p.get("citizen_wins", 0) + 1

            stats[key] = p

        # 🎩 گاد این بازی — شمارش دفعات گرداندن بازی
        if g.god_id:
            god_key = str(g.god_id)
            gp = stats.get(god_key, {
                "name": g.god_name or "بازیکن",
                "games": 0, "wins": 0,
                "citizen_games": 0, "citizen_wins": 0,
                "mafia_games": 0, "mafia_wins": 0,
                "indep_games": 0, "indep_wins": 0,
            })
            if g.god_name:
                gp["name"] = g.god_name
            gp["god_games"] = gp.get("god_games", 0) + 1
            stats[god_key] = gp

        save_player_stats(stats)
    except Exception as e:
        print("❌ update_player_stats error:", e)


def format_player_stats(p: dict) -> str:
    """متن فارسی آمار یک بازیکن — بدون نمایش آیدی."""
    def pct(w, n):
        return f" ({round(w * 100 / n)}٪)" if n > 0 else ""

    games = p.get("games", 0)
    wins = p.get("wins", 0)
    cg, cw = p.get("citizen_games", 0), p.get("citizen_wins", 0)
    mg, mw = p.get("mafia_games", 0), p.get("mafia_wins", 0)
    ig, iw = p.get("indep_games", 0), p.get("indep_wins", 0)

    name = escape(p.get("name", "بازیکن"), quote=False)
    lines = [
        f"📊 <b>آمار {name}</b>",
        "",
        f"🎮 کل بازی‌ها: <b>{games}</b>",
        f"🏆 کل بردها: <b>{wins}</b>{pct(wins, games)}",
        "",
        f"◽️ شهروند: {cg} بازی | {cw} برد{pct(cw, cg)}",
        f"◾️ مافیا: {mg} بازی | {mw} برد{pct(mw, mg)}",
    ]
    if ig > 0:
        lines.append(f"♦️ مستقل: {ig} بازی | {iw} برد{pct(iw, ig)}")

    gg = p.get("god_games", 0)
    if gg > 0:
        lines.append("")
        lines.append(f"🎩 گرداندن بازی (گاد): <b>{gg}</b> بار")
    return "\n".join(lines)


# ─── آمار هفتگی (لیدربرد) ───────────────────────────────────────
WEEKLY_META_FILENAME = "weekly_meta.json"   # {"last_sent": ts, "snapshot": {...}}
WEEKLY_PERIOD_SEC = 7 * 24 * 3600           # هر ۷ روز

def load_weekly_meta() -> dict:
    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        response = httpx.get(url, headers=headers)
        if response.status_code == 200:
            gist_data = response.json()
            content = gist_data["files"].get(WEEKLY_META_FILENAME, {}).get("content", "{}")
            return json.loads(content) or {}
        return {}
    except Exception as e:
        print("❌ load_weekly_meta error:", e)
        return {}

def save_weekly_meta(meta: dict):
    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        data = {
            "files": {
                WEEKLY_META_FILENAME: {
                    "content": json.dumps(meta, ensure_ascii=False, indent=2)
                }
            }
        }
        httpx.patch(url, headers=headers, json=data)
    except Exception as e:
        print("❌ save_weekly_meta error:", e)


def _weekly_delta(current: dict, snapshot: dict) -> dict:
    """تفاضل آمار هر بازیکن نسبت به اسنپ‌شات هفتهٔ قبل را برمی‌گرداند."""
    fields = [
        "games", "wins",
        "citizen_games", "citizen_wins",
        "mafia_games", "mafia_wins",
        "indep_games", "indep_wins",
        "god_games",
    ]
    delta = {}
    for uid, cur in current.items():
        old = snapshot.get(uid, {})
        d = {f: cur.get(f, 0) - old.get(f, 0) for f in fields}
        d["name"] = cur.get("name", "بازیکن")
        delta[uid] = d
    return delta


def _rank_block(delta: dict, win_key: str, game_key: str, top_n: int):
    """رتبه‌بندی بر اساس برد (تساوی → درصد برد)؛ فقط کسانی که حداقل ۱ برد دارند."""
    rows = [
        (uid, d) for uid, d in delta.items()
        if d.get(win_key, 0) > 0
    ]
    def sort_key(item):
        d = item[1]
        w = d.get(win_key, 0)
        n = d.get(game_key, 0)
        rate = (w / n) if n > 0 else 0
        score = w + rate * 2   # امتیاز ترکیبی: تعداد برد + وزن درصد برد
        return (score, w)
    rows.sort(key=sort_key, reverse=True)
    return rows[:top_n]


def _medal(i: int) -> str:
    return ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i + 1}."


def build_weekly_leaderboard_text(current: dict, snapshot: dict,
                                  require_weekly: bool = True) -> str | None:
    delta = _weekly_delta(current, snapshot)

    # ── این هفته ──
    w_overall = _rank_block(delta, "wins", "games", 10)
    w_citizens = _rank_block(delta, "citizen_wins", "citizen_games", 3)
    w_mafias = _rank_block(delta, "mafia_wins", "mafia_games", 3)
    w_gods = sorted(
        [(uid, d) for uid, d in delta.items() if d.get("god_games", 0) > 0],
        key=lambda it: it[1].get("god_games", 0),
        reverse=True,
    )[:2]

    # ── کل دوران (تجمعی) ──
    c_overall = _rank_block(current, "wins", "games", 10)

    weekly_active = bool(w_overall or w_citizens or w_mafias or w_gods)

    if require_weekly:
        # اجرای خودکار: اگر این هفته فعالیتی نبوده، چیزی نفرست
        if not weekly_active:
            return None
    else:
        # اجرای دستی (/weekly): اگر اصلاً هیچ آماری ثبت نشده، چیزی نفرست
        if not weekly_active and not c_overall:
            return None

    def pct(w, n):
        return f" ({round(w * 100 / n)}٪)" if n > 0 else ""

    def block(title, rows, win_key, game_key, unit="برد"):
        out = [title]
        if rows:
            for i, (_uid, d) in enumerate(rows):
                nm = escape(d["name"], quote=False)
                # نام قابل‌کلیک → پروفایل بازیکن (بدون نمایش آیدی)
                nm = f"<a href='tg://user?id={_uid}'>{nm}</a>"
                w = d.get(win_key, 0)
                n = d.get(game_key, 0)
                suffix = pct(w, n) if unit == "برد" else ""
                out.append(f"{_medal(i)} {nm} — {w} {unit}{suffix}")
        else:
            out.append("—")
        out.append("")
        return out

    lines = ["🏆 <b>برترین‌های هفته مافیا</b> 🏆", ""]
    lines += block("🏅 <b>۱۰ بازیکن برتر هفته</b>", w_overall, "wins", "games")
    lines += block("◽️ <b>۳ شهروند برتر هفته</b>", w_citizens, "citizen_wins", "citizen_games")
    lines += block("◾️ <b>۳ مافیای برتر هفته</b>", w_mafias, "mafia_wins", "mafia_games")
    lines += block("🎩 <b>پرکارترین راوی‌های هفته (گاد)</b>", w_gods, "god_games", "god_games", unit="بازی")

    lines.append("━━━━━━━━━━━━━")
    lines += block("👑 <b>۱۰ بازیکن برتر کل دوران</b>", c_overall, "wins", "games")

    # حذف خط خالی انتهایی
    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)


def build_alltime_leaderboard_text(current: dict) -> str | None:
    """لیدربرد کل دوران (تجمعی) — از همان داده‌ی player_stats.json."""
    overall = _rank_block(current, "wins", "games", 10)
    citizens = _rank_block(current, "citizen_wins", "citizen_games", 3)
    mafias = _rank_block(current, "mafia_wins", "mafia_games", 3)
    gods = sorted(
        [(uid, d) for uid, d in current.items() if d.get("god_games", 0) > 0],
        key=lambda it: it[1].get("god_games", 0),
        reverse=True,
    )[:2]

    if not (overall or citizens or mafias or gods):
        return None

    def pct(w, n):
        return f" ({round(w * 100 / n)}٪)" if n > 0 else ""

    def block(title, rows, win_key, game_key, unit="برد"):
        out = [title]
        if rows:
            for i, (_uid, d) in enumerate(rows):
                nm = escape(d.get("name", "بازیکن"), quote=False)
                nm = f"<a href='tg://user?id={_uid}'>{nm}</a>"
                w = d.get(win_key, 0)
                n = d.get(game_key, 0)
                suffix = pct(w, n) if unit == "برد" else ""
                out.append(f"{_medal(i)} {nm} — {w} {unit}{suffix}")
        else:
            out.append("—")
        out.append("")
        return out

    lines = ["👑 <b>برترین‌های کل دوران مافیا</b> 👑", ""]
    lines += block("🏅 <b>۱۰ بازیکن برتر کل</b>", overall, "wins", "games")
    lines += block("◽️ <b>۳ شهروند برتر کل</b>", citizens, "citizen_wins", "citizen_games")
    lines += block("◾️ <b>۳ مافیای برتر کل</b>", mafias, "mafia_wins", "mafia_games")
    lines += block("🎩 <b>پرکارترین راوی‌ها (گاد)</b>", gods, "god_games", "god_games", unit="بازی")

    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)


# ─── لیست منتخب (دعوت ۱۰ نفر برتر هفته) ─────────────────────────
SELECTED_FILENAME = "selected_list.json"
SELECTED_DAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
ADMIN_ID = 99347107

def load_selected_list() -> dict:
    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        response = httpx.get(url, headers=headers)
        if response.status_code == 200:
            gist_data = response.json()
            content = gist_data["files"].get(SELECTED_FILENAME, {}).get("content", "{}")
            return json.loads(content) or {}
        return {}
    except Exception as e:
        print("❌ load_selected_list error:", e)
        return {}

def save_selected_list(data: dict):
    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        payload = {
            "files": {
                SELECTED_FILENAME: {
                    "content": json.dumps(data, ensure_ascii=False, indent=2)
                }
            }
        }
        httpx.patch(url, headers=headers, json=payload)
    except Exception as e:
        print("❌ save_selected_list error:", e)


def kb_selected_days(selected_days) -> InlineKeyboardMarkup:
    sel = set(selected_days or [])
    rows = []
    for idx, day in enumerate(SELECTED_DAYS):
        label = f"✅ {day}" if day in sel else day
        rows.append([InlineKeyboardButton(label, callback_data=f"selday_{idx}")])
    rows.append([InlineKeyboardButton("📝 ثبت", callback_data="sel_submit")])
    return InlineKeyboardMarkup(rows)


async def launch_selected_round(bot, candidates=None) -> int:
    """دور جدید لیست منتخب: کاندیداها را ثبت و به همه پیام دعوت می‌فرستد."""
    if candidates is None:
        current = load_player_stats()
        meta = load_weekly_meta()
        snapshot = meta.get("snapshot", {})
        delta = _weekly_delta(current, snapshot)
        rows = _rank_block(delta, "wins", "games", 10)
        if not rows:  # برای تست، اگر هفته خالی بود از کل دوران بگیر
            rows = _rank_block(current, "wins", "games", 10)
        candidates = [(uid, d.get("name", "بازیکن")) for uid, d in rows]

    if not candidates:
        return 0

    data = {
        "candidates": {str(uid): nm for uid, nm in candidates},
        "responses": {},
        "started_at": datetime.now().timestamp(),
    }
    save_selected_list(data)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ بله", callback_data="sel_yes"),
        InlineKeyboardButton("❌ خیر", callback_data="sel_no"),
    ]])
    sent_names = []
    failed = []
    for uid, nm in candidates:
        try:
            await bot.send_message(
                int(uid),
                "🏆 تبریک! شما این هفته جزو ۱۰ نفر برتر شدید.\n\n"
                "آیا تمایل دارید در «لیست منتخب» شرکت کنید؟",
                reply_markup=kb,
            )
            sent_names.append(nm)
        except Exception as e:
            failed.append(nm)
            print(f"⚠️ selected invite failed for {uid}:", e)

    # اطلاع به ادمین دربارهٔ کسانی که پیام به آن‌ها نرسید
    if failed:
        try:
            await bot.send_message(
                ADMIN_ID,
                "⚠️ بات نتوانست به این افراد پیام دعوت بدهد "
                "(احتمالاً بات را استارت نکرده‌اند):\n"
                + "\n".join(f"• {escape(n, quote=False)}" for n in failed),
                parse_mode="HTML",
            )
        except Exception:
            pass

    return {"sent": sent_names, "failed": failed}


def build_selected_report(sl: dict) -> str:
    cand = sl.get("candidates", {})
    responses = sl.get("responses", {})

    def link(uid, name):
        nm = escape(name or "بازیکن", quote=False)
        return f"<a href='tg://user?id={uid}'>{nm}</a>"

    yes = [(u, r) for u, r in responses.items() if r.get("participate")]
    no = [(u, r) for u, r in responses.items() if not r.get("participate")]
    pending = [u for u in cand if u not in responses]

    lines = [
        "📋 <b>گزارش لیست منتخب</b>",
        f"کل کاندیداها: {len(cand)}",
        f"✅ مایل: {len(yes)} | ❌ غیرمایل: {len(no)} | ⏳ بی‌پاسخ: {len(pending)}",
        "",
    ]
    if yes:
        lines.append("✅ <b>مایل به شرکت:</b>")
        for u, r in yes:
            days = "، ".join(r.get("days", [])) or "—"
            mark = "" if r.get("submitted") else " (هنوز ثبت نکرده)"
            lines.append(f"• {link(u, r.get('name'))} (<code>{u}</code>) — {days}{mark}")
        lines.append("")
    if no:
        lines.append("❌ <b>غیرمایل:</b>")
        for u, r in no:
            lines.append(f"• {link(u, r.get('name'))} (<code>{u}</code>)")
        lines.append("")
    if pending:
        lines.append("⏳ <b>بی‌پاسخ:</b>")
        for u in pending:
            lines.append(f"• {link(u, cand.get(u))} (<code>{u}</code>)")

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


async def _maybe_send_full_report(bot, sl: dict):
    """وقتی همهٔ کاندیداها پاسخ نهایی دادند، یک گزارش کامل به ادمین می‌فرستد."""
    cand = sl.get("candidates", {})
    responses = sl.get("responses", {})

    def finalized(uid):
        r = responses.get(uid)
        return bool(r and (not r.get("participate") or r.get("submitted")))

    if not cand or not all(finalized(u) for u in cand):
        return
    if sl.get("report_sent"):
        return

    sl["report_sent"] = True
    save_selected_list(sl)
    try:
        await bot.send_message(
            ADMIN_ID,
            "🎉 همهٔ ۱۰ نفر پاسخ دادند!\n\n" + build_selected_report(sl),
            parse_mode="HTML",
        )
    except Exception:
        pass


SELECTED_DEADLINE_SEC = 48 * 3600  # مهلت ۴۸ ساعته برای پاسخ

async def check_selected_deadline(bot):
    """اگر بعد از ۴۸ ساعت همه پاسخ نداده باشند، گزارش ناقص را به ادمین می‌فرستد."""
    sl = load_selected_list()
    if not sl.get("candidates") or sl.get("report_sent"):
        return
    started = sl.get("started_at", 0)
    if not started or (datetime.now().timestamp() - started) < SELECTED_DEADLINE_SEC:
        return

    sl["report_sent"] = True
    save_selected_list(sl)
    try:
        await bot.send_message(
            ADMIN_ID,
            "⏰ مهلت ۴۸ ساعتهٔ لیست منتخب تمام شد.\n\n" + build_selected_report(sl),
            parse_mode="HTML",
        )
    except Exception:
        pass


async def handle_selected_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    uid = str(q.from_user.id)
    await safe_q_answer(q)

    sl = load_selected_list()
    cand = sl.get("candidates", {})
    responses = sl.setdefault("responses", {})

    if uid not in cand:
        try:
            await ctx.bot.send_message(int(uid), "⛔ شما در لیست برترین‌های این هفته نیستید.")
        except Exception:
            pass
        return

    name = cand.get(uid, q.from_user.full_name)

    if data == "sel_no":
        responses[uid] = {"name": name, "participate": False, "days": [], "submitted": True}
        save_selected_list(sl)
        try:
            await ctx.bot.edit_message_reply_markup(
                chat_id=int(uid), message_id=q.message.message_id, reply_markup=None
            )
        except Exception:
            pass
        await ctx.bot.send_message(int(uid), "باشه، ممنون! 🙏")
        await _maybe_send_full_report(ctx.bot, sl)
        return

    if data == "sel_yes":
        responses[uid] = {"name": name, "participate": True, "days": [], "submitted": False}
        save_selected_list(sl)
        await ctx.bot.send_message(
            int(uid),
            "📅 کدام روزها می‌توانید بازی کنید؟ (می‌توانید چند روز انتخاب کنید)\n"
            "در پایان «📝 ثبت» را بزنید:",
            reply_markup=kb_selected_days(set()),
        )
        return

    if data.startswith("selday_"):
        r = responses.get(uid)
        if not r or not r.get("participate"):
            return
        try:
            idx = int(data.split("_")[1])
        except Exception:
            return
        day = SELECTED_DAYS[idx]
        days = set(r.get("days", []))
        if day in days:
            days.remove(day)
        else:
            days.add(day)
        r["days"] = [d for d in SELECTED_DAYS if d in days]  # حفظ ترتیب
        save_selected_list(sl)
        try:
            await ctx.bot.edit_message_reply_markup(
                chat_id=int(uid),
                message_id=q.message.message_id,
                reply_markup=kb_selected_days(r["days"]),
            )
        except Exception:
            pass
        return

    if data == "sel_submit":
        r = responses.get(uid)
        if not r or not r.get("participate"):
            return
        if not r.get("days"):
            await safe_q_answer(q, "حداقل یک روز انتخاب کن.", show_alert=True)
            return
        r["submitted"] = True
        save_selected_list(sl)
        try:
            await ctx.bot.edit_message_reply_markup(
                chat_id=int(uid), message_id=q.message.message_id, reply_markup=None
            )
        except Exception:
            pass
        await ctx.bot.send_message(
            int(uid), f"✅ ثبت شد! روزهای انتخابی شما: {'، '.join(r['days'])}"
        )
        await _maybe_send_full_report(ctx.bot, sl)
        return


async def broadcast_weekly_stats(bot, force: bool = False):
    """در صورت رسیدن موعد هفتگی، لیدربرد را به همهٔ گروه‌های فعال می‌فرستد."""
    meta = load_weekly_meta()
    now = datetime.now().timestamp()
    last_sent = meta.get("last_sent", 0)
    snapshot = meta.get("snapshot", {})

    current = load_player_stats()

    # اولین اجرا: فقط خط مبنا را ثبت کن و صبر کن تا هفتهٔ بعد
    if not last_sent and not force:
        save_weekly_meta({"last_sent": now, "snapshot": current})
        return

    if not force and (now - last_sent) < WEEKLY_PERIOD_SEC:
        return {"sent": 0, "reason": "not_due"}

    text = build_weekly_leaderboard_text(current, snapshot, require_weekly=not force)

    sent = 0
    if text:
        for chat_id in list(store.active_groups):
            try:
                msg = await bot.send_message(chat_id, text, parse_mode="HTML")
                try:
                    await bot.pin_chat_message(
                        chat_id, msg.message_id, disable_notification=True
                    )
                except Exception as e:
                    print(f"⚠️ weekly pin failed for {chat_id}:", e)
                sent += 1
            except Exception as e:
                print(f"⚠️ weekly send failed for {chat_id}:", e)

    # 📋 دعوت ۱۰ نفر برتر هفته به لیست منتخب (فقط در اجرای خودکار)
    if not force and text:
        try:
            delta = _weekly_delta(current, snapshot)
            rows = _rank_block(delta, "wins", "games", 10)
            cands = [(uid, d.get("name", "بازیکن")) for uid, d in rows]
            if cands:
                await launch_selected_round(bot, cands)
        except Exception as e:
            print("⚠️ launch_selected_round error:", e)

    # در اجرای دستی (force) اسنپ‌شات هفته را صفر نکن تا تست بعدی هم کار کند
    if not force:
        save_weekly_meta({"last_sent": now, "snapshot": current})

    if not text:
        reason = "no_data" if not current else "no_activity"
        return {"sent": 0, "reason": reason}
    return {"sent": sent, "reason": "ok", "groups": len(store.active_groups)}


async def weekly_scheduler(app):
    """هر ساعت بررسی می‌کند که آیا موعد ارسال آمار هفتگی رسیده یا نه."""
    while True:
        try:
            await broadcast_weekly_stats(app.bot)
        except Exception as e:
            print("⚠️ weekly_scheduler error:", e)
        try:
            await check_selected_deadline(app.bot)
        except Exception as e:
            print("⚠️ check_selected_deadline error:", e)
        await asyncio.sleep(3600)


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
        [InlineKeyboardButton("📝 تغییر رویداد", callback_data="change_event")],
        [InlineKeyboardButton("⬅️ برگشت", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(rows)


async def _is_god_or_admin(ctx, chat_id: int, uid: int, g: GameState) -> bool:
    if uid == g.god_id:
        return True
    try:
        admins = await ctx.bot.get_chat_administrators(chat_id)
        return uid in {a.user.id for a in admins}
    except Exception:
        return False


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
        # تاج ♚ فقط قبل از شروع بازی (مرحلهٔ ثبت‌نام) نمایش داده می‌شود
        game_started = g.phase != "idle"
        cr = "" if game_started else "♚"
        event_num = int(get_event_numbers().get(str(chat_id), 1))

        lines = [
            f"{group_id_or_link}",
            f"{cr}🎯 <b>شماره رویداد:</b> {event_num}",
            f"{cr}🎭 <b>{escape(g.event_title, quote=False) if g.event_title else 'رویداد مافیا'}</b>",
            f"{cr}📆 <b>تاریخ:</b> {today}",
            f"{cr}🕰 <b>زمان:</b> {g.event_time or '---'}",
            f"{cr}🎩 <b>راوی:</b> <a href='tg://user?id={g.god_id}'>{g.god_name or '❓'}</a>",
        ]

        if g.scenario:
            lines.append(f"{cr}📜 <b>سناریو:</b> {g.scenario.name} | 👥 {sum(g.scenario.roles.values())} نفر")

        lines.append(f"\n\n{cr}📂 <b>بازیکنان:</b>\n")

        # لیست صندلی‌ها
        for i in range(1, g.max_seats + 1):
            emoji_num = emoji_numbers[i] if i < len(emoji_numbers) else str(i)
            if i in g.seats:
                uid, name = g.seats[i]
                safe_name = escape(name, quote=False)
                name_link = f"<a href='tg://user?id={uid}'>{safe_name}</a>"

                wn = 0
                if isinstance(getattr(g, "warnings", None), dict):
                    wn = g.warnings.get(i, 0)
                try:
                    wn = int(wn)
                except Exception:
                    wn = 0
                wn = max(0, wn)
                warn_suffix = (" " + ("❗️" * wn)) if wn > 0 else ""

                if not game_started:
                    # قبل از بازی: حالت اصلی با تاج
                    line = f"♚{i}  {name_link}{warn_suffix}"
                elif i in g.striked:
                    # مرده: دایرهٔ قرمز، خط روی شماره و اسم، فونت عادی
                    line = f"🔴<s>{i}  {name_link}</s>{warn_suffix}"
                else:
                    # زنده: دایرهٔ سبز، فونت بولد
                    line = f"🟢<b>{i}  {name_link}</b>{warn_suffix}"
            else:
                # صندلی خالی
                line = f"⬜{i} /{i}" if game_started else f"♚{i} ⬜ /{i}"
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
        except (TimedOut, RetryAfter):
            pass  # خطای گذرا – پیام جدید نفرست
        except telegram.error.BadRequest as e:
            if "not modified" in str(e).lower():
                pass  # محتوا تغییری نکرده (مثلاً شبِ بدون کشته) – پیام جدید نفرست
            else:
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
                            chat_id, msg.message_id, disable_notification=True))
                    except Exception:
                        pass
                if old_msg_id:
                    try:
                        await ctx.bot.delete_message(chat_id, old_msg_id)
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
                mafia_roles = {_nz(x) for x in load_mafia_roles()}
                indep_roles = load_indep_roles()
                indep_for_this = {_nz(x) for x in indep_roles.get(g.scenario.name, [])}
                mafia_lines = ["<b>نقش‌های مافیا:</b>"]
                citizen_lines = ["<b>نقش‌های شهروند:</b>"]
                indep_lines = ["<b>نقش‌های مستقل:</b>"]

                for role, count in g.scenario.roles.items():
                    for _ in range(count):
                        if _nz(role) in mafia_roles:
                            mafia_lines.append(f"♠️ {role}")
                        elif _nz(role) in indep_for_this:
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
def _is_valid_vote_text(text: str) -> bool:
    """رأی معتبر: فقط «دولایک با هر رنگ پوست» یا «دو نقطه ..»"""
    t = (text or "").strip()
    if t == "..":
        return True
    # حذف رنگ پوست، کاراکترهای نامرئی ایموجی و فاصله‌ها
    _strip = {"︎", "️", "‍", " ", "‌"}    # variation selectors + ZWJ + فاصله + نیم‌فاصله
    cleaned = "".join(
        ch for ch in t
        if not ("\U0001F3FB" <= ch <= "\U0001F3FF")           # skin tone modifiers
        and ch not in _strip
    )
    return cleaned == "\U0001F44D\U0001F44D"                  # 👍👍


def _try_capture_vote(g, msg, uid, text) -> bool:
    """اگر پیام یک رأی معتبر داخل پنجرهٔ رأی‌گیری (جاری یا قبلی) باشد، ثبتش می‌کند.
    ملاک زمان = ساعتِ ارسال تلگرام (msg.date)، نه زمان پردازش.
    پنجره‌ی قبلی برای رأی‌هایی است که به‌موقع فرستاده شده ولی دیر به بات رسیده‌اند."""
    if not _is_valid_vote_text(text):
        return False
    try:
        msg_ts = msg.date.timestamp()
    except Exception:
        msg_ts = datetime.now().timestamp()

    candidates = []
    cur = getattr(g, "vote_window", None)
    target_now = getattr(g, "current_vote_target", None)
    if cur and target_now and cur[2] == target_now:
        candidates.append(cur)
    prev = getattr(g, "vote_prev_window", None)
    if prev:
        candidates.append(prev)

    for start, end, win_target in candidates:
        if not (start <= msg_ts <= end):   # بدون تلورانس — ساعتِ بعد از «تمام» رد می‌شود
            continue
        voter_seat = next((s for s, (u, _) in g.seats.items() if u == uid), None)
        if not voter_seat or voter_seat == win_target or voter_seat in (g.striked or set()):
            return False
        g.votes_cast.setdefault(win_target, set())
        if uid not in g.votes_cast[win_target]:  # هر نفر برای «هر هدف» فقط یک رأی
            g.votes_cast[win_target].add(uid)
            if not hasattr(g, "vote_logs"):
                g.vote_logs = {}
            g.vote_logs.setdefault(win_target, [])
            g.vote_logs[win_target].append((uid, msg_ts - start))
            store.save()
        if not hasattr(g, "vote_cleanup_ids"):
            g.vote_cleanup_ids = []
        g.vote_cleanup_ids.append(msg.message_id)
        return True
    return False


async def _send_vote_timing_report(ctx, g, chat_id):
    """گزارش تست: زمانِ ثبت هر رأی نسبت به پیامِ «رأی‌گیری برای…» — داخل گروه."""
    logs = getattr(g, "vote_logs", {}) or {}
    if not logs:
        return
    order, seen = [], set()
    for t in (getattr(g, "vote_order", []) or []):
        if t not in seen:
            seen.add(t); order.append(t)
    for t in logs:
        if t not in seen:
            seen.add(t); order.append(t)
    lines = ["🕒 <b>گزارش زمان ثبت رأی‌ها (تست)</b>"]
    for target in order:
        entries = logs.get(target, [])
        tname = g.seats.get(target, (0, "؟"))[1]
        lines.append(f"\n🎯 {target}. {escape(tname, quote=False)} — <b>{len(entries)}</b> رأی:")
        for u, rel in entries:
            vs = next((s for s, (uu, _) in g.seats.items() if uu == u), None)
            vname = g.seats.get(vs, (0, "؟"))[1] if vs else "؟"
            lines.append(f"  • {vs}. {escape(vname, quote=False)} — {rel:.1f} ثانیه")
    try:
        await ctx.bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
    except Exception:
        pass


def _final_vote_threshold(alive_count: int) -> int:
    """حدنصابِ ورود به دفاعیه بر اساس تعداد زنده‌ها:
    ۴-۵ نفر → ۲ | ۶-۷ نفر → ۳ | ۸-۱۰ نفر → ۴ | بیشتر → نصفِ زنده‌ها (براکت پایین)"""
    if alive_count <= 5:
        return 2
    if alive_count <= 7:
        return 3
    if alive_count <= 10:
        return 4
    return alive_count // 2


async def _offer_auto_defense(ctx, chat_id, g):
    """بعد از پایانِ رأی اولیه: محاسبه‌ی حدنصاب، اعلامِ واجدانِ دفاعیه و پیشنهاد ساختِ رأی نهایی."""
    alive = len(_alive_seats(g))
    thr = _final_vote_threshold(alive)
    counts = {t: len(v) for t, v in (g.votes_cast or {}).items()}
    order, seen = [], set()
    for t in (getattr(g, "vote_order", []) or []):
        if t not in seen and t in counts:
            seen.add(t); order.append(t)
    for t in counts:
        if t not in seen:
            seen.add(t); order.append(t)
    qualified = [t for t in order
                 if counts.get(t, 0) >= thr and t in g.seats and t not in (g.striked or set())]

    # ⚖️ نماینده: اکتِ وکیل (فقط ۲۴ ساعت — همان شبِ قبل) مانع ورود به دفاعیه می‌شود
    lawyer_seat = None
    if _is_nemayande_scenario(g):
        lt = getattr(g, "night_lawyer_target", None)
        if lt in qualified:
            qualified.remove(lt)
            lawyer_seat = lt

    if not qualified and lawyer_seat is None:
        await ctx.bot.send_message(chat_id, f"ℹ️ هیچ‌کس به حدنصاب دفاعیه ({thr} رأی) نرسید.")
        return

    lines = [f"🗳 حدنصاب دفاعیه با {alive} بازیکنِ زنده: <b>{thr}</b> رأی",
             "🧍 <b>این افراد داخل دفاعیه هستند:</b>"]
    for t in qualified:
        lines.append(f"• {t}. {escape(g.seats[t][1], quote=False)} — {counts.get(t, 0)} رأی")
    if lawyer_seat is not None:
        lines.append(f"⚖️ {lawyer_seat}. {escape(g.seats[lawyer_seat][1], quote=False)} "
                     f"با اکتِ وکیل واردِ دفاع نمی‌شود.")
    if not qualified:
        await ctx.bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
        return

    g.pending_defense = list(qualified)
    store.save()
    lines.append("")
    lines.append("تأیید می‌کنید؟ (با «بله» رأی‌گیری نهایی برای همین افراد ساخته می‌شود)")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ بله", callback_data="autofinal_yes"),
        InlineKeyboardButton("🚫 خیر", callback_data="autofinal_no"),
    ]])
    await ctx.bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML", reply_markup=kb)


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

    g.vote_collecting = True
    g.votes_cast.setdefault(target_seat, set())
    g.vote_logs.setdefault(target_seat, [])

    if not hasattr(g, "vote_order"):
        g.vote_order = []
    g.vote_order.append(target_seat)

    if not hasattr(g, "vote_cleanup_ids"):
        g.vote_cleanup_ids = []

    msg = await ctx.bot.send_message(
        chat_id,
        f"⏳ رأی‌گیری برای <b>{target_seat}. {g.seats[target_seat][1]}</b>",
        parse_mode="HTML"
    )
    g.vote_cleanup_ids.append(msg.message_id)

    # ⏱ ملاک زمان = ساعتِ سرور تلگرام (msg.date) — سقفِ موقت تا ارسال «تمام»
    start_time = msg.date.timestamp()
    # 🕰 پنجره‌ی قبلی را نگه دار تا رأی‌های دیررسیده‌ی دورِ قبل گم نشوند
    _old = getattr(g, "vote_window", None)
    if _old:
        _os, _oe, _ot = _old
        if _oe > start_time:
            _oe = start_time   # سقفِ بازِ قبلی با شروعِ دورِ جدید بسته می‌شود
        g.vote_prev_window = (_os, _oe, _ot)
    g.vote_window = (start_time, start_time + 30.0, target_seat)
    store.save()

    await asyncio.sleep(4)

    g.vote_collecting = False
    end_msg = await ctx.bot.send_message(chat_id, "🛑 تمام", parse_mode="HTML")

    # ⏱ بستنِ پنجره با ساعتِ واقعیِ پیامِ «تمام» — رأی‌های دیررسیده ولی به‌موقع‌فرستاده هنوز شمرده می‌شوند
    g.vote_window = (start_time, end_msg.date.timestamp(), target_seat)
    store.save()

    if g.vote_stage == "initial_vote":
        g.last_vote_msg_id_initial = end_msg.message_id
    elif g.vote_stage == "final":
        g.last_vote_msg_id_final = end_msg.message_id

    g.voted_targets.add(target_seat)
    await update_vote_buttons(ctx, chat_id, g)
    store.save()









# ─── تایمر انقضای انتظار تغییر نام ──────────────────────────────
async def _expire_name_prompt(ctx, chat_id: int, uid: int, seat_no: int,
                              prompt_msg_id: int | None, token: float):
    """اگر تا ۶۰ ثانیه نام جدید دریافت نشد، انتظار را لغو و پیام درخواست را پاک می‌کند."""
    await asyncio.sleep(60)
    g = gs(chat_id)
    # فقط اگر هنوز منتظر همین درخواست هستیم لغو کن (کاربر دوباره کلیک نکرده باشد)
    if g.waiting_name.get(uid) != seat_no:
        return
    if getattr(g, "waiting_name_token", {}).get(uid) != token:
        return

    g.waiting_name.pop(uid, None)
    if isinstance(getattr(g, "waiting_name_token", None), dict):
        g.waiting_name_token.pop(uid, None)
    store.save()

    if prompt_msg_id:
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=prompt_msg_id)
        except Exception:
            pass


def _start_name_wait(ctx, chat_id: int, g: GameState, uid: int,
                     seat_no: int, prompt_msg):
    """انتظار تغییر نام را با یک تایمر ۶۰ ثانیه‌ای ثبت می‌کند."""
    g.waiting_name[uid] = seat_no
    if not isinstance(getattr(g, "waiting_name_token", None), dict):
        g.waiting_name_token = {}
    token = datetime.now().timestamp()
    g.waiting_name_token[uid] = token
    store.save()
    prompt_id = getattr(prompt_msg, "message_id", None)
    asyncio.create_task(
        _expire_name_prompt(ctx, chat_id, uid, seat_no, prompt_id, token)
    )


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

    event_title = escape(g.event_title, quote=False) if getattr(g, "event_title", None) else "رویداد مافیا"
    lines = [
        f"🎭 <b>{event_title}</b>",
        f"░⚜️🎮 گروه: {group_link}",
        f"░⚜️📅 تاریخ: {date_str}",
        f"░⚜️🎯 شماره رویداد:{event_num}",
        f"░💡🔱 راوی: <a href='tg://user?id={g.god_id}'>{g.god_name or '❓'}</a>",
        f"░⚜️📃 سناریو: {scenario_name}",
        "",
        "░⚜️💫 لیست بازیکنان ⬇️",
        "",
    ]

    mafia_roles = {_nz(x) for x in load_mafia_roles()}
    indep_roles = load_indep_roles()
    indep_for_this = {_nz(x) for x in indep_roles.get(g.scenario.name, [])}

    for seat in sorted(g.seats):
        uid, name = g.seats[seat]
        role = g.assigned_roles.get(seat, "—")

        # انتخاب مارکر بر اساس نقش
        if seat in getattr(g, "negotiated_seats", set()):
            role_display = f"{role} / مافیا ساده"
            marker = "◾️"  # مذاکره‌شده → مافیا ساده
        elif getattr(g, "purchased_seat", None) == seat or getattr(g, "purchased_player", None) == seat:
            role_display = f"{role} / مافیا"
            marker = "◾️"  # خریداری شده → مافیا
        elif _nz(role) in mafia_roles:
            marker = "◾️"  # مافیا
            role_display = role
        elif _nz(role) in indep_for_this:
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

    # 📊 ثبت آمار برد/باخت بازیکنان در Gist
    update_player_stats(g, mafia_roles, indep_for_this)

    g.phase = "ended"
    store.save()

    msg = await ctx.bot.send_message(chat.id, "\n".join(lines), parse_mode="HTML")
    try:
        await ctx.bot.pin_chat_message(chat_id=chat.id, message_id=msg.message_id)
    except Exception as e:
        print("⚠️ خطا در پین کردن پیام:", e)

    # 🌙 گزارش شب‌به‌شبِ اکت‌ها (به‌صورت متن جداگانه زیر لیست پایانی)
    night_log = getattr(g, "night_log", None)
    if night_log:
        report = "📜 <b>گزارش شب‌به‌شب</b>\n" + "\n".join(night_log)
        # تکه‌تکه کردن در صورت طولانی بودن (محدودیت ۴۰۹۶ کاراکتر تلگرام)
        chunk = ""
        for line in report.split("\n"):
            if len(chunk) + len(line) + 1 > 3500:
                try:
                    await ctx.bot.send_message(chat.id, chunk, parse_mode="HTML")
                except Exception:
                    pass
                chunk = ""
            chunk += (line + "\n")
        if chunk.strip():
            try:
                await ctx.bot.send_message(chat.id, chunk, parse_mode="HTML")
            except Exception:
                pass

    # 🔗 پایان بازی: پاک‌سازی اتاق مافیا (حذف همه + باطل‌کردن لینک)
    await _room_cleanup(ctx, g)




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




# ═════════════════════════════════════════════════════════════
#  موتور شبِ خودکار — سناریو «مذاکره»
#  گاد با /شب اکت‌گیری را شروع و با /روز تمام می‌کند.
#  بات فقط اکت‌ها را جمع و زنده به پیوی گاد گزارش می‌دهد؛
#  تصمیم نهایی مرگ‌ها با خود گاد است.
# ═════════════════════════════════════════════════════════════
NEG_SCENARIO_KEY = "مذاکر"   # تشخیص سناریو مذاکره (نرمالایز)

def _nz(s: str) -> str:
    """نرمالایز نام نقش/سناریو: یکسان‌سازی حروف عربی↔فارسی + حذف فاصله/نیم‌فاصله/کاراکترهای نامرئی."""
    s = s or ""
    # یکسان‌سازی حروف عربی به فارسی (با کدهای صریح یونیکد)
    s = (s.replace("ي", "ی")   # ي عربی → ی فارسی
           .replace("ى", "ی")  # ى الف مقصوره → ی
           .replace("ك", "ک")  # ك عربی → ک فارسی
           .replace("ة", "ه")  # ة → ه
           .replace("ۀ", "ه")) # ۀ → ه
    # حذف فاصله، نیم‌فاصله و کاراکترهای عرضِ صفر / جهت
    for ch in (" ", "‌", "​", "‍", "‎", "‏", "﻿", " "):
        s = s.replace(ch, "")
    return s.strip()

# نام نقش‌ها (نرمالایز شده)
_R_GODFATHER    = _nz("گادفادر")
_R_NEGOTIATOR   = _nz("مذاکره کننده")
_R_SIMPLE_MAFIA = _nz("مافیا ساده")
_R_ARMORED      = _nz("زره پوش")
_R_DETECTIVE    = _nz("کاراگاه")
_R_SNIPER       = _nz("تک تیرانداز")
_R_DOCTOR       = _nz("پزشک")
_R_REPORTER     = _nz("خبرنگار")
_R_CITIZEN      = {_nz("شهرساده"), _nz("شهر ساده"), _nz("شهروند ساده"), _nz("شهروند")}


def _is_neg_scenario(g) -> bool:
    return bool(getattr(g, "scenario", None)) and (_nz(NEG_SCENARIO_KEY) in _nz(g.scenario.name))

def _alive_seats(g):
    return [s for s in sorted(g.seats) if s not in (g.striked or set())]

def _seat_role_norm(g, seat):
    return _nz((g.assigned_roles or {}).get(seat, ""))

def _seat_of_uid(g, uid):
    for s, (u, _n) in g.seats.items():
        if u == uid:
            return s
    return None

def _find_seat_by_role(g, role_norm, alive_only=True):
    seats = _alive_seats(g) if alive_only else sorted(g.seats)
    for s in seats:
        if _seat_role_norm(g, s) == role_norm:
            return s
    return None

def _mafia_role_set(g):
    """نقش‌های تیم مافیا بسته به سناریو."""
    if _is_baazpors_scenario(g):
        return (_R_GODFATHER, _R_NATO, _R_SHIAD, _R_SIMPLE_MAFIA)
    if _is_nemayande_scenario(g):
        return (_R_DON, _R_HACKER, _R_YAGHI)
    if _is_takavar_scenario(g):
        return (_R_GODFATHER, _R_HOSTAGE, _R_NATO)
    if _is_kapu_scenario(g):
        return (_R_DON, _R_EXECUTIONER, _R_WITCH)
    if _is_gamer_scenario(g):
        return (_R_DONC, _R_TWOFACE, _R_MORIARTY)
    return (_R_GODFATHER, _R_NEGOTIATOR, _R_SIMPLE_MAFIA)

def _mafia_seats(g, alive_only=False):
    """صندلی‌های تیم مافیا (شامل جذب‌شده‌های مذاکره/یاکوزایی)."""
    mafia_roles = _mafia_role_set(g)
    out = set()
    for s in g.seats:
        if _seat_role_norm(g, s) in mafia_roles:
            out.add(s)
    out |= set(g.negotiated_seats or set())
    if alive_only:
        out = {s for s in out if s not in (g.striked or set())}
    return out

def _dead_nonneg_mafia_exists(g) -> bool:
    """آیا یک مافیای غیرِ مذاکره‌کننده مرده است؟ (شرط فعال‌شدن مذاکره)"""
    for s in g.seats:
        if s in (g.striked or set()) and _seat_role_norm(g, s) in (_R_GODFATHER, _R_SIMPLE_MAFIA):
            return True
    return False

def _doctor_targets(g, doc_seat):
    out = []
    for s in _alive_seats(g):
        if s == doc_seat and (g.doctor_self_saves or 0) >= 2:
            continue  # سقف سیو خودی = ۲ بار
        out.append(s)
    return out

def _detective_positive(g, seat) -> bool:
    rn = _seat_role_norm(g, seat)
    if rn in (_R_SIMPLE_MAFIA, _R_NEGOTIATOR):
        return True
    if seat in (g.negotiated_seats or set()):
        return True
    if g.night_is_negotiation and seat == g.night_negotiation_target:
        return True
    return False  # گادفادر برای کاراگاه منفی است

def _reporter_positive(g, seat) -> bool:
    # خبرنگار فقط روی فرد مذاکره‌شده مثبت می‌گیرد
    if seat in (g.negotiated_seats or set()):
        return True
    if g.night_is_negotiation and seat == g.night_negotiation_target:
        return True
    return False


def _kb_night_seats(seats, g, prefix, selected=None, confirm_cb=None):
    """کیبورد انتخاب صندلی برای اکت شب."""
    if selected is None:
        sel = set()
    elif isinstance(selected, (set, list, tuple)):
        sel = set(selected)
    else:
        sel = {selected}
    rows = []
    for s in seats:
        _u, name = g.seats[s]
        mark = "✅ " if s in sel else ""
        rows.append([InlineKeyboardButton(f"{mark}{s} {name}", callback_data=f"{prefix}{s}")])
    if confirm_cb:
        rows.append([InlineKeyboardButton("✅ تأیید", callback_data=confirm_cb)])
    return InlineKeyboardMarkup(rows)


async def _safe_pm(ctx, uid, text, kb=None):
    try:
        return await ctx.bot.send_message(uid, text, reply_markup=kb)
    except Exception:
        return None


async def _report_unreachable(ctx, chat_id, g):
    """بازیکن‌هایی که بات را استارت نکرده‌اند (نمی‌شود دکمه فرستاد) را به گروه گزارش می‌دهد."""
    unreachable = []
    for s in _alive_seats(g):
        uid, name = g.seats[s]
        try:
            await ctx.bot.send_chat_action(uid, "typing")
        except Exception:
            unreachable.append(f"{s}. {name}")
    if unreachable:
        txt = ("⚠️ <b>این بازیکن‌ها بات را استارت نکرده‌اند</b> و دکمهٔ اکت دریافت نمی‌کنند:\n"
               + "\n".join(unreachable)
               + "\n\nاز آن‌ها بخواهید بات را در پیوی باز کنند و /start بزنند، بعد دوباره /شب.")
        try:
            await ctx.bot.send_message(chat_id, txt, parse_mode="HTML")
        except Exception:
            pass
    return unreachable

async def _edit_pm(ctx, uid, msg_id, text, kb):
    try:
        await ctx.bot.edit_message_text(chat_id=uid, message_id=msg_id, text=text, reply_markup=kb)
    except Exception:
        pass

async def _close_pm(ctx, uid, msg_id, text):
    """ویرایش پیام به متن نهایی و حذف دکمه‌ها (تا نتوانند نظرشان را عوض کنند)."""
    try:
        await ctx.bot.edit_message_text(chat_id=uid, message_id=msg_id, text=text, reply_markup=None)
    except Exception:
        pass

async def _night_report(ctx, g, text):
    """ثبت در لاگ پایان‌بازی + ارسال زندهٔ گزارش به پیوی گاد."""
    g.night_log.append(text)
    store.save()
    try:
        await ctx.bot.send_message(g.god_id, text, parse_mode="HTML")
    except Exception:
        pass


def _night_all_done(g) -> bool:
    """آیا همهٔ اکت‌های موردانتظارِ امشب انجام شده؟ (پویا بر اساس نقش‌های زنده و شرایط)"""
    d = g.night_done or set()
    def alive(r):
        return _find_seat_by_role(g, r) is not None

    if _is_takavar_scenario(g):
        return "gunman" in d

    if _is_neg_scenario(g):
        if "mafia" not in d:
            return False
        need = set()
        if alive(_R_DETECTIVE):
            need.add("detective")
        if not g.night_is_negotiation and alive(_R_DOCTOR):
            need.add("doctor")
        if not g.sniper_used and _find_sniper(g) is not None:
            need.add("sniper")
        if getattr(g, "negotiation_used", False) and alive(_R_REPORTER):
            need.add("reporter")
        return need <= d

    if _is_baazpors_scenario(g):
        if not ({"mafia", "shiad"} <= d):
            return False
        need = set()
        if alive(_R_DETECTIVE):
            need.add("detective")
        if not g.night_doctor_blocked and alive(_R_DOCTOR):
            need.add("doctor")
        if not g.baazpors_used and alive(_R_BAAZPORS):
            need.add("baazpors")
        if not g.sniper_used and _find_seat_by_role(g, _R_SNIPER_BZP) is not None:
            need.add("sniper")
        return need <= d

    if _is_nemayande_scenario(g):
        if getattr(g, "night_awaiting_sacrifice", False):
            return False   # مین فعال شده و منتظر فدای دن‌مافیا هستیم
        if not ({"mafia", "hacker"} <= d):
            return False
        need = set()
        if not g.lawyer_used and _find_seat_role_sub(g, _R_LAWYER) is not None:
            need.add("lawyer")
        if alive(_R_GUARD):
            need.add("guard")
        if not g.night_doctor_blocked and alive(_R_DOCTOR):
            need.add("doctor")
        if alive(_R_GUIDE):
            need.add("guide")
        return need <= d

    if _is_kapu_scenario(g):
        if getattr(g, "poison_phase", False):
            return False
        if not ({"mafia", "witch"} <= d):
            return False
        need = set()
        if alive(_R_DETECTIVE):
            need.add("detective")
        if not g.night_doctor_blocked and alive(_R_ARMORER):
            need.add("armorer")
        if not g.attar_poison_used and alive(_R_ATTAR):
            need.add("attar")
        return need <= d

    if _is_gamer_scenario(g):
        if getattr(g, "gm_gift_pending", False) or getattr(g, "gm_james_waiting_don", False):
            return False
        if "citizens_opened" not in d:
            return False
        return (getattr(g, "gm_expected", set()) or set()) <= d

    return False


async def _maybe_notify_god_done(ctx, g):
    """اگر همهٔ اکت‌ها تمام شد، یک‌بار به پیوی گاد اطلاع بده تا بداند می‌تواند /روز بزند."""
    if not getattr(g, "night_active", False):
        return
    if getattr(g, "night_god_notified", False):
        return
    if not _night_all_done(g):
        return
    g.night_god_notified = True
    store.save()
    try:
        await ctx.bot.send_message(
            g.god_id, "✅ همهٔ اکت‌های امشب انجام شد. هر وقت خواستی «/روز» را بزن.")
    except Exception:
        pass


async def start_night(ctx, chat_id, g):
    is_neg = _is_neg_scenario(g)
    is_bzp = _is_baazpors_scenario(g)
    is_nem = _is_nemayande_scenario(g)
    is_tk = _is_takavar_scenario(g)
    is_kp = _is_kapu_scenario(g)
    is_gm = _is_gamer_scenario(g)
    if not (is_neg or is_bzp or is_nem or is_tk or is_kp or is_gm):
        sc = getattr(g, "scenario", None)
        await ctx.bot.send_message(
            chat_id,
            f"ℹ️ اکت خودکار برای سناریو «{escape(sc.name if sc else '—', quote=False)}» فعال نشد — "
            f"نامش با هیچ سناریوی خودکاری نمی‌خواند. با /چک بررسی کن.",
            parse_mode="HTML")
        return
    # فازهای رأی‌گیریِ روز هم قابل قبول‌اند؛ فقط قبل از شروع/بعد از پایان بازی رد می‌شود
    if g.phase in ("idle", "ended", "awaiting_winner") or not getattr(g, "assigned_roles", None):
        await ctx.bot.send_message(chat_id, "⛔ ابتدا باید بازی شروع شده باشد.")
        return
    if g.night_active:
        await ctx.bot.send_message(chat_id, "🌙 شب فعال است. برای پایان «/روز» را بنویسید.")
        return

    g.night_active = True
    g.maarefe_active = False
    g.phase = "playing"   # برگرداندنِ فاز از حالت‌های رأی‌گیریِ روز
    g.night_number = (g.night_number or 0) + 1
    g.night_is_negotiation = False
    g.night_negotiation_target = None
    g.night_shot_target = None
    g.night_doctor_blocked = False
    g.night_rest_opened = False
    g.night_shiad_guess = None
    g.night_nato_seat = None
    g.night_nato_target = None
    g.night_yakuza_sacrifice = None
    g.night_hunter_target = None
    # per-night نماینده
    g.night_hacker_actor = None
    g.night_hacker_target = None
    g.night_don_defuse = False
    g.night_don_act = None
    g.night_guide_target = None
    g.night_guide_recipient_inv = None
    g.night_nato_correct = False
    g.night_lawyer_target = None
    g.night_doc_saved = []
    g.night_sniper_target = None
    g.night_awaiting_sacrifice = False
    g.night_pending_dead = []
    g.night_pending_reasons = {}
    g.night_god_notified = False
    g.night_kick_seat = None
    g.night_mine_handled = False
    g.night_mine_sacrifice = None
    # per-night تکاور
    g.night_shield = False
    g.night_guard_seats = []
    g.night_guard_sel = {}
    g.night_hostage_seat = None
    g.night_commando_target = None
    g.night_gun1_target = None
    g.night_gun1_type = None
    g.night_gun2_target = None
    g.night_gun2_type = None
    # per-night کاپو
    g.night_jalad_target = None
    g.night_jalad_seat = None
    g.night_jalad_correct = False
    g.night_witch_target = None
    g.night_attar_poison_target = None
    g.poison_phase = False
    g.antidote_votes = {}
    g.antidote_expected = []
    # per-night گیمر (بمبِ شبِ قبل تا الان در روز تعیین‌تکلیف شده)
    g.gm_bomb_seat = None
    g.gm_bomb_fuses = {}
    g.gm_robbed_seat = None
    g.gm_gift_to = None
    g.gm_gift_accepted = False
    g.gm_gift_pending = False
    g.gm_holmes_correct = False
    g.gm_holmes_despair = None
    g.gm_moriarty_correct = False
    g.gm_moriarty_despair = False
    g.gm_eliot_protect = None
    g.gm_rick_target = None
    g.gm_expected = set()
    g.gm_james_nums = []
    g.gm_james_target = None
    g.gm_james_waiting_don = False
    g.gm_james_dice_val = None
    g.gm_tf_target = None
    g.gm_tf_map = {}
    g.gm_robin_steal_from = None
    g.night_done = set()
    g.night_sel = {}
    g.night_doc_sel = {}
    g.night_baz_sel = {}
    g.night_baz_targets = []
    g.night_pm_msgs = {}
    g.night_alive_at_start = len(_alive_seats(g))
    store.save()

    god_link = f"<a href='tg://user?id={g.god_id}'>{escape(g.god_name or 'گاد', quote=False)}</a>"
    await ctx.bot.send_message(
        chat_id,
        f"🌙 <b>شب {g.night_number} شروع شد.</b>\n"
        f"اکت خود را در پیوی بات انجام دهید (دکمه‌ها برایتان ارسال شد).\n"
        f"در صورت مشکل، اکت خود را به پیوی گاد بفرستید: {god_link}",
        parse_mode="HTML",
    )
    await _night_report(ctx, g, f"━━━━━━━━━━━━\n🌙 <b>شب {g.night_number}</b>")

    try:
        # 🔗 اتاق مافیا: باز کردن چت + حذف مافیای مرده + لینکِ معوقِ مذاکره
        await _room_sync_on_night(ctx, g)
        # ⚠️ هشدار درباره بازیکن‌هایی که بات را استارت نکرده‌اند
        await _report_unreachable(ctx, chat_id, g)

        # 👢 کیک شب (همه‌ی سناریوها): از گاد بپرس
        try:
            await ctx.bot.send_message(
                g.god_id,
                f"👢 شب {g.night_number} — آیا امشب کسی «کیکِ شب» می‌شود؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ بله", callback_data="nkick_yes")],
                    [InlineKeyboardButton("🚫 خیر", callback_data="nkick_no")],
                ]))
        except Exception:
            pass

        if is_neg:
            g.night_stage = "mafia_decision"
            store.save()
            await _night_open_mafia_decision(ctx, chat_id, g)
        elif is_bzp:
            g.night_stage = "hunter"
            store.save()
            await _bzp_open_hunter(ctx, chat_id, g)
        elif is_nem:
            g.night_stage = "mine"
            store.save()
            await _nem_open_mine(ctx, chat_id, g)
        elif is_tk:
            g.night_stage = "shield"
            store.save()
            await _tk_open_shield(ctx, chat_id, g)
        elif is_kp:
            g.night_stage = "don"
            store.save()
            if g.attar_poisoned_seat is not None:
                await _kp_begin_poison(ctx, chat_id, g)
            else:
                await _kp_open_don(ctx, chat_id, g)
        else:
            g.night_stage = "robin"
            store.save()
            await _gm_open_robin(ctx, chat_id, g)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print("❌ start_night open error:\n", tb)
        try:
            await ctx.bot.send_message(
                chat_id, f"❌ خطا هنگام باز کردن اکت‌های شب:\n<code>{escape(str(e), quote=False)}</code>",
                parse_mode="HTML")
        except Exception:
            pass


async def _night_open_mafia_decision(ctx, chat_id, g):
    gf  = _find_seat_by_role(g, _R_GODFATHER)
    neg = _find_seat_by_role(g, _R_NEGOTIATOR)
    sm  = _find_seat_by_role(g, _R_SIMPLE_MAFIA)
    decider = gf or neg or sm
    if not decider:
        await _night_report(ctx, g, "⚠️ هیچ مافیای زنده‌ای برای اکت شب نیست.")
        return
    g.night_decider_seat = decider
    can_negotiate = (not g.negotiation_used) and (neg is not None) and _dead_nonneg_mafia_exists(g)
    g.night_can_negotiate = can_negotiate
    store.save()

    duid, _dn = g.seats[decider]
    if can_negotiate:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤝 مذاکره", callback_data="night_dec_negotiate")],
            [InlineKeyboardButton("🔫 شات",    callback_data="night_dec_shoot")],
        ])
        m = await _safe_pm(ctx, duid, f"🌙 شب {g.night_number}\nمذاکره یا شات؟", kb)
    else:
        targets = list(_alive_seats(g))   # شاملِ خودِ مافیا (شاید بخواهند خودی بزنند)
        kb = _kb_night_seats(targets, g, "night_shot_", confirm_cb="night_shot_confirm")
        m = await _safe_pm(ctx, duid, f"🌙 شب {g.night_number}\n🔫 هدف شلیک را انتخاب کن:", kb)
    if m:
        g.night_pm_msgs[duid] = m.message_id
        store.save()


async def _broadcast_negotiation_night(ctx, g):
    for s in _alive_seats(g):
        u, _n = g.seats[s]
        try:
            await ctx.bot.send_message(u, "🌙 امشب مذاکره صورت می‌گیرد.")
        except Exception:
            pass


async def _night_open_citizens(ctx, chat_id, g):
    g.night_stage = "citizens"
    store.save()

    # 🔎 کاراگاه
    det = _find_seat_by_role(g, _R_DETECTIVE)
    if det:
        duid, _dn = g.seats[det]
        targets = [s for s in _alive_seats(g) if s != det]
        m = await _safe_pm(ctx, duid, "🔎 استعلام چه کسی را می‌گیری؟",
                           _kb_night_seats(targets, g, "night_det_"))
        if m:
            g.night_pm_msgs[duid] = m.message_id

    # 💉 پزشک — در شب مذاکره نیازی به سیو نیست
    if not g.night_is_negotiation:
        doc = _find_seat_by_role(g, _R_DOCTOR)
        if doc:
            duid, _dn = g.seats[doc]
            need = 2 if g.night_alive_at_start >= 8 else 1
            g.night_doc_need = need
            targets = _doctor_targets(g, doc)
            m = await _safe_pm(ctx, duid, f"💉 چه کسی را سیو می‌دهی؟ (تا {need} نفر)",
                               _kb_night_seats(targets, g, "night_doc_", selected=set(),
                                               confirm_cb="night_doc_confirm"))
            if m:
                g.night_pm_msgs[duid] = m.message_id

    # 🎯 تک‌تیرانداز — فقط اگر تیرش را استفاده نکرده
    if not g.sniper_used:
        sn = _find_seat_by_role(g, _R_SNIPER)
        if sn:
            suid, _sn = g.seats[sn]
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎯 بله، شلیک می‌کنم", callback_data="night_snipe_yes")],
                [InlineKeyboardButton("🚫 خیر",            callback_data="night_snipe_no")],
            ])
            m = await _safe_pm(ctx, suid, "🎯 امشب از تیرت استفاده می‌کنی؟", kb)
            if m:
                g.night_pm_msgs[suid] = m.message_id

    # 📰 خبرنگار — از شبِ مذاکره به بعد، هر شب اکت دارد
    # (فقط مذاکره‌شده برایش مثبت است؛ بقیه حتی مافیا منفی)
    if getattr(g, "negotiation_used", False):
        rep = _find_seat_by_role(g, _R_REPORTER)
        if rep:
            ruid, _rn = g.seats[rep]
            targets = [s for s in _alive_seats(g) if s != rep]
            m = await _safe_pm(ctx, ruid, "📰 استعلام چه کسی را می‌گیری؟",
                               _kb_night_seats(targets, g, "night_rep_"))
            if m:
                g.night_pm_msgs[ruid] = m.message_id

    store.save()


async def end_night(ctx, chat_id, g):
    if not g.night_active:
        await ctx.bot.send_message(chat_id, "ℹ️ شبی فعال نیست.")
        return
    g.night_active = False
    g.night_stage = None
    for u, mid in list((g.night_pm_msgs or {}).items()):
        try:
            await ctx.bot.edit_message_reply_markup(chat_id=u, message_id=mid, reply_markup=None)
        except Exception:
            pass
    g.night_pm_msgs = {}
    g.night_sel = {}
    g.night_doc_sel = {}
    store.save()
    await ctx.bot.send_message(chat_id, f"☀️ روز شد. اکت‌گیری شب {g.night_number} پایان یافت.")
    await _night_report(ctx, g, f"☀️ پایان شب {g.night_number}")
    # 🔒 بستن چتِ اتاق مافیا در روز
    await _room_set_locked(ctx, g, True)
    # 💀 محاسبه و خط‌زدن خودکار کشته‌های شب
    await _resolve_night(ctx, chat_id, g)


# ═════════════════════════════════════════════════════════════
#  محاسبهٔ خودکار مرگِ شب و خط‌زدن (مذاکره / بازپرس / نماینده)
# ═════════════════════════════════════════════════════════════
def _armor_kind(g, seat):
    """نوع زره: 'zereh' (زره‌پوش) یا 'rouin' (رویین‌تن) یا None."""
    rn = _seat_role_norm(g, seat)
    if rn == _R_ARMORED:
        return "zereh"
    if rn == _R_ROUIN:
        return "rouin"
    return None


def _is_saved(g, seat) -> bool:
    """آیا این صندلی سیو پزشک شده؟ (اگر هکر سیو را نبسته باشد)."""
    if seat not in (g.night_doc_saved or []):
        return False
    # هک روی سیو پزشک (فقط نماینده) → سیو باطل
    doc = _find_seat_by_role(g, _R_DOCTOR, alive_only=False)
    if (getattr(g, "night_hacker_actor", None) is not None
            and doc is not None
            and g.night_hacker_actor == doc
            and g.night_hacker_target == seat):
        return False
    return True


def _shot_outcome(g, seat):
    """نتیجهٔ شلیک روی یک صندلی:
    'saved' سیو شده | 'rouin' رویین‌تن (مصون) | 'zereh' زره‌پوش (دستِ گاد) | 'kill' کشته."""
    if _is_saved(g, seat):
        return "saved"
    kind = _armor_kind(g, seat)
    if kind == "rouin":
        return "rouin"
    if kind == "zereh":
        return "zereh"
    return "kill"


def _add_night_kick(g, dead, reasons):
    """👢 کیکِ شب (انتخابِ گاد) — جزو کشته‌های شب، ولی جدا گزارش می‌شود."""
    ks = getattr(g, "night_kick_seat", None)
    if ks and ks in g.seats and ks not in (g.striked or set()):
        dead.add(ks)
        reasons[ks] = "کیک شب"


async def _apply_deaths(ctx, chat_id, g, dead, reasons, zereh_warn=None):
    dead = {s for s in dead if s in g.seats and s not in (g.striked or set())}
    for s in dead:
        g.striked.add(s)
    store.save()
    if dead:
        def _why(s):
            return reasons.get(s) or reasons.get(str(s)) or ""
        kick_list = [s for s in sorted(dead) if _why(s) == "کیک شب"]
        kill_list = [s for s in sorted(dead) if s not in kick_list]
        lines = []
        if kill_list:
            lines.append("💀 <b>کشته‌های شب</b>:")
            for s in kill_list:
                nm = g.seats[s][1]
                why = _why(s)
                lines.append(f"• {s}. {escape(nm, quote=False)}" + (f" — {why}" if why else ""))
        if kick_list:
            lines.append("👢 <b>کیک شب</b>:")
            mafia_all = _mafia_seats(g)   # شامل جذب‌شده‌ها (مذاکره/یاکوزایی) هم می‌شود
            for s in kick_list:
                side = "مافیا" if s in mafia_all else "شهر"
                lines.append(f"• {s}. {escape(g.seats[s][1], quote=False)} ({side})")
        await _night_report(ctx, g, "\n".join(lines))
        await ctx.bot.send_message(chat_id, f"💀 {len(dead)} نفر امشب از لیست خط خوردند.")
    else:
        await _night_report(ctx, g, "💀 امشب کسی کشته نشد.")
        await ctx.bot.send_message(chat_id, "🕊 امشب کسی کشته نشد.")
    # ⚠️ هشدار زره‌پوش (بات خودکار خط نزد؛ تصمیم با گاد)
    for s in (zereh_warn or []):
        nm = g.seats[s][1]
        await _night_report(
            ctx, g,
            f"⚠️ <b>مهم — زره‌پوش:</b> مافیا به <b>{s}. {escape(nm, quote=False)}</b> شلیک کرد.\n"
            f"چون زره‌اش ممکن است با اجماعِ روز افتاده باشد، بات خودکار خط نزد. "
            f"اگر شات درست است، خودت از لیست خط بزن."
        )
    try:
        await publish_seating(ctx, chat_id, g, mode=CTRL)
    except Exception:
        pass


async def _resolve_night(ctx, chat_id, g):
    if _is_neg_scenario(g):
        await _resolve_mozakere(ctx, chat_id, g)
    elif _is_baazpors_scenario(g):
        await _resolve_baazpors(ctx, chat_id, g)
    elif _is_nemayande_scenario(g):
        await _resolve_nemayande(ctx, chat_id, g)
    elif _is_takavar_scenario(g):
        await _resolve_takavar(ctx, chat_id, g)
    elif _is_kapu_scenario(g):
        await _resolve_kapu(ctx, chat_id, g)
    elif _is_gamer_scenario(g):
        await _resolve_gamer(ctx, chat_id, g)


async def _resolve_gamer(ctx, chat_id, g):
    dead, reasons = set(), {}
    don = _find_seat_by_role(g, _R_DONC)
    logan = _find_seat_by_role(g, _R_LOGAN)
    holmes = _find_seat_by_role(g, _R_HOLMES)
    moriarty = _find_seat_by_role(g, _R_MORIARTY)
    robbed_ok = g.gm_gift_accepted   # سرقتِ پذیرفته‌شده

    # 🔫 شلیک مافیا — لوگان (یا گیرنده‌ی قدرتش) نامیرا؛ سیو کستیل
    st = g.night_shot_target
    if st and st in g.seats:
        immortal = False
        if logan is not None and st == logan and not (robbed_ok and g.gm_robbed_seat == logan):
            immortal = True   # لوگان نامیرای شب (مگر امشب دزدیده شده باشد)
        if robbed_ok and g.gm_robbed_seat == logan and st == g.gm_gift_to:
            immortal = True   # گیرنده‌ی هدیه، نامیرایی لوگان را دارد
        if don is not None and st == don and not (robbed_ok and g.gm_robbed_seat == don):
            immortal = True   # دن نامیرای شب است (مگر امشب دزدیده شده باشد)
        if not immortal and not _is_saved(g, st):
            dead.add(st); reasons[st] = "شلیک مافیا"

    # 🔫 شلیک ریک‌گرایمز — همه می‌میرند جز دن (اگر دزدیده نشده) و سیوشده
    rt = g.gm_rick_target
    if rt and rt in g.seats:
        don_immune = (don is not None and rt == don
                      and not (robbed_ok and g.gm_robbed_seat == don))
        if not don_immune and not _is_saved(g, rt):
            dead.add(rt); reasons[rt] = "شلیک ریک‌گرایمز"

    # ⚰️ یأس هلمز (سومین حدسِ غلط) — قطعی، حتی با سیو
    if g.gm_holmes_despair and g.gm_holmes_despair in g.seats:
        dead.add(g.gm_holmes_despair); reasons[g.gm_holmes_despair] = "یأسِ هلمز (سه حدسِ غلط)"

    # ⚰️ حدسِ درستِ موریارتی — هلمز قطعی می‌میرد
    if g.gm_moriarty_correct and holmes is not None and holmes in g.seats:
        dead.add(holmes); reasons[holmes] = "حدسِ درستِ موریارتی"

    # ⚰️ یأس موریارتی (سومین حدسِ غلط)
    if g.gm_moriarty_despair and moriarty is not None and moriarty in g.seats:
        dead.add(moriarty); reasons[moriarty] = "یأسِ موریارتی (سه حدسِ غلط)"

    # 💣 محافظت الیوت روی بمب — بمب بی‌سروصدا بی‌اثر می‌شود
    if g.gm_bomb_seat is not None and g.gm_eliot_protect == g.gm_bomb_seat:
        await _night_report(ctx, g, f"🛡 بمبِ جلوی صندلی {g.gm_bomb_seat} با محافظتِ الیوت بی‌اثر شد.")
        g.gm_bomb_seat = None
        g.gm_bomb_fuses = {}
        store.save()

    _add_night_kick(g, dead, reasons)
    await _apply_deaths(ctx, chat_id, g, dead, reasons)


async def _resolve_kapu(ctx, chat_id, g):
    dead, reasons = set(), {}
    # ⚔️ جلادیِ درست → خروج تحت هر شرایطی (زره/سیو/نامیرایی را رد می‌کند)
    if g.night_jalad_target and g.night_jalad_target in g.seats and g.night_jalad_correct:
        dead.add(g.night_jalad_target)
        reasons[g.night_jalad_target] = "جلادیِ درست"
    # 🔫 شلیک مافیا
    st = g.night_shot_target
    if st and st in g.seats and st not in dead:
        if _kp_heir_immune(g, st):
            pass  # وارثِ نامیرا
        elif not _is_saved(g, st):
            dead.add(st); reasons[st] = "شلیک مافیا"
    # 🥷 فدای یاکوزایی
    yk = g.night_yakuza_sacrifice
    if yk and yk in g.seats:
        dead.add(yk); reasons[yk] = "فدای یاکوزایی"

    _add_night_kick(g, dead, reasons)
    await _apply_deaths(ctx, chat_id, g, dead, reasons)

    # ⚱️ ارث‌بریِ وارث (اگر فردِ انتخابی امشب مُرد)
    await _kp_check_heir_inherit(ctx, g)


async def _resolve_takavar(ctx, chat_id, g):
    dead, reasons = set(), {}
    st = g.night_shot_target
    com = _find_seat_by_role(g, _R_COMMANDO, alive_only=False)
    countered = (st is not None and st == com and g.night_commando_target is not None)

    if st and st in g.seats:
        if countered:
            ct = g.night_commando_target
            if ct in _mafia_seats(g) and _seat_role_norm(g, ct) != _R_GODFATHER:
                # مافیای غیرگادفادر می‌میرد مگر سیو اشتباه پزشک؛ تکاور زنده می‌ماند
                if not _is_saved(g, ct):
                    dead.add(ct); reasons[ct] = "شلیک متقابل تکاور"
            elif _seat_role_norm(g, ct) == _R_GODFATHER:
                pass  # گادفادر مصون؛ هیچکس نمی‌میرد؛ تکاور زنده
            else:
                # تکاور به شهروند زد → فقط خودِ تکاور می‌میرد
                dead.add(st); reasons[st] = "تکاور به شهروند شلیک کرد و خودش کشته شد"
        else:
            # تکاور ضدشلیک نزد → شلیک عادی
            if not _is_saved(g, st):
                dead.add(st); reasons[st] = "شلیک مافیا"

    nt = g.night_nato_seat
    if nt and nt in g.seats and g.night_nato_correct:
        dead.add(nt); reasons[nt] = "ناتویی درست"

    _add_night_kick(g, dead, reasons)
    await _apply_deaths(ctx, chat_id, g, dead, reasons)

    # 🔫 برگشت تفنگ جنگی اگر دارنده‌اش کشته شد
    if g.war_gun_holder and g.war_gun_holder in (g.striked or set()):
        g.war_gun_used = False
        g.war_gun_holder = None
        store.save()
        await _night_report(ctx, g, "🔫 دارندهٔ تفنگ جنگی کشته شد؛ تفنگ به تفنگدار برگشت.")


async def _resolve_mozakere(ctx, chat_id, g):
    dead, reasons, zereh = set(), {}, []
    st = g.night_shot_target
    if st and st in g.seats:
        out = _shot_outcome(g, st)
        if out == "kill":
            dead.add(st); reasons[st] = "شلیک مافیا"
        elif out == "zereh":
            zereh.append(st)
    _resolve_sniper(g, dead, reasons)
    _add_night_kick(g, dead, reasons)
    await _apply_deaths(ctx, chat_id, g, dead, reasons, zereh)


def _resolve_sniper(g, dead, reasons):
    """قانون تک‌تیرانداز/اسنایپر: مافیای غیرگادفادر می‌میرد (مگر سیو)؛ اگر شهروند بزند خودِ اسنایپر."""
    sn_t = g.night_sniper_target
    if not (sn_t and sn_t in g.seats):
        return
    sniper_seat = _find_sniper(g, alive_only=False)
    if sn_t in _mafia_seats(g) and _seat_role_norm(g, sn_t) != _R_GODFATHER:
        if not _is_saved(g, sn_t):
            dead.add(sn_t); reasons[sn_t] = "شلیک اسنایپر"
    elif _seat_role_norm(g, sn_t) == _R_GODFATHER:
        pass  # اسنایپر نمی‌تواند گادفادر را بکشد
    else:
        if sniper_seat:
            dead.add(sniper_seat); reasons[sniper_seat] = "اسنایپر به شهروند شلیک کرد"


async def _resolve_baazpors(ctx, chat_id, g):
    dead, reasons, zereh = set(), {}, []
    st = g.night_shot_target
    if st and st in g.seats:
        out = _shot_outcome(g, st)
        if out == "kill":
            dead.add(st); reasons[st] = "شلیک مافیا"
        elif out == "zereh":
            zereh.append(st)
    nt = g.night_nato_target
    if nt and nt in g.seats and g.night_nato_correct:
        dead.add(nt); reasons[nt] = "ناتویی درست"
    yk = g.night_yakuza_sacrifice
    if yk and yk in g.seats:
        dead.add(yk); reasons[yk] = "فدای یاکوزایی"
    _resolve_sniper(g, dead, reasons)   # اسنایپر بازپرس ۱۲/۱۳ نفره

    # 🪢 هانتر: اگر امشب کشته شود و به مافیایی جز گادفادر بسته باشد، آن فرد را با خود می‌برد
    hunter = _find_seat_by_role(g, _R_HUNTER, alive_only=False)
    ht = g.night_hunter_target
    if hunter is not None and hunter in dead and ht and ht in g.seats and ht not in dead:
        draggable = (ht in _mafia_seats(g)) and (_seat_role_norm(g, ht) != _R_GODFATHER)
        if draggable:
            dead.add(ht); reasons[ht] = "هانتر با خود برد"

    _add_night_kick(g, dead, reasons)   # کیکِ شب هم کشته‌ی شب حساب می‌شود

    # 🧑‍⚖️ اگر یکی از احضارشدگانِ بازپرس همین امشب کشته شود (به هر شکلی، حتی کیک)،
    #     خطِ بازپرسی می‌شکند و بازپرس شبِ بعد دوباره حقِ اکت دارد
    bt = getattr(g, "night_baz_targets", []) or []
    if bt and any(t in dead for t in bt):
        g.baazpors_used = False
        await _night_report(ctx, g, "🧑‍⚖️ یکی از احضارشدگان به بازپرسی امشب کشته شد — "
                            "خطِ بازپرسی شکست؛ بازپرس شبِ بعد دوباره اکت دارد.")

    await _apply_deaths(ctx, chat_id, g, dead, reasons, zereh)


async def _resolve_nemayande(ctx, chat_id, g):
    dead, reasons, zereh = set(), {}, []
    target = None
    if g.night_don_act == "shot":
        target = g.night_shot_target
    elif g.night_don_act == "nato":
        target = g.night_nato_seat
    mine_hit = (target is not None and g.mine_seat is not None
                and target == g.mine_seat and not g.night_don_defuse)

    if g.night_don_act == "nato":
        if mine_hit:
            dead.add(target); reasons[target] = "مین + ناتویی"
        elif g.night_nato_correct:
            dead.add(target); reasons[target] = "ناتویی درست"
    elif g.night_don_act == "shot":
        if mine_hit:
            dead.add(target); reasons[target] = "مین + شلیک"
        elif target:
            out = _shot_outcome(g, target)
            if out == "kill":
                dead.add(target)
                reasons[target] = "شلیک دن‌مافیا" + (" (با خنثی)" if g.night_don_defuse else "")
            elif out == "zereh":
                zereh.append(target)

    # فدای مین (قبلاً بعد از اکت راهنما انتخاب شده)
    if mine_hit and g.night_mine_sacrifice and g.night_mine_sacrifice in g.seats:
        dead.add(g.night_mine_sacrifice); reasons[g.night_mine_sacrifice] = "فدای مین"

    _add_night_kick(g, dead, reasons)   # کیکِ شب هم کشته‌ی شب حساب می‌شود

    # مصرف وکالت: اگر موکل همان شب کشته نشد → وکالت مصرف می‌شود
    if g.night_lawyer_target is not None:
        if g.night_lawyer_target not in dead:
            g.lawyer_used = True
        store.save()

    await _apply_deaths(ctx, chat_id, g, dead, reasons, zereh)


async def handle_night_callback(update, ctx):
    q = update.callback_query
    data = q.data
    uid = q.from_user.id

    # پیدا کردن بازیِ فعالِ شب که این کاربر در آن بازیکن است
    g = None
    chat_id = None
    candidates = []
    for cid, game in store.games.items():
        if not (getattr(game, "night_active", False)
                or getattr(game, "night_awaiting_sacrifice", False)
                or getattr(game, "maarefe_active", False)):
            continue
        if _seat_of_uid(game, uid) is not None:
            candidates.append((cid, game))
    for cid, game in candidates:
        if q.message and (game.night_pm_msgs or {}).get(uid) == q.message.message_id:
            g, chat_id = game, cid
            break
    if g is None and candidates:
        chat_id, g = candidates[0]
    if g is None:
        await safe_q_answer(q, "بازی فعالی یافت نشد.", show_alert=True)
        return

    await safe_q_answer(q)
    mid = q.message.message_id if q.message else None

    # ── تصمیم مافیا: مذاکره / شات ──
    if data == "night_dec_shoot":
        g.night_is_negotiation = False
        store.save()
        targets = list(_alive_seats(g))   # شاملِ خودِ مافیا
        await _edit_pm(ctx, uid, mid, "🔫 هدف شلیک را انتخاب کن:",
                       _kb_night_seats(targets, g, "night_shot_",
                                       selected=g.night_sel.get(uid), confirm_cb="night_shot_confirm"))
        return

    if data == "night_dec_negotiate":
        neg = _find_seat_by_role(g, _R_NEGOTIATOR)
        if neg is None:
            await _close_pm(ctx, uid, mid, "⚠️ مذاکره‌کننده‌ای زنده نیست.")
            await _night_report(ctx, g, "⚠️ مذاکره ممکن نشد (مذاکره‌کننده زنده نیست).")
            return
        g.night_is_negotiation = True
        g.night_stage = "negotiator_pick"
        g.negotiation_used = True   # مذاکره مثل تک‌تیر؛ به‌محض انتخاب گادفادر مصرف می‌شود
        store.save()
        neg_uid, _nn = g.seats[neg]
        targets = [s for s in _alive_seats(g) if s not in _mafia_seats(g, alive_only=True)]
        kb = _kb_night_seats(targets, g, "night_neg_",
                             selected=g.night_sel.get(neg_uid), confirm_cb="night_neg_confirm")
        if neg_uid == uid:
            await _edit_pm(ctx, uid, mid, "🤝 با چه کسی مذاکره می‌کنی؟", kb)
            g.night_pm_msgs[neg_uid] = mid
        else:
            await _close_pm(ctx, uid, mid, "🤝 مذاکره انتخاب شد. منتظر مذاکره‌کننده بمانید.")
            m = await _safe_pm(ctx, neg_uid, "🤝 با چه کسی مذاکره می‌کنی؟", kb)
            if m:
                g.night_pm_msgs[neg_uid] = m.message_id
        store.save()
        return

    # ── شلیک مافیا (decider) ──
    if data == "night_shot_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_shot_target = s
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, f"✅ شلیک ثبت شد: {s}. {tname}")
        await _night_report(ctx, g, f"🔫 شلیک مافیا → <b>{s}. {escape(tname, quote=False)}</b>")
        g.night_done.add("mafia")
        store.save()
        await _night_open_citizens(ctx, chat_id, g)
        return

    if data.startswith("night_shot_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = list(_alive_seats(g))   # شاملِ خودِ مافیا
        await _edit_pm(ctx, uid, mid, "🔫 هدف شلیک را انتخاب کن:",
                       _kb_night_seats(targets, g, "night_shot_", selected=s, confirm_cb="night_shot_confirm"))
        return

    # ── مذاکره (negotiator) ──
    if data == "night_neg_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_negotiation_target = s
        g.negotiation_used = True
        tu, tname = g.seats[s]
        rn = _seat_role_norm(g, s)
        converted = (rn in _R_CITIZEN) or (rn == _R_ARMORED)
        if converted:
            g.negotiated_seats.add(s)
            try:
                await ctx.bot.send_message(tu, "🤝 با شما مذاکره شد. اکنون «مافیا ساده» هستید.")
            except Exception:
                pass
            # لینک اتاق مافیا با یک شب تأخیر ارسال می‌شود
            if g.mafia_room_id and tu not in (g.mafia_room_pending_link or []):
                g.mafia_room_pending_link.append(tu)
            await _night_report(ctx, g, f"🤝 مذاکره با <b>{s}. {escape(tname, quote=False)}</b> → تبدیل به مافیا ساده ✅")
        else:
            await _night_report(ctx, g, f"🤝 مذاکره با <b>{s}. {escape(tname, quote=False)}</b> → نقش قابل جذب نبود ❌")
        await _close_pm(ctx, uid, mid, "✅ مذاکره ثبت شد.")
        g.night_done.add("mafia")
        store.save()
        await _broadcast_negotiation_night(ctx, g)
        await _night_open_citizens(ctx, chat_id, g)
        return

    if data.startswith("night_neg_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = [x for x in _alive_seats(g) if x not in _mafia_seats(g, alive_only=True)]
        await _edit_pm(ctx, uid, mid, "🤝 با چه کسی مذاکره می‌کنی؟",
                       _kb_night_seats(targets, g, "night_neg_", selected=s, confirm_cb="night_neg_confirm"))
        return

    # ── کاراگاه (مستقیم) ──
    if data.startswith("night_det_"):
        s = int(data.rsplit("_", 1)[1])
        _tu, tname = g.seats[s]
        res = "مثبت ✅" if _detective_positive(g, s) else "منفی ❌"
        await _close_pm(ctx, uid, mid, f"🔎 استعلام {s}. {tname}: {res}")
        await _night_report(ctx, g, f"🔎 کاراگاه → استعلام {s}. {escape(tname, quote=False)}: <b>{res}</b>")
        g.night_done.add("detective")
        store.save()
        return

    # ── خبرنگار (مستقیم) ──
    if data.startswith("night_rep_"):
        s = int(data.rsplit("_", 1)[1])
        _tu, tname = g.seats[s]
        res = "مثبت ✅" if _reporter_positive(g, s) else "منفی ❌"
        await _close_pm(ctx, uid, mid, f"📰 استعلام {s}. {tname}: {res}")
        await _night_report(ctx, g, f"📰 خبرنگار → استعلام {s}. {escape(tname, quote=False)}: <b>{res}</b>")
        g.night_done.add("reporter")
        store.save()
        return

    # ── پزشک (چندانتخابی) ──
    if data == "night_doc_confirm":
        sel = list(g.night_doc_sel.get(uid, []))
        if not sel:
            await safe_q_answer(q, "حداقل یک نفر را انتخاب کن.", show_alert=True)
            return
        doc = _seat_of_uid(g, uid)
        if doc in sel:
            g.doctor_self_saves = (g.doctor_self_saves or 0) + 1
        g.night_doc_saved = list(sel)
        names = "، ".join(f"{s}. {g.seats[s][1]}" for s in sel)
        await _close_pm(ctx, uid, mid, f"💉 سیو ثبت شد: {names}")
        await _night_report(ctx, g, f"💉 پزشک → سیو: <b>{escape(names, quote=False)}</b>")
        g.night_done.add("doctor")
        store.save()
        return

    if data.startswith("night_doc_"):
        s = int(data.rsplit("_", 1)[1])
        sel = set(g.night_doc_sel.get(uid, []))
        need = g.night_doc_need or 1
        if s in sel:
            sel.remove(s)
        elif len(sel) >= need:
            await safe_q_answer(q, f"حداکثر {need} نفر.", show_alert=True)
            return
        else:
            sel.add(s)
        g.night_doc_sel[uid] = list(sel)
        store.save()
        doc = _seat_of_uid(g, uid)
        await _edit_pm(ctx, uid, mid, f"💉 چه کسی را سیو می‌دهی؟ (تا {need} نفر)",
                       _kb_night_seats(_doctor_targets(g, doc), g, "night_doc_",
                                       selected=sel, confirm_cb="night_doc_confirm"))
        return

    # ── تک‌تیرانداز ──
    if data == "night_snipe_no":
        await _close_pm(ctx, uid, mid, "🚫 از تیر استفاده نکردی.")
        await _night_report(ctx, g, "🎯 تک‌تیرانداز → شلیک نکرد")
        g.night_done.add("sniper")
        store.save()
        return

    if data == "night_snipe_yes":
        sn = _seat_of_uid(g, uid)
        targets = [s for s in _alive_seats(g) if s != sn]
        await _edit_pm(ctx, uid, mid, "🎯 به چه کسی شلیک می‌کنی؟",
                       _kb_night_seats(targets, g, "night_snipe_",
                                       selected=g.night_sel.get(uid), confirm_cb="night_snipe_confirm"))
        return

    if data == "night_snipe_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.sniper_used = True
        g.night_sniper_target = s
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, f"🎯 شلیک ثبت شد: {s}. {tname}")
        await _night_report(ctx, g, f"🎯 تک‌تیرانداز → شلیک به <b>{s}. {escape(tname, quote=False)}</b>")
        g.night_done.add("sniper")
        store.save()
        return

    if data.startswith("night_snipe_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        sn = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != sn]
        await _edit_pm(ctx, uid, mid, "🎯 به چه کسی شلیک می‌کنی؟",
                       _kb_night_seats(targets, g, "night_snipe_", selected=s, confirm_cb="night_snipe_confirm"))
        return


# ═════════════════════════════════════════════════════════════
#  موتور شبِ خودکار — سناریو «بازپرس»
#  ترتیب اکت: هانتر → (گادفادر + شیاد) → بقیه (کاراگاه/پزشک/بازپرس)
# ═════════════════════════════════════════════════════════════
BAAZPORS_KEY = "بازپرس"
_R_NATO       = _nz("ناتو")
_R_SHIAD      = _nz("شیاد")
_R_HUNTER     = _nz("هانتر")
_R_BAAZPORS   = _nz("بازپرس")
_R_ROUIN      = _nz("رویین‌تن")
_R_SNIPER_BZP = _nz("اسنایپر")   # اسنایپر بازپرس (۱۲/۱۳ نفره) — مثل تک‌تیرانداز مذاکره
_BZP_CITIZEN_ROLE_NAMES = ["شهرساده", "رویین‌تن", "کاراگاه", "هانتر", "پزشک", "بازپرس", "اسنایپر"]


def _find_sniper(g, alive_only=True):
    """اسنایپر/تک‌تیرانداز (هر سناریو)."""
    return (_find_seat_by_role(g, _R_SNIPER, alive_only)
            or _find_seat_by_role(g, _R_SNIPER_BZP, alive_only))


def _is_baazpors_scenario(g) -> bool:
    return bool(getattr(g, "scenario", None)) and (_nz(BAAZPORS_KEY) in _nz(g.scenario.name))


def _find_active_night_game(uid, q):
    """پیدا کردن بازیِ فعالِ شب که این کاربر در آن بازیکن است (مشترک بین موتورها)."""
    candidates = []
    for cid, game in store.games.items():
        if not (getattr(game, "night_active", False)
                or getattr(game, "night_awaiting_sacrifice", False)
                or getattr(game, "maarefe_active", False)):
            continue
        if _seat_of_uid(game, uid) is not None:
            candidates.append((cid, game))
    for cid, game in candidates:
        if q.message and (game.night_pm_msgs or {}).get(uid) == q.message.message_id:
            return game, cid
    if candidates:
        return candidates[0][1], candidates[0][0]
    return None, None


def _bzp_detective_positive(g, seat) -> bool:
    rn = _seat_role_norm(g, seat)
    # نقش‌هایی که معمولاً مثبت‌اند: شیاد، ناتو، و فرد یاکوزایی‌شده
    positive_role = (seat in (g.negotiated_seats or set())) or (rn in (_R_SHIAD, _R_NATO))
    if not positive_role:
        return False  # گادفادر و شهروندان منفی
    # اگر شیاد کاراگاه را درست حدس زده باشد → استتار (هر سه منفی می‌شوند)
    det = _find_seat_by_role(g, _R_DETECTIVE)
    if g.night_shiad_guess is not None and det is not None and g.night_shiad_guess == det:
        return False
    return True


async def _bzp_broadcast_special(ctx, g, kind):
    """kind = «یاکوزایی» یا «ناتویی». به همه اطلاع می‌دهد و به دکتر می‌گوید استراحت کند."""
    doc = _find_seat_by_role(g, _R_DOCTOR)
    for s in _alive_seats(g):
        u = g.seats[s][0]
        try:
            await ctx.bot.send_message(u, f"🌙 امشب {kind} صورت می‌گیرد.")
        except Exception:
            pass
    # به دکتر جداگانه: امشب استراحت
    if doc:
        try:
            await ctx.bot.send_message(g.seats[doc][0], "💤 امشب استراحت کن — حق سیو نداری.")
        except Exception:
            pass


async def _bzp_open_hunter(ctx, chat_id, g):
    h = _find_seat_by_role(g, _R_HUNTER)
    if not h:
        g.night_done.add("hunter")
        store.save()
        await _bzp_open_gf_shiad(ctx, chat_id, g)
        return
    huid, _hn = g.seats[h]
    targets = [s for s in _alive_seats(g) if s != h]
    m = await _safe_pm(ctx, huid, "🪢 خودت را به چه کسی می‌بندی؟",
                       _kb_night_seats(targets, g, "bzp_hunt_",
                                       selected=g.night_sel.get(huid), confirm_cb="bzp_hunt_confirm"))
    if m:
        g.night_pm_msgs[huid] = m.message_id
    store.save()


async def _bzp_open_gf_shiad(ctx, chat_id, g):
    # ── تصمیم‌گیرندهٔ مافیا: گادفادر → ناتو → شیاد → فرد یاکوزایی‌شده ──
    gf_alive = _find_seat_by_role(g, _R_GODFATHER)
    nato_alive = _find_seat_by_role(g, _R_NATO)
    shiad_alive = _find_seat_by_role(g, _R_SHIAD)
    converted = sorted(s for s in (g.negotiated_seats or set()) if s not in (g.striked or set()))
    decider = gf_alive or nato_alive or shiad_alive or (converted[0] if converted else None)
    if not decider:
        g.night_done.add("mafia")
    else:
        g.bzp_decider_seat = decider
        duid, _dn = g.seats[decider]
        rows = [[InlineKeyboardButton("🔫 شات", callback_data="bzp_gf_shoot")]]
        # یاکوزایی فقط وقتی گادفادر زنده است و مصرف نشده
        if gf_alive is not None and not g.yakuza_used:
            rows.append([InlineKeyboardButton("🥷 یاکوزایی", callback_data="bzp_gf_yakuza")])
        # ناتویی فقط وقتی ناتو زنده است
        if nato_alive is not None:
            rows.append([InlineKeyboardButton("🕵️ ناتویی", callback_data="bzp_gf_nato")])
        m = await _safe_pm(ctx, duid, f"🌙 شب {g.night_number}\nاکت مافیا را انتخاب کن:",
                           InlineKeyboardMarkup(rows))
        if m:
            g.night_pm_msgs[duid] = m.message_id

    # ── شیاد (حدس شمارهٔ کاراگاه) ──
    sh = _find_seat_by_role(g, _R_SHIAD)
    if not sh:
        g.night_done.add("shiad")
    else:
        suid, _sn = g.seats[sh]
        targets = [s for s in _alive_seats(g) if s not in _mafia_seats(g, alive_only=True)]
        m = await _safe_pm(ctx, suid, "🎭 حدس بزن کدام شماره کاراگاه است:",
                           _kb_night_seats(targets, g, "bzp_shiad_",
                                           selected=g.night_sel.get(suid), confirm_cb="bzp_shiad_confirm"))
        if m:
            g.night_pm_msgs[suid] = m.message_id
    store.save()
    await _bzp_check_open_rest(ctx, chat_id, g)


async def _bzp_check_open_rest(ctx, chat_id, g):
    """بعد از اینکه هم اکت مافیا (گادفادر) و هم شیاد تمام شد، بقیهٔ اکت‌ها باز می‌شوند."""
    if g.night_rest_opened:
        return
    if "mafia" not in g.night_done or "shiad" not in g.night_done:
        return
    g.night_rest_opened = True
    store.save()

    # 🔎 کاراگاه
    det = _find_seat_by_role(g, _R_DETECTIVE)
    if det:
        duid, _dn = g.seats[det]
        targets = [s for s in _alive_seats(g) if s != det]
        m = await _safe_pm(ctx, duid, "🔎 استعلام چه کسی را می‌گیری؟",
                           _kb_night_seats(targets, g, "bzp_det_"))
        if m:
            g.night_pm_msgs[duid] = m.message_id

    # 💉 پزشک — در شب یاکوزایی/ناتویی حق سیو ندارد
    if not g.night_doctor_blocked:
        doc = _find_seat_by_role(g, _R_DOCTOR)
        if doc:
            duid, _dn = g.seats[doc]
            g.night_doc_need = 1
            targets = _doctor_targets(g, doc)
            m = await _safe_pm(ctx, duid, "💉 چه کسی را سیو می‌دهی؟ (۱ نفر)",
                               _kb_night_seats(targets, g, "bzp_doc_", selected=set(),
                                               confirm_cb="bzp_doc_confirm"))
            if m:
                g.night_pm_msgs[duid] = m.message_id

    # 🧑‍⚖️ بازپرس — یکبار در کل بازی
    if not g.baazpors_used:
        bz = _find_seat_by_role(g, _R_BAAZPORS)
        if bz:
            buid, _bn = g.seats[bz]
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ بله", callback_data="bzp_baz_yes")],
                [InlineKeyboardButton("🚫 خیر", callback_data="bzp_baz_no")],
            ])
            m = await _safe_pm(ctx, buid, "🧑‍⚖️ امشب از حق بازپرسی استفاده می‌کنی؟", kb)
            if m:
                g.night_pm_msgs[buid] = m.message_id

    # 🎯 اسنایپر (فقط ۱۲/۱۳ نفره) — یک تیر در کل بازی
    if not g.sniper_used:
        sn = _find_seat_by_role(g, _R_SNIPER_BZP)
        if sn:
            suid, _sn2 = g.seats[sn]
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎯 بله، شلیک می‌کنم", callback_data="bzp_snipe_yes")],
                [InlineKeyboardButton("🚫 خیر",            callback_data="bzp_snipe_no")],
            ])
            m = await _safe_pm(ctx, suid, "🎯 امشب از تیرت استفاده می‌کنی؟", kb)
            if m:
                g.night_pm_msgs[suid] = m.message_id
    store.save()


async def handle_baazpors_callback(update, ctx):
    q = update.callback_query
    data = q.data
    uid = q.from_user.id
    g, chat_id = _find_active_night_game(uid, q)
    if g is None:
        await safe_q_answer(q, "بازی فعالی یافت نشد.", show_alert=True)
        return
    await safe_q_answer(q)
    mid = q.message.message_id if q.message else None

    # ── هانتر ──
    if data == "bzp_hunt_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_hunter_target = s
        _tu, tname = g.seats[s]
        # درست = هر مافیایی جز گادفادر (شیاد، ناتو، مافیا ساده، یاکوزایی‌شده)
        correct = (s in _mafia_seats(g)) and (_seat_role_norm(g, s) != _R_GODFATHER)
        tick = "✅" if correct else "❌"
        await _close_pm(ctx, uid, mid, f"🪢 خودت را به {s}. {tname} بستی.")
        await _night_report(ctx, g, f"🪢 هانتر → بست به {s}. {escape(tname, quote=False)} {tick}")
        g.night_done.add("hunter")
        store.save()
        await _bzp_open_gf_shiad(ctx, chat_id, g)
        return

    if data.startswith("bzp_hunt_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        h = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != h]
        await _edit_pm(ctx, uid, mid, "🪢 خودت را به چه کسی می‌بندی؟",
                       _kb_night_seats(targets, g, "bzp_hunt_", selected=s, confirm_cb="bzp_hunt_confirm"))
        return

    # ── تصمیم گادفادر ──
    if data == "bzp_gf_shoot":
        targets = list(_alive_seats(g))   # شاملِ خودِ مافیا
        await _edit_pm(ctx, uid, mid, "🔫 هدف شلیک را انتخاب کن:",
                       _kb_night_seats(targets, g, "bzp_shot_",
                                       selected=g.night_sel.get(uid), confirm_cb="bzp_shot_confirm"))
        return

    if data == "bzp_gf_yakuza":
        g.yakuza_used = True
        store.save()
        me = _seat_of_uid(g, uid)
        teammates = [s for s in _mafia_seats(g, alive_only=True) if s != me]
        if not teammates:
            g.night_yakuza_sacrifice = me
            g.night_sel.pop(uid, None)
            store.save()
            targets = [s for s in _alive_seats(g) if s not in _mafia_seats(g, alive_only=True)]
            await _edit_pm(ctx, uid, mid, "🥷 یاری نداری؛ خودت فدا می‌شوی.\nبا چه کسی یاکوزایی می‌کنی؟",
                           _kb_night_seats(targets, g, "bzp_yakrec_", confirm_cb="bzp_yakrec_confirm"))
        else:
            await _edit_pm(ctx, uid, mid, "🥷 کدام یارت را فدا می‌کنی؟",
                           _kb_night_seats(teammates, g, "bzp_yaksac_",
                                           selected=g.night_sel.get(uid), confirm_cb="bzp_yaksac_confirm"))
        return

    if data == "bzp_gf_nato":
        nato = _find_seat_by_role(g, _R_NATO)
        if nato is None:
            await safe_q_answer(q, "ناتو زنده نیست.", show_alert=True)
            return
        nato_uid, _nn = g.seats[nato]
        targets = [s for s in _alive_seats(g) if s not in _mafia_seats(g, alive_only=True)]
        kb = _kb_night_seats(targets, g, "bzp_nato_",
                             selected=g.night_sel.get(nato_uid), confirm_cb="bzp_nato_confirm")
        if nato_uid == uid:
            await _edit_pm(ctx, uid, mid, "🕵️ چه کسی را ناتویی می‌کنی؟", kb)
            g.night_pm_msgs[nato_uid] = mid
        else:
            await _close_pm(ctx, uid, mid, "🕵️ ناتویی انتخاب شد. منتظر ناتو بمانید.")
            m = await _safe_pm(ctx, nato_uid, "🕵️ چه کسی را ناتویی می‌کنی؟", kb)
            if m:
                g.night_pm_msgs[nato_uid] = m.message_id
        store.save()
        return

    # ── شلیک گادفادر ──
    if data == "bzp_shot_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_shot_target = s
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, f"✅ شلیک ثبت شد: {s}. {tname}")
        await _night_report(ctx, g, f"🔫 شلیک مافیا → <b>{s}. {escape(tname, quote=False)}</b>")
        g.night_done.add("mafia")
        store.save()
        await _bzp_check_open_rest(ctx, chat_id, g)
        return

    if data.startswith("bzp_shot_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = list(_alive_seats(g))   # شاملِ خودِ مافیا
        await _edit_pm(ctx, uid, mid, "🔫 هدف شلیک را انتخاب کن:",
                       _kb_night_seats(targets, g, "bzp_shot_", selected=s, confirm_cb="bzp_shot_confirm"))
        return

    # ── یاکوزایی: فدا کردن یار ──
    if data == "bzp_yaksac_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک یار را انتخاب کن.", show_alert=True)
            return
        g.night_yakuza_sacrifice = s
        g.night_sel.pop(uid, None)
        store.save()
        targets = [x for x in _alive_seats(g) if x not in _mafia_seats(g, alive_only=True)]
        await _edit_pm(ctx, uid, mid, "🥷 با چه کسی یاکوزایی می‌کنی؟",
                       _kb_night_seats(targets, g, "bzp_yakrec_", confirm_cb="bzp_yakrec_confirm"))
        return

    if data.startswith("bzp_yaksac_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        me = _seat_of_uid(g, uid)
        teammates = [x for x in _mafia_seats(g, alive_only=True) if x != me]
        await _edit_pm(ctx, uid, mid, "🥷 کدام یارت را فدا می‌کنی؟",
                       _kb_night_seats(teammates, g, "bzp_yaksac_", selected=s, confirm_cb="bzp_yaksac_confirm"))
        return

    # ── یاکوزایی: جذب شهروند ──
    if data == "bzp_yakrec_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        tu, tname = g.seats[s]
        rn = _seat_role_norm(g, s)
        if rn in _R_CITIZEN:
            converted = True
        elif rn == _R_ROUIN:
            converted = (g.max_seats != 12)  # در بازپرس ۱۲ نفره رویین‌تن خریداری نمی‌شود
        else:
            converted = False
        sac = g.night_yakuza_sacrifice
        sac_txt = f"{sac}. {g.seats[sac][1]}" if sac in g.seats else "—"
        if converted:
            g.negotiated_seats.add(s)
            try:
                await ctx.bot.send_message(tu, "🥷 با شما یاکوزایی شد. اکنون «مافیا ساده» هستید.")
            except Exception:
                pass
            await _room_send_link(ctx, g, tu)   # لینک اتاق مافیا فوری
            await _night_report(ctx, g, f"🥷 یاکوزایی → فدا: {sac_txt} | جذب: <b>{s}. {escape(tname, quote=False)}</b> → مافیا ساده ✅")
        else:
            await _night_report(ctx, g, f"🥷 یاکوزایی → فدا: {sac_txt} | جذب: <b>{s}. {escape(tname, quote=False)}</b> → ناموفق ❌")
        await _close_pm(ctx, uid, mid, "✅ یاکوزایی ثبت شد.")
        g.night_doctor_blocked = True
        g.night_done.add("mafia")
        store.save()
        await _bzp_broadcast_special(ctx, g, "یاکوزایی")
        await _bzp_check_open_rest(ctx, chat_id, g)
        return

    if data.startswith("bzp_yakrec_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = [x for x in _alive_seats(g) if x not in _mafia_seats(g, alive_only=True)]
        await _edit_pm(ctx, uid, mid, "🥷 با چه کسی یاکوزایی می‌کنی؟",
                       _kb_night_seats(targets, g, "bzp_yakrec_", selected=s, confirm_cb="bzp_yakrec_confirm"))
        return

    # ── ناتویی: انتخاب هدف، سپس حدس نقش ──
    if data.startswith("bzp_natorole_"):
        i = int(data.rsplit("_", 1)[1])
        guess_name = _BZP_CITIZEN_ROLE_NAMES[i]
        s = g.night_nato_seat
        if not s:
            await safe_q_answer(q, "اول هدف را انتخاب کن.", show_alert=True)
            return
        _tu, tname = g.seats[s]
        correct = (_nz(guess_name) == _seat_role_norm(g, s))
        g.night_nato_correct = correct
        tick = "✅" if correct else "❌"
        g.night_nato_target = s
        await _close_pm(ctx, uid, mid, f"🕵️ ناتویی روی {s}. {tname} ثبت شد.")
        await _night_report(ctx, g, f"🕵️ ناتو → صندلی {s}. {escape(tname, quote=False)} | حدس نقش: {guess_name} {tick}")
        g.night_doctor_blocked = True
        g.night_done.add("mafia")
        store.save()
        await _bzp_broadcast_special(ctx, g, "ناتویی")
        await _bzp_check_open_rest(ctx, chat_id, g)
        return

    if data == "bzp_nato_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_nato_seat = s
        g.night_sel.pop(uid, None)
        store.save()
        rows = [[InlineKeyboardButton(rn, callback_data=f"bzp_natorole_{i}")]
                for i, rn in enumerate(_BZP_CITIZEN_ROLE_NAMES)]
        await _edit_pm(ctx, uid, mid, f"🕵️ نقش صندلی {s} را حدس بزن:", InlineKeyboardMarkup(rows))
        return

    if data.startswith("bzp_nato_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = [x for x in _alive_seats(g) if x not in _mafia_seats(g, alive_only=True)]
        await _edit_pm(ctx, uid, mid, "🕵️ چه کسی را ناتویی می‌کنی؟",
                       _kb_night_seats(targets, g, "bzp_nato_", selected=s, confirm_cb="bzp_nato_confirm"))
        return

    # ── شیاد: حدس کاراگاه ──
    if data == "bzp_shiad_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_shiad_guess = s
        det = _find_seat_by_role(g, _R_DETECTIVE)
        correct = (det is not None and s == det)
        tick = "✅" if correct else "❌"
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, "🎭 حدس ثبت شد.")
        await _night_report(ctx, g, f"🎭 شیاد → حدس کاراگاه: {s}. {escape(tname, quote=False)} {tick}")
        g.night_done.add("shiad")
        store.save()
        await _bzp_check_open_rest(ctx, chat_id, g)
        return

    if data.startswith("bzp_shiad_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = [x for x in _alive_seats(g) if x not in _mafia_seats(g, alive_only=True)]
        await _edit_pm(ctx, uid, mid, "🎭 حدس بزن کدام شماره کاراگاه است:",
                       _kb_night_seats(targets, g, "bzp_shiad_", selected=s, confirm_cb="bzp_shiad_confirm"))
        return

    # ── کاراگاه (مستقیم) ──
    if data.startswith("bzp_det_"):
        s = int(data.rsplit("_", 1)[1])
        _tu, tname = g.seats[s]
        res = "مثبت ✅" if _bzp_detective_positive(g, s) else "منفی ❌"
        await _close_pm(ctx, uid, mid, f"🔎 استعلام {s}. {tname}: {res}")
        await _night_report(ctx, g, f"🔎 کاراگاه → استعلام {s}. {escape(tname, quote=False)}: <b>{res}</b>")
        g.night_done.add("detective")
        store.save()
        return

    # ── پزشک (۱ نفر) ──
    if data == "bzp_doc_confirm":
        sel = list(g.night_doc_sel.get(uid, []))
        if not sel:
            await safe_q_answer(q, "یک نفر را انتخاب کن.", show_alert=True)
            return
        doc = _seat_of_uid(g, uid)
        if doc in sel:
            g.doctor_self_saves = (g.doctor_self_saves or 0) + 1
        g.night_doc_saved = list(sel)
        names = "، ".join(f"{s}. {g.seats[s][1]}" for s in sel)
        await _close_pm(ctx, uid, mid, f"💉 سیو ثبت شد: {names}")
        await _night_report(ctx, g, f"💉 پزشک → سیو: <b>{escape(names, quote=False)}</b>")
        g.night_done.add("doctor")
        store.save()
        return

    if data.startswith("bzp_doc_"):
        s = int(data.rsplit("_", 1)[1])
        sel = set(g.night_doc_sel.get(uid, []))
        if s in sel:
            sel.remove(s)
        elif len(sel) >= 1:
            await safe_q_answer(q, "فقط ۱ نفر.", show_alert=True)
            return
        else:
            sel.add(s)
        g.night_doc_sel[uid] = list(sel)
        store.save()
        doc = _seat_of_uid(g, uid)
        await _edit_pm(ctx, uid, mid, "💉 چه کسی را سیو می‌دهی؟ (۱ نفر)",
                       _kb_night_seats(_doctor_targets(g, doc), g, "bzp_doc_",
                                       selected=sel, confirm_cb="bzp_doc_confirm"))
        return

    # ── بازپرس (یکبار: انتخاب ۲ نفر) ──
    if data == "bzp_baz_no":
        await _close_pm(ctx, uid, mid, "🧑‍⚖️ از حق بازپرسی استفاده نکردی.")
        await _night_report(ctx, g, "🧑‍⚖️ بازپرس → استفاده نکرد")
        g.night_done.add("baazpors")
        store.save()
        return

    if data == "bzp_baz_yes":
        bz = _seat_of_uid(g, uid)
        targets = [s for s in _alive_seats(g) if s != bz]
        await _edit_pm(ctx, uid, mid, "🧑‍⚖️ دو نفر را برای بازپرسی انتخاب کن:",
                       _kb_night_seats(targets, g, "bzp_baz_",
                                       selected=set(g.night_baz_sel.get(uid, [])), confirm_cb="bzp_baz_confirm"))
        return

    if data == "bzp_baz_confirm":
        sel = list(g.night_baz_sel.get(uid, []))
        if len(sel) != 2:
            await safe_q_answer(q, "باید دقیقاً ۲ نفر را انتخاب کنی.", show_alert=True)
            return
        g.baazpors_used = True
        g.night_baz_targets = list(sel)   # اگر یکی امشب کشته شود، حقِ بازپرسی برمی‌گردد
        names = "، ".join(f"{s}. {g.seats[s][1]}" for s in sel)
        await _close_pm(ctx, uid, mid, f"🧑‍⚖️ بازپرسی: {names}")
        await _night_report(ctx, g, f"🧑‍⚖️ بازپرس → احضار به بازپرسی: <b>{escape(names, quote=False)}</b>")
        g.night_done.add("baazpors")
        store.save()
        return

    if data.startswith("bzp_baz_"):
        s = int(data.rsplit("_", 1)[1])
        sel = set(g.night_baz_sel.get(uid, []))
        if s in sel:
            sel.remove(s)
        elif len(sel) >= 2:
            await safe_q_answer(q, "حداکثر ۲ نفر.", show_alert=True)
            return
        else:
            sel.add(s)
        g.night_baz_sel[uid] = list(sel)
        store.save()
        bz = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != bz]
        await _edit_pm(ctx, uid, mid, "🧑‍⚖️ دو نفر را برای بازپرسی انتخاب کن:",
                       _kb_night_seats(targets, g, "bzp_baz_", selected=sel, confirm_cb="bzp_baz_confirm"))
        return

    # ── اسنایپر (۱۲/۱۳ نفره) ──
    if data == "bzp_snipe_no":
        await _close_pm(ctx, uid, mid, "🚫 از تیر استفاده نکردی.")
        await _night_report(ctx, g, "🎯 اسنایپر → شلیک نکرد")
        g.night_done.add("sniper")
        store.save()
        return

    if data == "bzp_snipe_yes":
        sn = _seat_of_uid(g, uid)
        targets = [s for s in _alive_seats(g) if s != sn]
        await _edit_pm(ctx, uid, mid, "🎯 به چه کسی شلیک می‌کنی؟",
                       _kb_night_seats(targets, g, "bzp_snipe_",
                                       selected=g.night_sel.get(uid), confirm_cb="bzp_snipe_confirm"))
        return

    if data == "bzp_snipe_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.sniper_used = True
        g.night_sniper_target = s
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, f"🎯 شلیک ثبت شد: {s}. {tname}")
        await _night_report(ctx, g, f"🎯 اسنایپر → شلیک به <b>{s}. {escape(tname, quote=False)}</b>")
        g.night_done.add("sniper")
        store.save()
        return

    if data.startswith("bzp_snipe_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        sn = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != sn]
        await _edit_pm(ctx, uid, mid, "🎯 به چه کسی شلیک می‌کنی؟",
                       _kb_night_seats(targets, g, "bzp_snipe_", selected=s, confirm_cb="bzp_snipe_confirm"))
        return


# ═════════════════════════════════════════════════════════════
#  موتور شبِ خودکار — سناریو «نماینده»
#  ترتیب اکت: مین‌گذار → (دن‌مافیا + هکر) → (وکیل/محافظ/پزشک/راهنما)
# ═════════════════════════════════════════════════════════════
NEMAYANDE_KEY = "نماینده"
_R_DON    = _nz("دن‌مافیا")
_R_HACKER = _nz("هکر")
_R_YAGHI  = _nz("یاغی")
_R_GUARD  = _nz("محافظ")
_R_GUIDE  = _nz("راهنما")
_R_MINER  = _nz("مین‌گذار")
_R_LAWYER = _nz("وکیل")


def _is_nemayande_scenario(g) -> bool:
    return bool(getattr(g, "scenario", None)) and (_nz(NEMAYANDE_KEY) in _nz(g.scenario.name))


def _find_seat_role_sub(g, sub_norm, alive_only=True):
    """یافتن صندلی که نقش نرمالایزش شامل sub_norm باشد (برای نقش‌هایی مثل «وکیل(شهر)»)."""
    seats = _alive_seats(g) if alive_only else sorted(g.seats)
    for s in seats:
        if sub_norm in _seat_role_norm(g, s):
            return s
    return None


def _nem_citizen_role_names(g):
    """نام دقیق نقش‌های شهروندی سناریو (برای دکمه‌های حدس نقشِ ناتویی)."""
    mafia = set(_mafia_role_set(g))
    names, seen = [], set()
    for rname in (g.scenario.roles.keys() if g.scenario else []):
        n = _nz(rname)
        if n in mafia or n in seen:
            continue
        seen.add(n)
        names.append(rname)
    return names


def _nem_guide_positive(g, seat) -> bool:
    # استعلام راهنما: یاغی و هکر مثبت
    return _seat_role_norm(g, seat) in (_R_YAGHI, _R_HACKER)


def _nem_mine_kb(g, targets, selected=None):
    kb = _kb_night_seats(targets, g, "nem_mine_", selected=selected, confirm_cb="nem_mine_confirm")
    rows = list(kb.inline_keyboard) + [
        [InlineKeyboardButton("⏭ امشب نه (بعداً)", callback_data="nem_mine_skip")]
    ]
    return InlineKeyboardMarkup(rows)


async def _nem_open_mine(ctx, chat_id, g):
    if g.mine_seat is not None:
        g.night_done.add("mine")
        store.save()
        await _nem_open_mafia(ctx, chat_id, g)
        return
    m_seat = _find_seat_by_role(g, _R_MINER)
    if not m_seat:
        g.night_done.add("mine")
        store.save()
        await _nem_open_mafia(ctx, chat_id, g)
        return
    muid = g.seats[m_seat][0]
    targets = [s for s in _alive_seats(g) if s != m_seat]
    m = await _safe_pm(ctx, muid, "💣 جلوی چه کسی مین می‌گذاری؟ (تا آخر بازی می‌ماند)",
                       _nem_mine_kb(g, targets, selected=g.night_sel.get(muid)))
    if m:
        g.night_pm_msgs[muid] = m.message_id
    store.save()


async def _nem_open_mafia(ctx, chat_id, g):
    # تصمیم‌گیرندهٔ مافیا: دن‌مافیا → یاغی → هکر
    don_alive = _find_seat_by_role(g, _R_DON)
    yaghi_alive = _find_seat_by_role(g, _R_YAGHI)
    hacker_alive = _find_seat_by_role(g, _R_HACKER)
    decider = don_alive or yaghi_alive or hacker_alive
    if not decider:
        g.night_done.add("mafia")
    else:
        g.nem_decider_seat = decider
        duid = g.seats[decider][0]
        rows = [[InlineKeyboardButton("🔫 شات", callback_data="nem_don_shot")]]
        # ناتویی فقط وقتی دن‌مافیا زنده است (اکت خودِ دن‌مافیاست)
        if don_alive is not None:
            rows.append([InlineKeyboardButton("🕵️ ناتویی", callback_data="nem_don_nato")])
        m = await _safe_pm(ctx, duid, f"🌙 شب {g.night_number}\nاکت مافیا را انتخاب کن:",
                           InlineKeyboardMarkup(rows))
        if m:
            g.night_pm_msgs[duid] = m.message_id

    # هکر (مرحله ۱: انتخاب فاعلِ اکت)
    hk = _find_seat_by_role(g, _R_HACKER)
    if not hk:
        g.night_done.add("hacker")
    else:
        huid = g.seats[hk][0]
        targets = [s for s in _alive_seats(g) if s != hk]   # خودش نباشد
        m = await _safe_pm(ctx, huid, "💻 اکتِ چه کسی را هک می‌کنی؟ (مرحلهٔ ۱: فاعل)",
                           _kb_night_seats(targets, g, "nem_hka_",
                                           selected=g.night_sel.get(huid), confirm_cb="nem_hka_confirm"))
        if m:
            g.night_pm_msgs[huid] = m.message_id
    store.save()
    await _nem_check_open_rest(ctx, chat_id, g)


async def _nem_check_open_rest(ctx, chat_id, g):
    if g.night_rest_opened:
        return
    if "mafia" not in g.night_done or "hacker" not in g.night_done:
        return
    g.night_rest_opened = True
    store.save()

    # 🧑‍⚖️ وکیل (یکبار در بازی)
    if not g.lawyer_used:
        law = _find_seat_role_sub(g, _R_LAWYER)
        if law:
            luid = g.seats[law][0]
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ بله", callback_data="nem_law_yes")],
                [InlineKeyboardButton("🚫 خیر", callback_data="nem_law_no")],
            ])
            m = await _safe_pm(ctx, luid, "🧑‍⚖️ امشب وکالت کسی را می‌گیری؟", kb)
            if m:
                g.night_pm_msgs[luid] = m.message_id

    # 🛡 محافظ (هر شب)
    grd = _find_seat_by_role(g, _R_GUARD)
    if grd:
        guid = g.seats[grd][0]
        targets = [s for s in _alive_seats(g) if s != grd]
        m = await _safe_pm(ctx, guid, "🛡 از چه کسی محافظت می‌کنی؟",
                           _kb_night_seats(targets, g, "nem_grd_", confirm_cb="nem_grd_confirm"))
        if m:
            g.night_pm_msgs[guid] = m.message_id

    # 💉 پزشک — در شب ناتویی استراحت
    if not g.night_doctor_blocked:
        doc = _find_seat_by_role(g, _R_DOCTOR)
        if doc:
            duid = g.seats[doc][0]
            g.night_doc_need = 1
            targets = _doctor_targets(g, doc)
            m = await _safe_pm(ctx, duid, "💉 چه کسی را سیو می‌دهی؟ (۱ نفر)",
                               _kb_night_seats(targets, g, "nem_doc_", selected=set(),
                                               confirm_cb="nem_doc_confirm"))
            if m:
                g.night_pm_msgs[duid] = m.message_id

    # 🧭 راهنما (آخرین اکت)
    gd = _find_seat_by_role(g, _R_GUIDE)
    if gd:
        gduid = g.seats[gd][0]
        targets = [s for s in _alive_seats(g) if s != gd and s != g.guide_last_target]
        m = await _safe_pm(ctx, gduid, "🧭 به چه کسی راهنمایی می‌دهی؟",
                           _kb_night_seats(targets, g, "nem_guide_", confirm_cb="nem_guide_confirm"))
        if m:
            g.night_pm_msgs[gduid] = m.message_id
    store.save()
    # اگر راهنمایی زنده نیست، همین‌جا مین را بررسی کن (آخرین اکت گرفته شده)
    if not gd:
        await _nem_trigger_mine(ctx, chat_id, g)


async def _nem_trigger_mine(ctx, chat_id, g):
    """بعد از اکت راهنما (آخرین اکت): اگر مین فعال شده باشد، به همه اعلام و از دن‌مافیا فدا می‌خواهد."""
    if getattr(g, "night_mine_handled", False):
        return
    target = None
    if g.night_don_act == "shot":
        target = g.night_shot_target
    elif g.night_don_act == "nato":
        target = g.night_nato_seat
    mine_hit = (target is not None and g.mine_seat is not None
                and target == g.mine_seat and not g.night_don_defuse)
    if not mine_hit:
        return
    g.night_mine_handled = True
    store.save()
    for s in _alive_seats(g):
        try:
            await ctx.bot.send_message(g.seats[s][0], "💥 امشب مین فعال شد!")
        except Exception:
            pass
    await _night_report(ctx, g, "💥 مین فعال شد")
    mafia_alive = sorted(_mafia_seats(g, alive_only=True))
    # تصمیم‌گیرندهٔ فدا: دن‌مافیا → یاغی → هکر
    picker = (_find_seat_by_role(g, _R_DON)
              or _find_seat_by_role(g, _R_YAGHI)
              or _find_seat_by_role(g, _R_HACKER))
    if not picker or not mafia_alive:
        await _night_report(ctx, g, "⚠️ مافیایی برای فدا کردن نیست.")
        return
    g.night_awaiting_sacrifice = True
    store.save()
    puid = g.seats[picker][0]
    m = await _safe_pm(ctx, puid, "💥 مین فعال شد! چه کسی را از تیم خود فدا می‌کنی؟",
                       _kb_night_seats(mafia_alive, g, "nem_fada_", confirm_cb="nem_fada_confirm"))
    if m:
        g.night_pm_msgs[puid] = m.message_id
    store.save()


async def _nem_finalize_mafia(ctx, chat_id, g, uid, mid, defuse):
    g.night_don_defuse = defuse
    dtxt = "با خنثی‌سازی" if defuse else "بدون خنثی‌سازی"
    if g.night_don_act == "shot":
        s = g.night_shot_target
        tname = g.seats[s][1]
        await _close_pm(ctx, uid, mid, f"✅ شلیک ثبت شد: {s}. {tname} ({dtxt})")
        await _night_report(ctx, g, f"🔫 شلیک دن‌مافیا → <b>{s}. {escape(tname, quote=False)}</b> ({dtxt})")
        g.night_done.add("mafia")
        store.save()
        await _nem_check_open_rest(ctx, chat_id, g)
    else:  # nato
        await _close_pm(ctx, uid, mid, f"✅ ناتویی ثبت شد ({dtxt}).")
        await _night_report(ctx, g, f"   ↳ ناتویی {dtxt}")
        g.night_doctor_blocked = True
        g.night_done.add("mafia")
        store.save()
        await _bzp_broadcast_special(ctx, g, "ناتویی")
        await _nem_check_open_rest(ctx, chat_id, g)


async def _nem_ask_defuse(ctx, uid, mid):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💥 با خنثی‌سازی", callback_data="nem_defuse_yes")],
        [InlineKeyboardButton("➖ بدون خنثی‌سازی", callback_data="nem_defuse_no")],
    ])
    await _edit_pm(ctx, uid, mid, "تمایل به خنثی‌سازی داری؟", kb)


async def handle_nemayande_callback(update, ctx):
    q = update.callback_query
    data = q.data
    uid = q.from_user.id
    g, chat_id = _find_active_night_game(uid, q)
    if g is None:
        await safe_q_answer(q, "بازی فعالی یافت نشد.", show_alert=True)
        return
    await safe_q_answer(q)
    mid = q.message.message_id if q.message else None

    # ── فدای مین (بعد از اکت راهنما، فعال‌شدن مین) ──
    if data == "nem_fada_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_mine_sacrifice = s
        g.night_awaiting_sacrifice = False
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, f"💥 {s}. {tname} فدا شد.")
        await _night_report(ctx, g, f"💥 دن‌مافیا فدا کرد: <b>{s}. {escape(tname, quote=False)}</b>")
        store.save()
        return

    if data.startswith("nem_fada_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        mafia_alive = sorted(_mafia_seats(g, alive_only=True))
        await _edit_pm(ctx, uid, mid, "💥 مین فعال شد! چه کسی را از تیم خود فدا می‌کنی؟",
                       _kb_night_seats(mafia_alive, g, "nem_fada_", selected=s, confirm_cb="nem_fada_confirm"))
        return

    # ── مین‌گذار ──
    if data == "nem_mine_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.mine_seat = s
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, f"💣 مین جلوی {s}. {tname} گذاشته شد.")
        await _night_report(ctx, g, f"💣 مین‌گذار → مین جلوی <b>{s}. {escape(tname, quote=False)}</b>")
        g.night_done.add("mine")
        store.save()
        await _nem_open_mafia(ctx, chat_id, g)
        return

    if data == "nem_mine_skip":
        await _close_pm(ctx, uid, mid, "⏭ امشب مین نگذاشتی (می‌توانی شب بعد بگذاری).")
        await _night_report(ctx, g, "💣 مین‌گذار → امشب مین نگذاشت")
        g.night_done.add("mine")
        store.save()
        await _nem_open_mafia(ctx, chat_id, g)
        return

    if data.startswith("nem_mine_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        mn = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != mn]
        await _edit_pm(ctx, uid, mid, "💣 جلوی چه کسی مین می‌گذاری؟ (تا آخر بازی می‌ماند)",
                       _nem_mine_kb(g, targets, selected=s))
        return

    # ── دن‌مافیا: تصمیم ──
    if data == "nem_don_shot":
        g.night_don_act = "shot"
        store.save()
        targets = list(_alive_seats(g))   # شاملِ خودِ مافیا
        await _edit_pm(ctx, uid, mid, "🔫 هدف شلیک را انتخاب کن:",
                       _kb_night_seats(targets, g, "nem_shot_",
                                       selected=g.night_sel.get(uid), confirm_cb="nem_shot_confirm"))
        return

    if data == "nem_don_nato":
        g.night_don_act = "nato"
        store.save()
        targets = [s for s in _alive_seats(g)
                   if s not in _mafia_seats(g, alive_only=True) and s not in (g.nato_immune or set())]
        await _edit_pm(ctx, uid, mid, "🕵️ چه کسی را ناتویی می‌کنی؟",
                       _kb_night_seats(targets, g, "nem_natt_",
                                       selected=g.night_sel.get(uid), confirm_cb="nem_natt_confirm"))
        return

    # ── شلیک ──
    if data == "nem_shot_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_shot_target = s
        g.night_don_act = "shot"
        store.save()
        if g.defuse_used:
            await _nem_finalize_mafia(ctx, chat_id, g, uid, mid, defuse=False)
        else:
            await _nem_ask_defuse(ctx, uid, mid)
        return

    if data.startswith("nem_shot_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = list(_alive_seats(g))   # شاملِ خودِ مافیا
        await _edit_pm(ctx, uid, mid, "🔫 هدف شلیک را انتخاب کن:",
                       _kb_night_seats(targets, g, "nem_shot_", selected=s, confirm_cb="nem_shot_confirm"))
        return

    # ── ناتویی: انتخاب هدف، سپس حدس نقش ──
    if data.startswith("nem_natrole_"):
        i = int(data.rsplit("_", 1)[1])
        names = _nem_citizen_role_names(g)
        if i >= len(names):
            return
        guess_name = names[i]
        s = g.night_nato_seat
        if not s:
            await safe_q_answer(q, "اول هدف را انتخاب کن.", show_alert=True)
            return
        _tu, tname = g.seats[s]
        correct = (_nz(guess_name) == _seat_role_norm(g, s))
        g.night_nato_correct = correct
        g.night_nato_target = s
        tick = "✅" if correct else "❌"
        await _night_report(ctx, g, f"🕵️ ناتویی دن‌مافیا → صندلی {s}. {escape(tname, quote=False)} | حدس: {guess_name} {tick}")
        store.save()
        if g.defuse_used:
            await _nem_finalize_mafia(ctx, chat_id, g, uid, mid, defuse=False)
        else:
            await _nem_ask_defuse(ctx, uid, mid)
        return

    if data == "nem_natt_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_nato_seat = s
        g.night_sel.pop(uid, None)
        store.save()
        names = _nem_citizen_role_names(g)
        rows = [[InlineKeyboardButton(rn, callback_data=f"nem_natrole_{i}")] for i, rn in enumerate(names)]
        await _edit_pm(ctx, uid, mid, f"🕵️ نقش صندلی {s} را حدس بزن:", InlineKeyboardMarkup(rows))
        return

    if data.startswith("nem_natt_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = [x for x in _alive_seats(g)
                   if x not in _mafia_seats(g, alive_only=True) and x not in (g.nato_immune or set())]
        await _edit_pm(ctx, uid, mid, "🕵️ چه کسی را ناتویی می‌کنی؟",
                       _kb_night_seats(targets, g, "nem_natt_", selected=s, confirm_cb="nem_natt_confirm"))
        return

    # ── خنثی‌سازی ──
    if data == "nem_defuse_yes":
        g.defuse_used = True
        store.save()
        await _nem_finalize_mafia(ctx, chat_id, g, uid, mid, defuse=True)
        return
    if data == "nem_defuse_no":
        await _nem_finalize_mafia(ctx, chat_id, g, uid, mid, defuse=False)
        return

    # ── هکر: مرحله ۱ فاعل، مرحله ۲ مفعول ──
    if data == "nem_hka_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول فاعل را انتخاب کن.", show_alert=True)
            return
        g.night_hacker_actor = s
        g.night_sel.pop(uid, None)
        store.save()
        hk = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != hk and x != s]   # نه خودش، نه فاعل
        await _edit_pm(ctx, uid, mid, "💻 اکتش روی چه کسی بسته شود؟ (مرحلهٔ ۲: مفعول)",
                       _kb_night_seats(targets, g, "nem_hkt_", confirm_cb="nem_hkt_confirm"))
        return

    if data.startswith("nem_hka_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        hk = _seat_of_uid(g, uid)
        await _edit_pm(ctx, uid, mid, "💻 اکتِ چه کسی را هک می‌کنی؟ (مرحلهٔ ۱: فاعل)",
                       _kb_night_seats([x for x in _alive_seats(g) if x != hk], g, "nem_hka_",
                                       selected=s, confirm_cb="nem_hka_confirm"))
        return

    if data == "nem_hkt_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول مفعول را انتخاب کن.", show_alert=True)
            return
        g.night_hacker_target = s
        a = g.night_hacker_actor
        aname = g.seats[a][1] if a in g.seats else "—"
        tname = g.seats[s][1]
        await _close_pm(ctx, uid, mid, f"💻 هک ثبت شد: اکت {a}.{aname} روی {s}.{tname} بسته شد.")
        await _night_report(ctx, g, f"💻 هکر → اکت <b>{a}. {escape(aname, quote=False)}</b> روی <b>{s}. {escape(tname, quote=False)}</b> بسته شد")
        g.night_done.add("hacker")
        store.save()
        await _nem_check_open_rest(ctx, chat_id, g)
        return

    if data.startswith("nem_hkt_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        hk = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != hk and x != g.night_hacker_actor]
        await _edit_pm(ctx, uid, mid, "💻 اکتش روی چه کسی بسته شود؟ (مرحلهٔ ۲: مفعول)",
                       _kb_night_seats(targets, g, "nem_hkt_", selected=s, confirm_cb="nem_hkt_confirm"))
        return

    # ── وکیل (یکبار) ──
    if data == "nem_law_no":
        await _close_pm(ctx, uid, mid, "🧑‍⚖️ امشب وکالت نگرفتی.")
        await _night_report(ctx, g, "🧑‍⚖️ وکیل → استفاده نکرد")
        g.night_done.add("lawyer")
        store.save()
        return

    if data == "nem_law_yes":
        law = _seat_of_uid(g, uid)
        targets = [s for s in _alive_seats(g) if s != law]
        await _edit_pm(ctx, uid, mid, "🧑‍⚖️ وکالت چه کسی را می‌گیری؟",
                       _kb_night_seats(targets, g, "nem_law_", confirm_cb="nem_law_confirm"))
        return

    if data == "nem_law_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        # وکالت فقط در صورتی «مصرف» می‌شود که موکل همان شب شات نشود (در مرحله ۲ تعیین می‌شود)
        g.night_lawyer_target = s
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, f"🧑‍⚖️ وکالت {s}. {tname} ثبت شد.")
        await _night_report(ctx, g, f"🧑‍⚖️ وکیل → وکالت <b>{s}. {escape(tname, quote=False)}</b>")
        g.night_done.add("lawyer")
        store.save()
        return

    if data.startswith("nem_law_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        law = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != law]
        await _edit_pm(ctx, uid, mid, "🧑‍⚖️ وکالت چه کسی را می‌گیری؟",
                       _kb_night_seats(targets, g, "nem_law_", selected=s, confirm_cb="nem_law_confirm"))
        return

    # ── محافظ (هر شب، فقط گزارش) ──
    if data == "nem_grd_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, f"🛡 محافظت از {s}. {tname} ثبت شد.")
        await _night_report(ctx, g, f"🛡 محافظ → محافظت از <b>{s}. {escape(tname, quote=False)}</b>")
        g.night_done.add("guard")
        store.save()
        return

    if data.startswith("nem_grd_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        grd = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != grd]
        await _edit_pm(ctx, uid, mid, "🛡 از چه کسی محافظت می‌کنی؟",
                       _kb_night_seats(targets, g, "nem_grd_", selected=s, confirm_cb="nem_grd_confirm"))
        return

    # ── پزشک (۱ نفر) ──
    if data == "nem_doc_confirm":
        sel = list(g.night_doc_sel.get(uid, []))
        if not sel:
            await safe_q_answer(q, "یک نفر را انتخاب کن.", show_alert=True)
            return
        doc = _seat_of_uid(g, uid)
        if doc in sel:
            g.doctor_self_saves = (g.doctor_self_saves or 0) + 1
        g.night_doc_saved = list(sel)
        names = "، ".join(f"{s}. {g.seats[s][1]}" for s in sel)
        await _close_pm(ctx, uid, mid, f"💉 سیو ثبت شد: {names}")
        await _night_report(ctx, g, f"💉 پزشک → سیو: <b>{escape(names, quote=False)}</b>")
        g.night_done.add("doctor")
        store.save()
        return

    if data.startswith("nem_doc_"):
        s = int(data.rsplit("_", 1)[1])
        sel = set(g.night_doc_sel.get(uid, []))
        if s in sel:
            sel.remove(s)
        elif len(sel) >= 1:
            await safe_q_answer(q, "فقط ۱ نفر.", show_alert=True)
            return
        else:
            sel.add(s)
        g.night_doc_sel[uid] = list(sel)
        store.save()
        doc = _seat_of_uid(g, uid)
        await _edit_pm(ctx, uid, mid, "💉 چه کسی را سیو می‌دهی؟ (۱ نفر)",
                       _kb_night_seats(_doctor_targets(g, doc), g, "nem_doc_",
                                       selected=sel, confirm_cb="nem_doc_confirm"))
        return

    # ── راهنما (آخرین اکت) ──
    if data == "nem_guide_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_guide_target = s
        g.guide_last_target = s
        _tu, tname = g.seats[s]
        is_mafia = s in _mafia_seats(g, alive_only=True)
        # هکِ راهنما روی همین هدف؟ → راهنمایی بی‌اثر می‌شود
        hacked = (g.night_hacker_actor == _seat_of_uid(g, uid)
                  and g.night_hacker_target == s)
        if is_mafia:
            try:
                await ctx.bot.send_message(g.seats[s][0], f"🧭 سیت {_seat_of_uid(g, uid)} راهنماست.")
            except Exception:
                pass
            g.nato_immune.add(_seat_of_uid(g, uid))
            await _close_pm(ctx, uid, mid, f"🧭 راهنمایی به {s}. {tname} ثبت شد.")
            await _night_report(ctx, g, f"🧭 راهنما → راهنمایی به مافیا {s}. {escape(tname, quote=False)} (راهنما از ناتویی مصون شد)")
            g.night_done.add("guide")
            store.save()
            await _nem_trigger_mine(ctx, chat_id, g)
            return
        # شهروند
        if hacked:
            await _close_pm(ctx, uid, mid, f"🧭 راهنمایی به {s}. {tname} ثبت شد.")
            await _night_report(ctx, g, f"🧭 راهنما → راهنمایی به {s}. {escape(tname, quote=False)} (توسط هکر بی‌اثر شد، آن فرد متوجه نشد)")
            g.night_done.add("guide")
            store.save()
            await _nem_trigger_mine(ctx, chat_id, g)
            return
        # راهنمایی مؤثر: شهروند استعلام می‌گیرد
        g.night_guide_recipient_inv = s
        await _close_pm(ctx, uid, mid, f"🧭 راهنمایی به {s}. {tname} ثبت شد.")
        await _night_report(ctx, g, f"🧭 راهنما → راهنمایی به {s}. {escape(tname, quote=False)}")
        rec_uid = g.seats[s][0]
        targets = [x for x in _alive_seats(g) if x != s]
        m = await _safe_pm(ctx, rec_uid, "🔎 شما راهنمایی دارید! استعلام چه کسی را می‌گیری؟",
                           _kb_night_seats(targets, g, "nem_ginv_"))
        if m:
            g.night_pm_msgs[rec_uid] = m.message_id
        g.night_done.add("guide")
        store.save()
        await _nem_trigger_mine(ctx, chat_id, g)
        return

    if data.startswith("nem_guide_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        gd = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != gd and x != g.guide_last_target]
        await _edit_pm(ctx, uid, mid, "🧭 به چه کسی راهنمایی می‌دهی؟",
                       _kb_night_seats(targets, g, "nem_guide_", selected=s, confirm_cb="nem_guide_confirm"))
        return

    # ── استعلامِ شهروندِ راهنمایی‌گرفته ──
    if data.startswith("nem_ginv_"):
        s = int(data.rsplit("_", 1)[1])
        _tu, tname = g.seats[s]
        res = "مثبت ✅" if _nem_guide_positive(g, s) else "منفی ❌"
        await _close_pm(ctx, uid, mid, f"🔎 استعلام {s}. {tname}: {res}")
        await _night_report(ctx, g, f"🔎 (راهنمایی) استعلام {s}. {escape(tname, quote=False)}: <b>{res}</b>")
        store.save()
        return


# ═════════════════════════════════════════════════════════════
#  موتور شبِ خودکار — سناریو «تکاور»
#  ترتیب: (سوال شیلد از گاد) → نگهبان + گروگانگیر → مافیا →
#          کاراگاه/پزشک/تکاور → تفنگدار (آخر از همه)
# ═════════════════════════════════════════════════════════════
TAKAVAR_KEY  = "تکاور"
_R_HOSTAGE   = _nz("گروگانگیر")
_R_WATCHMAN  = _nz("نگهبان")
_R_GUNMAN    = _nz("تفنگدار")
_R_COMMANDO  = _nz("تکاور")


def _is_takavar_scenario(g) -> bool:
    return bool(getattr(g, "scenario", None)) and (_nz(TAKAVAR_KEY) in _nz(g.scenario.name))


def _tk_blocked(g, seat) -> bool:
    """گرو گرفته شده و نگهبانی نشده → حق اکت ندارد."""
    return (g.night_hostage_seat == seat) and (seat not in set(g.night_guard_seats or []))


def _tk_guide_positive(g, seat) -> bool:
    # استعلام کاراگاه: ناتو و گروگانگیر مثبت، بقیه منفی
    return _seat_role_norm(g, seat) in (_R_NATO, _R_HOSTAGE)


async def _tk_notify_hostaged(ctx, g, seat):
    # پیامِ «گرو گرفته شدید» به‌صورت مرکزی در _tk_send_hostage_notice ارسال می‌شود
    await _night_report(ctx, g, f"🔒 صندلی {seat} گرو گرفته شده و اکت نداد.")


async def _tk_send_hostage_notice(ctx, g):
    """پیامِ «گرو گرفته شدید» — فقط برای نقش‌دارهای اکت‌دار (کاراگاه/پزشک/تفنگدار/تکاور،
    حتی تکاورِ شات‌نشده)؛ نگهبان مستثنی است و نگهبانی‌شده هم بی‌خبر می‌ماند."""
    hs = getattr(g, "night_hostage_seat", None)
    if not hs or hs not in g.seats:
        return
    if hs in set(g.night_guard_seats or []):
        return   # نگهبانی شده → اکتِ عادی، بدون هیچ پیامی (گروگانگیر هم نمی‌فهمد)
    rn = _seat_role_norm(g, hs)
    if rn in (_R_DETECTIVE, _R_DOCTOR, _R_GUNMAN, _R_COMMANDO):
        try:
            await ctx.bot.send_message(g.seats[hs][0], "🔒 شما گرو گرفته شده‌اید و امشب حق اکت ندارید.")
        except Exception:
            pass
        await _night_report(ctx, g, f"🔒 پیامِ گرو به {hs}. {escape(g.seats[hs][1], quote=False)} رفت.")


async def _tk_open_shield(ctx, chat_id, g):
    # اول از همه: از گادِ راوی بپرس نگهبان شیلد دارد؟
    watch = _find_seat_by_role(g, _R_WATCHMAN)
    if not watch:
        g.night_shield = False
        g.night_done.add("shield")
        g.night_done.add("watchman")
        store.save()
        await _tk_open_first(ctx, chat_id, g)
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡 بله، شیلد دارد", callback_data="tk_shield_yes")],
        [InlineKeyboardButton("🚫 خیر", callback_data="tk_shield_no")],
    ])
    try:
        m = await ctx.bot.send_message(g.god_id, f"🌙 شب {g.night_number}\nآیا نگهبان شیلد دارد؟", reply_markup=kb)
        g.night_pm_msgs[g.god_id] = m.message_id
        store.save()
    except Exception:
        g.night_shield = True
        g.night_done.add("shield")
        store.save()
        await _tk_open_first(ctx, chat_id, g)


async def _tk_open_first(ctx, chat_id, g):
    # 🛡 نگهبان (اگر شیلد دارد)
    watch = _find_seat_by_role(g, _R_WATCHMAN)
    if g.night_shield and watch:
        wuid = g.seats[watch][0]
        need = 2 if g.night_alive_at_start >= 8 else 1
        g.tk_guard_need = need
        targets = [s for s in _alive_seats(g) if s != watch]
        m = await _safe_pm(ctx, wuid, f"🛡 از چه کسانی نگهبانی می‌دهی؟ (تا {need} نفر)",
                           _kb_night_seats(targets, g, "tk_grd_", selected=set(), confirm_cb="tk_grd_confirm"))
        if m:
            g.night_pm_msgs[wuid] = m.message_id
    else:
        g.night_done.add("watchman")

    # 🔒 گروگانگیر (اسپم ندارد: دو شب متوالی یک نفر ممنوع؛ رد شدن هم آزاد است)
    host = _find_seat_by_role(g, _R_HOSTAGE)
    if host:
        huid = g.seats[host][0]
        targets = [s for s in _alive_seats(g) if s != host and s != g.hostage_last_target]
        kb = _kb_night_seats(targets, g, "tk_host_",
                             selected=g.night_sel.get(huid), confirm_cb="tk_host_confirm")
        rows = list(kb.inline_keyboard) + [
            [InlineKeyboardButton("⏭ امشب گرو نمی‌گیرم", callback_data="tk_host_skip")]
        ]
        m = await _safe_pm(ctx, huid, "🔒 چه کسی را گرو می‌گیری؟", InlineKeyboardMarkup(rows))
        if m:
            g.night_pm_msgs[huid] = m.message_id
    else:
        g.night_done.add("hostage")
    store.save()
    await _tk_check_open_mafia(ctx, chat_id, g)


async def _tk_check_open_mafia(ctx, chat_id, g):
    if "mafia_opened" in g.night_done:
        return
    if "watchman" not in g.night_done or "hostage" not in g.night_done:
        return
    g.night_done.add("mafia_opened")
    store.save()
    # 🔒 حالا که هم نگهبان هم گروگانگیر مشخص شده‌اند، پیامِ گرو ارسال می‌شود
    await _tk_send_hostage_notice(ctx, g)
    await _tk_open_mafia(ctx, chat_id, g)


async def _tk_open_mafia(ctx, chat_id, g):
    gf = _find_seat_by_role(g, _R_GODFATHER)
    nato = _find_seat_by_role(g, _R_NATO)
    host = _find_seat_by_role(g, _R_HOSTAGE)
    decider = gf or nato or host
    if not decider:
        g.night_done.add("mafia")
        store.save()
        await _tk_check_open_citizens(ctx, chat_id, g)
        return
    g.tk_decider_seat = decider
    duid = g.seats[decider][0]
    rows = [[InlineKeyboardButton("🔫 شات", callback_data="tk_shot")]]
    if nato is not None:
        rows.append([InlineKeyboardButton("🕵️ ناتویی", callback_data="tk_nato")])
    m = await _safe_pm(ctx, duid, f"🌙 شب {g.night_number}\nاکت مافیا را انتخاب کن:",
                       InlineKeyboardMarkup(rows))
    if m:
        g.night_pm_msgs[duid] = m.message_id
    store.save()


async def _tk_check_open_citizens(ctx, chat_id, g):
    if "citizens_opened" in g.night_done:
        return
    if "mafia" not in g.night_done:
        return
    g.night_done.add("citizens_opened")
    store.save()
    await _tk_open_citizens(ctx, chat_id, g)


async def _tk_open_citizens(ctx, chat_id, g):
    # 🔎 کاراگاه
    det = _find_seat_by_role(g, _R_DETECTIVE)
    if not det:
        g.night_done.add("detective")
    elif _tk_blocked(g, det):
        await _tk_notify_hostaged(ctx, g, det)
        g.night_done.add("detective")
    else:
        duid = g.seats[det][0]
        targets = [s for s in _alive_seats(g) if s != det]
        m = await _safe_pm(ctx, duid, "🔎 استعلام چه کسی را می‌گیری؟",
                           _kb_night_seats(targets, g, "tk_det_"))
        if m:
            g.night_pm_msgs[duid] = m.message_id

    # 💉 پزشک
    doc = _find_seat_by_role(g, _R_DOCTOR)
    if not doc:
        g.night_done.add("doctor")
    elif _tk_blocked(g, doc):
        await _tk_notify_hostaged(ctx, g, doc)
        g.night_done.add("doctor")
    else:
        duid = g.seats[doc][0]
        need = 2 if g.night_alive_at_start >= 8 else 1
        g.night_doc_need = need
        targets = _doctor_targets(g, doc)
        m = await _safe_pm(ctx, duid, f"💉 چه کسی را سیو می‌دهی؟ (تا {need} نفر)",
                           _kb_night_seats(targets, g, "tk_doc_", selected=set(),
                                           confirm_cb="tk_doc_confirm"))
        if m:
            g.night_pm_msgs[duid] = m.message_id

    # 🎖 تکاور — فقط اگر مافیا او را «شات» کرده باشد و بلاک نشده باشد
    com = _find_seat_by_role(g, _R_COMMANDO)
    if com and g.night_shot_target == com and not _tk_blocked(g, com):
        cuid = g.seats[com][0]
        targets = [s for s in _alive_seats(g) if s != com]
        m = await _safe_pm(ctx, cuid, "🎖 شما شات شدید! می‌توانید یک نفر را بزنید (یک‌بار):",
                           _kb_night_seats(targets, g, "tk_com_",
                                           selected=g.night_sel.get(cuid), confirm_cb="tk_com_confirm"))
        if m:
            g.night_pm_msgs[cuid] = m.message_id
    else:
        g.night_done.add("commando")
    store.save()
    await _tk_check_open_gunman(ctx, chat_id, g)


async def _tk_check_open_gunman(ctx, chat_id, g):
    if "gunman_opened" in g.night_done:
        return
    if not all(k in g.night_done for k in ("detective", "doctor", "commando")):
        return
    g.night_done.add("gunman_opened")
    store.save()
    await _tk_open_gunman(ctx, chat_id, g)


async def _tk_open_gunman(ctx, chat_id, g):
    if g.war_gun_used:
        g.night_done.add("gunman")
        store.save()
        return
    gun = _find_seat_by_role(g, _R_GUNMAN)
    if not gun:
        g.night_done.add("gunman")
        store.save()
        return
    if _tk_blocked(g, gun):
        await _tk_notify_hostaged(ctx, g, gun)
        g.night_done.add("gunman")
        store.save()
        return
    guid = g.seats[gun][0]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ بله", callback_data="tk_gun_yes")],
        [InlineKeyboardButton("🚫 خیر", callback_data="tk_gun_no")],
    ])
    m = await _safe_pm(ctx, guid, "🔫 آیا امشب تفنگ می‌دهی؟", kb)
    if m:
        g.night_pm_msgs[guid] = m.message_id
    store.save()


def _tk_gun_type_kb(g, target, gun_num, gun_seat):
    allow_war = (target != gun_seat)  # به خودش تفنگ جنگی نمی‌دهد
    if gun_num == 2 and g.night_gun1_type == "war":
        allow_war = False              # اگر تفنگ اول جنگی بود، دومی جنگی نمی‌شود
    rows = []
    if allow_war:
        rows.append([InlineKeyboardButton("🔴 جنگی", callback_data=f"tk_g{gun_num}war")])
    rows.append([InlineKeyboardButton("⚪ مشقی", callback_data=f"tk_g{gun_num}blank")])
    return InlineKeyboardMarkup(rows)


async def _tk_ask_gun2(ctx, uid, mid):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ بله", callback_data="tk_gun2_yes")],
        [InlineKeyboardButton("🚫 خیر", callback_data="tk_gun2_no")],
    ])
    await _edit_pm(ctx, uid, mid, "🔫 آیا تفنگ دوم هم می‌دهی؟", kb)


async def _tk_finalize_gunman(ctx, chat_id, g, uid, mid):
    recips = []
    for t, typ in ((g.night_gun1_target, g.night_gun1_type), (g.night_gun2_target, g.night_gun2_type)):
        if t and typ:
            recips.append((t, typ))
    # تفنگ جنگی → مصرف و ثبت دارنده
    for t, typ in recips:
        if typ == "war":
            g.war_gun_used = True
            g.war_gun_holder = t
    # به گیرنده‌ها فقط بگو «تفنگ دارید» (بدون نوع)
    for t, typ in recips:
        try:
            await ctx.bot.send_message(g.seats[t][0], "🔫 شما تفنگ دارید.")
        except Exception:
            pass
    # گزارش کامل (با نوع) به گاد
    def _lbl(t, typ):
        nm = g.seats[t][1]
        return f"{t}. {nm} ({'جنگی🔴' if typ == 'war' else 'مشقی⚪'})"
    parts = [_lbl(t, typ) for t, typ in recips] or ["—"]
    await _close_pm(ctx, uid, mid, "🔫 توزیع تفنگ ثبت شد.")
    await _night_report(ctx, g, "🔫 تفنگدار → " + " | ".join(parts))
    g.night_done.add("gunman")
    store.save()


async def handle_takavar_callback(update, ctx):
    q = update.callback_query
    data = q.data
    uid = q.from_user.id

    # سوال شیلد از گادِ راوی (بازیکن نیست)
    if data.startswith("tk_shield_"):
        g = None
        chat_id = None
        for cid, game in store.games.items():
            if getattr(game, "night_active", False) and game.god_id == uid:
                g, chat_id = game, cid
                break
        if g is None:
            await safe_q_answer(q, "بازی فعالی یافت نشد.", show_alert=True)
            return
        await safe_q_answer(q)
        mid = q.message.message_id if q.message else None
        g.night_shield = (data == "tk_shield_yes")
        g.night_done.add("shield")
        await _close_pm(ctx, uid, mid,
                        "🛡 شیلد نگهبان: بله" if g.night_shield else "🚫 شیلد نگهبان: خیر (نگهبان امشب اکت ندارد)")
        store.save()
        await _tk_open_first(ctx, chat_id, g)
        return

    g, chat_id = _find_active_night_game(uid, q)
    if g is None:
        await safe_q_answer(q, "بازی فعالی یافت نشد.", show_alert=True)
        return
    await safe_q_answer(q)
    mid = q.message.message_id if q.message else None

    # ── نگهبان (چندانتخابی) ──
    if data == "tk_grd_confirm":
        sel = list(g.night_guard_sel.get(uid, []))
        if not sel:
            await safe_q_answer(q, "حداقل یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_guard_seats = list(sel)
        names = "، ".join(f"{s}. {g.seats[s][1]}" for s in sel)
        await _close_pm(ctx, uid, mid, f"🛡 نگهبانی ثبت شد: {names}")
        await _night_report(ctx, g, f"🛡 نگهبان → نگهبانی از: <b>{escape(names, quote=False)}</b>")
        g.night_done.add("watchman")
        store.save()
        await _tk_check_open_mafia(ctx, chat_id, g)
        return

    if data.startswith("tk_grd_"):
        s = int(data.rsplit("_", 1)[1])
        sel = set(g.night_guard_sel.get(uid, []))
        need = g.tk_guard_need or 1
        if s in sel:
            sel.remove(s)
        elif len(sel) >= need:
            await safe_q_answer(q, f"حداکثر {need} نفر.", show_alert=True)
            return
        else:
            sel.add(s)
        g.night_guard_sel[uid] = list(sel)
        store.save()
        watch = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != watch]
        await _edit_pm(ctx, uid, mid, f"🛡 از چه کسانی نگهبانی می‌دهی؟ (تا {need} نفر)",
                       _kb_night_seats(targets, g, "tk_grd_", selected=sel, confirm_cb="tk_grd_confirm"))
        return

    # ── گروگانگیر ──
    if data == "tk_host_skip":
        g.night_hostage_seat = None
        g.hostage_last_target = None   # محدودیتِ نفرِ قبلی آزاد می‌شود
        g.night_sel.pop(uid, None)
        await _close_pm(ctx, uid, mid, "⏭ امشب گرو نگرفتی.")
        await _night_report(ctx, g, "🔒 گروگانگیر → امشب گرو نگرفت")
        g.night_done.add("hostage")
        store.save()
        await _tk_check_open_mafia(ctx, chat_id, g)
        return

    if data == "tk_host_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_hostage_seat = s
        g.hostage_last_target = s
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, f"🔒 گرو گرفتی: {s}. {tname}")
        await _night_report(ctx, g, f"🔒 گروگانگیر → گرو گرفت: <b>{s}. {escape(tname, quote=False)}</b>")
        g.night_done.add("hostage")
        store.save()
        await _tk_check_open_mafia(ctx, chat_id, g)
        return

    if data.startswith("tk_host_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        host = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != host and x != g.hostage_last_target]
        await _edit_pm(ctx, uid, mid, "🔒 چه کسی را گرو می‌گیری؟",
                       _kb_night_seats(targets, g, "tk_host_", selected=s, confirm_cb="tk_host_confirm"))
        return

    # ── تصمیم مافیا ──
    if data == "tk_shot":
        targets = list(_alive_seats(g))   # شاملِ خودِ مافیا
        await _edit_pm(ctx, uid, mid, "🔫 هدف شلیک را انتخاب کن:",
                       _kb_night_seats(targets, g, "tk_st_",
                                       selected=g.night_sel.get(uid), confirm_cb="tk_st_confirm"))
        return

    if data == "tk_nato":
        nato = _find_seat_by_role(g, _R_NATO)
        if nato is None:
            await safe_q_answer(q, "ناتو زنده نیست.", show_alert=True)
            return
        nato_uid = g.seats[nato][0]
        targets = [s for s in _alive_seats(g) if s not in _mafia_seats(g, alive_only=True)]
        kb = _kb_night_seats(targets, g, "tk_nt_", selected=g.night_sel.get(nato_uid), confirm_cb="tk_nt_confirm")
        if nato_uid == uid:
            await _edit_pm(ctx, uid, mid, "🕵️ چه کسی را ناتویی می‌کنی؟", kb)
            g.night_pm_msgs[nato_uid] = mid
        else:
            await _close_pm(ctx, uid, mid, "🕵️ ناتویی انتخاب شد. منتظر ناتو بمانید.")
            m = await _safe_pm(ctx, nato_uid, "🕵️ چه کسی را ناتویی می‌کنی؟", kb)
            if m:
                g.night_pm_msgs[nato_uid] = m.message_id
        store.save()
        return

    # ── شلیک مافیا ──
    if data == "tk_st_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_shot_target = s
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, f"✅ شلیک ثبت شد: {s}. {tname}")
        await _night_report(ctx, g, f"🔫 شلیک مافیا → <b>{s}. {escape(tname, quote=False)}</b>")
        g.night_done.add("mafia")
        store.save()
        await _tk_check_open_citizens(ctx, chat_id, g)
        return

    if data.startswith("tk_st_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = list(_alive_seats(g))   # شاملِ خودِ مافیا
        await _edit_pm(ctx, uid, mid, "🔫 هدف شلیک را انتخاب کن:",
                       _kb_night_seats(targets, g, "tk_st_", selected=s, confirm_cb="tk_st_confirm"))
        return

    # ── ناتویی (حدس نقش) ──
    if data.startswith("tk_nrole_"):
        i = int(data.rsplit("_", 1)[1])
        names = _nem_citizen_role_names(g)
        if i >= len(names):
            return
        guess_name = names[i]
        s = g.night_nato_seat
        if not s:
            await safe_q_answer(q, "اول هدف را انتخاب کن.", show_alert=True)
            return
        _tu, tname = g.seats[s]
        correct = (_nz(guess_name) == _seat_role_norm(g, s))
        g.night_nato_correct = correct
        tick = "✅" if correct else "❌"
        await _close_pm(ctx, uid, mid, f"🕵️ ناتویی روی {s}. {tname} ثبت شد.")
        await _night_report(ctx, g, f"🕵️ ناتو → صندلی {s}. {escape(tname, quote=False)} | حدس نقش: {guess_name} {tick}")
        g.night_done.add("mafia")
        store.save()
        await _tk_check_open_citizens(ctx, chat_id, g)
        return

    if data == "tk_nt_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_nato_seat = s
        g.night_sel.pop(uid, None)
        store.save()
        names = _nem_citizen_role_names(g)
        rows = [[InlineKeyboardButton(rn, callback_data=f"tk_nrole_{i}")] for i, rn in enumerate(names)]
        await _edit_pm(ctx, uid, mid, f"🕵️ نقش صندلی {s} را حدس بزن:", InlineKeyboardMarkup(rows))
        return

    if data.startswith("tk_nt_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = [x for x in _alive_seats(g) if x not in _mafia_seats(g, alive_only=True)]
        await _edit_pm(ctx, uid, mid, "🕵️ چه کسی را ناتویی می‌کنی؟",
                       _kb_night_seats(targets, g, "tk_nt_", selected=s, confirm_cb="tk_nt_confirm"))
        return

    # ── کاراگاه (مستقیم) ──
    if data.startswith("tk_det_"):
        s = int(data.rsplit("_", 1)[1])
        _tu, tname = g.seats[s]
        res = "مثبت ✅" if _tk_guide_positive(g, s) else "منفی ❌"
        await _close_pm(ctx, uid, mid, f"🔎 استعلام {s}. {tname}: {res}")
        await _night_report(ctx, g, f"🔎 کاراگاه → استعلام {s}. {escape(tname, quote=False)}: <b>{res}</b>")
        g.night_done.add("detective")
        store.save()
        await _tk_check_open_gunman(ctx, chat_id, g)
        return

    # ── پزشک ──
    if data == "tk_doc_confirm":
        sel = list(g.night_doc_sel.get(uid, []))
        if not sel:
            await safe_q_answer(q, "یک نفر را انتخاب کن.", show_alert=True)
            return
        doc = _seat_of_uid(g, uid)
        if doc in sel:
            g.doctor_self_saves = (g.doctor_self_saves or 0) + 1
        g.night_doc_saved = list(sel)
        names = "، ".join(f"{s}. {g.seats[s][1]}" for s in sel)
        await _close_pm(ctx, uid, mid, f"💉 سیو ثبت شد: {names}")
        await _night_report(ctx, g, f"💉 پزشک → سیو: <b>{escape(names, quote=False)}</b>")
        g.night_done.add("doctor")
        store.save()
        await _tk_check_open_gunman(ctx, chat_id, g)
        return

    if data.startswith("tk_doc_"):
        s = int(data.rsplit("_", 1)[1])
        sel = set(g.night_doc_sel.get(uid, []))
        need = g.night_doc_need or 1
        if s in sel:
            sel.remove(s)
        elif len(sel) >= need:
            await safe_q_answer(q, f"حداکثر {need} نفر.", show_alert=True)
            return
        else:
            sel.add(s)
        g.night_doc_sel[uid] = list(sel)
        store.save()
        doc = _seat_of_uid(g, uid)
        await _edit_pm(ctx, uid, mid, f"💉 چه کسی را سیو می‌دهی؟ (تا {need} نفر)",
                       _kb_night_seats(_doctor_targets(g, doc), g, "tk_doc_",
                                       selected=sel, confirm_cb="tk_doc_confirm"))
        return

    # ── تکاور (شلیک متقابل) ──
    if data == "tk_com_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_commando_target = s
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, f"🎖 شلیک تکاور ثبت شد: {s}. {tname}")
        await _night_report(ctx, g, f"🎖 تکاور → شلیک متقابل به <b>{s}. {escape(tname, quote=False)}</b>")
        g.night_done.add("commando")
        store.save()
        await _tk_check_open_gunman(ctx, chat_id, g)
        return

    if data.startswith("tk_com_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        com = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != com]
        await _edit_pm(ctx, uid, mid, "🎖 شما شات شدید! می‌توانید یک نفر را بزنید (یک‌بار):",
                       _kb_night_seats(targets, g, "tk_com_", selected=s, confirm_cb="tk_com_confirm"))
        return

    # ── تفنگدار ──
    if data == "tk_gun_no":
        await _close_pm(ctx, uid, mid, "🔫 امشب تفنگ ندادی.")
        await _night_report(ctx, g, "🔫 تفنگدار → تفنگ نداد")
        g.night_done.add("gunman")
        store.save()
        return

    if data == "tk_gun_yes":
        gun = _seat_of_uid(g, uid)
        targets = _alive_seats(g)
        await _edit_pm(ctx, uid, mid, "🔫 تفنگ اول را به چه کسی می‌دهی؟",
                       _kb_night_seats(targets, g, "tk_g1_",
                                       selected=g.night_sel.get(uid), confirm_cb="tk_g1_confirm"))
        return

    if data == "tk_g1_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_gun1_target = s
        g.night_sel.pop(uid, None)
        store.save()
        gun = _seat_of_uid(g, uid)
        _tu, tname = g.seats[s]
        await _edit_pm(ctx, uid, mid, f"🔫 تفنگ اول به {s}. {tname} — نوع؟",
                       _tk_gun_type_kb(g, s, 1, gun))
        return

    if data.startswith("tk_g1_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        await _edit_pm(ctx, uid, mid, "🔫 تفنگ اول را به چه کسی می‌دهی؟",
                       _kb_night_seats(_alive_seats(g), g, "tk_g1_", selected=s, confirm_cb="tk_g1_confirm"))
        return

    if data in ("tk_g1war", "tk_g1blank"):
        g.night_gun1_type = "war" if data == "tk_g1war" else "blank"
        store.save()
        await _tk_ask_gun2(ctx, uid, mid)
        return

    if data == "tk_gun2_no":
        await _tk_finalize_gunman(ctx, chat_id, g, uid, mid)
        return

    if data == "tk_gun2_yes":
        await _edit_pm(ctx, uid, mid, "🔫 تفنگ دوم را به چه کسی می‌دهی؟",
                       _kb_night_seats(_alive_seats(g), g, "tk_g2_",
                                       selected=g.night_sel.get(uid), confirm_cb="tk_g2_confirm"))
        return

    if data == "tk_g2_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_gun2_target = s
        g.night_sel.pop(uid, None)
        store.save()
        gun = _seat_of_uid(g, uid)
        _tu, tname = g.seats[s]
        await _edit_pm(ctx, uid, mid, f"🔫 تفنگ دوم به {s}. {tname} — نوع؟",
                       _tk_gun_type_kb(g, s, 2, gun))
        return

    if data.startswith("tk_g2_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        await _edit_pm(ctx, uid, mid, "🔫 تفنگ دوم را به چه کسی می‌دهی؟",
                       _kb_night_seats(_alive_seats(g), g, "tk_g2_", selected=s, confirm_cb="tk_g2_confirm"))
        return

    if data in ("tk_g2war", "tk_g2blank"):
        g.night_gun2_type = "war" if data == "tk_g2war" else "blank"
        store.save()
        await _tk_finalize_gunman(ctx, chat_id, g, uid, mid)
        return


# ═════════════════════════════════════════════════════════════
#  موتور شبِ خودکار — سناریو «کاپو» (مرحله ۱: هسته)
#  ترتیب: دن (شات/یاکوزایی/جلادی) → جلاد → جادوگر → شهروندان
# ═════════════════════════════════════════════════════════════
KAPU_KEY       = "کاپو"
_R_EXECUTIONER = _nz("جلاد")
_R_WITCH       = _nz("جادوگر")
_R_SUSPECT     = _nz("مظنون")
_R_ATTAR       = _nz("عطار")
_R_ARMORER     = _nz("زره‌ساز")
_R_HEIR        = _nz("وارث")


def _is_kapu_scenario(g) -> bool:
    return bool(getattr(g, "scenario", None)) and (_nz(KAPU_KEY) in _nz(g.scenario.name))


def _kp_heir_immune(g, seat) -> bool:
    """وارث تا وقتی فردِ انتخابیِ شهروندش زنده است و هنوز ارث نبرده، نامیراست (جز جلادی)."""
    if seat != g.heir_seat or g.heir_inherited:
        return False
    ht = g.heir_target
    if ht is None or ht in (g.striked or set()):
        return False
    if ht in _mafia_seats(g):
        return False  # اگر مافیا انتخاب کرده باشد نامیرا نیست
    return True


def _kp_detective_positive(g, seat) -> bool:
    # مظنون، جلاد، جادوگر مثبت؛ دن‌مافیا همیشه منفی؛ بقیه منفی
    rn = _seat_role_norm(g, seat)
    if seat in (g.negotiated_seats or set()):
        return True
    return rn in (_R_SUSPECT, _R_EXECUTIONER, _R_WITCH)


async def _kp_open_don(ctx, chat_id, g):
    # ترتیب شات: دن → جادوگر → جلاد
    don = _find_seat_by_role(g, _R_DON)
    witch = _find_seat_by_role(g, _R_WITCH)
    ex = _find_seat_by_role(g, _R_EXECUTIONER)
    decider = don or witch or ex
    if not decider:
        g.night_done.add("mafia")
        store.save()
        await _kp_check_open_witch(ctx, chat_id, g)
        return
    g.kp_decider_seat = decider
    duid = g.seats[decider][0]
    rows = [[InlineKeyboardButton("🔫 شات", callback_data="kp_don_shot")]]
    # یاکوزایی فقط اگر دن زنده و مصرف نشده
    if don is not None and not g.yakuza_used:
        rows.append([InlineKeyboardButton("🥷 یاکوزایی", callback_data="kp_don_yakuza")])
    # جلادی فقط اگر جلاد زنده و مصرف نشده
    if ex is not None and not g.jalad_used:
        rows.append([InlineKeyboardButton("⚔️ جلادی", callback_data="kp_don_jalad")])
    m = await _safe_pm(ctx, duid, f"🌙 شب {g.night_number}\nاکت مافیا را انتخاب کن:",
                       InlineKeyboardMarkup(rows))
    if m:
        g.night_pm_msgs[duid] = m.message_id
    store.save()


async def _kp_check_open_witch(ctx, chat_id, g):
    if "witch_opened" in g.night_done:
        return
    if "mafia" not in g.night_done:
        return
    g.night_done.add("witch_opened")
    store.save()
    witch = _find_seat_by_role(g, _R_WITCH)
    if not witch:
        g.night_done.add("witch")
        store.save()
        await _kp_check_open_citizens(ctx, chat_id, g)
        return
    wuid = g.seats[witch][0]
    targets = [s for s in _alive_seats(g) if s != witch]
    m = await _safe_pm(ctx, wuid, "🔮 روی چه کسی جادو می‌کنی؟",
                       _kb_night_seats(targets, g, "kp_witch_",
                                       selected=g.night_sel.get(wuid), confirm_cb="kp_witch_confirm"))
    if m:
        g.night_pm_msgs[wuid] = m.message_id
    store.save()


async def _kp_check_open_citizens(ctx, chat_id, g):
    if "citizens_opened" in g.night_done:
        return
    if "witch" not in g.night_done:
        return
    g.night_done.add("citizens_opened")
    store.save()

    # 🔎 کاراگاه
    det = _find_seat_by_role(g, _R_DETECTIVE)
    if det:
        duid = g.seats[det][0]
        targets = [s for s in _alive_seats(g) if s != det]
        m = await _safe_pm(ctx, duid, "🔎 استعلام چه کسی را می‌گیری؟",
                           _kb_night_seats(targets, g, "kp_det_"))
        if m:
            g.night_pm_msgs[duid] = m.message_id

    # 🛡 زره‌ساز — در شب جلادی استراحت
    if not g.night_doctor_blocked:
        arm = _find_seat_by_role(g, _R_ARMORER)
        if arm:
            auid = g.seats[arm][0]
            targets = _kp_armorer_targets(g, arm)
            m = await _safe_pm(ctx, auid, "🛡 تن چه کسی را زره می‌پوشانی؟",
                               _kb_night_seats(targets, g, "kp_arm_",
                                               selected=g.night_sel.get(auid), confirm_cb="kp_arm_confirm"))
            if m:
                g.night_pm_msgs[auid] = m.message_id

    # 🧪 عطار — سم فقط یک‌بار در کل بازی
    attar = _find_seat_by_role(g, _R_ATTAR)
    if attar and not g.attar_poison_used:
        auid = g.seats[attar][0]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ بله", callback_data="kp_attar_yes")],
            [InlineKeyboardButton("🚫 خیر", callback_data="kp_attar_no")],
        ])
        m = await _safe_pm(ctx, auid, "🧪 امشب می‌خواهی به کسی سم بدهی؟", kb)
        if m:
            g.night_pm_msgs[auid] = m.message_id
    store.save()


def _kp_armorer_targets(g, arm_seat):
    out = []
    for s in _alive_seats(g):
        if s == arm_seat and (g.doctor_self_saves or 0) >= 1:
            continue  # خودش فقط یک‌بار در کل بازی
        out.append(s)
    return out


async def handle_kapu_callback(update, ctx):
    q = update.callback_query
    data = q.data
    uid = q.from_user.id
    g, chat_id = _find_active_night_game(uid, q)
    if g is None:
        await safe_q_answer(q, "بازی فعالی یافت نشد.", show_alert=True)
        return
    await safe_q_answer(q)
    mid = q.message.message_id if q.message else None

    # ── رأی پادزهر (همه به‌جز عطار) ──
    if data in ("kp_anti_yes", "kp_anti_no"):
        g.antidote_votes[uid] = (data == "kp_anti_yes")
        await _close_pm(ctx, uid, mid, "✅ رأی شما ثبت شد.")
        store.save()
        if len(g.antidote_votes) >= len(g.antidote_expected or []):
            await _kp_after_vote(ctx, chat_id, g)
        return

    # ── تصمیم عطار برای پادزهر ──
    if data in ("kp_ag_yes", "kp_ag_no"):
        survived = (data == "kp_ag_yes")
        await _close_pm(ctx, uid, mid, "✅ تصمیم ثبت شد.")
        await _kp_apply_poison(ctx, chat_id, g, g.attar_poisoned_seat, survived)
        return

    # ── تصمیم دن ──
    if data == "kp_don_shot":
        targets = list(_alive_seats(g))   # شاملِ خودِ مافیا
        await _edit_pm(ctx, uid, mid, "🔫 هدف شلیک را انتخاب کن:",
                       _kb_night_seats(targets, g, "kp_st_",
                                       selected=g.night_sel.get(uid), confirm_cb="kp_st_confirm"))
        return

    if data == "kp_don_yakuza":
        g.yakuza_used = True
        store.save()
        me = _seat_of_uid(g, uid)
        teammates = [s for s in _mafia_seats(g, alive_only=True) if s != me]
        if not teammates:
            g.night_yakuza_sacrifice = me
            g.night_sel.pop(uid, None)
            store.save()
            targets = _kp_yakuza_recruit_targets(g)
            await _edit_pm(ctx, uid, mid, "🥷 یاری نداری؛ خودت فدا می‌شوی.\nچه کسی را جذب می‌کنی؟",
                           _kb_night_seats(targets, g, "kp_yakrec_", confirm_cb="kp_yakrec_confirm"))
        else:
            await _edit_pm(ctx, uid, mid, "🥷 کدام یارت را فدا می‌کنی؟",
                           _kb_night_seats(teammates, g, "kp_yaksac_",
                                           selected=g.night_sel.get(uid), confirm_cb="kp_yaksac_confirm"))
        return

    if data == "kp_don_jalad":
        ex = _find_seat_by_role(g, _R_EXECUTIONER)
        if ex is None:
            await safe_q_answer(q, "جلاد زنده نیست.", show_alert=True)
            return
        ex_uid = g.seats[ex][0]
        targets = [s for s in _alive_seats(g) if s not in _mafia_seats(g, alive_only=True)]
        kb = _kb_night_seats(targets, g, "kp_jt_", selected=g.night_sel.get(ex_uid), confirm_cb="kp_jt_confirm")
        if ex_uid == uid:
            await _edit_pm(ctx, uid, mid, "⚔️ نقشِ چه کسی را حدس می‌زنی؟", kb)
            g.night_pm_msgs[ex_uid] = mid
        else:
            await _close_pm(ctx, uid, mid, "⚔️ جلادی انتخاب شد. منتظر جلاد بمانید.")
            m = await _safe_pm(ctx, ex_uid, "⚔️ نقشِ چه کسی را حدس می‌زنی؟", kb)
            if m:
                g.night_pm_msgs[ex_uid] = m.message_id
        store.save()
        return

    # ── شلیک ──
    if data == "kp_st_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_shot_target = s
        g.night_don_act = "shot"
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, f"✅ شلیک ثبت شد: {s}. {tname}")
        await _night_report(ctx, g, f"🔫 شلیک مافیا → <b>{s}. {escape(tname, quote=False)}</b>")
        g.night_done.add("mafia")
        store.save()
        await _kp_check_open_witch(ctx, chat_id, g)
        return

    if data.startswith("kp_st_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = list(_alive_seats(g))   # شاملِ خودِ مافیا
        await _edit_pm(ctx, uid, mid, "🔫 هدف شلیک را انتخاب کن:",
                       _kb_night_seats(targets, g, "kp_st_", selected=s, confirm_cb="kp_st_confirm"))
        return

    # ── یاکوزایی: فدا ──
    if data == "kp_yaksac_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک یار را انتخاب کن.", show_alert=True)
            return
        g.night_yakuza_sacrifice = s
        g.night_sel.pop(uid, None)
        store.save()
        targets = _kp_yakuza_recruit_targets(g)
        await _edit_pm(ctx, uid, mid, "🥷 چه کسی را جذب می‌کنی؟",
                       _kb_night_seats(targets, g, "kp_yakrec_", confirm_cb="kp_yakrec_confirm"))
        return

    if data.startswith("kp_yaksac_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        me = _seat_of_uid(g, uid)
        teammates = [x for x in _mafia_seats(g, alive_only=True) if x != me]
        await _edit_pm(ctx, uid, mid, "🥷 کدام یارت را فدا می‌کنی؟",
                       _kb_night_seats(teammates, g, "kp_yaksac_", selected=s, confirm_cb="kp_yaksac_confirm"))
        return

    # ── یاکوزایی: جذب (فقط شهرساده یا مظنون) ──
    if data == "kp_yakrec_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_don_act = "yakuza"
        tu, tname = g.seats[s]
        rn = _seat_role_norm(g, s)
        convertible = (rn in _R_CITIZEN or rn == _R_SUSPECT) and not (
            s == g.heir_seat and g.heir_no_yakuza)
        sac = g.night_yakuza_sacrifice
        sac_txt = f"{sac}. {g.seats[sac][1]}" if sac in g.seats else "—"
        if convertible:
            g.negotiated_seats.add(s)
            try:
                await ctx.bot.send_message(tu, "🥷 با شما یاکوزایی انجام شده است. اکنون مافیا هستید.")
            except Exception:
                pass
            await _room_send_link(ctx, g, tu)   # لینک اتاق مافیا فوری
            await _night_report(ctx, g, f"🥷 یاکوزایی → فدا: {sac_txt} | جذب: <b>{s}. {escape(tname, quote=False)}</b> ✅")
        else:
            await _night_report(ctx, g, f"🥷 یاکوزایی → فدا: {sac_txt} | جذب: <b>{s}. {escape(tname, quote=False)}</b> → ناموفق ❌")
        await _close_pm(ctx, uid, mid, "✅ یاکوزایی ثبت شد.")
        g.night_done.add("mafia")
        store.save()
        await _kp_check_open_witch(ctx, chat_id, g)
        return

    if data.startswith("kp_yakrec_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = _kp_yakuza_recruit_targets(g)
        await _edit_pm(ctx, uid, mid, "🥷 چه کسی را جذب می‌کنی؟",
                       _kb_night_seats(targets, g, "kp_yakrec_", selected=s, confirm_cb="kp_yakrec_confirm"))
        return

    # ── جلادی (حدس نقش) ──
    if data.startswith("kp_jrole_"):
        i = int(data.rsplit("_", 1)[1])
        names = _nem_citizen_role_names(g)
        if i >= len(names):
            return
        guess_name = names[i]
        s = g.night_jalad_seat
        if not s:
            await safe_q_answer(q, "اول هدف را انتخاب کن.", show_alert=True)
            return
        _tu, tname = g.seats[s]
        correct = (_nz(guess_name) == _seat_role_norm(g, s))
        g.night_jalad_correct = correct
        g.night_jalad_target = s
        tick = "✅" if correct else "❌"
        g.jalad_used = True
        await _close_pm(ctx, uid, mid, f"⚔️ جلادی روی {s}. {tname} ثبت شد.")
        await _night_report(ctx, g, f"⚔️ جلاد → صندلی {s}. {escape(tname, quote=False)} | حدس نقش: {guess_name} {tick}")
        g.night_doctor_blocked = True
        g.night_done.add("mafia")
        store.save()
        await _kp_broadcast_jalad(ctx, g)
        await _kp_check_open_witch(ctx, chat_id, g)
        return

    if data == "kp_jt_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_jalad_seat = s
        g.night_sel.pop(uid, None)
        store.save()
        names = _nem_citizen_role_names(g)
        rows = [[InlineKeyboardButton(rn, callback_data=f"kp_jrole_{i}")] for i, rn in enumerate(names)]
        await _edit_pm(ctx, uid, mid, f"⚔️ نقش صندلی {s} را حدس بزن:", InlineKeyboardMarkup(rows))
        return

    if data.startswith("kp_jt_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = [x for x in _alive_seats(g) if x not in _mafia_seats(g, alive_only=True)]
        await _edit_pm(ctx, uid, mid, "⚔️ نقشِ چه کسی را حدس می‌زنی؟",
                       _kb_night_seats(targets, g, "kp_jt_", selected=s, confirm_cb="kp_jt_confirm"))
        return

    # ── جادوگر ──
    if data == "kp_witch_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_witch_target = s
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, f"🔮 جادو ثبت شد: {s}. {tname}")
        await _night_report(ctx, g, f"🔮 جادوگر → جادو روی <b>{s}. {escape(tname, quote=False)}</b>")
        g.night_done.add("witch")
        store.save()
        await _kp_check_open_citizens(ctx, chat_id, g)
        return

    if data.startswith("kp_witch_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        witch = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != witch]
        await _edit_pm(ctx, uid, mid, "🔮 روی چه کسی جادو می‌کنی؟",
                       _kb_night_seats(targets, g, "kp_witch_", selected=s, confirm_cb="kp_witch_confirm"))
        return

    # ── کاراگاه (با اثر جادو) ──
    if data.startswith("kp_det_"):
        s = int(data.rsplit("_", 1)[1])
        det = _seat_of_uid(g, uid)
        witched = (g.night_witch_target == det)
        _tu, tname = g.seats[s]
        if witched:
            pos = False  # جادوشده → استعلامش همیشه منفی (روی خودش)
        else:
            pos = _kp_detective_positive(g, s)
        res = "مثبت ✅" if pos else "منفی ❌"
        await _close_pm(ctx, uid, mid, f"🔎 استعلام {s}. {tname}: {res}")
        await _night_report(ctx, g, f"🔎 کاراگاه → استعلام {s}. {escape(tname, quote=False)}: <b>{res}</b>"
                            + (" (جادو شده)" if witched else ""))
        g.night_done.add("detective")
        store.save()
        return

    # ── زره‌ساز (با اثر جادو) ──
    if data == "kp_arm_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        arm = _seat_of_uid(g, uid)
        witched = (g.night_witch_target == arm)
        if witched:
            target = arm  # جادو → زره روی خودش (بدون مصرفِ تک‌سیو)
        else:
            target = s
            if target == arm:
                g.doctor_self_saves = (g.doctor_self_saves or 0) + 1
        g.night_doc_saved = [target]
        # ⚠️ به خودِ زره‌ساز همیشه «انتخابِ خودش» را نشان بده تا جادوشدن لو نرود
        _sn = g.seats[s][1]
        await _close_pm(ctx, uid, mid, f"🛡 زره ثبت شد: {s}. {_sn}")
        _tn = g.seats[target][1]
        await _night_report(ctx, g, f"🛡 زره‌ساز → زره روی <b>{target}. {escape(_tn, quote=False)}</b>"
                            + (f" (جادو شده — انتخابش {s}. {escape(_sn, quote=False)} بود)" if witched else ""))
        g.night_done.add("armorer")
        store.save()
        return

    if data.startswith("kp_arm_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        arm = _seat_of_uid(g, uid)
        await _edit_pm(ctx, uid, mid, "🛡 تن چه کسی را زره می‌پوشانی؟",
                       _kb_night_seats(_kp_armorer_targets(g, arm), g, "kp_arm_", selected=s, confirm_cb="kp_arm_confirm"))
        return

    # ── عطار (مرحلهٔ ۱: ثبت سم؛ سازوکار کامل در مرحلهٔ ۲) ──
    if data == "kp_attar_no":
        await _close_pm(ctx, uid, mid, "🧪 امشب سم ندادی.")
        await _night_report(ctx, g, "🧪 عطار → سم نداد")
        g.night_done.add("attar")
        store.save()
        return

    if data == "kp_attar_yes":
        attar = _seat_of_uid(g, uid)
        targets = [s for s in _alive_seats(g) if s != attar]
        await _edit_pm(ctx, uid, mid, "🧪 به چه کسی سم می‌دهی؟",
                       _kb_night_seats(targets, g, "kp_att_", confirm_cb="kp_att_confirm"))
        return

    if data == "kp_att_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        attar = _seat_of_uid(g, uid)
        witched = (g.night_witch_target == attar)
        target = attar if witched else s
        g.night_attar_poison_target = target
        g.attar_poisoned_seat = target      # شب بعد تعیین‌تکلیف می‌شود
        g.attar_poison_used = True           # سم یک‌بار در کل بازی
        _tu, tname = g.seats[target]
        await _close_pm(ctx, uid, mid, "🧪 سم ثبت شد.")
        await _night_report(ctx, g, f"🧪 عطار → سم به <b>{target}. {escape(tname, quote=False)}</b>"
                            + (" (جادو شده)" if witched else ""))
        g.night_done.add("attar")
        store.save()
        return

    if data.startswith("kp_att_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        attar = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != attar]
        await _edit_pm(ctx, uid, mid, "🧪 به چه کسی سم می‌دهی؟",
                       _kb_night_seats(targets, g, "kp_att_", selected=s, confirm_cb="kp_att_confirm"))
        return

    # ── انتخاب وارث (شب معارفه) ──
    if data == "kp_heir_confirm":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.heir_seat = _seat_of_uid(g, uid)
        g.heir_target = s
        _tu, tname = g.seats[s]
        await _close_pm(ctx, uid, mid, f"⚱️ انتخاب وارث ثبت شد: {s}. {tname}")
        await _night_report(ctx, g, f"⚱️ وارث → انتخاب: <b>{s}. {escape(tname, quote=False)}</b>")
        store.save()
        return

    if data.startswith("kp_heir_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        heir = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != heir]
        await _edit_pm(ctx, uid, mid, "⚱️ چه کسی را انتخاب می‌کنی؟",
                       _kb_night_seats(targets, g, "kp_heir_", selected=s, confirm_cb="kp_heir_confirm"))
        return


def _kp_yakuza_recruit_targets(g):
    out = []
    for s in _alive_seats(g):
        if s in _mafia_seats(g, alive_only=True):
            continue
        rn = _seat_role_norm(g, s)
        if rn in _R_CITIZEN or rn == _R_SUSPECT:
            if s == g.heir_seat and g.heir_no_yakuza:
                continue
            out.append(s)
    return out


async def _kp_broadcast_jalad(ctx, g):
    for s in _alive_seats(g):
        try:
            await ctx.bot.send_message(g.seats[s][0], "🌙 امشب جلادی می‌شود.")
        except Exception:
            pass
    arm = _find_seat_by_role(g, _R_ARMORER)
    if arm:
        try:
            await ctx.bot.send_message(g.seats[arm][0], "💤 امشب استراحت کن — حق زره دادن نداری.")
        except Exception:
            pass


def _kp_antidote_holder(g):
    """دارندهٔ پادزهر: دن‌مافیا اگر زنده باشد، وگرنه جادوگر."""
    don = _find_seat_by_role(g, _R_DON)
    if don is not None:
        return don
    return _find_seat_by_role(g, _R_WITCH)


async def _kp_check_heir_inherit(ctx, g):
    """اگر فردِ انتخابیِ وارث مُرده باشد، وارث صاحب نقش/شهرساده می‌شود."""
    if not (g.heir_seat and not g.heir_inherited and g.heir_target):
        return
    if g.heir_target not in (g.striked or set()) or g.heir_target in _mafia_seats(g):
        return
    role = (g.assigned_roles or {}).get(g.heir_target)
    rn = _nz(role or "")
    g.heir_inherited = True
    if rn in (_R_DETECTIVE, _R_ARMORER, _R_ATTAR):
        g.assigned_roles[g.heir_seat] = role
        new_txt = role
    else:
        g.assigned_roles[g.heir_seat] = "شهرساده"
        g.heir_no_yakuza = True
        new_txt = "شهرساده (بدون توانایی)"
    store.save()
    try:
        await ctx.bot.send_message(g.seats[g.heir_seat][0], f"⚱️ شما اکنون «{new_txt}» هستید.")
    except Exception:
        pass
    await _night_report(ctx, g, f"⚱️ وارث → نقشِ جدید: «{escape(new_txt, quote=False)}»")


async def _kp_begin_poison(ctx, chat_id, g):
    """شروع شب: اعلام سم + رأی‌گیری پادزهر از همه (جز عطار)."""
    target = g.attar_poisoned_seat
    # اگر فردِ سم‌دار پیش از تعیین‌تکلیف از بازی خارج شده → بدون رأی‌گیری
    if target not in g.seats or target in (g.striked or set()):
        if target in g.seats:
            tname = g.seats[target][1]
            for s in _alive_seats(g):
                try:
                    await ctx.bot.send_message(
                        g.seats[s][0],
                        f"🧪 سم در بدن {target}. {tname} بود، اما ایشان دیگر در بازی نیستند.")
                except Exception:
                    pass
            await _night_report(ctx, g, f"🧪 سم در بدن {target}. {escape(tname, quote=False)} بود؛ از بازی خارج شده — رأی‌گیری لازم نیست.")
        g.attar_poisoned_seat = None
        store.save()
        await _kp_open_don(ctx, chat_id, g)
        return
    g.poison_phase = True
    g.antidote_votes = {}
    tname = g.seats[target][1]
    attar = _find_seat_by_role(g, _R_ATTAR)
    for s in _alive_seats(g):
        try:
            await ctx.bot.send_message(g.seats[s][0], f"🧪 سم عطار وارد خون سیت {target}. {tname} شده است.")
        except Exception:
            pass
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ بله (پادزهر)", callback_data="kp_anti_yes"),
        InlineKeyboardButton("🚫 خیر", callback_data="kp_anti_no"),
    ]])
    expected = []
    for s in _alive_seats(g):
        if s == attar:
            continue
        uid = g.seats[s][0]
        m = await _safe_pm(ctx, uid, "آیا موافق دادنِ پادزهر هستید؟", kb)
        if m:
            g.night_pm_msgs[uid] = m.message_id
            expected.append(uid)
    g.antidote_expected = expected
    store.save()
    if not expected:
        await _kp_after_vote(ctx, chat_id, g)


async def _kp_after_vote(ctx, chat_id, g):
    target = g.attar_poisoned_seat
    votes = g.antidote_votes or {}
    yes = sum(1 for v in votes.values() if v)
    no = sum(1 for v in votes.values() if not v)
    alive = len(_alive_seats(g))
    threshold = alive // 2 + 1
    majority_for = (yes >= threshold)

    def _names(want):
        out = []
        for u, v in votes.items():
            if v is want:
                seat = _seat_of_uid(g, u)
                if seat:
                    out.append(f"{seat}. {g.seats[seat][1]}")
        return "، ".join(out) if out else "—"

    tname = g.seats[target][1] if target in g.seats else "—"
    await _night_report(
        ctx, g,
        f"🧪 <b>رأی پادزهر</b> (هدف {target}. {escape(tname, quote=False)})\n"
        f"موافق: {yes} | مخالف: {no} (نصاب اکثریت: {threshold})\n"
        f"✅ موافقان: {escape(_names(True), quote=False)}\n"
        f"❌ مخالفان: {escape(_names(False), quote=False)}"
    )

    holder = _kp_antidote_holder(g)
    attar = _find_seat_by_role(g, _R_ATTAR)
    if target == holder:
        # دن/دارندهٔ پادزهر → زنده (پنهان)
        await _kp_apply_poison(ctx, chat_id, g, target, survived=True)
    elif attar is None:
        # عطار در بازی نیست → تصمیم با اکثریت
        await _kp_apply_poison(ctx, chat_id, g, target, survived=majority_for)
    else:
        attar_uid = g.seats[attar][0]
        mtxt = "اکثریت موافقِ دادن پادزهر هستند." if majority_for else "اکثریت مخالفِ دادن پادزهر هستند."
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ پادزهر می‌دهم", callback_data="kp_ag_yes"),
            InlineKeyboardButton("🚫 نمی‌دهم", callback_data="kp_ag_no"),
        ]])
        m = await _safe_pm(ctx, attar_uid, f"🧪 {mtxt}\nآیا پادزهر می‌دهی؟", kb)
        if m:
            g.night_pm_msgs[attar_uid] = m.message_id
        store.save()


async def _kp_apply_poison(ctx, chat_id, g, target, survived):
    recipients = list(_alive_seats(g))  # قبل از خط‌زدن
    tname = g.seats[target][1] if target in g.seats else "—"
    if survived:
        msg = f"🧪 {target}. {tname} پادزهر گرفت و زنده ماند."
        await _night_report(ctx, g, msg)
    else:
        if target in g.seats and target not in (g.striked or set()):
            g.striked.add(target)
        msg = f"🧪 {target}. {tname} پادزهر نگرفت و کشته شد."
        await _night_report(ctx, g, msg + " (خط خورد)")
    for s in recipients:
        try:
            await ctx.bot.send_message(g.seats[s][0], msg)
        except Exception:
            pass
    g.attar_poisoned_seat = None
    g.poison_phase = False
    g.antidote_votes = {}
    g.antidote_expected = []
    store.save()
    await _kp_check_heir_inherit(ctx, g)   # وارث ممکن است عطار جدید شود
    try:
        await publish_seating(ctx, chat_id, g, mode=CTRL)
    except Exception:
        pass
    await _kp_open_don(ctx, chat_id, g)     # حالا شبِ عادی باز می‌شود


# ═════════════════════════════════════════════════════════════
#  موتور شبِ خودکار — سناریو «گیمر» (۱۰/۱۲/۱۷ نفره)
#  تشخیص بر اساس «نقش‌ها» است نه اسم سناریو (دن‌کارلئونه = گیمر)
#  ترتیب: رابین‌هود → مسترهلمز → مافیا (دن/تووفیس/موریارتی) → شهروندان
# ═════════════════════════════════════════════════════════════
_R_DONC     = _nz("دن‌کارلئونه")
_R_TWOFACE  = _nz("تووفیس")
_R_MORIARTY = _nz("موریارتی")
_R_ELLIOT   = _nz("الیوت")
_R_RICK     = _nz("ریک‌گرایمز")
_R_ROBIN    = _nz("رابین‌هود")
_R_JAMES    = _nz("جیمزهالیدی")
_R_LOGAN    = _nz("لوگان")
_R_HOLMES   = _nz("مسترهلمز")
_R_CASTIEL  = _nz("کستیل")

_GM_FUSE_COLORS = ["زرد", "قرمز", "آبی"]
_GM_FUSE_TYPES  = ["انفجار", "خنثی", "سرعت"]


def _is_gamer_scenario(g) -> bool:
    """تشخیص بر اساس نقش: اگر دن‌کارلئونه بین نقش‌ها باشد → گیمر (هر تعداد نفره)."""
    roles = getattr(g, "assigned_roles", None) or {}
    if any(_nz(r) == _R_DONC for r in roles.values()):
        return True
    sc = getattr(g, "scenario", None)
    return bool(sc) and (_nz("گیمر") in _nz(sc.name))


def _gm_actor_for(g, role_seat):
    """چه صندلی‌ای امشب اکتِ این نقش را انجام می‌دهد؟ (انتقالِ رابین‌هود)"""
    if role_seat is None:
        return None
    if g.gm_gift_accepted and g.gm_robbed_seat == role_seat:
        return g.gm_gift_to
    return role_seat


def _gm_own_act_skipped(g, seat) -> bool:
    """گیرنده‌ی هدیه، اکتِ نقشِ خودش را در آن شب از دست می‌دهد."""
    return bool(g.gm_gift_accepted and g.gm_gift_to == seat and g.gm_robbed_seat != seat)


def _gm_rick_unlocked(g) -> bool:
    """ریک بعد از خروجِ ۲ شهروند آزاد می‌شود (شهروند = غیرمافیا، بر اساس نقش)."""
    mafia = _mafia_seats(g)
    return sum(1 for s in (g.striked or set()) if s not in mafia) >= 2


def _gm_citizen_role_names(g):
    mafia = set(_mafia_role_set(g))
    names, seen = [], set()
    for rname in (g.scenario.roles.keys() if g.scenario else []):
        n = _nz(rname)
        if n in mafia or n in seen:
            continue
        seen.add(n); names.append(rname)
    return names


async def _gm_prompt(ctx, g, seat, key, text, kb=None):
    """پرامپت اکت به بازیکن + ثبت در فهرست انتظار."""
    uid = g.seats[seat][0]
    m = await _safe_pm(ctx, uid, text, kb)
    if m:
        g.night_pm_msgs[uid] = m.message_id
    g.gm_expected.add(key)
    store.save()
    return m


def _gm_yesno_kb(yes_cb, no_cb):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ بله", callback_data=yes_cb)],
        [InlineKeyboardButton("🚫 خیر", callback_data=no_cb)],
    ])


# ── مرحله ۱: رابین‌هود ─────────────────────────────────────────
async def _gm_open_robin(ctx, chat_id, g):
    rb = _find_seat_by_role(g, _R_ROBIN)
    if not rb or g.gm_robin_uses >= 2:
        g.night_done.add("robin")
        store.save()
        await _gm_open_holmes(ctx, chat_id, g)
        return
    await _gm_prompt(ctx, g, rb, "robin",
                     f"🏹 شب {g.night_number} — می‌خواهی راهزنی کنی؟ (باقی‌مانده: {2 - g.gm_robin_uses})",
                     _gm_yesno_kb("gm_rb_yes", "gm_rb_no"))


# ── مرحله ۲: مسترهلمز ─────────────────────────────────────────
async def _gm_open_holmes(ctx, chat_id, g):
    hs = _find_seat_by_role(g, _R_HOLMES)
    if not hs or g.gm_holmes_uses >= 3 or _gm_own_act_skipped(g, hs):
        g.night_done.add("holmes")
        store.save()
        await _gm_open_mafia(ctx, chat_id, g)
        return
    actor = _gm_actor_for(g, hs)
    await _gm_prompt(ctx, g, actor, "holmes",
                     f"🕵️ می‌خواهی حدس بزنی دن‌کارلئونه کیست؟ (باقی‌مانده: {3 - g.gm_holmes_uses})",
                     _gm_yesno_kb("gm_hm_yes", "gm_hm_no"))


# ── مرحله ۳: مافیا (موازی: شات / بمب / موریارتی) ─────────────
async def _gm_open_mafia(ctx, chat_id, g):
    if "mafia_opened" in g.night_done:
        return
    g.night_done.add("mafia_opened")
    store.save()

    don = _find_seat_by_role(g, _R_DONC)
    tf = _find_seat_by_role(g, _R_TWOFACE)
    mo = _find_seat_by_role(g, _R_MORIARTY)

    # 🔫 شات — دزدیده‌شدنِ دن یا حدسِ درستِ هلمز = بدون شات برای مافیا
    don_robbed = (g.gm_gift_accepted and g.gm_robbed_seat == don and don is not None)
    if g.gm_holmes_correct:
        decider = don or tf or mo
        if decider:
            await _safe_pm(ctx, g.seats[decider][0], "😶 امشب مافیا توانِ شات ندارد.")
        await _night_report(ctx, g, "😶 مافیا امشب شات ندارد (حدسِ درستِ هلمز).")
        g.night_done.add("shot")
    elif don_robbed:
        await _safe_pm(ctx, g.seats[don][0], "🏹 نقش شما دزدیده شده و حق شات ندارید.")
        await _night_report(ctx, g, "🏹 شاتِ مافیا امشب دستِ گیرنده‌ی هدیه‌ی رابین‌هود است.")
        # شات را گیرنده‌ی هدیه می‌زند — می‌تواند «هر کسی» را بزند، حتی مافیا (فقط خودش نه)
        actor = g.gm_gift_to
        targets = [s for s in _alive_seats(g) if s != actor]
        await _gm_prompt(ctx, g, actor, "shot", "🔫 (اکتِ هدیه) هدف شلیک را انتخاب کن:",
                         _kb_night_seats(targets, g, "gm_st_", confirm_cb="gm_st_ok"))
    else:
        decider = don or tf or mo
        if not decider:
            g.night_done.add("shot")
        else:
            targets = list(_alive_seats(g))   # شاملِ خودِ مافیا
            await _gm_prompt(ctx, g, decider, "shot", "🔫 هدف شلیک را انتخاب کن:",
                             _kb_night_seats(targets, g, "gm_st_", confirm_cb="gm_st_ok"))

    # 💣 تووفیس — فقط شب‌های فرد
    odd = (g.night_number % 2 == 1)
    if odd and tf and not _gm_own_act_skipped(g, tf):
        actor = _gm_actor_for(g, tf)
        targets = [s for s in _alive_seats(g) if s not in _mafia_seats(g, alive_only=True)]
        await _gm_prompt(ctx, g, actor, "bomb", "💣 جلوی چه کسی بمب می‌گذاری؟",
                         _kb_night_seats(targets, g, "gm_tf_", confirm_cb="gm_tf_ok"))
    else:
        g.night_done.add("bomb")

    # 🎭 موریارتی — اختیاری، ۳ حدس در کل بازی
    if mo and g.gm_moriarty_uses < 3 and not _gm_own_act_skipped(g, mo):
        actor = _gm_actor_for(g, mo)
        await _gm_prompt(ctx, g, actor, "moriarty",
                         f"🎭 می‌خواهی حدس بزنی مسترهلمز کیست؟ (باقی‌مانده: {3 - g.gm_moriarty_uses})",
                         _gm_yesno_kb("gm_mo_yes", "gm_mo_no"))
    else:
        g.night_done.add("moriarty")
    store.save()
    await _gm_check_open_citizens(ctx, chat_id, g)


async def _gm_check_open_citizens(ctx, chat_id, g):
    if "citizens_opened" in g.night_done:
        return
    if not ({"shot", "bomb", "moriarty"} <= g.night_done):
        return
    g.night_done.add("citizens_opened")
    store.save()

    # 💉 کستیل (دکتر) — ۱ نفر در شب؛ خودش حداکثر ۲ بار در کل بازی
    cs = _find_seat_by_role(g, _R_CASTIEL)
    if cs and not _gm_own_act_skipped(g, cs):
        actor = _gm_actor_for(g, cs)
        targets = _doctor_targets(g, actor)
        await _gm_prompt(ctx, g, actor, "doctor", "💉 چه کسی را سیو می‌دهی؟ (۱ نفر)",
                         _kb_night_seats(targets, g, "gm_doc_", confirm_cb="gm_doc_ok"))

    # 🛡 الیوت — فقط شب‌های فرد، اختیاری
    el = _find_seat_by_role(g, _R_ELLIOT)
    if (g.night_number % 2 == 1) and el and not _gm_own_act_skipped(g, el):
        actor = _gm_actor_for(g, el)
        await _gm_prompt(ctx, g, actor, "eliot",
                         "🛡 می‌خواهی امشب از کسی در برابر بمب محافظت کنی؟",
                         _gm_yesno_kb("gm_el_yes", "gm_el_no"))

    # 🎲 جیمزهالیدی — اختیاری، ۲ بار در کل بازی
    jm = _find_seat_by_role(g, _R_JAMES)
    if jm and g.gm_james_uses < 2 and not _gm_own_act_skipped(g, jm):
        actor = _gm_actor_for(g, jm)
        await _gm_prompt(ctx, g, actor, "james",
                         f"🎲 می‌خواهی بازی کنی؟ (باقی‌مانده: {2 - g.gm_james_uses})",
                         _gm_yesno_kb("gm_jm_yes", "gm_jm_no"))

    # 🔫 ریک‌گرایمز — بعد از خروج ۲ شهروند، هر شب یک شات
    rk = _find_seat_by_role(g, _R_RICK)
    if rk and _gm_rick_unlocked(g) and not _gm_own_act_skipped(g, rk):
        actor = _gm_actor_for(g, rk)
        await _gm_prompt(ctx, g, actor, "rick", "🔫 می‌خواهی شات بزنی؟",
                         _gm_yesno_kb("gm_rk_yes", "gm_rk_no"))
    store.save()


def _gm_james_nums_kb(g):
    rows = []
    row = []
    for n in range(1, 7):
        mark = "✅ " if n in (g.gm_james_nums or []) else ""
        row.append(InlineKeyboardButton(f"{mark}{n}", callback_data=f"gm_jn_{n}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✅ تأیید (دقیقاً ۲ عدد)", callback_data="gm_jn_ok")])
    return InlineKeyboardMarkup(rows)


async def handle_gamer_callback(update, ctx):
    q = update.callback_query
    data = q.data
    uid = q.from_user.id

    # 💣 خنثی‌سازیِ روز (بعد از /باز) — خارج از شبِ فعال
    if data.startswith(("gm_bz_", "gm_bc_")):
        g = None; chat_id = None
        for cid, game in store.games.items():
            if getattr(game, "gm_bomb_seat", None) is None:
                continue
            el = _find_seat_by_role(game, _R_ELLIOT)
            if el and game.seats[el][0] == uid:
                g, chat_id = game, cid
                break
        if g is None:
            await safe_q_answer(q, "بمبی فعال نیست.", show_alert=True)
            return
        await safe_q_answer(q)
        mid = q.message.message_id if q.message else None
        if data == "gm_bz_no":
            await _close_pm(ctx, uid, mid, "🙅 کاری نکردی. بمب سرِ جای خودش است.")
            await _night_report(ctx, g, "💣 الیوت کاری با بمب نکرد.")
            return
        if data == "gm_bz_yes":
            rows = [[InlineKeyboardButton(c, callback_data=f"gm_bc_{i}")]
                    for i, c in enumerate(_GM_FUSE_COLORS)]
            await _edit_pm(ctx, uid, mid, "✂️ کدام رنگ را انتخاب می‌کنی؟", InlineKeyboardMarkup(rows))
            return
        if data.startswith("gm_bc_"):
            i = int(data.rsplit("_", 1)[1])
            color = _GM_FUSE_COLORS[i]
            ftype = (g.gm_bomb_fuses or {}).get(color, "خنثی")
            seat = g.gm_bomb_seat
            tname = g.seats[seat][1] if seat in g.seats else "؟"
            await _close_pm(ctx, uid, mid, f"✂️ رنگ {color} را انتخاب کردی.")
            await ctx.bot.send_message(chat_id, f"💥 چاشنی «{ftype}» فعال شد!")
            await _night_report(ctx, g, f"💣 الیوت رنگ {color} را زد → چاشنی «{ftype}»")
            if ftype == "خنثی":
                await ctx.bot.send_message(chat_id, "✅ الیوت با موفقیت بمب را خنثی کرد.")
            elif ftype == "انفجار":
                if seat in g.seats and seat not in (g.striked or set()):
                    g.striked.add(seat)
                await ctx.bot.send_message(chat_id, f"💥 بمب منفجر شد! {seat}. {tname} از بازی خارج شد.")
                try:
                    await publish_seating(ctx, chat_id, g, mode=CTRL)
                except Exception:
                    pass
            else:  # سرعت
                await ctx.bot.send_message(
                    chat_id, "⏩ چاشنی سرعت! بمب پس از صحبتِ نیمی از بازیکنان منفجر می‌شود (با گاد).")
            g.gm_bomb_seat = None
            g.gm_bomb_fuses = {}
            store.save()
            return
        return

    g, chat_id = _find_active_night_game(uid, q)
    if g is None:
        await safe_q_answer(q, "بازی فعالی یافت نشد.", show_alert=True)
        return
    await safe_q_answer(q)
    mid = q.message.message_id if q.message else None

    # ── رابین‌هود ──
    if data == "gm_rb_no":
        await _close_pm(ctx, uid, mid, "🏹 امشب راهزنی نکردی.")
        await _night_report(ctx, g, "🏹 رابین‌هود → راهزنی نکرد")
        g.night_done.add("robin")
        store.save()
        await _gm_open_holmes(ctx, chat_id, g)
        return

    if data == "gm_rb_yes":
        rb = _seat_of_uid(g, uid)
        targets = [s for s in _alive_seats(g) if s != rb]
        await _edit_pm(ctx, uid, mid, "🏹 اکتِ چه کسی را می‌دزدی؟",
                       _kb_night_seats(targets, g, "gm_rbx_", confirm_cb="gm_rbx_ok"))
        return

    if data == "gm_rbx_ok":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.gm_robin_steal_from = s
        g.night_sel.pop(uid, None)
        store.save()
        rb = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != rb and x != s]
        await _edit_pm(ctx, uid, mid, "🎁 به چه کسی هدیه می‌دهی؟",
                       _kb_night_seats(targets, g, "gm_rby_", confirm_cb="gm_rby_ok"))
        return

    if data.startswith("gm_rbx_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        rb = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != rb]
        await _edit_pm(ctx, uid, mid, "🏹 اکتِ چه کسی را می‌دزدی؟",
                       _kb_night_seats(targets, g, "gm_rbx_", selected=s, confirm_cb="gm_rbx_ok"))
        return

    if data == "gm_rby_ok":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        x = g.gm_robin_steal_from
        g.gm_robbed_seat = x
        g.gm_gift_to = s
        g.gm_gift_pending = True
        g.gm_robin_uses += 1
        g.night_sel.pop(uid, None)
        await _close_pm(ctx, uid, mid, "✅ راهزنی ثبت شد.")
        await _night_report(ctx, g, f"🏹 رابین‌هود → اکتِ {x}. {escape(g.seats[x][1], quote=False)} "
                            f"به {s}. {escape(g.seats[s][1], quote=False)} هدیه شد (منتظر پاسخ)")
        g.night_done.add("robin")
        store.save()
        await _gm_prompt(ctx, g, s, "gift",
                         "🎁 از رابین‌هود هدیه داری! آیا قبول می‌کنی؟",
                         _gm_yesno_kb("gm_gift_yes", "gm_gift_no"))
        return

    if data.startswith("gm_rby_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        rb = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != rb and x != g.gm_robin_steal_from]
        await _edit_pm(ctx, uid, mid, "🎁 به چه کسی هدیه می‌دهی؟",
                       _kb_night_seats(targets, g, "gm_rby_", selected=s, confirm_cb="gm_rby_ok"))
        return

    # ── پاسخ هدیه ──
    if data in ("gm_gift_yes", "gm_gift_no"):
        accepted = (data == "gm_gift_yes")
        g.gm_gift_accepted = accepted
        g.gm_gift_pending = False
        g.night_done.add("gift")
        if accepted:
            await _close_pm(ctx, uid, mid, "🎁 قبول کردی! اکتِ جدیدت به‌زودی برایت می‌آید.")
            await _night_report(ctx, g, "🎁 هدیه‌ی رابین‌هود پذیرفته شد ✅")
        else:
            await _close_pm(ctx, uid, mid, "🙅 هدیه را رد کردی.")
            await _night_report(ctx, g, "🎁 هدیه‌ی رابین‌هود رد شد ❌")
            g.gm_robbed_seat = None
            g.gm_gift_to = None
        store.save()
        await _gm_open_holmes(ctx, chat_id, g)
        return

    # ── هلمز ──
    if data == "gm_hm_no":
        await _close_pm(ctx, uid, mid, "🕵️ امشب حدس نزدی.")
        await _night_report(ctx, g, "🕵️ هلمز → حدس نزد")
        g.night_done.add("holmes")
        store.save()
        await _gm_open_mafia(ctx, chat_id, g)
        return

    if data == "gm_hm_yes":
        actor = _seat_of_uid(g, uid)
        targets = [s for s in _alive_seats(g) if s != actor]
        await _edit_pm(ctx, uid, mid, "🕵️ چه کسی دن‌کارلئونه است؟",
                       _kb_night_seats(targets, g, "gm_hmg_", confirm_cb="gm_hmg_ok"))
        return

    if data == "gm_hmg_ok":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_sel.pop(uid, None)
        don = _find_seat_by_role(g, _R_DONC)
        actor = _seat_of_uid(g, uid)
        correct = (don is not None and s == don)
        _tn = g.seats[s][1]
        if correct:
            g.gm_holmes_correct = True
            await _close_pm(ctx, uid, mid, f"🕵️ حدس زدی: {s}. {_tn}")
            try:
                await ctx.bot.send_message(uid, "👍")
            except Exception:
                pass
            await _night_report(ctx, g, f"🕵️ هلمز → حدس: {s}. {escape(_tn, quote=False)} ✅ (مافیا امشب شات ندارد)")
        else:
            g.gm_holmes_uses += 1
            await _close_pm(ctx, uid, mid, f"🕵️ حدس زدی: {s}. {_tn}")
            try:
                await ctx.bot.send_message(uid, "👎")
            except Exception:
                pass
            await _night_report(ctx, g, f"🕵️ هلمز → حدس: {s}. {escape(_tn, quote=False)} ❌ ({g.gm_holmes_uses}/3)")
            if g.gm_holmes_uses >= 3:
                g.gm_holmes_despair = actor
                await _night_report(ctx, g, "⚰️ سومین حدسِ غلطِ هلمز — از غصه می‌میرد (قطعی).")
        g.night_done.add("holmes")
        store.save()
        await _gm_open_mafia(ctx, chat_id, g)
        return

    if data.startswith("gm_hmg_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        actor = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != actor]
        await _edit_pm(ctx, uid, mid, "🕵️ چه کسی دن‌کارلئونه است؟",
                       _kb_night_seats(targets, g, "gm_hmg_", selected=s, confirm_cb="gm_hmg_ok"))
        return

    # ── شات مافیا ──
    if data == "gm_st_ok":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_shot_target = s
        g.night_sel.pop(uid, None)
        _tn = g.seats[s][1]
        await _close_pm(ctx, uid, mid, f"✅ شلیک ثبت شد: {s}. {_tn}")
        await _night_report(ctx, g, f"🔫 شلیک مافیا → <b>{s}. {escape(_tn, quote=False)}</b>")
        g.night_done.add("shot")
        store.save()
        await _gm_check_open_citizens(ctx, chat_id, g)
        return

    if data.startswith("gm_st_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        me = _seat_of_uid(g, uid)
        if me in _mafia_seats(g, alive_only=True):
            # تیرانداز مافیاست → همه‌ی زنده‌ها، شاملِ خودِ تیم (شاید بخواهند خودی بزنند)
            targets = list(_alive_seats(g))
        else:
            # گیرنده‌ی هدیه‌ی رابین‌هود → همه به‌جز خودش (مافیا هم شامل)
            targets = [x for x in _alive_seats(g) if x != me]
        await _edit_pm(ctx, uid, mid, "🔫 هدف شلیک را انتخاب کن:",
                       _kb_night_seats(targets, g, "gm_st_", selected=s, confirm_cb="gm_st_ok"))
        return

    # ── بمب تووفیس ──
    if data == "gm_tf_ok":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.gm_tf_target = s
        g.gm_tf_map = {}
        g.night_sel.pop(uid, None)
        store.save()
        rows = [[InlineKeyboardButton(t, callback_data=f"gm_fz_{i}")]
                for i, t in enumerate(_GM_FUSE_TYPES)]
        await _edit_pm(ctx, uid, mid, "🟡 چاشنیِ رنگ «زرد» کدام باشد؟", InlineKeyboardMarkup(rows))
        return

    if data.startswith("gm_tf_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = [x for x in _alive_seats(g) if x not in _mafia_seats(g, alive_only=True)]
        await _edit_pm(ctx, uid, mid, "💣 جلوی چه کسی بمب می‌گذاری؟",
                       _kb_night_seats(targets, g, "gm_tf_", selected=s, confirm_cb="gm_tf_ok"))
        return

    if data.startswith("gm_fz_"):
        i = int(data.rsplit("_", 1)[1])
        g.gm_tf_map["زرد"] = _GM_FUSE_TYPES[i]
        store.save()
        remaining = [t for t in _GM_FUSE_TYPES if t not in g.gm_tf_map.values()]
        rows = [[InlineKeyboardButton(t, callback_data=f"gm_fr_{_GM_FUSE_TYPES.index(t)}")]
                for t in remaining]
        await _edit_pm(ctx, uid, mid, "🔴 چاشنیِ رنگ «قرمز» کدام باشد؟", InlineKeyboardMarkup(rows))
        return

    if data.startswith("gm_fr_"):
        i = int(data.rsplit("_", 1)[1])
        g.gm_tf_map["قرمز"] = _GM_FUSE_TYPES[i]
        last = [t for t in _GM_FUSE_TYPES if t not in g.gm_tf_map.values()][0]
        g.gm_tf_map["آبی"] = last
        g.gm_bomb_seat = g.gm_tf_target
        g.gm_bomb_fuses = dict(g.gm_tf_map)
        seat = g.gm_bomb_seat
        _tn = g.seats[seat][1] if seat in g.seats else "؟"
        await _close_pm(ctx, uid, mid,
                        f"💣 بمب جلوی {seat}. {_tn} — زرد:{g.gm_tf_map['زرد']} | قرمز:{g.gm_tf_map['قرمز']} | آبی:{last}")
        await _night_report(ctx, g,
                            f"💣 تووفیس → بمب جلوی <b>{seat}. {escape(_tn, quote=False)}</b> | "
                            f"زرد:{g.gm_tf_map['زرد']} · قرمز:{g.gm_tf_map['قرمز']} · آبی:{last}")
        g.night_done.add("bomb")
        store.save()
        await _gm_check_open_citizens(ctx, chat_id, g)
        return

    # ── موریارتی ──
    if data == "gm_mo_no":
        await _close_pm(ctx, uid, mid, "🎭 امشب حدس نزدی.")
        await _night_report(ctx, g, "🎭 موریارتی → حدس نزد")
        g.night_done.add("moriarty")
        store.save()
        await _gm_check_open_citizens(ctx, chat_id, g)
        return

    if data == "gm_mo_yes":
        actor = _seat_of_uid(g, uid)
        targets = [s for s in _alive_seats(g) if s not in _mafia_seats(g, alive_only=True)]
        await _edit_pm(ctx, uid, mid, "🎭 چه کسی مسترهلمز است؟",
                       _kb_night_seats(targets, g, "gm_mog_", confirm_cb="gm_mog_ok"))
        return

    if data == "gm_mog_ok":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_sel.pop(uid, None)
        holmes = _find_seat_by_role(g, _R_HOLMES)
        correct = (holmes is not None and s == holmes)
        _tn = g.seats[s][1]
        await _close_pm(ctx, uid, mid, f"🎭 حدس ثبت شد: {s}. {_tn}")
        if correct:
            g.gm_moriarty_correct = True
            await _night_report(ctx, g, f"🎭 موریارتی → حدس: {s}. {escape(_tn, quote=False)} ✅ (هلمز می‌میرد)")
        else:
            g.gm_moriarty_uses += 1
            await _night_report(ctx, g, f"🎭 موریارتی → حدس: {s}. {escape(_tn, quote=False)} ❌ ({g.gm_moriarty_uses}/3)")
            if g.gm_moriarty_uses >= 3:
                g.gm_moriarty_despair = True
                await _night_report(ctx, g, "⚰️ سومین حدسِ غلطِ موریارتی — می‌میرد (قطعی).")
        g.night_done.add("moriarty")
        store.save()
        await _gm_check_open_citizens(ctx, chat_id, g)
        return

    if data.startswith("gm_mog_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        targets = [x for x in _alive_seats(g) if x not in _mafia_seats(g, alive_only=True)]
        await _edit_pm(ctx, uid, mid, "🎭 چه کسی مسترهلمز است؟",
                       _kb_night_seats(targets, g, "gm_mog_", selected=s, confirm_cb="gm_mog_ok"))
        return

    # ── کستیل (دکتر) ──
    if data == "gm_doc_ok":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        me = _seat_of_uid(g, uid)
        if s == me:
            g.doctor_self_saves = (g.doctor_self_saves or 0) + 1
        g.night_doc_saved = [s]
        g.night_sel.pop(uid, None)
        _tn = g.seats[s][1]
        await _close_pm(ctx, uid, mid, f"💉 سیو ثبت شد: {s}. {_tn}")
        await _night_report(ctx, g, f"💉 کستیل → سیو: <b>{s}. {escape(_tn, quote=False)}</b>")
        g.night_done.add("doctor")
        store.save()
        return

    if data.startswith("gm_doc_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        me = _seat_of_uid(g, uid)
        await _edit_pm(ctx, uid, mid, "💉 چه کسی را سیو می‌دهی؟ (۱ نفر)",
                       _kb_night_seats(_doctor_targets(g, me), g, "gm_doc_", selected=s, confirm_cb="gm_doc_ok"))
        return

    # ── الیوت ──
    if data == "gm_el_no":
        await _close_pm(ctx, uid, mid, "🛡 امشب محافظت نکردی.")
        await _night_report(ctx, g, "🛡 الیوت → محافظت نکرد")
        g.night_done.add("eliot")
        store.save()
        return

    if data == "gm_el_yes":
        actor = _seat_of_uid(g, uid)
        targets = list(_alive_seats(g))
        await _edit_pm(ctx, uid, mid, "🛡 از چه کسی در برابر بمب محافظت می‌کنی؟",
                       _kb_night_seats(targets, g, "gm_el_", confirm_cb="gm_el_ok"))
        return

    if data == "gm_el_ok":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.gm_eliot_protect = s
        g.night_sel.pop(uid, None)
        _tn = g.seats[s][1]
        await _close_pm(ctx, uid, mid, f"🛡 محافظت ثبت شد: {s}. {_tn}")
        await _night_report(ctx, g, f"🛡 الیوت → محافظت از <b>{s}. {escape(_tn, quote=False)}</b>")
        g.night_done.add("eliot")
        store.save()
        return

    if data.startswith("gm_el_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        await _edit_pm(ctx, uid, mid, "🛡 از چه کسی در برابر بمب محافظت می‌کنی؟",
                       _kb_night_seats(list(_alive_seats(g)), g, "gm_el_", selected=s, confirm_cb="gm_el_ok"))
        return

    # ── جیمزهالیدی ──
    if data == "gm_jm_no":
        await _close_pm(ctx, uid, mid, "🎲 امشب بازی نکردی.")
        await _night_report(ctx, g, "🎲 جیمز → بازی نکرد")
        g.night_done.add("james")
        store.save()
        return

    if data == "gm_jm_yes":
        g.gm_james_nums = []
        store.save()
        await _edit_pm(ctx, uid, mid, "🎲 دو عدد بین ۱ تا ۶ انتخاب کن:", _gm_james_nums_kb(g))
        return

    if data == "gm_jn_ok":
        if len(g.gm_james_nums or []) != 2:
            await safe_q_answer(q, "دقیقاً ۲ عدد انتخاب کن.", show_alert=True)
            return
        actor = _seat_of_uid(g, uid)
        targets = [s for s in _alive_seats(g) if s != actor]
        await _edit_pm(ctx, uid, mid, f"🎲 اعداد: {g.gm_james_nums[0]} و {g.gm_james_nums[1]} — با چه کسی بازی می‌کنی؟",
                       _kb_night_seats(targets, g, "gm_jt_", confirm_cb="gm_jt_ok"))
        return

    if data.startswith("gm_jn_"):
        n = int(data.rsplit("_", 1)[1])
        nums = list(g.gm_james_nums or [])
        if n in nums:
            nums.remove(n)
        elif len(nums) < 2:
            nums.append(n)
        else:
            await safe_q_answer(q, "حداکثر ۲ عدد.", show_alert=True)
            return
        g.gm_james_nums = nums
        store.save()
        await _edit_pm(ctx, uid, mid, "🎲 دو عدد بین ۱ تا ۶ انتخاب کن:", _gm_james_nums_kb(g))
        return

    if data == "gm_jt_ok":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.gm_james_target = s
        g.gm_james_uses += 1
        g.night_sel.pop(uid, None)
        store.save()
        _tn = g.seats[s][1]
        await _close_pm(ctx, uid, mid, f"🎲 تاس برای {s}. {_tn} انداخته شد...")
        target_uid = g.seats[s][0]
        val = None
        try:
            dm = await ctx.bot.send_dice(target_uid)
            val = dm.dice.value
        except Exception:
            val = random.randint(1, 6)
        await asyncio.sleep(4)
        nums = list(g.gm_james_nums or [])
        don = _find_seat_by_role(g, _R_DONC)
        hit = (val in nums)
        await _night_report(ctx, g, f"🎲 جیمز ({nums[0]} و {nums[1]}) با {s}. {escape(_tn, quote=False)} — تاس: {val} → "
                            + ("گرفت ✅" if hit else "نگرفت ❌"))
        if not hit:
            await _safe_pm(ctx, uid, f"🎲 تاس {val} آمد — نگرفت!")
            g.night_done.add("james")
            store.save()
            return
        don_robbed = (g.gm_gift_accepted and g.gm_robbed_seat == don)
        if don is not None and s == don and not don_robbed:
            # دن دروغ می‌گوید: انتخاب نقش شهروندی
            g.gm_james_waiting_don = True
            g.gm_james_dice_val = val
            store.save()
            names = _gm_citizen_role_names(g)
            rows = [[InlineKeyboardButton(rn, callback_data=f"gm_lie_{i}")] for i, rn in enumerate(names)]
            await _safe_pm(ctx, g.seats[don][0],
                           "🎲 جیمز با تو بازی کرد و تاس گرفت! کدام نقش شهروندی را به دروغ بفرستم؟",
                           InlineKeyboardMarkup(rows))
            return
        real_role = (g.assigned_roles or {}).get(s, "؟")
        await _safe_pm(ctx, uid, f"🎲 تاس {val} آمد — گرفتی! نقشِ {s}. {_tn}: «{real_role}»")
        await _night_report(ctx, g, f"🎲 نقشِ واقعی «{escape(real_role, quote=False)}» برای جیمز فرستاده شد.")
        g.night_done.add("james")
        store.save()
        return

    if data.startswith("gm_jt_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        actor = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != actor]
        await _edit_pm(ctx, uid, mid, "🎲 با چه کسی بازی می‌کنی؟",
                       _kb_night_seats(targets, g, "gm_jt_", selected=s, confirm_cb="gm_jt_ok"))
        return

    if data.startswith("gm_lie_"):
        i = int(data.rsplit("_", 1)[1])
        names = _gm_citizen_role_names(g)
        if i >= len(names):
            return
        lie = names[i]
        await _close_pm(ctx, uid, mid, f"🤥 «{lie}» فرستاده شد.")
        jm = _find_seat_by_role(g, _R_JAMES)
        jm_actor = _gm_actor_for(g, jm)
        t = g.gm_james_target
        _tn = g.seats[t][1] if t in g.seats else "؟"
        _v = g.gm_james_dice_val or "?"
        if jm_actor:
            # ⚠️ فرمتِ پیام باید دقیقاً مثل نقشِ واقعی باشد تا دروغِ دن لو نرود
            await _safe_pm(ctx, g.seats[jm_actor][0], f"🎲 تاس {_v} آمد — گرفتی! نقشِ {t}. {_tn}: «{lie}»")
        await _night_report(ctx, g, f"🤥 دن به دروغ «{escape(lie, quote=False)}» را برای جیمز فرستاد.")
        g.gm_james_waiting_don = False
        g.night_done.add("james")
        store.save()
        return

    # ── ریک‌گرایمز ──
    if data == "gm_rk_no":
        await _close_pm(ctx, uid, mid, "🔫 امشب شات نزدی.")
        await _night_report(ctx, g, "🔫 ریک → شات نزد")
        g.night_done.add("rick")
        store.save()
        return

    if data == "gm_rk_yes":
        actor = _seat_of_uid(g, uid)
        targets = [s for s in _alive_seats(g) if s != actor]
        await _edit_pm(ctx, uid, mid, "🔫 به چه کسی شلیک می‌کنی؟",
                       _kb_night_seats(targets, g, "gm_rk_", confirm_cb="gm_rk_ok"))
        return

    if data == "gm_rk_ok":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.gm_rick_target = s
        g.night_sel.pop(uid, None)
        _tn = g.seats[s][1]
        await _close_pm(ctx, uid, mid, f"🔫 شلیک ثبت شد: {s}. {_tn}")
        await _night_report(ctx, g, f"🔫 ریک‌گرایمز → شلیک به <b>{s}. {escape(_tn, quote=False)}</b>")
        g.night_done.add("rick")
        store.save()
        return

    if data.startswith("gm_rk_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        actor = _seat_of_uid(g, uid)
        targets = [x for x in _alive_seats(g) if x != actor]
        await _edit_pm(ctx, uid, mid, "🔫 به چه کسی شلیک می‌کنی؟",
                       _kb_night_seats(targets, g, "gm_rk_", selected=s, confirm_cb="gm_rk_ok"))
        return


async def handle_don_sentence_pm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """جمله‌ی معارفه‌ی دن‌کارلئونه (در پیوی) → برای مسترهلمز و گاد."""
    msg = update.message
    if not msg or not msg.text:
        return
    uid = msg.from_user.id
    for cid, g in store.games.items():
        if not getattr(g, "gm_awaiting_don_sentence", False):
            continue
        don = _find_seat_by_role(g, _R_DONC)
        if don is None or g.seats[don][0] != uid:
            continue
        sentence = msg.text.strip()[:200]
        g.gm_don_sentence = sentence
        g.gm_awaiting_don_sentence = False
        store.save()
        holmes = _find_seat_by_role(g, _R_HOLMES)
        if holmes:
            try:
                await ctx.bot.send_message(g.seats[holmes][0], f"📩 جمله‌ای به دستت رسید:\n«{sentence}»")
            except Exception:
                pass
        await _night_report(ctx, g, f"📩 جملهٔ دن‌کارلئونه → هلمز: «{escape(sentence, quote=False)}»")
        try:
            await msg.reply_text("✅ جمله‌ات ثبت و برای مسترهلمز ارسال شد.")
        except Exception:
            pass
        return


async def handle_night_kick_callback(update, ctx):
    """👢 کیک شب — انتخابِ گاد در پیوی (همه‌ی سناریوها)."""
    q = update.callback_query
    data = q.data
    uid = q.from_user.id
    g = None
    chat_id = None
    for cid, game in store.games.items():
        if getattr(game, "night_active", False) and game.god_id == uid:
            g, chat_id = game, cid
            break
    if g is None:
        await safe_q_answer(q, "شبِ فعالی یافت نشد.", show_alert=True)
        return
    await safe_q_answer(q)
    mid = q.message.message_id if q.message else None

    if data == "nkick_no":
        await _close_pm(ctx, uid, mid, "👢 امشب کیکِ شب نداریم.")
        return

    if data == "nkick_yes":
        await _edit_pm(ctx, uid, mid, "👢 چه کسی کیکِ شب می‌شود؟",
                       _kb_night_seats(_alive_seats(g), g, "nkick_",
                                       selected=g.night_sel.get(uid), confirm_cb="nkick_ok"))
        return

    if data == "nkick_ok":
        s = g.night_sel.get(uid)
        if not s:
            await safe_q_answer(q, "اول یک نفر را انتخاب کن.", show_alert=True)
            return
        g.night_kick_seat = s
        g.night_sel.pop(uid, None)
        _tn = g.seats[s][1]
        await _close_pm(ctx, uid, mid,
                        f"👢 کیک شب ثبت شد: {s}. {_tn}\n"
                        f"(امشب اکتش را انجام می‌دهد و هنگامِ روز خط می‌خورد)")
        await _night_report(ctx, g, f"👢 کیک شب: <b>{s}. {escape(_tn, quote=False)}</b>")
        store.save()
        return

    if data.startswith("nkick_"):
        s = int(data.rsplit("_", 1)[1])
        g.night_sel[uid] = s
        store.save()
        await _edit_pm(ctx, uid, mid, "👢 چه کسی کیکِ شب می‌شود؟",
                       _kb_night_seats(_alive_seats(g), g, "nkick_", selected=s, confirm_cb="nkick_ok"))
        return


# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
#  CALL-BACK ROUTER – نسخهٔ کامل با فاصله‌گذاری درست
# ─────────────────────────────────────────────────────────────
async def callback_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # 📋 لیست منتخب (مستقل از بازی، در پی‌وی) — قبل از هر چیز
    _q = update.callback_query
    if _q and _q.data and (_q.data.startswith("sel_") or _q.data.startswith("selday_")):
        await handle_selected_callback(update, ctx)
        return

    # 👢 کیک شب (گاد در پیوی) — قبل از گارد پی‌وی
    if _q and _q.data and _q.data.startswith("nkick_"):
        await handle_night_kick_callback(update, ctx)
        return

    # 🌙 اکت‌های شبِ خودکار (در پیوی بازیکنان) — قبل از گارد پی‌وی
    if _q and _q.data and _q.data.startswith(("night_", "bzp_", "nem_", "tk_", "kp_", "gm_")):
        _dt = _q.data
        if _dt.startswith("night_"):
            await handle_night_callback(update, ctx)
        elif _dt.startswith("bzp_"):
            await handle_baazpors_callback(update, ctx)
        elif _dt.startswith("nem_"):
            await handle_nemayande_callback(update, ctx)
        elif _dt.startswith("tk_"):
            await handle_takavar_callback(update, ctx)
        elif _dt.startswith("kp_"):
            await handle_kapu_callback(update, ctx)
        else:
            await handle_gamer_callback(update, ctx)
        # پس از هر اکت: اگر همه‌ی اکت‌ها تمام شد، به گاد اطلاع بده
        try:
            _gg, _ = _find_active_night_game(_q.from_user.id, _q)
            if _gg is not None:
                await _maybe_notify_god_done(ctx, _gg)
        except Exception:
            pass
        return

    # 🔹 جلوگیری از اجرای کال‌بک‌ها در پی‌وی مگر برای راوی در حالت خریداری
    if update.effective_chat.type == "private":
        q = update.callback_query
        data = q.data if q else None
        uid = q.from_user.id

        # 🟢 پیدا کردن بازی مرتبط
        g = None
        chat = None

        # برای همه‌ی purchase_ callbackها (انتخاب/تأیید/بازگشت): بر اساس شناسه پیام PM
        # (دکمه‌ها همه روی همان پیامِ پی‌وی هستند؛ مستقل از فازِ بازی)
        if data and data.startswith("purchase_") and q and q.message:
            target_msg_id = q.message.message_id
            for chat_id, game in store.games.items():
                if getattr(game, "purchase_pm_msg_id", None) == target_msg_id:
                    g = game
                    chat = chat_id
                    break

        # اگر بالا پیدا نشد → جستجو بر اساس god_id (فازهای رأی‌گیریِ روز هم قبول)
        if not g:
            for chat_id, game in store.games.items():
                if game.god_id == uid and game.phase in (
                        "playing", "awaiting_winner", "voting_selection", "defense_selection"):
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
        if getattr(g, "awaiting_shuffle_decision", False):
            await ctx.bot.send_message(chat, "⛔ بازی در حال شروع است؛ لغو ثبت‌نام فعلاً غیرفعال است.")
            return
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
        # گاد هم می‌تواند نام خود را تغییر دهد (با صندلی نمادین ۰)
        if uid == g.god_id:
            prompt = await ctx.bot.send_message(
                chat,
                "✏️ این پیام را ریپلای کنید و نام جدید راوی (گاد) را به فارسی وارد کنید:"
            )
            _start_name_wait(ctx, chat, g, uid, 0, prompt)
            return

        if uid not in [u for u, _ in g.seats.values()]:
            await ctx.bot.send_message(chat, "❗ شما هنوز ثبت‌نام نکرده‌اید.")
            return

        seat_no = [s for s in g.seats if g.seats[s][0] == uid][0]

        prompt = await ctx.bot.send_message(
            chat,
            f"✏️ این پیام را ریپلای کنید و نام جدید خود را برای صندلی {seat_no} به فارسی وارد کنید:"
        )
        _start_name_wait(ctx, chat, g, uid, seat_no, prompt)
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
        # حذف کیبورد قدیمی از پی‌وی گاد تا کلیک روی لیست قدیمی ممکن نباشد
        if getattr(g, "purchase_pm_msg_id", None):
            try:
                await ctx.bot.delete_message(chat_id=g.god_id, message_id=g.purchase_pm_msg_id)
            except Exception:
                pass
            g.purchase_pm_msg_id = None

        alive = [s for s in sorted(g.seats) if s not in (g.striked or set())]
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

        alive = [x for x in sorted(g.seats) if x not in (g.striked or set())]
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
        await _send_vote_timing_report(ctx, g, chat)   # 🕒 گزارش تست زمان‌بندی داخل گروه
        await _offer_auto_defense(ctx, chat, g)        # 🧍 پیشنهادِ دفاعیه‌ی خودکار (قبل از پاک شدن آرا)
        g.votes_cast = {}
        g.vote_logs = {}
        g.current_vote_target = None
        g.vote_window = None
        g.vote_prev_window = None
        g.vote_has_ended_initial = True
        g.vote_order = []
        store.save()
        return

    if data == "vote_done_final" and uid == g.god_id:
        await ctx.bot.send_message(chat, "✅ رأی‌گیری نهایی تمام شد.")
        await _send_vote_timing_report(ctx, g, chat)   # 🕒 گزارش تست زمان‌بندی داخل گروه
        g.votes_cast = {}
        g.vote_logs = {}
        g.current_vote_target = None
        g.vote_window = None
        g.vote_prev_window = None
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


    if data == "add_scenario" and (uid == g.god_id or uid in getattr(g, "admins", set())):
        g.adding_scenario_step = "name"
        g.adding_scenario_data = {}
        g.adding_scenario_last = datetime.now()
        store.save()
        await ctx.bot.send_message(chat, "📝 نام سناریوی جدید را بفرستید (۵ دقیقه فرصت دارید).")
        return

    # ─── تغییر موضوع/مناسبت رویداد (گاد یا ادمین‌ها) ─────────────
    if data == "change_event":
        if not await _is_god_or_admin(ctx, chat, uid, g):
            await ctx.bot.send_message(chat, "⛔ فقط راوی یا ادمین‌های گروه می‌توانند رویداد را تغییر دهند.")
            return
        g.awaiting_event_title = True
        store.save()
        await ctx.bot.send_message(
            chat,
            "📝 موضوع رویداد را بنویسید (مثلاً: تولد).\n"
            "می‌توانید همین پیام را ریپلای کنید یا بدون ریپلای بنویسید.\n"
            "برای حذف موضوع، بنویسید: حذف"
        )
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
        await asyncio.sleep(15)

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


    # ─── دفاعیه‌ی خودکار: تأیید/ردِ گاد ─────────────────────────────
    if data == "autofinal_yes" and uid == g.god_id:
        seats_list = [s for s in (getattr(g, "pending_defense", []) or [])
                      if s in g.seats and s not in (g.striked or set())]
        g.pending_defense = []
        try:
            await ctx.bot.edit_message_reply_markup(chat, q.message.message_id, reply_markup=None)
        except Exception:
            pass
        if not seats_list:
            await ctx.bot.send_message(chat, "ℹ️ لیست دفاعیه خالی است؛ از دکمه‌ی «رأی نهایی» استفاده کن.")
            store.save()
            return
        g.votes_cast = {}
        g.vote_logs = {}
        g.current_vote_target = None
        g.vote_window = None
        g.vote_prev_window = None
        g.voted_targets = set()
        g.defense_seats = list(seats_list)
        g.vote_type = "defense_selected"
        store.save()
        await ctx.bot.send_message(chat, f"🛡 صندلی‌های دفاع: {'، '.join(map(str, g.defense_seats))}")
        await start_vote(ctx, chat, g, "final")
        await publish_seating(ctx, chat, g, mode=CTRL)
        return

    if data == "autofinal_no" and uid == g.god_id:
        g.pending_defense = []
        store.save()
        try:
            await ctx.bot.edit_message_reply_markup(chat, q.message.message_id, reply_markup=None)
        except Exception:
            pass
        await ctx.bot.send_message(chat, "↩️ باشه؛ دفاعیه را خودت با دکمه‌ی «رأی نهایی» انتخاب کن.")
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

        mafia_roles = {_nz(x) for x in load_mafia_roles()}
        dead_seats = [s for s in g.striked]
        mafia_count = 0
        citizen_count = 0

        for s in dead_seats:
            role = g.assigned_roles.get(s)
            if role and _nz(role) in mafia_roles:
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

    # کش ساید هر صندلی در لحظه تخصیص نقش (برای ثبت آمار قابل اعتماد در پایان بازی)
    _mr = {_nz(x) for x in load_mafia_roles()}                    # نرمالایز (عربی/فارسی)
    _ir_all = load_indep_roles()
    _indep = {_nz(x) for x in (_ir_all.get(g.scenario.name, []) if g.scenario else [])}
    g.seat_sides = {}
    for _seat, (_uid, _name) in g.seats.items():
        _role = _nz(g.assigned_roles.get(_seat, "—"))
        if _role in _mr:
            g.seat_sides[_seat] = "مافیا"
        elif _role in _indep:
            g.seat_sides[_seat] = "مستقل"
        else:
            g.seat_sides[_seat] = "شهر"

    # 6) ارسال نقش‌ها به بازیکن‌ها (اختیاری) و ساخت لاگ برای گاد
    log, unreachable = [], []
    stickers = load_stickers()
    if notify_players:
        try:
            group_title = (await ctx.bot.get_chat(chat_id)).title or str(chat_id)
        except Exception:
            group_title = str(chat_id)
        scenario_name = getattr(g.scenario, "name", "—")
        for seat in sorted(g.seats):
            uid, name = g.seats[seat]
            role = g.assigned_roles[seat]
            if role in stickers:
                try:
                    await ctx.bot.send_sticker(uid, stickers[role])
                except:
                    pass
            role_msg = (
                f"گروه: {group_title}\n"
                f"سناریو: {scenario_name}\n"
                f"نقش: {role}"
            )
            try:
                await ctx.bot.send_message(uid, role_msg)
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


async def _try_set_event_title(ctx, chat_id: int, uid: int, g: GameState, text: str) -> bool:
    """اگر منتظر موضوع رویداد هستیم و فرستنده گاد است، موضوع را تنظیم می‌کند."""
    if not getattr(g, "awaiting_event_title", False):
        return False
    if uid != g.god_id:
        return False  # فقط گاد می‌تواند موضوع را تغییر دهد

    g.awaiting_event_title = False
    mode = REG if g.phase == "idle" else CTRL

    if text.strip() in ("حذف", "حذف موضوع", "پاک"):
        g.event_title = None
        store.save()
        await publish_seating(ctx, chat_id, g, mode=mode)
        await ctx.bot.send_message(chat_id, "✅ موضوع رویداد حذف شد.")
        return True

    title = text.strip()[:40]
    g.event_title = title
    store.save()
    await publish_seating(ctx, chat_id, g, mode=mode)
    await ctx.bot.send_message(
        chat_id, f"✅ موضوع رویداد روی «{escape(title, quote=False)}» تنظیم شد."
    )
    return True


async def name_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    text = msg.text.strip()
    uid = msg.from_user.id
    chat_id = msg.chat.id
    g = gs(chat_id)

    # 🗳 ثبت رأی حتی اگر ریپلای فرستاده شده باشد (قبلاً این رأی‌ها گم می‌شدند)
    if _try_capture_vote(g, msg, uid, text):
        return

    # تغییر موضوع رویداد (گاد/ادمین) — با یا بدون ریپلای
    if await _try_set_event_title(ctx, chat_id, uid, g, text):
        return

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

    if g.phase == "idle" and (not getattr(g, "awaiting_shuffle_decision", False)) and text.strip() == "کنسل":
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
        if isinstance(getattr(g, "waiting_name_token", None), dict):
            g.waiting_name_token.pop(uid, None)

        # ذخیره نام جدید در دفترچه
        g.user_names[uid] = text

        # ── تغییر نام گاد (صندلی نمادین ۰) ──
        if target_seat == 0:
            g.god_name = text
            store.save()
            mode = CTRL if g.phase != "idle" else REG
            await publish_seating(ctx, chat_id, g, mode=mode)
            await ctx.bot.send_message(chat_id, f"✅ نام راوی به «{text}» تغییر کرد.")
            try:
                save_usernames_to_gist(g.user_names)
            except Exception:
                pass
            return

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

    # 🔗 پاک‌سازی اتاق مافیای بازی قبلی (قبل از ساخت GameState جدید)
    old = store.games.get(chat_id)
    if ctx is not None and old is not None and getattr(old, "mafia_room_id", None):
        try:
            await _room_cleanup(ctx, old)
        except Exception:
            pass

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

    if not await _is_god_or_admin(ctx, chat, uid, g):
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

    # ✅ فقط ادمین‌های گروه یا گادِ فعلی اجازه دارند (هم خود، هم ریپلای)
    admins = await ctx.bot.get_chat_administrators(chat)
    admin_ids = {a.user.id for a in admins}
    if update.effective_user.id not in admin_ids and update.effective_user.id != g.god_id:
        await update.message.reply_text("❌ فقط ادمین‌های گروه یا گاد فعلی می‌تونن گاد رو تعیین کنن.")
        return

    # هدف: با ریپلای → آن شخص؛ بدون ریپلای → خودِ فرستنده
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
    else:
        target = update.effective_user

    if g.god_id == target.id:
        await update.message.reply_text("ℹ️ همین حالا هم گاد هست.")
        return

    # بازیکن زنده نمی‌تواند گاد شود — فقط وقتی نقش‌ها پخش شده‌اند
    # (قبل از پخش نقش‌ها هر کسی، حتی داخل لیست، می‌تواند گاد شود)
    if getattr(g, "assigned_roles", None):
        alive_uids = {g.seats[s][0] for s in g.seats if s not in (g.striked or set())}
        if target.id in alive_uids:
            await update.message.reply_text(
                "⛔ این بازیکن هنوز در بازی زنده است و نمی‌تواند گاد شود."
            )
            return

    # نام ترجیحی: از gist اگر موجود، وگرنه نام تلگرام
    new_name = g.user_names.get(target.id, target.full_name)

    old_god_id = g.god_id
    g.god_id = target.id
    g.god_name = new_name
    store.save()

    # 🔗 اتاق مافیا: گادِ قبلی حذف + چرخش لینک، لینکِ جدید به گادِ جدید
    if getattr(g, "mafia_room_id", None):
        if old_god_id and old_god_id in g.mafia_room_members and old_god_id != target.id:
            await _room_kick(ctx, g, old_god_id)
            g.mafia_room_members.discard(old_god_id)
            await _room_rotate_link(ctx, g)
        await _room_send_link(ctx, g, target.id)
        # ⏰ اگر گادِ جدید تا ۵ دقیقه جوین نشد، لینکِ جدید بساز و بفرست
        asyncio.create_task(_room_join_check_later(ctx, g))

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
            # 🌙 گزارش اکت‌های شب‌های گذشته برای گاد جدید
            night_log = getattr(g, "night_log", None)
            if night_log:
                report = "📜 <b>گزارش اکت‌های شب‌های گذشته</b>\n" + "\n".join(night_log)
                chunk = ""
                for line in report.split("\n"):
                    if len(chunk) + len(line) + 1 > 3500:
                        try:
                            await ctx.bot.send_message(target.id, chunk, parse_mode="HTML")
                        except Exception:
                            pass
                        chunk = ""
                    chunk += (line + "\n")
                if chunk.strip():
                    try:
                        await ctx.bot.send_message(target.id, chunk, parse_mode="HTML")
                    except Exception:
                        pass
        except telegram.error.Forbidden:
            await update.message.reply_text("⚠️ نتونستم نقش‌ها رو به پیوی گاد جدید بفرستم.")


async def start_welcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """پیام خوش‌آمد برای کسانی که بات را استارت می‌کنند (پیوی)."""
    try:
        await update.message.reply_text(
            "سلام! 🎭 به بات مافیا خوش آمدید.\n\n"
            "این بات برای اجرای بازی مافیا در گروه‌ها ساخته شده و اکت‌های شبانهٔ نقش شما "
            "در همین پیوی انجام می‌شود.\n\n"
            "• «آمار من» را بفرستید تا آمار بازی‌هایتان را ببینید.\n"
            "• «آمار کل» را بفرستید تا جدول برترین‌ها را ببینید.\n\n"
            "موفق باشید! 🌙"
        )
    except Exception:
        pass


async def handle_stats_pm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """«آمار من» و «آمار کل» در پیوی بات."""
    msg = update.message
    if not msg or not msg.text:
        return
    text = msg.text.strip()
    uid = msg.from_user.id
    if text == "آمار من":
        stats = load_player_stats()
        p = stats.get(str(uid))
        if not p or (p.get("games", 0) == 0 and p.get("god_games", 0) == 0):
            await msg.reply_text("📭 هنوز آماری برای شما ثبت نشده است.")
        else:
            await msg.reply_text(format_player_stats(p), parse_mode="HTML")
    elif text == "آمار کل":
        board = build_alltime_leaderboard_text(load_player_stats())
        if not board:
            await msg.reply_text("📭 هنوز آماری ثبت نشده است.")
        else:
            await msg.reply_text(board, parse_mode="HTML")


# ═════════════════════════════════════════════════════════════
#  اتاق چت مافیا (گروه‌های جداگانه)
# ═════════════════════════════════════════════════════════════
ROOMS_FILENAME = "mafia_rooms.json"


def load_mafia_rooms() -> list:
    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}
        r = httpx.get(url, headers=headers)
        if r.status_code == 200:
            content = r.json()["files"].get(ROOMS_FILENAME, {}).get("content", "[]")
            return json.loads(content) or []
    except Exception as e:
        print("❌ load_mafia_rooms error:", e)
    return []


def save_mafia_rooms(rooms: list):
    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}
        data = {"files": {ROOMS_FILENAME: {"content": json.dumps(rooms, ensure_ascii=False, indent=2)}}}
        httpx.patch(url, headers=headers, json=data)
    except Exception as e:
        print("❌ save_mafia_rooms error:", e)


def _mafia_room_seats(g):
    """صندلی‌های مافیایی که باید لینک اتاق بگیرند (در تکاور گروگانگیر مستثنی است)."""
    sides = getattr(g, "seat_sides", {}) or {}
    seats = [s for s in g.seats if s in (g.negotiated_seats or set()) or sides.get(s) == "مافیا"]
    if _is_takavar_scenario(g):
        seats = [s for s in seats if _seat_role_norm(g, s) != _R_HOSTAGE]
    return seats


async def _room_send_link(ctx, g, uid):
    if not g.mafia_room_id or not g.mafia_room_link or uid in g.mafia_room_members:
        return
    # 🔓 اگر از بازی‌های قبلی در این اتاق بن مانده، اول رفعِ بن کن
    # (کاربرِ بن‌شده هنگام کلیک روی لینک، «لینک منقضی شده» می‌بیند)
    try:
        await ctx.bot.unban_chat_member(g.mafia_room_id, uid, only_if_banned=True)
    except Exception:
        pass
    g.mafia_room_members.add(uid)
    store.save()
    try:
        await ctx.bot.send_message(uid, f"🔗 لینک گروه مافیا:\n{g.mafia_room_link}")
    except Exception:
        pass


async def _room_kick(ctx, g, uid, allow_return=True):
    """حذف از اتاق بدونِ بنِ دائمی (بن + رفعِ بنِ فوری = فقط ریموو).
    جلوگیری از برگشت با عوض‌کردنِ لینک انجام می‌شود، نه بن."""
    if not g.mafia_room_id:
        return
    try:
        await ctx.bot.ban_chat_member(g.mafia_room_id, uid)
        await ctx.bot.unban_chat_member(g.mafia_room_id, uid, only_if_banned=True)
    except Exception:
        pass


async def _room_rotate_link(ctx, g):
    """باطل‌کردنِ لینکِ فعلی و ساختِ لینکِ جدید (تا حذف‌شده‌ها نتوانند برگردند).
    لینکِ جدید برای اعضایی که هنوز واردِ اتاق نشده‌اند دوباره ارسال می‌شود."""
    if not g.mafia_room_id:
        return
    if g.mafia_room_link:
        try:
            await ctx.bot.revoke_chat_invite_link(g.mafia_room_id, g.mafia_room_link)
        except Exception:
            pass
    try:
        link = await ctx.bot.create_chat_invite_link(g.mafia_room_id, name=f"game-{g.god_id}")
        g.mafia_room_link = link.invite_link
        store.save()
    except Exception:
        return
    # 🔁 لینکِ قبلیِ کسانی که هنوز جوین نشده‌اند باطل شد — لینکِ جدید را برایشان بفرست
    for uid in list(g.mafia_room_members):
        try:
            cm = await ctx.bot.get_chat_member(g.mafia_room_id, uid)
            inside = cm.status in ("member", "administrator", "creator")
        except Exception:
            inside = False
        if not inside:
            try:
                await ctx.bot.unban_chat_member(g.mafia_room_id, uid, only_if_banned=True)
            except Exception:
                pass
            try:
                await ctx.bot.send_message(uid, f"🔗 لینکِ جدید گروه مافیا (لینک قبلی باطل شد):\n{g.mafia_room_link}")
            except Exception:
                pass


async def _room_set_locked(ctx, g, locked: bool):
    if not g.mafia_room_id:
        return
    try:
        if locked:
            perms = ChatPermissions(can_send_messages=False)
        else:
            perms = ChatPermissions(
                can_send_messages=True, can_send_polls=True,
                can_send_other_messages=True, can_add_web_page_previews=True)
        await ctx.bot.set_chat_permissions(g.mafia_room_id, perms)
    except Exception:
        pass


async def _room_allocate(ctx, g):
    """یک اتاقِ آزاد به این بازی اختصاص می‌دهد و لینک می‌سازد."""
    if g.mafia_room_id:
        return True
    rooms = load_mafia_rooms()
    used = {getattr(game, "mafia_room_id", None) for game in store.games.values()
            if getattr(game, "mafia_room_id", None)}
    room = next((r for r in rooms if r not in used), None)
    if room is None:
        return False
    try:
        link = await ctx.bot.create_chat_invite_link(room, name=f"game-{g.god_id}")
        g.mafia_room_id = room
        g.mafia_room_link = link.invite_link
        g.mafia_room_members = set()
        g.mafia_room_pending_link = []
        g.mafia_room_kicked = set()
        store.save()
        # 🔁 چرخش اتاق‌ها: اتاقِ استفاده‌شده به تهِ فهرست می‌رود تا بازیِ بعدی اتاقِ دیگری بگیرد
        try:
            rooms.remove(room)
            rooms.append(room)
            save_mafia_rooms(rooms)
        except Exception:
            pass
        await _room_set_locked(ctx, g, False)   # شبِ معارفه باز باشد
        return True
    except Exception as e:
        print("❌ _room_allocate error:", e)
        return False


async def _room_membership(ctx, g):
    """وضعیت عضویتِ اعضای موردانتظار اتاق: (داخل‌ها، بیرون‌مانده‌ها) — بدون نیاز به پیام از کسی."""
    inside, outside = [], []
    for uid in list(g.mafia_room_members):
        try:
            cm = await ctx.bot.get_chat_member(g.mafia_room_id, uid)
            ok = cm.status in ("member", "administrator", "creator")
        except Exception:
            ok = False
        (inside if ok else outside).append(uid)
    return inside, outside


async def _room_join_check_later(ctx, g, delay=300):
    """⏰ بعد از ۵ دقیقه: هر عضو موردانتظار (مافیا/گاد) که هنوز جوین نشده →
    لینکِ جدید ساخته و برایش ارسال می‌شود + گزارش به گاد."""
    await asyncio.sleep(delay)
    if not getattr(g, "mafia_room_id", None):
        return   # بازی تمام/ریست شده
    _in, _out = await _room_membership(ctx, g)
    if not _out:
        return
    await _room_rotate_link(ctx, g)   # لینک نو + رفعِ بن + ارسال به جوین‌نشده‌ها
    names = []
    for uid in _out:
        seat = _seat_of_uid(g, uid)
        names.append(f"{seat}. {g.seats[seat][1]}" if seat
                     else ("گاد" if uid == g.god_id else str(uid)))
    await _night_report(ctx, g, "⏰ بعد از ۵ دقیقه هنوز واردِ اتاق مافیا نشده بودند: "
                        + "، ".join(names) + "\n(لینکِ جدید ساخته و برایشان ارسال شد)")


async def _room_sync_on_night(ctx, g):
    """در /شب: باز کردن چت + حذف مافیای مرده + چرخشِ لینک + ارسال لینکِ معوق + چکِ جوین‌نشده‌ها."""
    if not g.mafia_room_id:
        return
    await _room_set_locked(ctx, g, False)
    removed_any = False
    for uid in list(g.mafia_room_members):
        if uid == g.god_id:
            continue   # ⚠️ گاد صندلی ندارد ولی هیچ‌وقت نباید حذف شود!
        seat = _seat_of_uid(g, uid)
        if seat is None or seat in (g.striked or set()):
            await _room_kick(ctx, g, uid)
            g.mafia_room_members.discard(uid)
            removed_any = True
    # 🔗 اگر کسی حذف شد، لینک عوض می‌شود تا با لینکِ قدیمی برنگردد
    if removed_any:
        await _room_rotate_link(ctx, g)
    for uid in list(g.mafia_room_pending_link or []):
        await _room_send_link(ctx, g, uid)
    g.mafia_room_pending_link = []
    store.save()
    # 👀 هر کس هنوز جوین نشده: رفعِ بن + ارسالِ دوباره‌ی لینک + گزارش به گاد
    _in, _out = await _room_membership(ctx, g)
    if _out:
        names = []
        for uid in _out:
            seat = _seat_of_uid(g, uid)
            names.append(f"{seat}. {g.seats[seat][1]}" if seat else str(uid))
            try:
                await ctx.bot.unban_chat_member(g.mafia_room_id, uid, only_if_banned=True)
            except Exception:
                pass
            try:
                await ctx.bot.send_message(uid, f"🔗 هنوز واردِ گروه مافیا نشده‌ای — لینک:\n{g.mafia_room_link}")
            except Exception:
                pass
        await _night_report(ctx, g, "⚠️ هنوز واردِ اتاق مافیا نشده‌اند: " + "، ".join(names)
                            + "\n(لینک دوباره برایشان ارسال شد)")


async def _room_cleanup(ctx, g):
    """پایان بازی: حذف همه + رفعِ بنِ همه + باطل‌کردن لینک + آزادسازی اتاق."""
    if not g.mafia_room_id:
        return
    for uid in list(g.mafia_room_members):
        await _room_kick(ctx, g, uid, allow_return=True)
    # 🔓 رفعِ بنِ کسانی که وسط بازی حذف شده بودند (تا در بازی‌های بعدیِ این اتاق «لینک منقضی» نگیرند)
    for uid in list(getattr(g, "mafia_room_kicked", set()) or set()):
        try:
            await ctx.bot.unban_chat_member(g.mafia_room_id, uid, only_if_banned=True)
        except Exception:
            pass
    if g.mafia_room_link:
        try:
            await ctx.bot.revoke_chat_invite_link(g.mafia_room_id, g.mafia_room_link)
        except Exception:
            pass
    g.mafia_room_id = None
    g.mafia_room_link = None
    g.mafia_room_members = set()
    g.mafia_room_pending_link = []
    g.mafia_room_kicked = set()
    store.save()


async def addroom_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ثبتِ گروهِ فعلی به‌عنوان اتاق چت مافیا (ادمین)."""
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("این دستور فقط در گروه‌ها کار می‌کند.")
        return
    try:
        admins = await ctx.bot.get_chat_administrators(chat.id)
        if update.effective_user.id not in {a.user.id for a in admins}:
            await update.message.reply_text("❌ فقط ادمین‌ها می‌توانند این گروه را ثبت کنند.")
            return
    except Exception:
        pass
    rooms = load_mafia_rooms()
    if chat.id in rooms:
        await update.message.reply_text("ℹ️ این گروه از قبل به‌عنوان اتاق مافیا ثبت شده است.")
        return
    rooms.append(chat.id)
    save_mafia_rooms(rooms)
    await update.message.reply_text(
        "✅ این گروه به‌عنوان «اتاق چت مافیا» ثبت شد.\n"
        "این گروه را «/active» نکنید. بات باید ادمین با دسترسیِ «دعوت» و «حذف اعضا» باشد.")


async def delroom_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    rooms = load_mafia_rooms()
    if chat.id in rooms:
        rooms.remove(chat.id)
        save_mafia_rooms(rooms)
        await update.message.reply_text("🗑 این گروه از فهرست اتاق‌های مافیا حذف شد.")
    else:
        await update.message.reply_text("ℹ️ این گروه در فهرست اتاق‌ها نبود.")


async def do_maarefe(ctx, chat_id, g):
    """شب معارفه: اعلام در گروه + ارسال اسم یاران به مافیاها (بدون اکت برای شهروندان)."""
    if not getattr(g, "assigned_roles", None):
        await ctx.bot.send_message(chat_id, "⛔ ابتدا باید نقش‌ها پخش شوند.")
        return
    g.maarefe_active = True   # شبِ معارفه (بدون اکت) — با /روز بسته می‌شود
    store.save()
    await ctx.bot.send_message(chat_id, "🎭 <b>شب معارفه</b>", parse_mode="HTML")
    sides = getattr(g, "seat_sides", {}) or {}
    mafia_seats = [s for s in sorted(g.seats) if sides.get(s) == "مافیا"]
    for s in mafia_seats:
        uid = g.seats[s][0]
        mates = [f"{m}. {g.seats[m][1]}" for m in mafia_seats if m != s]
        text = "🎭 شب معارفه\n\n😈 یاران مافیای شما:\n" + ("\n".join(mates) if mates else "—")
        try:
            await ctx.bot.send_message(uid, text)
        except Exception:
            pass

    # 🔗 اتاق چت مافیا: تخصیص + ارسال لینک به گاد و مافیاها (در تکاور گروگانگیر مستثنی)
    if await _room_allocate(ctx, g):
        await _room_send_link(ctx, g, g.god_id)
        for s in _mafia_room_seats(g):
            await _room_send_link(ctx, g, g.seats[s][0])
        await ctx.bot.send_message(chat_id, "🔗 لینک گروه مافیا به پیوی گاد و مافیاها ارسال شد.")
        # ⏰ بعد از ۵ دقیقه اگر کسی جوین نشده بود، لینکِ جدید بساز و بفرست
        asyncio.create_task(_room_join_check_later(ctx, g))
    else:
        if load_mafia_rooms():
            await ctx.bot.send_message(chat_id, "⚠️ اتاق مافیای آزادی موجود نیست (همه مشغول‌اند).")

    # سناریو گیمر: دن‌کارلئونه باید یک جمله بنویسد (به مسترهلمز می‌رسد)
    if _is_gamer_scenario(g):
        don = _find_seat_by_role(g, _R_DONC)
        if don and not g.gm_don_sentence:
            g.gm_awaiting_don_sentence = True
            store.save()
            try:
                await ctx.bot.send_message(
                    g.seats[don][0],
                    "✍️ شب معارفه — یک جمله بنویس و همین‌جا بفرست؛ "
                    "این جمله به مسترهلمز می‌رسد (اجباری).")
            except Exception:
                pass

    # سناریو کاپو: اکت اجباری وارث در شب معارفه
    if _is_kapu_scenario(g) and g.heir_target is None:
        heir = _find_seat_by_role(g, _R_HEIR)
        if heir:
            huid = g.seats[heir][0]
            targets = [s for s in _alive_seats(g) if s != heir]
            try:
                m = await ctx.bot.send_message(
                    huid, "⚱️ شب معارفه — چه کسی را انتخاب می‌کنی؟ (اجباری، یک‌بار)",
                    reply_markup=_kb_night_seats(targets, g, "kp_heir_", confirm_cb="kp_heir_confirm"))
                g.night_pm_msgs[huid] = m.message_id   # maarefe_active قبلاً ست شده تا کال‌بک پیدا شود
                store.save()
            except Exception:
                pass


def _diag_scenario_report(g) -> str:
    sc = getattr(g, "scenario", None)
    sc_name = sc.name if sc else "—"
    detected = []
    if _is_neg_scenario(g): detected.append("مذاکره")
    if _is_baazpors_scenario(g): detected.append("بازپرس")
    if _is_nemayande_scenario(g): detected.append("نماینده")
    if _is_takavar_scenario(g): detected.append("تکاور")
    if _is_kapu_scenario(g): detected.append("کاپو")
    if _is_gamer_scenario(g): detected.append("گیمر")
    lines = [
        "🔍 <b>تشخیص سناریو و نقش‌ها</b>",
        f"نام سناریو: «{escape(sc_name, quote=False)}»",
        "تشخیص خودکار: " + (", ".join(detected) if detected else "❌ هیچ‌کدام — اکت خودکار فعال نمی‌شود!"),
        "",
        "صندلی → نقش → تشخیص:",
    ]
    mafia = _mafia_seats(g)
    for s in sorted(g.seats):
        role = (g.assigned_roles or {}).get(s, "—")
        tag = "😈 مافیا" if s in mafia else "🏙 شهر"
        lines.append(f"{s}. {escape(role, quote=False)} → {tag}")
    if detected and not mafia:
        lines.append("\n⚠️ هیچ نقشِ مافیایی شناسایی نشد! احتمالاً املای نقش‌ها با سناریو نمی‌خواند.")
    return "\n".join(lines)


async def handle_direct_name_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    chat_id = msg.chat.id
    uid = msg.from_user.id
    g = gs(chat_id)
    text = msg.text.strip()

    # 🗳 ثبت رأی — فقط «دولایک با هر رنگ» یا «..»؛ ملاک زمان = ساعت تلگرام
    # (قبل از هر شرط دیگری، تا هیچ هندلری پیامِ رأی را ندزدد)
    if _try_capture_vote(g, msg, uid, text):
        return

    # 🔍 تشخیص سناریو/نقش‌ها (به پیوی گاد) — برای عیب‌یابی
    if text in ("/چک", "/تشخیص"):
        if await _is_god_or_admin(ctx, chat_id, uid, g):
            rep = _diag_scenario_report(g)
            try:
                await ctx.bot.send_message(g.god_id, rep, parse_mode="HTML")
                await msg.reply_text("🔍 گزارش تشخیص به پیوی گاد ارسال شد.")
            except Exception:
                await msg.reply_text("⚠️ نتوانستم به پیوی گاد بفرستم؛ گاد باید بات را استارت کند.")
        return

    # 🎭 شب معارفه (فقط گاد/ادمین)
    if text == "/معارفه":
        if await _is_god_or_admin(ctx, chat_id, uid, g):
            await do_maarefe(ctx, chat_id, g)
        return

    # 🌙 شروع/پایان اکت‌گیریِ شب (فقط گاد/ادمین)
    if text == "/شب":
        if await _is_god_or_admin(ctx, chat_id, uid, g):
            await start_night(ctx, chat_id, g)
        return

    if text == "/روز":
        if await _is_god_or_admin(ctx, chat_id, uid, g):
            if g.night_active:
                await end_night(ctx, chat_id, g)
            elif getattr(g, "maarefe_active", False):
                # پایانِ شبِ معارفه: فقط بستن چت مافیا (بدون محاسبهٔ مرگ)
                g.maarefe_active = False
                store.save()
                await _room_set_locked(ctx, g, True)
                await ctx.bot.send_message(chat_id, "☀️ روز شد. چت گروه مافیا بسته شد.")
            else:
                await end_night(ctx, chat_id, g)
        return

    # 🔓 باز کردن دستیِ چت اتاق مافیا وسط روز (برای سؤال از تیم مافیا)
    if text == "/باز":
        if await _is_god_or_admin(ctx, chat_id, uid, g):
            if getattr(g, "mafia_room_id", None):
                await _room_set_locked(ctx, g, False)
                await ctx.bot.send_message(chat_id, "🔓 چت گروه مافیا باز شد. با «/روز» یا «/بسته» دوباره بسته می‌شود.")
            else:
                await ctx.bot.send_message(chat_id, "ℹ️ اتاق مافیایی برای این بازی فعال نیست.")
            # 💣 گیمر: اگر بمبی فعال است، از الیوت بپرس می‌خواهد کاری کند؟
            if _is_gamer_scenario(g) and getattr(g, "gm_bomb_seat", None):
                el = _find_seat_by_role(g, _R_ELLIOT)
                seat = g.gm_bomb_seat
                if el and seat in g.seats:
                    tname = g.seats[seat][1]
                    m = await _safe_pm(
                        ctx, g.seats[el][0],
                        f"💣 جلوی {seat}. {tname} بمب وجود دارد!\nآیا می‌خواهی کاری کنی؟",
                        _gm_yesno_kb("gm_bz_yes", "gm_bz_no"))
                    if m:
                        await ctx.bot.send_message(chat_id, "💣 سؤالِ بمب به پیوی الیوت رفت.")
        return

    # 🔒 بستن دستیِ چت اتاق مافیا
    if text == "/بسته":
        if await _is_god_or_admin(ctx, chat_id, uid, g):
            if getattr(g, "mafia_room_id", None):
                await _room_set_locked(ctx, g, True)
                await ctx.bot.send_message(chat_id, "🔒 چت گروه مافیا بسته شد.")
            else:
                await ctx.bot.send_message(chat_id, "ℹ️ اتاق مافیایی برای این بازی فعال نیست.")
        return

    # تغییر موضوع رویداد (گاد/ادمین) — بدون ریپلای
    if await _try_set_event_title(ctx, chat_id, uid, g, text):
        return

    # 📊 آمار من — هر بازیکنی در گروه فعال می‌تواند آمار خودش را ببیند
    if text == "آمار من":
        stats = load_player_stats()
        p = stats.get(str(uid))
        if not p or (p.get("games", 0) == 0 and p.get("god_games", 0) == 0):
            await msg.reply_text("📭 هنوز آماری برای شما ثبت نشده است.")
        else:
            await msg.reply_text(format_player_stats(p), parse_mode="HTML")
        return

    # 📊 آمار کل — لیدربرد بهترین‌های کل دوران (برای همه در هر گروه فعال)
    if text == "آمار کل":
        current = load_player_stats()
        board = build_alltime_leaderboard_text(current)
        if not board:
            await msg.reply_text("📭 هنوز آماری ثبت نشده است.")
        else:
            await msg.reply_text(board, parse_mode="HTML")
        return

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

    if g.phase == "idle" and (not getattr(g, "awaiting_shuffle_decision", False)) and text.strip() == "کنسل":
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

async def weekly_now_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ارسال فوری آمار هفتگی به همهٔ گروه‌های فعال (فقط مدیر اصلی)."""
    if update.effective_user.id != 99347107:
        return
    result = await broadcast_weekly_stats(ctx.bot, force=True) or {}
    sent = result.get("sent", 0)
    reason = result.get("reason")

    if sent > 0:
        await update.message.reply_text(f"✅ آمار هفتگی به {sent} گروه فعال ارسال شد.")
    elif reason == "no_data":
        await update.message.reply_text(
            "📭 هنوز هیچ بازی تمام‌شده‌ای ثبت نشده، پس آماری برای ارسال وجود ندارد.\n"
            "ابتدا یک بازی کامل تا اعلام برنده انجام بده."
        )
    elif reason == "no_activity":
        await update.message.reply_text("ℹ️ آماری برای نمایش وجود دارد ولی ارسال نشد.")
    elif not store.active_groups:
        await update.message.reply_text("⚠️ هیچ گروه فعالی وجود ندارد (active_groups خالی است).")
    else:
        await update.message.reply_text("⚠️ ارسال انجام نشد — به لاگ‌ها نگاه کن.")


async def sendtoall_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ریپلای روی یک پیام در پی‌وی + /sendtoall → ارسال همان پیام به همهٔ گروه‌های فعال."""
    if update.effective_user.id != 99347107:
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("⚠️ این دستور را فقط در پی‌وی بات بزن.")
        return

    replied = update.message.reply_to_message
    if not replied:
        await update.message.reply_text(
            "❗ روی پیامی که می‌خواهی به همهٔ گروه‌ها بفرستی ریپلای کن و بعد /sendtoall را بزن."
        )
        return

    if not store.active_groups:
        await update.message.reply_text("⚠️ هیچ گروه فعالی وجود ندارد.")
        return

    sent = 0
    failed = 0
    pin = bool(ctx.args) and ctx.args[0].lower() in ("pin", "p", "سنجاق")
    for chat_id in list(store.active_groups):
        try:
            msg = await ctx.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=update.effective_chat.id,
                message_id=replied.message_id,
            )
            if pin:
                try:
                    await ctx.bot.pin_chat_message(
                        chat_id, msg.message_id, disable_notification=True
                    )
                except Exception:
                    pass
            sent += 1
        except Exception as e:
            failed += 1
            print(f"⚠️ sendtoall failed for {chat_id}:", e)

    report = f"✅ پیام به {sent} گروه ارسال شد."
    if pin:
        report += " (سنجاق شد)"
    if failed:
        report += f"\n⚠️ {failed} گروه ناموفق بود."
    await update.message.reply_text(report)


async def start_selected_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """شروع دستی دور لیست منتخب (برای تست) — فقط مدیر اصلی."""
    if update.effective_user.id != ADMIN_ID:
        return
    result = await launch_selected_round(ctx.bot)
    sent_names = result.get("sent", [])
    failed = result.get("failed", [])

    if not sent_names and not failed:
        await update.message.reply_text("⚠️ کاندیدایی پیدا نشد.")
        return

    lines = [f"✅ دعوت لیست منتخب به {len(sent_names)} نفر ارسال شد:"]
    for i, nm in enumerate(sent_names, 1):
        lines.append(f"{i}. {escape(nm, quote=False)}")
    if failed:
        lines.append("")
        lines.append(f"⚠️ به {len(failed)} نفر نرسید (بات را استارت نکرده‌اند):")
        for nm in failed:
            lines.append(f"• {escape(nm, quote=False)}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def selected_report_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """گزارش وضعیت لیست منتخب — فقط مدیر اصلی."""
    if update.effective_user.id != ADMIN_ID:
        return
    sl = load_selected_list()
    if not sl.get("candidates"):
        await update.message.reply_text("هنوز دور لیست منتخبی شروع نشده است.")
        return
    await update.message.reply_text(build_selected_report(sl), parse_mode="HTML")


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
        prompt = await ctx.bot.send_message(
            chat_id,
            f"✏️ این پیام را ریپلای کنید و نام جدید خود را برای صندلی {seat_no} به فارسی وارد کنید:"
        )
        _start_name_wait(ctx, chat_id, g, new_uid, seat_no, prompt)

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
    app.add_handler(CommandHandler("start", start_welcome, filters=filters.ChatType.PRIVATE))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & filters.Regex(r"^\s*(آمار من|آمار کل)\s*$"),
            handle_stats_pm
        )
    )
    # ✍️ جمله‌ی معارفه‌ی دن‌کارلئونه (سناریو گیمر) — هر متن پیوی که پرچمش فعال باشد
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            handle_don_sentence_pm
        )
    )
    app.add_handler(CommandHandler("addroom", addroom_cmd, filters=group_filter))
    app.add_handler(CommandHandler("delroom", delroom_cmd, filters=group_filter))
    app.add_handler(CommandHandler("active", activate_group))
    app.add_handler(CommandHandler("deactivate", deactivate_group))
    app.add_handler(CommandHandler("weekly", weekly_now_cmd))
    app.add_handler(CommandHandler("sendtoall", sendtoall_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("startselected", start_selected_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("selected", selected_report_cmd, filters=filters.ChatType.PRIVATE))
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
    # 🔒 مدیریتِ سناریو/نقش/کارت — فقط ادمینِ اصلیِ بات
    _owner = filters.User(ADMIN_ID)
    app.add_handler(CommandHandler("addscenario", addscenario, filters=_owner))
    app.add_handler(CommandHandler("listscenarios", list_scenarios, filters=_owner))
    app.add_handler(CommandHandler("removescenario", remove_scenario, filters=_owner))
    app.add_handler(CommandHandler("addmafia", cmd_addmafia, filters=_owner))
    app.add_handler(CommandHandler("listmafia", cmd_listmafia, filters=_owner))
    app.add_handler(CommandHandler("list", cmd_lists, filters=group_filter))
    app.add_handler(CommandHandler("addcard", add_card, filters=_owner))
    app.add_handler(CommandHandler("listcard", list_cards, filters=_owner))
    app.add_handler(CommandHandler("addindep", add_indep_role, filters=_owner))
    app.add_handler(CommandHandler("listindep", list_indep_roles, filters=_owner))
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

    # 📊 زمان‌بند آمار هفتگی
    asyncio.create_task(weekly_scheduler(app))

    # ⏳ جلوگیری از خاموشی برنامه
    await asyncio.Event().wait()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

