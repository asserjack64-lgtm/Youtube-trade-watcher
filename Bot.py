import os
import json
import requests
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timezone, timedelta


# ============================================================
# SETTINGS
# ============================================================

SYMBOL = "BTCUSDT"
INTERVAL = "5m"
CANDLES = 200

SWING_LOOKBACK = 3
ZONE_TOLERANCE = 0.001

SWEEP_PCT = 0.0005
VOLUME_MULTIPLIER = 1.5

CONFIRM_CANDLES = 2

STATE_FILE = "trade_state.json"
TRADE_LOG_FILE = "trade_log.json"
CHART_FILE = "btc_liquidity_setup.png"

IST = timezone(timedelta(hours=5, minutes=30))


# ============================================================
# TELEGRAM
# ============================================================

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def telegram(message):

    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    if not CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is missing")

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=20
    )

    response.raise_for_status()

    return response.json()


def send_chart(chart_file, caption):

    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    if not CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is missing")

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendPhoto"
    )

    with open(chart_file, "rb") as photo:

        response = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "caption": caption
            },
            files={
                "photo": photo
            },
            timeout=30
        )

    response.raise_for_status()

    return response.json()


# ============================================================
# BINANCE DATA
# ============================================================

def get_data():

    url = (
        "https://data-api.binance.vision/"
        "api/v3/klines"
    )

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "limit": CANDLES
    }

    response = requests.get(
        url,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    columns = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_buy_volume",
        "taker_buy_quote_volume",
        "unused"
    ]

    df = pd.DataFrame(
        data,
        columns=columns
    )

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]

    for column in numeric_columns:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df["time"] = pd.to_datetime(
        df["time"],
        unit="ms",
        utc=True
    )

    return df


# ============================================================
# SWINGS
# ============================================================

def find_swings(df):

    highs = []
    lows = []

    n = SWING_LOOKBACK

    if len(df) < (n * 2 + 1):
        return highs, lows

    for i in range(
        n,
        len(df) - n
    ):

        high = float(df.iloc[i]["high"])
        low = float(df.iloc[i]["low"])

        left_high = float(
            df.iloc[i - n:i]["high"].max()
        )

        right_high = float(
            df.iloc[i + 1:i + n + 1]["high"].max()
        )

        left_low = float(
            df.iloc[i - n:i]["low"].min()
        )

        right_low = float(
            df.iloc[i + 1:i + n + 1]["low"].min()
        )

        if high > left_high and high >= right_high:

            highs.append({
                "index": i,
                "price": high
            })

        if low < left_low and low <= right_low:

            lows.append({
                "index": i,
                "price": low
            })

    return highs, lows


# ============================================================
# CLUSTER LIQUIDITY
# ============================================================

def cluster(levels):

    zones = []

    for item in levels:

        if isinstance(item, dict):
            price = float(item["price"])
        else:
            price = float(item)

        found = False

        for i, zone in enumerate(zones):

            if (
                abs(price - zone)
                / zone
                <= ZONE_TOLERANCE
            ):

                zones[i] = (
                    zone + price
                ) / 2

                found = True
                break

        if not found:
            zones.append(price)

    return zones


# ============================================================
# FIND LIQUIDITY SWEEP
# ============================================================

def find_sweep(df, index, direction):

    if index < 30:
        return None

    history = df.iloc[:index].copy()

    highs, lows = find_swings(history)

    high_zones = cluster(highs)
    low_zones = cluster(lows)

    candle = df.iloc[index]

    price = float(candle["close"])


    # --------------------------------------------------------
    # BEARISH
    # --------------------------------------------------------

    if direction == "BEARISH":

        for level in high_zones:

            distance = (
                float(level) - price
            ) / price

            if distance < 0:
                continue

            if distance > 0.003:
                continue

            swept = (
                float(candle["high"])
                >
                level * (1 + SWEEP_PCT)
            )

            reclaimed = (
                float(candle["close"])
                < level
            )

            if swept and reclaimed:

                return float(level)


    # --------------------------------------------------------
    # BULLISH
    # --------------------------------------------------------

    if direction == "BULLISH":

        for level in low_zones:

            distance = (
                price - float(level)
            ) / price

            if distance < 0:
                continue

            if distance > 0.003:
                continue

            swept = (
                float(candle["low"])
                <
                level * (1 - SWEEP_PCT)
            )

            reclaimed = (
                float(candle["close"])
                > level
            )

            if swept and reclaimed:

                return float(level)

    return None


