# Il Ripassone

Sfida interattiva a squadre per il ripasso in aula. Pensato per qualsiasi
corso e qualsiasi livello scolastico — dalle scuole primarie all'università.

Le squadre di studenti si sfidano a colpi di domande a risposta multipla
caricate da un foglio Excel (può essere preparato dal docente, dagli studenti
stessi come compito, o pescato da qualunque banca-domande). Tre viste
sincronizzate via WebSocket: **/admin** (controllo del docente), **/team**
(mobile per studenti), **/display** (proiettore in aula).

Nato per il corso DIPA — *Digitalizzazione delle imprese e della Pubblica
Amministrazione* — insegnamento della Laurea Magistrale in *Diritto per
l'Innovazione di Imprese e Pubbliche Amministrazioni* (LM-63), Università di
Udine. Volutamente generalista: la struttura "domande di ripasso → squadre →
deliberazione di squadra → puntate" funziona indipendentemente dalla materia.

---

## Lanciamento in classe in 60 secondi

### 0. Prerequisiti (una volta sola)

```bash
# Python 3.14 + uv (gestore di pacchetti Python)
brew install uv

# Solo se servirà esporre il server agli studenti su rete cellulare:
brew install ngrok
ngrok config add-authtoken <TOKEN>   # account gratuito su ngrok.com
```

**Password admin** (opzionale ma consigliato): aggiungi al tuo `.zshrc` o
`.bashrc` la variabile d'ambiente
```bash
export RIPASSONE_ADMIN_PASSWORD="la-tua-password"
```
Senza variabile, la password di default è `ripassone`.

### 1. Avvio
Dalla cartella `quizzone/`:

```bash
uv run main.py --public
```

Stampa in console:
- 🌐 URL pubblico (es. `https://abc1-23.ngrok-free.app`)
- 🔑 Password admin
- 📱 QR code ASCII per `/team`

#### Opzioni CLI

| Flag | Effetto |
|---|---|
| `--public` | Avvia ngrok e pubblica un URL accessibile da rete cellulare. |
| `--no-reload` | Disattiva l'autoreload di uvicorn. **Consigliato in aula**: un edit accidentale ai sorgenti azzererebbe la partita in corso (lo stato è in RAM). |
| `--serious` | Grafica accademico-istituzionale: logo "Il Ripassone" del corso, palette navy/teal/oro, font Playfair Display + Inter, niente flash full-screen. Default: cartoon-pop. |

### 2. In aula

| Chi | Cosa apre | Note |
|---|---|---|
| **Prof** (laptop) | `<URL>/admin` | login con password, configure + upload Excel + Start |
| **Proiettore** | `<URL>/display` | clicca "▶ AVVIA" per sbloccare audio |
| **Tutti i dispositivi** (studenti+proiettore) | `<URL>/info` | QR e URL grandi da proiettare |
| **Studenti** (telefoni) | `<URL>/team` | nome+cognome+squadra |

### 3. Flusso tipico

