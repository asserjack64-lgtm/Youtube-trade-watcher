import os
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

STATE_FILE = "last_alert.txt"
CHART_FILE = "btc_liquidity_setup.png"


# ============================================================
# TELEGRAM SETTINGS
# ============================================================

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ============================================================
# SEND TELEGRAM TEXT
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

    data = {
        "chat_id": CHAT_ID,
        "text": message
    }

    response = requests.post(
        url,
        data=data,
        timeout=20
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# SEND TELEGRAM CHART
# ============================================================

def send_chart(chart_file, message):

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
                "caption": message
            },
            files={
                "photo": photo
            },
            timeout=30
        )

    response.raise_for_status()

    return response.json()


# ============================================================
# GET BINANCE DATA
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

    if len(df) < (n * 2 + 1):
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

            highs.append(
                {
                    "index": i,
                    "price": high
                }
            )

        if (
            low < left_low
            and low <= right_low
        ):

            lows.append(
                {
                    "index": i,
                    "price": low
                }
            )

    return highs, lows


# ============================================================
# CLUSTER LIQUIDITY LEVELS
# ============================================================

def cluster(levels):

    zones = []

    for item in levels:

        if isinstance(item, dict):
            price = float(
                item["price"]
            )
        else:
            price = float(item)

        found = False

        for zone in zones:

            if (
                abs(price - zone)
                / zone
                <= ZONE_TOLERANCE
            ):

                zones.remove(zone)

                zones.append(
                    (zone + price) / 2
                )

                found = True

                break

        if not found:

            zones.append(price)

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

    # Only candles BEFORE the sweep.
    history = df.iloc[:index].copy()

    highs, lows = find_swings(history)

    high_zones = cluster(highs)
    low_zones = cluster(lows)

    candle = df.iloc[index]

    price = float(
        candle["close"]
    )


    # ========================================================
    # BEARISH SETUP
    # Price sweeps previous high and closes below it.
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
                level * (1 + SWEEP_PCT)
            )

            reclaimed = (
                float(candle["close"])
                < level
            )

            if swept and reclaimed:

                return float(level)


    # ========================================================
    # BULLISH SETUP
    # Price sweeps previous low and closes above it.
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
# CHECK CONFIRMATION
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

        for _, candle in confirmation.iterrows():

            if (
                float(candle["close"])
                >= level
            ):

                return False

    else:

        for _, candle in confirmation.iterrows():

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
        / float(average)
    )


# ============================================================
# ANALYZE DATA
# ============================================================

def analyze(df):

    # Ignore currently forming candle.
    df = df.iloc[:-1].copy()

    minimum = (
        30 + CONFIRM_CANDLES
    )

    if len(df) < minimum:
        return None


    latest_index = len(df) - 1

    first_index = 30


    for confirmation_end in range(
        latest_index,
        first_index + CONFIRM_CANDLES - 1,
        -1
    ):

        sweep_index = (
            confirmation_end
            - CONFIRM_CANDLES
        )

        if sweep_index < first_index:
            continue


        # ====================================================
        # BEARISH
        # ====================================================

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


        # ====================================================
        # BULLISH
        # ====================================================

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
# LOAD LAST ALERT
# ============================================================

def load_state():

    try:

        with open(
            STATE_FILE,
            "r"
        ) as f:

            return f.read().strip()

    except FileNotFoundError:

        return ""


# ============================================================
# SAVE LAST ALERT
# ============================================================

def save_state(event_id):

    with open(
        STATE_FILE,
        "w"
    ) as f:

        f.write(event_id)


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
        df.tail(60)
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


    # ========================================================
    # CANDLESTICKS
    # ========================================================

    for i, candle in chart_df.iterrows():

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


        if close_price >= open_price:

            candle_color = "green"

        else:

            candle_color = "red"


        # Wick

        ax.plot(
            [i, i],
            [
                low_price,
                high_price
            ],
            color=candle_color,
            linewidth=1
        )


        # Body

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
    # LIQUIDITY LEVEL
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
    # FIND TRENDLINE
    # ========================================================

    highs, lows = find_swings(
        chart_df
    )


    if direction == "BULLISH":

        if len(lows) >= 2:

            p1 = lows[-2]
            p2 = lows[-1]

            x1 = p1["index"]
            x2 = p2["index"]

            y1 = p1["price"]
            y2 = p2["price"]

            # Extend trendline to latest candle.

            if x2 != x1:

                slope = (
                    y2 - y1
                ) / (
                    x2 - x1
                )

                x3 = len(chart_df) - 1

                y3 = (
                    y2
                    + slope
                    * (x3 - x2)
                )

                ax.plot(
                    [x1, x2, x3],
                    [y1, y2, y3],
                    linewidth=3,
                    label="Bullish trendline"
                )


    else:

        if len(highs) >= 2:

            p1 = highs[-2]
            p2 = highs[-1]

            x1 = p1["index"]
            x2 = p2["index"]

            y1 = p1["price"]
            y2 = p2["price"]

            # Extend trendline to latest candle.

            if x2 != x1:

                slope = (
                    y2 - y1
                ) / (
                    x2 - x1
                )

                x3 = len(chart_df) - 1

                y3 = (
                    y2
                    + slope
                    * (x3 - x2)
                )

                ax.plot(
                    [x1, x2, x3],
                    [y1, y2, y3],
                    linewidth=3,
                    label="Bearish trendline"
                )


    # ========================================================
    # MARK SWEEP CANDLE
    # ========================================================

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
            s=120,
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
# MAIN
# ============================================================

