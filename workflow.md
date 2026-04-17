# anchorctl — Workflow Guide

> ⚓ Zero-downtime deployment orchestrator.
> Declarative Blue/Green deploys with automated rollback.
> Think: Terraform, but for deployments.

---

## Prerequisites

- Python 3.11+
- Docker Desktop (running)
- Docker Compose v2 (`docker compose` not `docker-compose`)

---

## Installation

### Option 1 — Homebrew (recommended)

```bash
brew tap aryankinha/tap && brew install anchorctl
```

### Option 2 — From source

```bash
git clone https://github.com/aryankinha/anchor
cd anchor

python3 -m venv ~/.anchorctl-venv
source ~/.anchorctl-venv/bin/activate
pip install -e .
```

To make `anchorctl` available in every new shell, add this to your `~/.zshrc`:

```bash
source ~/.anchorctl-venv/bin/activate
```

That's it. You now have `anchorctl` available as a system command.

```bash
anchorctl --help
```

---

## Project Setup

### 1. Initialize a new project

From inside any project directory:

```bash
anchorctl init
```

Like `git init`, this creates a `.anchor/` directory in the current folder:

```
myproject/
├── .anchor/
│   ├── config.yml      ← deployment config
│   ├── HEAD            ← marker file
│   ├── .gitignore      ← ignores state.db
│   └── state.db        ← created by orchestrator at runtime
└── ...
```

anchorctl will ask a few questions and write `.anchor/config.yml`:

```
App name: myapp
Docker image: myapp:v2
Blue (stable) port: 8001
Green (new) port: 8002
Health check path: /health
Rollback threshold (0-1): 0.01
```

Or skip prompts with defaults:

```bash
anchorctl init --non-interactive
```

> **How discovery works:** All anchorctl commands walk up the directory tree
> looking for `.anchor/` (just like git finds `.git/`). Run `anchorctl plan`
> from any subdirectory of your project and it will find the right config.

### 2. Review the generated `.anchor/config.yml`

```yaml
app:
  name: myapp
  image: myapp:v2

ports:
  blue: 8001
  green: 8002

health_check:
  path: /health
  timeout: 5
  retries: 3

rollback:
  error_rate_threshold: 0.01   # 1% 5xx errors triggers auto-rollback
  window: 120                  # watch for 2 minutes after traffic flip
  poll_interval: 15            # check every 15 seconds

strategy: bluegreen
```

---

## Starting the Infrastructure

```bash
docker compose up --build
```

This starts 6 services:

| Service | URL | Purpose |
|---|---|---|
| Blue app | `localhost:8001` | Stable production version |
| Green app | `localhost:8002` | New version being deployed |
| Nginx | `localhost:80` | Traffic router (points to Blue by default) |
| Orchestrator | `localhost:8080` | The Anchor API |
| Prometheus | `localhost:9090` | Metrics scraper |
| Grafana | `localhost:3000` | Live dashboard (login: admin/admin) |

Wait until all containers are running (~60 seconds first time).

---

## Deployment Workflow

### Step 1 — Preview the deployment

```bash
anchorctl plan
```

Shows exactly what will happen. No changes made. Like `terraform plan`.

```
  ─── Deployment Plan ───────────────────────────────
    App:           myapp
    Image:         myapp:v2
    Strategy:      bluegreen
    Blue port:     8001
    Green port:    8002
    Health check:  GET /health  (timeout=5s, retries=3)
    Rollback if:   error_rate > 1.0% over 120s

  ─── Execution Steps ──────────────────────────────
    1 │ Start Green container on port 8002
    2 │ Health check Green at :8002/health
    3 │ Switch Nginx traffic  Blue → Green
    4 │ Monitor error rate for 120s
    5 │ If error rate > 1.0%  → auto-rollback to Blue
    6 │ If clean              → promote Green as production

  ─── No changes made ──────────────────────────────
    Run 'anchorctl apply' to execute this plan.
```

### Step 2 — Deploy

```bash
anchorctl apply
```

