# Cloudflare Tunnel Memory

## Purpose

Cloudflare Tunnel is the preferred dispatcher app path for mobile access because it avoids LAN routing, SSH tunnel setup on phones, and Tailscale user-limit friction.

## Quick Tunnel

For temporary public access, use Cloudflare Quick Tunnel:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

It prints a random `https://...trycloudflare.com` URL. The URL only works while the `cloudflared` process is running and may change after restart.

## Skill

`scripts/run_dispatcher_tunnel.py` is an integrated helper capability under the root `dispatcher-skill`. The helper:

1. Starts `dispatcher_app.server`.
2. Selects the requested port or a free fallback.
3. Starts `dispatcher_app.dispatcher` as the queue loop.
4. Starts `dispatcher_app.reboot` as the host-side reboot request watcher.
5. Starts Cloudflare Quick Tunnel for the selected local URL.
6. Extracts and prints the public `trycloudflare.com` URL.

Use:

```bash
python3 scripts/run_dispatcher_tunnel.py start --repo <repo> --port 8000
```

Use `--repo .` from the skill root, or `--repo <repo>` for another installed tree.

The default `start` command creates a tmux session with separate `server`, `dispatcher`, `reboot`, and `tunnel` windows. This keeps the Cloudflare URL stable during code debugging because the web server or dispatcher loop can be restarted without restarting `cloudflared`.

The tunnel helper starts the dispatcher loop with a default 0.5-second poll interval unless `--poll-seconds` is supplied.

Prefer keeping the Linux `cloudflared` binary outside exported skill packages. The helper resolves the binary from an explicit `--cloudflared` path, persisted `DISPATCHER_CLOUDFLARED`, ignored `data/bin/cloudflared`, PATH, then optional package-local `bin/cloudflared` only when the chosen package profile intentionally ships it.

For a host-managed server install, initialize with an explicit path:

```bash
python3 scripts/run_dispatcher_tunnel.py init --repo <repo> --cloudflared ~/.local/bin/cloudflared --port 8000
```

When `cloudflared` is missing, `init-status` prints copy-ready commands for the common server-local path. For a helper-managed install that stays out of package payload, use:

```bash
python3 scripts/run_dispatcher_tunnel.py install-cloudflared --repo <repo> --cloudflared-install-dir ~/.local/bin
python3 scripts/run_dispatcher_tunnel.py init --repo <repo> --cloudflared ~/.local/bin/cloudflared --port 8000
```

The installer defaults to ignored `data/bin/cloudflared` when no install directory is supplied; `~/.local/bin` is the recommended server-local path outside the skill checkout. Normal `start` does not download executables silently. `--install-cloudflared` is the opt-in install path for `start` or `foreground`.

The current installer downloads Cloudflare's latest GitHub release URL for standalone Linux amd64/arm64, checks only that the downloaded data is nonempty, and marks the target executable. It does not pin a version or verify a checksum. For reproducible or high-assurance packaging, add a pinned version plus checksum verification or ship a separately vetted binary. Unsupported OS/architecture combinations fail clearly instead of guessing.

Managers inside `codex exec` must not call tmux, Cloudflare tunnel commands, or `dispatcher_app.reboot request` directly. When runtime restart is needed, the manager finishes implementation, docs/memory updates, and verification first, then adds an operational marker to the final result:

```text
REBOOT_AFTER_TASK restart-dispatcher <short reason>
```

The dispatcher strips `REBOOT_AFTER_TASK` marker lines from the stored user-facing result, marks the task done, and appends the validated host-side reboot request. The `reboot` tmux window watches that file and applies `restart`, `restart-server`, `restart-dispatcher`, or `restart-reboot` while preserving the `tunnel` window. Use `restart-server`, `restart-dispatcher`, or `restart-reboot` for single-process restarts; use `restart` when both server and dispatcher must restart.

After code changes, restart only the server:

```bash
python3 scripts/run_dispatcher_tunnel.py restart-server --repo <repo> --port 8000
```

For the current installed tree, use `.`, or pass `<repo>` explicitly.

After dispatcher loop changes, restart only the dispatcher:

```bash
python3 scripts/run_dispatcher_tunnel.py restart-dispatcher --repo <repo> --port 8000
```

For the current installed tree, use `.`, or pass `<repo>` explicitly.

Inspect or stop:

```bash
python3 scripts/run_dispatcher_tunnel.py url
python3 scripts/run_dispatcher_tunnel.py status
python3 scripts/run_dispatcher_tunnel.py stop
```

## Fixed URL

A stable custom URL requires a Cloudflare-managed domain and a named tunnel with a Public Hostname. In Cloudflare One, choose `Published applications` when routing a web app to the dispatcher.

Use service target:

```text
http://localhost:8000
```

## Notes

- Do not store tunnel tokens or private generated URLs in this repository.
- Keep exported packages slim by omitting `bin/cloudflared` unless the release profile intentionally includes a vetted binary.
- Use `--cloudflared ~/.local/bin/cloudflared`, another host-managed path, PATH, or ignored `data/bin/cloudflared` for normal server installs.
- Keep dispatcher and `cloudflared` running in the same environment if the service target is `localhost`.
- For containers, Quick Tunnel is simpler than Tailscale because it does not require `systemd` or a TUN device.
