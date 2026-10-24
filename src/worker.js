/**
 * Sheytoon Deal Agent - Cloudflare Worker version 
 *
 * HOW IT WORKS
 *  - fetch():     Telegram "rings the doorbell" (webhook) -> instant replies, buttons.
 *  - scheduled(): Cloudflare's timer wakes the bot every minute -> check deal feeds, send alerts.
 *
 * SETTINGS (Cloudflare dashboard -> your Worker -> Settings)
 *  - TELEGRAM_TOKEN : bot token from BotFather (keep it as a Secret)
 *  - OWNER_ID       : your Telegram chat ID (the owner / admin)
 *  - KV binding DB  : storage for users and seen deals
 *  - Cron trigger   : every minute
 *
 * ONE-TIME LINKS (open in a browser after deploying)
 *  - /setup    -> connects the bot to Telegram, sets the menu and the welcome card
 */

const BOT_NAME = "Sheytoon Deal Agent";
const FEEDS = [
  { url: "https://forums.redflagdeals.com/feed/forum/9", kind: "hot" },       // RFD forum: Hot Deals
  { url: "https://www.redflagdeals.com/rss/deals.php?city=montreal&content_type=editorial", kind: "pick" }, // RFD editors' picks
];
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36";

// Popular interests shown as one-tap buttons: [emoji, label, keyword]
const PICKS = [
  ["🎧", "AirPods", "airpods"], ["📱", "iPhone", "iphone"],
  ["💻", "Laptop", "laptop"], ["🎮", "PS5", "ps5"],
  ["📺", "TV", "tv"], ["👟", "Nike", "nike"],
  ["🏃", "Running shoes", "running shoes"], ["🛒", "Costco", "costco"],
  ["🔵", "Best Buy", "best buy"], ["🏬", "Walmart", "walmart"],
  ["✈️", "Flights", "flight"], ["🎁", "Freebies", "free"],
];

// ---------------------------------------------------------------- texts

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const T = {
  welcomeNew: (name) =>
    `👋 <b>Hey ${esc(name)}, welcome to ${BOT_NAME}!</b> 😈\n\n` +
    `I'm your personal <b>deal hunter</b> for Canada 🇨🇦\n` +
    `I watch <b>RedFlagDeals</b> 24/7 and ping you the <b>moment</b> a deal matches what <i>you</i> love.\n\n` +
    `✨ <b>How it works</b>\n` +
    `1️⃣  Pick your interests — AirPods, Nike, PS5, Costco…\n` +
    `2️⃣  I check new deals <b>every minute</b>\n` +
    `3️⃣  You get an instant alert with the link 🔥\n\n` +
    `🔐 <i>This bot is invite-only.</i> Your request was sent to the owner — ` +
    `I'll message you as soon as you're approved. ⏳`,
  stillPending: "⏳ <b>Almost there!</b>\nYour request is still waiting for the owner's approval. I'll message you the moment you're in. 🙌",
  approved: (name) =>
    `🎉 <b>You're in, ${esc(name)}!</b>\n\n` +
    `Let's set up your <b>deal radar</b> 📡\n` +
    `• Tap <b>⭐ Quick picks</b> to choose popular interests\n` +
    `• Or tap <b>➕ Add interest</b> and type anything — a product, brand or store\n\n` +
    `<i>Tip: you can also just send me a word, like</i> <code>sony</code>`,
  menu: (u) =>
    `😈 <b>${BOT_NAME}</b>\n\n` +
    `📡 Status: ${u.status === "stopped" ? "⏸ <b>Paused</b>" : "🟢 <b>Hunting deals</b>"}\n` +
    `🎯 Following: <b>${u.keywords.length}</b> interest${u.keywords.length === 1 ? "" : "s"}\n\n` +
    `What would you like to do?`,
  picks: "⭐ <b>Quick picks</b>\nTap to turn interests <b>on ✅ / off</b>:",
  askAdd: "✍️ <b>Type what you want to follow</b>\nA product, brand or store — e.g. <code>ps5</code>, <code>nike</code>, <code>costco</code>",
  list: (u) =>
    `📋 <b>Your interests</b>\n` +
    (u.keywords.length ? u.keywords.map((w) => `• ${esc(w)}`).join("\n") : "<i>None yet — add some to start getting alerts!</i>") +
    `\n\n🚫 <b>Ignored words:</b> ${u.exclude.length ? u.exclude.map(esc).join(", ") : "<i>none</i>"}\n\n` +
    (u.keywords.length ? "<i>Tap ❌ to remove an interest.</i>" : ""),
  help:
    `❓ <b>How to use ${BOT_NAME}</b>\n\n` +
    `<b>Buttons</b> — tap /menu anytime to open the main menu.\n\n` +
    `<b>Commands</b> (if you prefer typing)\n` +
    `/add <i>word</i> — follow an interest\n` +
    `/remove <i>word</i> — stop following it\n` +
    `/list — your interests\n` +
    `/block <i>word</i> — ignore deals with this word\n` +
    `/unblock <i>word</i> — stop ignoring it\n` +
    `/test <i>word</i> — search current deals now\n` +
    `/stop · /start — pause or resume alerts\n` +
    `/deleteme — erase all your data\n\n` +
    `💡 <b>Tip:</b> deal titles use <b>brand and store names</b> — add those for more alerts (sony, asics, costco…).`,
  ownerHelp:
    `\n\n👑 <b>Owner</b>\n/users — everyone and their status\n/approve_ID · /deny_ID · /kick_ID`,
  privacy:
    `🔐 <b>Your privacy</b>\n\n` +
    `I only save:\n• your Telegram name and @username\n• your interests and ignored words\n\n` +
    `No phone number, email or location. Only the bot owner can see the user list.\n` +
    `You can erase everything anytime with /deleteme.`,
};

