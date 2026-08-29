"""
backtest.py

Prima versione del backtest: regole di ingresso/uscita SEMPLICI sopra il
regime_score gia' calcolato, con costi ed esecuzione realistici.

Regole (v1, deliberatamente semplice -- pullback/breakout di timing fine
sono un raffinamento successivo, ha senso aggiungerli solo dopo aver
validato che la logica di base regge):

  ENTRATA: bull_regime passa da False a True (nuovo regime confermato)
  USCITA:  bull_regime torna False, OPPURE trailing stop ATR-based colpito
           (chiusura sotto: massimo dei close da entrata in poi - N*ATR)

REGOLE DI ESECUZIONE REALISTICA (fondamentali, non dettagli):
  - Il segnale si basa sul CLOSE del giorno T (unica informazione disponibile
    a fine giornata T).
  - L'esecuzione avviene all'OPEN del giorno T+1 (mai lo stesso giorno del
    segnale: nella realta' non puoi eseguire istantaneamente al prezzo di
    chiusura, e tu esegui manualmente, quindi c'e' anche un ritardo umano).
  - Si applicano fee (taker Binance) + slippage stimato per esecuzione manuale.

Uso:
    python backtest.py
    python backtest.py --atr-multiplier 3.0 --fee-pct 0.001 --slippage-pct 0.001

Output:
  data/processed/backtest_trades.parquet   (log di ogni trade)
  data/processed/backtest_equity.parquet   (equity curve giorno per giorno)
  Report a schermo con metriche vs buy-and-hold
"""

import argparse
import os

import numpy as np
import pandas as pd

