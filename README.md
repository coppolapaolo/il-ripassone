# Il Ripassone

Sfida interattiva a squadre per il corso DIPA, Master Diritto per
l'Innovazione, Università di Udine.

Le squadre di studenti si sfidano a colpi di domande a risposta multipla
caricate dagli studenti stessi (template Excel `DIPA_L19_compito_template.xlsx`).
Tre viste sincronizzate via WebSocket: **/admin** (controllo del prof),
**/team** (mobile per studenti), **/display** (proiettore in aula).

---

## Lanciamento in classe in 60 secondi

### 0. Prerequisiti (una volta sola)

```bash
brew install ngrok      # tunnel pubblico per studenti su rete cellulare
ngrok config add-authtoken <TOKEN>   # account gratuito su ngrok.com
# uv (gestore Python) gia installato
```

### 1. Avvio
Dalla cartella `quizzone/`:

```bash
uv run main.py --public
```

Stampa in console:
- 🌐 URL pubblico (es. `https://abc1-23.ngrok-free.app`)
- 🔑 Password admin
- 📱 QR code ASCII per `/team`

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
5. **Elezioni**: ogni studente vota i membri della propria squadra su 5 livelli (Eccellente/Buono/Accettabile/Scarso/Inadeguato). Il capitano provvisorio (Majority Judgment) è ricalcolato in tempo reale e visibile in admin/team. Si può rinominare la squadra, correggere nome/cognome, cambiare squadra.
6. Admin clicca *Chiudi elezioni e annuncia capitani* → fase **PRE_GAME**
7. **PRE_GAME**: capitani annunciati su tutti i client. Solo il capitano può rinominare la sua squadra. Il cambio squadra è bloccato. Edit nome/cognome ancora possibile. L'admin può tornare alle elezioni se serve.
8. Admin clicca *Avvia sfida* (sorteggia ordine, apre primo turno)
9. Loop: capitano sceglie domanda+puntata+target → countdown → risposta → reveal → *Next turn*. Un *round* è un giro completo: ogni squadra pone una domanda una volta. Con N squadre e R round si giocano N×R sfide totali.
10. A fine sfida: classifica finale su `/display` e tab Partita di admin

### Test con dati precompilati

Per provare il sistema senza file degli studenti, sono disponibili due fonti
di domande:

- **`prova_domande_1.xlsx`** (in cartella): 142 domande reali estratte dal pool
  ufficiale del corso (firme/eIDAS, CAD, codifica, AI/ML…). Distribuzione:
  L01:11 · L02:13 · L04:35 · L05:54 · L06:11 · L11:18; difficoltà 65/63/14.
  Caricare via tab Setup → *Carica file*.
- **Seed demo**: bottone *Seed 6 demo* nel pannello upload — 6 domande hardcoded
  per test rapidissimi senza file.

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

Il countdown è **server-side** (asyncio task) e dura `seconds × time_factor[difficolta]` (default `0.7 / 1.0 / 1.4`).
Cambia colore a 10s (giallo) e a 5s (rosso pulsante) sul display, con flash full-screen e suoni.

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
quizzone/
├── main.py                 # entry point + ngrok wrapper
├── pyproject.toml
├── DIPA_L19_compito_template.xlsx  # template per gli studenti
├── src/ripassone/
│   ├── app.py              # FastAPI route
│   ├── auth.py             # password bcrypt + cookie
│   ├── config.py           # settings runtime
│   ├── excel.py            # parser Excel
│   ├── models.py           # Pydantic
│   ├── state.py            # state machine + handlers
│   └── ws.py               # ConnectionManager + dispatch
├── templates/{base,admin,team,display,login,info}.html
├── static/{css/cartoon.css, img/hero.png}
└── mockups/                # mockup HTML autonomi (riferimento design)
```

### Cambiare la password admin

In `src/ripassone/config.py`, modifica `ADMIN_PASSWORD_PLAIN`. L'hash bcrypt
viene rigenerato a ogni avvio.

---

## Limiti noti

- 2-8 squadre, niente partite simultanee (un solo `GameState` in RAM)
- Riavvio del server = perdita partita corrente (non c'è persistenza)
- Cookie admin condiviso tra tab dello stesso browser (atteso: prof = un'identità)
- Audio gate solo su `/display` (gli studenti non hanno bisogno di audio)
