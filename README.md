# Disaster Alert Bot: Thailand + Indonesia → Telegram

Checks official government warning feeds every 2 minutes and sends new warnings to your Telegram.

| Country | Source (official) | What it covers |
|---|---|---|
| 🇹🇭 Thailand | TMD CAP feed `tmd.go.th/en/api/xml/CAP` | Heavy rain, storms, strong wind/waves, with severity, area, expiry and instructions |
| 🇹🇭 Thailand | TMD warning bulletins `tmd.go.th/en/api/xml/storm-tracking` | Numbered TMD warnings ("Heavy rain in the South No.9") |
| 🇮🇩 Indonesia | BMKG CAP feed `cuaca.bmkg.go.id/.../cap/feed/en/rss.xml` | Extreme-weather early warnings |
| 🇮🇩 Indonesia | BMKG InaTEWS CAP (Atom) | Earthquake and tsunami warnings |
| 🇮🇩 Indonesia | BMKG open data `data.bmkg.go.id/DataMKG/TEWS/` | Latest earthquake + M5.0+ list, tsunami potential |
| 🌏 Optional | USGS earthquake API | Quakes around Thailand/Myanmar. TMD has no public earthquake API, so USGS fills this gap. It is a US agency, not Thai. |

No API keys are needed for any of these. BMKG asks you to credit BMKG as the source of its data.

## 1. Create your Telegram bot (2 minutes)

1. In Telegram, open **@BotFather** → send `/newbot` → choose a name → copy the **token**.
2. Open your new bot and send it any message (e.g. "hi"). For a family group, add the bot to the group and post a message there.
3. Copy `.env.example` to `.env` and paste the token into `TELEGRAM_BOT_TOKEN`.
4. Run `python3 disaster_alert_bot.py --get-chat-id` and paste the number into `TELEGRAM_CHAT_ID`.
5. Run `python3 disaster_alert_bot.py --test`. You should get a test message.

### Groups: just add the bot

Once the bot is running, add it to any Telegram group. That's the whole setup for that group:

- The bot posts **"Disaster alerts are on"** plus the warnings active right now.
- Every new warning is posted there automatically. Nobody has to type a command.
- When someone **joins the group**, the bot welcomes them with the warnings active right now.
- Remove the bot from the group and it stops posting there.

You don't need to put group ids in `TELEGRAM_CHAT_ID`. Keep your own chat there: "still running" and "source down" messages go only to that chat, not to groups. Anyone can also open a private chat with the bot and press **Start** to get alerts.

If a big group hides join messages, the bot can't see who joined, but alerts still arrive.

## 2. Check it with live data

```bash
python3 disaster_alert_bot.py --dry-run --once
```
This prints what it would send without sending anything. Delete `state.json` afterwards so the real first run starts clean. Subscribed groups are kept in `state.subscribers.json` (and stopped ones in `state.muted.json`), so deleting `state.json` does not forget them.

## 3. Run it all the time (pick one)

**A. Docker (fastest alerts: polls every 2 minutes)**
```bash
docker compose up -d --build
docker compose logs -f
```
Run it on a machine that stays on, like a home server, a VPS or a Raspberry Pi. A laptop only works while it is awake.

