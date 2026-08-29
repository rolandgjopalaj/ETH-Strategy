# Riassunto completo — ETH Bull-Run Strategy

Questo documento racconta tutto il percorso: perche' abbiamo fatto ogni
scelta, cosa e' andato storto lungo la strada, cosa sappiamo davvero oggi
(e cosa no), e cosa serve per arrivare alla versione migliore possibile del
sistema — comprese le opzioni a pagamento che valgono l'investimento.

---

## 1. Genesi e filosofia di partenza

Il progetto nasce da una domanda semplice: costruire un sistema che generi
segnali di trading per ETH (esecuzione manuale, non automatica), inizialmente
generale, poi ristretto a **una strategia solo per i bull run**.

La primissima decisione importante e' stata di **onesta' intellettuale sul
problema**: i mercati sono efficienti nel breve termine, la maggior parte
delle strategie retail che sembrano funzionare nel backtest falliscono in
live trading. Da qui, tre principi guida che hanno orientato ogni scelta
successiva:

1. **Il risk management conta piu' del segnale** — l'80% del vantaggio vero
   sta nella gestione del rischio (position sizing, stop, asimmetria
   rischio/rendimento), non nella "formula magica" di ingresso.
2. **La conferma multi-fattore riduce il rumore** — un singolo indicatore
   (es. solo EMA200) da' troppi falsi segnali. Serve conferma indipendente
   da categorie diverse: trend di prezzo, forza del trend, derivatives,
   on-chain.
3. **La validazione statistica seria e' la parte che quasi nessuno fa bene**
   — walk-forward, robustness ai parametri, permutation test, validazione
   cross-asset. E' anche la parte piu' lunga di questo lavoro, e a
   ragione: e' li' che si scopre se un sistema e' reale o e' overfitting
   travestito da buon backtest.

Un quarto principio, emerso strada facendo ma altrettanto centrale: **la
qualita' dei dati viene prima di qualunque modello**. Un sistema
sofisticato costruito su dati con un bug silenzioso non e' "un po'
sbagliato", e' inutile — e il problema e' che nel backtest spesso non si
vede.

---

## 2. La pipeline dati — cosa abbiamo raccolto e perche'

Sette dataset, ciascuno per una ragione precisa legata alla mappa
dell'edge discussa all'inizio:

| Dataset | Categoria | Perche' |
|---|---|---|
| OHLCV (1d/4h/1h, ETH+BTC) | Trend price-based | Spina dorsale: regime su daily, timing su 4h/1h |
| Funding rate | Derivatives | Long crowded ma sostenuto spesso accompagna i bull run |
| Open interest | Derivatives | Conferma se il trend e' "genuino" o solo leva |
| Netflow exchange | On-chain | ETH che esce dagli exchange = accumulo |
| Staking flow | On-chain | Piu' staking = fiducia/accumulo strutturale |
| TVL L2 (Arbitrum/Base/Optimism) | On-chain | Proxy di attivita' di rete reale |
| Gas fee | On-chain | Proxy aggiuntivo di attivita' (ruolo minore, aggiunto per completezza) |

**Perche' non abbiamo usato provider "premium" come Glassnode fin
dall'inizio**: durante la costruzione abbiamo scoperto che Glassnode ha
reso l'accesso API a pagamento (Professional plan, centinaia di $/mese) e
beaconcha.in ha eliminato il tier gratuito. Abbiamo ricostruito tutto su
**Dune Analytics** (query SQL scritte a mano, gratis con limiti di
crediti) + **DefiLlama** (completamente gratis, nessuna key) + **ccxt**
(Binance, gratis). Questo ha richiesto piu' lavoro manuale (scrivere query
SQL, gestire un indirizzo di contratto sbagliato che ha causato un errore
criptico, riscrivere una query community che calcolava solo un aggregato
24h invece di una serie storica) ma ha tenuto il costo a zero.

**Il limite piu' importante rimasto**: l'open interest storico e'
strutturalmente limitato a ~30 giorni (limite dell'API pubblica di
Binance, non risolvibile senza un provider a pagamento). Nel backtest
attuale questa feature ha circa l'1% di copertura — di fatto inutilizzata
per la validazione storica, utile solo prospetticamente in live (vedi
sezione miglioramenti).

---

## 3. Feature engineering e regime score — le decisioni di design

**EMA50/200, ADX(14), RSI(14) calcolati a mano** invece di usare
`pandas-ta`, per evitare i problemi di compatibilita' ricorrenti di quella
libreria con versioni recenti di pandas/numpy, e per avere controllo
totale sulla causalita' (nessun look-ahead).

