import argparse
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests


# ============================================================
# BASIC SETTINGS
# ============================================================

SYMBOL = "BTCUSDT"
INTERVAL = "5m"

API_URL = "https://data-api.binance.vision/api/v3/klines"


# ============================================================
# BASE STRATEGY
# ============================================================

RANGE_CANDLES = 24

MAX_RANGE_WIDTH_PCT = 0.015

SWEEP_PCT = 0.0003

SL_BUFFER_PCT = 0.0005

VOLUME_LOOKBACK = 20

MIN_VOLUME_RATIO = 1.50

CONFIRM_BODY_MIN_PCT = 0.0008

MAX_BARS_IN_TRADE = 72


# ============================================================
# TRADE MANAGEMENT
# ============================================================

TP1_R = 1.0
TP2_R = 2.0

TP1_PARTIAL = 0.50

MOVE_SL_TO_BREAKEVEN = True


# ============================================================
# PARAMETER TESTS
# ============================================================

TEST_CONFIGS = [

    {
        "name": "BASE",
        "volume": 1.50,
        "body": 0.0008,
        "range": 0.015,
    },

    {
        "name": "LOWER_VOLUME",
        "volume": 1.30,
        "body": 0.0008,
        "range": 0.015,
    },

    {
        "name": "HIGHER_VOLUME",
        "volume": 1.80,
        "body": 0.0008,
        "range": 0.015,
    },

    {
        "name": "LOWER_BODY",
        "volume": 1.50,
        "body": 0.0005,
        "range": 0.015,
    },

    {
        "name": "TIGHT_RANGE",
        "volume": 1.50,
        "body": 0.0008,
        "range": 0.012,
    },

    {
        "name": "STRICT",
        "volume": 1.80,
        "body": 0.0010,
        "range": 0.012,
    },
]


# ============================================================
# DOWNLOAD DATA
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

        last_open = int(batch[-1][0])

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


    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]


    for column in numeric_columns:

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
        .drop_duplicates("open_time")
        .sort_values("open_time")
        .reset_index(drop=True)
    )


    # Never use the currently forming candle.

    now = pd.Timestamp.now(
        tz="UTC"
    )

    df = df[
        df["close_time"] <= now
    ].reset_index(drop=True)


    return df


# ============================================================
# VOLUME
# ============================================================

def volume_ratio(
    df,
    index,
):

    start = max(
        0,
        index - VOLUME_LOOKBACK,
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
    i,
    config,
):

    sweep_i = i - 1


    if sweep_i < (
        RANGE_CANDLES
        + VOLUME_LOOKBACK
    ):

        return None


    # --------------------------------------------------------
    # RANGE
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


    range_width = (
        range_high
        - range_low
    ) / midpoint


    if range_width > config["range"]:

        return None


    # --------------------------------------------------------
    # SWEEP CANDLE
    # --------------------------------------------------------

    sweep = df.iloc[
        sweep_i
    ]


    sweep_high = float(
        sweep["high"]
    )


    sweep_close = float(
        sweep["close"]
    )


    # --------------------------------------------------------
    # BEARISH LIQUIDITY SWEEP
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
    # VOLUME
    # --------------------------------------------------------

    vr = volume_ratio(
        df,
        sweep_i,
    )


    if vr < config["volume"]:

        return None


    # --------------------------------------------------------
    # CONFIRMATION CANDLE
    # --------------------------------------------------------

    confirm = df.iloc[
        i
    ]


    confirm_open = float(
        confirm["open"]
    )


    confirm_close = float(
        confirm["close"]
    )


    bearish_body = (
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

        bearish_body
        >= config["body"]

        and

        confirm_close
        <
        (
            sweep_high
            + sweep_close
        ) / 2
    )


    if not bearish_confirmation:

        return None


    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    entry = confirm_close


    # SL goes beyond actual sweep high.

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


    # --------------------------------------------------------
    # TP1
    # --------------------------------------------------------

    tp1 = (
        entry
        - (
            risk
            * TP1_R
        )
    )


    # --------------------------------------------------------
    # TP2
    # --------------------------------------------------------

    theoretical_tp2 = (
        entry
        - (
            risk
            * TP2_R
        )
    )


    # Opposite side of range.

    opposite_range = range_low


    # We require the range target to offer
    # at least 1.5R.

    range_reward_r = (
        entry
        - opposite_range
    ) / risk


    if range_reward_r < 1.5:

        return None


    # Use the nearer of 2R and opposite
    # range target.

    tp2 = max(
        theoretical_tp2,
        opposite_range,
    )


    actual_reward_r = (
        entry
        - tp2
    ) / risk


    if actual_reward_r < 1.5:

        return None


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

        "volume_ratio":
            vr,

        "range_width_pct":
            range_width,

        "planned_reward_r":
            actual_reward_r,
    }


