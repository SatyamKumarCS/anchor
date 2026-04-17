#!/usr/bin/env python3
"""
deploy.py — CLI client for the Blue/Green Deployment Orchestrator.

Thin HTTP wrapper that talks to the Orchestrator service at localhost:8080.
"""

import sys
import json
import click
import httpx

ORCHESTRATOR_URL = "http://localhost:8080"


def _api(method: str, path: str, **kwargs) -> dict:
    """Make an API call to the orchestrator."""
    url = f"{ORCHESTRATOR_URL}{path}"
    try:
        resp = httpx.request(method, url, timeout=30, **kwargs)
        data = resp.json()
        if resp.status_code >= 400:
            click.secho(f"ERROR: {data.get('detail', resp.text)}", fg="red")
            sys.exit(1)
        return data
    except httpx.ConnectError:
        click.secho("ERROR: Cannot connect to orchestrator at " + ORCHESTRATOR_URL, fg="red")
        click.echo("Is the orchestrator service running? (docker compose up)")
        sys.exit(1)


@click.group()
def cli():
    """Blue/Green Deployment Orchestrator CLI"""
    pass


@cli.command()
@click.option("--config", default="deploy.yml", help="Path to deploy.yml config file")
@click.option("--dry-run", is_flag=True, help="Show deployment plan without executing")
def deploy(config, dry_run):
    """Deploy a new version using Blue/Green strategy."""
    if dry_run:
        # Dry-run: parse config locally and print plan
        from orchestrator.config_parser import load_config, print_plan

        try:
            cfg = load_config(config)
            print_plan(cfg)
        except ValueError as e:
            click.secho(f"Config error: {e}", fg="red")
            sys.exit(1)
        except FileNotFoundError:
            click.secho(f"Config file not found: {config}", fg="red")
            sys.exit(1)
        return

    click.secho("Starting deployment...", fg="cyan")
    data = _api("POST", "/deploy", json={"config_path": config})

    click.secho(f"Deployment #{data['deployment_id']} started", fg="green")
    click.echo(f"  Version: {data['version']}")
    click.echo(f"  Status:  {data['status']}")
    click.echo()
    click.echo("Monitor with: deploy.py status")


@cli.command()
def status():
    """Show current orchestrator state and recent events."""
    data = _api("GET", "/status")

    state = data["state"]
    color_map = {
        "IDLE": "white",
        "DEPLOYING": "cyan",
        "HEALTH_CHECKING": "yellow",
        "LIVE": "green",
        "ROLLING_BACK": "red",
    }

    click.echo()
    click.secho(f"  State: {state}", fg=color_map.get(state, "white"), bold=True)

    dep = data.get("deployment")
    if dep:
        click.echo(f"  Active Color: {dep.get('active_color', 'unknown')}")
        click.echo(f"  Version: {dep.get('version', 'unknown')}")
        click.echo(f"  Started: {dep.get('started_at', 'N/A')}")
        if dep.get("finished_at"):
            click.echo(f"  Finished: {dep['finished_at']}")

    events = data.get("recent_events", [])
    if events:
        click.echo()
        click.secho("  Recent Events:", bold=True)
        for ev in events:
            click.echo(f"    [{ev['timestamp']}] {ev['from_state']} → {ev['to_state']}: {ev.get('reason', '')}")
    click.echo()


@cli.command()
def rollback():
    """Force rollback to Blue."""
    click.secho("Triggering manual rollback...", fg="yellow")
    data = _api("POST", "/rollback")
    click.secho(f"Rollback complete. Active: {data['active']}", fg="green")


@cli.command()
@click.argument("target", type=click.Choice(["blue", "green"]))
def switch(target):
    """Manually switch traffic to blue or green."""
    click.secho(f"Switching traffic to {target}...", fg="yellow")
    data = _api("POST", "/switch", json={"target": target})
    click.secho(f"Switched to {data['target']} (port {data['port']})", fg="green")


@cli.command()
def history():
    """Show deployment history."""
    data = _api("GET", "/deployments")
    deployments = data.get("deployments", [])

    if not deployments:
        click.echo("No deployments yet.")
        return

    click.echo()
    click.secho(f"  {'ID':<5} {'Version':<15} {'State':<18} {'Color':<8} {'Started':<25} {'Finished':<25}", bold=True)
    click.echo("  " + "-" * 96)
    for d in deployments:
        click.echo(
            f"  {d['id']:<5} {d['version']:<15} {d['state']:<18} {d['active_color']:<8} "
            f"{d.get('started_at', 'N/A'):<25} {d.get('finished_at', 'N/A') or '':<25}"
        )
    click.echo()


if __name__ == "__main__":
    cli()
