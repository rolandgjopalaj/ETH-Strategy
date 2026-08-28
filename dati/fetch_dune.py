"""
fetch_dune.py

Esegue query Dune Analytics gia' salvate (via query_id) e salva il
risultato in Parquet. Generico: funziona per qualsiasi query salvata
(gas fee, netflow, staking flow, ecc.), basta passare il query_id.

Richiede la variabile d'ambiente DUNE_API_KEY.

Uso:
    export DUNE_API_KEY="la_tua_chiave"
    python fetch_dune.py --query-id 3456789 --name gas_fee_daily
    python fetch_dune.py --query-id 1234567 --name eth_netflow_daily
"""

import argparse
import os
import sys
import time

import pandas as pd
import requests

OUTPUT_DIR = os.path.join("data", "raw", "onchain")
BASE_URL = "https://api.dune.com/api/v1"


def get_api_key() -> str:
    key = os.environ.get("DUNE_API_KEY")
    if not key:
        print("ERRORE: variabile d'ambiente DUNE_API_KEY non impostata.")
        print('Impostala con: export DUNE_API_KEY="la_tua_chiave"')
        sys.exit(1)
    return key


def execute_query(query_id: int, api_key: str) -> str:
    """Avvia l'esecuzione della query salvata. Ritorna execution_id."""
    resp = requests.post(
        f"{BASE_URL}/query/{query_id}/execute",
        headers={"X-Dune-API-Key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["execution_id"]


def wait_for_results(execution_id: str, api_key: str, poll_seconds: int = 3, timeout_seconds: int = 300) -> dict:
    """Polling dello stato dell'esecuzione finche' non e' completa."""
    elapsed = 0
    while elapsed < timeout_seconds:
        resp = requests.get(
            f"{BASE_URL}/execution/{execution_id}/status",
            headers={"X-Dune-API-Key": api_key},
            timeout=30,
        )
        resp.raise_for_status()
        status = resp.json()["state"]

        if status == "QUERY_STATE_COMPLETED":
            results_resp = requests.get(
                f"{BASE_URL}/execution/{execution_id}/results",
                headers={"X-Dune-API-Key": api_key},
                timeout=60,
            )
            results_resp.raise_for_status()
            return results_resp.json()

        if status in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
            raise RuntimeError(f"Esecuzione Dune fallita con stato: {status}")

        time.sleep(poll_seconds)
        elapsed += poll_seconds

    raise TimeoutError(f"Timeout dopo {timeout_seconds}s in attesa dei risultati Dune")


def main():
    parser = argparse.ArgumentParser(description="Esegue una query Dune salvata e scarica i risultati")
    parser.add_argument("--query-id", type=int, required=True, help="ID della query salvata su dune.com")
    parser.add_argument("--name", required=True, help="Nome descrittivo usato per il file di output (es. gas_fee_daily)")
    args = parser.parse_args()

    api_key = get_api_key()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Eseguendo query Dune {args.query_id}...")
    execution_id = execute_query(args.query_id, api_key)

    print("In attesa dei risultati (puo' richiedere qualche secondo/minuto)...")
    results = wait_for_results(execution_id, api_key)

    rows = results.get("result", {}).get("rows", [])
    if not rows:
        print("Nessuna riga ricevuta -- controlla la query su dune.com")
        return

    df = pd.DataFrame(rows)

    # Le query Dune di solito hanno una colonna data/giorno con nomi diversi
    # (day, date, block_date...): la rinominiamo a 'timestamp' se la troviamo,
    # altrimenti lasciamo le colonne cosi' come sono e avvisiamo.
    date_col_candidates = [c for c in df.columns if c.lower() in ("day", "date", "block_date", "timestamp")]
    if date_col_candidates:
        df = df.rename(columns={date_col_candidates[0]: "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
    else:
        print("ATTENZIONE: nessuna colonna data riconosciuta automaticamente -- controlla manualmente le colonne:")
        print(list(df.columns))

    filepath = os.path.join(OUTPUT_DIR, f"{args.name}.parquet")
    df.to_parquet(filepath, index=False)
    print(f"Salvato: {len(df)} righe in {filepath}")


if __name__ == "__main__":
    main()