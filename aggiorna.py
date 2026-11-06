#!/usr/bin/env python3
"""
Cerca Concorsi - raccolta e classificazione dei bandi aperti su inPA.

Gira su GitHub Actions (vedi .github/workflows/aggiorna.yml) e produce data/bandi.json,
letto dalla pagina index.html pubblicata su GitHub Pages.

Fonte: API pubblica NON documentata del Portale inPA (la stessa usata da www.inpa.gov.it):
  POST https://portale.inpa.gov.it/concorsi-smart/api/concorso-public-area/search-better?page=N&size=100
       body {"text": "", "status": ["OPEN"]}         (status deve essere una LISTA)
  GET  https://portale.inpa.gov.it/concorsi-smart/api/concorso-public-area/{id}   -> dettaglio
  GET  https://portale.inpa.gov.it/api/media/{mediaId}                            -> PDF allegato

Per ogni bando NUOVO (o modificato) scarica il PDF del bando e ricava:
  - prove:     solo scritto / scritto + orale / solo orale-colloquio / titoli / titoli + colloquio
  - contratto: tempo indeterminato / determinato
  - lauree:    classi (L-31, LM-18, ...), aree disciplinari, "qualsiasi laurea", livello minimo
I bandi gia' analizzati restano in data/cache.json: ogni giro analizza solo le novita'.

Uso:
  python aggiorna.py              giro normale
  python aggiorna.py --probe      stampa un record grezzo della ricerca e del dettaglio (diagnostica)
Variabili d'ambiente:
  MAX_NUOVI   quanti bandi nuovi analizzare per giro (default 400)
  REGIONI     se impostata (es. "Campania"), analizza i PDF solo dei bandi con sede in quelle
              regioni; gli altri compaiono comunque nella pagina, senza analisi. Vuota = tutte.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# --------------------------------------------------------------------------------------
# Configurazione
# --------------------------------------------------------------------------------------
API = "https://portale.inpa.gov.it/concorsi-smart/api"
SEARCH = f"{API}/concorso-public-area/search-better"
DETAIL = f"{API}/concorso-public-area/{{id}}"
MEDIA = "https://portale.inpa.gov.it/api/media/{mid}"
PAGINA_BANDO = "https://www.inpa.gov.it/bandi-e-avvisi/dettaglio-bando-avviso/?concorso_id={id}"

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE_FILE = DATA / "cache.json"
OUT_FILE = DATA / "bandi.json"

MAX_NUOVI = int(os.environ.get("MAX_NUOVI", "400"))
REGIONI = {r.strip().lower() for r in os.environ.get("REGIONI", "").split(",") if r.strip()}
PAUSA = 0.4                 # secondi tra una richiesta e l'altra: essere discreti col portale
MAX_PDF_MB = 20
MAX_PAGINE_PDF = 60
VERSIONE_CLASSIFICATORE = 4  # alzala se cambi le regole: i bandi in cache verranno rianalizzati

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

log = logging.getLogger("cerca-concorsi")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class ErroreFonte(Exception):
    pass


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------
class Client:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "it-IT,it;q=0.9",
            "Origin": "https://www.inpa.gov.it",
            "Referer": "https://www.inpa.gov.it/",
        })

    def _req(self, metodo: str, url: str, **kw) -> requests.Response:
        ultimo = ""
        for i in range(3):
            try:
                r = self.s.request(metodo, url, timeout=45, **kw)
                if r.status_code == 200:
                    time.sleep(PAUSA)
                    return r
                ultimo = f"HTTP {r.status_code} {(r.text or '')[:150]!r}"
            except requests.RequestException as e:
                ultimo = str(e)
            log.warning("%s %s -> %s (tentativo %d)", metodo, url, ultimo, i + 1)
            time.sleep(3 * (i + 1))
        raise ErroreFonte(f"{url}: {ultimo}")

    def cerca(self, pagina: int) -> dict:
        return self._req("POST", f"{SEARCH}?page={pagina}&size=100",
                         json={"text": "", "status": ["OPEN"]}).json()

    def dettaglio(self, id_: str) -> dict:
        return self._req("GET", DETAIL.format(id=id_)).json()

    def pdf(self, media_id: str) -> bytes | None:
        r = self._req("GET", MEDIA.format(mid=media_id), stream=True)
        dati = r.raw.read(MAX_PDF_MB * 1024 * 1024 + 1, decode_content=True)
        if len(dati) > MAX_PDF_MB * 1024 * 1024:
            log.warning("PDF %s oltre %d MB: ignorato", media_id, MAX_PDF_MB)
            return None
        return dati if dati[:5] == b"%PDF-" else None


# --------------------------------------------------------------------------------------
# Estrazione testo
# --------------------------------------------------------------------------------------
def testo_pdf(dati: bytes) -> str:
    try:
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(dati))
        parti = []
        for p in rd.pages[:MAX_PAGINE_PDF]:
            try:
                parti.append(p.extract_text() or "")
            except Exception:  # pagina corrotta: si va avanti
                continue
        return "\n".join(parti)
    except Exception as e:
        log.warning("estrazione PDF fallita: %s", e)
        return ""


def stringhe(obj, max_len=20000) -> str:
    """Tutte le stringhe di un JSON (dettaglio del bando), esclusi URL e id: cosi' non serve
    conoscere a priori i nomi dei campi che contengono requisiti, contratto, ecc."""
    out: list[str] = []

    def visita(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, str) and (k.lower().endswith("id") or v.startswith("http")):
                    continue
                visita(v)
        elif isinstance(o, list):
            for v in o:
                visita(v)
        elif isinstance(o, str) and len(o) > 2:
            out.append(re.sub(r"<[^>]+>", " ", o))  # via eventuale HTML

    visita(obj)
    return " \n ".join(out)[:max_len]


def normalizza(t: str) -> str:
    t = t.replace("’", "'").replace("‘", "'").replace(" ", " ")
    t = t.replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-")
    t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", t)   # parole spezzate a fine riga
    t = re.sub(r"\s+", " ", t)
    return t.lower()


def estratto(t: str, m: re.Match, prima=90, dopo=160) -> str:
    a, b = max(0, m.start() - prima), min(len(t), m.end() + dopo)
    return ("…" if a else "") + t[a:b].strip() + ("…" if b < len(t) else "")


# --------------------------------------------------------------------------------------
# Classificazione: PROVE
# --------------------------------------------------------------------------------------
RE_SCRITTA = re.compile(r"prov[ae]\s+(?:d'esame\s+)?scritt[ae]|elaborato\s+scritto|test\s+scritto|"
                        r"quesiti\s+a\s+risposta\s+(?:multipla|sintetica|aperta)")
RE_PRATICA = re.compile(r"prov[ae]\s+(?:teorico[\s-]*)?pratic[ah]e?|prova\s+tecnico[\s-]*pratica")
RE_ORALE = re.compile(r"prov[ae]\s+oral[ei]|\bcolloqui[oi]\b|esame\s+orale")
RE_NO_ORALE = re.compile(
    r"(?:senza|non\s+(?:è|e'|e|sarà|sara')\s+previst[ao]|non\s+prevede|esclus[ao])\s+"
    r"(?:la\s+|alcuna\s+|una\s+|lo\s+svolgimento\s+(?:di\s+|della\s+)?)?(?:successiva\s+)?"
    r"(?:prova\s+orale|colloquio)")
RE_UNICA_SCRITTA = re.compile(
    r"(?:un'?\s*unica|una\s+sola|una\s+unica|sola|unica)\s+prova\s+(?:d'esame\s+)?scritta|"
    r"prova\s+scritta\s+unica|prova\s+unica\s+scritta|esclusivamente\s+(?:in\s+)?(?:una\s+)?prova\s+scritta|"
    r"unica\s+prova\s+(?:d'esame\s+)?(?:consistente\s+in|a\s+risposta\s+multipla|mediante\s+test)")
RE_PRESELETTIVA = re.compile(r"preselettiv|preselezion")
RE_COLLOQUIO_CONOSCITIVO = re.compile(r"colloquio\s+(?:di\s+)?(?:lingua|conoscitivo|motivazional)")

TIPO_PROCEDURA = {   # campo tipoProcedura dell'API inPA
    "TITOLI": "titoli",
    "COLLOQUIO": "solo_orale",
    "TITOLI_COLLOQUIO": "titoli_colloquio",
}


def classifica_prove(t: str, tipo_proc: str | None, ha_pdf: bool) -> dict:
    tipo_proc = (tipo_proc or "").upper()
    preselettiva = bool(RE_PRESELETTIVA.search(t))
    if tipo_proc in TIPO_PROCEDURA:
        return {"prove": TIPO_PROCEDURA[tipo_proc], "preselettiva": preselettiva, "pratica": False,
                "conf": "alta", "perche": f"Tipo procedura su inPA: {tipo_proc.replace('_', ' ').lower()}"}

    pulito = RE_NO_ORALE.sub(" ", t)
    # i "colloqui di lingua" sono quasi sempre dentro la prova orale: non contano da soli
    m_scr = RE_SCRITTA.search(pulito)
    m_pra = RE_PRATICA.search(pulito)
    orali = [m for m in RE_ORALE.finditer(pulito) if not RE_COLLOQUIO_CONOSCITIVO.match(pulito, m.start())]
    m_ora = orali[0] if orali else None
    m_unica = RE_UNICA_SCRITTA.search(pulito)
    m_no_ora = RE_NO_ORALE.search(t)

    if (m_scr or m_pra or m_unica) and not m_ora:
        m = m_unica or m_no_ora or m_scr or m_pra
        return {"prove": "solo_scritto", "preselettiva": preselettiva, "pratica": bool(m_pra),
                "conf": "alta" if (m_unica or m_no_ora) else "media", "perche": estratto(t if m is m_no_ora else pulito, m)}
    if (m_scr or m_pra or m_unica) and m_ora:
        m = m_scr or m_unica or m_pra
        return {"prove": "scritto_orale", "preselettiva": preselettiva, "pratica": bool(m_pra),
                "conf": "media" if (m_unica and len(orali) <= 1) else "alta", "perche": estratto(pulito, m)}
    if m_ora:
        return {"prove": "solo_orale", "preselettiva": preselettiva, "pratica": False,
                "conf": "media", "perche": estratto(pulito, m_ora)}
    if tipo_proc in ("ESAMI", "TITOLI_ESAMI"):
        return {"prove": "non_rilevato", "preselettiva": preselettiva, "pratica": False, "conf": "bassa",
                "perche": "Procedura per " + ("titoli ed esami" if tipo_proc == "TITOLI_ESAMI" else "esami")
                          + (": dettaglio prove non trovato nel bando" if ha_pdf else ": PDF del bando non leggibile")}
    return {"prove": "non_rilevato", "preselettiva": preselettiva, "pratica": False, "conf": "bassa",
            "perche": "Prove non trovate nel testo" if ha_pdf else "PDF del bando non disponibile o non leggibile"}


# --------------------------------------------------------------------------------------
# Classificazione: CONTRATTO
# --------------------------------------------------------------------------------------
RE_INDET = re.compile(r"tempo\s+indeterminat[oaie]|ruolo\s+a\s+tempo\s+indet")
RE_DET = re.compile(r"tempo\s+determinat[oaie]|(?<!in)determinat[oa]\s+(?:della\s+durata|per\s+(?:la\s+durata|mesi|anni))|"
                    r"art(?:icolo|\.)?\s*110\b|contratto\s+a\s+termine|"
                    r"incaric[oh]i?\s+a\s+tempo")
RE_PART = re.compile(r"part[\s-]?time|tempo\s+parziale")


def classifica_contratto(intestazione: str, t: str) -> dict:
    """intestazione = titolo + profilo (affidabile); t = testo completo (rumoroso)."""
    hi, hd = RE_INDET.search(intestazione), RE_DET.search(intestazione)
    part = bool(RE_PART.search(intestazione) or RE_PART.search(t[:6000]))
    if hi and not hd:
        return {"contratto": "indeterminato", "part_time": part, "conf": "alta", "perche": estratto(intestazione, hi, 60, 80)}
    if hd and not hi:
        return {"contratto": "determinato", "part_time": part, "conf": "alta", "perche": estratto(intestazione, hd, 60, 80)}
    if hi and hd:
        return {"contratto": "entrambi", "part_time": part, "conf": "alta", "perche": intestazione[:200]}
    # nel testo: contano le occorrenze nella prima parte (oggetto del bando)
    testa = t[:15000]
    ni = len(RE_INDET.findall(testa))
    nd = len(RE_DET.findall(testa))
    if ni == 0 and nd == 0:
        return {"contratto": "non_rilevato", "part_time": part, "conf": "bassa", "perche": ""}
    if ni >= 2 * nd or (ni > 0 and nd == 0):
        m = RE_INDET.search(testa)
        return {"contratto": "indeterminato", "part_time": part, "conf": "media" if nd else "alta", "perche": estratto(testa, m)}
    if nd >= 2 * ni or (nd > 0 and ni == 0):
        m = RE_DET.search(testa)
        return {"contratto": "determinato", "part_time": part, "conf": "media" if ni else "alta", "perche": estratto(testa, m)}
    m = RE_INDET.search(testa)
    return {"contratto": "entrambi", "part_time": part, "conf": "bassa", "perche": estratto(testa, m)}


# --------------------------------------------------------------------------------------
# Classificazione: TITOLO DI STUDIO
# --------------------------------------------------------------------------------------
RE_CLASSE = re.compile(r"\b(lm|ls|lmg|l)\s*[-/]?\s*(snt\s*[-/]?\s*)?(\d{1,2})\b(?!\s*(?:/\d{4}|\.\d))")
RE_LAUREA_CTX = re.compile(r"laure[ae]|diploma\s+di\s+laurea|classe\s+(?:di\s+laurea|delle\s+lauree)")
RE_QUALSIASI = re.compile(
    r"(?:qualsiasi|qualunque|ogni)\s+(?:tipo\s+di\s+)?(?:diploma\s+di\s+)?laurea|"
    r"laurea\s+(?:triennale\s+|magistrale\s+|specialistica\s+)?(?:conseguita\s+)?in\s+(?:qualsiasi|qualunque)\s+(?:disciplina|classe|ambito)|"
    r"laurea\s+(?:di\s+primo\s+livello\s+)?(?:\(l\)\s+)?(?:o\s+laurea\s+magistrale\s+)?(?:in\s+)?qualsiasi")

AREE = {
    "informatica": r"informatic|scienze\s+dell'?\s*informazione|computer\s+science|data\s+science|cybersecurity|sicurezza\s+informatica",
    "ingegneria": r"ingegneri",
    "giurisprudenza": r"giurisprudenza|scienze\s+giuridiche|servizi\s+giuridici|\blmg\b",
    "economia": r"economi|scienze\s+aziendali|scienze\s+statistiche|statistic|finanza",
    "scienze_politiche": r"scienze\s+politiche|relazioni\s+internazionali|scienze\s+dell'amministrazione|scienze\s+sociali",
    "psicologia": r"psicologi",
    "sanita": r"medicina\s+e\s+chirurgia|infermierist|professioni\s+sanitarie|\bsnt\b|farmacia|biologi",
    "architettura": r"architettur|pianificazione\s+territoriale|urbanistic",
    "servizio_sociale": r"servizio\s+sociale|servizi\s+sociali",
    "educazione": r"scienze\s+dell'educazione|scienze\s+della\s+formazione|pedagogi",
    "umanistica": r"lettere|beni\s+culturali|storia|filosofia|lingue",
    "scienze": r"chimic|fisic|geolog|scienze\s+ambientali|scienze\s+naturali|agrari|forestal|veterinari",
}
RE_AREE = {k: re.compile(v) for k, v in AREE.items()}
# false piste tipiche: "conoscenza delle applicazioni informatiche" e' in quasi tutti i bandi
RE_RUMORE_INFO = re.compile(r"(?:conoscenz\w*|utilizzo|uso)\s+(?:\w+\s+){0,6}(?:informatic|applicazioni|apparecchiature)")

# diploma richiesto: si guarda il testo subito dopo "diploma" (escluso "diploma di laurea")
RE_DIP_CTX = re.compile(r"diplom[ai](?!\s+(?:di\s+)?laure)(?!\s+accademic)(?!\s+di\s+specializzazione)")
AREE_DIPLOMA = {
    "informatica": r"informatic|telecomunicazion|perito\s+(?:industriale\s+)?(?:capotecnico\s+)?(?:per\s+l'|in\s+)?informatic",
    "elettronica": r"elettronic|elettrotecnic|automazione",
    "meccanica": r"meccanic|meccatronic|termotecnic",
    "geometra": r"geometr|costruzioni,?\s+ambiente\s+e\s+territorio|\bc\.?a\.?t\.?\b",
    "ragioneria": r"ragioner|amministrazione,?\s+finanza\s+e\s+marketing|\bafm\b|perito\s+commercial|tecnico\s+commercial",
    "agraria": r"agrari|agrotecnic|agroaliment",
    "chimica": r"chimic|biotecnolog",
    "socio_sanitario": r"socio[\s-]*sanitari|servizi\s+sociali|dirigente\s+di\s+comunit",
    "turismo": r"turism|alberghier",
}
RE_AREE_DIP = {k: re.compile(v) for k, v in AREE_DIPLOMA.items()}
RE_DIP_QUALSIASI = re.compile(
    r"(?:qualsiasi|qualunque|ogni)\s+(?:tipo\s+di\s+)?diploma|diploma\s+di\s+(?:istruzione|scuola)\s+secondaria\s+(?:di\s+secondo\s+grado|superiore)"
    r"(?:\s*\([^)]{0,40}\))?\s*(?:[,.;]|o\s+equipollente|valido|di\s+durata|quinquennale|conseguit)|altro\s+diploma|diploma\s+di\s+maturit[aà]\s*(?:[,.;]|\(|o\s+equ)")

LIVELLI = {"nessuno": 0, "diploma": 1, "triennale": 2, "magistrale": 3}
RE_LIV_TRIEN = re.compile(r"laurea\s+triennale|laurea\s+di\s+primo\s+livello|laurea\s+\(l\)|\bl\s*-\s*\d{1,2}\b|laurea\s+breve")
RE_LIV_MAG = re.compile(r"laurea\s+magistrale|laurea\s+specialistica|vecchio\s+ordinamento|\blm\s*-\s*\d{1,2}\b|\bls\s*-?\s*\d{1,2}\b")
RE_LIV_DIP = re.compile(r"diploma\s+di\s+(?:scuola\s+secondaria|istruzione\s+secondaria|maturit|scuola\s+media\s+superiore)|"
                        r"diploma\s+(?:quinquennale|di\s+qualifica)|maturit[àa]")
RE_LIV_OBB = re.compile(r"scuola\s+dell'obbligo|licenza\s+(?:media|di\s+scuola\s+secondaria\s+di\s+primo\s+grado)")
PROFILI = [   # profilo -> livello minimo (CCNL PA: aree operatori/istruttori/funzionari/dirigenza)
    (re.compile(r"dirigent|ricercator|tecnolog[oi]\b|medic[oi]\b|veterinari"), "magistrale"),
    (re.compile(r"funzionari|specialist|collaboratore\s+professionale\s+sanitario|infermier|assistente\s+sociale"), "triennale"),
    (re.compile(r"istruttor|assistente|agente|ispettore|collaboratore\s+amministrativo"), "diploma"),
    (re.compile(r"operatore|esecutor|autista|usciere|ausiliari|commesso|bidell"), "nessuno"),
]


def classifica_titolo(intestazione: str, t: str) -> dict:
    classi = set()
    for m in RE_CLASSE.finditer(t):
        pref, snt, num = m.group(1).upper(), m.group(2), int(m.group(3))
        if pref == "L" and num > 43 or pref in ("LM", "LS") and num > 99:
            continue
        if pref == "LMG":
            classi.add("LMG-01"); continue
        classi.add(f"{pref}-SNT{num}" if snt else f"{pref}-{num}")
    # finestre di testo dopo "laurea": li' cerchiamo area disciplinare e "qualsiasi laurea"
    finestre = [t[m.start(): m.start() + 320] for m in RE_LAUREA_CTX.finditer(t)][:120]
    ctx = " | ".join(RE_RUMORE_INFO.sub(" ", f) for f in finestre)
    aree = sorted(k for k, r in RE_AREE.items() if r.search(ctx))
    m_q = RE_QUALSIASI.search(t)

    livello, fonte_liv = None, ""
    for r, liv in PROFILI:
        if r.search(intestazione):
            livello, fonte_liv = liv, "profilo"
            break
    if livello is None:
        trovati = []
        if RE_LIV_OBB.search(t): trovati.append("nessuno")
        if RE_LIV_DIP.search(t): trovati.append("diploma")
        if RE_LIV_TRIEN.search(t): trovati.append("triennale")
        if RE_LIV_MAG.search(t): trovati.append("magistrale")
        # i titoli piu' bassi spesso compaiono tra i "titoli valutabili": se c'e' una laurea, vale quella
        lauree = [x for x in trovati if x in ("triennale", "magistrale")]
        scelta = lauree or trovati
        if scelta:
            livello, fonte_liv = min(scelta, key=LIVELLI.get), "testo"
        elif RE_LAUREA_CTX.search(t):
            livello, fonte_liv = "triennale", "testo"
    # se il bando elenca solo classi magistrali (LM/LS/LMG) e mai triennali, serve la magistrale
    if classi and all(not c.startswith("L-") for c in classi) and not RE_LIV_TRIEN.search(t) \
            and LIVELLI.get(livello or "nessuno", 0) < 3:
        livello, fonte_liv = "magistrale", "classi"
    # diplomi: solo se il bando e' di livello diploma (altrimenti "diploma" e' quasi sempre rumore)
    diplomi, diploma_qualsiasi, m_dip = [], False, None
    if livello in ("diploma", "nessuno", None):
        fin_dip = [t[m.start(): m.start() + 260] for m in RE_DIP_CTX.finditer(t)][:80]
        ctx_dip = " | ".join(RE_RUMORE_INFO.sub(" ", f) for f in fin_dip)
        diplomi = sorted(k for k, r in RE_AREE_DIP.items() if r.search(ctx_dip))
        diploma_qualsiasi = bool(RE_DIP_QUALSIASI.search(t)) or not diplomi
        for f in fin_dip:
            if any(r.search(RE_RUMORE_INFO.sub(" ", f)) for r in RE_AREE_DIP.values()):
                m_dip = re.search(r".+", f[:220]); break
        if m_dip is None and fin_dip:
            m_dip = re.search(r".+", fin_dip[0][:220])
    m_ev = next((m for m in RE_CLASSE.finditer(t)), None) or m_q or RE_LAUREA_CTX.search(t)
    return {
        "classi": sorted(classi, key=lambda c: (c.split("-")[0] != "L", c)),
        "aree": aree,
        "qualsiasi_laurea": bool(m_q),
        "livello": livello or "non_rilevato",
        "livello_da": fonte_liv,
        "diplomi": diplomi,
        "diploma_qualsiasi": diploma_qualsiasi,
        "perche": (m_dip.group(0) + "…") if (m_dip and livello in ("diploma", "nessuno")) else (estratto(t, m_ev) if m_ev else ""),
    }


# --------------------------------------------------------------------------------------
# Record
# --------------------------------------------------------------------------------------
def firma(raw: dict) -> str:
    return "|".join(str(raw.get(k) or "") for k in ("titolo", "dataScadenza", "allegatoMediaId", "dataPubblicazione"))


def base_record(raw: dict) -> dict:
    sedi = [s for s in (raw.get("sedi") or []) if isinstance(s, str)]
    enti = [e for e in (raw.get("entiRiferimento") or []) if isinstance(e, str)]
    cat = [c for c in (raw.get("categorie") or []) if isinstance(c, str)]
    posti = raw.get("numPosti")
    if not isinstance(posti, (int, float)) or posti <= 0 or posti >= 9999:
        posti = None
    return {
        "id": raw["id"],
        "titolo": (raw.get("titolo") or "").strip(),
        "profilo": (raw.get("figuraRicercata") or "").strip(),
        "ente": enti[0] if enti else "",
        "sedi": sedi,
        "regione": sedi[0] if sedi else "",
        "categoria": cat[0] if cat else "",
        "settori": [s for s in (raw.get("settori") or []) if isinstance(s, str)],
        "posti": int(posti) if posti else None,
        "pubblicato": (raw.get("dataPubblicazione") or "")[:10],
        "scadenza": (raw.get("dataScadenza") or "")[:16],
        "tipo_procedura": raw.get("tipoProcedura") or "",
        "url": PAGINA_BANDO.format(id=raw["id"]),
        "pdf": MEDIA.format(mid=raw["allegatoMediaId"]) if raw.get("allegatoMediaId") else "",
    }


def analizza(cli: Client, raw: dict) -> dict:
    rec = base_record(raw)
    testo_det, allegati = "", []
    try:
        det = cli.dettaglio(raw["id"])
        testo_det = stringhe(det)
        if isinstance(det.get("company"), dict) and det["company"].get("name"):
            rec["ente"] = det["company"]["name"]
        allegati = sorted([a for a in (det.get("allegati") or []) if isinstance(a, dict) and a.get("mediaId")],
                          key=lambda a: (a.get("tipo") != "BANDO_CONCORSO", a.get("sequence") or 99))
    except ErroreFonte as e:
        log.warning("dettaglio %s non disponibile: %s", raw["id"], e)

    media = [a["mediaId"] for a in allegati[:2]] or ([raw["allegatoMediaId"]] if raw.get("allegatoMediaId") else [])
    if media:
        rec["pdf"] = MEDIA.format(mid=media[0])
    testo_pdf_tot = ""
    for mid in media:
        try:
            dati = cli.pdf(mid)
        except ErroreFonte as e:
            log.warning("PDF %s non scaricato: %s", mid, e)
            continue
        if dati:
            testo_pdf_tot += "\n" + testo_pdf(dati)
        if len(testo_pdf_tot) > 3000:   # basta il bando principale
            break

    ha_pdf = len(testo_pdf_tot.strip()) > 500
    intest = normalizza(f"{rec['titolo']} . {rec['profilo']}")
    t = normalizza(f"{rec['titolo']} . {rec['profilo']} . {testo_det} . {testo_pdf_tot}")

    comparto = ""
    m = re.search(r"graduatori", t)
    if m:
        from filtri import comparto_da_testo
        comparto = comparto_da_testo(t[:20000])
    rec["analisi"] = {
        "comparto": comparto,
        "prove": classifica_prove(t, rec["tipo_procedura"], ha_pdf),
        "contratto": classifica_contratto(intest, t),
        "titolo": classifica_titolo(intest, t),
        "pdf_letto": ha_pdf,
        "v": VERSIONE_CLASSIFICATORE,
    }
    return rec


def in_regioni(raw: dict) -> bool:
    if not REGIONI:
        return True
    sedi = " ".join(s for s in (raw.get("sedi") or []) if isinstance(s, str)).lower()
    return any(r in sedi for r in REGIONI) or not sedi or "nazional" in sedi or "italia" in sedi


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------
def scarica_elenco(cli: Client) -> list[dict]:
    tutti, pagina = [], 0
    while pagina < 60:
        dati = cli.cerca(pagina)
        items = dati.get("content")
        if not isinstance(items, list):
            raise ErroreFonte("risposta senza 'content': la struttura dell'API e' cambiata")
        if pagina == 0 and not items:
            raise ErroreFonte("prima pagina vuota: probabile blocco del portale o API cambiata")
        tutti.extend(i for i in items if isinstance(i, dict) and i.get("id"))
        if dati.get("last", True):
            break
        pagina += 1
    log.info("inPA: %d bandi aperti (%s dichiarati)", len(tutti), dati.get("totalElements"))
    return tutti


def carica_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def main():
    cli = Client()
    if "--probe" in sys.argv:
        primo = cli.cerca(0)["content"][0]
        print("=== RECORD RICERCA ===")
        print(json.dumps(primo, ensure_ascii=False, indent=1)[:4000])
        print("=== DETTAGLIO ===")
        print(json.dumps(cli.dettaglio(primo["id"]), ensure_ascii=False, indent=1)[:6000])
        return

    DATA.mkdir(exist_ok=True)
    cache: dict = carica_json(CACHE_FILE, {})
    elenco = scarica_elenco(cli)

    nuovi = [r for r in elenco
             if in_regioni(r) and (r["id"] not in cache or cache[r["id"]].get("firma") != firma(r)
                                   or cache[r["id"]].get("rec", {}).get("analisi", {}).get("v") != VERSIONE_CLASSIFICATORE)]
    # prima i bandi in scadenza piu' vicina: sono quelli che servono subito
    nuovi.sort(key=lambda r: r.get("dataScadenza") or "9999")
    da_fare = nuovi[:MAX_NUOVI]
    log.info("da analizzare: %d (questo giro: %d)", len(nuovi), len(da_fare))

    t0 = time.time()
    for i, raw in enumerate(da_fare, 1):
        try:
            cache[raw["id"]] = {"firma": firma(raw), "rec": analizza(cli, raw)}
        except Exception as e:  # un bando difettoso non deve fermare il giro
            log.warning("bando %s saltato: %s", raw.get("id"), e)
        if i % 25 == 0:
            log.info("  %d/%d analizzati (%.0f s)", i, len(da_fare), time.time() - t0)
            CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")  # salvataggio intermedio

    aperti = {r["id"] for r in elenco}
    cache = {k: v for k, v in cache.items() if k in aperti}   # via i bandi chiusi
    bandi = []
    for raw in elenco:
        c = cache.get(raw["id"])
        if c and c.get("firma") == firma(raw):
            bandi.append(c["rec"])
        else:
            bandi.append({**base_record(raw), "analisi": None})   # in coda per il prossimo giro

    analizzati = sum(1 for b in bandi if b.get("analisi"))
    uscita = {
        "aggiornato": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "totale": len(bandi),
        "analizzati": analizzati,
        "in_coda": len(bandi) - analizzati,
        "bandi": bandi,
    }
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    OUT_FILE.write_text(json.dumps(uscita, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("scritto %s: %d bandi, %d analizzati, %d in coda", OUT_FILE.name, len(bandi), analizzati, len(bandi) - analizzati)


if __name__ == "__main__":
    try:
        main()
    except ErroreFonte as e:
        log.error("FONTE NON DISPONIBILE: %s", e)
        sys.exit(1)
