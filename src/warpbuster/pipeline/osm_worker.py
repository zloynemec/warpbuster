"""Linux resource boundary used by the shared OSM pipeline."""

from __future__ import annotations

import os
import pickle
from contextlib import suppress
from typing import Any


def child_main(
    connection: Any,
    activity: Any,
    integrity: Any,
    base_plan: Any,
    config: Any,
    policy: Any,
    endpoint_hints: Any = None,
) -> None:
    """Enter a new process group, enforce inherited limits and return bounded IPC."""
    from .osm import OSMResult, execute_osm_pipeline

    try:
        os.setsid()
        _apply_resource_limits(config)

        def progress(stage: str) -> None:
            connection.send_bytes(pickle.dumps(("stage", stage)))

        result = execute_osm_pipeline(
            activity,
            integrity,
            base_plan,
            config,
            policy=policy,
            stage_callback=progress,
            endpoint_hints=endpoint_hints,
        )
        payload = pickle.dumps(("result", result), protocol=pickle.HIGHEST_PROTOCOL)
        if len(payload) > config.osm_ipc_maximum_bytes:
            result = OSMResult(base_plan, "unavailable", "routing", "ipc_invalid")
            payload = pickle.dumps(("result", result), protocol=pickle.HIGHEST_PROTOCOL)
        connection.send_bytes(payload)
    except BaseException:
        with suppress(BaseException):
            connection.send_bytes(
                pickle.dumps(
                    (
                        "result",
                        OSMResult(base_plan, "unavailable", "setup", "resource_limit"),
                    )
                )
            )
    finally:
        connection.close()


def _apply_resource_limits(config: Any) -> None:
    import resource

    resource.setrlimit(
        resource.RLIMIT_AS,
        (config.osm_child_memory_limit_bytes, config.osm_child_memory_limit_bytes),
    )
    resource.setrlimit(
        resource.RLIMIT_CPU,
        (config.osm_child_cpu_seconds, config.osm_child_cpu_seconds),
    )
