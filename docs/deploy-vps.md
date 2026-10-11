# Deploying to a VPS with Cloudflare Tunnel + Traefik

This guide deploys the TradingAgents-webGUI server to a plain Ubuntu LTS VPS
behind a Cloudflare Tunnel with Traefik v3 as the internal edge router. No
inbound ports are opened on the VPS.

## How it fits together

```
                        (outbound-only, no open ports)

  Browser                 Cloudflare edge                VPS
  -------                 ---------------           -------------
     |  https://gui...       |                          |
     |---------------------->|  tunnel (outbound)       |
     |                       |------------------------->| cloudflared
     |                       |                          |   |
     |                       |                          |   v
     |                       |                          | traefik :80  (internal)
     |                       |                          |   |  labels: rules + middlewares
     |                       |                          |   v
     |                       |                          | uvicorn :8000 (server)
     |                       |                          |   |  /api/*  bearer gate
     |                       |          response        |   |  /       SPA
     |<----------------------|<-------------------------|---'
```

What runs where:

| Component | Runs | Listens | Exposed to internet |
| --- | --- | --- | --- |
| cloudflared | container `ta-webgui-cloudflared-1` | outbound to Cloudflare edge | no (outbound only) |
| Traefik v3.3 | container `ta-webgui-traefik-1` | `:80` on internal docker network only | no |
| uvicorn (FastAPI + SPA) | container `ta-webgui-server-1` | `:8000` container-internal | no |


The only machine with a credential is the VPS: the tunnel token in
`deploy/.env`. A compromised VPS cannot be reached from the internet by the
attacker of a zero-day in a listening daemon — there is simply no listener on
the public interface.

## Prerequisites

- An Ubuntu LTS VPS (22.04/24.04) with a non-root sudo user.
- A Cloudflare account with the domain's DNS on Cloudflare (free plan is fine).
- Docker Engine + compose plugin (installed below).
- ~1 GB RAM is enough; the server process is single-process by design (ADR 0006).

## Step 1 — Install Docker

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" |
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER   # log out/in to take effect
```

Verify: `docker run --rm hello-world`.

## Step 2 — Create the tunnel in Cloudflare

1. Log in to the **Cloudflare Zero Trust dashboard** → **Networks → Tunnels**
   → **Create a tunnel** → connector type **Cloudflared**, name it e.g.
   `ta-webgui`.
2. Copy the **tunnel token** (starts with `eyJ...`) — you will paste it into
   `deploy/.env` on the VPS in the next step.
3. Under the tunnel's **Public Hostname** tab add:
   - **Subdomain + domain**: your `gui.example.com` (must exactly match
     `DOMAIN` in `.env`),
   - **Service**: `HTTP` `traefik:80`

   Note the service URL is a **docker-network hostname**, not localhost: the
   connector runs inside the compose network `ta-webgui-edge` and reaches
   Traefik by container name.

## Step 3 — Get the files onto the VPS

Two supported ways:

**A. Pull the prebuilt image** (recommended; build happens in CI on tag push):

```bash
sudo mkdir -p /srv/ta-webgui && cd /srv/ta-webgui
# fetch only the deploy directory of the repo (or clone if you prefer):
git clone --depth 1 https://github.com/DoThatKarma/TradingAgents-webGUI.git
cd TradingAgents-webGUI/deploy
```


**B. Build locally on the VPS** (no GHCR pull needed):

A and B share the same compose file; B uses the `build:` section of
`deploy/docker-compose.yml`.

## Step 4 — Configure and start

```bash
cd /srv/ta-webgui/TradingAgents-webGUI/deploy
cp .env.example .env
nano .env   # set TUNNEL_TOKEN, DOMAIN, TA_WEBGUI_API_TOKEN, limits
```

Generate tokens:

```bash
openssl rand -hex 32   # TA_WEBGUI_API_TOKEN
```

Then start:

```bash
docker compose up -d          # pulls ghcr.io/...:latest
# or build on the VPS:
docker compose up -d --build
```

Compose file: `deploy/docker-compose.yml` — three services (cloudflared,
traefik, server), one network, one volume, **no `ports:`** anywhere.

## Step 5 — Verify

```bash
# containers healthy:
docker compose ps

# health endpoint (auth-exempt by design):
curl -s https://<DOMAIN>/api/health

