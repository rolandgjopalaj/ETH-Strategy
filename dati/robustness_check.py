"""
robustness_check.py

Verifica se i parametri scelti (soglia regime score=60, ATR multiplier=3.0)
sono robusti o sono overfitting sul singolo backtest. Principio guida (dalla
discussione iniziale): "se EMA50/200 funziona ma EMA48/205 no, e'
overfitting -- un parametro robusto funziona bene anche con variazioni
del +-20%".

Rieseguo lo stesso identico backtest.run_backtest() su una griglia di:
  - soglia regime score: 60 +-20% -> [48, 54, 60, 66, 72]
  - ATR multiplier:       3.0 +-20% -> [2.4, 2.7, 3.0, 3.3, 3.6]
  - min_categorie:        [1, 2, 3] (a parita' degli altri due a baseline)

IMPORTANTE: non sto ottimizzando (cercando la combinazione migliore) --
sto verificando STABILITA'. Se la performance cambia poco spostandosi nel
vicinato, i parametri sono robusti. Se cambia bruscamente, il risultato
originale era probabilmente fortuna statistica su quella combinazione
esatta, non edge reale.

Uso:
    python robustness_check.py
"""

import os

import numpy as np
import pandas as pd

from backtest import compute_atr, add_entry_signal_columns, run_backtest, compute_metrics

REGIME_PATH = os.path.join("data", "processed", "regime_score_daily.parquet")
OHLCV_PATH = os.path.join("data", "raw", "ohlcv", "eth_usdt_1d.parquet")
OUTPUT_PATH = os.path.join("data", "processed", "robustness_grid.parquet")

BASELINE_THRESHOLD = 60
BASELINE_ATR_MULT = 3.0
BASELINE_MIN_CATEGORIES = 2

THRESHOLD_GRID = [48, 54, 60, 66, 72]       # +-20% attorno a 60
ATR_MULT_GRID = [2.4, 2.7, 3.0, 3.3, 3.6]   # +-20% attorno a 3.0
MIN_CATEGORIES_GRID = [1, 2, 3]

FEE_PCT = 0.001
SLIPPAGE_PCT = 0.001


def load_data_for_grid() -> pd.DataFrame:
    """
    Come backtest.load_data(), ma tiene regime_score continuo e
    price_above_ema200 invece del bull_regime gia' fissato a soglia=60 --
    ci serve poterlo ricalcolare per ogni combinazione della griglia.
    """
    regime = pd.read_parquet(REGIME_PATH)[
        ["date", "price_above_ema200", "regime_score", "n_categorie_disponibili"]
    ]
    ohlcv = pd.read_parquet(OHLCV_PATH)
    ohlcv["date"] = ohlcv["timestamp"].dt.floor("D")
    ohlcv = ohlcv[["date", "open", "high", "low", "close", "volume"]]

    df = ohlcv.merge(regime, on="date", how="inner")
    df = df.sort_values("date").reset_index(drop=True)
    df["atr_14"] = compute_atr(df, period=14)
    df = add_entry_signal_columns(df)
    return df


def compute_bull_regime(df: pd.DataFrame, threshold: float, min_categories: int) -> pd.Series:
    return (
        (df["regime_score"] >= threshold)
        & (df["price_above_ema200"] == 1)
        & (df["n_categorie_disponibili"] >= min_categories)
    )


def run_one_combo(df: pd.DataFrame, threshold: float, atr_mult: float, min_categories: int) -> dict:
    df_combo = df[df["n_categorie_disponibili"] >= min_categories].copy().reset_index(drop=True)
    df_combo["bull_regime"] = compute_bull_regime(df_combo, threshold, min_categories)

    trades_df, equity_df = run_backtest(df_combo, atr_mult, FEE_PCT, SLIPPAGE_PCT)

    if equity_df.empty or len(equity_df) < 30:
        return {"threshold": threshold, "atr_mult": atr_mult, "min_categories": min_categories,
                "cagr_pct": np.nan, "max_drawdown_pct": np.nan, "calmar_ratio": np.nan,
                "n_trades": 0, "sharpe_annualized": np.nan}

    metrics = compute_metrics(equity_df, trades_df, label="")
    metrics.pop("label", None)
    metrics.update({"threshold": threshold, "atr_mult": atr_mult, "min_categories": min_categories})
    return metrics