# ============================================================
# SIMULATE TRADE
# ============================================================

def simulate_trade(
    df,
    entry_i,
    signal,
):

    entry = float(
        signal["entry"]
    )

    original_stop = float(
        signal["stop"]
    )

    tp1 = float(
        signal["tp1"]
    )

    tp2 = float(
        signal["tp2"]
    )


    risk = (
        original_stop
        - entry
    )


    tp1_hit = False

    breakeven = False

    bars = 0


    # Track realized and remaining R.

    realized_r = 0.0

    remaining_fraction = 1.0


    for j in range(
        entry_i + 1,
        len(df),
    ):

        bars += 1

        candle = df.iloc[j]


        high = float(
            candle["high"]
        )

        low = float(
            candle["low"]
        )


        # ====================================================
        # STOP
        # ====================================================

        current_stop = (
            entry
            if breakeven
            else original_stop
        )


        if high >= current_stop:

            # If TP1 already occurred, remaining
            # 50% closes at breakeven.

            if tp1_hit:

                # Remaining 50% = 0R.

                final_r = realized_r

            else:

                # Entire position loses 1R.

                final_r = -1.0


            return {

                "outcome":
                    "SL"
                    if not tp1_hit
                    else "TP1_SL",

                "r":
                    final_r,

                "close_time":
                    candle["close_time"],

                "bars":
                    bars,

                "tp1_hit":
                    tp1_hit,

                "breakeven":
                    breakeven,

                "partial_realized_r":
                    realized_r,
            }


        # ====================================================
        # TP1
        # ====================================================

        if (
            not tp1_hit
            and low <= tp1
        ):

            tp1_hit = True


            # 50% position closes at 1R.

            realized_r += (
                TP1_PARTIAL
                * TP1_R
            )


            remaining_fraction = (
                1
                - TP1_PARTIAL
            )


            if MOVE_SL_TO_BREAKEVEN:

                breakeven = True


        # ====================================================
        # TP2
        # ====================================================

        if low <= tp2:

            remaining_r = (
                remaining_fraction
                * TP2_R
            )


            final_r = (
                realized_r
                + remaining_r
            )


            return {

                "outcome":
                    "TP2",

                "r":
                    final_r,

                "close_time":
                    candle["close_time"],

                "bars":
                    bars,

                "tp1_hit":
                    True,

                "breakeven":
                    breakeven,

                "partial_realized_r":
                    realized_r,
            }


        # ====================================================
        # TIME EXIT
        # ====================================================

        if bars >= MAX_BARS_IN_TRADE:

            close = float(
                candle["close"]
            )


            if tp1_hit:

                remaining_r = (
                    remaining_fraction
                    *
                    (
                        entry - close
                    )
                    / risk
                )


                final_r = (
                    realized_r
                    + remaining_r
                )

            else:

                final_r = (
                    entry - close
                ) / risk


            return {

                "outcome":
                    "TIME",

                "r":
                    final_r,

                "close_time":
                    candle["close_time"],

                "bars":
                    bars,

                "tp1_hit":
                    tp1_hit,

                "breakeven":
                    breakeven,

                "partial_realized_r":
                    realized_r,
            }


    # ========================================================
    # END OF DATA
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

        "breakeven":
            breakeven,

        "partial_realized_r":
            realized_r,
    }


# ============================================================
# RUN BACKTEST
# ============================================================

def run_backtest(
    df,
    config,
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
            i,
            config,
        )


        if signal is None:

            i += 1

            continue


        result = simulate_trade(
            df,
            i,
            signal,
        )


        trade = {

            **signal,

            **result,

            "entry_time":
                df.iloc[i]["open_time"],
        }


        trades.append(
            trade
        )


        # No overlapping trades.

        exit_i = (
            i
            + result["bars"]
        )


        i = max(
            i + 1,
            exit_i + 1,
        )


    return pd.DataFrame(
        trades
    )


# ============================================================
# MAX CONSECUTIVE LOSSES
# ============================================================

def max_consecutive_losses(
    trades,
):

    maximum = 0

    current = 0


    for r in trades["r"]:

        if float(r) < 0:

            current += 1

            maximum = max(
                maximum,
                current,
            )

        else:

            current = 0


    return maximum