# auth gate works (401 without token, 200 with):
curl -s -o /dev/null -w '%{http_code}\n' https://<DOMAIN>/api/runs
# expect 401 when TA_WEBGUI_API_TOKEN is set

curl -s -o /dev/null -w '%{http_code}\n' --max-time 10 https://<DOMAIN>/   # expect 200

```

**SSE check** (the live run stream must not stall):

```bash
curl -N https://<DOMAIN>/api/runs/<run-id>/events
```

It should stream continuously. Two things protect it:

- **Traefik does not buffer responses by default**, so the SSE stream passes
  through untouched (no `compress` middleware on the /api router — see
  `deploy/docker-compose.yml` labels).
- **Cloudflare free-tier idle timeout is ~100 s**; the backend keepalive
  frames on the SSE endpoint reset that timer, so runs keep streaming as long
  as the run is active.

## Step 6 — Upgrades

```bash
cd /srv/ta-webgui/TradingAgents-webGUI/deploy
docker compose pull server
docker compose up -d
```

The upgrade is zero-downtime-ish: the tunnel connector reconnects, Traefik
re-routes, and in-flight SSE streams of the OLD process get cut when the old
container stops. The SPA will auto-reconnect the event stream.

## Token rotation

Rotate the app token periodically (e.g. quarterly) or after staff changes:

1. Generate a new token: `openssl rand -hex 32`
2. Update `TA_WEBGUI_API_TOKEN` in `deploy/.env`

3. Apply: `docker compose up -d` (recreates only the server container)
4. Verify: old token → 401, new token → 200
5. Notify users of the new token — the frontend login asks for it.

Rotation is instant: the token is checked per-request by the app (constant-time compare); no restart beyond `up -d` is needed.

## Rotate the tunnel token too (rare)

If the TUNNEL_TOKEN leaks:

1. Zero Trust dashboard → Tunnels → your tunnel → **Delete the connector** (or the whole tunnel and create a new one).

2. Paste the new token into `.env` → `docker compose up -d` (recreates the cloudflared container).
3. Public hostnames and ingress state lives in the dashboard, so nothing on the VPS needs reconfiguration beyond the token.

## Why no open ports

Every open listener is attack surface. A public `:80/:443` on the VPS means:

- TLS termination, cert renewal, and HTTP hardening must be perfect on the VPS
  (certbot timers, cipher suites, ALPN, request-size limits — your problem now);
- Every CVE in a listening daemon (nginx, traefik, the app itself) is directly
  internet-reachable and every scanner can fingerprint the stack;

- Rate limiting is the only brake against credential stuffing.

With the tunnel model, the VPS initiates all connections outbound; Cloudflare
absorbs DDoS and hides the VPS IP; TLS terminates at Cloudflare's edge; and the
only ingress path is the tunnel itself, which authenticates with the token.
Traefik still does real routing/middleware work — but behind the tunnel, on a
network nothing can reach except cloudflared.

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `curl https://<DOMAIN>/api/health` → 530/1033 from Cloudflare | Connector not running: `docker compose logs cloudflared`. Check `TUNNEL_TOKEN` and that the tunnel is healthy in the dashboard. |
| 404 for the SPA but /api/health works | Traefik `Host()` rule does not match: `DOMAIN` in `.env` must exactly match the dashboard hostname. |
| 401 on every /api call | Token missing/wrong: the frontend login needs `TA_WEBGUI_API_TOKEN`'s value. |
| SSE stalls after ~100 s | Keepalive broken: check server logs; confirm the /api router has no compress middleware. |
| compose wants to rebuild every time | You edited labels/env; `up -d` recreates containers when env/labels change — expected. |


## What the owner must do in the Cloudflare dashboard (checklist)

- [ ] Create tunnel (`Networks → Tunnels → Create a tunnel → Cloudflared`), copy token → `.env`
- [ ] Public hostname: subdomain/domain = `DOMAIN` in .env, service = `HTTP traefik:80`

- [ ] (Optional) WAF rules to block obvious scanners, or a Cloudflare Access policy in front of the whole hostname for team-internal use
- [ ] Confirm the domain uses Cloudflare DNS (proxied DNS records are auto-managed by the tunnel feature; no manual CNAME needed)

- [ ] Token hygiene: tunnel token lives only in VPS `.env` (chmod 600), never in git