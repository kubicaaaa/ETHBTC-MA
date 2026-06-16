import time
import json
import requests
import schedule
import logging
import os
from dotenv import load_dotenv
from datetime import datetime

# ─── KONFIGURACJA ────────────────────────────────────────────────────────────

load_dotenv()  # wczytuje zmienne z pliku .env

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]

CHECK_INTERVAL_HOURS = 1
CANDLES_LIMIT        = 250
LOAN_ETH             = 0.1
STATE_FILE           = "bot_state.json"
TRANSACTIONS_FILE    = "transactions.json"
DAILY_REPORT_TIME    = "08:00"

# ─── LOGGING ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── STAN ────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "stage":        "idle",
        "last_signals": [],
        "position": {
            "entry":     None,
            "sl_pct":    None,
            "tp_pct":    None,
            "opened_at": None,
        },
        "last_update_id": 0,
    }

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

state = load_state()

# ─── TRANSAKCJE ──────────────────────────────────────────────────────────────

def load_transactions() -> list:
    if os.path.exists(TRANSACTIONS_FILE):
        try:
            with open(TRANSACTIONS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_transaction(exit_price: float | None, reason: str):
    """Zapisuje zamkniętą pozycję do transactions.json."""
    pos          = state["position"]
    transactions = load_transactions()

    entry     = pos["entry"]
    sl_pct    = pos["sl_pct"]
    tp_pct    = pos["tp_pct"]
    opened_at = pos["opened_at"]
    closed_at = datetime.now().isoformat()

    # Oblicz wynik jeśli znamy kurs wyjścia
    pnl_eth = None
    if entry and exit_price:
        btc_held  = LOAN_ETH * entry
        eth_out   = btc_held / exit_price
        pnl_eth   = round(eth_out - LOAN_ETH, 6)

    transaction = {
        "opened_at":   opened_at,
        "closed_at":   closed_at,
        "entry_price": entry,
        "exit_price":  exit_price,
        "sl_pct":      sl_pct,
        "tp_pct":      tp_pct,
        "loan_eth":    LOAN_ETH,
        "pnl_eth":     pnl_eth,
        "reason":      reason,   # "sl_hit" | "tp_hit" | "manual"
    }

    transactions.append(transaction)

    with open(TRANSACTIONS_FILE, "w") as f:
        json.dump(transactions, f, indent=2)

    log.info(f"Transakcja zapisana: {reason} | P&L: {pnl_eth} ETH")

# ─── DANE RYNKOWE ────────────────────────────────────────────────────────────

def get_ethbtc_closes(limit: int = 250) -> list[float]:
    since  = int(time.time()) - (limit * 86400)
    url    = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": "ETHXBT", "interval": 1440, "since": since}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            log.error(f"Kraken error: {data['error']}")
            return []
        ohlc = list(data["result"].values())[0]
        return [float(c[4]) for c in ohlc]
    except Exception as e:
        log.error(f"Błąd pobierania danych: {e}")
        return []

def moving_average(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period

# ─── TELEGRAM SEND ───────────────────────────────────────────────────────────

def send(text: str, keyboard: list[list[str]] | None = None) -> bool:
    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "Markdown",
    }
    if keyboard:
        payload["reply_markup"] = {
            "keyboard":          [[{"text": btn} for btn in row] for row in keyboard],
            "one_time_keyboard": True,
            "resize_keyboard":   True,
        }
    else:
        payload["reply_markup"] = {"remove_keyboard": True}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Telegram send error: {e}")
        return False

# ─── TELEGRAM RECEIVE (long polling) ─────────────────────────────────────────

def get_updates() -> list[dict]:
    url    = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"offset": state["last_update_id"] + 1, "timeout": 5}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        updates = resp.json().get("result", [])
        if updates:
            state["last_update_id"] = updates[-1]["update_id"]
            save_state(state)
        return updates
    except Exception as e:
        log.error(f"Telegram getUpdates error: {e}")
        return []

def get_user_message() -> str | None:
    for update in get_updates():
        msg = update.get("message", {})
        if str(msg.get("chat", {}).get("id", "")) == str(TELEGRAM_CHAT_ID):
            return msg.get("text", "").strip()
    return None

# ─── WIZUALIZACJA OUTCOME ────────────────────────────────────────────────────

