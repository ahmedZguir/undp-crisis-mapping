# UNDP
we need to clean this up.

## What we're building (in 30 seconds)

A community-driven crisis mapping platform for UNDP. When a disaster hits — earthquake, flood, conflict, fire — affected residents use it to report damage to specific buildings, and coordinators use it to see the whole picture and direct response.

**Three audiences, one platform:**
- **Community members submit** reports via a mobile web app (PWA) or WhatsApp — photo + damage class (minimal/partial/complete) + infrastructure type + crisis type + location. Works fully offline; reports queue locally and sync when connectivity returns.
- **Anyone can browse** a public map and feed of damage in their area, in any of the 6 UN languages. Per-crisis kill switch in case UNDP needs to lock it down for sensitive situations.
- **UNDP coordinators and field enumerators** access a gated dashboard with full data, filters, per-building history, and exports to CSV / GeoJSON / Shapefile.


## Repo layout

- `apps/api/` — FastAPI backend (Python 3.12, uv)
- `apps/pwa/` — Progressive web app (React + TS, Vite, pnpm)
- `supabase/` — Database schema migrations (source of truth)
- `infra/` — Docker Compose, Caddy config
- `docs/` — Architecture, decisions (ADRs), feature briefs
- `.github/workflows/` — CI

## Prerequisites

Install once on your machine:

