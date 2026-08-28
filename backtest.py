import argparse
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests


SYMBOL = "BTCUSDT"
INTERVAL = "5m"

# Strategy parameters — deliberately fixed for the first validation run.
RANGE_CANDLES = 24              # 2 hours on 5m
MAX_RANGE_WIDTH_PCT = 0.015     # range must be <= 1.5%
SWEEP_PCT = 0.0003              # 0.03% beyond range
SL_BUFFER_PCT = 0.0005          # 0.05% beyond sweep extreme
VOLUME_LOOKBACK = 20
MIN_VOLUME_RATIO = 1.20

# Confirmation is the candle immediately after the sweep.
# It must reclaim the range and show directional displacement.
CONFIRM_BODY_MIN_PCT = 0.0005

# Do not open another trade while one is active.
MAX_BARS_IN_TRADE = 72          # 6 hours
STARTING_R = 0.0

API_URL = "https://data-api.binance.vision/api/v3/klines"


def fetch_klines(start_ms, end_ms):
    rows = []
    cursor = start_ms

    while cursor < end_ms:
        params = {
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1000,
        }

        r = requests.get(API_URL, params=params, timeout=30)
        r.raise_for_status()
        batch = r.json()

        if not batch:
            break

        rows.extend(batch)

        last_open = int(batch[-1][0])
        next_cursor = last_open + 5 * 60 * 1000

        if next_cursor <= cursor:
            break

        cursor = next_cursor

        if len(batch) < 1000:
            break

        time.sleep(0.08)

    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_volume", "taker_buy_quote_volume", "unused"
    ]

    df = pd.DataFrame(rows, columns=cols)

    if df.empty:
        raise RuntimeError("Binance returned no candles.")

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["open_time"] = pd.to_datetime(
        df["open_time"], unit="ms", utc=True
    )

    df["close_time"] = pd.to_datetime(
        df["close_time"], unit="ms", utc=True
    )

    df = df.drop_duplicates("open_time").sort_values("open_time")
    df = df.reset_index(drop=True)

    # Never backtest the currently-forming candle.
    now = pd.Timestamp.now(tz="UTC")
    df = df[df["close_time"] <= now].reset_index(drop=True)

    return df


def volume_ratio(df, i):
    start = max(0, i - VOLUME_LOOKBACK)
    x = df.iloc[start:i]["volume"]

    if len(x) < 5 or x.mean() <= 0:
        return 0.0

    return float(df.iloc[i]["volume"] / x.mean())


