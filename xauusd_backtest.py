import argparse
import io
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests


# ============================================================
# XAUUSD V4 BACKTEST
# ============================================================

SYMBOL = "xauusd"
TIMEFRAME = "m5"

# V4 starting parameters.
# These are deliberately NOT optimized for gold yet.
RANGE_CANDLES = 24
MAX_RANGE_WIDTH_PCT = 0.015

SWEEP_PCT = 0.0003
SL_BUFFER_PCT = 0.0005

VOLUME_LOOKBACK = 20
MIN_VOLUME_RATIO = 1.50

CONFIRM_BODY_MIN_PCT = 0.0008

TP1_R = 1.0
TP2_R = 2.0

MAX_BARS_IN_TRADE = 72

MIN_TP2_R = 1.5


# ============================================================
# DUKASCOPY DATA
# ============================================================

BASE_URL = (
    "https://datafeed.dukascopy.com/datafeed"
)


def download_day(
    date,
):

    year = date.year
    month = date.month - 1
    day = date.day

    url = (
        f"{BASE_URL}/"
        f"{SYMBOL}/"
        f"{year}/"
        f"{month:02d}/"
        f"{day:02d}/"
        f"BID_candles_{TIMEFRAME}.bi5"
    )

    response = requests.get(
        url,
        timeout=30
    )

    if response.status_code != 200:
        return pd.DataFrame()

    if not response.content:
        return pd.DataFrame()

    # Dukascopy candle files are compressed binary.
    # Try normal decoding first.
    try:

        import lzma

        raw = lzma.decompress(
            response.content
        )

    except Exception:

        try:

            import zlib

            raw = zlib.decompress(
                response.content
            )

        except Exception:

            return pd.DataFrame()


    # --------------------------------------------------------
    # Dukascopy candle structure
    # --------------------------------------------------------

    rows = []

    row_size = 24

    for offset in range(
        0,
        len(raw),
        row_size
    ):

        chunk = raw[
            offset:
            offset + row_size
        ]

        if len(chunk) != row_size:
            continue

        try:

            import struct

            timestamp = struct.unpack(
                ">I",
                chunk[0:4]
            )[0]

            open_price = struct.unpack(
                ">I",
                chunk[4:8]
            )[0]

            close_price = struct.unpack(
                ">I",
                chunk[8:12]
            )[0]

            low_price = struct.unpack(
                ">I",
                chunk[12:16]
            )[0]

            high_price = struct.unpack(
                ">I",
                chunk[16:20]
            )[0]

            volume = struct.unpack(
                ">I",
                chunk[20:24]
            )[0]

        except Exception:

            continue


        # Dukascopy FX/metals prices use
        # a 1e-3 price multiplier for XAUUSD.

        multiplier = 1000.0


        rows.append({

            "time":
                pd.Timestamp(
                    datetime(
                        year,
                        date.month,
                        day,
                        tzinfo=timezone.utc
                    )
                )
                +
                pd.Timedelta(
                    milliseconds=timestamp
                ),

            "open":
                open_price / multiplier,

            "close":
                close_price / multiplier,

            "low":
                low_price / multiplier,

            "high":
                high_price / multiplier,

            "volume":
                volume
        })


    if not rows:

        return pd.DataFrame()


    return pd.DataFrame(
        rows
    )


# ============================================================
# DOWNLOAD PERIOD
# ============================================================

def download_data(
    start,
    end
):

    all_days = []

    current = start

    total_days = (
        end.date()
        - start.date()
    ).days + 1

    processed = 0


    while current.date() <= end.date():

        processed += 1

        print(
            f"Downloading "
            f"{current.date()} "
            f"({processed}/{total_days})"
        )


        try:

            df = download_day(
                current
            )

            if not df.empty:

                all_days.append(
                    df
                )

        except Exception as e:

            print(
                "Download error:",
                e
            )


        current += timedelta(
            days=1
        )


        # Avoid hammering the server.

        time.sleep(
            0.15
        )


    if not all_days:

        raise RuntimeError(
            "No XAUUSD data was downloaded."
        )


    df = pd.concat(
        all_days,
        ignore_index=True
    )


    df = (
        df
        .drop_duplicates(
            "time"
        )
        .sort_values(
            "time"
        )
        .reset_index(
            drop=True
        )
    )


    df = df[
        (df["time"] >= pd.Timestamp(start))
        &
        (df["time"] <= pd.Timestamp(end))
    ]


    return df.reset_index(
        drop=True
    )


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
# FIND V4 SIGNAL
# ============================================================

