# ETH/BTC Signal Bot

A Telegram bot that monitors the ETH/BTC trading pair on Kraken and sends entry signals based on moving average crossovers. When a signal is detected, the bot guides you through opening and tracking a short ETH position via an Aave flash loan, monitoring your SL/TP levels and sending daily status reports.

---

## How It Works

The bot checks the ETH/BTC daily chart every hour and detects three entry signals:

- **Below MA50** — ETH/BTC price drops below the 50-day moving average
- **Below MA200** — ETH/BTC price drops below the 200-day moving average
- **Death Cross** — MA50 crosses below MA200

When a signal fires, the bot asks whether you opened a 0.1 ETH loan on Aave. If yes, it collects your entry price, stop-loss %, and take-profit %, then visualizes the outcome and begins actively monitoring the position.

The position logic is based on a **short ETH/BTC trade**:
- ETH/BTC falling = positive P&L (you buy back ETH cheaper than you sold it)
- ETH/BTC rising = negative P&L (you buy back ETH more expensively)

The bot displays P&L from your position's perspective, separate from the raw price movement, so the signs are always intuitive.

---

## Features

- 📡 Hourly market check (MA50, MA200, Death Cross, Golden Cross)
- 💬 Conversational Telegram flow for opening a position
- 📊 SL/TP outcome visualization with visual bars at position open
- 🔔 Instant alerts when SL or TP is hit
- 📈 `/status` command — current position P&L at any time
- 📅 Daily status report at 08:00 (local system time)
- `/close` command — manual position close
- 💾 Position state persists across restarts (`bot_state.json`)
- 📝 Full transaction history saved to `transactions.json`

---

## Project Structure

```
ETHBTC/
├── main.py                 # Main bot script
├── tokens.env              # Your secrets — never commit this
├── tokens.env.example      # Template for required env vars
├── bot_state.json          # Runtime state (auto-generated, gitignored)
├── transactions.json       # Closed position history (auto-generated, gitignored)
├── .gitignore
└── README.md
```

---

## Requirements

- Python 3.10+
- Packages: `requests`, `schedule`, `python-dotenv`

```bash
pip install requests schedule python-dotenv
```

---

## Setup

### 1. Create a Telegram bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the bot token you receive

### 2. Get your chat ID

1. Send any message to your new bot
2. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
3. Find `"chat": {"id": ...}` in the response — that's your chat ID

### 3. Configure environment variables

Copy the example file and fill in your values:

```bash
cp tokens.env.example tokens.env
```

Edit `tokens.env`:

```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

### 4. Run the bot

```bash
python main.py
```

---

## Configuration

All tuneable constants are at the top of `main.py`:

| Constant | Default | Description |
|---|---|---|
| `CHECK_INTERVAL_HOURS` | `1` | How often to check the market |
| `CANDLES_LIMIT` | `250` | Number of daily candles to fetch |
| `LOAN_ETH` | `0.1` | ETH loan size used in P&L calculations |
| `DAILY_REPORT_TIME` | `"08:00"` | Time for the daily status report (local system time) |

---

## Transaction History

Every closed position is appended to `transactions.json`:

```json
{
  "opened_at": "2025-11-01T09:15:00",
  "closed_at": "2025-11-18T08:00:00",
  "entry_price": 0.025340,
  "exit_price": 0.019800,
  "sl_pct": 15.0,
  "tp_pct": 25.0,
  "loan_eth": 0.1,
  "pnl_eth": 0.02801,
  "reason": "tp_hit"
}
```

`reason` is one of: `tp_hit`, `sl_hit`, `manual`.

---

## Deployment

The bot is planned to be deployed on a Raspberry Pi Zero 2 W for 24/7 low-power operation.

---

## Notes

- The bot uses **Kraken's public API** (no API key required) to fetch daily ETH/BTC candles
- `schedule` uses **local system time** — make sure your timezone is set correctly before relying on the 08:00 daily report
- `bot_state.json` and `transactions.json` are excluded from the repository via `.gitignore` — they are generated locally at runtime
- Never commit `tokens.env` — it contains your bot token and chat ID