def main():
    if not os.path.exists(REGIME_PATH):
        print(f"ERRORE: {REGIME_PATH} non trovato. Esegui prima regime_score.py")
        return

    print("Caricamento dati...")
    df = load_data_for_grid()

    print(f"\n=== Griglia 1: soglia score x ATR multiplier (min_categorie fisso a {BASELINE_MIN_CATEGORIES}) ===")
    results_grid1 = []
    for threshold in THRESHOLD_GRID:
        for atr_mult in ATR_MULT_GRID:
            r = run_one_combo(df, threshold, atr_mult, BASELINE_MIN_CATEGORIES)
            results_grid1.append(r)
    grid1_df = pd.DataFrame(results_grid1)

    print("\nCalmar ratio (CAGR / |MaxDrawdown|) -- riga=soglia, colonna=ATR multiplier:")
    pivot_calmar = grid1_df.pivot(index="threshold", columns="atr_mult", values="calmar_ratio")
    print(pivot_calmar.round(3).to_string())

    print("\nCAGR %% -- stessa struttura:")
    pivot_cagr = grid1_df.pivot(index="threshold", columns="atr_mult", values="cagr_pct")
    print(pivot_cagr.round(1).to_string())

    print("\nMax Drawdown %% -- stessa struttura:")
    pivot_dd = grid1_df.pivot(index="threshold", columns="atr_mult", values="max_drawdown_pct")
    print(pivot_dd.round(1).to_string())

    print("\nNumero trade -- stessa struttura:")
    pivot_trades = grid1_df.pivot(index="threshold", columns="atr_mult", values="n_trades")
    print(pivot_trades.to_string())

    print(f"\n=== Griglia 2: min_categorie richieste (soglia={BASELINE_THRESHOLD}, ATR={BASELINE_ATR_MULT} fissi) ===")
    results_grid2 = []
    for min_cat in MIN_CATEGORIES_GRID:
        r = run_one_combo(df, BASELINE_THRESHOLD, BASELINE_ATR_MULT, min_cat)
        results_grid2.append(r)
    grid2_df = pd.DataFrame(results_grid2)
    print(grid2_df[["min_categories", "cagr_pct", "max_drawdown_pct", "calmar_ratio", "n_trades"]].to_string(index=False))

    # Salva tutto per ispezione successiva
    all_results = pd.concat([grid1_df, grid2_df], ignore_index=True)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    all_results.to_parquet(OUTPUT_PATH, index=False)

    # --- Valutazione di stabilita' ---
    baseline_row = grid1_df[(grid1_df["threshold"] == BASELINE_THRESHOLD) & (grid1_df["atr_mult"] == BASELINE_ATR_MULT)]
    baseline_calmar = baseline_row["calmar_ratio"].iloc[0]

    calmar_values = grid1_df["calmar_ratio"].dropna()
    calmar_std = calmar_values.std()
    calmar_range = calmar_values.max() - calmar_values.min()
    pct_positive = (calmar_values > 0).mean() * 100

    print("\n" + "=" * 70)
    print("VALUTAZIONE DI STABILITA'")
    print("=" * 70)
    print(f"Calmar ratio alla combinazione scelta (60, 3.0): {baseline_calmar:.3f}")
    print(f"Calmar ratio nella griglia: min={calmar_values.min():.3f}, "
          f"max={calmar_values.max():.3f}, std={calmar_std:.3f}")
    print(f"Percentuale di combinazioni con Calmar positivo: {pct_positive:.0f}%")

    if calmar_std / abs(baseline_calmar) > 0.5:
        print("\nATTENZIONE: alta variabilita' del Calmar ratio nella griglia rispetto al "
              "valore baseline -- il risultato potrebbe dipendere fortemente dalla combinazione "
              "esatta di parametri scelta, segnale di possibile overfitting. Non fidarti ancora "
              "della combinazione (60, 3.0) come 'quella giusta'.")
    else:
        print("\nLa performance resta ragionevolmente stabile nell'intorno dei parametri scelti "
              "-- buon segno, ma questo da solo non basta: serve ancora il permutation test e "
              "la validazione cross-asset su BTC prima di fidarsi.")


if __name__ == "__main__":
    main()