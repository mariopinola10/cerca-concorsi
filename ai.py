"""
Cerca Concorsi - analisi dei bandi con Google Gemini (livello gratuito).

Serve il secret GEMINI_API_KEY (gratis, senza carta: https://aistudio.google.com -> Get API key).
Senza chiave il modulo non fa nulla e restano le regole di aggiorna.py.

Ogni bando viene letto UNA volta: il risultato finisce in data/cache.json.
Il ritmo delle richieste rispetta i limiti del livello gratuito (richieste/minuto e token/minuto);
se Google risponde "quota esaurita" (429) l'analisi AI si ferma fino al giro successivo.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time

import requests

API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# modelli provati in ordine (il primo che esiste viene usato); si puo' forzare con GEMINI_MODEL
MODELLI = [m for m in [os.environ.get("GEMINI_MODEL", "").strip(),
                       "gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
                       "gemini-flash-latest", "gemini-2.5-flash-lite"] if m]
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
PAUSA_MIN = float(os.environ.get("GEMINI_PAUSA", "7"))   # secondi tra due richieste (~8 al minuto)
MAX_CARATTERI = 32000                                     # ~8-9 mila token per bando

_modello_ok: str | None = None
_ultima = 0.0
esaurito = False          # True dopo un 429: niente piu' AI in questo giro


class QuotaEsaurita(Exception):
    pass


def disponibile() -> bool:
    return bool(API_KEY) and not esaurito


# ----------------------------------------------------------------------------- testo
PAROLE_CHIAVE = re.compile(
    r"requisit|titolo\s+di\s+studio|laure|diplom|prov[ae]\s+(?:d'esame|scritt|oral|pratic|preselett)|preselez|"
    r"colloquio|graduatori|idone|interpell|elenc|comparto|ccnl|tempo\s+(?:in)?determinat|part[\s-]?time|"
    r"profilo|area\s+(?:dei|degli)|categoria|mobilit|domanda\s+di\s+partecipazione", re.I)


def seleziona_testo(t: str, limite: int = MAX_CARATTERI) -> str:
    """inizio del bando + i passaggi intorno alle parole chiave, entro il limite"""
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= limite:
        return t
    pezzi, usati = [t[:12000]], 12000
    fine_ult = 12000
    for m in PAROLE_CHIAVE.finditer(t, 12000):
        if m.start() < fine_ult:
            continue
        a, b = max(fine_ult, m.start() - 600), min(len(t), m.end() + 1200)
        pezzi.append(t[a:b])
        usati += b - a
        fine_ult = b
        if usati >= limite:
            break
    return " […] ".join(pezzi)[:limite]


# ----------------------------------------------------------------------------- schema
S = lambda **k: {"type": "STRING", **k}
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "tipo_avviso": S(enum=["concorso", "manifestazione_interesse_graduatorie", "interpello_elenco_idonei", "mobilita", "altro"]),
        "elenco_idonei": S(description="nome dell'elenco di idonei per gli interpelli (es. ASMEL), altrimenti stringa vuota"),
        "profilo": S(description="profilo richiesto, breve (es. 'Istruttore contabile')"),
        "livello": S(enum=["dirigente", "funzionario", "assistente", "istruttore", "operatore", "non_indicato"]),
        "area_profilo": S(enum=["informatico", "contabile", "amministrativo", "tecnico", "vigilanza", "sociale",
                                "culturale", "comunicazione", "altro"]),
        "comparto_richiesto": S(enum=["funzioni_locali", "funzioni_centrali", "sanita", "istruzione_ricerca", "qualsiasi", "non_indicato"]),
        "prove": S(enum=["solo_scritto", "scritto_orale", "solo_orale", "titoli", "titoli_colloquio", "non_indicato"]),
        "preselettiva": S(enum=["si", "eventuale", "no"]),
        "contratto": S(enum=["indeterminato", "determinato", "entrambi", "non_indicato"]),
        "part_time": {"type": "BOOLEAN"},
        "titolo_livello_minimo": S(enum=["nessuno", "diploma", "triennale", "magistrale", "non_indicato"]),
        "classi_laurea": {"type": "ARRAY", "items": S(), "description": "classi ammesse, es. L-31, LM-18, LMG-01"},
        "aree_laurea": {"type": "ARRAY", "items": S(enum=["informatica", "ingegneria", "giurisprudenza", "economia",
                        "scienze_politiche", "psicologia", "sanita", "architettura", "servizio_sociale", "educazione",
                        "umanistica", "scienze"])},
        "qualsiasi_laurea": {"type": "BOOLEAN"},
        "diplomi": {"type": "ARRAY", "items": S(enum=["informatica", "elettronica", "meccanica", "geometra", "ragioneria",
                    "agraria", "chimica", "socio_sanitario", "turismo"])},
        "qualsiasi_diploma": {"type": "BOOLEAN"},
        "frase_prove": S(description="frase del bando sulle prove, max 200 caratteri"),
        "frase_titolo": S(description="frase del bando sui requisiti di accesso, max 200 caratteri"),
        "frase_contratto": S(description="frase del bando sul contratto, max 160 caratteri"),
        "frase_tipo": S(description="frase che mostra il tipo di avviso o i requisiti di idoneita', max 200 caratteri"),
    },
    "required": ["tipo_avviso", "livello", "area_profilo", "comparto_richiesto", "prove", "preselettiva", "contratto",
                 "titolo_livello_minimo", "classi_laurea", "aree_laurea", "qualsiasi_laurea", "diplomi", "qualsiasi_diploma"],
}

ISTRUZIONI = """Sei un esperto di concorsi pubblici italiani. Leggi l'avviso e compila i campi richiesti SOLO con quello che c'e' scritto.
Regole:
- tipo_avviso: "concorso" = concorso/selezione pubblica aperta (anche se il titolo non contiene la parola concorso);
  "manifestazione_interesse_graduatorie" = utilizzo/scorrimento di idonei in graduatorie di concorsi di altri enti;
  "interpello_elenco_idonei" = interpello su elenchi di idonei (es. ASMEL, elenchi di Unioni o Regioni): in tal caso elenco_idonei = nome dell'elenco;
  "mobilita" = mobilita' volontaria tra enti;
  "altro" = avviamenti a selezione tramite centri per l'impiego (art. 16 L. 56/87), collocamento mirato / categorie protette
  (L. 68/99) riservati agli iscritti alle liste, incarichi, borse, tirocini e tutto cio' che non e' un concorso aperto.