// ---------------------------------------------------------------- keyboards

const btn = (text, data) => ({ text, callback_data: data });
const KB = {
  menu: (u) => ({
    inline_keyboard: [
      [btn("⭐ Quick picks", "picks"), btn("➕ Add interest", "add")],
      [btn("📋 My interests", "list"), btn("🧪 Test now", "test")],
      [btn(u.status === "stopped" ? "▶️ Resume alerts" : "⏸ Pause alerts", "toggle"), btn("❓ Help", "help")],
    ],
  }),
  picks: (u) => {
    const rows = [];
    for (let i = 0; i < PICKS.length; i += 2) {
      rows.push(PICKS.slice(i, i + 2).map(([e, label, kw], j) =>
        btn(`${u.keywords.includes(kw) ? "✅" : e} ${label}`, `pk:${i + j}`)));
    }
    rows.push([btn("➕ Add my own", "add"), btn("✔️ Done", "menu")]);
    return { inline_keyboard: rows };
  },
  list: (u) => {
    const rows = [];
    const kws = u.keywords.slice(0, 30);
    for (let i = 0; i < kws.length; i += 2) {
      rows.push(kws.slice(i, i + 2).map((w, j) => btn(`❌ ${w}`.slice(0, 40), `rm:${i + j}`)));
    }
    rows.push([btn("➕ Add", "add"), btn("⭐ Quick picks", "picks"), btn("⬅️ Menu", "menu")]);
    return { inline_keyboard: rows };
  },
  back: { inline_keyboard: [[btn("⬅️ Menu", "menu")]] },
  pending: { inline_keyboard: [[btn("🔐 What data do you keep?", "privacy")]] },
  approve: (cid) => ({ inline_keyboard: [[btn("✅ Approve", `ap:${cid}`), btn("🚫 Deny", `dn:${cid}`)]] }),
  confirmDelete: { inline_keyboard: [[btn("🗑 Yes, delete my data", "delyes"), btn("Cancel", "menu")]] },
};

// ---------------------------------------------------------------- helpers

async function sha256hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
async function webhookSecret(env) {
  return (await sha256hex("webhook:" + env.TELEGRAM_TOKEN)).slice(0, 48);
}

async function tg(env, method, body) {
  try {
    const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${method}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!j.ok && !/message is not modified/.test(j.description || "")) console.log(method, "error:", j.description);
    return j;
  } catch (e) {
    console.log(method, "error", e);
    return { ok: false };
  }
}
const send = (env, chatId, text, extra = {}) =>
  tg(env, "sendMessage", { chat_id: chatId, text, parse_mode: "HTML", link_preview_options: { is_disabled: true }, ...extra });
const edit = (env, chatId, messageId, text, markup) =>
  tg(env, "editMessageText", { chat_id: chatId, message_id: messageId, text, parse_mode: "HTML",
    link_preview_options: { is_disabled: true }, reply_markup: markup });

