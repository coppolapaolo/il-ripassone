# Mockup — Il Ripassone

File HTML statici autocontenuti (nessun backend, nessun build step). Aprili con doppio click nel browser.

Branding e palette derivati dal logo `assets/logo.png`:

| Token | Valore |
|---|---|
| Navy | `#1E3A5F` |
| Teal | `#3795B5` |
| Azzurro | `#7FB9CC` |
| Oro (accento) | `#E8A93C` |
| Sfondo | `#F4F7FA` |

| File | Vista | Note |
|---|---|---|
| `display.html` | Schermo pubblico (proiettore) | Loop di gioco completo: punteggi, domanda, countdown |
| `team_answer.html` | Capitano in fase risposta (mobile-first) | Voti dei compagni in tempo reale, CTA risposta |

## Interazioni dimostrative

- **`display.html`**: il countdown parte automaticamente. Pulsante in alto a destra `⇄ question/reveal` per mostrare la fase di rivelazione (la risposta corretta diventa verde, le sbagliate rosse). Pulsante "Mostra risposta" / "Reset countdown" sotto al timer.
- **`team_answer.html`**: clicca su un'opzione per selezionarla, poi sul grande pulsante giallo "RISPONDI". Negli ultimi 5 secondi del countdown lo sfondo lampeggia rosso.

## Stack frontend (poi sarà lo stesso nel backend FastAPI)

- Tailwind CSS via Play CDN
- Alpine.js via CDN per stato UI locale
- Google Fonts: Playfair Display (titoli) + Inter (UI) + JetBrains Mono (codice/URL)

I dati nei mockup sono **finti** (squadre, domanda, voti), servono solo a mostrare l'estetica.
