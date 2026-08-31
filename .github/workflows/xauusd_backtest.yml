name: XAUUSD V4 Backtest

on:
  workflow_dispatch:
    inputs:
      days:
        description: "Number of calendar days to backtest"
        required: true
        default: "180"

      start:
        description: "Optional start date YYYY-MM-DD"
        required: false
        default: ""

      end:
        description: "Optional end date YYYY-MM-DD"
        required: false
        default: ""

      no_robustness:
        description: "Skip robustness test"
        required: false
        type: boolean
        default: false


jobs:

  backtest:

    runs-on: ubuntu-latest

    steps:

      # ======================================================
      # CHECKOUT
      # ======================================================

      - name: Checkout repository
        uses: actions/checkout@v4


      # ======================================================
      # PYTHON
      # ======================================================

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"


      # ======================================================
      # DEPENDENCIES
      # ======================================================

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install pandas requests


      # ======================================================
      # RUN XAUUSD V4 BACKTEST
      # ======================================================

      - name: Run XAUUSD V4 backtest
        run: |
          if [ -n "${{ inputs.start }}" ] && [ -n "${{ inputs.end }}" ]; then
            python xauusd_backtest.py \
              --start "${{ inputs.start }}" \
              --end "${{ inputs.end }}"
          elif [ "${{ inputs.no_robustness }}" = "true" ]; then
            python xauusd_backtest.py \
              --days "${{ inputs.days }}" \
              --no-robustness
          else
            python xauusd_backtest.py \
              --days "${{ inputs.days }}"
          fi


      # ======================================================
      # UPLOAD RESULTS
      # ======================================================

      - name: Upload XAUUSD V4 results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: xauusd-v4-backtest-results
          path: |
            xauusd_v4_trades.csv
            xauusd_v4_robustness.csv
            xauusd_v4_data.csv
          if-no-files-found: ignore
