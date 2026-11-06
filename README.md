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
| `bot.py` | GitHub Actions, a ogni messaggio (con il Worker) o ogni ~15 minuti | Legge i messaggi al bot Telegram (filtri inviati dalla pagina, comandi) e risponde |
| `ai.py` | dentro `aggiorna.py` | Lettura dei bandi con Gemini (gratuito, facoltativo) |
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

## Analisi con AI gratuita (Google Gemini)

Se c'è il secret `GEMINI_API_KEY`, ogni bando nuovo viene letto anche da **Gemini** (livello gratuito, nessuna carta di credito), che ricava tipo di avviso (concorso, manifestazione d'interesse, interpello ASMEL, mobilità), profilo/livello/comparto, prove, preselettiva, contratto e titoli ammessi, con la frase del bando. Dove risponde sostituisce le regole automatiche; i PDF scansionati vengono letti lo stesso.

1. Vai su **aistudio.google.com** con un account Google → **Get API key** → **Create API key** → copia la chiave.
2. Settings → Secrets and variables → Actions → New repository secret → `GEMINI_API_KEY`.
3. Al giro successivo Gemini legge fino a 150 bandi (prima la Campania); i bandi già presenti vengono riletti a poco a poco. Quando la quota gratuita del giorno finisce, si ferma e riprende il giorno dopo.

Nella scheda del bando, "Perché queste etichette" mostra 🤖 quando l'analisi viene da Gemini. Nel livello gratuito Google può usare i testi inviati per migliorare i suoi servizi: sono bandi pubblici, nessun dato personale.

## Notifiche

### Telegram (consigliato)
1. Su Telegram: **@BotFather** → `/newbot` → copia il **token**.
2. Apri il tuo bot e premi **Avvia**.
3. Apri `https://api.telegram.org/bot<TOKEN>/getUpdates` e copia il numero dopo `"chat":{"id":`.
4. Settings → Secrets and variables → Actions → crea i secret `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID`.
5. Actions → *Bot Telegram* → **Run workflow** una volta: da quel momento la pagina mostra il pulsante 🔔.

**Risposte immediate (facoltativo, gratis)**: senza altro il bot legge i messaggi ogni ~15 minuti (GitHub può ritardare). Con il Worker in `cloudflare/worker.js` risponde subito "⏳ Ricevuto" e dopo circa un minuto con il risultato:
1. Token GitHub: github.com → foto profilo → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token. Repository access: *Only select repositories* → `cerca-concorsi`. Permissions → Repository → **Contents: Read and write**. Copia il token (`github_pat_…`).
2. Cloudflare (account gratuito su dash.cloudflare.com) → **Workers & Pages** → Create → *Start with Hello World* → nome `cerca-concorsi-bot` → Deploy → **Edit code** → incolla `cloudflare/worker.js` → Deploy. Annota l'indirizzo `https://cerca-concorsi-bot.<tuo-nome>.workers.dev`.
3. Nel Worker: Settings → **Variables and Secrets** → aggiungi come *Secret* `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `GITHUB_TOKEN`, `WEBHOOK_SECRET` (una parola inventata, solo lettere, numeri, `_` e `-`) e come *Text* `GITHUB_REPO` = `utente/cerca-concorsi` → Deploy.
4. Collega Telegram al Worker aprendo nel browser:
   `https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://cerca-concorsi-bot.<tuo-nome>.workers.dev&secret_token=<WEBHOOK_SECRET>`
   Deve rispondere `"Webhook was set"`. Per tornare indietro: `https://api.telegram.org/bot<TOKEN>/deleteWebhook`.

**Scegliere i filtri delle notifiche**: imposta i filtri sulla pagina e tocca **🔔 Usa questi filtri per le notifiche**. Si apre Telegram: premi **Avvia**. Il bot conferma (subito con il Worker, altrimenti entro 15-30 minuti) i filtri ed elenca i bandi già aperti che li rispettano; da lì in poi ti scrive solo per quelli nuovi. Ricerca libera, tipo e scadenza non valgono per le notifiche.

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
