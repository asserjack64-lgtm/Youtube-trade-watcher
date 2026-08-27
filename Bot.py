
import os
import json
import argparse
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# SETTINGS
# ============================================================

SYMBOL = "BTCUSDT"
INTERVAL = "5m"
CANDLES = 250

SWING_LOOKBACK = 3
ZONE_TOLERANCE = 0.001
TRENDLINE_TOLERANCE = 0.0015
MAX_TRENDLINE_SWING_GAP = 80
MIN_TRENDLINE_TOUCHES = 2

SWEEP_PCT = 0.0005
MAX_LEVEL_DISTANCE = 0.003
VOLUME_MULTIPLIER = 1.5

CONFIRM_CANDLES = 2
COOLDOWN_MINUTES = 30
MIN_SETUP_SCORE = 7

STATE_FILE = "trade_state.json"
HISTORY_FILE = "trade_history.json"
CHART_FILE = "btc_liquidity_setup.png"


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

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={"chat_id": CHAT_ID, "text": message},
        timeout=20
    )
    response.raise_for_status()
    return response.json()


def send_chart(chart_file, caption):
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    if not CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is missing")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"

    with open(chart_file, "rb") as photo:
        response = requests.post(
            url,
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"photo": photo},
            timeout=30
        )

    response.raise_for_status()
    return response.json()


# ============================================================
# BINANCE DATA
# ============================================================

def get_data():
    url = "https://data-api.binance.vision/api/v3/klines"

    response = requests.get(
        url,
        params={
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "limit": CANDLES
        },
        timeout=20
    )
    response.raise_for_status()

    columns = [
        "time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_volume", "taker_buy_quote_volume", "unused"
    ]

    df = pd.DataFrame(response.json(), columns=columns)

    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)

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

    for i in range(n, len(df) - n):
        high = float(df.iloc[i]["high"])
        low = float(df.iloc[i]["low"])

        left_high = float(df.iloc[i - n:i]["high"].max())
        right_high = float(df.iloc[i + 1:i + n + 1]["high"].max())

        left_low = float(df.iloc[i - n:i]["low"].min())
        right_low = float(df.iloc[i + 1:i + n + 1]["low"].min())

        if high > left_high and high >= right_high:
            highs.append({"index": i, "price": high})

        if low < left_low and low <= right_low:
            lows.append({"index": i, "price": low})

    return highs, lows


# ============================================================
# CLUSTER LIQUIDITY
# ============================================================

def cluster(levels):
    zones = []

    for item in levels:
        price = float(item["price"]) if isinstance(item, dict) else float(item)
        found = False

        for i, zone in enumerate(zones):
            if abs(price - zone) / zone <= ZONE_TOLERANCE:
                zones[i] = (zone + price) / 2
                found = True
                break

        if not found:
            zones.append(price)

    return zones


# ============================================================
# TRENDLINE
# ============================================================

def trendline_from_swings(swings, sweep_index, direction):
    """
    Find the best recent trendline before the sweep.

    BEARISH:
        descending resistance through swing highs.

    BULLISH:
        ascending support through swing lows.

    Returns:
        {
            x1, y1, x2, y2,
            touches,
            value_at_sweep,
            slope
        }
        or None
    """

    usable = [
        p for p in swings
        if p["index"] < sweep_index
        and sweep_index - p["index"] <= MAX_TRENDLINE_SWING_GAP
    ]

    if len(usable) < 2:
        return None

    best = None

    # Test pairs, preferring recent points and more touches.
    for a in range(len(usable) - 1):
        for b in range(a + 1, len(usable)):
            p1 = usable[a]
            p2 = usable[b]

            x1, y1 = p1["index"], p1["price"]
            x2, y2 = p2["index"], p2["price"]

            if x2 <= x1:
                continue

            slope = (y2 - y1) / (x2 - x1)

            if direction == "BEARISH" and slope >= 0:
                continue

            if direction == "BULLISH" and slope <= 0:
                continue

            # Reject very old / excessively wide lines.
            if x2 - x1 > MAX_TRENDLINE_SWING_GAP:
                continue

            # Count swing points close to the projected line.
            touches = 0
            touch_indices = []

            for point in usable:
                x = point["index"]
                expected = y1 + slope * (x - x1)
                tolerance = expected * TRENDLINE_TOLERANCE

                if abs(point["price"] - expected) <= tolerance:
                    touches += 1
                    touch_indices.append(x)

            if touches < MIN_TRENDLINE_TOUCHES:
                continue

            value_at_sweep = y1 + slope * (sweep_index - x1)

            candidate = {
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "slope": slope,
                "touches": touches,
                "touch_indices": touch_indices,
                "value_at_sweep": value_at_sweep
            }

            if best is None:
                best = candidate
            else:
                # Prefer more touches, then the more recent second point.
                if (
                    candidate["touches"] > best["touches"]
                    or (
                        candidate["touches"] == best["touches"]
                        and candidate["x2"] > best["x2"]
                    )
                ):
                    best = candidate

    return best


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

    if direction == "BEARISH":
        for level in high_zones:
            distance = (level - price) / price

            if distance < 0 or distance > MAX_LEVEL_DISTANCE:
                continue

            swept = float(candle["high"]) > level * (1 + SWEEP_PCT)
            reclaimed = price < level

            if swept and reclaimed:
                return float(level)

    if direction == "BULLISH":
        for level in low_zones:
            distance = (price - level) / price

            if distance < 0 or distance > MAX_LEVEL_DISTANCE:
                continue

            swept = float(candle["low"]) < level * (1 - SWEEP_PCT)
            reclaimed = price > level

            if swept and reclaimed:
                return float(level)

    return None


