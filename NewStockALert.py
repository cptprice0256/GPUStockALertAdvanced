import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import json
import os
import time
import cloudscraper  # <-- Added for 100% free scraping

# ───────────────────────────────────────────────────────────────────────
# CONFIG
# ───────────────────────────────────────────────────────────────────────
BASE_URL = (
 "https://gameloot.in"
 "?swoof=1&stock=instock&really_curr_tax=237-product_cat"
)
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]
# SCRAPER_API_KEY removed since it is no longer required
CACHE_FILE = "previous_stock.json"

HEADERS = {
 "User-Agent": (
 "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
 "AppleWebKit/537.36 (KHTML, like Gecko) "
 "Chrome/124.0.0.0 Safari/537.36"
 ),
 "Accept-Language": "en-US,en;q=0.9",
 "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ───────────────────────────────────────────────────────────────────────
# CACHE
# ───────────────────────────────────────────────────────────────────────
def load_previous():
 if os.path.exists(CACHE_FILE):
  with open(CACHE_FILE, "r") as f:
   return set(json.load(f))
 return None # None = first run

def save_current(names: set):
 with open(CACHE_FILE, "w") as f:
  json.dump(list(names), f)

# ───────────────────────────────────────────────────────────────────────
# SCRAPER (UPDATED FOR FREE)
# ───────────────────────────────────────────────────────────────────────
def fetch_page_cloudscraper():
 """Fetch page via CloudScraper for free, bypassing Cloudflare blocks."""
 print(" [INFO] Fetching via CloudScraper (Free, no credits used)...")
 scraper = cloudscraper.create_scraper()
 resp = scraper.get(
  BASE_URL,
  headers=HEADERS,
  timeout=30
 )
 resp.raise_for_status()
 return resp.text

def fetch_page_direct():
 """Fallback: fetch page directly with browser-like headers."""
 print(" [INFO] Fallback: fetching directly...")
 resp = requests.get(BASE_URL, headers=HEADERS, timeout=30)
 resp.raise_for_status()
 return resp.text

def get_products():
 html = None
 # ── Try Free CloudScraper first ──────────────────────────────────────
 try:
  html = fetch_page_cloudscraper()
 except Exception as e:
  print(f" [WARN] CloudScraper failed: {e}")
  
 # ── Fallback to direct fetch ─────────────────────────────────────────
 if not html:
  try:
   html = fetch_page_direct()
  except requests.RequestException as e:
   print(f" [ERROR] Direct fetch also failed: {e}")
   return []

 soup = BeautifulSoup(html, "html.parser")
 # ── Selector cascade ─────────────────────────────────────────
 products = soup.select("div.products div.product")
 print(f" [INFO] Primary selector → {len(products)} products found.")
 if not products:
  products = soup.select("div.grid_item.product_item")
  print(f" [INFO] Fallback selector → {len(products)} products found.")
 if not products:
  products = soup.select(".product")
  print(f" [INFO] Last-resort selector → {len(products)} products found.")
 return products

# ───────────────────────────────────────────────────────────────────────
# PARSER
# ───────────────────────────────────────────────────────────────────────
def parse_products(products):
 available = []
 for p in products:
  # Only in-stock items
  if not p.select_one("div.in-stock"):
   continue
  # Name
  name_tag = (
   p.select_one("div.product_details h5") or
   p.select_one("h2.woocommerce-loop-product__title") or
   p.select_one("h5")
  )
  # Price
  price_tag = (
   p.select_one("span.product_price ins span.woocommerce-Price-amount") or
   p.select_one("span.product_price span.woocommerce-Price-amount") or
   p.select_one("span.woocommerce-Price-amount")
  )
  # URL
  url_tag = (
   p.select_one("a.product_item_link") or
   p.select_one("a.add_to_cart_button") or
   p.select_one("a")
  )
  
  name = name_tag.get_text(strip=True) if name_tag else "Unknown"
  price = price_tag.get_text(strip=True) if price_tag else "N/A"
  url = url_tag["href"] if url_tag else "N/A"
  available.append({"name": name, "price": price, "url": url})
 return available

# ───────────────────────────────────────────────────────────────────────
# DISCORD NOTIFIER
# ───────────────────────────────────────────────────────────────────────
def send_discord_message(payload):
 try:
  res = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
  res.raise_for_status()
 except requests.RequestException as e:
  print(f" [ERROR] Discord send failed: {e}")

def chunk_product_list(products, max_chars=3800):
 chunks, current = [], ""
 for p in products:
  line = f"• **[{p['name']}]({p['url']})** — {p['price']}\n"
  if len(current) + len(line) > max_chars:
   chunks.append(current)
   current = line
  else:
   current += line
 if current:
  chunks.append(current)
 return chunks

def notify_discord(available, newly_available, is_first_run):
 ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
 embeds = []
 # ── 1. Summary ──────────────────────────────────────────────────────
 summary_value = f" **In Stock:** {len(available)}"
 if newly_available:
  summary_value += f"\n **Newly Available:** {len(newly_available)}"
 embeds.append({
  "title" : " GPU Stock Report — GameLoot.in",
  "url" : BASE_URL,
  "color" : 0x00FF99,
  "timestamp": ts,
  "footer" : {"text": "gpustockalert • Checks every hour"},
  "fields" : [{"name": " Summary", "value": summary_value, "inline": False}],
 })
 # ── 2. New Arrivals (skipped on first run) ───────────────────────────
 if newly_available and not is_first_run:
  chunks = chunk_product_list(newly_available)
  for i, chunk in enumerate(chunks):
   embeds.append({
    "title" : " NEW Stock Alert!" + (f" (Part {i+1})" if len(chunks) > 1 else ""),
    "description": chunk,
    "color" : 0xFF4500,
    "timestamp" : ts,
   })
 # ── 3. In-Stock List ─────────────────────────────────────────────────
 if available:
  chunks = chunk_product_list(available)
  for i, chunk in enumerate(chunks):
   embeds.append({
    "title" : f" In Stock ({len(available)} items)" + (f" — Part {i+1}" if len(chunks) > 1 else ""),
    "description": chunk,
    "color" : 0x00BFFF,
    "timestamp" : ts,
   })
 else:
  embeds.append({
   "title" : " In Stock",
   "description": " No GPU items currently in stock on GameLoot.in",
   "color" : 0x808080,
   "timestamp" : ts,
  })
 # ── Send in batches of 10 (Discord limit) ─────────────────────────────
 for i in range(0, len(embeds), 10):
  send_discord_message({"embeds": embeds[i:i+10]})
  time.sleep(0.5)
 print(f" [INFO] Discord notified — {len(available)} in stock | {len(newly_available)} new.")

# ───────────────────────────────────────────────────────────────────────
# CONSOLE REPORT
# ───────────────────────────────────────────────────────────────────────
def print_report(available, newly_available):
 ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
 sep = "=" * 70
 print(f"\n{sep}")
 print(f" GPU STOCK REPORT — {ts}")
 print(sep)
 if newly_available:
  print(f"\n NEWLY IN STOCK ({len(newly_available)} items):\n")
  for i, p in enumerate(newly_available, 1):
   print(f" {i:>3}. {p['name']}")
   print(f" Price : {p['price']}")
   print(f" URL : {p['url']}\n")
 print(f"\n IN STOCK ({len(available)} items):\n")
 if available:
  for i, p in enumerate(available, 1):
   print(f" {i:>3}. {p['name']}")
   print(f" Price : {p['price']}")
   print(f" URL : {p['url']}\n")
 else:
  print(" No GPU items currently in stock.\n")
 print(f"\n{sep}\n")

# ───────────────────────────────────────────────────────────────────────
# MAIN
# ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
 print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ── gpustockalert running...")
 previous = load_previous()
 is_first_run = previous is None
 product_elements = get_products()
 if not product_elements:
  print(" [WARNING] No products found — possible block or page change.")
  send_discord_message({
   "embeds": [{
    "title" : " gpustockalert Warning",
    "description": (
     "No products found on GameLoot.in GPU category.\n"
     "CloudScraper may have failed or page structure changed.\n"
     "Manual check: https://gameloot.in"
    ),
    "color" : 0xFFCC00,
    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
   }]
  })
  exit(1)
  
 available = parse_products(product_elements)
 current_names = {p["name"] for p in available}
 newly_available = [] if is_first_run else [
  p for p in available if p["name"] not in previous
 ]
 save_current(current_names)
 print_report(available, newly_available)
 notify_discord(available, newly_available, is_first_run)
