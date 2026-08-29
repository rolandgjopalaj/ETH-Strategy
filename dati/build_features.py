"""
build_features.py

Allinea tutti i dataset grezzi (OHLCV, funding, OI, on-chain) su un'unica
griglia daily e calcola le feature necessarie al regime score composito:

  Trend price-based:    EMA50, EMA200, pendenza EMA200, price vs EMA200
  Trend strength:       ADX(14)
  Derivatives:          funding rate medio/persistenza, variazione OI
  On-chain:             netflow/staking/TVL normalizzati (z-score rolling)

REGOLA FONDAMENTALE (no look-ahead): ogni feature calcolata usa SOLO dati
disponibili fino al timestamp incluso -- mai il futuro. Le rolling window di
pandas (.rolling(), .ewm()) sono causali di default (guardano indietro nel
tempo), quindi va bene usarle direttamente, ma NON va mai fatto un merge che
"anticipi" un dato (es. un dato Dune con timestamp di fine giornata assegnato
erroneamente all'inizio della stessa giornata).

Uso:
    python build_features.py

Output: data/processed/regime_features_daily.parquet
"""

import argparse
import os

import numpy as np
import pandas as pd

RAW_DIR = os.path.join("data", "raw")
PROCESSED_DIR = os.path.join("data", "processed")
OUTPUT_PATH = os.path.join(PROCESSED_DIR, "regime_features_daily.parquet")


# ---------------------------------------------------------------------------
# Caricamento e normalizzazione al giorno (date, senza componente oraria)
# ---------------------------------------------------------------------------

def load_parquet(relpath: str) -> pd.DataFrame:
    filepath = os.path.join(RAW_DIR, relpath)
    if not os.path.exists(filepath):
        print(f"ATTENZIONE: file non trovato, verra' saltato: {filepath}")
        return pd.DataFrame()
    return pd.read_parquet(filepath)


def to_daily_date(df: pd.DataFrame, agg: str = "last") -> pd.DataFrame:
    """
    Normalizza una serie a granularita' daily usando la sola data (senza ora),
    aggregando eventuali righe multiple nello stesso giorno (es. funding
    ogni 8h -> media giornaliera, OI orario -> media giornaliera).
    """
    if df.empty:
        return df
    out = df.copy()
    out["date"] = out["timestamp"].dt.floor("D")
    out = out.drop(columns=["timestamp"])
    numeric_cols = out.select_dtypes(include=[np.number]).columns.tolist()

    if agg == "mean":
        grouped = out.groupby("date")[numeric_cols].mean()
    else:  # "last": per OHLCV daily gia' 1 riga/giorno, "last" e' equivalente
        grouped = out.groupby("date")[numeric_cols].last()

    return grouped.reset_index()


# ---------------------------------------------------------------------------
# Indicatori tecnici (implementati a mano, causali di default)
# ---------------------------------------------------------------------------