# ============================================================
# CONFIRMATION
# ============================================================

def check_confirmation(df, sweep_index, level, direction):
    end = sweep_index + CONFIRM_CANDLES

    if end >= len(df):
        return False

    confirmation = df.iloc[sweep_index + 1:end + 1]

    if direction == "BEARISH":
        return all(float(c["close"]) < level for _, c in confirmation.iterrows())

    return all(float(c["close"]) > level for _, c in confirmation.iterrows())


# ============================================================
# VOLUME
# ============================================================

def volume_ratio(df, index):
    start = max(0, index - 20)
    previous = df.iloc[start:index]["volume"]

    if len(previous) == 0:
        return 0

    average = previous.mean()

    if average <= 0:
        return 0

    return float(df.iloc[index]["volume"]) / float(average)


# ============================================================
# STRUCTURE
# ============================================================

def structure_direction(df, index, direction):
    history = df.iloc[:index]
    highs, lows = find_swings(history)

    if direction == "BEARISH":
        if len(highs) < 2 or len(lows) < 2:
            return False

        return (
            highs[-1]["price"] < highs[-2]["price"]
            and lows[-1]["price"] < lows[-2]["price"]
        )

    if len(highs) < 2 or len(lows) < 2:
        return False

    return (
        highs[-1]["price"] > highs[-2]["price"]
        and lows[-1]["price"] > lows[-2]["price"]
    )


# ============================================================
# ANALYZE
# ============================================================

def analyze(df):
    # Ignore the currently forming candle.
    df = df.iloc[:-1].copy()

    minimum = 30 + CONFIRM_CANDLES

    if len(df) < minimum:
        return None

    confirmation_end = len(df) - 1
    sweep_index = confirmation_end - CONFIRM_CANDLES

    if sweep_index < 30:
        return None

    candidates = []

    for direction in ["BEARISH", "BULLISH"]:
        level = find_sweep(df, sweep_index, direction)

        if level is None:
            continue

        ratio = volume_ratio(df, sweep_index)

        confirmed = check_confirmation(
            df,
            sweep_index,
            level,
            direction
        )

        structure_ok = structure_direction(
            df,
            sweep_index,
            direction
        )

        history = df.iloc[:sweep_index]
        highs, lows = find_swings(history)

        points = highs if direction == "BEARISH" else lows

        trendline = trendline_from_swings(
            points,
            sweep_index,
            direction
        )

        trendline_ok = False

        if trendline:
            line_value = trendline["value_at_sweep"]
            sweep_high = float(df.iloc[sweep_index]["high"])
            sweep_low = float(df.iloc[sweep_index]["low"])

            if direction == "BEARISH":
                trendline_ok = (
                    abs(sweep_high - line_value) / line_value
                    <= 0.003
                    or sweep_high >= line_value
                )
            else:
                trendline_ok = (
                    abs(sweep_low - line_value) / line_value
                    <= 0.003
                    or sweep_low <= line_value
                )

        # ====================================================
        # SETUP SCORE
        # ====================================================
        score = 0

        if structure_ok:
            score += 1

        if trendline_ok:
            score += 2

        if trendline and trendline["touches"] >= 3:
            score += 1

        # Sweep + reclaim is worth 2.
        score += 2

        if ratio >= VOLUME_MULTIPLIER:
            score += 1

        if confirmed:
            score += 1

        # A trendline is now a required confluence filter.
        if (
            not trendline_ok
            or not confirmed
            or ratio < VOLUME_MULTIPLIER
            or score < MIN_SETUP_SCORE
        ):
            continue

        candidates.append({
            "direction": direction,
            "level": level,
            "price": float(df.iloc[confirmation_end]["close"]),
            "volume_ratio": ratio,
            "time": df.iloc[sweep_index]["time"],
            "confirmation_time": df.iloc[confirmation_end]["time"],
            "sweep_index": sweep_index,
            "score": score,
            "structure_ok": structure_ok,
            "trendline_ok": trendline_ok,
            "trendline_touches": trendline["touches"],
            "trendline": trendline
        })

    if not candidates:
        return None

    # Normally only one direction will qualify.
    return max(
        candidates,
        key=lambda x: (
            x["score"],
            x["confirmation_time"]
        )
    )