**Il problema centrale del regime score**: non tutte le feature sono
disponibili in ogni periodo storico (TVL L2 parte da meta' 2021, OI quasi
mai disponibile). La soluzione adottata e' stata calcolare lo score come
**media pesata delle sole categorie disponibili in quel giorno**, con i
pesi ri-normalizzati sul totale presente, piu' una colonna
`n_categorie_disponibili` che quantifica quanto e' "confermato" un
segnale. Questo ha permesso di non perdere storico utile senza fingere che
dati mancanti siano zero.

I pesi scelti (trend price-based 35%, trend strength 15%, derivatives 20%,
on-chain 30%) e la soglia di attivazione (score >= 60, minimo 2 categorie
disponibili) sono stati verificati con un robustness check (vedi sezione
5) ma **restano scelte largdefault, non ottimizzate matematicamente** — e
questo e' intenzionale: ottimizzarle sui dati storici sarebbe stato
esattamente il data-snooping che il progetto voleva evitare fin
dall'inizio.

---

## 4. Backtest — evoluzione delle regole di ingresso/uscita

**Versione 1 (troppo semplice)**: entra quando il regime diventa bull,
esci quando si rompe o scatta un trailing stop ATR-based. Il robustness
check ha rivelato un problema strutturale: **se un trailing stop stretto
ti fa uscire mentre il trend prosegue, il sistema non rientrava** finche'
il regime non si resettava completamente — restavi fuori dal resto del
trend.

**Versione 2 (corretta)**: aggiunto un layer di ingresso "micro" dentro al
regime macro gia' confermato — pullback (RSI in recupero da ipervenduto
con prezzo sopra EMA50) o breakout (nuovo massimo locale con volume
elevato). Questo ha risolto il problema di rientro e ha portato alla
scoperta (poi verificata) di un trade dominante nell'ottobre 2020.

