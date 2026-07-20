import asyncio
import html
import io
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
import yaml

CONFIG_FILE = "config.yml"
DB_FILE = "tickets.db"
ALLOWED_GUILD_ID = 1504088095220568094
CATEGORY_KEYS = ["buy_orders", "general_support", "complaints_reports", "other_issues"]
CATEGORY_ID_KEYS = {
    "buy_orders": "buy_orders_id",
    "general_support": "general_support_id",
    "complaints_reports": "complaints_reports_id",
    "other_issues": "other_issues_id",
}
PIN_PREFIX = "📌・"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("zerox-ticket-bot")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime] = None) -> str:
    return (dt or utcnow()).isoformat()


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    total_hours = days * 24 + hours
    if total_hours and minutes:
        return f"{total_hours}h {minutes}m"
    if total_hours:
        return f"{total_hours}h"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def deep_defaults() -> dict[str, Any]:
    return {
        "bot": {"token": ""},
        "guild": {"guild_id": str(ALLOWED_GUILD_ID)},
        "roles": {"owner_role_id": "", "staff_role_id": ""},
        "channels": {"logs_channel_id": "", "panel_channel_id": ""},
        "ticket_categories": {"buy_orders_id": "", "general_support_id": "", "complaints_reports_id": "", "other_issues_id": ""},
        "tickets": {
            "max_active_per_user": 1,
            "inactivity_close_hours": 24,
            "closed_delete_hours": 24,
            "channel_name_format": "ticket-{username}-{number}",
            "category_channel_name_formats": {
                "buy_orders": "paid-{username}",
                "general_support": "support-{username}",
                "complaints_reports": "report-{username}",
                "other_issues": "other-{username}",
            },
        },
        "panel": {
            "title": "ZeroX Host Support",
            "description": "<a:fire_gif:1514165449275871393> **Need help? Open a ticket by choosing the correct category below. Our team will assist you as quickly as possible.**\n\n<:Store:1514165709616451696> **Buy / Orders**\n> Purchase items or services\n> Custom orders & payments\n> Order-related questions\n\n<a:support:1514165749097173074> **General Support**\n> Server-related help\n> Technical issues\n> General questions & guidance\n\n<a:hammer_gif:1514165780999180309> **Complaints / Reports**\n> Report rule breakers\n> Staff-related issues\n> Scams, abuse, or disputes\n\n<a:purchase:1528210165642432594> **Other Issues**\n> Anything not listed above\n> Suggestions or feedback\n> Miscellaneous problems\n\n<a:Minecraft_diamond:1528237013852225609> **Please provide clear details after opening a ticket to help us assist you faster.**\n",
            "color": "#5865F2", "thumbnail_url": "", "image_url": "", "footer_text": "ZeroX Host Support", "footer_icon_url": "", "dropdown_placeholder": "Select the correct ticket category",
        },
        "categories": {
            "buy_orders": {"name": "Buy / Orders", "emoji": "<:Store:1514165709616451696>"},
            "general_support": {"name": "General Support", "emoji": "<a:support:1514165749097173074>"},
            "complaints_reports": {"name": "Complaints / Reports", "emoji": "<a:hammer_gif:1514165780999180309>"},
            "other_issues": {"name": "Other Issues", "emoji": "<a:purchase:1528210165642432594>"},
        },
        "transcripts": {"enabled": True, "mandatory": True, "format": "html", "send_to_opener_dm": True},
        "panel_state": {"message_id": "", "channel_id": ""},
    }


def merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config() -> dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        save_config(deep_defaults())
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise SystemExit(f"Invalid config.yml: {e}")
    cfg = merge(deep_defaults(), raw)
    cfg["guild"]["guild_id"] = str(ALLOWED_GUILD_ID)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    os.replace(tmp, CONFIG_FILE)


def int_id(v: Any) -> Optional[int]:
    try:
        return int(v) if str(v).strip() else None
    except Exception:
        return None


def color_value(s: str) -> int:
    try:
        return int(str(s).strip().lstrip("#"), 16)
    except Exception:
        return 0x5865F2


def positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def sanitize_name(name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_\- ]+", "", name).strip().lower().replace(" ", "-")
    name = re.sub(r"-+", "-", name).strip("-")
    return (name or "ticket")[:90]


def emoji_from(raw: str):
    if not raw:
        return None
    try:
        return discord.PartialEmoji.from_str(raw)
    except Exception:
        return None


async def defer_if_needed(interaction: discord.Interaction, *, ephemeral: bool = True) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)


