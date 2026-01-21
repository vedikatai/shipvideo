"""
Gets the preview URL from project configuration.

For MVP, we record from the main deployed URL configured in project_config.json.
No need to resolve PR-specific preview URLs from GitHub commit statuses.
"""
from app.config import load_config


def get_preview_url() -> str:
    """
    Gets the preview URL from project configuration.
    
    Returns:
        str: The preview URL (e.g., "https://shipvideo-demo.vercel.app")
    
    Raises:
        ValueError: If preview URL is not configured
    """
    config = load_config()
    preview_url = config.get("preview_url_template")
    
    if not preview_url:
        raise ValueError(
            "preview_url_template not configured in project_config.json. "
            "Please set it to your deployed app URL (e.g., https://shipvideo-demo.vercel.app)"
        )
    
    print(f"✅ Using configured preview URL: {preview_url}", flush=True)
    return preview_url