# ============================================================
# CONFIRMATION
# ============================================================

def check_confirmation(
    df,
    sweep_index,
    level,
    direction
):

    end = sweep_index + CONFIRM_CANDLES

    if end >= len(df):
        return False

    confirmation = df.iloc[
        sweep_index + 1:end + 1
    ]

    if direction == "BEARISH":

        for _, candle in confirmation.iterrows():

            if float(candle["close"]) >= level:
                return False

    else:

        for _, candle in confirmation.iterrows():

            if float(candle["close"]) <= level:
                return False

    return True


# ============================================================
# VOLUME
# ============================================================

def volume_ratio(df, index):

    start = max(0, index - 20)

    previous = df.iloc[
        start:index
    ]["volume"]

    if len(previous) == 0:
        return 0

    average = previous.mean()

    if average <= 0:
        return 0

    return (
        float(df.iloc[index]["volume"])
        / float(average)
    )


# ============================================================
# ANALYZE
# ============================================================

def analyze(df):

    # Remove currently forming candle.
    df = df.iloc[:-1].copy()

    minimum = 30 + CONFIRM_CANDLES

    if len(df) < minimum:
        return None

    confirmation_end = len(df) - 1

    sweep_index = (
        confirmation_end
        - CONFIRM_CANDLES
    )

    if sweep_index < 30:
        return None


    # --------------------------------------------------------
    # BEARISH
    # --------------------------------------------------------

    bearish_level = find_sweep(
        df,
        sweep_index,
        "BEARISH"
    )

    if bearish_level is not None:

        ratio = volume_ratio(
            df,
            sweep_index
        )

        if ratio >= VOLUME_MULTIPLIER:

            confirmed = check_confirmation(
                df,
                sweep_index,
                bearish_level,
                "BEARISH"
            )

            if confirmed:

                return {
                    "direction": "BEARISH",
                    "level": bearish_level,
                    "price": float(
                        df.iloc[
                            confirmation_end
                        ]["close"]
                    ),
                    "volume_ratio": ratio,
                    "time": df.iloc[
                        sweep_index
                    ]["time"],
                    "confirmation_time": df.iloc[
                        confirmation_end
                    ]["time"]
                }


    # --------------------------------------------------------
    # BULLISH
    # --------------------------------------------------------

    bullish_level = find_sweep(
        df,
        sweep_index,
        "BULLISH"
    )

    if bullish_level is not None:

        ratio = volume_ratio(
            df,
            sweep_index
        )

        if ratio >= VOLUME_MULTIPLIER:

            confirmed = check_confirmation(
                df,
                sweep_index,
                bullish_level,
                "BULLISH"
            )

            if confirmed:

                return {
                    "direction": "BULLISH",
                    "level": bullish_level,
                    "price": float(
                        df.iloc[
                            confirmation_end
                        ]["close"]
                    ),
                    "volume_ratio": ratio,
                    "time": df.iloc[
                        sweep_index
                    ]["time"],
                    "confirmation_time": df.iloc[
                        confirmation_end
                    ]["time"]
                }

    return None


# ============================================================
# TRADE STATE
# ============================================================

def default_state():

    return {
        "active": False,
        "event_id": "",
        "direction": "",
        "entry": 0,
        "stop_loss": 0,
        "tp1": 0,
        "tp2": 0,
        "level": 0,
        "sweep_time": "",
        "confirmation_time": "",
        "volume_ratio": 0,
        "tp1_hit": False,
        "tp2_hit": False,
        "sl_hit": False,
        "outcome": ""
    }


def load_state():

    try:

        with open(
            STATE_FILE,
            "r"
        ) as f:

            state = json.load(f)

            if not isinstance(state, dict):
                return default_state()

            base = default_state()
            base.update(state)

            return base

    except (
        FileNotFoundError,
        json.JSONDecodeError
    ):

        return default_state()


