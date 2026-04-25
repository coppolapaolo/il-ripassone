# Mockup — Il Ripassone

File HTML statici autocontenuti (nessun backend, nessun build step). Aprili con doppio click nel browser.

## Stile: cartoon-pop / neobrutalism

Branding e palette estratti dal mood-board `assets/hero.png`:

| Token | Valore | Uso |
|---|---|---|
| Teal | `#4ABEC8` | sfondo accenti, squadre |
| Teal deep | `#2A8893` | testo squadre, anelli countdown |
| Coral | `#E84C3D` | CTA principali, urgenza, accento |
| Yellow | `#F5C44A` | highlight, "in palio", squadra che pone |
| Cream | `#FBF1D9` | sfondo pagina |
| Cream deep | `#F5E4C3` | bottoni neutri, lettere opzioni |
| Navy | `#1A2D40` | bordi, ombre, testo principale |

**Tipografia**:
- `Luckiest Guy` (Google Fonts) — display chunky, per titoli e numeri
- `Fredoka` (400-700) — body, leggibile e arrotondato
- `JetBrains Mono` — codice (lezione, URL)

**Sintassi visiva (neobrutalism)**:
- Bordi spessi 2.5–3px navy
- Ombre offset solid `4–6px 4–6px 0 navy` (mai blur)
- Bottoni che si "schiacciano" su hover (`translate(2px,2px)` + ombra ridotta)
- Pattern halftone a puntini sullo sfondo

## File

| File | Vista | Note |
|---|---|---|
| `display.html` | Schermo pubblico (proiettore) | Domanda dominante, opzioni full-width, countdown fisso |
| `team_answer.html` | Capitano in fase risposta (mobile-first) | Voti compagni live, CTA risposta cartoon |

## Interazioni dimostrative

- **`display.html`**: il countdown parte automatico. Pulsanti footer a destra: "▸ Mostra risposta" (rivela la corretta in verde con animazione wiggle, le altre sbiadiscono) e "↻ Reset".
- **`team_answer.html`**: clicca un'opzione → si tinge di giallo → premi "RISPONDI" coral. Negli ultimi 5 secondi del countdown la card squadra ha bordo coral con ombra coral.

## Principi HCI applicati

1. **Visual hierarchy**: domanda dominante (`clamp(36px, 4.4vw, 64px)`), opzioni 24px, countdown 48px (medio, posizione fissa), squadre 28px.
2. **List pattern per le opzioni**: 4 row full-width invece di griglia 2×2 — l'occhio le scansiona verticalmente in <1s.
3. **Common region (Gestalt)**: squadre in band orizzontale unica, meta turno in singola riga, non card sparse.
4. **Posizione fissa per il countdown**: angolo alto-destra, sempre lì, l'occhio sa dove cercarlo.
5. **Reading order Z-pattern**: header → squadre → meta → DOMANDA → opzioni → footer.
