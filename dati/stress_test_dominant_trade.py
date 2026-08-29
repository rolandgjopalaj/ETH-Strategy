"""
stress_test_dominant_trade.py

Due verifiche mirate sul trade breakout 2020-10-13 -> 2021-03-01 (+266%),
che nel backtest attuale pesa piu' di tutti gli altri 43 trade messi insieme.

VERIFICA 1 -- Sensibilita' ai parametri di breakout:
  Il trade e' stato catturato con breakout_window=20 giorni e
  breakout_volume_mult=1.3. Se cambiando questi parametri di poco il trade
  sparisce o cambia radicalmente, e' un segnale che la cattura di
  quell'evento e' fragile (quasi-casuale), non un pattern robusto.

VERIFICA 2 -- Ri-simulazione bloccando quell'ingresso specifico:
  Non ci limitiamo a "sottrarre" il suo contributo aritmeticamente --
  blocchiamo quell'ingresso e lasciamo che il resto della simulazione
  prosegua naturalmente (magari il sistema sarebbe comunque rientrato
  poco dopo con un altro segnale). Questo dice onestamente quanto la
  performance totale dipenda da QUEL trade specifico.

Uso:
    python stress_test_dominant_trade.py
"""

import os
from datetime import datetime

import pandas as pd

from backtest import (
    compute_atr, add_entry_signal_columns, run_backtest, compute_metrics, load_data
)

DOMINANT_TRADE_ENTRY = pd.Timestamp("2020-10-13", tz="UTC")
DOMINANT_TRADE_EXIT = pd.Timestamp("2021-03-01", tz="UTC")
# Finestra un po' piu' larga della sola data di ingresso, per bloccare
# qualunque tentativo di entrata nello stesso periodo (non solo il giorno esatto)
BLOCK_WINDOW_START = pd.Timestamp("2020-09-15", tz="UTC")
BLOCK_WINDOW_END = pd.Timestamp("2020-11-15", tz="UTC")

THRESHOLD = 60
ATR_MULT = 3.0
MIN_CATEGORIES = 2
FEE_PCT = 0.001
SLIPPAGE_PCT = 0.001

BREAKOUT_WINDOW_GRID = [10, 15, 20, 25, 30]
BREAKOUT_VOLUME_MULT_GRID = [1.1, 1.3, 1.5, 1.7]


def prepare_base_df():
    """Carica i dati grezzi SENZA le colonne di segnale (le ricalcoliamo per ogni combo)."""
    ohlcv = pd.read_parquet(os.path.join("data", "raw", "ohlcv", "eth_usdt_1d.parquet"))
    ohlcv["date"] = ohlcv["timestamp"].dt.floor("D")
    ohlcv = ohlcv[["date", "open", "high", "low", "close", "volume"]]

    regime = pd.read_parquet(os.path.join("data", "processed", "regime_score_daily.parquet"))[
        ["date", "regime_score", "price_above_ema200", "n_categorie_disponibili"]
    ]
    df = ohlcv.merge(regime, on="date", how="inner").sort_values("date").reset_index(drop=True)
    df["atr_14"] = compute_atr(df, period=14)
    df["bull_regime"] = (
        (df["regime_score"] >= THRESHOLD)
        & (df["price_above_ema200"] == 1)
        & (df["n_categorie_disponibili"] >= MIN_CATEGORIES)
    )
    df = df[df["n_categorie_disponibili"] >= MIN_CATEGORIES].reset_index(drop=True)
    return df


