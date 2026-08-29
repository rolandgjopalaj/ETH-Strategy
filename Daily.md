# Guida operativa — Paper Trading ETH Bull Strategy

Questa guida copre la gestione quotidiana del sistema una volta finita la
fase di costruzione. Presuppone che tutta la pipeline dati + regime score +
backtest sia gia' validata (vedi README.md per la storia completa di come
ci siamo arrivati e le insidie incontrate).

## Routine quotidiana (ogni giorno, stesso orario)

**1. Lancia il segnale**

```bash
export DUNE_API_KEY="la_tua_chiave"
python daily_signal.py
```

Fallo **sempre alla stessa ora**, idealmente poco dopo la chiusura della
candela daily (00:00 UTC + qualche minuto di margine per essere sicuro che
Binance abbia pubblicato la candela). Orari incoerenti giorno per giorno
introducono un bias silenzioso nei tempi di esecuzione che il backtest non
ha modellato.

**2. Leggi l'output con attenzione**

Lo script ti da' sempre una di queste tre risposte:

- `ENTRA` — apri la posizione (paper) il prima possibile allo stesso prezzo
  di mercato corrente, e segnati manualmente su un tuo file separato **il
  prezzo e l'ora esatti a cui l'avresti davvero eseguita**. Questa
  differenza rispetto al prezzo di riferimento del segnale e' il tuo
  "slippage comportamentale" — se lo scrivi coerentemente ogni volta, dopo
  qualche mese saprai se stai davvero seguendo il sistema o te ne stai
  discostando.
- `ESCI` — stessa cosa, chiudi la posizione (paper) e annota.
- `NESSUNA AZIONE` — non fare nulla. Non serve annotare nulla di
  particolare, il journal automatico gia' registra che il sistema e' stato
  controllato quel giorno.

**3. NON aggiungere discrezione**

Se il sistema dice `ENTRA` e "non ti convince" (prezzo che sembra alto,
notizie negative, sensazione di pancia), esegui comunque, in paper. Lo
scopo di questa fase e' scoprire se il sistema *meccanico* funziona, non se
tu + il sistema insieme funzionate meglio del sistema da solo — quella e'
una domanda diversa, per dopo. Deviare ora invalida silenziosamente tutto
il paper trading: non saprai mai se un risultato buono o cattivo viene dal
sistema o dalle tue correzioni.

## Routine settimanale

**Aggiorna le fonti Dune** (non lo fa `daily_signal.py` in automatico,
consumano crediti):

```bash
python fetch_dune.py --query-id <ID_NETFLOW> --name eth_netflow_daily
python fetch_dune.py --query-id <ID_STAKING> --name staking_flow_daily
python fetch_dune.py --query-id <ID_GAS> --name gas_fee_daily
```

Se salti una settimana non e' un problema: il regime score si adatta gia'
automaticamente escludendo le categorie con dati non freschi dal calcolo
(ri-normalizzazione dei pesi, vedi README). Ma non lasciarle ferme per
mesi: a un certo punto la mancanza di conferma on-chain riduce
strutturalmente il numero di categorie disponibili e quindi la qualita'
del segnale.

**Controlla la qualita' dei dati:**

```bash
python sanity_check.py
```

Un `[FAIL]` improvviso su un file che prima era pulito e' il segnale piu'
importante da non ignorare mai — vuol dire che qualcosa nella pipeline si
e' rotto (API cambiata, rate limit, bug), e ogni segnale generato da quel
momento in poi e' sospetto finche' non risolvi.

## Cosa guardare nel journal (`signals_journal.csv`)

Apri il file di tanto in tanto (settimanale/mensile) e guarda:

- **Frequenza dei segnali**: se vedi `ENTRA`/`ESCI` che si alternano ogni
  1-3 giorni per un periodo prolungato, e' whipsaw — il regime score sta
  oscillando vicino alla soglia in una fase di mercato incerta. Non e' un
  bug, ma e' il momento di massima cautela: le fee/slippage si accumulano
  velocemente in questi periodi.
- **Coerenza tra `regime_score` e `n_categorie_disponibili`**: uno score
  alto con solo 2 categorie disponibili e' un segnale piu' debole di uno
  score piu' basso ma confermato da 4 categorie. Il flag `bull_regime` gia'
  incorpora questa logica (richiede minimo 2 categorie), ma vale la pena
  guardarlo con i tuoi occhi quando prendi una decisione importante.