**Un bug scoperto e corretto durante i test**: se una posizione era ancora
aperta all'ultimo giorno disponibile dei dati, l'equity curve la contava
correttamente ma il trade non veniva mai registrato nel log — disallineando
le metriche aggregate (basate sull'equity) da quelle a livello di singolo
trade (basate sul log). Corretto registrando esplicitamente la posizione
aperta come trade con `exit_reason=still_open_at_backtest_end`.

**Costi ed esecuzione modellati**: fee 0.1% (taker Binance), slippage 0.1%
stimato per esecuzione manuale, segnale calcolato sul close di un giorno
ma eseguito all'apertura del giorno successivo — mai medesimo giorno,
perche' l'esecuzione e' manuale.

---

## 5. Cosa ci dice la validazione — il quadro onesto

Questa e' la parte piu' importante del documento, perche' e' dove si
decide se fidarsi del sistema.

### Backtest completo (con funding + on-chain), ETH, 2018-2026
- CAGR: 34.8% (vs 13.2% buy-and-hold)
- Max drawdown: -55.7% (vs -94.0% buy-and-hold) — **la differenza piu'
  importante**, coerente con la tesi originale che il vero valore sta
  nella protezione dai crolli, non nel rendimento assoluto
- Calmar ratio: 0.625 (vs 0.141 buy-and-hold, ~4.4x)
- Win rate 35.6%, profit factor 3.96 — firma tipica di un sistema
  trend-following sano (perdi spesso piccolo, vinci raramente ma grande)

### Robustness check sui parametri (soglia score, ATR multiplier)
Griglia ±20% attorno ai parametri scelti: performance ragionevolmente
stabile nell'intorno, ma con una **zona di instabilita' identificata a
soglia score >= 66-72** (whipsaw, drawdown che peggiora invece di
migliorare) — non ignorata, documentata come limite noto.

### Stress test sul trade dominante (ottobre 2020, +266%)
- **Verifica 1** (sensibilita' ai parametri di breakout): il trade viene
  catturato nel 100% di 20 combinazioni di parametri testate — non e' un
  artefatto della scelta esatta dei parametri.
- **Verifica 2** (rimozione e ri-simulazione): togliendo quell'ingresso
  specifico, CAGR scende da 34.8% a 30.5%, Calmar da 0.625 a 0.549 — il
  vantaggio si riduce ma resta sostanziale. Il max drawdown resta
  **identico** (-55.7%), a conferma che la protezione dai crolli non
  dipende da quel trade.

### Cross-asset su BTC (solo trend+derivatives, no on-chain)
- Calmar strategia: 0.655 (vs 0.265 buy-and-hold BTC, ~2.5x)
- Pattern coerente con ETH (win rate ~33%, profit factor 3.55) pur usando
  solo 3 categorie su 4 (BTC non ha staking/L2/netflow in questa
  pipeline). Rafforza la fiducia che il vantaggio non sia un artefatto
  specifico di ETH — **ma ETH e BTC sono fortemente correlati**, quindi
  questo non e' un test statisticamente indipendente, e' un secondo data
  point nella stessa "famiglia" di asset.

### Permutation test (il risultato piu' importante da non ignorare)
Testando SOLO lo scheletro di prezzo (trend+ADX+RSI/breakout, senza
funding/on-chain) contro 1000 serie sintetiche con le stesse proprieta'
statistiche ma ordine temporale casuale:
- **Calmar: p=0.110** (sopra la soglia convenzionale di significativita'
  0.05)
- Total return/CAGR: p=0.079

**Conclusione onesta**: lo scheletro di puro prezzo non ha un edge
statisticamente dimostrabile con questo test. Il sistema completo (con
funding+on-chain) ha un Calmar piu' che doppio rispetto allo scheletro
nudo (0.625 vs 0.278), il che suggerisce che la conferma multi-fattore
stia facendo un lavoro di filtro reale — ma **questo non e' stato provato
con lo stesso rigore statistico**, perche' funding e on-chain non si
possono permutare insieme al prezzo in modo statisticamente valido. E' un
limite del metodo, non una dimostrazione che quella parte non funzioni ne'
che funzioni.

---

## 6. Stato attuale, in una frase

Il sistema ha un **vantaggio strutturale plausibile e ben documentato
sulla protezione dai drawdown**, un **edge di puro prezzo statisticamente
debole**, e un **contributo della conferma multi-fattore osservato ma non
statisticamente isolato**. E' una base solida per il paper trading, non
ancora una prova sufficiente per capitale reale.

---

## 7. Limiti noti (riepilogo)

- Open interest storicamente inutilizzabile (limite API, non della
  pipeline)
- Netflow/gas fee partono dal 2021 (scelta nostra per risparmiare crediti
  Dune, non un limite dei dati)
- Il numero di veri cicli bull-bear indipendenti nella storia di ETH resta
  piccolo (2-3) — nessun test statistico puo' aggirare del tutto questo
  limite strutturale
- Il permutation test copre solo lo scheletro di prezzo, non la versione
  completa
- I pesi del regime score sono scelte ragionevoli, non ottimizzate — ne'
  dovrebbero esserlo senza il rischio di overfitting
- Nessun position sizing dinamico ancora implementato (il backtest usa
  100% capitale ogni trade, non l'ATR-based sizing o il Kelly frazionato
  discussi come "vero edge" fin dall'inizio)

---

## 8. Come migliorare — miglioramenti gratuiti/a basso costo

**Position sizing basato su volatilita'** (il pezzo mancante piu'
importante): invece di allocare 100% del capitale a ogni trade, dimensionare
in base all'ATR corrente (meno capitale quando la volatilita' e' alta).
Questo era gia' identificato all'inizio come "l'80% del vantaggio vero" e
non e' ancora stato implementato nel backtest — resta la singola aggiunta
piu' promettente.

**Piramidazione controllata**: aggiungere alla posizione solo se il trade
e' gia' in profitto, mai mediare in perdita. Coerente con "trend-following
puro", non ancora implementato.

**Walk-forward vero** (non solo robustness check): invece di validare su
tutto lo storico in un colpo, ottimizzare su una finestra e testare sulla
finestra successiva mai vista, facendo scorrere la finestra in avanti.
Piu' rigoroso del robustness check attuale, richiede pero' piu' storico
per essere significativo.

**Stress test su blow-off top specifici**: verificare esplicitamente il
comportamento del sistema durante i picchi parabolici storici (maggio
2021, novembre 2021) — menzionato nella roadmap originale, non ancora
fatto in modo mirato (il backtest generale li include ma non li isola).

**Permutation test con block bootstrap**: invece di mescolare
completamente l'ordine dei giorni (che distrugge anche il volatility
clustering, non solo il trend), mescolare blocchi di N giorni consecutivi
— test piu' permissivo e forse piu' realistico come null hypothesis. Da
fare come test aggiuntivo, non in sostituzione di quello gia' fatto.

**Estendere Dune al 2018-2020** per netflow/gas fee (oggi partono dal
2021): coprirebbe anche la prima parte del bull run 2020, a costo di piu'
crediti Dune.

---

## 9. Come migliorare — soluzioni a pagamento che varrebbero l'investimento

Se in futuro si vuole portare questo sistema a un livello professionale,
ecco dove i soldi spesi bene farebbero la differenza maggiore, in ordine
di impatto atteso:

**1. Open interest storico completo** — Coinalyze, Coinglass API, o Coin
Metrics (tipicamente $50-300/mese a seconda del piano) darebbero storico
OI pluriennale invece degli attuali ~30 giorni. E' il gap piu' netto tra
"quello che vorremmo" e "quello che abbiamo", perche' l'OI e' concettualmente
tra i segnali derivatives piu' informativi (eccessi di leva spesso
precedono correzioni) ma oggi e' quasi completamente inutilizzabile nel
backtest.

