# Anchor — Workflow Guide

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

```bash
git clone <your-repo-url>
cd deploy-orchestrator

pip install -e .
```

That's it. You now have `anchor` available as a system command.

```bash
anchor --help
```

---

## Project Setup

### 1. Initialize a config file

```bash
anchor init
```

Anchor will ask a few questions and write a `deploy.yml`:

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
anchor init --non-interactive
```

### 2. Review the generated `deploy.yml`

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
anchor plan
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
    Run 'anchor apply' to execute this plan.
```

### Step 2 — Deploy

```bash
anchor apply
```

Anchor executes the plan:
1. Health-checks the Green container
2. Switches Nginx traffic Blue → Green
3. Watches Prometheus error rate for 2 minutes
4. If errors spike above threshold → **auto-rollback, no human needed**
5. If clean → Green is promoted as the permanent production version

### Step 3 — Watch it live

```bash
anchor status
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
| `anchor init` | Scaffold a `deploy.yml` interactively |
| `anchor plan` | Preview deployment — no changes made |
| `anchor apply` | Deploy new version via Blue/Green |
| `anchor status` | Current state + recent FSM events |
| `anchor rollback` | Force revert to stable (Blue) immediately |
| `anchor destroy` | Rollback with confirmation prompt |
| `anchor switch blue\|green` | Manually flip traffic (no health checks) |
| `anchor history` | Full deployment history table |
| `anchor --version` | Show version |

---

## The Auto-Rollback Demo

Open 3 terminals:

```bash
# Terminal 1 — watch live traffic the whole time
while true; do curl -s http://localhost/ ; sleep 0.3; done

# Terminal 2 — deploy (green has 20% 500 errors)
anchor apply

# Terminal 3 — watch state transitions
watch -n 2 "anchor status"
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
anchor rollback

# Manually flip traffic to green (skip all checks)
anchor switch green

# See full deployment history
anchor history
```

---

## CI/CD Integration

Because Anchor exposes a REST API, any pipeline can trigger a deployment:

```bash
# GitHub Actions / Jenkins / any CI
curl -X POST http://your-server:8080/deploy \
  -H "Content-Type: application/json" \
  -d '{"config_path": "deploy.yml"}'
```

Or install anchor on your CI runner and use the CLI directly:

```bash
ANCHOR_HOST=http://your-server:8080 anchor apply
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ANCHOR_HOST` | `http://localhost:8080` | Orchestrator URL |
| `BLUE_HOST` | `blue` | Blue container hostname |
| `GREEN_HOST` | `green` | Green container hostname |

---

## Crash Recovery

If the Orchestrator process dies mid-deployment, on restart it reads the last FSM state from SQLite and automatically resumes:

- If stuck in `HEALTH_CHECKING` → restarts the monitoring thread
- If stuck in `ROLLING_BACK` → completes the rollback
- If stuck in `DEPLOYING` → reverts to Blue

No deployment is ever left in limbo.
