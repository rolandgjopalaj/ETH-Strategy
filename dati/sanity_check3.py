"""
sanity_check.py

Verifica la qualita' dei dati grezzi (OHLCV, funding rate, open interest)
PRIMA di usarli per costruire feature o segnali.

OHLCV: griglia temporale fissa (1d/4h/1h), buchi isolati sotto soglia -> WARN.
Funding/OI: frequenza non sempre fissa, si confronta ogni intervallo con la
mediana degli intervalli della serie stessa; gap anomali (> 3x la mediana)
sotto soglia -> WARN, sopra -> FAIL.

Uso:
    python sanity_check.py
    python sanity_check.py --file data/raw/ohlcv/eth_usdt_4h.parquet --timeframe 4h
"""

import argparse
import glob
import os

import pandas as pd

OHLCV_DIR = os.path.join("data", "raw", "ohlcv")
FUNDING_DIR = os.path.join("data", "raw", "derivatives", "funding")
OI_DIR = os.path.join("data", "raw", "derivatives", "open_interest")
ONCHAIN_DIR = os.path.join("data", "raw", "onchain")

# Colonna "valore principale" da controllare per ciascun tipo di file on-chain,
# individuata dal pattern nel nome file. Usata solo per il controllo su valori
# negativi/nulli -- i gap si controllano su tutte le colonne numeriche insieme.
ONCHAIN_VALUE_COL_BY_PATTERN = {
    "netflow": "netflow_eth",
    "staking": "net_staking_flow_eth",
    "tvl": "tvl_usd",
}

TIMEFRAME_TO_FREQ = {
    "1d": "D",
    "4h": "4h",
    "1h": "h",
}


def infer_timeframe_from_filename(filename: str) -> str:
    for tf in TIMEFRAME_TO_FREQ:
        if filename.endswith(f"_{tf}.parquet"):
            return tf
    return "1d"


