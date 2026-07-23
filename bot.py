import asyncio
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

CONFIG_PATH = Path("config.json")
DB_PATH = Path("data/zerox_host_db.json")
SQLITE_PATH = Path("data/zerox_host.db")
DEVELOPER = "ekamsidhu07 ekam"
BRAND = "ZeroX Host"
PAID_LOG_CHANNEL_ID = 1504092779700289536
FREE_LOG_CHANNEL_ID = 1529782327348170873
ADMIN_LOG_CHANNEL_ID = 1504092779700289536
ADMIN_ROLE_ID = 1504092228778459226
OWNER_ROLE_ID = 1504092176492265553
PANEL_URL = "https://gp.zeroxhost.space"
DEFAULT_NODE_STATUS_WEBHOOK_URL = "https://discord.com/api/webhooks/1529670294250328164/IphtUOnIeUURVI1tJSVlHek12qCegKHJZ5k8Gy96J7GLTOFIb_IDggK2WWnfRP6MBGOT"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(default, indent=2), encoding="utf-8")
        return default
    return json.loads(path.read_text(encoding="utf-8"))


config = load_json(CONFIG_PATH, {})
database = load_json(DB_PATH, {"servers": {}, "users": {}, "whitelist": []})


def saga_auto_suspend_enabled() -> bool:
    value = config.get("saga_auto_suspend_enabled", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def saga_expiration_fields() -> list[str]:
    configured = config.get("saga_auto_suspend_fields") or config.get("saga_auto_suspend_field") or "suspended_at"
    fields = configured if isinstance(configured, list) else [configured]
    for fallback in ("suspended_at", "expiration_date", "expires_at"):
        if fallback not in fields:
            fields.append(fallback)
    return [str(field) for field in fields if str(field).strip()]


def format_saga_expiration(expires_at: datetime | None) -> str | None:
    if expires_at is None:
        return None
    return expires_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def save_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_text(json.dumps(database, indent=2), encoding="utf-8")


def panel_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "suspended"}
    return bool(value)


def panel_server_is_suspended(panel_server: dict[str, Any]) -> bool:
    status_values = {
        str(panel_server.get("status", "")).strip().lower(),
        str(panel_server.get("state", "")).strip().lower(),
        str((panel_server.get("container") or {}).get("status", "")).strip().lower(),
    }
    return (
        panel_bool(panel_server.get("suspended", False))
        or panel_bool(panel_server.get("is_suspended", False))
        or bool(panel_server.get("suspended_at"))
        or "suspended" in status_values
    )


