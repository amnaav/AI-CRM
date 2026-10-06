import time
from typing import Tuple, Optional
from django.http import HttpRequest


DEFAULT_THROTTLE_LIMIT = 15
DEFAULT_THROTTLE_WINDOW_SECONDS = 60


def check_throttled(
    request: HttpRequest,
    max_requests: int = DEFAULT_THROTTLE_LIMIT,
    window_seconds: int = DEFAULT_THROTTLE_WINDOW_SECONDS
) -> Tuple[bool, Optional[str]]:
    """
    Lightweight, deterministic session-based sliding-window request rate limiter.
    Does not require Redis, Memcached, or external services.
    Applies strictly to assistant submissions, not ordinary page views.
    Guarantees user session isolation.
    """
    now = time.time()
    raw_timestamps = request.session.get('assistant_throttle_timestamps', [])

    # Filter timestamps within the sliding window
    active_timestamps = [
        ts for ts in raw_timestamps
        if isinstance(ts, (int, float)) and (now - ts) < window_seconds
    ]

    if len(active_timestamps) >= max_requests:
        # User has exceeded the allowed query limit
        retry_after = int(window_seconds - (now - active_timestamps[0])) + 1
        retry_after = max(1, retry_after)
        message = (
            f"You have reached the temporary assistant question limit ({max_requests} questions per minute). "
            f"Please wait approximately {retry_after} second(s) before submitting another question."
        )
        return True, message

    # Record the current query timestamp
    active_timestamps.append(now)
    request.session['assistant_throttle_timestamps'] = active_timestamps
    request.session.modified = True

    return False, None


def reset_throttle(request: HttpRequest) -> None:
    """Helper to clear throttle state for tests or admin resets."""
    if 'assistant_throttle_timestamps' in request.session:
        del request.session['assistant_throttle_timestamps']
        request.session.modified = True
