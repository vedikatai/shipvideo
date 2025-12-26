import json
from pathlib import Path

def load_config():
    """Load project configuration from project_config.json at repo root."""
    repo_root = Path(__file__).resolve().parent.parent
    config_path = repo_root / "project_config.json"
    with open(config_path) as f:
        return json.load(f)

