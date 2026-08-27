import os
import time
import requests
import pandas as pd


# ============================================================
# SETTINGS
# ============================================================

SYMBOL = "BTCUSDT"
INTERVAL = "5m"
CANDLES = 200

SWING_LOOKBACK = 3
ZONE_TOLERANCE = 0.001

SWEEP_PCT = 0.0005
VOLUME_MULTIPLIER = 1.2

CONFIRM_CANDLES = 2
COOLDOWN_MINUTES = 30

STATE_FILE = "last_alert.txt"


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
# BINANCE DATA
# ============================================================

def get_data():

    url = "https://api.binance.com/api/v3/klines"

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

    numeric = [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]

    for col in numeric:
        df[col] = pd.to_numeric(
            df[col],
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

        high = df.iloc[i]["high"]
        low = df.iloc[i]["low"]

        left_high = df.iloc[
            i - n:i
        ]["high"].max()

        right_high = df.iloc[
            i + 1:i + n + 1
        ]["high"].max()

        left_low = df.iloc[
            i - n:i
        ]["low"].min()

        right_low = df.iloc[
            i + 1:i + n + 1
        ]["low"].min()

        if (
            high > left_high
            and high >= right_high
        ):
            highs.append(float(high))

        if (
            low < left_low
            and low <= right_low
        ):
            lows.append(float(low))

    return highs, lows


# ============================================================
# CLUSTER LIQUIDITY LEVELS
# ============================================================

def cluster(levels):

    zones = []

    for price in levels:

        price = float(price)

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
# CHECK FOR LIQUIDITY SWEEP
# ============================================================

def find_sweep(
    df,
    index,
    direction
):

    if index < 30:
        return None

    # Only use candles BEFORE the sweep.
    history = df.iloc[:index].copy()

    highs, lows = find_swings(history)

    high_zones = cluster(highs)
    low_zones = cluster(lows)

    candle = df.iloc[index]

    price = float(candle["close"])

    # --------------------------------------------------------
    # BUY-SIDE LIQUIDITY
    # Price runs above previous highs and closes back below.
    # This creates a bearish setup.
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
    # SELL-SIDE LIQUIDITY
    # Price runs below previous lows and closes back above.
    # This creates a bullish setup.
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
# VOLUME CHECK
# ============================================================

def volume_ratio(df, index):

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
        float(df.iloc[index]["volume"])
        / float(average)
    )


# ============================================================
# FIND MOST RECENT CONFIRMED SIGNAL
# ============================================================

def analyze(df):

    # Ignore currently forming candle.
    df = df.iloc[:-1].copy()

    minimum = (
        30 + CONFIRM_CANDLES
    )

    if len(df) < minimum:
        return None

    # Search from newest possible confirmed event backwards.
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

        # ----------------------------------------------------
        # BEARISH
        # ----------------------------------------------------

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

                if check_confirmation(
                    df,
                    sweep_index,
                    bearish_level,
                    "BEARISH"
                ):

                    return {
                        "direction": "BEARISH",
                        "level": bearish_level,
                        "price": float(
                            df.iloc[confirmation_end]["close"]
                        ),
                        "volume_ratio": ratio,
                        "time": df.iloc[
                            sweep_index
                        ]["time"],
                        "confirmation_time": df.iloc[
                            confirmation_end
                        ]["time"]
                    }

        # ----------------------------------------------------
        # BULLISH
        # ----------------------------------------------------

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

                if check_confirmation(
                    df,
                    sweep_index,
                    bullish_level,
                    "BULLISH"
                ):

                    return {
                        "direction": "BULLISH",
                        "level": bullish_level,
                        "price": float(
                            df.iloc[confirmation_end]["close"]
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
# STATE / DUPLICATE PROTECTION
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


def save_state(event_id):

    with open(
        STATE_FILE,
        "w"
    ) as f:

        f.write(event_id)


# ============================================================
# MAIN
# ============================================================

def main():

    print("BTC Liquidity Bot started")

    df = get_data()

    print(
        f"Loaded {len(df)} candles"
    )

    signal = analyze(df)

    if signal is None:

        print(
            "No confirmed liquidity sweep detected."
        )

        return

    direction = signal["direction"]
    level = signal["level"]
    price = signal["price"]
    volume = signal["volume_ratio"]

    signal_time = str(
        signal["time"]
    )

    confirmation_time = str(
        signal["confirmation_time"]
    )

    event_id = (
        f"{signal_time}|"
        f"{direction}|"
        f"{level:.2f}"
    )

    last_alert = load_state()

    # --------------------------------------------------------
    # EXACT DUPLICATE PROTECTION
    # --------------------------------------------------------

    if event_id == last_alert:

        print(
            "Duplicate signal - alert skipped."
        )

        return

    # --------------------------------------------------------
    # COOLDOWN
    # --------------------------------------------------------

    if last_alert:

        try:

            previous_time = pd.to_datetime(
                last_alert.split("|")[0],
                utc=True
            )

            current_time = pd.to_datetime(
                signal_time,
                utc=True
            )

            minutes = (
                current_time
                - previous_time
            ).total_seconds() / 60

            if (
                minutes >= 0
                and minutes < COOLDOWN_MINUTES
            ):

                print(
                    f"Cooldown active: "
                    f"{minutes:.1f} minutes"
                )

                return

        except Exception:

            pass

    # --------------------------------------------------------
    # TELEGRAM MESSAGE
    # --------------------------------------------------------

    if direction == "BEARISH":
        emoji = "🔴"
    else:
        emoji = "🟢"

    message = f"""
{emoji} BTC LIQUIDITY EVENT

Direction: {direction}

Liquidity level:
${level:,.2f}

Price:
${price:,.2f}

Sweep volume:
{volume:.2f}x average

Sweep candle:
{signal_time}

Confirmation:
{CONFIRM_CANDLES} candles

Confirmed at:
{confirmation_time}

⚠️ Liquidity sweep confirmed.
Wait for manual trade confirmation.
"""

    telegram(message)

    print(message)

    # Save only AFTER successful Telegram delivery.
    save_state(event_id)

    print(
        "Alert sent and state saved."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