def find_signal(
    df,
    confirmation_index
):

    sweep_index = (
        confirmation_index - 1
    )


    minimum = (
        RANGE_CANDLES
        +
        VOLUME_LOOKBACK
        +
        2
    )


    if sweep_index < minimum:

        return None


    # --------------------------------------------------------
    # RANGE
    # --------------------------------------------------------

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


    width_pct = (
        range_high
        - range_low
    ) / midpoint


    if width_pct > MAX_RANGE_WIDTH_PCT:

        return None


    sweep = df.iloc[
        sweep_index
    ]


    confirm = df.iloc[
        confirmation_index
    ]


    sweep_high = float(
        sweep["high"]
    )


    sweep_low = float(
        sweep["low"]
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
    # VOLUME
    # --------------------------------------------------------

    vr = volume_ratio(
        df,
        sweep_index
    )


    if vr < MIN_VOLUME_RATIO:

        return None


    # ========================================================
    # BEARISH
    # ========================================================

    bearish_sweep = (

        sweep_high
        >
        range_high
        *
        (1 + SWEEP_PCT)

        and

        sweep_close
        <
        range_high
    )


    bearish_body = (

        confirm_open
        -
        confirm_close
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

        bearish_body
        >= CONFIRM_BODY_MIN_PCT

        and

        confirm_close
        <
        (
            sweep_high
            +
            sweep_close
        ) / 2
    )


    if (
        bearish_sweep
        and
        bearish_confirm
    ):

        entry = confirm_close


        stop = (
            sweep_high
            *
            (1 + SL_BUFFER_PCT)
        )


        risk = (
            stop
            - entry
        )


        if risk <= 0:

            return None


        tp1 = (
            entry
            -
            risk * TP1_R
        )


        tp2 = max(

            entry
            -
            risk * TP2_R,

            range_low
        )


        reward_r = (
            entry
            -
            tp2
        ) / risk


        if reward_r < MIN_TP2_R:

            return None


        return {

            "direction":
                "BEARISH",

            "signal_time":
                confirm["time"],

            "sweep_time":
                sweep["time"],

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
                width_pct,

            "reward_r":
                reward_r
        }


    # ========================================================
    # BULLISH
    # ========================================================

    bullish_sweep = (

        sweep_low
        <
        range_low
        *
        (1 - SWEEP_PCT)

        and

        sweep_close
        >
        range_low
    )


    bullish_body = (

        confirm_close
        -
        confirm_open
    ) / confirm_open


    bullish_confirm = (

        confirm_close
        >
        range_low

        and

        confirm_close
        >
        confirm_open

        and

        bullish_body
        >= CONFIRM_BODY_MIN_PCT

        and

        confirm_close
        >
        (
            sweep_low
            +
            sweep_close
        ) / 2
    )


    if (
        bullish_sweep
        and
        bullish_confirm
    ):

        entry = confirm_close


        stop = (
            sweep_low
            *
            (1 - SL_BUFFER_PCT)
        )


        risk = (
            entry
            - stop
        )


        if risk <= 0:

            return None


        tp1 = (
            entry
            +
            risk * TP1_R
        )


        tp2 = min(

            entry
            +
            risk * TP2_R,

            range_high
        )


        reward_r = (
            tp2
            -
            entry
        ) / risk


        if reward_r < MIN_TP2_R:

            return None


        return {

            "direction":
                "BULLISH",

            "signal_time":
                confirm["time"],

            "sweep_time":
                sweep["time"],

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
                width_pct,

            "reward_r":
                reward_r
        }


    return None


# ============================================================
# SIMULATE TRADE
# ============================================================

def simulate_trade(
    df,
    entry_index,
    signal
):

    direction = signal[
        "direction"
    ]

    entry = signal[
        "entry"
    ]

    stop = signal[
        "stop"
    ]

    tp1 = signal[
        "tp1"
    ]

    tp2 = signal[
        "tp2"
    ]


    tp1_hit = False


    for bars, j in enumerate(
        range(
            entry_index + 1,
            len(df)
        ),
        start=1
    ):

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
        # BULLISH
        # ====================================================

        if direction == "BULLISH":

            # Conservative:
            # SL first if both are touched.

            current_stop = (
                entry
                if tp1_hit
                else stop
            )


            if low <= current_stop:

                if tp1_hit:

                    return {

                        "outcome":
                            "TP1_BE",

                        "r":
                            0.5,

                        "bars":
                            bars,

                        "close_time":
                            candle["time"]
                    }


                return {

                    "outcome":
                        "SL",

                    "r":
                        -1.0,

                    "bars":
                        bars,

                    "close_time":
                        candle["time"]
                }


            if (
                not tp1_hit
                and
                high >= tp1
            ):

                tp1_hit = True


            if (
                tp1_hit
                and
                high >= tp2
            ):

                return {

                    "outcome":
                        "TP2",

                    "r":
                        1.5,

                    "bars":
                        bars,

                    "close_time":
                        candle["time"]
                }


        # ====================================================
        # BEARISH
        # ====================================================

        else:

            current_stop = (
                entry
                if tp1_hit
                else stop
            )


            if high >= current_stop:

                if tp1_hit:

                    return {

                        "outcome":
                            "TP1_BE",

                        "r":
                            0.5,

                        "bars":
                            bars,

                        "close_time":
                            candle["time"]
                    }


                return {

                    "outcome":
                        "SL",

                    "r":
                        -1.0,

                    "bars":
                        bars,

                    "close_time":
                        candle["time"]
                }


            if (
                not tp1_hit
                and
                low <= tp1
            ):

                tp1_hit = True


            if (
                tp1_hit
                and
                low <= tp2
            ):

                return {

                    "outcome":
                        "TP2",

                    "r":
                        1.5,

                    "bars":
                        bars,

                    "close_time":
                        candle["time"]
                }


        # ====================================================
        # TIME EXIT
        # ====================================================

        if bars >= MAX_BARS_IN_TRADE:

            if direction == "BULLISH":

                risk = (
                    entry
                    - stop
                )

                raw_r = (
                    close
                    - entry
                ) / risk

            else:

                risk = (
                    stop
                    - entry
                )

                raw_r = (
                    entry
                    - close
                ) / risk


            if tp1_hit:

                result_r = (
                    0.5
                    +
                    0.5 * raw_r
                )

            else:

                result_r = raw_r


            return {

                "outcome":
                    "TIME",

                "r":
                    result_r,

                "bars":
                    bars,

                "close_time":
                    candle["time"]
            }


    return {

        "outcome":
            "OPEN_AT_END",

        "r":
            0.0,

        "bars":
            len(df)
            - entry_index
            - 1,

        "close_time":
            df.iloc[-1]["time"]
    }


# ============================================================
# RUN BACKTEST
# ============================================================

def run_backtest(
    df
):

    trades = []


    start = (
        RANGE_CANDLES
        +
        VOLUME_LOOKBACK
        +
        2
    )


    i = start


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
                df.iloc[i]["time"]
        }


        trades.append(
            trade
        )


        # No overlapping trades.

        i += max(
            1,
            int(
                result["bars"]
            )
        )


    return pd.DataFrame(
        trades
    )


