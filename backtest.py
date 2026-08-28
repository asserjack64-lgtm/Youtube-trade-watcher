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

API_URL = "https://data-api.binance.vision/api/v3/klines"


# ============================================================
# STRATEGY
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
# BASE CONFIGURATION
#
# IMPORTANT:
# These parameters are FIXED for the validation test.
# ============================================================

BASE_CONFIG = {
    "name": "BASE",
    "volume": 1.50,
    "body": 0.0008,
    "range": 0.015,
}


# ============================================================
# TRAINING ROBUSTNESS TESTS
#
# These are ONLY tested on the training period.
# We do NOT use validation data to select parameters.
# ============================================================

TRAINING_TESTS = [

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
        .drop_duplicates("open_time")
        .sort_values("open_time")
        .reset_index(drop=True)
    )


    # Never use a forming candle.

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

    # i = confirmation candle
    sweep_i = i - 1


    if sweep_i < (
        RANGE_CANDLES
        + VOLUME_LOOKBACK
    ):

        return None


    # --------------------------------------------------------
    # RANGE BEFORE SWEEP
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
    # SWEEP
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
    # CONFIRMATION
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


    body_pct = (
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

        body_pct
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


    # Stop above actual sweep extreme.

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
        - risk * TP1_R
    )


    # --------------------------------------------------------
    # TP2
    # --------------------------------------------------------

    theoretical_tp2 = (
        entry
        - risk * TP2_R
    )


    opposite_range = range_low


    range_reward_r = (
        entry
        - opposite_range
    ) / risk


    # Require at least 1.5R potential.

    if range_reward_r < 1.5:

        return None


    # Do not pretend we can get more than
    # the available range target.

    tp2 = max(
        theoretical_tp2,
        opposite_range,
    )


    planned_reward_r = (
        entry
        - tp2
    ) / risk


    if planned_reward_r < 1.5:

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
            planned_reward_r,
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


        # ----------------------------------------------------
        # CURRENT STOP
        # ----------------------------------------------------

        current_stop = (
            entry
            if breakeven
            else original_stop
        )


        # ----------------------------------------------------
        # STOP
        # ----------------------------------------------------

        if high >= current_stop:

            if tp1_hit:

                final_r = realized_r

                outcome = "TP1_BE"

            else:

                final_r = -1.0

                outcome = "SL"


            return {

                "outcome":
                    outcome,

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


        # ----------------------------------------------------
        # TP1
        # ----------------------------------------------------

        if (
            not tp1_hit
            and low <= tp1
        ):

            tp1_hit = True


            # 50% position gets +1R.

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


        # ----------------------------------------------------
        # TP2
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # TIME EXIT
        # ----------------------------------------------------

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


    # --------------------------------------------------------
    # DATASET ENDED
    # --------------------------------------------------------

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
    start_time=None,
    end_time=None,
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


        entry_time = df.iloc[
            i
        ]["open_time"]


        # ----------------------------------------------------
        # OUT-OF-SAMPLE FILTER
        #
        # The signal calculation still has access to the
        # historical candles before validation.
        #
        # But trades are only accepted inside the requested
        # period.
        # ----------------------------------------------------

        if (
            start_time is not None
            and entry_time < start_time
        ):

            i += 1

            continue


        if (
            end_time is not None
            and entry_time >= end_time
        ):

            i += 1

            continue


        result = simulate_trade(
            df,
            i,
            signal,
        )


        # ----------------------------------------------------
        # Do not allow a trade to continue beyond the
        # validation period.
        #
        # If the exit occurs after end_time, discard the
        # trade from the validation statistics because its
        # complete outcome isn't known inside the period.
        # ----------------------------------------------------

        if (
            end_time is not None
            and result["close_time"] >= end_time
        ):

            break


        trade = {

            **signal,

            **result,

            "entry_time":
                entry_time,
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


    for value in trades["r"]:

        if float(value) < 0:

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

    if trades.empty:

        return 0.0


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
# PERFORMANCE METRICS
# ============================================================

def calculate_metrics(
    trades,
):

    if trades.empty:

        return {

            "trades": 0,

            "total_r": 0.0,

            "avg_r": 0.0,

            "profit_factor": 0.0,

            "win_rate": 0.0,

            "max_dd": 0.0,

            "max_losses": 0,

            "tp2": 0,

            "sl": 0,

            "tp1_be": 0,

            "time": 0,
        }


    r = trades["r"].astype(
        float
    )


    positive = r[r > 0]

    negative = r[r < 0]


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


    return {

        "trades":
            len(trades),

        "total_r":
            float(r.sum()),

        "avg_r":
            float(r.mean()),

        "profit_factor":
            profit_factor,

        "win_rate":
            float(
                (
                    r > 0
                ).mean()
                * 100
            ),

        "max_dd":
            max_drawdown(
                trades
            ),

        "max_losses":
            max_consecutive_losses(
                trades
            ),

        "tp2":
            int(
                (
                    trades["outcome"]
                    == "TP2"
                ).sum()
            ),

        "sl":
            int(
                (
                    trades["outcome"]
                    == "SL"
                ).sum()
            ),

        "tp1_be":
            int(
                (
                    trades["outcome"]
                    == "TP1_BE"
                ).sum()
            ),

        "time":
            int(
                (
                    trades["outcome"]
                    == "TIME"
                ).sum()
            ),
    }


# ============================================================
# PRINT PERFORMANCE
# ============================================================

def print_performance(
    trades,
    title,
):

    print(
        "\n"
        + "=" * 72
    )

    print(title)

    print(
        "=" * 72
    )


    if trades.empty:

        print(
            "NO COMPLETED TRADES."
        )

        return


    metrics = calculate_metrics(
        trades
    )


    print(
        f"Trades:             "
        f"{metrics['trades']}"
    )


    print(
        "\nOUTCOMES"
    )

    print(
        "-" * 72
    )


    print(
        f"TP2:                "
        f"{metrics['tp2']}"
    )


    print(
        f"Full SL:            "
        f"{metrics['sl']}"
    )


    print(
        f"TP1 -> BE:          "
        f"{metrics['tp1_be']}"
    )


    print(
        f"Time exits:         "
        f"{metrics['time']}"
    )


    print(
        "\nPERFORMANCE"
    )

    print(
        "-" * 72
    )


    print(
        f"Profitable trades:  "
        f"{int((trades['r'] > 0).sum())}"
    )


    print(
        f"Losing trades:      "
        f"{int((trades['r'] < 0).sum())}"
    )


    print(
        f"Win rate:           "
        f"{metrics['win_rate']:.1f}%"
    )


    print(
        f"Total R:            "
        f"{metrics['total_r']:+.2f}R"
    )


    print(
        f"Average R/trade:    "
        f"{metrics['avg_r']:+.3f}R"
    )


    print(
        f"Expectancy:         "
        f"{metrics['avg_r']:+.3f}R"
    )


    print(
        f"Profit factor:      "
        f"{metrics['profit_factor']:.2f}"
    )


    print(
        f"Max drawdown:       "
        f"{metrics['max_dd']:.2f}R"
    )


    print(
        f"Max losing streak:  "
        f"{metrics['max_losses']}"
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


    print(
        "\nMONTHLY PERFORMANCE"
    )

    print(
        "-" * 72
    )


    for month, group in (
        temp.groupby("month")
    ):

        r = group["r"].astype(
            float
        )


        print(
            f"{month}  "
            f"trades={len(group):3d}  "
            f"R={r.sum():+7.2f}  "
            f"avg={r.mean():+.3f}"
        )


# ============================================================
# ROBUSTNESS TEST
#
# TRAINING DATA ONLY
# ============================================================

def robustness_test(
    training_df,
):

    print(
        "\n"
        + "=" * 72
    )

    print(
        "TRAINING-PERIOD ROBUSTNESS TEST"
    )

    print(
        "=" * 72
    )


    results = []


    for config in TRAINING_TESTS:

        trades = run_backtest(
            training_df,
            config,
        )


        metrics = calculate_metrics(
            trades
        )


        results.append({

            "config":
                config["name"],

            "trades":
                metrics["trades"],

            "total_r":
                metrics["total_r"],

            "avg_r":
                metrics["avg_r"],

            "profit_factor":
                metrics["profit_factor"],

            "max_dd":
                metrics["max_dd"],

            "win_rate":
                metrics["win_rate"],

        })


    result_df = pd.DataFrame(
        results
    )


    print(
        result_df.to_string(
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
        "--train-days",
        type=int,
        default=120,
    )


    parser.add_argument(
        "--output",
        default="backtest_v4_validation_trades.csv",
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


    split_time = (
        end
        - timedelta(
            days=args.days
            - args.train_days
        )
    )


    print(
        "\n"
        + "=" * 72
    )

    print(
        "BTCUSDT V4 OUT-OF-SAMPLE BACKTEST"
    )

    print(
        "=" * 72
    )


    print(
        f"Total period:      "
        f"{start.date()} → {end.date()}"
    )


    print(
        f"Training period:   "
        f"{start.date()} → {split_time.date()}"
    )


    print(
        f"Validation period: "
        f"{split_time.date()} → {end.date()}"
    )


    print(
        "\nDownloading BTCUSDT 5m data..."
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
    # TRAINING DATA
    # ========================================================

    training_df = df[
        df["open_time"] < split_time
    ].copy()


    # ========================================================
    # VALIDATION DATA
    #
    # We intentionally use the COMPLETE df here so the
    # first validation signals have enough historical candles.
    #
    # Trades themselves are restricted to validation dates.
    # ========================================================

    print(
        "\nRunning BASE strategy on training data..."
    )


    training_trades = run_backtest(
        df,
        BASE_CONFIG,
        start_time=start,
        end_time=split_time,
    )


    print_performance(
        training_trades,
        "TRAINING RESULTS — FIRST 120 DAYS",
    )


    monthly_report(
        training_trades
    )


    # ========================================================
    # VALIDATION
    # ========================================================

    print(
        "\nRunning untouched BASE strategy on validation data..."
    )


    validation_trades = run_backtest(
        df,
        BASE_CONFIG,
        start_time=split_time,
        end_time=end,
    )


    print_performance(
        validation_trades,
        "VALIDATION RESULTS — FINAL 60 DAYS",
    )


    monthly_report(
        validation_trades
    )


    # ========================================================
    # SAVE VALIDATION TRADES
    # ========================================================

    if not validation_trades.empty:

        validation_trades.to_csv(
            args.output,
            index=False,
        )


        print(
            f"\nSaved validation trades to "
            f"{args.output}"
        )


    # ========================================================
    # SAVE TRAINING TRADES
    # ========================================================

    if not training_trades.empty:

        training_trades.to_csv(
            "backtest_v4_training_trades.csv",
            index=False,
        )


        print(
            "Saved training trades to "
            "backtest_v4_training_trades.csv"
        )


    # ========================================================
    # TRAINING ROBUSTNESS
    # ========================================================

    robustness = robustness_test(
        training_df
    )


    robustness.to_csv(
        "backtest_v4_training_robustness.csv",
        index=False,
    )


    print(
        "\nSaved robustness results to "
        "backtest_v4_training_robustness.csv"
    )


    # ========================================================
    # FINAL COMPARISON
    # ========================================================

    train_metrics = calculate_metrics(
        training_trades
    )


    validation_metrics = calculate_metrics(
        validation_trades
    )


    print(
        "\n"
        + "=" * 72
    )

    print(
        "TRAINING vs VALIDATION"
    )

    print(
        "=" * 72
    )


    print(
        f"{'Metric':<24}"
        f"{'Training':>16}"
        f"{'Validation':>16}"
    )


    print(
        "-" * 56
    )


    print(
        f"{'Trades':<24}"
        f"{train_metrics['trades']:>16}"
        f"{validation_metrics['trades']:>16}"
    )


    print(
        f"{'Total R':<24}"
        f"{train_metrics['total_r']:>15.2f}R"
        f"{validation_metrics['total_r']:>15.2f}R"
    )


    print(
        f"{'Avg R/trade':<24}"
        f"{train_metrics['avg_r']:>15.3f}"
        f"{validation_metrics['avg_r']:>15.3f}"
    )


    print(
        f"{'Profit factor':<24}"
        f"{train_metrics['profit_factor']:>16.2f}"
        f"{validation_metrics['profit_factor']:>16.2f}"
    )


    print(
        f"{'Win rate':<24}"
        f"{train_metrics['win_rate']:>15.1f}%"
        f"{validation_metrics['win_rate']:>15.1f}%"
    )


    print(
        f"{'Max drawdown':<24}"
        f"{train_metrics['max_dd']:>15.2f}R"
        f"{validation_metrics['max_dd']:>15.2f}R"
    )


    print(
        f"{'Max losing streak':<24}"
        f"{train_metrics['max_losses']:>16}"
        f"{validation_metrics['max_losses']:>16}"
    )


    print(
        "=" * 72
    )


    # ========================================================
    # INTERPRETATION
    # ========================================================

    print(
        "\nVALIDATION VERDICT"
    )

    print(
        "-" * 72
    )


    if validation_trades.empty:

        print(
            "⚠ No validation trades."
        )


    elif (
        validation_metrics["total_r"] > 0
        and validation_metrics["profit_factor"] > 1
        and validation_metrics["avg_r"] > 0
    ):

        print(
            "🟢 POSITIVE OUT-OF-SAMPLE RESULT"
        )

        print(
            "The BASE strategy remained profitable "
            "on unseen data."
        )


    else:

        print(
            "🔴 NEGATIVE OUT-OF-SAMPLE RESULT"
        )

        print(
            "Do NOT move the strategy to the live bot yet."
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
