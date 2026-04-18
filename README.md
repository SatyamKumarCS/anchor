<div align="center">
  <h1>Anchor</h1>
  <p><b>Zero-Downtime Deployment Orchestrator</b></p>
</div>

Anchor is an open-source, declarative Blue/Green deployment orchestrator with automated rollback capabilities. Think of it as Terraform, but specifically designed for managing application deployments. With its intuitive CLI interface (`anchorctl`), you can execute safe, zero-downtime releases backed by real-time metric monitoring.

## Features

- **Zero-Downtime Deployments**: Automates health checking and smooth Nginx traffic switching for Blue/Green environments.
- **Automated Rollbacks**: Integrates directly with Prometheus to monitor your application's 5xx error rate. If errors exceed your configured threshold, Anchor automatically reverts traffic to the stable version—no human intervention needed.
- **Declarative Configuration**: Define your deployment rules, health checks, ports, and rollback thresholds in a simple, version-controllable `.anchor/config.yml` file.
- **Intuitive CLI (`anchorctl`)**: A developer-friendly toolset to securely plan, apply, track status, and monitor your deployments locally or in CI environments.
- **CI/CD Ready**: Easily trigger deployments programmatically via the Orchestrator's REST API.
- **Crash Recovery**: Orchestrator never leaves a deployment in a broken state; it automatically resumes and recovers based on the saved state machine log.

---

## Prerequisites

Before using Anchor, ensure you have the following installed:
- [Python 3.11+](https://www.python.org/downloads/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (must be running)
- Docker Compose v2 (run as `docker compose`, not `docker-compose`)

---

## Installation

### Option 1: Homebrew (Recommended on macOS)
```bash
brew tap aryankinha/tap && brew install anchorctl
```

### Option 2: Build from Source
```bash
git clone https://github.com/aryankinha/anchor
cd anchor

# Set up a virtual environment and install the CLI
python3 -m venv ~/.anchorctl-venv
source ~/.anchorctl-venv/bin/activate
pip install -e .
```

To make `anchorctl` globally available in all new terminal sessions:
```bash
echo 'source ~/.anchorctl-venv/bin/activate' >> ~/.zshrc
```

Verify your installation:
```bash
anchorctl --version
```

---

## Getting Started

### 1. Initialize a Project

Run `anchorctl init` from inside any of your project directories (much like `git init`):
```bash
anchorctl init
```
Anchor will create an `.anchor/` directory at the root of your project containing a `config.yml` file. 

Example generated configuration (`.anchor/config.yml`):
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
  error_rate_threshold: 0.01   # Auto-rollback if 5xx errors > 1%
  window: 120                  # Monitor for 2 minutes post-switch
  poll_interval: 15            # Poll Prometheus metrics every 15s

strategy: bluegreen
```

### 2. Start the Infrastructure

Boot up the required services (Blue/Green apps, Nginx, Prometheus, Grafana, and Orchestrator):
```bash
docker compose up --build -d
```

### 3. Deploy Your Application!

**Preview the deployment changes** (dry-run):
```bash
anchorctl plan
```

**Apply the deployment**:
```bash
anchorctl apply
```
Anchor will:
1. Health-check the newly spun-up Green container.
2. Route Nginx traffic from Blue to Green.
3. Monitor the Prometheus error rate for your specified window (default 2 mins).
4. Auto-rollback if metrics degrade, otherwise safely promote Green to production!

**Monitor live status**:
```bash
anchorctl status
```
*Note: Run `anchorctl switch blue` or `anchorctl rollback` at any point to manually fallback.*

---

## CLI Command Reference

Here are all availability options through `anchorctl`:

| Command | Description |
| :--- | :--- |
| `anchorctl init` | Initialize an `.anchor/` project repository. |
| `anchorctl info` | Show the project root, config path, and orchestrator connectivity status. |
| `anchorctl plan` | Preview the deployment execution plan (dry-run). |
| `anchorctl apply` | Execute a new deployment via Blue/Green strategy. |
| `anchorctl status` | Display the current FSM state and recent deployment events. |
| `anchorctl history`| View a summarized table of your deployment history. |
| `anchorctl rollback`| Force an immediate rollback to the stable (Blue) container. |
| `anchorctl switch <blue\|green>` | Manually redirect infrastructure traffic (skips health/status checks). |
| `anchorctl destroy`| Rollback to Blue and completely tear down the Green container. |

Pass `--help` to any command to see flags and usage configuration.

---

## Auto-Rollback Demo Workflow

You can simulate how Anchor reacts to faulty releases directly on your local machine:

1. **Terminal 1** (Simulate Traffic): Send continuous requests to your app to generate metrics.
   ```bash
   while true; do curl -s http://localhost/ ; sleep 0.3; done
   ```
2. **Terminal 2** (Execute Deploy): Apply a change where the deployed Green app triggers 500 errors.
   ```bash
   anchorctl apply
   ```
3. **Terminal 3** (Watch Safety Mechanisms):
   ```bash
   watch -n 2 "anchorctl status"
   ```

**Outcome**: Anchor detects that the error threshold limits have been breached. State automatically shifts: `HEALTH_CHECKING` → `ROLLING_BACK` → `IDLE`. End-users (Terminal 1) experience absolutely **Zero downtime**.

---

## CI/CD Integration

Anchor is built with automation in mind. Since it runs via a REST API, you can easily plug it into GitHub Actions, GitLab CI, or Jenkins.

Trigger a deployment remotely via cURL:
```bash
curl -X POST http://your-server:8080/deploy \
  -H "Content-Type: application/json" \
  -d '{"config_path": ".anchor/config.yml"}'
```

Or apply settings directly using the CLI in your pipeline runners:
```bash
ANCHOR_HOST=http://your-server:8080 anchorctl apply --yes
```

---

## Canary Testing (Prototype Localhost Setup)

While Anchor currently focuses primarily on Blue/Green deployments, you can use the prototype configuration to test Canary traffic splitting on your localhost. Under this strategy, Anchor allows a controlled amount of traffic instead of a 100% immediate flip.

To test this locally:

1. Update your `.anchor/config.yml` to specify the `canary` strategy:
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

strategy: canary
```

2. Run `anchorctl plan` to preview the canary routing mechanism.
3. Once applied (`anchorctl apply`), you can observe the logs and watch Nginx metrics on Grafana (`localhost:3000`) segmenting traffic between Blue and Green during the probationary window before automatic full-promotion.

---

## Environment Variables

Override the default behavior and connections using standard environment variables:

| Variable | Default | Description |
|---|---|---|
| `ANCHOR_HOST` | `http://localhost:8080` | Network URL for the backend Orchestrator service. |
| `BLUE_HOST` | `blue` | Docker Compose hostname for the Blue container. |
| `GREEN_HOST` | `green` | Docker Compose hostname for the Green container. |

---

## Project Layout

When initialized, Anchor operates out of a highly-contained `.anchor/` directory so it won't clutter your project root. 
```text
myproject/
├── .anchor/
│   ├── config.yml      ← Standard configuration (Commit this to Git)
│   ├── .gitignore      ← Auto-tells Git to ignore transient state
│   └── state.db        ← Backend SQLite database syncing state (Ignored)
├── docker-compose.yml
└── src/
```

## Contributing

This project is Open Source and thrives on community contributions. Feel free to open bug reports, feature requests, or Pull Requests on our [issues page](#). Be sure to check any formatting or testing protocols beforehand! 

## License

This software is licensed under the [MIT License](LICENSE).
