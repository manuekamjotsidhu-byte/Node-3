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
ADMIN_ROLE_ID = 1504092228778459226
OWNER_ROLE_ID = 1504092176492265553
PANEL_URL = "https://gp.zeroxhost.space"


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
            autosuspend_enabled INTEGER NOT NULL DEFAULT 1
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
        """)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(servers)").fetchall()}
        if "autosuspend_enabled" not in columns:
            connection.execute("ALTER TABLE servers ADD COLUMN autosuspend_enabled INTEGER NOT NULL DEFAULT 1")


def upsert_server_record(record: dict[str, Any]) -> None:
    with db() as connection:
        connection.execute("""
        INSERT OR REPLACE INTO servers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (record["server_id"], record.get("identifier"), record.get("uuid"), record["name"], record["plan"], record["discord_user_id"], record["panel_user_id"], record["panel_email"], record["ram"], record["disk"], record["cpu"], record["nest_id"], record["nest_name"], record["egg_id"], record["egg_name"], record["node_id"], record["node_name"], record["databases"], record["allocations"], record["backups"], record["created_at"], record["expires_at"], int(record.get("suspended", False)), int(record.get("deleted", False)), int(record.get("autosuspend_enabled", True))))


def fetch_server(server_id: str) -> sqlite3.Row | None:
    with db() as connection:
        return connection.execute("SELECT * FROM servers WHERE server_id = ?", (server_id,)).fetchone()


def fetch_user_servers(discord_user_id: int) -> list[sqlite3.Row]:
    with db() as connection:
        return connection.execute("SELECT * FROM servers WHERE discord_user_id = ? AND deleted = 0 ORDER BY created_at DESC", (str(discord_user_id),)).fetchall()


def mark_server_deleted(server_id: str) -> None:
    with db() as connection:
        connection.execute("UPDATE servers SET deleted = 1 WHERE server_id = ?", (str(server_id),))
    if str(server_id) in database.get("servers", {}):
        database["servers"][str(server_id)]["deleted"] = True
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
        "deleted": 0,
    }
    with db() as connection:
        connection.execute(
            """
            UPDATE servers
            SET identifier=?, uuid=?, name=?, panel_user_id=?, panel_email=COALESCE(?, panel_email), ram=?, disk=?, cpu=?, databases=?, allocations=?, backups=?, deleted=?
            WHERE server_id=?
            """,
            (updates["identifier"], updates["uuid"], updates["name"], updates["panel_user_id"], updates["panel_email"], updates["ram"], updates["disk"], updates["cpu"], updates["databases"], updates["allocations"], updates["backups"], updates["deleted"], str(server_id)),
        )
    if str(server_id) in database.get("servers", {}):
        mirror_updates = {key: value for key, value in updates.items() if value is not None}
        database["servers"][str(server_id)].update(mirror_updates)
        save_database()


def fetch_all_servers() -> list[sqlite3.Row]:
    with db() as connection:
        return connection.execute("SELECT * FROM servers WHERE deleted = 0 ORDER BY created_at DESC").fetchall()


def is_whitelisted(server_id: str) -> bool:
    with db() as connection:
        return connection.execute("SELECT 1 FROM whitelist WHERE server_id = ?", (server_id,)).fetchone() is not None


def fetch_link(discord_user_id: int) -> sqlite3.Row | None:
    with db() as connection:
        return connection.execute("SELECT * FROM links WHERE discord_user_id = ?", (str(discord_user_id),)).fetchone()


def fetch_links_by_panel_user() -> dict[int, sqlite3.Row]:
    with db() as connection:
        rows = connection.execute("SELECT * FROM links").fetchall()
    return {int(row["panel_user_id"]): row for row in rows}


def notification_sent(server_id: str, notification_type: str) -> bool:
    with db() as connection:
        return connection.execute("SELECT 1 FROM server_notifications WHERE server_id=? AND notification_type=?", (server_id, notification_type)).fetchone() is not None


def mark_notification_sent(server_id: str, notification_type: str) -> None:
    with db() as connection:
        connection.execute("INSERT OR IGNORE INTO server_notifications(server_id, notification_type, sent_at) VALUES (?,?,?)", (server_id, notification_type, utc_now().isoformat()))


