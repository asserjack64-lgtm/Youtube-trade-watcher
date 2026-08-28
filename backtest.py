import argparse
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests


# ============================================================
# VERSION 2 — BEARISH-ONLY RANGE LIQUIDITY SWEEP
# ============================================================

SYMBOL = "BTCUSDT"
INTERVAL = "5m"

# Range
RANGE_CANDLES = 24              # 2 hours
MAX_RANGE_WIDTH_PCT = 0.015     # <= 1.5%

# Sweep
SWEEP_PCT = 0.0003              # 0.03% beyond range
SL_BUFFER_PCT = 0.0005          # 0.05% beyond sweep extreme

# Stronger signal-quality filter
VOLUME_LOOKBACK = 20
MIN_VOLUME_RATIO = 1.50         # V2: raised from 1.20x

# Stronger bearish confirmation
CONFIRM_BODY_MIN_PCT = 0.0008   # V2: 0.08% bearish body

# Trade management
MAX_BARS_IN_TRADE = 72          # 6 hours
MIN_TP2_R = 1.50                 # minimum planned reward
TARGET_R = 2.0


API_URL = (
    "https://data-api.binance.vision/api/v3/klines"
)


# ============================================================
# DOWNLOAD BINANCE DATA
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
            timeout=30,
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
        "unused",
    ]


    df = pd.DataFrame(
        rows,
        columns=columns,
    )


    if df.empty:

        raise RuntimeError(
            "Binance returned no candles."
        )


    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )


    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        utc=True,
    )


    df["close_time"] = pd.to_datetime(
        df["close_time"],
        unit="ms",
        utc=True,
    )


    df = (
        df
        .drop_duplicates(
            "open_time"
        )
        .sort_values(
            "open_time"
        )
        .reset_index(
            drop=True
        )
    )


    # Never backtest the currently
    # forming candle.

    now = pd.Timestamp.now(
        tz="UTC"
    )

    df = df[
        df["close_time"] <= now
    ].reset_index(
        drop=True
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


    if (
        len(previous) < 5
        or previous.mean() <= 0
    ):

        return 0.0


    return float(
        df.iloc[index]["volume"]
        /
        previous.mean()
    )


# ============================================================
# FIND BEARISH SIGNAL
# ============================================================

def find_signal(
    df,
    i
):

    """
    V2 bearish-only model.

    1. Establish a 2-hour range.
    2. Price sweeps above range high.
    3. Sweep candle closes back inside range.
    4. Next candle confirms with strong bearish body.
    5. Sweep volume >= 1.50x average.
    6. Entry = confirmation close.
    7. SL = sweep high + buffer.
    8. TP1 = 1R.
    9. TP2 = 2R, but only if >= 1.5R available.
    """


    # i = confirmation candle

    sweep_i = i - 1


    minimum_history = (
        RANGE_CANDLES
        + VOLUME_LOOKBACK
    )


    if sweep_i < minimum_history:

        return None


    # --------------------------------------------------------
    # RANGE
    # --------------------------------------------------------

    range_df = df.iloc[
        sweep_i
        - RANGE_CANDLES:
        sweep_i
    ]


    range_high = float(
        range_df["high"].max()
    )


    range_low = float(
        range_df["low"].min()
    )


    mid = (
        range_high
        + range_low
    ) / 2


    if mid <= 0:

        return None


    range_width_pct = (
        range_high
        - range_low
    ) / mid


    if (
        range_width_pct
        > MAX_RANGE_WIDTH_PCT
    ):

        return None


    sweep = df.iloc[
        sweep_i
    ]

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
    # BEARISH SWEEP
    # --------------------------------------------------------

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


    if not bearish_sweep:

        return None


    # --------------------------------------------------------
    # STRONG BEARISH CONFIRMATION
    # --------------------------------------------------------

    confirm_body_pct = (

        confirm_open
        - confirm_close

    ) / confirm_open


    bearish_confirm = (

        confirm_close
        <
        range_high

        and

        confirm_close
        <
        confirm_open

        and

        confirm_body_pct
        >=
        CONFIRM_BODY_MIN_PCT

        and

        confirm_close
        <
        (
            sweep_high
            + sweep_close
        ) / 2
    )


    if not bearish_confirm:

        return None


    # --------------------------------------------------------
    # VOLUME FILTER
    # --------------------------------------------------------

    vr = volume_ratio(
        df,
        sweep_i
    )


    if vr < MIN_VOLUME_RATIO:

        return None


    # --------------------------------------------------------
    # TRADE LEVELS
    # --------------------------------------------------------

    entry = confirm_close


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
        - risk
    )


    two_r_target = (
        entry
        - (
            TARGET_R
            * risk
        )
    )


    # TP2 uses the farther target:
    #
    # 2R OR opposite side of range

    tp2 = min(
        two_r_target,
        range_low
    )


    planned_reward_r = (

        entry
        - tp2

    ) / risk


    # Require at least 1.5R
    # planned reward.

    if (
        planned_reward_r
        < MIN_TP2_R
    ):

        return None


    return {

        "direction":
            "BEARISH",

        "signal_time":
            confirm[
                "open_time"
            ],

        "sweep_time":
            sweep[
                "open_time"
            ],

        "range_high":
            range_high,

        "range_low":
            range_low,

        "range_width_pct":
            range_width_pct,

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

        "confirm_body_pct":
            confirm_body_pct,

        "planned_reward_r":
            planned_reward_r,
    }