def check_ohlcv_file(filepath: str, timeframe: str, max_tolerable_gap: int = 2) -> tuple:
    """Ritorna (critical_issues, warnings) per un file OHLCV a griglia fissa."""
    issues = []
    warnings = []
    df = pd.read_parquet(filepath)

    if df.empty:
        return ([f"File vuoto: {filepath}"], [])

    required_cols = {"timestamp", "open", "high", "low", "close", "volume"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        return ([f"Colonne mancanti: {missing_cols}"], [])

    if not df["timestamp"].is_monotonic_increasing:
        issues.append("Timestamp non ordinati in modo crescente")

    n_dup = df["timestamp"].duplicated().sum()
    if n_dup > 0:
        issues.append(f"{n_dup} timestamp duplicati")

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

    n_null = df[["open", "high", "low", "close", "volume"]].isnull().sum().sum()
    if n_null > 0:
        issues.append(f"{n_null} valori nulli nelle colonne OHLCV")

    price_cols = ["open", "high", "low", "close"]
    n_bad_price = (df[price_cols] <= 0).sum().sum()
    if n_bad_price > 0:
        issues.append(f"{n_bad_price} valori di prezzo <= 0")

    if (df["volume"] < 0).sum() > 0:
        issues.append("Trovati volumi negativi")

    bad_high = df[df["high"] < df[["open", "close", "low"]].max(axis=1)]
    if len(bad_high) > 0:
        issues.append(f"{len(bad_high)} candele con high inferiore a open/close/low")

    bad_low = df[df["low"] > df[["open", "close", "high"]].min(axis=1)]
    if len(bad_low) > 0:
        issues.append(f"{len(bad_low)} candele con low superiore a open/close/high")

    return (issues, warnings)


def check_irregular_series(filepath: str, value_col: str, allow_negative: bool = False,
                            max_tolerable_gap: int = 2) -> tuple:
    """
    Sanity check per serie a frequenza non rigidamente fissa (funding rate,
    open interest): niente griglia di date attese, si confronta ogni
    intervallo con la mediana degli intervalli osservati nella serie stessa.
    Un gap > 3x la mediana e' quasi sempre un buco reale, non normale
    variabilita' di frequenza.
    """
    issues = []
    warnings = []
    df = pd.read_parquet(filepath)

    if df.empty:
        return ([f"File vuoto: {filepath}"], [])

    required_cols = {"timestamp", value_col}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        return ([f"Colonne mancanti: {missing_cols}"], [])

    if not df["timestamp"].is_monotonic_increasing:
        issues.append("Timestamp non ordinati in modo crescente")

    n_dup = df["timestamp"].duplicated().sum()
    if n_dup > 0:
        issues.append(f"{n_dup} timestamp duplicati")

    n_null = df[value_col].isnull().sum()
    if n_null > 0:
        issues.append(f"{n_null} valori nulli in {value_col}")

    diffs = df["timestamp"].diff().dropna()
    if len(diffs) > 5:
        median_gap = diffs.median()
        anomalous = diffs[diffs > median_gap * 3]
        if len(anomalous) > 0:
            # non basta contare QUANTI buchi ci sono: un singolo buco esteso
            # (es. 10 candele mancanti di fila) produce comunque un solo
            # "salto" nella serie dei diff, ma nasconde molte righe mancanti.
            # Stimiamo il numero di candele effettivamente mancanti dividendo
            # ogni gap anomalo per l'intervallo mediano.
            implied_missing = int((anomalous / median_gap).sum() - len(anomalous))
            msg = (f"{len(anomalous)} gap temporali anomali (> 3x l'intervallo mediano di {median_gap}), "
                   f"corrispondenti a circa {implied_missing} candele mancanti stimate")
            if implied_missing <= max_tolerable_gap:
                warnings.append(msg + " -- entro soglia di tolleranza")
            else:
                issues.append(msg)

    if not allow_negative:
        n_bad = (df[value_col] < 0).sum()
        if n_bad > 0:
            issues.append(f"{n_bad} valori negativi in {value_col} (inatteso per questa serie)")

    return (issues, warnings)


def check_onchain_file(filepath: str, max_tolerable_gap: int = 2) -> tuple:
    """
    Check per file on-chain (netflow, staking, TVL): niente griglia fissa
    (i timestamp Dune/DefiLlama non sono sempre a mezzanotte UTC esatta),
    stessa logica a gap-relativi usata per funding/OI. Valori negativi sono
    permessi ovunque tranne che per il TVL (un TVL negativo e' sempre un bug).
    """
    filename = os.path.basename(filepath).lower()
    value_col = None
    for pattern, col in ONCHAIN_VALUE_COL_BY_PATTERN.items():
        if pattern in filename:
            value_col = col
            break

    if value_col is None:
        # nome non riconosciuto: controlliamo solo struttura/duplicati generici
        df = pd.read_parquet(filepath)
        if df.empty or "timestamp" not in df.columns:
            return ([f"File non riconosciuto o senza colonna timestamp: {filepath}"], [])
        issues = []
        if not df["timestamp"].is_monotonic_increasing:
            issues.append("Timestamp non ordinati in modo crescente")
        n_dup = df["timestamp"].duplicated().sum()
        if n_dup > 0:
            issues.append(f"{n_dup} timestamp duplicati")
        return (issues, ["Nome file non riconosciuto: controllo solo struttura base, non valori"])

    allow_negative = value_col != "tvl_usd"
    return check_irregular_series(filepath, value_col, allow_negative=allow_negative,
                                   max_tolerable_gap=max_tolerable_gap)


def run_check(filepath: str, args) -> tuple:
    """Decide quale check applicare in base al path/nome del file."""
    filename = os.path.basename(filepath)
    if "funding" in filepath:
        return check_irregular_series(filepath, "funding_rate", allow_negative=True,
                                       max_tolerable_gap=args.max_tolerable_gap)
    elif "open_interest" in filepath:
        return check_irregular_series(filepath, "open_interest", allow_negative=False,
                                       max_tolerable_gap=args.max_tolerable_gap)
    elif "onchain" in filepath.replace("\\", "/"):
        return check_onchain_file(filepath, max_tolerable_gap=args.max_tolerable_gap)
    else:
        timeframe = args.timeframe or infer_timeframe_from_filename(filename)
        return check_ohlcv_file(filepath, timeframe, max_tolerable_gap=args.max_tolerable_gap)


def print_result(filename: str, issues: list, warnings: list) -> tuple:
    """Stampa il risultato e ritorna (is_fail, is_warn)."""
    if issues:
        print(f"[FAIL] {filename}")
        for issue in issues:
            print(f"   - {issue}")
        return (True, False)
    elif warnings:
        print(f"[WARN] {filename}")
        for warning in warnings:
            print(f"   - {warning}")
        return (False, True)
    else:
        print(f"[OK]   {filename}")
        return (False, False)


def main():
    parser = argparse.ArgumentParser(description="Sanity check sui dati grezzi (OHLCV, funding, open interest)")
    parser.add_argument("--file", default=None, help="Controlla un singolo file invece di tutte le cartelle")
    parser.add_argument("--timeframe", default=None,
                         help="Forza il timeframe OHLCV (1d,4h,1h). Ha effetto solo insieme a --file su un file OHLCV.")
    parser.add_argument("--max-tolerable-gap", type=int, default=2,
                         help="Numero massimo di gap trattati come WARN invece di FAIL (default: 2)")
    args = parser.parse_args()

    if args.file:
        issues, warnings = run_check(args.file, args)
        print_result(os.path.basename(args.file), issues, warnings)
        return

    sections = [
        ("OHLCV", sorted(glob.glob(os.path.join(OHLCV_DIR, "*.parquet")))),
        ("Funding rate", sorted(glob.glob(os.path.join(FUNDING_DIR, "*.parquet")))),
        ("Open interest", sorted(glob.glob(os.path.join(OI_DIR, "*.parquet")))),
        ("On-chain", sorted(glob.glob(os.path.join(ONCHAIN_DIR, "*.parquet")))),
    ]

    any_files_found = False
    any_fail = False
    any_warn = False

    for section_name, filepaths in sections:
        if not filepaths:
            continue
        any_files_found = True
        print(f"\n== {section_name} ==")
        for filepath in filepaths:
            issues, warnings = run_check(filepath, args)
            is_fail, is_warn = print_result(os.path.basename(filepath), issues, warnings)
            any_fail = any_fail or is_fail
            any_warn = any_warn or is_warn

    if not any_files_found:
        print("Nessun file trovato. Esegui prima fetch_ohlcv.py e/o fetch_derivatives.py")
        return

    print()
    if any_fail:
        print("Trovati problemi bloccanti: risolvili prima di costruire qualsiasi feature o segnale sopra questi dati.")
    elif any_warn:
        print("Nessun problema bloccante. Ci sono WARN documentati -- puoi procedere, ma tienine traccia.")
    else:
        print("Tutti i controlli superati: puoi procedere con la fase successiva.")


if __name__ == "__main__":
    main()