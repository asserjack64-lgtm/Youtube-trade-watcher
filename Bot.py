import os
import time
import requests
import pandas as pd

# ==============================
# SETTINGS
# ==============================

SYMBOL = "BTCUSDT"
INTERVAL = "5m"

CANDLES = 200

SWING_LOOKBACK = 3
ZONE_TOLERANCE = 0.0015

SWEEP_PCT = 0.0005
VOLUME_MULTIPLIER = 1.5

CONFIRM_CANDLES = 4

# Don't repeatedly alert the same event
COOLDOWN_MINUTES = 30


# ==============================
# TELEGRAM
# ==============================

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def telegram(message):

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    data = {
        "chat_id": CHAT_ID,
        "text": message
    }

    r = requests.post(
        url,
        data=data,
        timeout=20
    )

    r.raise_for_status()


# ==============================
# BINANCE DATA
# ==============================

def get_data():

    url = (
        "https://api.binance.com/api/v3/klines"
    )

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "limit": CANDLES
    }

    r = requests.get(
        url,
        params=params,
        timeout=20
    )

    r.raise_for_status()

    data = r.json()

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
            df[col]
        )

    df["time"] = pd.to_datetime(
        df["time"],
        unit="ms",
        utc=True
    )

    return df


# ==============================
# FIND SWINGS
# ==============================

def find_swings(df):

    highs = []
    lows = []

    n = SWING_LOOKBACK

    for i in range(
        n,
        len(df) - n
    ):

        high = df.iloc[i]["high"]
        low = df.iloc[i]["low"]

        left_high = df.iloc[
            i-n:i
        ]["high"].max()

        right_high = df.iloc[
            i+1:i+n+1
        ]["high"].max()

        left_low = df.iloc[
            i-n:i
        ]["low"].min()

        right_low = df.iloc[
            i+1:i+n+1
        ]["low"].min()

        if (
            high > left_high
            and
            high >= right_high
        ):
            highs.append(high)

        if (
            low < left_low
            and
            low <= right_low
        ):
            lows.append(low)

    return highs, lows


# ==============================
# CLUSTER LEVELS
# ==============================

def cluster(levels):

    zones = []

    for price in levels:

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


# ==============================
# ANALYZE
# ==============================

def analyze(df):

    # Ignore currently forming candle
    df = df.iloc[:-1].copy()

    if len(df) < 50:
        return None

    highs, lows = find_swings(df)

    high_zones = cluster(highs)
    low_zones = cluster(lows)

    last = df.iloc[-1]

    previous = df.iloc[-2]

    price = last["close"]

    avg_volume = (
        df["volume"]
        .iloc[-21:-1]
        .mean()
    )

    # =================================
    # CHECK BUY-SIDE LIQUIDITY
    # =================================

    for level in high_zones:

        if level <= price:
            continue

        distance = (
            level - price
        ) / price

        if distance > 0.003:
            continue

        swept = (
            last["high"]
            >
            level * (1 + SWEEP_PCT)
        )

        reclaimed = (
            last["close"] < level
        )

        if swept and reclaimed:

            volume_ratio = (
                last["volume"]
                / avg_volume
            )

            return {
                "direction": "BEARISH",
                "level": level,
                "price": price,
                "volume_ratio": volume_ratio,
                "time": last["time"]
            }


    # =================================
    # CHECK SELL-SIDE LIQUIDITY
    # =================================

    for level in low_zones:

        if level >= price:
            continue

        distance = (
            price - level
        ) / price

        if distance > 0.003:
            continue

        swept = (
            last["low"]
            <
            level * (1 - SWEEP_PCT)
        )

        reclaimed = (
            last["close"] > level
        )

        if swept and reclaimed:

            volume_ratio = (
                last["volume"]
                / avg_volume
            )

            return {
                "direction": "BULLISH",
                "level": level,
                "price": price,
                "volume_ratio": volume_ratio,
                "time": last["time"]
            }

    return None


# ==============================
# MAIN
# ==============================

def main():

    print("BTC Liquidity Bot started")

    df = get_data()

    signal = analyze(df)

    if signal is None:
        print("No liquidity sweep detected.")
        return

    direction = signal["direction"]
    level = signal["level"]
    price = signal["price"]
    signal_time = str(signal["time"])

    # Persistent duplicate protection
    state_file = "last_alert.txt"

    try:
        with open(state_file, "r") as f:
            last_alert = f.read().strip()
    except FileNotFoundError:
        last_alert = ""

    event_id = f"{signal_time}|{direction}|{level:.2f}"

    if event_id == last_alert:
        print("Duplicate signal - alert skipped.")
        return

    emoji = "🔴" if direction == "BEARISH" else "🟢"

    message = f"""
{emoji} BTC LIQUIDITY EVENT

Direction: {direction}

Liquidity level:
${level:,.2f}

Current price:
${price:,.2f}

Volume:
{signal["volume_ratio"]:.2f}x average

Time:
{signal_time}

⚠️ Liquidity sweep detected.
Wait for manual confirmation.
"""

    telegram(message)

    print(message)

    # Remember this exact event
    with open(state_file, "w") as f:
        f.write(event_id)


if __name__ == "__main__":
    main()
    main()
