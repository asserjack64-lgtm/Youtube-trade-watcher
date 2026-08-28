import argparse
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests


# ============================================================
# SETTINGS
# ============================================================

SYMBOL = "BTCUSDT"
INTERVAL = "5m"

# Range = previous 24 completed 5-minute candles
# = approximately 2 hours.
RANGE_CANDLES = 24

# Maximum allowed range width.
MAX_RANGE_WIDTH_PCT = 0.015

# Sweep must move beyond the range by this percentage.
SWEEP_PCT = 0.0003

# Stop is placed slightly beyond the sweep extreme.
SL_BUFFER_PCT = 0.0005

# Volume confirmation.
VOLUME_LOOKBACK = 20
MIN_VOLUME_RATIO = 1.20

# Confirmation candle body.
CONFIRM_BODY_MIN_PCT = 0.0005

# Maximum time to hold a paper trade.
MAX_BARS_IN_TRADE = 72


# ============================================================
# BINANCE API
# ============================================================

API_URL = (
    "https://data-api.binance.vision/"
    "api/v3/klines"
)


# ============================================================
# FETCH HISTORICAL DATA
# ============================================================

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

        response = requests.get(
            API_URL,
            params=params,
            timeout=30
        )

        response.raise_for_status()

        batch = response.json()

        if not batch:
            break

        rows.extend(batch)

        last_open = int(
            batch[-1][0]
        )

        next_cursor = (
            last_open
            + 5 * 60 * 1000
        )

        if next_cursor <= cursor:
            break

        cursor = next_cursor

        if len(batch) < 1000:
            break

        time.sleep(0.08)


    columns = [
        "open_time",
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
        rows,
        columns=columns
    )


    if df.empty:

        raise RuntimeError(
            "Binance returned no candles."
        )


    # --------------------------------------------------------
    # Convert numerical columns
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # Convert timestamps
    # --------------------------------------------------------

    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        utc=True
    )


    df["close_time"] = pd.to_datetime(
        df["close_time"],
        unit="ms",
        utc=True
    )


    # --------------------------------------------------------
    # Clean data
    # --------------------------------------------------------

    df = (
        df
        .drop_duplicates("open_time")
        .sort_values("open_time")
        .reset_index(drop=True)
    )


    # --------------------------------------------------------
    # Never use a currently-forming candle
    # --------------------------------------------------------

    now = pd.Timestamp.now(
        tz="UTC"
    )

    df = df[
        df["close_time"] <= now
    ].reset_index(drop=True)


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
# FIND BEARISH SIGNAL
# ============================================================

