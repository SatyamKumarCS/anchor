import yaml
import sys

REQUIRED_FIELDS = {
    "app": {"name", "image"},
    "ports": {"blue", "green"},
    "health_check": {"path", "timeout", "retries"},
    "rollback": {"error_rate_threshold", "window", "poll_interval"},
}


def load_config(path: str) -> dict:
    """Load and validate deploy.yml. Returns config dict or raises ValueError."""
    with open(path, "r") as f:
        config = yaml.safe_load(f)

    errors = []

    # Check top-level keys
    for section, fields in REQUIRED_FIELDS.items():
        if section not in config:
            errors.append(f"Missing required section: '{section}'")
            continue
        for field in fields:
            if field not in config[section]:
                errors.append(f"Missing required field: '{section}.{field}'")

    # Validate strategy
    if "strategy" not in config:
        errors.append("Missing required field: 'strategy'")
    elif config["strategy"] not in ("bluegreen", "canary"):
        errors.append(f"Invalid strategy: '{config['strategy']}'. Must be 'bluegreen' or 'canary'")

    # Validate types
    if "ports" in config:
        for port_name in ("blue", "green"):
            if port_name in config["ports"]:
                val = config["ports"][port_name]
                if not isinstance(val, int) or val < 1 or val > 65535:
                    errors.append(f"ports.{port_name} must be an integer between 1 and 65535")

    if "health_check" in config:
        hc = config["health_check"]
        if "timeout" in hc and (not isinstance(hc["timeout"], (int, float)) or hc["timeout"] <= 0):
            errors.append("health_check.timeout must be a positive number")
        if "retries" in hc and (not isinstance(hc["retries"], int) or hc["retries"] < 1):
            errors.append("health_check.retries must be a positive integer")

    if "rollback" in config:
        rb = config["rollback"]
        if "error_rate_threshold" in rb:
            if not isinstance(rb["error_rate_threshold"], (int, float)) or rb["error_rate_threshold"] <= 0:
                errors.append("rollback.error_rate_threshold must be a positive number")
        if "window" in rb:
            if not isinstance(rb["window"], (int, float)) or rb["window"] <= 0:
                errors.append("rollback.window must be a positive number")
        if "poll_interval" in rb:
            if not isinstance(rb["poll_interval"], (int, float)) or rb["poll_interval"] <= 0:
                errors.append("rollback.poll_interval must be a positive number")

    if errors:
        raise ValueError("Config validation failed:\n  " + "\n  ".join(errors))

    return config


def print_plan(config: dict):
    """Print what the deployment would do (dry-run output)."""
    app = config["app"]
    ports = config["ports"]
    hc = config["health_check"]
    rb = config["rollback"]

    print("=" * 60)
    print("  DEPLOYMENT PLAN (dry-run)")
    print("=" * 60)
    print(f"  App:          {app['name']}")
    print(f"  Image:        {app['image']}")
    print(f"  Strategy:     {config['strategy']}")
    print(f"  Blue port:    {ports['blue']}")
    print(f"  Green port:   {ports['green']}")
    print(f"  Health check: GET {hc['path']} (timeout={hc['timeout']}s, retries={hc['retries']})")
    print(f"  Rollback:     error_rate > {rb['error_rate_threshold']*100:.1f}% over {rb['window']}s window")
    print(f"  Poll interval: {rb['poll_interval']}s")
    print("=" * 60)
    print()
    print("  Steps that will execute:")
    print("  1. Start Green container (v2) on port", ports["green"])
    print("  2. Health check Green at GET :{}{} ".format(ports["green"], hc["path"]))
    print("  3. Switch Nginx traffic Blue → Green")
    print("  4. Monitor error rate for {}s".format(rb["window"]))
    print("  5. If error rate > {:.1f}% → auto rollback to Blue".format(rb["error_rate_threshold"] * 100))
    print("  6. If clean → promote Green, drain & kill Blue")
    print("=" * 60)
    print()
    print("  No changes made. Run without --dry-run to execute.")
    print()
