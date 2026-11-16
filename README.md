# iPIXEL Color – integrazione Home Assistant (fork cash83)

Fork di [cagcoach/ha-ipixel-color](https://github.com/cagcoach/ha-ipixel-color)
per i display LED a matrice "iPixel Color" (BLE, es. pannelli 64×16 venduti
anche come B.K. Light LED Pixel Board). *English: this is a heavily extended
fork of the upstream integration; the panel UI is in Italian.*

## Cosa aggiunge questo fork

**Pannello laterale "iPixel"** completo (tema neon, anteprima dal vivo che
rispecchia il display fisico, pixel per pixel):

- **Composizione a caselle**: sensori e testi trascinabili, icona emoji
  automatica per tipo di sensore, unità di misura, decimali, e
  **grandezza/posizione verticale PER CASELLA** nella riga che scorre
  (ogni blocco del protocollo viaggia con la sua bitmap e il suo colore).
- **12+1 stili di orologio italiano** disegnati dal server (due righe, cifre
  grandi, binario, sole/luna, fiamme, cielo stellato, ora a parole, ora+icona
  MDI o emoji…), con data e anno.
- **Scritta libera posizionabile**: testo e/o valore di un'entità come
  immagine, con grandezza, posizione, scorrimento a velocità libera (anche
  più lenta del minimo del firmware).
- **Icone**: Material Design (per nome) o **emoji Twemoji**, con grandezza,
  posizione e scorrimento; emoji anche dentro il testo scorrevole.
- **Rotazione automatica con scaletta**: decidi tu i passi
  (sensori / immagini / orologio), l'ordine e la durata di ciascuno;
  la scaletta si rilegge a ogni giro.
- **Galleria** delle animazioni originali dell'app + libreria personale,
  **Disegno** pixel-art con modalità **in diretta** (fun mode + set_pixel),
  **Timer**, **Da internet** (GIF/immagini/video→GIF via ffmpeg).
- **Comandi nativi del firmware**: cronometro di bordo (conta da solo, zero
  BLE), tabellone segnapunti, **accensione/spegnimento programmati scritti
  nel display** (funzionano anche con HA spento), equalizzatore, **memoria
  del display** (slot di bordo: salva e richiama con 7 byte).
- **"Libera per l'app"**: lascia il BLE all'app del telefono per 10 minuti
  e si riaggancia da solo.

Sotto il cofano: client BLE con ritrasmissione e riconnessione, blocco delle
animazioni 3/4 (bootloop sui pannelli non 32×32), emoji ridimensionate dentro
i blocchi 16×16 del protocollo, aggiornamenti automatici con anti-riavvio
dello scorrimento (minimo 30 s tra ri-invii), ripristino del contenuto dopo
i riavvii di Home Assistant.

## Installazione (HACS)

1. HACS → ⋮ → **Repository personalizzati**
2. URL: `https://github.com/cash83/ha-ipixel-color` — categoria **Integration**
3. Installa "iPIXEL Color", riavvia Home Assistant
4. Il display viene trovato da solo via Bluetooth (nome `LED_BLE_*`);
   il pannello "iPixel" compare nella barra laterale.

## Requisiti

- Home Assistant 2025.1+ con Bluetooth (adattatore locale consigliato:
  via proxy ESPHome i pacchetti grandi possono perdersi)
- Dipendenze Python installate da HA: `pypixelcolor >=0.4,<0.5`, `pillow`,
  `bleak`, `bleak-retry-connector`, `resvg_py`

## Crediti

- [cagcoach](https://github.com/cagcoach/ha-ipixel-color) — integrazione originale
- [lucagoc/pypixelcolor](https://github.com/lucagoc/pypixelcolor) — libreria del protocollo
- ahzs645, tigers75, CzarofAK, nielsmaerten, scyto — ricerche sul protocollo
  e idee riprese nei rispettivi fork
- Twemoji (CC-BY 4.0) per le emoji, Material Design Icons per le icone

## Avvertenze

- Il comando di cancellazione ROM del firmware **non è esposto da nessuna
  parte**, di proposito: cancella tutto, impostazioni comprese.
- Le animazioni testo "scorri su/giù" sono bloccate sui pannelli non 32×32:
  mandano il display in bootloop (documentato dalla community).
