"""
fetch_defillama.py

Scarica TVL storico giornaliero per le principali L2 Ethereum (Arbitrum,
Base, Optimism) da DefiLlama. Nessuna API key richiesta.

Uso:
    python fetch_defillama.py
    python fetch_defillama.py --chains arbitrum base optimism

Output: data/raw/onchain/l2_tvl_<chain>.parquet
"""

import argparse
import os

import pandas as pd
import requests

OUTPUT_DIR = os.path.join("data", "raw", "onchain")
DEFAULT_CHAINS = ["arbitrum", "base", "optimism"]

BASE_URL = "https://api.llama.fi/v2/historicalChainTvl/{chain}"


def fetch_chain_tvl(chain: str) -> pd.DataFrame:
    """
    L'endpoint ritorna una lista di {date: unix_seconds, tvl: float}.
    Nessuna paginazione richiesta: DefiLlama ritorna sempre tutto lo storico
    disponibile in un'unica risposta.
    """
    url = BASE_URL.format(chain=chain)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    df = pd.DataFrame(data)
    if df.empty:
        return df

    df["timestamp"] = pd.to_datetime(df["date"], unit="s", utc=True)
    df = df.rename(columns={"tvl": "tvl_usd"})
    df = df[["timestamp", "tvl_usd"]].sort_values("timestamp").reset_index(drop=True)
    return df


def main():
    parser = argparse.ArgumentParser(description="Scarica TVL storico L2 da DefiLlama")
    parser.add_argument("--chains", nargs="+", default=DEFAULT_CHAINS,
                         help="Nomi chain DefiLlama (es. arbitrum, base, optimism, zksync)")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for chain in args.chains:
        print(f"Scaricando TVL storico per {chain}...")
        try:
            df = fetch_chain_tvl(chain)
        except requests.RequestException as e:
            print(f"  Errore nel contattare DefiLlama per {chain}: {e}")
            continue

        if df.empty:
            print(f"  Nessun dato ricevuto per '{chain}' -- controlla che il nome chain sia corretto "
                  f"(vedi https://defillama.com/chains per i nomi esatti usati da DefiLlama)")
            continue

        filepath = os.path.join(OUTPUT_DIR, f"l2_tvl_{chain}.parquet")
        df.to_parquet(filepath, index=False)
        print(f"  Salvato: {len(df)} righe in {filepath} "
              f"({df['timestamp'].min()} -> {df['timestamp'].max()})")


if __name__ == "__main__":
    main()