# ============================================================
# STATE
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
        "setup_score": 0,
        "trendline_touches": 0,
        "tp1_hit": False,
        "tp2_hit": False,
        "sl_hit": False
    }


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)

        if not isinstance(state, dict):
            return default_state()

        base = default_state()
        base.update(state)
        return base

    except (FileNotFoundError, json.JSONDecodeError):
        return default_state()


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ============================================================
# HISTORY
# ============================================================

def load_history():
    try:
        with open(HISTORY_FILE, "r") as f:
            data = json.load(f)

        return data if isinstance(data, list) else []

    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def record_trade_open(state):
    history = load_history()

    history.append({
        "event_id": state["event_id"],
        "opened_at": state["confirmation_time"],
        "direction": state["direction"],
        "entry": state["entry"],
        "stop_loss": state["stop_loss"],
        "tp1": state["tp1"],
        "tp2": state["tp2"],
        "setup_score": state["setup_score"],
        "trendline_touches": state["trendline_touches"],
        "volume_ratio": state["volume_ratio"],
        "result": "OPEN",
        "closed_at": "",
        "r_result": 0
    })

    save_history(history)


def update_trade_result(event_id, result, closed_at, r_result):
    history = load_history()

    for trade in reversed(history):
        if trade.get("event_id") == event_id:
            trade["result"] = result
            trade["closed_at"] = closed_at
            trade["r_result"] = round(float(r_result), 2)
            break

    save_history(history)


# ============================================================
# EOD REPORT
# ============================================================

def send_eod_report():
    history = load_history()

    # EOD is based on India Standard Time (UTC+5:30).
    ist = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(ist).date()

    todays = []

    for trade in history:
        opened = trade.get("opened_at", "")

        if not opened:
            continue

        try:
            opened_date = pd.to_datetime(opened, utc=True).tz_convert(ist).date()
        except Exception:
            continue

        if opened_date == today:
            todays.append(trade)

    total = len(todays)
    closed = [t for t in todays if t.get("result") != "OPEN"]

    wins = [
        t for t in closed
        if t.get("result") in ("TP1", "TP2")
    ]

    losses = [
        t for t in closed
        if t.get("result") == "SL"
    ]

    total_r = sum(float(t.get("r_result", 0)) for t in closed)

    win_rate = (
        len(wins) / len(closed) * 100
        if closed
        else 0
    )

    if total == 0:
        message = (
            "📊 BTC EOD PAPER REPORT\n\n"
            "No qualified setups detected today.\n\n"
            "Strategy: Liquidity + Trendline Confluence"
        )
    else:
        message = (
            "📊 BTC EOD PAPER REPORT\n\n"
            f"Qualified setups: {total}\n"
            f"Closed trades: {len(closed)}\n"
            f"Wins: {len(wins)}\n"
            f"Losses: {len(losses)}\n"
            f"Win rate: {win_rate:.1f}%\n"
            f"Net result: {total_r:+.2f}R\n\n"
            "🧪 PAPER TRADING ONLY"
        )

    telegram(message)


# ============================================================
# CREATE CHART
# ============================================================