def save_state(state):

    with open(
        STATE_FILE,
        "w"
    ) as f:

        json.dump(
            state,
            f,
            indent=2
        )


# ============================================================
# TRADE LOG
# ============================================================

def load_trade_log():

    try:

        with open(
            TRADE_LOG_FILE,
            "r"
        ) as f:

            data = json.load(f)

            if isinstance(data, list):
                return data

            return []

    except (
        FileNotFoundError,
        json.JSONDecodeError
    ):

        return []


def save_trade_log(log):

    with open(
        TRADE_LOG_FILE,
        "w"
    ) as f:

        json.dump(
            log,
            f,
            indent=2
        )


def add_closed_trade(state, outcome):

    log = load_trade_log()

    trade = {
        "event_id": state["event_id"],
        "direction": state["direction"],
        "entry": state["entry"],
        "stop_loss": state["stop_loss"],
        "tp1": state["tp1"],
        "tp2": state["tp2"],
        "level": state["level"],
        "sweep_time": state["sweep_time"],
        "confirmation_time": state["confirmation_time"],
        "volume_ratio": state["volume_ratio"],
        "tp1_hit": state["tp1_hit"],
        "outcome": outcome,
        "closed_at": datetime.now(
            timezone.utc
        ).isoformat()
    }

    # Prevent duplicate logging.
    for old_trade in log:

        if old_trade.get("event_id") == state["event_id"]:
            return

    log.append(trade)

    save_trade_log(log)


# ============================================================
# CREATE CHART
# ============================================================

def create_chart(
    df,
    signal,
    entry,
    stop_loss,
    tp1,
    tp2
):

    chart_df = (
        df.iloc[:-1]
        .tail(60)
        .copy()
        .reset_index(drop=True)
    )

    direction = signal["direction"]

    liquidity_level = float(
        signal["level"]
    )

    fig, ax = plt.subplots(
        figsize=(14, 8)
    )


    # --------------------------------------------------------
    # CANDLES
    # --------------------------------------------------------

    for i, candle in chart_df.iterrows():

        open_price = float(candle["open"])
        high_price = float(candle["high"])
        low_price = float(candle["low"])
        close_price = float(candle["close"])

        candle_color = (
            "green"
            if close_price >= open_price
            else "red"
        )

        ax.plot(
            [i, i],
            [low_price, high_price],
            color=candle_color,
            linewidth=1
        )

        body_low = min(
            open_price,
            close_price
        )

        body_height = abs(
            close_price - open_price
        )

        if body_height == 0:

            body_height = (
                high_price * 0.00001
            )

        ax.bar(
            i,
            body_height,
            bottom=body_low,
            width=0.65,
            color=candle_color
        )


    # --------------------------------------------------------
    # LEVELS
    # --------------------------------------------------------

    ax.axhline(
        liquidity_level,
        linestyle="--",
        linewidth=2,
        label=f"Liquidity ${liquidity_level:,.2f}"
    )

    ax.axhline(
        entry,
        linestyle="-",
        linewidth=2,
        label=f"Entry ${entry:,.2f}"
    )

    ax.axhline(
        stop_loss,
        linestyle="--",
        linewidth=2,
        label=f"Stop Loss ${stop_loss:,.2f}"
    )

    ax.axhline(
        tp1,
        linestyle="--",
        linewidth=2,
        label=f"TP1 ${tp1:,.2f}"
    )

    ax.axhline(
        tp2,
        linestyle="--",
        linewidth=2,
        label=f"TP2 ${tp2:,.2f}"
    )


    # --------------------------------------------------------
    # TRENDLINE
    # --------------------------------------------------------

    highs, lows = find_swings(chart_df)

    if direction == "BULLISH":

        points = lows
        label = "Bullish trendline"

    else:

        points = highs
        label = "Bearish trendline"


    if len(points) >= 2:

        p1 = points[-2]
        p2 = points[-1]

        x1 = p1["index"]
        x2 = p2["index"]

        y1 = p1["price"]
        y2 = p2["price"]

        if x2 != x1:

            slope = (
                y2 - y1
            ) / (
                x2 - x1
            )

            x3 = len(chart_df) - 1

            y3 = (
                y2
                + slope * (x3 - x2)
            )

            ax.plot(
                [x1, x2, x3],
                [y1, y2, y3],
                linewidth=3,
                label=label
            )


    # --------------------------------------------------------
    # SWEEP MARKER
    # --------------------------------------------------------

    sweep_time = signal["time"]

    sweep_rows = chart_df[
        chart_df["time"] == sweep_time
    ]

    if not sweep_rows.empty:

        sweep_index = int(
            sweep_rows.index[0]
        )

        sweep_price = float(
            chart_df.iloc[
                sweep_index
            ]["close"]
        )

        ax.scatter(
            sweep_index,
            sweep_price,
            s=150,
            marker="*",
            zorder=5,
            label="Liquidity sweep"
        )


    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    ax.set_title(
        (
            f"BTCUSDT 5m Liquidity Sweep — "
            f"{direction}"
        ),
        fontsize=16,
        fontweight="bold"
    )

    ax.set_xlabel("Candles")
    ax.set_ylabel("Price (USDT)")

    ax.grid(
        True,
        alpha=0.2
    )

    ax.legend(
        loc="best",
        fontsize=9
    )

    plt.tight_layout()

    plt.savefig(
        CHART_FILE,
        dpi=150,
        bbox_inches="tight"
    )

    plt.close()

    return CHART_FILE


