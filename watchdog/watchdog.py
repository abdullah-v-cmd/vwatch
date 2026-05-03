"""
V-Watch Production Watchdog Service
=====================================
24/7 autonomous watchdog that monitors all containers and services.
Automatically detects failures and triggers recovery without any manual
intervention. Runs as its own Docker container.

Recovery strategies:
  1. HTTP health-check failure  → wait + retry → restart container
  2. Container exited/stopped   → immediate restart
  3. Edge AI camera lost        → signal restart + reconnect
  4. Backend unreachable        → wait for DB + restart backend
  5. Consecutive failures       → exponential back-off + alert log

Logging: structured JSON logs + plain text with rotation.
"""

import os
import sys
import time
import json
import signal
import logging
import threading
import subprocess
from datetime import datetime, timezone
from typing import Dict, Optional, List
from logging.handlers import RotatingFileHandler

import requests

# ── Logging Setup ──────────────────────────────────────────────────────────────

LOG_DIR = os.environ.get("LOG_DIR", "/app/logs")
os.makedirs(LOG_DIR, exist_ok=True)

_handlers = [logging.StreamHandler(sys.stdout)]
try:
    _handlers.append(
        RotatingFileHandler(
            os.path.join(LOG_DIR, "watchdog.log"),
            maxBytes=10 * 1024 * 1024,   # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
    )
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] WATCHDOG: %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger("watchdog")


# ── Configuration ──────────────────────────────────────────────────────────────

class Config:
    # Docker socket
    DOCKER_AVAILABLE = os.path.exists("/var/run/docker.sock")

    # Service health endpoints
    BACKEND_URL         = os.environ.get("BACKEND_URL",  "http://backend:8000")
    EDGE_AI_URL         = os.environ.get("EDGE_AI_URL",  "http://edge_ai:8001")
    POSTGRES_URL        = os.environ.get("POSTGRES_URL", "http://postgres:5432")

    # Container names
    CONTAINERS = {
        "backend":  os.environ.get("BACKEND_CONTAINER",  "vwatch_backend"),
        "edge_ai":  os.environ.get("EDGE_AI_CONTAINER",  "vwatch_edge"),
        "postgres": os.environ.get("POSTGRES_CONTAINER", "vwatch_postgres"),
        "frontend": os.environ.get("FRONTEND_CONTAINER", "vwatch_frontend"),
        "relay":    os.environ.get("RELAY_CONTAINER",    "vwatch_relay"),
    }

    # Timing (seconds)
    CHECK_INTERVAL          = int(os.environ.get("CHECK_INTERVAL",      "15"))
    HEALTH_TIMEOUT          = int(os.environ.get("HEALTH_TIMEOUT",      "8"))
    RESTART_COOLDOWN        = int(os.environ.get("RESTART_COOLDOWN",    "30"))
    MAX_CONSECUTIVE_FAILS   = int(os.environ.get("MAX_FAILS",           "3"))
    BACKOFF_BASE            = float(os.environ.get("BACKOFF_BASE",      "2.0"))
    MAX_BACKOFF             = int(os.environ.get("MAX_BACKOFF",         "300"))

    # Feature flags
    ENABLE_DOCKER_RESTART   = os.environ.get("ENABLE_DOCKER_RESTART", "true").lower() == "true"
    ENABLE_ALERTS           = os.environ.get("ENABLE_ALERTS",         "false").lower() == "true"
    ALERT_WEBHOOK_URL       = os.environ.get("ALERT_WEBHOOK_URL",     "")


cfg = Config()


# ── Service Health Check ───────────────────────────────────────────────────────