FEATURES_PATH = os.path.join("data", "processed", "regime_features_daily.parquet")
REGIME_PATH = os.path.join("data", "processed", "regime_score_daily.parquet")
TRADES_OUTPUT = os.path.join("data", "processed", "backtest_trades.parquet")
EQUITY_OUTPUT = os.path.join("data", "processed", "backtest_equity.parquet")


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR causale (ewm guarda solo indietro)."""
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI(14) standard, metodo di Wilder. Causale (ewm guarda solo indietro)."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    # Caso limite: avg_loss=0 -> nessuna perdita nella finestra recente.
    # Se c'e' stato comunque un minimo di guadagno, il momentum e' puro
    # rialzo (RSI=100); se anche il guadagno e' zero (serie piatta), il RSI
    # e' convenzionalmente 50 (nessun movimento in nessuna direzione).
    rsi = rsi.where(avg_loss != 0, np.where(avg_gain > 0, 100.0, 50.0))
    return rsi


def add_entry_signal_columns(df: pd.DataFrame, pullback_rsi_oversold: float = 40,
                              pullback_rsi_recovery: float = 45, breakout_window: int = 20,
                              breakout_volume_mult: float = 1.3) -> pd.DataFrame:
    """
    Calcola le colonne per il layer di ingresso "micro" (dentro al regime
    macro gia' confermato):

      pullback_signal:  RSI era in ipervenduto-in-trend (sotto
                         pullback_rsi_oversold il giorno prima) e ora sta
                         rimbalzando (torna sopra pullback_rsi_recovery),
                         con il prezzo ancora sopra EMA50 (il trend di fondo
                         non si e' rotto, e' solo un pullback fisiologico).

      breakout_signal:   il prezzo chiude sopra il massimo delle precedenti
                         breakout_window candele (ESCLUSA quella di oggi,
                         altrimenti sarebbe banalmente sempre vero) con
                         volume sopra breakout_volume_mult x la media dei
                         volumi delle stesse candele precedenti.

    Serve principalmente per il RIENTRO dopo essere stati fermati da un
    trailing stop mentre il regime macro e' ancora bull -- senza questo, il
    sistema resta fuori dal mercato fino al reset completo del regime anche
    se il trend prosegue forte dopo lo stop (vedi note di robustness check).
    """
    out = df.copy()
    out["rsi_14"] = compute_rsi(out["close"], period=14)
    out["ema50"] = out["close"].ewm(span=50, adjust=False).mean()

    rsi_was_oversold = out["rsi_14"].shift(1) < pullback_rsi_oversold
    rsi_recovering = out["rsi_14"] >= pullback_rsi_recovery
    trend_intact = out["close"] > out["ema50"]
    out["pullback_signal"] = rsi_was_oversold & rsi_recovering & trend_intact

    # shift(1) per escludere la candela di oggi dal proprio stesso massimo/media
    rolling_high = out["high"].rolling(breakout_window).max().shift(1)
    volume_avg = out["volume"].rolling(breakout_window).mean().shift(1)
    out["breakout_signal"] = (out["close"] > rolling_high) & (out["volume"] > breakout_volume_mult * volume_avg)

    out["pullback_signal"] = out["pullback_signal"].fillna(False)
    out["breakout_signal"] = out["breakout_signal"].fillna(False)
    return out


def load_data(symbol: str = "eth") -> pd.DataFrame:
    suffix = "" if symbol == "eth" else f"_{symbol}"
    features_path = os.path.join("data", "processed", f"regime_features_daily{suffix}.parquet")
    regime_path = os.path.join("data", "processed", f"regime_score_daily{suffix}.parquet")

    features = pd.read_parquet(features_path)
    regime = pd.read_parquet(regime_path)[["date", "regime_score", "bull_regime", "n_categorie_disponibili"]]

    # Serve anche 'high'/'low'/'volume' che regime_features_daily non porta
    # a valle -- le ricarichiamo dal grezzo OHLCV per ATR/RSI/breakout.
    ohlcv = pd.read_parquet(os.path.join("data", "raw", "ohlcv", f"{symbol}_usdt_1d.parquet"))
    ohlcv["date"] = ohlcv["timestamp"].dt.floor("D")
    ohlcv = ohlcv[["date", "open", "high", "low", "close", "volume"]]

    df = ohlcv.merge(regime, on="date", how="inner")
    df = df.sort_values("date").reset_index(drop=True)
    df["atr_14"] = compute_atr(df, period=14)
    df = add_entry_signal_columns(df)
    return df


def run_backtest(df: pd.DataFrame, atr_multiplier: float, fee_pct: float, slippage_pct: float,
                  initial_capital: float = 10000.0, blocked_entry_start=None, blocked_entry_end=None) -> tuple:
    """
    Simulazione giorno per giorno. Ritorna (trades_df, equity_df).

    blocked_entry_start/end: se forniti, nessuna NUOVA entrata viene aperta
    con entry_date in quella finestra (usato per stress test: "cosa sarebbe
    successo se quel trade specifico non fosse mai stato aperto", lasciando
    che il resto della simulazione prosegua naturalmente da li' in poi).

    IMPORTANTE sul no-look-ahead: il segnale di entrata/uscita del giorno T
    viene DECISO usando dati fino al close di T incluso, ma ESEGUITO
    all'open di T+1. Il ciclo scorre i giorni in ordine e non guarda mai
    avanti nel tempo per decidere un segnale.
    """
    trades = []
    equity_curve = []

    in_position = False
    entry_price = None
    entry_date = None
    current_entry_reason = None
    highest_close_since_entry = None
    cash = initial_capital
    units = 0.0

    n = len(df)
    for i in range(n - 1):  # -1 perche' l'esecuzione avviene il giorno dopo
        row = df.iloc[i]
        next_row = df.iloc[i + 1]

        # --- Gestione posizione aperta: check trailing stop e regime ---
        if in_position:
            highest_close_since_entry = max(highest_close_since_entry, row["close"])
            trailing_stop_level = highest_close_since_entry - atr_multiplier * row["atr_14"]

            exit_signal = (not row["bull_regime"]) or (row["close"] < trailing_stop_level)

            if exit_signal:
                exit_price_raw = next_row["open"]
                exit_price = exit_price_raw * (1 - slippage_pct)  # slippage sfavorevole in vendita
                proceeds = units * exit_price * (1 - fee_pct)

                trade_return_pct = (exit_price / entry_price - 1) * 100
                trades.append({
                    "entry_date": entry_date, "exit_date": next_row["date"],
                    "entry_price": entry_price, "exit_price": exit_price,
                    "return_pct": trade_return_pct,
                    "exit_reason": "regime_break" if not row["bull_regime"] else "trailing_stop",
                    "entry_reason": current_entry_reason,
                })

                cash = proceeds
                units = 0.0
                in_position = False
                entry_price = None
                highest_close_since_entry = None

        # --- Check nuova entrata (solo se non gia' in posizione) ---
        elif row["bull_regime"]:
            is_fresh_regime_transition = (i == 0) or (not df.iloc[i - 1]["bull_regime"])
            # Su una transizione fresca del regime, entra subito (e' la
            # conferma macro stessa il segnale). Se invece il regime era
            # gia' attivo (es. sei stato fermato da un trailing stop mentre
            # il trend proseguiva), serve un segnale di rientro piu' fine:
            # pullback con RSI in recupero, oppure breakout con volume.
            entry_signal = is_fresh_regime_transition or row["pullback_signal"] or row["breakout_signal"]

            if blocked_entry_start is not None and blocked_entry_start <= next_row["date"] <= blocked_entry_end:
                entry_signal = False

            if entry_signal:
                entry_price_raw = next_row["open"]
                entry_price = entry_price_raw * (1 + slippage_pct)  # slippage sfavorevole in acquisto
                units = (cash * (1 - fee_pct)) / entry_price
                cash = 0.0
                in_position = True
                entry_date = next_row["date"]
                highest_close_since_entry = entry_price
                current_entry_reason = ("regime_transition" if is_fresh_regime_transition
                                         else ("pullback" if row["pullback_signal"] else "breakout"))

        # --- Mark-to-market equity di fine giornata (usando il close di oggi) ---
        current_equity = cash + units * row["close"]
        equity_curve.append({"date": row["date"], "equity": current_equity, "in_position": in_position})

    # Se una posizione e' ancora aperta all'ultimo giorno disponibile, la
    # registriamo comunque nel trade log (mark-to-market, non un'uscita
    # reale) -- altrimenti l'equity curve include il suo contributo ma
    # win_rate/profit_factor lo escluderebbero, disallineando le due
    # visioni della stessa simulazione.
    if in_position:
        last_row = df.iloc[n - 1]
        open_value_price = last_row["close"]
        trade_return_pct = (open_value_price / entry_price - 1) * 100
        trades.append({
            "entry_date": entry_date, "exit_date": last_row["date"],
            "entry_price": entry_price, "exit_price": open_value_price,
            "return_pct": trade_return_pct,
            "exit_reason": "still_open_at_backtest_end",
            "entry_reason": current_entry_reason,
        })

    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity_curve)
    return trades_df, equity_df


def compute_metrics(equity_df: pd.DataFrame, trades_df: pd.DataFrame, label: str) -> dict:
    equity = equity_df["equity"]
    returns = equity.pct_change().dropna()

    total_return_pct = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    n_years = (equity_df["date"].iloc[-1] - equity_df["date"].iloc[0]).days / 365.25
    cagr_pct = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / n_years) - 1) * 100 if n_years > 0 else np.nan

    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    max_drawdown_pct = drawdown.min() * 100

    sharpe = (returns.mean() / returns.std() * np.sqrt(365)) if returns.std() > 0 else np.nan
    calmar = (cagr_pct / abs(max_drawdown_pct)) if max_drawdown_pct != 0 else np.nan

    metrics = {
        "label": label,
        "total_return_pct": round(total_return_pct, 1),
        "cagr_pct": round(cagr_pct, 1),
        "max_drawdown_pct": round(max_drawdown_pct, 1),
        "sharpe_annualized": round(sharpe, 2),
        "calmar_ratio": round(calmar, 3),
    }

    if trades_df is not None and not trades_df.empty:
        wins = trades_df[trades_df["return_pct"] > 0]
        losses = trades_df[trades_df["return_pct"] <= 0]
        win_rate = len(wins) / len(trades_df) * 100
        profit_factor = (wins["return_pct"].sum() / abs(losses["return_pct"].sum())
                          if len(losses) > 0 and losses["return_pct"].sum() != 0 else np.nan)
        metrics.update({
            "n_trades": len(trades_df),
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2) if pd.notna(profit_factor) else None,
            "avg_trade_return_pct": round(trades_df["return_pct"].mean(), 2),
        })

    return metrics


def main():
    parser = argparse.ArgumentParser(description="Backtest regime-following con trailing stop ATR")
    parser.add_argument("--atr-multiplier", type=float, default=3.0,
                         help="Moltiplicatore ATR per il trailing stop (default: 3.0)")
    parser.add_argument("--fee-pct", type=float, default=0.001, help="Fee per trade, frazione (default: 0.1%%)")
    parser.add_argument("--slippage-pct", type=float, default=0.001,
                         help="Slippage stimato per esecuzione manuale, frazione (default: 0.1%%)")
    parser.add_argument("--symbol", default="eth", choices=["eth", "btc"])
    args = parser.parse_args()

    suffix = "" if args.symbol == "eth" else f"_{args.symbol}"
    regime_path = os.path.join("data", "processed", f"regime_score_daily{suffix}.parquet")
    trades_output = os.path.join("data", "processed", f"backtest_trades{suffix}.parquet")
    equity_output = os.path.join("data", "processed", f"backtest_equity{suffix}.parquet")

    if not os.path.exists(regime_path):
        print(f"ERRORE: {regime_path} non trovato. Esegui prima regime_score.py --symbol {args.symbol}")
        return

    print(f"Caricamento dati ({args.symbol.upper()})...")
    df = load_data(symbol=args.symbol)

    # Il backtest e' significativo solo da quando il regime score e'
    # ragionevolmente confermato: usiamo n_categorie_disponibili >= 2 come
    # soglia (stessa usata per bull_regime), altrimenti le prime righe con
    # score debole falserebbero il confronto.
    df_valid = df[df["n_categorie_disponibili"] >= 2].reset_index(drop=True)
    print(f"Periodo di backtest: {df_valid['date'].min()} -> {df_valid['date'].max()} ({len(df_valid)} giorni)")

    print(f"\nEseguendo backtest (ATR multiplier={args.atr_multiplier}, "
          f"fee={args.fee_pct*100:.2f}%, slippage={args.slippage_pct*100:.2f}%)...")
    trades_df, equity_df = run_backtest(df_valid, args.atr_multiplier, args.fee_pct, args.slippage_pct)

    os.makedirs(os.path.dirname(trades_output), exist_ok=True)
    trades_df.to_parquet(trades_output, index=False)
    equity_df.to_parquet(equity_output, index=False)

    # Buy-and-hold sullo stesso identico periodo, per un confronto onesto
    bh_equity = df_valid[["date", "close"]].copy()
    bh_equity["equity"] = bh_equity["close"] / bh_equity["close"].iloc[0] * equity_df["equity"].iloc[0]

    strategy_metrics = compute_metrics(equity_df, trades_df, "Strategia (regime-following)")
    bh_metrics = compute_metrics(bh_equity, None, "Buy & Hold")

    print("\n" + "=" * 70)
    print("RISULTATI BACKTEST")
    print("=" * 70)
    for metrics in [strategy_metrics, bh_metrics]:
        print(f"\n{metrics['label']}:")
        for k, v in metrics.items():
            if k != "label":
                print(f"  {k:25s} {v}")

    print(f"\nTrade log salvato in {trades_output}")
    print(f"Equity curve salvata in {equity_output}")
    if args.symbol == "eth":
        print("\nATTENZIONE: questo e' un singolo backtest su un singolo set di parametri,")
        print("NON ancora una validazione walk-forward. Non trarre conclusioni definitive")
        print("da questo risultato da solo -- serve ancora: robustness check sui parametri")
        print("(+-20%), permutation test, validazione cross-asset su BTC.")
    else:
        print("\nQuesto e' il backtest cross-asset su BTC (solo trend+derivatives, nessuna")
        print("feature on-chain). Confronta questi risultati con quelli ETH: se la logica")
        print("regge in modo simile su un asset diverso, e' un forte indizio che il pattern")
        print("e' reale e non overfitting sul rumore specifico di ETH.")


if __name__ == "__main__":
    main()