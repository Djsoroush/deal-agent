# 😈 Sheytoon Deal Agent

**A serverless Telegram bot that hunts Canadian deals 24/7 and sends instant, personalized alerts.**

It watches [RedFlagDeals](https://forums.redflagdeals.com/hot-deals-f9/) (Canada's largest deal community) every minute and notifies each user the moment a deal matches *their* interests — AirPods, Nike, PS5, Costco, anything.

👉 **Try it on Telegram:** [@sheytoon_deal_bot](https://t.me/sheytoon_deal_bot) *(invite-only: press Start and the owner approves you)*

---

## ✨ Features

- ⚡ **Instant replies** — Telegram webhooks, no polling (responds in under a second)
- ⏱ **Checks deals every minute** with Cloudflare Cron Triggers
- 👥 **Multi-user** — every user has their own interests and blocked words
- 🔐 **Invite-only** — the owner approves or denies new users with one tap
- ⭐ **Interactive UI** — inline buttons, quick-pick interests, rich deal cards with a "View deal" button and image preview
- 🧪 **Test search** — search current deals on demand
- 📰 **Two sources** — RFD Hot Deals forum + RFD editors' picks
- 🛡 **Secure** — webhook secret token, secrets stored encrypted, `/deleteme` lets users erase their data
- 💸 **Runs free** on the Cloudflare Workers free plan, with no server to maintain

## 🏗 Architecture

```
 Telegram user ──message/button──▶ Telegram ──webhook (secret token)──▶ Cloudflare Worker: fetch()
                                                                              │
 RedFlagDeals RSS ◀──every minute── Cloudflare Cron ──▶ Cloudflare Worker: scheduled()
                                                                              │
                                                     Cloudflare KV (users, interests, seen deals)
                                                                              │
 Telegram user ◀──────────── deal alert (HTML card + button) ◀────────────────┘
```

| Part | Technology |
|---|---|
| Runtime | Cloudflare Workers (JavaScript, ES modules) |
| Scheduling | Cloudflare Cron Triggers (`* * * * *`) |
| Storage | Cloudflare Workers KV |
| Messaging | Telegram Bot API (webhooks, inline keyboards, HTML formatting) |
| Data source | RSS / Atom feeds |

**Design notes**
- KV writes happen only when data changes, which keeps the bot well inside the free plan (1,000 writes/day).
- A brand-new feed is "remembered" on its first read, so users never get flooded with old posts.
- Keyword matching uses whole-word, case-insensitive regex, with per-user exclusion words.

## 🤖 Bot commands

| Command | What it does |
|---|---|
| `/menu` | Main menu with buttons |
| `/add word` *(or just type a word)* | Follow an interest |
| `/list` | Your interests (tap ❌ to remove) |
| `/test word` | Search current deals now |
| `/block word` · `/unblock word` | Ignore deals containing a word |
| `/stop` · `/start` | Pause or resume alerts |
| `/privacy` · `/deleteme` | See what is stored · erase your data |
| `/users` *(owner)* | List users; approve, deny or remove them |

## 🚀 Deploy your own copy (about 10 minutes, free)

1. **Create a bot:** in Telegram, message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. **Get your chat ID:** message [@userinfobot](https://t.me/userinfobot) and copy your ID (you'll be the owner).
3. **Install Wrangler and log in** (needs [Node.js](https://nodejs.org)):
   ```bash
   npm install -g wrangler
   wrangler login
   ```
4. **Create the storage** and paste the printed `id` into `wrangler.toml`:
   ```bash
   wrangler kv namespace create DB
   ```
5. **Add your secrets:**
   ```bash
   wrangler secret put TELEGRAM_TOKEN
   wrangler secret put OWNER_ID
   ```
6. **Deploy:**
   ```bash
   wrangler deploy
   ```
7. **Connect Telegram:** open `https://<your-worker>.workers.dev/setup` in a browser. You should see four ✅ lines.
8. Open your bot in Telegram and press **Start** 🎉

> 💡 No terminal? You can also create the Worker in the Cloudflare dashboard (Workers & Pages → Create → Hello World), paste `src/worker.js`, then add the KV binding `DB`, the two secrets, and a Cron Trigger for every minute in **Settings**.

**Customize:** change the feeds (any RSS/Atom feed works) and the quick-pick interests at the top of `src/worker.js`.

## 🗺 Project history

This project started as a local Python script on Windows, then moved to GitHub Actions (scheduled runs), and finally to Cloudflare Workers for true real-time, serverless operation.

## 👤 Author

**Soroush** — Montreal, Canada

## 📄 License

[MIT](LICENSE)
