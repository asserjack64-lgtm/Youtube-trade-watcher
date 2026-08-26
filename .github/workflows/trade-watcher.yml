name: Trade Watcher

on:
  workflow_dispatch:

jobs:
  watch:
    runs-on: ubuntu-latest
    timeout-minutes: 350

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install yt-dlp requests

      - name: Start Trade Watcher
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          python trade_watcher.py
