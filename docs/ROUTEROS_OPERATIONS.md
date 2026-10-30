# ALWATHIQ-NET: optional MikroTik Telegram operations

## Provenance and scope

Feature goals were compared against the public `zesbe/mikrotik-telegram-bot` README and Python entrypoint on 2026-10-30.
This implementation is independent; **no third-party code, credentials, VPN preshared keys, scripts or backups were copied**.
The original repository has no GitHub-detected license; do not copy its code into this service.

This branch extends the existing secured ALWATHIQ bot from PR #1. It does not merge or deploy to `main`, modify RB5009, touch SAS4/RADIUS, or connect to a production network.

## Read-only commands (from an explicitly configured MikroTik HTTPS REST endpoint)

- `/resource`: uptime, RouterOS version, CPU and memory
- `/interfaces`: first 15 interface states
- `/traffic`: cumulative interface RX/TX bytes (not instantaneous throughput)
- `/vpn`: L2TP client interface states; does not reveal credentials or pre-shared keys
- `/ping 8.8.8.8`: RouterOS-originated ping, numeric IPv4/IPv6 destination only, count 3
- `/listpppoe`, `/activepppoe`, `/profiles`: PPP secrets (passwords excluded), active sessions, profiles
- `/listhotspot`, `/activehotspot`: Hotspot users and active sessions
- Existing BRAIN commands `/router`, `/pppoe`, `/status`, `/incidents` remain unchanged and use the BRAIN service.

The bot is private-chat-only and requires a nonempty Telegram user allowlist. Lists display at most 15 rows. RouterOS REST `.proplist` intentionally excludes passwords from all GET responses. A missing integration is an error, never a fake success.

## Owner-only operations (disabled by default)

Set both `ROUTEROS_WRITES_ENABLED=true` and a numeric `ROUTEROS_OWNER_TELEGRAM_IDS` subset of `ALLOWED_TELEGRAM_USER_IDS` to enable:

- `/disablepppoe username`, `/enablepppoe username`, `/delpppoe username`, `/kickpppoe username`
- `/disablehotspot username`, `/enablehotspot username`, `/delhotspot username`

The bot queues one pending action per owner, then demands `/confirm CODE` within 90 seconds. The code is one-use and in-memory only. Commands verify the exact user name and resolve a RouterOS internal record ID, not user-supplied path fragments. Disabling an account blocks new authentication but can leave existing sessions connected; use a separately confirmed session kick as required.

Even with owner write approval, `/reboot` and `/backup` require **another** explicit flag: `ROUTEROS_CRITICAL_ENABLED=true`. The backup command saves a local binary file named `alwathiq-YYYYMMDD-HHMMSS` on the router. It does not download or forward this sensitive file to Telegram. Backups consume router storage. Never enable these commands without rollback planning.

`/addpppoe`, `/addhotspot`, and `/vpnconfig` are registered but intentionally return a safe **not-implemented** message. Credential creation/distribution must be implemented via the approved subscriber administration backend rather than storing passwords in Telegram chats. This is **not** full feature parity with the reference bot.

## HTTPS configuration

```env
ROUTEROS_HTTPS_URL=https://10.10.10.1
ROUTEROS_USERNAME=<dedicated-least-privileged-account>
ROUTEROS_PASSWORD=<secret-stored-only-on-ubuntu>
ROUTEROS_CA_FILE=/etc/alwathiq/ca/router.crt
ROUTEROS_OWNER_TELEGRAM_IDS=<numeric-owner-id>
ROUTEROS_WRITES_ENABLED=false
ROUTEROS_CRITICAL_ENABLED=false
```

Use an independently verified certificate trust chain and a narrow RouterOS `www-ssl` service address restriction. If the device uses a private CA, place only its **public CA certificate** in `./routeros-certs/router.crt` on Ubuntu (the Compose mount is read-only). Neither the certificate private key nor the device admin password belongs in the repository. Do not enable `www` / plain HTTP, disable TLS verification, or expose the API to the Internet.

The RouterOS account should be restricted to the Ubuntu source IP. For read-only mode, grant only the RouterOS policies actually necessary for querying the selected endpoints; only grant write permissions after validating the exact command requirements on RouterOS 7.24.2 in a test environment. Avoid full admin privileges.

## Acceptance gates

1. Offline CI and security tests pass on this branch.
2. Review code and API permissions; do not merge without the owner's review.
3. Confirm single Telegram poller, secure token distribution, private DNS/routing, firewall reachability and CA certificate.
4. On a lab or approved router, verify each read endpoint, including PPP and Hotspot field shapes.
5. For writes, implement **persistent** action audit, transaction/idempotency control, independent approvals where needed, explicit rollback and rate limits across restarts. In-memory confirmation alone is not sufficient for an unattended production rollout.
6. Only then test approved non-disruptive actions on a test subscriber. Never test kick/reboot/delete on the production RB5009 without explicit change control.

### Offline verification

```bash
python3.12 -m pip install -r requirements.txt
python3.12 -m compileall -q bot.py core.py routeros_ops.py routeros_bot.py tests
python3.12 -m unittest discover -s tests -v
```

All tests are HTTP-mocked. A green GitHub job cannot prove RouterOS reachability, certificate validity, Telegram delivery or live subscriber operations.
