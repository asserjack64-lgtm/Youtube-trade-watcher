import os
import json
import requests
import pandas as pd
import matplotlib.pyplot as plt


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
COOLDOWN_MINUTES = 30

# Paper trading
PAPER_ACCOUNT = 1000.0
PAPER_RISK_PERCENT = 1.0

# 50% position is considered closed at TP1.
# Remaining 50% is closed at TP2 or SL.
TP1_CLOSE_PERCENT = 0.50
TP2_CLOSE_PERCENT = 0.50

# Files
TRADE_STATE_FILE = "trade_state.json"
TRADE_HISTORY_FILE = "trade_history.json"
REPORT_STATE_FILE = "report_state.json"

CHART_FILE = "btc_liquidity_setup.png"


# ============================================================
# TELEGRAM
# ============================================================

BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN"
)

CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID"
)


def telegram(message):

    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing"
        )

    if not CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID is missing"
        )

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
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing"
        )

    if not CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID is missing"
        )

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendPhoto"
    )

    with open(
        chart_file,
        "rb"
    ) as photo:

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

    if len(df) < (
        n * 2 + 1
    ):
        return highs, lows

    for i in range(
        n,
        len(df) - n
    ):

        high = float(
            df.iloc[i]["high"]
        )

        low = float(
            df.iloc[i]["low"]
        )

        left_high = float(
            df.iloc[
                i - n:i
            ]["high"].max()
        )

        right_high = float(
            df.iloc[
                i + 1:i + n + 1
            ]["high"].max()
        )

        left_low = float(
            df.iloc[
                i - n:i
            ]["low"].min()
        )

        right_low = float(
            df.iloc[
                i + 1:i + n + 1
            ]["low"].min()
        )

        if (
            high > left_high
            and high >= right_high
        ):

            highs.append({
                "index": i,
                "price": high
            })

        if (
            low < left_low
            and low <= right_low
        ):

            lows.append({
                "index": i,
                "price": low
            })

    return highs, lows


# ============================================================
# LIQUIDITY CLUSTERING
# ============================================================

def cluster(levels):

    zones = []

    for item in levels:

        if isinstance(
            item,
            dict
        ):

            price = float(
                item["price"]
            )

        else:

            price = float(item)

        found = False

        for i, zone in enumerate(
            zones
        ):

            if (
                abs(
                    price - zone
                )
                / zone
                <= ZONE_TOLERANCE
            ):

                zones[i] = (
                    zone + price
                ) / 2

                found = True

                break

        if not found:

            zones.append(
                price
            )

    return zones


# ============================================================
# FIND LIQUIDITY SWEEP
# ============================================================

