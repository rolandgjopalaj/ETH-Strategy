"""
fetch_defillama.py

Scarica TVL storico giornaliero per le principali L2 Ethereum (Arbitrum,
Base, Optimism) da DefiLlama. Nessuna API key richiesta.

Uso:
    python fetch_defillama.py
    python fetch_defillama.py --chains arbitrum base optimism

Output: data/raw/onchain/l2_tvl_<chain>.parquet

Note:
- DefiLlama non supporta paginazione/since: l'endpoint ritorna sempre tutto
  lo storico disponibile in un'unica risposta. Per questo il file esistente
  viene unito (merge + dedup su timestamp) al nuovo fetch invece di essere
  sovrascritto, cosi' rieseguire lo script periodicamente e' sicuro come per
  fetch_ohlcv.py e fetch_derivatives.py.
- Le righe con tvl_usd <= 0 (periodo pre-lancio della chain, es. Arbitrum
  fino a meta' agosto 2021) sono osservazioni reali e non un buco di dati,
  ma non sono utili per il regime score a valle: vengono filtrate qui ad
  ogni run, quindi non serve piu' un cleanup manuale separato dopo il fetch.
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

        n_before = len(df)
        df = df[df["tvl_usd"] > 0].reset_index(drop=True)
        n_dropped = n_before - len(df)

        filepath = os.path.join(OUTPUT_DIR, f"l2_tvl_{chain}.parquet")
        combined = merge_and_dedup(df, filepath)
        combined.to_parquet(filepath, index=False)

        drop_note = f", {n_dropped} righe pre-lancio (tvl_usd<=0) scartate" if n_dropped > 0 else ""
        print(f"  Salvato: {len(combined)} righe totali in {filepath} "
              f"({combined['timestamp'].min()} -> {combined['timestamp'].max()}){drop_note}")


if __name__ == "__main__":
    main()