# ============================================================
# SIMULATE TRADE
# ============================================================

def simulate_trade(
    df,
    entry_i,
    signal
):

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

    bars = 0


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


        # ----------------------------------------------------
        # CONSERVATIVE RULE
        # ----------------------------------------------------
        #
        # If SL and TP are both touched
        # in the same candle, SL wins.
        #

        if high >= stop:

            return {

                "outcome":
                    "SL",

                "r":
                    -1.0,

                "close_time":
                    candle[
                        "close_time"
                    ],

                "bars":
                    bars,

                "tp1_hit":
                    tp1_hit,
            }


        # ----------------------------------------------------
        # TP1
        # ----------------------------------------------------

        if (
            not tp1_hit
            and low <= tp1
        ):

            tp1_hit = True


        # ----------------------------------------------------
        # TP2
        # ----------------------------------------------------

        if low <= tp2:

            return {

                "outcome":
                    "TP2",

                "r":
                    TARGET_R,

                "close_time":
                    candle[
                        "close_time"
                    ],

                "bars":
                    bars,

                "tp1_hit":
                    True,
            }


        # ----------------------------------------------------
        # TIME EXIT
        # ----------------------------------------------------

        if (
            bars
            >= MAX_BARS_IN_TRADE
        ):

            r = (

                entry
                - close

            ) / (

                stop
                - entry
            )


            return {

                "outcome":
                    "TIME",

                "r":
                    r,

                "close_time":
                    candle[
                        "close_time"
                    ],

                "bars":
                    bars,

                "tp1_hit":
                    tp1_hit,
            }


    # --------------------------------------------------------
    # OPEN AT END
    # --------------------------------------------------------

    return {

        "outcome":
            "OPEN_AT_END",

        "r":
            0.0,

        "close_time":
            df.iloc[-1][
                "close_time"
            ],

        "bars":
            bars,

        "tp1_hit":
            tp1_hit,
    }


# ============================================================
# RUN BACKTEST
# ============================================================

