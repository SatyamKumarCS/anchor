import logging
import docker

logger = logging.getLogger("orchestrator.docker")

_client: docker.DockerClient | None = None


def _get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def start_container(image: str, name: str, port: int) -> str:
    """Start a container with the given image on the specified port. Returns container ID."""
    client = _get_client()

    # Remove existing container with same name if it exists
    try:
        existing = client.containers.get(name)
        logger.info(f"Removing existing container: {name}")
        existing.stop(timeout=5)
        existing.remove(force=True)
    except docker.errors.NotFound:
        pass

    logger.info(f"Starting container '{name}' from image '{image}' on port {port}")
    container = client.containers.run(
        image,
        name=name,
        detach=True,
        ports={"8001/tcp": port} if "blue" in name else {"8002/tcp": port},
        network="deploy-orchestrator_default",
        remove=False,
    )
    logger.info(f"Container '{name}' started: {container.short_id}")
    return container.id


def stop_container(name: str):
    """Stop and remove a container by name."""
    client = _get_client()
    try:
        container = client.containers.get(name)
        logger.info(f"Stopping container: {name}")
        container.stop(timeout=10)
        container.remove(force=True)
        logger.info(f"Container '{name}' stopped and removed")
    except docker.errors.NotFound:
        logger.warning(f"Container '{name}' not found — already removed?")


def is_running(name: str) -> bool:
    """Check if a container is running."""
    client = _get_client()
    try:
        container = client.containers.get(name)
        return container.status == "running"
    except docker.errors.NotFound:
        return False


def get_container_ip(name: str) -> str | None:
    """Get the IP address of a container on the default network."""
    client = _get_client()
    try:
        container = client.containers.get(name)
        networks = container.attrs["NetworkSettings"]["Networks"]
        for net_name, net_info in networks.items():
            if net_info.get("IPAddress"):
                return net_info["IPAddress"]
        return None
    except docker.errors.NotFound:
        return None