function unescapeHtml(s) {
  for (let i = 0; i < 2; i++) {
    s = s.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
      .replace(/&quot;/g, '"').replace(/&#0?39;|&apos;/g, "'")
      .replace(/&lt;/g, "<").replace(/&gt;/g, ">")
      .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n))
      .replace(/&amp;/g, "&");
  }
  return s.trim();
}
const escapeRegex = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
function containsAny(text, words) {
  const t = text.toLowerCase();
  return words.some((w) => new RegExp("\\b" + escapeRegex(w.toLowerCase()) + "\\b").test(t));
}
function wants(user, title, words) {
  words = words ?? user.keywords;
  if (!words || !words.length) return false;
  if (user.exclude?.length && containsAny(title, user.exclude)) return false;
  return containsAny(title, words);
}
const newUser = (name, status) => ({ name, status, keywords: [], exclude: ["expired", "dead"] });
function displayName(from = {}) {
  let n = `${from.first_name || ""} ${from.last_name || ""}`.trim() || "Someone";
  if (from.username) n += ` (@${from.username})`;
  return n;
}
const firstName = (u) => (u.name || "friend").split(" (")[0].split(" ")[0];

// A nice-looking deal card with a "View deal" button and a link preview (image).
function dealCard(p) {
  let title = p.title, store = "";
  const m = title.match(/^\[([^\]]+)\]\s*(.*)$/);
  if (m) { store = m[1]; title = m[2]; }
  const price = (title.match(/\$\s?\d[\d,]*(?:\.\d{1,2})?/) || [])[0];
  const head = p.kind === "hot" ? "🔥 <b>HOT DEAL</b>" : "📰 <b>RFD PICK</b>";
  const text = `${head}${store ? "  ·  🏷 " + esc(store) : ""}\n\n<b>${esc(title)}</b>` + (price ? `\n\n💰 <b>${esc(price)}</b>` : "");
  return {
    text,
    extra: {
      reply_markup: { inline_keyboard: [[{ text: "🔗 View deal", url: p.link }]] },
      link_preview_options: { url: p.link, prefer_small_media: true, show_above_text: false },
    },
  };
}
const sendDeal = (env, cid, p) => { const c = dealCard(p); return send(env, cid, c.text, c.extra); };

// ---------------------------------------------------------------- storage (KV)

async function loadUsers(env) {
  return (await env.DB.get("users", "json")) || { [String(env.OWNER_ID)]: newUser("Owner", "approved") };
}
const saveUsers = (env, users) => env.DB.put("users", JSON.stringify(users));
const loadState = async (env) => (await env.DB.get("state", "json")) || { seen: [], feeds: [] };
const saveState = (env, state) => { state.seen = state.seen.slice(-2000); return env.DB.put("state", JSON.stringify(state)); };

// ---------------------------------------------------------------- feeds

