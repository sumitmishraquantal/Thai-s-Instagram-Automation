"""Cron-style trigger: automatically start the pipeline on a schedule.

Configure in backend/.env:

    SCHEDULE_ENABLED=true
    SCHEDULE_CRON=0 9 * * *
    SCHEDULE_WORKFLOW=podcast
    SCHEDULE_TOPIC=                # optional category override; blank = auto-pick
"""
import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import get_settings

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_runner: Callable[[str, str], None] | None = None


def register_runner(fn: Callable[[str, str], None]):
    """fn(workflow, topic) starts a pipeline run. Registered by main.py."""
    global _runner
    _runner = fn


def _fire():
    s = get_settings()
    if _runner is None:
        logger.error("Scheduled trigger fired but no runner registered")
        return
    logger.info("Scheduled trigger firing: workflow=%s topic=%r",
                s.schedule_workflow, s.schedule_topic)
    try:
        _runner(s.schedule_workflow, s.schedule_topic)
    except Exception as e:  # noqa: BLE001
        logger.exception("Scheduled run failed: %s", e)


def start_scheduler():
    """Start the background scheduler if enabled in settings."""
    global _scheduler
    s = get_settings()
    if not s.schedule_enabled or not s.schedule_cron.strip():
        logger.info("Scheduler disabled (SCHEDULE_ENABLED off or no SCHEDULE_CRON).")
        return None
    try:
        trigger = CronTrigger.from_crontab(s.schedule_cron.strip())
    except Exception as e:  # noqa: BLE001
        logger.error("Invalid SCHEDULE_CRON %r: %s", s.schedule_cron, e)
        return None
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_fire, trigger, id="workflow_trigger", replace_existing=True)
    _scheduler.start()
    logger.info("Scheduler started: cron=%r workflow=%s",
                s.schedule_cron, s.schedule_workflow)
    return _scheduler


def next_run_time() -> str | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job("workflow_trigger")
    return str(job.next_run_time) if job and job.next_run_time else None