- livello: "Istruttore direttivo", "Area dei Funzionari e dell'Elevata Qualificazione", ex categoria D = funzionario;
  "Area degli Istruttori", ex categoria C = istruttore; "Area degli Assistenti" (Funzioni Centrali) = assistente;
  "Area degli Operatori (esperti)", ex categorie A/B = operatore. L'assistente sociale e' un funzionario.
- comparto_richiesto: solo per manifestazioni/interpelli, SE l'avviso limita gli enti di provenienza delle graduatorie
  (es. "enti che applicano il CCNL Funzioni Locali"); se non pone limiti = "qualsiasi"; per i concorsi = "non_indicato".
- prove: considera le prove d'esame vere e proprie (non la preselettiva). Titoli + colloquio = "titoli_colloquio".
- preselettiva: "si" se prevista sempre; "eventuale" se solo oltre un certo numero di domande; "no" se esclusa o non citata.
- Titolo di studio: livello minimo richiesto per accedere; classi_laurea solo se elencate; qualsiasi_laurea / qualsiasi_diploma
  se va bene qualunque laurea / diploma di scuola secondaria superiore; diplomi = indirizzi specifici richiesti.
  Per manifestazioni/interpelli i requisiti sono l'idoneita': metti titolo_livello_minimo = "non_indicato" se il titolo non e' citato.
