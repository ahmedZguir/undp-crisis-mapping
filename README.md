<p align="center">
  <img src=".github/assets/rasid-citizen.svg" alt="RASID citizen app" width="120">
  &nbsp;&nbsp;&nbsp;
  <img src=".github/assets/rasid-admin.svg" alt="RASID admin console" width="120">
</p>

<h1 align="center">RASID</h1>

<p align="center"><b>Community-driven crisis mapping: residents report building damage, coordinators direct the response.</b></p>

<p align="center">
  <a href="https://rasid.qcri.org">Live demo</a> &nbsp;|&nbsp;
  <a href="https://rasid.qcri.org/landing.html">Landing page</a>
</p>

<p align="center">
  <a href="#features">Features</a> &nbsp;|&nbsp;
  <a href="#project-structure">Structure</a> &nbsp;|&nbsp;
  <a href="#quick-start-local-trial">Quick start</a> &nbsp;|&nbsp;
  <a href="#deploying-to-a-server">Deploy</a> &nbsp;|&nbsp;
  <a href="#optional-features">Optional features</a> &nbsp;|&nbsp;
  <a href="#troubleshooting">Troubleshooting</a> &nbsp;|&nbsp;
  <a href="#development">Development</a>
</p>

RASID lets people affected by a crisis report damage to specific buildings in the first hours, and lets coordinators see those reports live and direct the response. It was built for the UNDP Crisis Mapping Challenge by the Humanitarian AI team at the Qatar Computing Research Institute (QCRI). The whole platform is open source and self-hosted: one Docker Compose stack on a single server.

## Features

### For residents: a damage report in under a minute

- **Guided and multilingual:** the six UN languages (Arabic, Chinese, English, French, Russian, Spanish), with right-to-left layout for Arabic.
- **Building-level location:** tap your building's footprint on the map, or describe the place in words when there is no pin.
- **Photo or description:** add a photo, type a description or record a voice note. With AI enabled, the photo suggests a damage level and voice notes are transcribed.
- **Works offline:** with no signal, the report is queued on the device and sent automatically once a connection returns.
- **Per-crisis forms:** coordinators add their own survey questions for each crisis.
- **Many ways in:** the installable web app, plus optional WhatsApp, SMS and voice-call channels that reach the same map, even from a basic phone.

### For coordinators: one operating picture

- **Live dashboard:** reports rolled up by severity over a heatmap, with filtering and search.
- **Crisis setup:** create a crisis, draw or pick its area, load building footprints from Overture Maps, and choose what the public sees.
- **Prioritisation:** clustering surfaces the worst-hit areas and likely duplicate reports.
- **AI briefs (optional):** one-click summaries, findings and recommended actions grounded in the report data, plus a chat over the reports.
- **Export:** GeoJSON and CSV exports, and photo bundles.
- **Privacy built in:** consent before reporting, field stripping on public data, and citizen data deletion.

Native Android and iOS shells (Capacitor) live in `apps/pwa/android` and `apps/pwa/ios`. This guide covers the web app only.

## Project structure

```text
apps/api/              FastAPI backend and background worker
apps/pwa/              React web app (citizen app and admin console)
apps/pwa/android, ios  Capacitor native shells (not covered by this guide)
supabase/migrations/   Database schema, applied automatically on startup
infra/                 Docker Compose, Dockerfiles, Caddy, secret generator
docs/legal/            Privacy policy shown to citizens
```

## Quick start (local trial)

Requirements: Docker Engine (Linux) or Docker Desktop, Docker Compose v2.24 or newer (check with `docker compose version`), about 4 GB of RAM, and internet access for the first build. Deploying to a server also needs Python 3.10 or newer.

```bash
git clone https://github.com/ahmedZguir/undp-crisis-mapping.git
cd undp-crisis-mapping
cp .env.example .env
docker compose up -d --build
```

