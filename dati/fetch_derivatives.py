"""
fetch_derivatives.py

Scarica dati derivatives per ETH e BTC perpetual da Binance (via ccxt):
  - funding rate: storico completo, paginato (come fetch_ohlcv.py)
  - open interest: SOLO ultimi ~30 giorni per limite dell'API Binance
    (endpoint /futures/data/openInterestHist). Non e' possibile recuperare
    OI storico oltre questa finestra da Binance direttamente. Per costruire
    storico OI utile nel tempo, questo script va rilanciato periodicamente
    (es. cron giornaliero): ogni run accumula i nuovi dati sopra quelli gia'
    salvati, senza sovrascrivere.

Uso:
    python fetch_derivatives.py
    python fetch_derivatives.py --data-type funding --since 2020-01-01
    python fetch_derivatives.py --data-type oi --oi-period 1h

Simboli: usa la sintassi ccxt per i perpetual USDT-margined, es. 'ETH/USDT:USDT'
(non lo spot 'ETH/USDT').
"""

import argparse
import os
import time
from datetime import datetime, timezone

import ccxt
import pandas as pd

FUNDING_DIR = os.path.join("data", "raw", "derivatives", "funding")
OI_DIR = os.path.join("data", "raw", "derivatives", "open_interest")

DEFAULT_SYMBOLS = ["ETH/USDT:USDT", "BTC/USDT:USDT"]


def symbol_to_filename(symbol: str, suffix: str) -> str:
    """ETH/USDT:USDT, 'funding' -> eth_usdt_funding.parquet"""
    base = symbol.split(":")[0].replace("/", "_").lower()
    return f"{base}_{suffix}.parquet"


def get_resume_since_ms(filepath: str, default_since_ms: int) -> int:
    if not os.path.exists(filepath):
        return default_since_ms
    existing = pd.read_parquet(filepath, columns=["timestamp"])
    if existing.empty:
        return default_since_ms
    return int(existing["timestamp"].max().timestamp() * 1000)


def merge_and_dedup(new_df: pd.DataFrame, filepath: str) -> pd.DataFrame:
    if os.path.exists(filepath):
        old_df = pd.read_parquet(filepath)
        combined = pd.concat([old_df, new_df], ignore_index=True)
    else:
        combined = new_df
    combined = combined.drop_duplicates(subset="timestamp", keep="last")
    combined = combined.sort_values("timestamp").reset_index(drop=True)
    return combined


def fetch_funding_history(exchange, symbol: str, since_ms: int) -> pd.DataFrame:
    """Pagina fetch_funding_rate_history finche' non arriva a 'oggi'."""
    all_rows = []
    limit = 1000
    now_ms = exchange.milliseconds()

    while since_ms < now_ms:
        batch = exchange.fetch_funding_rate_history(symbol, since=since_ms, limit=limit)
        if not batch:
            break

        for entry in batch:
            all_rows.append({
                "timestamp": entry["timestamp"],
                "funding_rate": entry["fundingRate"],
            })

        last_ts = batch[-1]["timestamp"]
        if last_ts is None:
            break
        since_ms = last_ts + 1  # avanza oltre l'ultimo per evitare loop infinito

        time.sleep(exchange.rateLimit / 1000)

        if len(batch) < limit:
            break

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def fetch_oi_history(exchange, symbol: str, period: str) -> pd.DataFrame:
    """
    Scarica open interest storico. Binance espone solo l'ultimo mese circa
    per questo endpoint: non serve paginare all'indietro, la finestra e'
    fissa lato server.
    """
    all_rows = []
    limit = 500

    batch = exchange.fetch_open_interest_history(symbol, timeframe=period, limit=limit)
    for entry in batch:
        all_rows.append({
            "timestamp": entry["timestamp"],
            "open_interest": entry.get("openInterestAmount") or entry.get("openInterestValue"),
        })

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def main():
    parser = argparse.ArgumentParser(description="Scarica funding rate e open interest da Binance via ccxt")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--data-type", choices=["funding", "oi", "both"], default="both")
    parser.add_argument("--since", default="2020-01-01", help="Data di inizio per il funding rate se il file non esiste (YYYY-MM-DD)")
    parser.add_argument("--oi-period", default="1h", help="Risoluzione dell'open interest (5m,15m,30m,1h,2h,4h,6h,12h,1d)")
    args = parser.parse_args()

    exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})

    if args.data_type in ("funding", "both"):
        os.makedirs(FUNDING_DIR, exist_ok=True)
        default_since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        default_since_ms = int(default_since_dt.timestamp() * 1000)

        for symbol in args.symbols:
            filename = symbol_to_filename(symbol, "funding")
            filepath = os.path.join(FUNDING_DIR, filename)

            since_ms = get_resume_since_ms(filepath, default_since_ms)
            print(f"[funding] Scaricando {symbol} da {pd.to_datetime(since_ms, unit='ms', utc=True)}...")

            df = fetch_funding_history(exchange, symbol, since_ms)
            if df.empty:
                print(f"  Nessun dato nuovo per {symbol}.")
                continue

            combined = merge_and_dedup(df, filepath)
            combined.to_parquet(filepath, index=False)
            print(f"  Salvato: {len(combined)} righe totali in {filepath} "
                  f"({combined['timestamp'].min()} -> {combined['timestamp'].max()})")

    if args.data_type in ("oi", "both"):
        os.makedirs(OI_DIR, exist_ok=True)

        for symbol in args.symbols:
            filename = symbol_to_filename(symbol, "oi")
            filepath = os.path.join(OI_DIR, filename)

            print(f"[open interest] Scaricando {symbol} [{args.oi_period}] (ultimi ~30gg disponibili da Binance)...")
            df = fetch_oi_history(exchange, symbol, args.oi_period)
            if df.empty:
                print(f"  Nessun dato ricevuto per {symbol}.")
                continue

            combined = merge_and_dedup(df, filepath)
            combined.to_parquet(filepath, index=False)
            print(f"  Salvato: {len(combined)} righe totali in {filepath} "
                  f"({combined['timestamp'].min()} -> {combined['timestamp'].max()})")
            print("  Nota: per costruire storico OI piu' lungo, rilancia periodicamente (cron giornaliero).")


if __name__ == "__main__":
    main()