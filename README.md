# ZeroX Host Pterodactyl Manager

A single-file Python Discord slash-command bot for **ZeroX Host** Pterodactyl provisioning and cleanup.

Inspired by the Vortex Ptero Manager workflow, but rebuilt as one `bot.py` with all runtime settings in `config.json`.

**Developer:** ekamsidhu07 ekam

## Current behavior

- No invite system.
- No SMTP flow.
- All account, server, and management actions use the single configured Pterodactyl panel at `https://gp.zeroxhost.space`; `/admin createuser` can create and link panel accounts there.
- Only configured owners/admin roles can create, purge, whitelist, or view management data. Admin commands are guild-only and are intentionally hidden/blocked in DMs; DMs only expose user-safe commands for linked servers.
- Admins select a deployment node by name/ID with slash-command autocomplete.
- Admins create either `/create-free` or `/create-paid` servers on the same panel with custom `time`, nest, egg, node, RAM/Disk entered in GB, CPU, databases, allocations, and backups; the Discord user must already be linked with `/link` or `/admin createuser`.
- Paid server creations are logged to channel `1504092779700289536` unless overridden in `config.json`.
- `/purge` deletes tracked free servers only; paid and whitelisted servers are skipped.
- Created users receive styled ZeroX Host DM embeds with specs, panel URL, node, extras, expiration, and a Trustpilot review link.
- Tracked server details are refreshed from the live Pterodactyl panel before user lists and management actions, and a 60-second background sync refetches panel nodes, nests, eggs, servers, users, and updates tracked server names/specs/suspension/deletion status from panel activity.
- Saga Auto Suspension can be synced during create, renew, and `/autosuspend` changes by configuring `saga_auto_suspend_enabled` and the panel field name in `saga_auto_suspend_field` (fallbacks try `suspended_at`, `expiration_date`, and `expires_at`).
- Expired tracked servers are automatically suspended by the background task. Paid users receive renewal reminders 7 days and 24 hours before suspension; free users receive the 24-hour reminder. Everyone receives a deletion warning 24 hours before cleanup, and suspended servers are deleted after 7 days.

## Files

- `bot.py` - the full Discord bot, slash commands, Pterodactyl Application/Client API clients, local SQLite `.db`, DM embeds, paid logs, purge, whitelist, autobackups, and expiration loop.
- `config.example.json` - copy to `config.json` and fill in Discord/Pterodactyl settings for the single `https://gp.zeroxhost.space` panel. Nests and eggs are fetched from the panel, not hardcoded.
- `requirements.txt` - Python dependencies.
- `data/zerox_host.db` - generated SQLite runtime database used for links, servers, whitelist, autobackups, and exact-time suspension. A legacy JSON mirror may also be created.

## Setup

Commands are synced globally for guild and DM visibility, and the bot clears configured-guild command copies to prevent duplicate slash commands such as duplicate `/unlink` entries. If a new command does not appear immediately after an update, restart the bot once and wait for Discord global command propagation.


```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
# Fill owner_ids with real Discord user IDs for DM admin access.
python bot.py
```

## Slash commands

- `/create-free` - admin-only free-plan server creation on the single configured panel. Its node, nest, and egg autocomplete values are loaded from `https://gp.zeroxhost.space`, and it requires the user to have a linked panel account.
- `/create-paid` - admin-only paid server creation with `time` duration, RAM/Disk in GB, nest/egg/spec customization, automatic whitelist, and paid logging.
- `/admin list` - admin-only paginated embed list fetched live from the Pterodactyl panel, showing each server UUID, panel email, and linked Discord user when available.
- `/admin createuser` - admin-only panel account creation and Discord linking on `https://gp.zeroxhost.space`. It requests the target Discord user, email, username, first/last name, and temporary password, then DMs polished credentials.
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
- `/deletesuspended` - guild admin-only cleanup command that fetches live server details from the panel, detects suspended servers from panel flags/status values, and deletes suspended `free`, `paid`, or `all` servers after confirmation.
- `/resize` - guild admin-only resize modal GUI for RAM, disk, CPU, databases, allocations, and backups.
- `/suspend` and `/unsuspend` - admin-only suspension controls. `/suspend` can suspend a direct server, show a selectable menu by Discord user/email, or bulk suspend all except paid/whitelisted servers.
- `/stopall` - admin-only stop for all tracked servers except whitelisted servers.
- `/autobackup-enable` - owner/admin-only automatic backup scheduler using durations like `2d`, `4h`, or `12h`.
- `/link` - admin-only link of an existing panel email to a Discord user. It refuses to overwrite an existing link and shows the currently linked email; use `/unlink` first to change accounts.
- `/unlink` - admin-only removal of a Discord user’s linked panel account so the user can be linked again.
- `/nodes` - admin-only list of Pterodactyl deployment node names and IDs.
- `/whitelist` - admin-only `add`, `remove`, or `list` command. Its `add` autocomplete only shows unprotected servers, `remove` only shows manually whitelisted servers, and `list` fetches the live `https://gp.zeroxhost.space` panel, labels paid servers as **Paid** and manually protected servers as **Whitelisted**, and automatically removes deleted servers from the whitelist. Purge protection checks server ID, UUID, and identifier.
- `/purge confirm:True skip_keyword:smp` - admin-only purge for live panel servers; the confirmation scans panel-created and tracked servers, paid tracked servers, whitelist matches by server ID/UUID/identifier, and names starting with the optional prefix such as `smp` or `[smp]` are not deleted.
- `/autosuspend` - admin-only command; selecting only `server` shows auto-suspend status, suspension time, remaining time, and the last configured duration (not the original server age), while `state:on/off` toggles automatic expiration suspension. For panel-only servers, pass `time` like `30d` when turning it on so Saga has an expiration date.
- `/server-expirations` - admin-only view of tracked server expiration and autosuspend status.

## Pterodactyl notes

Use a Pterodactyl **Application API** key in `panel_api_key` and a **Client API** key in `client_api_key`. The Application key handles search/create/resize/suspend/delete/list operations; the Client key handles power signals, rename, console commands, resources, and backups for tracked server identifiers.

The bot uses the single configured Pterodactyl panel and does **not** use default eggs. Admins must run `/link` or `/admin createuser` before `/create-free` or `/create-paid`; if creation is used for an unlinked Discord user, the bot stops with an admin-visible error. Admins must select a nest first, then select an egg from that nest; startup, Docker image, and default egg variables are read from the panel. Server creation uses an available allocation from the selected node instead of blind automatic deployment.

## Branding

All embeds use ZeroX Host branding and include the developer credit: `ekamsidhu07 ekam`.
