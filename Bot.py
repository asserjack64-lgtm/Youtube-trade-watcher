import os
import sys
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

HEARTBEAT_MINUTES = 60

STATE_FILE = "trade_state.json"
HISTORY_FILE = "trade_history.json"
CHART_FILE = "btc_liquidity_setup.png"


# ============================================================
# TELEGRAM SETTINGS
# ============================================================

BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN"
)

CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID"
)


# ============================================================
# TELEGRAM TEXT
# ============================================================

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


# ============================================================
# TELEGRAM CHART
# ============================================================

def send_chart(
    chart_file,
    caption
):

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
# HEARTBEAT
# ============================================================

def send_heartbeat(state):

    now = pd.Timestamp.now(
        tz="UTC"
    )

    last_heartbeat = state.get(
        "last_heartbeat",
        ""
    )

    if last_heartbeat:

        try:

            previous = pd.to_datetime(
                last_heartbeat,
                utc=True
            )

            minutes = (
                now - previous
            ).total_seconds() / 60

            if minutes < HEARTBEAT_MINUTES:

                return False

        except Exception:

            pass


    message = f"""
🟢 BTC LIQUIDITY MONITOR

Bot status: ACTIVE

Market: BTCUSDT
Timeframe: 5m

Last market check:
{now.strftime("%Y-%m-%d %H:%M UTC")}

Status:
Market analysis running normally.

Monitoring:

💧 Liquidity sweeps
📊 Volume confirmation
📈 Price confirmation
🎯 Paper-trade setups

⚠️ No action required.
"""

    telegram(message)

    state["last_heartbeat"] = (
        now.isoformat()
    )

    return True


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
# FIND SWINGS
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
# CLUSTER LIQUIDITY LEVELS
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

    history = df.iloc[
        :index
    ].copy()

    highs, lows = find_swings(
        history
    )

    high_zones = cluster(
        highs
    )

    low_zones = cluster(
        lows
    )

    candle = df.iloc[
        index
    ]

    price = float(
        candle["close"]
    )


    # ========================================================
    # BEARISH
    # ========================================================

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
                level * (
                    1 + SWEEP_PCT
                )
            )

            reclaimed = (
                float(candle["close"])
                < level
            )

            if swept and reclaimed:

                return float(level)


    # ========================================================
    # BULLISH
    # ========================================================

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
                level * (
                    1 - SWEEP_PCT
                )
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
        sweep_index + 1:end + 1
    ]

    if direction == "BEARISH":

        for _, candle in (
            confirmation.iterrows()
        ):

            if (
                float(candle["close"])
                >= level
            ):

                return False

    else:

        for _, candle in (
            confirmation.iterrows()
        ):

            if (
                float(candle["close"])
                <= level
            ):

                return False

    return True


# ============================================================
# VOLUME RATIO
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
        /
        float(average)
    )


# ============================================================
# ANALYZE MARKET
# ============================================================

def analyze(df):

    # Ignore currently forming candle.

    df = df.iloc[
        :-1
    ].copy()

    minimum = (
        30 + CONFIRM_CANDLES
    )

    if len(df) < minimum:

        return None

    confirmation_end = (
        len(df) - 1
    )

    sweep_index = (
        confirmation_end
        - CONFIRM_CANDLES
    )

    if sweep_index < 30:

        return None


    # ========================================================
    # BEARISH
    # ========================================================

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

                    "level":
                        bearish_level,

                    "price":
                        float(
                            df.iloc[
                                confirmation_end
                            ]["close"]
                        ),

                    "volume_ratio":
                        ratio,

                    "time":
                        df.iloc[
                            sweep_index
                        ]["time"],

                    "confirmation_time":
                        df.iloc[
                            confirmation_end
                        ]["time"]
                }


    # ========================================================
    # BULLISH
    # ========================================================

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

                    "level":
                        bullish_level,

                    "price":
                        float(
                            df.iloc[
                                confirmation_end
                            ]["close"]
                        ),

                    "volume_ratio":
                        ratio,

                    "time":
                        df.iloc[
                            sweep_index
                        ]["time"],

                    "confirmation_time":
                        df.iloc[
                            confirmation_end
                        ]["time"]
                }

    return None


# ============================================================
# DEFAULT STATE
# ============================================================

def default_state():

    return {

        "active": False,

        "last_heartbeat": "",

        # NEW:
        # Stores the last setup that was processed.
        # This prevents the same closed setup from
        # generating another alert.

        "last_processed_event_id": "",

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

        "sl_hit": False
    }


