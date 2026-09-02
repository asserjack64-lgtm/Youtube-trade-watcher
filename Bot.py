import os
import json
import requests
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# V4 PAPER-TRADE BOT
# ============================================================
# V4 is the exact bearish-only range-liquidity model that was
# validated out-of-sample in backtest_v4.
#
# IMPORTANT:
# - PAPER TRADING ONLY
# - No exchange orders are placed.
# - State is persisted so GitHub Actions runs can be stateless.
# - processed_event_ids prevents the same closed signal from
#   being alerted again every 5 minutes.
# ============================================================


SYMBOL = "XAUUSD"
# Spot XAUUSD has no centralized exchange volume. The default feed below
# uses COMEX gold futures as a liquid, no-key proxy for Gold with real volume.
DATA_SYMBOL = "GC=F"
DATA_PROVIDER = "YAHOO_GOLD_FUTURES"
INTERVAL = "5m"
CANDLES = 200

# ---------------- V4 STRATEGY ----------------

RANGE_CANDLES = 24              # previous 2 hours
MAX_RANGE_WIDTH_PCT = 0.015     # <= 1.5%
SWEEP_PCT = 0.0003              # 0.03% above range high
SL_BUFFER_PCT = 0.0005          # 0.05% above sweep high

VOLUME_LOOKBACK = 20
MIN_VOLUME_RATIO = 1.50         # V4
CONFIRM_BODY_MIN_PCT = 0.0008   # V4 = 0.08%

MAX_BARS_IN_TRADE = 72          # 6 hours

TP1_R = 1.0
TP2_R = 2.0
TP1_PARTIAL = 0.50
MOVE_SL_TO_BREAKEVEN = True

MIN_PLANNED_REWARD_R = 1.50


# ---------------- BOT STATE ----------------

STATE_FILE = "xauusd_trade_state.json"
HISTORY_FILE = "xauusd_trade_history.json"
CHART_FILE = "xauusd_v4_liquidity_setup.png"

HEARTBEAT_ENABLED = False
HEARTBEAT_MINUTES = 60
MAX_PROCESSED_EVENTS = 200

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"


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
        data={
            "chat_id": CHAT_ID,
            "text": message,
        },
        timeout=20,
    )

    response.raise_for_status()


def send_chart(path, caption):
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    if not CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is missing")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"

    with open(path, "rb") as photo:
        response = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "caption": caption,
            },
            files={"photo": photo},
            timeout=30,
        )

    response.raise_for_status()


# ============================================================
# STATE
# ============================================================

def default_state():
    return {
        "active": False,

        "event_id": "",
        "last_signal_event_id": "",
        "processed_event_ids": [],

        "last_heartbeat": "",

        "direction": "",

        "entry": 0.0,
        "stop_loss": 0.0,
        "tp1": 0.0,
        "tp2": 0.0,
        "level": 0.0,

        "range_high": 0.0,
        "range_low": 0.0,

        "sweep_time": "",
        "entry_time": "",
        "confirmation_time": "",

        "volume_ratio": 0.0,
        "range_width_pct": 0.0,
        "planned_reward_r": 0.0,

        "tp1_hit": False,
        "tp2_hit": False,
        "sl_hit": False,
        "breakeven": False,

        "partial_realized_r": 0.0,
    }


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        if not isinstance(state, dict):
            return default_state()

    except (FileNotFoundError, json.JSONDecodeError):
        return default_state()

    defaults = default_state()

    for key, value in defaults.items():
        if key not in state:
            state[key] = value

    if not isinstance(state.get("processed_event_ids"), list):
        state["processed_event_ids"] = []

    state["processed_event_ids"] = state["processed_event_ids"][
        -MAX_PROCESSED_EVENTS:
    ]

    return state


def save_state(state):
    tmp = STATE_FILE + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    os.replace(tmp, STATE_FILE)


def mark_event_processed(state, event_id):
    events = state.setdefault("processed_event_ids", [])

    if event_id not in events:
        events.append(event_id)

    state["processed_event_ids"] = events[-MAX_PROCESSED_EVENTS:]


def event_already_processed(state, event_id):
    return event_id in state.get("processed_event_ids", [])