- Le frasi devono essere copiate dal testo, brevi."""


# ----------------------------------------------------------------------------- chiamata
def _chiama(modello: str, testo: str, pdf: bytes | None = None) -> requests.Response:
    parti = [{"text": testo}]
    if pdf:
        parti.append({"inline_data": {"mime_type": "application/pdf", "data": base64.b64encode(pdf).decode()}})
    corpo = {
        "systemInstruction": {"parts": [{"text": ISTRUZIONI}]},
        "contents": [{"role": "user", "parts": parti}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA, "temperature": 0},
    }
    return requests.post(URL.format(m=modello), params={"key": API_KEY}, json=corpo, timeout=90)


def analizza(titolo: str, testo: str, pdf: bytes | None = None) -> dict | None:
    """risultato strutturato di Gemini, o None se non disponibile/errore. Solleva QuotaEsaurita sul 429."""
    global _modello_ok, _ultima, esaurito
    if not disponibile():
        return None
    entrata = f"TITOLO: {titolo}\n\nTESTO DELL'AVVISO:\n{seleziona_testo(testo)}"
    if pdf and len(pdf) > 8 * 1024 * 1024:
        pdf = None
    if pdf:
        entrata += "\n\n(Il PDF allegato e' il bando: il testo non era leggibile, leggi direttamente il PDF.)"
    modelli = [_modello_ok] if _modello_ok else MODELLI
    for modello in modelli:
        for tentativo in range(2):
            attesa = PAUSA_MIN - (time.time() - _ultima)
            if attesa > 0:
                time.sleep(attesa)
            _ultima = time.time()
            try:
                r = _chiama(modello, entrata, pdf)
            except requests.RequestException as e:
                print(f"Gemini: errore di rete {e}")
                time.sleep(5)
                continue
            if r.status_code == 404:            # modello inesistente: si prova il successivo
                break
            if r.status_code == 429:
                if "PerDay" in r.text or "per day" in r.text.lower() or tentativo == 1:
                    esaurito = True
                    raise QuotaEsaurita(r.text[:200])
                time.sleep(60)                  # limite al minuto: si aspetta e si riprova
                continue
            if r.status_code >= 500:
                time.sleep(10)
                continue
            if r.status_code != 200:
                print(f"Gemini {modello}: HTTP {r.status_code} {r.text[:200]}")
                return None
            try:
                parti = r.json()["candidates"][0]["content"]["parts"]
                dati = json.loads("".join(p.get("text", "") for p in parti))
            except (KeyError, IndexError, ValueError) as e:
                print(f"Gemini: risposta non leggibile ({e})")
                return None
            _modello_ok = modello
            dati["modello"] = modello
            return dati
    if not _modello_ok:
        print("Gemini: nessun modello disponibile tra", MODELLI)
    return None


# ----------------------------------------------------------------------------- conversione
LIV_AI = {"dirigente": "D", "funzionario": "F", "assistente": "A", "istruttore": "I", "operatore": "O"}
AREA_AI = {"informatico": "INF", "contabile": "CON", "amministrativo": "AMM", "tecnico": "TEC", "vigilanza": "VIG",
           "sociale": "SOC", "culturale": "CUL", "comunicazione": "COM"}
COMP_AI = {"funzioni_locali": "L", "funzioni_centrali": "C", "sanita": "S", "istruzione_ricerca": "U"}
TIPO_AI = {"concorso": "C", "manifestazione_interesse_graduatorie": "G", "interpello_elenco_idonei": "I", "mobilita": "M", "altro": "A"}


def applica(analisi: dict, ai: dict) -> dict:
    """sovrascrive l'analisi delle regole con quella dell'AI (stessa struttura usata da pagina e notifiche)"""
    corto = lambda x, n=220: (x or "").strip()[:n]
    p = analisi["prove"]
    if ai.get("prove") and ai["prove"] != "non_indicato":
        p.update(prove=ai["prove"], conf="alta", perche=corto(ai.get("frase_prove")) or p.get("perche", ""))
    p["preselettiva"] = ai.get("preselettiva") in ("si", "eventuale")
    p["preselettiva_eventuale"] = ai.get("preselettiva") == "eventuale"
    c = analisi["contratto"]
    if ai.get("contratto") and ai["contratto"] != "non_indicato":
        c.update(contratto=ai["contratto"], conf="alta", perche=corto(ai.get("frase_contratto")) or c.get("perche", ""))
    if "part_time" in ai:
        c["part_time"] = bool(ai["part_time"])
    t = analisi["titolo"]
    liv = ai.get("titolo_livello_minimo")
    if liv and liv != "non_indicato":
        t.update(livello=liv, livello_da="ai")
    elif ai.get("tipo_avviso") in ("manifestazione_interesse_graduatorie", "interpello_elenco_idonei"):
        t.update(livello="non_rilevato", livello_da="ai")
    classi = [x.strip().upper().replace(" ", "") for x in ai.get("classi_laurea") or [] if x and x.strip()]
    t.update(classi=sorted(set(classi), key=lambda x: (not x.startswith("L-"), x)),
             aree=sorted(set(ai.get("aree_laurea") or [])), qualsiasi_laurea=bool(ai.get("qualsiasi_laurea")),
             diplomi=sorted(set(ai.get("diplomi") or [])), diploma_qualsiasi=bool(ai.get("qualsiasi_diploma")))
    if ai.get("frase_titolo"):
        t["perche"] = corto(ai["frase_titolo"])
    elenco = corto(ai.get("elenco_idonei"), 60)
    analisi["ai"] = {
        "tipo": TIPO_AI.get(ai.get("tipo_avviso"), ""),
        "elenco": ("ASMEL" if "asmel" in elenco.lower() else "ALTRO") if ai.get("tipo_avviso") == "interpello_elenco_idonei" else "",
        "elenco_nome": elenco,
        "profilo": corto(ai.get("profilo"), 80),
        "liv": LIV_AI.get(ai.get("livello"), ""),
        "area": AREA_AI.get(ai.get("area_profilo"), ""),
        "comparto": COMP_AI.get(ai.get("comparto_richiesto"), ""),
        "perche": corto(ai.get("frase_tipo")),
        "modello": ai.get("modello", ""),
    }
    return analisi
