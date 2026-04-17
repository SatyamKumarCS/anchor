import logging
import threading
import time
import httpx

logger = logging.getLogger("orchestrator.metrics")

PROMETHEUS_URL = "http://prometheus:9090"


class MetricsGate:
    """
    Background thread that polls Prometheus for 5xx error rate.
    Calls on_fail or on_pass callback when decision is reached.
    """

    def __init__(
        self,
        target_port: int,
        threshold: float,
        window: int,
        poll_interval: int,
        on_fail: callable,
        on_pass: callable,
    ):
        self.target_port = target_port
        self.threshold = threshold
        self.window = window
        self.poll_interval = poll_interval
        self.on_fail = on_fail
        self.on_pass = on_pass
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        """Start the monitoring background thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info(f"Metrics gate started: threshold={self.threshold}, window={self.window}s, poll={self.poll_interval}s")

    def stop(self):
        """Stop the monitoring thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Metrics gate stopped")

    def _query_error_rate(self) -> float | None:
        """
        Query Prometheus for the 5xx error ratio.
        Returns error rate as float (0.0 to 1.0) or None on failure.
        Uses: rate(5xx) / rate(total) to get the actual ratio.
        """
        # Ratio query: errors / total requests
        query = (
            'sum(rate(flask_http_request_total{status=~"5.."}[2m]))'
            ' / '
            'sum(rate(flask_http_request_total[2m]))'
        )

        try:
            resp = httpx.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query},
                timeout=10,
            )
            data = resp.json()

            if data.get("status") != "success":
                logger.warning(f"Prometheus query failed: {data}")
                return None

            results = data.get("data", {}).get("result", [])
            if not results:
                # No data yet — treat as 0 errors
                logger.info("Prometheus: no data yet (no requests?)")
                return 0.0

            # Take the first result value
            value = float(results[0]["value"][1])
            # Handle NaN (division by zero when no requests)
            if value != value:  # NaN check
                return 0.0
            return value

        except Exception as e:
            logger.error(f"Prometheus query error: {e}")
            return None  # Fail-safe: will trigger rollback

    def _monitor_loop(self):
        """Main monitoring loop — runs in background thread."""
        start_time = time.time()
        logger.info(f"Monitoring started. Will watch for {self.window}s")

        while not self._stop_event.is_set():
            elapsed = time.time() - start_time

            # Stuck state timeout — window exceeded
            if elapsed >= self.window:
                logger.info(f"Monitoring window complete ({self.window}s). Metrics clean — promoting.")
                self.on_pass()
                return

            # Query Prometheus
            error_rate = self._query_error_rate()

            if error_rate is None:
                # Fail-safe: Prometheus unreachable → rollback
                logger.error("Prometheus unreachable — fail-safe: triggering rollback")
                self.on_fail("prometheus_unreachable")
                return

            logger.info(f"Error rate: {error_rate:.4f} (threshold: {self.threshold}) [{elapsed:.0f}s / {self.window}s]")

            if error_rate > self.threshold:
                logger.warning(
                    f"5xx rate: {error_rate:.3f} > threshold {self.threshold} → triggering rollback"
                )
                self.on_fail(f"5xx_rate_{error_rate:.4f}_exceeded_{self.threshold}")
                return

            # Wait for next poll
            self._stop_event.wait(self.poll_interval)

        logger.info("Monitoring stopped (external stop signal)")
