#!/usr/bin/env python3
"""
Cerca Concorsi - notifiche dei bandi NUOVI che rispettano i tuoi filtri (Telegram e/o email).

Gira dopo aggiorna.py (vedi .github/workflows/aggiorna.yml). Legge data/bandi.json e tiene in
data/notificati.json gli id gia' segnalati, cosi' ogni bando arriva una volta sola.
Al PRIMO avvio non manda l'elenco di tutti i bandi gia' aperti: li segna come visti e invia
solo un messaggio di conferma con il conteggio.

Canali (GitHub -> Settings -> Secrets and variables -> Actions -> New repository secret):
  Telegram: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
  Email:    EMAIL_USER (es. tuo@gmail.com), EMAIL_PASSWORD (password per le app di Google), EMAIL_TO
Un canale senza secret viene semplicemente saltato.

Filtri (variabili d'ambiente, impostate nel workflow):
  NOTIFICA_REGIONI          es. "Campania"  (piu' regioni separate da virgola; vuoto = tutte)
  NOTIFICA_LAUREA           es. "L-31"      (vuoto = nessun filtro sulla laurea)
  NOTIFICA_COMPAT           "si" = solo compatibili | "forse" = compatibili o da verificare
  NOTIFICA_PROVE            es. "solo_scritto,scritto_orale"  (vuoto = tutte)
                            valori: solo_scritto, scritto_orale, solo_orale, titoli, titoli_colloquio, non_rilevato
  NOTIFICA_CONTRATTO        es. "indeterminato"  (vuoto = tutti) valori: indeterminato, determinato, non_rilevato
  NOTIFICA_NO_PRESELETTIVA  "1" = escludi i bandi con preselettiva
  NOTIFICA_DRY              "1" = stampa i messaggi senza inviarli (prova)
"""
from __future__ import annotations

import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
BANDI = ROOT / "data" / "bandi.json"
NOTIFICATI = ROOT / "data" / "notificati.json"

env = lambda k, d="": (os.environ.get(k) or d).strip()
lista = lambda k: [x.strip() for x in env(k).split(",") if x.strip()]

REGIONI = [r.lower() for r in lista("NOTIFICA_REGIONI")]
LAUREA = env("NOTIFICA_LAUREA").upper()
COMPAT = env("NOTIFICA_COMPAT", "forse")
PROVE = lista("NOTIFICA_PROVE")
CONTRATTO = lista("NOTIFICA_CONTRATTO")
NO_PRES = env("NOTIFICA_NO_PRESELETTIVA") == "1"
DRY = env("NOTIFICA_DRY") == "1"

repo = env("GITHUB_REPOSITORY", "utente/cerca-concorsi")
PAGINA = env("PAGINA_URL") or f"https://{repo.split('/')[0].lower()}.github.io/{repo.split('/')[1]}/"

ETICHETTE_PROVE = {"solo_scritto": "Solo scritto", "scritto_orale": "Scritto + orale", "solo_orale": "Solo orale/colloquio",
                   "titoli": "Solo titoli", "titoli_colloquio": "Titoli + colloquio", "non_rilevato": "Prove da verificare"}
ETICHETTE_CONTR = {"indeterminato": "Tempo indeterminato", "determinato": "Tempo determinato",
                   "entrambi": "Indeterminato e determinato", "non_rilevato": "Contratto da verificare"}
LIV = {"nessuno": 0, "diploma": 1, "triennale": 2, "magistrale": 3}
# stessa tabella di index.html: classe di laurea -> area disciplinare
AREA_CLASSE = {
    "L-31": "informatica", "LM-18": "informatica", "LM-66": "informatica", "LM-91": "informatica",
    "L-8": "ingegneria", "L-7": "ingegneria", "L-9": "ingegneria", "LM-32": "ingegneria", "LM-23": "ingegneria",
    "LM-24": "ingegneria", "LM-25": "ingegneria", "LM-27": "ingegneria", "LM-29": "ingegneria", "LM-33": "ingegneria",
    "LMG-01": "giurisprudenza", "L-14": "giurisprudenza",
    "L-18": "economia", "L-33": "economia", "L-41": "economia", "LM-56": "economia", "LM-77": "economia", "LM-82": "economia", "LM-16": "economia",
    "L-16": "scienze_politiche", "L-36": "scienze_politiche", "L-40": "scienze_politiche", "LM-52": "scienze_politiche",
    "LM-62": "scienze_politiche", "LM-63": "scienze_politiche", "LM-88": "scienze_politiche",
    "L-24": "psicologia", "LM-51": "psicologia", "L-39": "servizio_sociale", "LM-87": "servizio_sociale",
    "L-19": "educazione", "LM-85": "educazione", "LM-57": "educazione",
    "L-17": "architettura", "L-21": "architettura", "LM-4": "architettura", "LM-48": "architettura",
}


