"""
permutation_test.py

Verifica se la strategia batte il "caso puro" in modo statisticamente
significativo, come discusso all'inizio: "shuffla l'ordine [...] o genera
serie sintetiche random con le stesse proprieta' statistiche del prezzo
reale, verifica che la tua strategia batta il caso in modo significativo."

METODO: prendo ogni giorno reale come un blocco indivisibile (rendimento,
range intraday relativo, volume) e ne mescolo casualmente l'ORDINE
temporale. Questo preserva esattamente:
  - la distribuzione dei rendimenti giornalieri
  - il rapporto high/low/open rispetto al close di ogni giorno
  - la distribuzione dei volumi
Ma distrugge completamente:
  - trend, autocorrelazione, struttura temporale reale

Se la strategia ha un vero edge nel catturare trend persistenti, deve
performare MOLTO peggio su queste serie "rimescolate" (dove per
costruzione non esiste alcun trend persistente da catturare) rispetto
alla serie reale.

SCOPE: questo test usa SOLO trend (EMA/ADX) + timing di ingresso
(RSI/breakout) + trailing stop ATR -- NON include funding/on-chain, che
non si possono ricostruire fedelmente su una serie sintetica. E' quindi
un test dello "scheletro" centrale della strategia, non della versione
completa con conferma multi-fattore (che verosimilmente e' solo piu'
prudente, non meno valida, della versione qui testata).

Uso:
    python permutation_test.py
    python permutation_test.py --n-permutations 300
"""

import argparse
import os

import numpy as np
import pandas as pd

from build_features import compute_ema, compute_adx
from backtest import compute_atr, add_entry_signal_columns, run_backtest

OHLCV_PATH = os.path.join("data", "raw", "ohlcv", "eth_usdt_1d.parquet")

ADX_THRESHOLD = 20  # soglia minima di forza del trend per considerare il regime "attivo"
ATR_MULT = 3.0
FEE_PCT = 0.001
SLIPPAGE_PCT = 0.001


def load_real_ohlcv() -> pd.DataFrame:
    df = pd.read_parquet(OHLCV_PATH)
    df["date"] = df["timestamp"].dt.floor("D")
    df = df[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)
    return df


def build_simple_trend_regime(df: pd.DataFrame) -> pd.DataFrame:
    """
    Versione semplificata (solo prezzo) del regime score, usata sia sui dati
    reali che su quelli sintetici per un confronto onesto "stesso identico
    algoritmo, dati diversi".
    """
    out = df.copy()
    out["ema50"] = compute_ema(out["close"], 50)
    out["ema200"] = compute_ema(out["close"], 200)
    out["adx_14"] = compute_adx(out, period=14)
    out["atr_14"] = compute_atr(out, period=14)

    out["bull_regime"] = (
        (out["close"] > out["ema200"])
        & (out["ema50"] > out["ema200"])
        & (out["adx_14"] >= ADX_THRESHOLD)
    )
    out = add_entry_signal_columns(out)
    return out


