#!/usr/bin/env python3
"""
anchorctl — Zero-downtime deployment orchestrator CLI.

Declarative Blue/Green deploys with automated rollback.
Terraform-inspired command interface for the Anchor orchestrator service.
"""

import os
import sys
import json
from pathlib import Path

import click
import httpx
import yaml

__version__ = "0.1.0"

ORCHESTRATOR_URL = os.environ.get("ANCHOR_HOST", "http://localhost:8080")
ANCHOR_DIR_NAME = ".anchor"
CONFIG_FILE_NAME = "config.yml"
LEGACY_CONFIG_NAME = "deploy.yml"

BANNER = r"""
  ⚓  anchorctl v{}
  Zero-downtime deployment orchestrator
""".format(__version__)


# ── .anchor/ discovery (git-style) ─────────────────────────────────

def find_anchor_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` (or CWD) looking for a .anchor/ directory.
    Returns the project root (parent of .anchor/) or None if not found.
    """
    cur = (start or Path.cwd()).resolve()
    for parent in [cur, *cur.parents]:
        if (parent / ANCHOR_DIR_NAME).is_dir():
            return parent
    return None


def resolve_config_path(explicit: str | None) -> str:
    """Resolve which config file to use.

    Priority:
      1. --config flag (explicit) — used as-is
      2. .anchor/config.yml (walking up from CWD, like git)
      3. ./deploy.yml (legacy fallback, backward compat)
    """
    if explicit:
        return explicit
    root = find_anchor_root()
    if root:
        return str(root / ANCHOR_DIR_NAME / CONFIG_FILE_NAME)
    if Path(LEGACY_CONFIG_NAME).exists():
        return LEGACY_CONFIG_NAME
    # No project found — return the path we'd expect, let caller error nicely
    return str(Path.cwd() / ANCHOR_DIR_NAME / CONFIG_FILE_NAME)

# ── Default deploy.yml template ──────────────────────────────────────────────

DEFAULT_DEPLOY_YML = """\
app:
  name: {app_name}
  image: {image}

ports:
  blue: {blue_port}
  green: {green_port}

health_check:
  path: {health_path}
  timeout: 5
  retries: 3

rollback:
  error_rate_threshold: {threshold}
  window: 120
  poll_interval: 15

strategy: bluegreen
"""


# ── API helper ───────────────────────────────────────────────────────────────

def _api(method: str, path: str, **kwargs) -> dict:
    """Make an API call to the orchestrator."""
    url = f"{ORCHESTRATOR_URL}{path}"
    try:
        resp = httpx.request(method, url, timeout=30, **kwargs)
        data = resp.json()
        if resp.status_code >= 400:
            click.secho(f"  ✗ {data.get('detail', resp.text)}", fg="red")
            sys.exit(1)
        return data
    except httpx.ConnectError:
        click.secho(f"  ✗ Cannot connect to orchestrator at {ORCHESTRATOR_URL}", fg="red")
        click.echo("  Hint: Is the orchestrator running? (docker compose up)")
        click.echo(f"  Override with: ANCHOR_HOST=http://your-host:port anchorctl <command>")
        sys.exit(1)


# ── State icons ──────────────────────────────────────────────────────────────

STATE_STYLE = {
    "IDLE":            ("●", "white"),
    "DEPLOYING":       ("◉", "cyan"),
    "HEALTH_CHECKING": ("◉", "yellow"),
    "LIVE":            ("●", "green"),
    "ROLLING_BACK":    ("◉", "red"),
}


# ── CLI Group ────────────────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.version_option(__version__, "--version", "-v", prog_name="anchorctl")
@click.pass_context
def cli(ctx):
    """⚓  anchorctl — Zero-downtime deployment orchestrator.

    Declarative Blue/Green deploys with automated rollback.
    """
    if ctx.invoked_subcommand is None:
        click.echo(BANNER)
        click.echo(ctx.get_help())


# ── Local config loader (used by plan & apply for client-side validation) ───

def _load_config_local(path: str) -> dict:
    """Load and validate a config file. Exits on error with friendly message."""
    try:
        from orchestrator.config_parser import load_config
    except ImportError:
        # Fall back to plain YAML parsing when orchestrator package isn't
        # available (e.g. when installed via Homebrew without the server side).
        try:
            with open(path) as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            click.secho(f"  ✗ Config file not found: {path}", fg="red")
            click.echo("  Hint: Run 'anchorctl init' to create one.")
            sys.exit(1)
        except yaml.YAMLError as e:
            click.secho(f"  ✗ Config error: {e}", fg="red")
            sys.exit(1)

    try:
        return load_config(path)
    except FileNotFoundError:
        click.secho(f"  ✗ Config file not found: {path}", fg="red")
        click.echo("  Hint: Run 'anchorctl init' to create one.")
        sys.exit(1)
    except ValueError as e:
        click.secho(f"  ✗ Config error: {e}", fg="red")
        sys.exit(1)


# ── anchorctl init ──────────────────────────────────────────────────────────

@cli.command()
@click.option("--non-interactive", is_flag=True, help="Use defaults without prompting")
@click.option("--force", "-f", is_flag=True, help="Overwrite existing .anchor/ without confirmation")
def init(non_interactive, force):
    """Initialize a new anchorctl project in the current directory.

    Creates a .anchor/ directory (like .git/) with config.yml inside.
    """
    project_root = Path.cwd()
    anchor_dir = project_root / ANCHOR_DIR_NAME
    config_path = anchor_dir / CONFIG_FILE_NAME

    if anchor_dir.exists() and not force:
        if not click.confirm(
            f"  ⚠  {anchor_dir} already exists. Reinitialize?", default=False
        ):
            click.echo("  Aborted.")
            return

    click.echo(BANNER)
    click.secho("  Initializing new anchorctl project...\n", fg="cyan")

    if non_interactive:
        values = {
            "app_name": "myapp",
            "image": "myapp:latest",
            "blue_port": 8001,
            "green_port": 8002,
            "health_path": "/health",
            "threshold": 0.01,
        }
    else:
        values = {
            "app_name": click.prompt("  App name", default="myapp"),
            "image": click.prompt("  Docker image", default="myapp:latest"),
            "blue_port": click.prompt("  Blue port", default=8001, type=int),
            "green_port": click.prompt("  Green port", default=8002, type=int),
            "health_path": click.prompt("  Health check path", default="/health"),
            "threshold": click.prompt("  Rollback error threshold (0-1)", default=0.01, type=float),
        }

    anchor_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(DEFAULT_DEPLOY_YML.format(**values))
    (anchor_dir / "HEAD").write_text(
        "This directory is managed by anchorctl. Do not edit by hand.\n"
        "Edit config.yml to change deployment settings.\n"
    )
    gitignore = anchor_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("state.db\nstate.db-journal\nstate.db-wal\nstate.db-shm\n")

    click.echo()
    click.secho(
        f"  ✓ Initialized empty anchorctl project in {anchor_dir}/",
        fg="green",
        bold=True,
    )
    click.echo()
    click.echo("  Next steps:")
    click.echo("    1. Review .anchor/config.yml")
    click.echo("    2. docker compose up    — start infrastructure")
    click.echo("    3. anchorctl plan       — preview the deployment")
    click.echo("    4. anchorctl apply      — deploy for real")
    click.echo()


# ── anchorctl plan ──────────────────────────────────────────────────────────

@cli.command()
@click.option("--config", "-c", default=None, help="Path to config.yml (overrides .anchor/ discovery)")
def plan(config):
    """Show deployment plan without making changes."""
    click.echo(BANNER)

    config = resolve_config_path(config)

    cfg = _load_config_local(config)

    app = cfg["app"]
    ports = cfg["ports"]
    hc = cfg["health_check"]
    rb = cfg["rollback"]

    click.secho("  ─── Deployment Plan ───────────────────────────────", fg="cyan", bold=True)
    click.echo()
    click.echo(f"    App:            {app['name']}")
    click.echo(f"    Image:          {app['image']}")
    click.echo(f"    Strategy:       {cfg['strategy']}")
    click.echo(f"    Blue port:      {ports['blue']}")
    click.echo(f"    Green port:     {ports['green']}")
    click.echo(f"    Health check:   GET {hc['path']}  (timeout={hc['timeout']}s, retries={hc['retries']})")
    click.echo(f"    Rollback if:    error_rate > {rb['error_rate_threshold']*100:.1f}% over {rb['window']}s")
    click.echo(f"    Poll interval:  {rb['poll_interval']}s")
    click.echo()
    click.secho("  ─── Execution Steps ──────────────────────────────", fg="cyan", bold=True)
    click.echo()
    click.echo(f"    1 │ Start Green container on port {ports['green']}")
    click.echo(f"    2 │ Health check Green at :{ports['green']}{hc['path']}")
    click.echo(f"    3 │ Switch Nginx traffic  Blue → Green")
    click.echo(f"    4 │ Monitor error rate for {rb['window']}s")
    click.echo(f"    5 │ If error rate > {rb['error_rate_threshold']*100:.1f}%  → auto-rollback to Blue")
    click.echo(f"    6 │ If clean              → promote Green as production")
    click.echo()
    click.secho("  ─── No changes made ──────────────────────────────", fg="yellow")
    click.echo()
    click.echo("    Run 'anchorctl apply' to execute this plan.")
    click.echo()


# ── anchor apply ─────────────────────────────────────────────────────────────

@cli.command()
@click.option("--config", "-c", default=None, help="Path to config.yml (overrides .anchor/ discovery)")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def apply(config, yes):
    """Deploy a new version using Blue/Green strategy."""
    click.echo(BANNER)

    config = resolve_config_path(config)

    if not os.path.exists(config):
        click.secho(f"  ✗ Config file not found: {config}", fg="red")
        click.echo("  Hint: Run 'anchorctl init' to create one.")
        sys.exit(1)

    # Validate locally before sending to orchestrator
    cfg = _load_config_local(config)

    if not yes:
        if not click.confirm("  Apply deployment?", default=True):
            click.echo("  Aborted.")
            return

    click.secho("  Deploying...", fg="cyan")
    # Send parsed config inline so the orchestrator (which may run in a
    # different filesystem, e.g. inside Docker) doesn't need to read our path.
    data = _api("POST", "/deploy", json={"config_path": config, "config": cfg})

    click.echo()
    click.secho(f"  ✓ Deployment #{data['deployment_id']} started", fg="green", bold=True)
    click.echo(f"    Version:  {data['version']}")
    click.echo(f"    Status:   {data['status']}")
    click.echo()
    click.echo("    Monitor with: anchorctl status")
    click.echo()


# ── anchor destroy ───────────────────────────────────────────────────────────

@cli.command()
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def destroy(yes):
    """Rollback and stop the Green (new) version."""
    click.echo(BANNER)

    if not yes:
        click.secho("  ⚠  This will rollback to Blue and tear down the Green deployment.", fg="yellow")
        if not click.confirm("  Proceed?", default=False):
            click.echo("  Aborted.")
            return

    click.secho("  Destroying deployment...", fg="red")
    data = _api("POST", "/rollback")

    click.echo()
    click.secho(f"  ✓ Destroy complete", fg="green", bold=True)
    click.echo(f"    Active: {data['active']} (port {data['port']})")
    click.echo(f"    Green container stopped.")
    click.echo()


# ── anchor status ────────────────────────────────────────────────────────────

@cli.command()
def status():
    """Show current deployment state and recent events."""
    click.echo(BANNER)

    data = _api("GET", "/status")
    state = data["state"]
    icon, color = STATE_STYLE.get(state, ("●", "white"))

    click.secho("  ─── Current State ────────────────────────────────", bold=True)
    click.echo()
    click.secho(f"    {icon}  {state}", fg=color, bold=True)

    dep = data.get("deployment")
    if dep:
        click.echo(f"    Color:    {dep.get('active_color', 'unknown')}")
        click.echo(f"    Version:  {dep.get('version', 'unknown')}")
        click.echo(f"    Started:  {dep.get('started_at', 'N/A')}")
        if dep.get("finished_at"):
            click.echo(f"    Finished: {dep['finished_at']}")

    events = data.get("recent_events", [])
    if events:
        click.echo()
        click.secho("  ─── Recent Events ────────────────────────────────", bold=True)
        click.echo()
        for ev in events:
            ts = ev["timestamp"][:19]  # trim microseconds
            from_s = ev["from_state"]
            to_s = ev["to_state"]
            reason = ev.get("reason", "")
            _, to_color = STATE_STYLE.get(to_s, ("●", "white"))
            click.echo(f"    {ts}  ", nl=False)
            click.secho(f"{from_s} → {to_s}", fg=to_color, nl=False)
            if reason:
                click.echo(f"  {reason}")
            else:
                click.echo()
    click.echo()


# ── anchor rollback ──────────────────────────────────────────────────────────

@cli.command()
def rollback():
    """Force rollback to stable (Blue)."""
    click.echo(BANNER)
    click.secho("  Rolling back to Blue...", fg="yellow")
    data = _api("POST", "/rollback")

    click.echo()
    click.secho(f"  ✓ Rollback complete", fg="green", bold=True)
    click.echo(f"    Active: {data['active']} (port {data['port']})")
    click.echo()


# ── anchor switch ────────────────────────────────────────────────────────────

@cli.command()
@click.argument("target", type=click.Choice(["blue", "green"]))
def switch(target):
    """Manually flip traffic to blue or green."""
    click.echo(BANNER)
    click.secho(f"  Switching traffic to {target}...", fg="yellow")
    data = _api("POST", "/switch", json={"target": target})

    click.echo()
    click.secho(f"  ✓ Traffic switched", fg="green", bold=True)
    click.echo(f"    Target: {data['target']} (port {data['port']})")
    click.echo()


# ── anchor history ───────────────────────────────────────────────────────────

@cli.command()
def history():
    """Show full deployment history."""
    click.echo(BANNER)
    data = _api("GET", "/deployments")
    deployments = data.get("deployments", [])

    if not deployments:
        click.echo("  No deployments yet.")
        click.echo()
        return

    click.secho("  ─── Deployment History ───────────────────────────", bold=True)
    click.echo()
    click.secho(f"    {'ID':<5} {'Version':<15} {'State':<18} {'Color':<8} {'Started':<22} {'Finished':<22}", bold=True)
    click.echo("    " + "─" * 90)

    for d in deployments:
        state = d["state"]
        _, color = STATE_STYLE.get(state, ("●", "white"))
        started = (d.get("started_at") or "N/A")[:19]
        finished = (d.get("finished_at") or "—")[:19]
        click.echo(f"    {d['id']:<5} {d['version']:<15} ", nl=False)
        click.secho(f"{state:<18}", fg=color, nl=False)
        click.echo(f" {d['active_color']:<8} {started:<22} {finished:<22}")

    click.echo()


# ── anchorctl info ──────────────────────────────────────────────────────────

@cli.command()
def info():
    """Show project info: .anchor/ location, config path, orchestrator status."""
    click.echo(BANNER)

    root = find_anchor_root()
    click.secho("  ─── Project ──────────────────────────────────────", bold=True)
    click.echo()
    if root:
        click.echo(f"    Project root:  {root}")
        click.echo(f"    Anchor dir:    {root / ANCHOR_DIR_NAME}")
        config_path = root / ANCHOR_DIR_NAME / CONFIG_FILE_NAME
        click.echo(f"    Config:        {config_path}  "
                   f"{'✓' if config_path.exists() else '✗ missing'}")
    else:
        legacy = Path.cwd() / LEGACY_CONFIG_NAME
        if legacy.exists():
            click.echo(f"    Project root:  (legacy mode, no .anchor/)")
            click.echo(f"    Config:        {legacy}")
        else:
            click.secho("    No anchorctl project found in this directory or parents.", fg="yellow")
            click.echo("    Run 'anchorctl init' to create one.")

    click.echo()
    click.secho("  ─── Orchestrator ─────────────────────────────────", bold=True)
    click.echo()
    click.echo(f"    URL:           {ORCHESTRATOR_URL}")
    try:
        resp = httpx.get(f"{ORCHESTRATOR_URL}/health", timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            click.secho(f"    Status:        ● reachable", fg="green")
            click.echo(f"    State:         {data.get('state', 'unknown')}")
        else:
            click.secho(f"    Status:        ✗ HTTP {resp.status_code}", fg="red")
    except httpx.RequestError:
        click.secho(f"    Status:        ✗ unreachable", fg="red")
        click.echo(f"    Hint:          docker compose up")
    click.echo()


# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
