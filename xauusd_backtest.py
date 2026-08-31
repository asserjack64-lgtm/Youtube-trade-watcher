import argparse
import lzma
import math
import struct
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests


# ============================================================
# XAUUSD V4 BACKTEST
# ============================================================

SYMBOL = "XAUUSD"
TIMEFRAME = "M5"

# ============================================================
# V4 PARAMETERS
# ============================================================

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
# DATA SETTINGS
# ============================================================

BASE_URL = (
    "https://datafeed.dukascopy.com/datafeed"
)

REQUEST_TIMEOUT = 30

RETRY_COUNT = 3

RETRY_DELAY = 1.0


# ============================================================
# OUTPUT FILES
# ============================================================

TRADES_FILE = "xauusd_v4_trades.csv"

ROBUSTNESS_FILE = "xauusd_v4_robustness.csv"


# ============================================================
# SESSION
# ============================================================

UTC = timezone.utc


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent":
            "Mozilla/5.0 "
            "(XAUUSD V4 Backtest)"
    }
)


# ============================================================
# DOWNLOAD ONE DAY
# ============================================================

def download_day(
    day,
    timeframe="M5"
):
    """
    Download one day's Dukascopy candle data.

    M5 is attempted first.

    If M5 is unavailable, M1 is downloaded
    and resampled to M5.
    """

    symbol = SYMBOL.upper()

    year = day.year

    month = day.month - 1

    day_number = day.day


    # --------------------------------------------------------
    # M5 native candle
    # --------------------------------------------------------

    if timeframe == "M5":

        filenames = [
            "BID_candles_min_5.bi5",
            "BID_candles_min_1.bi5"
        ]

    else:

        filenames = [
            "BID_candles_min_1.bi5"
        ]


    for filename in filenames:

        url = (
            f"{BASE_URL}/"
            f"{symbol}/"
            f"{year:04d}/"
            f"{month:02d}/"
            f"{day_number:02d}/"
            f"{filename}"
        )


        for attempt in range(
            RETRY_COUNT
        ):

            try:

                response = SESSION.get(
                    url,
                    timeout=REQUEST_TIMEOUT
                )

                if response.status_code == 404:

                    break

                if response.status_code != 200:

                    if attempt < RETRY_COUNT - 1:

                        time.sleep(
                            RETRY_DELAY
                        )

                        continue

                    break

                if not response.content:

                    break


                parsed = parse_bi5_candles(
                    response.content,
                    day
                )


                if parsed.empty:

                    break


                if filename == (
                    "BID_candles_min_1.bi5"
                ):

                    parsed = resample_to_m5(
                        parsed
                    )


                return parsed


            except Exception as exc:

                if attempt < RETRY_COUNT - 1:

                    time.sleep(
                        RETRY_DELAY
                    )

                else:

                    print(
                        f"Download error "
                        f"{day}: {exc}"
                    )


    return pd.DataFrame()


# ============================================================
# PARSE BI5 CANDLES
# ============================================================

def parse_bi5_candles(
    compressed,
    day
):
    """
    Dukascopy candle records:

    24 bytes each

    uint32 seconds from day start
    uint32 open
    uint32 close
    uint32 low
    uint32 high
    float32 volume
    """

    try:

        raw = lzma.decompress(
            compressed
        )

    except Exception:

        try:

            decompressor = (
                lzma.LZMADecompressor(
                    format=lzma.FORMAT_AUTO
                )
            )

            raw = decompressor.decompress(
                compressed
            )

        except Exception:

            return pd.DataFrame()


    row_size = 24

    if len(raw) < row_size:

        return pd.DataFrame()


    rows = []


    # --------------------------------------------------------
    # XAUUSD price scale
    # --------------------------------------------------------

    price_multiplier = 1000.0


    for offset in range(
        0,
        len(raw) - row_size + 1,
        row_size
    ):

        chunk = raw[
            offset:
            offset + row_size
        ]


        try:

            (
                seconds,
                open_raw,
                close_raw,
                low_raw,
                high_raw,
                volume
            ) = struct.unpack(
                ">IIIIIf",
                chunk
            )

        except struct.error:

            continue


        # ----------------------------------------------------
        # Convert prices
        # ----------------------------------------------------

        open_price = (
            open_raw /
            price_multiplier
        )

        close_price = (
            close_raw /
            price_multiplier
        )

        low_price = (
            low_raw /
            price_multiplier
        )

        high_price = (
            high_raw /
            price_multiplier
        )


        # ----------------------------------------------------
        # Protect against malformed records
        # ----------------------------------------------------

        if (
            not math.isfinite(open_price)
            or not math.isfinite(close_price)
            or not math.isfinite(low_price)
            or not math.isfinite(high_price)
        ):

            continue


        if low_price > high_price:

            low_price, high_price = (
                high_price,
                low_price
            )


        if (
            open_price <= 0
            or close_price <= 0
            or low_price <= 0
            or high_price <= 0
        ):

            continue


        base_day = pd.Timestamp(day)

