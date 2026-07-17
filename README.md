# ZeroX Host Pterodactyl Manager

A single-file Python Discord slash-command bot for **ZeroX Host** Pterodactyl provisioning and cleanup.

Inspired by the Vortex Ptero Manager workflow, but rebuilt as one `bot.py` with all runtime settings in `config.json`.

**Developer:** ekamsidhu07 ekam

## Current behavior

- No invite system.
- No SMTP flow.
- Paid-panel accounts are never created by the bot: users register through the paid panel. `/admin createuser` creates and links **FreeDash-only** accounts using the separate free-panel API.
- Only configured owners/admin roles can create, purge, whitelist, or view management data. Admin commands are guild-only and are intentionally hidden/blocked in DMs; DMs only expose user-safe commands for linked servers.
- Admins select a deployment node by name/ID with slash-command autocomplete.
- Admins create either `/create-free` or `/create-paid` servers with custom `time`, nest, egg, node, RAM/Disk entered in GB, CPU, databases, allocations, and backups; the Discord user must already be linked with `/link`.
- Paid server creations are logged to channel `1504092779700289536` unless overridden in `config.json`.
- `/purge` deletes tracked free servers only; paid and whitelisted servers are skipped.
- Created users receive styled ZeroX Host DM embeds with specs, panel URL, node, extras, expiration, and a Trustpilot review link.
- Tracked server details are refreshed from the live Pterodactyl panel before user lists and management actions, so renamed/resized/deleted panel servers do not rely on stale local DB values.
- Saga Auto Suspension can be synced during create, renew, and `/autosuspend` changes by configuring `saga_auto_suspend_enabled` and the panel field name in `saga_auto_suspend_field` (fallbacks try `suspended_at`, `expiration_date`, and `expires_at`).
- Expired tracked servers are automatically suspended by the background task. Users receive renewal reminders before suspension, a deletion warning 24 hours before cleanup, and suspended servers are deleted after 7 days.

## Files

- `bot.py` - the full Discord bot, slash commands, Pterodactyl Application/Client API clients, local SQLite `.db`, DM embeds, paid logs, purge, whitelist, autobackups, and expiration loop.
- `config.example.json` - copy to `config.json` and fill in Discord/Pterodactyl settings. FreeDash needs its own `free_panel_api_key` **and** `free_client_api_key`; the Application key provisions/administers FreeDash, while the Client key is required for FreeDash server power, console, resources, and backups. Nests and eggs are fetched from the panel, not hardcoded.
- `requirements.txt` - Python dependencies.
- `data/zerox_host.db` - generated SQLite runtime database used for links, servers, whitelist, autobackups, and exact-time suspension. A legacy JSON mirror may also be created.

## Setup

Commands are synced globally once for both guild and DM visibility, and the bot clears old guild-only copies to prevent duplicate slash commands. If old duplicates remain, restart the bot once and wait for Discord global command propagation.


```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
# Fill owner_ids with real Discord user IDs for DM admin access.
python bot.py
```

## Slash commands

- `/create-free` - admin-only free server creation with linked Discord user, name, RAM/Disk in GB, specs, nest, egg, node, `time` duration, and optional feature limits.
- `/create-paid` - admin-only paid server creation with `time` duration, RAM/Disk in GB, nest/egg/spec customization, automatic whitelist, and paid logging.
- `/admin list` - admin-only paginated embed list fetched live from the Pterodactyl panel, showing each server UUID, panel email, and linked Discord user when available.
- `/admin createuser` - admin-only **FreeDash** account creation and Discord linking. It requests the target Discord user, email, username, first/last name, and temporary password, then DMs polished credentials. It has no paid/free selector because the bot never creates paid-panel accounts.
- `/admin manage`, `/admin console`, `/admin rename` - admin-only versions that can target any tracked server **and panel-created servers that are not in the local DB yet**; normal `/manage`, `/console`, `/rename`, `/power`, and `/reinstall` also show admin-wide server autocomplete when used by admins in the guild, while regular users only see their own servers.
- `/list` - public paginated embed list that still shows only the command executor’s own linked/tracked servers. Admins should use `/admin list` for all panel servers.
- `/manage` - users open a premium control panel for one of their own linked servers with live resource usage and start/stop/restart/kill buttons.
- `/power` - users or admins start, stop, or restart an owned/tracked server.
- `/reinstall` - users or admins reinstall an owned/tracked server.
- `/change-egg` - users can change their own server egg, while guild admins get all-server autocomplete; it asks for nest, egg, whether to wipe files, and whether to reinstall.
- `/console` - users send a console command to their own linked server.
- `/rename` - users rename their own linked server.
- `/schedule-restart` - users schedule a restart for one selected server and a time such as `12h` or `1d`; guild admins may use `all_servers:True` to schedule all tracked servers.
- `/renew` - guild admin-only renewal command with admin server autocomplete; tracked servers update the local DB, and panel-only servers still sync Saga/panel expiration by a time such as `30d`.
- `/delete` - guild admin-only command to delete one specific tracked server with `confirm:True`.
- `/deletesuspended` - guild admin-only cleanup command to delete suspended `free`, `paid`, or `all` servers after confirmation.
- `/resize` - guild admin-only resize modal GUI for RAM, disk, CPU, databases, allocations, and backups.
- `/suspend` and `/unsuspend` - admin-only suspension controls. `/suspend` can suspend a direct server, show a selectable menu by Discord user/email, or bulk suspend all except paid/whitelisted servers.
- `/stopall` - admin-only stop for all tracked servers except whitelisted servers.
- `/autobackup-enable` - owner/admin-only automatic backup scheduler using durations like `2d`, `4h`, or `12h`.
- `/link` - admin-only link of an existing panel email to a Discord user.
- `/nodes` - admin-only list of Pterodactyl deployment node names and IDs.
- `/whitelist` - admin-only add/remove whitelist for a server selected by admin name/UUID autocomplete; purge protection checks server ID, UUID, and identifier.
- `/purge confirm:True` - admin-only purge for tracked free servers only; paid servers and whitelist matches by server ID, UUID, or identifier are not deleted.
- `/autosuspend` - admin-only toggle to turn automatic expiration suspension on/off with admin server autocomplete; for panel-only servers, pass `time` like `30d` when turning it on so Saga has an expiration date.
- `/server-expirations` - admin-only view of tracked server expiration and autosuspend status.

## Pterodactyl notes

Use a Pterodactyl **Application API** key in `panel_api_key` and a **Client API** key in `client_api_key`. The Application key handles search/create/resize/suspend/delete/list operations; the Client key handles power signals, rename, console commands, resources, and backups for tracked server identifiers.

The bot does **not** create paid-panel users and does **not** use default eggs. Paid users must register through the paid panel; FreeDash accounts may be created only with `/admin createuser`. Admins must run `/link` for a paid-panel user before `/create-paid`; if it is used for an unlinked Discord user, the bot stops with an admin-visible error. Admins must select a nest first, then select an egg from that nest; startup, Docker image, and default egg variables are read from the panel. Server creation uses an available allocation from the selected node instead of blind automatic deployment.

## Branding

All embeds use ZeroX Host branding and include the developer credit: `ekamsidhu07 ekam`.