# ============================================================
# HEARTBEAT
# ============================================================

def send_heartbeat(state):
    if not HEARTBEAT_ENABLED:
        return False

    now = pd.Timestamp.now(tz="UTC")

    previous_text = state.get("last_heartbeat", "")

    if previous_text:
        try:
            previous = pd.to_datetime(previous_text, utc=True)

            minutes = (
                now - previous
            ).total_seconds() / 60

            if minutes < HEARTBEAT_MINUTES:
                return False

        except Exception:
            pass

    telegram(
        f"""🟢 GOLD / XAUUSD V4 LIQUIDITY MONITOR

Bot status: ACTIVE

Market: {SYMBOL} (data: {DATA_SYMBOL})
Timeframe: {INTERVAL}

Last check:
{now.strftime("%Y-%m-%d %H:%M UTC")}

Strategy:
V4 Bearish Range Liquidity Sweep

Monitoring:
💧 Range liquidity sweeps
📊 Volume >= {MIN_VOLUME_RATIO:.2f}x
📉 Strong bearish confirmation
🎯 TP1 / TP2
🛑 Stop Loss
🔒 Duplicate-signal protection

⚠️ PAPER TRADING ONLY
"""
    )

    state["last_heartbeat"] = now.isoformat()

    return True


# ============================================================
# GOLD DATA
# ============================================================

