#!/usr/bin/env python3
"""
Cerca Concorsi - bot Telegram per impostare i filtri delle notifiche.

Gira ogni ~10 minuti su GitHub Actions (.github/workflows/bot.yml): legge i messaggi arrivati al bot,
risponde e salva i filtri in data/notifiche.json. Risponde solo alla chat TELEGRAM_CHAT_ID.

Comandi:
  /start <codice>  imposta i filtri (il link lo crea la pagina con il pulsante 🔔)
  /filtri          mostra i filtri attuali
  /bandi           elenca i bandi aperti che rispettano i filtri
  /pausa           sospende le notifiche        /riprendi  le riattiva
  /aiuto           elenco comandi
"""
from __future__ import annotations

import html
import json
import os
import sys

import requests

from filtri import (BANDI, blocchi_lista, carica_config, decodifica, descrivi_filtri, frase_conteggio, leggi,
                    pagina_url, passa, salva_config, scheda, telegram)

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"

AIUTO = ("<b>Comandi</b>\n"
         "/filtri – filtri attuali delle notifiche\n"
         "/bandi – bandi aperti che li rispettano\n"
         "/pausa – sospendi le notifiche\n"
         "/riprendi – riattiva le notifiche\n\n"
         f"Per cambiare i filtri: imposta quelli che vuoi sulla pagina e tocca <b>🔔 Usa questi filtri per le notifiche</b>.\n{pagina_url()}\n\n"
         "<i>Rispondo in circa un minuto.</i>")


def bandi_adatti(f: dict) -> list[dict]:
    dati = leggi(BANDI, {"bandi": []})
    return sorted([b for b in dati.get("bandi", []) if passa(b, f)], key=lambda b: b.get("scadenza") or "9999")


def gestisci(cfg: dict, msg: dict):
    """risponde a un messaggio e aggiorna cfg (filtri, pausa)"""
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    testo = (msg.get("text") or "").strip()
    if chat_id != CHAT:
        if chat_id:
            requests.post(f"{API}/sendMessage", timeout=20, json={"chat_id": chat_id, "text": "Questo è un bot privato."})
        return
    cmd, _, arg = testo.partition(" ")
    cmd = cmd.split("@")[0].lower()

    if cmd == "/start" and arg.strip():
        nuovi = decodifica(arg.strip())
        if nuovi is None:
            telegram(["⚠️ Link dei filtri non valido. Rifallo dalla pagina con il pulsante 🔔."])
            return
        cfg["filtri"], cfg["pausa"] = nuovi, False
        adatti = bandi_adatti(nuovi)
        cfg["visti_extra"] = [b["id"] for b in adatti]   # questi li vedi ora: non verranno rinotificati
        intro = (f"💥 <b>Notifiche nuovi concorsi aggiornate</b>\nFiltri: {html.escape(descrivi_filtri(nuovi))}\n\n"
                 f"{frase_conteggio(len(adatti))}")
        telegram(blocchi_lista(intro, [scheda(b, nuovi) for b in adatti])
                 + ["Da ora ti scrivo solo per i concorsi nuovi che rispettano questi filtri."])
    elif cmd == "/filtri":
        n = len(bandi_adatti(cfg["filtri"]))
        stato = "⏸ in pausa" if cfg["pausa"] else "▶️ attive"
        telegram([f"🔔 Notifiche {stato}\nFiltri: {html.escape(descrivi_filtri(cfg['filtri']))}\n"
                  f"Bandi aperti che li rispettano: <b>{n}</b> (/bandi per l'elenco)"])
    elif cmd == "/bandi":
        f = cfg["filtri"]
        adatti = bandi_adatti(f)
        telegram(blocchi_lista(f"Filtri: {html.escape(descrivi_filtri(f))}\n\n{frase_conteggio(len(adatti))}",
                               [scheda(b, f) for b in adatti]))
    elif cmd == "/pausa":
        cfg["pausa"] = True
        telegram(["⏸ Notifiche in pausa. Scrivi /riprendi per riattivarle."])
    elif cmd == "/riprendi":
        cfg["pausa"] = False
        telegram(["▶️ Notifiche riattivate."])
    else:
        telegram([("👋 Ciao! " if cmd == "/start" else "") + AIUTO])


def main():
    if not TOKEN or not CHAT:
        print("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID mancanti: bot disattivato")
        return
    cfg = carica_config()
    prima = repr(cfg)

    if not cfg.get("bot"):
        try:
            cfg["bot"] = requests.get(f"{API}/getMe", timeout=20).json()["result"]["username"]
        except Exception as e:
            print(f"getMe fallito: {e}")

    # 1) avviato dal Worker Cloudflare: il messaggio arriva nell'evento (risposta immediata)
    # 2) avviato dalla programmazione: si leggono i messaggi in coda (se non c'e' il webhook)
    aggiornamenti = []
    if os.environ.get("GITHUB_EVENT_NAME") == "repository_dispatch" and os.environ.get("GITHUB_EVENT_PATH"):
        evento = json.loads(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8").read())
        u = (evento.get("client_payload") or {}).get("update")
        if u:
            aggiornamenti = [u]
    else:
        r = requests.get(f"{API}/getUpdates", params={"offset": cfg["offset"] + 1 if cfg["offset"] else None, "timeout": 0,
                                                        "allowed_updates": '["message"]'}, timeout=30)
        if r.status_code == 409:
            print("webhook Cloudflare attivo: i messaggi arrivano dal Worker, niente da leggere qui")
            r = None
        elif r.status_code != 200:
            print(f"getUpdates: HTTP {r.status_code} {r.text[:200]}")
            sys.exit(1)
        if r is not None:
            aggiornamenti = r.json().get("result", [])
    print(f"{len(aggiornamenti)} messaggi nuovi")

    for u in aggiornamenti:
        cfg["offset"] = max(cfg["offset"], u.get("update_id", 0))
        gestisci(cfg, u.get("message") or {})

    if repr(cfg) != prima:
        salva_config(cfg)
        print("configurazione salvata")


if __name__ == "__main__":
    main()
