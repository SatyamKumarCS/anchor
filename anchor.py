#!/usr/bin/env python3
"""
anchor — Zero-downtime deployment orchestrator CLI.

Declarative Blue/Green deploys with automated rollback.
Terraform-inspired command interface for the Anchor orchestrator service.
"""

import os
import sys
import json

import click
import httpx
import yaml

__version__ = "0.1.0"

ORCHESTRATOR_URL = os.environ.get("ANCHOR_HOST", "http://localhost:8080")

BANNER = r"""
  ⚓  Anchor v{}
  Zero-downtime deployment orchestrator
""".format(__version__)

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
        click.echo(f"  Override with: ANCHOR_HOST=http://your-host:port anchor <command>")
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
@click.version_option(__version__, "--version", "-v", prog_name="anchor")
@click.pass_context
def cli(ctx):
    """⚓  Anchor — Zero-downtime deployment orchestrator.

    Declarative Blue/Green deploys with automated rollback.
    """
    if ctx.invoked_subcommand is None:
        click.echo(BANNER)
        click.echo(ctx.get_help())


# ── anchor init ──────────────────────────────────────────────────────────────

@cli.command()
@click.option("--output", "-o", default="deploy.yml", help="Output file path")
@click.option("--non-interactive", is_flag=True, help="Use defaults without prompting")
def init(output, non_interactive):
    """Scaffold a new deploy.yml in the current directory."""
    if os.path.exists(output):
        if not click.confirm(f"  ⚠  {output} already exists. Overwrite?", default=False):
            click.echo("  Aborted.")
            return

    click.echo(BANNER)
    click.secho("  Initializing new deployment config...\n", fg="cyan")

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

    content = DEFAULT_DEPLOY_YML.format(**values)

    with open(output, "w") as f:
        f.write(content)

    click.echo()
    click.secho(f"  ✓ Created {output}", fg="green", bold=True)
    click.echo()
    click.echo("  Next steps:")
    click.echo(f"    1. Review {output}")
    click.echo("    2. anchor plan        — preview the deployment")
    click.echo("    3. anchor apply       — deploy for real")
    click.echo()


# ── anchor plan ──────────────────────────────────────────────────────────────

@cli.command()
@click.option("--config", "-c", default="deploy.yml", help="Path to deploy.yml")
def plan(config):
    """Show deployment plan without making changes."""
    click.echo(BANNER)

    try:
        from orchestrator.config_parser import load_config
    except ImportError:
        click.secho("  ✗ Cannot import orchestrator package.", fg="red")
        click.echo("  Hint: Run from the anchor project root, or pip install -e .")
        sys.exit(1)

    try:
        cfg = load_config(config)
    except FileNotFoundError:
        click.secho(f"  ✗ Config file not found: {config}", fg="red")
        click.echo("  Hint: Run 'anchor init' to create one.")
        sys.exit(1)
    except ValueError as e:
        click.secho(f"  ✗ Config error: {e}", fg="red")
        sys.exit(1)

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
    click.echo("    Run 'anchor apply' to execute this plan.")
    click.echo()


# ── anchor apply ─────────────────────────────────────────────────────────────

@cli.command()
@click.option("--config", "-c", default="deploy.yml", help="Path to deploy.yml")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def apply(config, yes):
    """Deploy a new version using Blue/Green strategy."""
    click.echo(BANNER)

    if not os.path.exists(config):
        click.secho(f"  ✗ Config file not found: {config}", fg="red")
        click.echo("  Hint: Run 'anchor init' to create one.")
        sys.exit(1)

    if not yes:
        if not click.confirm("  Apply deployment?", default=True):
            click.echo("  Aborted.")
            return

    click.secho("  Deploying...", fg="cyan")
    data = _api("POST", "/deploy", json={"config_path": config})

    click.echo()
    click.secho(f"  ✓ Deployment #{data['deployment_id']} started", fg="green", bold=True)
    click.echo(f"    Version:  {data['version']}")
    click.echo(f"    Status:   {data['status']}")
    click.echo()
    click.echo("    Monitor with: anchor status")
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


# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