def verifica_1_sensibilita_breakout(base_df: pd.DataFrame):
    print("=" * 70)
    print("VERIFICA 1: sensibilita' del trade dominante ai parametri di breakout")
    print("=" * 70)

    results = []
    for window in BREAKOUT_WINDOW_GRID:
        for vol_mult in BREAKOUT_VOLUME_MULT_GRID:
            df = add_entry_signal_columns(base_df.copy(), breakout_window=window,
                                           breakout_volume_mult=vol_mult)
            trades, equity = run_backtest(df, ATR_MULT, FEE_PCT, SLIPPAGE_PCT)

            # Il trade dominante e' catturato se esiste un trade con
            # entry_date dentro la finestra e return_pct grande (>100%,
            # soglia larga per non perderlo se cambia leggermente durata/ampiezza)
            dominant_captured = False
            dominant_return = None
            if not trades.empty:
                mask = (trades["entry_date"] >= BLOCK_WINDOW_START) & (trades["entry_date"] <= BLOCK_WINDOW_END)
                candidates = trades[mask]
                if not candidates.empty and candidates["return_pct"].max() > 100:
                    dominant_captured = True
                    dominant_return = candidates["return_pct"].max()

            total_return = (equity["equity"].iloc[-1] / equity["equity"].iloc[0] - 1) * 100 if not equity.empty else None

            results.append({
                "breakout_window": window, "breakout_volume_mult": vol_mult,
                "trade_dominante_catturato": dominant_captured,
                "return_trade_dominante_pct": round(dominant_return, 1) if dominant_return else None,
                "total_return_pct": round(total_return, 1) if total_return else None,
                "n_trades": len(trades),
            })

    results_df = pd.DataFrame(results)
    print("\n(baseline attuale: window=20, volume_mult=1.3)\n")
    print(results_df.to_string(index=False))

    pct_captured = results_df["trade_dominante_catturato"].mean() * 100
    print(f"\nIl trade dominante viene catturato in {pct_captured:.0f}% delle combinazioni testate.")
    if pct_captured < 50:
        print("ATTENZIONE: il trade dominante sparisce nella maggioranza delle varianti di parametro --")
        print("e' un segnale di FRAGILITA': la cattura di quell'evento sembra dipendere dalla scelta")
        print("quasi esatta di breakout_window=20/volume_mult=1.3, non da un pattern robusto.")
    else:
        print("Il trade dominante viene catturato nella maggioranza delle varianti -- meno fragile di")
        print("quanto temuto, il pattern di breakout sembra reale e non un artefatto della singola")
        print("combinazione di parametri scelta.")

    return results_df


def verifica_2_rimozione_trade(base_df: pd.DataFrame):
    print("\n" + "=" * 70)
    print("VERIFICA 2: ri-simulazione bloccando l'ingresso del trade dominante")
    print("=" * 70)

    df = add_entry_signal_columns(base_df.copy())  # parametri di default (baseline attuale)

    trades_baseline, equity_baseline = run_backtest(df, ATR_MULT, FEE_PCT, SLIPPAGE_PCT)
    trades_blocked, equity_blocked = run_backtest(
        df, ATR_MULT, FEE_PCT, SLIPPAGE_PCT,
        blocked_entry_start=BLOCK_WINDOW_START, blocked_entry_end=BLOCK_WINDOW_END,
    )

    metrics_baseline = compute_metrics(equity_baseline, trades_baseline, "Baseline (con il trade dominante)")
    metrics_blocked = compute_metrics(equity_blocked, trades_blocked, "Con ingresso bloccato in quella finestra")

    print(f"\nTrade totali baseline: {len(trades_baseline)}  |  con blocco: {len(trades_blocked)}")
    print("\nConfronto metriche:")
    for m in [metrics_baseline, metrics_blocked]:
        print(f"\n{m['label']}:")
        for k, v in m.items():
            if k != "label":
                print(f"  {k:25s} {v}")

    return metrics_baseline, metrics_blocked


def main():
    if not os.path.exists(os.path.join("data", "processed", "regime_score_daily.parquet")):
        print("ERRORE: regime_score_daily.parquet non trovato. Esegui prima l'intera pipeline.")
        return

    base_df = prepare_base_df()

    verifica_1_sensibilita_breakout(base_df)
    verifica_2_rimozione_trade(base_df)

    print("\n" + "=" * 70)
    print("Nota: questo stress test riguarda UN trade specifico identificato come")
    print("dominante. Non sostituisce il permutation test generale ne' la validazione")
    print("cross-asset su BTC, che restano i controlli piu' probanti sulla robustezza")
    print("complessiva della strategia.")


if __name__ == "__main__":
    main()