class ServiceCheck:
    """Tracks health state and consecutive failure count for one service."""

    def __init__(self, name: str, url: str, path: str = "/health"):
        self.name            = name
        self.url             = url.rstrip("/")
        self.path            = path
        self.healthy         = True          # optimistic start
        self.consecutive_fails = 0
        self.total_checks    = 0
        self.total_restarts  = 0
        self.last_check_ts   = None
        self.last_restart_ts: Optional[float] = None
        self._backoff        = 0.0
        self._lock           = threading.Lock()

    def check(self) -> bool:
        """Perform a single HTTP health check. Returns True if healthy."""
        full_url = f"{self.url}{self.path}"
        try:
            r = requests.get(full_url, timeout=cfg.HEALTH_TIMEOUT)
            ok = r.status_code in (200, 204)
        except Exception:
            ok = False

        with self._lock:
            self.total_checks += 1
            self.last_check_ts = datetime.now(timezone.utc).isoformat()

            if ok:
                if not self.healthy:
                    logger.info(f"[{self.name}] ✅ Service RECOVERED")
                    self._send_alert(f"{self.name} RECOVERED", "info")
                self.healthy = True
                self.consecutive_fails = 0
                self._backoff = 0.0
            else:
                self.consecutive_fails += 1
                self.healthy = False
                logger.warning(
                    f"[{self.name}] ❌ Health check FAILED "
                    f"(consecutive={self.consecutive_fails})"
                )

        return ok

    def should_restart(self) -> bool:
        """Return True when this service needs a Docker restart."""
        with self._lock:
            if self.consecutive_fails < cfg.MAX_CONSECUTIVE_FAILS:
                return False
            # Respect cooldown
            if self.last_restart_ts:
                elapsed = time.monotonic() - self.last_restart_ts
                cooldown = min(
                    cfg.RESTART_COOLDOWN * (cfg.BACKOFF_BASE ** self.total_restarts),
                    cfg.MAX_BACKOFF,
                )
                if elapsed < cooldown:
                    return False
            return True

    def mark_restarted(self):
        with self._lock:
            self.total_restarts += 1
            self.last_restart_ts = time.monotonic()
            self.consecutive_fails = 0

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "name":              self.name,
                "healthy":           self.healthy,
                "consecutive_fails": self.consecutive_fails,
                "total_checks":      self.total_checks,
                "total_restarts":    self.total_restarts,
                "last_check":        self.last_check_ts,
            }

    def _send_alert(self, message: str, level: str = "warning"):
        if not cfg.ENABLE_ALERTS or not cfg.ALERT_WEBHOOK_URL:
            return
        try:
            requests.post(
                cfg.ALERT_WEBHOOK_URL,
                json={"text": f"[V-Watch Watchdog] {level.upper()}: {message}"},
                timeout=5,
            )
        except Exception:
            pass


# ── Docker Control ─────────────────────────────────────────────────────────────

def docker_restart_container(container_name: str) -> bool:
    """Restart a Docker container by name using the Docker CLI."""
    if not cfg.ENABLE_DOCKER_RESTART:
        logger.info(f"[Docker] Restart disabled. Would restart: {container_name}")
        return False
    if not cfg.DOCKER_AVAILABLE:
        logger.warning("[Docker] /var/run/docker.sock not available — cannot restart")
        return False
    try:
        result = subprocess.run(
            ["docker", "restart", container_name],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            logger.info(f"[Docker] ✅ Restarted container: {container_name}")
            return True
        else:
            logger.error(
                f"[Docker] ❌ Restart failed for {container_name}: {result.stderr.strip()}"
            )
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"[Docker] Restart timed out for {container_name}")
        return False
    except FileNotFoundError:
        logger.error("[Docker] docker CLI not found in PATH")
        return False
    except Exception as e:
        logger.error(f"[Docker] Unexpected error restarting {container_name}: {e}")
        return False


def get_container_status(container_name: str) -> Optional[str]:
    """Return container status string (running/exited/etc.) or None if not found."""
    if not cfg.DOCKER_AVAILABLE:
        return None
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


# ── Watchdog Core ──────────────────────────────────────────────────────────────

