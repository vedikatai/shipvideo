import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from app.config import load_config

APP_DIR = Path(__file__).resolve().parent

def capture_demo(pr_number=None):
    """
    Capture screenshots from preview environment.
    
    Args:
        pr_number: PR number to use in preview URL template. If None, reads from PR_NUMBER env var.
    """
    config = load_config()
    template = config["preview_url_template"]
    
    # Get PR number from parameter or environment variable
    if pr_number is None:
        pr_number = os.getenv("PR_NUMBER")
    
    # Format the URL template with PR number
    if pr_number:
        try:
            preview_url = template.format(pr_number=pr_number)
        except KeyError:
            # Template doesn't have {pr_number} placeholder, use as-is
            print(f"⚠️ Template doesn't have {{pr_number}} placeholder, using template as-is", flush=True)
            preview_url = template
    else:
        # Fallback: use template as-is if no PR number (for backward compatibility)
        preview_url = template
    
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
    # Support both direct call and module execution
    pr_number = os.getenv("PR_NUMBER")
    if len(sys.argv) > 1:
        pr_number = sys.argv[1]
    capture_demo(pr_number=pr_number)