def get_data():
    """
    Download completed 5-minute Gold futures candles from Yahoo Finance.

    GC=F is COMEX Gold futures. It is used as the no-key market-data
    proxy because spot XAUUSD has no centralized exchange volume. The V4
    strategy needs a meaningful volume series for its liquidity-sweep filter.
    No exchange orders are placed.
    """
    params = {
        "interval": "5m",
        "range": "5d",
        "includePrePost": "true",
        "events": "div,splits",
    }

    response = requests.get(
        YAHOO_CHART_URL,
        params=params,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    payload = response.json()

    result = payload.get("chart", {}).get("result")
    if not result:
        error = payload.get("chart", {}).get("error")
        raise RuntimeError(f"Yahoo Gold data error: {error}")

    result = result[0]
    timestamps = result.get("timestamp", [])
    quote = result.get("indicators", {}).get("quote", [{}])[0]

    if not timestamps:
        raise RuntimeError("Yahoo returned no Gold candles.")

    df = pd.DataFrame({
        "time": pd.to_datetime(timestamps, unit="s", utc=True),
        "open": quote.get("open", []),
        "high": quote.get("high", []),
        "low": quote.get("low", []),
        "close": quote.get("close", []),
        "volume": quote.get("volume", []),
    })

    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(subset=["open", "high", "low", "close"]).copy()
    df["volume"] = df["volume"].fillna(0.0)

    # Yahoo's 5m feed can contain duplicate timestamps.
    df = (
        df.drop_duplicates("time")
        .sort_values("time")
        .reset_index(drop=True)
    )

    # Treat only fully completed 5-minute candles as signal data.
    now = pd.Timestamp.now(tz="UTC")
    df["close_time"] = df["time"] + pd.Timedelta(minutes=5)
    df = df[df["close_time"] <= now].reset_index(drop=True)

    if len(df) < RANGE_CANDLES + VOLUME_LOOKBACK + 2:
        raise RuntimeError(
            f"Not enough completed Gold candles: {len(df)}"
        )

    if float(df["volume"].max()) <= 0:
        raise RuntimeError(
            "Gold feed returned no usable volume. V4 volume filter cannot be validated."
        )

    return df


# ============================================================
# V4 SIGNAL
# ============================================================

def volume_ratio(df, index):
    start = max(0, index - VOLUME_LOOKBACK)

    previous = df.iloc[start:index]["volume"]

    if len(previous) < 5:
        return 0.0

    average = previous.mean()

    if average <= 0:
        return 0.0

    return float(
        df.iloc[index]["volume"] / average
    )


def find_v4_signal(df):
    """
    V4 exact model:

    1. Previous 24 completed candles define the range.
    2. Range width <= 1.5%.
    3. Sweep candle trades above range high by >= 0.03%.
    4. Sweep closes back below range high.
    5. Sweep volume >= 1.50x prior 20-candle average.
    6. Immediately following candle confirms:
       - closes below range high
       - bearish candle
       - body >= 0.08%
       - closes below midpoint of sweep high/sweep close
    7. Entry = confirmation close.
    8. SL = sweep high + 0.05%.
    9. TP1 = 1R.
    10. Opposite range low must provide >= 1.5R.
    11. TP2 = the closer of theoretical 2R and opposite range
        target, while still >= 1.5R.
    """

    # Need enough candles:
    # range + volume lookback + sweep + confirmation.
    if len(df) < RANGE_CANDLES + VOLUME_LOOKBACK + 2:
        return None

    # Last completed candle = confirmation.
    confirmation_i = len(df) - 1
    sweep_i = confirmation_i - 1

    if sweep_i < RANGE_CANDLES + VOLUME_LOOKBACK:
        return None

    range_df = df.iloc[
        sweep_i - RANGE_CANDLES:sweep_i
    ]

    range_high = float(range_df["high"].max())
    range_low = float(range_df["low"].min())

    midpoint = (
        range_high + range_low
    ) / 2

    if midpoint <= 0:
        return None

    range_width_pct = (
        range_high - range_low
    ) / midpoint

    if range_width_pct > MAX_RANGE_WIDTH_PCT:
        return None

    sweep = df.iloc[sweep_i]
    confirm = df.iloc[confirmation_i]

    sweep_high = float(sweep["high"])
    sweep_close = float(sweep["close"])

    # V4 bearish sweep.
    bearish_sweep = (
        sweep_high
        > range_high * (1 + SWEEP_PCT)
        and sweep_close < range_high
    )

    if not bearish_sweep:
        return None

    vr = volume_ratio(df, sweep_i)

    if vr < MIN_VOLUME_RATIO:
        return None

    confirm_open = float(confirm["open"])
    confirm_close = float(confirm["close"])

    body_pct = (
        confirm_open - confirm_close
    ) / confirm_open

    bearish_confirmation = (
        confirm_close < range_high
        and confirm_close < confirm_open
        and body_pct >= CONFIRM_BODY_MIN_PCT
        and confirm_close
        < (sweep_high + sweep_close) / 2
    )

    if not bearish_confirmation:
        return None

    entry = confirm_close

    stop = sweep_high * (
        1 + SL_BUFFER_PCT
    )

    risk = stop - entry

    if risk <= 0:
        return None

    tp1 = entry - (
        risk * TP1_R
    )

    theoretical_tp2 = entry - (
        risk * TP2_R
    )

    opposite_range = range_low

    range_reward_r = (
        entry - opposite_range
    ) / risk

    if range_reward_r < MIN_PLANNED_REWARD_R:
        return None

    # Exact V4 logic.
    tp2 = max(
        theoretical_tp2,
        opposite_range,
    )

    planned_reward_r = (
        entry - tp2
    ) / risk

    if planned_reward_r < MIN_PLANNED_REWARD_R:
        return None

    event_id = (
        f"{confirm['time'].isoformat()}|"
        f"BEARISH|"
        f"{range_high:.2f}|"
        f"{sweep_high:.2f}"
    )

    return {
        "event_id": event_id,
        "direction": "BEARISH",

        "signal_time": confirm["time"],
        "sweep_time": sweep["time"],

        "range_high": range_high,
        "range_low": range_low,

        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,

        "volume_ratio": vr,
        "range_width_pct": range_width_pct,
        "planned_reward_r": planned_reward_r,
    }


# ============================================================
# HISTORY
# ============================================================

def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)

        return history if isinstance(history, list) else []

    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(history):
    tmp = HISTORY_FILE + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    os.replace(tmp, HISTORY_FILE)


