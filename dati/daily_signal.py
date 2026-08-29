"""
daily_signal.py

Script da lanciare UNA VOLTA AL GIORNO (dopo la chiusura della candela
daily, es. poco dopo 00:00 UTC) per il paper trading. Fa 3 cose:

  1. Aggiorna i dati (OHLCV, funding, OI, TVL -- tutto cio' che non
     richiede Dune/crediti a pagamento; netflow/staking/gas vanno
     aggiornati separatamente e periodicamente, vedi nota sotto)
  2. Ricalcola feature e regime score con i dati freschi
  3. Decide il segnale di oggi usando la STESSA identica logica del
     backtest (stessa funzione, stesso codice -- nessuna divergenza
     possibile tra "quello che abbiamo validato" e "quello che gira live")

Mantiene uno stato persistente (data/processed/paper_trading_state.json)
perche' il sistema deve ricordarsi se sei gia' in posizione da un giorno
all'altro. Registra ogni segnale in un journal CSV per poter confrontare
nel tempo segnali generati vs le tue esecuzioni reali (fondamentale,
discusso fin dall'inizio: "la differenza -- slippage comportamentale --
ti dice se stai davvero seguendo il sistema").

REGOLA DI ESECUZIONE: il segnale di oggi si basa sul CLOSE della candela
daily piu' recente (ieri, se lanci lo script stamattina). Esegui l'ordine
manualmente il prima possibile dopo aver letto il segnale -- piu' aspetti,
piu' ti allontani dal prezzo su cui il segnale e' stato calcolato.

Uso:
    python daily_signal.py                  # aggiorna tutto e genera il segnale
    python daily_signal.py --skip-refresh    # usa i dati gia' presenti (utile per test/debug)
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pandas as pd

from backtest import load_data

STATE_PATH = os.path.join("data", "processed", "paper_trading_state.json")
JOURNAL_PATH = os.path.join("data", "processed", "signals_journal.csv")

ATR_MULTIPLIER = 3.0  # deve restare uguale a quello usato nel backtest validato


def refresh_data():
    """
    Aggiorna le fonti che si possono richiamare senza costi/limiti (nessuna
    key o crediti a consumo). netflow/staking/gas (Dune) NON vengono
    aggiornati qui automaticamente -- vanno rilanciati periodicamente a
    mano (es. settimanalmente) con fetch_dune.py, perche' consumano
    crediti a ogni esecuzione. Se non li aggiorni per qualche giorno non
    e' un problema: il regime score si adatta gia' automaticamente (le
    categorie con dati non aggiornati/mancanti vengono escluse dalla media
    pesata, non riempite con zeri).
    """
    print("Aggiornamento OHLCV, funding, open interest, TVL L2...")
    steps = [
        [sys.executable, "fetch_ohlcv.py", "--timeframe", "1d"],
        [sys.executable, "fetch_derivatives.py"],
        [sys.executable, "fetch_defillama.py"],
    ]
    for step in steps:
        result = subprocess.run(step, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"ATTENZIONE: {' '.join(step)} ha fallito:")
            print(result.stderr[-1000:])
        else:
            print(f"  OK: {' '.join(step)}")

    print("\nPromemoria: netflow/staking/gas fee (Dune) non sono stati aggiornati automaticamente.")
    print("Se e' passata piu' di una settimana dall'ultimo aggiornamento, rilancia a mano:")
    print("  python fetch_dune.py --query-id <ID_NETFLOW> --name eth_netflow_daily")
    print("  python fetch_dune.py --query-id <ID_STAKING> --name staking_flow_daily")
    print("  python fetch_dune.py --query-id <ID_GAS> --name gas_fee_daily")


def rebuild_pipeline():
    print("\nRicalcolo feature e regime score...")
    for step in [[sys.executable, "build_features.py"], [sys.executable, "regime_score.py"]]:
        result = subprocess.run(step, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"ERRORE in {' '.join(step)}:")
            print(result.stderr[-2000:])
            sys.exit(1)


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {"in_position": False, "entry_date": None, "entry_price": None,
                "highest_close_since_entry": None, "entry_reason": None}
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, default=str)


def append_journal(row: dict):
    os.makedirs(os.path.dirname(JOURNAL_PATH), exist_ok=True)
    row_to_write = dict(row)
    row_to_write["date_segnale"] = str(row_to_write["date_segnale"])

    if os.path.exists(JOURNAL_PATH):
        existing = pd.read_csv(JOURNAL_PATH)
        if not existing.empty and str(existing.iloc[-1]["date_segnale"]) == row_to_write["date_segnale"]:
            print(f"\n(Segnale per {row_to_write['date_segnale']} gia' presente nel journal -- "
                  f"aggiorno la riga invece di duplicarla, probabilmente hai rilanciato lo script oggi)")
            existing = existing.iloc[:-1]
            updated = pd.concat([existing, pd.DataFrame([row_to_write])], ignore_index=True)
            updated.to_csv(JOURNAL_PATH, mode="w", header=True, index=False)
            return

    df_row = pd.DataFrame([row_to_write])
    if os.path.exists(JOURNAL_PATH):
        df_row.to_csv(JOURNAL_PATH, mode="a", header=False, index=False)
    else:
        df_row.to_csv(JOURNAL_PATH, mode="w", header=True, index=False)


def decide_signal(latest: pd.Series, previous: pd.Series, state: dict) -> tuple:
    """
    STESSA identica logica di run_backtest() in backtest.py, applicata
    pero' a un solo giorno (l'ultimo disponibile) invece che a un ciclo
    storico. Ritorna (action, reason, new_state).
    """
    if state["in_position"]:
        highest = max(state["highest_close_since_entry"], latest["close"])
        trailing_stop_level = highest - ATR_MULTIPLIER * latest["atr_14"]

        if not latest["bull_regime"]:
            action = "ESCI"
            reason = f"Il regime bull si e' rotto (score={latest['regime_score']:.1f}, sotto soglia o prezzo sotto EMA200)"
        elif latest["close"] < trailing_stop_level:
            action = "ESCI"
            reason = f"Trailing stop colpito: close={latest['close']:.2f} < livello stop={trailing_stop_level:.2f}"
        else:
            action = "NESSUNA AZIONE"
            reason = (f"Resta in posizione. Trailing stop attuale: {trailing_stop_level:.2f} "
                       f"(massimo da entrata: {highest:.2f})")

        new_state = dict(state)
        if action == "ESCI":
            new_state = {"in_position": False, "entry_date": None, "entry_price": None,
                         "highest_close_since_entry": None, "entry_reason": None}
        else:
            new_state["highest_close_since_entry"] = highest

    else:
        is_fresh_transition = latest["bull_regime"] and not previous["bull_regime"]
        entry_signal = is_fresh_transition or (latest["bull_regime"] and (latest["pullback_signal"] or latest["breakout_signal"]))

        if entry_signal:
            action = "ENTRA"
            entry_reason = ("regime_transition" if is_fresh_transition
                             else ("pullback" if latest["pullback_signal"] else "breakout"))
            reason = f"Segnale di ingresso: {entry_reason} (regime_score={latest['regime_score']:.1f})"
            new_state = {"in_position": True, "entry_date": str(latest["date"]),
                         "entry_price": float(latest["close"]), "highest_close_since_entry": float(latest["close"]),
                         "entry_reason": entry_reason}
        else:
            action = "NESSUNA AZIONE"
            reason = (f"Resta fuori dal mercato. regime_score={latest['regime_score']:.1f}, "
                       f"bull_regime={latest['bull_regime']}, categorie disponibili={latest['n_categorie_disponibili']}")
            new_state = dict(state)

    return action, reason, new_state


def main():
    parser = argparse.ArgumentParser(description="Genera il segnale di paper trading del giorno")
    parser.add_argument("--skip-refresh", action="store_true",
                         help="Salta l'aggiornamento dati (usa quelli gia' presenti su disco)")
    args = parser.parse_args()

    if not args.skip_refresh:
        refresh_data()
        rebuild_pipeline()
    else:
        print("Skip refresh: uso i dati gia' presenti su disco.")

    print("\nCaricamento dati per la decisione...")
    df = load_data(symbol="eth")
    df_valid = df[df["n_categorie_disponibili"] >= 2].reset_index(drop=True)

    if len(df_valid) < 2:
        print("ERRORE: dati insufficienti per generare un segnale.")
        return

    latest = df_valid.iloc[-1]
    previous = df_valid.iloc[-2]

    state = load_state()
    action, reason, new_state = decide_signal(latest, previous, state)

    print("\n" + "=" * 70)
    print(f"SEGNALE DEL {latest['date'].date()} (basato sul close di quella giornata)")
    print("=" * 70)
    print(f"\n  AZIONE: {action}")
    print(f"  Motivo: {reason}")
    print(f"\n  Prezzo di riferimento (close): {latest['close']:.2f}")
    print(f"  Regime score: {latest['regime_score']:.1f} | bull_regime: {latest['bull_regime']}")
    print(f"  Categorie disponibili: {latest['n_categorie_disponibili']}/4")

    if action != "NESSUNA AZIONE":
        print(f"\n  >>> Esegui manualmente il prima possibile e conferma nel tuo trade journal <<<")

    save_state(new_state)
    append_journal({
        "timestamp_generazione": datetime.now(timezone.utc).isoformat(),
        "date_segnale": latest["date"],
        "action": action,
        "reason": reason,
        "close_price": latest["close"],
        "regime_score": latest["regime_score"],
        "n_categorie_disponibili": latest["n_categorie_disponibili"],
    })
    print(f"\nSegnale registrato in {JOURNAL_PATH}")
    print(f"Stato salvato in {STATE_PATH}")


if __name__ == "__main__":
    main()