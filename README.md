# ETH Bull-Run Signal System

Sistema di generazione segnali (non esecuzione automatica) per una strategia
**trend-following, attiva solo durante fasi di bull market**, su ETH (con
validazione cross-asset su BTC). Combina prezzo, forza del trend, derivatives
(funding/open interest) e dati on-chain in un unico **regime score
composito 0-100**, e genera segnali `ENTRA` / `ESCI` / `NESSUNA AZIONE` che
tu esegui manualmente.

> **Nota importante**: questo non è un consiglio di investimento e questo
> sistema non esegue transazioni per te. È uno strumento di supporto alla
> decisione, pensato per essere usato prima in **paper trading** (posizioni
> simulate) e solo eventualmente, dopo validazione, con capitale reale e size
> ridotta. Nessun livello di rigore statistico garantisce profitti futuri.

Per il racconto completo di *perché* ogni scelta è stata fatta, cosa è
andato storto lungo la strada, e il quadro onesto dei risultati di
validazione, vedi **[`Riasunto.md`](./Riasunto.md)**. Per la routine
operativa quotidiana una volta che il sistema è avviato, vedi
**[`Daily.md`](./Daily.md)**. Questo README copre solo **setup e avvio**.

---

## 1. Cosa fa il sistema, in breve

1. Scarica dati grezzi (prezzo, funding rate, open interest, dati on-chain)
2. Verifica la qualità di quei dati (`sanity_check.py`)
3. Calcola le feature (EMA, ADX, RSI, z-score on-chain, ecc.)
4. Combina le feature in un **regime score** 0-100, usando solo le categorie
   di dati effettivamente disponibili in un dato giorno
5. Applica delle regole di ingresso/uscita sopra il regime score e le valida
   con backtest, robustness check, permutation test e stress test dedicati
6. In produzione, genera **un segnale al giorno** (`daily_signal.py`) che tu
   esegui a mano, registrando tutto in un journal CSV

Non c'è nessuna componente di machine learning: tutto è regole esplicite,
scelte per essere interpretabili e il più possibile robuste a piccole
variazioni di parametro (vedi `Riasunto.md`, sezione 5, per la logica dietro
a questa scelta).

---

## 2. Struttura del progetto

```
.
├── README.md                  <- questo file
├── Riasunto.md                <- storia completa del progetto, risultati, limiti
├── Daily.md                   <- routine operativa quotidiana/settimanale
└── dati/
    ├── requirements.txt
    ├── fetch_ohlcv.py          <- OHLCV ETH/BTC da Binance (ccxt)
    ├── fetch_derivatives.py    <- funding rate + open interest da Binance (ccxt)
    ├── fetch_dune.py           <- query Dune Analytics salvate (netflow/staking/gas)
    ├── fetch_defillama.py      <- TVL L2 da DefiLlama
    ├── sanity_check.py         <- controllo qualità dati grezzi
    ├── build_features.py       <- feature engineering (EMA/ADX/RSI/z-score)
    ├── regime_score.py         <- regime score composito 0-100
    ├── backtest.py             <- backtest con costi realistici
    ├── robustness_check.py     <- stabilità dei parametri (±20%)
    ├── permutation_test.py     <- test statistico vs serie sintetiche
    ├── stress_test_dominant_trade.py  <- verifica sul trade che pesa di più
    └── daily_signal.py         <- segnale giornaliero + paper trading

data/                           <- creata automaticamente dagli script, non versionare
├── raw/
│   ├── ohlcv/                  <- eth_usdt_1d.parquet, btc_usdt_1d.parquet, ...
│   ├── derivatives/
│   │   ├── funding/            <- eth_usdt_funding.parquet, ...
│   │   └── open_interest/      <- eth_usdt_oi.parquet, ...
│   └── onchain/                <- eth_netflow_daily.parquet, staking_flow_daily.parquet,
│                                   gas_fee_daily.parquet, l2_tvl_arbitrum.parquet, ...
└── processed/
    ├── regime_features_daily.parquet      <- (e varianti _btc)
    ├── regime_score_daily.parquet         <- (e varianti _btc)
    ├── backtest_trades.parquet / backtest_equity.parquet
    ├── robustness_grid.parquet
    ├── paper_trading_state.json           <- stato posizione corrente (creato da daily_signal.py)
    └── signals_journal.csv                <- storico di tutti i segnali generati
```

