from pathlib import Path
from playwright.sync_api import sync_playwright
from app.config import load_config

APP_DIR = Path(__file__).resolve().parent
config = load_config()
base_url = config["preview_url_template"]

def capture_demo():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base_url)  
        page.screenshot(path=str(APP_DIR / "shot1.png"))
        page.click("button#new-feature")
        page.screenshot(path=str(APP_DIR / "shot2.png"))
        browser.close()

if __name__ == "__main__":
    capture_demo()