# ============================================================
# LOAD STATE
# ============================================================

def load_state():

    try:

        with open(
            STATE_FILE,
            "r"
        ) as f:

            state = json.load(f)

            if not isinstance(
                state,
                dict
            ):

                return default_state()

            default = default_state()

            for key, value in (
                default.items()
            ):

                if key not in state:

                    state[key] = value

            return state

    except (
        FileNotFoundError,
        json.JSONDecodeError
    ):

        return default_state()


# ============================================================
# SAVE STATE
# ============================================================

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
# LOAD TRADE HISTORY
# ============================================================

def load_history():

    try:

        with open(
            HISTORY_FILE,
            "r"
        ) as f:

            history = json.load(f)

            if isinstance(
                history,
                list
            ):

                return history

            return []

    except (
        FileNotFoundError,
        json.JSONDecodeError
    ):

        return []


# ============================================================
# SAVE TRADE HISTORY
# ============================================================

def save_history(history):

    with open(
        HISTORY_FILE,
        "w"
    ) as f:

        json.dump(
            history,
            f,
            indent=2
        )


# ============================================================
# CHECK IF EVENT WAS ALREADY PROCESSED
# ============================================================

def event_already_processed(
    event_id,
    state
):

    # --------------------------------------------------------
    # CHECK CURRENT STATE
    # --------------------------------------------------------

    if (
        state.get(
            "last_processed_event_id",
            ""
        )
        == event_id
    ):

        return True


    # --------------------------------------------------------
    # CHECK CURRENT / PREVIOUS TRADE
    # --------------------------------------------------------

    if (
        state.get(
            "event_id",
            ""
        )
        == event_id
    ):

        return True


    # --------------------------------------------------------
    # CHECK TRADE HISTORY
    # --------------------------------------------------------

    history = load_history()

    for trade in history:

        if (
            trade.get(
                "event_id",
                ""
            )
            == event_id
        ):

            return True


    return False


# ============================================================
# RECORD COMPLETED TRADE
# ============================================================