Tutti gli script in `dati/` vanno lanciati **da dentro la cartella `dati/`**,
perché usano path relativi (`data/raw/...`, `data/processed/...`) rispetto
alla directory corrente.

---

## 3. Prerequisiti

- **Python 3.10+** (consigliato; usa `python3 --version` per controllare)
- Connessione internet (nessuna VPN/proxy necessaria per Binance/DefiLlama;
  se il tuo paese ha restrizioni sull'accesso a Binance, dovrai gestirlo tu)
- **Nessun account exchange richiesto**: tutti i dati OHLCV/funding/OI sono
  endpoint pubblici via `ccxt`, non servono API key né account Binance
- Un **account Dune Analytics** (gratuito) per i dati on-chain (netflow,
  staking flow, gas fee) — vedi sezione 5.3
- Spazio disco: trascurabile, i file Parquet sono piccoli (storico giornaliero
  di alcuni anni è dell'ordine di poche centinaia di KB per file)

---

## 4. Installazione

```bash
# 1. Clona/scarica il progetto e posizionati nella root
cd percorso/del/progetto

# 2. Crea un ambiente virtuale (fortemente consigliato)
python3 -m venv venv
source venv/bin/activate        # su Windows: venv\Scripts\activate

# 3. Installa le dipendenze
pip install -r dati/requirements.txt
```

`requirements.txt` include: `ccxt`, `pandas`, `pyarrow`, `requests`. Non
serve altro (niente `pandas-ta`/`ta-lib`: gli indicatori — EMA, ADX, RSI,
ATR — sono implementati a mano in `build_features.py`/`backtest.py` per
avere controllo totale sulla causalità, evitando problemi di compatibilità
di quelle librerie con pandas/numpy recenti).

---

## 5. Configurazione e primo avvio (in ordine)

L'ordine conta: ogni fase dipende dall'output della precedente.

### 5.1 Scarica OHLCV (ETH e BTC)

```bash
cd dati
python fetch_ohlcv.py --symbols ETH/USDT BTC/USDT --timeframe 1d --since 2018-01-01
```

Scarica storico giornaliero da Binance via `ccxt`. È **rieseguibile**: se il
file esiste già, riparte dall'ultimo timestamp salvato e fa merge/dedup
invece di riscaricare tutto. Se ti serve anche granularità più fine per il
timing (4h/1h), rilancia con `--timeframe 4h` o `--timeframe 1h`.

Output: `data/raw/ohlcv/eth_usdt_1d.parquet`, `data/raw/ohlcv/btc_usdt_1d.parquet`

### 5.2 Scarica funding rate e open interest

```bash
python fetch_derivatives.py --since 2020-01-01
```

- **Funding rate**: storico completo, paginato come l'OHLCV.
- **Open interest**: Binance espone solo gli **ultimi ~30 giorni** per questo
  endpoint — è un limite dell'API pubblica, non risolvibile senza un
  provider a pagamento (vedi `Riasunto.md`, sezione 9). Per accumulare più
  storico OI nel tempo, questo script va **rilanciato periodicamente** (es.
  cron giornaliero): ogni run aggiunge dati nuovi sopra quelli già salvati.

Output: `data/raw/derivatives/funding/*.parquet`, `data/raw/derivatives/open_interest/*.parquet`

### 5.3 Scarica dati on-chain da Dune Analytics

Questo è l'unico passaggio che richiede una **configurazione manuale da
parte tua**, perché le query SQL sono specifiche e vanno scritte/salvate sul
tuo account Dune:

1. Crea un account gratuito su [dune.com](https://dune.com)
2. Scrivi (o trova e forka una query community) tre query SQL salvate:
   - **Netflow exchange ETH** (giornaliero): flusso netto di ETH verso/da
     exchange centralizzati — colonna valore attesa: `netflow_eth`
   - **Staking flow** (giornaliero): flusso netto di staking/unstaking ETH
     — colonna valore attesa: `net_staking_flow_eth`
   - **Gas fee medio** (giornaliero): prezzo medio del gas — colonna valore
     attesa: `avg_gas_price_gwei`
3. Prendi il `query_id` di ciascuna query (visibile nell'URL della query su
   dune.com) e la tua API key Dune (Settings → API)
4. Esegui:

```bash
export DUNE_API_KEY="la_tua_chiave"

python fetch_dune.py --query-id <ID_NETFLOW> --name eth_netflow_daily
python fetch_dune.py --query-id <ID_STAKING> --name staking_flow_daily
python fetch_dune.py --query-id <ID_GAS>     --name gas_fee_daily
```

Lo script è generico: riconosce automaticamente la colonna data (`day`,
`date`, `block_date`, `timestamp`) e la rinomina in `timestamp`. Se la tua
query ha nomi di colonna diversi da quelli attesi sopra (`netflow_eth`,
`net_staking_flow_eth`, `avg_gas_price_gwei`), `build_features.py` e
`sanity_check.py` non troveranno la colonna giusta: allinea i nomi o
rinominali dopo il salvataggio.

Ogni esecuzione **consuma crediti Dune** (il piano gratuito ne ha un numero
limitato al mese) — per questo non è automatizzato in `daily_signal.py`, va
rilanciato tu manualmente, idealmente una volta a settimana (vedi
`Daily.md`).

Output: `data/raw/onchain/eth_netflow_daily.parquet`,
`data/raw/onchain/staking_flow_daily.parquet`,
`data/raw/onchain/gas_fee_daily.parquet`

### 5.4 Scarica TVL delle L2 (DefiLlama, nessuna key richiesta)

```bash
python fetch_defillama.py --chains arbitrum base optimism
```

Nessuna API key, nessun limite di crediti. Sicuro da rilanciare quanto
vuoi (`daily_signal.py` lo fa già automaticamente ogni giorno).

Output: `data/raw/onchain/l2_tvl_arbitrum.parquet`, ecc.

### 5.5 Controlla la qualità dei dati

```bash
python sanity_check.py
```

Verifica buchi nella griglia temporale, duplicati, valori nulli/negativi,
candele OHLC incoerenti. Interpretazione dell'output:

- `[OK]` — nessun problema
- `[WARN]` — problemi minori entro soglia di tolleranza (es. 1-2 candele
  mancanti per un micro-outage exchange), puoi procedere ma tienine traccia
- `[FAIL]` — problema bloccante: **non costruire feature/segnali sopra
  questi dati finché non risolvi**

Esegui questo controllo **sempre dopo un fetch**, non solo la prima volta.

### 5.6 Costruisci le feature

```bash
python build_features.py --symbol eth
python build_features.py --symbol btc    # per la validazione cross-asset
```

Allinea tutti i dataset grezzi su una griglia daily comune e calcola EMA50,
EMA200, pendenza EMA200, ADX(14), medie/persistenza del funding, variazione
OI, z-score rolling di netflow/staking/TVL/gas. Tutto causale (nessun
look-ahead: ogni feature usa solo dati fino al giorno stesso incluso). Su
BTC le feature on-chain (specifiche di Ethereum) sono automaticamente
escluse.

Output: `data/processed/regime_features_daily.parquet` (e `_btc`)

Lo script stampa anche una tabella di copertura per colonna (% di righe
non-NaN e data del primo valore valido) — utile per capire da quando ogni
feature è realmente utilizzabile.

### 5.7 Calcola il regime score

```bash
python regime_score.py --symbol eth
python regime_score.py --symbol btc
```

Combina 4 categorie pesate (trend price-based 35%, trend strength 15%,
derivatives 20%, on-chain 30%) in uno score 0-100, usando **solo le
categorie disponibili quel giorno** (pesi ri-normalizzati sul totale
presente — non riempie mai con zeri). La colonna `n_categorie_disponibili`
indica quanto è "confermato" lo score in ogni riga.

Output: `data/processed/regime_score_daily.parquet` (e `_btc`)

### 5.8 Backtest

```bash
python backtest.py --symbol eth
python backtest.py --symbol btc
```

Simula la strategia (ingresso su conferma di regime bull + pullback/breakout
di rientro, uscita su rottura di regime o trailing stop ATR-based), con fee
0.1%, slippage 0.1%, ed esecuzione realistica (segnale sul close di un
giorno, eseguito all'open del giorno dopo). Stampa metriche vs buy-and-hold
(CAGR, max drawdown, Calmar, Sharpe, win rate, profit factor).

Output: `data/processed/backtest_trades.parquet`, `backtest_equity.parquet`
(e varianti `_btc`)

### 5.9 Validazione statistica (fortemente consigliata prima di fidarti di qualunque risultato)

```bash
python robustness_check.py         # stabilità dei parametri a ±20%
python permutation_test.py         # la strategia batte il caso puro?
python stress_test_dominant_trade.py   # quanto dipende tutto da un solo trade?
```

Questi tre script **non modificano nulla**, producono solo report a
schermo (più `robustness_grid.parquet` per il primo). Leggi `Riasunto.md`
sezione 5 per come interpretare onestamente i risultati — in particolare il
permutation test è il controllo più importante da non ignorare mai.

---

## 6. Uso quotidiano: il segnale giornaliero

Una volta completata tutta la pipeline sopra almeno una volta:

```bash
export DUNE_API_KEY="la_tua_chiave"   # serve solo se vuoi che rifreschi anche Dune, normalmente non necessario qui
python daily_signal.py
```

Questo script, in un colpo solo:

1. Aggiorna OHLCV, funding, open interest, TVL L2 (**non** i dati Dune —
   quelli li aggiorni tu a parte, vedi 5.3, perché consumano crediti)
2. Ricalcola feature e regime score
3. Applica la **stessa identica funzione di decisione** usata nel backtest
   (nessuna divergenza possibile tra "quello che hai validato" e "quello che
   gira live")
4. Stampa `ENTRA` / `ESCI` / `NESSUNA AZIONE` con motivazione
5. Salva lo stato (`paper_trading_state.json`) e registra il segnale nel
   journal (`signals_journal.csv`)

Per la disciplina operativa completa (quando fidarsi, quando fermarsi,
cosa guardare nel journal, checkpoint temporali prima di passare a
capitale vero) vedi **`Daily.md`** — è pensato per essere seguito passo
passo, non solo letto una volta.

Usa `python daily_signal.py --skip-refresh` solo per debug/test: salta
l'aggiornamento dati e usa quelli già su disco.

---

## 7. Cosa il sistema NON fa

- Non esegue ordini reali né si connette a un account exchange per fare
  trading — genera solo segnali testuali
- Non gestisce automaticamente il position sizing (il backtest usa 100% del
  capitale per trade; position sizing ATR-based è nella roadmap, vedi
  `Riasunto.md` sezione 8)
- Non è machine learning: nessun modello viene "allenato", tutte le regole
  sono esplicite e ispezionabili nel codice
- Non aggiorna automaticamente i dati Dune (costano crediti) — richiede
  intervento manuale periodico

---

## 8. Limiti noti (riassunto — dettagli in `Riasunto.md` §7)

- Open interest storicamente quasi inutilizzabile (limite API Binance, ~30
  giorni), copertura ~1% nel backtest attuale
- Netflow/staking/gas partono dal 2021 (scelta per risparmiare crediti Dune)
- Pochi bull run indipendenti nella storia di ETH (2-3) su cui validare
- Il permutation test copre solo lo scheletro di prezzo (trend+ADX+timing),
  non la versione completa con funding/on-chain
- Nessun position sizing dinamico ancora implementato

---

## 9. Se qualcosa si rompe

- `fetch_ohlcv.py` / `fetch_derivatives.py` falliscono → controlla prima i
  changelog di `ccxt`/Binance, gli endpoint cambiano nel tempo
- `sanity_check.py` in `[FAIL]` ricorrente su una fonte → quella categoria
  di segnale non è più affidabile, non ignorarlo
- Dune restituisce colonne inattese → verifica i nomi colonna nella tua
  query rispetto a quelli attesi in `sanity_check.py`
  (`ONCHAIN_VALUE_COL_BY_PATTERN`) e `build_features.py`

Per tutto il resto — perché le cose sono progettate così, cosa è stato
scartato e perché, i risultati completi di validazione — vedi
**`Riasunto.md`**.
