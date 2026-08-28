"""
sanity_check.py

Verifica la qualita' dei file OHLCV in data/raw/ohlcv/ PRIMA di usarli per
costruire feature o segnali. Controlla:
  - timestamp monotonicamente crescenti e senza duplicati
  - nessun buco di data rispetto alla frequenza attesa del timeframe
  - nessun valore nullo/negativo/zero anomalo
  - coerenza interna OHLC (high >= open/close/low, low <= open/close/high)

Uso:
    python sanity_check.py
    python sanity_check.py --file data/raw/ohlcv/eth_usdt_4h.parquet --timeframe 4h

Filosofia: questo script deve dare esito [OK] su TUTTI i file prima che tu
costruisca anche solo un indicatore sopra questi dati. Un problema qui si
propaga silenziosamente in ogni fase successiva.
"""

import argparse
import glob
import os

import pandas as pd

DATA_DIR = os.path.join("data", "raw", "ohlcv")

# frequenza attesa per pandas.date_range in base al timeframe nel filename
TIMEFRAME_TO_FREQ = {
    "1d": "D",
    "4h": "4h",
    "1h": "h",
}


def infer_timeframe_from_filename(filename: str) -> str:
    for tf in TIMEFRAME_TO_FREQ:
        if filename.endswith(f"_{tf}.parquet"):
            return tf
    return "1d"  # default ragionevole se non riconosciuto


def check_file(filepath: str, timeframe: str, max_tolerable_gap: int = 2) -> tuple:
    """
    Ritorna (critical_issues, warnings).

    max_tolerable_gap: numero massimo di candele mancanti che viene declassato
    a WARN invece di FAIL. Di norma le API degli exchange hanno occasionali
    micro-outage isolati (1-2 candele su migliaia) che non sono un bug della
    pipeline — ma qualunque cosa sopra questa soglia resta un FAIL da
    investigare, perche' un buco esteso spesso indica un vero problema di
    download (sessione interrotta, rate limit non gestito, ecc.).
    """
    issues = []
    warnings = []
    df = pd.read_parquet(filepath)

    if df.empty:
        return ([f"File vuoto: {filepath}"], [])

    required_cols = {"timestamp", "open", "high", "low", "close", "volume"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        return ([f"Colonne mancanti: {missing_cols}"], [])

    # 1. Timestamp monotonico e senza duplicati
    if not df["timestamp"].is_monotonic_increasing:
        issues.append("Timestamp non ordinati in modo crescente")

    n_dup = df["timestamp"].duplicated().sum()
    if n_dup > 0:
        issues.append(f"{n_dup} timestamp duplicati")

    # 2. Buchi nella serie temporale
    freq = TIMEFRAME_TO_FREQ.get(timeframe, "D")
    expected_index = pd.date_range(df["timestamp"].min(), df["timestamp"].max(), freq=freq, tz="UTC")
    missing = expected_index.difference(df["timestamp"])
    if len(missing) > 0:
        preview = ", ".join(str(m) for m in missing[:5])
        suffix = "..." if len(missing) > 5 else ""
        msg = f"{len(missing)} timestamp mancanti (es: {preview}{suffix})"
        if len(missing) <= max_tolerable_gap:
            warnings.append(msg + " -- entro soglia di tolleranza, probabile micro-outage exchange")
        else:
            issues.append(msg)

    # 3. Valori nulli
    n_null = df[["open", "high", "low", "close", "volume"]].isnull().sum().sum()
    if n_null > 0:
        issues.append(f"{n_null} valori nulli nelle colonne OHLCV")

    # 4. Valori di prezzo <= 0 (un prezzo a zero o negativo e' sempre un bug)
    price_cols = ["open", "high", "low", "close"]
    n_bad_price = (df[price_cols] <= 0).sum().sum()
    if n_bad_price > 0:
        issues.append(f"{n_bad_price} valori di prezzo <= 0")

    if (df["volume"] < 0).sum() > 0:
        issues.append("Trovati volumi negativi")

    # 5. Coerenza OHLC interna
    bad_high = df[df["high"] < df[["open", "close", "low"]].max(axis=1)]
    if len(bad_high) > 0:
        issues.append(f"{len(bad_high)} candele con high inferiore a open/close/low")

    bad_low = df[df["low"] > df[["open", "close", "high"]].min(axis=1)]
    if len(bad_low) > 0:
        issues.append(f"{len(bad_low)} candele con low superiore a open/close/high")

    return (issues, warnings)


def main():
    parser = argparse.ArgumentParser(description="Sanity check sui file OHLCV")
    parser.add_argument("--file", default=None, help="Controlla un singolo file invece di tutta la cartella")
    parser.add_argument("--timeframe", default=None, help="Forza il timeframe (1d, 4h, 1h). Ha effetto SOLO insieme a --file: nella scansione di cartella ogni file usa sempre il proprio timeframe dedotto dal nome.")
    parser.add_argument("--max-tolerable-gap", type=int, default=2, help="Numero massimo di candele mancanti trattato come WARN invece di FAIL (default: 2)")
    args = parser.parse_args()

    # IMPORTANTE: --timeframe forza il timeframe solo per un singolo file
    # esplicito (--file). Nella scansione della cartella, ogni file deve
    # sempre usare il proprio timeframe dedotto dal nome file — altrimenti
    # un file 1d controllato con --timeframe 4h genera migliaia di falsi
    # positivi (buchi che non esistono davvero).
    if args.file:
        filepaths = [args.file]
        forced_timeframe = args.timeframe
    else:
        filepaths = sorted(glob.glob(os.path.join(DATA_DIR, "*.parquet")))
        forced_timeframe = None

    if not filepaths:
        print(f"Nessun file trovato in {DATA_DIR}. Esegui prima fetch_ohlcv.py")
        return

    all_ok = True
    any_warnings = False
    for filepath in filepaths:
        filename = os.path.basename(filepath)
        timeframe = forced_timeframe or infer_timeframe_from_filename(filename)

        issues, warnings = check_file(filepath, timeframe, max_tolerable_gap=args.max_tolerable_gap)

        if issues:
            all_ok = False
            print(f"\n[FAIL] {filename} (controllato come {timeframe})")
            for issue in issues:
                print(f"   - {issue}")
        elif warnings:
            any_warnings = True
            print(f"[WARN] {filename} (controllato come {timeframe})")
            for warning in warnings:
                print(f"   - {warning}")
        else:
            print(f"[OK]   {filename}")

    print()
    if not all_ok:
        print("Trovati problemi bloccanti: risolvili prima di costruire qualsiasi feature o segnale sopra questi dati.")
    elif any_warnings:
        print("Nessun problema bloccante. Ci sono WARN documentati (gap isolati sotto soglia) -- puoi procedere,"
              " ma tienine traccia in caso servano in futuro per spiegare eventuali discontinuita' nelle feature.")
    else:
        print("Tutti i controlli superati: puoi procedere con la fase successiva.")


if __name__ == "__main__":
    main()