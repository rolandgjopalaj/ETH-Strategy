"""
fetch_ohlcv.py

Scarica OHLCV daily (o altro timeframe) storico per ETH/USDT e BTC/USDT da
Binance (via ccxt), paginando le richieste per coprire tutto lo storico
richiesto, e salva i dati in Parquet con dedup/merge sui timestamp esistenti.

Uso:
    python fetch_ohlcv.py
    python fetch_ohlcv.py --symbols ETH/USDT BTC/USDT --timeframe 1d --since 2018-01-01
    python fetch_ohlcv.py --symbols ETH/USDT --timeframe 4h --since 2022-01-01

Note:
- Rieseguibile senza problemi: se il file esiste già, scarica solo da dove
  si era fermato l'ultima volta e fa merge/dedup, non riscarica da zero.
- Salva sempre in data/raw/ohlcv/<symbol>_<timeframe>.parquet
"""

import argparse
import os
import time
from datetime import datetime, timezone

import ccxt
import pandas as pd

DATA_DIR = os.path.join("data", "raw", "ohlcv")


def symbol_to_filename(symbol: str, timeframe: str) -> str:
    """ETH/USDT, 1d -> eth_usdt_1d.parquet"""
    base = symbol.replace("/", "_").lower()
    return f"{base}_{timeframe}.parquet"


def get_resume_since_ms(filepath: str, default_since_ms: int) -> int:
    """
    Se il file esiste già, riparte dall'ultimo timestamp salvato (per non
    riscaricare tutto lo storico ogni volta). Altrimenti usa il default.
    """
    if not os.path.exists(filepath):
        return default_since_ms

    existing = pd.read_parquet(filepath, columns=["timestamp"])
    if existing.empty:
        return default_since_ms

    last_ts = existing["timestamp"].max()
    return int(last_ts.timestamp() * 1000)


def fetch_full_history(exchange, symbol: str, timeframe: str, since_ms: int) -> pd.DataFrame:
    """
    Scarica tutto lo storico OHLCV paginando sul timestamp.
    Binance limita a ~1000 candele per chiamata: continuiamo finché
    non arriviamo a 'oggi' o l'exchange smette di restituire dati.
    """
    all_rows = []
    limit = 1000
    now_ms = exchange.milliseconds()
    step_ms = exchange.parse_timeframe(timeframe) * 1000

    while since_ms < now_ms:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=limit)
        if not batch:
            break

        all_rows.extend(batch)

        last_ts = batch[-1][0]
        # avanza oltre l'ultima candela ricevuta, altrimenti loop infinito
        since_ms = last_ts + step_ms

        # rispetta i rate limit dell'exchange (obbligatorio, non opzionale)
        time.sleep(exchange.rateLimit / 1000)

        if len(batch) < limit:
            break  # ultima pagina raggiunta

    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def merge_and_dedup(new_df: pd.DataFrame, filepath: str) -> pd.DataFrame:
    """Unisce ai dati esistenti (se presenti) e rimuove duplicati su timestamp."""
    if os.path.exists(filepath):
        old_df = pd.read_parquet(filepath)
        combined = pd.concat([old_df, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = combined.drop_duplicates(subset="timestamp", keep="last")
    combined = combined.sort_values("timestamp").reset_index(drop=True)
    return combined


def main():
    parser = argparse.ArgumentParser(description="Scarica OHLCV storico da Binance via ccxt")
    parser.add_argument("--symbols", nargs="+", default=["ETH/USDT", "BTC/USDT"])
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument("--since", default="2018-01-01", help="Data di inizio se il file non esiste ancora (YYYY-MM-DD)")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    exchange = ccxt.binance({"enableRateLimit": True})
    default_since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    default_since_ms = int(default_since_dt.timestamp() * 1000)

    for symbol in args.symbols:
        filename = symbol_to_filename(symbol, args.timeframe)
        filepath = os.path.join(DATA_DIR, filename)

        since_ms = get_resume_since_ms(filepath, default_since_ms)
        since_label = pd.to_datetime(since_ms, unit="ms", utc=True)

        print(f"Scaricando {symbol} [{args.timeframe}] da {since_label}...")
        df = fetch_full_history(exchange, symbol, args.timeframe, since_ms)

        if df.empty:
            print(f"  Nessun dato nuovo per {symbol}.")
            continue

        combined = merge_and_dedup(df, filepath)
        combined.to_parquet(filepath, index=False)

        print(f"  Salvato: {len(combined)} candele totali in {filepath} "
              f"({combined['timestamp'].min()} -> {combined['timestamp'].max()})")


if __name__ == "__main__":
    main()