def find_signal(df, i):
    """
    The video-inspired model:

    1) Price has been consolidating in a defined range.
    2) Price sweeps one side of that range.
    3) The sweep candle closes back inside the range.
    4) The following candle confirms the reversal.
    5) Entry is at confirmation close.
    6) SL is beyond the actual sweep extreme.
    7) TP1 is 1R.
    8) TP2 is the opposite side of the range, provided it gives >= 1.5R.
    """
    # i = confirmation candle
    sweep_i = i - 1

    if sweep_i < RANGE_CANDLES + VOLUME_LOOKBACK:
        return None

    range_df = df.iloc[sweep_i - RANGE_CANDLES:sweep_i]

    range_high = float(range_df["high"].max())
    range_low = float(range_df["low"].min())

    mid = (range_high + range_low) / 2
    if mid <= 0:
        return None

    width_pct = (range_high - range_low) / mid

    if width_pct > MAX_RANGE_WIDTH_PCT:
        return None

    sweep = df.iloc[sweep_i]
    confirm = df.iloc[i]

    sweep_high = float(sweep["high"])
    sweep_low = float(sweep["low"])
    sweep_close = float(sweep["close"])

    confirm_open = float(confirm["open"])
    confirm_high = float(confirm["high"])
    confirm_low = float(confirm["low"])
    confirm_close = float(confirm["close"])

    vr = volume_ratio(df, sweep_i)

    # Bearish: run above range high, then close back inside.
    bearish_sweep = (
        sweep_high > range_high * (1 + SWEEP_PCT)
        and sweep_close < range_high
    )

    bearish_confirm = (
        confirm_close < range_high
        and confirm_close < confirm_open
        and (confirm_open - confirm_close) / confirm_open
        >= CONFIRM_BODY_MIN_PCT
        and confirm_close < (sweep_high + sweep_close) / 2
    )

    if bearish_sweep and bearish_confirm and vr >= MIN_VOLUME_RATIO:
        entry = confirm_close
        stop = sweep_high * (1 + SL_BUFFER_PCT)
        risk = stop - entry

        if risk <= 0:
            return None

        tp1 = entry - risk
        opposite = range_low
        tp2 = min(entry - 2 * risk, opposite)

        if (entry - tp2) / risk < 1.5:
            return None

        return {
            "direction": "BEARISH",
            "signal_time": confirm["open_time"],
            "sweep_time": sweep["open_time"],
            "range_high": range_high,
            "range_low": range_low,
            "entry": entry,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "volume_ratio": vr,
            "range_width_pct": width_pct,
        }

    # Bullish: run below range low, then close back inside.
    bullish_sweep = (
        sweep_low < range_low * (1 - SWEEP_PCT)
        and sweep_close > range_low
    )

    bullish_confirm = (
        confirm_close > range_low
        and confirm_close > confirm_open
        and (confirm_close - confirm_open) / confirm_open
        >= CONFIRM_BODY_MIN_PCT
        and confirm_close > (sweep_low + sweep_close) / 2
    )

    if bullish_sweep and bullish_confirm and vr >= MIN_VOLUME_RATIO:
        entry = confirm_close
        stop = sweep_low * (1 - SL_BUFFER_PCT)
        risk = entry - stop

        if risk <= 0:
            return None

        tp1 = entry + risk
        opposite = range_high
        tp2 = max(entry + 2 * risk, opposite)

        if (tp2 - entry) / risk < 1.5:
            return None

        return {
            "direction": "BULLISH",
            "signal_time": confirm["open_time"],
            "sweep_time": sweep["open_time"],
            "range_high": range_high,
            "range_low": range_low,
            "entry": entry,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "volume_ratio": vr,
            "range_width_pct": width_pct,
        }

    return None


def simulate_trade(df, entry_i, signal):
    direction = signal["direction"]
    entry = signal["entry"]
    stop = signal["stop"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]

    tp1_hit = False
    bars = 0

    for j in range(entry_i + 1, len(df)):
        bars += 1
        candle = df.iloc[j]

        high = float(candle["high"])
        low = float(candle["low"])

        # Conservative rule: if SL and a target are both touched
        # in the same candle, count the SL first.
        if direction == "BULLISH":
            if low <= stop:
                return {
                    "outcome": "SL",
                    "r": -1.0,
                    "close_time": candle["close_time"],
                    "bars": bars,
                    "tp1_hit": tp1_hit,
                }

            if not tp1_hit and high >= tp1:
                tp1_hit = True

            if high >= tp2:
                return {
                    "outcome": "TP2",
                    "r": 2.0,
                    "close_time": candle["close_time"],
                    "bars": bars,
                    "tp1_hit": True,
                }

        else:
            if high >= stop:
                return {
                    "outcome": "SL",
                    "r": -1.0,
                    "close_time": candle["close_time"],
                    "bars": bars,
                    "tp1_hit": tp1_hit,
                }

            if not tp1_hit and low <= tp1:
                tp1_hit = True

            if low <= tp2:
                return {
                    "outcome": "TP2",
                    "r": 2.0,
                    "close_time": candle["close_time"],
                    "bars": bars,
                    "tp1_hit": True,
                }

        if bars >= MAX_BARS_IN_TRADE:
            # Time exit: mark to market in R.
            close = float(candle["close"])
            if direction == "BULLISH":
                r = (close - entry) / (entry - stop)
            else:
                r = (entry - close) / (stop - entry)

            return {
                "outcome": "TIME",
                "r": r,
                "close_time": candle["close_time"],
                "bars": bars,
                "tp1_hit": tp1_hit,
            }

    return {
        "outcome": "OPEN_AT_END",
        "r": 0.0,
        "close_time": df.iloc[-1]["close_time"],
        "bars": bars,
        "tp1_hit": tp1_hit,
    }