## Checkpoint temporali — quando valutare, quando NON valutare ancora

- **Prime 2-4 settimane**: troppo presto per giudicare qualunque cosa.
  Serve solo a verificare che la meccanica funzioni (lo script gira senza
  errori, i segnali sono chiari, tu li stai seguendo disciplinatamente).
- **Dopo 1 ciclo di volatilita' completo** (mesi, non settimane — un vero
  pullback o mini-correzione dentro un trend, non solo giorni tranquilli):
  prima vera occasione per valutare se il sistema si comporta come nel
  backtest (entra/esce quando dovrebbe, il trailing stop protegge davvero).
- **Prima di passare a capitale vero**: non un numero fisso di giorni, ma
  aver visto il sistema attraversare almeno un episodio di stress reale
  (una correzione del 15-20%+) in paper, e aver verificato che la tua
  esecuzione reale sia rimasta vicina ai segnali generati (basso slippage
  comportamentale).

Ricorda cosa e' emerso dal permutation test: lo scheletro di puro prezzo
non ha superato la soglia di significativita' statistica standard (p=0.110
sul Calmar) — il valore del sistema, se c'e', sta soprattutto nella
conferma multi-fattore (funding + on-chain) sopra quello scheletro. Il
paper trading e' anche il modo per scoprire se questo si conferma nella
pratica, non solo nel backtest.

## Segnali di allarme — quando fermarsi e rivalutare

Fermati e non passare a capitale vero se durante il paper trading osservi:

- **Whipsaw persistente** su piu' cicli (non un episodio isolato) che
  eroderebbe qualunque capitale vero in fee/slippage.
- **Divergenza sistematica** tra segnali generati e quello che avresti
  fatto d'istinto, nella stessa direzione ripetuta (es. il sistema esce
  sempre "troppo presto" secondo il tuo giudizio) — potrebbe indicare un
  parametro mal calibrato, ma verificalo con i dati prima di cambiare
  qualcosa, non a sensazione.
- **`sanity_check.py` in FAIL ricorrente** su una fonte specifica — vuol
  dire che quella categoria di segnale non e' piu' affidabile.
- **Un singolo trade che domina tutto il risultato** (come successo con il
  trade di ottobre 2020 su ETH) — se lo vedi accadere ancora, torna allo
  stress test dedicato (`stress_test_dominant_trade.py`) prima di trarre
  conclusioni.

## Manutenzione occasionale della pipeline

- **Open interest**: continua ad accumulare storico via
  `fetch_derivatives.py` (gia' incluso in `daily_signal.py`). Dopo alcuni
  mesi avrai abbastanza storico OI perche' diventi una categoria utile
  anche nel regime score, cosa che oggi non e' (copertura ~1% nel
  backtest).
- **Nuove versioni di ccxt/API exchange**: se `fetch_ohlcv.py` o
  `fetch_derivatives.py` iniziano a fallire, controlla prima i changelog di
  ccxt/Binance — gli endpoint cambiano nel tempo (vedi la storia di
  Etherscan e beaconcha.in nel README, entrambi cambiati durante la
  costruzione di questo stesso sistema).
- **Non modificare i parametri del sistema** (soglia regime score=60, ATR
  multiplier=3.0, ecc.) durante il paper trading basandoti su come sta
  andando settimana per settimana — e' la stessa forma di data-snooping
  discussa dall'inizio. Se dopo un ciclo completo di valutazione i
  parametri sembrano davvero da rivedere, rifai un robustness check
  completo (`robustness_check.py`) su dati aggiornati, non un aggiustamento
  estemporaneo.

## Riepilogo: la disciplina che conta piu' del codice

Il sistema e' stato costruito con un livello di rigore insolito per un
progetto personale (test di causalita', permutation test, cross-asset su
BTC, stress test sui trade dominanti). Tutto questo lavoro vale zero se poi
in fase di paper trading si comincia a ignorare i segnali "perche' oggi non
sembra il caso". La parte piu' difficile da qui in avanti non e' tecnica,
e' comportamentale — esattamente il punto sollevato all'inizio di questo
intero progetto.