Anchor executes the plan:
1. Health-checks the Green container
2. Switches Nginx traffic Blue → Green
3. Watches Prometheus error rate for 2 minutes
4. If errors spike above threshold → **auto-rollback, no human needed**
5. If clean → Green is promoted as the permanent production version

### Step 3 — Watch it live

```bash
anchorctl status
```

```
  ─── Current State ────────────────────────────────
    ◉  HEALTH_CHECKING
    Color:    green
    Version:  myapp:v2
    Started:  2026-04-18 14:23:01

  ─── Recent Events ────────────────────────────────
    2026-04-18 14:23:01  IDLE → DEPLOYING
    2026-04-18 14:23:04  DEPLOYING → HEALTH_CHECKING
```

---

## All Commands

| Command | What it does |
|---|---|
| `anchorctl init` | Initialize a `.anchor/` project (like `git init`) |
| `anchorctl info` | Show project root, config path, orchestrator status |
| `anchorctl plan` | Preview deployment — no changes made |
| `anchorctl apply` | Deploy new version via Blue/Green |
| `anchorctl status` | Current state + recent FSM events |
| `anchorctl rollback` | Force revert to stable (Blue) immediately |
| `anchorctl destroy` | Rollback with confirmation prompt |
| `anchorctl switch blue\|green` | Manually flip traffic (no health checks) |
| `anchorctl history` | Full deployment history table |
| `anchorctl --version` | Show version |

---

## The Auto-Rollback Demo

Open 3 terminals:

```bash
# Terminal 1 — watch live traffic the whole time
while true; do curl -s http://localhost/ ; sleep 0.3; done

# Terminal 2 — deploy (green has 20% 500 errors)
anchorctl apply

# Terminal 3 — watch state transitions
watch -n 2 "anchorctl status"
```

What you'll see in Terminal 2:

```
IDLE → DEPLOYING → HEALTH_CHECKING
5xx rate: 0.043 > threshold 0.01 → rollback triggered
HEALTH_CHECKING → ROLLING_BACK → IDLE
```

Terminal 1 never shows an error. **Zero downtime.**

---

## Manual Operations

```bash
# Force rollback right now
anchorctl rollback

# Manually flip traffic to green (skip all checks)
anchorctl switch green

# See full deployment history
anchorctl history
```

---

## CI/CD Integration

Because Anchor exposes a REST API, any pipeline can trigger a deployment:

```bash
# GitHub Actions / Jenkins / any CI
curl -X POST http://your-server:8080/deploy \
  -H "Content-Type: application/json" \
  -d '{"config_path": ".anchor/config.yml"}'
```

Or install anchorctl on your CI runner and use the CLI directly:

```bash
ANCHOR_HOST=http://your-server:8080 anchorctl apply
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ANCHOR_HOST` | `http://localhost:8080` | Orchestrator URL |
| `BLUE_HOST` | `blue` | Blue container hostname |
| `GREEN_HOST` | `green` | Green container hostname |

---

## Project Layout (`.anchor/`)

anchorctl stores per-project state in a `.anchor/` directory at the root of
your project — exactly like `git` uses `.git/`:

```
myproject/
├── .anchor/
│   ├── config.yml      ← your deployment config (committed to git)
│   ├── HEAD            ← marker file (committed)
│   ├── .gitignore      ← auto-ignores state.db
│   └── state.db        ← FSM state, history, events (NOT committed)
├── docker-compose.yml
└── apps/
```

Every command walks up the directory tree to find `.anchor/`, so
`anchorctl plan` works from any subdirectory.

**Backward compatibility:** if no `.anchor/` exists but a plain `deploy.yml`
is present in the current directory, anchorctl still uses it. New projects
should prefer the `.anchor/` layout.

**Override:** `anchorctl plan --config /path/to/file.yml` always wins over
auto-discovery.

---

## Crash Recovery

If the Orchestrator process dies mid-deployment, on restart it reads the last FSM state from SQLite and automatically resumes:

- If stuck in `HEALTH_CHECKING` → restarts the monitoring thread
- If stuck in `ROLLING_BACK` → completes the rollback
- If stuck in `DEPLOYING` → reverts to Blue

No deployment is ever left in limbo.