def build_outcome_message(entry: float, sl_pct: float, tp_pct: float) -> str:
    loan = LOAN_ETH

    tp_rate   = entry * (1 - tp_pct / 100)
    btc_held  = loan * entry
    eth_at_tp = btc_held / tp_rate
    profit_tp = eth_at_tp - loan

    sl_rate   = entry * (1 + sl_pct / 100)
    eth_at_sl = btc_held / sl_rate
    loss_sl   = loan - eth_at_sl

    def bar(value: float, max_val: float, width: int = 10, positive: bool = True) -> str:
        filled = min(round(abs(value) / max_val * width), width)
        char   = "█" if positive else "▓"
        return char * filled + "░" * (width - filled)

    max_val = max(profit_tp, loss_sl, 0.001)

    return (
        f"📊 *Wizualizacja pozycji* (pożyczka: {loan} ETH)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📍 *Kurs wejścia:* `{entry:.6f}` ETH/BTC\n\n"
        f"✅ *TAKE PROFIT ({tp_pct}% spadek ETH/BTC)*\n"
        f"   Kurs zamknięcia: `{tp_rate:.6f}`\n"
        f"   Odkupujesz: `{eth_at_tp:.5f}` ETH\n"
        f"   Spłacasz: `{loan}` ETH\n"
        f"   Zysk: `+{profit_tp:.5f}` ETH\n"
        f"   {bar(profit_tp, max_val)} +{profit_tp:.5f} ETH\n\n"
        f"🔴 *STOP LOSS ({sl_pct}% wzrost ETH/BTC)*\n"
        f"   Kurs zamknięcia: `{sl_rate:.6f}`\n"
        f"   Odkupujesz: `{eth_at_sl:.5f}` ETH\n"
        f"   Spłacasz: `{loan}` ETH\n"
        f"   Strata: `-{loss_sl:.5f}` ETH\n"
        f"   {bar(loss_sl, max_val, positive=False)} -{loss_sl:.5f} ETH\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 Stosunek zysk/strata: `{profit_tp/loss_sl:.2f}:1`\n"
        f"_Bot będzie monitorował pozycję i powiadomi przy osiągnięciu SL lub TP._"
    )

# ─── P&L POMOCNICZE ──────────────────────────────────────────────────────────

def calc_pnl(entry: float, current_price: float) -> tuple[float, float]:
    """
    Zwraca (pnl_pct, pnl_eth) z perspektywy pozycji short ETH/BTC.
    ETH/BTC spada  → pnl_pct dodatni  → zarabiamy.
    ETH/BTC rośnie → pnl_pct ujemny   → tracimy.
    """
    btc_held  = LOAN_ETH * entry
    eth_out   = btc_held / current_price
    pnl_eth   = eth_out - LOAN_ETH
    pnl_pct   = (entry - current_price) / entry * 100   # odwrócony znak względem ruchu kursu
    return pnl_pct, pnl_eth

# ─── MONITOROWANIE AKTYWNEJ POZYCJI ─────────────────────────────────────────

def check_position(current_price: float):
    pos = state["position"]
    if not pos["entry"]:
        return

    entry  = pos["entry"]
    sl_pct = pos["sl_pct"]
    tp_pct = pos["tp_pct"]

    # Zmiana kursu ETH/BTC względem wejścia (+ = kurs rośnie = źle dla nas)
    price_change_pct = (current_price - entry) / entry * 100

    if price_change_pct >= sl_pct:
        save_transaction(current_price, "sl_hit")
        send(
            f"🚨 *STOP LOSS OSIĄGNIĘTY!*\n\n"
            f"Kurs wejścia: `{entry:.6f}`\n"
            f"Kurs obecny:  `{current_price:.6f}`\n"
            f"Kurs ETH/BTC wzrósł o `+{price_change_pct:.1f}%` → P&L: `-{price_change_pct:.1f}%`\n\n"
            f"💡 Kup ETH za WBTC na DEX → spłać {LOAN_ETH} ETH na Aave → zamknij pozycję."
        )
        reset_position("sl_hit")

    elif price_change_pct <= -tp_pct:
        pnl_pct, pnl_eth = calc_pnl(entry, current_price)
        save_transaction(current_price, "tp_hit")
        send(
            f"🎯 *TAKE PROFIT OSIĄGNIĘTY!*\n\n"
            f"Kurs wejścia: `{entry:.6f}`\n"
            f"Kurs obecny:  `{current_price:.6f}`\n"
            f"Kurs ETH/BTC spadł o `{price_change_pct:.1f}%` → P&L: `+{pnl_pct:.1f}%`\n\n"
            f"Odkupujesz: `{LOAN_ETH + pnl_eth:.5f}` ETH\n"
            f"Zysk: `+{pnl_eth:.5f}` ETH\n\n"
            f"💡 Kup ETH za WBTC na DEX → spłać {LOAN_ETH} ETH na Aave → zgarnij zysk."
        )
        reset_position("tp_hit")

    else:
        pnl_pct, pnl_eth = calc_pnl(entry, current_price)
        log.info(
            f"Pozycja aktywna | ETH/BTC={current_price:.6f} | "
            f"kurs: {price_change_pct:+.2f}% | P&L: {pnl_pct:+.2f}% ({pnl_eth:+.5f} ETH)"
        )

