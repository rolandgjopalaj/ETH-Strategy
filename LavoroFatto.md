# ETH Bull-Run Strategy — Pipeline Dati

Pipeline per raccogliere, pulire e validare tutti i dati necessari a costruire
un sistema di trading trend-following per ETH, specializzato su regime "bull
run" (vedi discussione strategica per il contesto completo: risk management >
segnale, conferma multi-fattore, validazione anti-overfitting rigorosa).

Questo documento copre **solo la fase 1: raccolta e validazione dati**. La
fase 2 (feature engineering + regime score) e la fase 3 (backtest) non sono
ancora iniziate.

## Stato attuale

✅ **Blocco dati completo**: 7 dataset scaricati e validati (`sanity_check.py`
verde su tutti, solo WARN documentati e accettati consapevolmente).

## Setup

```bash
pip install -r requirements.txt
```

Variabili d'ambiente richieste (mai committare, mai condividere in chat):

```bash
export DUNE_API_KEY="la_tua_chiave"
```

(Etherscan è stato registrato ma non è più usato nella pipeline attuale —
vedi nota sotto sul perché.)

## Struttura dati

```
data/raw/
  ohlcv/
    eth_usdt_1d.parquet, eth_usdt_4h.parquet, eth_usdt_1h.parquet
    btc_usdt_1d.parquet, btc_usdt_4h.parquet, btc_usdt_1h.parquet
  derivatives/
    funding/
      eth_usdt_funding.parquet, btc_usdt_funding.parquet
    open_interest/
      eth_usdt_oi.parquet, btc_usdt_oi.parquet
  onchain/
    eth_netflow_daily.parquet
    staking_flow_daily.parquet
    gas_fee_daily.parquet
    l2_tvl_arbitrum.parquet, l2_tvl_base.parquet, l2_tvl_optimism.parquet
```

## Script disponibili

| Script | Cosa fa | Fonte | Serve key? |
|---|---|---|---|
| `fetch_ohlcv.py` | OHLCV storico ETH/BTC, 3 timeframe | Binance (ccxt) | No |
| `fetch_derivatives.py` | Funding rate + open interest | Binance futures (ccxt) | No |
| `fetch_defillama.py` | TVL storico L2 (Arbitrum, Base, Optimism) | DefiLlama | No |
| `fetch_dune.py` | Esegue query Dune salvate (generico) | Dune Analytics | Sì (`DUNE_API_KEY`) |
| `sanity_check.py` | Valida tutti i dati raccolti | — | — |

### Comandi tipici

```bash
# OHLCV
python fetch_ohlcv.py --timeframe 1d --since 2018-01-01
python fetch_ohlcv.py --timeframe 4h --since 2022-01-01
python fetch_ohlcv.py --timeframe 1h --since 2022-01-01

# Derivatives
python fetch_derivatives.py

# On-chain via DefiLlama (nessuna key)
python fetch_defillama.py

# On-chain via Dune (richiede query salvata su dune.com, vedi sotto)
python fetch_dune.py --query-id <ID> --name <nome_output>

# Validazione (rilanciare dopo OGNI nuovo download)
python sanity_check.py
```

## Query Dune attualmente in uso

Tutte richiedono un account Dune gratuito e la query salvata sul sito prima
di poterla chiamare via API. Il `query_id` è il numero nell'URL dopo il
salvataggio.

### 1. Netflow exchange (`eth_netflow_daily`)
Basata su una query community forkata (`dune.com/queries/1621987`), poi
**riscritta a mano** perché l'originale calcolava solo un aggregato "ultime
24 ore" per exchange (7 righe totali), non una serie storica giorno per
giorno. La versione in uso usa `ethereum.traces`, ETH nativo (niente
ERC-20/USD per tenerla semplice e leggera sui crediti), finestra da
`2023-01-01`.

### 2. Staking flow (`staking_flow_daily`)
Depositi da `ethereum.transactions` verso il Beacon Deposit Contract, prelievi
da `ethereum.withdrawals`. Finestra da `2020-11-01`.

### 3. Gas fee (`gas_fee_daily`)
Media giornaliera di `gas_price` da `ethereum.transactions`. Finestra da
`2023-01-01`.

Le tre query complete sono conservate in `dune_queries.md`.

## ⚠️ Note e insidie importanti (leggere prima di modificare qualcosa)

Queste sono le cose che sono andate storte durante la costruzione e vale la
pena ricordare per non ripeterle.

