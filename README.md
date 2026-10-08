# Rete bus UDR05 – mappe e orari

Feed GTFS: `UDR05 GTFS dal 12-10-2026.zip` (Bus International Service, validità 12/10/2026 – 31/12/2026).

## Contenuto

| Percorso | Cosa contiene |
|---|---|
| `docs/index.html` | Pagina unica e autonoma con mappa interattiva e tabelle orarie di tutte le linee |
| `docs/orari/*.csv` | Tabelle orarie per linea, calendario e direzione (separatore `;`, apribili in Excel) |
| `tools/genera_sito.py` | Generatore (solo libreria standard Python) |
| `tools/template.html` | Modello della pagina |
| `tools/vendor/leaflet/` | Leaflet 1.9.4 (licenza BSD-2), incorporato nella pagina |

## Funzioni della pagina

- **Mappa** di tutte le linee con i colori di `routes.txt` su sfondo OpenStreetMap, a scelta tra OSM standard, OSM CARTO Voyager, Esri stradale, Esri satellite, ÖPNVKarte (trasporto pubblico), OSM Humanitarian e OpenTopoMap; clic su una linea per aprirla.
- **Scelta del giorno**: elenco e tabelle indicano quali calendari sono attivi nella data scelta (festività escluse da `calendar_dates.txt`).
- **Tabella oraria** per linea: schede per calendario (Lun–Ven, Lun–Sab, Sabato, Domenica e festivi) e per direzione; filtro «solo fermate principali» (fermate `timepoint=1` e capolinea).
- Clic sul numero di una **corsa** → il suo percorso viene evidenziato sulla mappa.
- Clic su una **fermata** (in mappa, in tabella o dalla ricerca) → tutte le partenze di quel giorno, per linea e direzione.
- **Strumenti della mappa** (sotto la mappa):
  - **Zone**: mostra o nasconde le linee per comune/area; **Solo linee in servizio nel giorno** nasconde quelle non attive nella data scelta (le scelte restano memorizzate).
  - **Bus in viaggio**: posizione dei bus ricavata dall'orario programmato (non è tempo reale), con cursore dell'ora, avvio/pausa dello scorrere del tempo e pulsante «Adesso»; clic su un bus per aprire la linea con la sua corsa evidenziata.
  - **Vicino a me**: mostra la propria posizione e le 6 fermate più vicine con la distanza (richiede il permesso di posizione del browser).
  - **Schermo intero** e **Stampa** (mappa e tabella oraria, senza elenco e pulsanti).
- **Ricerca** di linee e fermate; collegamenti diretti a una linea con `index.html#linea-<route_id>` (es. `#linea-14TV`).

Si apre direttamente nel browser (doppio clic su `docs/index.html`). Aperta come file locale, gli sfondi OpenStreetMap e CARTO vengono rifiutati dai loro server (riquadri «Access blocked» / «API key required»), perché richiedono l'indirizzo del sito di provenienza: in quel caso il predefinito è «Esri stradale». Pubblicata online (GitHub Pages) il predefinito è OpenStreetMap. Serve comunque la connessione per scaricare lo sfondo. Per pubblicarla online basta attivare GitHub Pages sulla cartella `docs/`.

## Rigenerare

```sh
python3 tools/genera_sito.py "UDR05 GTFS dal 12-10-2026.zip" docs
```

Con un nuovo feed basta sostituire lo zip e rilanciare il comando.