# ============================================================
# SUMMARY
# ============================================================

def summarize(
    trades,
    start,
    end,
    candles
):

    print()
    print("=" * 65)
    print(
        "XAUUSD 5m V4 RANGE LIQUIDITY BACKTEST"
    )
    print("=" * 65)

    print(
        f"Period:       "
        f"{start.date()} → {end.date()}"
    )

    print(
        f"Candles:      "
        f"{len(candles):,}"
    )


    if trades.empty:

        print(
            "\nNO TRADES FOUND."
        )

        print(
            "This means the current V4 filters "
            "are too strict for this period."
        )

        return


    total = len(
        trades
    )


    tp2 = int(
        (
            trades["outcome"]
            ==
            "TP2"
        ).sum()
    )


    tp1_be = int(
        (
            trades["outcome"]
            ==
            "TP1_BE"
        ).sum()
    )


    sl = int(
        (
            trades["outcome"]
            ==
            "SL"
        ).sum()
    )


    time_exits = int(
        (
            trades["outcome"]
            ==
            "TIME"
        ).sum()
    )


    r = trades[
        "r"
    ].astype(float)


    total_r = float(
        r.sum()
    )


    avg_r = float(
        r.mean()
    )


    wins = int(
        (
            r > 0
        ).sum()
    )


    losses = int(
        (
            r < 0
        ).sum()
    )


    gross_profit = float(
        r[
            r > 0
        ].sum()
    )


    gross_loss = abs(
        float(
            r[
                r < 0
            ].sum()
        )
    )


    if gross_loss > 0:

        profit_factor = (
            gross_profit
            /
            gross_loss
        )

    else:

        profit_factor = float(
            "inf"
        )


    equity = r.cumsum()


    peak = equity.cummax()


    drawdown = (
        equity
        -
        peak
    )


    max_dd = float(
        drawdown.min()
    )


    print(
        f"Trades:       {total}"
    )


    print(
        f"TP2 wins:     {tp2}"
    )


    print(
        f"TP1 → BE:     {tp1_be}"
    )


    print(
        f"SL losses:    {sl}"
    )


    print(
        f"Time exits:   {time_exits}"
    )


    print(
        f"Win rate:     "
        f"{wins / total * 100:.1f}%"
    )


    print(
        f"Total R:      "
        f"{total_r:+.2f}R"
    )


    print(
        f"Average R:    "
        f"{avg_r:+.3f}R"
    )


    print(
        f"Profit factor:"
        f" {profit_factor:.2f}"
    )


    print(
        f"Max drawdown: "
        f"{max_dd:.2f}R"
    )


    print("=" * 65)


    print(
        "\nBY DIRECTION"
    )


    for direction in [
        "BULLISH",
        "BEARISH"
    ]:

        x = trades[
            trades["direction"]
            ==
            direction
        ]


        if len(x):

            print(

                f"{direction:<10}"

                f"Trades={len(x):4d}  "

                f"R={x['r'].sum():+8.2f}  "

                f"Win={("
                x["r"] > 0
                ).mean() * 100:5.1f}%"
            )


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

        "volume_ratio",

        "outcome",

        "r"
    ]


    print(
        trades[
            columns
        ].tail(15).to_string(
            index=False
        )
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
        default="xauusd_v4_trades.csv"
    )


    args = parser.parse_args()


    end = datetime.now(
        timezone.utc
    )


    start = (
        end
        -
        timedelta(
            days=args.days
        )
    )


    print()
    print(
        f"Downloading XAUUSD "
        f"5m data for {args.days} days..."
    )


    data = download_data(
        start,
        end
    )


    print()
    print(
        f"Downloaded "
        f"{len(data):,} candles."
    )


    if data.empty:

        raise RuntimeError(
            "No XAUUSD candles available."
        )


    trades = run_backtest(
        data
    )


    if not trades.empty:

        trades.to_csv(
            args.output,
            index=False
        )


        print()
        print(
            f"Trade list saved to "
            f"{args.output}"
        )


    summarize(
        trades,
        start,
        end,
        data
    )


if __name__ == "__main__":

    main()