The `.env.example` defaults use Supabase's public demo secrets. They are fine on your own machine and must never be used on a server (see [Deploying to a server](#deploying-to-a-server)).

The first build takes several minutes. Run `docker compose ps`: the stack is ready when `api` shows `healthy`. The `migrate` service applies the database schema once and exits; `docker compose ps -a` lists it as `Exited (0)`, which is expected.

| What | Where |
|---|---|
| Citizen app | http://localhost:8080 |
| Admin console | http://localhost:8080/admin (default login `admin@example.com` / `change-me-now`, set by `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `.env`) |
| Supabase Studio (database UI) | http://localhost:54321 (user `supabase`, password `DASHBOARD_PASSWORD` from `.env`) |

## First steps

1. Open the admin console in a desktop browser (at least 1000 px wide) and sign in. A welcome screen offers **Start the walkthrough** or **Skip for now**.
2. Go to **Crises** and click **New Crisis**. Fill in the name, type, area ("Where it happened": countries, a searched place, a drawn polygon or a GeoJSON upload) and dates, then click **Create crisis**. Loading building data needs an area or at least one country. You can also schedule activation in this form.
3. Optional: in the crisis's **Building shapes** section, click **Load building data**, then **Yes, load buildings** (or pick **Yes, load now** while creating the crisis). This needs internet and can take several minutes for large areas. Without it, citizens can still report by pin or description, but cannot tap a building.
4. Click **Activate**, then **Yes, go live**. New crises start inactive and are invisible to citizens until activated.
5. Open http://localhost:8080 in any browser (it is built for phones), pick the crisis, and submit a report. It appears in the admin console.
6. Add more coordinators from the **Admins** tab. Public sign-up is disabled.

## Deploying to a server

**1. Point a domain at the server.** Create a DNS A record for your domain (for example `crisis.example.org`) with the server's IP address, and wait until it resolves (`nslookup crisis.example.org`). Do this before the first start: Caddy requests the TLS certificate when it starts. Ports 80 and 443 must be reachable from the internet.

**2. Generate real secrets instead of copying `.env.example`.** Do this before the first `docker compose up`: the database keeps the passwords it was created with.

```bash
python3 infra/gen_secrets.py
```

This writes `.env` with fresh secrets and prints the admin password; note it down. It refuses to run if `.env` already exists. If you already ran a local trial on this machine, erase that trial's data with `docker compose down -v` and delete `.env` first.

**3. Set the domain in `.env`.** Edit the existing `SITE_URL`, `HTTP_PORT` and `ADMIN_EMAIL` lines, and uncomment `SITE_ADDRESS` and `HTTPS_PORT` in the "Going live on a domain" block, so each key appears once:

```bash
SITE_URL=https://crisis.example.org
SITE_ADDRESS=crisis.example.org
HTTP_PORT=80
HTTPS_PORT=443
ADMIN_EMAIL=you@your-org.org
```

**4. Start the stack.**

```bash
docker compose up -d --build
```

**5. Sign in** at `https://<domain>/admin` with `ADMIN_EMAIL` and the password printed by `gen_secrets.py`.

To change the admin password later, edit `ADMIN_PASSWORD` in `.env` and run `docker compose up -d`. The API applies it at startup and signs out existing sessions.

Supabase Studio is reachable at `https://<domain>/supabase/` behind the `DASHBOARD_PASSWORD` login, so keep that password strong (`gen_secrets.py` generates one). For day-to-day use, an SSH tunnel is simpler: `ssh -L 54321:localhost:54321 user@server`, then open http://localhost:54321.

CORS is open by default. If you restrict it with `CORS_ORIGIN_REGEX`, keep `capacitor://localhost` and `https://localhost` in the pattern when the native apps are in use; the web app alone needs nothing.

## Optional features

The core flow (citizen reporting through the web app, the admin console, the map and building footprints) works with none of these. Fill in the settings in `.env`, then run `docker compose up -d`.

