# cloudflared configuration

Two modes are supported. **Token mode is the default** (used by
`deploy/docker-compose.yml`); the files here exist for the alternative
file-managed mode.

## Token mode (default, recommended)

1. Create the tunnel in the Cloudflare Zero Trust dashboard:
   **Networks → Tunnels → Create a tunnel → Cloudflared**.
2. Copy the tunnel token (`eyJ...`) into `deploy/.env` as `TUNNEL_TOKEN`.
3. Add the public hostname in the dashboard and point its service URL at
   `http://traefik:80` (the static Traefik entrypoint on the internal network).
4. `docker compose up -d` — done. Routing rules and middlewares live in Traefik
   docker labels on the `server` service; nothing routing-related is stored on
   the VPS.

## File mode (alternative)

For GitOps-style setups where the ingress file is version-controlled:

```bash
# on the VPS (or wherever you keep credentials):  
cloudflared tunnel login
cloudflared tunnel create ta-webgui      # prints the tunnel UUID, writes <UUID>.json
```

Then:

1. Put `config.yml` next to the credentials file and set `tunnel:` to the UUID
   from `tunnel create`.
2. Mount both into the connector container and switch the command with a small
   compose override (`deploy/compose.override.filemode.yml`):

```yaml
# deploy/compose.override.filemode.yml
services:
  cloudflared:
    command:
      - tunnel
      - --no-autoupdate
      - --config=/etc/cloudflared/config.yml
      - run
      - ta-webgui
    volumes:
      - ./cloudflared/config.yml:/etc/cloudflared/config.yml:ro
      - ${CLOUDFLARED_CREDENTIALS_FILE}:/etc/cloudflared/credentials.json:ro
```

```bash
# run with the override:
docker compose -f docker-compose.yml -f compose.override.filemode.yml up -d
```

Add a `hostname:` rule in `config.yml` for each public hostname and keep the
`http_status:404` catch-all last (required by cloudflared when hostname rules
exist).

## Handling credentials safely

- The credentials JSON (`<UUID>.json`) is a **secret**: store it outside the
  repo, `chmod 600`, never commit it, and mount it read-only.
- The tunnel token in `.env` is likewise secret: `.env` is git-ignored. If a
  credential leaks, delete the tunnel in the dashboard and create a new one —
  rotation is instant and requires no VPS changes beyond the new token.

## Why routing lives in Traefik labels (both modes)

cloudflared forwards each public hostname as the HTTP `Host` header to one
static service URL (`http://traefik:80`). Traefik's docker provider then routes
on `Host()`/`PathPrefix()` rules defined next to the app it routes to. One
routing truth, testable locally with `docker compose config`, changeable with
`docker compose up -d` — no Cloudflare API round-trips for app-level changes.