# 3.25 by SHRA1 — NSE swing-trading desk

Scans every liquid NSE stock each evening for your **down-streak pattern** (how far and how long each stock
usually falls before bouncing), shows the buy zones on a password-protected dashboard, and sends Telegram
alerts for your trades. **It never places orders** — it analyses, suggests, tracks and alerts.

*For analysis only, not investment advice.*

---

## What runs, and when (all automatic once online)

| Job | When (IST) | What it does |
|---|---|---|
| Evening scan | Mon–Fri 18:45 (backup 20:30 if missed) | Full scan → dashboard → new READY stocks + evening summary on Telegram |
| Market-hours monitor | every 15 min, 09:15–15:30 | Alerts for your trades (+3.25 %, every +1 %, −2 %, every −1 %, stop loss), watchlist price alerts, "touched your buy level" |
| Telegram commands | every 30 min outside market hours (incl. weekends) | Replies to /buy, /sell, /positions … (during market hours the monitor replies) |
| Morning global cues | Mon–Fri 08:45 | Global markets → expected NIFTY opening on Telegram + dashboard |
| Weekly refresh | Sunday 06:00 | Stock list, industries, fundamentals, surveillance lists |

GitHub may start a scheduled run a few minutes late. Yahoo prices are ~15 minutes delayed, so alerts are
near-real-time, not tick-by-tick. **Cost: ₹0** (public repository; everything personal is encrypted).

---

## Going live — step by step (first time only, ~20 minutes)

You already have: the Telegram bot (**@stockyshra1bot**), your token and chat ID in `.secrets.env`, and your
password in `.app_password` on your PC. These files are **never uploaded**.

### 1. Create a GitHub account
1. Go to **github.com** → **Sign up** → follow the steps (free plan).
2. Turn on two-factor authentication: profile picture → **Settings** → **Password and authentication**.