def run_simple_backtest(df: pd.DataFrame) -> dict:
    df_valid = df.dropna(subset=["ema200", "adx_14"]).reset_index(drop=True)
    trades, equity = run_backtest(df_valid, ATR_MULT, FEE_PCT, SLIPPAGE_PCT)

    if equity.empty or len(equity) < 30:
        return {"total_return_pct": 0.0, "cagr_pct": 0.0, "calmar_ratio": 0.0}

    total_return_pct = (equity["equity"].iloc[-1] / equity["equity"].iloc[0] - 1) * 100
    n_years = (equity["date"].iloc[-1] - equity["date"].iloc[0]).days / 365.25
    cagr_pct = ((equity["equity"].iloc[-1] / equity["equity"].iloc[0]) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0.0

    running_max = equity["equity"].cummax()
    drawdown = (equity["equity"] - running_max) / running_max
    max_dd_pct = drawdown.min() * 100

    calmar = cagr_pct / abs(max_dd_pct) if max_dd_pct != 0 else 0.0

    return {"total_return_pct": total_return_pct, "cagr_pct": cagr_pct, "calmar_ratio": calmar, "n_trades": len(trades)}


def generate_synthetic_ohlcv(real_df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """
    Mescola l'ordine dei "blocchi giornalieri" (rendimento + range relativo +
    volume), poi ricostruisce una serie di prezzo sintetica concatenando i
    rendimenti nel nuovo ordine casuale. Il primo giorno resta fisso come
    ancora (serve un punto di partenza).
    """
    df = real_df.copy()
    log_ret = np.log(df["close"] / df["close"].shift(1))
    rel_high = df["high"] / df["close"]
    rel_low = df["low"] / df["close"]
    rel_open = df["open"] / df["close"]
    volume = df["volume"].values

    n = len(df)
    # Permuta gli indici 1..n-1 (l'indice 0 non ha un rendimento definito,
    # resta com'e' come punto di ancoraggio della serie sintetica)
    perm_idx = rng.permutation(np.arange(1, n))

    synthetic_log_ret = log_ret.values[perm_idx]
    synthetic_rel_high = rel_high.values[perm_idx]
    synthetic_rel_low = rel_low.values[perm_idx]
    synthetic_rel_open = rel_open.values[perm_idx]
    synthetic_volume = volume[perm_idx]

    synthetic_close = np.empty(n)
    synthetic_close[0] = df["close"].iloc[0]
    synthetic_close[1:] = synthetic_close[0] * np.exp(np.cumsum(synthetic_log_ret))

    synthetic_df = pd.DataFrame({
        "date": df["date"],
        "close": synthetic_close,
        "high": np.concatenate([[df["high"].iloc[0]], synthetic_close[1:] * synthetic_rel_high]),
        "low": np.concatenate([[df["low"].iloc[0]], synthetic_close[1:] * synthetic_rel_low]),
        "open": np.concatenate([[df["open"].iloc[0]], synthetic_close[1:] * synthetic_rel_open]),
        "volume": np.concatenate([[df["volume"].iloc[0]], synthetic_volume]),
    })
    return synthetic_df


def main():
    parser = argparse.ArgumentParser(description="Permutation test sulla logica di trend-following")
    parser.add_argument("--n-permutations", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if not os.path.exists(OHLCV_PATH):
        print(f"ERRORE: {OHLCV_PATH} non trovato.")
        return

    print("Caricamento dati reali ETH...")
    real_df = load_real_ohlcv()

    print("Calcolo performance sui dati REALI (solo trend+ADX+RSI/breakout, no funding/on-chain)...")
    real_features = build_simple_trend_regime(real_df)
    real_metrics = run_simple_backtest(real_features)
    print(f"  Total return reale: {real_metrics['total_return_pct']:.1f}%")
    print(f"  CAGR reale: {real_metrics['cagr_pct']:.1f}%")
    print(f"  Calmar reale: {real_metrics['calmar_ratio']:.3f}")
    print(f"  N trade reali: {real_metrics['n_trades']}")

    print(f"\nGenerazione e test di {args.n_permutations} serie sintetiche (stesso identico algoritmo)...")
    rng = np.random.default_rng(args.seed)
    synthetic_results = []
    for i in range(args.n_permutations):
        synthetic_df = generate_synthetic_ohlcv(real_df, rng)
        synthetic_features = build_simple_trend_regime(synthetic_df)
        metrics = run_simple_backtest(synthetic_features)
        synthetic_results.append(metrics)
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{args.n_permutations} completate...")

    synthetic_df_results = pd.DataFrame(synthetic_results)

    print("\n" + "=" * 70)
    print("RISULTATI PERMUTATION TEST")
    print("=" * 70)

    for metric in ["total_return_pct", "cagr_pct", "calmar_ratio"]:
        real_value = real_metrics[metric]
        synthetic_values = synthetic_df_results[metric]
        percentile = (synthetic_values < real_value).mean() * 100
        p_value = (synthetic_values >= real_value).mean()

        print(f"\n{metric}:")
        print(f"  Reale: {real_value:.2f}")
        print(f"  Sintetico: media={synthetic_values.mean():.2f}, std={synthetic_values.std():.2f}, "
              f"min={synthetic_values.min():.2f}, max={synthetic_values.max():.2f}")
        print(f"  Percentile del risultato reale nella distribuzione sintetica: {percentile:.1f}%")
        print(f"  p-value (probabilita' che il caso puro faccia meglio o uguale): {p_value:.3f}")

    print("\n" + "=" * 70)
    calmar_p = (synthetic_df_results["calmar_ratio"] >= real_metrics["calmar_ratio"]).mean()
    if calmar_p < 0.05:
        print(f"RISULTATO: il Calmar reale batte il {100*(1-calmar_p):.0f}% delle serie sintetiche "
              f"(p={calmar_p:.3f} < 0.05). Evidenza statistica che la strategia sfrutta trend reali,")
        print("non solo le proprieta' statistiche generiche del prezzo di ETH.")
    else:
        print(f"ATTENZIONE: il Calmar reale NON batte il caso in modo statisticamente significativo")
        print(f"(p={calmar_p:.3f} >= 0.05). Una parte sostanziale della performance osservata potrebbe")
        print("essere spiegata dalle sole proprieta' statistiche generiche del prezzo (volatilita',")
        print("distribuzione dei rendimenti), non da un vero pattern di trend catturato dal sistema.")

    print("\nNota: questo test riguarda lo scheletro trend+ADX+RSI/breakout, non la versione")
    print("completa con funding/on-chain, che nella pratica e' solo un filtro aggiuntivo piu'")
    print("prudente sopra questa stessa logica di base.")


if __name__ == "__main__":
    main()