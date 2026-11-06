"""
Cerca Concorsi - logica comune dei filtri (usata da notifica.py e bot.py).
La stessa logica e' replicata in JavaScript in index.html: se cambi una tabella qui, cambiala anche li'.

Configurazione delle notifiche: data/notifiche.json
  {"bot": "username_del_bot",
   "filtri": {"regioni": ["Campania"], "prove": [], "contratto": [], "titoli": ["L-31"],
              "verificare": true, "nopres": false},
   "pausa": false, "offset": 0, "visti_extra": [...], "aggiornato": "..."}
Se il file non c'e' si usano le variabili NOTIFICA_* del workflow.

Codice filtri (parametro /start del bot, max 64 caratteri, separatore "_"):
  R<regione>  es. RCM          P<prove>  es. PSO (S=solo scritto, O=scritto+orale, C=solo orale,
  C<contratto> es. CI (I/D/N)                     T=titoli, K=titoli+colloquio, N=da verificare)
  T<titoli>   es. TL31-L9-DINF (classi senza trattino, D=diploma qualsiasi, DINF=diploma informatico...)
  VS = solo compatibili certi (default: anche da verificare)     X = escludi preselettiva
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
BANDI = ROOT / "data" / "bandi.json"
CONFIG = ROOT / "data" / "notifiche.json"
NOTIFICATI = ROOT / "data" / "notificati.json"

ETICHETTE_PROVE = {"solo_scritto": "Solo scritto", "scritto_orale": "Scritto + orale", "solo_orale": "Solo orale/colloquio",
                   "titoli": "Solo titoli", "titoli_colloquio": "Titoli + colloquio", "non_rilevato": "Prove da verificare"}
ETICHETTE_CONTR = {"indeterminato": "Tempo indeterminato", "determinato": "Tempo determinato",
                   "entrambi": "Indeterminato e determinato", "non_rilevato": "Contratto da verificare"}
CODICI_PROVE = {"solo_scritto": "S", "scritto_orale": "O", "solo_orale": "C", "titoli": "T", "titoli_colloquio": "K", "non_rilevato": "N"}
CODICI_CONTR = {"indeterminato": "I", "determinato": "D", "non_rilevato": "N"}
REGIONI = {"Abruzzo": "AB", "Basilicata": "BA", "Calabria": "CL", "Campania": "CM", "Emilia-Romagna": "ER",
           "Friuli-Venezia Giulia": "FV", "Lazio": "LZ", "Liguria": "LI", "Lombardia": "LO", "Marche": "MA",
           "Molise": "MO", "Piemonte": "PI", "Puglia": "PU", "Sardegna": "SA", "Sicilia": "SI", "Toscana": "TO",
           "Trentino-Alto Adige": "TA", "Umbria": "UM", "Valle d'Aosta": "VA", "Veneto": "VE"}
DIPLOMI = {  # codice titolo -> (area del diploma, etichetta)
    "DIP": (None, "Diploma (qualsiasi)"), "DIP-INF": ("informatica", "Diploma informatico"),
    "DIP-ELE": ("elettronica", "Diploma elettronico/elettrotecnico"), "DIP-MEC": ("meccanica", "Diploma meccanico"),
    "DIP-GEO": ("geometra", "Diploma geometra/CAT"), "DIP-RAG": ("ragioneria", "Diploma ragioneria/AFM"),
    "DIP-AGR": ("agraria", "Diploma agrario"), "DIP-CHI": ("chimica", "Diploma chimico/biotecnologico"),
    "DIP-SOC": ("socio_sanitario", "Diploma socio-sanitario"), "DIP-TUR": ("turismo", "Diploma turistico/alberghiero"),
}
LIV = {"nessuno": 0, "diploma": 1, "triennale": 2, "magistrale": 3}
AREA_CLASSE = {
    "L-31": "informatica", "LM-18": "informatica", "LM-66": "informatica", "LM-91": "informatica",
    "L-7": "ingegneria", "L-8": "ingegneria", "L-9": "ingegneria", "L-23": "ingegneria", "L-28": "ingegneria",
    "LM-20": "ingegneria", "LM-21": "ingegneria", "LM-22": "ingegneria", "LM-23": "ingegneria", "LM-24": "ingegneria",
    "LM-25": "ingegneria", "LM-26": "ingegneria", "LM-27": "ingegneria", "LM-28": "ingegneria", "LM-29": "ingegneria",
    "LM-30": "ingegneria", "LM-31": "ingegneria", "LM-32": "ingegneria", "LM-33": "ingegneria", "LM-35": "ingegneria",
    "LMG-01": "giurisprudenza", "L-14": "giurisprudenza",
    "L-18": "economia", "L-33": "economia", "L-41": "economia", "LM-16": "economia", "LM-56": "economia",
    "LM-77": "economia", "LM-82": "economia", "LM-83": "economia",
    "L-16": "scienze_politiche", "L-20": "scienze_politiche", "L-36": "scienze_politiche", "L-37": "scienze_politiche",
    "L-40": "scienze_politiche", "LM-52": "scienze_politiche", "LM-59": "scienze_politiche", "LM-62": "scienze_politiche",
    "LM-63": "scienze_politiche", "LM-88": "scienze_politiche", "LM-90": "scienze_politiche",
    "L-24": "psicologia", "LM-51": "psicologia", "L-39": "servizio_sociale", "LM-87": "servizio_sociale",
    "L-19": "educazione", "LM-57": "educazione", "LM-85": "educazione",
    "L-17": "architettura", "L-21": "architettura", "LM-4": "architettura", "LM-48": "architettura",
    "L-1": "umanistica", "L-3": "umanistica", "L-5": "umanistica", "L-6": "umanistica", "L-10": "umanistica",
    "L-11": "umanistica", "L-12": "umanistica", "L-15": "umanistica", "L-42": "umanistica", "L-43": "umanistica",
    "LM-14": "umanistica", "LM-89": "umanistica",
    "L-2": "scienze", "L-13": "sanita", "L-25": "scienze", "L-26": "scienze", "L-27": "scienze", "L-29": "sanita",
    "L-30": "scienze", "L-32": "scienze", "L-34": "scienze", "L-35": "scienze", "L-38": "scienze",
    "LM-6": "sanita", "LM-13": "sanita", "LM-17": "scienze", "LM-40": "scienze", "LM-54": "scienze",
    "LM-69": "scienze", "LM-74": "scienze", "LM-75": "scienze", "LM-41": "sanita",
}
# area della laurea -> diplomi "affini" (laurea assorbente rispetto a un diploma specifico)
LAUREA_DIPLOMA = {"informatica": ["informatica"], "ingegneria": ["informatica", "elettronica", "meccanica", "geometra"],
                  "architettura": ["geometra"], "economia": ["ragioneria"], "scienze": ["agraria", "chimica"],
                  "servizio_sociale": ["socio_sanitario"], "sanita": ["socio_sanitario", "chimica"]}
RANGO = {"si": 3, "forse": 2, "?": 1, "no": 0}

# avvisi per idonei in graduatorie di altri enti (riconosciuti dal titolo; stessa logica in index.html)
RE_GRAD = re.compile(r"idone[io]|interpell|elenc\w*\s+(?:degli\s+|di\s+)?idonei|albo\s+(?:degli\s+)?idonei|\basmel|utilizz\w*\s+(?:di\s+)?(?:idonei\s+)?(?:delle\s+|di\s+)?graduatori|"
                     r"scorriment\w*\s+(?:di\s+|delle\s+)?graduatori|"
                     r"graduatori\w*\s+(?:\S+\s+){0,6}(?:da|di)\s+altr[ie]\s+(?:enti|amministrazioni|pp\.?\s*aa)")
LIV_PROF = [("D", re.compile(r"dirigent")),
            ("F", re.compile(r"funzionari|direttiv|specialist|assistent[ei]\s+social|\bcat(?:egoria)?\.?\s*d\b|area\s+(?:dei\s+)?funzionari")),
            ("A", re.compile(r"assistent[ei](?!\s+social)|area\s+(?:degli\s+)?assistenti")),
            ("I", re.compile(r"\bistr(?:\.|uttor)|\bcat(?:egoria)?\.?\s*c\b|area\s+(?:degli\s+)?istruttori|agent[ei]\s+di\s+polizia")),
            ("O", re.compile(r"operator|esecutor|collaborator|\bcat(?:egoria)?\.?\s*b\b|area\s+(?:degli\s+)?operatori"))]
AREE_PROF = {"INF": ("informatico", re.compile(r"informatic|sistemi\s+informativ|\bict\b|digital|programmator|analista")),
             "CON": ("contabile", re.compile(r"contabil|finanziar|ragioner|economic|tribut")),
             "AMM": ("amministrativo", re.compile(r"amministrativ")),
             "TEC": ("tecnico", re.compile(r"tecnic|geometr|ingegner|architett|lavori\s+pubblici")),
             "VIG": ("vigilanza", re.compile(r"vigilanza|polizia\s+locale|agent[ei]\s+di\s+p")),
             "SOC": ("sociale/educativo", re.compile(r"social|educator|pedagog")),
             "CUL": ("culturale/biblioteca", re.compile(r"bibliotec|cultural|archivist|museal")),
             "COM": ("comunicazione", re.compile(r"comunicazion|giornalist|ufficio\s+stampa"))}
NOMI_LIV = {"D": "Dirigente", "F": "Funzionario", "A": "Assistente", "I": "Istruttore", "O": "Operatore"}
LIV_EQ = {"A": "I"}   # Assistenti (Funzioni Centrali) = Istruttori (Funzioni Locali)
COMPARTI = {"L": "Funzioni Locali", "C": "Funzioni Centrali", "S": "Sanità", "U": "Istruzione e ricerca"}
# solo frasi che RESTRINGONO gli enti di provenienza ("enti che applicano il CCNL...", "enti del comparto...")
RE_COMPARTO = re.compile(
    r"(?:enti|amministrazioni|pubbliche\s+amministrazioni)[^.;]{0,50}?"
    r"(?:che\s+applican\w*|del(?:lo\s+stesso|\s+medesimo)?\s+comparto|appartenent\w*\s+al(?:lo\s+stesso)?\s+comparto|soggett\w*\s+al)"
    r"[^.;]{0,40}?(funzioni\s+locali|enti\s+locali|regioni\s+e\s+(?:delle\s+)?autonomie\s+locali|funzioni\s+centrali|"
    r"sanit[aà]|istruzione\s+e\s+(?:della\s+)?ricerca)")
TIPI = {"C": "Concorsi", "G": "Manifestazioni d'interesse (idonei)", "M": "Mobilità", "A": "Altri avvisi"}


def comparto_da_testo(t: str) -> str:
    m = RE_COMPARTO.search(t.lower())
    if not m:
        return ""
    x = m.group(1)
    return "L" if ("local" in x) else "C" if "centrali" in x else "S" if x.startswith("sanit") else "U"


RE_MOBILITA = re.compile(r"(?:avviso|bando|procedura)\s+(?:pubblico\s+)?(?:di|per)\s+(?:la\s+)?mobilit|mobilit[aà]\s+(?:volontaria|esterna|tra\s+enti|ex\s+art)")
RE_CONCORSO = re.compile(r"\bconcors|per\s+(?:soli\s+)?esami|per\s+titoli\s+ed\s+esami|selezione\s+pubblica")


def tipo(b: dict) -> str:
    if graduatoria(b):
        return "G"
    cat = (b.get("categoria") or "").lower()
    t = testo_avviso(b)
    if "mobilit" in cat or RE_MOBILITA.search(t):
        return "M"
    if not cat or "concors" in cat or RE_CONCORSO.search(t):
        return "C"
    return "A"


def testo_avviso(b: dict) -> str:
    return f"{b.get('titolo', '')} . {b.get('profilo', '')} . {(b.get('descrizione') or '')[:800]}".lower()


def graduatoria(b: dict) -> dict | None:
    t = testo_avviso(b)
    if not RE_GRAD.search(t):
        return None
    liv = next((k for k, r in LIV_PROF if r.search(t)), "")
    aree = [k for k, (_, r) in AREE_PROF.items() if r.search(t)]
    nome = f"{NOMI_LIV.get(liv, 'Profilo')} {'/'.join(AREE_PROF[a][0] for a in aree)}".strip()
    comp = ((b.get("analisi") or {}).get("comparto") or "") or comparto_da_testo(t)
    # elenchi di idonei: ASMEL (Comuni associati) o altri elenchi/interpelli non specificati
    elenco = "ASMEL" if re.search(r"\basmel", t) else "ALTRO" if re.search(r"interpell|elenc\w*\s+(?:degli\s+|di\s+)?idonei|albo\s+(?:degli\s+)?idonei", t) else ""
    if elenco == "ASMEL":
        comp = "L"
    return {"liv": liv, "aree": aree, "nome": nome, "comparto": comp, "elenco": elenco}


def etichetta_avviso(g: dict) -> str:
    if g.get("elenco") == "ASMEL":
        return f"Interpello elenco ASMEL · {g['nome']}"
    if g.get("elenco") == "ALTRO":
        return f"Interpello/elenco idonei · {g['nome']}"
    return f"Idonei in graduatoria · {g['nome']}" + (f" ({COMPARTI[g['comparto']]})" if g.get("comparto") else "")


def nome_idoneita(c: str) -> str:
    """codice idoneita': livello(1) + area(3) + comparto(1: L/C/S/U, X = non indicato) + fonte(1, facoltativa: A = elenco ASMEL)
    es. AINFC = assistente informatico Funzioni Centrali; IAMMLA = istruttore amministrativo, elenco ASMEL"""
    comp = COMPARTI.get(c[4:5], "")
    base = f"{NOMI_LIV.get(c[:1], '')} {AREE_PROF.get(c[1:4], (c[1:4],))[0]}".strip()
    if c[5:6] == "A":
        return base + " (elenco ASMEL)"
    return base + (f" ({comp})" if comp else "")


def compat_idoneita(g: dict, idon: list[str]) -> tuple[str, str]:
    best = ("no", "")
    for c in idon:
        liv, area, comp = LIV_EQ.get(c[:1], c[:1]), c[1:4], c[4:5].replace("X", "")
        asmel = c[5:6] == "A"
        if (g.get("elenco") == "ASMEL") != asmel:     # interpello ASMEL: vale solo l'iscrizione all'elenco ASMEL, e viceversa
            continue
        gliv = LIV_EQ.get(g["liv"], g["liv"])
        stessa_area, stesso_liv = area in g["aree"], gliv == liv
        if stessa_area and stesso_liv:
            k = "si"
        elif (stessa_area and not g["liv"]) or (stesso_liv and not g["aree"]) or (stessa_area and g["liv"] and not stesso_liv):
            k = "forse"
        else:
            k = "no"
        if k != "no" and g.get("comparto"):        # l'avviso accetta solo graduatorie di un comparto
            if not comp:
                k = "forse"
            elif comp != g["comparto"]:
                k = "no"
        if k == "si" and g.get("elenco") == "ALTRO":   # interpello su un elenco non riconosciuto: da verificare
            k = "forse"
        if RANGO[k] > RANGO[best[0]]:
            best = (k, c)
    return best


def valuta(b: dict, f: dict) -> tuple[str, str] | None:
    """(esito, descrizione) rispetto a titoli e idoneita' dell'utente; None se non ci sono filtri sui requisiti"""
    g = graduatoria(b)
    if g:
        req = g["nome"] + (" (elenco ASMEL)" if g.get("elenco") == "ASMEL" else f" ({COMPARTI[g['comparto']]})" if g.get("comparto") else "")
        idon = f.get("idoneita") or []
        if not idon:
            return "?", f"serve idoneità: {req}"
        k, c = compat_idoneita(g, idon)
        return k, {"si": f"idoneo {nome_idoneita(c)}", "forse": f"idoneità da verificare: {req}"}.get(k, f"serve idoneità: {req}")
    if not f.get("titoli"):
        return None
    k, tit = compat_migliore(b, f["titoli"])
    req = LIV.get(((b.get("analisi") or {}).get("titolo") or {}).get("livello"))
    if k == "si" and req is not None and req <= 1:
        # il bando chiede solo il diploma: se tra i tuoi titoli c'e' un diploma valido mostro quello
        dip = next((c for c in f["titoli"] if c in DIPLOMI and compatibile(b, c) == "si"), None)
        if dip:
            tit = dip
        else:
            return k, (f"nessun titolo specifico (hai {etichetta_titolo(tit)})" if req == 0
                       else f"basta il diploma (hai {etichetta_titolo(tit)})")
    return k, {"si": etichetta_titolo(tit), "forse": f"{etichetta_titolo(tit)} da verificare", "?": "titolo da verificare"}.get(k, "")


# ----------------------------------------------------------------------------- titoli
def norm_titolo(s: str) -> str:
    s = (s or "").strip().upper().replace(" ", "")
    if s in DIPLOMI:
        return s
    m = re.match(r"^(LMG|LM|LS|L)[-/]?(SNT)?(\d{1,2})$", s)
    if not m:
        return s
    if m.group(1) == "LMG":
        return "LMG-01"
    return f"{m.group(1)}-{'SNT' if m.group(2) else ''}{int(m.group(3))}"


def etichetta_titolo(t: str) -> str:
    return DIPLOMI[t][1] if t in DIPLOMI else t


def livello_titolo(t: str):
    if t in DIPLOMI:
        return 1
    if t.startswith("L-"):
        return 2
    if re.match(r"^(LM|LS|LMG)-", t):
        return 3
    return None


def compatibile(b: dict, tit: str) -> str:
    """'si' | 'forse' | 'no' | '?' per un singolo titolo"""
    a = b.get("analisi")
    if not a or not tit:
        return "?"
    t = a["titolo"]
    req = LIV.get(t["livello"])
    dipl = t.get("diplomi") or []
    dip_ok = (not dipl) or t.get("diploma_qualsiasi", True)
    if tit in DIPLOMI:
        if req is None:
            return "?"
        if req >= 2:
            return "no"
        area = DIPLOMI[tit][0]
        if dip_ok or (area and area in dipl):
            return "si"
        return "no" if area else "forse"
    mio = livello_titolo(tit)
    if req is not None and mio is not None and mio < req:
        return "no"
    area = AREA_CLASSE.get(tit)
    if req is not None and req <= 1:
        if dip_ok or any(d in dipl for d in LAUREA_DIPLOMA.get(area, [])):
            return "si"
        return "forse"
    if tit in t["classi"] or t["qualsiasi_laurea"]:
        return "si"
    if area and (area in t["aree"] or any(AREA_CLASSE.get(c) == area for c in t["classi"])):
        return "forse"
    if t["classi"] or t["aree"]:
        return "no"
    return "?"


def compat_migliore(b: dict, titoli: list[str]) -> tuple[str, str]:
    """(esito, titolo) del titolo piu' favorevole tra quelli dell'utente"""
    best = ("no", "")
    for tit in titoli:
        k = compatibile(b, tit)
        if RANGO[k] > RANGO[best[0]]:
            best = (k, tit)
    return best


# ----------------------------------------------------------------------------- filtri
def passa(b: dict, f: dict) -> bool:
    a = b.get("analisi")
    if not a:
        return False
    regioni = [r.lower() for r in f.get("regioni") or []]
    if regioni and not any(r in " ".join(b.get("sedi") or [b.get("regione", "")]).lower() for r in regioni):
        return False
    if f.get("tipi") and tipo(b) not in f["tipi"]:
        return False
    # prove, preselettiva e contratto valgono solo per i concorsi, non per le manifestazioni d'interesse (idonei)
    if not graduatoria(b):
        if f.get("prove") and a["prove"]["prove"] not in f["prove"]:
            return False
        if f.get("nopres") and a["prove"]["preselettiva"]:
            return False
        if f.get("contratto"):
            c = a["contratto"]["contratto"]
            if not (c in f["contratto"] or (c == "entrambi" and {"indeterminato", "determinato"} & set(f["contratto"]))):
                return False
    v = valuta(b, f)
    if v:
        k = v[0]
        if k == "no" or (not f.get("verificare", True) and k != "si"):
            return False
    return True


def descrivi_filtri(f: dict) -> str:
    parti = ["/".join(f.get("regioni") or []) or "Tutte le regioni"]
    if f.get("prove"):
        parti.append("/".join(ETICHETTE_PROVE.get(p, p) for p in f["prove"]))
    if f.get("contratto"):
        parti.append("/".join(ETICHETTE_CONTR.get(c, c) for c in f["contratto"]))
    if f.get("titoli"):
        parti.append(", ".join(etichetta_titolo(t) for t in f["titoli"])
                     + (" (anche da verificare)" if f.get("verificare", True) else " (solo certi)"))
    if f.get("idoneita"):
        parti.append("idoneo " + ", ".join(nome_idoneita(c) for c in f["idoneita"]))
    if f.get("tipi"):
        parti.append("/".join(TIPI.get(x, x) for x in f["tipi"]))
    if f.get("nopres"):
        parti.append("senza preselettiva")
    return " · ".join(parti)


def decodifica(codice: str) -> dict | None:
    f = {"regioni": [], "prove": [], "contratto": [], "titoli": [], "idoneita": [], "tipi": [], "verificare": True, "nopres": False}
    inv_reg = {v: k for k, v in REGIONI.items()}
    inv_pro = {v: k for k, v in CODICI_PROVE.items()}
    inv_con = {v: k for k, v in CODICI_CONTR.items()}
    try:
        for tok in [t for t in codice.strip().split("_") if t]:
            k, v = tok[0], tok[1:]
            if k == "R":
                f["regioni"] = [inv_reg[v[i:i + 2]] for i in range(0, len(v), 2) if v[i:i + 2] in inv_reg]
            elif k == "P":
                f["prove"] = [inv_pro[c] for c in v if c in inv_pro]
            elif k == "C":
                f["contratto"] = [inv_con[c] for c in v if c in inv_con]
            elif k == "T":
                tit = []
                for x in v.split("-"):
                    if x == "D":
                        tit.append("DIP")
                    elif x.startswith("D") and f"DIP-{x[1:]}" in DIPLOMI:
                        tit.append(f"DIP-{x[1:]}")
                    elif x:
                        tit.append(norm_titolo(x))
                f["titoli"] = [t for t in tit if t in DIPLOMI or livello_titolo(t)]
            elif k == "I":
                f["idoneita"] = [x for x in v.split("-") if x[:1] in NOMI_LIV and x[1:4] in AREE_PROF
                                 and (len(x) == 4 or x[4:5] in COMPARTI or x[4:5] == "X") and x[5:6] in ("", "A")]
            elif k == "Y":
                f["tipi"] = [x for x in v if x in TIPI]
            elif tok == "G0":                       # vecchio formato: "senza avvisi per idonei"
                f["tipi"] = ["C", "M", "A"]
            elif tok == "VS":
                f["verificare"] = False
            elif tok == "X":
                f["nopres"] = True
            else:
                return None
    except (KeyError, IndexError):
        return None
    return f


# ----------------------------------------------------------------------------- dati
def leggi(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def scrivi(p: Path, dati):
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")


def filtri_da_env() -> dict:
    env = lambda k, d="": (os.environ.get(k) or d).strip()
    lista = lambda k: [x.strip() for x in env(k).split(",") if x.strip()]
    tit = [norm_titolo(x) for x in lista("NOTIFICA_TITOLI") or lista("NOTIFICA_LAUREA")]
    return {"regioni": lista("NOTIFICA_REGIONI"), "prove": lista("NOTIFICA_PROVE"), "contratto": lista("NOTIFICA_CONTRATTO"),
            "titoli": tit, "verificare": env("NOTIFICA_COMPAT", "forse") != "si", "nopres": env("NOTIFICA_NO_PRESELETTIVA") == "1"}


def carica_config() -> dict:
    c = leggi(CONFIG, {})
    if not c.get("filtri"):
        c["filtri"] = filtri_da_env()
    c.setdefault("pausa", False)
    c.setdefault("offset", 0)
    c.setdefault("visti_extra", [])
    return c


def salva_config(c: dict):
    c["aggiornato"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    scrivi(CONFIG, c)


def pagina_url() -> str:
    repo = (os.environ.get("GITHUB_REPOSITORY") or "utente/cerca-concorsi").split("/")
    return os.environ.get("PAGINA_URL") or f"https://{repo[0].lower()}.github.io/{repo[1]}/"


# ----------------------------------------------------------------------------- messaggi
def fmt_data(s: str) -> str:
    try:
        return datetime.fromisoformat(s[:16]).strftime("%d/%m/%Y")
    except ValueError:
        return s[:10]


def scheda(b: dict, f: dict) -> dict:
    a = b["analisi"]
    p, c = a["prove"], a["contratto"]
    g = graduatoria(b)
    tag = ([etichetta_avviso(g)] if g else []) + [ETICHETTE_PROVE.get(p["prove"], p["prove"]) + (" (incerto)" if p["conf"] == "bassa" and p["prove"] != "non_rilevato" else "")]
    if p["preselettiva"]:
        tag.append("preselettiva")
    tag.append(ETICHETTE_CONTR.get(c["contratto"], c["contratto"]))
    icona = ""
    v = valuta(b, f)
    if v:
        icona = {"si": "✅", "forse": "🔎", "?": "🔎"}.get(v[0], "")
        tag.append(f"{icona} {v[1]}".strip())
    sede = ", ".join((b.get("sedi") or [])[1:]) or b.get("regione", "")
    return {"titolo": b.get("titolo") or b.get("profilo") or "Bando", "ente": b.get("ente", ""), "sede": sede,
            "posti": b.get("posti"), "scadenza": fmt_data(b.get("scadenza", "")), "tag": " · ".join(t for t in tag if t),
            "url": b.get("url", ""), "pdf": b.get("pdf", ""), "icona": icona}


def msg_bando(d: dict, intestazione: str = "💥 <b>Nuovo concorso</b>") -> str:
    from html import escape as e
    luogo = " · ".join(x for x in (d["ente"], d["sede"]) if x)
    if d["posti"]:
        luogo += f" · {d['posti']} {'posto' if d['posti'] == 1 else 'posti'}"
    link = [f'<a href="{e(d["url"])}">Apri su inPA</a>'] + ([f'<a href="{e(d["pdf"])}">Bando PDF</a>'] if d["pdf"] else [])
    righe = [intestazione] if intestazione else []
    righe += [f"<b>{e(d['titolo'])}</b>", e(luogo), e(d["tag"]), f"⏳ Scade il <b>{e(d['scadenza'])}</b>", " | ".join(link)]
    return "\n".join(righe)


def riga_breve(d: dict) -> str:
    from html import escape as e
    return f'{d.get("icona") or "•"} <a href="{e(d["url"])}">{e(d["titolo"][:90])}</a> — {e(d["ente"][:50])}, scade {e(d["scadenza"])}'


def frase_conteggio(n: int) -> str:
    if n == 0:
        return "Al momento <b>nessun bando</b> aperto rispetta questi filtri."
    if n == 1:
        return "C'è <b>1 bando</b> aperto che rispetta questi filtri:"
    return f"Ci sono <b>{n} bandi</b> aperti che rispettano questi filtri:"


def telegram(testi: list[str], dry: bool = False) -> bool:
    import time
    token, chat = os.environ.get("TELEGRAM_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return False
    for t in testi:
        if dry:
            print("[TELEGRAM]\n" + t + "\n")
            continue
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
                          json={"chat_id": chat, "text": t[:4090], "parse_mode": "HTML", "disable_web_page_preview": True})
        if r.status_code != 200:
            print(f"Telegram: errore {r.status_code} {r.text[:200]}")
        time.sleep(1.1)
    return True


def blocchi_lista(intro: str, schede: list[dict], max_righe: int = 25) -> list[str]:
    """intro + elenco breve, spezzato in piu' messaggi se serve (limite Telegram 4096 caratteri)"""
    msgs, cur = [], intro
    for d in schede[:max_righe]:
        r = riga_breve(d)
        if len(cur) + len(r) + 1 > 3800:
            msgs.append(cur); cur = ""
        cur += ("\n" if cur else "") + r
    if len(schede) > max_righe:
        cur += f"\n…e altri {len(schede) - max_righe}: {pagina_url()}"
    msgs.append(cur)
    return msgs