def record_completed_trade(state, outcome, result_r):
    history = load_history()

    event_id = state.get("event_id", "")

    for existing in history:
        if existing.get("event_id") == event_id:
            return

    trade = {
        "event_id": event_id,
        "symbol": SYMBOL,
        "direction": state.get("direction", ""),

        "entry": float(state.get("entry", 0)),
        "stop_loss": float(state.get("stop_loss", 0)),
        "tp1": float(state.get("tp1", 0)),
        "tp2": float(state.get("tp2", 0)),

        "range_high": float(state.get("range_high", 0)),
        "range_low": float(state.get("range_low", 0)),

        "sweep_time": state.get("sweep_time", ""),
        "entry_time": state.get("entry_time", ""),
        "confirmation_time": state.get(
            "confirmation_time", ""
        ),

        "volume_ratio": float(
            state.get("volume_ratio", 0)
        ),

        "range_width_pct": float(
            state.get("range_width_pct", 0)
        ),

        "planned_reward_r": float(
            state.get("planned_reward_r", 0)
        ),

        "tp1_hit": bool(
            state.get("tp1_hit", False)
        ),

        "tp2_hit": bool(
            state.get("tp2_hit", False)
        ),

        "sl_hit": bool(
            state.get("sl_hit", False)
        ),

        "breakeven": bool(
            state.get("breakeven", False)
        ),

        "partial_realized_r": float(
            state.get("partial_realized_r", 0)
        ),

        "outcome": outcome,
        "result_r": float(result_r),

        "closed_at": pd.Timestamp.now(
            tz="UTC"
        ).isoformat(),
    }

    history.append(trade)
    save_history(history)


# ============================================================
# CHART
# ============================================================

def create_chart(
    df,
    signal,
):
    chart_df = (
        df.tail(60)
        .copy()
        .reset_index(drop=True)
    )

    fig, ax = plt.subplots(
        figsize=(14, 8)
    )

    for i, candle in chart_df.iterrows():
        o = float(candle["open"])
        h = float(candle["high"])
        l = float(candle["low"])
        c = float(candle["close"])

        candle_color = (
            "green" if c >= o else "red"
        )

        ax.plot(
            [i, i],
            [l, h],
            color=candle_color,
            linewidth=1,
        )

        body_low = min(o, c)
        body_height = abs(c - o)

        if body_height == 0:
            body_height = h * 0.00001

        ax.bar(
            i,
            body_height,
            bottom=body_low,
            width=0.65,
            color=candle_color,
        )

    ax.axhline(
        signal["range_high"],
        linestyle="--",
        linewidth=2,
        label=f"Liquidity ${signal['range_high']:,.2f}",
    )

    ax.axhline(
        signal["entry"],
        linestyle="-",
        linewidth=2,
        label=f"Entry ${signal['entry']:,.2f}",
    )

    ax.axhline(
        signal["stop"],
        linestyle="--",
        linewidth=2,
        label=f"SL ${signal['stop']:,.2f}",
    )

    ax.axhline(
        signal["tp1"],
        linestyle="--",
        linewidth=2,
        label=f"TP1 ${signal['tp1']:,.2f}",
    )

    ax.axhline(
        signal["tp2"],
        linestyle="--",
        linewidth=2,
        label=f"TP2 ${signal['tp2']:,.2f}",
    )

    ax.set_title(
        "XAUUSD / Gold 5m V4 Bearish Range Liquidity Sweep",
        fontsize=16,
        fontweight="bold",
    )

    ax.set_xlabel("Candles")
    ax.set_ylabel("Gold Price (USD)")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="best", fontsize=9)

    plt.tight_layout()
    plt.savefig(
        CHART_FILE,
        dpi=150,
        bbox_inches="tight",
    )
    plt.close()

    return CHART_FILE


# ============================================================
# CREATE PAPER TRADE
# ============================================================