def find_signal(
    df,
    i
):

    """
    BEARISH-ONLY RANGE LIQUIDITY SWEEP MODEL

    1. Price consolidates inside a range.
    2. Price sweeps above the range high.
    3. Sweep candle closes back below the range high.
    4. Next candle confirms bearish displacement.
    5. Entry = confirmation candle close.
    6. Stop = above sweep extreme.
    7. TP1 = 1R.
    8. TP2 = opposite side of range, capped at 2R.
    """

    # i = confirmation candle
    # i-1 = sweep candle

    sweep_i = i - 1


    # --------------------------------------------------------
    # Ensure enough history
    # --------------------------------------------------------

    if sweep_i < (
        RANGE_CANDLES
        + VOLUME_LOOKBACK
    ):

        return None


    # --------------------------------------------------------
    # Build previous range
    # --------------------------------------------------------

    range_df = df.iloc[
        sweep_i - RANGE_CANDLES:
        sweep_i
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


    # Reject excessively wide ranges.

    if (
        range_width_pct
        > MAX_RANGE_WIDTH_PCT
    ):

        return None


    # --------------------------------------------------------
    # Sweep candle
    # --------------------------------------------------------

    sweep = df.iloc[
        sweep_i
    ]


    # --------------------------------------------------------
    # Confirmation candle
    # --------------------------------------------------------

    confirm = df.iloc[
        i
    ]


    sweep_high = float(
        sweep["high"]
    )

    sweep_close = float(
        sweep["close"]
    )


    confirm_open = float(
        confirm["open"]
    )

    confirm_close = float(
        confirm["close"]
    )


    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    vr = volume_ratio(
        df,
        sweep_i
    )


    # ========================================================
    # BEARISH LIQUIDITY SWEEP
    # ========================================================

    bearish_sweep = (

        sweep_high
        >
        range_high
        * (1 + SWEEP_PCT)

        and

        sweep_close
        <
        range_high
    )


    # ========================================================
    # BEARISH CONFIRMATION
    # ========================================================

    bearish_confirmation = (

        confirm_close
        <
        range_high

        and

        confirm_close
        <
        confirm_open

        and

        (
            confirm_open
            - confirm_close
        )
        / confirm_open
        >= CONFIRM_BODY_MIN_PCT

        and

        confirm_close
        <
        (
            sweep_high
            + sweep_close
        ) / 2
    )


    # --------------------------------------------------------
    # All conditions must pass
    # --------------------------------------------------------

    if not (
        bearish_sweep
        and bearish_confirmation
        and vr >= MIN_VOLUME_RATIO
    ):

        return None


    # ========================================================
    # ENTRY
    # ========================================================

    entry = confirm_close


    # ========================================================
    # STOP LOSS
    # ========================================================

    stop = (
        sweep_high
        * (1 + SL_BUFFER_PCT)
    )


    risk = (
        stop
        - entry
    )


    if risk <= 0:

        return None


    # ========================================================
    # TP1
    # ========================================================

    tp1 = (
        entry
        - risk
    )


    # ========================================================
    # TP2
    # ========================================================

    opposite = range_low


    tp2 = min(
        entry - 2 * risk,
        opposite
    )


    # ========================================================
    # PLANNED R/R
    # ========================================================

    planned_reward = (
        entry
        - tp2
    ) / risk


    # Need at least 1.5R available.

    if planned_reward < 1.5:

        return None


    # ========================================================
    # RETURN SIGNAL
    # ========================================================

    return {

        "direction":
            "BEARISH",

        "signal_time":
            confirm["open_time"],

        "sweep_time":
            sweep["open_time"],

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

        "planned_reward_r":
            planned_reward,

        "volume_ratio":
            vr,

        "range_width_pct":
            range_width_pct
    }


# ============================================================
# SIMULATE PAPER TRADE
# ============================================================

def simulate_trade(
    df,
    entry_i,
    signal
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


    tp1_hit = False

    tp1_bar = None

    bars = 0


    # ========================================================
    # WALK FORWARD THROUGH FUTURE CANDLES
    # ========================================================

    for j in range(
        entry_i + 1,
        len(df)
    ):

        bars += 1

        candle = df.iloc[
            j
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


        # ====================================================
        # BEARISH TRADE
        # ====================================================

        if direction == "BEARISH":

            # ------------------------------------------------
            # STOP LOSS
            # ------------------------------------------------
            #
            # Conservative assumption:
            # if stop and target are both touched
            # on the same candle, SL is counted first.
            #

            if high >= stop:

                return {

                    "outcome":
                        "SL",

                    "r":
                        -1.0,

                    "close_time":
                        candle["close_time"],

                    "bars":
                        bars,

                    "tp1_hit":
                        tp1_hit,

                    "tp1_bar":
                        tp1_bar
                }


            # ------------------------------------------------
            # TP1
            # ------------------------------------------------

            if (
                not tp1_hit
                and low <= tp1
            ):

                tp1_hit = True

                tp1_bar = bars


            # ------------------------------------------------
            # TP2
            # ------------------------------------------------

            if low <= tp2:

                return {

                    "outcome":
                        "TP2",

                    "r":
                        2.0,

                    "close_time":
                        candle["close_time"],

                    "bars":
                        bars,

                    "tp1_hit":
                        True,

                    "tp1_bar":
                        tp1_bar
                }


        # ====================================================
        # TIME EXIT
        # ====================================================

        if bars >= MAX_BARS_IN_TRADE:

            risk = (
                stop
                - entry
            )


            if risk <= 0:

                r = 0.0

            else:

                r = (
                    entry
                    - close
                ) / risk


            return {

                "outcome":
                    "TIME",

                "r":
                    float(r),

                "close_time":
                    candle["close_time"],

                "bars":
                    bars,

                "tp1_hit":
                    tp1_hit,

                "tp1_bar":
                    tp1_bar
            }


    # ========================================================
    # OPEN AT END OF DATA
    # ========================================================

    return {

        "outcome":
            "OPEN_AT_END",

        "r":
            0.0,

        "close_time":
            df.iloc[-1]["close_time"],

        "bars":
            bars,

        "tp1_hit":
            tp1_hit,

        "tp1_bar":
            tp1_bar
    }


# ============================================================
# RUN BACKTEST
# ============================================================

def run_backtest(
    df
):

    trades = []


    i = (
        RANGE_CANDLES
        + VOLUME_LOOKBACK
        + 1
    )


    while i < len(df) - 2:

        signal = find_signal(
            df,
            i
        )


        if signal is None:

            i += 1

            continue


        result = simulate_trade(
            df,
            i,
            signal
        )


        trade = {

            **signal,

            **result,

            "entry_time":
                df.iloc[i]["open_time"]
        }


        trades.append(
            trade
        )


        # ----------------------------------------------------
        # Prevent overlapping trades.
        # ----------------------------------------------------

        exit_i = (
            i
            + result["bars"]
        )


        i = max(
            i + 1,
            exit_i + 1
        )


    return pd.DataFrame(
        trades
    )


# ============================================================
# SUMMARY
# ============================================================

def summarize(
    trades,
    df
):

    if trades.empty:

        print(
            "\n"
            + "=" * 65
        )

        print(
            "NO TRADES FOUND"
        )

        print(
            "=" * 65
        )

        print(
            "The bearish filters may be "
            "too strict for this period."
        )

        return


    # ========================================================
    # BASIC COUNTS
    # ========================================================

    total = len(
        trades
    )


    tp2_wins = int(
        (
            trades["outcome"]
            == "TP2"
        ).sum()
    )


    stop_losses = int(
        (
            trades["outcome"]
            == "SL"
        ).sum()
    )


    time_exits = int(
        (
            trades["outcome"]
            == "TIME"
        ).sum()
    )


    open_at_end = int(
        (
            trades["outcome"]
            == "OPEN_AT_END"
        ).sum()
    )


    # ========================================================
    # TP1
    # ========================================================

    tp1_hits = int(
        trades["tp1_hit"].sum()
    )


    tp1_then_sl = int(
        (
            (trades["tp1_hit"] == True)
            &
            (trades["outcome"] == "SL")
        ).sum()
    )


    # ========================================================
    # R VALUES
    # ========================================================

    r_values = (
        trades["r"]
        .astype(float)
    )


    total_r = float(
        r_values.sum()
    )


    average_r = float(
        r_values.mean()
    )


    # ========================================================
    # PROFIT FACTOR
    # ========================================================

    gross_profit = float(
        r_values[
            r_values > 0
        ].sum()
    )


    gross_loss = abs(
        float(
            r_values[
                r_values < 0
            ].sum()
        )
    )


    if gross_loss > 0:

        profit_factor = (
            gross_profit
            / gross_loss
        )

    else:

        profit_factor = float(
            "inf"
        )


    # ========================================================
    # EQUITY CURVE
    # ========================================================

    equity = (
        r_values
        .cumsum()
    )


    peak = (
        equity
        .cummax()
    )


    drawdown = (
        equity
        - peak
    )


    max_drawdown = float(
        drawdown.min()
    )


    # ========================================================
    # R CONTRIBUTION
    # ========================================================

    tp2_r = float(
        trades.loc[
            trades["outcome"] == "TP2",
            "r"
        ].sum()
    )


    sl_r = float(
        trades.loc[
            trades["outcome"] == "SL",
            "r"
        ].sum()
    )


    time_r = float(
        trades.loc[
            trades["outcome"] == "TIME",
            "r"
        ].sum()
    )


    total_check = (
        tp2_r
        + sl_r
        + time_r
    )


    # ========================================================
    # REPORT
    # ========================================================

    print(
        "\n"
        + "=" * 65
    )

    print(
        "BTCUSDT 5m BEARISH-ONLY "
        "RANGE LIQUIDITY BACKTEST"
    )

    print(
        "=" * 65
    )


    print(
        f"Period:              "
        f"{df_start} → {df_end}"
    )


    print(
        f"Candles:             "
        f"{len(df):,}"
    )


    print(
        f"Trades:              "
        f"{total}"
    )


    # ========================================================
    # OUTCOMES
    # ========================================================

    print(
        "\nOUTCOMES"
    )


    print(
        f"TP2 wins:            "
        f"{tp2_wins}"
    )


    print(
        f"Stop losses:         "
        f"{stop_losses}"
    )


    print(
        f"Time exits:          "
        f"{time_exits}"
    )


    print(
        f"Open at end:         "
        f"{open_at_end}"
    )


    print(
        f"TP1 hits:            "
        f"{tp1_hits}"
    )


    print(
        f"TP1 → SL:            "
        f"{tp1_then_sl}"
    )


    # ========================================================
    # PERFORMANCE
    # ========================================================

    print(
        "\nPERFORMANCE"
    )


    print(
        f"TP2 win rate:        "
        f"{tp2_wins / total * 100:.1f}%"
    )


    print(
        f"Total R:             "
        f"{total_r:+.2f}R"
    )


    print(
        f"Average R/trade:     "
        f"{average_r:+.3f}R"
    )


    print(
        f"Profit factor:       "
        f"{profit_factor:.2f}"
    )


    print(
        f"Max drawdown:        "
        f"{max_drawdown:.2f}R"
    )


    # ========================================================
    # R CONTRIBUTION
    # ========================================================

    print(
        "\nR CONTRIBUTION"
    )


    print(
        f"TP2 R:               "
        f"{tp2_r:+.2f}R"
    )


    print(
        f"SL R:                "
        f"{sl_r:+.2f}R"
    )


    print(
        f"TIME R:              "
        f"{time_r:+.2f}R"
    )


    print(
        f"TOTAL R:             "
        f"{total_check:+.2f}R"
    )


    # ========================================================
    # VOLUME
    # ========================================================

    print(
        "\nSIGNAL QUALITY"
    )


    print(
        f"Average sweep volume: "
        f"{trades['volume_ratio'].mean():.2f}x"
    )


    print(
        f"Minimum sweep volume:  "
        f"{trades['volume_ratio'].min():.2f}x"
    )


    print(
        f"Maximum sweep volume:  "
        f"{trades['volume_ratio'].max():.2f}x"
    )


    # ========================================================
    # RANGE
    # ========================================================

    print(
        "\nRANGE ANALYSIS"
    )


    print(
        f"Average range width: "
        f"{trades['range_width_pct'].mean() * 100:.3f}%"
    )


    print(
        f"Minimum range width: "
        f"{trades['range_width_pct'].min() * 100:.3f}%"
    )


    print(
        f"Maximum range width: "
        f"{trades['range_width_pct'].max() * 100:.3f}%"
    )


    # ========================================================
    # LAST TRADES
    # ========================================================

    print(
        "\nLAST 15 TRADES"
    )


    columns = [

        "entry_time",

        "direction",

        "entry",

        "stop",

        "tp1",

        "tp2",

        "planned_reward_r",

        "volume_ratio",

        "outcome",

        "r",

        "tp1_hit"
    ]


    available_columns = [

        column

        for column in columns

        if column in trades.columns
    ]


    print(
        trades[
            available_columns
        ]
        .tail(15)
        .to_string(
            index=False
        )
    )


    # ========================================================
    # FINAL CHECK
    # ========================================================

    print(
        "\n"
        + "=" * 65
    )


    print(
        "ACCOUNTING CHECK"
    )


    print(
        f"TP2 + SL + TIME = "
        f"{total_check:+.2f}R"
    )


    print(
        f"Reported TOTAL R = "
        f"{total_r:+.2f}R"
    )


    if abs(
        total_check - total_r
    ) < 0.000001:

        print(
            "✓ R accounting is consistent."
        )

    else:

        print(
            "⚠ R accounting mismatch detected."
        )


    print(
        "=" * 65
    )


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser()


    parser.add_argument(
        "--days",
        type=int,
        default=180
    )


    parser.add_argument(
        "--output",
        default="backtest_trades.csv"
    )


    args = parser.parse_args()


    # ========================================================
    # DATE RANGE
    # ========================================================

    end = datetime.now(
        timezone.utc
    )


    start = (
        end
        - timedelta(
            days=args.days
        )
    )


    global df_start
    global df_end


    df_start = (
        start.strftime(
            "%Y-%m-%d"
        )
    )


    df_end = (
        end.strftime(
            "%Y-%m-%d"
        )
    )


    # ========================================================
    # DOWNLOAD
    # ========================================================

    print(
        f"Downloading BTCUSDT 5m "
        f"data for {args.days} days..."
    )


    data = fetch_klines(

        int(
            start.timestamp()
            * 1000
        ),

        int(
            end.timestamp()
            * 1000
        )
    )


    print(
        f"Downloaded "
        f"{len(data):,} completed candles."
    )


    # ========================================================
    # RUN
    # ========================================================

    trades = run_backtest(
        data
    )


    # ========================================================
    # SAVE
    # ========================================================

    if not trades.empty:

        trades.to_csv(
            args.output,
            index=False
        )


        print(
            f"\nSaved trade list to "
            f"{args.output}"
        )


    # ========================================================
    # REPORT
    # ========================================================

    summarize(
        trades,
        data
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