def livello_mio(c: str):
    if c.startswith("DIPLOMA"):
        return 1
    if c.startswith("L-"):
        return 2
    if re.match(r"^(LM|LS|LMG)-", c):
        return 3
    return None


def compatibile(b: dict, mia: str) -> str:
    """'si' | 'forse' | 'no' | '?'  (stessa logica della pagina)"""
    a = b.get("analisi")
    if not a or not mia:
        return "?"
    t = a["titolo"]
    mio, req = livello_mio(mia), LIV.get(t["livello"])
    if req is not None and mio is not None and mio < req:
        return "no"
    if req is not None and req <= 1:
        return "si"
    if mia in t["classi"] or t["qualsiasi_laurea"]:
        return "si"
    area = AREA_CLASSE.get(mia)
    if area and area in t["aree"]:
        return "forse"
    if t["classi"] or t["aree"]:
        return "no"
    return "?"


def passa(b: dict) -> bool:
    a = b.get("analisi")
    if not a:
        return False   # ancora in analisi: sara' valutato al prossimo giro
    if REGIONI and not any(r in " ".join(b.get("sedi") or [b.get("regione", "")]).lower() for r in REGIONI):
        return False
    if PROVE and a["prove"]["prove"] not in PROVE:
        return False
    if NO_PRES and a["prove"]["preselettiva"]:
        return False
    if CONTRATTO:
        c = a["contratto"]["contratto"]
        if not (c in CONTRATTO or (c == "entrambi" and {"indeterminato", "determinato"} & set(CONTRATTO))):
            return False
    if LAUREA and COMPAT in ("si", "forse"):
        k = compatibile(b, LAUREA)
        if COMPAT == "si" and k != "si":
            return False
        if COMPAT == "forse" and k == "no":
            return False
    return True


def fmt_data(s: str) -> str:
    try:
        return datetime.fromisoformat(s[:16]).strftime("%d/%m/%Y")
    except ValueError:
        return s[:10]


def descrivi(b: dict) -> dict:
    a = b["analisi"]
    p, c = a["prove"], a["contratto"]
    tag = [ETICHETTE_PROVE.get(p["prove"], p["prove"]) + (" (incerto)" if p["conf"] == "bassa" and p["prove"] != "non_rilevato" else "")]
    if p["preselettiva"]:
        tag.append("preselettiva")
    tag.append(ETICHETTE_CONTR.get(c["contratto"], c["contratto"]))
    if LAUREA:
        k = compatibile(b, LAUREA)
        tag.append({"si": f"compatibile {LAUREA}", "forse": f"{LAUREA} da verificare", "?": "laurea da verificare"}.get(k, ""))
    sede = ", ".join((b.get("sedi") or [])[1:]) or b.get("regione", "")
    return {
        "titolo": b.get("titolo") or b.get("profilo") or "Bando",
        "ente": b.get("ente", ""),
        "sede": sede,
        "posti": b.get("posti"),
        "scadenza": fmt_data(b.get("scadenza", "")),
        "tag": " · ".join(t for t in tag if t),
        "url": b.get("url", ""),
        "pdf": b.get("pdf", ""),
    }


# ----------------------------------------------------------------------------- Telegram
def telegram(testi: list[str]):
    token, chat = env("TELEGRAM_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    for t in testi:
        if DRY:
            print("[TELEGRAM]\n" + t + "\n"); continue
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
                          json={"chat_id": chat, "text": t, "parse_mode": "HTML", "disable_web_page_preview": True})
        if r.status_code != 200:
            print(f"Telegram: errore {r.status_code} {r.text[:200]}", file=sys.stderr)
        time.sleep(1.1)   # limite di Telegram: ~1 messaggio al secondo per chat


def msg_telegram(d: dict) -> str:
    e = html.escape
    righe = [f"🆕 <b>{e(d['titolo'])}</b>", e(" · ".join(x for x in (d["ente"], d["sede"]) if x))]
    if d["posti"]:
        righe[-1] += f" · {d['posti']} {'posto' if d['posti'] == 1 else 'posti'}"
    righe.append(e(d["tag"]))
    righe.append(f"⏳ Scade il <b>{e(d['scadenza'])}</b>")
    link = [f'<a href="{e(d["url"])}">Apri su inPA</a>']
    if d["pdf"]:
        link.append(f'<a href="{e(d["pdf"])}">Bando PDF</a>')
    righe.append(" | ".join(link))
    return "\n".join(righe)