def create_trade(df, signal):
    state = default_state()

    state.update({
        "active": True,

        "event_id": signal["event_id"],
        "last_signal_event_id": signal["event_id"],

        "direction": "BEARISH",

        "entry": float(signal["entry"]),
        "stop_loss": float(signal["stop"]),
        "tp1": float(signal["tp1"]),
        "tp2": float(signal["tp2"]),

        "range_high": float(signal["range_high"]),
        "range_low": float(signal["range_low"]),

        "level": float(signal["range_high"]),

        "sweep_time": str(signal["sweep_time"]),
        "entry_time": str(signal["signal_time"]),
        "confirmation_time": str(signal["signal_time"]),

        "volume_ratio": float(signal["volume_ratio"]),
        "range_width_pct": float(
            signal["range_width_pct"]
        ),
        "planned_reward_r": float(
            signal["planned_reward_r"]
        ),
    })

    mark_event_processed(
        state,
        signal["event_id"],
    )

    entry = state["entry"]
    stop = state["stop_loss"]
    tp1 = state["tp1"]
    tp2 = state["tp2"]

    risk = stop - entry

    message = f"""🔴 Gold XAUUSD V4 PAPER TRADE

BEARISH RANGE LIQUIDITY SWEEP

Liquidity:
${signal['range_high']:,.2f}

Sweep:
{signal['sweep_time']}

Confirmation:
{signal['signal_time']}

Sweep volume:
{signal['volume_ratio']:.2f}x average

Range width:
{signal['range_width_pct'] * 100:.3f}%

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
{signal['planned_reward_r']:.2f}R

━━━━━━━━━━━━━━━━━━

V4 MANAGEMENT

TP1:
50% position at +1R

After TP1:
Stop moves to breakeven

TP2:
Remaining 50% at target

⚠️ PAPER TRADING ONLY
"""

    telegram(message)

    chart = create_chart(
        df,
        signal,
    )

    send_chart(
        chart,
        (
            f"🔴 Gold XAUUSD V4 BEARISH PAPER SETUP\n\n"
            f"Entry: ${entry:,.2f}\n"
            f"SL: ${stop:,.2f}\n"
            f"TP1: ${tp1:,.2f}\n"
            f"TP2: ${tp2:,.2f}\n\n"
            f"Volume: {signal['volume_ratio']:.2f}x\n"
            f"Planned: {signal['planned_reward_r']:.2f}R\n"
            f"⚠️ Paper trading only"
        ),
    )

    return state


# ============================================================
# MONITOR V4 PAPER TRADE
# ============================================================

