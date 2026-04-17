import logging
import os
import subprocess
import time
from jinja2 import Template

logger = logging.getLogger("orchestrator.nginx")

NGINX_TEMPLATE_PATH = os.environ.get("NGINX_TEMPLATE", "/app/nginx/nginx.template.conf")
NGINX_CONF_PATH = os.environ.get("NGINX_CONF", "/etc/nginx/nginx.conf")
DRAIN_WAIT_SECONDS = 5


def switch_to(target_host: str, target_port: int):
    """
    Rewrite the Nginx config to route to the target upstream and reload.
    Waits for drain period after reload.
    """
    logger.info(f"Switching Nginx upstream to {target_host}:{target_port}")

    # Read template
    with open(NGINX_TEMPLATE_PATH, "r") as f:
        template = Template(f.read())

    # Render with target upstream
    rendered = template.render(
        upstream_host=target_host,
        upstream_port=target_port,
    )

    # Write config
    with open(NGINX_CONF_PATH, "w") as f:
        f.write(rendered)

    logger.info(f"Nginx config written to {NGINX_CONF_PATH}")

    # Reload Nginx (graceful — finishes in-flight requests)
    _reload_nginx()

    # Drain wait: let in-flight requests on old upstream complete
    logger.info(f"Draining connections ({DRAIN_WAIT_SECONDS}s)...")
    time.sleep(DRAIN_WAIT_SECONDS)
    logger.info("Drain complete")


def _reload_nginx():
    """Send reload signal to Nginx."""
    try:
        result = subprocess.run(
            ["nginx", "-s", "reload"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            logger.info("Nginx reloaded successfully")
            return
        logger.error(f"Nginx reload failed: {result.stderr}")
        raise RuntimeError(f"Nginx reload failed: {result.stderr}")
    except FileNotFoundError:
        # No local nginx binary — talk to the nginx container via the Docker SDK.
        # The Docker daemon socket is mounted into the orchestrator container.
        logger.info("Nginx binary not found locally, using Docker SDK...")
        try:
            import docker
            client = docker.from_env()
            container = client.containers.get(os.environ.get("NGINX_CONTAINER", "nginx"))
            exit_code, output = container.exec_run(["nginx", "-s", "reload"])
            if exit_code == 0:
                logger.info("Nginx reloaded via Docker SDK")
            else:
                msg = output.decode(errors="replace") if output else ""
                logger.error(f"Nginx reload via Docker SDK failed: {msg}")
                raise RuntimeError(f"Nginx reload via Docker SDK failed: {msg}")
        except Exception as e:
            logger.error(f"Nginx reload error: {e}")
            raise
