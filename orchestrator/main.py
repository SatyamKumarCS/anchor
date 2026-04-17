import logging
import os
import signal
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from orchestrator import db
from orchestrator.config_parser import load_config, print_plan
from orchestrator.fsm import DeploymentFSM
from orchestrator.health_prober import check_health
from orchestrator.metrics_gate import MetricsGate
from orchestrator.nginx_controller import switch_to

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("orchestrator")

# --- Global State ---
fsm: DeploymentFSM = DeploymentFSM()
metrics_gate: MetricsGate | None = None
deploy_lock = threading.Lock()
current_config: dict | None = None

# Container names used by docker-compose
BLUE_HOST = os.environ.get("BLUE_HOST", "blue")
GREEN_HOST = os.environ.get("GREEN_HOST", "green")


# --- Request Models ---
class DeployRequest(BaseModel):
    config_path: str = "deploy.yml"


class SwitchRequest(BaseModel):
    target: str  # "blue" or "green"


# --- Lifespan: startup + shutdown ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP
    logger.info("Orchestrator starting up...")
    db.init_db()
    _recover_from_crash()
    yield
    # SHUTDOWN
    logger.info("Orchestrator shutting down...")
    _graceful_shutdown()


app = FastAPI(title="Blue/Green Deployment Orchestrator", lifespan=lifespan)


# --- Crash Recovery ---
def _recover_from_crash():
    """On startup, check SQLite for incomplete deployments and recover."""
    global fsm
    last = db.get_last_deployment()
    if last is None:
        logger.info("No previous deployments found. Starting fresh.")
        return

    state = last["state"]
    deployment_id = last["id"]

    if state in ("LIVE", "IDLE"):
        logger.info(f"Last deployment #{deployment_id} ended in {state}. No recovery needed.")
        return

    logger.warning(f"Last deployment #{deployment_id} stuck in {state}. Recovering...")

    if state in ("DEPLOYING", "HEALTH_CHECKING", "ROLLING_BACK"):
        # Safe default: rollback to Blue
        logger.warning("Recovering by reverting to Blue")
        try:
            config = db.get_last_deployment()
            blue_port = 8001  # fallback
            if config and config.get("config"):
                import json
                parsed = json.loads(config["config"])
                blue_port = parsed.get("ports", {}).get("blue", 8001)
            switch_to(BLUE_HOST, blue_port)
        except Exception as e:
            logger.error(f"Recovery Nginx switch failed: {e}")

        db.log_event(deployment_id, state, "IDLE", "crash recovery: reverted to Blue")
        db.finish_deployment(deployment_id, "IDLE", "blue")
        logger.info("Recovery complete. Blue is live.")

    fsm = DeploymentFSM()


def _graceful_shutdown():
    """Handle SIGTERM — persist current state."""
    global metrics_gate
    if metrics_gate:
        metrics_gate.stop()
    logger.info("State persisted. Shutdown clean.")


# --- Deploy Flow (runs in background thread) ---
def _run_deploy(config: dict, deployment_id: int):
    """Execute the full deployment lifecycle."""
    global fsm, metrics_gate

    ports = config["ports"]
    hc = config["health_check"]
    rb = config["rollback"]

    try:
        # 1. IDLE → DEPLOYING
        fsm.start_deploy()
        logger.info(f"Deploying version: {config['app']['image']}")

        # 2. Health check Green
        logger.info(f"Health checking Green at {GREEN_HOST}:{ports['green']}{hc['path']}")
        healthy = check_health(
            host=GREEN_HOST,
            port=ports["green"],
            path=hc["path"],
            timeout=hc["timeout"],
            retries=hc["retries"],
        )

        if not healthy:
            fsm.set_reason("health check failed on Green container")
            fsm.health_fail()
            logger.error("Health check FAILED. Staying on Blue.")
            return

        # 3. DEPLOYING → HEALTH_CHECKING (switch traffic to Green)
        logger.info("Health check passed. Switching traffic to Green...")
        switch_to(GREEN_HOST, ports["green"])
        fsm.set_reason("health check passed, traffic switched to Green")
        fsm.health_ok()

        # 4. Start metrics monitoring
        def on_metrics_fail(reason: str):
            global fsm
            logger.warning(f"Metrics gate triggered rollback: {reason}")
            try:
                fsm.set_reason(reason)
                fsm.metrics_fail()
                # Rollback: switch back to Blue
                switch_to(BLUE_HOST, ports["blue"])
                fsm.rollback_complete()
                logger.info("Rollback complete. Blue is live.")
            except Exception as e:
                logger.error(f"Rollback error: {e}")

        def on_metrics_pass():
            global fsm
            logger.info("Metrics clean for full window. Deployment promoted!")
            try:
                fsm.metrics_ok()
                logger.info("Deployment LIVE. Green is the new production.")
            except Exception as e:
                logger.error(f"Promotion error: {e}")

        metrics_gate = MetricsGate(
            target_port=ports["green"],
            threshold=rb["error_rate_threshold"],
            window=rb["window"],
            poll_interval=rb["poll_interval"],
            on_fail=on_metrics_fail,
            on_pass=on_metrics_pass,
        )
        metrics_gate.start()

    except Exception as e:
        logger.error(f"Deploy flow error: {e}")
        # Try to rollback
        try:
            switch_to(BLUE_HOST, ports["blue"])
        except Exception:
            pass
        db.log_event(deployment_id, fsm.current_state.name, "IDLE", f"error: {e}")
        db.finish_deployment(deployment_id, "IDLE", "blue")
        fsm = DeploymentFSM()  # Reset FSM