# ----------------------------------------------------------------------------- Email
def email(oggetto: str, corpo_html: str):
    user, pwd, to = env("EMAIL_USER"), env("EMAIL_PASSWORD"), env("EMAIL_TO") or env("EMAIL_USER")
    if not user or not pwd:
        return
    if DRY:
        print(f"[EMAIL a {to}] {oggetto}\n{re.sub(r'<[^>]+>', ' ', corpo_html)[:1500]}\n"); return
    m = MIMEText(corpo_html, "html", "utf-8")
    m["Subject"], m["From"], m["To"] = oggetto, f"Cerca Concorsi <{user}>", to
    host = env("EMAIL_SMTP", "smtp.gmail.com")
    try:
        with smtplib.SMTP_SSL(host, 465, context=ssl.create_default_context(), timeout=30) as s:
            s.login(user, pwd)
            s.sendmail(user, [x.strip() for x in to.split(",")], m.as_string())
    except Exception as ex:
        print(f"Email: invio fallito: {ex}", file=sys.stderr)


def corpo_email(descr: list[dict]) -> str:
    e = html.escape
    blocchi = []
    for d in descr:
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
            f'<h2 style="color:#1f4e79;margin:0 0 12px">{len(descr)} nuovi bandi per te</h2>{"".join(blocchi)}'
            f'<p style="color:#5d6b7c;font-size:12px">Etichette ricavate in automatico dal bando: verifica sempre il testo ufficiale. '
            f'<a href="{e(PAGINA)}">Apri Cerca Concorsi</a></p></div>')


# ----------------------------------------------------------------------------- Main
def main():
    try:
        dati = json.loads(BANDI.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        print("data/bandi.json assente: niente da notificare"); return
    try:
        visti = set(json.loads(NOTIFICATI.read_text(encoding="utf-8")))
        primo_avvio = False
    except (FileNotFoundError, json.JSONDecodeError):
        visti, primo_avvio = set(), True

    bandi = dati.get("bandi", [])
    aperti = {b["id"] for b in bandi}
    adatti = [b for b in bandi if passa(b)]
    nuovi = sorted([b for b in adatti if b["id"] not in visti], key=lambda b: b.get("scadenza") or "9999")

    filtri = ", ".join(x for x in (
        "/".join(lista("NOTIFICA_REGIONI")) or "tutte le regioni",
        f"laurea {LAUREA}" if LAUREA and COMPAT in ("si", "forse") else "",
        "prove: " + "/".join(ETICHETTE_PROVE.get(p, p) for p in PROVE) if PROVE else "",
        "contratto: " + "/".join(CONTRATTO) if CONTRATTO else "",
        "senza preselettiva" if NO_PRES else "") if x)

    if primo_avvio:
        testo = (f"✅ <b>Notifiche Cerca Concorsi attive</b>\nFiltri: {html.escape(filtri)}\n"
                 f"Ci sono già <b>{len(adatti)}</b> bandi aperti che li rispettano: li trovi sulla pagina.\n"
                 f"Da ora ti scrivo solo per quelli nuovi.\n{html.escape(PAGINA)}")
        telegram([testo])
        email("Cerca Concorsi: notifiche attive",
              f"<p>Notifiche attive. Filtri: {html.escape(filtri)}.</p><p>Bandi già aperti che li rispettano: <b>{len(adatti)}</b>. "
              f'<a href="{html.escape(PAGINA)}">Apri la pagina</a>.</p><p>Da ora ricevi solo i bandi nuovi.</p>')
        print(f"primo avvio: {len(adatti)} bandi segnati come gia' visti")
    elif nuovi:
        descr = [descrivi(b) for b in nuovi]
        MAX_SINGOLI = 15
        msgs = [msg_telegram(d) for d in descr[:MAX_SINGOLI]]
        if len(descr) > MAX_SINGOLI:
            msgs.append(f"…e altri <b>{len(descr) - MAX_SINGOLI}</b> bandi nuovi: {html.escape(PAGINA)}")
        telegram(msgs)
        email(f"Cerca Concorsi: {len(descr)} nuov{'o bando' if len(descr) == 1 else 'i bandi'}", corpo_email(descr))
        print(f"notificati {len(descr)} bandi nuovi")
    else:
        print("nessun bando nuovo da notificare")

    # si tengono solo gli id ancora aperti (il file non cresce all'infinito)
    visti = (visti | {b["id"] for b in adatti}) & aperti
    if not DRY:
        NOTIFICATI.write_text(json.dumps(sorted(visti)), encoding="utf-8")


if __name__ == "__main__":
    main()
