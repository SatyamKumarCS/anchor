import logging
import time
import httpx

logger = logging.getLogger("orchestrator.health")


def check_health(host: str, port: int, path: str, timeout: int = 5, retries: int = 3) -> bool:
    """
    Hit the health endpoint with retries.
    Returns True if any attempt returns 200, False otherwise.
    """
    url = f"http://{host}:{port}{path}"
    logger.info(f"Health check: {url} (timeout={timeout}s, retries={retries})")

    for attempt in range(1, retries + 1):
        try:
            resp = httpx.get(url, timeout=timeout)
            if resp.status_code == 200:
                logger.info(f"Health check passed on attempt {attempt}")
                return True
            else:
                logger.warning(f"Health check attempt {attempt}: status {resp.status_code}")
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.warning(f"Health check attempt {attempt}: {e}")

        if attempt < retries:
            wait = min(2 ** attempt, 10)
            logger.info(f"Retrying in {wait}s...")
            time.sleep(wait)

    logger.error(f"Health check FAILED after {retries} attempts")
    return False