# === ENDPOINTS ===


@app.post("/deploy")
def deploy(req: DeployRequest):
    """Trigger a full Blue/Green deployment."""
    global fsm, current_config

    # Deployment lock: reject if not IDLE
    if fsm.current_state.name != "IDLE":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot deploy: orchestrator is in {fsm.current_state.name} state. Wait or rollback first.",
        )

    # Parse and validate config
    try:
        config = load_config(req.config_path)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    current_config = config

    # Create deployment record
    deployment_id = db.create_deployment(config["app"]["image"], config)

    # Create new FSM with deployment ID
    fsm = DeploymentFSM(deployment_id=deployment_id)

    # Run deploy flow in background thread
    thread = threading.Thread(target=_run_deploy, args=(config, deployment_id), daemon=True)
    thread.start()

    return {
        "status": "deploying",
        "deployment_id": deployment_id,
        "version": config["app"]["image"],
        "message": "Deployment started. Monitor with GET /status",
    }


@app.post("/switch")
def manual_switch(req: SwitchRequest):
    """Manual traffic switch — no health check, no monitoring."""
    if req.target not in ("blue", "green"):
        raise HTTPException(status_code=400, detail="target must be 'blue' or 'green'")

    host = BLUE_HOST if req.target == "blue" else GREEN_HOST
    port = 8001 if req.target == "blue" else 8002

    if current_config:
        port = current_config["ports"].get(req.target, port)

    try:
        switch_to(host, port)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Switch failed: {e}")

    return {"status": "switched", "target": req.target, "port": port}


@app.post("/rollback")
def manual_rollback():
    """Force rollback to Blue regardless of current metrics."""
    global fsm, metrics_gate

    if fsm.current_state.name == "IDLE":
        raise HTTPException(status_code=400, detail="Nothing to rollback — already IDLE")

    # Stop metrics monitoring if running
    if metrics_gate:
        metrics_gate.stop()

    port = 8001
    if current_config:
        port = current_config["ports"].get("blue", 8001)

    try:
        # Force rollback
        state_name = fsm.current_state.name
        if state_name == "DEPLOYING":
            fsm.set_reason("manual rollback")
            fsm.force_rollback_from_deploying()
        elif state_name == "HEALTH_CHECKING":
            fsm.set_reason("manual rollback")
            fsm.metrics_fail()
        elif state_name == "LIVE":
            fsm.set_reason("manual rollback")
            fsm.force_rollback_from_live()

        switch_to(BLUE_HOST, port)

        if fsm.current_state.name == "ROLLING_BACK":
            fsm.rollback_complete()

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Rollback failed: {e}")

    return {"status": "rolled_back", "active": "blue", "port": port}


@app.get("/status")
def get_status():
    """Get current FSM state + active version + recent events."""
    last_deployment = db.get_last_deployment()
    recent_events = db.get_last_events(5)

    return {
        "state": fsm.current_state.name,
        "deployment": last_deployment,
        "recent_events": recent_events,
    }


@app.get("/deployments")
def get_deployments():
    """Get full deployment history."""
    return {"deployments": db.get_deployment_history()}


@app.get("/health")
def orchestrator_health():
    """Orchestrator's own health check."""
    return {"healthy": True, "state": fsm.current_state.name}
