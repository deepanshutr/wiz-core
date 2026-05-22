# CLAUDE.md — wiz-core

Local HTTP daemon for Philips WiZ smart bulbs.

## Conventions

- Python 3.11+
- All identifiers (subnet, broadcast addr, bulb IPs) come from env or
  `~/.config/wiz/state.json`. **Never hard-code IPs in source.**
- WiZ uses JSON over UDP 38899. `getPilot` / `setPilot` / `getSystemConfig`.
- Tests use `pytest`; `asyncio_mode = "auto"`.
- Run `ruff check`, `mypy`, `pytest -v` before commit.

## Local state (gitignored)

- `~/.config/wiz/state.json` — bulb registry (mode 0600)
- `~/.config/wiz/state.env` — env overrides

## systemd

- User unit: `~/.config/systemd/user/wiz-core.service`
  (copied from `systemd/wiz-core.service`)
- `systemctl --user restart wiz-core`
- `journalctl --user -u wiz-core -f`

## Don't repeat

- Only `NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict`,
  `ReadWritePaths=` work in user-scope systemd. The heavier `Protect*`
  knobs trip `status=218`.
- The WiZ JSON-over-UDP protocol is undocumented; field names are case-sensitive
  (`sceneId` not `scene_id`, `dimming` 10-100 not 0-100).
- ESP-TOUCH onboarding is provided by the standalone `esptouch` library
  (a git dependency — `esptouch @ git+https://github.com/deepanshutr/esptouch.git`),
  NOT a `wiz_core` sub-package. The handler `wiz_core/onboard.py` imports
  `from esptouch import run, NewBulb, EsptouchError` and owns discovery
  polling. The encoding audit (`python -m esptouch.audit`) is a CI gate
  in the `esptouch` repo, not here. ESP-TOUCH is 2.4 GHz only and
  broadcasts to 255.255.255.255.
