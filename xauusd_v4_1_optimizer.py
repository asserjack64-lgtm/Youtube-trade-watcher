"""
XAUUSD V4.1 PARAMETER SWEEP + WALK-FORWARD VALIDATION

Place this beside xauusd_backtest.py.
It reuses the existing V4 engine and downloads data only once.
"""

import pandas as pd
import xauusd_backtest as v4


DAYS = 180
TRAIN_DAYS = 120

VOLUME_VALUES = [1.20, 1.25, 1.30, 1.35, 1.40, 1.45, 1.50, 1.55, 1.60]
RANGE_VALUES = [0.008, 0.009, 0.010, 0.011, 0.012, 0.013, 0.014, 0.015]
BODY_VALUES = [0.0005, 0.0006, 0.0007, 0.0008, 0.0009, 0.0010]
SWEEP_VALUES = [0.0002, 0.0003, 0.0004]


def metrics(trades):
    if trades.empty:
        return {
            "trades": 0, "total_r": 0.0, "avg_r": 0.0,
            "profit_factor": 0.0, "max_dd": 0.0, "win_rate": 0.0
        }

    r = trades["r"].astype(float)
    gross_profit = float(r[r > 0].sum())
    gross_loss = abs(float(r[r < 0].sum()))
    pf = gross_profit / gross_loss if gross_loss > 0 else 999.0

    equity = r.cumsum()
    max_dd = float((equity - equity.cummax()).min())

    return {
        "trades": int(len(trades)),
        "total_r": float(r.sum()),
        "avg_r": float(r.mean()),
        "profit_factor": float(pf),
        "max_dd": max_dd,
        "win_rate": float((r > 0).mean() * 100.0)
    }


def test_config(df, volume, range_pct, body_pct, sweep_pct):
    trades = v4.run_backtest(
        df,
        min_volume_ratio=volume,
        max_range_width_pct=range_pct,
        sweep_pct=sweep_pct,
        confirm_body_min_pct=body_pct
    )

    result = {
        "volume": volume,
        "range_pct": range_pct,
        "body_pct": body_pct,
        "sweep_pct": sweep_pct
    }
    result.update(metrics(trades))
    return result