if base_day.tzinfo is None:
    base_day = base_day.tz_localize("UTC")
else:
    base_day = base_day.tz_convert("UTC")

timestamp = (
    base_day
    +
    pd.Timedelta(
        seconds=int(seconds)
    )
)


        rows.append(
            {
                "time": timestamp,

                "open": open_price,

                "high": high_price,

                "low": low_price,

                "close": close_price,

                "volume": float(volume)
            }
        )


    if not rows:

        return pd.DataFrame()


    df = pd.DataFrame(
        rows
    )


    df = (
        df
        .drop_duplicates(
            subset=["time"]
        )
        .sort_values("time")
        .reset_index(drop=True)
    )


    return df


# ============================================================
# RESAMPLE M1 -> M5
# ============================================================

def resample_to_m5(df):

    if df.empty:

        return df


    work = (
        df
        .set_index("time")
        .sort_index()
    )


    result = (
        work
        .resample(
            "5min",
            label="left",
            closed="left"
        )
        .agg(
            {
                "open": "first",

                "high": "max",

                "low": "min",

                "close": "last",

                "volume": "sum"
            }
        )
        .dropna(
            subset=[
                "open",
                "high",
                "low",
                "close"
            ]
        )
        .reset_index()
    )


    return result


# ============================================================
# DOWNLOAD COMPLETE PERIOD
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

    successful_days = 0


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

                successful_days += 1


        except Exception as exc:

            print(
                "Download error:",
                exc
            )


        current += timedelta(
            days=1
        )


        # ----------------------------------------------------
        # Avoid hammering Dukascopy
        # ----------------------------------------------------

        time.sleep(
            0.10
        )


    print()
    print(
        f"Successful days: "
        f"{successful_days}/{total_days}"
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
            subset=["time"]
        )
        .sort_values("time")
        .reset_index(drop=True)
    )


    # --------------------------------------------------------
    # Filter requested period
    # --------------------------------------------------------

    df = df[
        (df["time"] >= start)
        &
        (df["time"] <= end)
    ].copy()


    df = (
        df
        .drop_duplicates(
            subset=["time"]
        )
        .sort_values("time")
        .reset_index(drop=True)
    )


    return df


# ============================================================
# VOLUME RATIO
# ============================================================

def volume_ratio(
    df,
    index,
    lookback=None
):

    if lookback is None:

        lookback = VOLUME_LOOKBACK


    start = max(
        0,
        index - lookback
    )


    previous = df.iloc[
        start:index
    ]["volume"]


    if len(previous) < 5:

        return 0.0


    average = float(
        previous.mean()
    )


    if average <= 0:

        return 0.0


    current_volume = float(
        df.iloc[index]["volume"]
    )


    return (
        current_volume /
        average
    )


# ============================================================
# BODY SIZE
# ============================================================

def body_pct(candle):

    open_price = float(
        candle["open"]
    )

    close_price = float(
        candle["close"]
    )


    if open_price <= 0:

        return 0.0


    return (
        abs(close_price - open_price)
        /
        open_price
    )


# ============================================================
# FIND V4 SIGNAL
# ============================================================

