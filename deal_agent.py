"""
Sheytoon Deal Agent (cloud, multi-user version)

Runs on GitHub Actions every ~10 minutes:
  1. Reads Telegram messages from everyone (commands, join requests)
  2. Checks the deal forum's RSS feed
  3. Sends each approved user the new deals that match THEIR interests

The owner (TELEGRAM_CHAT_ID) approves or denies new users.
User data is stored encrypted in users.enc (key derived from the bot token),
so chat IDs and interests are not readable in the public repository.

Secrets (GitHub -> Settings -> Secrets and variables -> Actions):
  TELEGRAM_TOKEN    bot token from BotFather
  TELEGRAM_CHAT_ID  the owner's chat ID
"""

import base64
import hashlib
import json
import os
import re
import time

import feedparser
import requests
from cryptography.fernet import Fernet, InvalidToken

# (feed URL, label shown in alerts)
FEEDS = [
    ("https://forums.redflagdeals.com/feed/forum/9", "🔥"),  # RedFlagDeals forum: Hot Deals
    ("https://www.redflagdeals.com/rss/deals.php?city=montreal&content_type=editorial", "📰"),  # RFD main site: editors' picks
]
BOT_NAME = "Sheytoon Deal Agent"

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")      # public: seen post IDs, update offset
USERS_FILE = os.path.join(HERE, "users.enc")       # encrypted: users and their interests
OLD_KEYWORDS = os.path.join(HERE, "keywords.txt")  # only used once, to migrate the owner
OLD_EXCLUDE = os.path.join(HERE, "exclude.txt")

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
OWNER = str(os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
API = f"https://api.telegram.org/bot{TOKEN}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

HELP = (
    f"🤖 {BOT_NAME} commands:\n"
    "/add word - follow an interest (e.g. /add ps5)\n"
    "/remove word - stop following it\n"
    "/list - show your interests\n"
    "/block word - ignore deals with this word\n"
    "/unblock word - stop ignoring it\n"
    "/test word - search current deals now (e.g. /test shoes)\n"
    "/test - search current deals for all your interests\n"
    "/watch link - watch a product page (Walmart etc.) and get told when it's\n"
    "   back in stock or cheaper. Add a max price: /watch link 10\n"
    "/watches - list your watched products\n"
    "/stop - pause your alerts (send /start to resume)\n"
    "/help - show this message\n\n"
    "Tip: deal titles use brand names, so add brands too (nike, asics, sony...).\n"
    "Replies come at the next check (within ~10 minutes)."
)
OWNER_HELP = (
    "\n\n👑 Owner commands:\n"
    "/users - list everyone and their status\n"
    "/approve_ID or /deny_ID - answer a join request\n"
    "/kick_ID - remove a user"
)


# ---------------- storage ----------------

def _fernet():
    key = base64.urlsafe_b64encode(hashlib.sha256(("deal-agent:" + TOKEN).encode()).digest())
    return Fernet(key)


def read_list(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [l.strip().lower() for l in f if l.strip() and not l.strip().startswith("#")]
    except FileNotFoundError:
        return []


def new_user(name, status):
    return {"name": name, "status": status, "keywords": [], "exclude": ["expired", "dead"]}


def load_users():
    try:
        with open(USERS_FILE, "rb") as f:
            return json.loads(_fernet().decrypt(f.read()))
    except FileNotFoundError:
        pass
    except InvalidToken:
        print("WARNING: users.enc could not be decrypted (bot token changed?). Starting fresh.")
    # First run (or unreadable file): create the owner from the old keyword files.
    owner = new_user("Owner", "approved")
    owner["keywords"] = read_list(OLD_KEYWORDS)
    owner["exclude"] = read_list(OLD_EXCLUDE) or owner["exclude"]
    return {OWNER: owner}


def save_users(users):
    with open(USERS_FILE, "wb") as f:
        f.write(_fernet().encrypt(json.dumps(users).encode()))


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seen": [], "update_offset": 0}


def save_state(state):
    state["seen"] = state["seen"][-2000:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)


# ---------------- telegram ----------------

def send(chat_id, text):
    try:
        r = requests.post(f"{API}/sendMessage",
                          data={"chat_id": chat_id, "text": text}, timeout=20)
        if not r.ok:
            print("Telegram error:", r.status_code, r.text[:200])
        return r.ok
    except Exception as e:
        print("Telegram error:", e)
        return False


def display_name(msg):
    u = msg.get("from", {})
    name = (u.get("first_name", "") + " " + u.get("last_name", "")).strip() or "Someone"
    if u.get("username"):
        name += f" (@{u['username']})"
    return name


def handle_commands(state, users, tests):
    try:
        data = requests.get(f"{API}/getUpdates",
                            params={"offset": state.get("update_offset", 0), "timeout": 0},
                            timeout=20).json()
    except Exception as e:
        print("Could not read Telegram messages:", e)
        return
    if not data.get("ok"):
        print("Telegram getUpdates failed:", data.get("description"))
        return

    for u in data.get("result", []):
        state["update_offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        chat = msg.get("chat", {})
        if chat.get("type") != "private":
            continue  # only private chats
        cid = str(chat.get("id"))
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip().lower()
        user = users.get(cid)

        # ---- people who are not approved yet ----
        if user is None or user["status"] in ("pending", "denied"):
            if user is None and cmd == "/start":
                users[cid] = new_user(display_name(msg), "pending")
                send(cid, f"👋 Hi! Your request to use {BOT_NAME} was sent to the owner. "
                          "You'll get a message when you're approved.")
                send(OWNER, f"🆕 Join request from {users[cid]['name']}\n"
                            f"Approve: /approve_{cid}\nDeny: /deny_{cid}")
            elif user is not None and user["status"] == "pending":
                send(cid, "⏳ Your request is still waiting for the owner's approval.")
            continue  # denied users are ignored silently

        # ---- owner-only commands ----
        if cid == OWNER:
            m = re.match(r"^/(approve|deny|kick)_(-?\d+)$", cmd)
            if m:
                action, target = m.groups()
                t = users.get(target)
                if not t:
                    send(OWNER, "That user was not found. Send /users to see everyone.")
                elif action == "approve":
                    t["status"] = "approved"
                    send(target, f"✅ You're in! Welcome to {BOT_NAME}.\n"
                                 "Add your interests, e.g. /add airpods\n\n" + HELP)
                    send(OWNER, f"✅ Approved {t['name']}.")
                elif action == "deny":
                    t["status"] = "denied"
                    send(OWNER, f"🚫 Denied {t['name']}.")
                elif action == "kick" and target != OWNER:
                    users.pop(target)
                    send(OWNER, f"👋 Removed {t['name']}.")
                continue
            if cmd == "/users":
                lines = [f"{'👑' if k == OWNER else '•'} {v['name']} - {v['status']}, "
                         f"{len(v['keywords'])} interests"
                         + (f"  /kick_{k}" if k != OWNER else "")
                         + (f"  /approve_{k}" if v["status"] == "pending" else "")
                         for k, v in users.items()]
                send(OWNER, "👥 Users:\n" + "\n".join(lines))
                continue

        # ---- normal user commands ----
        kw, ex = user["keywords"], user["exclude"]
        if cmd == "/start":
            user["status"] = "approved"
            send(cid, "▶️ Alerts are on. Send /list to see your interests.")
        elif cmd == "/stop":
            user["status"] = "stopped"
            send(cid, "⏸ Alerts paused. Send /start to turn them back on.")
        elif cmd == "/add" and arg:
            if arg in kw:
                send(cid, f"'{arg}' is already in your list.")
            else:
                kw.append(arg)
                send(cid, f"✅ Added '{arg}'. You now follow {len(kw)} interests.")
        elif cmd == "/remove" and arg:
            if arg in kw:
                kw.remove(arg)
                send(cid, f"🗑 Removed '{arg}'.")
            else:
                send(cid, f"'{arg}' is not in your list. Send /list to see it.")
        elif cmd == "/block" and arg:
            if arg not in ex:
                ex.append(arg)
            send(cid, f"🚫 Deals with '{arg}' will be ignored.")
        elif cmd == "/unblock" and arg:
            if arg in ex:
                ex.remove(arg)
            send(cid, f"👍 '{arg}' is no longer blocked.")
        elif cmd == "/list":
            k = "\n".join(f"• {w}" for w in kw) or "(none yet - add one with /add)"
            send(cid, f"📋 Your interests:\n{k}\n\n🚫 Blocked: {', '.join(ex) or '(none)'}")
        elif cmd == "/test":
            tests.append((cid, arg))
        elif cmd == "/watch" and arg:
            parts = text.split()[1:]
            url = clean_url(parts[0])
            target = None
            if len(parts) > 1:
                try:
                    target = float(parts[1].replace("$", ""))
                except ValueError:
                    pass
            watches = user.setdefault("watches", [])
            if not url.startswith("http"):
                send(cid, "Please send a full product link, e.g. /watch https://www.walmart.ca/en/ip/...")
            elif any(w["url"] == url for w in watches):
                send(cid, "You're already watching that product. Send /watches to see your list.")
            elif len(watches) >= MAX_WATCHES:
                send(cid, f"You can watch up to {MAX_WATCHES} products. Remove one first (/watches).")
            else:
                watches.append({"url": url, "target": target, "name": None,
                                "status": None, "price": None, "new": True})
        elif cmd == "/watches":
            ws = user.get("watches", [])
            if not ws:
                send(cid, "You're not watching any products. Send /watch plus a product link.")
            else:
                lines = []
                for i, w in enumerate(ws, 1):
                    st = {"IN_STOCK": "🟢 in stock", "OUT_OF_STOCK": "🔴 out of stock"}.get(w["status"], "⚪ not checked yet")
                    price = f"${w['price']:.2f}" if w.get("price") else "?"
                    tgt = f", alert at ≤ ${w['target']:.2f}" if w.get("target") else ""
                    lines.append(f"{i}. {w.get('name') or w['url']}\n   {price} - {st}{tgt}  /unwatch_{i}")
                send(cid, "👀 Watched products:\n" + "\n".join(lines))
        elif re.match(r"^/unwatch_\d+$", cmd):
            i = int(cmd.split("_")[1]) - 1
            ws = user.get("watches", [])
            if 0 <= i < len(ws):
                w = ws.pop(i)
                send(cid, f"🗑 Stopped watching {w.get('name') or w['url']}.")
            else:
                send(cid, "That number is not in your list. Send /watches to see it.")
        else:
            send(cid, HELP + (OWNER_HELP if cid == OWNER else ""))


# ---------------- deals ----------------

def contains_any(text, words):
    text = text.lower()
    return any(re.search(r"\b" + re.escape(w.lower()) + r"\b", text) for w in words)


def fetch_posts():
    posts = []
    for url, icon in FEEDS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            entries = feedparser.parse(resp.content).entries
        except Exception as e:
            print(f"Could not read {url}: {e}")
            continue
        print(f"Read {len(entries)} posts from {url}")
        for e in entries:
            posts.append({"id": e.get("id") or e.get("link"),
                          "title": (e.get("title") or "").strip(),
                          "link": e.get("link", ""),
                          "icon": icon, "feed": url})
    return posts


def wants(user, title, words=None):
    words = words if words is not None else user["keywords"]
    if not words:
        return False
    if user["exclude"] and contains_any(title, user["exclude"]):
        return False
    return contains_any(title, words)


def run_tests(tests, users, posts):
    for cid, word in tests:
        user = users[cid]
        words = [word] if word else user["keywords"]
        label = f"'{word}'" if word else "your interests"
        hits = [p for p in posts if wants(user, p["title"], words)]
        if not hits:
            send(cid, f"🧪 Test: checked {len(posts)} current deals - nothing matches {label} right now.")
            continue
        send(cid, f"🧪 Test: {len(hits)} current deal(s) match {label}:")
        for p in hits:
            send(cid, f"{p['icon']} {p['title']}\n{p['link']}")
            time.sleep(0.5)


def send_new_deals(state, users, posts):
    seen = set(state["seen"])
    # A feed seen for the first time only gets "remembered", so users are not flooded.
    known = set(state.get("feeds", []))
    if not known and seen:
        known.add(FEEDS[0][0])  # the forum feed was already running before this list existed
    fetched = {p["feed"] for p in posts}
    new = []
    for p in posts:
        if not p["id"] or p["id"] in seen:
            continue
        seen.add(p["id"])
        state["seen"].append(p["id"])
        if p["feed"] in known:
            new.append(p)
    fresh = fetched - known
    if fresh:
        print(f"New feed(s) {sorted(fresh)}: remembered current posts, no alerts.")
    state["feeds"] = sorted(known | fetched)
    sent = 0
    for p in new:
        for cid, user in users.items():
            if user["status"] == "approved" and wants(user, p["title"]):
                send(cid, f"{p['icon']} {p['title']}\n{p['link']}")
                sent += 1
                time.sleep(0.5)
    print(f"{len(new)} new post(s), {sent} alert(s) sent.")


# ---------------- product watching ----------------

MAX_WATCHES = 20
PAGE_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-CA,en;q=0.9",
}


def clean_url(url):
    """Remove tracking junk (?utm_..., affiliate IDs) from a product link."""
    return url.split("?")[0].split("#")[0].strip()


def _find_jsonld_product(obj):
    if isinstance(obj, list):
        for o in obj:
            r = _find_jsonld_product(o)
            if r:
                return r
    elif isinstance(obj, dict):
        if obj.get("@type") in ("Product", ["Product"]):
            return obj
        for k in ("@graph", "mainEntity"):
            if k in obj:
                r = _find_jsonld_product(obj[k])
                if r:
                    return r
    return None


def check_product(url):
    """Return {"name", "price", "was", "status"} or {"error": ...}."""
    try:
        r = requests.get(url, headers=PAGE_HEADERS, timeout=30)
    except Exception as e:
        return {"error": f"could not connect ({e.__class__.__name__})"}
    html = r.text
    if r.status_code != 200 or "Robot or human" in html or "px-captcha" in html:
        return {"error": f"the store blocked the check (HTTP {r.status_code})"}

    # 1) Walmart.ca: data is in the __NEXT_DATA__ script
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            p = json.loads(m.group(1))["props"]["pageProps"]["initialData"]["data"]["product"]
            pi = p.get("priceInfo") or {}
            return {"name": p.get("name"),
                    "price": (pi.get("currentPrice") or {}).get("price"),
                    "was": (pi.get("wasPrice") or {}).get("price"),
                    "status": "IN_STOCK" if p.get("availabilityStatus") == "IN_STOCK" else "OUT_OF_STOCK"}
        except (KeyError, TypeError, ValueError):
            pass

    # 2) Most other stores: schema.org JSON-LD
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            prod = _find_jsonld_product(json.loads(block))
        except ValueError:
            continue
        if prod:
            offers = prod.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            avail = str(offers.get("availability", ""))
            price = offers.get("price") or offers.get("lowPrice")
            return {"name": prod.get("name"),
                    "price": float(price) if price else None, "was": None,
                    "status": "IN_STOCK" if "InStock" in avail else "OUT_OF_STOCK"}
    return {"error": "could not find product info on that page"}


def fmt_product(info, url):
    price = f"${info['price']:.2f}" if info.get("price") else "price unknown"
    was = f" (was ${info['was']:.2f})" if info.get("was") and info.get("price") and info["was"] > info["price"] else ""
    return f"{info.get('name') or 'Product'}\n💲 {price}{was}\n{url}"


def check_watches(users):
    cache = {}
    for cid, user in users.items():
        if user["status"] != "approved":
            continue
        for w in user.get("watches", []):
            url = w["url"]
            if url not in cache:
                cache[url] = check_product(url)
                time.sleep(2)  # be gentle with the store
            info = cache[url]
            if "error" in info:
                if w.get("new"):
                    send(cid, f"⚠️ I couldn't check that product: {info['error']}.\n{url}")
                    w["new"] = False
                print("Watch error:", url, info["error"])
                continue
            st, price = info["status"], info.get("price")
            stock_word = "🟢 In stock" if st == "IN_STOCK" else "🔴 Out of stock"
            if w.get("new"):
                tgt = f" I'll alert you when it's in stock at ${w['target']:.2f} or less." if w.get("target") else \
                      " I'll alert you when it's back in stock or the price drops."
                send(cid, f"👀 Now watching:\n{fmt_product(info, url)}\n{stock_word}.{tgt}")
                w["new"] = False
            else:
                cheap_enough = w.get("target") is None or (price is not None and price <= w["target"])
                back = st == "IN_STOCK" and w.get("status") != "IN_STOCK"
                dropped = (price is not None and w.get("price") is not None and price < w["price"]
                           and st == "IN_STOCK")
                if back and cheap_enough:
                    send(cid, f"🟢 BACK IN STOCK!\n{fmt_product(info, url)}")
                elif dropped and cheap_enough:
                    send(cid, f"📉 PRICE DROP! ${w['price']:.2f} → ${price:.2f}\n{fmt_product(info, url)}")
            w.update({"name": info.get("name"), "status": st, "price": price})
    print(f"Checked {len(cache)} watched product(s).")


def main():
    if not TOKEN or not OWNER:
        raise SystemExit("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID secret.")
    state = load_state()
    users = load_users()
    tests = []
    handle_commands(state, users, tests)
    manual = os.environ.get("TEST_SEARCH", "").strip()
    if manual.startswith("http"):
        info = check_product(clean_url(manual))
        print("Product test:", info)
        send(OWNER, "🧪 Product test:\n" + (f"⚠️ {info['error']}" if "error" in info else
              fmt_product(info, clean_url(manual)) + f"\n{info['status']}"))
    elif manual:
        tests.append((OWNER, "" if manual.lower() == "all" else manual.lower()))
    posts = fetch_posts()
    run_tests(tests, users, posts)
    send_new_deals(state, users, posts)
    check_watches(users)
    save_users(users)
    save_state(state)
    print(f"Users: {sum(u['status'] == 'approved' for u in users.values())} approved, "
          f"{sum(u['status'] == 'pending' for u in users.values())} pending.")


if __name__ == "__main__":
    main()
