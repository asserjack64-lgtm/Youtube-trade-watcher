import os
import sys
import json
import requests
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# V4 BTC RANGE LIQUIDITY STRATEGY
# ============================================================

SYMBOL = "BTCUSDT"
INTERVAL = "5m"
CANDLES = 300


# ============================================================
# V4 STRATEGY PARAMETERS
# ============================================================

RANGE_CANDLES = 24

MAX_RANGE_WIDTH_PCT = 0.015

SWEEP_PCT = 0.0003

SL_BUFFER_PCT = 0.0005

VOLUME_LOOKBACK = 20

MIN_VOLUME_RATIO = 1.50

CONFIRM_BODY_MIN_PCT = 0.0008


# ============================================================
# TRADE MANAGEMENT
# ============================================================

TP1_R = 1.0
TP2_R = 2.0

TP1_PARTIAL = 0.50

MOVE_SL_TO_BREAKEVEN = True

MAX_BARS_IN_TRADE = 72


# ============================================================
# HEARTBEAT
# ============================================================

HEARTBEAT_MINUTES = 60


# ============================================================
# FILES
# ============================================================

STATE_FILE = "trade_state.json"

HISTORY_FILE = "trade_history.json"

CHART_FILE = "btc_v4_setup.png"


# ============================================================
# TELEGRAM
# ============================================================

BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN"
)

CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID"
)


# ============================================================
# TELEGRAM MESSAGE
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

    last = state.get(
        "last_heartbeat",
        ""
    )

    if last:

        try:

            previous = pd.to_datetime(
                last,
                utc=True
            )

            minutes = (
    (now - previous).total_seconds()
    / 60
            )

            if minutes < HEARTBEAT_MINUTES:

                return False

        except Exception:

            pass


    message = f"""
🟢 BTC V4 MONITOR

Bot status: ACTIVE

Market:
BTCUSDT

Timeframe:
5m

Strategy:
V4 Range Liquidity Sweep

Current check:
{now.strftime("%Y-%m-%d %H:%M UTC")}

Monitoring:

📦 2-hour range
💧 Liquidity sweep
📊 Volume confirmation
📈 Displacement confirmation
🎯 Paper trade setups

⚠️ No real trades are being placed.
"""

    telegram(message)

    state[
        "last_heartbeat"
    ] = now.isoformat()

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

        "symbol":
            SYMBOL,

        "interval":
            INTERVAL,

        "limit":
            CANDLES
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


    numeric = [

        "open",

        "high",

        "low",

        "close",

        "volume"
    ]


    for column in numeric:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )


    df["time"] = pd.to_datetime(
        df["time"],
        unit="ms",
        utc=True
    )


    df["close_time"] = pd.to_datetime(
        df["close_time"],
        unit="ms",
        utc=True
    )


    # Remove forming candle.

    now = pd.Timestamp.now(
        tz="UTC"
    )

    df = df[
        df["close_time"] <= now
    ]


    df = (
        df
        .drop_duplicates(
            "time"
        )
        .sort_values(
            "time"
        )
        .reset_index(
            drop=True
        )
    )


    return df


# ============================================================
# VOLUME RATIO
# ============================================================

def volume_ratio(
    df,
    index
):

    start = max(
        0,
        index - VOLUME_LOOKBACK
    )

    previous = df.iloc[
        start:index
    ]["volume"]


    if len(previous) < 5:

        return 0.0


    average = previous.mean()


    if average <= 0:

        return 0.0


    return float(
        df.iloc[index]["volume"]
        / average
    )


# ============================================================
# V4 RANGE SIGNAL
# ============================================================