**2. Dati on-chain via Glassnode Professional** (centinaia di $/mese) —
darebbe accesso a metriche molto piu' raffinate di quelle costruite a mano
su Dune: SOPR, MVRV, dormancy, cohort analysis per holder di lungo/breve
termine. Utile soprattutto se si vuole affinare ulteriormente la categoria
on-chain oltre netflow/staking/TVL/gas.

**3. Un vero motore di backtest** (es. `vectorbt` PRO, o infrastruttura
dedicata) per eseguire walk-forward e permutation test su migliaia di
combinazioni di parametri in tempo ragionevole — il backtest attuale e'
scritto in puro Python/pandas riga per riga, corretto ma non ottimizzato
per velocita' su grandi griglie di parametri.

**4. Dati di liquidazione e order book storici** (Coinglass, Kaiko) — per
costruire una vera "liquidation heatmap" (menzionata nel piano originale
come fonte che quasi nessun retail guarda bene), utile per raffinare il
timing delle uscite parziali.

**5. Infrastruttura di esecuzione** — se in futuro si passasse da manuale
ad automatico (fuori scope originale, che richiedeva esplicitamente
esecuzione manuale), un colocation/VPS vicino ai server dell'exchange
ridurrebbe lo slippage reale sotto quello stimato nel backtest (0.1%).

**Nota importante**: nessuna di queste spese ha senso finche' il paper
trading non ha completato almeno un ciclo di volatilita' — spendere in
dati migliori prima di sapere se la logica di base regge nella pratica
sarebbe mettere il carro davanti ai buoi.

---

## 10. Roadmap per la versione migliore possibile

In ordine di priorita' realistica:

1. **Completa il paper trading** (in corso) — nessun miglioramento tecnico
   vale piu' di questo passo.
2. **Aggiungi il position sizing ATR-based** al backtest e ripeti la
   validazione (robustness + cross-asset) con questa modifica — e' il gap
   piu' importante rimasto nel sistema attuale.
3. **Rifai il permutation test includendo un proxy per funding/on-chain**
   (es. permutando le serie derivatives/on-chain in blocco insieme al
   prezzo, preservando la relazione tra le due invece di distruggerla) per
   provare a isolare statisticamente il contributo della conferma
   multi-fattore, oggi solo osservato.
4. **Estendi lo storico Dune** (netflow/gas al 2018-2020) se il paper
   trading conferma che vale la pena investire ulteriore tempo nel
   sistema.
5. **Valuta l'upgrade a OI storico a pagamento** solo dopo il punto 1 e se
   il paper trading suggerisce che la categoria derivatives meriterebbe
   piu' peso di quanto i dati attuali permettano di verificare.
6. **Rivedi i pesi del regime score con walk-forward vero**, non prima di
   avere accumulato piu' storico di paper trading da usare come ulteriore
   validazione fuori campione.

---

## 11. L'ultima nota, la piu' importante

Il lavoro tecnico fatto qui — pipeline dati validata con sanity check
automatici, feature causali testate esplicitamente per assenza di
look-ahead, regime score con gestione esplicita dei dati mancanti,
backtest con costi realistici, robustness check, stress test mirato,
validazione cross-asset, permutation test — e' un livello di rigore che la
stragrande maggioranza dei sistemi di trading retail non ha mai. Questo
non garantisce che il sistema sia profittevole in futuro: nessun livello
di rigore puo' garantirlo, i mercati cambiano e i cicli storici disponibili
restano pochi.

Quello che il rigore garantisce e' di sapere onestamente **quanto ci si
puo' fidare** di ogni singolo pezzo — dove l'evidenza e' solida (protezione
dai drawdown, robustezza ai parametri, generalizzazione a BTC) e dove resta
debole o non dimostrata (edge di puro prezzo, contributo isolato
dell'on-chain). Il passo finale, il paper trading, e' l'unico che puo'
davvero rispondere alla domanda che conta: se tu, seguendo questo sistema
con disciplina, faresti meglio che senza.