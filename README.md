# Cerca Concorsi

Pagina per cercare i concorsi pubblici aperti su [inPA](https://www.inpa.gov.it) con filtri che il portale non ha:

- **Prove**: solo scritto · scritto + orale · solo orale/colloquio · solo titoli · titoli + colloquio (+ esclusione preselettiva)
- **Contratto**: tempo indeterminato · determinato (+ part-time)
- **Titolo di studio**: classi di laurea (L-31, LM-18…), "qualsiasi laurea", livello minimo, compatibilità con la tua laurea
- Regione, tipo di procedura, scadenza, ricerca libera

## Come funziona

| Parte | Dove gira | Cosa fa |
|---|---|---|
| `aggiorna.py` | GitHub Actions, 4 volte al giorno | Legge i bandi aperti dall'API pubblica di inPA, scarica il PDF dei bandi **nuovi**, ne ricava prove/contratto/lauree e scrive `data/bandi.json` |
| `index.html` | GitHub Pages | Legge `data/bandi.json` e applica i filtri nel browser |

Niente server e niente costi: i bandi già analizzati restano in `data/cache.json`, quindi ogni giro analizza solo le novità.

## Installazione (anche da smartphone, dal browser su github.com)

1. **Crea il repository** `cerca-concorsi` (Add → New repository). Pubblico va bene: i dati sono pubblici.
2. **Aggiungi i file** con *Add file → Create new file*, incollando il contenuto:
   - `index.html`
   - `aggiorna.py`
   - `.github/workflows/aggiorna.yml` (scrivendo il percorso con le barre, GitHub crea le cartelle)
   - `README.md` (facoltativo)
3. **Permessi del workflow**: Settings → Actions → General → *Workflow permissions* → **Read and write permissions** → Save.
4. **Primo giro**: Actions → *Aggiorna concorsi* → **Run workflow** → in *max_nuovi* scrivi `2000` → Run.
   Dura circa 1-1,5 ore (analizza tutti i bandi aperti, ~1.700). I giri successivi durano pochi minuti.
5. **Pubblica la pagina**: Settings → Pages → *Build and deployment* → Source **Deploy from a branch** → Branch `main`, cartella `/ (root)` → Save.
   Dopo un paio di minuti la pagina è su `https://<tuo-utente>.github.io/cerca-concorsi/`.

Su telefono: apri la pagina e usa *Aggiungi a schermata Home* per averla come app.

## Se qualcosa non va

- **Il workflow fallisce con "FONTE NON DISPONIBILE"**: inPA non risponde o ha cambiato l'API. Avvia il workflow a mano con *probe* = `si`: nel log trovi un record grezzo da confrontare con i campi usati in `aggiorna.py` (`titolo`, `sedi`, `dataScadenza`, `tipoProcedura`, `allegatoMediaId`, `allegati[].mediaId`).
- **Etichette sbagliate su un bando**: apri "Perché queste etichette" nella scheda per vedere la frase usata; le regole sono le espressioni regolari `RE_*` in `aggiorna.py`. Dopo averle modificate alza `VERSIONE_CLASSIFICATORE` per far rianalizzare tutto.
- **Solo Campania**: per analizzare meno PDF imposta `REGIONI: "Campania"` nel workflow (gli altri bandi restano visibili, senza analisi).

## Limiti

- L'API di inPA è pubblica ma **non documentata**: può cambiare senza preavviso.
- Prove, contratto e lauree sono ricavati dal testo del bando con regole automatiche: le etichette con `?` sono incerte. **Verifica sempre sul bando ufficiale** prima di candidarti.
- I PDF scansionati (immagini) non sono leggibili: quei bandi risultano "Da verificare".
- Nel giro dei bandi viene fatta una richiesta ogni ~0,4 secondi, per non pesare sul portale.