| Feature | What it does | Webhook |
|---|---|---|
| AI | Damage-level suggestions from photos, captions, voice transcription, translation, summaries and chat over reports | None |
| WhatsApp | Guided WhatsApp chat (Meta Cloud API) | `/api/whatsapp/webhook/meta` |
| SMS | Numbered text menus on any phone (sms-gate.app Android gateway) | `/api/sms/webhook` |
| Voice calls | Spoken report over a phone line (Twilio Voice) | `/api/ivr/voice` |

Settings, all in `.env`:

- **AI:** `AI_BASE_URL`, `AI_API_KEY`, and one `AI_*_MODEL` per feature. Each feature turns on once its model is set.
- **WhatsApp:** the four `META_WHATSAPP_*` values.
- **SMS:** `SMS_GATEWAY_USERNAME`, `SMS_GATEWAY_PASSWORD`, `SMS_WEBHOOK_SECRET`. Replies go out on SIM slot 2 by default; set `SMS_GATEWAY_SIM=1` on a single-SIM phone.
- **Voice calls:** the three `TWILIO_*` values, `IVR_PUBLIC_URL=https://<domain>/api` (for the Twilio signature check), and the AI transcription model (`AI_BASE_URL`, `AI_TRANSCRIPTION_MODEL`). Without transcription, calls are only logged.

Webhook paths are relative to `https://<domain>` and need the public HTTPS deployment above. The AI settings accept any OpenAI-compatible server (for example vLLM); example serving scripts are `infra/serve_small_classifier.sh` and those in `infra/ai_scripts/` (adjust paths for your machine).

**Reference data (optional).** Building-count estimates before a footprint download, and the asset-value and displaced-people estimates, use precomputed world grids built by `build_density_grid.py`, `build_litpop_grid.py` and `build_population_grid.py` in `apps/api/src/api/scripts/` (run from `apps/api/`, for example `uv run python -m api.scripts.build_density_grid`; each script's header has the details). Everything else works without them.

## Data and backups

All data (database, uploaded photos, TLS certificates) lives in Docker named volumes, so `docker compose down` and updates keep it. To back up the database:

```bash
docker compose exec -T db pg_dump -U postgres postgres > backup.sql
```

Uploaded photos are in the `storage-data` volume; back it up separately. To update, pull the new code and run `docker compose up -d --build`; new migrations apply automatically.

> **Warning:** `docker compose down -v` deletes the volumes, which permanently erases every report, photo and account. Back up first.

## Troubleshooting

- **Port already in use:** the stack uses 8080 and 8443 (web), plus 54321 (Supabase), 54322 (Postgres) and 6379 (Redis) on localhost only. Set `HTTP_PORT`, `HTTPS_PORT`, `SUPABASE_PORT`, `DB_PORT` or `REDIS_PORT` in `.env` to a free port, then run `docker compose up -d`.
- **A service fails to start:** check `docker compose logs migrate api` for the error.
- **Start over on a local trial:** `docker compose down -v`, then `docker compose up -d --build`. This erases all data.

## Development

Tech stack: FastAPI (Python 3.12), PostgreSQL with PostGIS via self-hosted Supabase, React with Vite (PWA), Redis with an arq worker, Caddy.

API (needs uv, plus the WeasyPrint system libraries Pango, Cairo and GDK-PixBuf; see `infra/Dockerfile.api` for Debian package names). Run from the repo root:

```bash
(cd apps/api && uv sync && uv run pytest -m "not integration and not redis")
```

Web app (needs Node 22 and pnpm). Run from the repo root:

```bash
(cd apps/pwa && pnpm install && pnpm test)
```

Tests marked `integration` and `redis` run against the local stack and are skipped when it is not running. They create and delete data, so never run them against a stack that holds real reports.

Schema changes are new SQL files in `supabase/migrations/`; the `migrate` service applies them on the next `docker compose up`.
