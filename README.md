# wiz-core

Local HTTP daemon that controls Philips **WiZ** smart bulbs on the LAN
via JSON over UDP 38899. Sister to
[lgtv-core](https://github.com/deepanshutr/lgtv-core).

- Port: `127.0.0.1:8766`
- Discovery: UDP broadcast + unicast sweep
- Multi-bulb registry keyed by MAC; persists to `~/.config/philips-wiz-bulb/state.json`
- See [`docs/superpowers/specs/2026-05-14-philips-wiz-bulb-stack-design.md`](docs/superpowers/specs/2026-05-14-philips-wiz-bulb-stack-design.md)
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

- [philips-wiz-bulb-cli](https://github.com/deepanshutr/philips-wiz-bulb-cli) — Go cobra CLI
- [philips-wiz-bulb-mcp](https://github.com/deepanshutr/philips-wiz-bulb-mcp) — MCP stdio server for Claude Code
