import threading
import time
import logging
from services.email_service import process_automated_stale_reminders, process_upcoming_event_reminders

logger = logging.getLogger(__name__)

_scheduler_started = False
_lock = threading.Lock()

def run_reminder_cycle(app):
    """Execute one reminder pass (stale + upcoming-event). Idempotent: both
    processors stamp sent dates and skip already-notified rows, so retries
    and overlapping cron invocations never double-send.

    Returns {'stale': int, 'events': int}. Safe to call from a request
    (Vercel Cron endpoint), a thread, or a script. Never raises.
    """
    try:
        with app.app_context():
            stale_count = process_automated_stale_reminders()
            event_count = process_upcoming_event_reminders()
            total_sent = stale_count + event_count
            if total_sent > 0:
                logger.info(f"Reminder cycle sent {total_sent} email(s) ({stale_count} follow-up, {event_count} 24h event reminders).")
            return {'stale': stale_count, 'events': event_count, 'total': total_sent}
    except Exception as e:
        logger.error(f"Error in reminder cycle: {e}")
        return {'stale': 0, 'events': 0, 'total': 0, 'error': type(e).__name__}


def start_email_scheduler(app, check_interval_seconds=3600):
    global _scheduler_started
    with _lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def run_loop():
        logger.info("Email follow-up & event background scheduler started.")
        # Delay initial run slightly to allow app to fully initialize
        time.sleep(5)
        while True:
            run_reminder_cycle(app)
            time.sleep(check_interval_seconds)

    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()