def run_backtest(df):
    trades = []
    i = RANGE_CANDLES + VOLUME_LOOKBACK + 1

    while i < len(df) - 2:
        signal = find_signal(df, i)

        if signal is None:
            i += 1
            continue

        result = simulate_trade(df, i, signal)

        trade = {
            **signal,
            **result,
            "entry_time": df.iloc[i]["open_time"],
        }

        trades.append(trade)

        # Do not allow overlapping trades.
        exit_i = i + result["bars"]
        i = max(i + 1, exit_i + 1)

    return pd.DataFrame(trades)


def summarize(trades):
    if trades.empty:
        print("\nNO TRADES FOUND.")
        print("The filters may be too strict for this period.")
        return

    total = len(trades)
    wins = int((trades["outcome"] == "TP2").sum())
    losses = int((trades["outcome"] == "SL").sum())
    time_exits = int((trades["outcome"] == "TIME").sum())

    r_values = trades["r"].astype(float)
    total_r = float(r_values.sum())
    avg_r = float(r_values.mean())

    gross_profit = float(r_values[r_values > 0].sum())
    gross_loss = abs(float(r_values[r_values < 0].sum()))
    profit_factor = (
        gross_profit / gross_loss if gross_loss > 0 else float("inf")
    )

    equity = r_values.cumsum()
    peak = equity.cummax()
    drawdown = equity - peak
    max_dd = float(drawdown.min())

    print("\n" + "=" * 60)
    print("BTCUSDT 5m RANGE LIQUIDITY SWEEP BACKTEST")
    print("=" * 60)
    print(f"Period:          {df_start} → {df_end}")
    print(f"Candles:         {len(DATA):,}")
    print(f"Trades:          {total}")
    print(f"TP2 wins:        {wins}")
    print(f"SL losses:       {losses}")
    print(f"Time exits:      {time_exits}")
    print(f"Win rate (TP2):  {wins / total * 100:.1f}%")
    print(f"Total R:         {total_r:+.2f}R")
    print(f"Average R/trade: {avg_r:+.3f}R")
    print(f"Profit factor:   {profit_factor:.2f}")
    print(f"Max drawdown:    {max_dd:.2f}R")
    print("=" * 60)

    print("\nBy direction:")
    for direction in ["BULLISH", "BEARISH"]:
        x = trades[trades["direction"] == direction]
        if len(x):
            print(
                f"  {direction:<8} "
                f"trades={len(x):3d}  "
                f"R={x['r'].sum():+7.2f}  "
                f"win={((x['outcome']=='TP2').mean()*100):5.1f}%"
            )

    print("\nLast 10 trades:")
    cols = [
        "entry_time", "direction", "entry",
        "stop", "tp1", "tp2", "outcome", "r"
    ]
    print(trades[cols].tail(10).to_string(index=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--output", default="backtest_trades.csv")
    args = parser.parse_args()

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)

    global df_start, df_end, DATA
    df_start = start.strftime("%Y-%m-%d")
    df_end = end.strftime("%Y-%m-%d")

    print(f"Downloading BTCUSDT 5m data for {args.days} days...")
    DATA = fetch_klines(
        int(start.timestamp() * 1000),
        int(end.timestamp() * 1000),
    )
    print(f"Downloaded {len(DATA):,} completed candles.")

    trades = run_backtest(DATA)

    if not trades.empty:
        trades.to_csv(args.output, index=False)
        print(f"\nSaved trade list to {args.output}")

    summarize(trades)


if __name__ == "__main__":
    main()