- **Beacon Deposit Contract address**: `0x00000000219ab540356cBB839Cbe05303d7705Fa`
  (40 caratteri esadecimali dopo `0x`). Un singolo carattere sbagliato/mancante
  qui produce un errore SQL criptico ("Binary literal must contain an even
  number of digits") che non dice affatto "indirizzo sbagliato" — se vedi
  quell'errore, il primo sospetto è sempre un indirizzo troncato o mal
  copiato, conta sempre che siano esattamente 40 caratteri.

- **Open interest storico**: Binance espone via API pubblica **solo gli
  ultimi ~30 giorni** di OI (`/futures/data/openInterestHist`). Non è un
  bug di `fetch_derivatives.py` — è un limite server-side. Per costruire uno
  storico OI più lungo, l'unica strada è rilanciare `fetch_derivatives.py`
  periodicamente (cron) e lasciare che accumuli nel tempo. Non è possibile
  recuperare il passato.

- **Etherscan non è usato per gas fee**: l'endpoint storico
  (`dailyavggasprice`) è **PRO-only**, non incluso nel piano gratuito.
  Abbiamo spostato il gas fee su Dune invece. La key Etherscan resta
  registrata ma al momento non serve a nulla in questa pipeline — utile
  solo se in futuro serve altro (verifica contratti, dati non storici).

- **beaconcha.in**: il tier API gratuito è stato **discontinuato** (ora solo
  trial 30gg o piani a pagamento). Per questo lo staking flow è stato
  costruito interamente su Dune invece.

- **Query Dune "community" vanno sempre verificate prima di fidarsi**: la
  prima query forkata per il netflow sembrava corretta ma in realtà
  calcolava solo un totale aggregato sulle ultime 24 ore, non una serie
  storica — il sintomo era "sempre 7 righe indipendentemente dai parametri".
  Se una query Dune ritorna sempre lo stesso numero di righe a prescindere
  dallo storico atteso, molto probabilmente manca un `GROUP BY` sulla data o
  c'è un filtro `WHERE block_time >= NOW() - INTERVAL '24' hour` nascosto da
  qualche parte.

- **Crediti Dune sono limitati sul piano free**: tutte le query su
  `ethereum.transactions` / `ethereum.traces` sono state deliberatamente
  limitate a partire da `2023-01-01` invece che dallo storico completo, per
  non rischiare di esaurire il budget mensile gratuito in un colpo solo. Se
  serve storico più lungo (es. per includere il bull run 2020-21 nel
  backtest), va allargata la finestra gradualmente, verificando i crediti
  consumati ogni volta.

- **`--timeframe` in `sanity_check.py` ha effetto solo insieme a `--file`**:
  passarlo durante una scansione di cartella intera veniva applicato
  (erroneamente, in una versione precedente dello script) a *tutti* i file
  trovati, causando falsi positivi enormi su file con timeframe diverso da
  quello passato. Il bug è stato corretto — ora ogni file usa sempre il
  proprio timeframe dedotto dal nome quando si scansiona la cartella.

- **TVL Arbitrum ha un gap iniziale legittimo**: righe con `tvl_usd = 0` fino
  a metà agosto 2021 (prima del lancio pubblico della chain). Sono state
  rimosse dal file (non troncate via tolleranza del check, ma filtrate
  fisicamente: `df[df["tvl_usd"] > 0]`), perché sono osservazioni reali (TVL
  effettivamente zero) e non un buco di dati da "tollerare".

- **Sistema di tolleranza WARN/FAIL in `sanity_check.py`**: gap isolati
  (default: ≤2 candele/osservazioni mancanti) diventano `[WARN]` invece di
  `[FAIL]`, perché micro-outage degli exchange/provider sono fisiologici e
  non vale la pena inseguirli. Gap più estesi restano `[FAIL]` e vanno
  sempre investigati — non alzare la soglia di tolleranza per farli sparire,
  è un modo per nascondere problemi reali, non per risolverli.

- **Mai reinserire dati "mancanti" con forward-fill sul prezzo.** L'unico
  gap noto e accettato (1 candela isolata, `2023-03-24 13:00` su OHLCV 1h
  ETH e BTC) è stato lasciato come buco reale, non riempito.

## Prossimi passi (non ancora fatti)

1. **Allineamento temporale**: unire i 7 dataset (frequenze da 1h a 8h a
   daily) su un'unica griglia daily pulita.
2. **Feature engineering**: EMA50/200, ADX, funding persistente, OI trend,
   netflow/staking/TVL normalizzati (z-score rolling, mai su tutto lo
   storico per evitare look-ahead).
3. **Regime score composito**: score 0-100 pesato su 4-5 categorie di
   segnale indipendenti.
4. **Segnali di ingresso/uscita**: pullback EMA20/50 + RSI, breakout con
   volume, trailing stop ATR-based.
5. **Backtest walk-forward**: mai train/test con shuffle casuale su serie
   temporali; commissioni e slippage realistici inclusi.
6. **Validazione anti-overfitting**: parameter robustness (±20%), Monte
   Carlo/permutation test, validazione cross-asset su BTC.
7. **Stress test** su blow-off top storici (maggio 2021, novembre 2021).
8. **Paper trading** per almeno un ciclo di volatilità completo prima di
   capitale reale.