def create_chart(df, signal, entry, stop_loss, tp1, tp2):
    chart_df = (
        df.iloc[:-1]
        .tail(80)
        .copy()
        .reset_index(drop=True)
    )

    direction = signal["direction"]
    liquidity_level = float(signal["level"])

    fig, ax = plt.subplots(figsize=(14, 8))

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

        body_low = min(open_price, close_price)
        body_height = abs(close_price - open_price)

        if body_height == 0:
            body_height = high_price * 0.00001

        ax.bar(
            i,
            body_height,
            bottom=body_low,
            width=0.65,
            color=candle_color
        )

    # Liquidity zone/level.
    zone_width = liquidity_level * ZONE_TOLERANCE

    ax.axhspan(
        liquidity_level - zone_width,
        liquidity_level + zone_width,
        alpha=0.12,
        label="Liquidity zone"
    )

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

    # Trendline from signal data.
    trendline = signal.get("trendline")

    if trendline:
        x1 = trendline["x1"]
        y1 = trendline["y1"]
        x2 = trendline["x2"]
        y2 = trendline["y2"]

        # Convert global df indices to chart-local indices.
        chart_start = len(df.iloc[:-1]) - len(chart_df)

        lx1 = x1 - chart_start
        lx2 = x2 - chart_start
        lx3 = len(chart_df) - 1

        if lx2 >= 0:
            slope = trendline["slope"]
            y3 = y1 + slope * ((chart_start + lx3) - x1)

            ax.plot(
                [lx1, lx2, lx3],
                [y1, y2, y3],
                linewidth=3,
                label=(
                    f"{direction.title()} trendline "
                    f"({trendline['touches']} touches)"
                )
            )

    # Sweep marker.
    sweep_time = signal["time"]
    sweep_rows = chart_df[
        chart_df["time"] == sweep_time
    ]

    if not sweep_rows.empty:
        sweep_index = int(sweep_rows.index[0])
        sweep_price = float(chart_df.iloc[sweep_index]["close"])

        ax.scatter(
            sweep_index,
            sweep_price,
            s=180,
            marker="*",
            zorder=5,
            label="Liquidity sweep"
        )

    ax.set_title(
        (
            f"BTCUSDT {INTERVAL} — "
            f"{direction} Liquidity + Trendline Setup "
            f"({signal['score']}/8)"
        ),
        fontsize=16,
        fontweight="bold"
    )

    ax.set_xlabel("Candles")
    ax.set_ylabel("Price (USDT)")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="best", fontsize=9)

    plt.tight_layout()
    plt.savefig(
        CHART_FILE,
        dpi=150,
        bbox_inches="tight"
    )
    plt.close()

    return CHART_FILE


# ============================================================
# MONITOR ACTIVE TRADE
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
    now = str(candle["time"])

    # IMPORTANT:
    # If one candle touches both SL and TP, we conservatively
    # count the stop first because OHLC data cannot tell us
    # which level was hit first inside the candle.

    if direction == "BULLISH":
        if not state["sl_hit"] and low <= stop_loss:
            telegram(
                f"🔴 BTC PAPER TRADE\n\n"
                f"BULLISH\n\n"
                f"🛑 STOP LOSS HIT\n\n"
                f"Entry: ${entry:,.2f}\n"
                f"Stop: ${stop_loss:,.2f}\n"
                f"TP1: ${tp1:,.2f}\n"
                f"TP2: ${tp2:,.2f}"
            )

            state["sl_hit"] = True
            state["active"] = False
            update_trade_result(
                state["event_id"],
                "SL",
                now,
                -1
            )
            return True

        if not state["tp1_hit"] and high >= tp1:
            telegram(
                f"🟢 BTC PAPER TRADE\n\n"
                f"BULLISH\n\n"
                f"🎯 TP1 HIT\n\n"
                f"Entry: ${entry:,.2f}\n"
                f"TP1: ${tp1:,.2f}\n"
                f"TP2: ${tp2:,.2f}"
            )

            state["tp1_hit"] = True
            changed = True


        if not state["tp2_hit"] and high >= tp2:
            telegram(
                f"🟢 BTC PAPER TRADE\n\n"
                f"BULLISH\n\n"
                f"🎯🎯 TP2 HIT\n\n"
                f"Entry: ${entry:,.2f}\n"
                f"TP1: ${tp1:,.2f}\n"
                f"TP2: ${tp2:,.2f}"
            )

            state["tp2_hit"] = True
            state["active"] = False
            changed = True

            update_trade_result(
                state["event_id"],
                "TP2",
                now,
                2
            )

    else:
        if not state["sl_hit"] and high >= stop_loss:
            telegram(
                f"🔴 BTC PAPER TRADE\n\n"
                f"BEARISH\n\n"
                f"🛑 STOP LOSS HIT\n\n"
                f"Entry: ${entry:,.2f}\n"
                f"Stop: ${stop_loss:,.2f}\n"
                f"TP1: ${tp1:,.2f}\n"
                f"TP2: ${tp2:,.2f}"
            )

            state["sl_hit"] = True
            state["active"] = False
            update_trade_result(
                state["event_id"],
                "SL",
                now,
                -1
            )
            return True

        if not state["tp1_hit"] and low <= tp1:
            telegram(
                f"🔴 BTC PAPER TRADE\n\n"
                f"BEARISH\n\n"
                f"🎯 TP1 HIT\n\n"
                f"Entry: ${entry:,.2f}\n"
                f"TP1: ${tp1:,.2f}\n"
                f"TP2: ${tp2:,.2f}"
            )

            state["tp1_hit"] = True
            changed = True


        if not state["tp2_hit"] and low <= tp2:
            telegram(
                f"🔴 BTC PAPER TRADE\n\n"
                f"BEARISH\n\n"
                f"🎯🎯 TP2 HIT\n\n"
                f"Entry: ${entry:,.2f}\n"
                f"TP1: ${tp1:,.2f}\n"
                f"TP2: ${tp2:,.2f}"
            )

            state["tp2_hit"] = True
            state["active"] = False
            changed = True

            update_trade_result(
                state["event_id"],
                "TP2",
                now,
                2
            )

    return changed