function parseFeed(xml) {
  const posts = [];
  for (const b of xml.match(/<(item|entry)\b[\s\S]*?<\/\1>/g) || []) {
    const title = unescapeHtml((b.match(/<title[^>]*>([\s\S]*?)<\/title>/) || [])[1] || "");
    const link = unescapeHtml((b.match(/<link[^>]*href="([^"]+)"/) || [])[1] || (b.match(/<link>([\s\S]*?)<\/link>/) || [])[1] || "");
    const id = unescapeHtml((b.match(/<(?:id|guid)[^>]*>([\s\S]*?)<\/(?:id|guid)>/) || [])[1] || link);
    if (title) posts.push({ id, title, link });
  }
  return posts;
}
async function fetchPosts() {
  const all = [];
  await Promise.all(FEEDS.map(async (f) => {
    try {
      const r = await fetch(f.url, { headers: { "User-Agent": UA, Accept: "application/rss+xml, application/xml, */*" } });
      if (!r.ok) throw new Error("HTTP " + r.status);
      for (const p of parseFeed(await r.text())) all.push({ ...p, kind: f.kind, feed: f.url });
    } catch (e) { console.log("Could not read", f.url, String(e)); }
  }));
  return all;
}

async function runTest(env, cid, user, word) {
  const posts = await fetchPosts();
  const words = word ? [word] : user.keywords;
  const label = word ? `“${esc(word)}”` : "your interests";
  if (!words.length) return send(env, cid, "🎯 Add some interests first, then I can search for you!", { reply_markup: KB.menu(user) });
  const hits = posts.filter((p) => wants(user, p.title, words));
  if (!hits.length) {
    return send(env, cid, `🧪 <b>Test search</b>\nI checked <b>${posts.length}</b> current deals — nothing matches ${label} right now.\n\n<i>Don't worry, I'll ping you the moment one appears!</i> 📡`, { reply_markup: KB.back });
  }
  await send(env, cid, `🧪 <b>Test search</b> — ${hits.length} current deal${hits.length > 1 ? "s" : ""} match${hits.length > 1 ? "" : "es"} ${label}:`);
  for (const p of hits.slice(0, 10)) await sendDeal(env, cid, p);
}

// ---------------------------------------------------------------- messages (commands & text)

async function handleMessage(env, msg) {
  if (msg?.chat?.type !== "private") return;
  const cid = String(msg.chat.id);
  const owner = String(env.OWNER_ID);
  const text = (msg.text || "").trim();
  if (!text) return;
  const isCmd = text.startsWith("/");
  const sp = text.indexOf(" ");
  const cmd = isCmd ? (sp < 0 ? text : text.slice(0, sp)).split("@")[0].toLowerCase() : "";
  const arg = (isCmd ? (sp < 0 ? "" : text.slice(sp + 1)) : text).trim().toLowerCase();

  const users = await loadUsers(env);
  const user = users[cid];

  // ---- new or not-yet-approved people ----
  if (!user || user.status === "pending" || user.status === "denied") {
    if (!user && cmd === "/start") {
      users[cid] = newUser(displayName(msg.from), "pending");
      await saveUsers(env, users);
      await send(env, cid, T.welcomeNew(msg.from?.first_name || "there"), { reply_markup: KB.pending });
      await send(env, owner, `🆕 <b>New join request</b>\n\n👤 ${esc(users[cid].name)}\n🆔 <code>${cid}</code>`, { reply_markup: KB.approve(cid) });
    } else if (user?.status === "pending") {
      await send(env, cid, T.stillPending, { reply_markup: KB.pending });
    }
    return;
  }

  // ---- owner-only commands ----
  if (cid === owner) {
    const m = cmd.match(/^\/(approve|deny|kick)_(-?\d+)$/);
    if (m) return ownerAction(env, users, m[1], m[2]);
    if (cmd === "/users") {
      const lines = Object.entries(users).map(([k, v]) =>
        `${k === owner ? "👑" : v.status === "pending" ? "⏳" : v.status === "approved" ? "🟢" : v.status === "stopped" ? "⏸" : "🚫"} ` +
        `<b>${esc(v.name)}</b> — ${v.keywords.length} interests` +
        (k !== owner ? `\n     /kick_${k}` : "") + (v.status === "pending" ? `  /approve_${k}` : ""));
      return send(env, owner, `👥 <b>Users (${lines.length})</b>\n\n` + lines.join("\n"));
    }
  }

  // ---- approved users ----
  const kw = user.keywords, ex = user.exclude;
  let changed = false;
  const save = async () => { if (changed) await saveUsers(env, users); };

  // Plain text (no slash) = add it as an interest. Friendly and fast.
  if (!isCmd || cmd === "/add") {
    if (!arg) return send(env, cid, T.askAdd, { reply_markup: { force_reply: true, input_field_placeholder: "e.g. ps5, nike, costco" } });
    const words = arg.split(",").map((w) => w.trim()).filter(Boolean).slice(0, 10);
    const added = words.filter((w) => !kw.includes(w) && w.length <= 40);
    added.forEach((w) => kw.push(w));
    changed = added.length > 0;
    await save();
    return send(env, cid,
      added.length ? `✅ <b>Added:</b> ${added.map(esc).join(", ")}\n🎯 You now follow <b>${kw.length}</b> interests. I'll ping you when a deal shows up!`
                   : `👍 You already follow <b>${words.map(esc).join(", ")}</b>.`,
      { reply_markup: KB.menu(user) });
  }

  switch (cmd) {
    case "/start":
      if (user.status === "stopped") { user.status = "approved"; changed = true; }
      await save();
      return send(env, cid, T.menu(user), { reply_markup: KB.menu(user) });
    case "/menu":
      return send(env, cid, T.menu(user), { reply_markup: KB.menu(user) });
    case "/stop":
      user.status = "stopped"; changed = true; await save();
      return send(env, cid, "⏸ <b>Alerts paused.</b>\nTap ▶️ <b>Resume alerts</b> or send /start when you want them back.", { reply_markup: KB.menu(user) });
    case "/remove":
      if (arg && kw.includes(arg)) { kw.splice(kw.indexOf(arg), 1); changed = true; await save(); return send(env, cid, `🗑 Removed <b>${esc(arg)}</b>.`, { reply_markup: KB.list(user) }); }
      return send(env, cid, T.list(user), { reply_markup: KB.list(user) });
    case "/list":
      return send(env, cid, T.list(user), { reply_markup: KB.list(user) });
    case "/block":
      if (arg && !ex.includes(arg)) { ex.push(arg); changed = true; await save(); }
      return send(env, cid, arg ? `🚫 Deals with <b>${esc(arg)}</b> will be ignored.` : "Send <code>/block word</code>, e.g. <code>/block refurbished</code>");
    case "/unblock":
      if (arg && ex.includes(arg)) { ex.splice(ex.indexOf(arg), 1); changed = true; await save(); }
      return send(env, cid, arg ? `👍 <b>${esc(arg)}</b> is no longer ignored.` : "Send <code>/unblock word</code>");
    case "/test":
      return runTest(env, cid, user, arg);
    case "/deleteme":
      if (cid === owner) return send(env, cid, "👑 You're the owner, so your data stays. Use /list to clear interests.");
      return send(env, cid, "⚠️ <b>Delete all your data?</b>\nThis removes your interests and your access to the bot.", { reply_markup: KB.confirmDelete });
    case "/privacy":
      return send(env, cid, T.privacy, { reply_markup: KB.back });
    default:
      return send(env, cid, T.help + (cid === owner ? T.ownerHelp : ""), { reply_markup: KB.back });
  }
}

async function ownerAction(env, users, action, target, editRef) {
  const owner = String(env.OWNER_ID);
  const t = users[target];
  let note;
  if (!t) note = "⚠️ That user was not found.";
  else if (action === "approve") {
    t.status = "approved";
    await saveUsers(env, users);
    await send(env, target, T.approved(firstName(t)), { reply_markup: KB.menu(t) });
    note = `✅ <b>Approved</b> ${esc(t.name)}`;
  } else if (action === "deny") {
    t.status = "denied"; await saveUsers(env, users);
    note = `🚫 <b>Denied</b> ${esc(t.name)}`;
  } else if (action === "kick" && target !== owner) {
    delete users[target]; await saveUsers(env, users);
    note = `👋 <b>Removed</b> ${esc(t.name)}`;
  }
  if (!note) return;
  return editRef ? edit(env, owner, editRef, note) : send(env, owner, note);
}

// ---------------------------------------------------------------- button taps

async function handleCallback(env, cq) {
  const cid = String(cq.message?.chat?.id);
  const mid = cq.message?.message_id;
  const data = cq.data || "";
  const owner = String(env.OWNER_ID);
  let toast = "";
  const users = await loadUsers(env);
  const user = users[cid];

  try {
    if (cid === owner && /^(ap|dn):/.test(data)) {
      const [a, target] = data.split(":");
      await ownerAction(env, users, a === "ap" ? "approve" : "deny", target, mid);
      toast = a === "ap" ? "Approved ✅" : "Denied";
      return;
    }
    if (data === "privacy") { await send(env, cid, T.privacy); return; }
    if (!user || user.status === "pending" || user.status === "denied") { toast = "⏳ Waiting for approval"; return; }

    const kw = user.keywords;
    if (data === "menu") await edit(env, cid, mid, T.menu(user), KB.menu(user));
    else if (data === "picks") await edit(env, cid, mid, T.picks, KB.picks(user));
    else if (data.startsWith("pk:")) {
      const p = PICKS[+data.slice(3)];
      if (p) {
        const i = kw.indexOf(p[2]);
        if (i >= 0) { kw.splice(i, 1); toast = `Removed ${p[1]}`; } else { kw.push(p[2]); toast = `✅ ${p[1]} added`; }
        await saveUsers(env, users);
        await tg(env, "editMessageReplyMarkup", { chat_id: cid, message_id: mid, reply_markup: KB.picks(user) });
      }
    } else if (data === "list") await edit(env, cid, mid, T.list(user), KB.list(user));
    else if (data.startsWith("rm:")) {
      const w = kw[+data.slice(3)];
      if (w) { kw.splice(kw.indexOf(w), 1); toast = `Removed ${w}`; await saveUsers(env, users); }
      await edit(env, cid, mid, T.list(user), KB.list(user));
    } else if (data === "add") {
      await send(env, cid, T.askAdd, { reply_markup: { force_reply: true, input_field_placeholder: "e.g. ps5, nike, costco" } });
    } else if (data === "test") {
      toast = "🔎 Searching current deals…";
      await tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: toast });
      toast = null;
      await runTest(env, cid, user, "");
    } else if (data === "toggle") {
      user.status = user.status === "stopped" ? "approved" : "stopped";
      toast = user.status === "stopped" ? "⏸ Alerts paused" : "▶️ Alerts on";
      await saveUsers(env, users);
      await edit(env, cid, mid, T.menu(user), KB.menu(user));
    } else if (data === "help") await edit(env, cid, mid, T.help + (cid === owner ? T.ownerHelp : ""), KB.back);
    else if (data === "delyes" && cid !== owner) {
      delete users[cid]; await saveUsers(env, users);
      await edit(env, cid, mid, "🗑 <b>All your data was deleted.</b>\nThanks for trying " + BOT_NAME + "! Send /start anytime to come back. 👋");
    }
  } finally {
    if (toast !== null) await tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: toast || undefined });
  }
}

