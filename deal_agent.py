"""
Soroush Deal Agent (cloud version)

Runs on GitHub Actions every ~10 minutes:
  1. Reads your Telegram commands (/add, /remove, /list, /help)
  2. Checks the deal forum's RSS feed
  3. Sends new matching deals to your Telegram

Secrets (set in GitHub -> Settings -> Secrets and variables -> Actions):
  TELEGRAM_TOKEN    your bot token from BotFather
  TELEGRAM_CHAT_ID  your chat ID
"""

import json
import os
import re
import time

import feedparser
import requests

FEEDS = [
    "https://forums.redflagdeals.com/feed/forum/9",  # RedFlagDeals Hot Deals
]

HERE = os.path.dirname(os.path.abspath(__file__))
KEYWORDS_FILE = os.path.join(HERE, "keywords.txt")
EXCLUDE_FILE = os.path.join(HERE, "exclude.txt")
STATE_FILE = os.path.join(HERE, "state.json")

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
API = f"https://api.telegram.org/bot{TOKEN}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

HELP = (
    "🤖 Soroush Deal Agent commands:\n"
    "/add word  - follow a new interest (e.g. /add ps5)\n"
    "/remove word - stop following it\n"
    "/list - show your interests\n"
    "/block word - ignore deals with this word\n"
    "/unblock word - stop ignoring it\n"
    "/test word - search current deals now (e.g. /test shoes)\n"
    "/test - search current deals for ALL your interests\n"
    "/help - show this message\n\n"
    "Changes apply at the next check (within ~10 minutes)."
)


# ---------------- files ----------------

def read_list(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
    except FileNotFoundError:
        return []


def write_list(path, items, header):
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        for i in items:
            f.write(i + "\n")


KW_HEADER = "# One interest per line. Change from Telegram with /add and /remove."
EX_HEADER = "# Deals containing these words are ignored. Change with /block and /unblock."


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

def send(text):
    try:
        r = requests.post(f"{API}/sendMessage",
                          data={"chat_id": CHAT_ID, "text": text}, timeout=20)
        if not r.ok:
            print("Telegram error:", r.status_code, r.text[:200])
    except Exception as e:
        print("Telegram error:", e)


TEST_REQUESTS = []


def handle_commands(state):
    """Read new Telegram messages and update keywords."""
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

    keywords = read_list(KEYWORDS_FILE)
    exclude = read_list(EXCLUDE_FILE)
    changed = False

    for u in data.get("result", []):
        state["update_offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != CHAT_ID:
            continue  # ignore anyone who is not you
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip().lower()

        if cmd == "/add" and arg:
            if arg in keywords:
                send(f"'{arg}' is already in your list.")
            else:
                keywords.append(arg); changed = True
                send(f"✅ Added '{arg}'. You now follow {len(keywords)} interests.")
        elif cmd == "/remove" and arg:
            if arg in keywords:
                keywords.remove(arg); changed = True
                send(f"🗑 Removed '{arg}'.")
            else:
                send(f"'{arg}' is not in your list. Send /list to see it.")
        elif cmd == "/block" and arg:
            if arg not in exclude:
                exclude.append(arg); changed = True
            send(f"🚫 Deals with '{arg}' will be ignored.")
        elif cmd == "/unblock" and arg:
            if arg in exclude:
                exclude.remove(arg); changed = True
            send(f"👍 '{arg}' is no longer blocked.")
        elif cmd == "/test":
            TEST_REQUESTS.append(arg)
        elif cmd == "/list":
            kw = "\n".join(f"• {k}" for k in keywords) or "(none: you get ALL deals)"
            ex = ", ".join(exclude) or "(none)"
            send(f"📋 Your interests:\n{kw}\n\n🚫 Blocked: {ex}")
        else:
            send(HELP)

    if changed:
        write_list(KEYWORDS_FILE, keywords, KW_HEADER)
        write_list(EXCLUDE_FILE, exclude, EX_HEADER)


# ---------------- deals ----------------

def contains_any(text, words):
    text = text.lower()
    return any(re.search(r"\b" + re.escape(w.lower()) + r"\b", text) for w in words)


def fetch_all():
    """Return (title, link, id) for every post in the feeds right now."""
    posts = []
    for url in FEEDS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            entries = feedparser.parse(resp.content).entries
        except Exception as e:
            print(f"Could not read {url}: {e}")
            continue
        for e in entries:
            posts.append(((e.get("title") or "").strip(), e.get("link", ""), e.get("id") or e.get("link")))
    return posts


def test_search(word):
    """Send current deals matching `word` (or all interests if empty), even if already seen."""
    words = [word] if word else read_list(KEYWORDS_FILE)
    posts = fetch_all()
    hits = [(t, l) for t, l, _ in posts if words and contains_any(t, words)]
    label = f"'{word}'" if word else "your interests"
    print(f"Test search for {label}: {len(hits)} hit(s) in {len(posts)} posts")
    if not hits:
        send(f"🧪 Test: checked {len(posts)} current deals - nothing matches {label} right now.")
        return
    send(f"🧪 Test: {len(hits)} current deal(s) match {label}:")
    for t, l in hits:
        send(f"🔥 {t}\n{l}")
        time.sleep(1)


def check_deals(state):
    keywords = read_list(KEYWORDS_FILE)
    exclude = read_list(EXCLUDE_FILE)
    seen = set(state["seen"])
    first_run = len(seen) == 0
    matches = []

    for url in FEEDS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            entries = feedparser.parse(resp.content).entries
        except Exception as e:
            print(f"Could not read {url}: {e}")
            continue
        print(f"Read {len(entries)} posts from {url}")
        for e in entries:
            pid = e.get("id") or e.get("link")
            title = (e.get("title") or "").strip()
            if not pid or pid in seen:
                continue
            seen.add(pid)
            state["seen"].append(pid)
            if exclude and contains_any(title, exclude):
                continue
            if not keywords or contains_any(title, keywords):
                matches.append((title, e.get("link", "")))

    if first_run:
        print(f"First run: remembered {len(seen)} posts, no alerts.")
        send("☁️ Deal Agent is now running in the cloud 24/7! Send /help to see commands.")
        return
    for title, link in matches:
        print("MATCH:", title)
        send(f"🔥 {title}\n{link}")
        time.sleep(1)
    print(f"{len(matches)} new matching deal(s).")


def main():
    if not TOKEN or not CHAT_ID:
        raise SystemExit("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID secret.")
    state = load_state()
    handle_commands(state)
    manual = os.environ.get("TEST_SEARCH", "").strip().lower()
    if manual:
        TEST_REQUESTS.append("" if manual == "all" else manual)
    for w in TEST_REQUESTS:
        test_search(w)
    check_deals(state)
    save_state(state)


if __name__ == "__main__":
    main()