# ============================================================
# CREATE NEW TRADE
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
        risk = abs(entry - stop_loss)
        tp1 = entry - risk
        tp2 = entry - (risk * 2)
    else:
        emoji = "🟢"
        entry = price
        stop_loss = level
        risk = abs(entry - stop_loss)
        tp1 = entry + risk
        tp2 = entry + (risk * 2)

    if risk <= 0:
        raise RuntimeError("Invalid risk: entry and stop are identical.")

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
        "sweep_time": str(signal["time"]),
        "confirmation_time": str(signal["confirmation_time"]),
        "volume_ratio": volume,
        "setup_score": signal["score"],
        "trendline_touches": signal["trendline_touches"],
        "tp1_hit": False,
        "tp2_hit": False,
        "sl_hit": False
    }

    message = (
        f"{emoji} BTC A+ PAPER SETUP\n\n"
        f"Direction: {direction}\n\n"
        f"⭐ Setup Score: {signal['score']}/8\n"
        f"📈 Trendline touches: {signal['trendline_touches']}\n"
        f"📊 Volume: {volume:.2f}x average\n\n"
        f"💧 Liquidity: ${level:,.2f}\n"
        f"📍 Entry: ${entry:,.2f}\n"
        f"🛑 Stop Loss: ${stop_loss:,.2f}\n"
        f"🎯 TP1: ${tp1:,.2f}\n"
        f"🎯 TP2: ${tp2:,.2f}\n\n"
        f"📊 Risk/Reward: 1:1 / 1:2\n\n"
        f"🧪 PAPER TRADE ONLY\n"
        f"Liquidity sweep + trendline confluence confirmed."
    )

    chart_file = create_chart(
        df,
        signal,
        entry,
        stop_loss,
        tp1,
        tp2
    )

    telegram(message)
    send_chart(
        chart_file,
        (
            f"{emoji} BTC {direction} PAPER SETUP\n"
            f"Score: {signal['score']}/8\n"
            f"Entry: ${entry:,.2f}\n"
            f"SL: ${stop_loss:,.2f}\n"
            f"TP1: ${tp1:,.2f}\n"
            f"TP2: ${tp2:,.2f}"
        )
    )

    record_trade_open(state)

    return state


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--eod", action="store_true")
    args = parser.parse_args()

    if args.eod:
        print("Generating daily EOD paper-trading report...")
        send_eod_report()
        print("EOD report sent.")
        return

    print("BTC Liquidity + Trendline Bot started")

    df = get_data()
    print(f"Loaded {len(df)} candles")

    state = load_state()

    # --------------------------------------------------------
    # Existing paper trade
    # --------------------------------------------------------
    if state.get("active"):
        print("Active paper trade found.")

        changed = monitor_trade(df, state)

        if changed:
            save_state(state)
            print("Trade state updated.")
        else:
            print("Active paper trade still running.")

        return

    # --------------------------------------------------------
    # New signal
    # --------------------------------------------------------
    signal = analyze(df)

    if signal is None:
        print("No new qualified liquidity + trendline setup.")
        return

    # Cooldown against the last completed trade.
    history = load_history()

    completed = [
        t for t in history
        if t.get("closed_at")
    ]

    if completed:
        last = completed[-1]

        try:
            previous_time = pd.to_datetime(
                last["opened_at"],
                utc=True
            )
            current_time = pd.to_datetime(
                signal["confirmation_time"],
                utc=True
            )

            minutes = (
                current_time - previous_time
            ).total_seconds() / 60

            if 0 <= minutes < COOLDOWN_MINUTES:
                print(
                    f"Cooldown active: {minutes:.1f} minutes"
                )
                return

        except Exception:
            pass

    new_state = create_trade(df, signal)
    save_state(new_state)

    print(
        "New paper trade created, "
        "chart sent and state saved."
    )


if __name__ == "__main__":
    main()