def run_backtest(df):

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
                df.iloc[i][
                    "open_time"
                ],
        }


        trades.append(
            trade
        )


        # ----------------------------------------------------
        # NO OVERLAPPING TRADES
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
    df,
    start_date,
    end_date
):

    print(
        "\n"
        + "=" * 68
    )


    print(
        "BTCUSDT 5m V2 "
        "BEARISH-ONLY "
        "RANGE LIQUIDITY BACKTEST"
    )


    print(
        "=" * 68
    )


    print(
        f"Period:              "
        f"{start_date} → {end_date}"
    )


    print(
        f"Candles:             "
        f"{len(df):,}"
    )


    print(
        f"Filters:             "
        f"volume >= "
        f"{MIN_VOLUME_RATIO:.2f}x | "
        f"body >= "
        f"{CONFIRM_BODY_MIN_PCT * 100:.2f}%"
    )


    if trades.empty:

        print(
            "\nNO TRADES FOUND."
        )

        print(
            "Filters may be too strict."
        )

        print(
            "=" * 68
        )

        return


    # ========================================================
    # BASIC RESULTS
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


    sl_losses = int(
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


    open_end = int(
        (
            trades["outcome"]
            == "OPEN_AT_END"
        ).sum()
    )


    tp1_hits = int(
        trades["tp1_hit"].sum()
    )


    tp1_to_sl = int(
        (
            (
                trades["outcome"]
                == "SL"
            )
            &
            trades["tp1_hit"]
        ).sum()
    )


    # ========================================================
    # R CALCULATIONS
    # ========================================================

    r_values = trades[
        "r"
    ].astype(float)


    total_r = float(
        r_values.sum()
    )


    avg_r = float(
        r_values.mean()
    )


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


    profit_factor = (

        gross_profit
        / gross_loss

        if gross_loss > 0

        else float("inf")
    )


    # ========================================================
    # DRAW DOWN
    # ========================================================

    equity = (
        r_values.cumsum()
    )


    peak = (
        equity.cummax()
    )


    drawdown = (
        equity - peak
    )


    max_dd = float(
        drawdown.min()
    )


    # ========================================================
    # OTHER METRICS
    # ========================================================

    win_rate = (
        tp2_wins
        / total
        * 100
    )


    avg_volume = float(
        trades[
            "volume_ratio"
        ].mean()
    )


    min_volume = float(
        trades[
            "volume_ratio"
        ].min()
    )


    max_volume = float(
        trades[
            "volume_ratio"
        ].max()
    )


    avg_range = float(
        trades[
            "range_width_pct"
        ].mean()
        * 100
    )


    avg_planned_rr = float(
        trades[
            "planned_reward_r"
        ].mean()
    )


    # ========================================================
    # OUTPUT
    # ========================================================

    print(
        "\nOUTCOMES"
    )

    print(
        "-" * 68
    )


    print(
        f"Trades:              "
        f"{total}"
    )


    print(
        f"TP2 wins:            "
        f"{tp2_wins}"
    )


    print(
        f"Stop losses:         "
        f"{sl_losses}"
    )


    print(
        f"Time exits:          "
        f"{time_exits}"
    )


    print(
        f"Open at end:         "
        f"{open_end}"
    )


    print(
        f"TP1 hits:            "
        f"{tp1_hits}"
    )


    print(
        f"TP1 → SL:            "
        f"{tp1_to_sl}"
    )


    print(
        "\nPERFORMANCE"
    )


    print(
        "-" * 68
    )


    print(
        f"TP2 win rate:        "
        f"{win_rate:.1f}%"
    )


    print(
        f"Total R:             "
        f"{total_r:+.2f}R"
    )


    print(
        f"Average R/trade:     "
        f"{avg_r:+.3f}R"
    )


    print(
        f"Profit factor:       "
        f"{profit_factor:.2f}"
    )


    print(
        f"Max drawdown:        "
        f"{max_dd:.2f}R"
    )


    print(
        "\nSIGNAL QUALITY"
    )


    print(
        "-" * 68
    )


    print(
        f"Average sweep volume:"
        f"{avg_volume:>8.2f}x"
    )


    print(
        f"Minimum sweep volume:"
        f"{min_volume:>7.2f}x"
    )


    print(
        f"Maximum sweep volume:"
        f"{max_volume:>7.2f}x"
    )


    print(
        f"Average range width: "
        f"{avg_range:.3f}%"
    )


    print(
        f"Average planned R:   "
        f"{avg_planned_rr:.2f}R"
    )


    # ========================================================
    # R CONTRIBUTION
    # ========================================================

    time_r = float(
        r_values[
            trades["outcome"]
            == "TIME"
        ].sum()
    )


    print(
        "\nR CONTRIBUTION"
    )


    print(
        "-" * 68
    )


    print(
        f"TP2 R:               "
        f"{tp2_wins * TARGET_R:+.2f}R"
    )


    print(
        f"SL R:                "
        f"{sl_losses * -1.0:+.2f}R"
    )


    print(
        f"TIME R:              "
        f"{time_r:+.2f}R"
    )


    print(
        f"TOTAL R:             "
        f"{total_r:+.2f}R"
    )


    # ========================================================
    # ACCOUNTING CHECK
    # ========================================================

    print(
        "\nACCOUNTING CHECK"
    )


    print(
        "-" * 68
    )


    expected_r = (

        tp2_wins
        * TARGET_R

        - sl_losses

        + time_r
    )


    print(
        f"TP2 + SL + TIME:     "
        f"{expected_r:+.2f}R"
    )


    print(
        f"Reported Total R:    "
        f"{total_r:+.2f}R"
    )


    if abs(
        expected_r - total_r
    ) < 0.0001:

        print(
            "✓ R accounting is consistent."
        )

    else:

        print(
            "⚠ R accounting mismatch detected."
        )


    # ========================================================
    # LAST TRADES
    # ========================================================

    print(
        "\nLAST 15 TRADES"
    )


    print(
        "-" * 68
    )


    columns = [

        "entry_time",
        "direction",
        "entry",
        "stop",
        "tp1",
        "tp2",
        "volume_ratio",
        "planned_reward_r",
        "outcome",
        "r",
        "tp1_hit",
    ]


    display = (
        trades[
            columns
        ]
        .tail(15)
        .copy()
    )


    print(
        display.to_string(
            index=False
        )
    )


    print(
        "=" * 68
    )


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser()


    parser.add_argument(
        "--days",
        type=int,
        default=180,
    )


    parser.add_argument(
        "--output",
        default="backtest_v2_trades.csv",
    )


    args = parser.parse_args()


    end = datetime.now(
        timezone.utc
    )


    start = (
        end
        - timedelta(
            days=args.days
        )
    )


    start_date = (
        start.strftime(
            "%Y-%m-%d"
        )
    )


    end_date = (
        end.strftime(
            "%Y-%m-%d"
        )
    )


    print(
        f"Downloading BTCUSDT 5m data "
        f"for {args.days} days..."
    )


    data = fetch_klines(

        int(
            start.timestamp()
            * 1000
        ),

        int(
            end.timestamp()
            * 1000
        ),
    )


    print(
        f"Downloaded "
        f"{len(data):,} "
        f"completed candles."
    )


    trades = run_backtest(
        data
    )


    if not trades.empty:

        trades.to_csv(
            args.output,
            index=False,
        )


        print(
            f"\nSaved trade list to "
            f"{args.output}"
        )


    summarize(

        trades,
        data,
        start_date,
        end_date,
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