def find_signal(
    df,
    index,
    min_volume_ratio=None,
    max_range_width_pct=None,
    sweep_pct=None,
    confirm_body_min_pct=None
):

    if min_volume_ratio is None:

        min_volume_ratio = (
            MIN_VOLUME_RATIO
        )

    if max_range_width_pct is None:

        max_range_width_pct = (
            MAX_RANGE_WIDTH_PCT
        )

    if sweep_pct is None:

        sweep_pct = SWEEP_PCT

    if confirm_body_min_pct is None:

        confirm_body_min_pct = (
            CONFIRM_BODY_MIN_PCT
        )


    # --------------------------------------------------------
    # Need range + sweep + confirmation
    # --------------------------------------------------------

    if index < (
        RANGE_CANDLES + 1
    ):

        return None


    if index + 1 >= len(df):

        return None


    # --------------------------------------------------------
    # Range immediately before sweep candle
    # --------------------------------------------------------

    range_start = (
        index -
        RANGE_CANDLES
    )

    range_end = index


    range_df = df.iloc[
        range_start:range_end
    ]


    range_high = float(
        range_df["high"].max()
    )

    range_low = float(
        range_df["low"].min()
    )


    if range_low <= 0:

        return None


    range_width = (
        range_high -
        range_low
    ) / range_low


    if (
        range_width >
        max_range_width_pct
    ):

        return None


    sweep = df.iloc[
        index
    ]

    confirmation = df.iloc[
        index + 1
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


    # ========================================================
    # BEARISH LIQUIDITY SWEEP
    # ========================================================

    bearish_sweep = (
        sweep_high
        >
        range_high *
        (1 + sweep_pct)
    )


    bearish_reclaim = (
        sweep_close
        <
        range_high
    )


    bearish_volume = (
        volume_ratio(
            df,
            index
        )
        >=
        min_volume_ratio
    )


    if (
        bearish_sweep
        and bearish_reclaim
        and bearish_volume
    ):

        confirmation_open = float(
            confirmation["open"]
        )

        confirmation_close = float(
            confirmation["close"]
        )


        confirmation_bearish = (
            confirmation_close
            <
            confirmation_open
        )


        confirmation_body = (
            body_pct(
                confirmation
            )
            >=
            confirm_body_min_pct
        )


        confirmation_reclaim = (
            confirmation_close
            <
            range_high
        )


        if (
            confirmation_bearish
            and confirmation_body
            and confirmation_reclaim
        ):

            return {
                "direction":
                    "BEARISH",

                "sweep_index":
                    index,

                "confirmation_index":
                    index + 1,

                "sweep_time":
                    sweep["time"],

                "confirmation_time":
                    confirmation["time"],

                "range_high":
                    range_high,

                "range_low":
                    range_low,

                "sweep_high":
                    sweep_high,

                "sweep_low":
                    sweep_low,

                "entry":
                    confirmation_close,

                "volume_ratio":
                    volume_ratio(
                        df,
                        index
                    ),

                "range_width":
                    range_width
            }


    # ========================================================
    # BULLISH LIQUIDITY SWEEP
    # ========================================================

    bullish_sweep = (
        sweep_low
        <
        range_low *
        (1 - sweep_pct)
    )


    bullish_reclaim = (
        sweep_close
        >
        range_low
    )


    bullish_volume = (
        volume_ratio(
            df,
            index
        )
        >=
        min_volume_ratio
    )


    if (
        bullish_sweep
        and bullish_reclaim
        and bullish_volume
    ):

        confirmation_open = float(
            confirmation["open"]
        )

        confirmation_close = float(
            confirmation["close"]
        )


        confirmation_bullish = (
            confirmation_close
            >
            confirmation_open
        )


        confirmation_body = (
            body_pct(
                confirmation
            )
            >=
            confirm_body_min_pct
        )


        confirmation_reclaim = (
            confirmation_close
            >
            range_low
        )


        if (
            confirmation_bullish
            and confirmation_body
            and confirmation_reclaim
        ):

            return {
                "direction":
                    "BULLISH",

                "sweep_index":
                    index,

                "confirmation_index":
                    index + 1,

                "sweep_time":
                    sweep["time"],

                "confirmation_time":
                    confirmation["time"],

                "range_high":
                    range_high,

                "range_low":
                    range_low,

                "sweep_high":
                    sweep_high,

                "sweep_low":
                    sweep_low,

                "entry":
                    confirmation_close,

                "volume_ratio":
                    volume_ratio(
                        df,
                        index
                    ),

                "range_width":
                    range_width
            }


    return None


# ============================================================
# BUILD TRADE LEVELS
# ============================================================

def build_trade(
    signal
):

    direction = signal[
        "direction"
    ]

    entry = float(
        signal["entry"]
    )


    if direction == "BEARISH":

        # Stop above sweep high.

        stop = (
            float(
                signal["sweep_high"]
            )
            *
            (1 + SL_BUFFER_PCT)
        )


        risk = (
            stop -
            entry
        )


        if risk <= 0:

            return None


        tp1 = (
            entry -
            risk * TP1_R
        )


        tp2 = (
            entry -
            risk * TP2_R
        )


    else:

        # Stop below sweep low.

        stop = (
            float(
                signal["sweep_low"]
            )
            *
            (1 - SL_BUFFER_PCT)
        )


        risk = (
            entry -
            stop
        )


        if risk <= 0:

            return None


        tp1 = (
            entry +
            risk * TP1_R
        )


        tp2 = (
            entry +
            risk * TP2_R
        )


    tp2_r = abs(
        tp2 - entry
    ) / risk


    if tp2_r < MIN_TP2_R:

        return None


    return {
        "direction":
            direction,

        "entry":
            entry,

        "stop":
            stop,

        "tp1":
            tp1,

        "tp2":
            tp2,

        "risk":
            risk
    }


# ============================================================
# SIMULATE ONE TRADE
# ============================================================

def simulate_trade(
    df,
    signal,
    trade
):

    direction = trade[
        "direction"
    ]

    entry = float(
        trade["entry"]
    )

    stop = float(
        trade["stop"]
    )

    tp1 = float(
        trade["tp1"]
    )

    tp2 = float(
        trade["tp2"]
    )


    start_index = (
        signal["confirmation_index"]
        + 1
    )


    end_index = min(
        len(df) - 1,
        start_index +
        MAX_BARS_IN_TRADE -
        1
    )


    tp1_hit = False

    tp2_hit = False

    exit_price = None

    exit_time = None

    outcome = None

    exit_index = None


    # ========================================================
    # TRADE LOOP
    # ========================================================

    for i in range(
        start_index,
        end_index + 1
    ):

        candle = df.iloc[i]

        high = float(
            candle["high"]
        )

        low = float(
            candle["low"]
        )

        current_time = (
            candle["time"]
        )


        # ====================================================
        # BEFORE TP1
        # ====================================================

        if not tp1_hit:

            if direction == "BULLISH":

                # Conservative:
                # if both SL and TP1 occur
                # in same candle, SL wins.

                if low <= stop:

                    exit_price = stop

                    exit_time = current_time

                    outcome = "SL"

                    exit_index = i

                    break


                if high >= tp1:

                    tp1_hit = True

                    continue


            else:

                if high >= stop:

                    exit_price = stop

                    exit_time = current_time

                    outcome = "SL"

                    exit_index = i

                    break


                if low <= tp1:

                    tp1_hit = True

                    continue


        # ====================================================
        # AFTER TP1
        # ====================================================

        else:

            # After TP1, half position has
            # been closed at +1R.
            #
            # Remaining half is protected
            # at breakeven.

            breakeven = entry


            if direction == "BULLISH":

                # Conservative:
                # breakeven before TP2.

                if low <= breakeven:

                    exit_price = breakeven

                    exit_time = current_time

                    outcome = "TP1_BE"

                    exit_index = i

                    break


                if high >= tp2:

                    tp2_hit = True

                    exit_price = tp2

                    exit_time = current_time

                    outcome = "TP2"

                    exit_index = i

                    break


            else:

                if high >= breakeven:

                    exit_price = breakeven

                    exit_time = current_time

                    outcome = "TP1_BE"

                    exit_index = i

                    break


                if low <= tp2:

                    tp2_hit = True

                    exit_price = tp2

                    exit_time = current_time

                    outcome = "TP2"

                    exit_index = i

                    break


    # ========================================================
    # TIME EXIT
    # ========================================================

    if outcome is None:

        exit_index = end_index

        candle = df.iloc[
            end_index
        ]

        exit_price = float(
            candle["close"]
        )

        exit_time = (
            candle["time"]
        )

        outcome = "TIME"


    # ========================================================
    # R RESULT
    # ========================================================

    risk = float(
        trade["risk"]
    )


    if outcome == "SL":

        result_r = -1.0


    elif outcome == "TP2":

        # Half at TP1 = +0.5R
        # Half at TP2 = +1.0R
        #
        # Total = +1.5R

        result_r = (
            TP1_R * 0.5
            +
            TP2_R * 0.5
        )


    elif outcome == "TP1_BE":

        # Half at TP1 = +0.5R
        # Remaining half exits at BE.

        result_r = (
            TP1_R * 0.5
        )


    else:

        # Time exit.
        #
        # Calculate actual R based on
        # the close, with 50/50 position
        # treatment if TP1 was reached.

        if direction == "BULLISH":

            raw_r = (
                exit_price -
                entry
            ) / risk

        else:

            raw_r = (
                entry -
                exit_price
            ) / risk


        if tp1_hit:

            result_r = (
                TP1_R * 0.5
                +
                raw_r * 0.5
            )

        else:

            result_r = raw_r


    return {
        "entry_time":
            signal["confirmation_time"],

        "sweep_time":
            signal["sweep_time"],

        "direction":
            direction,

        "entry":
            entry,

        "stop":
            stop,

        "tp1":
            tp1,

        "tp2":
            tp2,

        "volume_ratio":
            signal["volume_ratio"],

        "range_width":
            signal["range_width"],

        "outcome":
            outcome,

        "r":
            result_r,

        "exit_price":
            exit_price,

        "exit_time":
            exit_time,

        "bars_in_trade":
            exit_index -
            signal["confirmation_index"],

        "tp1_hit":
            tp1_hit,

        "tp2_hit":
            tp2_hit
    }


# ============================================================
# RUN BACKTEST
# ============================================================

def run_backtest(
    df,
    min_volume_ratio=None,
    max_range_width_pct=None,
    sweep_pct=None,
    confirm_body_min_pct=None
):

    trades = []

    i = (
        RANGE_CANDLES +
        1
    )


    while i < len(df) - 1:

        signal = find_signal(
            df,
            i,
            min_volume_ratio=
                min_volume_ratio,

            max_range_width_pct=
                max_range_width_pct,

            sweep_pct=
                sweep_pct,

            confirm_body_min_pct=
                confirm_body_min_pct
        )


        if signal is None:

            i += 1

            continue


        trade = build_trade(
            signal
        )


        if trade is None:

            i += 1

            continue


        result = simulate_trade(
            df,
            signal,
            trade
        )


        if result is None:

            i += 1

            continue


        trades.append(
            result
        )


        # ----------------------------------------------------
        # No overlapping trades.
        #
        # Resume searching after the trade closes.
        # ----------------------------------------------------

        exit_time = (
            result["exit_time"]
        )


        matching = df.index[
            df["time"] ==
            exit_time
        ]


        if len(matching):

            i = (
                int(matching[0])
                + 1
            )

        else:

            i += 1


    return pd.DataFrame(
        trades
    )


# ============================================================
# SAVE TRADES
# ============================================================

def save_trades(
    trades,
    filename=TRADES_FILE
):

    if trades.empty:

        trades.to_csv(
            filename,
            index=False
        )

        return


    trades.to_csv(
        filename,
        index=False
    )


# ============================================================
# SUMMARY
# ============================================================

def summarize(
    trades,
    title="XAUUSD 5m V4 BACKTEST"
):

    print()
    print("=" * 68)
    print(title)
    print("=" * 68)


    if trades.empty:

        print(
            "Trades: 0"
        )

        print(
            "No qualifying setups found."
        )

        print("=" * 68)

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


    tp1_be = int(
        (
            trades["outcome"]
            == "TP1_BE"
        ).sum()
    )


    time_exits = int(
        (
            trades["outcome"]
            == "TIME"
        ).sum()
    )


    profitable = int(
        (
            trades["r"]
            > 0
        ).sum()
    )


    losing = int(
        (
            trades["r"]
            < 0
        ).sum()
    )


    breakeven = int(
        (
            trades["r"]
            == 0
        ).sum()
    )


    total_r = float(
        trades["r"].sum()
    )


    average_r = float(
        trades["r"].mean()
    )


    win_rate = (
        profitable /
        total *
        100
    )


    gross_profit = float(
        trades.loc[
            trades["r"] > 0,
            "r"
        ].sum()
    )


    gross_loss = abs(
        float(
            trades.loc[
                trades["r"] < 0,
                "r"
            ].sum()
        )
    )


    if gross_loss > 0:

        profit_factor = (
            gross_profit /
            gross_loss
        )

    else:

        profit_factor = float(
            "inf"
        )


    # --------------------------------------------------------
    # Drawdown
    # --------------------------------------------------------

    equity = (
        trades["r"]
        .cumsum()
    )


    running_max = (
        equity
        .cummax()
    )


    drawdown = (
        equity -
        running_max
    )


    max_drawdown = float(
        drawdown.min()
    )


    max_win_streak = 0

    current_win_streak = 0


    for result_r in trades["r"]:

        if result_r > 0:

            current_win_streak += 1

            max_win_streak = max(
                max_win_streak,
                current_win_streak
            )

        else:

            current_win_streak = 0


    print(
        f"Trades:              {total}"
    )

    print(
        f"TP2 wins:            {tp2}"
    )

    print(
        f"Full SL:             {sl}"
    )

    print(
        f"TP1 -> BE:           {tp1_be}"
    )

    print(
        f"Time exits:          {time_exits}"
    )

    print()

    print(
        f"Profitable trades:   {profitable}"
    )

    print(
        f"Losing trades:       {losing}"
    )

    print(
        f"Breakeven trades:    {breakeven}"
    )

    print()

    print(
        f"Win rate:            {win_rate:.1f}%"
    )

    print(
        f"Total R:             {total_r:+.2f}R"
    )

    print(
        f"Average R/trade:     {average_r:+.3f}R"
    )

    print(
        f"Profit factor:       {profit_factor:.2f}"
    )

    print(
        f"Max drawdown:        {max_drawdown:+.2f}R"
    )

    print(
        f"Max winning streak:  {max_win_streak}"
    )

    print()

    print(
        "R CONTRIBUTION"
    )

    print(
        "-" * 68
    )


    tp2_r = (
        tp2 *
        (
            TP1_R * 0.5
            +
            TP2_R * 0.5
        )
    )


    sl_r = (
        sl *
        -1
    )


    tp1_be_r = (
        tp1_be *
        TP1_R *
        0.5
    )


    time_r = float(
        trades.loc[
            trades["outcome"]
            == "TIME",
            "r"
        ].sum()
    )


    print(
        f"TP2 R:               {tp2_r:+.2f}R"
    )

    print(
        f"Full SL R:           {sl_r:+.2f}R"
    )

    print(
        f"TP1 -> BE R:         {tp1_be_r:+.2f}R"
    )

    print(
        f"TIME R:              {time_r:+.2f}R"
    )

    print(
        f"TOTAL R:             {total_r:+.2f}R"
    )


    print()

    print(
        "SIGNAL QUALITY"
    )

    print(
        "-" * 68
    )


    print(
        "Average sweep volume: "
        f"{trades['volume_ratio'].mean():.2f}x"
    )


    print(
        "Minimum sweep volume: "
        f"{trades['volume_ratio'].min():.2f}x"
    )


    print(
        "Maximum sweep volume: "
        f"{trades['volume_ratio'].max():.2f}x"
    )


    print(
        "Average range width: "
        f"{trades['range_width'].mean() * 100:.3f}%"
    )


    print()

    print(
        "MONTHLY PERFORMANCE"
    )

    print(
        "-" * 68
    )


    monthly = (
        trades.assign(
            month=pd.to_datetime(
                trades["entry_time"]
            ).dt.strftime(
                "%Y-%m"
            )
        )
        .groupby("month")
        .agg(
            trades=("r", "count"),

            wins=(
                "r",
                lambda x:
                    int((x > 0).sum())
            ),

            losses=(
                "r",
                lambda x:
                    int((x < 0).sum())
            ),

            R=(
                "r",
                "sum"
            )
        )
    )


    for month, row in (
        monthly.iterrows()
    ):

        print(
            f"{month} "
            f"trades={int(row['trades']):3d} "
            f"wins={int(row['wins']):3d} "
            f"losses={int(row['losses']):3d} "
            f"R={row['R']:+.2f}"
        )


    print()

    print(
        "LAST 15 TRADES"
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
        "outcome",
        "r"
    ]


    available = [
        c
        for c in columns
        if c in trades.columns
    ]


    print(
        trades[
            available
        ]
        .tail(15)
        .to_string(
            index=False
        )
    )


    print("=" * 68)


# ============================================================
# ROBUSTNESS TEST
# ============================================================

def run_robustness(
    df
):

    print()
    print("=" * 68)
    print(
        "PARAMETER ROBUSTNESS TEST"
    )
    print("=" * 68)


    configurations = [

        (
            "BASE",
            MIN_VOLUME_RATIO,
            MAX_RANGE_WIDTH_PCT,
            SWEEP_PCT,
            CONFIRM_BODY_MIN_PCT
        ),

        (
            "LOWER_VOLUME",
            1.25,
            MAX_RANGE_WIDTH_PCT,
            SWEEP_PCT,
            CONFIRM_BODY_MIN_PCT
        ),

        (
            "HIGHER_VOLUME",
            1.75,
            MAX_RANGE_WIDTH_PCT,
            SWEEP_PCT,
            CONFIRM_BODY_MIN_PCT
        ),

        (
            "LOWER_BODY",
            MIN_VOLUME_RATIO,
            MAX_RANGE_WIDTH_PCT,
            SWEEP_PCT,
            0.0005
        ),

        (
            "TIGHT_RANGE",
            MIN_VOLUME_RATIO,
            0.010,
            SWEEP_PCT,
            CONFIRM_BODY_MIN_PCT
        ),

        (
            "STRICT",
            1.75,
            0.010,
            SWEEP_PCT,
            0.0010
        )
    ]


    results = []


    for (
        name,
        volume_filter,
        range_filter,
        sweep_filter,
        body_filter
    ) in configurations:

        print(
            f"Testing {name}..."
        )


        trades = run_backtest(
            df,

            min_volume_ratio=
                volume_filter,

            max_range_width_pct=
                range_filter,

            sweep_pct=
                sweep_filter,

            confirm_body_min_pct=
                body_filter
        )


        if trades.empty:

            results.append(
                {
                    "config":
                        name,

                    "trades":
                        0,

                    "total_r":
                        0.0,

                    "avg_r":
                        0.0,

                    "profit_factor":
                        0.0,

                    "max_dd":
                        0.0,

                    "win_rate":
                        0.0
                }
            )

            continue


        total_r = float(
            trades["r"].sum()
        )


        avg_r = float(
            trades["r"].mean()
        )


        wins = (
            trades["r"] > 0
        ).sum()


        win_rate = (
            wins /
            len(trades) *
            100
        )


        gross_profit = float(
            trades.loc[
                trades["r"] > 0,
                "r"
            ].sum()
        )


        gross_loss = abs(
            float(
                trades.loc[
                    trades["r"] < 0,
                    "r"
                ].sum()
            )
        )


        if gross_loss > 0:

            profit_factor = (
                gross_profit /
                gross_loss
            )

        else:

            profit_factor = 0.0


        equity = (
            trades["r"]
            .cumsum()
        )


        running_max = (
            equity
            .cummax()
        )


        max_dd = float(
            (
                equity -
                running_max
            ).min()
        )


        results.append(
            {
                "config":
                    name,

                "trades":
                    len(trades),

                "total_r":
                    total_r,

                "avg_r":
                    avg_r,

                "profit_factor":
                    profit_factor,

                "max_dd":
                    max_dd,

                "win_rate":
                    win_rate
            }
        )


    results_df = pd.DataFrame(
        results
    )


    print()
    print(
        f"{'config':<16}"
        f"{'trades':>8}"
        f"{'total_r':>12}"
        f"{'avg_r':>10}"
        f"{'profit_factor':>16}"
        f"{'max_dd':>12}"
        f"{'win_rate':>12}"
    )


    print(
        "-" * 80
    )


    for _, row in (
        results_df.iterrows()
    ):

        print(
            f"{str(row['config']):<16}"
            f"{int(row['trades']):>8}"
            f"{row['total_r']:>12.3f}"
            f"{row['avg_r']:>10.3f}"
            f"{row['profit_factor']:>16.3f}"
            f"{row['max_dd']:>12.3f}"
            f"{row['win_rate']:>11.3f}%"
        )


    results_df.to_csv(
        ROBUSTNESS_FILE,
        index=False
    )


    print()
    print(
        f"Saved robustness results to "
        f"{ROBUSTNESS_FILE}"
    )


    return results_df


# ============================================================
# DATA QUALITY CHECK
# ============================================================

def data_quality_check(
    df
):

    print()
    print(
        "DATA QUALITY CHECK"
    )

    print(
        "-" * 68
    )


    if df.empty:

        print(
            "No data."
        )

        return


    print(
        f"Rows:       {len(df):,}"
    )


    print(
        f"Start:      {df['time'].min()}"
    )


    print(
        f"End:        {df['time'].max()}"
    )


    print(
        f"Open:       {df.iloc[0]['open']:.3f}"
    )


    print(
        f"Close:      {df.iloc[-1]['close']:.3f}"
    )


    print(
        f"Min price:  {df['low'].min():.3f}"
    )


    print(
        f"Max price:  {df['high'].max():.3f}"
    )


    print(
        f"Avg volume: {df['volume'].mean():.3f}"
    )


    duplicate_count = int(
        df["time"].duplicated().sum()
    )


    print(
        f"Duplicates: {duplicate_count}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=
            "XAUUSD V4 Liquidity Sweep Backtest"
    )


    parser.add_argument(
        "--days",
        type=int,
        default=180,
        help=
            "Number of calendar days "
            "to test"
    )


    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help=
            "Start date YYYY-MM-DD"
    )


    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help=
            "End date YYYY-MM-DD"
    )


    parser.add_argument(
        "--no-robustness",
        action="store_true",
        help=
            "Skip robustness test"
    )


    args = parser.parse_args()


    # ========================================================
    # DATE RANGE
    # ========================================================

    if args.end:

        end_date = datetime.strptime(
            args.end,
            "%Y-%m-%d"
        ).date()

    else:

        # Use yesterday to avoid
        # requesting the incomplete
        # current day.

        end_date = (
            date.today()
            -
            timedelta(days=1)
        )


    if args.start:

        start_date = datetime.strptime(
            args.start,
            "%Y-%m-%d"
        ).date()

    else:

        start_date = (
            end_date -
            timedelta(
                days=args.days - 1
            )
        )


    start = pd.Timestamp(
        start_date,
        tz="UTC"
    )


    end = (
        pd.Timestamp(
            end_date,
            tz="UTC"
        )
        +
        pd.Timedelta(
            hours=23,
            minutes=59,
            seconds=59
        )
    )


    print()
    print("=" * 68)
    print(
        "XAUUSD V4 LIQUIDITY SWEEP BACKTEST"
    )
    print("=" * 68)


    print(
        f"Symbol:       {SYMBOL}"
    )


    print(
        f"Timeframe:    {TIMEFRAME}"
    )


    print(
        f"Period:       "
        f"{start_date} -> {end_date}"
    )


    print(
        f"Range candles:{RANGE_CANDLES}"
    )


    print(
        f"Max range:    "
        f"{MAX_RANGE_WIDTH_PCT * 100:.3f}%"
    )


    print(
        f"Sweep:        "
        f"{SWEEP_PCT * 100:.3f}%"
    )


    print(
        f"Volume filter:{MIN_VOLUME_RATIO:.2f}x"
    )


    print(
        f"Body filter:  "
        f"{CONFIRM_BODY_MIN_PCT * 100:.3f}%"
    )


    print(
        f"SL buffer:    "
        f"{SL_BUFFER_PCT * 100:.3f}%"
    )


    print(
        f"TP1:          {TP1_R:.1f}R"
    )


    print(
        f"TP2:          {TP2_R:.1f}R"
    )


    print(
        f"Max bars:     {MAX_BARS_IN_TRADE}"
    )


    print("=" * 68)


    # ========================================================
    # DOWNLOAD
    # ========================================================

    print()

    print(
        "Downloading XAUUSD historical data..."
    )


    df = download_data(
        start,
        end
    )


    print()

    print(
        f"Downloaded "
        f"{len(df):,} completed candles."
    )


    if df.empty:

        raise RuntimeError(
            "No usable XAUUSD candles "
            "were downloaded."
        )


    # ========================================================
    # DATA QUALITY
    # ========================================================

    data_quality_check(
        df
    )


    # ========================================================
    # SAVE RAW BACKTEST DATA
    # ========================================================

    df.to_csv(
        "xauusd_v4_data.csv",
        index=False
    )


    print()
    print(
        "Saved data to "
        "xauusd_v4_data.csv"
    )


    # ========================================================
    # RUN BASE BACKTEST
    # ========================================================

    print()

    print(
        "Running V4 backtest..."
    )


    trades = run_backtest(
        df
    )


    # ========================================================
    # SAVE TRADES
    # ========================================================

    save_trades(
        trades
    )


    print()

    print(
        f"Saved trade list to "
        f"{TRADES_FILE}"
    )


    # ========================================================
    # SUMMARY
    # ========================================================

    summarize(
        trades,
        "XAUUSD 5m V4 BASE BACKTEST"
    )


    # ========================================================
    # ROBUSTNESS
    # ========================================================

    if not args.no_robustness:

        run_robustness(
            df
        )


    # ========================================================
    # ACCOUNTING CHECK
    # ========================================================

    print()
    print(
        "=" * 68
    )

    print(
        "ACCOUNTING CHECK"
    )

    print(
        "-" * 68
    )


    if trades.empty:

        print(
            "No trades to check."
        )

    else:

        total_r = float(
            trades["r"].sum()
        )


        tp2_count = int(
            (
                trades["outcome"]
                == "TP2"
            ).sum()
        )


        sl_count = int(
            (
                trades["outcome"]
                == "SL"
            ).sum()
        )


        tp1_be_count = int(
            (
                trades["outcome"]
                == "TP1_BE"
            ).sum()
        )


        time_r = float(
            trades.loc[
                trades["outcome"]
                == "TIME",
                "r"
            ].sum()
        )


        component_total = (
            tp2_count
            *
            (
                TP1_R * 0.5
                +
                TP2_R * 0.5
            )
            +
            sl_count * -1.0
            +
            tp1_be_count
            *
            TP1_R
            *
            0.5
            +
            time_r
        )


        print(
            f"TP2 + TP1 partial: "
            f"{tp2_count * 1.5:+.2f}R"
        )


        print(
            f"Full SL:           "
            f"{sl_count * -1.0:+.2f}R"
        )


        print(
            f"TP1 -> BE:         "
            f"{tp1_be_count * 0.5:+.2f}R"
        )


        print(
            f"TIME:              "
            f"{time_r:+.2f}R"
        )


        print(
            f"Components total:  "
            f"{component_total:+.2f}R"
        )


        print(
            f"Reported total:    "
            f"{total_r:+.2f}R"
        )


        if abs(
            component_total -
            total_r
        ) < 0.000001:

            print(
                "✓ R accounting is consistent."
            )

        else:

            print(
                "⚠ R accounting mismatch."
            )


    print(
        "=" * 68
    )


    print()

    print(
        "XAUUSD V4 BACKTEST COMPLETE."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