def db() -> sqlite3.Connection:
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(SQLITE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with db() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS links (
            discord_user_id TEXT PRIMARY KEY,
            panel_user_id INTEGER NOT NULL,
            email TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS servers (
            server_id TEXT PRIMARY KEY,
            identifier TEXT,
            uuid TEXT,
            name TEXT NOT NULL,
            plan TEXT NOT NULL,
            discord_user_id TEXT NOT NULL,
            panel_user_id INTEGER NOT NULL,
            panel_email TEXT NOT NULL,
            ram INTEGER NOT NULL,
            disk INTEGER NOT NULL,
            cpu INTEGER NOT NULL,
            nest_id INTEGER NOT NULL,
            nest_name TEXT NOT NULL,
            egg_id INTEGER NOT NULL,
            egg_name TEXT NOT NULL,
            node_id INTEGER NOT NULL,
            node_name TEXT NOT NULL,
            databases INTEGER NOT NULL,
            allocations INTEGER NOT NULL,
            backups INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            suspended INTEGER NOT NULL DEFAULT 0,
            deleted INTEGER NOT NULL DEFAULT 0,
            autosuspend_enabled INTEGER NOT NULL DEFAULT 1,
            autosuspend_seconds INTEGER
        );
        CREATE TABLE IF NOT EXISTS whitelist (server_id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS autobackups (
            server_id TEXT PRIMARY KEY,
            interval_seconds INTEGER NOT NULL,
            next_run_at TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS scheduled_restarts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL,
            discord_user_id TEXT NOT NULL,
            interval_seconds INTEGER NOT NULL,
            next_run_at TEXT NOT NULL,
            all_servers INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS server_notifications (
            server_id TEXT NOT NULL,
            notification_type TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            PRIMARY KEY (server_id, notification_type)
        );
        CREATE TABLE IF NOT EXISTS node_status (
            node_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            summary TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            down_since TEXT
        );
        """)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(servers)").fetchall()}
        if "autosuspend_enabled" not in columns:
            connection.execute("ALTER TABLE servers ADD COLUMN autosuspend_enabled INTEGER NOT NULL DEFAULT 1")
        if "autosuspend_seconds" not in columns:
            connection.execute("ALTER TABLE servers ADD COLUMN autosuspend_seconds INTEGER")
        node_status_columns = {row[1] for row in connection.execute("PRAGMA table_info(node_status)").fetchall()}
        if "down_since" not in node_status_columns:
            connection.execute("ALTER TABLE node_status ADD COLUMN down_since TEXT")
        # Legacy databases may contain older link columns, but new installs use one panel only.


def upsert_server_record(record: dict[str, Any]) -> None:
    with db() as connection:
        connection.execute("""
        INSERT OR REPLACE INTO servers (server_id, identifier, uuid, name, plan, discord_user_id, panel_user_id, panel_email, ram, disk, cpu, nest_id, nest_name, egg_id, egg_name, node_id, node_name, databases, allocations, backups, created_at, expires_at, suspended, deleted, autosuspend_enabled, autosuspend_seconds)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (record["server_id"], record.get("identifier"), record.get("uuid"), record["name"], record["plan"], record["discord_user_id"], record["panel_user_id"], record["panel_email"], record["ram"], record["disk"], record["cpu"], record["nest_id"], record["nest_name"], record["egg_id"], record["egg_name"], record["node_id"], record["node_name"], record["databases"], record["allocations"], record["backups"], record["created_at"], record["expires_at"], int(record.get("suspended", False)), int(record.get("deleted", False)), int(record.get("autosuspend_enabled", True)), record.get("autosuspend_seconds")))


def fetch_server(server_id: str) -> sqlite3.Row | None:
    with db() as connection:
        return connection.execute("SELECT * FROM servers WHERE server_id = ?", (server_id,)).fetchone()


def fetch_user_servers(discord_user_id: int) -> list[sqlite3.Row]:
    with db() as connection:
        return connection.execute("SELECT * FROM servers WHERE discord_user_id = ? AND deleted = 0 ORDER BY created_at DESC", (str(discord_user_id),)).fetchall()


def remove_whitelist_entries(values: set[str]) -> None:
    """Remove deleted server identifiers from both whitelist stores."""
    if not values:
        return
    database["whitelist"] = sorted({str(item) for item in database.setdefault("whitelist", [])} - values, key=str)
    with db() as connection:
        connection.executemany("DELETE FROM whitelist WHERE server_id=?", [(value,) for value in values])


def mark_server_deleted(server_id: str) -> None:
    record = fetch_server(server_id) or database.get("servers", {}).get(str(server_id), {})
    identifiers = {
        str(server_id),
        str(record_value(record, "uuid", "")),
        str(record_value(record, "identifier", "")),
    }
    with db() as connection:
        connection.execute("UPDATE servers SET deleted = 1 WHERE server_id = ?", (str(server_id),))
    if str(server_id) in database.get("servers", {}):
        database["servers"][str(server_id)]["deleted"] = True
    remove_whitelist_entries({identifier for identifier in identifiers if identifier})
    save_database()


def update_tracked_server_from_panel(server_id: str, panel_server: dict[str, Any], panel_email: str | None = None) -> None:
    limits = panel_server.get("limits") or {}
    feature_limits = panel_server.get("feature_limits") or {}
    updates = {
        "identifier": panel_server.get("identifier"),
        "uuid": panel_server.get("uuid"),
        "name": panel_server.get("name", f"Server {server_id}"),
        "panel_user_id": int(panel_server.get("user") or 0),
        "panel_email": panel_email,
        "ram": int(limits.get("memory") or 0),
        "disk": int(limits.get("disk") or 0),
        "cpu": int(limits.get("cpu") or 0),
        "databases": int(feature_limits.get("databases") or 0),
        "allocations": int(feature_limits.get("allocations") or 0),
        "backups": int(feature_limits.get("backups") or 0),
        "suspended": int(panel_server_is_suspended(panel_server)),
        "deleted": 0,
    }
    with db() as connection:
        connection.execute(
            """
            UPDATE servers
            SET identifier=?, uuid=?, name=?, panel_user_id=?, panel_email=COALESCE(?, panel_email), ram=?, disk=?, cpu=?, databases=?, allocations=?, backups=?, suspended=?, deleted=?
            WHERE server_id=?
            """,
            (updates["identifier"], updates["uuid"], updates["name"], updates["panel_user_id"], updates["panel_email"], updates["ram"], updates["disk"], updates["cpu"], updates["databases"], updates["allocations"], updates["backups"], updates["suspended"], updates["deleted"], str(server_id)),
        )
    if str(server_id) in database.get("servers", {}):
        mirror_updates = {key: value for key, value in updates.items() if value is not None}
        database["servers"][str(server_id)].update(mirror_updates)
        save_database()


def fetch_all_servers() -> list[sqlite3.Row]:
    with db() as connection:
        return connection.execute("SELECT * FROM servers WHERE deleted = 0 ORDER BY created_at DESC").fetchall()


def whitelist_values() -> set[str]:
    values = {str(item) for item in database.setdefault("whitelist", [])}
    with db() as connection:
        values.update(str(row["server_id"]) for row in connection.execute("SELECT server_id FROM whitelist").fetchall())
    return values


def is_whitelisted(server_id: str) -> bool:
    return str(server_id) in whitelist_values()


def server_identifiers(record: sqlite3.Row | dict[str, Any] | None, server_id: str | None = None) -> set[str]:
    identifiers = {str(server_id or "")}
    if record:
        identifiers.update({
            str(record_value(record, "server_id", "")),
            str(record_value(record, "uuid", "")),
            str(record_value(record, "identifier", "")),
        })
    return {identifier for identifier in identifiers if identifier}


def is_server_protected(record: sqlite3.Row | dict[str, Any]) -> bool:
    if str(record_value(record, "plan", "")).lower() == "paid":
        return True
    protected = whitelist_values()
    return bool(server_identifiers(record) & protected)


def fetch_link(discord_user_id: int) -> sqlite3.Row | None:
    with db() as connection:
        row = connection.execute("SELECT * FROM links WHERE discord_user_id = ?", (str(discord_user_id),)).fetchone()
    return row if row and int(row["panel_user_id"] or 0) > 0 and str(row["email"] or "").strip() else None


def fetch_links_by_panel_user() -> dict[int, sqlite3.Row]:
    with db() as connection:
        rows = connection.execute("SELECT * FROM links").fetchall()
    return {int(row["panel_user_id"]): row for row in rows if int(row["panel_user_id"] or 0) > 0}


def fetch_link_by_panel_user(panel_user_id: int) -> sqlite3.Row | None:
    with db() as connection:
        return connection.execute("SELECT * FROM links WHERE panel_user_id = ?", (int(panel_user_id),)).fetchone()


def unlink_discord_user(discord_user_id: int) -> sqlite3.Row | None:
    existing = fetch_link(discord_user_id)
    if existing:
        with db() as connection:
            connection.execute("DELETE FROM links WHERE discord_user_id = ?", (str(discord_user_id),))
    return existing




def fetch_node_status(node_id: str) -> sqlite3.Row | None:
    with db() as connection:
        return connection.execute("SELECT * FROM node_status WHERE node_id=?", (str(node_id),)).fetchone()


def upsert_node_status(node_id: str, state: str, summary: str, down_since: str | None = None) -> None:
    with db() as connection:
        connection.execute(
            """
            INSERT INTO node_status(node_id, state, summary, updated_at, down_since) VALUES (?,?,?,?,?)
            ON CONFLICT(node_id) DO UPDATE SET state=excluded.state, summary=excluded.summary, updated_at=excluded.updated_at, down_since=excluded.down_since
            """,
            (str(node_id), state, summary, utc_now().isoformat(), down_since),
        )


def node_status_webhook_url() -> str | None:
    value = str(config.get("node_status_webhook_url") or DEFAULT_NODE_STATUS_WEBHOOK_URL or "").strip()
    if value.lower() in {"", "none", "null", "false", "0"}:
        return None
    return value


def notification_sent(server_id: str, notification_type: str) -> bool:
    with db() as connection:
        return connection.execute("SELECT 1 FROM server_notifications WHERE server_id=? AND notification_type=?", (server_id, notification_type)).fetchone() is not None


def mark_notification_sent(server_id: str, notification_type: str) -> None:
    with db() as connection:
        connection.execute("INSERT OR IGNORE INTO server_notifications(server_id, notification_type, sent_at) VALUES (?,?,?)", (server_id, notification_type, utc_now().isoformat()))


def clear_server_notifications(server_id: str) -> None:
    with db() as connection:
        connection.execute("DELETE FROM server_notifications WHERE server_id=?", (server_id,))




def discord_owner_label(record: sqlite3.Row | dict[str, Any], user: discord.User | None = None) -> str:
    discord_id = str(record_value(record, "discord_user_id", "")).strip()
    if not discord_id:
        return "Unknown"
    if user:
        return f"{user.mention} (`{discord_id}`)"
    return f"<@{discord_id}> (`{discord_id}`)"


def server_admin_details(record: sqlite3.Row | dict[str, Any], *, user: discord.User | None = None, event_when: datetime | None = None, reason: str | None = None) -> str:
    created_at_raw = str(record_value(record, "created_at", "")).strip()
    expires_at_raw = str(record_value(record, "expires_at", "")).strip()
    created_line = clean(created_at_raw) or "Unknown"
    expires_line = clean(expires_at_raw) or "Unknown"
    try:
        created_line = f"<t:{int(datetime.fromisoformat(created_at_raw).timestamp())}:F>"
    except (TypeError, ValueError):
        pass
    try:
        expires_line = f"<t:{int(datetime.fromisoformat(expires_at_raw).timestamp())}:F>"
    except (TypeError, ValueError):
        pass
    event_line = ""
    if event_when is not None:
        event_line = f"\nEvent time: <t:{int(event_when.timestamp())}:F>"
    reason_line = f"\nReason: {clean(reason, 300)}" if reason else ""
    return (
        f"Server: **{clean(str(record_value(record, 'name', 'Unknown')), 120)}** (`{record_value(record, 'server_id', 'unknown')}`)\n"
        f"UUID: `{record_value(record, 'uuid', 'unknown') or 'unknown'}`\n"
        f"Identifier: `{record_value(record, 'identifier', 'unknown') or 'unknown'}`\n"
        f"Plan: **{clean(str(record_value(record, 'plan', 'unknown')).title())}**\n"
        f"Owner Discord: {discord_owner_label(record, user)}\n"
        f"Owner Email: `{clean(str(record_value(record, 'panel_email', 'unknown')), 120)}`\n"
        f"Panel User ID: `{record_value(record, 'panel_user_id', 'unknown')}`\n"
        f"Created: {created_line}\n"
        f"Suspension/Expiry: {expires_line}{event_line}{reason_line}\n"
        f"Node: **{clean(str(record_value(record, 'node_name', 'Unknown')), 80)}** (`{record_value(record, 'node_id', 'unknown')}`)\n"
        f"Nest: **{clean(str(record_value(record, 'nest_name', 'Unknown')), 80)}** (`{record_value(record, 'nest_id', 'unknown')}`)\n"
        f"Egg: **{clean(str(record_value(record, 'egg_name', 'Unknown')), 80)}** (`{record_value(record, 'egg_id', 'unknown')}`)\n"
        f"Specs: RAM `{record_value(record, 'ram', 0)} MB` / Disk `{record_value(record, 'disk', 0)} MB` / CPU `{record_value(record, 'cpu', 0)}%`\n"
        f"Extras: DB `{record_value(record, 'databases', 0)}` / Alloc `{record_value(record, 'allocations', 0)}` / Backups `{record_value(record, 'backups', 0)}`\n"
        f"State: suspended=`{bool(record_value(record, 'suspended', False))}` deleted=`{bool(record_value(record, 'deleted', False))}` autosuspend=`{bool(record_value(record, 'autosuspend_enabled', True))}`"
    )


def fetch_servers_by_email(email: str) -> list[sqlite3.Row]:
    with db() as connection:
        return connection.execute("SELECT * FROM servers WHERE panel_email = ? AND deleted = 0 ORDER BY created_at DESC", (email,)).fetchall()


init_db()


def require_config() -> None:
    required = ["discord_token", "guild_id", "panel_url", "panel_api_key", "admin_role_ids", "client_api_key"]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise RuntimeError(f"Missing config.json values: {', '.join(missing)}")


class PterodactylClient:
    def __init__(self, panel_url: str, api_key: str) -> None:
        self.panel_url = panel_url.rstrip("/")
        self.api_key = api_key
        self.session: aiohttp.ClientSession | None = None
        self.node_cache: list[dict[str, Any]] = []
        self.server_cache: list[dict[str, Any]] = []
        self.nest_cache: list[dict[str, Any]] = []
        self.egg_cache: dict[int, list[dict[str, Any]]] = {}
        self.user_cache: dict[int, dict[str, Any]] = {}

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(headers={
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    async def request(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.session:
            raise RuntimeError("Pterodactyl client is not started")
        url = f"{self.panel_url}/api/application/{endpoint.lstrip('/')}"
        async with self.session.request(method, url, json=payload) as response:
            if response.status == 204:
                return {}
            data = await response.json(content_type=None)
            if response.status >= 400:
                detail = data.get("errors", [{}])[0].get("detail", data)
                raise RuntimeError(f"Pterodactyl API error {response.status}: {detail}")
            return data

    async def get_user(self, user_id: int, *, refresh: bool = False) -> dict[str, Any] | None:
        if not refresh and user_id in self.user_cache:
            return self.user_cache[user_id]
        try:
            data = await self.request("GET", f"users/{user_id}")
        except RuntimeError:
            return None
        self.user_cache[user_id] = data["attributes"]
        return self.user_cache[user_id]

    async def list_paginated(self, endpoint: str) -> list[dict[str, Any]]:
        separator = "&" if "?" in endpoint else "?"
        page = 1
        items: list[dict[str, Any]] = []
        while True:
            data = await self.request("GET", f"{endpoint}{separator}per_page=100&page={page}")
            items.extend(item["attributes"] for item in data.get("data", []))
            pagination = data.get("meta", {}).get("pagination", {})
            total_pages = int(pagination.get("total_pages") or page)
            if page >= total_pages:
                return items
            page += 1

    async def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        data = await self.request("GET", f"users?filter[email]={email}")
        users = data.get("data", [])
        return users[0]["attributes"] if users else None

    async def get_required_panel_user(self, email: str) -> dict[str, Any]:
        user = await self.find_user_by_email(email)
        if not user:
            raise RuntimeError("No Pterodactyl user exists with that email. Create the panel user first, then retry.")
        return user

    async def create_user(self, *, email: str, username: str, first_name: str, last_name: str, password: str) -> dict[str, Any]:
        """Create a standard, non-admin Pterodactyl account on this client panel."""
        payload = {
            "email": email,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "password": password,
            "root_admin": False,
            "language": "en",
        }
        data = await self.request("POST", "users", payload)
        return data["attributes"]

    async def get_egg(self, nest_id: int, egg_id: int) -> dict[str, Any]:
        data = await self.request("GET", f"nests/{nest_id}/eggs/{egg_id}?include=variables")
        return data["attributes"]

    async def egg_environment(self, nest_id: int, egg_id: int) -> dict[str, str]:
        data = await self.request("GET", f"nests/{nest_id}/eggs/{egg_id}?include=variables")
        relationships = data.get("attributes", {}).get("relationships") or data.get("relationships") or {}
        variables = relationships.get("variables", {}).get("data", [])
        environment: dict[str, str] = {}
        for variable in variables:
            attributes = variable.get("attributes", {})
            env_name = attributes.get("env_variable")
            if env_name:
                environment[env_name] = str(attributes.get("default_value") or "")
        return environment

    def egg_docker_image(self, egg: dict[str, Any]) -> str | None:
        docker_images = egg.get("docker_images") or {}
        if isinstance(docker_images, dict):
            return egg.get("docker_image") or next(iter(docker_images.values()), None)
        if isinstance(docker_images, list):
            return egg.get("docker_image") or next(iter(docker_images), None)
        return egg.get("docker_image")

    async def change_server_egg(self, server_id: str, nest_id: int, egg_id: int, *, skip_scripts: bool = False) -> dict[str, Any]:
        egg = await self.get_egg(nest_id, egg_id)
        docker_image = self.egg_docker_image(egg)
        startup = egg.get("startup")
        if not docker_image or not startup:
            raise RuntimeError("Selected egg is missing a startup command or Docker image on the panel.")
        payload = {
            "startup": startup,
            "environment": await self.egg_environment(nest_id, egg_id),
            "egg": egg_id,
            "image": docker_image,
            "skip_scripts": skip_scripts,
        }
        data = await self.request("PATCH", f"servers/{server_id}/startup", payload)
        return data.get("attributes", {})

    async def create_server(self, *, panel_user_id: int, name: str, ram: int, disk: int, cpu: int, node_id: int, nest_id: int, egg_id: int, databases: int, allocations: int, backups: int) -> dict[str, Any]:
        egg = await self.get_egg(nest_id, egg_id)
        docker_image = self.egg_docker_image(egg)
        startup = egg.get("startup")
        if not docker_image or not startup:
            raise RuntimeError("Selected egg is missing a startup command or Docker image on the panel.")
        allocation_id = await self.get_free_allocation(node_id)
        payload = {
            "name": name,
            "user": panel_user_id,
            "egg": egg_id,
            "docker_image": docker_image,
            "startup": startup,
            "environment": await self.egg_environment(nest_id, egg_id),
            "limits": {"memory": ram, "swap": 0, "disk": disk, "io": 500, "cpu": cpu},
            "feature_limits": {"databases": databases, "allocations": allocations, "backups": backups},
            "allocation": {"default": allocation_id},
            "start_on_completion": True,
        }
        data = await self.request("POST", "servers", payload)
        return data["attributes"]

    async def list_nests(self) -> list[dict[str, Any]]:
        self.nest_cache = await self.list_paginated("nests")
        return self.nest_cache

    async def list_eggs(self, nest_id: int) -> list[dict[str, Any]]:
        eggs = await self.list_paginated(f"nests/{nest_id}/eggs")
        self.egg_cache[nest_id] = eggs
        return eggs

    async def list_nodes(self) -> list[dict[str, Any]]:
        self.node_cache = await self.list_paginated("nodes")
        return self.node_cache

    async def list_servers(self) -> list[dict[str, Any]]:
        self.server_cache = await self.list_paginated("servers")
        return self.server_cache

    async def get_server(self, server_id: str) -> dict[str, Any]:
        data = await self.request("GET", f"servers/{server_id}")
        return data["attributes"]

    async def get_free_allocation(self, node_id: int) -> int:
        data = await self.request("GET", f"nodes/{node_id}/allocations?per_page=100")
        for item in data.get("data", []):
            attributes = item.get("attributes", {})
            if not attributes.get("assigned"):
                return int(attributes["id"])
        raise RuntimeError(f"No free allocations were found on node {node_id}.")

    async def delete_server(self, server_id: str) -> None:
        await self.request("DELETE", f"servers/{server_id}")

    async def suspend_server(self, server_id: str) -> None:
        await self.request("POST", f"servers/{server_id}/suspend")

    async def unsuspend_server(self, server_id: str) -> None:
        await self.request("POST", f"servers/{server_id}/unsuspend")

    async def reinstall_server(self, server_id: str) -> None:
        await self.request("POST", f"servers/{server_id}/reinstall")

    async def server_build_payload(self, server_id: str, **overrides: Any) -> dict[str, Any]:
        server = await self.get_server(server_id)
        limits = server.get("limits") or {}
        feature_limits = server.get("feature_limits") or {}
        allocation = server.get("allocation") or server.get("allocation_id")
        if not allocation:
            raise RuntimeError("Pterodactyl did not return a primary allocation for this server, so the build cannot be updated safely.")
        payload = {
            "allocation": allocation,
            "memory": int(limits.get("memory") or 0),
            "swap": int(limits.get("swap") or 0),
            "disk": int(limits.get("disk") or 0),
            "io": int(limits.get("io") or 500),
            "cpu": int(limits.get("cpu") or 0),
            "threads": limits.get("threads"),
            "feature_limits": {
                "databases": int(feature_limits.get("databases") or 0),
                "allocations": int(feature_limits.get("allocations") or 0),
                "backups": int(feature_limits.get("backups") or 0),
            },
        }
        payload.update(overrides)
        return payload

    async def resize_server(self, server_id: str, ram: int, disk: int, cpu: int, databases: int, allocations: int, backups: int) -> None:
        payload = await self.server_build_payload(
            server_id,
            memory=ram,
            disk=disk,
            cpu=cpu,
            feature_limits={"databases": databases, "allocations": allocations, "backups": backups},
        )
        await self.request("PATCH", f"servers/{server_id}/build", payload)

    async def set_saga_auto_suspend(self, server_id: str, expires_at: datetime | None) -> bool:
        if not saga_auto_suspend_enabled():
            return False
        value = format_saga_expiration(expires_at)
        last_error: RuntimeError | None = None
        for field in saga_expiration_fields():
            try:
                await self.request("PATCH", f"servers/{server_id}/build", await self.server_build_payload(server_id, **{field: value}))
                return True
            except RuntimeError as error:
                last_error = error
        if last_error:
            print(f"Failed to sync Saga auto suspension for server {server_id}: {last_error}")
        return False


class PterodactylClientApi:
    def __init__(self, panel_url: str, api_key: str) -> None:
        self.panel_url = panel_url.rstrip("/")
        self.api_key = api_key
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(headers={
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    async def request(self, method: str, identifier: str, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.session:
            raise RuntimeError("Pterodactyl client API is not started")
        url = f"{self.panel_url}/api/client/servers/{identifier}/{endpoint.lstrip('/')}"
        async with self.session.request(method, url, json=payload) as response:
            if response.status == 204:
                return {}
            data = await response.json(content_type=None)
            if response.status >= 400:
                detail = data.get("errors", [{}])[0].get("detail", data)
                raise RuntimeError(f"Pterodactyl Client API error {response.status}: {detail}")
            return data

    async def power(self, identifier: str, signal: str) -> None:
        await self.request("POST", identifier, "power", {"signal": signal})

    async def reinstall(self, identifier: str) -> None:
        await self.request("POST", identifier, "settings/reinstall")

    async def backup(self, identifier: str, name: str) -> None:
        await self.request("POST", identifier, "backups", {"name": name})

    async def resources(self, identifier: str) -> dict[str, Any]:
        data = await self.request("GET", identifier, "resources")
        return data.get("attributes", {})

    async def command(self, identifier: str, command: str) -> None:
        await self.request("POST", identifier, "command", {"command": command})

    async def rename(self, identifier: str, name: str) -> None:
        await self.request("POST", identifier, "settings/rename", {"name": name})


intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
ptero = PterodactylClient(config.get("panel_url", PANEL_URL), config.get("panel_api_key", ""))
client_api = PterodactylClientApi(config.get("panel_url", PANEL_URL), config.get("client_api_key", ""))
def client_api_for_record(record: sqlite3.Row | dict[str, Any]) -> PterodactylClientApi:
    """Return the single configured panel client API for all server plans."""
    return client_api


async def ready_client_api_for(record: sqlite3.Row | dict[str, Any]) -> PterodactylClientApi:
    """Start the selected panel client on demand before a client-API request."""
    api = client_api_for_record(record)
    if not api.session:
        await api.start()
    return api



def int_set(values: list[Any]) -> set[int]:
    result: set[int] = set()
    for value in values:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result

def is_admin(member: discord.abc.User) -> bool:
    owner_ids = int_set(config.get("owner_ids", []))
    if member.id in owner_ids:
        return True
    if not isinstance(member, discord.Member):
        return False
    admin_roles = {ADMIN_ROLE_ID, OWNER_ROLE_ID, *int_set(config.get("admin_role_ids", []))}
    return any(role.id in admin_roles for role in member.roles)


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            raise app_commands.CheckFailure("Admin commands only work inside the ZeroX Host Discord server, not in DMs.")
        if is_admin(interaction.user):
            return True
        raise app_commands.CheckFailure("Only ZeroX Host admins can use this command.")
    return app_commands.check(predicate)


def parse_id(value: str) -> int:
    return int(value.split(":", 1)[0])


def clean(text: str, limit: int = 80) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def matches_purge_skip_keyword(server_name: str, keyword: str | None) -> bool:
    if not keyword:
        return False
    normalized = keyword.strip().lower()
    if not normalized:
        return False
    core = normalized.strip("[]")
    prefixes = {normalized, core, f"[{core}]"}
    return server_name.strip().lower().startswith(tuple(prefix for prefix in prefixes if prefix))


def branded_embed(title: str, description: str, color: int = 0x00d4ff) -> discord.Embed:
    embed = discord.Embed(title=f"✨ {title}", description=description, color=color, timestamp=utc_now())
    if config.get("brand_icon_url"):
        embed.set_author(name=BRAND, icon_url=config["brand_icon_url"])
    else:
        embed.set_author(name=BRAND)
    embed.set_footer(text=f"{BRAND} • Developer: {DEVELOPER}")
    return embed




def plan_log_channel_id(plan: str) -> int:
    if str(plan).strip().lower() == "paid":
        return int(config.get("paid_log_channel_id", PAID_LOG_CHANNEL_ID))
    return int(config.get("free_log_channel_id", FREE_LOG_CHANNEL_ID))


async def send_plan_log(plan: str, embed: discord.Embed) -> bool:
    try:
        channel_id = plan_log_channel_id(plan)
        channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
        await channel.send(embed=embed)
        return True
    except Exception as error:
        print(f"Failed to send {plan} server log: {error}")
        return False


async def send_server_event_log(record: sqlite3.Row | dict[str, Any], title: str, description: str, *, actor: discord.abc.User | None = None, color: int = 0x7c3aed) -> bool:
    """Send server lifecycle/admin logs to the channel that matches the server plan."""
    actor_line = f"\nAdmin: {actor.mention} (`{actor.id}`)" if actor else ""
    plan = str(record_value(record, "plan", "free")).strip().lower()
    embed = branded_embed(f"Admin Log: {title}", f"{description}{actor_line}", color)
    return await send_plan_log(plan, embed)


async def send_admin_audit(title: str, description: str, *, actor: discord.abc.User | None = None, color: int = 0x7c3aed) -> None:
    channel_id_value = config.get("admin_log_channel_id") or config.get("paid_log_channel_id", ADMIN_LOG_CHANNEL_ID)
    if not channel_id_value:
        return
    try:
        channel = client.get_channel(int(channel_id_value)) or await client.fetch_channel(int(channel_id_value))
        actor_line = f"\nAdmin: {actor.mention} (`{actor.id}`)" if actor else ""
        embed = branded_embed(f"🛡️ Audit • {title}", f"{description}{actor_line}", color)
        await channel.send(embed=embed)
    except Exception as error:
        print(f"Failed to send admin audit log '{title}': {error}")


def about_embed() -> discord.Embed:
    embed = branded_embed(
        "Pterodactyl Manager",
        "An advanced Discord bot for managing Pterodactyl hosting services through a beautiful, permission-controlled Discord interface.",
        0x00d4ff,
    )
    embed.add_field(name="👥 User Management", value="Panel account creation • Safe link/unlink • Secure credential DMs • Owner assignment", inline=False)
    embed.add_field(name="🖥️ Server Management", value="Free/paid/custom server creation • Power controls • Suspend/unsuspend/delete • Resize in GB • Egg changes", inline=False)
    embed.add_field(name="📦 Plans & Custom Builds", value="Premade free/paid flows plus custom CPU, RAM, disk, allocations, backups, nodes, nests, and eggs.", inline=False)
    embed.add_field(name="📜 Logging & Audit", value="Admin-only audit logs for creation, deletion, suspension, renewal, resize, and lifecycle events.", inline=False)
    embed.add_field(name="🧾 Receipts & Customer DMs", value="Styled customer DMs for creation, credentials, renewal reminders, expirations, and Trustpilot review prompts.", inline=False)
    embed.add_field(name="📡 Node Monitoring", value="Live node dashboard • webhook alerts • offline/online detection • overload/crash warnings • downtime tracking", inline=False)
    embed.add_field(name="⏰ Expiration System", value="Automatic reminders, Saga sync, suspension after expiry, cleanup warnings, and deletion after the grace period.", inline=False)
    embed.add_field(name="🔐 Permissions", value="Admin commands are role/owner protected; user commands only expose safe owned-server actions.", inline=False)
    return embed


def specs_embed(plan: str, name: str, ram: int, disk: int, cpu: int, node_name: str, nest: str, egg: str, expires_at: datetime, databases: int, allocations: int, backups: int) -> discord.Embed:
    panel_url = config.get("panel_url", PANEL_URL)
    embed = branded_embed(f"Your {BRAND} {plan.title()} Server Is Ready", f"Panel: **{panel_url.rstrip('/')}**")
    embed.add_field(name="🖥️ Server", value=name, inline=True)
    embed.add_field(name="🪺 Nest", value=nest, inline=True)
    embed.add_field(name="🥚 Egg", value=egg, inline=True)
    embed.add_field(name="🌐 Node", value=node_name, inline=True)
    embed.add_field(name="⚙️ Specs", value=f"RAM: **{ram} MB**\nDisk: **{disk} MB**\nCPU: **{cpu}%**", inline=True)
    embed.add_field(name="📦 Extras", value=f"DB: **{databases}**\nAlloc: **{allocations}**\nBackups: **{backups}**", inline=True)
    embed.add_field(name="⏳ Expires", value=f"<t:{int(expires_at.timestamp())}:F>", inline=False)
    return embed


def trustpilot_embed() -> discord.Embed:
    embed = branded_embed(
        "Thanks For Using Our Service",
        "Make sure to drop a positive review on our [Trustpilot](https://www.trustpilot.com/review/status.zeroxhost.space).\n\nYour review means a lot to our service — it helps us improve and keeps the team motivated.",
        0x00c781,
    )
    embed.add_field(name="⭐ Review Link", value="https://www.trustpilot.com/review/status.zeroxhost.space", inline=False)
    return embed



def format_bill_money(amount: float, currency: str) -> str:
    safe_currency = clean(currency.upper(), 8) or "USD"
    return f"{safe_currency} {amount:,.2f}"


def premium_bill_embed(
    *,
    user: discord.User,
    plan: str,
    price: float,
    specifications: str,
    tax_percentage: float,
    discount_percentage: float,
    other_charges: float,
    currency: str,
    notes: str | None,
    bill_id: str,
) -> discord.Embed:
    discount_amount = price * (discount_percentage / 100)
    taxable_subtotal = max(price - discount_amount + other_charges, 0)
    tax_amount = taxable_subtotal * (tax_percentage / 100)
    total = taxable_subtotal + tax_amount
    embed = branded_embed(
        "Premium Bill",
        f"Premium {BRAND} invoice for {user.mention}. Please review the plan, specifications, and total before payment.",
        0x0b132b,
    )
    embed.add_field(name="🧾 Bill ID", value=f"`{bill_id}`", inline=True)
    embed.add_field(name="👤 Customer", value=f"{user.mention}\n`{user.id}`", inline=True)
    embed.add_field(name="📦 Plan", value=plan.title(), inline=True)
    embed.add_field(name="⚙️ Specifications", value=clean(specifications, 1000) or "Not specified", inline=False)
    embed.add_field(
        name="💰 Price Summary",
        value=(
            f"Base price: **{format_bill_money(price, currency)}**\n"
            f"Discount ({discount_percentage:.2f}%): **-{format_bill_money(discount_amount, currency)}**\n"
            f"Other charges: **{format_bill_money(other_charges, currency)}**\n"
            f"Tax ({tax_percentage:.2f}%): **{format_bill_money(tax_amount, currency)}**\n"
            f"Total due: **{format_bill_money(total, currency)}**"
        ),
        inline=False,
    )
    if notes and notes.strip():
        embed.add_field(name="📝 Notes / Payment Details", value=clean(notes, 1000), inline=False)
    embed.add_field(name="✅ Status", value="Premium bill created. Pay only through official ZeroX Host payment methods.", inline=False)
    return embed

def panel_server_embed(server: dict[str, Any], email: str, discord_label: str) -> discord.Embed:
    embed = branded_embed("Panel Server", f"**{server['name']}**", 0x5865f2)
    embed.add_field(name="Server ID", value=f"`{server['id']}`", inline=True)
    embed.add_field(name="UUID", value=f"`{server.get('uuid', 'no-uuid')}`", inline=False)
    embed.add_field(name="Panel Email", value=f"`{email}`", inline=True)
    embed.add_field(name="Discord User", value=discord_label, inline=True)
    return embed


async def node_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    try:
        nodes = await asyncio.wait_for(ptero.list_nodes(), timeout=2.0)
    except Exception as error:
        print(f"Node autocomplete used cache because panel fetch failed: {error}")
        nodes = ptero.node_cache
    matches = [node for node in nodes if current.lower() in f"{node['id']} {node['name']}".lower()]
    return [app_commands.Choice(name=f"{node['name']} (ID {node['id']})", value=f"{node['id']}:{node['name']}") for node in matches[:25]]



async def nest_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    try:
        nests = await asyncio.wait_for(ptero.list_nests(), timeout=2.0)
    except Exception as error:
        print(f"Nest autocomplete used cache because panel fetch failed: {error}")
        nests = ptero.nest_cache
    matches = [nest for nest in nests if current.lower() in f"{nest['id']} {nest['name']}".lower()]
    return [app_commands.Choice(name=f"{nest['name']} (ID {nest['id']})", value=f"{nest['id']}:{nest['name']}") for nest in matches[:25]]



async def egg_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    nest_value = getattr(interaction.namespace, "nest", None)
    if not nest_value:
        return [app_commands.Choice(name="Select a nest first", value="0:select-nest-first")]
    nest_id = parse_id(str(nest_value))
    try:
        eggs = await asyncio.wait_for(ptero.list_eggs(nest_id), timeout=2.0)
    except Exception as error:
        print(f"Egg autocomplete used cache for nest {nest_id} because panel fetch failed: {error}")
        eggs = ptero.egg_cache.get(nest_id, [])
    matches = [egg for egg in eggs if current.lower() in f"{egg['id']} {egg['name']}".lower()]
    return [app_commands.Choice(name=f"{egg['name']} (ID {egg['id']})", value=f"{egg['id']}:{egg['name']}") for egg in matches[:25]]



async def server_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    try:
        servers = await asyncio.wait_for(ptero.list_servers(), timeout=2.0)
    except Exception as error:
        print(f"Server autocomplete used cache because panel fetch failed: {error}")
        servers = ptero.server_cache
    matches = [server for server in servers if current.lower() in f"{server['id']} {server['name']} {server.get('uuid', '')}".lower()]
    return [app_commands.Choice(name=f"{server['name']} • {server.get('uuid', server['id'])}", value=str(server["id"])) for server in matches[:25]]


def record_value(record: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    try:
        return record[key]
    except (KeyError, IndexError, TypeError):
        return default


def panel_label_for_plan(plan: str) -> str:
    return "gp.zeroxhost.space"


def application_client_for_record(record: sqlite3.Row | dict[str, Any]) -> PterodactylClient:
    """Return the single configured Pterodactyl application API client."""
    return ptero


async def ready_application_client_for(record: sqlite3.Row | dict[str, Any]) -> PterodactylClient:
    """Start the selected application API client on demand."""
    panel = application_client_for_record(record)
    if not panel.session:
        await panel.start()
    return panel


def panel_server_record(server: dict[str, Any], plan: str = "panel") -> dict[str, Any]:
    limits = server.get("limits") or {}
    return {
        "server_id": str(server.get("id")),
        "identifier": server.get("identifier"),
        "uuid": server.get("uuid"),
        "name": server.get("name", f"Panel Server {server.get('id')}"),
        "plan": plan,
        "panel_label": panel_label_for_plan(plan),
        "discord_user_id": "",
        "panel_user_id": server.get("user") or 0,
        "panel_email": f"{panel_label_for_plan(plan)} panel-created/unlinked",
        "ram": int(limits.get("memory") or 0),
        "disk": int(limits.get("disk") or 0),
        "cpu": int(limits.get("cpu") or 0),
        "status": server.get("status") or server.get("state") or (server.get("container") or {}).get("status"),
        "node_id": int(server.get("node") or server.get("node_id") or 0),
        "node_name": f"Node {server.get('node') or server.get('node_id') or 'unknown'}",
        "nest_id": int(server.get("nest") or server.get("nest_id") or 0),
        "egg_id": int(server.get("egg") or server.get("egg_id") or 0),
        "expires_at": None,
        "deleted": 0,
    }


def row_server_choices(current: str, rows: list[sqlite3.Row | dict[str, Any]]) -> list[app_commands.Choice[str]]:
    lowered = current.lower()
    matches = [row for row in rows if lowered in f"{panel_label_for_plan(str(record_value(row, 'plan', 'panel')))} {record_value(row, 'server_id', '')} {record_value(row, 'name', '')} {record_value(row, 'uuid', '') or ''} {record_value(row, 'identifier', '') or ''}".lower()]
    return [app_commands.Choice(name=f"[{panel_label_for_plan(str(record_value(row, 'plan', 'panel')))}] {record_value(row, 'name', 'unknown')} • {record_value(row, 'server_id')}", value=str(record_value(row, "server_id"))) for row in matches[:25]]


async def tracked_server_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return row_server_choices(current, fetch_user_servers(interaction.user.id))


async def accessible_server_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if interaction.guild is not None and is_admin(interaction.user):
        return await admin_tracked_server_autocomplete(interaction, current)
    return await tracked_server_autocomplete(interaction, current)


def command_allows_admin_access(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and is_admin(interaction.user)


async def admin_or_owner_server(interaction: discord.Interaction, server: str) -> sqlite3.Row | dict[str, Any]:
    return await ensure_server_access(interaction, server, allow_admin=command_allows_admin_access(interaction))




def server_is_suspended(row: sqlite3.Row | dict[str, Any]) -> bool:
    if bool(record_value(row, "suspended", False)):
        return True
    status = str(record_value(row, "status", "") or "").strip().lower()
    return "suspend" in status


def suspended_action_message(row: sqlite3.Row | dict[str, Any], action: str = "that action") -> str:
    return (
        f"**{record_value(row, 'name', record_value(row, 'server_id', 'this server'))}** is currently suspended, "
        f"so {action} is unavailable until an admin unsuspends it."
    )


def ensure_not_suspended(row: sqlite3.Row | dict[str, Any], action: str = "that action") -> None:
    if server_is_suspended(row):
        raise RuntimeError(suspended_action_message(row, action))

async def require_client_identifier(row: sqlite3.Row | dict[str, Any]) -> str:
    identifier = record_value(row, "identifier")
    if not identifier:
        raise RuntimeError("This server is missing its Pterodactyl client identifier. Run `/admin list` to refresh panel data, then try again.")
    return str(identifier)


async def admin_tracked_server_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if interaction.guild is None or not is_admin(interaction.user):
        return []
    try:
        rows = list((await asyncio.wait_for(fetch_live_panel_records(), timeout=3.0)).values())
    except Exception as error:
        print(f"Failed to include fresh live panel servers in admin autocomplete quickly: {error}")
        try:
            rows = list((await fetch_live_panel_records(use_cache=True)).values())
        except Exception:
            rows = list(fetch_all_servers())
    return row_server_choices(current, rows)




async def fetch_live_panel_records(*, enrich_owner: bool = False, use_cache: bool = False) -> dict[str, sqlite3.Row | dict[str, Any]]:
    if not ptero.session:
        await ptero.start()
    panel_servers = ptero.server_cache if use_cache and ptero.server_cache else await ptero.list_servers()
    live_ids = {str(server.get("id")) for server in panel_servers}
    records: dict[str, sqlite3.Row | dict[str, Any]] = {}
    for row in fetch_all_servers():
        server_id = str(row["server_id"])
        if server_id in live_ids:
            records[server_id] = row
    links = fetch_links_by_panel_user()
    for panel_server in panel_servers:
        server_id = str(panel_server.get("id"))
        if server_id in records:
            continue
        record = panel_server_record(panel_server, "panel")
        panel_user_id = int(record_value(record, "panel_user_id", 0) or 0)
        link = links.get(panel_user_id)
        if enrich_owner:
            panel_user = await ptero.get_user(panel_user_id) if panel_user_id else None
            if panel_user and panel_user.get("email"):
                record["panel_email"] = str(panel_user["email"]).strip().lower()
            if link:
                record["discord_user_id"] = str(link["discord_user_id"])
                record["panel_email"] = str(link["email"]).strip().lower()
        records[server_id] = record
    return records


def whitelist_choice_name(record: sqlite3.Row | dict[str, Any], protected_ids: set[str]) -> str:
    identifiers = server_identifiers(record)
    plan = str(record_value(record, "plan", "panel")).lower()
    status = "Paid" if plan == "paid" else "Whitelisted" if identifiers & protected_ids else "Not whitelisted"
    name = clean(str(record_value(record, "name", "unknown")), 70)
    server_id = record_value(record, "server_id", "unknown")
    return clean(f"[{status}] {name} • {server_id}", 100)




def whitelist_candidate_records(records: dict[str, sqlite3.Row | dict[str, Any]], action_value: str) -> list[sqlite3.Row | dict[str, Any]]:
    protected = whitelist_values()
    candidates: list[sqlite3.Row | dict[str, Any]] = []
    for record in records.values():
        identifiers = server_identifiers(record)
        is_manual = bool(identifiers & protected)
        is_paid = str(record_value(record, "plan", "")).lower() == "paid"
        if action_value == "add" and not is_manual and not is_paid:
            candidates.append(record)
        elif action_value == "remove" and is_manual:
            candidates.append(record)
    return sorted(candidates, key=lambda row: str(record_value(row, "name", "")).lower())


def whitelist_candidate_embeds(action_value: str, candidates: list[sqlite3.Row | dict[str, Any]]) -> list[discord.Embed]:
    title = "Whitelist Add Candidates" if action_value == "add" else "Whitelist Remove Candidates"
    description = "Unwhitelisted, non-paid live panel servers eligible for `/whitelist add`." if action_value == "add" else "Manually whitelisted live panel servers eligible for `/whitelist remove`."
    embeds: list[discord.Embed] = []
    pages = chunked(candidates, 10)
    for page_number, page in enumerate(pages, start=1):
        embed = branded_embed(title, description)
        for record in page:
            server_id = str(record_value(record, "server_id", "unknown"))
            name = clean(str(record_value(record, "name", server_id)), 80)
            uuid = record_value(record, "uuid") or "unknown"
            identifier = record_value(record, "identifier") or "unknown"
            panel_label = panel_label_for_plan(str(record_value(record, "plan", "panel")))
            embed.add_field(name=f"{name} (`{server_id}`)", value=f"Panel: **{panel_label}**\nUUID: `{uuid}`\nIdentifier: `{identifier}`", inline=False)
        embed.set_footer(text=f"{BRAND} • Page {page_number}/{len(pages)} • {len(candidates)} candidate server(s) • Developer: {DEVELOPER}")
        embeds.append(embed)
    return embeds


async def whitelist_server_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if interaction.guild is None or not is_admin(interaction.user):
        return []
    action = getattr(interaction.namespace, "action", "")
    action_value = str(getattr(action, "value", action) or "add").lower()
    if action_value not in {"add", "remove"}:
        action_value = "add"
    try:
        records = await asyncio.wait_for(fetch_live_panel_records(), timeout=3.0)
    except Exception as error:
        print(f"Failed to include fresh live panel servers in whitelist autocomplete quickly: {error}")
        try:
            records = await fetch_live_panel_records(use_cache=True)
        except Exception:
            records = {str(row["server_id"]): row for row in fetch_all_servers()}
    protected = whitelist_values()
    lowered = current.lower()
    choices: list[app_commands.Choice[str]] = []
    for record in whitelist_candidate_records(records, action_value):
        searchable = f"{record_value(record, 'server_id', '')} {record_value(record, 'name', '')} {record_value(record, 'uuid', '') or ''} {record_value(record, 'identifier', '') or ''}".lower()
        if lowered not in searchable:
            continue
        choices.append(app_commands.Choice(name=whitelist_choice_name(record, protected), value=str(record_value(record, "server_id"))))
        if len(choices) >= 25:
            break
    return choices


def parse_duration(value: str) -> int:
    total = 0
    for amount, unit in re.findall(r"(\d+)\s*([dhm])", value.lower()):
        number = int(amount)
        total += number * {"d": 86400, "h": 3600, "m": 60}[unit]
    if total <= 0:
        raise ValueError("Use duration like 2d, 4h, 12h, or 1d12h.")
    return total


def describe_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def is_not_found_error(error: Exception) -> bool:
    message = str(error).lower()
    return " 404" in message or "not found" in message or "could not be found" in message


async def refresh_tracked_server(row: sqlite3.Row) -> sqlite3.Row | None:
    """Refresh a tracked DB server from its owning panel so local state never wins over live panel data."""
    server_id = str(row["server_id"])
    panel = await ready_application_client_for(row)
    try:
        panel_server = await panel.get_server(server_id)
    except RuntimeError as error:
        if is_not_found_error(error):
            mark_server_deleted(server_id)
            return None
        raise
    panel_user_id = int(panel_server.get("user") or 0)
    panel_user = await panel.get_user(panel_user_id) if panel_user_id else None
    update_tracked_server_from_panel(server_id, panel_server, panel_user.get("email") if panel_user else None)
    return fetch_server(server_id)


async def refresh_user_servers(discord_user_id: int) -> list[sqlite3.Row | dict[str, Any]]:
    refreshed: list[sqlite3.Row | dict[str, Any]] = []
    seen: set[str] = set()
    for row in fetch_user_servers(discord_user_id):
        live_row = await refresh_tracked_server(row)
        if live_row and str(live_row["discord_user_id"]) == str(discord_user_id):
            refreshed.append(live_row)
            seen.add(str(live_row["server_id"]))
    link = fetch_link(discord_user_id)
    if link:
        for server_id, record in (await fetch_live_panel_records(enrich_owner=True)).items():
            if server_id in seen:
                continue
            if int(record_value(record, "panel_user_id", 0) or 0) == int(link["panel_user_id"]):
                refreshed.append(record)
                seen.add(server_id)
    return refreshed


async def refresh_email_servers(email: str) -> list[sqlite3.Row | dict[str, Any]]:
    normalized_email = email.strip().lower()
    refreshed: list[sqlite3.Row | dict[str, Any]] = []
    seen: set[str] = set()
    for row in fetch_servers_by_email(normalized_email):
        live_row = await refresh_tracked_server(row)
        if live_row and str(live_row["panel_email"]).strip().lower() == normalized_email:
            refreshed.append(live_row)
            seen.add(str(live_row["server_id"]))
    for server_id, record in (await fetch_live_panel_records(enrich_owner=True)).items():
        if server_id in seen:
            continue
        if str(record_value(record, "panel_email", "")).strip().lower() == normalized_email:
            refreshed.append(record)
            seen.add(server_id)
    return refreshed


def server_row_to_line(row: sqlite3.Row) -> str:
    expires = int(datetime.fromisoformat(row["expires_at"]).timestamp())
    return f"`{row['server_id']}` • **{row['name']}** • {row['plan']} • <t:{expires}:R>"


async def ensure_server_access(interaction: discord.Interaction, server_id: str, *, allow_admin: bool = False) -> sqlite3.Row | dict[str, Any]:
    row = fetch_server(server_id)
    if row and not row["deleted"]:
        live_row = await refresh_tracked_server(row)
        if not live_row:
            raise RuntimeError("This server no longer exists on the Pterodactyl panel, so it was removed from active bot lists.")
        if allow_admin and interaction.guild is not None and is_admin(interaction.user):
            return live_row
        if str(interaction.user.id) == live_row["discord_user_id"]:
            return live_row
        raise RuntimeError("You can only control your own servers. Use `/admin manage` for staff access to other users' servers.")
    if allow_admin and interaction.guild is not None and is_admin(interaction.user):
        try:
            return panel_server_record(await ptero.get_server(server_id), "panel")
        except RuntimeError as error:
            raise RuntimeError(f"Unknown tracked or panel server: {server_id}") from error
    raise RuntimeError("Unknown tracked server.")


def format_mb(value: float) -> str:
    return f"{value:,.1f} MB"


def manage_embed(row: sqlite3.Row | dict[str, Any], resources: dict[str, Any]) -> discord.Embed:
    raw = resources.get("resources", {}) if resources else {}
    state = resources.get("current_state", "unknown") if resources else "unknown"
    if server_is_suspended(row):
        state = "suspended"
    memory_mb = float(raw.get("memory_bytes") or 0) / 1024 / 1024
    disk_mb = float(raw.get("disk_bytes") or 0) / 1024 / 1024
    cpu_usage = float(raw.get("cpu_absolute") or 0)
    uptime_seconds = int(raw.get("uptime") or 0) // 1000
    uptime = str(timedelta(seconds=uptime_seconds)) if uptime_seconds else "offline/unknown"
    expires_at = record_value(row, "expires_at")
    embed = branded_embed("Server Manager", f"**{record_value(row, 'name', 'unknown')}** (`{record_value(row, 'server_id')}`)", 0x2ecc71)
    embed.add_field(name="State", value=f"`{state}`", inline=True)
    embed.add_field(name="Uptime", value=f"`{uptime}`", inline=True)
    embed.add_field(name="CPU", value=f"**{cpu_usage:.2f}%** / {record_value(row, 'cpu', 0)}%", inline=True)
    embed.add_field(name="RAM", value=f"**{format_mb(memory_mb)}** / {int(record_value(row, 'ram', 0)):,} MB", inline=True)
    embed.add_field(name="Disk", value=f"**{format_mb(disk_mb)}** / {int(record_value(row, 'disk', 0)):,} MB", inline=True)
    embed.add_field(name="Panel", value=config.get("panel_url", PANEL_URL).rstrip("/"), inline=True)
    embed.add_field(name="Tracking", value="Local DB tracked" if expires_at else "Panel server (not locally tracked)", inline=True)
    embed.add_field(name="Expires", value=f"<t:{int(datetime.fromisoformat(expires_at).timestamp())}:R>" if expires_at else "Not tracked by bot", inline=True)
    return embed


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index:index + size] for index in range(0, len(items), size)] or [[]]


class PaginatedEmbeds(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        await send_view_error(interaction, error)

    def __init__(self, embeds: list[discord.Embed]) -> None:
        super().__init__(timeout=180)
        self.embeds = embeds
        self.index = 0
        self.update_buttons()

    def update_buttons(self) -> None:
        self.previous.disabled = self.index <= 0
        self.next.disabled = self.index >= len(self.embeds) - 1

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="⬅️")
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = max(0, self.index - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, emoji="➡️")
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = min(len(self.embeds) - 1, self.index + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)


async def send_view_error(interaction: discord.Interaction, error: Exception) -> None:
    embed = branded_embed("Action Failed", clean(str(error), 3500), 0xff4d4d)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ManageView(discord.ui.View):
    def __init__(self, server_id: str, *, allow_admin: bool = False) -> None:
        super().__init__(timeout=300)
        self.server_id = server_id
        self.allow_admin = allow_admin
        self.add_item(discord.ui.Button(label="Open Panel", style=discord.ButtonStyle.link, url=config.get("panel_url", PANEL_URL).rstrip("/"), emoji="🔗", row=2))

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        await send_view_error(interaction, error)

    async def row(self, interaction: discord.Interaction) -> sqlite3.Row:
        return await ensure_server_access(interaction, self.server_id, allow_admin=self.allow_admin)

    async def refresh_message(self, interaction: discord.Interaction, note: str) -> None:
        row = await self.row(interaction)
        if server_is_suspended(row):
            embed = manage_embed(row, {})
            embed.description = f"{embed.description}\n\n⚠️ {suspended_action_message(row, 'live controls')}"
            await interaction.response.edit_message(embed=embed, view=self)
            return
        resources = await (await ready_client_api_for(row)).resources(row["identifier"]) if row["identifier"] else {}
        embed = manage_embed(row, resources)
        embed.description = f"{embed.description}\n\n{note}"
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        row = await self.row(interaction)
        if not row["identifier"]:
            raise RuntimeError("This tracked server is missing its client identifier.")
        ensure_not_suspended(row, "power controls")
        await (await ready_client_api_for(row)).power(row["identifier"], "start")
        await self.refresh_message(interaction, "✅ Start signal sent.")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️", row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        row = await self.row(interaction)
        if not row["identifier"]:
            raise RuntimeError("This tracked server is missing its client identifier.")
        ensure_not_suspended(row, "power controls")
        await (await ready_client_api_for(row)).power(row["identifier"], "stop")
        await self.refresh_message(interaction, "✅ Stop signal sent.")

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.primary, emoji="🔁", row=0)
    async def restart_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        row = await self.row(interaction)
        if not row["identifier"]:
            raise RuntimeError("This tracked server is missing its client identifier.")
        ensure_not_suspended(row, "power controls")
        await (await ready_client_api_for(row)).power(row["identifier"], "restart")
        await self.refresh_message(interaction, "✅ Restart signal sent.")

    @discord.ui.button(label="Kill", style=discord.ButtonStyle.danger, emoji="💀", row=0)
    async def kill_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        row = await self.row(interaction)
        if not row["identifier"]:
            raise RuntimeError("This tracked server is missing its client identifier.")
        ensure_not_suspended(row, "power controls")
        await (await ready_client_api_for(row)).power(row["identifier"], "kill")
        await self.refresh_message(interaction, "✅ Kill signal sent.")


class ResizeModal(discord.ui.Modal, title="Resize ZeroX Host Server"):
    ram = discord.ui.TextInput(label="RAM GB", placeholder="2", required=True)
    disk = discord.ui.TextInput(label="Disk GB", placeholder="10", required=True)
    cpu = discord.ui.TextInput(label="CPU %", placeholder="100", required=True)
    extras = discord.ui.TextInput(label="DB, Allocations, Backups", placeholder="1,1,1", required=False, default="1,1,1")

    def __init__(self, server_id: str, *, allow_admin: bool = False) -> None:
        super().__init__()
        self.server_id = server_id
        self.allow_admin = allow_admin

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        row = await ensure_server_access(interaction, self.server_id, allow_admin=self.allow_admin)
        try:
            extras = [int(part.strip()) for part in str(self.extras.value).split(",")]
            if len(extras) != 3:
                raise ValueError
            databases, allocations, backups = extras
            ram_gb = int(str(self.ram.value).strip())
            disk_gb = int(str(self.disk.value).strip())
            cpu = int(str(self.cpu.value).strip())
        except ValueError as error:
            raise RuntimeError("Use whole numbers for RAM GB, disk GB, CPU, and extras in `databases,allocations,backups` format, for example `1,1,1`.") from error
        if min(ram_gb, disk_gb, cpu) <= 0 or min(databases, allocations, backups) < 0:
            raise RuntimeError("RAM GB, disk GB, and CPU must be positive. Databases, allocations, and backups cannot be negative.")
        ram_mb = ram_gb * 1024
        disk_mb = disk_gb * 1024
        await (await ready_application_client_for(row)).resize_server(self.server_id, ram_mb, disk_mb, cpu, databases, allocations, backups)
        if fetch_server(self.server_id):
            with db() as connection:
                connection.execute("UPDATE servers SET ram=?, disk=?, cpu=?, databases=?, allocations=?, backups=? WHERE server_id=?", (ram_mb, disk_mb, cpu, databases, allocations, backups, self.server_id))
        await interaction.followup.send(embed=branded_embed("Server Resized", f"**{record_value(row, 'name', self.server_id)}** is now {ram_gb:,} GB RAM ({ram_mb:,} MB) / {disk_gb:,} GB disk ({disk_mb:,} MB) / {cpu}% CPU."), ephemeral=True)
        await send_server_event_log(row, "Server Resized", f"**{record_value(row, 'name', self.server_id)}** (`{self.server_id}`) resized to {ram_gb:,} GB RAM / {disk_gb:,} GB disk / {cpu}% CPU.", actor=interaction.user, color=0x3498db)


class SuspendSelect(discord.ui.View):
    def __init__(self, rows: list[sqlite3.Row]) -> None:
        super().__init__(timeout=120)
        options = [
            discord.SelectOption(
                label=row["name"][:100],
                value=row["server_id"],
                description=f"{row['plan']} • {row['panel_email']}"[:100],
            )
            for row in rows[:25]
        ]
        self.select = discord.ui.Select(placeholder="Select a server to suspend", min_values=1, max_values=1, options=options)
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction) -> None:
        server_id = self.select.values[0]
        row = fetch_server(server_id)
        if not row:
            await interaction.response.send_message(embed=branded_embed("Missing Server", "That tracked server was not found anymore.", 0xff4d4d), ephemeral=True)
            return
        await (await ready_application_client_for(row)).suspend_server(server_id)
        with db() as connection:
            connection.execute("UPDATE servers SET suspended=1 WHERE server_id=?", (server_id,))
        await interaction.response.edit_message(embed=branded_embed("Server Suspended", f"Suspended **{row['name']}** (`{server_id}`)."), view=None)


async def create_plan(interaction: discord.Interaction, plan: str, user: discord.User, name: str, ram: int, disk: int, cpu: int, nest: str, egg: str, node: str, time: str, databases: int, allocations: int, backups: int) -> None:
    await interaction.response.defer(ephemeral=True)
    if ram <= 0 or disk <= 0 or cpu <= 0:
        raise RuntimeError("RAM, disk, and CPU must be positive numbers.")
    ram_mb = ram * 1024
    disk_mb = disk * 1024
    duration_seconds = parse_duration(time)
    node_id = parse_id(node)
    nest_id = parse_id(nest)
    egg_id = parse_id(egg)
    node_name = node.split(":", 1)[1] if ":" in node else f"Node {node_id}"
    nest_name = nest.split(":", 1)[1] if ":" in nest else f"Nest {nest_id}"
    egg_name = egg.split(":", 1)[1] if ":" in egg else f"Egg {egg_id}"
    if nest_id <= 0 or egg_id <= 0:
        await interaction.followup.send(embed=branded_embed("Nest And Egg Required", "Select a real nest first, then select an egg from that nest.", 0xff4d4d), ephemeral=True)
        return
    panel = ptero
    link = fetch_link(user.id)
    if not link:
        raise RuntimeError(f"Discord user is not linked to the panel. Use /link first.")
    panel_email = link["email"]
    panel_user = {"id": link["panel_user_id"]}
    expires_at = utc_now() + timedelta(seconds=duration_seconds)
    server = await panel.create_server(panel_user_id=panel_user["id"], name=name, ram=ram_mb, disk=disk_mb, cpu=cpu, node_id=node_id, nest_id=nest_id, egg_id=egg_id, databases=databases, allocations=allocations, backups=backups)
    server_id = str(server["id"])
    saga_synced = await panel.set_saga_auto_suspend(server_id, expires_at)
    record = {
        "server_id": server_id,
        "plan": plan,
        "discord_user_id": str(user.id),
        "panel_user_id": panel_user["id"],
        "panel_email": panel_email,
        "name": name,
        "uuid": server.get("uuid"),
        "identifier": server.get("identifier"),
        "ram": ram_mb,
        "disk": disk_mb,
        "cpu": cpu,
        "nest_id": nest_id,
        "nest_name": nest_name,
        "egg_id": egg_id,
        "egg_name": egg_name,
        "node_id": node_id,
        "node_name": node_name,
        "databases": databases,
        "allocations": allocations,
        "backups": backups,
        "created_at": utc_now().isoformat(),
        "expires_at": expires_at.isoformat(),
        "suspended": False,
        "deleted": False,
        "autosuspend_enabled": True,
        "autosuspend_seconds": duration_seconds,
    }
    database["servers"][server_id] = record
    database["users"].setdefault(str(user.id), {"servers": []})["servers"].append(server_id)
    if plan == "paid":
        database.setdefault("whitelist", []).append(server_id)
    save_database()
    upsert_server_record(record)
    if plan == "paid":
        with db() as connection:
            connection.execute("INSERT OR IGNORE INTO whitelist(server_id) VALUES (?)", (server_id,))

    dm_embed = specs_embed(plan, name, ram_mb, disk_mb, cpu, node_name, nest_name, egg_name, expires_at, databases, allocations, backups)
    try:
        await user.send(embed=dm_embed)
        await user.send(embed=trustpilot_embed())
    except discord.Forbidden:
        pass

    created = branded_embed("Server Created", f"**{name}** was created for {user.mention}.", 0x2ecc71)
    created.add_field(name="Server", value=f"ID: `{server_id}`\nUUID: `{server.get('uuid', 'unknown')}`", inline=False)
    created.add_field(name="Owner", value=f"Discord: {user.mention}\nEmail: `{panel_email}`", inline=True)
    created.add_field(name="Specs", value=f"RAM: **{ram_mb:,} MB** ({ram} GB)\nDisk: **{disk_mb:,} MB** ({disk} GB)\nCPU: **{cpu}%**", inline=True)
    created.add_field(name="Deployment", value=f"Node: **{node_name}**\nNest: **{nest_name}**\nEgg: **{egg_name}**", inline=False)
    created.add_field(name="Extras", value=f"Databases: **{databases}**\nAllocations: **{allocations}**\nBackups: **{backups}**", inline=True)
    created.add_field(name="Expiration", value=f"<t:{int(expires_at.timestamp())}:F>\n<t:{int(expires_at.timestamp())}:R>", inline=True)
    created.add_field(name="Saga Auto Suspension", value="Synced to panel" if saga_synced else "Bot DB only / panel sync unavailable", inline=True)
    await interaction.followup.send(embed=created, ephemeral=True)
    plan_title = "Paid" if plan == "paid" else "Free"
    log_embed = branded_embed(
        f"{plan_title} Server Created",
        server_admin_details(record, user=user, event_when=expires_at, reason=f"{plan_title} server created; Saga auto suspension {'synced' if saga_synced else 'not synced'}"),
        0xf1c40f if plan == "paid" else 0x00d4ff,
    )
    await send_plan_log(plan, log_embed)




@tree.command(name="about", description="Show the ZeroX Host Pterodactyl Manager overview")
async def about(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(embed=about_embed(), ephemeral=True)



@tree.command(name="bill", description="Admin: create a premium bill for VPS or Minecraft hosting")
@admin_only()
@app_commands.describe(
    user="Customer who should receive the bill",
    price="Base price before discount, tax, and other charges",
    specifications="Server specifications, term, add-ons, or package details",
    tax_percentage="Tax percentage to add after discount and other charges",
    discount_percentage="Discount percentage to subtract from the base price",
    other_charges="Additional fixed charges such as setup fees",
    currency="Currency code, for example USD, INR, EUR",
    notes="Optional payment instructions or extra bill notes",
)
@app_commands.choices(plan=[app_commands.Choice(name="VPS", value="vps"), app_commands.Choice(name="Minecraft", value="minecraft")])
async def bill(
    interaction: discord.Interaction,
    user: discord.User,
    plan: app_commands.Choice[str],
    price: float,
    specifications: str,
    tax_percentage: float = 0.0,
    discount_percentage: float = 0.0,
    other_charges: float = 0.0,
    currency: str = "USD",
    notes: str | None = None,
) -> None:
    await interaction.response.defer(ephemeral=True)
    if price < 0 or other_charges < 0:
        raise RuntimeError("Price and other charges cannot be negative.")
    if tax_percentage < 0 or discount_percentage < 0:
        raise RuntimeError("Tax and discount percentages cannot be negative.")
    if discount_percentage > 100:
        raise RuntimeError("Discount percentage cannot be more than 100%.")
    bill_id = f"ZX-{utc_now().strftime('%Y%m%d%H%M%S')}-{user.id % 10000:04d}"
    embed = premium_bill_embed(
        user=user,
        plan=plan.value,
        price=price,
        specifications=specifications,
        tax_percentage=tax_percentage,
        discount_percentage=discount_percentage,
        other_charges=other_charges,
        currency=currency,
        notes=notes,
        bill_id=bill_id,
    )
    dm_status = "sent"
    try:
        await user.send(embed=embed)
    except discord.Forbidden:
        dm_status = "blocked by the user"
    await interaction.followup.send(embed=embed, ephemeral=True)
    await interaction.followup.send(f"Bill `{bill_id}` created for {user.mention}. Customer DM: **{dm_status}**.", ephemeral=True)
    await send_admin_audit("Premium Bill Created", f"Bill `{bill_id}` for {user.mention} (`{user.id}`) • Plan: **{plan.value.title()}** • Total shown in bill embed.", actor=interaction.user, color=0xf1c40f)


@tree.command(name="create-free", description="Create free server")
@admin_only()
@app_commands.autocomplete(nest=nest_autocomplete, egg=egg_autocomplete, node=node_autocomplete)
@app_commands.describe(ram="RAM in GB (the bot sends GB x 1024 MB to Pterodactyl)", disk="Disk in GB (the bot sends GB x 1024 MB to Pterodactyl)", time="Duration like 30d, 12h, or 1d6h")
async def create_free(interaction: discord.Interaction, user: discord.User, name: str, ram: int, disk: int, cpu: int, nest: str, egg: str, node: str, time: str = "30d", databases: int = 0, allocations: int = 1, backups: int = 0) -> None:
    await create_plan(interaction, "free", user, name, ram, disk, cpu, nest, egg, node, time, databases, allocations, backups)


@tree.command(name="create-paid", description="Create paid server")
@admin_only()
@app_commands.autocomplete(nest=nest_autocomplete, egg=egg_autocomplete, node=node_autocomplete)
@app_commands.describe(ram="RAM in GB (the bot sends GB x 1024 MB to Pterodactyl)", disk="Disk in GB (the bot sends GB x 1024 MB to Pterodactyl)", time="Duration like 30d, 12h, or 1d6h")
async def create_paid(interaction: discord.Interaction, user: discord.User, name: str, ram: int, disk: int, cpu: int, nest: str, egg: str, node: str, time: str, databases: int = 1, allocations: int = 1, backups: int = 1) -> None:
    await create_plan(interaction, "paid", user, name, ram, disk, cpu, nest, egg, node, time, databases, allocations, backups)


@tree.command(name="link", description="Link panel email")
@admin_only()
async def link(interaction: discord.Interaction, user: discord.User, panel_email: str) -> None:
    await interaction.response.defer(ephemeral=True)
    existing_user_link = fetch_link(user.id)
    if existing_user_link:
        await interaction.followup.send(embed=branded_embed("Already Linked", f"{user.mention} is already linked to `{existing_user_link['email']}`. Use `/unlink` before linking a different account.", 0xffcc00), ephemeral=True)
        return

    normalized_email = panel_email.strip().lower()
    panel_user = await ptero.get_required_panel_user(normalized_email)
    existing_panel_link = fetch_link_by_panel_user(int(panel_user["id"]))
    if existing_panel_link:
        await interaction.followup.send(embed=branded_embed("Panel Account Already Linked", f"Panel account `{existing_panel_link['email']}` is already linked to <@{existing_panel_link['discord_user_id']}> (`{existing_panel_link['discord_user_id']}`). Use `/unlink` first if this should be changed.", 0xffcc00), ephemeral=True)
        return

    with db() as connection:
        connection.execute(
            """
            INSERT INTO links(discord_user_id, panel_user_id, email) VALUES (?,?,?)
            ON CONFLICT(discord_user_id) DO NOTHING
            """,
            (str(user.id), panel_user["id"], normalized_email),
        )
    await interaction.followup.send(embed=branded_embed("User Linked", f"{user.mention} linked to `{normalized_email}` / panel user `{panel_user['id']}`."), ephemeral=True)


@tree.command(name="unlink", description="Admin: unlink a Discord user from their panel account")
@admin_only()
async def unlink(interaction: discord.Interaction, user: discord.User) -> None:
    await interaction.response.defer(ephemeral=True)
    existing = unlink_discord_user(user.id)
    if not existing:
        await interaction.followup.send(embed=branded_embed("No Link Found", f"{user.mention} does not have a linked panel account.", 0xffcc00), ephemeral=True)
        return
    await interaction.followup.send(embed=branded_embed("User Unlinked", f"Removed {user.mention}'s link to `{existing['email']}` / panel user `{existing['panel_user_id']}`."), ephemeral=True)


admin_group = app_commands.Group(name="admin", description="ZeroX Host admin tools")


@admin_group.command(name="createuser", description="Create a panel account")
@admin_only()
@app_commands.describe(
    user="Discord user who will own this panel account",
    email="Panel account email",
    username="Panel username",
    first_name="Account first name",
    last_name="Account last name",
    password="Temporary panel password",
)
async def admin_createuser(
    interaction: discord.Interaction,
    user: discord.User,
    email: str,
    username: str,
    first_name: str,
    last_name: str,
    password: str,
) -> None:
    """Provision and link accounts on the single configured panel."""
    await interaction.response.defer(ephemeral=True)
    email = email.strip().lower()
    username = username.strip()
    first_name = first_name.strip()
    last_name = last_name.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise RuntimeError("Enter a valid email address.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise RuntimeError("Username must be 3-32 characters using letters, numbers, `.`, `_`, or `-`.")
    if not first_name or not last_name:
        raise RuntimeError("First name and last name are required.")
    if len(password) < 8:
        raise RuntimeError("Use a temporary password with at least 8 characters.")
    existing = await ptero.find_user_by_email(email)
    if existing:
        raise RuntimeError("A panel account with that email already exists. Use the existing account instead.")
    account = await ptero.create_user(
        email=email,
        username=username,
        first_name=first_name,
        last_name=last_name,
        password=password,
    )
    with db() as connection:
        connection.execute(
            """
            INSERT INTO links(discord_user_id, panel_user_id, email) VALUES (?,?,?)
            ON CONFLICT(discord_user_id) DO UPDATE SET panel_user_id=excluded.panel_user_id, email=excluded.email
            """,
            (str(user.id), int(account["id"]), email),
        )
    credentials = branded_embed(
        "Your Panel Account Is Ready",
        f"Your panel account has been created for **{BRAND}**.",
        0x2ECC71,
    )
    credentials.add_field(name="Panel", value=config.get("panel_url", PANEL_URL), inline=False)
    credentials.add_field(name="Email", value=f"`{email}`", inline=True)
    credentials.add_field(name="Username", value=f"`{username}`", inline=True)
    credentials.add_field(name="Temporary password", value=f"||{password}||", inline=False)
    credentials.add_field(name="Keep it secure", value="Change this temporary password after your first sign-in. Do not share it with anyone.", inline=False)
    dm_status = "sent"
    try:
        await user.send(embed=credentials)
    except discord.Forbidden:
        dm_status = "blocked by the user"
    result = branded_embed("Panel Account Created", f"Created and linked the panel account for {user.mention}.", 0x2ECC71)
    result.add_field(name="Panel user ID", value=f"`{account['id']}`", inline=True)
    result.add_field(name="Email", value=f"`{email}`", inline=True)
    result.add_field(name="Credential DM", value=dm_status, inline=True)
    result.add_field(name="Panel", value=config.get("panel_url", PANEL_URL), inline=False)
    await interaction.followup.send(embed=result, ephemeral=True)


@admin_group.command(name="list", description="List panel servers")
@admin_only()
async def admin_list(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    servers = await ptero.list_servers()
    links = fetch_links_by_panel_user()
    if not servers:
        await interaction.followup.send(embed=branded_embed("Panel Servers", "No panel servers found."), ephemeral=True)
        return

    embeds: list[discord.Embed] = []
    pages = chunked(servers, 6)
    for page_number, page in enumerate(pages, start=1):
        embed = branded_embed("Panel Servers", "Live data from the Pterodactyl panel. Emails are shown even when a Discord user is not linked.")
        for server in page:
            panel_user_id = int(server.get("user") or 0)
            link = links.get(panel_user_id)
            panel_user = await ptero.get_user(panel_user_id) if panel_user_id else None
            email = link["email"] if link else panel_user.get("email") if panel_user else "unknown-email"
            discord_label = f"<@{link['discord_user_id']}>" if link else "Not linked"
            embed.add_field(
                name=f"#{server['id']} • {server['name']}",
                value=f"**UUID:** `{server.get('uuid', 'no-uuid')}`\n**Email:** `{email}`\n**Discord:** {discord_label}",
                inline=False,
            )
        embed.set_footer(text=f"{BRAND} • Page {page_number}/{len(pages)} • {len(servers)} panel servers • Developer: {DEVELOPER}")
        embeds.append(embed)
    if len(embeds) > 1:
        await interaction.followup.send(embed=embeds[0], view=PaginatedEmbeds(embeds), ephemeral=True)
    else:
        await interaction.followup.send(embed=embeds[0], ephemeral=True)

@admin_group.command(name="manage", description="Manage any tracked server")
@admin_only()
@app_commands.autocomplete(server=admin_tracked_server_autocomplete)
async def admin_manage(interaction: discord.Interaction, server: str) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await ensure_server_access(interaction, server, allow_admin=True)
    resources = {}
    if row["identifier"] and not server_is_suspended(row):
        resources = await (await ready_client_api_for(row)).resources(row["identifier"])
    embed = manage_embed(row, resources)
    if server_is_suspended(row):
        embed.description = f"{embed.description}\n\n⚠️ {suspended_action_message(row, 'live controls')}"
    await interaction.followup.send(embed=embed, view=ManageView(server, allow_admin=True), ephemeral=True)


@admin_group.command(name="console", description="Send console command to any tracked server")
@admin_only()
@app_commands.autocomplete(server=admin_tracked_server_autocomplete)
async def admin_console(interaction: discord.Interaction, server: str, command: str) -> None:
    await interaction.response.defer(ephemeral=True)
    if not command.strip():
        raise RuntimeError("Console command cannot be empty.")
    row = await ensure_server_access(interaction, server, allow_admin=True)
    identifier = await require_client_identifier(row)
    ensure_not_suspended(row, "console commands")
    await (await ready_client_api_for(row)).command(identifier, command.strip())
    await interaction.followup.send(embed=branded_embed("Admin Console Command Sent", f"Sent command to **{record_value(row, 'name', server)}**.\n```{clean(command, 1000)}```"), ephemeral=True)


@admin_group.command(name="rename", description="Rename any tracked server")
@admin_only()
@app_commands.autocomplete(server=admin_tracked_server_autocomplete)
async def admin_rename(interaction: discord.Interaction, server: str, new_name: str) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await ensure_server_access(interaction, server, allow_admin=True)
    if not row["identifier"]:
        raise RuntimeError("This tracked server is missing its client identifier.")
    ensure_not_suspended(row, "renaming")
    await (await ready_client_api_for(row)).rename(row["identifier"], new_name)
    with db() as connection:
        connection.execute("UPDATE servers SET name=? WHERE server_id=?", (new_name, server))
    await interaction.followup.send(embed=branded_embed("Admin Server Renamed", f"`{row['name']}` is now **{new_name}**."), ephemeral=True)


tree.add_command(admin_group)


@tree.command(name="list", description="List your servers, or filter by user/email as an admin")
@app_commands.describe(user="Admin-only: Discord user whose servers should be shown", email="Admin-only: panel email whose servers should be shown")
async def list_mine(interaction: discord.Interaction, user: discord.User | None = None, email: str | None = None) -> None:
    admin_filter_requested = user is not None or bool(email and email.strip())
    await interaction.response.defer(ephemeral=admin_filter_requested)
    if admin_filter_requested and not command_allows_admin_access(interaction):
        raise RuntimeError("Only admins can filter /list by Discord user or panel email.")
    if user and email and email.strip():
        raise RuntimeError("Use either user or email for /list, not both.")

    if user:
        rows = await refresh_user_servers(user.id)
        title = f"Servers For {user.display_name}"
        description = f"Tracked servers linked to {user.mention}."
    elif email and email.strip():
        normalized_email = email.strip().lower()
        rows = await refresh_email_servers(normalized_email)
        title = "Servers For Email"
        description = f"Tracked servers linked to panel email `{clean(normalized_email, 120)}`."
    else:
        rows = await refresh_user_servers(interaction.user.id)
        title = "Your Servers"
        description = "Only your own linked/tracked servers are shown here. Admins can use `/admin list` for all servers."

    if not rows:
        await interaction.followup.send(embed=branded_embed(title, "No servers found."), ephemeral=admin_filter_requested)
        return
    embeds: list[discord.Embed] = []
    pages = chunked(rows, 10)
    for page_number, page in enumerate(pages, start=1):
        embed = branded_embed(title, description)
        for row in page:
            expires_raw = record_value(row, "expires_at")
            expires_text = "Panel-created / not tracked"
            if expires_raw:
                expires_text = f"<t:{int(datetime.fromisoformat(str(expires_raw)).timestamp())}:R>"
            owner_id = str(record_value(row, "discord_user_id", "")).strip()
            owner_text = f"<@{owner_id}>" if owner_id else "Not linked"
            state_text = "Suspended" if server_is_suspended(row) else "Active"
            important_value = f"**Plan:** {record_value(row, 'plan')}\n**State:** {state_text}\n**Owner:** {owner_text}\n**Specs:** {int(record_value(row, 'ram', 0)):,} MB RAM / {int(record_value(row, 'disk', 0)):,} MB Disk / {int(record_value(row, 'cpu', 0))}% CPU\n**Expires:** {expires_text}"
            sensitive_value = f"{important_value}\n**UUID:** `{record_value(row, 'uuid') or 'unknown'}`\n**Email:** `{record_value(row, 'panel_email')}`"
            embed.add_field(
                name=f"#{record_value(row, 'server_id')} • {record_value(row, 'name')}",
                value=sensitive_value if admin_filter_requested else important_value,
                inline=False,
            )
        embed.set_footer(text=f"{BRAND} • Page {page_number}/{len(pages)} • {len(rows)} server(s) • Developer: {DEVELOPER}")
        embeds.append(embed)
    if len(embeds) > 1:
        await interaction.followup.send(embed=embeds[0], view=PaginatedEmbeds(embeds), ephemeral=admin_filter_requested)
    else:
        await interaction.followup.send(embed=embeds[0], ephemeral=admin_filter_requested)

@tree.command(name="manage", description="Open server manager")
@app_commands.autocomplete(server=accessible_server_autocomplete)
async def manage(interaction: discord.Interaction, server: str) -> None:
    await interaction.response.defer(ephemeral=True)
    allow_admin = command_allows_admin_access(interaction)
    row = await ensure_server_access(interaction, server, allow_admin=allow_admin)
    identifier = record_value(row, "identifier")
    resources = {}
    if identifier and not server_is_suspended(row):
        resources = await (await ready_client_api_for(row)).resources(str(identifier))
    embed = manage_embed(row, resources)
    if server_is_suspended(row):
        embed.description = f"{embed.description}\n\n⚠️ {suspended_action_message(row, 'live controls')}"
    await interaction.followup.send(embed=embed, view=ManageView(server, allow_admin=allow_admin), ephemeral=True)


@tree.command(name="console", description="Send console command")
@app_commands.autocomplete(server=accessible_server_autocomplete)
async def console(interaction: discord.Interaction, server: str, command: str) -> None:
    await interaction.response.defer(ephemeral=True)
    if not command.strip():
        raise RuntimeError("Console command cannot be empty.")
    row = await admin_or_owner_server(interaction, server)
    identifier = await require_client_identifier(row)
    ensure_not_suspended(row, "console commands")
    await (await ready_client_api_for(row)).command(identifier, command.strip())
    await interaction.followup.send(embed=branded_embed("Console Command Sent", f"Sent command to **{record_value(row, 'name', server)}**.\n```{clean(command, 1000)}```"), ephemeral=True)


@tree.command(name="rename", description="Rename server")
@app_commands.autocomplete(server=accessible_server_autocomplete)
async def rename(interaction: discord.Interaction, server: str, new_name: str) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await admin_or_owner_server(interaction, server)
    identifier = await require_client_identifier(row)
    ensure_not_suspended(row, "renaming")
    await (await ready_client_api_for(row)).rename(identifier, new_name)
    if fetch_server(server):
        with db() as connection:
            connection.execute("UPDATE servers SET name=? WHERE server_id=?", (new_name, server))
    await interaction.followup.send(embed=branded_embed("Server Renamed", f"`{record_value(row, 'name', server)}` is now **{new_name}**."), ephemeral=True)


@tree.command(name="schedule-restart", description="Schedule one-time delayed restart")
@app_commands.autocomplete(server=accessible_server_autocomplete, node=node_autocomplete)
@app_commands.describe(time="Delay before the one-time restart, like 5m, 12h, or 1d", node="Admin-only with all_servers: restart only servers on this node")
async def schedule_restart(interaction: discord.Interaction, time: str, server: str | None = None, all_servers: bool = False, node: str | None = None) -> None:
    await interaction.response.defer(ephemeral=True)
    seconds = parse_duration(time)
    next_run = (utc_now() + timedelta(seconds=seconds)).isoformat()
    if all_servers:
        if interaction.guild is None or not is_admin(interaction.user):
            raise RuntimeError("Scheduling restarts for all servers is admin-only and cannot be used in DMs.")
        node_id = parse_id(node) if node else None
        rows = list((await fetch_live_panel_records()).values())
        if node_id:
            rows = [row for row in rows if int(record_value(row, "node_id", 0) or 0) == node_id]
        rows = [row for row in rows if record_value(row, "identifier") and not bool(record_value(row, "suspended", False))]
        with db() as connection:
            for row in rows:
                server_id = str(record_value(row, "server_id"))
                connection.execute("DELETE FROM scheduled_restarts WHERE server_id=?", (server_id,))
                connection.execute("INSERT INTO scheduled_restarts(server_id, discord_user_id, interval_seconds, next_run_at, all_servers, enabled) VALUES (?,?,?,?,1,1)", (server_id, str(interaction.user.id), seconds, next_run))
        node_note = f" on node **{node.split(':', 1)[1] if node and ':' in node else node_id}**" if node_id else ""
        await interaction.followup.send(embed=branded_embed("One-Time Restarts Scheduled", f"Scheduled **{len(rows)}** server(s){node_note} to restart once in **{time}**."), ephemeral=True)
        return
    if node:
        raise RuntimeError("The node option is only used with all_servers:True.")
    if not server:
        raise RuntimeError("Select one server, or admins can set all_servers:True inside the Discord server.")
    row = await admin_or_owner_server(interaction, server)
    identifier = await require_client_identifier(row)
    with db() as connection:
        connection.execute("DELETE FROM scheduled_restarts WHERE server_id=?", (server,))
        connection.execute("INSERT INTO scheduled_restarts(server_id, discord_user_id, interval_seconds, next_run_at, all_servers, enabled) VALUES (?,?,?,?,0,1)", (server, str(interaction.user.id), seconds, next_run))
    await interaction.followup.send(embed=branded_embed("One-Time Restart Scheduled", f"**{record_value(row, 'name', server)}** (`{identifier}`) will restart once in **{time}**."), ephemeral=True)


@tree.command(name="renew", description="Admin renew server")
@admin_only()
@app_commands.autocomplete(server=admin_tracked_server_autocomplete)
async def renew(interaction: discord.Interaction, server: str, time: str) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await ensure_server_access(interaction, server, allow_admin=True)
    tracked_row = fetch_server(server)
    seconds = parse_duration(time)
    stored_expiry = record_value(row, "expires_at")
    current_expiry = datetime.fromisoformat(str(stored_expiry)) if stored_expiry else utc_now()
    base = current_expiry if current_expiry > utc_now() else utc_now()
    new_expiry = base + timedelta(seconds=seconds)
    if tracked_row:
        with db() as connection:
            connection.execute("UPDATE servers SET expires_at=?, suspended=0, autosuspend_enabled=1, autosuspend_seconds=? WHERE server_id=?", (new_expiry.isoformat(), seconds, server))
        if str(server) in database.get("servers", {}):
            database["servers"][str(server)].update({"expires_at": new_expiry.isoformat(), "suspended": False, "autosuspend_enabled": True, "autosuspend_seconds": seconds})
            save_database()
        clear_server_notifications(server)
    saga_synced = await (await ready_application_client_for(row)).set_saga_auto_suspend(server, new_expiry)
    try:
        row = await ensure_server_access(interaction, server, allow_admin=True)
        await (await ready_application_client_for(row)).unsuspend_server(server)
    except RuntimeError:
        pass
    discord_user_id = record_value(row, "discord_user_id")
    if discord_user_id:
        try:
            user = await client.fetch_user(int(discord_user_id))
            await user.send(embed=branded_embed("Service Renewed", f"Your server **{record_value(row, 'name', server)}** was renewed until <t:{int(new_expiry.timestamp())}:F>."))
        except Exception:
            pass
    warning_note = "No renewal warning due"
    if tracked_row:
        refreshed_row = fetch_server(server)
        if refreshed_row:
            sent_warnings = await send_due_suspension_warnings(refreshed_row)
            if sent_warnings:
                warning_note = "Sent " + ", ".join(expiration_warning_lead(notification_type) for notification_type in sent_warnings) + " renewal warning(s)"
    tracking_note = "Local DB updated" if tracked_row else "Panel/Saga updated only (server is not locally tracked)"
    await interaction.followup.send(embed=branded_embed("Server Renewed", f"**{record_value(row, 'name', server)}** renewed by **{time}**.\nNext expiry: <t:{int(new_expiry.timestamp())}:F>\nTracking: **{tracking_note}**\nWarnings: **{warning_note}**\nSaga auto suspension: **{'synced' if saga_synced else 'not synced'}**"), ephemeral=True)
    await send_server_event_log(row, "Server Renewed", f"**{record_value(row, 'name', server)}** (`{server}`) renewed by **{time}**.\nNext expiry: <t:{int(new_expiry.timestamp())}:F>\nTracking: **{tracking_note}**\nSaga auto suspension: **{'synced' if saga_synced else 'not synced'}**", actor=interaction.user, color=0x00d4ff)


@tree.command(name="delete", description="Admin delete one server")
@admin_only()
@app_commands.autocomplete(server=admin_tracked_server_autocomplete)
async def delete(interaction: discord.Interaction, server: str, confirm: bool = False) -> None:
    await interaction.response.defer(ephemeral=True)
    row = fetch_server(server)
    if not row:
        row = panel_server_record(await ptero.get_server(server), "panel")
    if not confirm:
        await interaction.followup.send(embed=branded_embed("Confirm Delete", f"Run `/delete server:{server} confirm:True` to permanently delete **{record_value(row, 'name', server)}**.", 0xffcc00), ephemeral=True)
        return
    await (await ready_application_client_for(row)).delete_server(server)
    mark_server_deleted(server)
    await interaction.followup.send(embed=branded_embed("Server Deleted", f"Deleted **{record_value(row, 'name', server)}** (`{server}`).", 0xe74c3c), ephemeral=True)
    await send_server_event_log(row, "Server Deleted", f"Deleted **{record_value(row, 'name', server)}** (`{server}`).", actor=interaction.user, color=0xe74c3c)


@tree.command(name="power", description="Power server")
@app_commands.autocomplete(server=accessible_server_autocomplete)
@app_commands.choices(action=[app_commands.Choice(name="start", value="start"), app_commands.Choice(name="stop", value="stop"), app_commands.Choice(name="restart", value="restart")])
async def power(interaction: discord.Interaction, server: str, action: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await admin_or_owner_server(interaction, server)
    identifier = await require_client_identifier(row)
    ensure_not_suspended(row, "power controls")
    await (await ready_client_api_for(row)).power(identifier, action.value)
    await interaction.followup.send(embed=branded_embed("Power Signal Sent", f"Sent **{action.value}** to **{record_value(row, 'name', server)}**."), ephemeral=True)


@tree.command(name="reinstall", description="Reinstall server")
@app_commands.autocomplete(server=accessible_server_autocomplete)
async def reinstall(interaction: discord.Interaction, server: str) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await admin_or_owner_server(interaction, server)
    ensure_not_suspended(row, "reinstall")
    await (await ready_application_client_for(row)).reinstall_server(server)
    await interaction.followup.send(embed=branded_embed("Reinstall Started", f"Reinstall started for **{record_value(row, 'name', server)}**."), ephemeral=True)


@tree.command(name="change-egg", description="Change server egg")
@app_commands.autocomplete(server=accessible_server_autocomplete, nest=nest_autocomplete, egg=egg_autocomplete)
async def change_egg(interaction: discord.Interaction, server: str, nest: str, egg: str, wipe_files: bool = False, reinstall: bool = True) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await admin_or_owner_server(interaction, server)
    nest_id = parse_id(nest)
    egg_id = parse_id(egg)
    if nest_id <= 0 or egg_id <= 0:
        raise RuntimeError("Select a real nest and egg from autocomplete before changing the server egg.")
    nest_name = nest.split(":", 1)[1] if ":" in nest else f"Nest {nest_id}"
    egg_name = egg.split(":", 1)[1] if ":" in egg else f"Egg {egg_id}"
    ensure_not_suspended(row, "egg changes")
    panel = await ready_application_client_for(row)
    await panel.change_server_egg(server, nest_id, egg_id)
    reinstall_requested = reinstall or wipe_files
    if reinstall_requested:
        await panel.reinstall_server(server)
    if fetch_server(server):
        with db() as connection:
            connection.execute("UPDATE servers SET nest_id=?, nest_name=?, egg_id=?, egg_name=? WHERE server_id=?", (nest_id, nest_name, egg_id, egg_name, server))
    wipe_note = "Wipe requested; Pterodactyl reinstall was started so files will be rebuilt by the panel." if wipe_files else "Files were left in place unless the panel reinstall process changes them."
    reinstall_note = "Reinstall started." if reinstall_requested else "Reinstall was not started."
    await interaction.followup.send(embed=branded_embed("Egg Changed", f"**{record_value(row, 'name', server)}** is now set to **{nest_name} / {egg_name}**.\n{reinstall_note}\n{wipe_note}"), ephemeral=True)


@tree.command(name="resize", description="Resize server")
@admin_only()
@app_commands.autocomplete(server=admin_tracked_server_autocomplete)
async def resize(interaction: discord.Interaction, server: str) -> None:
    await interaction.response.send_modal(ResizeModal(server, allow_admin=True))


@tree.command(name="suspend", description="Suspend server")
@admin_only()
@app_commands.autocomplete(server=admin_tracked_server_autocomplete)
async def suspend(interaction: discord.Interaction, server: str | None = None, user: discord.User | None = None, email: str | None = None, all_except_whitelist_paid: bool = False) -> None:
    await interaction.response.defer(ephemeral=True)
    if all_except_whitelist_paid:
        suspended = 0
        for row in fetch_all_servers():
            if row["plan"] == "paid" or is_whitelisted(row["server_id"]):
                continue
            await (await ready_application_client_for(row)).suspend_server(row["server_id"])
            with db() as connection:
                connection.execute("UPDATE servers SET suspended=1 WHERE server_id=?", (row["server_id"],))
            suspended += 1
        await interaction.followup.send(embed=branded_embed("Bulk Suspend Complete", f"Suspended **{suspended}** non-paid, non-whitelisted server(s)."), ephemeral=True)
        return

    if server:
        row = await ensure_server_access(interaction, server, allow_admin=True)
        await (await ready_application_client_for(row)).suspend_server(server)
        with db() as connection:
            connection.execute("UPDATE servers SET suspended=1 WHERE server_id=?", (server,))
        await interaction.followup.send(embed=branded_embed("Server Suspended", f"Suspended **{record_value(row, 'name', server)}**."), ephemeral=True)
        await send_server_event_log(row, "Server Suspended", f"Suspended **{record_value(row, 'name', server)}** (`{server}`).", actor=interaction.user, color=0xe67e22)
        return

    rows = fetch_user_servers(user.id) if user else fetch_servers_by_email(email) if email else []
    if not rows:
        await interaction.followup.send(embed=branded_embed("No Servers Found", "Provide `server`, `user`, `email`, or `all_except_whitelist_paid:True`.", 0xffcc00), ephemeral=True)
        return
    await interaction.followup.send(embed=branded_embed("Select Server To Suspend", "Choose one of the matched servers below."), view=SuspendSelect(rows), ephemeral=True)


@tree.command(name="unsuspend", description="Unsuspend server")
@admin_only()
@app_commands.autocomplete(server=admin_tracked_server_autocomplete)
async def unsuspend(interaction: discord.Interaction, server: str) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await ensure_server_access(interaction, server, allow_admin=True)
    await (await ready_application_client_for(row)).unsuspend_server(server)
    with db() as connection:
        connection.execute("UPDATE servers SET suspended=0 WHERE server_id=?", (server,))
    await interaction.followup.send(embed=branded_embed("Server Unsuspended", f"Unsuspended server `{server}`."), ephemeral=True)
    await send_server_event_log(row, "Server Unsuspended", f"Unsuspended **{record_value(row, 'name', server)}** (`{server}`).", actor=interaction.user, color=0x2ecc71)


@tree.command(name="stopall", description="Stop all except whitelist")
@admin_only()
async def stopall(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    stopped = 0
    skipped_suspended = 0
    skipped_whitelisted = 0
    skipped_missing_identifier = 0
    for row in fetch_all_servers():
        if is_whitelisted(row["server_id"]):
            skipped_whitelisted += 1
            continue
        if not row["identifier"]:
            skipped_missing_identifier += 1
            continue
        live_row = await refresh_tracked_server(row) or row
        if server_is_suspended(live_row):
            skipped_suspended += 1
            continue
        await (await ready_client_api_for(live_row)).power(live_row["identifier"], "stop")
        stopped += 1
    await interaction.followup.send(embed=branded_embed("Stop All Complete", f"Stopped **{stopped}** server(s).\nSkipped: **{skipped_whitelisted}** whitelisted • **{skipped_suspended}** suspended • **{skipped_missing_identifier}** missing client identifier."), ephemeral=True)


@tree.command(name="autobackup-enable", description="Enable autobackups")
@admin_only()
@app_commands.autocomplete(server=admin_tracked_server_autocomplete)
async def autobackup_enable(interaction: discord.Interaction, server: str, every: str) -> None:
    await interaction.response.defer(ephemeral=True)
    seconds = parse_duration(every)
    next_run = (utc_now() + timedelta(seconds=seconds)).isoformat()
    with db() as connection:
        connection.execute("INSERT OR REPLACE INTO autobackups VALUES (?,?,?,1)", (server, seconds, next_run))
    await interaction.followup.send(embed=branded_embed("Autobackup Enabled", f"Server `{server}` will back up every **{every}**."), ephemeral=True)




def pct_bar(percent: float) -> str:
    filled = max(0, min(10, round(percent / 10)))
    return "█" * filled + "░" * (10 - filled)


def node_capacity_value(node: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = node.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def server_status_label(record: sqlite3.Row | dict[str, Any]) -> str:
    status = str(record_value(record, "status", "") or "").strip().lower()
    if not status and panel_server_is_suspended(dict(record) if isinstance(record, dict) else {key: record[key] for key in record.keys()}):
        status = "suspended"
    return status


def node_dashboard_field(node: dict[str, Any], records: list[sqlite3.Row | dict[str, Any]]) -> tuple[str, str]:
    node_id = int(node.get("id") or 0)
    node_records = [record for record in records if int(record_value(record, "node_id", 0) or 0) == node_id]
    memory_total = node_capacity_value(node, "memory", "memory_limit")
    disk_total = node_capacity_value(node, "disk", "disk_limit")
    memory_used = sum(int(record_value(record, "ram", 0) or 0) for record in node_records)
    disk_used = sum(int(record_value(record, "disk", 0) or 0) for record in node_records)
    memory_pct = (memory_used / memory_total * 100) if memory_total else 0
    disk_pct = (disk_used / disk_total * 100) if disk_total else 0
    statuses = [server_status_label(record) for record in node_records]
    suspended = sum(1 for status in statuses if "suspend" in status)
    crashing = sum(1 for status in statuses if any(word in status for word in ("crash", "error", "failed", "offline")))
    maintenance = bool(node.get("maintenance_mode") or node.get("maintenance"))
    overloaded = memory_pct >= 90 or disk_pct >= 90
    state = "🟡 Maintenance" if maintenance else "🔴 Warning" if overloaded or crashing else "🟢 Online"
    warnings: list[str] = []
    if overloaded:
        warnings.append("⚠️ overloaded")
    if crashing:
        warnings.append(f"💥 {crashing} crashing/error")
    if maintenance:
        warnings.append("🛠️ maintenance")
    warning_line = " • ".join(warnings) if warnings else "✅ healthy"
    value = (
        f"Status: **{state}** • {warning_line}\n"
        f"Servers: **{len(node_records)}** live • Suspended: **{suspended}**\n"
        f"Memory: `{memory_used:,}/{memory_total:,} MB` **{memory_pct:.1f}%**\n`{pct_bar(memory_pct)}`\n"
        f"Disk: `{disk_used:,}/{disk_total:,} MB` **{disk_pct:.1f}%**\n`{pct_bar(disk_pct)}`"
    )
    return f"{state.split(' ', 1)[0]} {node.get('name', f'Node {node_id}')} (`{node_id}`)", value






def node_daemon_url(node: dict[str, Any]) -> str | None:
    fqdn = str(node.get("fqdn") or node.get("address") or "").strip()
    if not fqdn:
        return None
    scheme = str(node.get("scheme") or "https").strip() or "https"
    port = int(node.get("daemon_listen") or node.get("daemon_port") or 8080)
    return f"{scheme}://{fqdn}:{port}/api/system"


async def node_daemon_reachable(node: dict[str, Any]) -> bool:
    url = node_daemon_url(node)
    if not url:
        return False
    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, ssl=False) as response:
                # 200 means the daemon answered; 401/403 also means Wings is reachable
                # but requires auth. Connection errors/timeouts mean offline.
                return response.status < 500
    except Exception:
        return False


def format_downtime(down_since: str | None, ended_at: datetime | None = None) -> str | None:
    if not down_since:
        return None
    try:
        started = datetime.fromisoformat(down_since)
    except ValueError:
        return None
    ended = ended_at or utc_now()
    return describe_duration(int((ended - started).total_seconds()))


def node_health_summary(node: dict[str, Any], records: list[sqlite3.Row | dict[str, Any]], daemon_online: bool | None = None) -> tuple[str, str, int, float, float]:
    node_id = int(node.get("id") or 0)
    node_records = [record for record in records if int(record_value(record, "node_id", 0) or 0) == node_id]
    memory_total = node_capacity_value(node, "memory", "memory_limit")
    disk_total = node_capacity_value(node, "disk", "disk_limit")
    memory_used = sum(int(record_value(record, "ram", 0) or 0) for record in node_records)
    disk_used = sum(int(record_value(record, "disk", 0) or 0) for record in node_records)
    memory_pct = (memory_used / memory_total * 100) if memory_total else 0
    disk_pct = (disk_used / disk_total * 100) if disk_total else 0
    statuses = [server_status_label(record) for record in node_records]
    crashing = sum(1 for status in statuses if any(word in status for word in ("crash", "error", "failed", "offline")))
    maintenance = bool(node.get("maintenance_mode") or node.get("maintenance"))
    overloaded = memory_pct >= 90 or disk_pct >= 90
    state = "offline" if daemon_online is False else "maintenance" if maintenance else "warning" if overloaded or crashing else "online"
    daemon_text = "offline" if daemon_online is False else "online" if daemon_online is True else "unknown"
    summary = f"daemon={daemon_text} servers={len(node_records)} memory={memory_pct:.1f}% disk={disk_pct:.1f}% crashing={crashing}"
    return state, summary, len(node_records), memory_pct, disk_pct


async def send_node_status_webhook(node: dict[str, Any], previous_state: str | None, state: str, summary: str, server_count: int, memory_pct: float, disk_pct: float, downtime: str | None = None) -> None:
    webhook_url = node_status_webhook_url()
    if not webhook_url:
        return
    node_id = str(node.get("id", "unknown"))
    node_name = str(node.get("name", f"Node {node_id}"))
    color = 0x2ecc71 if state == "online" else 0xf1c40f if state == "maintenance" else 0xe74c3c
    emoji = "🟢" if state == "online" else "🟡" if state == "maintenance" else "🔴"
    transition = f"{previous_state or 'new'} → {state}"
    fields = [
        {"name": "Transition", "value": transition, "inline": True},
        {"name": "Live servers", "value": str(server_count), "inline": True},
        {"name": "Load", "value": f"Memory **{memory_pct:.1f}%** • Disk **{disk_pct:.1f}%**", "inline": False},
        {"name": "Summary", "value": summary, "inline": False},
    ]
    if downtime:
        fields.insert(2, {"name": "Downtime", "value": downtime, "inline": True})
    payload = {
        "username": f"{BRAND} Node Watch",
        "embeds": [{
            "title": f"{emoji} Node Status Changed",
            "description": f"**{node_name}** (`{node_id}`) is now **{state.upper()}**",
            "color": color,
            "fields": fields,
            "footer": {"text": f"{BRAND} • Developer: {DEVELOPER}"},
            "timestamp": utc_now().isoformat(),
        }],
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(webhook_url, json=payload) as response:
            if response.status >= 400:
                text = await response.text()
                print(f"Node status webhook failed for node {node_id}: HTTP {response.status} {text[:300]}")


@tree.command(name="nodes", description="Show live node status")
@admin_only()
async def nodes(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    panel_nodes = await ptero.list_nodes()
    records = list((await fetch_live_panel_records()).values())
    if not panel_nodes:
        await interaction.followup.send(embed=branded_embed("Live Node Status", "No nodes found."), ephemeral=True)
        return
    embeds: list[discord.Embed] = []
    pages = chunked(panel_nodes, 5)
    total_servers = len(records)
    for page_number, page in enumerate(pages, start=1):
        embed = branded_embed("🚀 Live Node Status", f"Real-time panel allocation view across **{len(panel_nodes)}** node(s) and **{total_servers}** live server(s).")
        for node in page:
            name, value = node_dashboard_field(node, records)
            embed.add_field(name=name, value=value, inline=False)
        embed.set_footer(text=f"{BRAND} • Page {page_number}/{len(pages)} • 🟢 healthy / 🔴 warning / 🟡 maintenance • Developer: {DEVELOPER}")
        embeds.append(embed)
    if len(embeds) > 1:
        await interaction.followup.send(embed=embeds[0], view=PaginatedEmbeds(embeds), ephemeral=True)
    else:
        await interaction.followup.send(embed=embeds[0], ephemeral=True)


@tree.command(name="whitelist", description="Admin: manage or list whitelisted servers")
@admin_only()
@app_commands.autocomplete(server=whitelist_server_autocomplete)
@app_commands.choices(action=[app_commands.Choice(name="add", value="add"), app_commands.Choice(name="remove", value="remove"), app_commands.Choice(name="list", value="list")])
async def whitelist(interaction: discord.Interaction, action: app_commands.Choice[str], server: str | None = None) -> None:
    await interaction.response.defer(ephemeral=True)
    if action.value == "list":
        protected_ids = whitelist_values()
        records = await fetch_live_panel_records()
        active_identifiers = {
            str(record_value(record, field, ""))
            for record in records.values()
            for field in ("server_id", "uuid", "identifier")
            if record_value(record, field, "")
        }
        stale_entries = protected_ids - active_identifiers
        if stale_entries:
            remove_whitelist_entries(stale_entries)
            save_database()
            protected_ids -= stale_entries
        entries: list[tuple[str, str]] = []
        for key, record in sorted(records.items(), key=lambda item: (panel_label_for_plan(str(record_value(item[1], "plan", "paid"))), str(record_value(item[1], "name", "")))):
            record_identifiers = {
                str(record_value(record, field, ""))
                for field in ("server_id", "uuid", "identifier")
                if record_value(record, field, "")
            }
            plan = str(record_value(record, "plan", "")).lower()
            is_manual = bool(record_identifiers & protected_ids)
            if not is_manual:
                continue
            server_id = str(record_value(record, "server_id", key.split(":", 1)[-1]))
            name = clean(str(record_value(record, "name", server_id)), 80)
            panel_label = panel_label_for_plan(plan)
            entries.append(("Whitelisted", f"**{name}** (`{server_id}`)\nPanel: **{panel_label}** • Source: **manual whitelist**"))
        if not entries:
            await interaction.followup.send(embed=branded_embed("Whitelisted Servers", "No manually whitelisted servers found."), ephemeral=True)
            return
        embeds: list[discord.Embed] = []
        pages = chunked(entries, 10)
        for page_number, page in enumerate(pages, start=1):
            embed = branded_embed("Whitelisted Servers", "Only manually whitelisted servers are shown here. Paid servers remain protected automatically but are not listed here.")
            for category, label in page:
                embed.add_field(name=category, value=label, inline=False)
            embed.set_footer(text=f"{BRAND} • Page {page_number}/{len(pages)} • {len(entries)} protected servers • Developer: {DEVELOPER}")
            embeds.append(embed)
        if len(embeds) > 1:
            await interaction.followup.send(embed=embeds[0], view=PaginatedEmbeds(embeds), ephemeral=True)
        else:
            await interaction.followup.send(embed=embeds[0], ephemeral=True)
        return
    live_records = await fetch_live_panel_records()
    if not server:
        candidates = whitelist_candidate_records(live_records, action.value)
        if not candidates:
            message = "No unwhitelisted, non-paid live panel servers found." if action.value == "add" else "No manually whitelisted live panel servers found."
            await interaction.followup.send(embed=branded_embed("No Whitelist Candidates", message, 0xffcc00), ephemeral=True)
            return
        embeds = whitelist_candidate_embeds(action.value, candidates)
        if len(embeds) > 1:
            await interaction.followup.send(embed=embeds[0], view=PaginatedEmbeds(embeds), ephemeral=True)
        else:
            await interaction.followup.send(embed=embeds[0], ephemeral=True)
        return
    record = live_records.get(str(server))
    if not record:
        raise RuntimeError("That server was not found on the live panel, so it cannot be whitelisted.")
    identifiers = server_identifiers(record, server)
    protected_ids = whitelist_values()
    whitelist_set = set(str(item) for item in database.setdefault("whitelist", []))
    if action.value == "add":
        if str(record_value(record, "plan", "")).lower() == "paid":
            raise RuntimeError("Paid servers are already protected automatically and do not need manual whitelist entries.")
        if identifiers & protected_ids:
            raise RuntimeError("That server is already manually whitelisted, so it is hidden from whitelist add choices.")
        whitelist_set.add(str(record_value(record, "server_id", server)))
        database["whitelist"] = sorted(whitelist_set, key=str)
        save_database()
        with db() as connection:
            connection.execute("INSERT OR IGNORE INTO whitelist(server_id) VALUES (?)", (str(record_value(record, "server_id", server)),))
    else:
        if not identifiers & protected_ids:
            raise RuntimeError("That server is not manually whitelisted, so it is hidden from whitelist remove choices.")
        remove_whitelist_entries(identifiers)
        save_database()
    server_name = record_value(record, "name", server) if record else server
    await interaction.followup.send(embed=branded_embed("Whitelist Updated", f"Action: **{action.value}**\nServer: **{server_name}**\nWhitelist count: **{len(whitelist_values())}**"), ephemeral=True)


@tree.command(name="purge", description="Admin: purge live panel servers except paid, whitelisted, or prefix-skipped servers")
@admin_only()
@app_commands.describe(skip_keyword="Optional name prefix to skip, for example smp skips names starting smp or [smp].")
async def purge(interaction: discord.Interaction, confirm: bool = False, skip_keyword: str | None = None) -> None:
    await interaction.response.defer(ephemeral=True)
    normalized_skip = skip_keyword.strip() if skip_keyword else ""
    normalized_core = normalized_skip.strip("[]")
    bracketed_skip = f"[{normalized_core}]" if normalized_core else ""
    keyword_note = f" Servers starting with `{normalized_core}` or `{bracketed_skip}` will also be skipped." if normalized_core else ""
    if not ptero.session:
        await ptero.start()
    panel_servers = await ptero.list_servers()
    tracked_by_id = {str(row["server_id"]): row for row in fetch_all_servers()}
    victims: list[sqlite3.Row | dict[str, Any]] = []
    skipped = 0
    for listed_server in panel_servers:
        server_id = str(listed_server["id"])
        try:
            panel_server = await ptero.get_server(server_id)
        except RuntimeError:
            panel_server = listed_server
        tracked = tracked_by_id.get(server_id)
        record = tracked or panel_server_record(panel_server, "panel")
        if tracked:
            panel_user_id = int(panel_server.get("user") or 0)
            panel_user = await ptero.get_user(panel_user_id, refresh=True) if panel_user_id else None
            update_tracked_server_from_panel(server_id, panel_server, panel_user.get("email") if panel_user else None)
            record = fetch_server(server_id) or record
        if is_server_protected(record) or matches_purge_skip_keyword(str(record_value(record, "name", server_id)), skip_keyword):
            skipped += 1
            continue
        victims.append(record)
    if not confirm:
        preview = "\n".join(f"{record_value(record, 'name', record_value(record, 'server_id'))} (`{record_value(record, 'server_id')}`)" for record in victims[:10])
        description = f"Found **{len(victims)}** unprotected live panel server(s) that would be deleted. Skipped **{skipped}** paid/whitelisted/protected server(s).{keyword_note}\nRun `/purge confirm:True` to delete them."
        if preview:
            description += "\n\nWill delete:\n" + preview
        await interaction.followup.send(embed=branded_embed("Confirmation Required", description, 0xffcc00), ephemeral=True)
        return
    deleted = []
    failed = []
    for record in victims:
        server_id = str(record_value(record, "server_id"))
        try:
            await ptero.delete_server(server_id)
            deleted.append(f"{record_value(record, 'name', server_id)} (`{server_id}`)")
            mark_server_deleted(server_id)
            clear_server_notifications(server_id)
        except RuntimeError as error:
            failed.append(f"{server_id}: {error}")
    save_database()
    skipped_reason = "paid/whitelisted/protected/prefix-matched" if normalized_core else "paid/whitelisted/protected"
    skip_line = f"\nSkipped name prefix: **{normalized_core}**" if normalized_core else ""
    description = f"Deleted panel servers: **{len(deleted)}**\nSkipped {skipped_reason} servers: **{skipped}**.{skip_line}"
    if deleted:
        description += "\n\n" + "\n".join(deleted[:15])
    if failed:
        description += "\n\nFailures:\n" + "\n".join(failed[:5])
    await interaction.followup.send(embed=branded_embed("Panel Server Purge Complete", description, 0xe74c3c), ephemeral=True)


@tree.command(name="autosuspend", description="Toggle autosuspend")
@admin_only()
@app_commands.autocomplete(server=admin_tracked_server_autocomplete)
@app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
async def autosuspend(interaction: discord.Interaction, server: str, state: app_commands.Choice[str] | None = None, time: str | None = None) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await ensure_server_access(interaction, server, allow_admin=True)
    tracked_row = fetch_server(server)
    if state is None:
        enabled = bool(int(record_value(row, "autosuspend_enabled", 0) or 0))
        stored_expiry = record_value(row, "expires_at")
        configured_seconds = record_value(row, "autosuspend_seconds")
        created_at_value = record_value(row, "created_at")
        details = [
            f"Server: **{record_value(row, 'name', server)}** (`{server}`)",
            f"Auto suspend: **{'ON' if enabled else 'OFF'}**",
        ]
        if stored_expiry:
            expires_at = datetime.fromisoformat(str(stored_expiry))
            remaining = expires_at - utc_now()
            details.append(f"Suspension time: <t:{int(expires_at.timestamp())}:F> (<t:{int(expires_at.timestamp())}:R>)")
            details.append(f"Time remaining: **{describe_duration(int(remaining.total_seconds()))}**")
            if configured_seconds:
                details.append(f"Configured period: **{describe_duration(int(configured_seconds))}**")
            elif created_at_value:
                created_at = datetime.fromisoformat(str(created_at_value))
                configured_period = expires_at - created_at
                details.append(f"Configured period: **{describe_duration(int(configured_period.total_seconds()))}**")
        else:
            details.append("Suspension time: **not set**")
            details.append("Configured period: **unknown for panel-only server**")
        await interaction.followup.send(embed=branded_embed("Autosuspend Details", "\n".join(details)), ephemeral=True)
        return
    enabled = 1 if state.value == "on" else 0
    expires_at: datetime | None = None
    configured_seconds: int | None = None
    if enabled:
        if time:
            configured_seconds = parse_duration(time)
            expires_at = utc_now() + timedelta(seconds=configured_seconds)
        else:
            stored_expiry = record_value(row, "expires_at")
            if not stored_expiry:
                raise RuntimeError("This panel server is not tracked by the bot yet. Provide `time` like `30d`, `12h`, or `1d6h` so Saga can receive an expiration date.")
            expires_at = datetime.fromisoformat(str(stored_expiry))
    if tracked_row:
        with db() as connection:
            if expires_at and time:
                connection.execute("UPDATE servers SET autosuspend_enabled=?, expires_at=?, suspended=0, autosuspend_seconds=? WHERE server_id=?", (enabled, expires_at.isoformat(), configured_seconds, server))
            else:
                connection.execute("UPDATE servers SET autosuspend_enabled=? WHERE server_id=?", (enabled, server))
        if str(server) in database.get("servers", {}):
            mirror_update = {"autosuspend_enabled": bool(enabled)}
            if expires_at and time:
                mirror_update.update({"expires_at": expires_at.isoformat(), "suspended": False, "autosuspend_seconds": configured_seconds})
            database["servers"][str(server)].update(mirror_update)
            save_database()
    saga_synced = await (await ready_application_client_for(row)).set_saga_auto_suspend(server, expires_at if enabled else None)
    expiry_line = f"\nExpiration: <t:{int(expires_at.timestamp())}:F>" if expires_at else ""
    await interaction.followup.send(embed=branded_embed("Autosuspend Updated", f"Automatic expiration suspension for **{record_value(row, 'name', server)}** is now **{state.value.upper()}**.{expiry_line}\nSaga auto suspension: **{'synced' if saga_synced else 'cleared/not synced'}**"), ephemeral=True)


@tree.command(name="deletesuspended", description="Delete suspended servers")
@admin_only()
@app_commands.choices(plan=[app_commands.Choice(name="free", value="free"), app_commands.Choice(name="paid", value="paid"), app_commands.Choice(name="all", value="all")])
async def deletesuspended(interaction: discord.Interaction, plan: app_commands.Choice[str], confirm: bool = False) -> None:
    await interaction.response.defer(ephemeral=True)
    if not ptero.session:
        await ptero.start()
    panel_servers = await ptero.list_servers()
    tracked_by_id = {str(row["server_id"]): row for row in fetch_all_servers()}
    victims: list[sqlite3.Row | dict[str, Any]] = []
    for listed_server in panel_servers:
        server_id = str(listed_server["id"])
        try:
            panel_server = await ptero.get_server(server_id)
        except RuntimeError:
            panel_server = listed_server
        if not panel_server_is_suspended(panel_server):
            continue
        tracked = tracked_by_id.get(server_id)
        if tracked:
            panel_user_id = int(panel_server.get("user") or 0)
            panel_user = await ptero.get_user(panel_user_id, refresh=True) if panel_user_id else None
            update_tracked_server_from_panel(server_id, panel_server, panel_user.get("email") if panel_user else None)
            tracked = fetch_server(server_id) or tracked
        record = tracked or panel_server_record(panel_server, "panel")
        record_plan = str(record_value(record, "plan", "panel")).lower()
        if plan.value != "all" and record_plan != plan.value:
            continue
        victims.append(record)
    if not confirm:
        await interaction.followup.send(embed=branded_embed("Confirm Suspended Delete", f"Found **{len(victims)}** live suspended **{plan.value}** server(s). Run `/deletesuspended plan:{plan.value} confirm:True` to permanently delete them from the panel.", 0xffcc00), ephemeral=True)
        return
    deleted: list[str] = []
    failed: list[str] = []
    for row in victims:
        server_id = str(record_value(row, "server_id"))
        try:
            await ptero.delete_server(server_id)
            mark_server_deleted(server_id)
            clear_server_notifications(server_id)
            deleted.append(f"`{server_id}` • {record_value(row, 'name', server_id)}")
        except Exception as error:
            failed.append(f"`{server_id}` • {clean(str(error), 120)}")
    save_database()
    description = f"Deleted **{len(deleted)}** live suspended server(s) for plan **{plan.value}**."
    if deleted:
        description += "\n\n" + "\n".join(deleted[:15])
    if failed:
        description += "\n\nFailures:\n" + "\n".join(failed[:5])
    await interaction.followup.send(embed=branded_embed("Suspended Delete Complete", description, 0xe74c3c), ephemeral=True)


@tree.command(name="server-expirations", description="Show expirations")
@admin_only()
async def server_expirations(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    rows = []
    for record in (await fetch_live_panel_records(enrich_owner=True)).values():
        server_id = str(record_value(record, "server_id", ""))
        stored_expiry = record_value(record, "expires_at")
        expiry_text = "panel-created / not tracked"
        if stored_expiry:
            expires = int(datetime.fromisoformat(str(stored_expiry)).timestamp())
            expiry_text = f"<t:{expires}:R>"
        flag = "⭐ whitelisted" if server_identifiers(record) & whitelist_values() else record_value(record, "plan", "panel")
        auto_value = record_value(record, "autosuspend_enabled")
        auto = "autosuspend:unknown" if auto_value is None else "autosuspend:on" if bool(auto_value) else "autosuspend:off"
        rows.append(f"`{server_id}` • **{record_value(record, 'name', server_id)}** • {flag} • {auto} • {expiry_text}")
    await interaction.followup.send(embed=branded_embed("Server Expirations", "\n".join(rows[:25]) or "No live panel servers."), ephemeral=True)


def expiration_warning_lead(notification_type: str | None) -> str:
    return "7 days" if notification_type == "suspend_7d" else "24 hours"


def warning_threshold_reached(now: datetime, expires_at: datetime, lead: timedelta) -> bool:
    due_at = expires_at - lead
    # The lifecycle task runs once per minute, so allow a small grace window after
    # the exact threshold timestamp without sending broad "any time inside the
    # window" reminders. This keeps paid 7-day and all-plan 24-hour alerts tied
    # to their scheduled send time.
    return due_at <= now < due_at + timedelta(seconds=90)


def due_suspension_warnings(record: sqlite3.Row, now: datetime, expires_at: datetime) -> list[tuple[str, timedelta]]:
    if now > expires_at:
        return []
    warnings: list[tuple[str, timedelta]] = []
    if str(record["plan"]).lower() == "paid" and warning_threshold_reached(now, expires_at, timedelta(days=7)):
        warnings.append(("suspend_7d", timedelta(days=7)))
    if warning_threshold_reached(now, expires_at, timedelta(days=1)):
        warnings.append(("suspend_1d", timedelta(days=1)))
    return warnings


async def send_due_suspension_warnings(record: sqlite3.Row, now: datetime | None = None) -> list[str]:
    now = now or utc_now()
    expires_at = datetime.fromisoformat(record["expires_at"])
    sent: list[str] = []
    for notification_type, _window in due_suspension_warnings(record, now, expires_at):
        if notification_sent(record["server_id"], notification_type):
            continue
        if await send_lifecycle_dm(record, "suspension_warning", expires_at, notification_type):
            mark_notification_sent(record["server_id"], notification_type)
            sent.append(notification_type)
    return sent


async def send_lifecycle_dm(record: sqlite3.Row, event: str, when: datetime, notification_type: str | None = None) -> bool:
    user: discord.User | None = None
    try:
        discord_user_id = int(record["discord_user_id"])
        user = await client.fetch_user(discord_user_id)
    except Exception as error:
        print(f"Failed to fetch lifecycle DM user for server {record['server_id']}: {error}")
    plan = str(record["plan"]).lower()
    server_name = record["name"]
    timestamp = int(when.timestamp())
    if event == "suspension_warning":
        lead = expiration_warning_lead(notification_type)
        if plan == "paid":
            title = "Paid Service Renewal Reminder"
            message = f"Your paid server **{server_name}** is scheduled for suspension in **{lead}** at <t:{timestamp}:F>. Please renew by clearing the recurring amount due to keep your service active."
        else:
            title = "Free Server Renewal Reminder"
            message = f"Your free server **{server_name}** will be suspended in **{lead}** at <t:{timestamp}:F>. Please renew your server if you still need it."
    elif event == "suspended":
        title = "Server Suspended"
        message = f"Your server **{server_name}** expired and has been suspended. It will be deleted after 7 days if it is not renewed."
    elif event == "delete_warning":
        title = "Server Deletion Warning"
        if plan == "paid":
            message = f"Your paid server **{server_name}** is scheduled for permanent deletion in **24 hours** at <t:{timestamp}:F>. Please renew by clearing the recurring amount due to avoid losing the server."
        else:
            message = f"Your free server **{server_name}** is scheduled for permanent deletion in **24 hours** at <t:{timestamp}:F>. Please renew your server if you want to keep it."
    else:
        title = "Server Deleted"
        message = f"Your server **{server_name}** was permanently deleted after remaining suspended for 7 days."
    embed = branded_embed(title, message, 0xe67e22 if event != "deleted" else 0xe74c3c)
    delivered = False
    if user:
        try:
            await user.send(embed=embed)
            delivered = True
        except discord.Forbidden:
            print(f"Lifecycle DM forbidden for user {record['discord_user_id']} on server {record['server_id']}.")
        except Exception as error:
            print(f"Failed to send lifecycle DM for server {record['server_id']}: {error}")
    try:
        admin_embed = branded_embed(
            f"Admin Log: {title}",
            server_admin_details(record, user=user, event_when=when, reason=message),
            0xe67e22 if event != "deleted" else 0xe74c3c,
        )
        delivered = await send_plan_log(plan, admin_embed) or delivered
    except Exception as error:
        print(f"Failed to send {plan} lifecycle reminder for server {record['server_id']} to log channel: {error}")
    return delivered




@tasks.loop(seconds=60)
async def sync_panel_activity() -> None:
    """Refresh every cached panel object and tracked server from the live panel every 60 seconds."""
    try:
        nodes = await ptero.list_nodes()
        nests = await ptero.list_nests()
        for nest in nests:
            await ptero.list_eggs(int(nest["id"]))
        panel_servers = await ptero.list_servers()
    except Exception as error:
        print(f"Failed to sync panel activity: {error}")
        return
    live_by_id = {str(server.get("id")): server for server in panel_servers}
    live_user_ids = {int(server.get("user") or 0) for server in panel_servers if server.get("user")}
    for user_id in live_user_ids:
        await ptero.get_user(user_id, refresh=True)
    for row in fetch_all_servers():
        server_id = str(row["server_id"])
        panel_server = live_by_id.get(server_id)
        if not panel_server:
            mark_server_deleted(server_id)
            clear_server_notifications(server_id)
            continue
        try:
            panel_user_id = int(panel_server.get("user") or 0)
            panel_user = await ptero.get_user(panel_user_id) if panel_user_id else None
            update_tracked_server_from_panel(server_id, panel_server, panel_user.get("email") if panel_user else None)
        except Exception as error:
            print(f"Failed to refresh tracked server {server_id}: {error}")
    print(f"Panel sync refreshed {len(nodes)} nodes, {len(nests)} nests, {sum(len(eggs) for eggs in ptero.egg_cache.values())} eggs, {len(panel_servers)} servers, and {len(live_user_ids)} users.")

@tasks.loop(minutes=1)
async def suspend_expired_servers() -> None:
    now = utc_now()
    for record in fetch_all_servers():
        server_id = record["server_id"]
        expires_at = datetime.fromisoformat(record["expires_at"])
        delete_at = expires_at + timedelta(days=7)
        try:
            if not record["suspended"]:
                await send_due_suspension_warnings(record, now)
                if not record["autosuspend_enabled"] or expires_at > now:
                    continue
                await (await ready_application_client_for(record)).suspend_server(server_id)
                with db() as connection:
                    connection.execute("UPDATE servers SET suspended=1 WHERE server_id=?", (server_id,))
                await send_lifecycle_dm(record, "suspended", expires_at)
                continue

            if delete_at - now <= timedelta(days=1) and now < delete_at and not notification_sent(server_id, "delete_1d"):
                if await send_lifecycle_dm(record, "delete_warning", delete_at):
                    mark_notification_sent(server_id, "delete_1d")
            if now >= delete_at:
                await (await ready_application_client_for(record)).delete_server(server_id)
                mark_server_deleted(server_id)
                clear_server_notifications(server_id)
                save_database()
                await send_lifecycle_dm(record, "deleted", delete_at)
        except Exception as error:
            print(f"Failed lifecycle processing for server {server_id}: {error}")




@tasks.loop(minutes=1)
async def monitor_node_status() -> None:
    try:
        panel_nodes = await ptero.list_nodes()
        records = list((await fetch_live_panel_records()).values())
    except Exception as error:
        print(f"Failed to monitor node status: {error}")
        return
    for node in panel_nodes:
        node_id = str(node.get("id"))
        daemon_online = await node_daemon_reachable(node)
        state, summary, server_count, memory_pct, disk_pct = node_health_summary(node, records, daemon_online)
        previous = fetch_node_status(node_id)
        previous_state = str(previous["state"]) if previous else None
        previous_summary = str(previous["summary"]) if previous else None
        previous_down_since = str(previous["down_since"] or "") if previous and "down_since" in previous.keys() else ""
        down_since = previous_down_since or utc_now().isoformat() if state == "offline" else None
        downtime = format_downtime(previous_down_since) if previous_state == "offline" and state != "offline" else None
        changed = previous is not None and (previous_state != state or (state == "warning" and previous_summary != summary))
        if changed:
            try:
                await send_node_status_webhook(node, previous_state, state, summary, server_count, memory_pct, disk_pct, downtime)
            except Exception as error:
                print(f"Failed to send node status webhook for node {node_id}: {error}")
        upsert_node_status(node_id, state, summary, down_since)


@tasks.loop(minutes=1)
async def run_scheduled_restarts() -> None:
    now = utc_now()
    with db() as connection:
        restarts = connection.execute("SELECT * FROM scheduled_restarts WHERE enabled=1 AND next_run_at <= ?", (now.isoformat(),)).fetchall()
    processed: set[str] = set()
    for restart in restarts:
        server_id = str(restart["server_id"])
        if server_id in processed:
            with db() as connection:
                connection.execute("DELETE FROM scheduled_restarts WHERE id=?", (restart["id"],))
            continue
        processed.add(server_id)
        try:
            row: sqlite3.Row | dict[str, Any] | None = fetch_server(server_id)
            if not row:
                try:
                    row = panel_server_record(await ptero.get_server(server_id), "panel")
                except RuntimeError:
                    row = None
            if not row or not record_value(row, "identifier") or bool(record_value(row, "deleted", False)) or bool(record_value(row, "suspended", False)):
                continue
            await (await ready_client_api_for(row)).power(str(record_value(row, "identifier")), "restart")
        except Exception as error:
            print(f"Failed one-time scheduled restart for {server_id}: {error}")
        finally:
            with db() as connection:
                connection.execute("DELETE FROM scheduled_restarts WHERE server_id=?", (server_id,))


@tasks.loop(minutes=1)
async def run_autobackups() -> None:
    now = utc_now()
    with db() as connection:
        backups = connection.execute("SELECT * FROM autobackups WHERE enabled=1 AND next_run_at <= ?", (now.isoformat(),)).fetchall()
    for backup in backups:
        row = fetch_server(backup["server_id"])
        if not row or not row["identifier"] or row["deleted"] or row["suspended"]:
            continue
        try:
            await client_api.backup(row["identifier"], f"Auto Backup {now.strftime('%Y-%m-%d %H:%M UTC')}")
        except Exception as error:
            print(f"Failed to create autobackup for {backup['server_id']}: {error}")
        with db() as connection:
            connection.execute("UPDATE autobackups SET next_run_at=? WHERE server_id=?", ((now + timedelta(seconds=backup["interval_seconds"])).isoformat(), backup["server_id"]))




def configure_command_visibility() -> None:
    """Sync user commands to DMs while keeping admin commands guild-only."""
    user_contexts = app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True)
    admin_contexts = app_commands.AppCommandContext(guild=True, dm_channel=False, private_channel=False)
    # Keep commands guild-install only. Allowing both guild and user installs can
    # make Discord show duplicate slash commands when the app is installed both
    # ways for the same user/server.
    installs = app_commands.AppInstallationType(guild=True, user=False)
    admin_command_names = {
        "admin", "create-free", "create-paid", "link", "unlink", "resize", "suspend", "unsuspend", "stopall",
        "autobackup-enable", "nodes", "whitelist", "purge", "autosuspend", "server-expirations", "renew", "delete", "deletesuspended",
    }

    def apply(command: app_commands.Command[Any, ..., Any] | app_commands.Group, admin_only_command: bool = False) -> None:
        command.allowed_contexts = admin_contexts if admin_only_command else user_contexts
        command.allowed_installs = installs
        if isinstance(command, app_commands.Group):
            for child in command.commands:
                apply(child, admin_only_command=True)

    for command in tree.get_commands():
        apply(command, admin_only_command=command.name in admin_command_names)


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    message = str(error.original) if isinstance(error, app_commands.CommandInvokeError) else str(error)
    response = branded_embed("Command Failed", clean(message, 3500), 0xff4d4d)
    if interaction.response.is_done():
        await interaction.followup.send(embed=response, ephemeral=True)
    else:
        await interaction.response.send_message(embed=response, ephemeral=True)


@client.event
async def on_ready() -> None:
    if not ptero.session:
        await ptero.start()
    if not client_api.session:
        await client_api.start()
    # Warm all panel caches at every ready event. This keeps node/nest/egg/server
    # autocompletes current after reconnects instead of waiting for commands.
    try:
        nodes = await ptero.list_nodes()
        nests = await ptero.list_nests()
        for nest in nests:
            await ptero.list_eggs(int(nest["id"]))
        servers = await ptero.list_servers()
        print(f"Synced {len(nodes)} nodes, {len(nests)} nests, {sum(len(eggs) for eggs in ptero.egg_cache.values())} eggs, and {len(servers)} servers at startup.")
    except Exception as error:
        print(f"Could not sync panel caches at startup: {error}")
    configure_command_visibility()
    global_commands = await tree.sync()
    guild_id = config.get("guild_id")
    if guild_id:
        guild = discord.Object(id=int(guild_id))
        tree.copy_global_to(guild=guild)
        guild_commands = await tree.sync(guild=guild)
        command_names = ", ".join(sorted(command.name for command in guild_commands))
        print(f"Synced {len(global_commands)} global/DM commands and {len(guild_commands)} instant guild commands: {command_names}")
    else:
        print(f"Synced {len(global_commands)} global/DM commands.")
    if not suspend_expired_servers.is_running():
        suspend_expired_servers.start()
    if not run_autobackups.is_running():
        run_autobackups.start()
    if not run_scheduled_restarts.is_running():
        run_scheduled_restarts.start()
    if not sync_panel_activity.is_running():
        sync_panel_activity.start()
    if not monitor_node_status.is_running():
        monitor_node_status.start()
    print(f"{BRAND} bot online as {client.user} | Developer: {DEVELOPER}")


async def main() -> None:
    require_config()
    try:
        await client.start(config["discord_token"])
    finally:
        await ptero.close()
        await client_api.close()


if __name__ == "__main__":
    asyncio.run(main())