def monitor_trade(df, state):
    if not state.get("active"):
        return False

    if len(df) < 2:
        return False

    candle = df.iloc[-1]

    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])

    entry = float(state["entry"])
    original_stop = float(state["stop_loss"])
    tp1 = float(state["tp1"])
    tp2 = float(state["tp2"])

    changed = False

    # --------------------------------------------------------
    # V4 uses bearish trades only.
    #
    # Conservative same-candle ordering:
    # check current stop BEFORE targets.
    # This matches the V4 backtest.
    # --------------------------------------------------------

    current_stop = (
        entry
        if state.get("breakeven", False)
        else original_stop
    )

    if high >= current_stop:
        if state.get("tp1_hit", False):
            result_r = float(
                state.get("partial_realized_r", 0)
            )

            state["active"] = False
            state["breakeven"] = True

            record_completed_trade(
                state,
                "TP1_BE",
                result_r,
            )

            telegram(
                f"""🟡 Gold XAUUSD V4 PAPER TRADE CLOSED

BEARISH setup

🔒 TP1 → BREAKEVEN

TP1 was already secured.

Realized result:
{result_r:+.2f}R

Entry:
${entry:,.2f}

The remaining position returned
to breakeven.

Trade is CLOSED.
No further alerts will be sent
for this signal.
"""
            )

            mark_event_processed(
                state,
                state["event_id"],
            )

            changed = True
            return changed

        state["sl_hit"] = True
        state["active"] = False

        record_completed_trade(
            state,
            "SL",
            -1.0,
        )

        telegram(
            f"""🔴 Gold XAUUSD V4 PAPER TRADE CLOSED

BEARISH setup

🛑 FULL STOP LOSS

Entry:
${entry:,.2f}

Stop:
${original_stop:,.2f}

Result:
-1.00R

Trade is CLOSED.
No further alerts will be sent
for this signal.
"""
        )

        mark_event_processed(
            state,
            state["event_id"],
        )

        changed = True
        return changed

    # --------------------------------------------------------
    # TP1
    # --------------------------------------------------------

    if (
        not state.get("tp1_hit", False)
        and low <= tp1
    ):
        state["tp1_hit"] = True
        state["partial_realized_r"] = (
            TP1_PARTIAL * TP1_R
        )

        if MOVE_SL_TO_BREAKEVEN:
            state["breakeven"] = True

        telegram(
            f"""🟡 Gold XAUUSD V4 PAPER TRADE UPDATE

BEARISH setup

🎯 TP1 HIT

Entry:
${entry:,.2f}

TP1:
${tp1:,.2f}

50% position:
+1R

Realized:
+{state['partial_realized_r']:.2f}R

Remaining 50%:
Still active

🔒 Stop moved to breakeven.

TP2:
${tp2:,.2f}
"""
        )

        changed = True

    # --------------------------------------------------------
    # TP2
    # --------------------------------------------------------

    if (
        not state.get("tp2_hit", False)
        and low <= tp2
    ):
        state["tp2_hit"] = True
        state["active"] = False

        remaining_fraction = (
            1.0 - TP1_PARTIAL
        )

        remaining_r = (
            remaining_fraction * TP2_R
        )

        result_r = (
            float(
                state.get(
                    "partial_realized_r",
                    0,
                )
            )
            + remaining_r
        )

        record_completed_trade(
            state,
            "TP2",
            result_r,
        )

        telegram(
            f"""🟢 Gold XAUUSD V4 PAPER TRADE CLOSED

BEARISH setup

🎯🎯 TP2 HIT

Entry:
${entry:,.2f}

TP1:
${tp1:,.2f}

TP2:
${tp2:,.2f}

Result:
+{result_r:.2f}R

Trade is CLOSED.
No further alerts will be sent
for this signal.
"""
        )

        mark_event_processed(
            state,
            state["event_id"],
        )

        changed = True
        return changed

    # --------------------------------------------------------
    # TIME EXIT
    # --------------------------------------------------------

    try:
        entry_time = pd.to_datetime(
            state["entry_time"],
            utc=True,
        )

        current_time = candle["time"]

        bars_held = int(
            (
                current_time - entry_time
            ).total_seconds()
            // (5 * 60)
        )

    except Exception:
        bars_held = 0

    if bars_held >= MAX_BARS_IN_TRADE:
        risk = original_stop - entry

        if risk <= 0:
            result_r = 0.0

        elif state.get("tp1_hit", False):
            remaining_fraction = (
                1.0 - TP1_PARTIAL
            )

            remaining_r = (
                remaining_fraction
                * (entry - close)
                / risk
            )

            result_r = (
                float(
                    state.get(
                        "partial_realized_r",
                        0,
                    )
                )
                + remaining_r
            )

        else:
            result_r = (
                entry - close
            ) / risk

        state["active"] = False

        record_completed_trade(
            state,
            "TIME",
            result_r,
        )

        telegram(
            f"""🟡 Gold XAUUSD V4 PAPER TRADE CLOSED

BEARISH setup

⏱ TIME EXIT

Held:
{bars_held} candles

Entry:
${entry:,.2f}

Current close:
${close:,.2f}

Result:
{result_r:+.2f}R

Trade is CLOSED.
No further alerts will be sent
for this signal.
"""
        )

        mark_event_processed(
            state,
            state["event_id"],
        )

        changed = True

    return changed


# ============================================================
# DAILY REPORT
# ============================================================

