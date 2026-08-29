"""
regime_score.py

Costruisce il regime score composito 0-100 dalle feature gia' calcolate da
build_features.py, combinando 4 categorie di segnale indipendenti (coerenti
con la mappa dell'edge discussa: risk management > segnale, ma qui ci
occupiamo della parte "quando siamo in bull regime"):

  A) Trend price-based   (peso 0.35): price>EMA200, golden cross, slope EMA200
  B) Trend strength       (peso 0.15): ADX
  C) Derivatives          (peso 0.20): persistenza funding positivo
  D) On-chain             (peso 0.30): netflow, staking, TVL L2 (accumulo)

REGOLA IMPORTANTE: non tutte le categorie sono sempre disponibili (es. TVL
L2 parte da meta' 2021). Lo score usa SOLO le categorie disponibili in quel
giorno, ri-normalizzando i pesi sul totale disponibile -- non riempie mai
con zeri le categorie mancanti (falserebbe lo score verso il ribasso in modo
arbitrario). La colonna n_categorie_disponibili dice sempre quanto e'
"confermato" un segnale: uno score alto basato su 1 sola categoria vale
molto meno di uno score alto confermato da tutte e 4.

Uso:
    python regime_score.py

Output: data/processed/regime_score_daily.parquet
"""

import os

import numpy as np
import pandas as pd

INPUT_PATH = os.path.join("data", "processed", "regime_features_daily.parquet")
OUTPUT_PATH = os.path.join("data", "processed", "regime_score_daily.parquet")

WEIGHTS = {
    "trend_price": 0.35,
    "trend_strength": 0.15,
    "derivatives": 0.20,
    "onchain": 0.30,
}

BULL_SCORE_THRESHOLD = 60      # score minimo per considerare il regime "bull"
MIN_CATEGORIES_REQUIRED = 2    # sotto questa soglia, il segnale e' troppo debole per fidarsi


def clip01(series: pd.Series) -> pd.Series:
    return series.clip(lower=0, upper=1)


def score_trend_price(df: pd.DataFrame) -> pd.Series:
    """
    Media di 3 segnali booleani/derivati, tutti gia' disponibili quando lo e'
    price_above_ema200 (dipendono solo da EMA50/200, presenti dal day 1).
    """
    slope_positive = (df["ema200_slope_10d"] > 0).astype(float)
    components = pd.concat([
        df["price_above_ema200"].astype(float),
        df["golden_cross"].astype(float),
        slope_positive.where(df["ema200_slope_10d"].notna()),
    ], axis=1)
    return components.mean(axis=1, skipna=True)


def score_trend_strength(df: pd.DataFrame) -> pd.Series:
    """ADX normalizzato: 40+ = trend molto forte, score pieno."""
    return clip01(df["adx_14"] / 40)


def score_derivatives(df: pd.DataFrame) -> pd.Series:
    """
    Persistenza del funding positivo (long crowded ma sostenuto = bullish).
    Gia' in [0,1] per costruzione (e' una frazione), nessuna trasformazione
    necessaria.
    """
    return clip01(df["funding_positive_share_14d"])


def score_onchain(df: pd.DataFrame) -> pd.Series:
    """
    Media di 3 sotto-segnali on-chain, ciascuno mappato a [0,1] con un
    sigmoide lineare centrato sullo zero (z-score/variazione 0 -> 0.5):
      - netflow: NEGATIVO e' bullish (ETH esce dagli exchange = accumulo)
      - staking: POSITIVO e' bullish (piu' si stakea, piu' fiducia/accumulo)
      - TVL L2:  crescita POSITIVA e' bullish (attivita' di rete in aumento)
    """
    netflow_bull = clip01(-df["netflow_zscore_30d"] / 2 + 0.5)
    staking_bull = clip01(df["staking_zscore_30d"] / 2 + 0.5)
    tvl_bull = clip01(df["tvl_l2_growth_pct_30d"] / 20 + 0.5)  # 20% crescita 30d = score pieno

    components = pd.concat([netflow_bull, staking_bull, tvl_bull], axis=1)
    return components.mean(axis=1, skipna=True)


def build_composite_score(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["date", "close", "price_above_ema200"]].copy()

    category_scores = {
        "trend_price": score_trend_price(df),
        "trend_strength": score_trend_strength(df),
        "derivatives": score_derivatives(df),
        "onchain": score_onchain(df),
    }

    for name, series in category_scores.items():
        out[f"cat_{name}"] = series

    # Per ogni riga: somma pesata SOLO delle categorie non-NaN, pesi
    # ri-normalizzati sul totale disponibile in quella riga.
    cat_df = pd.DataFrame(category_scores)
    weights_series = pd.Series(WEIGHTS)

    available_mask = cat_df.notna()
    weights_matrix = available_mask.mul(weights_series, axis=1)
    weight_sums = weights_matrix.sum(axis=1)

    weighted_scores = (cat_df.fillna(0) * weights_matrix).sum(axis=1)
    composite = np.where(weight_sums > 0, weighted_scores / weight_sums * 100, np.nan)

    out["regime_score"] = composite
    out["n_categorie_disponibili"] = available_mask.sum(axis=1)

    # Bull regime richiede: score sopra soglia + prezzo sopra EMA200 (gate
    # rigido, coerente con la discussione iniziale: il filtro di regime
    # macro e' price>EMA200, non negoziabile) + un numero minimo di
    # categorie che confermano, altrimenti il segnale e' troppo debole.
    out["bull_regime"] = (
        (out["regime_score"] >= BULL_SCORE_THRESHOLD)
        & (out["price_above_ema200"] == 1)
        & (out["n_categorie_disponibili"] >= MIN_CATEGORIES_REQUIRED)
    )

    return out


def main():
    if not os.path.exists(INPUT_PATH):
        print(f"ERRORE: {INPUT_PATH} non trovato. Esegui prima build_features.py")
        return

    print("Caricamento feature...")
    df = pd.read_parquet(INPUT_PATH)

    print("Costruzione regime score composito...")
    result = build_composite_score(df)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    result.to_parquet(OUTPUT_PATH, index=False)

    print(f"\nSalvato: {len(result)} righe in {OUTPUT_PATH}")

    # Report riassuntivo: quanto tempo il sistema segnala bull regime, e con
    # quale livello medio di conferma
    valid = result.dropna(subset=["regime_score"])
    pct_bull = (valid["bull_regime"].sum() / len(valid) * 100) if len(valid) else 0
    print(f"\nGiorni con score valido: {len(valid)} su {len(result)}")
    print(f"Giorni in bull_regime=True: {valid['bull_regime'].sum()} ({pct_bull:.1f}%)")

    print("\nDistribuzione n_categorie_disponibili:")
    print(valid["n_categorie_disponibili"].value_counts().sort_index())

    print("\nMedia regime_score per anno (per un controllo rapido di sanita'):")
    valid_copy = valid.copy()
    valid_copy["year"] = valid_copy["date"].dt.year
    print(valid_copy.groupby("year")["regime_score"].mean().round(1))


if __name__ == "__main__":
    main()