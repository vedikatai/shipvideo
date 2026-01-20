import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

APP_DIR = Path(__file__).resolve().parent

def capture_demo(preview_url: str):
    """
    Capture screenshots from preview environment.
    
    Args:
        preview_url: The preview URL to record from (e.g., "https://yourapp-pr456.vercel.app")
    
    Raises:
        ValueError: If preview_url is None or empty
    """
    if not preview_url:
        raise ValueError("preview_url cannot be None or empty")
    
    print(f"🌐 Navigating to preview URL: {preview_url}", flush=True)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(preview_url)  
        page.screenshot(path=str(APP_DIR / "shot1.png"))
        page.click("button#new-feature")
        page.screenshot(path=str(APP_DIR / "shot2.png"))
        browser.close()

if __name__ == "__main__":
    # Support direct call for testing
    if len(sys.argv) > 1:
        preview_url = sys.argv[1]
    else:
        preview_url = os.getenv("PREVIEW_URL")
        if not preview_url:
            raise ValueError("PREVIEW_URL environment variable or command line argument required")
    capture_demo(preview_url=preview_url)