class Store:
    def __init__(self):
        self.db = sqlite3.connect(DB_FILE)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS tickets(
            ticket_id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER UNIQUE,
            opener_id INTEGER NOT NULL, category_key TEXT NOT NULL, category_name TEXT NOT NULL, discord_category_id INTEGER,
            reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, last_activity_at TEXT NOT NULL,
            closed_at TEXT, scheduled_delete_at TEXT, claimed_by INTEGER, claim_at TEXT, pinned INTEGER NOT NULL DEFAULT 0,
            added_users TEXT NOT NULL DEFAULT '', custom_channel_name TEXT, transcript_status TEXT NOT NULL DEFAULT 'missing',
            transcript_reference TEXT, closed_by INTEGER, deleted_by INTEGER, deleted_at TEXT, close_reason TEXT
        )""")
        self.db.commit()

    def row(self, q: str, args=()):
        return self.db.execute(q, args).fetchone()

    def rows(self, q: str, args=()):
        return self.db.execute(q, args).fetchall()

    def exec(self, q: str, args=()):
        cur = self.db.execute(q, args)
        self.db.commit()
        return cur


class TicketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.cfg = load_config()
        self.store = Store()
        self.user_locks: dict[int, asyncio.Lock] = {}

    async def setup_hook(self):
        self.add_view(PanelView(self))
        self.add_view(TicketControlView(self))
        self.tree.copy_global_to(guild=discord.Object(id=ALLOWED_GUILD_ID))
        await self.tree.sync(guild=discord.Object(id=ALLOWED_GUILD_ID))
        self.maintenance.start()

    async def on_guild_join(self, guild: discord.Guild):
        if guild.id != ALLOWED_GUILD_ID:
            log.warning("Joined unsupported guild %s; leaving", guild.id)
            await guild.leave()

    def allowed_guild(self, guild: Optional[discord.Guild]) -> bool:
        return bool(guild and guild.id == ALLOWED_GUILD_ID)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return self.allowed_guild(interaction.guild)

    def owner_role(self, guild: discord.Guild): return guild.get_role(int_id(self.cfg["roles"].get("owner_role_id")) or 0)
    def staff_role(self, guild: discord.Guild): return guild.get_role(int_id(self.cfg["roles"].get("staff_role_id")) or 0)
    def logs_channel(self, guild: discord.Guild): return guild.get_channel(int_id(self.cfg["channels"].get("logs_channel_id")) or 0)
    def panel_channel(self, guild: discord.Guild): return guild.get_channel(int_id(self.cfg["channels"].get("panel_channel_id")) or 0)

    def is_owner(self, member: discord.Member) -> bool:
        rid = int_id(self.cfg["roles"].get("owner_role_id")); return bool(rid and any(r.id == rid for r in member.roles))

    def is_staff(self, member: discord.Member) -> bool:
        return self.is_owner(member) or bool((rid := int_id(self.cfg["roles"].get("staff_role_id"))) and any(r.id == rid for r in member.roles))

    def category_id(self, key: str) -> Optional[int]: return int_id(self.cfg["ticket_categories"].get(CATEGORY_ID_KEYS[key]))

    def emoji_display(self, raw: str, guild: Optional[discord.Guild] = None) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""
        try:
            parsed = discord.PartialEmoji.from_str(raw)
            if guild and parsed.id:
                found = guild.get_emoji(parsed.id) or discord.utils.get(guild.emojis, name=parsed.name)
                return str(found or parsed)
            if guild and parsed.name:
                found = discord.utils.get(guild.emojis, name=parsed.name)
                if found:
                    return str(found)
            return str(parsed)
        except Exception:
            if guild:
                found = discord.utils.get(guild.emojis, name=raw.strip(":<>"))
                if found:
                    return str(found)
            return raw

    def category_display(self, key: str, guild: Optional[discord.Guild] = None) -> str:
        data = self.cfg["categories"].get(key, {})
        emoji = self.emoji_display(data.get("emoji", ""), guild)
        return f"{emoji} {data.get('name', key).strip()}".strip()

    def inactivity_close_hours(self) -> float:
        return positive_float(self.cfg["tickets"].get("inactivity_close_hours", 24), 24)

    def closed_delete_hours(self) -> float:
        return positive_float(self.cfg["tickets"].get("closed_delete_hours", 24), 24)

    def active_tickets_for(self, uid: int):
        return self.store.rows("SELECT * FROM tickets WHERE opener_id=? AND guild_id=? AND status='open'", (uid, ALLOWED_GUILD_ID))

    def active_ticket_for(self, uid: int):
        rows = self.active_tickets_for(uid)
        return rows[0] if rows else None

    async def cleanup_stale_user_tickets(self, guild: discord.Guild, uid: int) -> None:
        for ticket in self.active_tickets_for(uid):
            if not guild.get_channel(ticket["channel_id"]):
                self.store.exec("UPDATE tickets SET status='deleted', deleted_at=?, close_reason=? WHERE ticket_id=?", (iso(), "Ticket channel was missing during validation.", ticket["ticket_id"]))
                await self.log_event("Stale ticket cleaned", "Ticket channel was missing; database record was released.", ticket)

    def ticket_by_channel(self, cid: int):
        return self.store.row("SELECT * FROM tickets WHERE channel_id=? AND guild_id=? AND status!='deleted'", (cid, ALLOWED_GUILD_ID))

    async def safe_send(self, interaction: discord.Interaction, content=None, **kwargs):
        try:
            if interaction.response.is_done():
                return await interaction.followup.send(content, **kwargs)
            return await interaction.response.send_message(content, **kwargs)
        except Exception as e:
            log.warning("interaction response failed: %s", e)

    async def log_event(self, title: str, desc: str = "", ticket=None, file: Optional[discord.File] = None) -> bool:
        guild = self.get_guild(ALLOWED_GUILD_ID)
        ch = self.logs_channel(guild) if guild else None
        embed = discord.Embed(title=title, description=desc[:3500], color=0x5865F2, timestamp=utcnow())
        if ticket:
            embed.add_field(name="Ticket ID", value=str(ticket["ticket_id"]), inline=True)
            embed.add_field(name="Opener", value=f"<@{ticket['opener_id']}>\n{ticket['opener_id']}", inline=True)
            embed.add_field(name="Category", value=ticket["category_name"], inline=True)
            embed.add_field(name="Reason", value=ticket["reason"][:1024], inline=False)
            embed.add_field(name="Opened", value=ticket["created_at"], inline=True)
            embed.add_field(name="Closed", value=ticket["closed_at"] or "—", inline=True)
            embed.add_field(name="Deleted", value=ticket["deleted_at"] or "—", inline=True)
            embed.add_field(name="Claimed By", value=f"<@{ticket['claimed_by']}>" if ticket["claimed_by"] else "Unclaimed", inline=True)
            embed.add_field(name="Closed By", value=f"<@{ticket['closed_by']}>" if ticket["closed_by"] else "—", inline=True)
            embed.add_field(name="Deleted By", value=f"<@{ticket['deleted_by']}>" if ticket["deleted_by"] else "—", inline=True)
            embed.add_field(name="Pinned", value="Yes" if ticket["pinned"] else "No", inline=True)
            embed.add_field(name="Close Reason", value=(ticket["close_reason"] or "—")[:1024], inline=False)
            embed.add_field(name="Transcript", value=ticket["transcript_status"], inline=True)
        if ch:
            try:
                await ch.send(embed=embed, file=file)
                return True
            except Exception as e:
                log.warning("log send failed: %s", e)
                return False
        log.warning("log channel missing: %s %s", title, desc)
        return False

    def panel_embed(self) -> discord.Embed:
        p = self.cfg["panel"]
        e = discord.Embed(title=p["title"], description=p["description"], color=color_value(p["color"]), timestamp=utcnow())
        if p.get("thumbnail_url"): e.set_thumbnail(url=p["thumbnail_url"])
        if p.get("image_url"): e.set_image(url=p["image_url"])
        e.set_footer(text=p.get("footer_text") or "ZeroX Host Support", icon_url=p.get("footer_icon_url") or None)
        return e

    async def ensure_categories(self, guild: discord.Guild) -> list[str]:
        report: list[str] = []
        me = guild.me or guild.get_member(self.user.id if self.user else 0)
        can_manage = bool(me and me.guild_permissions.manage_channels)
        if not can_manage:
            report.append("⚠️ Manage Channels is not visible in the bot member cache; creation will still be attempted and Discord will return the real permission result.")
        resolved: list[discord.CategoryChannel] = []
        for key in CATEGORY_KEYS:
            name = self.cfg["categories"][key]["name"]
            cat = guild.get_channel(self.category_id(key) or 0)
            if not isinstance(cat, discord.CategoryChannel):
                cat = discord.utils.get(guild.categories, name=name)
            if not isinstance(cat, discord.CategoryChannel):
                try:
                    cat = await guild.create_category_channel(name=name, reason="ZeroX Host ticket setup")
                    report.append(f"✅ Created category `{name}` (`{cat.id}`).")
                except discord.Forbidden:
                    report.append(f"❌ Discord denied permission to create `{name}`. Give the bot Manage Channels or Administrator, then run `/setup` again.")
                    log.warning("Forbidden while creating category %s", name)
                    continue
                except discord.HTTPException as e:
                    report.append(f"❌ Discord API failed to create `{name}`: {e.status} {e.text[:120]}.")
                    log.warning("HTTP error creating category %s: %s", name, e)
                    continue
                except Exception as e:
                    report.append(f"❌ Failed to create `{name}`: {type(e).__name__}: {e}.")
                    log.warning("category create failed for %s: %s", name, e)
                    continue
            elif cat.name != name and can_manage:
                try:
                    old_name = cat.name
                    await cat.edit(name=name, reason="ZeroX Host ticket category name sync")
                    report.append(f"✅ Renamed category `{old_name}` to `{name}`.")
                except Exception as e:
                    report.append(f"⚠️ Reused `{cat.name}` but could not rename it to `{name}`.")
                    log.warning("category rename failed for %s: %s", name, e)
            else:
                report.append(f"✅ Reused category `{cat.name}`.")
            if can_manage:
                try:
                    await cat.set_permissions(guild.default_role, view_channel=False, reason="ZeroX Host ticket category privacy")
                    if me:
                        await cat.set_permissions(me, view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, reason="ZeroX Host ticket category bot access")
                    staff_role = self.staff_role(guild)
                    owner_role = self.owner_role(guild)
                    if staff_role:
                        await cat.set_permissions(staff_role, view_channel=True, send_messages=True, read_message_history=True, reason="ZeroX Host ticket category staff access")
                    if owner_role:
                        await cat.set_permissions(owner_role, view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, reason="ZeroX Host ticket category owner access")
                    report.append(f"✅ Synced private permissions for `{cat.name}`.")
                except Exception as e:
                    report.append(f"⚠️ Could not sync permissions for `{cat.name}`: {type(e).__name__}.")
                    log.warning("category permission sync failed for %s: %s", cat.name, e)
            self.cfg["ticket_categories"][CATEGORY_ID_KEYS[key]] = str(cat.id)
            resolved.append(cat)
        report.append("ℹ️ Discord may hide empty categories from some users until a ticket channel exists inside them.")
        save_config(self.cfg)
        try:
            start = max(0, len(guild.categories) - len(resolved))
            for i, cat in enumerate(resolved):
                await cat.edit(position=start + i, reason="ZeroX Host ticket category ordering")
            if resolved:
                report.append("✅ Ticket categories were grouped near the bottom where Discord permissions allowed.")
        except Exception as e:
            report.append("⚠️ Category reordering failed, but ticket categories were still saved.")
            log.warning("category reorder failed: %s", e)
        return report

    def ticket_embed(self, ticket) -> discord.Embed:
        hours = self.inactivity_close_hours()
        guild = self.get_guild(ticket["guild_id"])
        category = self.category_display(ticket["category_key"], guild)
        opener = f"<@{ticket['opener_id']}>"
        claimed = f"<@{ticket['claimed_by']}>" if ticket["claimed_by"] else "`Unclaimed`"
        status_icon = "🟢" if ticket["status"] == "open" else "🔒"
        e = discord.Embed(
            title="🎟️ Ticket Opened",
            description=(
                "**Please provide a detailed description of your issue.**\n"
                "ZeroX Host support staff will reply as soon as possible — thank you for being patient.\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "📝 **Reason For Opening This Ticket**\n"
                f"> {ticket['reason'][:900]}\n\n"
                f"⏱️ **Auto-close:** inactive for `{hours}h`"
            ),
            color=0xF59E0B,
            timestamp=utcnow(),
        )
        e.set_author(name=f"ZeroX Host Support • {ticket['category_name']}")
        if self.cfg["panel"].get("thumbnail_url"):
            e.set_thumbnail(url=self.cfg["panel"]["thumbnail_url"])
        e.add_field(name="Ticket", value=f"`#{ticket['ticket_id']:04d}`", inline=True)
        e.add_field(name="Opened By", value=opener, inline=True)
        e.add_field(name="Category", value=category, inline=True)
        e.add_field(name="Status", value=f"{status_icon} `{ticket['status'].title()}`", inline=True)
        e.add_field(name="Claimed", value=claimed, inline=True)
        e.add_field(name="Pinned", value="`Yes`" if ticket["pinned"] else "`No`", inline=True)
        e.set_footer(text="ZeroX Host • Premium Support")
        return e

    async def refresh_ticket_message(self, channel: discord.TextChannel) -> None:
        ticket = self.ticket_by_channel(channel.id)
        if not ticket:
            return
        try:
            async for message in channel.history(limit=25):
                if message.author == self.user and message.embeds and message.embeds[0].title == "🎟️ Ticket Opened":
                    await message.edit(embed=self.ticket_embed(ticket), view=TicketControlView(self))
                    return
        except Exception as e:
            log.warning("failed to refresh ticket embed for channel %s: %s", channel.id, e)

    async def create_ticket(self, interaction: discord.Interaction, key: str, reason: str):
        lock = self.user_locks.setdefault(interaction.user.id, asyncio.Lock())
        async with lock:
            guild = interaction.guild; assert guild
            await self.cleanup_stale_user_tickets(guild, interaction.user.id)
            active = self.active_tickets_for(interaction.user.id)
            max_active = max(1, int(self.cfg["tickets"].get("max_active_per_user", 1)))
            if len(active) >= max_active:
                existing = active[0]
                view = discord.ui.View(); view.add_item(discord.ui.Button(label="🎟️ Visit Ticket ↗", url=f"https://discord.com/channels/{ALLOWED_GUILD_ID}/{existing['channel_id']}"))
                return await self.safe_send(interaction, "You already have an active ticket.", ephemeral=True, view=view)
            cat = guild.get_channel(self.category_id(key) or 0)
            if not isinstance(cat, discord.CategoryChannel):
                await self.ensure_categories(guild); cat = guild.get_channel(self.category_id(key) or 0)
            staff, owner = self.staff_role(guild), self.owner_role(guild)
            overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False), guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True), interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)}
            if staff: overwrites[staff] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            if owner: overwrites[owner] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            cur = self.store.exec("INSERT INTO tickets(guild_id, channel_id, opener_id, category_key, category_name, discord_category_id, reason, status, created_at, last_activity_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (ALLOWED_GUILD_ID, None, interaction.user.id, key, self.cfg["categories"][key]["name"], cat.id if cat else None, reason, "open", iso(), iso()))
            tid = cur.lastrowid
            username = sanitize_name(interaction.user.name)
            formats = self.cfg["tickets"].get("category_channel_name_formats", {})
            name_format = formats.get(key) or self.cfg["tickets"].get("channel_name_format", "ticket-{username}-{number}")
            base = sanitize_name(name_format.format(username=username, number=f"{tid:04d}", id=tid, category=key))[:90]
            ch = None
            try:
                ch = await guild.create_text_channel(base, category=cat, overwrites=overwrites, reason=f"Ticket #{tid}")
                self.store.exec("UPDATE tickets SET channel_id=?, custom_channel_name=? WHERE ticket_id=?", (ch.id, base, tid))
                ticket = self.ticket_by_channel(ch.id)
                await ch.send(
                    content=f"{interaction.user.mention} {staff.mention if staff else ''}".strip(),
                    embed=self.ticket_embed(ticket),
                    view=TicketControlView(self),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                )
                await self.log_event("Ticket opened", f"Channel: {ch.mention}", ticket)
                view = discord.ui.View(); view.add_item(discord.ui.Button(label="🎟️ Visit Ticket ↗", url=ch.jump_url))
                created = discord.Embed(title="🎟️ Ticket Created", description="Your ticket has been created. Click the button below to access it!", color=0xF59E0B, timestamp=utcnow())
                created.set_author(name="ZeroX Host Support")
                created.set_footer(text="ZeroX Host • Fast, clean support")
                await self.safe_send(interaction, embed=created, ephemeral=True, view=view)
            except Exception as e:
                self.store.exec("DELETE FROM tickets WHERE ticket_id=?", (tid,))
                if ch:
                    try:
                        await ch.delete(reason="Ticket creation rollback after setup failure")
                    except Exception:
                        pass
                await self.log_event("Ticket creation failed", f"User: {interaction.user} ({interaction.user.id})\nError: {e}")
                await self.safe_send(interaction, "❌ Ticket creation failed safely. Please contact staff or try again shortly.", ephemeral=True)

    async def transcript_file(self, channel: discord.TextChannel, ticket) -> discord.File:
        parts = ["<!doctype html><meta charset='utf-8'><style>body{font-family:Inter,Arial;background:#111827;color:#e5e7eb}.msg{border-bottom:1px solid #374151;padding:12px}.meta{color:#93c5fd}.att{color:#fbbf24}</style>", f"<h1>ZeroX Host Ticket #{ticket['ticket_id']:04d}</h1><p>Opener: {ticket['opener_id']} Category: {html.escape(ticket['category_name'])} Reason: {html.escape(ticket['reason'])}</p>"]
        async for m in channel.history(limit=None, oldest_first=True):
            content = html.escape(m.content or "")
            atts = "".join(f"<div class='att'>Attachment: <a href='{html.escape(a.url)}'>{html.escape(a.filename)}</a></div>" for a in m.attachments)
            embeds = "".join(f"<pre>{html.escape(str(e.to_dict()))}</pre>" for e in m.embeds)
            ref = f" reply to {m.reference.message_id}" if m.reference else ""
            parts.append(f"<div class='msg'><div class='meta'>{html.escape(str(m.author))} ({m.author.id}) {m.created_at.isoformat()}{ref}</div><div>{content}</div>{atts}{embeds}</div>")
        data = "\n".join(parts).encode("utf-8")
        return discord.File(io.BytesIO(data), filename=f"zerox-ticket-{ticket['ticket_id']:04d}.html")

    async def send_transcript_dm(self, channel: discord.TextChannel, ticket, file: discord.File, status_text: str, actor: Optional[discord.abc.User] = None, reason: str = "No reason provided") -> None:
        if not self.cfg["transcripts"].get("send_to_opener_dm"):
            return
        opener = channel.guild.get_member(ticket["opener_id"])
        if not opener:
            try:
                opener = await self.fetch_user(ticket["opener_id"])
            except Exception as e:
                log.warning("could not fetch opener for transcript DM on ticket %s: %s", ticket["ticket_id"], e)
                return
        try:
            created = parse_dt(ticket["created_at"]) or utcnow()
            finished = parse_dt(ticket["deleted_at"] or ticket["closed_at"]) or utcnow()
            taken = format_duration(max(0, int((finished - created).total_seconds())))
            closed_by = f"<@{ticket['closed_by']}>" if ticket["closed_by"] else "—"
            deleted_by = f"<@{ticket['deleted_by']}>" if ticket["deleted_by"] else "—"
            embed = discord.Embed(
                title=channel.name,
                description=(
                    f"Ticket **{channel.name}** is {status_text}.\n\n"
                    f"**Created by :**\n<@{ticket['opener_id']}> - {getattr(opener, 'name', ticket['opener_id'])}\n"
                    f"**Opened at :**\n{ticket['created_at']}\n\n"
                    f"**Closed by :**\n{closed_by}\n"
                    f"**Closed at :**\n{ticket['closed_at'] or '—'}\n\n"
                    f"**Deleted by :**\n{deleted_by}\n"
                    f"**Deleted at :**\n{ticket['deleted_at'] or '—'}\n\n"
                    f"**Action by :**\n{actor.mention if actor else 'System'} - {getattr(actor, 'name', 'System')}\n\n"
                    f"**Reason :**\n{reason or ticket['close_reason'] or 'No reason provided'}\n\n"
                    f"**Time taken :**\n{taken}"
                ),
                color=0x7C3AED,
                timestamp=utcnow(),
            )
            embed.set_footer(text="Zerox Host | Where Power meets precision")
            await opener.send(
                f"Your ticket **{channel.name}** in **Zerox Host | Where Power meets precision** has been {status_text} by **{getattr(actor, 'name', 'System')}**",
                file=file,
                embed=embed,
            )
        except Exception as e:
            log.warning("transcript DM failed for ticket %s: %s", ticket["ticket_id"], e)

    async def ensure_transcript(self, channel: discord.TextChannel, ticket, reason="Transcript generated") -> bool:
        if ticket["transcript_status"] == "uploaded" and ticket["transcript_reference"]:
            return True
        try:
            file = await self.transcript_file(channel, ticket)
            logged = await self.log_event(reason, "HTML transcript attached.", ticket, file)
            if not logged:
                self.store.exec("UPDATE tickets SET transcript_status='failed' WHERE ticket_id=?", (ticket["ticket_id"],))
                return False
            self.store.exec("UPDATE tickets SET transcript_status='uploaded', transcript_reference=? WHERE ticket_id=?", (iso(), ticket["ticket_id"]))
            return True
        except Exception as e:
            await self.log_event("Transcript upload failed", str(e), ticket)
            return False

    async def close_ticket(self, channel: discord.TextChannel, actor: discord.abc.User, reason: str, auto=False):
        ticket = self.ticket_by_channel(channel.id)
        if not ticket or ticket["status"] != "open": return False
        try:
            transcript = await self.transcript_file(channel, ticket)
            delete_at = utcnow() + timedelta(hours=self.closed_delete_hours())
            self.store.exec("UPDATE tickets SET status='closed', closed_at=?, scheduled_delete_at=?, closed_by=?, close_reason=?, transcript_status='pending' WHERE ticket_id=?", (iso(), iso(delete_at), actor.id, reason, ticket["ticket_id"]))
            updated = self.ticket_by_channel(channel.id)
            log_ok = await self.log_event("Ticket auto-closed" if auto else "Ticket closed", reason or "No reason provided", updated, transcript)
            if self.cfg["transcripts"].get("mandatory") and not log_ok:
                self.store.exec("UPDATE tickets SET status='open', closed_at=NULL, scheduled_delete_at=NULL, closed_by=NULL, close_reason=?, transcript_status='failed' WHERE ticket_id=?", (reason, ticket["ticket_id"]))
                return False
            self.store.exec("UPDATE tickets SET transcript_status='uploaded', transcript_reference=? WHERE ticket_id=?", (iso(), ticket["ticket_id"]))
            updated = self.ticket_by_channel(channel.id)
            await self.refresh_ticket_message(channel)
            try: await channel.set_permissions(channel.guild.get_member(ticket["opener_id"]), send_messages=False, view_channel=True)
            except Exception: pass
            closed_embed = discord.Embed(title="🔒 Ticket Closed", description=f"**Reason:** {reason or 'No reason provided'}\n\n📄 Transcript has been saved and sent where configured.", color=0xED4245, timestamp=utcnow())
            closed_embed.set_footer(text="ZeroX Host • Ticket Locked")
            await channel.send(embed=closed_embed)
            return True
        except Exception as e:
            await self.log_event("Transcript upload failed", str(e), ticket)
            return False

    async def reopen_ticket(self, channel: discord.TextChannel, actor: discord.abc.User, reason: str = "Ticket reopened") -> bool:
        ticket = self.ticket_by_channel(channel.id)
        if not ticket or ticket["status"] != "closed":
            return False
        try:
            self.store.exec("UPDATE tickets SET status='open', closed_at=NULL, scheduled_delete_at=NULL, closed_by=NULL, close_reason=NULL, last_activity_at=? WHERE ticket_id=?", (iso(), ticket["ticket_id"]))
            opener = channel.guild.get_member(ticket["opener_id"])
            if opener:
                await channel.set_permissions(opener, view_channel=True, send_messages=True, read_message_history=True)
            await self.refresh_ticket_message(channel)
            embed = discord.Embed(title="🔓 Ticket Reopened", description=reason or "Ticket reopened", color=0x57F287, timestamp=utcnow())
            embed.set_footer(text="ZeroX Host • Ticket Active")
            await channel.send(embed=embed)
            await self.log_event("Ticket reopened", f"By {actor.mention}\nReason: {reason or 'Ticket reopened'}", self.ticket_by_channel(channel.id))
            return True
        except Exception as e:
            log.warning("ticket reopen failed for channel %s: %s", channel.id, e)
            return False

    async def delete_ticket(self, channel: discord.TextChannel, actor: discord.abc.User, auto=False):
        ticket = self.ticket_by_channel(channel.id)
        if not ticket: return False
        old_status = ticket["status"]
        try:
            transcript = await self.transcript_file(channel, ticket)
            self.store.exec("UPDATE tickets SET status='deleted', deleted_by=?, deleted_at=? WHERE ticket_id=?", (actor.id, iso(), ticket["ticket_id"]))
            updated = self.store.row("SELECT * FROM tickets WHERE ticket_id=?", (ticket["ticket_id"],))
            attach_file = None if ticket["transcript_status"] == "uploaded" and ticket["transcript_reference"] else transcript
            log_ok = await self.log_event("Ticket auto-deleted" if auto else "Ticket deleted", f"Channel: #{channel.name}", updated, attach_file)
            if self.cfg["transcripts"].get("mandatory") and not log_ok:
                self.store.exec("UPDATE tickets SET status=?, deleted_by=NULL, deleted_at=NULL WHERE ticket_id=?", (old_status, ticket["ticket_id"]))
                return False
            if attach_file:
                self.store.exec("UPDATE tickets SET transcript_status='uploaded', transcript_reference=? WHERE ticket_id=?", (iso(), ticket["ticket_id"]))
            updated = self.store.row("SELECT * FROM tickets WHERE ticket_id=?", (ticket["ticket_id"],))
            await self.send_transcript_dm(channel, updated, await self.transcript_file(channel, updated), "deleted", actor, updated["close_reason"] or "No reason provided")
            await channel.delete(reason="ZeroX Host ticket deleted")
            return True
        except Exception as e:
            await self.log_event("Transcript upload failed", str(e), ticket)
            return False

    @tasks.loop(minutes=5)
    async def maintenance(self):
        guild = self.get_guild(ALLOWED_GUILD_ID)
        if not guild: return
        now = utcnow()
        for t in self.store.rows("SELECT * FROM tickets WHERE status='open'"):
            ch = guild.get_channel(t["channel_id"])
            if not ch:
                self.store.exec("UPDATE tickets SET status='deleted', deleted_at=?, close_reason=? WHERE ticket_id=?", (iso(), "Ticket channel was manually deleted.", t["ticket_id"]))
                await self.log_event("Stale ticket cleaned", "Open ticket channel was missing during maintenance.", t)
                continue
            last = parse_dt(t["last_activity_at"]) or now
            if now - last >= timedelta(hours=self.inactivity_close_hours()):
                await self.close_ticket(ch, guild.me, "Closed automatically due to inactivity.", True)
        for t in self.store.rows("SELECT * FROM tickets WHERE status='closed' AND scheduled_delete_at IS NOT NULL"):
            ch = guild.get_channel(t["channel_id"])
            due = parse_dt(t["scheduled_delete_at"])
            if ch and due and now >= due:
                await self.delete_ticket(ch, guild.me, True)

    @maintenance.before_loop
    async def before_maintenance(self): await self.wait_until_ready()

    async def on_message(self, message: discord.Message):
        if message.guild and message.guild.id == ALLOWED_GUILD_ID and not message.author.bot:
            t = self.ticket_by_channel(message.channel.id)
            if t and t["status"] == "open":
                member = message.author if isinstance(message.author, discord.Member) else None
                if message.author.id == t["opener_id"] or (member and self.is_staff(member)):
                    self.store.exec("UPDATE tickets SET last_activity_at=? WHERE ticket_id=?", (iso(), t["ticket_id"]))
        await self.process_commands(message)


class ReasonModal(discord.ui.Modal, title="Open ZeroX Host Ticket"):
    reason = discord.ui.TextInput(label="Reason For Opening This Ticket", style=discord.TextStyle.paragraph, required=True, max_length=1000)
    def __init__(self, bot: TicketBot, key: str): super().__init__(timeout=300); self.bot = bot; self.key = key
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.create_ticket(interaction, self.key, str(self.reason.value))

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Reason modal failed", exc_info=(type(error), error, error.__traceback__))
        await self.bot.safe_send(interaction, "❌ Something went wrong while opening your ticket. Please try again.", ephemeral=True)


class RenameModal(discord.ui.Modal, title="Rename Ticket"):
    name = discord.ui.TextInput(label="New ticket name", required=True, max_length=90)
    def __init__(self, bot: TicketBot): super().__init__(timeout=300); self.bot = bot
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await rename_channel(self.bot, interaction, str(self.name.value))

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Rename modal failed", exc_info=(type(error), error, error.__traceback__))
        await self.bot.safe_send(interaction, "❌ Something went wrong while renaming this ticket.", ephemeral=True)


class CloseModal(discord.ui.Modal, title="Close Ticket"):
    reason = discord.ui.TextInput(label="Close reason", style=discord.TextStyle.paragraph, required=True, max_length=1000)
    def __init__(self, bot: TicketBot): super().__init__(timeout=300); self.bot = bot
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok = await self.bot.close_ticket(interaction.channel, interaction.user, str(self.reason.value))
        await self.bot.safe_send(interaction, "Ticket closed." if ok else "Unable to close ticket; transcript upload may have failed.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Close modal failed", exc_info=(type(error), error, error.__traceback__))
        await self.bot.safe_send(interaction, "❌ Something went wrong while closing this ticket.", ephemeral=True)


class PanelSelect(discord.ui.Select):
    def __init__(self, bot: TicketBot):
        opts = []
        for key in CATEGORY_KEYS:
            c = bot.cfg["categories"][key]
            opts.append(discord.SelectOption(label=c["name"], value=key, emoji=emoji_from(c.get("emoji", ""))))
        super().__init__(placeholder=bot.cfg["panel"].get("dropdown_placeholder", "Select the correct ticket category"), options=opts, custom_id="zerox:panel:select", min_values=1, max_values=1)
        self.bot = bot
    async def callback(self, interaction: discord.Interaction):
        if not self.bot.allowed_guild(interaction.guild): return await self.bot.safe_send(interaction, "This bot is restricted to ZeroX Host.", ephemeral=True)
        await interaction.response.send_modal(ReasonModal(self.bot, self.values[0]))


class PanelView(discord.ui.View):
    def __init__(self, bot: TicketBot): super().__init__(timeout=None); self.add_item(PanelSelect(bot))


class TicketControlView(discord.ui.View):
    def __init__(self, bot: TicketBot):
        super().__init__(timeout=None); self.bot = bot
    async def guard(self, interaction):
        if not isinstance(interaction.user, discord.Member) or not self.bot.is_staff(interaction.user):
            await self.bot.safe_send(interaction, "Only ZeroX Host staff can use this control.", ephemeral=True); return None
        t = self.bot.ticket_by_channel(interaction.channel.id)
        if not t: await self.bot.safe_send(interaction, "This is not a valid ticket channel.", ephemeral=True); return None
        return t
    @discord.ui.button(label="👋 Claim Ticket", style=discord.ButtonStyle.success, custom_id="zerox:ticket:claim", row=0)
    async def claim(self, interaction, button): await claim_ticket(self.bot, interaction)
    @discord.ui.button(label="📌 Pin Ticket", style=discord.ButtonStyle.secondary, custom_id="zerox:ticket:pin", row=1)
    async def pin(self, interaction, button): await pin_ticket(self.bot, interaction)
    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="zerox:ticket:close", row=1)
    async def close(self, interaction, button):
        if await self.guard(interaction): await interaction.response.send_modal(CloseModal(self.bot))
    @discord.ui.button(label="❌ Delete Ticket", style=discord.ButtonStyle.danger, custom_id="zerox:ticket:delete", row=2)
    async def delete(self, interaction, button):
        if await self.guard(interaction): await interaction.response.send_message("Confirm ticket deletion.", view=ConfirmDeleteView(self.bot), ephemeral=True)


class UserActionView(discord.ui.View):
    def __init__(self, bot: TicketBot, action: str): super().__init__(timeout=120); self.add_item(UserActionSelect(bot, action))
class UserActionSelect(discord.ui.UserSelect):
    def __init__(self, bot: TicketBot, action: str): super().__init__(placeholder="Choose a user", min_values=1, max_values=1); self.bot=bot; self.action=action
    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await user_action(self.bot, interaction, self.values[0], self.action)
class ConfirmDeleteView(discord.ui.View):
    def __init__(self, bot: TicketBot): super().__init__(timeout=120); self.bot=bot
    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger)
    async def yes(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok = await self.bot.delete_ticket(interaction.channel, interaction.user)
        if not ok: await self.bot.safe_send(interaction, "Delete blocked because transcript upload failed.", ephemeral=True)


async def require_staff(bot, interaction):
    if not isinstance(interaction.user, discord.Member) or not bot.is_staff(interaction.user):
        await bot.safe_send(interaction, "You do not have permission.", ephemeral=True); return None
    t = bot.ticket_by_channel(interaction.channel.id)
    if not t: await bot.safe_send(interaction, "Use this inside a valid ticket channel.", ephemeral=True); return None
    return t

async def claim_ticket(bot, interaction):
    await defer_if_needed(interaction)
    t = await require_staff(bot, interaction)
    if not t: return
    if t["claimed_by"] and t["claimed_by"] != interaction.user.id and not bot.is_owner(interaction.user):
        return await bot.safe_send(interaction, "This ticket is already claimed.", ephemeral=True)
    bot.store.exec("UPDATE tickets SET claimed_by=?, claim_at=? WHERE ticket_id=?", (interaction.user.id, iso(), t["ticket_id"]))
    await bot.refresh_ticket_message(interaction.channel)
    await bot.log_event("Ticket claimed", f"Claimed by {interaction.user.mention}", bot.ticket_by_channel(interaction.channel.id))
    await bot.safe_send(interaction, "Ticket claimed.", ephemeral=True)
async def unclaim_ticket(bot, interaction):
    await defer_if_needed(interaction)
    t = await require_staff(bot, interaction)
    if not t: return
    bot.store.exec("UPDATE tickets SET claimed_by=NULL, claim_at=NULL WHERE ticket_id=?", (t["ticket_id"],))
    await bot.refresh_ticket_message(interaction.channel)
    await bot.log_event("Ticket unclaimed", f"Unclaimed by {interaction.user.mention}", bot.ticket_by_channel(interaction.channel.id))
    await bot.safe_send(interaction, "Ticket unclaimed.", ephemeral=True)
async def set_pin_ticket(bot, interaction, desired: Optional[bool] = None):
    await defer_if_needed(interaction)
    t = await require_staff(bot, interaction)
    if not t: return
    new = (not bool(t["pinned"])) if desired is None else bool(desired)
    if bool(t["pinned"]) == new:
        return await bot.safe_send(interaction, "Ticket is already pinned." if new else "Ticket is already unpinned.", ephemeral=True)
    base = t["custom_channel_name"] or interaction.channel.name.removeprefix(PIN_PREFIX)
    name = (PIN_PREFIX + base) if new else base
    bot.store.exec("UPDATE tickets SET pinned=?, custom_channel_name=? WHERE ticket_id=?", (1 if new else 0, base, t["ticket_id"]))
    try: await interaction.channel.edit(name=name[:100], position=0 if new else None)
    except Exception as e: log.warning("pin reorder/rename failed: %s", e)
    await bot.refresh_ticket_message(interaction.channel)
    await bot.log_event("Ticket pinned" if new else "Ticket unpinned", f"By {interaction.user.mention}", bot.ticket_by_channel(interaction.channel.id))
    await bot.safe_send(interaction, "Pinned." if new else "Unpinned.", ephemeral=True)

async def pin_ticket(bot, interaction):
    await set_pin_ticket(bot, interaction, None)
async def rename_channel(bot, interaction, name):
    await defer_if_needed(interaction)
    t = await require_staff(bot, interaction)
    if not t: return
    clean = sanitize_name(name)
    final = (PIN_PREFIX if t["pinned"] else "") + clean
    old = interaction.channel.name
    await interaction.channel.edit(name=final[:100])
    bot.store.exec("UPDATE tickets SET custom_channel_name=? WHERE ticket_id=?", (clean, t["ticket_id"]))
    await bot.log_event("Ticket renamed", f"By {interaction.user.mention}\nOld: {old}\nNew: {final}", bot.ticket_by_channel(interaction.channel.id))
    await bot.safe_send(interaction, "Ticket renamed.", ephemeral=True)
async def user_action(bot, interaction, member, action):
    await defer_if_needed(interaction)
    t = await require_staff(bot, interaction)
    if not t: return
    added = set(filter(None, (t["added_users"] or "").split(",")))
    if action == "add":
        if str(member.id) in added: return await bot.safe_send(interaction, "User is already added.", ephemeral=True)
        added.add(str(member.id)); await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        msg = "User added"
    else:
        if member.id == t["opener_id"]: return await bot.safe_send(interaction, "You cannot remove the original opener.", ephemeral=True)
        if bot.is_staff(member): return await bot.safe_send(interaction, "You cannot remove staff or owner access.", ephemeral=True)
        if str(member.id) not in added: return await bot.safe_send(interaction, "That user was never added.", ephemeral=True)
        added.remove(str(member.id)); await interaction.channel.set_permissions(member, overwrite=None); msg = "User removed"
    bot.store.exec("UPDATE tickets SET added_users=? WHERE ticket_id=?", (",".join(sorted(added)), t["ticket_id"]))
    await bot.log_event(msg, f"{member.mention} by {interaction.user.mention}", bot.ticket_by_channel(interaction.channel.id))
    await bot.safe_send(interaction, msg + ".", ephemeral=True)

bot = TicketBot()

async def owner_check(interaction: discord.Interaction) -> bool:
    if not bot.allowed_guild(interaction.guild) or not isinstance(interaction.user, discord.Member) or not bot.is_owner(interaction.user):
        await bot.safe_send(interaction, "Only the configured ZeroX Host owner role can use this.", ephemeral=True); return False
    return True

@bot.tree.command(name="setup", guild=discord.Object(id=ALLOWED_GUILD_ID), description="Configure and validate the ZeroX Host ticket bot.")
@app_commands.check(owner_check)
async def setup_cmd(interaction: discord.Interaction):
    guild = interaction.guild; await interaction.response.defer(ephemeral=True, thinking=True)
    category_report = await bot.ensure_categories(guild)
    issues = []
    for label, getter in [("owner role", bot.owner_role), ("staff role", bot.staff_role), ("logs channel", bot.logs_channel), ("panel channel", bot.panel_channel)]:
        if not getter(guild): issues.append(f"Missing or invalid {label}")
    view = SetupView(bot)
    details = "\n".join(category_report) if category_report else "No category changes were needed."
    await interaction.followup.send("✅ Setup validation complete. " + ("Issues: " + "; ".join(issues) if issues else "All required Discord objects are valid.") + f"\n\n**Ticket category setup**\n{details}" + "\n\nUse the controls below to edit settings.", view=view, ephemeral=True)

class SetupView(discord.ui.View):
    def __init__(self, bot): super().__init__(timeout=600); self.bot=bot
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not self.bot.is_owner(interaction.user):
            await self.bot.safe_send(interaction, "Only the configured owner role can use setup controls.", ephemeral=True)
            return False
        return True
    @discord.ui.button(label="IDs", style=discord.ButtonStyle.primary)
    async def ids(self, i,b): await i.response.send_message("Choose roles and channels to save.", view=SetupSelectView(self.bot), ephemeral=True)
    @discord.ui.button(label="Panel Text", style=discord.ButtonStyle.secondary)
    async def panel(self,i,b): await i.response.send_modal(ConfigModal(self.bot, "Panel Text", [("title","panel.title"),("description","panel.description"),("color","panel.color"),("placeholder","panel.dropdown_placeholder"),("footer","panel.footer_text")]))
    @discord.ui.button(label="Panel Media", style=discord.ButtonStyle.secondary)
    async def media(self,i,b): await i.response.send_modal(ConfigModal(self.bot, "Panel Media", [("thumbnail_url","panel.thumbnail_url"),("image_url","panel.image_url"),("footer_icon_url","panel.footer_icon_url")]))
    @discord.ui.button(label="Tickets", style=discord.ButtonStyle.secondary)
    async def tickets(self,i,b): await i.response.send_modal(ConfigModal(self.bot, "Tickets", [("max_active","tickets.max_active_per_user"),("inactive_hours","tickets.inactivity_close_hours"),("delete_hours","tickets.closed_delete_hours"),("name_format","tickets.channel_name_format")]))
    @discord.ui.button(label="Categories", style=discord.ButtonStyle.success)
    async def cats(self,i,b): await i.response.send_modal(CategoryConfigModal(self.bot))

class SetupSelectView(discord.ui.View):
    def __init__(self, bot): super().__init__(timeout=300); self.bot=bot
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not self.bot.is_owner(interaction.user):
            await self.bot.safe_send(interaction, "Only the configured owner role can use setup controls.", ephemeral=True)
            return False
        return True
    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="Owner role", min_values=1, max_values=1)
    async def owner(self, i, select): self.bot.cfg["roles"]["owner_role_id"]=str(select.values[0].id); save_config(self.bot.cfg); await self.bot.safe_send(i,"Owner role saved.",ephemeral=True)
    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="Staff role", min_values=1, max_values=1)
    async def staff(self, i, select): self.bot.cfg["roles"]["staff_role_id"]=str(select.values[0].id); save_config(self.bot.cfg); await self.bot.safe_send(i,"Staff role saved.",ephemeral=True)
    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="Logs channel", channel_types=[discord.ChannelType.text], min_values=1, max_values=1)
    async def logs(self, i, select): self.bot.cfg["channels"]["logs_channel_id"]=str(select.values[0].id); save_config(self.bot.cfg); await self.bot.safe_send(i,"Logs channel saved.",ephemeral=True)
    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="Panel channel", channel_types=[discord.ChannelType.text], min_values=1, max_values=1)
    async def panel_ch(self, i, select): self.bot.cfg["channels"]["panel_channel_id"]=str(select.values[0].id); save_config(self.bot.cfg); await self.bot.safe_send(i,"Panel channel saved.",ephemeral=True)

class CategoryConfigModal(discord.ui.Modal, title="Ticket Categories"):
    def __init__(self, bot):
        super().__init__(timeout=300); self.bot=bot; self.inputs=[]
        for key in CATEGORY_KEYS:
            c = bot.cfg["categories"][key]
            item = discord.ui.TextInput(label=key, default=f"{c['name']}|{c.get('emoji','')}", required=True, max_length=150)
            self.inputs.append((key, item)); self.add_item(item)
    async def on_submit(self, interaction):
        for key, item in self.inputs:
            raw = str(item.value)
            name, emoji = raw.split("|", 1) if "|" in raw else (raw, "")
            self.bot.cfg["categories"][key]["name"] = name.strip() or self.bot.cfg["categories"][key]["name"]
            self.bot.cfg["categories"][key]["emoji"] = emoji.strip()
        save_config(self.bot.cfg)
        await interaction.response.defer(ephemeral=True, thinking=True)
        report = await self.bot.ensure_categories(interaction.guild)
        details = "\n".join(report) if report else "No category changes were needed."
        await self.bot.safe_send(interaction,"Category names/emojis saved and Discord categories validated. Use name|emoji in each field.\n\n" + details,ephemeral=True)
class ConfigModal(discord.ui.Modal):
    def __init__(self, bot, title, fields):
        super().__init__(title=title, timeout=300); self.bot=bot; self.fields=fields; self.inputs=[]
        for label,path in fields:
            cur=bot.cfg
            for p in path.split('.'): cur=cur[p]
            inp=discord.ui.TextInput(label=label, default=str(cur), required=False, style=discord.TextStyle.paragraph if "description" in path else discord.TextStyle.short, max_length=1000)
            self.inputs.append((path, inp)); self.add_item(inp)
    async def on_submit(self, interaction):
        for path, inp in self.inputs:
            cur=self.bot.cfg; parts=path.split('.')
            for p in parts[:-1]: cur=cur[p]
            val=str(inp.value)
            try:
                if parts[-1] in {"max_active_per_user"}:
                    val = max(1, int(val or 1))
                elif parts[-1].endswith("hours"):
                    val = max(0.1, float(val or 24))
            except ValueError:
                return await self.bot.safe_send(interaction, f"Invalid numeric value for {path}. Please enter only a number.", ephemeral=True)
            if parts[-1].endswith("url") and val and not re.match(r"^https?://", val):
                return await self.bot.safe_send(interaction, f"Invalid URL for {path}. URLs must start with http:// or https://.", ephemeral=True)
            cur[parts[-1]]=val
        save_config(self.bot.cfg); await self.bot.safe_send(interaction,"Settings saved.",ephemeral=True)

panel_group = app_commands.Group(name="panel", description="Manage the ZeroX Host ticket panel", guild_ids=[ALLOWED_GUILD_ID])
@panel_group.command(name="send", description="Send or replace the active ticket panel")
@app_commands.check(owner_check)
async def panel_send(interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    ch = bot.panel_channel(interaction.guild)
    if not ch: return await bot.safe_send(interaction, "Panel channel is invalid.", ephemeral=True)
    old_mid, old_cid = int_id(bot.cfg["panel_state"].get("message_id")), int_id(bot.cfg["panel_state"].get("channel_id"))
    if old_mid and old_cid:
        old_ch = interaction.guild.get_channel(old_cid)
        try:
            if old_ch: (await old_ch.fetch_message(old_mid)); return await bot.safe_send(interaction, "An active panel already exists. Use /panel update or /panel delete first.", ephemeral=True)
        except Exception: pass
    msg = await ch.send(embed=bot.panel_embed(), view=PanelView(bot))
    bot.cfg["panel_state"]={"message_id":str(msg.id),"channel_id":str(ch.id)}; save_config(bot.cfg)
    await bot.safe_send(interaction, "Panel sent and saved.", ephemeral=True)
@panel_group.command(name="update", description="Update the active ticket panel")
@app_commands.check(owner_check)
async def panel_update(interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    ch=interaction.guild.get_channel(int_id(bot.cfg["panel_state"].get("channel_id")) or 0)
    try: msg=await ch.fetch_message(int(bot.cfg["panel_state"].get("message_id") or 0)); await msg.edit(embed=bot.panel_embed(), view=PanelView(bot)); await bot.safe_send(interaction,"Panel updated.",ephemeral=True)
    except Exception: await bot.safe_send(interaction,"Saved panel message was not found. Use /panel send.",ephemeral=True)
@panel_group.command(name="delete", description="Delete the active ticket panel")
@app_commands.check(owner_check)
async def panel_delete(interaction): await bot.safe_send(interaction,"Confirm panel deletion.",view=ConfirmPanelDelete(),ephemeral=True)
class ConfirmPanelDelete(discord.ui.View):
    def __init__(self): super().__init__(timeout=120)
    @discord.ui.button(label="Confirm Delete Panel",style=discord.ButtonStyle.danger)
    async def yes(self,i,b):
        ch=i.guild.get_channel(int_id(bot.cfg["panel_state"].get("channel_id")) or 0)
        try:
            if ch: await (await ch.fetch_message(int(bot.cfg["panel_state"].get("message_id") or 0))).delete()
        except Exception: pass
        bot.cfg["panel_state"]={"message_id":"","channel_id":""}; save_config(bot.cfg); await bot.safe_send(i,"Panel deleted and state cleared.",ephemeral=True)
@panel_group.command(name="preview", description="Preview the current ticket panel privately")
@app_commands.check(owner_check)
async def panel_preview(interaction): await bot.safe_send(interaction, embed=bot.panel_embed(), view=PanelView(bot), ephemeral=True)
bot.tree.add_command(panel_group)

ticket_group = app_commands.Group(name="ticket", description="Manage ZeroX Host tickets", guild_ids=[ALLOWED_GUILD_ID])
@ticket_group.command(name="claim")
async def tc_claim(i): await i.response.defer(ephemeral=True, thinking=True); await claim_ticket(bot,i)
@ticket_group.command(name="unclaim")
async def tc_unclaim(i): await i.response.defer(ephemeral=True, thinking=True); await unclaim_ticket(bot,i)
@ticket_group.command(name="pin")
async def tc_pin(i): await i.response.defer(ephemeral=True, thinking=True); await set_pin_ticket(bot,i,True)
@ticket_group.command(name="unpin")
async def tc_unpin(i): await i.response.defer(ephemeral=True, thinking=True); await set_pin_ticket(bot,i,False)
@ticket_group.command(name="add")
async def tc_add(i, user: discord.Member): await i.response.defer(ephemeral=True, thinking=True); await user_action(bot,i,user,"add")
@ticket_group.command(name="remove")
async def tc_remove(i, user: discord.Member): await i.response.defer(ephemeral=True, thinking=True); await user_action(bot,i,user,"remove")
@ticket_group.command(name="rename")
async def tc_rename(i, name: str): await i.response.defer(ephemeral=True, thinking=True); await rename_channel(bot,i,name)
@ticket_group.command(name="close")
async def tc_close(i): await i.response.send_modal(CloseModal(bot)) if await require_staff(bot,i) else None
@ticket_group.command(name="delete")
async def tc_delete(i): await bot.safe_send(i,"Confirm ticket deletion.",view=ConfirmDeleteView(bot),ephemeral=True) if await require_staff(bot,i) else None
@ticket_group.command(name="info")
async def tc_info(i):
    t=await require_staff(bot,i)
    if t: await bot.safe_send(i,embed=bot.ticket_embed(t),ephemeral=True)
@ticket_group.command(name="transcript")
async def tc_transcript(i):
    t=await require_staff(bot,i)
    if t:
        await i.response.defer(ephemeral=True)
        ok=await bot.ensure_transcript(i.channel,t,"Transcript generated by command")
        await i.followup.send("Transcript uploaded." if ok else "Transcript upload failed.",ephemeral=True)
bot.tree.add_command(ticket_group)

@bot.tree.command(name="reopen", guild=discord.Object(id=ALLOWED_GUILD_ID), description="Reopen the current closed ticket.")
async def reopen_cmd(interaction: discord.Interaction, reason: str = "Ticket reopened"):
    t = await require_staff(bot, interaction)
    if not t:
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    if t["status"] != "closed":
        return await bot.safe_send(interaction, "Only closed tickets can be reopened.", ephemeral=True)
    ok = await bot.reopen_ticket(interaction.channel, interaction.user, reason)
    await bot.safe_send(interaction, "Ticket reopened." if ok else "Unable to reopen this ticket.", ephemeral=True)

@bot.event
async def on_ready(): log.info("Logged in as %s", bot.user)

if __name__ == "__main__":
    token = bot.cfg.get("bot", {}).get("token")
    if not token:
        raise SystemExit("Set bot.token in config.yml before running.")
    bot.run(token)