def compute_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    ADX(14) standard. Richiede colonne high, low, close.
    Interamente causale: ewm() e diff() guardano solo indietro nel tempo.
    """
    high, low, close = df["high"], df["low"], df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > 0) & (plus_dm > minus_dm), 0.0)
    minus_dm = minus_dm.where((minus_dm > 0) & (minus_dm > plus_dm), 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx


def rolling_zscore(series: pd.Series, window: int = 30, min_periods: int = 10) -> pd.Series:
    """
    Z-score su finestra rolling (MAI sull'intero storico): usare l'intero
    storico per normalizzare userebbe implicitamente informazione futura,
    perche' la media/std calcolate su tutto lo storico "sanno" gia' cosa
    succedera' dopo il punto corrente.
    """
    roll_mean = series.rolling(window, min_periods=min_periods).mean()
    roll_std = series.rolling(window, min_periods=min_periods).std()
    return (series - roll_mean) / roll_std.replace(0, np.nan)


# ---------------------------------------------------------------------------
# Costruzione feature per categoria
# ---------------------------------------------------------------------------

def build_trend_features(ohlcv_daily: pd.DataFrame) -> pd.DataFrame:
    df = ohlcv_daily[["date", "open", "high", "low", "close", "volume"]].copy()
    df = df.sort_values("date").reset_index(drop=True)

    df["ema50"] = compute_ema(df["close"], 50)
    df["ema200"] = compute_ema(df["close"], 200)
    df["price_above_ema200"] = (df["close"] > df["ema200"]).astype(int)
    df["golden_cross"] = (df["ema50"] > df["ema200"]).astype(int)
    # pendenza EMA200 su 10 giorni, in % -- positiva = trend rialzista in accelerazione
    df["ema200_slope_10d"] = df["ema200"].pct_change(10) * 100

    df["adx_14"] = compute_adx(df, period=14)

    return df[["date", "close", "ema50", "ema200", "price_above_ema200",
               "golden_cross", "ema200_slope_10d", "adx_14"]]


def build_derivatives_features(funding_daily: pd.DataFrame, oi_daily: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()

    if not funding_daily.empty:
        f = funding_daily[["date", "funding_rate"]].sort_values("date").copy()
        f["funding_rate_mean_7d"] = f["funding_rate"].rolling(7, min_periods=3).mean()
        # frazione di giorni con funding positivo nelle ultime 2 settimane
        # (persistenza, non solo il valore puntuale)
        f["funding_positive_share_14d"] = (f["funding_rate"] > 0).rolling(14, min_periods=5).mean()
        out = f[["date", "funding_rate", "funding_rate_mean_7d", "funding_positive_share_14d"]]

    if not oi_daily.empty:
        oi = oi_daily[["date", "open_interest"]].sort_values("date").copy()
        # OI ha solo ~30gg di storico da Binance: la % di variazione sara'
        # NaN per la stragrande maggioranza dello storico, per costruzione.
        oi["oi_change_pct_7d"] = oi["open_interest"].pct_change(7) * 100
        if out.empty:
            out = oi[["date", "open_interest", "oi_change_pct_7d"]]
        else:
            out = out.merge(oi[["date", "open_interest", "oi_change_pct_7d"]], on="date", how="outer")

    return out


def build_onchain_features(netflow: pd.DataFrame, staking: pd.DataFrame,
                            tvl_frames: dict, gas: pd.DataFrame) -> pd.DataFrame:
    frames = []

    if not netflow.empty:
        n = netflow[["date", "netflow_eth"]].sort_values("date").copy()
        n["netflow_zscore_30d"] = rolling_zscore(n["netflow_eth"], window=30)
        frames.append(n)

    if not staking.empty:
        s = staking[["date", "net_staking_flow_eth"]].sort_values("date").copy()
        s["staking_zscore_30d"] = rolling_zscore(s["net_staking_flow_eth"], window=30)
        frames.append(s)

    if tvl_frames:
        tvl_merged = None
        for chain, tvl_df in tvl_frames.items():
            if tvl_df.empty:
                continue
            t = tvl_df[["date", "tvl_usd"]].rename(columns={"tvl_usd": f"tvl_{chain}"}).sort_values("date")
            tvl_merged = t if tvl_merged is None else tvl_merged.merge(t, on="date", how="outer")
        if tvl_merged is not None:
            tvl_cols = [c for c in tvl_merged.columns if c.startswith("tvl_")]
            tvl_merged = tvl_merged.sort_values("date")
            tvl_merged["tvl_l2_total"] = tvl_merged[tvl_cols].sum(axis=1, min_count=1)
            tvl_merged["tvl_l2_growth_pct_30d"] = tvl_merged["tvl_l2_total"].pct_change(30) * 100
            frames.append(tvl_merged[["date", "tvl_l2_total", "tvl_l2_growth_pct_30d"]])

    if not gas.empty:
        g = gas[["date", "avg_gas_price_gwei"]].sort_values("date").copy()
        g["gas_zscore_30d"] = rolling_zscore(g["avg_gas_price_gwei"], window=30)
        frames.append(g)

    if not frames:
        return pd.DataFrame()

    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on="date", how="outer")
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Costruisce le feature per il regime score")
    parser.add_argument("--symbol", default="eth", choices=["eth", "btc"],
                         help="Simbolo da processare. 'btc' esclude automaticamente le feature "
                              "on-chain (netflow/staking/TVL/gas), specifiche di Ethereum -- "
                              "usato per il test cross-asset di validazione della logica di trend.")
    args = parser.parse_args()
    symbol = args.symbol

    # Su ETH manteniamo i nomi file originali (retrocompatibilita' con tutto
    # il lavoro gia' fatto finora); su BTC usiamo un suffisso dedicato.
    suffix = "" if symbol == "eth" else f"_{symbol}"
    output_path = os.path.join(PROCESSED_DIR, f"regime_features_daily{suffix}.parquet")

    os.makedirs(PROCESSED_DIR, exist_ok=True)

    print(f"Caricamento dati grezzi per {symbol.upper()}...")
    ohlcv_1d = to_daily_date(load_parquet(os.path.join("ohlcv", f"{symbol}_usdt_1d.parquet")))
    funding = to_daily_date(load_parquet(os.path.join("derivatives", "funding", f"{symbol}_usdt_funding.parquet")), agg="mean")
    oi = to_daily_date(load_parquet(os.path.join("derivatives", "open_interest", f"{symbol}_usdt_oi.parquet")), agg="mean")

    if symbol == "eth":
        netflow = to_daily_date(load_parquet(os.path.join("onchain", "eth_netflow_daily.parquet")))
        staking = to_daily_date(load_parquet(os.path.join("onchain", "staking_flow_daily.parquet")))
        gas = to_daily_date(load_parquet(os.path.join("onchain", "gas_fee_daily.parquet")))
        tvl_frames = {
            "arbitrum": to_daily_date(load_parquet(os.path.join("onchain", "l2_tvl_arbitrum.parquet"))),
            "base": to_daily_date(load_parquet(os.path.join("onchain", "l2_tvl_base.parquet"))),
            "optimism": to_daily_date(load_parquet(os.path.join("onchain", "l2_tvl_optimism.parquet"))),
        }
    else:
        # BTC non ha staking, L2, o netflow ETH-style raccolto in questa
        # pipeline -- il regime score si adattera' automaticamente usando
        # solo trend+derivatives (vedi ri-normalizzazione pesi in regime_score.py)
        print("Simbolo BTC: nessuna feature on-chain disponibile in questa pipeline "
              "(staking/L2/netflow sono concetti specifici di Ethereum) -- il regime "
              "score user\u00e0 solo trend+derivatives, con i pesi ri-normalizzati di conseguenza.")
        netflow = pd.DataFrame()
        staking = pd.DataFrame()
        gas = pd.DataFrame()
        tvl_frames = {}

    if ohlcv_1d.empty:
        print(f"ERRORE: OHLCV daily {symbol.upper()} non trovato -- e' la spina dorsale della griglia, impossibile continuare.")
        return

    print("Costruzione feature di trend (EMA, ADX)...")
    trend = build_trend_features(ohlcv_1d)

    print("Costruzione feature derivatives (funding, OI)...")
    deriv = build_derivatives_features(funding, oi)

    print("Costruzione feature on-chain (netflow, staking, TVL, gas)...")
    onchain = build_onchain_features(netflow, staking, tvl_frames, gas)

    print("Allineamento sulla griglia daily OHLCV (left join, mai il contrario)...")
    result = trend
    if not deriv.empty:
        result = result.merge(deriv, on="date", how="left")
    if not onchain.empty:
        result = result.merge(onchain, on="date", how="left")

    result = result.sort_values("date").reset_index(drop=True)
    result.to_parquet(output_path, index=False)

    print(f"\nSalvato: {len(result)} righe, {len(result.columns)} colonne in {output_path}")
    print(f"Periodo: {result['date'].min()} -> {result['date'].max()}")

    print("\nCopertura dati per colonna (% di righe non-NaN, utile per capire da quando ogni feature e' realmente utilizzabile):")
    coverage = (result.notna().mean() * 100).round(1)
    for col, pct in coverage.items():
        if col == "date":
            continue
        first_valid = result.loc[result[col].notna(), "date"].min() if result[col].notna().any() else "mai"
        print(f"  {col:30s} {pct:5.1f}%   (primo valore valido: {first_valid})")


if __name__ == "__main__":
    main()