def reset_position(reason: str):
    state["stage"]    = "idle"
    state["position"] = {"entry": None, "sl_pct": None, "tp_pct": None, "opened_at": None}
    save_state(state)
    log.info(f"Pozycja zamknięta: {reason}")

# ─── DZIENNY RAPORT / STATUS ──────────────────────────────────────────────────

def send_status_report():
    """Wysyła raport statusu — wywoływane przez /status oraz codziennie o 8:00."""
    closes = get_ethbtc_closes(CANDLES_LIMIT)
    price  = closes[-1] if closes else None
    pos    = state["position"]

    if state["stage"] == "active" and pos["entry"] and price:
        price_change_pct = (price - pos["entry"]) / pos["entry"] * 100
        pnl_pct, pnl_eth = calc_pnl(pos["entry"], price)

        # Emoji zależne od P&L
        pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"

        send(
            f"📈 *Status pozycji*\n\n"
            f"Kurs wejścia: `{pos['entry']:.6f}`\n"
            f"Kurs obecny:  `{price:.6f}`\n"
            f"Zmiana kursu ETH/BTC: `{price_change_pct:+.2f}%`\n"
            f"{pnl_emoji} P&L pozycji: `{pnl_pct:+.2f}%` (`{pnl_eth:+.5f}` ETH)\n\n"
            f"SL przy: `+{pos['sl_pct']}%` kursu → P&L `-{pos['sl_pct']}%`\n"
            f"TP przy: `-{pos['tp_pct']}%` kursu → P&L `+{pos['tp_pct']}%`\n\n"
            f"Otwarto: {pos['opened_at'][:16]}"
        )
    else:
        if price:
            send(f"ℹ️ Brak aktywnej pozycji.\nETH/BTC: `{price:.6f}`")
        else:
            send("ℹ️ Brak aktywnej pozycji.")

def daily_status_job():
    log.info("Wysyłanie dziennego raportu statusu (8:00)...")
    send_status_report()

# ─── OBSŁUGA WIADOMOŚCI OD UŻYTKOWNIKA ───────────────────────────────────────

def handle_user_input():
    msg = get_user_message()
    if not msg:
        return

    log.info(f"Wiadomość od użytkownika: '{msg}' | stage: {state['stage']}")

    if state["stage"] == "ask_opened":
        if msg.lower() in ("tak", "yes", "✅ tak"):
            state["stage"] = "ask_entry"
            save_state(state)
            send("📍 Podaj kurs ETH/BTC w momencie otwarcia pozycji (np. `0.02534`):")
        elif msg.lower() in ("nie", "no", "❌ nie"):
            state["stage"] = "idle"
            save_state(state)
            send("OK, daj znać gdy otworzysz pozycję. Bot dalej monitoruje rynek. 👀")
        else:
            send("Odpowiedz *Tak* lub *Nie* — czy otworzyłeś pożyczkę na Aave?",
                 keyboard=[["✅ Tak", "❌ Nie"]])

    elif state["stage"] == "ask_entry":
        try:
            entry = float(msg.replace(",", "."))
            if not (0.001 < entry < 1):
                raise ValueError
            state["position"]["entry"] = entry
            state["stage"] = "ask_sl"
            save_state(state)
            send(f"✅ Kurs wejścia: `{entry:.6f}`\n\n"
                 f"🔴 Podaj *stop-loss* w % (np. `15` = zamknij gdy ETH/BTC wzrośnie o 15%):")
        except ValueError:
            send("❌ Nieprawidłowa wartość. Podaj kurs jako liczbę, np. `0.02534`:")

    elif state["stage"] == "ask_sl":
        try:
            sl = float(msg.replace(",", ".").replace("%", ""))
            if not (0.1 < sl < 100):
                raise ValueError
            state["position"]["sl_pct"] = sl
            state["stage"] = "ask_tp"
            save_state(state)
            send(f"✅ Stop-loss: `{sl}%`\n\n"
                 f"🎯 Podaj *take-profit* w % (np. `20` = zamknij gdy ETH/BTC spadnie o 20%):")
        except ValueError:
            send("❌ Nieprawidłowa wartość. Podaj liczbę, np. `15`:")

    elif state["stage"] == "ask_tp":
        try:
            tp = float(msg.replace(",", ".").replace("%", ""))
            if not (0.1 < tp < 100):
                raise ValueError
            state["position"]["tp_pct"]    = tp
            state["position"]["opened_at"] = datetime.now().isoformat()
            state["stage"] = "active"
            save_state(state)
            send(build_outcome_message(
                state["position"]["entry"],
                state["position"]["sl_pct"],
                state["position"]["tp_pct"],
            ))
        except ValueError:
            send("❌ Nieprawidłowa wartość. Podaj liczbę, np. `20`:")

    elif msg.lower() in ("/status", "status"):
        send_status_report()

    elif msg.lower() in ("/close", "close", "zamknij"):
        if state["stage"] == "active":
            closes = get_ethbtc_closes(CANDLES_LIMIT)
            exit_price = closes[-1] if closes else None
            save_transaction(exit_price, "manual")
            reset_position("manual")
            send("✅ Pozycja zamknięta ręcznie. Bot wraca do monitorowania rynku.")
        else:
            send("ℹ️ Brak aktywnej pozycji do zamknięcia.")

