# ALWATHIQ-NET Telegram Bot

Production-oriented Telegram control plane with **read-only default and optional, owner-gated RouterOS operations** for the ALWATHIQ-NET BRAIN API. This repository is a separate bot service; it does not change PostgreSQL, SAS4, RADIUS or MikroTik configuration.

## Current functions

This branch adds independently written RouterOS 7 HTTPS REST commands for `/resource`, `/interfaces`, `/traffic`, `/vpn`, `/ping`, `/listpppoe`, `/activepppoe`, `/profiles`, `/listhotspot` and `/activehotspot`. It also stages owner-only `/disablepppoe`, `/enablepppoe`, `/delpppoe`, `/kickpppoe`, equivalent Hotspot controls, `/backup`, and `/reboot` behind explicit disabled-by-default flags and single-use confirmations.

**Important:** adding new users, publishing VPN credentials, and distributing subscriber passwords in Telegram are **not implemented**. All live router operations require separately configured RouterOS HTTPS access; BRAIN remains read-only. Full installation, safety and acceptance details: [RouterOS operations](docs/ROUTEROS_OPERATIONS.md).


- Private-chat-only authorization by fixed Telegram numeric user IDs.
- `/start`, `/help`: bot command menu.
- `/status`: BRAIN `GET /readyz`; readiness is **not** proof of a working RouterOS integration.
- `/router`: allowlisted `routeros.resources` diagnostic; returns only selected resource fields.
- `/pppoe`: allowlisted `routeros.pppoe_active`; returns only active-session count, never subscriber identifiers.
- `/incidents`: bounded incident-page summary, no raw evidence or subscriber details.
- A missing or unconfigured integration is reported as such, never as healthy.

No dynamic RouterOS command or shell execution, no passwords sent to Telegram, no PPP account creation, no balance changes, and no SAS4 access. Direct RouterOS HTTPS REST access is **optional** and absent until separately configured. RouterOS writes, backup and reboot remain opt-in, confirmed and **not authorized for production deployment by this code review**.

## Safe installation on Ubuntu (Docker Compose)

Prerequisites: Ubuntu with Docker Engine and Compose plugin. The upstream BRAIN service must be **separately approved**, bound to a local/private interface, and must have a strong API bearer token if enabled. Make sure only one polling process uses this bot token. The previous GitHub Actions workflow no longer starts a persistent polling process.

```bash
git clone https://github.com/Aa123hh/telegram-bot.git alwathiq-telegram
cd alwathiq-telegram
# Use the approved release commit/branch, not an unreviewed default branch.
cp .env.example .env
chmod 600 .env
# Edit .env locally; never paste the bot token or BRAIN token into GitHub/chat/logs.
docker compose config --quiet
docker compose up --build -d
docker compose ps
docker compose logs --tail=30 alwathiq-telegram
```

Before launch, set `BOT_TOKEN` and `ALLOWED_TELEGRAM_USER_IDS` (numeric IDs). At least one allowed user is mandatory; startup fails otherwise. `ALWATHIQ_BRAIN_URL` and `ALWATHIQ_BRAIN_TOKEN` are optional together. Without both, read commands return an explicit not-connected result. Using `http://127.0.0.1:8000` is appropriate **only** when the approved BRAIN service is actually listening there. Do not enable RouterOS adapters or change BRAIN settings as part of this install.

The Linux host-network option in `compose.yaml` lets the isolated bot access a separate BRAIN service bound to host loopback. The bot itself listens on **no** inbound HTTP/TCP port and uses Telegram long polling. Restrict SSH and local file permissions, and ensure Docker access is limited to trusted operators. Docker daemon administrators can read container environment values: use host-level secret management if that risk is unacceptable.

Do not run multiple copies of the bot with the same token. To stop:
```bash
docker compose down
```

Rollback: stop the new container, check out the previously approved commit and rebuild it only if it has been independently reviewed and secured. Never revert to the old GitHub Actions job that runs continuously inside CI. Any token ever exposed should be rotated via BotFather.

## API contract

Use the BRAIN API documented in `Aa123hh/alwathiq--brain`. This bot sends Bearer authentication and only permits these exact request routes:

- GET `/readyz`
- GET `/api/v1/incidents`
- POST `/api/v1/diagnostics/runs` with `routeros.resources` or `routeros.pppoe_active`.

Requests use a fixed 0.5–15 second timeout and a 64 KiB reply size ceiling; no redirects or environment proxy trust. Commands use an in-process per-user cooldown. The raw diagnostic result is not forwarded to Telegram. This **does not** provide distributed throttling, telemetry freshness guarantees, bot-token rotation, production audit storage, or end-to-end verified device monitoring.

For live access, use a separate least-privilege source approved by the infrastructure owner and independently verify BRAIN access, certificate/route security and source freshness. Production network hardware and SAS4 must remain unchanged until separately authorized.

## Offline verification

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m compileall -q bot.py core.py routeros_ops.py routeros_bot.py tests
python -m unittest discover -s tests -v
```

GitHub Actions runs only offline tests and compilation. Its success is not proof that Telegram login, DNS, Docker deployment, or the real BRAIN API works. A release is operationally accepted only after separate token provisioning, a single active poller, completed CI, valid /status response, negative authorization test, safe observability, and a documented rollback.

## Deployment status

The repository contains candidate code and a repeatable container definition; writes are disabled by default. **No actual Ubuntu deployment, successful Telegram poll, MikroTik read, or BRAIN integration is implied by this source repository.**
