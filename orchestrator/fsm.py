import logging
from statemachine import StateMachine, State
from orchestrator import db

logger = logging.getLogger("orchestrator.fsm")


class DeploymentFSM(StateMachine):
    """
    State machine for Blue/Green deployment lifecycle.

    States: IDLE → DEPLOYING → HEALTH_CHECKING → LIVE
                                    ↓
                              ROLLING_BACK → IDLE
    """

    # --- States ---
    idle = State("IDLE", initial=True)
    deploying = State("DEPLOYING")
    health_checking = State("HEALTH_CHECKING")
    live = State("LIVE")
    rolling_back = State("ROLLING_BACK")

    # --- Transitions ---
    start_deploy = idle.to(deploying)
    health_ok = deploying.to(health_checking)
    health_fail = deploying.to(idle)
    metrics_ok = health_checking.to(live)
    metrics_fail = health_checking.to(rolling_back)
    stuck_timeout = health_checking.to(rolling_back)
    rollback_complete = rolling_back.to(idle)

    # Manual rollback from any active state
    force_rollback_from_deploying = deploying.to(rolling_back)
    force_rollback_from_live = live.to(rolling_back)

    def __init__(self, deployment_id: int | None = None):
        self.deployment_id = deployment_id
        self._reason = ""
        super().__init__()

    def set_reason(self, reason: str):
        self._reason = reason

    # --- Hooks: log every transition ---
    def on_enter_deploying(self):
        logger.info("[FSM] IDLE → DEPLOYING")
        if self.deployment_id:
            db.log_event(self.deployment_id, "IDLE", "DEPLOYING", self._reason or "deploy started")
            db.update_deployment_state(self.deployment_id, "DEPLOYING")
        self._reason = ""

    def on_enter_health_checking(self):
        logger.info("[FSM] DEPLOYING → HEALTH_CHECKING")
        if self.deployment_id:
            db.log_event(self.deployment_id, "DEPLOYING", "HEALTH_CHECKING", self._reason or "health check passed, traffic switched, monitoring metrics")
            db.update_deployment_state(self.deployment_id, "HEALTH_CHECKING", "green")
        self._reason = ""

    def on_enter_live(self):
        logger.info("[FSM] HEALTH_CHECKING → LIVE (deployment successful)")
        if self.deployment_id:
            db.log_event(self.deployment_id, "HEALTH_CHECKING", "LIVE", self._reason or "metrics clean, deployment promoted")
            db.finish_deployment(self.deployment_id, "LIVE", "green")
        self._reason = ""

    def on_enter_rolling_back(self):
        reason = self._reason or "rollback triggered"
        logger.warning(f"[FSM] → ROLLING_BACK (reason: {reason})")
        if self.deployment_id:
            prev = self.current_state.name if hasattr(self, '_prev_state') else "UNKNOWN"
            db.log_event(self.deployment_id, prev, "ROLLING_BACK", reason)
            db.update_deployment_state(self.deployment_id, "ROLLING_BACK", "blue")
        self._reason = ""

    def on_exit_rolling_back(self):
        logger.info("[FSM] ROLLING_BACK → IDLE (rollback complete)")
        if self.deployment_id:
            db.log_event(self.deployment_id, "ROLLING_BACK", "IDLE", "rollback completed, Blue restored")
            db.finish_deployment(self.deployment_id, "IDLE", "blue")

    def on_exit_deploying(self):
        # If transitioning to IDLE (health_fail), log it
        pass

    def on_enter_idle(self):
        # Only log if coming from a failed health check (deployment_id set but not initial)
        pass
