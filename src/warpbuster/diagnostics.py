"""Bounded exception diagnostics for private local audits, never public responses."""

import traceback

# Character limits keep one failure bounded within the private audit or journal.
DIAGNOSTIC_LIMITS = {"exception_type": 256, "reason": 4096, "traceback": 32768}


def bounded_diagnostic(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        # Native paths may contain surrogates; private audits must remain UTF-8 writable.
        key: value[key].encode("utf-8", errors="backslashreplace").decode("utf-8")[-limit:]
        for key, limit in DIAGNOSTIC_LIMITS.items()
        if isinstance(value.get(key), str)
    }


def exception_diagnostic(error: BaseException) -> dict[str, str]:
    return bounded_diagnostic(
        {
            "exception_type": type(error).__name__,
            "reason": str(error),
            # Include chained exceptions and stack locations, but never frame locals.
            "traceback": "".join(traceback.format_exception(error, limit=20)),
        }
    )