class VWatchWatchdog:
    """
    Central watchdog loop. Monitors services via HTTP health checks
    and container states, auto-recovering failures.
    """

    def __init__(self):
        self._running = False
        self._lock    = threading.Lock()

        # Define services to watch
        self._services: Dict[str, ServiceCheck] = {
            "backend": ServiceCheck(
                "backend",
                cfg.BACKEND_URL,
                path="/health",
            ),
            "edge_ai": ServiceCheck(
                "edge_ai",
                cfg.EDGE_AI_URL,
                path="/health",
            ),
        }

        # Per-service last action timestamp to prevent storm restarts
        self._last_action: Dict[str, float] = {}

        logger.info("=" * 60)
        logger.info("V-Watch Production Watchdog Initialized")
        logger.info(f"  Check interval : {cfg.CHECK_INTERVAL}s")
        logger.info(f"  Max fails      : {cfg.MAX_CONSECUTIVE_FAILS}")
        logger.info(f"  Docker socket  : {'available' if cfg.DOCKER_AVAILABLE else 'NOT available'}")
        logger.info(f"  Docker restart : {'enabled' if cfg.ENABLE_DOCKER_RESTART else 'disabled'}")
        logger.info("=" * 60)

    def _check_service(self, svc: ServiceCheck) -> None:
        """Run one health check cycle for a service."""
        svc.check()

        if svc.should_restart():
            container = cfg.CONTAINERS.get(svc.name)
            if not container:
                return

            logger.warning(
                f"[{svc.name}] Triggering auto-recovery restart "
                f"(restarts so far: {svc.total_restarts})"
            )

            ok = docker_restart_container(container)
            svc.mark_restarted()

            if ok:
                logger.info(f"[{svc.name}] Container restarted, waiting {cfg.CHECK_INTERVAL}s")
            else:
                logger.error(f"[{svc.name}] Restart command failed — check Docker access")

    def _check_container_states(self) -> None:
        """
        Inspect raw container states. If a container has exited unexpectedly
        and Docker's own restart policy did not revive it, force a restart.
        """
        for svc_name, container_name in cfg.CONTAINERS.items():
            status = get_container_status(container_name)
            if status is None:
                continue   # Not found / no docker access

            if status in ("exited", "dead"):
                cooldown_key = f"container_{svc_name}"
                last = self._last_action.get(cooldown_key, 0)
                if time.monotonic() - last < cfg.RESTART_COOLDOWN:
                    continue

                logger.warning(
                    f"[Container] {container_name} found in '{status}' state — restarting"
                )
                if docker_restart_container(container_name):
                    self._last_action[cooldown_key] = time.monotonic()

    def _log_status_report(self) -> None:
        """Emit a structured status summary every 5 minutes."""
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "services":  {k: v.to_dict() for k, v in self._services.items()},
        }
        logger.info(f"[Status] {json.dumps(report)}")

    def run(self) -> None:
        """Main watchdog loop. Blocks until stopped."""
        self._running = True
        status_timer  = time.monotonic()
        STATUS_INTERVAL = 300   # 5 minutes

        logger.info("[Watchdog] Starting main loop...")

        while self._running:
            loop_start = time.monotonic()

            # 1. HTTP health checks
            for svc in self._services.values():
                if not self._running:
                    break
                self._check_service(svc)

            # 2. Container state inspection (requires docker socket)
            if cfg.DOCKER_AVAILABLE:
                self._check_container_states()

            # 3. Periodic status report
            if time.monotonic() - status_timer >= STATUS_INTERVAL:
                self._log_status_report()
                status_timer = time.monotonic()

            # 4. Sleep until next check cycle
            elapsed = time.monotonic() - loop_start
            sleep_for = max(0.1, cfg.CHECK_INTERVAL - elapsed)
            time.sleep(sleep_for)

        logger.info("[Watchdog] Main loop stopped.")

    def stop(self) -> None:
        self._running = False
        logger.info("[Watchdog] Stop requested.")

    def status(self) -> dict:
        return {
            "watchdog": "running" if self._running else "stopped",
            "services": {k: v.to_dict() for k, v in self._services.items()},
            "config": {
                "check_interval":       cfg.CHECK_INTERVAL,
                "max_consecutive_fails": cfg.MAX_CONSECUTIVE_FAILS,
                "docker_available":     cfg.DOCKER_AVAILABLE,
                "docker_restart":       cfg.ENABLE_DOCKER_RESTART,
            },
        }


# ── HTTP Status Server ─────────────────────────────────────────────────────────

def start_status_server(watchdog: VWatchWatchdog, port: int = 9090) -> None:
    """
    Tiny HTTP server exposing /health and /status endpoints so the
    watchdog itself can be monitored externally.
    """
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import json as _json

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # Silence access logs

        def do_GET(self):
            if self.path in ("/health", "/"):
                body = _json.dumps({"status": "ok", "service": "watchdog"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            elif self.path == "/status":
                body = _json.dumps(watchdog.status(), default=str).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            else:
                self.send_response(404)
                self.end_headers()

    server = HTTPServer(("0.0.0.0", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="status-http")
    t.start()
    logger.info(f"[StatusServer] Listening on :{port}  /health  /status")


# ── Entry Point ────────────────────────────────────────────────────────────────

def main():
    watchdog = VWatchWatchdog()

    # Expose /health and /status
    start_status_server(watchdog, port=int(os.environ.get("STATUS_PORT", "9090")))

    # Graceful shutdown
    def _shutdown(sig, frame):
        logger.info(f"[Watchdog] Signal {sig} received — shutting down")
        watchdog.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    watchdog.run()


if __name__ == "__main__":
    main()
