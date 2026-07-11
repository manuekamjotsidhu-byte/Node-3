# ZeroX Host Pterodactyl Manager

A single-file Python Discord slash-command bot for **ZeroX Host** Pterodactyl provisioning and cleanup.

Inspired by the Vortex Ptero Manager workflow, but rebuilt as one `bot.py` with all runtime settings in `config.json`.

**Developer:** ekamsidhu07 ekam

## Current behavior

- No invite system.
- No SMTP flow.
- No panel user creation: the Pterodactyl user must already exist, and admins provide the panel email during creation.
- Only configured owners/admin roles can create, purge, whitelist, or view management data.
- Admins select a deployment node by name/ID with slash-command autocomplete.
- Admins create either `/create-free` or `/create-paid` servers with custom time, nest, egg, node, RAM, disk, CPU, databases, allocations, and backups; the Discord user must already be linked with `/link`.
- Paid server creations are logged to channel `1504092779700289536` unless overridden in `config.json`.
- `/purge` deletes tracked free servers only; paid and whitelisted servers are skipped.
- Created users receive styled ZeroX Host DM embeds with specs, panel URL, node, extras, expiration, and a Trustpilot review link.
- Expired tracked servers are automatically suspended by the background task.

## Files

- `bot.py` - the full Discord bot, slash commands, Pterodactyl Application/Client API clients, local SQLite `.db`, DM embeds, paid logs, purge, whitelist, autobackups, and expiration loop.
- `config.example.json` - copy to `config.json` and fill in Discord/Pterodactyl settings. Nests and eggs are fetched from the panel, not hardcoded.
- `requirements.txt` - Python dependencies.
- `data/zerox_host.db` - generated SQLite runtime database used for links, servers, whitelist, autobackups, and exact-time suspension. A legacy JSON mirror may also be created.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
python bot.py
```

## Slash commands

- `/create-free` - admin-only free server creation with linked Discord user, name, specs, nest, egg, node, days, and optional feature limits.
- `/create-paid` - admin-only paid server creation with duration, nest/egg/spec customization, automatic whitelist, and paid logging.
- `/admin list` - admin-only organized embed list fetched live from the Pterodactyl panel, showing each server UUID, panel email, and linked Discord user when available.
- `/list` - users list their own servers; admins see all tracked servers.
- `/power` - users or admins start, stop, or restart an owned/tracked server.
- `/reinstall` - users or admins reinstall an owned/tracked server.
- `/resize` - admin-only resize modal GUI for RAM, disk, CPU, databases, allocations, and backups.
- `/suspend` and `/unsuspend` - admin-only suspension controls. `/suspend` can suspend a direct server, show a selectable menu by Discord user/email, or bulk suspend all except paid/whitelisted servers.
- `/stopall` - admin-only stop for all tracked servers except whitelisted servers.
- `/autobackup-enable` - owner/admin-only automatic backup scheduler using durations like `2d`, `4h`, or `12h`.
- `/link` - admin-only link of an existing panel email to a Discord user.
- `/nodes` - admin-only list of Pterodactyl deployment node names and IDs.
- `/whitelist` - admin-only add/remove whitelist for a server selected by name/UUID autocomplete.
- `/purge confirm:True` - admin-only purge for tracked free servers; paid and whitelisted servers are not deleted.
- `/autosuspend` - admin-only toggle to turn automatic expiration suspension on/off per tracked server.
- `/server-expirations` - admin-only view of tracked server expiration and autosuspend status.

## Pterodactyl notes

Use a Pterodactyl **Application API** key in `panel_api_key` and a **Client API** key in `client_api_key`. The Application key handles search/create/resize/suspend/delete/list operations; the Client key handles power signals and backups for tracked server identifiers.

The bot does **not** create panel users and does **not** use default eggs. Admins must run `/link` for the Discord user first; if `/create-free` or `/create-paid` is used for an unlinked Discord user, the bot stops with an admin-visible error. Admins must select a nest first, then select an egg from that nest; startup, Docker image, and default egg variables are read from the Pterodactyl panel. Server creation uses an available allocation from the selected node instead of blind automatic deployment.

## Branding

All embeds use ZeroX Host branding and include the developer credit: `ekamsidhu07 ekam`.
