# Pipeline dati ETH/BTC — step 1-3

## Setup

```bash
pip install -r requirements.txt
```

## Uso

1. Scarica lo storico daily ETH+BTC (default: dal 2018 a oggi):

```bash
python fetch_ohlcv.py
```

2. Verifica la qualità dei dati scaricati:

```bash
python sanity_check.py
```

Deve dare `[OK]` su tutti i file prima di procedere a costruire feature o
segnali sopra questi dati.

## Riesecuzione

`fetch_ohlcv.py` è sicuro da rilanciare: se il file esiste già, riparte
dall'ultimo timestamp salvato invece di riscaricare tutto da zero, e fa
merge/dedup con quello che c'è già. Puoi quindi schedularlo (es. cron
giornaliero) senza preoccuparti di duplicati.

## Altri timeframe

```bash
python fetch_ohlcv.py --timeframe 4h --since 2022-01-01
python sanity_check.py --timeframe 4h
```

## Prossimo passo

Una volta che `sanity_check.py` è verde (o solo WARN documentati) su OHLCV
daily/4h/1h, il passo successivo è funding rate + open interest:

```bash
python fetch_derivatives.py
python sanity_check2.py
```

**Nota sull'open interest**: Binance conserva storico OI solo per ~30 giorni
via API pubblica. Non è possibile recuperare OI storico oltre questa
finestra da Binance direttamente — per costruire uno storico OI utile nel
tempo, rilancia `fetch_derivatives.py` periodicamente (es. cron giornaliero):
ogni run accumula i nuovi dati sopra quelli già salvati. Il funding rate
invece ha storico completo scaricabile fin da subito.

## Struttura dati completa

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
```

`sanity_check.py` (senza `--file`) scansiona automaticamente tutte e tre le
categorie e riporta i risultati raggruppati per sezione.

export DUNE_API_KEY="PpNgPSa0I2oxbdkmNHzy8au4FosLIxBq"