from typing import List, Dict, Any


VALID_ACTIONS = {"goto", "click", "screenshot"}


def validate_steps(steps: Any) -> List[Dict[str, Any]]:
    """
    Keep only steps with a known action to avoid executor crashes.
    """
    if not isinstance(steps, list):
        return []

    valid: List[Dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        action = step.get("action")
        if action in VALID_ACTIONS:
            valid.append(step)
    return valid


def normalize_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize heterogeneous LLM step shapes into the minimal executor format.
    """
    normalized: List[Dict[str, Any]] = []

    for step in steps:
        action = step.get("action")

        if action == "click":
            selector = (
                step.get("selector")
                or step.get("element")
                or step.get("target")
            )
            if not selector:
                continue
            normalized.append(
                {
                    "action": "click",
                    "selector": selector,
                }
            )

        elif action == "goto":
            url = step.get("url", "/")
            normalized.append(
                {
                    "action": "goto",
                    "url": url,
                }
            )

        elif action == "screenshot":
            normalized.append(
                {
                    "action": "screenshot",
                    "label": step.get("label", ""),
                }
            )

    return normalized