# ============================================================
# MONITOR ACTIVE PAPER TRADE
# ============================================================

def monitor_trade(df, state):

    if not state.get("active"):
        return False

    direction = state["direction"]

    entry = float(state["entry"])
    stop_loss = float(state["stop_loss"])
    tp1 = float(state["tp1"])
    tp2 = float(state["tp2"])

    candle = df.iloc[-2]

    high = float(candle["high"])
    low = float(candle["low"])

    changed = False


    # ========================================================
    # BULLISH
    # ========================================================

    if direction == "BULLISH":

        # Conservative rule:
        # if both SL and TP happen in one candle,
        # SL is counted first.

        if (
            not state["sl_hit"]
            and low <= stop_loss
        ):

            state["sl_hit"] = True
            state["active"] = False
            state["outcome"] = "LOSS"

            telegram(
                f"""
🔴 BTC PAPER TRADE — LOSS

BULLISH setup

🛑 STOP LOSS HIT

Entry: ${entry:,.2f}
Stop Loss: ${stop_loss:,.2f}
TP1: ${tp1:,.2f}
TP2: ${tp2:,.2f}

TP1 previously hit:
{"YES" if state["tp1_hit"] else "NO"}

📊 Result: LOSS

This was a paper trade.
No real order was placed.
"""
            )

            add_closed_trade(
                state,
                "LOSS"
            )

            return True


        if (
            not state["tp1_hit"]
            and high >= tp1
        ):

            state["tp1_hit"] = True
            changed = True

            telegram(
                f"""
🟢 BTC PAPER TRADE UPDATE

BULLISH setup

🎯 TP1 HIT

Entry: ${entry:,.2f}
TP1: ${tp1:,.2f}
TP2: ${tp2:,.2f}

Paper trade remains active.

Watching TP2 / Stop Loss.
"""
            )


        if (
            not state["tp2_hit"]
            and high >= tp2
        ):

            state["tp2_hit"] = True
            state["active"] = False
            state["outcome"] = "WIN"

            telegram(
                f"""
🟢 BTC PAPER TRADE — WIN

BULLISH setup

🎯🎯 TP2 HIT

Entry: ${entry:,.2f}
TP1: ${tp1:,.2f}
TP2: ${tp2:,.2f}

📊 Result: WIN

This was a paper trade.
No real order was placed.
"""
            )

            add_closed_trade(
                state,
                "WIN"
            )

            return True


    # ========================================================
    # BEARISH
    # ========================================================

    else:

        if (
            not state["sl_hit"]
            and high >= stop_loss
        ):

            state["sl_hit"] = True
            state["active"] = False
            state["outcome"] = "LOSS"

            telegram(
                f"""
🔴 BTC PAPER TRADE — LOSS

BEARISH setup

🛑 STOP LOSS HIT

Entry: ${entry:,.2f}
Stop Loss: ${stop_loss:,.2f}
TP1: ${tp1:,.2f}
TP2: ${tp2:,.2f}

TP1 previously hit:
{"YES" if state["tp1_hit"] else "NO"}

📊 Result: LOSS

This was a paper trade.
No real order was placed.
"""
            )

            add_closed_trade(
                state,
                "LOSS"
            )

            return True


        if (
            not state["tp1_hit"]
            and low <= tp1
        ):

            state["tp1_hit"] = True
            changed = True

            telegram(
                f"""
🔴 BTC PAPER TRADE UPDATE

BEARISH setup

🎯 TP1 HIT

Entry: ${entry:,.2f}
TP1: ${tp1:,.2f}
TP2: ${tp2:,.2f}

Paper trade remains active.

Watching TP2 / Stop Loss.
"""
            )


        if (
            not state["tp2_hit"]
            and low <= tp2
        ):

            state["tp2_hit"] = True
            state["active"] = False
            state["outcome"] = "WIN"

            telegram(
                f"""
🟢 BTC PAPER TRADE — WIN

BEARISH setup

🎯🎯 TP2 HIT

Entry: ${entry:,.2f}
TP1: ${tp1:,.2f}
TP2: ${tp2:,.2f}

📊 Result: WIN

This was a paper trade.
No real order was placed.
"""
            )

            add_closed_trade(
                state,
                "WIN"
            )

            return True


    return changed