def clear_server_notifications(server_id: str) -> None:
    with db() as connection:
        connection.execute("DELETE FROM server_notifications WHERE server_id=?", (server_id,))


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

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        if user_id in self.user_cache:
            return self.user_cache[user_id]
        try:
            data = await self.request("GET", f"users/{user_id}")
        except RuntimeError:
            return None
        self.user_cache[user_id] = data["attributes"]
        return self.user_cache[user_id]

    async def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        data = await self.request("GET", f"users?filter[email]={email}")
        users = data.get("data", [])
        return users[0]["attributes"] if users else None

    async def get_required_panel_user(self, email: str) -> dict[str, Any]:
        user = await self.find_user_by_email(email)
        if not user:
            raise RuntimeError("No Pterodactyl user exists with that email. Create the panel user first, then retry.")
        return user

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

    async def create_server(self, *, panel_user_id: int, name: str, ram: int, disk: int, cpu: int, node_id: int, nest_id: int, egg_id: int, databases: int, allocations: int, backups: int) -> dict[str, Any]:
        egg = await self.get_egg(nest_id, egg_id)
        docker_images = egg.get("docker_images") or {}
        if isinstance(docker_images, dict):
            docker_image = egg.get("docker_image") or next(iter(docker_images.values()), None)
        elif isinstance(docker_images, list):
            docker_image = egg.get("docker_image") or next(iter(docker_images), None)
        else:
            docker_image = egg.get("docker_image")
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
        data = await self.request("GET", "nests?per_page=100")
        self.nest_cache = [item["attributes"] for item in data.get("data", [])]
        return self.nest_cache

    async def list_eggs(self, nest_id: int) -> list[dict[str, Any]]:
        data = await self.request("GET", f"nests/{nest_id}/eggs?per_page=100")
        eggs = [item["attributes"] for item in data.get("data", [])]
        self.egg_cache[nest_id] = eggs
        return eggs

    async def list_nodes(self) -> list[dict[str, Any]]:
        data = await self.request("GET", "nodes?per_page=100")
        self.node_cache = [item["attributes"] for item in data.get("data", [])]
        return self.node_cache

    async def list_servers(self) -> list[dict[str, Any]]:
        data = await self.request("GET", "servers?per_page=100")
        self.server_cache = [item["attributes"] for item in data.get("data", [])]
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


def branded_embed(title: str, description: str, color: int = 0x00d4ff) -> discord.Embed:
    embed = discord.Embed(title=f"✨ {title}", description=description, color=color, timestamp=utc_now())
    if config.get("brand_icon_url"):
        embed.set_author(name=BRAND, icon_url=config["brand_icon_url"])
    else:
        embed.set_author(name=BRAND)
    embed.set_footer(text=f"{BRAND} • Developer: {DEVELOPER}")
    return embed


def specs_embed(plan: str, name: str, ram: int, disk: int, cpu: int, node_name: str, nest: str, egg: str, expires_at: datetime, databases: int, allocations: int, backups: int) -> discord.Embed:
    embed = branded_embed(f"Your {BRAND} {plan.title()} Server Is Ready", f"Panel: **{config['panel_url'].rstrip('/')}**")
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


def panel_server_embed(server: dict[str, Any], email: str, discord_label: str) -> discord.Embed:
    embed = branded_embed("Panel Server", f"**{server['name']}**", 0x5865f2)
    embed.add_field(name="Server ID", value=f"`{server['id']}`", inline=True)
    embed.add_field(name="UUID", value=f"`{server.get('uuid', 'no-uuid')}`", inline=False)
    embed.add_field(name="Panel Email", value=f"`{email}`", inline=True)
    embed.add_field(name="Discord User", value=discord_label, inline=True)
    return embed


async def node_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    nodes = ptero.node_cache or await ptero.list_nodes()
    matches = [node for node in nodes if current.lower() in f"{node['id']} {node['name']}".lower()]
    return [app_commands.Choice(name=f"{node['name']} (ID {node['id']})", value=f"{node['id']}:{node['name']}") for node in matches[:25]]


async def nest_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    nests = ptero.nest_cache or await ptero.list_nests()
    matches = [nest for nest in nests if current.lower() in f"{nest['id']} {nest['name']}".lower()]
    return [app_commands.Choice(name=f"{nest['name']} (ID {nest['id']})", value=f"{nest['id']}:{nest['name']}") for nest in matches[:25]]


async def egg_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    nest_value = getattr(interaction.namespace, "nest", None)
    if not nest_value:
        return [app_commands.Choice(name="Select a nest first", value="0:select-nest-first")]
    nest_id = parse_id(str(nest_value))
    eggs = ptero.egg_cache.get(nest_id) or await ptero.list_eggs(nest_id)
    matches = [egg for egg in eggs if current.lower() in f"{egg['id']} {egg['name']}".lower()]
    return [app_commands.Choice(name=f"{egg['name']} (ID {egg['id']})", value=f"{egg['id']}:{egg['name']}") for egg in matches[:25]]


async def server_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    servers = ptero.server_cache or await ptero.list_servers()
    matches = [server for server in servers if current.lower() in f"{server['id']} {server['name']} {server.get('uuid', '')}".lower()]
    return [app_commands.Choice(name=f"{server['name']} • {server.get('uuid', server['id'])}", value=str(server["id"])) for server in matches[:25]]