def main():

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
    # ANALYZE
    # ========================================================

    signal = analyze(df)


    if signal is None:

        print(
            "No confirmed liquidity sweep detected."
        )

        return


    direction = signal[
        "direction"
    ]

    level = signal[
        "level"
    ]

    price = signal[
        "price"
    ]

    volume = signal[
        "volume_ratio"
    ]


    signal_time = str(
        signal["time"]
    )

    confirmation_time = str(
        signal["confirmation_time"]
    )


    # ========================================================
    # EVENT ID
    # ========================================================

    event_id = (
        f"{signal_time}|"
        f"{direction}|"
        f"{level:.2f}"
    )


    last_alert = load_state()


    # ========================================================
    # DUPLICATE PROTECTION
    # ========================================================

    if event_id == last_alert:

        print(
            "Duplicate signal - "
            "alert skipped."
        )

        return


    # ========================================================
    # COOLDOWN
    # ========================================================

    if last_alert:

        try:

            previous_time = (
                pd.to_datetime(
                    last_alert.split("|")[0],
                    utc=True
                )
            )


            current_time = (
                pd.to_datetime(
                    signal_time,
                    utc=True
                )
            )


            minutes = (
                current_time
                - previous_time
            ).total_seconds() / 60


            if (
                minutes >= 0
                and
                minutes < COOLDOWN_MINUTES
            ):

                print(
                    f"Cooldown active: "
                    f"{minutes:.1f} minutes"
                )

                return


        except Exception:

            pass


    # ========================================================
    # ENTRY / STOP / TARGETS
    # ========================================================

    if direction == "BEARISH":

        emoji = "🔴"

        entry = price

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
            - (risk * 2)
        )


    else:

        emoji = "🟢"

        entry = price

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
            + (risk * 2)
        )


    # ========================================================
    # TELEGRAM MESSAGE
    # ========================================================

    message = f"""
{emoji} BTC LIQUIDITY EVENT

Direction: {direction}

Liquidity level:
${level:,.2f}

Current price:
${price:,.2f}

Sweep volume:
{volume:.2f}x average

Sweep candle:
{signal_time}

Confirmation:
{CONFIRM_CANDLES} candles

Confirmed at:
{confirmation_time}

━━━━━━━━━━━━━━━━━━

📍 Reference Entry:
${entry:,.2f}

🛑 Reference Stop Loss:
${stop_loss:,.2f}

🎯 Reference TP1:
${tp1:,.2f}

🎯 Reference TP2:
${tp2:,.2f}

📊 Risk/Reward:
1:1 → TP1
1:2 → TP2

⚠️ MANUAL TRADE

Liquidity sweep confirmed.
Review the setup before entering.
"""


    # ========================================================
    # CREATE CHART
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
    # SEND TEXT MESSAGE
    # ========================================================

    telegram(message)


    print(
        "Telegram text alert sent."
    )


    # ========================================================
    # SEND CHART
    # ========================================================

    chart_caption = (
        f"{emoji} BTC "
        f"{direction} SETUP\n\n"
        f"Entry: ${entry:,.2f}\n"
        f"Stop Loss: ${stop_loss:,.2f}\n"
        f"TP1: ${tp1:,.2f}\n"
        f"TP2: ${tp2:,.2f}\n\n"
        f"Volume: {volume:.2f}x average\n\n"
        f"⚠️ Manual trade setup\n"
        f"Review before entering."
    )


    send_chart(
        chart_file,
        chart_caption
    )


    print(
        "Chart sent to Telegram."
    )


    # ========================================================
    # SAVE STATE
    # ONLY AFTER BOTH MESSAGES SUCCEED
    # ========================================================

    save_state(
        event_id
    )


    print(
        "Alert sent, chart sent "
        "and state saved."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