### 2. Create the repository
1. Top-right **+** → **New repository**.
2. Name: e.g. `swing-desk` · Visibility: **Public** (free scans; your data is encrypted) · leave everything else unticked.
3. Click **Create repository**. Then Claude uploads the code (you'll be asked to sign in once).

### 3. Add your three secrets (they stay hidden, even in a public repository)
Repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. Add each:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from @BotFather (same as in your `.secrets.env`) |
| `TELEGRAM_CHAT_ID` | your chat ID (in your `.secrets.env`, line `TELEGRAM_CHAT_ID=`) |
| `APP_PASSWORD` | your dashboard password (same as in `.app_password`) |

> Use **exactly the same password** as on your PC, otherwise the encrypted files can't be opened.

### 4. Switch on the dashboard (GitHub Pages)
Repository → **Settings** → **Pages** → under **Build and deployment**, **Source: GitHub Actions**.

### 5. First runs (by hand)
Repository → **Actions** tab:
1. If asked, click **I understand my workflows, go ahead and enable them**.
2. **Weekly refresh** → **Run workflow** → **Run workflow**. The first run downloads 3 years of data for
   ~2,400 stocks: **about 1–1.5 hours**. Later runs take ~15 minutes.
3. When it is green ✅: **Evening scan** → **Run workflow**.
4. **Morning global cues** → **Run workflow**.

### 6. Open the dashboard
Address: **`https://<your-github-username>.github.io/<repository-name>/`**
(also shown under Settings → Pages). Enter your password. On your phone: browser menu → **Add to Home screen**
— it opens like an app with the 3.25 icon.

### 7. Telegram
Send **`/help`** to @stockyshra1bot. Replies come at the next scheduled check (≤ 15 min in market hours,
≤ 30 min otherwise).

### 8. Sync your watchlist & trades from the dashboard (optional, recommended)
So that trades you add on the dashboard (phone or PC) are saved online and alerted:
1. GitHub → profile picture → **Settings** → **Developer settings** → **Personal access tokens** →
   **Fine-grained tokens** → **Generate new token**.
2. Name `dashboard`, expiry 1 year, **Repository access: Only select repositories** → your repository.
3. **Permissions → Repository permissions → Contents: Read and write**. Generate, copy the token.
4. Dashboard → sync chip at top right (or `#/settings`) → paste the token → **Save & connect**.
   It is stored only in that browser; do this once per device. Your data is encrypted before it is sent.

---

## Telegram commands

| Command | Example |
|---|---|
| `/buy SYMBOL PRICE QTY [date] [sl=…] [tgt=…]` | `/buy TCS 3500 10` · `/buy TCS 3500 10 2026-11-10 sl=3420` |
| `/sell SYMBOL PRICE [QTY] [#ID]` | `/sell TCS 3620` · `/sell TCS 3620 5 #3` (part sale) |
| `/positions` | open trades, gain, next alert levels |
| `/status` | market mood + top READY / Getting close |
| `/stock SYMBOL` | one stock's pattern summary |
| `/remove ID` · `/edit ID PRICE QTY` | fix mistakes |
| `/help` | the list |

## What each alert means

| Alert | Meaning |
|---|---|
| 🎯 reached +3.25 % | Your target. Start watching to sell (the tool never sells). |
| 📈 now +4.25 % (+5.25 % …) | Every further +1 %, with the peak since you bought. |
| ⚠ −2 % below your buy | Not following the pattern; includes this stock's usual dip so you can judge. Then every −1 %. If the price recovers above −2 % and falls again, the warnings start again. |
| 🛑 touched your stop loss | Only if you set a stop on the trade. Your decision. |
| 🔔 watchlist alert | Your price alert (above/below) was reached. Fires once. |
| 🟢 touched your buy level | A READY / Getting-close stock reached its buy price during the day (once per day per stock). |
| 🟢 New READY stocks | Evening: stocks that entered the buy zone today, with both buy prices, hit rates and checks. |
| 🛑 buy signals paused | Market STRESSED: no buy alerts; shows how the pattern did in stressed vs normal markets. |
| 🌡 mood changed | NORMAL / CAUTION / STRESSED changed, with reasons. |
| 🌅 morning cues | Expected NIFTY opening from overnight global markets (a statistical hint, not a forecast). |

## Changing settings

All thresholds are in **`config.yaml`**, each with a plain-English comment (target %, alert steps, filters,
pattern rules, market-mood rules, checklist, Telegram). To change one online: open `config.yaml` on GitHub →
pencil icon ✏️ → edit the number → **Commit changes**. It applies from the next run.

## Data limitations (free sources)
- Yahoo prices are ~15 minutes delayed; the evening scan uses final end-of-day data.
- NSE sometimes blocks cloud servers. Delivery %, bulk/block deals, shareholding/pledge and surveillance
  lists may then show **N/A**; the tool keeps going and retries on later runs. Prices, patterns and
  fundamentals come from Yahoo and are not affected.
- Gift Nifty has no free feed and is not shown.
- News is fetched for READY, Getting-close and deep-fall stocks only.
- GitHub can delay scheduled runs by several minutes, and pauses schedules after 60 days without any repository
  activity (the daily state saves count as activity, so this should not happen).

## Troubleshooting a failed run
1. **Actions** tab → click the red ❌ run → click the failed step to read the message.
2. Common causes:
   - *"APP_PASSWORD not set"* → step 3 secrets (names must match exactly).
   - *Telegram messages not arriving* → check `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` secrets; send `/help` to the bot.
   - *Download / HTTP errors* → Yahoo or NSE was busy; the next run usually works. Re-run with **Re-run jobs**.
   - *Dashboard shows "This data is from …"* → the evening scan didn't finish; check its run.
3. A run can always be started by hand: Actions → choose the workflow → **Run workflow**.

## Prefer a private repository? (still free)
Make the repository private and:
- change the Telegram-commands schedule in `.github/workflows/listen.yml` to every 60 min
  (`0 0-2,11-23 * * 1-5` and `0 * * * 0,6`) to stay within the 2,000 free minutes a month;
- host the dashboard on **Cloudflare Pages** (free) instead of GitHub Pages: create a Pages project connected
  to the repository with build command empty and output folder `output/site`, or upload `output/site` from the
  workflow with `cloudflare/wrangler-action` (needs a free Cloudflare API token as a secret).

## Running on your PC (optional)
```
.venv\Scripts\python.exe -m nsepattern serve                 # dashboard at http://127.0.0.1:8765
.venv\Scripts\python.exe -m nsepattern evening --dry-run     # full scan, print alerts instead of sending
.venv\Scripts\python.exe -m nsepattern demo                  # offline demo (synthetic data)
.venv\Scripts\python.exe -m pytest -q                        # automatic tests
```

## Security & privacy
- Secrets live only in GitHub Actions secrets and in `.secrets.env` / `.app_password` on your PC (never uploaded).
- Your trades, watchlist, alerts and the bot's memory are encrypted at rest (AES-256-GCM, key from your
  password with PBKDF2-SHA256, 250,000 rounds). The dashboard data is encrypted the same way and only
  decrypted in your browser after you type the password.
- The bot answers only your own Telegram chat.