def record_value(record: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    try:
        return record[key]
    except (KeyError, IndexError, TypeError):
        return default


def panel_server_record(server: dict[str, Any]) -> dict[str, Any]:
    limits = server.get("limits") or {}
    return {
        "server_id": str(server.get("id")),
        "identifier": server.get("identifier"),
        "uuid": server.get("uuid"),
        "name": server.get("name", f"Panel Server {server.get('id')}"),
        "plan": "panel",
        "discord_user_id": "",
        "panel_user_id": server.get("user") or 0,
        "panel_email": "panel-created/unlinked",
        "ram": int(limits.get("memory") or 0),
        "disk": int(limits.get("disk") or 0),
        "cpu": int(limits.get("cpu") or 0),
        "expires_at": None,
        "deleted": 0,
    }


def row_server_choices(current: str, rows: list[sqlite3.Row | dict[str, Any]]) -> list[app_commands.Choice[str]]:
    lowered = current.lower()
    matches = [row for row in rows if lowered in f"{record_value(row, 'server_id', '')} {record_value(row, 'name', '')} {record_value(row, 'uuid', '') or ''} {record_value(row, 'identifier', '') or ''}".lower()]
    return [app_commands.Choice(name=f"{record_value(row, 'name', 'unknown')} • {record_value(row, 'server_id')}", value=str(record_value(row, "server_id"))) for row in matches[:25]]


async def tracked_server_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return row_server_choices(current, fetch_user_servers(interaction.user.id))


async def admin_tracked_server_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if interaction.guild is None or not is_admin(interaction.user):
        return []
    rows: list[sqlite3.Row | dict[str, Any]] = list(fetch_all_servers())
    seen = {str(row["server_id"]) for row in rows}
    try:
        panel_servers = ptero.server_cache or await asyncio.wait_for(ptero.list_servers(), timeout=2.5)
        for server in panel_servers:
            server_id = str(server.get("id"))
            if server_id not in seen:
                rows.append(panel_server_record(server))
                seen.add(server_id)
    except Exception as error:
        print(f"Failed to include panel servers in admin autocomplete quickly: {error}")
    return row_server_choices(current, rows)


def parse_duration(value: str) -> int:
    total = 0
    for amount, unit in re.findall(r"(\d+)\s*([dhm])", value.lower()):
        number = int(amount)
        total += number * {"d": 86400, "h": 3600, "m": 60}[unit]
    if total <= 0:
        raise ValueError("Use duration like 2d, 4h, 12h, or 1d12h.")
    return total


def is_not_found_error(error: Exception) -> bool:
    message = str(error).lower()
    return " 404" in message or "not found" in message or "could not be found" in message


async def refresh_tracked_server(row: sqlite3.Row) -> sqlite3.Row | None:
    """Refresh a tracked DB server from the panel so local state never wins over live panel data."""
    server_id = str(row["server_id"])
    try:
        panel_server = await ptero.get_server(server_id)
    except RuntimeError as error:
        if is_not_found_error(error):
            mark_server_deleted(server_id)
            return None
        raise
    panel_user_id = int(panel_server.get("user") or 0)
    panel_user = await ptero.get_user(panel_user_id) if panel_user_id else None
    update_tracked_server_from_panel(server_id, panel_server, panel_user.get("email") if panel_user else None)
    return fetch_server(server_id)


async def refresh_user_servers(discord_user_id: int) -> list[sqlite3.Row]:
    refreshed: list[sqlite3.Row] = []
    for row in fetch_user_servers(discord_user_id):
        live_row = await refresh_tracked_server(row)
        if live_row and str(live_row["discord_user_id"]) == str(discord_user_id):
            refreshed.append(live_row)
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
            return panel_server_record(await ptero.get_server(server_id))
        except RuntimeError as error:
            raise RuntimeError(f"Unknown tracked or panel server: {server_id}") from error
    raise RuntimeError("Unknown tracked server.")


def format_mb(value: float) -> str:
    return f"{value:,.1f} MB"


def manage_embed(row: sqlite3.Row | dict[str, Any], resources: dict[str, Any]) -> discord.Embed:
    raw = resources.get("resources", {}) if resources else {}
    state = resources.get("current_state", "unknown") if resources else "unknown"
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
        resources = await client_api.resources(row["identifier"]) if row["identifier"] else {}
        embed = manage_embed(row, resources)
        embed.description = f"{embed.description}\n\n{note}"
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        row = await self.row(interaction)
        if not row["identifier"]:
            raise RuntimeError("This tracked server is missing its client identifier.")
        await client_api.power(row["identifier"], "start")
        await self.refresh_message(interaction, "✅ Start signal sent.")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️", row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        row = await self.row(interaction)
        if not row["identifier"]:
            raise RuntimeError("This tracked server is missing its client identifier.")
        await client_api.power(row["identifier"], "stop")
        await self.refresh_message(interaction, "✅ Stop signal sent.")

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.primary, emoji="🔁", row=0)
    async def restart_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        row = await self.row(interaction)
        if not row["identifier"]:
            raise RuntimeError("This tracked server is missing its client identifier.")
        await client_api.power(row["identifier"], "restart")
        await self.refresh_message(interaction, "✅ Restart signal sent.")

    @discord.ui.button(label="Kill", style=discord.ButtonStyle.danger, emoji="💀", row=0)
    async def kill_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        row = await self.row(interaction)
        if not row["identifier"]:
            raise RuntimeError("This tracked server is missing its client identifier.")
        await client_api.power(row["identifier"], "kill")
        await self.refresh_message(interaction, "✅ Kill signal sent.")


