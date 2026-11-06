#!/usr/bin/env python3
"""
Cerca Concorsi - notifiche dei bandi NUOVI che rispettano i tuoi filtri (Telegram e/o email).

Gira dopo aggiorna.py (vedi .github/workflows/aggiorna.yml). I filtri si impostano dalla pagina
(pulsante "Usa questi filtri per le notifiche") o con i comandi del bot: stanno in data/notifiche.json.
In data/notificati.json restano gli id gia' segnalati, cosi' ogni bando arriva una volta sola.

Canali (GitHub -> Settings -> Secrets and variables -> Actions):
  Telegram: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
  Email:    EMAIL_USER (es. tuo@gmail.com), EMAIL_PASSWORD (password per le app di Google), EMAIL_TO
Un canale senza secret viene saltato.  NOTIFICA_DRY=1 stampa i messaggi senza inviarli.
"""
from __future__ import annotations

import html
import os
import re
import smtplib
import ssl
import sys
from email.mime.text import MIMEText

from filtri import (BANDI, NOTIFICATI, blocchi_lista, carica_config, descrivi_filtri, frase_conteggio, leggi,
                    msg_bando, pagina_url, passa, scheda, scrivi, telegram)

DRY = os.environ.get("NOTIFICA_DRY") == "1"
MAX_SINGOLI = 15


def email(oggetto: str, corpo_html: str):
    env = lambda k: (os.environ.get(k) or "").strip()
    user, pwd, to = env("EMAIL_USER"), env("EMAIL_PASSWORD"), env("EMAIL_TO") or env("EMAIL_USER")
    if not user or not pwd:
        return
    if DRY:
        print(f"[EMAIL a {to}] {oggetto}\n{re.sub(r'<[^>]+>', ' ', corpo_html)[:1500]}\n")
        return
    m = MIMEText(corpo_html, "html", "utf-8")
    m["Subject"], m["From"], m["To"] = oggetto, f"Cerca Concorsi <{user}>", to
    try:
        with smtplib.SMTP_SSL(env("EMAIL_SMTP") or "smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
            s.login(user, pwd)
            s.sendmail(user, [x.strip() for x in to.split(",")], m.as_string())
    except Exception as ex:
        print(f"Email: invio fallito: {ex}", file=sys.stderr)


def corpo_email(titolo: str, schede: list[dict], filtri_txt: str) -> str:
    e = html.escape
    blocchi = []
    for d in schede:
        posti = f" · {d['posti']} posti" if d["posti"] else ""
        pdf = f' &nbsp;|&nbsp; <a href="{e(d["pdf"])}">Bando PDF</a>' if d["pdf"] else ""
        blocchi.append(
            f'<div style="border:1px solid #dde3ea;border-radius:10px;padding:12px 14px;margin:0 0 10px">'
            f'<div style="font-weight:600;font-size:15px">{e(d["titolo"])}</div>'
            f'<div style="color:#5d6b7c;font-size:13px">{e(d["ente"])} · {e(d["sede"])}{posti}</div>'
            f'<div style="margin:6px 0;font-size:13px">{e(d["tag"])}</div>'
            f'<div style="font-size:14px"><b>Scade il {e(d["scadenza"])}</b> &nbsp;|&nbsp; '
            f'<a href="{e(d["url"])}">Apri su inPA</a>{pdf}</div></div>')
    return (f'<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:640px">'
            f'<h2 style="color:#1f4e79;margin:0 0 4px">{e(titolo)}</h2>'
            f'<p style="color:#5d6b7c;font-size:13px;margin:0 0 12px">Filtri: {e(filtri_txt)}</p>{"".join(blocchi)}'
            f'<p style="color:#5d6b7c;font-size:12px">Etichette ricavate in automatico dal bando: verifica sempre il testo ufficiale. '
            f'<a href="{e(pagina_url())}">Apri Cerca Concorsi</a></p></div>')


def main():
    dati = leggi(BANDI, None)
    if not dati:
        print("data/bandi.json assente: niente da notificare")
        return
    cfg = carica_config()
    f = cfg["filtri"]
    filtri_txt = descrivi_filtri(f)
    visti_file = leggi(NOTIFICATI, None)
    primo_avvio = visti_file is None
    visti = set(visti_file or []) | set(cfg.get("visti_extra") or [])

    bandi = dati.get("bandi", [])
    aperti = {b["id"] for b in bandi}
    adatti = sorted([b for b in bandi if passa(b, f)], key=lambda b: b.get("scadenza") or "9999")
    nuovi = [b for b in adatti if b["id"] not in visti]

    if cfg.get("pausa"):
        print(f"notifiche in pausa: {len(nuovi)} bandi nuovi non inviati")
    elif primo_avvio:
        schede = [scheda(b, f) for b in adatti]
        intro = (f"💥 <b>Notifiche nuovi concorsi attive</b>\nFiltri: {html.escape(filtri_txt)}\n\n"
                 f"{frase_conteggio(len(adatti))}")
        telegram(blocchi_lista(intro, schede) + [f"Da ora ti scrivo solo per i bandi nuovi.\n"
                                                  f"Per cambiare i filtri usa il pulsante 🔔 sulla pagina: {pagina_url()}"], DRY)
        email("Cerca Concorsi: notifiche attive", corpo_email(f"Notifiche attive — {len(adatti)} bandi già aperti", schede, filtri_txt))
        print(f"primo avvio: {len(adatti)} bandi segnati come gia' visti")
    elif nuovi:
        schede = [scheda(b, f) for b in nuovi]
        msgs = [msg_bando(d) for d in schede[:MAX_SINGOLI]]
        if len(schede) > MAX_SINGOLI:
            msgs.append(f"…e altri <b>{len(schede) - MAX_SINGOLI}</b> concorsi nuovi: {pagina_url()}")
        telegram(msgs, DRY)
        n = len(schede)
        email(f"💥 {n} nuov{'o concorso' if n == 1 else 'i concorsi'} per te", corpo_email(f"{n} nuov{'o concorso' if n == 1 else 'i concorsi'} per te", schede, filtri_txt))
        print(f"notificati {n} bandi nuovi")
    else:
        print("nessun bando nuovo da notificare")

    # si tengono solo gli id ancora aperti (il file non cresce all'infinito)
    if not DRY:
        scrivi(NOTIFICATI, sorted((visti | {b["id"] for b in adatti}) & aperti))


if __name__ == "__main__":
    main()
