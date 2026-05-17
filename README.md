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

## Sibling repos

- [wiz-cli](https://github.com/deepanshutr/wiz-cli) — Go cobra CLI
- [wiz-mcp](https://github.com/deepanshutr/wiz-mcp) — MCP stdio server for Claude Code