class ResizeModal(discord.ui.Modal, title="Resize ZeroX Host Server"):
    ram = discord.ui.TextInput(label="RAM MB", placeholder="2048", required=True)
    disk = discord.ui.TextInput(label="Disk MB", placeholder="10240", required=True)
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
            ram = int(str(self.ram.value).strip())
            disk = int(str(self.disk.value).strip())
            cpu = int(str(self.cpu.value).strip())
        except ValueError as error:
            raise RuntimeError("Use whole numbers for RAM, disk, CPU, and extras in `databases,allocations,backups` format, for example `1,1,1`.") from error
        if min(ram, disk, cpu) <= 0 or min(databases, allocations, backups) < 0:
            raise RuntimeError("RAM, disk, and CPU must be positive. Databases, allocations, and backups cannot be negative.")
        await ptero.resize_server(self.server_id, ram, disk, cpu, databases, allocations, backups)
        if fetch_server(self.server_id):
            with db() as connection:
                connection.execute("UPDATE servers SET ram=?, disk=?, cpu=?, databases=?, allocations=?, backups=? WHERE server_id=?", (ram, disk, cpu, databases, allocations, backups, self.server_id))
        await interaction.followup.send(embed=branded_embed("Server Resized", f"**{record_value(row, 'name', self.server_id)}** is now {ram:,} MB RAM / {disk:,} MB disk / {cpu}% CPU."), ephemeral=True)


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
        await ptero.suspend_server(server_id)
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
    link = fetch_link(user.id)
    if not link:
        raise RuntimeError("Discord user is not linked. Use /link first.")
    panel_email = link["email"]
    panel_user = {"id": link["panel_user_id"]}
    expires_at = utc_now() + timedelta(seconds=duration_seconds)
    server = await ptero.create_server(panel_user_id=panel_user["id"], name=name, ram=ram_mb, disk=disk_mb, cpu=cpu, node_id=node_id, nest_id=nest_id, egg_id=egg_id, databases=databases, allocations=allocations, backups=backups)
    server_id = str(server["id"])
    saga_synced = await ptero.set_saga_auto_suspend(server_id, expires_at)
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

    if plan == "paid":
        channel = client.get_channel(int(config.get("paid_log_channel_id", PAID_LOG_CHANNEL_ID))) or await client.fetch_channel(int(config.get("paid_log_channel_id", PAID_LOG_CHANNEL_ID)))
        await channel.send(embed=branded_embed("Paid Server Created", f"Discord User: {user.mention} (`{user.id}`)\nEmail: `{panel_email}`\nServer: **{name}** (`{server_id}`)\nSpecs: {ram_mb}MB RAM / {disk_mb}MB Disk / {cpu}% CPU\nExtras: DB {databases} / Alloc {allocations} / Backups {backups}\nNode: {node_name}\nNext renewal: <t:{int(expires_at.timestamp())}:F>\nSaga auto suspension: {'synced' if saga_synced else 'not synced'}", 0xf1c40f))


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
    panel_user = await ptero.get_required_panel_user(panel_email)
    with db() as connection:
        connection.execute("INSERT OR REPLACE INTO links VALUES (?,?,?)", (str(user.id), panel_user["id"], panel_email))
    await interaction.followup.send(embed=branded_embed("User Linked", f"{user.mention} linked to `{panel_email}` / panel user `{panel_user['id']}`."), ephemeral=True)


admin_group = app_commands.Group(name="admin", description="ZeroX Host admin tools")


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
    resources = await client_api.resources(row["identifier"]) if row["identifier"] else {}
    await interaction.followup.send(embed=manage_embed(row, resources), view=ManageView(server, allow_admin=True), ephemeral=True)


@admin_group.command(name="console", description="Send console command to any tracked server")
@admin_only()
@app_commands.autocomplete(server=admin_tracked_server_autocomplete)
async def admin_console(interaction: discord.Interaction, server: str, command: str) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await ensure_server_access(interaction, server, allow_admin=True)
    if not row["identifier"]:
        raise RuntimeError("This tracked server is missing its client identifier.")
    await client_api.command(row["identifier"], command)
    await interaction.followup.send(embed=branded_embed("Admin Console Command Sent", f"Sent command to **{row['name']}**.\n```{clean(command, 1000)}```"), ephemeral=True)


@admin_group.command(name="rename", description="Rename any tracked server")
@admin_only()
@app_commands.autocomplete(server=admin_tracked_server_autocomplete)
async def admin_rename(interaction: discord.Interaction, server: str, new_name: str) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await ensure_server_access(interaction, server, allow_admin=True)
    if not row["identifier"]:
        raise RuntimeError("This tracked server is missing its client identifier.")
    await client_api.rename(row["identifier"], new_name)
    with db() as connection:
        connection.execute("UPDATE servers SET name=? WHERE server_id=?", (new_name, server))
    await interaction.followup.send(embed=branded_embed("Admin Server Renamed", f"`{row['name']}` is now **{new_name}**."), ephemeral=True)


tree.add_command(admin_group)


