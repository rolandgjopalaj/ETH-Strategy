Cosa manca da qui al sistema completo

1. Allineamento temporale
Unire i 7 dataset (frequenze diverse: 1h/4h/1d OHLCV, 8h funding, 1h OI, daily on-chain) su un'unica griglia daily pulita, con forward-fill solo dove concettualmente corretto (mai sul prezzo).

2. Feature engineering
Calcolare su questa griglia unificata:

Trend: EMA50, EMA200, pendenza EMA, struttura higher-highs/lows
Forza trend: ADX
Derivatives: funding rate medio/persistenza, OI trend
On-chain: netflow normalizzato, staking flow normalizzato, TVL L2 trend

3. Costruzione del regime score composito
Combinare le feature normalizzate (z-score rolling) in uno score 0-100 pesato, con la soglia che definisce "siamo in bull regime" sì/no.

4. Segnali di ingresso/uscita dentro al regime
Pullback su EMA20/50 + RSI, breakout con volume, trailing stop ATR-based, uscita su rottura EMA200.

5. Backtest walk-forward
Includere commissioni/slippage realistici, train/test split temporale (mai shuffle), test su periodi diversi (2023 vs 2024-25).

6. Validazione anti-overfitting
Parameter robustness check (±20%), Monte Carlo/permutation test, test cross-asset (stessa logica su BTC).

7. Stress test
Verifica su blow-off top storici (maggio 2021, novembre 2021) per controllare che il sistema esca in tempo.

8. Paper trading
Almeno un ciclo di volatilità completo prima di capitale vero.