# ============================================================
# CREATE PAPER TRADE
# ============================================================

def create_trade(df, signal):

    direction = signal["direction"]
    level = float(signal["level"])
    price = float(signal["price"])
    volume = float(signal["volume_ratio"])


    if direction == "BEARISH":

        emoji = "🔴"

        entry = price
        stop_loss = level

        risk = abs(
            entry - stop_loss
        )

        tp1 = entry - risk
        tp2 = entry - (risk * 2)

    else:

        emoji = "🟢"

        entry = price
        stop_loss = level

        risk = abs(
            entry - stop_loss
        )

        tp1 = entry + risk
        tp2 = entry + (risk * 2)


    event_id = (
        f"{signal['time']}|"
        f"{direction}|"
        f"{level:.2f}"
    )


    state = {
        "active": True,

        "event_id": event_id,

        "direction": direction,

        "entry": entry,

        "stop_loss": stop_loss,

        "tp1": tp1,

        "tp2": tp2,

        "level": level,

        "sweep_time": str(
            signal["time"]
        ),

        "confirmation_time": str(
            signal["confirmation_time"]
        ),

        "volume_ratio": volume,

        "tp1_hit": False,

        "tp2_hit": False,

        "sl_hit": False,

        "outcome": ""
    }


    message = f"""
{emoji} BTC PAPER TRADE SETUP

Direction: {direction}

Liquidity level:
${level:,.2f}

Entry:
${entry:,.2f}

Stop Loss:
${stop_loss:,.2f}

TP1:
${tp1:,.2f}

TP2:
${tp2:,.2f}

Sweep volume:
{volume:.2f}x average

Sweep candle:
{signal["time"]}

Confirmation:
{CONFIRM_CANDLES} candles

Confirmed at:
{signal["confirmation_time"]}

━━━━━━━━━━━━━━━━━━

📊 PAPER TRADE

Risk / Reward:

1:1 → TP1
1:2 → TP2

🤖 Paper trade tracker ACTIVE

The bot will monitor:

🎯 TP1
🎯 TP2
🛑 Stop Loss

⚠️ NO REAL TRADE

This setup is for strategy testing only.
"""


    chart_file = create_chart(
        df,
        signal,
        entry,
        stop_loss,
        tp1,
        tp2
    )


    telegram(message)

    print(
        "Paper trade alert sent."
    )


    caption = (
        f"{emoji} BTC {direction} PAPER TRADE\n\n"
        f"Entry: ${entry:,.2f}\n"
        f"Stop Loss: ${stop_loss:,.2f}\n"
        f"TP1: ${tp1:,.2f}\n"
        f"TP2: ${tp2:,.2f}\n\n"
        f"Volume: {volume:.2f}x average\n\n"
        f"🤖 Paper trade tracker active\n"
        f"⚠️ No real trade"
    )


    send_chart(
        chart_file,
        caption
    )

    print(
        "Chart sent to Telegram."
    )

    return state


