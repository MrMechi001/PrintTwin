# Security

## Design

* PrintTwin only **reads** from printers (status, job file, telemetry); it has no endpoint
  that sends commands, uploads or changes settings on a printer.
* Printer API keys stay in `config.local.toml` on the computer running PrintTwin; the
  browser never receives them.
* The viewer has **no login**. It is meant for your local network: don't expose port 8790
  to the internet (no router port forwarding). For remote viewing use a VPN such as
  Tailscale or WireGuard.
* The static file server only serves files inside `web/`.

## Reporting a vulnerability

Please report security problems **privately** via GitHub: *Security → Report a
vulnerability* on this repository – not in a public issue. You'll get an answer as soon
as possible; fixes are released with a note in `CHANGELOG.md`.
