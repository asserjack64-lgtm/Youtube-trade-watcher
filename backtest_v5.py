
import argparse
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

SYMBOL = "BTCUSDT"
API_URL = "https://data-api.binance.vision/api/v3/klines"

# ============================================================
# V5 STRATEGY
# ============================================================

RANGE_CANDLES = 24
MAX_RANGE_WIDTH_PCT = 0.012
MIN_RANGE_WIDTH_PCT = 0.003

SWEEP_PCT = 0.0003
SL_BUFFER_PCT = 0.0005

VOLUME_LOOKBACK = 20
MIN_VOLUME_RATIO = 1.50
CONFIRM_BODY_MIN_PCT = 0.0008

ATR_PERIOD = 14
ATR_MEDIAN_LOOKBACK = 100
MIN_ATR_FACTOR = 0.70
MAX_ATR_FACTOR = 1.80

MIN_SWEEP_WICK_ATR = 0.25
MAX_SWEEP_CLOSE_LOCATION = 0.55

EMA_PERIOD = 50
EMA_SLOPE_LOOKBACK = 3

MIN_PLANNED_R = 1.80
MAX_BARS_IN_TRADE = 72

TP1_R = 1.0
TP2_R = 2.0
TP1_PARTIAL = 0.50
MOVE_SL_TO_BREAKEVEN = True

# Keep this at 0 for the first research run.
# After the signal edge is validated, test realistic fees/slippage.
ROUND_TRIP_COST_R = 0.00

BASE_CONFIG = {
    "name": "V4_BASE",
    "volume": 1.50,
    "body": 0.0008,
    "range_max": 0.015,
    "range_min": 0.0,
    "wick_atr": 0.0,
    "atr_min": 0.0,
    "atr_max": 999.0,
    "require_htf": False,
    "min_reward": 1.50,
    "close_location": 1.0,
}

V5_CONFIG = {
    "name": "V5_SELECTIVE",
    "volume": 1.50,
    "body": 0.0008,
    "range_max": 0.012,
    "range_min": 0.003,
    "wick_atr": 0.25,
    "atr_min": 0.70,
    "atr_max": 1.80,
    "require_htf": True,
    "min_reward": 1.80,
    "close_location": 0.55,
}

# These are tested ONLY on the training period.
TRAINING_TESTS = [
    BASE_CONFIG,
    {**V5_CONFIG, "name": "V5_NO_HTF", "require_htf": False},
    {**V5_CONFIG, "name": "V5_TIGHT_RANGE", "range_max": 0.010},
    {**V5_CONFIG, "name": "V5_HIGH_VOLUME", "volume": 1.80},
    {**V5_CONFIG, "name": "V5_STRONG_WICK", "wick_atr": 0.35},
    {**V5_CONFIG, "name": "V5_STRICT_BODY", "body": 0.0010},
    {**V5_CONFIG, "name": "V5_MIN_REWARD_2R", "min_reward": 2.00},
]


# ============================================================
# DATA
# ============================================================