# ============================================================
# EOD REPORT
# ============================================================

def send_eod_report():

    now_ist = datetime.now(IST)

    today = now_ist.strftime(
        "%Y-%m-%d"
    )

    log = load_trade_log()


    today_trades = []

    for trade in log:

        closed_at = trade.get(
            "closed_at",
            ""
        )

        try:

            dt = pd.to_datetime(
                closed_at,
                utc=True
            )

            dt_ist = dt.tz_convert(
                "Asia/Kolkata"
            )

            trade_date = dt_ist.strftime(
                "%Y-%m-%d"
            )

            if trade_date == today:
                today_trades.append(trade)

        except Exception:

            continue


    wins = sum(
        1
        for trade in today_trades
        if trade.get("outcome") == "WIN"
    )

    losses = sum(
        1
        for trade in today_trades
        if trade.get("outcome") == "LOSS"
    )

    total = len(today_trades)


    tp1_count = sum(
        1
        for trade in today_trades
        if trade.get("tp1_hit")
    )


    win_rate = (
        (wins / total) * 100
        if total > 0
        else 0
    )


    state = load_state()

    open_trade = state.get(
        "active",
        False
    )


    if open_trade:

        open_text = (
            f"""
🔵 Open paper trade:

Direction: {state["direction"]}
Entry: ${float(state["entry"]):,.2f}
SL: ${float(state["stop_loss"]):,.2f}
TP1: ${float(state["tp1"]):,.2f}
TP2: ${float(state["tp2"]):,.2f}
"""
        )

    else:

        open_text = (
            "🔵 Open paper trades: 0"
        )


    message = f"""
📊 BTC LIQUIDITY BOT — EOD REPORT

📅 Date: {today} IST

━━━━━━━━━━━━━━━━━━

📈 PAPER TRADE PERFORMANCE

Trades closed: {total}

🟢 Wins: {wins}

🔴 Losses: {losses}

🎯 TP1 reached: {tp1_count}

🏆 Win rate: {win_rate:.1f}%

{open_text}

━━━━━━━━━━━━━━━━━━

🤖 Strategy status:

Paper trading only.
No real orders were placed.

The statistics are based on
the bot's detected liquidity setups.

━━━━━━━━━━━━━━━━━━

BTC Liquidity Monitor
"""


    telegram(message)

    print(
        "EOD report sent."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "BTC Liquidity Bot started"
    )


    # --------------------------------------------------------
    # EOD MODE
    # --------------------------------------------------------

    if os.environ.get(
        "EOD_REPORT"
    ) == "1":

        send_eod_report()

        return


    # --------------------------------------------------------
    # DATA
    # --------------------------------------------------------

    df = get_data()

    print(
        f"Loaded {len(df)} candles"
    )


    # --------------------------------------------------------
    # STATE
    # --------------------------------------------------------

    state = load_state()


    # --------------------------------------------------------
    # MONITOR EXISTING PAPER TRADE
    # --------------------------------------------------------

    if state.get("active"):

        print(
            "Active paper trade found."
        )

        changed = monitor_trade(
            df,
            state
        )

        if changed:

            save_state(state)

            print(
                "Paper trade state updated."
            )

        else:

            print(
                "Paper trade still running."
            )

        return


    # --------------------------------------------------------
    # FIND NEW SIGNAL
    # --------------------------------------------------------

    signal = analyze(df)


    if signal is None:

        print(
            "No new confirmed liquidity sweep."
        )

        return


    # --------------------------------------------------------
    # CREATE PAPER TRADE
    # --------------------------------------------------------

    new_state = create_trade(
        df,
        signal
    )


    # --------------------------------------------------------
    # SAVE STATE
    # --------------------------------------------------------

    save_state(
        new_state
    )


    print(
        "New paper trade created."
    )

    print(
        "Alert, chart and state saved."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