1. Admin tab **Setup**: imposta round/tempo/punti/puntate, clicca *Applica e vai in Lobby*
2. Admin tab **Setup**: carica i `.xlsx` con le domande proposte dagli studenti
3. Studenti aprono `/team` da QR e si iscrivono (squadra esistente come opzione cliccabile, oppure crea nuova). Min 2 membri/squadra.
4. Admin tab **Lobby**: clicca *Apri elezioni capitano*
5. **Elezioni**: ogni studente vota i membri della propria squadra su 5 livelli (Eccellente/Buono/Accettabile/Scarso/Inadeguato). Il capitano provvisorio (Majority Judgment) è ricalcolato lato server ma **non** mostrato durante le votazioni — viene rivelato solo alla chiusura, per evitare effetto bandwagon. L'admin vede solo la distribuzione grezza dei voti. Si può rinominare la squadra, correggere nome/cognome, cambiare squadra.
6. Admin clicca *Chiudi elezioni e annuncia capitani* → fase **PRE_GAME**
7. **PRE_GAME**: capitani annunciati su tutti i client. Solo il capitano può rinominare la sua squadra. Il cambio squadra è bloccato. Edit nome/cognome ancora possibile. L'admin può tornare alle elezioni se serve.
8. Admin clicca *Avvia sfida* (sorteggia ordine, apre primo turno)
9. Loop: durante TURN_CHOICE i membri filtrano il pool e **propongono in tempo reale** — appena toccano una domanda, una puntata o un bersaglio, la proposta è già visibile al capitano (chip colorati sotto ogni opzione, nessun bottone "Proponi" da premere). Il capitano può adottarne una con un tap o decidere autonomamente. Countdown → risposta → reveal → *Next turn*. Tra un turno e l'altro l'admin può modificare i tempi (sezione *⏱ Tempi prossimo turno* nella tab Partita): il nuovo `seconds` e i `time_factors` valgono dal turno seguente. Un *round* è un giro completo: con N squadre **attive** (score>0) e R round si giocano fino a N×R sfide. Una squadra che va a zero (o sotto) viene **saltata** quando tocca a lei porre, ma resta in classifica e può rispondere alle domande aperte; se torna sopra zero, riprende a porre nei round successivi.
10. La tab **Lobby** di admin resta consultabile in ogni fase: durante la sfida mostra le squadre con capitano + membri, e lo storico dei voti dell'elezione (read-only).
11. A fine sfida: classifica finale su `/display` e tab Partita di admin

### Test con dati precompilati

Per provare il sistema senza file proprio:

- **`domande_esempio.xlsx`** (in cartella): 2 domande generiche (geografia,
  matematica) per smoke-test dell'import. Caricare via tab Setup → *Carica file*.
- **Seed demo**: bottone *Seed 6 demo* nel pannello upload — 6 domande
  hardcoded per test rapidissimi senza file.

### Preparare il proprio pool di domande