def find_sweep(
    df,
    index,
    direction
):

    if index < 30:
        return None

    history = (
        df.iloc[:index]
        .copy()
    )

    highs, lows = find_swings(
        history
    )

    high_zones = cluster(
        highs
    )

    low_zones = cluster(
        lows
    )

    candle = df.iloc[index]

    price = float(
        candle["close"]
    )


    # --------------------------------------------------------
    # BEARISH
    # --------------------------------------------------------

    if direction == "BEARISH":

        for level in high_zones:

            distance = (
                float(level)
                - price
            ) / price

            if distance < 0:
                continue

            if distance > 0.003:
                continue

            swept = (
                float(candle["high"])
                >
                level
                * (1 + SWEEP_PCT)
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
                price
                - float(level)
            ) / price

            if distance < 0:
                continue

            if distance > 0.003:
                continue

            swept = (
                float(candle["low"])
                <
                level
                * (1 - SWEEP_PCT)
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

    end = (
        sweep_index
        + CONFIRM_CANDLES
    )

    if end >= len(df):
        return False

    confirmation = df.iloc[
        sweep_index + 1:
        end + 1
    ]

    if direction == "BEARISH":

        for _, candle in (
            confirmation.iterrows()
        ):

            if (
                float(
                    candle["close"]
                )
                >= level
            ):

                return False

    else:

        for _, candle in (
            confirmation.iterrows()
        ):

            if (
                float(
                    candle["close"]
                )
                <= level
            ):

                return False

    return True


# ============================================================
# VOLUME
# ============================================================

def volume_ratio(
    df,
    index
):

    start = max(
        0,
        index - 20
    )

    previous = df.iloc[
        start:index
    ]["volume"]

    if len(previous) == 0:
        return 0

    average = previous.mean()

    if average <= 0:
        return 0

    return (
        float(
            df.iloc[index]["volume"]
        )
        / float(average)
    )


# ============================================================
# ANALYZE
# ============================================================

def analyze(df):

    # Ignore currently forming candle.
    df = (
        df.iloc[:-1]
        .copy()
    )

    minimum = (
        30
        + CONFIRM_CANDLES
    )

    if len(df) < minimum:
        return None

    # Only inspect the newest completed setup.
    confirmation_end = (
        len(df) - 1
    )

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

        if (
            ratio
            >= VOLUME_MULTIPLIER
        ):

            confirmed = (
                check_confirmation(
                    df,
                    sweep_index,
                    bearish_level,
                    "BEARISH"
                )
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

                    "confirmation_time":
                        df.iloc[
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

        if (
            ratio
            >= VOLUME_MULTIPLIER
        ):

            confirmed = (
                check_confirmation(
                    df,
                    sweep_index,
                    bullish_level,
                    "BULLISH"
                )
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

                    "confirmation_time":
                        df.iloc[
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
        "risk": 0,
        "risk_amount": 0,
        "position_size": 0,
        "level": 0,
        "sweep_time": "",
        "confirmation_time": "",
        "volume_ratio": 0,
        "tp1_hit": False,
        "tp2_hit": False,
        "sl_hit": False,
        "tp1_r": 0,
        "realized_r": 0,
        "realized_pnl": 0,
        "opened_date": ""
    }


def load_trade_state():

    try:

        with open(
            TRADE_STATE_FILE,
            "r"
        ) as f:

            state = json.load(f)

            if not isinstance(
                state,
                dict
            ):

                return default_state()

            return state

    except (
        FileNotFoundError,
        json.JSONDecodeError
    ):

        return default_state()


def save_trade_state(
    state
):

    with open(
        TRADE_STATE_FILE,
        "w"
    ) as f:

        json.dump(
            state,
            f,
            indent=2
        )


# ============================================================
# TRADE HISTORY
# ============================================================

def load_history():

    try:

        with open(
            TRADE_HISTORY_FILE,
            "r"
        ) as f:

            history = json.load(f)

            if isinstance(
                history,
                list
            ):

                return history

    except (
        FileNotFoundError,
        json.JSONDecodeError
    ):

        pass

    return []


def save_history(
    history
):

    with open(
        TRADE_HISTORY_FILE,
        "w"
    ) as f:

        json.dump(
            history,
            f,
            indent=2
        )


def add_completed_trade(
    state
):

    history = load_history()

    trade = {
        "event_id": state["event_id"],
        "direction": state["direction"],
        "entry": state["entry"],
        "stop_loss": state["stop_loss"],
        "tp1": state["tp1"],
        "tp2": state["tp2"],
        "risk": state["risk"],
        "risk_amount": state["risk_amount"],
        "position_size": state["position_size"],
        "level": state["level"],
        "sweep_time": state["sweep_time"],
        "confirmation_time": state[
            "confirmation_time"
        ],
        "opened_date": state[
            "opened_date"
        ],
        "tp1_hit": state[
            "tp1_hit"
        ],
        "tp2_hit": state[
            "tp2_hit"
        ],
        "sl_hit": state[
            "sl_hit"
        ],
        "tp1_r": state[
            "tp1_r"
        ],
        "realized_r": state[
            "realized_r"
        ],
        "realized_pnl": state[
            "realized_pnl"
        ]
    }

    # Prevent duplicates.
    for old_trade in history:

        if (
            old_trade.get(
                "event_id"
            )
            == trade["event_id"]
        ):

            return

    history.append(
        trade
    )

    save_history(
        history
    )


# ============================================================
# REPORT STATE
# ============================================================

def load_report_state():

    try:

        with open(
            REPORT_STATE_FILE,
            "r"
        ) as f:

            return json.load(f)

    except (
        FileNotFoundError,
        json.JSONDecodeError
    ):

        return {
            "last_report_date": ""
        }


def save_report_state(
    state
):

    with open(
        REPORT_STATE_FILE,
        "w"
    ) as f:

        json.dump(
            state,
            f,
            indent=2
        )


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

    direction = signal[
        "direction"
    ]

    liquidity_level = float(
        signal["level"]
    )


    fig, ax = plt.subplots(
        figsize=(14, 8)
    )


    # --------------------------------------------------------
    # CANDLES
    # --------------------------------------------------------

    for i, candle in (
        chart_df.iterrows()
    ):

        open_price = float(
            candle["open"]
        )

        high_price = float(
            candle["high"]
        )

        low_price = float(
            candle["low"]
        )

        close_price = float(
            candle["close"]
        )

        candle_color = (
            "green"
            if close_price >= open_price
            else "red"
        )


        ax.plot(
            [i, i],
            [
                low_price,
                high_price
            ],
            color=candle_color,
            linewidth=1
        )


        body_low = min(
            open_price,
            close_price
        )

        body_height = abs(
            close_price
            - open_price
        )


        if body_height == 0:

            body_height = (
                high_price
                * 0.00001
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
        label=(
            f"Liquidity "
            f"${liquidity_level:,.2f}"
        )
    )

    ax.axhline(
        entry,
        linestyle="-",
        linewidth=2,
        label=(
            f"Entry "
            f"${entry:,.2f}"
        )
    )

    ax.axhline(
        stop_loss,
        linestyle="--",
        linewidth=2,
        label=(
            f"Stop Loss "
            f"${stop_loss:,.2f}"
        )
    )

    ax.axhline(
        tp1,
        linestyle="--",
        linewidth=2,
        label=(
            f"TP1 "
            f"${tp1:,.2f}"
        )
    )

    ax.axhline(
        tp2,
        linestyle="--",
        linewidth=2,
        label=(
            f"TP2 "
            f"${tp2:,.2f}"
        )
    )


    # --------------------------------------------------------
    # TRENDLINE
    # --------------------------------------------------------

    highs, lows = find_swings(
        chart_df
    )


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

            x3 = (
                len(chart_df) - 1
            )

            y3 = (
                y2
                + slope
                * (x3 - x2)
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

    sweep_time = signal[
        "time"
    ]

    sweep_rows = chart_df[
        chart_df["time"]
        == sweep_time
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
            f"BTCUSDT 5m "
            f"Liquidity Sweep — "
            f"{direction}"
        ),
        fontsize=16,
        fontweight="bold"
    )

    ax.set_xlabel(
        "Candles"
    )

    ax.set_ylabel(
        "Price (USDT)"
    )

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
# CREATE PAPER TRADE
# ============================================================

def create_paper_trade(
    df,
    signal
):

    direction = signal[
        "direction"
    ]

    level = float(
        signal["level"]
    )

    entry = float(
        signal["price"]
    )

    volume = float(
        signal["volume_ratio"]
    )


    if direction == "BEARISH":

        emoji = "🔴"

        stop_loss = level

        risk = abs(
            entry
            - stop_loss
        )

        tp1 = (
            entry
            - risk
        )

        tp2 = (
            entry
            - (
                risk * 2
            )
        )

    else:

        emoji = "🟢"

        stop_loss = level

        risk = abs(
            entry
            - stop_loss
        )

        tp1 = (
            entry
            + risk
        )

        tp2 = (
            entry
            + (
                risk * 2
            )
        )


    # --------------------------------------------------------
    # Simulated risk
    # --------------------------------------------------------

    risk_amount = (
        PAPER_ACCOUNT
        * PAPER_RISK_PERCENT
        / 100
    )


    if risk > 0:

        position_size = (
            risk_amount
            / risk
        )

    else:

        position_size = 0


    event_id = (
        f"{signal['time']}|"
        f"{direction}|"
        f"{level:.2f}"
    )


    opened_date = (
        pd.to_datetime(
            signal["confirmation_time"],
            utc=True
        ).strftime(
            "%Y-%m-%d"
        )
    )


    state = default_state()


    state.update({

        "active": True,

        "event_id": event_id,

        "direction": direction,

        "entry": entry,

        "stop_loss": stop_loss,

        "tp1": tp1,

        "tp2": tp2,

        "risk": risk,

        "risk_amount": risk_amount,

        "position_size": position_size,

        "level": level,

        "sweep_time": str(
            signal["time"]
        ),

        "confirmation_time": str(
            signal["confirmation_time"]
        ),

        "volume_ratio": volume,

        "opened_date": opened_date

    })


    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    message = f"""
{emoji} PAPER TRADE OPENED

BTCUSDT {direction}

━━━━━━━━━━━━━━━━━━

📍 Entry
${entry:,.2f}

🛑 Stop Loss
${stop_loss:,.2f}

🎯 TP1
${tp1:,.2f}

🎯 TP2
${tp2:,.2f}

━━━━━━━━━━━━━━━━━━

💰 PAPER ACCOUNT
${PAPER_ACCOUNT:,.2f}

Risk per trade:
{PAPER_RISK_PERCENT:.1f}%

Simulated risk:
${risk_amount:,.2f}

Position size:
{position_size:.6f} BTC

━━━━━━━━━━━━━━━━━━

💧 Liquidity:
${level:,.2f}

📊 Volume:
{volume:.2f}x average

🤖 PAPER TRADE ONLY

No real order was placed.
"""


    chart_file = create_chart(
        df,
        signal,
        entry,
        stop_loss,
        tp1,
        tp2
    )


    telegram(
        message
    )


    caption = (
        f"{emoji} BTC "
        f"{direction} PAPER TRADE\n\n"
        f"Entry: ${entry:,.2f}\n"
        f"SL: ${stop_loss:,.2f}\n"
        f"TP1: ${tp1:,.2f}\n"
        f"TP2: ${tp2:,.2f}\n\n"
        f"Paper risk: "
        f"${risk_amount:,.2f}\n\n"
        f"🤖 PAPER TRADING ONLY"
    )


    send_chart(
        chart_file,
        caption
    )


    print(
        "Paper trade opened."
    )

    return state


# ============================================================
# MONITOR PAPER TRADE
# ============================================================

def monitor_trade(
    df,
    state
):

    if not state.get(
        "active"
    ):

        return False


    direction = state[
        "direction"
    ]

    entry = float(
        state["entry"]
    )

    stop_loss = float(
        state["stop_loss"]
    )

    tp1 = float(
        state["tp1"]
    )

    tp2 = float(
        state["tp2"]
    )

    risk = float(
        state["risk"]
    )

    risk_amount = float(
        state["risk_amount"]
    )


    candle = df.iloc[-2]

    high = float(
        candle["high"]
    )

    low = float(
        candle["low"]
    )


    # ========================================================
    # BULLISH
    # ========================================================

    if direction == "BULLISH":

        # SL before TP.
        if (
            not state["sl_hit"]
            and low <= stop_loss
        ):

            remaining_percent = (
                1.0
                - (
                    TP1_CLOSE_PERCENT
                    if state["tp1_hit"]
                    else 0
                )
            )


            remaining_pnl = (
                -risk_amount
                * remaining_percent
            )


            total_pnl = (
                float(
                    state["realized_pnl"]
                )
                + remaining_pnl
            )


            total_r = (
                total_pnl
                / risk_amount
                if risk_amount > 0
                else 0
            )


            state[
                "realized_pnl"
            ] = total_pnl

            state[
                "realized_r"
            ] = total_r

            state[
                "sl_hit"
            ] = True

            state[
                "active"
            ] = False


            telegram(
                f"""
🛑 PAPER TRADE — STOP LOSS

BTCUSDT BULLISH

Entry:
${entry:,.2f}

Stop:
${stop_loss:,.2f}

TP1 hit:
{"YES" if state["tp1_hit"] else "NO"}

━━━━━━━━━━━━━━━━━━

Trade result:
{total_r:+.2f}R

Simulated P&L:
${total_pnl:+,.2f}

🤖 Paper trading only.
"""
            )

            return True


        # TP1
        if (
            not state["tp1_hit"]
            and high >= tp1
        ):

            tp1_pnl = (
                risk_amount
                * TP1_CLOSE_PERCENT
            )

            tp1_r = (
                tp1_pnl
                / risk_amount
            )


            state[
                "tp1_hit"
            ] = True

            state[
                "tp1_r"
            ] = tp1_r

            state[
                "realized_pnl"
            ] = (
                float(
                    state[
                        "realized_pnl"
                    ]
                )
                + tp1_pnl
            )

            state[
                "realized_r"
            ] = (
                float(
                    state[
                        "realized_r"
                    ]
                )
                + tp1_r
            )


            telegram(
                f"""
🎯 PAPER TRADE — TP1 HIT

BTCUSDT BULLISH

Entry:
${entry:,.2f}

TP1:
${tp1:,.2f}

TP1 result:
+{tp1_r:.2f}R

Realized P&L:
+${tp1_pnl:,.2f}

Remaining:
50% → TP2

🤖 Paper trading only.
"""
            )


            return True


        # TP2
        if (
            state["tp1_hit"]
            and not state["tp2_hit"]
            and high >= tp2
        ):

            tp2_pnl = (
                risk_amount
                * TP2_CLOSE_PERCENT
                * 2
            )

            tp2_r = (
                tp2_pnl
                / risk_amount
            )


            total_pnl = (
                float(
                    state[
                        "realized_pnl"
                    ]
                )
                + tp2_pnl
            )


            total_r = (
                total_pnl
                / risk_amount
            )


            state[
                "tp2_hit"
            ] = True

            state[
                "realized_pnl"
            ] = total_pnl

            state[
                "realized_r"
            ] = total_r

            state[
                "active"
            ] = False


            telegram(
                f"""
🎯🎯 PAPER TRADE — TP2 HIT

BTCUSDT BULLISH

Entry:
${entry:,.2f}

TP2:
${tp2:,.2f}

Trade result:
+{total_r:.2f}R

Simulated P&L:
+${total_pnl:,.2f}

🤖 Paper trading only.
"""
            )


            return True


    # ========================================================
    # BEARISH
    # ========================================================

    else:

        # SL
        if (
            not state["sl_hit"]
            and high >= stop_loss
        ):

            remaining_percent = (
                1.0
                - (
                    TP1_CLOSE_PERCENT
                    if state["tp1_hit"]
                    else 0
                )
            )


            remaining_pnl = (
                -risk_amount
                * remaining_percent
            )


            total_pnl = (
                float(
                    state["realized_pnl"]
                )
                + remaining_pnl
            )


            total_r = (
                total_pnl
                / risk_amount
                if risk_amount > 0
                else 0
            )


            state[
                "realized_pnl"
            ] = total_pnl

            state[
                "realized_r"
            ] = total_r

            state[
                "sl_hit"
            ] = True

            state[
                "active"
            ] = False


            telegram(
                f"""
🛑 PAPER TRADE — STOP LOSS

BTCUSDT BEARISH

Entry:
${entry:,.2f}

Stop:
${stop_loss:,.2f}

TP1 hit:
{"YES" if state["tp1_hit"] else "NO"}

━━━━━━━━━━━━━━━━━━

Trade result:
{total_r:+.2f}R

Simulated P&L:
${total_pnl:+,.2f}

🤖 Paper trading only.
"""
            )

            return True


        # TP1
        if (
            not state["tp1_hit"]
            and low <= tp1
        ):

            tp1_pnl = (
                risk_amount
                * TP1_CLOSE_PERCENT
            )

            tp1_r = (
                tp1_pnl
                / risk_amount
            )


            state[
                "tp1_hit"
            ] = True

            state[
                "tp1_r"
            ] = tp1_r

            state[
                "realized_pnl"
            ] = (
                float(
                    state[
                        "realized_pnl"
                    ]
                )
                + tp1_pnl
            )

            state[
                "realized_r"
            ] = (
                float(
                    state[
                        "realized_r"
                    ]
                )
                + tp1_r
            )


            telegram(
                f"""
🎯 PAPER TRADE — TP1 HIT

BTCUSDT BEARISH

Entry:
${entry:,.2f}

TP1:
${tp1:,.2f}

TP1 result:
+{tp1_r:.2f}R

Realized P&L:
+${tp1_pnl:,.2f}

Remaining:
50% → TP2

🤖 Paper trading only.
"""
            )


            return True


        # TP2
        if (
            state["tp1_hit"]
            and not state["tp2_hit"]
            and low <= tp2
        ):

            tp2_pnl = (
                risk_amount
                * TP2_CLOSE_PERCENT
                * 2
            )

            tp2_r = (
                tp2_pnl
                / risk_amount
            )


            total_pnl = (
                float(
                    state[
                        "realized_pnl"
                    ]
                )
                + tp2_pnl
            )


            total_r = (
                total_pnl
                / risk_amount
            )


            state[
                "tp2_hit"
            ] = True

            state[
                "realized_pnl"
            ] = total_pnl

            state[
                "realized_r"
            ] = total_r

            state[
                "active"
            ] = False


            telegram(
                f"""
🎯🎯 PAPER TRADE — TP2 HIT

BTCUSDT BEARISH

Entry:
${entry:,.2f}

TP2:
${tp2:,.2f}

Trade result:
+{total_r:.2f}R

Simulated P&L:
+${total_pnl:,.2f}

🤖 Paper trading only.
"""
            )


            return True


    return False


# ============================================================
# DAILY REPORT
# ============================================================

def send_daily_report():

    # Use UTC date.
    # GitHub Actions runs in UTC.

    today = (
        pd.Timestamp.now(
            tz="UTC"
        ).strftime(
            "%Y-%m-%d"
        )
    )


    report_state = (
        load_report_state()
    )


    if (
        report_state.get(
            "last_report_date"
        )
        == today
    ):

        print(
            "Daily report already sent."
        )

        return False


    history = load_history()


    today_trades = []

    for trade in history:

        if (
            trade.get(
                "opened_date"
            )
            == today
        ):

            today_trades.append(
                trade
            )


    total = len(
        today_trades
    )


    completed = 0
    winners = 0
    losers = 0
    tp1_count = 0
    tp2_count = 0
    sl_count = 0

    total_r = 0.0
    total_pnl = 0.0


    best_r = None
    worst_r = None


    for trade in today_trades:

        realized_r = float(
            trade.get(
                "realized_r",
                0
            )
        )

        realized_pnl = float(
            trade.get(
                "realized_pnl",
                0
            )
        )


        if (
            trade.get("tp1_hit")
            or trade.get("tp2_hit")
            or trade.get("sl_hit")
        ):

            completed += 1


        if trade.get(
            "tp1_hit"
        ):

            tp1_count += 1


        if trade.get(
            "tp2_hit"
        ):

            tp2_count += 1


        if trade.get(
            "sl_hit"
        ):

            sl_count += 1


        if (
            trade.get("tp2_hit")
            and realized_r > 0
        ):

            winners += 1

        elif (
            trade.get("sl_hit")
            and realized_r <= 0
        ):

            losers += 1


        total_r += realized_r

        total_pnl += realized_pnl


        if best_r is None:

            best_r = realized_r

        else:

            best_r = max(
                best_r,
                realized_r
            )


        if worst_r is None:

            worst_r = realized_r

        else:

            worst_r = min(
                worst_r,
                realized_r
            )


    if completed > 0:

        win_rate = (
            winners
            / completed
            * 100
        )

        average_r = (
            total_r
            / completed
        )

    else:

        win_rate = 0
        average_r = 0


    if best_r is None:
        best_r = 0

    if worst_r is None:
        worst_r = 0


    # --------------------------------------------------------
    # Check if there is an active trade from today.
    # --------------------------------------------------------

    state = load_trade_state()

    active_today = (
        state.get("active")
        and state.get(
            "opened_date"
        ) == today
    )


    report = f"""
📊 BTC LIQUIDITY BOT

DAILY PAPER-TRADING REPORT

Date:
{today}

━━━━━━━━━━━━━━━━━━

📈 SIGNALS / TRADES

Signals opened:
{total}

Completed:
{completed}

Active:
{"1" if active_today else "0"}

━━━━━━━━━━━━━━━━━━

🏆 RESULTS

Winners:
{winners}

Losers:
{losers}

Win rate:
{win_rate:.1f}%

━━━━━━━━━━━━━━━━━━

🎯 TARGETS

TP1 hit:
{tp1_count}

TP2 hit:
{tp2_count}

🛑 SL hit:
{sl_count}

━━━━━━━━━━━━━━━━━━

💰 PAPER PERFORMANCE

Total:
{total_r:+.2f}R

Average:
{average_r:+.2f}R

Simulated P&L:
${total_pnl:+,.2f}

Best:
{best_r:+.2f}R

Worst:
{worst_r:+.2f}R

━━━━━━━━━━━━━━━━━━

💼 Paper account:
${PAPER_ACCOUNT:,.2f}

Risk/trade:
{PAPER_RISK_PERCENT:.1f}%

⚠️ PAPER TRADING ONLY

No real orders were placed.
"""


    telegram(
        report
    )


    report_state[
        "last_report_date"
    ] = today


    save_report_state(
        report_state
    )


    print(
        "Daily report sent."
    )

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "BTC Liquidity Bot started"
    )


    # --------------------------------------------------------
    # GET DATA
    # --------------------------------------------------------

    df = get_data()


    print(
        f"Loaded {len(df)} candles"
    )


    # --------------------------------------------------------
    # EXISTING PAPER TRADE
    # --------------------------------------------------------

    state = (
        load_trade_state()
    )


    if state.get(
        "active"
    ):

        print(
            "Active paper trade found."
        )


        changed = monitor_trade(
            df,
            state
        )


        if changed:

            if not state.get(
                "active"
            ):

                add_completed_trade(
                    state
                )


            save_trade_state(
                state
            )


            print(
                "Paper trade state updated."
            )

        else:

            print(
                "Paper trade still active."
            )


        return


    # --------------------------------------------------------
    # FIND NEW SIGNAL
    # --------------------------------------------------------

    signal = analyze(
        df
    )


    if signal is None:

        print(
            "No new confirmed liquidity sweep."
        )

        return


    # --------------------------------------------------------
    # CREATE PAPER TRADE
    # --------------------------------------------------------

    new_state = (
        create_paper_trade(
            df,
            signal
        )
    )


    save_trade_state(
        new_state
    )


    print(
        "Paper trade saved."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