def send_eod_report():
    history = load_history()

    now = pd.Timestamp.now(tz="UTC")
    today = now.strftime("%Y-%m-%d")

    trades = []

    for trade in history:
        closed_at = trade.get("closed_at", "")

        if not closed_at:
            continue

        try:
            date = pd.to_datetime(
                closed_at,
                utc=True,
            ).strftime("%Y-%m-%d")

        except Exception:
            continue

        if date == today:
            trades.append(trade)

    total = len(trades)

    if total == 0:
        telegram(
            f"""📊 Gold XAUUSD V4 DAILY PAPER REPORT

Date: {today}

No completed V4 paper trades today.

Market:
{SYMBOL}

Timeframe:
{INTERVAL}

Status:
🟢 Monitor active

⚠️ PAPER TRADING ONLY
"""
        )
        return

    total_r = sum(
        float(t.get("result_r", 0))
        for t in trades
    )

    profitable = sum(
        1 for t in trades
        if float(t.get("result_r", 0)) > 0
    )

    losses = sum(
        1 for t in trades
        if float(t.get("result_r", 0)) < 0
    )

    tp2 = sum(
        1 for t in trades
        if t.get("outcome") == "TP2"
    )

    sl = sum(
        1 for t in trades
        if t.get("outcome") == "SL"
    )

    tp1_be = sum(
        1 for t in trades
        if t.get("outcome") == "TP1_BE"
    )

    time_exits = sum(
        1 for t in trades
        if t.get("outcome") == "TIME"
    )

    win_rate = (
        profitable / total * 100
    )

    telegram(
        f"""📊 Gold XAUUSD V4 DAILY PAPER REPORT

Date: {today}

━━━━━━━━━━━━━━━━━━

Completed trades:
{total}

Profitable:
{profitable}

Losing:
{losses}

Win rate:
{win_rate:.1f}%

━━━━━━━━━━━━━━━━━━

🎯 TP2:
{tp2}

🟡 TP1 → BE:
{tp1_be}

🛑 Full SL:
{sl}

⏱ Time exits:
{time_exits}

━━━━━━━━━━━━━━━━━━

Total result:
{total_r:+.2f}R

Market:
{SYMBOL}

Timeframe:
{INTERVAL}

Strategy:
V4 Bearish Range Liquidity Sweep

⚠️ PAPER TRADING ONLY
"""
    )


# ============================================================
# MAIN
# ============================================================

def main():
    print("Gold XAUUSD V4 Liquidity Bot started")

    state = load_state()

    # --------------------------------------------------------
    # EOD MODE
    # --------------------------------------------------------

    import sys

    if (
        len(sys.argv) > 1
        and sys.argv[1].lower() == "--eod"
    ):
        send_eod_report()
        return

    # --------------------------------------------------------
    # DATA
    # --------------------------------------------------------

    df = get_data()

    print(
        f"Loaded {len(df)} completed candles"
    )

    if len(df) < RANGE_CANDLES + VOLUME_LOOKBACK + 2:
        print("Not enough data.")
        save_state(state)
        return

    # --------------------------------------------------------
    # HEARTBEAT
    # --------------------------------------------------------
    # Disabled by default for GitHub Actions because each run uses
    # a fresh runner. Trade alerts remain enabled.
    if HEARTBEAT_ENABLED:
        try:
            if send_heartbeat(state):
                save_state(state)
                print("Heartbeat sent.")
        except Exception as exc:
            print(
                "Heartbeat error:",
                str(exc),
            )
    else:
        print("Heartbeat Telegram alert disabled.")

    # --------------------------------------------------------
    # ACTIVE TRADE
    # --------------------------------------------------------

    if state.get("active"):
        print("Active V4 paper trade found.")

        changed = monitor_trade(
            df,
            state,
        )

        if changed:
            save_state(state)
            print("Trade state updated.")

        else:
            print("Trade remains active.")

        # Never create a second trade while active.
        return

    # --------------------------------------------------------
    # NEW SIGNAL
    # --------------------------------------------------------

    signal = find_v4_signal(df)

    if signal is None:
        print("No new V4 signal.")
        return

    event_id = signal["event_id"]

    # This is the critical duplicate-alert protection.
    #
    # A completed signal can remain detectable on subsequent
    # 5-minute GitHub Actions runs. Once processed, it is never
    # alerted again.
    if event_already_processed(
        state,
        event_id,
    ):
        print(
            "Signal already processed. "
            "No duplicate alert."
        )
        return

    # Extra protection against the immediately previous signal.
    if (
        state.get("last_signal_event_id")
        == event_id
    ):
        mark_event_processed(
            state,
            event_id,
        )
        save_state(state)

        print(
            "Duplicate signal ID ignored."
        )
        return

    print(
        "NEW V4 BEARISH SIGNAL:",
        event_id,
    )

    new_state = create_trade(
        df,
        signal,
    )

    save_state(new_state)

    print(
        "V4 paper trade created."
    )


if __name__ == "__main__":
    main()
