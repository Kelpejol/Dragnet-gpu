# =============================================================================
# scripts/worker_scaler.py — Smart RunPod Worker Scaler
#
# Three cron jobs call this script with different commands:
#
#   python3 worker_scaler.py warmup   — 8am WAT (7am UTC) weekdays
#                                       Unconditionally sets workersMin=1
#                                       Ensures first request of day is always warm
#
#   python3 worker_scaler.py check    — Every 30 mins, 8am-6pm WAT weekdays
#                                       Sets workersMin=0 only if no activity for 2+ hrs
#                                       Scales down during genuine idle periods
#
#   python3 worker_scaler.py cooldown — 6pm WAT (5pm UTC) weekdays
#                                       Unconditionally sets workersMin=0
#                                       Stops cost at end of business day
#
# Cron entries (server time is UTC, WAT = UTC+1):
#   0 7 * * 1-5    /usr/bin/python3 /var/www/dragnet-gpu/scripts/worker_scaler.py warmup   >> /var/log/worker_scaler.log 2>&1
#   */30 7-16 * * 1-5 /usr/bin/python3 /var/www/dragnet-gpu/scripts/worker_scaler.py check >> /var/log/worker_scaler.log 2>&1
#   0 17 * * 1-5   /usr/bin/python3 /var/www/dragnet-gpu/scripts/worker_scaler.py cooldown >> /var/log/worker_scaler.log 2>&1
#
# Redis key written by dragnet-gpu gateway on every request:
#   last_ai_request → Unix timestamp of last request
# =============================================================================

import os
import sys
import time
import httpx
import logging
from dotenv import load_dotenv

load_dotenv("/var/www/dragnet-gpu/.env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# =============================================================================
#  Configuration
# =============================================================================

RUNPOD_API_KEY    = os.environ.get("RUNPOD_API_KEY", "")
HEAVY_ENDPOINT_ID = os.environ.get("RUNPOD_HEAVY_ENDPOINT_ID", "h6m8xvw9e3f9pk")
LIGHT_ENDPOINT_ID = os.environ.get("RUNPOD_LIGHT_ENDPOINT_ID", "xjp0viock5cnn6")
REDIS_URL         = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# If no AI request in this many seconds during business hours, scale down
INACTIVITY_THRESHOLD_SECONDS = 2 * 60 * 60  # 2 hours

RUNPOD_UPDATE_URL = "https://rest.runpod.io/v1/endpoints/{endpoint_id}/update"


# =============================================================================
#  RunPod API
# =============================================================================

def set_workers_min(endpoint_id: str, workers_min: int) -> bool:
    """Update workersMin on a RunPod endpoint via REST API."""
    url = RUNPOD_UPDATE_URL.format(endpoint_id=endpoint_id)
    headers = {
        "Authorization": f"Bearer {RUNPOD_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        response = httpx.post(
            url,
            json={"workersMin": workers_min},
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        logger.info(f"[runpod] Endpoint {endpoint_id} → workersMin={workers_min} ✓")
        return True
    except Exception as exc:
        logger.error(f"[runpod] Failed to update {endpoint_id}: {exc}")
        return False


def update_both_endpoints(workers_min: int):
    """Update workersMin on both heavy and light endpoints."""
    heavy_ok = set_workers_min(HEAVY_ENDPOINT_ID, workers_min)
    light_ok = set_workers_min(LIGHT_ENDPOINT_ID, workers_min)

    if heavy_ok and light_ok:
        logger.info(f"Both endpoints → workersMin={workers_min} ✓")
    else:
        logger.warning("One or more endpoint updates failed — check logs above")


# =============================================================================
#  Activity check (used by 'check' command only)
# =============================================================================

def get_last_activity_seconds_ago() -> float:
    """
    Read last_ai_request from Redis.
    Returns seconds since last request, or infinity if none recorded.
    """
    try:
        import redis
        r = redis.from_url(REDIS_URL, socket_connect_timeout=3)
        val = r.get("last_ai_request")
        r.close()

        if val is None:
            logger.info("No last_ai_request in Redis — no prior activity recorded")
            return float("inf")

        seconds_ago = time.time() - float(val)
        logger.info(
            f"Last AI request: {seconds_ago:.0f}s ago "
            f"({seconds_ago/3600:.1f} hrs)"
        )
        return seconds_ago

    except Exception as exc:
        logger.warning(f"Redis read failed: {exc} — assuming inactive")
        return float("inf")


# =============================================================================
#  Commands
# =============================================================================

def cmd_warmup():
    """
    Called at 8am WAT every weekday.
    Unconditionally warms both endpoints — no activity check.
    Ensures the first request of the day is always fast.
    """
    logger.info("WARMUP — Business day starting. Setting workersMin=1.")
    update_both_endpoints(workers_min=1)


def cmd_check():
    """
    Called every 30 minutes during business hours.
    Scales down to 0 only if no activity for 2+ hours.
    Does nothing if there has been recent activity.
    """
    logger.info("CHECK — Evaluating recent activity.")
    seconds_ago = get_last_activity_seconds_ago()

    if seconds_ago > INACTIVITY_THRESHOLD_SECONDS:
        logger.info(
            f"No activity for {seconds_ago/3600:.1f} hrs "
            f"(threshold {INACTIVITY_THRESHOLD_SECONDS/3600:.0f} hrs). "
            f"Scaling down."
        )
        update_both_endpoints(workers_min=0)
    else:
        logger.info(
            f"Activity {seconds_ago/60:.0f} mins ago — "
            f"keeping workers warm, no change."
        )


def cmd_cooldown():
    """
    Called at 6pm WAT every weekday.
    Unconditionally scales down — end of business day.
    """
    logger.info("COOLDOWN — Business day ending. Setting workersMin=0.")
    update_both_endpoints(workers_min=0)


# =============================================================================
#  Entry point
# =============================================================================

COMMANDS = {
    "warmup":   cmd_warmup,
    "check":    cmd_check,
    "cooldown": cmd_cooldown,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: python3 worker_scaler.py [{' | '.join(COMMANDS.keys())}]")
        sys.exit(1)

    command = sys.argv[1]
    logger.info("=" * 50)
    logger.info(f"Worker Scaler — {command.upper()}")

    COMMANDS[command]()

    logger.info("=" * 50)