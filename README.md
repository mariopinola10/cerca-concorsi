# Cerca Concorsi

Pagina per cercare i concorsi pubblici aperti su [inPA](https://www.inpa.gov.it) con filtri che il portale non ha:

- **Titoli di studio**: uno o più (es. diploma informatico + L-31 + L-9), anche cercandoli per nome; vedi solo i bandi a cui puoi accedere
- **Prove**: solo scritto · scritto + orale · solo orale/colloquio · solo titoli · titoli + colloquio (+ esclusione preselettiva)
- **Contratto**: tempo indeterminato · determinato (+ part-time)
- **Avvisi per idonei in graduatorie di altri enti**: riconosciuti dal titolo e confrontati con le tue idoneità (es. Funzionario informatico), non con il titolo di studio
- Regione, tipo di procedura, scadenza, ricerca libera (scrivendo una classe, es. `L-9`, vedi solo i bandi che la citano)
- **Notifiche Telegram/email** dei bandi nuovi, con i filtri scelti dalla pagina

## File

| File | Dove gira | Cosa fa |
|---|---|---|
| `aggiorna.py` | GitHub Actions, 4 volte al giorno | Legge i bandi aperti da inPA, scarica il PDF dei bandi **nuovi** e ne ricava prove, contratto, lauree e diplomi richiesti → `data/bandi.json` |
| `notifica.py` | subito dopo `aggiorna.py` | Manda su Telegram/email i bandi nuovi che rispettano i filtri delle notifiche |
| `bot.py` | GitHub Actions, ogni ~10 minuti | Legge i messaggi al bot Telegram (filtri inviati dalla pagina, comandi) e risponde |
| `filtri.py` | – | Logica comune dei filtri (la stessa è replicata in `index.html`) |
| `index.html` | GitHub Pages | La pagina con i filtri |
| `.github/workflows/aggiorna.yml`, `bot.yml` | – | Programmazione dei due workflow |

I dati stanno in `data/`: `bandi.json` (bandi aperti), `cache.json` (analisi già fatte), `notifiche.json` (filtri delle notifiche), `notificati.json` (bandi già segnalati).

## Installazione

1. Crea il repository e carica tutti i file (da PC: *uploading an existing file* e trascina il contenuto dello zip, cartella `.github` compresa).
2. Settings → Actions → General → *Workflow permissions* → **Read and write permissions** → Save.
3. Actions → *Aggiorna concorsi* → **Run workflow** con *max_nuovi* = `2000` (primo giro: ~1 ora).
4. Settings → Pages → Source **Deploy from a branch** → `main` / `(root)` → Save.
   La pagina è su `https://<utente>.github.io/cerca-concorsi/`.

## Notifiche

### Telegram (consigliato)
1. Su Telegram: **@BotFather** → `/newbot` → copia il **token**.
2. Apri il tuo bot e premi **Avvia**.
3. Apri `https://api.telegram.org/bot<TOKEN>/getUpdates` e copia il numero dopo `"chat":{"id":`.
4. Settings → Secrets and variables → Actions → crea i secret `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID`.
5. Actions → *Bot Telegram* → **Run workflow** una volta: da quel momento la pagina mostra il pulsante 🔔.

**Scegliere i filtri delle notifiche**: imposta i filtri sulla pagina e tocca **🔔 Usa questi filtri per le notifiche**. Si apre Telegram: premi **Avvia**. Entro 10-20 minuti il bot conferma i filtri ed elenca i bandi già aperti che li rispettano; da lì in poi ti scrive solo per quelli nuovi. Ricerca libera, tipo e scadenza non valgono per le notifiche.

Comandi del bot: `/filtri` (filtri attuali), `/bandi` (elenco dei bandi aperti che li rispettano), `/pausa`, `/riprendi`, `/aiuto`.
Il bot risponde solo alla tua chat.

### Email (Gmail)
Attiva la verifica in due passaggi su Google, crea una **password per le app** (myaccount.google.com → Sicurezza → Password per le app) e aggiungi i secret `EMAIL_USER`, `EMAIL_PASSWORD`, `EMAIL_TO`. L'email usa gli stessi filtri di Telegram.

## Se qualcosa non va

- **"FONTE NON DISPONIBILE"** nel workflow: inPA non risponde o ha cambiato l'API. Avvia il workflow a mano con *probe* = `si` e confronta il record grezzo con i campi usati in `aggiorna.py`.
- **Etichette sbagliate**: apri "Perché queste etichette" nella scheda del bando; le regole sono le espressioni `RE_*` e `AREE*` in `aggiorna.py`. Dopo averle cambiate alza `VERSIONE_CLASSIFICATORE` e lancia un giro con *max_nuovi* = `2000`.
- **Il bot non risponde**: controlla nella scheda Actions che *Bot Telegram* giri (GitHub a volte ritarda i giri programmati) e che i secret siano scritti bene.

## Limiti

- L'API di inPA è pubblica ma **non documentata**: può cambiare senza preavviso.
- Prove, contratto e titoli sono ricavati dal testo del bando con regole automatiche: le etichette con `?` e "da verificare" sono incerte. **Verifica sempre sul bando ufficiale.**
- I PDF scansionati non sono leggibili: quei bandi risultano "da verificare".
