"""Bounded exception diagnostics for private local logs, never public responses."""

import traceback

# Character limits keep one failure bounded within the rotating journal.
DIAGNOSTIC_LIMITS = {"exception_type": 256, "reason": 4096, "traceback": 32768}


def bounded_diagnostic(value):
    if not isinstance(value, dict):
        return {}
    return {
        key: value[key][-limit:]
        for key, limit in DIAGNOSTIC_LIMITS.items()
        if isinstance(value.get(key), str)
    }


def exception_diagnostic(error):
    return bounded_diagnostic(
        {
            "exception_type": type(error).__name__,
            "reason": str(error),
            # Include chained exceptions and stack locations, but never frame locals.
            "traceback": "".join(traceback.format_exception(error, limit=20)),
        }
    )
