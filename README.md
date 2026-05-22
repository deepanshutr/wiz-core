# wiz-core

Local HTTP daemon that controls Philips **WiZ** smart bulbs on the LAN
via JSON over UDP 38899. Sister to
[lgtv-core](https://github.com/deepanshutr/lgtv-core).

- Port: `127.0.0.1:8766`
- Discovery: UDP broadcast + unicast sweep
- Multi-bulb registry keyed by MAC; persists to `~/.config/wiz/state.json`
- See [`docs/superpowers/specs/2026-05-14-wiz-stack-design.md`](docs/superpowers/specs/2026-05-14-wiz-stack-design.md)
  for the full design.

## Quick start

```bash
pip install -e .[dev]
wiz-core serve            # foreground
# or as a systemd user unit:
mkdir -p ~/.config/systemd/user
cp systemd/wiz-core.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wiz-core
```

## Onboarding new bulbs (ESP-TOUCH)

A WiZ bulb in setup mode (its own `wiz_*` Wi-Fi AP) is provisioned with
your home Wi-Fi via Espressif's ESP-TOUCH protocol. ESP-TOUCH itself is
implemented in the standalone
[esptouch](https://github.com/deepanshutr/esptouch) library, which
`wiz-core` depends on — it is not a `wiz-core` sub-package.

```bash
curl -X POST http://127.0.0.1:8766/onboard \
  -H 'content-type: application/json' \
  -d '{"ssid": "MyHomeWiFi", "password": "secret", "timeout_s": 60}'
```

Responses:
- `200 {"onboarded": [{"mac", "ip", "name", "rssi"}, ...]}` — bulb(s) joined.
- `408 {"error": "timeout", ...}` — no bulb joined within `timeout_s`.
- `422` — malformed request body.
- `500 {"error": "esptouch_internal", ...}` — encoding or socket failure.

The host running `wiz-core` must be on the **2.4 GHz** home Wi-Fi (ESP-TOUCH
is 2.4 GHz only) for the bulb to receive the broadcast.

### Broadcast — apply one op to every bulb

`POST /bulb/all/{op}` fans an operation out to **every registered bulb**
concurrently (best-effort). It **always returns HTTP 200**, even when some or
all bulbs fail or the registry is empty.

| Method | Path | Body |
|---|---|---|
| POST | `/bulb/all/on` | — |
| POST | `/bulb/all/off` | — |
| POST | `/bulb/all/brightness` | `{"level": 10..100}` |
| POST | `/bulb/all/temp` | `{"kelvin": 2200..6500}` |
| POST | `/bulb/all/color` | `{"r": 0..255, "g": 0..255, "b": 0..255}` |
| POST | `/bulb/all/scene` | `{"scene": "<name>"\|<id>, "speed"?: 10..200}` |

Response shape:

```json
{
  "op": "on",
  "total": 7,
  "ok": 6,
  "failed": 1,
  "duration_ms": 412,
  "results": [
    {"mac": "d8a0118dc5c3", "ok": true,  "duration_ms": 38},
    {"mac": "d8a011c0a795", "ok": false, "error": "udp_timeout", "duration_ms": 1500}
  ]
}
```

Fan-out is concurrent, bounded by `min(bulb_count, 16)`. Raise or lower the
ceiling with the `WIZ_ALL_CONCURRENCY` environment variable. The per-bulb
timeout is the standard single-call WiZ timeout — there is no separate
broadcast timeout. An unknown scene name/id returns HTTP 400 (it is invalid
for every bulb); a malformed body returns HTTP 422.

## Sibling repos

- [wiz-cli](https://github.com/deepanshutr/wiz-cli) — Go cobra CLI
- [wiz-mcp](https://github.com/deepanshutr/wiz-mcp) — MCP stdio server for Claude Code