**B. GitHub Actions (free, no server, but checks only every ~5 minutes or slower)**
Push this folder to a **private** GitHub repo. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` under *Settings → Secrets and variables → Actions → Secrets*, then optional settings under *Variables*. The workflow in `.github/workflows/` runs by itself.

**C. Plain Python on any machine**
`python3 disaster_alert_bot.py` loops forever. Use `--once` with cron instead if you prefer.

## Settings (in `.env`)

| Setting | Default | Meaning |
|---|---|---|
| `COUNTRIES` | `TH,ID` | Which countries to watch |
| `MIN_SEVERITY` | `Severe` | `Minor` / `Moderate` / `Severe` / `Extreme`. Use `Moderate` if you want more warnings. |
| `AREA_KEYWORDS` | empty | e.g. `Chiang Mai,North,Jakarta`. Only warnings that mention these places are sent. |
| `MIN_QUAKE_MAG` | `5.0` | Smallest earthquake to send |
| `HOME_LAT` / `HOME_LON` | empty | Your location. Adds "≈ N km from you" to quake alerts. |
| `QUAKE_RADIUS_KM` | `0` | Only quakes within this distance (0 = anywhere). Tsunami-potential quakes are **always** sent. |
| `ALERT_LANG` | `en` | `local` = Thai TMD feed / Indonesian BMKG feed |
| `HEARTBEAT_HOURS` | `24` | Sends a "still running" message to `TELEGRAM_CHAT_ID` so you know the bot is alive |
| `AUTO_SUBSCRIBE` | `true` | Groups the bot is added to (and people who press Start) get alerts automatically. `false` = only `TELEGRAM_CHAT_ID` |
| `WELCOME_NEW_MEMBERS` | `true` | Greet people who join a group with the warnings active right now |

## Bot commands

Commands are optional: alerts arrive without them. The bot answers `TELEGRAM_CHAT_ID` and groups it is subscribed to.

| Command | Reply |
|---|---|
| `/weather Chiang Mai` (or just send `Chiang Mai`) | Weather now, the next 3 days, and the official warnings for that province. Accepts Thai and Indonesian province names, English names (`West Java`) and big towns (`Bandung`, `Pattaya`, `Hat Yai`) |
| `/warnings` | Every official warning active right now |
| `/stop` | Stop alerts **and** replies in this chat. In a group only admins can use it. The bot stays in the group but stays silent |
| `/resume` | Turn a stopped chat back on |
| `/chats` (from your own `TELEGRAM_CHAT_ID` chat) | Lists every group getting alerts and every stopped one, with ids. Then `/stop -100…` or `/resume -100…` controls a group without being in it |
| `/country_list_province` | All 77 Thai and 38 Indonesian provinces, grouped by region, with ✅ next to the ones your `AREA_KEYWORDS` match |
| `/country_list_province TH` or `ID` | One country only |
| `/help` | Short help |

Telegram command names can't contain `-`, so the menu shows `/country_list_province`. If you type `/country-list-provience`, that works too. Indonesian provinces are listed by the Indonesian names BMKG uses in its warnings (for example `Jawa Barat`), so copy those into `AREA_KEYWORDS`. Area matching ignores spaces and the Si/Sri spelling difference.

Sending only a province name works in groups only when the bot can read normal messages: make it a group admin, or turn off privacy mode in @BotFather (`/setprivacy` → Disable). `/weather` always works. The forecast comes from [Open-Meteo](https://open-meteo.com) (free model data, not a government source); warnings still come from TMD and BMKG only.

Docker and plain Python answer within seconds. GitHub Actions answers at its next run (about 5 minutes). Preview a reply offline with `python3 disaster_alert_bot.py --command "/country_list_province TH"`.

Note: once the bot is running it marks Telegram messages as read, so `--get-chat-id` only lists chats that message the bot after that.

## How it behaves

- **First run:** sends one summary of the warnings active right now. It does not resend old items one by one.
- **After that:** each new warning or earthquake is sent once. Expired warnings and test messages are skipped.
- **If a source goes down:** the other sources keep working, and you get a Telegram warning after repeated failures.

## Important

This bot is an **extra** layer, not a replacement for official warnings. Feeds can be late or down. Keep these on too:
- 🇹🇭 **T-Alert** cell broadcast on your phone, the **Thai Disaster Alert** app (NDWC), and **EarthquakeTMD** (app + Telegram channel)
- 🇮🇩 The **Info BMKG** app

## Why Telegram and not WhatsApp?

A Telegram bot is free and takes 2 minutes to set up. A WhatsApp bot needs a Meta Business account and the WhatsApp Cloud API. Proactive messages there must use pre-approved templates and can cost money. LINE Notify was shut down in 2025. If you want, you can add a WhatsApp or LINE Messaging API sender to the `Notifier` class later.

## Tests
```bash
python3 -m unittest discover -s tests -v
```