# ============================================================
# MAX DRAWDOWN
# ============================================================

def max_drawdown(
    trades,
):

    equity = (
        trades["r"]
        .astype(float)
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


    return float(
        drawdown.min()
    )


# ============================================================
# MONTHLY PERFORMANCE
# ============================================================

def monthly_report(
    trades,
):

    if trades.empty:

        return


    temp = trades.copy()


    temp["month"] = (
        pd.to_datetime(
            temp["entry_time"],
            utc=True,
        ).dt.strftime(
            "%Y-%m"
        )
    )


    grouped = (
        temp
        .groupby("month")
    )


    print(
        "\nMONTHLY PERFORMANCE"
    )


    print(
        "-" * 70
    )


    for month, group in grouped:

        count = len(group)

        total_r = float(
            group["r"]
            .sum()
        )


        wins = int(
            (
                group["r"] > 0
            ).sum()
        )


        losses = int(
            (
                group["r"] < 0
            ).sum()
        )


        print(
            f"{month}  "
            f"trades={count:3d}  "
            f"wins={wins:3d}  "
            f"losses={losses:3d}  "
            f"R={total_r:+7.2f}"
        )


# ============================================================
# SUMMARIZE
# ============================================================

def summarize(
    trades,
    config_name,
):

    print(
        "\n"
        + "=" * 72
    )

    print(
        f"BTCUSDT 5m V3 "
        f"BEARISH RANGE LIQUIDITY BACKTEST"
    )

    print(
        "=" * 72
    )


    print(
        f"Configuration: {config_name}"
    )


    if trades.empty:

        print(
            "\nNO TRADES FOUND."
        )

        return


    total = len(
        trades
    )


    tp2 = int(
        (
            trades["outcome"]
            == "TP2"
        ).sum()
    )


    sl = int(
        (
            trades["outcome"]
            == "SL"
        ).sum()
    )


    tp1_sl = int(
        (
            trades["outcome"]
            == "TP1_SL"
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
        trades["tp1_hit"]
        .sum()
    )


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


    positive = (
        r_values[
            r_values > 0
        ]
    )


    negative = (
        r_values[
            r_values < 0
        ]
    )


    gross_profit = float(
        positive.sum()
    )


    gross_loss = abs(
        float(
            negative.sum()
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


    winning_trades = int(
        (
            r_values > 0
        ).sum()
    )


    losing_trades = int(
        (
            r_values < 0
        ).sum()
    )


    win_rate = (
        winning_trades
        / total
        * 100
    )


    expectancy = (
        average_r
    )


    dd = max_drawdown(
        trades
    )


    consecutive_losses = (
        max_consecutive_losses(
            trades
        )
    )


    average_volume = float(
        trades[
            "volume_ratio"
        ].mean()
    )


    minimum_volume = float(
        trades[
            "volume_ratio"
        ].min()
    )


    maximum_volume = float(
        trades[
            "volume_ratio"
        ].max()
    )


    average_range = float(
        trades[
            "range_width_pct"
        ].mean()
        * 100
    )


    average_planned_reward = float(
        trades[
            "planned_reward_r"
        ].mean()
    )


    print(
        f"\nTrades:             {total}"
    )


    print(
        "\nOUTCOMES"
    )

    print(
        "-" * 72
    )


    print(
        f"TP2 wins:           {tp2}"
    )

    print(
        f"Full SL:            {sl}"
    )

    print(
        f"TP1 -> BE:          {tp1_sl}"
    )

    print(
        f"Time exits:         {time_exits}"
    )

    print(
        f"Open at end:        {open_end}"
    )

    print(
        f"TP1 hits:           {tp1_hits}"
    )


    print(
        "\nPERFORMANCE"
    )

    print(
        "-" * 72
    )


    print(
        f"Profitable trades:  {winning_trades}"
    )

    print(
        f"Losing trades:      {losing_trades}"
    )

    print(
        f"Win rate:           {win_rate:.1f}%"
    )

    print(
        f"Total R:            {total_r:+.2f}R"
    )

    print(
        f"Average R/trade:    {average_r:+.3f}R"
    )

    print(
        f"Expectancy:         {expectancy:+.3f}R"
    )

    print(
        f"Profit factor:      {profit_factor:.2f}"
    )

    print(
        f"Max drawdown:       {dd:.2f}R"
    )

    print(
        f"Max losing streak:  {consecutive_losses}"
    )


    print(
        "\nSIGNAL QUALITY"
    )

    print(
        "-" * 72
    )


    print(
        f"Avg sweep volume:   {average_volume:.2f}x"
    )

    print(
        f"Min sweep volume:   {minimum_volume:.2f}x"
    )

    print(
        f"Max sweep volume:   {maximum_volume:.2f}x"
    )

    print(
        f"Avg range width:    {average_range:.3f}%"
    )

    print(
        f"Avg planned reward: {average_planned_reward:.2f}R"
    )


    print(
        "\nR CONTRIBUTION"
    )

    print(
        "-" * 72
    )


    tp2_r = float(
        trades.loc[
            trades["outcome"] == "TP2",
            "r"
        ].sum()
    )


    sl_r = float(
        trades.loc[
            trades["outcome"].isin(
                ["SL"]
            ),
            "r"
        ].sum()
    )


    tp1_sl_r = float(
        trades.loc[
            trades["outcome"] == "TP1_SL",
            "r"
        ].sum()
    )


    time_r = float(
        trades.loc[
            trades["outcome"] == "TIME",
            "r"
        ].sum()
    )


    print(
        f"TP2 R:              {tp2_r:+.2f}R"
    )


    print(
        f"Full SL R:          {sl_r:+.2f}R"
    )


    print(
        f"TP1 -> BE R:        {tp1_sl_r:+.2f}R"
    )


    print(
        f"TIME R:             {time_r:+.2f}R"
    )


    print(
        f"TOTAL R:            {total_r:+.2f}R"
    )


    # --------------------------------------------------------
    # ACCOUNTING CHECK
    # --------------------------------------------------------

    calculated_r = (
        tp2_r
        + sl_r
        + tp1_sl_r
        + time_r
    )


    print(
        "\nACCOUNTING CHECK"
    )

    print(
        "-" * 72
    )


    print(
        f"Components:         {calculated_r:+.2f}R"
    )


    print(
        f"Reported total:     {total_r:+.2f}R"
    )


    difference = (
        calculated_r
        - total_r
    )


    if abs(difference) < 0.0001:

        print(
            "✓ R accounting is consistent."
        )

    else:

        print(
            f"⚠ R accounting difference: "
            f"{difference:+.5f}"
        )


    monthly_report(
        trades
    )


# ============================================================
# ROBUSTNESS TABLE
# ============================================================

def robustness_summary(
    df,
):

    print(
        "\n"
        + "=" * 72
    )

    print(
        "PARAMETER ROBUSTNESS TEST"
    )

    print(
        "=" * 72
    )


    results = []


    for config in TEST_CONFIGS:

        print(
            f"\nTesting {config['name']}..."
        )


        trades = run_backtest(
            df,
            config,
        )


        if trades.empty:

            results.append({

                "config":
                    config["name"],

                "trades":
                    0,

                "total_r":
                    0,

                "avg_r":
                    0,

                "profit_factor":
                    0,

                "max_dd":
                    0,

                "win_rate":
                    0,
            })


            continue


        r = trades["r"].astype(
            float
        )


        gross_profit = float(
            r[r > 0].sum()
        )


        gross_loss = abs(
            float(
                r[r < 0].sum()
            )
        )


        pf = (
            gross_profit
            / gross_loss
            if gross_loss > 0
            else float("inf")
        )


        results.append({

            "config":
                config["name"],

            "trades":
                len(trades),

            "total_r":
                float(r.sum()),

            "avg_r":
                float(r.mean()),

            "profit_factor":
                pf,

            "max_dd":
                max_drawdown(
                    trades
                ),

            "win_rate":
                float(
                    (
                        r > 0
                    ).mean()
                    * 100
                ),
        })


    result_df = pd.DataFrame(
        results
    )


    print(
        "\n"
        + result_df.to_string(
            index=False,
            float_format=lambda x:
                f"{x:.3f}"
        )
    )


    return result_df


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
        default="backtest_v3_trades.csv",
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


    print(
        f"Downloading "
        f"BTCUSDT 5m data "
        f"for {args.days} days..."
    )


    df = fetch_klines(
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
        f"{len(df):,} completed candles."
    )


    # ========================================================
    # BASE TEST
    # ========================================================

    base_config = TEST_CONFIGS[0]


    trades = run_backtest(
        df,
        base_config,
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
        base_config["name"],
    )


    # ========================================================
    # ROBUSTNESS
    # ========================================================

    robustness = robustness_summary(
        df
    )


    robustness.to_csv(
        "backtest_v3_robustness.csv",
        index=False,
    )


    print(
        "\nSaved robustness results to "
        "backtest_v3_robustness.csv"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