def main():
    print("=" * 72)
    print("XAUUSD V4.1 PARAMETER SWEEP + WALK-FORWARD")
    print("=" * 72)

    end_date = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=1)
    start_date = end_date - pd.Timedelta(days=DAYS - 1)

    start = start_date
    end = end_date + pd.Timedelta(hours=23, minutes=59, seconds=59)

    print(f"Period: {start.date()} -> {end.date()}")
    print("Downloading data once...")
    df = v4.download_data(start, end)

    if df.empty:
        raise RuntimeError("No XAUUSD data downloaded.")

    print(f"Downloaded {len(df):,} candles.")

    split_time = start + pd.Timedelta(days=TRAIN_DAYS)
    train_df = df[df["time"] < split_time].copy()

    split_index = int(df["time"].searchsorted(split_time))
    warmup_start = max(0, split_index - v4.RANGE_CANDLES - 2)
    test_df = df.iloc[warmup_start:].copy()

    if len(train_df) < 1000 or len(test_df) < 500:
        raise RuntimeError("Not enough candles for train/test split.")

    print(f"Training candles: {len(train_df):,}")
    print(f"Test candles:     {len(test_df):,}")

    total_configs = (
        len(VOLUME_VALUES)
        * len(RANGE_VALUES)
        * len(BODY_VALUES)
        * len(SWEEP_VALUES)
    )

    print(f"Testing {total_configs} configurations...")

    rows = []
    completed = 0

    for volume in VOLUME_VALUES:
        for range_pct in RANGE_VALUES:
            for body_pct in BODY_VALUES:
                for sweep_pct in SWEEP_VALUES:
                    completed += 1

                    result = test_config(
                        train_df,
                        volume,
                        range_pct,
                        body_pct,
                        sweep_pct
                    )

                    rows.append(result)

                    if completed % 25 == 0 or completed == total_configs:
                        print(f"Progress: {completed}/{total_configs}")

    train_results = pd.DataFrame(rows)

    eligible = train_results[train_results["trades"] >= 25].copy()

    if eligible.empty:
        raise RuntimeError(
            "No configuration produced at least 25 training trades."
        )

    # Ranking rewards expectancy and PF while penalizing drawdown.
    eligible["score"] = (
        eligible["avg_r"] * 100.0
        + eligible["profit_factor"] * 5.0
        + eligible["win_rate"] * 0.03
        + eligible["max_dd"] * 0.25
        + eligible["trades"] * 0.01
    )

    eligible = eligible.sort_values(
        ["score", "avg_r", "profit_factor"],
        ascending=False
    )

    train_results.to_csv(
        "xauusd_v4_1_parameter_sweep.csv",
        index=False
    )

    top = eligible.head(10).copy()
    top.to_csv("xauusd_v4_1_top10.csv", index=False)

    print()
    print("=" * 72)
    print("TOP TRAINING CONFIGURATIONS")
    print("=" * 72)

    print(
        top[
            [
                "volume", "range_pct", "body_pct", "sweep_pct",
                "trades", "total_r", "avg_r",
                "profit_factor", "max_dd", "win_rate"
            ]
        ].to_string(index=False)
    )

    validation_rows = []

    for _, row in top.iterrows():
        result = test_config(
            test_df,
            float(row["volume"]),
            float(row["range_pct"]),
            float(row["body_pct"]),
            float(row["sweep_pct"])
        )

        result["train_trades"] = int(row["trades"])
        result["train_total_r"] = float(row["total_r"])
        result["train_avg_r"] = float(row["avg_r"])
        result["train_pf"] = float(row["profit_factor"])
        result["train_max_dd"] = float(row["max_dd"])
        result["train_win_rate"] = float(row["win_rate"])

        validation_rows.append(result)

    validation = pd.DataFrame(validation_rows)

    validation.to_csv(
        "xauusd_v4_1_walkforward.csv",
        index=False
    )

    print()
    print("=" * 72)
    print("UNSEEN TEST / WALK-FORWARD RESULTS")
    print("=" * 72)

    print(
        validation[
            [
                "volume", "range_pct", "body_pct", "sweep_pct",
                "train_trades", "train_total_r",
                "trades", "total_r", "avg_r",
                "profit_factor", "max_dd", "win_rate"
            ]
        ].to_string(index=False)
    )

    valid = validation[validation["trades"] >= 10].copy()

    print()
    if valid.empty:
        print("WARNING: No candidate produced 10+ unseen-test trades.")
        print("Do NOT change the paper bot yet.")
    else:
        valid = valid.sort_values(
            ["avg_r", "profit_factor", "max_dd"],
            ascending=[False, False, False]
        )

        best = valid.iloc[0]

        print("=" * 72)
        print("PROVISIONAL V4.1 CANDIDATE")
        print("=" * 72)
        print(f"Volume filter: {best['volume']:.2f}x")
        print(f"Max range:     {best['range_pct']:.4f}")
        print(f"Body filter:   {best['body_pct']:.4f}")
        print(f"Sweep:         {best['sweep_pct']:.4f}")
        print(f"Test trades:   {int(best['trades'])}")
        print(f"Test total R:  {best['total_r']:+.2f}R")
        print(f"Test avg R:    {best['avg_r']:+.3f}R")
        print(f"Test PF:       {best['profit_factor']:.3f}")
        print(f"Test DD:       {best['max_dd']:+.2f}R")
        print(f"Test win rate: {best['win_rate']:.2f}%")

    print()
    print("Saved:")
    print("  xauusd_v4_1_parameter_sweep.csv")
    print("  xauusd_v4_1_top10.csv")
    print("  xauusd_v4_1_walkforward.csv")


if __name__ == "__main__":
    main()