@tree.command(name="list", description="List your servers")
async def list_mine(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    rows = await refresh_user_servers(interaction.user.id)
    if not rows:
        await interaction.followup.send(embed=branded_embed("Your Servers", "No servers found."))
        return
    embeds: list[discord.Embed] = []
    pages = chunked(rows, 10)
    for page_number, page in enumerate(pages, start=1):
        embed = branded_embed("Your Servers", "Only your own linked/tracked servers are shown here.")
        for row in page:
            expires = int(datetime.fromisoformat(row["expires_at"]).timestamp())
            embed.add_field(
                name=f"#{row['server_id']} • {row['name']}",
                value=f"**Plan:** {row['plan']}\n**UUID:** `{row['uuid'] or 'unknown'}`\n**Specs:** {row['ram']:,} MB RAM / {row['disk']:,} MB Disk / {row['cpu']}% CPU\n**Expires:** <t:{expires}:R>",
                inline=False,
            )
        embed.set_footer(text=f"{BRAND} • Page {page_number}/{len(pages)} • {len(rows)} server(s) • Developer: {DEVELOPER}")
        embeds.append(embed)
    if len(embeds) > 1:
        await interaction.followup.send(embed=embeds[0], view=PaginatedEmbeds(embeds))
    else:
        await interaction.followup.send(embed=embeds[0])

@tree.command(name="manage", description="Open server manager")
@app_commands.autocomplete(server=tracked_server_autocomplete)
async def manage(interaction: discord.Interaction, server: str) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await ensure_server_access(interaction, server)
    resources = await client_api.resources(row["identifier"]) if row["identifier"] else {}
    await interaction.followup.send(embed=manage_embed(row, resources), view=ManageView(server), ephemeral=True)


@tree.command(name="console", description="Send console command")
@app_commands.autocomplete(server=tracked_server_autocomplete)
async def console(interaction: discord.Interaction, server: str, command: str) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await ensure_server_access(interaction, server)
    if not row["identifier"]:
        raise RuntimeError("This tracked server is missing its client identifier.")
    await client_api.command(row["identifier"], command)
    await interaction.followup.send(embed=branded_embed("Console Command Sent", f"Sent command to **{row['name']}**.\n```{clean(command, 1000)}```"), ephemeral=True)


@tree.command(name="rename", description="Rename server")
@app_commands.autocomplete(server=tracked_server_autocomplete)
async def rename(interaction: discord.Interaction, server: str, new_name: str) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await ensure_server_access(interaction, server)
    if not row["identifier"]:
        raise RuntimeError("This tracked server is missing its client identifier.")
    await client_api.rename(row["identifier"], new_name)
    with db() as connection:
        connection.execute("UPDATE servers SET name=? WHERE server_id=?", (new_name, server))
    await interaction.followup.send(embed=branded_embed("Server Renamed", f"`{row['name']}` is now **{new_name}**."), ephemeral=True)


@tree.command(name="schedule-restart", description="Schedule restarts")
@app_commands.autocomplete(server=tracked_server_autocomplete)
async def schedule_restart(interaction: discord.Interaction, time: str, server: str | None = None, all_servers: bool = False) -> None:
    await interaction.response.defer(ephemeral=True)
    seconds = parse_duration(time)
    next_run = (utc_now() + timedelta(seconds=seconds)).isoformat()
    if all_servers:
        if interaction.guild is None or not is_admin(interaction.user):
            raise RuntimeError("Scheduling restarts for all servers is admin-only and cannot be used in DMs.")
        rows = fetch_all_servers()
        with db() as connection:
            for row in rows:
                connection.execute("INSERT INTO scheduled_restarts(server_id, discord_user_id, interval_seconds, next_run_at, all_servers, enabled) VALUES (?,?,?,?,1,1)", (row["server_id"], str(interaction.user.id), seconds, next_run))
        await interaction.followup.send(embed=branded_embed("All Server Restarts Scheduled", f"Scheduled **{len(rows)}** tracked server(s) to restart every **{time}**."), ephemeral=True)
        return
    if not server:
        raise RuntimeError("Select one server, or admins can set all_servers:True inside the Discord server.")
    row = await ensure_server_access(interaction, server)
    with db() as connection:
        connection.execute("INSERT INTO scheduled_restarts(server_id, discord_user_id, interval_seconds, next_run_at, all_servers, enabled) VALUES (?,?,?,?,0,1)", (server, str(interaction.user.id), seconds, next_run))
    await interaction.followup.send(embed=branded_embed("Restart Scheduled", f"**{row['name']}** will restart every **{time}**."), ephemeral=True)


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
            connection.execute("UPDATE servers SET expires_at=?, suspended=0, autosuspend_enabled=1 WHERE server_id=?", (new_expiry.isoformat(), server))
        clear_server_notifications(server)
    saga_synced = await ptero.set_saga_auto_suspend(server, new_expiry)
    try:
        await ptero.unsuspend_server(server)
    except RuntimeError:
        pass
    discord_user_id = record_value(row, "discord_user_id")
    if discord_user_id:
        try:
            user = await client.fetch_user(int(discord_user_id))
            await user.send(embed=branded_embed("Service Renewed", f"Your server **{record_value(row, 'name', server)}** was renewed until <t:{int(new_expiry.timestamp())}:F>."))
        except Exception:
            pass
    tracking_note = "Local DB updated" if tracked_row else "Panel/Saga updated only (server is not locally tracked)"
    await interaction.followup.send(embed=branded_embed("Server Renewed", f"**{record_value(row, 'name', server)}** renewed by **{time}**.\nNext expiry: <t:{int(new_expiry.timestamp())}:F>\nTracking: **{tracking_note}**\nSaga auto suspension: **{'synced' if saga_synced else 'not synced'}**"), ephemeral=True)


@tree.command(name="delete", description="Admin delete one server")
@admin_only()
@app_commands.autocomplete(server=admin_tracked_server_autocomplete)
async def delete(interaction: discord.Interaction, server: str, confirm: bool = False) -> None:
    await interaction.response.defer(ephemeral=True)
    row = fetch_server(server)
    if not row:
        row = panel_server_record(await ptero.get_server(server))
    if not confirm:
        await interaction.followup.send(embed=branded_embed("Confirm Delete", f"Run `/delete server:{server} confirm:True` to permanently delete **{record_value(row, 'name', server)}**.", 0xffcc00), ephemeral=True)
        return
    await ptero.delete_server(server)
    with db() as connection:
        connection.execute("UPDATE servers SET deleted=1 WHERE server_id=?", (server,))
    database.get("servers", {}).pop(server, None)
    save_database()
    await interaction.followup.send(embed=branded_embed("Server Deleted", f"Deleted **{record_value(row, 'name', server)}** (`{server}`).", 0xe74c3c), ephemeral=True)


@tree.command(name="power", description="Power server")
@app_commands.autocomplete(server=tracked_server_autocomplete)
@app_commands.choices(action=[app_commands.Choice(name="start", value="start"), app_commands.Choice(name="stop", value="stop"), app_commands.Choice(name="restart", value="restart")])
async def power(interaction: discord.Interaction, server: str, action: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await ensure_server_access(interaction, server)
    if not row["identifier"]:
        raise RuntimeError("This tracked server is missing its client identifier.")
    await client_api.power(row["identifier"], action.value)
    await interaction.followup.send(embed=branded_embed("Power Signal Sent", f"Sent **{action.value}** to **{row['name']}**."), ephemeral=True)


@tree.command(name="reinstall", description="Reinstall server")
@app_commands.autocomplete(server=tracked_server_autocomplete)
async def reinstall(interaction: discord.Interaction, server: str) -> None:
    await interaction.response.defer(ephemeral=True)
    row = await ensure_server_access(interaction, server)
    await ptero.reinstall_server(server)
    await interaction.followup.send(embed=branded_embed("Reinstall Started", f"Reinstall started for **{row['name']}**."), ephemeral=True)


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
            await ptero.suspend_server(row["server_id"])
            with db() as connection:
                connection.execute("UPDATE servers SET suspended=1 WHERE server_id=?", (row["server_id"],))
            suspended += 1
        await interaction.followup.send(embed=branded_embed("Bulk Suspend Complete", f"Suspended **{suspended}** non-paid, non-whitelisted server(s)."), ephemeral=True)
        return

    if server:
        row = fetch_server(server)
        await ptero.suspend_server(server)
        with db() as connection:
            connection.execute("UPDATE servers SET suspended=1 WHERE server_id=?", (server,))
        await interaction.followup.send(embed=branded_embed("Server Suspended", f"Suspended **{row['name'] if row else server}**."), ephemeral=True)
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
    await ptero.unsuspend_server(server)
    with db() as connection:
        connection.execute("UPDATE servers SET suspended=0 WHERE server_id=?", (server,))
    await interaction.followup.send(embed=branded_embed("Server Unsuspended", f"Unsuspended server `{server}`."), ephemeral=True)


@tree.command(name="stopall", description="Stop all except whitelist")
@admin_only()
async def stopall(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    stopped = 0
    for row in fetch_all_servers():
        if is_whitelisted(row["server_id"]) or not row["identifier"]:
            continue
        await client_api.power(row["identifier"], "stop")
        stopped += 1
    await interaction.followup.send(embed=branded_embed("Stop All Complete", f"Stopped **{stopped}** server(s). Whitelisted servers were skipped."), ephemeral=True)


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


@tree.command(name="nodes", description="Show nodes")
@admin_only()
async def nodes(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    rows = [f"`{node['id']}` • **{node['name']}**" for node in await ptero.list_nodes()]
    await interaction.followup.send(embed=branded_embed("Deployment Nodes", "\n".join(rows) or "No nodes found."), ephemeral=True)


@tree.command(name="whitelist", description="Admin: whitelist or unwhitelist a server by name/UUID")
@admin_only()
@app_commands.autocomplete(server=tracked_server_autocomplete)
@app_commands.choices(action=[app_commands.Choice(name="add", value="add"), app_commands.Choice(name="remove", value="remove")])
async def whitelist(interaction: discord.Interaction, server: str, action: app_commands.Choice[str]) -> None:
    await interaction.response.defer(ephemeral=True)
    whitelist_set = set(str(item) for item in database.setdefault("whitelist", []))
    if action.value == "add":
        whitelist_set.add(server)
    else:
        whitelist_set.discard(server)
    database["whitelist"] = sorted(whitelist_set, key=str)
    save_database()
    with db() as connection:
        if action.value == "add":
            connection.execute("INSERT OR IGNORE INTO whitelist(server_id) VALUES (?)", (server,))
        else:
            connection.execute("DELETE FROM whitelist WHERE server_id=?", (server,))
    tracked = database.get("servers", {}).get(server, {})
    await interaction.followup.send(embed=branded_embed("Whitelist Updated", f"Action: **{action.value}**\nServer: **{tracked.get('name', server)}**\nWhitelist count: **{len(database['whitelist'])}**"), ephemeral=True)


@tree.command(name="purge", description="Admin: purge tracked free servers, excluding paid and whitelisted servers")
@admin_only()
async def purge(interaction: discord.Interaction, confirm: bool = False) -> None:
    await interaction.response.defer(ephemeral=True)
    if not confirm:
        await interaction.followup.send(embed=branded_embed("Confirmation Required", "Run `/purge confirm:True` to delete tracked free servers. Paid and whitelisted servers are skipped.", 0xffcc00), ephemeral=True)
        return
    victims = [row for row in fetch_all_servers() if row["plan"] == "free" and not is_whitelisted(row["server_id"])]
    deleted = []
    failed = []
    for record in victims:
        server_id = record["server_id"]
        try:
            await ptero.delete_server(server_id)
            deleted.append(f"{record['name']} (`{server_id}`)")
            with db() as connection:
                connection.execute("UPDATE servers SET deleted=1 WHERE server_id=?", (server_id,))
        except RuntimeError as error:
            failed.append(f"{server_id}: {error}")
    save_database()
    description = f"Deleted free servers: **{len(deleted)}**\nSkipped paid/whitelisted servers automatically."
    if deleted:
        description += "\n\n" + "\n".join(deleted[:15])
    if failed:
        description += "\n\nFailures:\n" + "\n".join(failed[:5])
    await interaction.followup.send(embed=branded_embed("Free Server Purge Complete", description, 0xe74c3c), ephemeral=True)


@tree.command(name="autosuspend", description="Toggle autosuspend")
@admin_only()
@app_commands.autocomplete(server=admin_tracked_server_autocomplete)
@app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
async def autosuspend(interaction: discord.Interaction, server: str, state: app_commands.Choice[str], time: str | None = None) -> None:
    await interaction.response.defer(ephemeral=True)
    enabled = 1 if state.value == "on" else 0
    row = await ensure_server_access(interaction, server, allow_admin=True)
    tracked_row = fetch_server(server)
    expires_at: datetime | None = None
    if enabled:
        if time:
            expires_at = utc_now() + timedelta(seconds=parse_duration(time))
        else:
            stored_expiry = record_value(row, "expires_at")
            if not stored_expiry:
                raise RuntimeError("This panel server is not tracked by the bot yet. Provide `time` like `30d`, `12h`, or `1d6h` so Saga can receive an expiration date.")
            expires_at = datetime.fromisoformat(str(stored_expiry))
    if tracked_row:
        with db() as connection:
            if expires_at and time:
                connection.execute("UPDATE servers SET autosuspend_enabled=?, expires_at=?, suspended=0 WHERE server_id=?", (enabled, expires_at.isoformat(), server))
            else:
                connection.execute("UPDATE servers SET autosuspend_enabled=? WHERE server_id=?", (enabled, server))
    saga_synced = await ptero.set_saga_auto_suspend(server, expires_at if enabled else None)
    expiry_line = f"\nExpiration: <t:{int(expires_at.timestamp())}:F>" if expires_at else ""
    await interaction.followup.send(embed=branded_embed("Autosuspend Updated", f"Automatic expiration suspension for **{record_value(row, 'name', server)}** is now **{state.value.upper()}**.{expiry_line}\nSaga auto suspension: **{'synced' if saga_synced else 'cleared/not synced'}**"), ephemeral=True)


@tree.command(name="deletesuspended", description="Delete suspended servers")
@admin_only()
@app_commands.choices(plan=[app_commands.Choice(name="free", value="free"), app_commands.Choice(name="paid", value="paid"), app_commands.Choice(name="all", value="all")])
async def deletesuspended(interaction: discord.Interaction, plan: app_commands.Choice[str], confirm: bool = False) -> None:
    await interaction.response.defer(ephemeral=True)
    victims = [row for row in fetch_all_servers() if row["suspended"] and (plan.value == "all" or row["plan"] == plan.value)]
    if not confirm:
        await interaction.followup.send(embed=branded_embed("Confirm Suspended Delete", f"Found **{len(victims)}** suspended **{plan.value}** server(s). Run `/deletesuspended plan:{plan.value} confirm:True` to permanently delete them from the panel.", 0xffcc00), ephemeral=True)
        return
    deleted: list[str] = []
    failed: list[str] = []
    for row in victims:
        try:
            await ptero.delete_server(row["server_id"])
            with db() as connection:
                connection.execute("UPDATE servers SET deleted=1 WHERE server_id=?", (row["server_id"],))
            database.get("servers", {}).pop(row["server_id"], None)
            clear_server_notifications(row["server_id"])
            deleted.append(f"`{row['server_id']}` • {row['name']}")
        except Exception as error:
            failed.append(f"`{row['server_id']}` • {clean(str(error), 120)}")
    save_database()
    description = f"Deleted **{len(deleted)}** suspended server(s) for plan **{plan.value}**."
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
    for record in fetch_all_servers():
        expires = int(datetime.fromisoformat(record["expires_at"]).timestamp())
        flag = "⭐ whitelisted" if is_whitelisted(record["server_id"]) else record["plan"]
        auto = "autosuspend:on" if record["autosuspend_enabled"] else "autosuspend:off"
        rows.append(f"`{record['server_id']}` • **{record['name']}** • {flag} • {auto} • <t:{expires}:R>")
    await interaction.followup.send(embed=branded_embed("Tracked Expirations", "\n".join(rows[:25]) or "No tracked servers."), ephemeral=True)


async def send_lifecycle_dm(record: sqlite3.Row, event: str, when: datetime, notification_type: str | None = None) -> None:
    try:
        user = await client.fetch_user(int(record["discord_user_id"]))
    except Exception:
        return
    plan = record["plan"]
    server_name = record["name"]
    timestamp = int(when.timestamp())
    if event == "suspension_warning":
        if plan == "paid":
            title = "Paid Service Renewal Reminder"
            lead = "7 days" if notification_type == "suspend_7d" else "24 hours"
            message = f"Your paid server **{server_name}** is scheduled for suspension in **{lead}** at <t:{timestamp}:F>. Please renew by clearing the recurring amount due to keep your service active."
        else:
            title = "Free Server Renewal Reminder"
            message = f"Your free server **{server_name}** will be suspended in **24 hours** at <t:{timestamp}:F>. Please renew your server if you still need it."
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
    try:
        await user.send(embed=branded_embed(title, message, 0xe67e22 if event != "deleted" else 0xe74c3c))
    except discord.Forbidden:
        pass


@tasks.loop(minutes=1)
async def suspend_expired_servers() -> None:
    now = utc_now()
    for record in fetch_all_servers():
        server_id = record["server_id"]
        expires_at = datetime.fromisoformat(record["expires_at"])
        delete_at = expires_at + timedelta(days=7)
        try:
            if not record["suspended"]:
                suspension_warnings = [("suspend_1d", timedelta(days=1))]
                if record["plan"] == "paid":
                    suspension_warnings.insert(0, ("suspend_7d", timedelta(days=7)))
                for notification_type, window in suspension_warnings:
                    if now <= expires_at and expires_at - now <= window and not notification_sent(server_id, notification_type):
                        await send_lifecycle_dm(record, "suspension_warning", expires_at, notification_type)
                        mark_notification_sent(server_id, notification_type)
                if not record["autosuspend_enabled"] or expires_at > now:
                    continue
                await ptero.suspend_server(server_id)
                with db() as connection:
                    connection.execute("UPDATE servers SET suspended=1 WHERE server_id=?", (server_id,))
                await send_lifecycle_dm(record, "suspended", expires_at)
                continue

            if delete_at - now <= timedelta(days=1) and now < delete_at and not notification_sent(server_id, "delete_1d"):
                await send_lifecycle_dm(record, "delete_warning", delete_at)
                mark_notification_sent(server_id, "delete_1d")
            if now >= delete_at:
                await ptero.delete_server(server_id)
                with db() as connection:
                    connection.execute("UPDATE servers SET deleted=1 WHERE server_id=?", (server_id,))
                database.get("servers", {}).pop(server_id, None)
                clear_server_notifications(server_id)
                save_database()
                await send_lifecycle_dm(record, "deleted", delete_at)
        except Exception as error:
            print(f"Failed lifecycle processing for server {server_id}: {error}")


@tasks.loop(minutes=1)
async def run_scheduled_restarts() -> None:
    now = utc_now()
    with db() as connection:
        restarts = connection.execute("SELECT * FROM scheduled_restarts WHERE enabled=1 AND next_run_at <= ?", (now.isoformat(),)).fetchall()
    for restart in restarts:
        row = fetch_server(restart["server_id"])
        if not row or not row["identifier"] or row["deleted"] or row["suspended"]:
            continue
        try:
            await client_api.power(row["identifier"], "restart")
        except Exception as error:
            print(f"Failed to scheduled-restart {restart['server_id']}: {error}")
        with db() as connection:
            connection.execute("UPDATE scheduled_restarts SET next_run_at=? WHERE id=?", ((now + timedelta(seconds=restart["interval_seconds"])).isoformat(), restart["id"]))


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
    installs = app_commands.AppInstallationType(guild=True, user=True)
    admin_command_names = {
        "admin", "create-free", "create-paid", "link", "resize", "suspend", "unsuspend", "stopall",
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
    configure_command_visibility()
    global_commands = await tree.sync()
    guild_id = config.get("guild_id")
    if guild_id:
        guild = discord.Object(id=int(guild_id))
        tree.clear_commands(guild=guild)
        cleared_guild_commands = await tree.sync(guild=guild)
        print(f"Synced {len(global_commands)} global/DM commands and cleared {len(cleared_guild_commands)} guild-only duplicates.")
    else:
        print(f"Synced {len(global_commands)} global/DM commands.")
    if not suspend_expired_servers.is_running():
        suspend_expired_servers.start()
    if not run_autobackups.is_running():
        run_autobackups.start()
    if not run_scheduled_restarts.is_running():
        run_scheduled_restarts.start()
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