# ─── SPRAWDZENIE RYNKU ────────────────────────────────────────────────────────

def market_job():
    closes = get_ethbtc_closes(CANDLES_LIMIT)
    if not closes:
        return

    price      = closes[-1]
    ma50       = moving_average(closes, 50)
    ma200      = moving_average(closes, 200)
    ma50_prev  = moving_average(closes[:-1], 50)
    ma200_prev = moving_average(closes[:-1], 200)

    if not ma50 or not ma200:
        return

    log.info(f"ETH/BTC={price:.6f} | MA50={ma50:.6f} | MA200={ma200:.6f} | stage={state['stage']}")

    if state["stage"] == "active":
        check_position(price)
        if ma50_prev and ma200_prev and ma50_prev <= ma200_prev and ma50 > ma200:
            send(
                f"✅ *GOLDEN CROSS przy aktywnej pozycji!*\n\n"
                f"MA50 ({ma50:.6f}) przecięła MA200 ({ma200:.6f}) od dołu.\n"
                f"Trend może się odwracać — rozważ zamknięcie pozycji.\n"
                f"Napisz /close aby zamknąć lub /status aby sprawdzić stan."
            )
        return

    if state["stage"] != "idle":
        return

    last        = set(state["last_signals"])
    now_signals = []

    if price < ma50:
        now_signals.append("below_ma50")
    if price < ma200:
        now_signals.append("below_ma200")
    if ma50_prev and ma200_prev and ma50_prev >= ma200_prev and ma50 < ma200:
        now_signals.append("death_cross")

    entry_signals = [s for s in now_signals if s not in last or "cross" in s]

    if entry_signals:
        labels = {
            "below_ma50":  "🔴 ETH/BTC poniżej MA50",
            "below_ma200": "🔴🔴 ETH/BTC poniżej MA200",
            "death_cross": "💀 Death Cross (MA50 < MA200)",
        }
        signal_lines = "\n".join(f"• {labels[s]}" for s in entry_signals)
        send(
            f"📡 *Sygnał wejścia wykryty!*\n\n"
            f"{signal_lines}\n\n"
            f"Kurs ETH/BTC: `{price:.6f}`\n"
            f"MA50: `{ma50:.6f}` | MA200: `{ma200:.6f}`\n\n"
            f"Czy otworzyłeś pożyczkę 0.1 ETH na Aave?",
            keyboard=[["✅ Tak", "❌ Nie"]],
        )
        state["stage"] = "ask_opened"

    if ma50_prev and ma200_prev and ma50_prev <= ma200_prev and ma50 > ma200:
        send(
            f"✅ *Golden Cross* — MA50 ({ma50:.6f}) przecięła MA200 ({ma200:.6f}) od dołu.\n"
            f"Trend wzrostowy na ETH/BTC — brak sygnału wejścia."
        )

    state["last_signals"] = list(set(now_signals))
    save_state(state)

# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Bot uruchomiony.")
    send(
        "🤖 *ETH/BTC Signal Bot uruchomiony*\n\n"
        "Komendy:\n"
        "• /status — sprawdź aktywną pozycję\n"
        "• /close — zamknij pozycję ręcznie"
    )

    market_job()

    schedule.every(CHECK_INTERVAL_HOURS).hours.do(market_job)
    schedule.every().day.at(DAILY_REPORT_TIME).do(daily_status_job)

    while True:
        handle_user_input()
        schedule.run_pending()
        time.sleep(30)