def find_signal(
    df,
    confirmation_index
):

    # confirmation_index is the candle
    # immediately after the sweep.

    sweep_index = (
        confirmation_index - 1
    )


    minimum = (
        RANGE_CANDLES
        + VOLUME_LOOKBACK
        + 2
    )


    if sweep_index < minimum:

        return None


    # ========================================================
    # RANGE
    # ========================================================

    range_df = df.iloc[
        sweep_index - RANGE_CANDLES:
        sweep_index
    ]


    range_high = float(
        range_df["high"].max()
    )


    range_low = float(
        range_df["low"].min()
    )


    midpoint = (
        range_high
        + range_low
    ) / 2


    if midpoint <= 0:

        return None


    range_width_pct = (
        range_high
        - range_low
    ) / midpoint


    if (
        range_width_pct
        > MAX_RANGE_WIDTH_PCT
    ):

        return None


    sweep = df.iloc[
        sweep_index
    ]


    confirm = df.iloc[
        confirmation_index
    ]


    sweep_high = float(
        sweep["high"]
    )


    sweep_low = float(
        sweep["low"]
    )


    sweep_close = float(
        sweep["close"]
    )


    confirm_open = float(
        confirm["open"]
    )


    confirm_high = float(
        confirm["high"]
    )


    confirm_low = float(
        confirm["low"]
    )


    confirm_close = float(
        confirm["close"]
    )


    # ========================================================
    # VOLUME
    # ========================================================

    vr = volume_ratio(
        df,
        sweep_index
    )


    if vr < MIN_VOLUME_RATIO:

        return None


    # ========================================================
    # BEARISH SWEEP
    # ========================================================

    bearish_sweep = (

        sweep_high
        >
        range_high
        * (
            1 + SWEEP_PCT
        )

        and

        sweep_close
        <
        range_high
    )


    bearish_body_pct = (

        confirm_open
        - confirm_close
    ) / confirm_open


    bearish_confirmation = (

        confirm_close
        <
        range_high

        and

        confirm_close
        <
        confirm_open

        and

        bearish_body_pct
        >= CONFIRM_BODY_MIN_PCT

        and

        confirm_close
        <
        (
            sweep_high
            + sweep_close
        ) / 2
    )


    if (
        bearish_sweep
        and
        bearish_confirmation
    ):

        entry = confirm_close


        # Stop beyond actual sweep extreme.

        stop = (
            sweep_high
            * (
                1 + SL_BUFFER_PCT
            )
        )


        risk = (
            stop
            - entry
        )


        if risk <= 0:

            return None


        tp1 = (
            entry
            - risk * TP1_R
        )


        theoretical_tp2 = (
            entry
            - risk * TP2_R
        )


        # Opposite side of range.

        opposite_range = (
            range_low
        )


        # We cannot demand more than the
        # available range.

        tp2 = max(
            theoretical_tp2,
            opposite_range
        )


        reward_r = (
            entry
            - tp2
        ) / risk


        # Require at least 1.5R.

        if reward_r < 1.5:

            return None


        return {

            "direction":
                "BEARISH",

            "signal_time":
                confirm["time"],

            "sweep_time":
                sweep["time"],

            "range_high":
                range_high,

            "range_low":
                range_low,

            "entry":
                entry,

            "stop":
                stop,

            "tp1":
                tp1,

            "tp2":
                tp2,

            "volume_ratio":
                vr,

            "range_width_pct":
                range_width_pct,

            "reward_r":
                reward_r
        }


    # ========================================================
    # BULLISH SWEEP
    # ========================================================

    bullish_sweep = (

        sweep_low
        <
        range_low
        * (
            1 - SWEEP_PCT
        )

        and

        sweep_close
        >
        range_low
    )


    bullish_body_pct = (

        confirm_close
        - confirm_open
    ) / confirm_open


    bullish_confirmation = (

        confirm_close
        >
        range_low

        and

        confirm_close
        >
        confirm_open

        and

        bullish_body_pct
        >= CONFIRM_BODY_MIN_PCT

        and

        confirm_close
        >
        (
            sweep_low
            + sweep_close
        ) / 2
    )


    if (
        bullish_sweep
        and
        bullish_confirmation
    ):

        entry = confirm_close


        stop = (
            sweep_low
            * (
                1 - SL_BUFFER_PCT
            )
        )


        risk = (
            entry
            - stop
        )


        if risk <= 0:

            return None


        tp1 = (
            entry
            + risk * TP1_R
        )


        theoretical_tp2 = (
            entry
            + risk * TP2_R
        )


        opposite_range = (
            range_high
        )


        tp2 = min(
            theoretical_tp2,
            opposite_range
        )


        reward_r = (
            tp2
            - entry
        ) / risk


        if reward_r < 1.5:

            return None


        return {

            "direction":
                "BULLISH",

            "signal_time":
                confirm["time"],

            "sweep_time":
                sweep["time"],

            "range_high":
                range_high,

            "range_low":
                range_low,

            "entry":
                entry,

            "stop":
                stop,

            "tp1":
                tp1,

            "tp2":
                tp2,

            "volume_ratio":
                vr,

            "range_width_pct":
                range_width_pct,

            "reward_r":
                reward_r
        }


    return None


