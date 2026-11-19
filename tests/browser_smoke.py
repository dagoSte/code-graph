"""Optional real-browser checks. Requires Playwright; not a runtime dependency.

python tests/browser_smoke.py --browser /path/to/chromium [--demo /tmp/code-graph-demo.html]
"""
import argparse
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
CG = REPO / "scripts/cg"

SAMPLE = {
    "app.py": """from services.checkout import checkout
def submit_order(cart): return checkout(cart)
def retry_order(cart): return checkout(cart)
""",
    "services/checkout.py": """from services.pricing import calculate_total, validate_cart
from services.stock import reserve_stock
from services.payment import charge
from services.mail import send_receipt
from repositories.orders import save_order
def checkout(cart):
    validate_cart(cart)
    total = calculate_total(cart)
    reserve_stock(cart)
    order = save_order(cart)
    receipt = charge(total)
    send_receipt(receipt)
    return order
""",
    "services/pricing.py": """def validate_cart(cart): return bool(cart)
def calculate_total(cart): return subtotal(cart) + calculate_tax(cart)
def subtotal(cart): return sum(cart)
def calculate_tax(cart): return subtotal(cart) * 0.22
""",
    "services/stock.py": """def reserve_stock(cart): return check_availability(cart)
def check_availability(cart): return bool(cart)
def release_stock(cart): return cart
""",
    "services/payment.py": """def charge(amount): return authorize(amount)
def authorize(amount): return amount
def refund(amount): return amount
""",
    "services/mail.py": """def send_receipt(receipt): return enqueue(format_receipt(receipt))
def format_receipt(receipt): return str(receipt)
def enqueue(message): return message
""",
    "repositories/orders.py": """def save_order(cart): return write_record(cart)
def write_record(cart): return cart
def find_order(key): return key
""",
    "diagnostics.py": """class A:
    def ping(self): return 1
class B:
    def ping(self): return 2
def probe(client): return client.ping()
""",
}