def fetch_klines(start_ms, end_ms, interval):
    rows = []
    cursor = start_ms
    step_ms = 5 * 60 * 1000 if interval == "5m" else 60 * 60 * 1000

    while cursor < end_ms:
        params = {
            "symbol": SYMBOL,
            "interval": interval,
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
        next_cursor = last_open + step_ms

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
            f"Binance returned no {interval} candles."
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

    now = pd.Timestamp.now(
        tz="UTC"
    )

    return df[
        df["close_time"] <= now
    ].reset_index(drop=True)


def add_atr(df):
    previous_close = df["close"].shift(1)

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    result = df.copy()

    result["atr"] = (
        true_range
        .rolling(ATR_PERIOD)
        .mean()
    )

    result["atr_median"] = (
        result["atr"]
        .rolling(ATR_MEDIAN_LOOKBACK)
        .median()
    )

    return result


def prepare_htf(df):
    result = df.copy()

    result["ema50"] = (
        result["close"]
        .ewm(
            span=EMA_PERIOD,
            adjust=False,
        )
        .mean()
    )

    result["ema_slope"] = (
        result["ema50"]
        - result["ema50"].shift(
            EMA_SLOPE_LOOKBACK
        )
    )

    result["bearish"] = (
        (result["close"] < result["ema50"])
        &
        (result["ema_slope"] <= 0)
    )

    return result[
        [
            "close_time",
            "close",
            "ema50",
            "ema_slope",
            "bearish",
        ]
    ].rename(
        columns={
            "close": "htf_close"
        }
    )


def attach_htf(df, htf):
    left = (
        df
        .sort_values("close_time")
        .copy()
    )

    right = (
        prepare_htf(htf)
        .sort_values("close_time")
    )

    # Only an already completed 1H candle can be used.
    return pd.merge_asof(
        left,
        right,
        on="close_time",
        direction="backward",
        allow_exact_matches=True,
    )


# ============================================================
# INDICATORS
# ============================================================

def volume_ratio(df, index):
    start = max(
        0,
        index - VOLUME_LOOKBACK,
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
# SIGNAL
# ============================================================

def find_signal(
    df,
    index,
    config,
):
    # index = confirmation candle
    sweep_index = index - 1

    minimum = (
        RANGE_CANDLES
        + VOLUME_LOOKBACK
        + ATR_MEDIAN_LOOKBACK
    )

    if sweep_index < minimum:
        return None

    range_df = df.iloc[
        sweep_index - RANGE_CANDLES:
        sweep_index
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

    if (
        range_width
        > config["range_max"]
    ):
        return None

    if (
        range_width
        < config["range_min"]
    ):
        return None

    sweep = df.iloc[
        sweep_index
    ]

    confirm = df.iloc[
        index
    ]

    sweep_high = float(
        sweep["high"]
    )

    sweep_low = float(
        sweep["low"]
    )

    sweep_open = float(
        sweep["open"]
    )

    sweep_close = float(
        sweep["close"]
    )

    sweep_range = max(
        sweep_high
        - sweep_low,
        1e-12,
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
        sweep_index,
    )

    if (
        vr
        < config["volume"]
    ):
        return None

    # --------------------------------------------------------
    # REJECTION WICK
    # --------------------------------------------------------

    atr = float(
        sweep.get(
            "atr",
            0.0,
        )
    )

    if (
        config["wick_atr"]
        > 0
    ):
        upper_wick = (
            sweep_high
            - max(
                sweep_open,
                sweep_close,
            )
        )

        if (
            atr <= 0
            or
            upper_wick / atr
            < config["wick_atr"]
        ):
            return None

    # --------------------------------------------------------
    # SWEEP CLOSE LOCATION
    # --------------------------------------------------------

    if (
        config["close_location"]
        < 1.0
    ):
        close_location = (
            sweep_close
            - sweep_low
        ) / sweep_range

        if (
            close_location
            >
            config["close_location"]
        ):
            return None

    # --------------------------------------------------------
    # VOLATILITY REGIME
    # --------------------------------------------------------

    atr_factor = 0.0

    if (
        config["atr_min"] > 0
        or
        config["atr_max"] < 999
    ):
        atr_median = float(
            sweep.get(
                "atr_median",
                0.0,
            )
        )

        if (
            atr <= 0
            or
            atr_median <= 0
        ):
            return None

        atr_factor = (
            atr
            /
            atr_median
        )

        if not (
            config["atr_min"]
            <= atr_factor
            <= config["atr_max"]
        ):
            return None

    # --------------------------------------------------------
    # 1H BEARISH REGIME
    # --------------------------------------------------------

    if config["require_htf"]:
        bearish_htf = confirm.get(
            "bearish",
            False,
        )

        if (
            pd.isna(bearish_htf)
            or
            not bool(bearish_htf)
        ):
            return None

    # --------------------------------------------------------
    # CONFIRMATION CANDLE
    # --------------------------------------------------------

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
        < range_high
        and
        confirm_close
        < confirm_open
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
    # ENTRY / STOP
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
        - risk * TP1_R
    )

    theoretical_tp2 = (
        entry
        - risk * TP2_R
    )

    opposite_range = range_low

    range_reward_r = (
        entry
        - opposite_range
    ) / risk

    if (
        range_reward_r
        < config["min_reward"]
    ):
        return None

    tp2 = max(
        theoretical_tp2,
        opposite_range,
    )

    planned_reward_r = (
        entry
        - tp2
    ) / risk

    if (
        planned_reward_r
        < config["min_reward"]
    ):
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
        "range_width_pct": range_width,
        "planned_reward_r": planned_reward_r,
        "atr_factor": atr_factor,
    }


# ============================================================
# TRADE SIMULATION
# ============================================================

def simulate_trade(
    df,
    entry_index,
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

    for index in range(
        entry_index + 1,
        len(df),
    ):
        bars += 1

        candle = df.iloc[
            index
        ]

        high = float(
            candle["high"]
        )

        low = float(
            candle["low"]
        )

        current_stop = (
            entry
            if breakeven
            else original_stop
        )

        # Conservative ordering:
        # stop is checked before target.
        if high >= current_stop:

            if tp1_hit:
                final_r = realized_r
                outcome = "TP1_BE"
            else:
                final_r = -1.0
                outcome = "SL"

            return {
                "outcome": outcome,
                "r": (
                    final_r
                    - ROUND_TRIP_COST_R
                ),
                "gross_r": final_r,
                "close_time":
                    candle["close_time"],
                "bars": bars,
                "tp1_hit":
                    tp1_hit,
                "breakeven":
                    breakeven,
            }

        # TP1 = 50% at +1R.
        if (
            not tp1_hit
            and
            low <= tp1
        ):
            tp1_hit = True

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

        # TP2.
        if low <= tp2:

            final_r = (
                realized_r
                +
                remaining_fraction
                * TP2_R
            )

            return {
                "outcome": "TP2",
                "r": (
                    final_r
                    - ROUND_TRIP_COST_R
                ),
                "gross_r": final_r,
                "close_time":
                    candle["close_time"],
                "bars": bars,
                "tp1_hit": True,
                "breakeven":
                    breakeven,
            }

        # Time exit.
        if (
            bars
            >= MAX_BARS_IN_TRADE
        ):
            close = float(
                candle["close"]
            )

            if tp1_hit:

                remaining_r = (
                    remaining_fraction
                    *
                    (
                        entry
                        - close
                    )
                    /
                    risk
                )

                final_r = (
                    realized_r
                    +
                    remaining_r
                )

            else:

                final_r = (
                    entry
                    - close
                ) / risk

            return {
                "outcome": "TIME",
                "r": (
                    final_r
                    - ROUND_TRIP_COST_R
                ),
                "gross_r": final_r,
                "close_time":
                    candle["close_time"],
                "bars": bars,
                "tp1_hit":
                    tp1_hit,
                "breakeven":
                    breakeven,
            }

    return {
        "outcome": "OPEN_AT_END",
        "r": 0.0,
        "gross_r": 0.0,
        "close_time":
            df.iloc[-1]["close_time"],
        "bars": bars,
        "tp1_hit":
            tp1_hit,
        "breakeven":
            breakeven,
    }


# ============================================================
# BACKTEST ENGINE
# ============================================================

def run_backtest(
    df,
    config,
    start_time=None,
    end_time=None,
):
    trades = []

    start_index = (
        RANGE_CANDLES
        + VOLUME_LOOKBACK
        + ATR_MEDIAN_LOOKBACK
        + 1
    )

    index = start_index

    while index < len(df) - 2:

        signal = find_signal(
            df,
            index,
            config,
        )

        if signal is None:
            index += 1
            continue

        entry_time = df.iloc[
            index
        ]["open_time"]

        if (
            start_time is not None
            and
            entry_time < start_time
        ):
            index += 1
            continue

        if (
            end_time is not None
            and
            entry_time >= end_time
        ):
            break

        result = simulate_trade(
            df,
            index,
            signal,
        )

        if (
            end_time is not None
            and
            result["close_time"]
            >= end_time
        ):
            break

        trades.append({
            **signal,
            **result,
            "entry_time":
                entry_time,
        })

        # No overlapping trades.
        exit_index = (
            index
            + result["bars"]
        )

        index = max(
            index + 1,
            exit_index + 1,
        )

    return pd.DataFrame(
        trades
    )


# ============================================================
# METRICS
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
        }

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

    profit_factor = (
        gross_profit
        /
        gross_loss
        if gross_loss > 0
        else float("inf")
    )

    equity = r.cumsum()
    peak = equity.cummax()
    drawdown = (
        equity
        - peak
    )

    return {
        "trades": len(trades),
        "total_r": float(
            r.sum()
        ),
        "avg_r": float(
            r.mean()
        ),
        "profit_factor":
            profit_factor,
        "win_rate": float(
            (r > 0).mean()
            * 100
        ),
        "max_dd": float(
            drawdown.min()
        ),
        "max_losses":
            max_consecutive_losses(
                trades
            ),
    }


def print_metrics(
    name,
    trades,
):
    metrics = calculate_metrics(
        trades
    )

    print(
        f"{name:<22}"
        f" trades={metrics['trades']:3d}"
        f" R={metrics['total_r']:+7.2f}"
        f" avg={metrics['avg_r']:+.3f}"
        f" PF={metrics['profit_factor']:.2f}"
        f" win={metrics['win_rate']:.1f}%"
        f" DD={metrics['max_dd']:.2f}R"
        f" loss_streak={metrics['max_losses']}"
    )

    return metrics


# ============================================================
# TRAINING-ONLY CONFIG SELECTION
# ============================================================

def select_training_config(
    results,
):
    candidates = []

    for config, metrics in results:

        if (
            metrics["trades"] >= 20
            and
            metrics["total_r"] > 0
            and
            metrics["profit_factor"]
            > 1.25
        ):

            score = (
                metrics["avg_r"]
                * 100
                +
                min(
                    metrics["profit_factor"],
                    3.0,
                )
                * 5
                -
                abs(
                    metrics["max_dd"]
                )
                * 0.25
            )

            candidates.append(
                (
                    score,
                    config,
                )
            )

    if not candidates:
        return BASE_CONFIG

    return max(
        candidates,
        key=lambda x: x[0],
    )[1]


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--days",
        type=int,
        default=365,
    )

    parser.add_argument(
        "--train-days",
        type=int,
        default=240,
    )

    parser.add_argument(
        "--validation-days",
        type=int,
        default=60,
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

    # 365-day run:
    # 240 days training
    # 60 days validation
    # remaining 65 days untouched final test.
    split_time = (
        start
        + timedelta(
            days=args.train_days
        )
    )

    final_test_start = (
        split_time
        + timedelta(
            days=args.validation_days
        )
    )

    print(
        "\n"
        + "=" * 78
    )

    print(
        "BTCUSDT 5m V5 SELECTIVE "
        "RANGE-LIQUIDITY BACKTEST"
    )

    print(
        "=" * 78
    )

    print(
        f"Total:       "
        f"{start.date()} -> "
        f"{end.date()}"
    )

    print(
        f"Training:    "
        f"{start.date()} -> "
        f"{split_time.date()}"
    )

    print(
        f"Validation:  "
        f"{split_time.date()} -> "
        f"{final_test_start.date()}"
    )

    print(
        f"Final test:  "
        f"{final_test_start.date()} -> "
        f"{end.date()}"
    )

    print(
        "\nDownloading BTCUSDT "
        "5m + 1h data..."
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
        "5m",
    )

    htf = fetch_klines(
        int(
            start.timestamp()
            * 1000
        ),
        int(
            end.timestamp()
            * 1000
        ),
        "1h",
    )

    df = add_atr(
        df
    )

    df = attach_htf(
        df,
        htf,
    )

    print(
        f"5m candles: "
        f"{len(df):,}"
    )

    print(
        f"1h candles: "
        f"{len(htf):,}"
    )

    # ========================================================
    # TRAINING ABLATIONS
    # ========================================================

    print(
        "\nTRAINING ABLATION TESTS"
    )

    print(
        "-" * 78
    )

    results = []

    for config in (
        TRAINING_TESTS
    ):

        trades = run_backtest(
            df,
            config,
            start_time=start,
            end_time=split_time,
        )

        metrics = print_metrics(
            config["name"],
            trades,
        )

        results.append(
            (
                config,
                metrics,
            )
        )

    selected = (
        select_training_config(
            results
        )
    )

    print(
        "\nSELECTED FROM "
        "TRAINING ONLY:"
    )

    print(
        selected["name"]
    )

    # ========================================================
    # FROZEN VALIDATION
    # ========================================================

    print(
        "\nFROZEN VALIDATION"
    )

    print(
        "-" * 78
    )

    validation = run_backtest(
        df,
        selected,
        start_time=split_time,
        end_time=final_test_start,
    )

    validation_metrics = (
        print_metrics(
            selected["name"],
            validation,
        )
    )

    # ========================================================
    # FROZEN FINAL TEST
    # ========================================================

    print(
        "\nFROZEN FINAL TEST"
    )

    print(
        "-" * 78
    )

    final_test = run_backtest(
        df,
        selected,
        start_time=final_test_start,
        end_time=end,
    )

    final_metrics = (
        print_metrics(
            selected["name"],
            final_test,
        )
    )

    # ========================================================
    # BASELINE VS V5
    # ========================================================

    print(
        "\nBASELINE VS SELECTED "
        "ON FINAL TEST"
    )

    print(
        "-" * 78
    )

    baseline_final = run_backtest(
        df,
        BASE_CONFIG,
        start_time=final_test_start,
        end_time=end,
    )

    print_metrics(
        "V4_BASE",
        baseline_final,
    )

    print_metrics(
        selected["name"],
        final_test,
    )

    # ========================================================
    # SAVE RESULTS
    # ========================================================

    validation.to_csv(
        "backtest_v5_validation_trades.csv",
        index=False,
    )

    final_test.to_csv(
        "backtest_v5_final_test_trades.csv",
        index=False,
    )

    pd.DataFrame([
        {
            "set": "validation",
            **validation_metrics,
        },
        {
            "set": "final_test",
            **final_metrics,
        },
    ]).to_csv(
        "backtest_v5_summary.csv",
        index=False,
    )

    print(
        "\nSaved:"
    )

    print(
        "  backtest_v5_validation_trades.csv"
    )

    print(
        "  backtest_v5_final_test_trades.csv"
    )

    print(
        "  backtest_v5_summary.csv"
    )

    # ========================================================
    # FINAL VERDICT
    # ========================================================

    if final_metrics["trades"] < 20:

        print(
            "\n⚠ FINAL TEST HAS "
            "FEWER THAN 20 TRADES."
        )

        print(
            "Treat the result as "
            "preliminary."
        )

    elif (
        final_metrics["total_r"] > 0
        and
        final_metrics["profit_factor"]
        > 1.25
        and
        final_metrics["avg_r"] > 0
    ):

        print(
            "\n🟢 FINAL TEST POSITIVE"
        )

        print(
            "V5 remained profitable "
            "on untouched data."
        )

    else:

        print(
            "\n🔴 FINAL TEST FAILED"
        )

        print(
            "Do not deploy V5 yet."
        )


if __name__ == "__main__":
    main()