Usa **`DIPA_L19_compito_template.xlsx`** come traccia: foglio *Domande*, riga 7
con gli header, dati dalla riga 8 in poi. Colonne (l'ordine conta):

| # | Lezione | Argomento | Domanda | Opzione A | Opzione B | Opzione C | Opzione D | Risposta corretta | Difficoltà | Spiegazione | Fonte |

Note pratiche:
- *Lezione*: stringa libera (es. `L01`, `Cap. 3`, `Unità 2`). Diventa un filtro nella UI.
- *Risposta corretta*: una lettera fra A, B, C, D.
- *Difficoltà*: 1 (facile), 2 (media), 3 (tosta) — modula il countdown.
- Le opzioni vuote (es. solo A/B/C) vengono ignorate: puoi avere domande con 2-4 opzioni.
- *Spiegazione* e *Fonte* sono opzionali, mostrate al reveal.

---

## Architettura

- **Backend**: FastAPI + uvicorn + WebSocket (Python 3.14)
- **Stato**: Pydantic + state machine 9 fasi (`setup → lobby → captain_election → pre_game → ready → turn_choice → turn_question → turn_reveal → finished`)
- **Elezione capitano**: Majority Judgment (Balinski-Laraki 2010) su scala 5 livelli. Tiebreak deterministico via lower-median sequence.
- **Persistenza**: in-RAM (1 partita = 1 sessione server)
- **Frontend**: Tailwind CSS via CDN + Alpine.js + Web Audio API
- **Auth**: bcrypt per password admin, cookie HttpOnly+SameSite=Lax
- **Tunnel pubblico**: ngrok (per WiFi ateneo + rete cellulare)

### Comportamento del countdown

Il countdown è **server-side** (asyncio task) e dura `seconds × time_factor[difficolta]` (default `0.5 / 1.0 / 1.4` su `seconds=90`).
Cambia colore a 10s (giallo) e a 5s (rosso pulsante) sul display, con flash full-screen e suoni (il flash è disattivato in modalità `--serious`).

### Regole di scoring

- **Domanda a squadra X**: corretto → X vince la puntata da chi pone; sbagliato/timeout → X perde a chi pone
- **Domanda aperta**: prima squadra che risponde blocca le altre. Corretto → vince da chi pone; sbagliato → perde a chi pone; timeout → tutte le altre squadre perdono `bet/(N-1)` troncato all'unità, somma a chi pone

---

## Sviluppo

```bash
uv sync                  # installa dipendenze
uv run main.py           # solo locale http://localhost:8000
uv run main.py --public  # con ngrok
```

### Layout

```
il-ripassone/
├── main.py                          # entry point + ngrok wrapper
├── pyproject.toml
├── domande_esempio.xlsx             # 2 domande di esempio per smoke-test
├── DIPA_L19_compito_template.xlsx   # template (vuoto) compatibile con il parser
├── LICENSE                          # MIT
├── src/ripassone/
│   ├── app.py              # FastAPI route
│   ├── auth.py             # password bcrypt + cookie
│   ├── config.py           # settings runtime
│   ├── excel.py            # parser Excel
│   ├── models.py           # Pydantic
│   ├── state.py            # state machine + handlers
│   └── ws.py               # ConnectionManager + dispatch
├── templates/{base,admin,team,display,login,info}.html
├── static/
│   ├── css/cartoon.css     # design system cartoon-pop (default)
│   ├── css/serious.css     # override modalità --serious (accademico)
│   └── img/{hero.png, logo_serious.png}
└── mockups/                # mockup HTML autonomi (riferimento design)
```

### Cambiare la password admin

```bash
export RIPASSONE_ADMIN_PASSWORD="la-tua-password"
uv run main.py
```

Senza variabile d'ambiente, la password è `ripassone`. L'hash bcrypt viene
rigenerato a ogni avvio: la password in chiaro non viene mai persistita.

---

## Resilienza connessioni

- **Reconnect automatico WebSocket**: se la WS cade (telefono in standby, switch tra app, microblip wifi), il client la riapre con backoff esponenziale 1/2/4/8s.
- **Auto-rejoin**: appena la WS si riconnette, se `me` è in localStorage il client manda automaticamente `team/rejoin` e lo studente ritrova lo stato senza fare nulla.
- **Rejoin manuale**: se uno studente cambia dispositivo o pulisce il browser, dalla pagina `/team` (durante una sfida in corso) può rientrare con solo nome+cognome.
- **Heartbeat** ogni 10s: il server libera entro ~25s le sessioni "appese" (browser killato senza FIN/RST), così la riconnessione non resta bloccata dall'anti-scherzo.
- **Anti-scherzo**: se uno studente prova un rejoin a nome di un compagno la cui sessione è ancora viva, il server rifiuta. Il messaggio è esplicito: "se sei tu, chiudi l'altra finestra; altrimenti riprova fra qualche secondo".

## Limiti noti

- 2-8 squadre, niente partite simultanee (un solo `GameState` in RAM)
- Riavvio del server = perdita partita corrente (non c'è persistenza)
- Cookie admin condiviso tra tab dello stesso browser (atteso: prof = un'identità)
- Audio gate solo su `/display` (gli studenti non hanno bisogno di audio)

---

## Adottare in altri corsi

Il codice è scritto per essere il più possibile *content-agnostic*. Per usarlo
in un corso diverso da DIPA basta:

1. Preparare un `.xlsx` con le tue domande seguendo lo schema di
   `DIPA_L19_compito_template.xlsx` (vedi sezione *Preparare il proprio pool*).
2. (Opzionale) Sostituire il logo `static/img/hero.png` con quello del tuo
   corso, o usare `--serious` per il logo istituzionale generico.
3. (Opzionale) Personalizzare i colori squadra in
   `src/ripassone/state.py` (`TEAM_COLORS_POP` / `TEAM_COLORS_SERIOUS`).

Funziona già a tutti i livelli scolastici — dalle elementari (domande di
storia, geografia, tabelline) all'università (esami orali simulati, ripassi
pre-prova). Il modello a squadre con deliberazione interna scala bene da 6 a
40 studenti.

Se lo adatti per un tuo corso, segnalalo aprendo una issue / discussion: mi
piacerebbe linkare gli adattamenti.

## Licenza

[MIT](LICENSE) — usa, modifica, ridistribuisci liberamente, anche per scopi
commerciali. Niente clausole copyleft: se forki per il tuo istituto, sei
libero di tenere le modifiche private o open, come preferisci.