- **Node 22** via [nvm](https://github.com/nvm-sh/nvm): `nvm install 22 && nvm alias default 22`
- **pnpm 10**: `npm install -g pnpm`
- **uv** (Python tooling): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **pre-commit**: `uv tool install pre-commit` (or `pip install pre-commit`)

## First-time setup

After cloning:

```bash
# Install pre-commit hooks into your local git
pre-commit install

# Set up the API
cd apps/api
uv sync
cd ../..

# Set up the PWA
cd apps/pwa
pnpm install
cd ../..
```

That's it. The two `*sync*`/`install` commands materialize each app's dependencies into a local environment.

## Day-to-day commands

**Run the API dev server** (from `apps/api/`):
```bash
uv run uvicorn api.main:app --reload --app-dir src
```
Serves on `http://localhost:8000`. Swagger UI at `/docs`.

**Run the PWA dev server** (from `apps/pwa/`):
```bash
pnpm dev
```
Serves on `http://localhost:5173`.

**Run the API checks** (from `apps/api/`):
```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

**Run the PWA checks** (from `apps/pwa/`):
```bash
pnpm lint
pnpm typecheck
pnpm build
```

## Exposing a local stack publicly (Caddy + ngrok)

To put the single-origin front (PWA + `/api` + `/supabase`) behind the ngrok pre-launch gate, run Caddy directly (the VM blocks :80, so use :8090) then point ngrok at it. Adjust `API_UPSTREAM` to wherever the API is listening.

**Caddy** (from `infra/`):
```bash
cd infra && SITE_ADDRESS=:8090 API_UPSTREAM=localhost:8012 SUPABASE_UPSTREAM=localhost:54321 PWA_DIST=../apps/pwa/dist caddy run --config Caddyfile
```

**ngrok** (basic-auth gate from `infra/ngrok-traffic-policy.yml`):
```bash
ngrok http --domain=bacon-rotunda-flock.ngrok-free.dev 8090 --traffic-policy-file infra/ngrok-traffic-policy.yml
```

Requires `pnpm build` first so `apps/pwa/dist` exists.

## Updating the Android APK with a new dist

### One-time toolchain setup (server has no Java)

The Gradle build needs **JDK 21** (Capacitor plugins require 21) and the Android command-line SDK. A fresh server has neither, so install both once. JDK 21 specifically — newer JDKs break the Capacitor Gradle plugin.

```bash
# JDK 21 (Temurin)
mkdir -p ~/jdk && cd ~/jdk
wget https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.7%2B6/OpenJDK21U-jdk_x64_linux_hotspot_21.0.7_6.tar.gz
tar -xzf OpenJDK21U-jdk_x64_linux_hotspot_21.0.7_6.tar.gz

# Android command-line tools
mkdir -p ~/Android/Sdk/cmdline-tools && cd ~/Android/Sdk/cmdline-tools
wget https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip
unzip commandlinetools-linux-13114758_latest.zip && mv cmdline-tools latest

# Environment (persist to ~/.bashrc so future shells have it)
echo 'export JAVA_HOME=$HOME/jdk/jdk-21.0.7+6' >> ~/.bashrc
echo 'export ANDROID_HOME=$HOME/Android/Sdk' >> ~/.bashrc
echo 'export PATH=$JAVA_HOME/bin:$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools' >> ~/.bashrc
source ~/.bashrc

# SDK components
yes | sdkmanager --licenses
sdkmanager "platform-tools" "platforms;android-36" "build-tools;36.0.0"
```

Verify with `java -version` (should report 21).

### Rebuild and copy the APK

To rebuild the sideload APK against the latest PWA build and refresh the package Caddy serves (from the repo root):

```bash
cd apps/pwa
pnpm build
npx cap sync android
cd android
JAVA_HOME=$HOME/jdk/jdk-21.0.7+6 ./gradlew assembleDebug
cd ../../..
cp apps/pwa/android/app/build/outputs/apk/debug/app-debug.apk apps/pwa/dist/rasid.apk
```

`pnpm build` produces the new `dist/`, `npx cap sync android` copies it into the Android project, `./gradlew assembleDebug` builds the APK (first build ~8 min while Gradle downloads dependencies, then ~30–60s incremental), and the `cp` drops it where Caddy serves it. The download URL is unchanged (`…/rasid.apk`); testers re-download and reinstall over the existing app (same debug keystore, so it updates in place). Full build + handoff details: [docs/native-app-handoff.md](docs/native-app-handoff.md).

## How commits work

Every commit triggers pre-commit hooks (formatters, linters, secret scanner). Three things to know:

1. **Auto-fixers may modify files during commit.** If a hook reformats your code, the commit is rejected and you re-stage:
```bash
   git add .
   git commit -m "..."   # now passes
```
2. **Don't bypass hooks casually.** `git commit --no-verify` exists for emergencies. If you find yourself reaching for it, fix the underlying issue instead.
3. **CI runs the same checks plus type checking and a full build.** Even if your commit passes locally, CI may fail — usually a missing file or lockfile drift. Fix forward.

## CI

Every push and pull request triggers `.github/workflows/ci.yml`:

- **API job**: ruff lint + format check, pyright, pytest
- **PWA job**: biome lint, tsc typecheck, build

Both run in parallel. Results show on GitHub's Actions tab and on the commit/PR page.

If CI fails, click the red X → into the failing job → read the logs of the failing step. Same output as if you ran the command locally.

## Adding dependencies

Don't edit `pyproject.toml` or `package.json` deps directly. Use the package managers so lockfiles stay in sync:

- **API**: `uv add <pkg>` (runtime) or `uv add --dev <pkg>` (dev only)
- **PWA**: `pnpm add <pkg>` (runtime) or `pnpm add -D <pkg>` (dev only)

Commit both the manifest and the lockfile (`uv.lock` / `pnpm-lock.yaml`).

## Troubleshooting

**Pre-commit hook fails with "executable not found"**: re-run `pre-commit install` from the repo root.

**`pnpm install --frozen-lockfile` fails in CI**: someone changed `package.json` without committing the new `pnpm-lock.yaml`. Run `pnpm install` locally, commit the lockfile, push.

**`uv sync` fails in CI**: same idea with `uv.lock`. Run `uv sync` locally, commit, push.

**VS Code shows red squiggles on `from api.main import app`**: select the right Python interpreter (Cmd/Ctrl+Shift+P → "Python: Select Interpreter" → `apps/api/.venv/bin/python`).

## Where to look next

- **Architecture overview**: [docs/tech_stack.md](docs/tech_stack.md)
- **Why we picked specific approaches**: [docs/decisions/](docs/decisions/)
- **Feature briefs (in-flight work)**: [docs/briefs/](docs/briefs/)
- **Agent operating guide**: [CLAUDE.md](CLAUDE.md)