// ---------------------------------------------------------------- timer (every minute)

async function checkDeals(env) {
  const [state, users, posts] = await Promise.all([loadState(env), loadUsers(env), fetchPosts()]);
  const seen = new Set(state.seen);
  const known = new Set(state.feeds || []);
  const fresh = [];
  let stateChanged = false;
  for (const p of posts) {
    if (!p.id || seen.has(p.id)) continue;
    seen.add(p.id); state.seen.push(p.id); stateChanged = true;
    if (known.has(p.feed)) fresh.push(p); // a brand-new feed is only remembered, not sent
  }
  for (const f of new Set(posts.map((p) => p.feed))) if (!known.has(f)) { known.add(f); stateChanged = true; }
  state.feeds = [...known];
  let sent = 0;
  for (const p of fresh)
    for (const [cid, u] of Object.entries(users))
      if (u.status === "approved" && wants(u, p.title)) { await sendDeal(env, cid, p); sent++; }
  if (stateChanged) await saveState(env, state); // only write when something changed (free plan limit)
  console.log(`Read ${posts.length} posts, ${fresh.length} new, ${sent} alert(s) sent.`);
}

// ---------------------------------------------------------------- setup

async function setup(env, origin) {
  const results = [];
  const hook = await tg(env, "setWebhook", {
    url: `${origin}/telegram`, secret_token: await webhookSecret(env), allowed_updates: ["message", "callback_query"],
  });
  results.push(hook.ok ? "✅ Connected to Telegram (instant replies + buttons)" : "❌ Webhook: " + hook.description);
  const cmds = await tg(env, "setMyCommands", { commands: [
    { command: "menu", description: "🏠 Main menu" },
    { command: "add", description: "➕ Follow an interest" },
    { command: "list", description: "📋 My interests" },
    { command: "test", description: "🧪 Search current deals now" },
    { command: "stop", description: "⏸ Pause alerts" },
    { command: "help", description: "❓ How to use the bot" },
    { command: "privacy", description: "🔐 What data is kept" },
    { command: "deleteme", description: "🗑 Erase my data" },
  ] });
  results.push(cmds.ok ? "✅ Command menu set" : "❌ Commands: " + cmds.description);
  const d = await tg(env, "setMyDescription", { description:
    `😈 ${BOT_NAME} — your personal deal hunter for Canada 🇨🇦\n\n` +
    `🔥 Watches RedFlagDeals 24/7\n⚡ Instant alerts for the things YOU love\n` +
    `🎯 AirPods, Nike, PS5, Costco… you choose\n🔐 Invite-only & private\n\nTap START to join! 👇` });
  results.push(d.ok ? "✅ Welcome card (before Start) set" : "❌ Description: " + d.description);
  const sd = await tg(env, "setMyShortDescription", { short_description:
    "Your personal Canadian deal hunter 🔥 Instant RedFlagDeals alerts for the things you love." });
  results.push(sd.ok ? "✅ Profile description set" : "❌ Short description: " + sd.description);
  return results.join("\n");
}

// ---------------------------------------------------------------- entry points

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/telegram") {
      if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== (await webhookSecret(env))) {
        return new Response("forbidden", { status: 403 }); // not from Telegram
      }
      const update = await request.json();
      const job = update.callback_query ? handleCallback(env, update.callback_query) : handleMessage(env, update.message);
      ctx.waitUntil(job.catch((e) => console.log("Error", e.stack || e)));
      return new Response("ok");
    }
    if (url.pathname === "/setup") return new Response(await setup(env, url.origin));
    return new Response(`${BOT_NAME} is running. 🤖`);
  },
  async scheduled(event, env, ctx) {
    ctx.waitUntil(checkDeals(env));
  },
};