def record_completed_trade(
    state,
    outcome
):

    history = load_history()

    if outcome == "TP2":

        result_r = 2

    elif outcome == "TP1":

        result_r = 1

    else:

        result_r = -1


    trade = {

        "event_id":
            state.get(
                "event_id",
                ""
            ),

        "symbol":
            SYMBOL,

        "direction":
            state.get(
                "direction",
                ""
            ),

        "entry":
            float(
                state.get(
                    "entry",
                    0
                )
            ),

        "stop_loss":
            float(
                state.get(
                    "stop_loss",
                    0
                )
            ),

        "tp1":
            float(
                state.get(
                    "tp1",
                    0
                )
            ),

        "tp2":
            float(
                state.get(
                    "tp2",
                    0
                )
            ),

        "level":
            float(
                state.get(
                    "level",
                    0
                )
            ),

        "sweep_time":
            state.get(
                "sweep_time",
                ""
            ),

        "confirmation_time":
            state.get(
                "confirmation_time",
                ""
            ),

        "volume_ratio":
            float(
                state.get(
                    "volume_ratio",
                    0
                )
            ),

        "tp1_hit":
            bool(
                state.get(
                    "tp1_hit",
                    False
                )
            ),

        "tp2_hit":
            bool(
                state.get(
                    "tp2_hit",
                    False
                )
            ),

        "sl_hit":
            bool(
                state.get(
                    "sl_hit",
                    False
                )
            ),

        "outcome":
            outcome,

        "result_r":
            result_r,

        "closed_at":
            pd.Timestamp.now(
                tz="UTC"
            ).isoformat()
    }


    # Prevent duplicate history entries.

    for existing in history:

        if (
            existing.get(
                "event_id"
            )
            ==
            trade["event_id"]
        ):

            return


    history.append(
        trade
    )

    save_history(
        history
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


    # ========================================================
    # CANDLESTICKS
    # ========================================================

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


    # ========================================================
    # LIQUIDITY
    # ========================================================

    ax.axhline(
        liquidity_level,
        linestyle="--",
        linewidth=2,
        label=(
            f"Liquidity "
            f"${liquidity_level:,.2f}"
        )
    )


    # ========================================================
    # ENTRY
    # ========================================================

    ax.axhline(
        entry,
        linestyle="-",
        linewidth=2,
        label=(
            f"Entry "
            f"${entry:,.2f}"
        )
    )


    # ========================================================
    # STOP LOSS
    # ========================================================

    ax.axhline(
        stop_loss,
        linestyle="--",
        linewidth=2,
        label=(
            f"Stop Loss "
            f"${stop_loss:,.2f}"
        )
    )


    # ========================================================
    # TP1
    # ========================================================

    ax.axhline(
        tp1,
        linestyle="--",
        linewidth=2,
        label=(
            f"TP1 "
            f"${tp1:,.2f}"
        )
    )


    # ========================================================
    # TP2
    # ========================================================

    ax.axhline(
        tp2,
        linestyle="--",
        linewidth=2,
        label=(
            f"TP2 "
            f"${tp2:,.2f}"
        )
    )


    # ========================================================
    # TRENDLINE
    # ========================================================

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


    # ========================================================
    # SWEEP MARKER
    # ========================================================

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


    # ========================================================
    # TITLE
    # ========================================================

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
# MONITOR ACTIVE PAPER TRADE
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


    # Latest completed candle.

    candle = df.iloc[
        -2
    ]

    high = float(
        candle["high"]
    )

    low = float(
        candle["low"]
    )

    changed = False


    # ========================================================
    # BULLISH
    # ========================================================

    if direction == "BULLISH":

        # Stop loss first.

        if (
            not state["sl_hit"]
            and low <= stop_loss
        ):

            telegram(
                f"""
🔴 BTC PAPER TRADE UPDATE

BULLISH setup

🛑 STOP LOSS HIT

Entry: ${entry:,.2f}
Stop Loss: ${stop_loss:,.2f}
TP1: ${tp1:,.2f}
TP2: ${tp2:,.2f}

Result: -1R

The bullish paper trade is closed.
"""
            )

            state["sl_hit"] = True

            state["active"] = False

            record_completed_trade(
                state,
                "SL"
            )

            changed = True

            return changed


        # TP1

        if (
            not state["tp1_hit"]
            and high >= tp1
        ):

            telegram(
                f"""
🟢 BTC PAPER TRADE UPDATE

BULLISH setup

🎯 TP1 HIT

Entry: ${entry:,.2f}
TP1: ${tp1:,.2f}
TP2: ${tp2:,.2f}

TP1 = +1R

Paper trade remains active
for TP2.
"""
            )

            state["tp1_hit"] = True

            changed = True


        # TP2

        if (
            not state["tp2_hit"]
            and high >= tp2
        ):

            telegram(
                f"""
🟢 BTC PAPER TRADE UPDATE

BULLISH setup

🎯🎯 TP2 HIT

Entry: ${entry:,.2f}
TP1: ${tp1:,.2f}
TP2: ${tp2:,.2f}

Result: +2R

The bullish paper trade is complete.
"""
            )

            state["tp2_hit"] = True

            state["active"] = False

            record_completed_trade(
                state,
                "TP2"
            )

            changed = True


    # ========================================================
    # BEARISH
    # ========================================================

    else:

        # Stop loss first.

        if (
            not state["sl_hit"]
            and high >= stop_loss
        ):

            telegram(
                f"""
🔴 BTC PAPER TRADE UPDATE

BEARISH setup

🛑 STOP LOSS HIT

Entry: ${entry:,.2f}
Stop Loss: ${stop_loss:,.2f}
TP1: ${tp1:,.2f}
TP2: ${tp2:,.2f}

Result: -1R

The bearish paper trade is closed.
"""
            )

            state["sl_hit"] = True

            state["active"] = False

            record_completed_trade(
                state,
                "SL"
            )

            changed = True

            return changed


        # TP1

        if (
            not state["tp1_hit"]
            and low <= tp1
        ):

            telegram(
                f"""
🔴 BTC PAPER TRADE UPDATE

BEARISH setup

🎯 TP1 HIT

Entry: ${entry:,.2f}
TP1: ${tp1:,.2f}
TP2: ${tp2:,.2f}

TP1 = +1R

Paper trade remains active
for TP2.
"""
            )

            state["tp1_hit"] = True

            changed = True


        # TP2

        if (
            not state["tp2_hit"]
            and low <= tp2
        ):

            telegram(
                f"""
🔴 BTC PAPER TRADE UPDATE

BEARISH setup

🎯🎯 TP2 HIT

Entry: ${entry:,.2f}
TP1: ${tp1:,.2f}
TP2: ${tp2:,.2f}

Result: +2R

The bearish paper trade is complete.
"""
            )

            state["tp2_hit"] = True

            state["active"] = False

            record_completed_trade(
                state,
                "TP2"
            )

            changed = True


    return changed


# ============================================================
# CREATE NEW PAPER TRADE
# ============================================================

def create_trade(
    df,
    signal,
    previous_state
):

    direction = signal[
        "direction"
    ]

    level = float(
        signal["level"]
    )

    price = float(
        signal["price"]
    )

    volume = float(
        signal["volume_ratio"]
    )


    # ========================================================
    # BEARISH
    # ========================================================

    if direction == "BEARISH":

        emoji = "🔴"

        entry = price

        stop_loss = level

        risk = abs(
            entry - stop_loss
        )

        tp1 = (
            entry - risk
        )

        tp2 = (
            entry - (
                risk * 2
            )
        )


    # ========================================================
    # BULLISH
    # ========================================================

    else:

        emoji = "🟢"

        entry = price

        stop_loss = level

        risk = abs(
            entry - stop_loss
        )

        tp1 = (
            entry + risk
        )

        tp2 = (
            entry + (
                risk * 2
            )
        )


    # ========================================================
    # EVENT ID
    # ========================================================

    event_id = (
        f"{signal['time']}|"
        f"{direction}|"
        f"{level:.2f}"
    )


    # ========================================================
    # STATE
    # ========================================================

    state = {

        "active": True,

        "last_heartbeat":
            previous_state.get(
                "last_heartbeat",
                ""
            ),

        # IMPORTANT:
        # Remember this setup permanently in state.

        "last_processed_event_id":
            event_id,

        "event_id":
            event_id,

        "direction":
            direction,

        "entry":
            entry,

        "stop_loss":
            stop_loss,

        "tp1":
            tp1,

        "tp2":
            tp2,

        "level":
            level,

        "sweep_time": str(
            signal["time"]
        ),

        "confirmation_time": str(
            signal["confirmation_time"]
        ),

        "volume_ratio":
            volume,

        "tp1_hit": False,

        "tp2_hit": False,

        "sl_hit": False
    }


    # ========================================================
    # TELEGRAM MESSAGE
    # ========================================================

    message = f"""
{emoji} BTC LIQUIDITY SETUP

Direction: {direction}

Liquidity level:
${level:,.2f}

Current price:
${price:,.2f}

Sweep volume:
{volume:.2f}x average

Sweep candle:
{signal["time"]}

Confirmation:
{CONFIRM_CANDLES} candles

Confirmed at:
{signal["confirmation_time"]}

━━━━━━━━━━━━━━━━━━

📍 REFERENCE ENTRY
${entry:,.2f}

🛑 STOP LOSS
${stop_loss:,.2f}

🎯 TP1
${tp1:,.2f}

🎯 TP2
${tp2:,.2f}

📊 RISK / REWARD

1:1 → TP1
1:2 → TP2

━━━━━━━━━━━━━━━━━━

🤖 PAPER TRADE TRACKER ACTIVE

The bot will monitor:

🎯 TP1
🎯 TP2
🛑 Stop Loss

━━━━━━━━━━━━━━━━━━

⚠️ MANUAL TRADE ONLY

This is NOT a real trade.

Review the setup before entering.
"""


    # ========================================================
    # CHART
    # ========================================================

    chart_file = create_chart(
        df,
        signal,
        entry,
        stop_loss,
        tp1,
        tp2
    )

    print(
        "Chart created."
    )


    # ========================================================
    # SEND TEXT
    # ========================================================

    telegram(
        message
    )

    print(
        "Telegram text alert sent."
    )


    # ========================================================
    # SEND CHART
    # ========================================================

    caption = (
        f"{emoji} BTC "
        f"{direction} PAPER SETUP\n\n"

        f"Entry: "
        f"${entry:,.2f}\n"

        f"Stop Loss: "
        f"${stop_loss:,.2f}\n"

        f"TP1: "
        f"${tp1:,.2f}\n"

        f"TP2: "
        f"${tp2:,.2f}\n\n"

        f"Volume: "
        f"{volume:.2f}x average\n\n"

        f"🤖 Paper trade tracker active\n"

        f"⚠️ Manual trade only"
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

    print(
        "Generating daily EOD paper-trading report..."
    )

    history = load_history()

    now = pd.Timestamp.now(
        tz="UTC"
    )

    today = now.strftime(
        "%Y-%m-%d"
    )

    today_trades = []

    for trade in history:

        closed_at = trade.get(
            "closed_at",
            ""
        )

        if not closed_at:

            continue

        try:

            closed_date = (
                pd.to_datetime(
                    closed_at,
                    utc=True
                ).strftime(
                    "%Y-%m-%d"
                )
            )

        except Exception:

            continue

        if closed_date == today:

            today_trades.append(
                trade
            )


    total = len(
        today_trades
    )

    tp1_hits = sum(
        1
        for t in today_trades
        if t.get(
            "tp1_hit",
            False
        )
    )

    tp2_hits = sum(
        1
        for t in today_trades
        if t.get(
            "tp2_hit",
            False
        )
    )

    sl_hits = sum(
        1
        for t in today_trades
        if t.get(
            "sl_hit",
            False
        )
    )


    wins = sum(
        1
        for t in today_trades
        if t.get(
            "outcome"
        ) in (
            "TP1",
            "TP2"
        )
    )


    losses = sum(
        1
        for t in today_trades
        if t.get(
            "outcome"
        ) == "SL"
    )


    total_r = sum(
        float(
            t.get(
                "result_r",
                0
            )
        )
        for t in today_trades
    )


    if total > 0:

        win_rate = (
            wins / total
        ) * 100

    else:

        win_rate = 0


    if total == 0:

        report = f"""
📊 BTC DAILY PAPER-TRADE REPORT

Date: {today}

No completed paper trades today.

Signals are still being monitored.

━━━━━━━━━━━━━━━━━━

Market:
BTCUSDT

Timeframe:
5m

Status:
🟢 Monitor active

⚠️ Paper trading only
"""

    else:

        report = f"""
📊 BTC DAILY PAPER-TRADE REPORT

Date: {today}

━━━━━━━━━━━━━━━━━━

📈 PERFORMANCE

Completed trades:
{total}

Winning trades:
{wins}

Losing trades:
{losses}

Win rate:
{win_rate:.1f}%

━━━━━━━━━━━━━━━━━━

🎯 TARGETS

TP1 hits:
{tp1_hits}

TP2 hits:
{tp2_hits}

🛑 Stop Loss:
{sl_hits}

━━━━━━━━━━━━━━━━━━

📊 R RESULT

Total:
{total_r:+.1f}R

━━━━━━━━━━━━━━━━━━

Market:
BTCUSDT

Timeframe:
5m

🤖 Strategy:
Liquidity Sweep + Volume
+ Confirmation

⚠️ PAPER TRADING ONLY

This report measures the strategy
without using real money.
"""


    telegram(
        report
    )

    print(
        "EOD report sent."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    # ========================================================
    # EOD MODE
    # ========================================================

    if (
        len(sys.argv) > 1
        and sys.argv[1].lower()
        == "--eod"
    ):

        send_eod_report()

        return


    print(
        "BTC Liquidity Bot started"
    )


    # ========================================================
    # GET DATA
    # ========================================================

    df = get_data()

    print(
        f"Loaded {len(df)} candles"
    )


    # ========================================================
    # STATE
    # ========================================================

    state = load_state()


    # ========================================================
    # HEARTBEAT
    # ========================================================

    try:

        heartbeat_sent = (
            send_heartbeat(
                state
            )
        )

        if heartbeat_sent:

            save_state(
                state
            )

            print(
                "Heartbeat sent."
            )

    except Exception as e:

        print(
            "Heartbeat error:",
            str(e)
        )


    # ========================================================
    # MONITOR ACTIVE TRADE
    # ========================================================

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

            save_state(
                state
            )

            print(
                "Paper trade state updated."
            )

        else:

            print(
                "Active paper trade still running."
            )

        # Do not create another trade
        # while one is active.

        return


    # ========================================================
    # FIND NEW SIGNAL
    # ========================================================

    signal = analyze(
        df
    )


    if signal is None:

        print(
            "No new confirmed liquidity sweep."
        )

        return


    # ========================================================
    # BUILD EVENT ID
    # ========================================================

    event_id = (
        f"{signal['time']}|"
        f"{signal['direction']}|"
        f"{float(signal['level']):.2f}"
    )


    print(
        "Signal detected:",
        event_id
    )


    # ========================================================
    # DUPLICATE PROTECTION
    # ========================================================

    if event_already_processed(
        event_id,
        state
    ):

        print(
            "Signal already processed."
        )

        print(
            "No duplicate paper trade will be created."
        )

        return


    # ========================================================
    # CREATE PAPER TRADE
    # ========================================================

    new_state = create_trade(
        df,
        signal,
        state
    )


    # ========================================================
    # SAVE STATE
    # ========================================================

    save_state(
        new_state
    )


    print(
        "New paper trade created."
    )

    print(
        "Alert sent, chart sent "
        "and trade state saved."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
