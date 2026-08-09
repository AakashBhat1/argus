"""
Smart Parking System — Lightweight in-process event bus for real-time SSE.

Thread-safe pub/sub using stdlib queue + Lock.
Used by /api/events to push entry/exit/profile_change to all connected clients.
"""

import queue
import threading


_lock = threading.Lock()
_subscribers = []  # list[queue.Queue]


def subscribe():
    """Create and register a new bounded subscriber queue. Returns the queue."""
    q = queue.Queue(maxsize=100)  # prevent unbounded memory growth for slow clients
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q):
    """Remove the given subscriber queue (idempotent)."""
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)


def publish(event_dict):
    """Non-blocking publish to all current subscribers.
    Silently drops the event for any queue that is full.
    Never blocks the publisher (critical path in parking ops).
    """
    with _lock:
        subs = list(_subscribers)  # snapshot under lock
    for q in subs:
        try:
            q.put_nowait(event_dict)
        except queue.Full:
            # Slow consumer; drop this event for them and continue
            pass


def subscriber_count():
    """Return current number of active SSE subscribers (read under lock; approximate is fine)."""
    with _lock:
        return len(_subscribers)
