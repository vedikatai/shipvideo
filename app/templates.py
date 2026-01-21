"""
Demo video capture templates.

Templates define the sequence of actions (screenshots, clicks, navigation)
to perform when recording a demo video for a PR.
"""

TEMPLATES = {
    # Billing flow: capture home page, then navigate to /billing
    "billing": {
        "steps": [
            {"action": "screenshot"},  # Home page first
            {"action": "goto", "url": "/billing"},
            {"action": "screenshot"},  # Billing page
        ]
    },
    # Dashboard flow: capture home page, then navigate to /dashboard
    "dashboard": {
        "steps": [
            {"action": "screenshot"},  # Home page first
            {"action": "goto", "url": "/dashboard"},
            {"action": "screenshot"},  # Dashboard page
        ]
    },
    # Default: just capture the root page with no navigation
    "default": {
        "steps": [
            {"action": "screenshot"},
        ]
    },
}


def match_template(pr_title: str):
    """
    Match a PR title to a demo template using simple keyword matching.
    
    Args:
        pr_title: The PR title to match against
        
    Returns:
        dict: Template dictionary with "steps" key containing list of actions
    """
    title = (pr_title or "").lower()

    if "billing" in title:
        key = "billing"
    elif "dashboard" in title:
        key = "dashboard"
    else:
        key = "default"

    template = TEMPLATES[key]
    print(f"🧩 Matched template '{key}' for PR title: {pr_title!r}", flush=True)
    return template
