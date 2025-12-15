from playwright.sync_api import sync_playwright

def capture_demo():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:3000")  # your test app
        page.screenshot(path="shot1.png")
        page.click("button#new-feature")
        page.screenshot(path="shot2.png")
        browser.close()

if __name__ == "__main__":
    capture_demo()