def command(root, *args):
    subprocess.run([str(CG), "--root", str(root), *args], check=True, capture_output=True, text=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", help="Chromium executable; omit to use Playwright's installed version")
    parser.add_argument("--demo", type=Path, help="save the synthetic example HTML here")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    checks, errors, requests = [], [], []

    def check(condition, message):
        if not condition:
            raise AssertionError(message)
        checks.append(message)

    with tempfile.TemporaryDirectory(prefix="cg-browser-") as temp:
        root = Path(temp) / "checkout-demo"
        root.mkdir()
        for name, source in SAMPLE.items():
            file = root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(source, encoding="utf-8")
        command(root, "index")
        command(root, "export", "--output", ".code-graph/all.html")
        command(root, "export", "--symbol", "checkout", "--depth", "2")
        html = root / ".code-graph/explore.html"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=args.browser)
            context = browser.new_context(viewport={"width": 1440, "height": 900}, offline=True)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.on("request", lambda request: requests.append(request.url) if request.url.startswith(("http:", "https:")) else None)
            page.goto(html.as_uri())
            check(page.locator("#details h2").inner_text() == "checkout", "focused graph opens on the requested symbol")
            check(page.locator(".node").count() >= 7, "callers and callees render as SVG nodes offline")
            check(page.locator(".edge").count() >= 6, "directed call edges render")
            page.select_option("#depth", "2")
            check(page.locator(".node").count() > 9, "two-hop exploration expands the neighborhood")
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.screenshot), full_page=True)
            page.locator("#details .neighbor").first.click()
            check(page.locator("#details h2").inner_text() != "checkout", "neighbor button navigates to its symbol")
            page.fill("#search", "calculate_tax")
            check(page.locator("#results .result").count() == 1, "search finds a symbol by name")
            page.locator("#results .result").press("Enter")
            check(page.locator("#details h2").inner_text() == "calculate_tax", "keyboard selection displays symbol metadata")
            check(page.locator(".node").count() > 1, "selecting a search result reveals its neighbors")
            check(page.evaluate("document.activeElement.classList.contains('node')"), "keyboard focus survives graph navigation")
            page.fill("#search", "")
            page.select_option("#module", "repositories")
            check(page.locator("#results .result").count() > 0 and page.evaluate("state.visible.every(n => n.module === 'repositories')"), "module filter applies to the visible graph")
            page.select_option("#module", "")
            page.click("#overview")
            before = page.evaluate("state.scale")
            page.click("#zoom-in")
            check(page.evaluate("state.scale") > before, "zoom control changes the viewport")
            page.click("#fit")
            node = page.locator(".node").first
            box = node.bounding_box()
            original = node.get_attribute("transform")
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] / 2 + 45, box["y"] + box["height"] / 2 + 20, steps=5)
            page.mouse.up()
            check(node.get_attribute("transform") != original, "nodes can be dragged")
            box = page.locator("#graph").bounding_box()
            before = page.evaluate("[state.x, state.y]")
            page.mouse.move(box["x"] + 3, box["y"] + box["height"] - 50)
            page.mouse.down()
            page.mouse.move(box["x"] + 40, box["y"] + box["height"] - 35, steps=4)
            page.mouse.up()
            check(page.evaluate("[state.x, state.y]") != before, "the canvas can be panned")
            page.goto((root / ".code-graph/all.html").as_uri())
            check(page.locator(".edge.low").count() > 0, "low-confidence edges are visibly marked")
            page.select_option("#confidence", "high")
            check(page.locator(".edge.low").count() == 0 and page.evaluate("state.edges.every(e => e.confidence === 'high')"), "confidence filter excludes uncertain edges")
            page.set_viewport_size({"width": 390, "height": 844})
            check(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "mobile layout has no horizontal overflow")
            page.set_viewport_size({"width": 1440, "height": 900})

            hostile = '</script><script>window.PWNED=1</script><img src="https://invalid.example/x" onerror="alert(1)">'
            with sqlite3.connect(root / ".code-graph/graph.db") as db:
                db.execute("UPDATE symbols SET name=?,qname=?,sig=? WHERE name='find_order'", (hostile, hostile, hostile))
            command(root, "export", "--output", ".code-graph/hostile.html")
            page.goto((root / ".code-graph/hostile.html").as_uri())
            page.fill("#search", "PWNED")
            page.locator("#results .result").click()
            check(page.locator("#details h2").inner_text() == hostile, "hostile source text is displayed literally")
            check(page.evaluate("typeof window.PWNED") == "undefined" and page.locator("img").count() == 0, "embedded source cannot execute HTML or scripts")

            large = Path(temp) / "large"
            large.mkdir()
            (large / "many.py").write_text("\n".join(f"def symbol_{i:03}(): return {i}" for i in range(220)))
            command(large, "index")
            command(large, "export")
            page.goto((large / ".code-graph/explore.html").as_uri())
            check(page.locator(".node").count() == 160 and "limitata" in page.locator("#view-count").inner_text(), "large views show an explicit display bound")
            page.get_by_role("button", name="Altri 80 simboli").press("Enter")
            check(page.locator("#results .result").count() == 160, "result pagination makes additional symbols available")
            check(page.evaluate("document.activeElement === document.querySelectorAll('#results .result')[80]"), "pagination focuses the first newly loaded symbol")
            page.fill("#search", "symbol_219")
            page.locator("#results .result").click()
            check(page.locator("#details h2").inner_text() == "symbol_219", "symbols outside the initial view remain searchable")

            empty = Path(temp) / "empty"
            empty.mkdir()
            command(empty, "index")
            command(empty, "export")
            page.goto((empty / ".code-graph/explore.html").as_uri())
            check(page.locator("#empty").is_visible(), "an empty index has a useful empty state")
            check(not errors, "no JavaScript or CSP errors: " + repr(errors))
            check(not requests, "no HTTP requests: " + repr(requests))
            browser.close()
        if args.demo:
            args.demo.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(html, args.demo)
    print(f"{len(checks)}/{len(checks)} browser checks passed")
    for message in checks:
        print("PASS " + message)


if __name__ == "__main__":
    main()