# ============================================================
# ANALYZE CURRENT MARKET
# ============================================================

def analyze(df):

    # --------------------------------------------------------
    # Latest completed candle
    # --------------------------------------------------------

    if len(df) < (
        RANGE_CANDLES
        + VOLUME_LOOKBACK
        + 3
    ):

        return None


    # Last candle is completed because get_data()
    # removed the currently-forming candle.

    confirmation_index = (
        len(df) - 1
    )


    signal = find_signal(
        df,
        confirmation_index
    )


    return signal


# ============================================================
# DEFAULT STATE
# ============================================================

def default_state():

    return {

        "active":
            False,

        "last_heartbeat":
            "",

        "last_processed_event_id":
            "",

        "event_id":
            "",

        "direction":
            "",

        "entry":
            0,

        "stop_loss":
            0,

        "original_stop_loss":
            0,

        "tp1":
            0,

        "tp2":
            0,

        "level":
            0,

        "range_high":
            0,

        "range_low":
            0,

        "sweep_time":
            "",

        "confirmation_time":
            "",

        "volume_ratio":
            0,

        "range_width_pct":
            0,

        "reward_r":
            0,

        "tp1_hit":
            False,

        "tp2_hit":
            False,

        "sl_hit":
            False,

        "breakeven":
            False,

        "bars_in_trade":
            0
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


        defaults = default_state()


        for key, value in (
            defaults.items()
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
# LOAD HISTORY
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
# SAVE HISTORY
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
# DUPLICATE PROTECTION
# ============================================================

def event_already_processed(
    event_id,
    state
):

    if (
        state.get(
            "last_processed_event_id",
            ""
        )
        == event_id
    ):

        return True


    if (
        state.get(
            "event_id",
            ""
        )
        == event_id
    ):

        return True


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
    outcome,
    exit_price=None
):

    history = load_history()


    # --------------------------------------------------------
    # Prevent duplicate history record
    # --------------------------------------------------------

    event_id = state.get(
        "event_id",
        ""
    )


    for existing in history:

        if (
            existing.get(
                "event_id",
                ""
            )
            == event_id
        ):

            return


    entry = float(
        state.get(
            "entry",
            0
        )
    )


    stop = float(
        state.get(
            "original_stop_loss",
            state.get(
                "stop_loss",
                0
            )
        )
    )


    tp1 = float(
        state.get(
            "tp1",
            0
        )
    )


    tp2 = float(
        state.get(
            "tp2",
            0
        )
    )


    direction = state.get(
        "direction",
        ""
    )


    # --------------------------------------------------------
    # Calculate realized R
    # --------------------------------------------------------

    if outcome == "SL":

        result_r = -1.0


    elif outcome == "TP1_BE":

        # 50% made +1R.
        # Remaining 50% exited at breakeven.

        result_r = (
            TP1_PARTIAL
            * TP1_R
        )


    elif outcome == "TP2":

        # 50% at TP1 = +0.5R
        # 50% at TP2 = +1.0R
        #
        # Total = +1.5R

        result_r = (
            TP1_PARTIAL
            * TP1_R
            +
            (
                1
                - TP1_PARTIAL
            )
            * TP2_R
        )


    elif outcome == "TIME":

        if exit_price is None:

            result_r = 0.0

        else:

            if direction == "BULLISH":

                risk = (
                    entry
                    - stop
                )

                if risk > 0:

                    raw_r = (
                        exit_price
                        - entry
                    ) / risk

                else:

                    raw_r = 0.0

            else:

                risk = (
                    stop
                    - entry
                )

                if risk > 0:

                    raw_r = (
                        entry
                        - exit_price
                    ) / risk

                else:

                    raw_r = 0.0


            if state.get(
                "tp1_hit",
                False
            ):

                remaining_fraction = (
                    1
                    - TP1_PARTIAL
                )


                if direction == "BULLISH":

                    remaining_r = (
                        remaining_fraction
                        * raw_r
                    )

                else:

                    remaining_r = (
                        remaining_fraction
                        * raw_r
                    )


                result_r = (
                    TP1_PARTIAL
                    * TP1_R
                    +
                    remaining_r
                )

            else:

                result_r = raw_r


    else:

        result_r = 0.0


    trade = {

        "event_id":
            event_id,

        "symbol":
            SYMBOL,

        "strategy":
            "V4_RANGE_LIQUIDITY",

        "direction":
            direction,

        "entry":
            entry,

        "stop_loss":
            stop,

        "tp1":
            tp1,

        "tp2":
            tp2,

        "level":
            float(
                state.get(
                    "level",
                    0
                )
            ),

        "range_high":
            float(
                state.get(
                    "range_high",
                    0
                )
            ),

        "range_low":
            float(
                state.get(
                    "range_low",
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

        "range_width_pct":
            float(
                state.get(
                    "range_width_pct",
                    0
                )
            ),

        "reward_r":
            float(
                state.get(
                    "reward_r",
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

        "breakeven":
            bool(
                state.get(
                    "breakeven",
                    False
                )
            ),

        "outcome":
            outcome,

        "result_r":
            result_r,

        "exit_price":
            exit_price,

        "closed_at":
            pd.Timestamp.now(
                tz="UTC"
            ).isoformat()
    }


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
    signal
):

    chart_df = (
        df
        .tail(80)
        .copy()
        .reset_index(
            drop=True
        )
    )


    entry = float(
        signal["entry"]
    )


    stop = float(
        signal["stop"]
    )


    tp1 = float(
        signal["tp1"]
    )


    tp2 = float(
        signal["tp2"]
    )


    range_high = float(
        signal["range_high"]
    )


    range_low = float(
        signal["range_low"]
    )


    direction = signal[
        "direction"
    ]


    fig, ax = plt.subplots(
        figsize=(14, 8)
    )


    # ========================================================
    # CANDLES
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
    # RANGE HIGH
    # ========================================================

    ax.axhline(
        range_high,
        linestyle="--",
        linewidth=2,
        label=(
            f"Range High "
            f"${range_high:,.2f}"
        )
    )


    # ========================================================
    # RANGE LOW
    # ========================================================

    ax.axhline(
        range_low,
        linestyle="--",
        linewidth=2,
        label=(
            f"Range Low "
            f"${range_low:,.2f}"
        )
    )


    # ========================================================
    # ENTRY
    # ========================================================

    ax.axhline(
        entry,
        linewidth=2,
        label=(
            f"Entry "
            f"${entry:,.2f}"
        )
    )


    # ========================================================
    # STOP
    # ========================================================

    ax.axhline(
        stop,
        linestyle="--",
        linewidth=2,
        label=(
            f"Stop "
            f"${stop:,.2f}"
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
    # SWEEP MARKER
    # ========================================================

    sweep_time = signal[
        "sweep_time"
    ]


    matches = chart_df[
        chart_df["time"]
        ==
        sweep_time
    ]


    if not matches.empty:

        sweep_index = int(
            matches.index[0]
        )


        sweep = chart_df.iloc[
            sweep_index
        ]


        if direction == "BEARISH":

            marker_price = float(
                sweep["high"]
            )

        else:

            marker_price = float(
                sweep["low"]
            )


        ax.scatter(
            sweep_index,
            marker_price,
            s=180,
            marker="*",
            zorder=5,
            label="Liquidity Sweep"
        )


    # ========================================================
    # TITLE
    # ========================================================

    ax.set_title(
        (
            f"BTCUSDT 5m V4 "
            f"Range Liquidity Sweep — "
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

def create_trade(
    df,
    signal,
    previous_state
):

    direction = signal[
        "direction"
    ]


    entry = float(
        signal["entry"]
    )


    stop = float(
        signal["stop"]
    )


    tp1 = float(
        signal["tp1"]
    )


    tp2 = float(
        signal["tp2"]
    )


    range_high = float(
        signal["range_high"]
    )


    range_low = float(
        signal["range_low"]
    )


    volume = float(
        signal["volume_ratio"]
    )


    reward_r = float(
        signal["reward_r"]
    )


    sweep_time = str(
        signal["sweep_time"]
    )


    confirmation_time = str(
        signal["signal_time"]
    )


    # ========================================================
    # EVENT ID
    # ========================================================

    event_id = (

        f"{sweep_time}|"

        f"{confirmation_time}|"

        f"{direction}|"

        f"{range_high:.2f}|"

        f"{range_low:.2f}"
    )


    # ========================================================
    # STATE
    # ========================================================

    state = {

        "active":
            True,

        "last_heartbeat":
            previous_state.get(
                "last_heartbeat",
                ""
            ),

        "last_processed_event_id":
            event_id,

        "event_id":
            event_id,

        "direction":
            direction,

        "entry":
            entry,

        "stop_loss":
            stop,

        "original_stop_loss":
            stop,

        "tp1":
            tp1,

        "tp2":
            tp2,

        # Keep the range high as the reference
        # level for bearish and range low for bullish.

        "level":
            (
                range_high
                if direction == "BEARISH"
                else range_low
            ),

        "range_high":
            range_high,

        "range_low":
            range_low,

        "sweep_time":
            sweep_time,

        "confirmation_time":
            confirmation_time,

        "volume_ratio":
            volume,

        "range_width_pct":
            float(
                signal[
                    "range_width_pct"
                ]
            ),

        "reward_r":
            reward_r,

        "tp1_hit":
            False,

        "tp2_hit":
            False,

        "sl_hit":
            False,

        "breakeven":
            False,

        "bars_in_trade":
            0
    }


    emoji = (
        "🔴"
        if direction == "BEARISH"
        else "🟢"
    )


    # ========================================================
    # TELEGRAM SETUP MESSAGE
    # ========================================================

    message = f"""
{emoji} BTC V4 PAPER SETUP

Strategy:
Range Liquidity Sweep

Direction:
{direction}

━━━━━━━━━━━━━━━━━━

📦 RANGE

High:
${range_high:,.2f}

Low:
${range_low:,.2f}

Range width:
{signal["range_width_pct"] * 100:.2f}%

━━━━━━━━━━━━━━━━━━

💧 LIQUIDITY SWEEP

Sweep:
{sweep_time}

Volume:
{volume:.2f}x average

Confirmation:
{confirmation_time}

━━━━━━━━━━━━━━━━━━

📍 ENTRY

${entry:,.2f}

🛑 STOP LOSS

${stop:,.2f}

🎯 TP1

${tp1:,.2f}

🎯 TP2

${tp2:,.2f}

Planned reward:
{reward_r:.2f}R

━━━━━━━━━━━━━━━━━━

🤖 PAPER TRADE

Position model:

50% → TP1
50% → TP2

After TP1:
SL → breakeven

━━━━━━━━━━━━━━━━━━

⚠️ MANUAL TRADE ONLY

No real order has been placed.
"""


    # ========================================================
    # CHART
    # ========================================================

    chart_file = create_chart(
        df,
        signal
    )


    telegram(
        message
    )


    caption = (
        f"{emoji} BTC V4 "
        f"{direction} PAPER SETUP\n\n"

        f"Entry: "
        f"${entry:,.2f}\n"

        f"SL: "
        f"${stop:,.2f}\n"

        f"TP1: "
        f"${tp1:,.2f}\n"

        f"TP2: "
        f"${tp2:,.2f}\n\n"

        f"Volume: "
        f"{volume:.2f}x\n"

        f"Range: "
        f"{signal['range_width_pct'] * 100:.2f}%\n\n"

        f"🤖 V4 paper tracker active"
    )


    send_chart(
        chart_file,
        caption
    )


    return state


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


    original_stop = float(
        state[
            "original_stop_loss"
        ]
    )


    current_stop = float(
        state[
            "stop_loss"
        ]
    )


    tp1 = float(
        state["tp1"]
    )


    tp2 = float(
        state["tp2"]
    )


    # --------------------------------------------------------
    # Only latest completed candle.
    # --------------------------------------------------------

    candle = df.iloc[
        -1
    ]


    high = float(
        candle["high"]
    )


    low = float(
        candle["low"]
    )


    close = float(
        candle["close"]
    )


    state[
        "bars_in_trade"
    ] = int(
        state.get(
            "bars_in_trade",
            0
        )
    ) + 1


    changed = True


    # ========================================================
    # BULLISH
    # ========================================================

    if direction == "BULLISH":

        # ----------------------------------------------------
        # STOP
        # ----------------------------------------------------

        if low <= current_stop:

            if state.get(
                "breakeven",
                False
            ):

                telegram(
                    f"""
🟡 BTC V4 PAPER TRADE

BULLISH

TP1 was already hit.

Price returned to entry.

⚖️ BREAKEVEN EXIT

Entry:
${entry:,.2f}

Exit:
${entry:,.2f}

Result:
+{TP1_PARTIAL * TP1_R:.1f}R

Paper trade closed.
"""
                )


                record_completed_trade(
                    state,
                    "TP1_BE",
                    entry
                )

            else:

                telegram(
                    f"""
🔴 BTC V4 PAPER TRADE

BULLISH

🛑 STOP LOSS HIT

Entry:
${entry:,.2f}

Stop:
${current_stop:,.2f}

TP1:
${tp1:,.2f}

TP2:
${tp2:,.2f}

Result:
-1R

Paper trade closed.
"""
                )


                state[
                    "sl_hit"
                ] = True


                record_completed_trade(
                    state,
                    "SL",
                    current_stop
                )


            state[
                "active"
            ] = False


            return True


        # ----------------------------------------------------
        # TP1
        # ----------------------------------------------------

        if (
            not state[
                "tp1_hit"
            ]
            and
            high >= tp1
        ):

            state[
                "tp1_hit"
            ] = True


            if MOVE_SL_TO_BREAKEVEN:

                state[
                    "stop_loss"
                ] = entry

                state[
                    "breakeven"
                ] = True


            telegram(
                f"""
🟢 BTC V4 PAPER TRADE

BULLISH

🎯 TP1 HIT

Entry:
${entry:,.2f}

TP1:
${tp1:,.2f}

TP2:
${tp2:,.2f}

50% position:
+1R

Remaining:
50%

🛡️ Stop moved to breakeven.

Paper trade remains active.
"""
            )


            save_state(
                state
            )


        # ----------------------------------------------------
        # TP2
        # ----------------------------------------------------

        if (
            not state[
                "tp2_hit"
            ]
            and
            low < tp2 + (
                tp2 * 0.0000001
            )
            and
            high >= tp2
        ):

            # This branch is intentionally
            # replaced below for direction handling.
            pass


        if (
            not state[
                "tp2_hit"
            ]
            and
            high >= tp2
        ):

            state[
                "tp2_hit"
            ] = True


            telegram(
                f"""
🟢 BTC V4 PAPER TRADE

BULLISH

🎯🎯 TP2 HIT

Entry:
${entry:,.2f}

TP1:
${tp1:,.2f}

TP2:
${tp2:,.2f}

50% at TP1:
+0.5R

50% at TP2:
+1.0R

Total:
+1.5R

Paper trade complete.
"""
            )


            record_completed_trade(
                state,
                "TP2",
                tp2
            )


            state[
                "active"
            ] = False


            return True


    # ========================================================
    # BEARISH
    # ========================================================

    else:

        # ----------------------------------------------------
        # STOP
        # ----------------------------------------------------

        if high >= current_stop:

            if state.get(
                "breakeven",
                False
            ):

                telegram(
                    f"""
🟡 BTC V4 PAPER TRADE

BEARISH

TP1 was already hit.

Price returned to entry.

⚖️ BREAKEVEN EXIT

Entry:
${entry:,.2f}

Exit:
${entry:,.2f}

Result:
+{TP1_PARTIAL * TP1_R:.1f}R

Paper trade closed.
"""
                )


                record_completed_trade(
                    state,
                    "TP1_BE",
                    entry
                )

            else:

                telegram(
                    f"""
🔴 BTC V4 PAPER TRADE

BEARISH

🛑 STOP LOSS HIT

Entry:
${entry:,.2f}

Stop:
${current_stop:,.2f}

TP1:
${tp1:,.2f}

TP2:
${tp2:,.2f}

Result:
-1R

Paper trade closed.
"""
                )


                state[
                    "sl_hit"
                ] = True


                record_completed_trade(
                    state,
                    "SL",
                    current_stop
                )


            state[
                "active"
            ] = False


            return True


        # ----------------------------------------------------
        # TP1
        # ----------------------------------------------------

        if (
            not state[
                "tp1_hit"
            ]
            and
            low <= tp1
        ):

            state[
                "tp1_hit"
            ] = True


            if MOVE_SL_TO_BREAKEVEN:

                state[
                    "stop_loss"
                ] = entry

                state[
                    "breakeven"
                ] = True


            telegram(
                f"""
🔴 BTC V4 PAPER TRADE

BEARISH

🎯 TP1 HIT

Entry:
${entry:,.2f}

TP1:
${tp1:,.2f}

TP2:
${tp2:,.2f}

50% position:
+1R

Remaining:
50%

🛡️ Stop moved to breakeven.

Paper trade remains active.
"""
            )


            save_state(
                state
            )


        # ----------------------------------------------------
        # TP2
        # ----------------------------------------------------

        if (
            not state[
                "tp2_hit"
            ]
            and
            low <= tp2
        ):

            state[
                "tp2_hit"
            ] = True


            telegram(
                f"""
🔴 BTC V4 PAPER TRADE

BEARISH

🎯🎯 TP2 HIT

Entry:
${entry:,.2f}

TP1:
${tp1:,.2f}

TP2:
${tp2:,.2f}

50% at TP1:
+0.5R

50% at TP2:
+1.0R

Total:
+1.5R

Paper trade complete.
"""
            )


            record_completed_trade(
                state,
                "TP2",
                tp2
            )


            state[
                "active"
            ] = False


            return True


    # ========================================================
    # TIME EXIT
    # ========================================================

    if (
        state[
            "bars_in_trade"
        ]
        >= MAX_BARS_IN_TRADE
    ):

        telegram(
            f"""
🟡 BTC V4 PAPER TRADE

TIME EXIT

Direction:
{direction}

Entry:
${entry:,.2f}

Current price:
${close:,.2f}

Trade duration:
{state["bars_in_trade"]} candles

The maximum trade duration
has been reached.

Paper trade closed.
"""
        )


        record_completed_trade(
            state,
            "TIME",
            close
        )


        state[
            "active"
        ] = False


        return True


    return changed


# ============================================================
# EOD REPORT
# ============================================================

def send_eod_report():

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

            date = pd.to_datetime(
                closed_at,
                utc=True
            ).strftime(
                "%Y-%m-%d"
            )

        except Exception:

            continue


        if date == today:

            today_trades.append(
                trade
            )


    total = len(
        today_trades
    )


    wins = sum(
        1
        for t in today_trades
        if float(
            t.get(
                "result_r",
                0
            )
        ) > 0
    )


    losses = sum(
        1
        for t in today_trades
        if float(
            t.get(
                "result_r",
                0
            )
        ) < 0
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


    if total:

        win_rate = (
            wins
            / total
            * 100
        )

    else:

        win_rate = 0


    if total == 0:

        report = f"""
📊 BTC V4 DAILY PAPER REPORT

Date:
{today}

No completed trades today.

━━━━━━━━━━━━━━━━━━

Strategy:
V4 Range Liquidity Sweep

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
📊 BTC V4 DAILY PAPER REPORT

Date:
{today}

━━━━━━━━━━━━━━━━━━

📈 PERFORMANCE

Completed trades:
{total}

Profitable:
{wins}

Losing:
{losses}

Win rate:
{win_rate:.1f}%

━━━━━━━━━━━━━━━━━━

🎯 TARGETS

TP1 hits:
{tp1_hits}

TP2 hits:
{tp2_hits}

🛑 Stop losses:
{sl_hits}

━━━━━━━━━━━━━━━━━━

📊 RESULT

Total:
{total_r:+.2f}R

Average:
{total_r / total:+.3f}R

━━━━━━━━━━━━━━━━━━

Strategy:
V4 Range Liquidity Sweep

Market:
BTCUSDT

Timeframe:
5m

🤖 PAPER TRADING ONLY
"""


    telegram(
        report
    )


# ============================================================
# MAIN
# ============================================================

def main():

    # ========================================================
    # EOD
    # ========================================================

    if (
        len(sys.argv) > 1
        and
        sys.argv[1].lower()
        == "--eod"
    ):

        send_eod_report()

        return


    print(
        "BTC V4 Liquidity Bot started"
    )


    # ========================================================
    # DATA
    # ========================================================

    df = get_data()


    print(
        f"Loaded {len(df)} completed candles."
    )


    if len(df) < 50:

        print(
            "Not enough completed candles."
        )

        return


    # ========================================================
    # STATE
    # ========================================================

    state = load_state()


    # ========================================================
    # HEARTBEAT
    # ========================================================

    try:

        if send_heartbeat(
            state
        ):

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
    # ACTIVE PAPER TRADE
    # ========================================================

    if state.get(
        "active"
    ):

        print(
            "Active V4 paper trade found."
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
                "Active paper trade remains open."
            )


        # Never search for another setup
        # while one is active.

        return


    # ========================================================
    # FIND NEW V4 SIGNAL
    # ========================================================

    signal = analyze(
        df
    )


    if signal is None:

        print(
            "No new confirmed V4 setup."
        )

        return


    print(
        "V4 signal detected:",
        signal["direction"],
        signal["signal_time"]
    )


    # ========================================================
    # EVENT ID
    # ========================================================

    event_id = (

        f"{signal['sweep_time']}|"

        f"{signal['signal_time']}|"

        f"{signal['direction']}|"

        f"{float(signal['range_high']):.2f}|"

        f"{float(signal['range_low']):.2f}"
    )


    print(
        "Event ID:",
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
            "V4 signal already processed."
        )

        print(
            "No duplicate alert."
        )

        return


    # ========================================================
    # CREATE TRADE
    # ========================================================

    new_state = create_trade(
        df,
        signal,
        state
    )


    # ========================================================
    # SAVE
    # ========================================================

    save_state(
        new_state
    )


    print(
        "V4 paper trade created."
    )

    print(
        "Telegram alert sent."
    )

    print(
        "Trade state saved."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
