import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

VALID_TRIGGER_MODES = {"auto", "on-demand", "smart"}


class ConfigValidationError(Exception):
    """Named exception for config validation failures.
    Production code only logs; tests may catch this to assert validation behaviour."""


def validate_config(config: Dict[str, Any]) -> None:
    """
    Validate fields in project_config.json.

    Logs a warning for each invalid value. Does NOT raise — a config typo
    must never break the live webhook handler.

    Checks:
      - trigger.mode ∈ {auto, on-demand, smart}
      - capture.viewport.width and .height are positive integers
      - routeMap values are str or list[str]
    """
    trigger = config.get("trigger") or {}
    mode = trigger.get("mode")
    if mode is not None and mode not in VALID_TRIGGER_MODES:
        logger.warning(
            "project_config.json: trigger.mode=%r is not one of %s; defaulting to 'auto'.",
            mode,
            sorted(VALID_TRIGGER_MODES),
        )

    capture = config.get("capture") or {}
    viewport = capture.get("viewport") or {}
    for dim in ("width", "height"):
        val = viewport.get(dim)
        if val is not None:
            try:
                iv = int(val)
                if iv <= 0:
                    raise ValueError("non-positive")
            except (ValueError, TypeError):
                logger.warning(
                    "project_config.json: capture.viewport.%s=%r must be a positive integer.",
                    dim,
                    val,
                )

    route_map = config.get("routeMap") or {}
    for pattern, routes in route_map.items():
        if not isinstance(routes, (str, list)):
            logger.warning(
                "project_config.json: routeMap[%r]=%r must be a str or list[str].",
                pattern,
                routes,
            )
            continue
        if isinstance(routes, list):
            for r in routes:
                if not isinstance(r, str):
                    logger.warning(
                        "project_config.json: routeMap[%r] contains non-string item %r.",
                        pattern,
                        r,
                    )


def load_config() -> Dict[str, Any]:
    """Load and validate project configuration from project_config.json at repo root."""
    repo_root = Path(__file__).resolve().parent.parent
    config_path = repo_root / "project_config.json"
    with open(config_path) as f:
        config = json.load(f)
    validate_config(config)
    return config
