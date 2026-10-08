#!/usr/bin/env python3
"""Genera mappe interattive e tabelle orarie da un feed GTFS.

Uso:
    python3 tools/genera_sito.py ["UDR05 GTFS dal 12-10-2026.zip"] [cartella_output] [--frammento]

    --frammento  scrive la pagina senza <html>/<head> (per pubblicarla come Artifact)

Produce:
    <output>/index.html        pagina unica e autonoma (mappa + orari)
    <output>/orari/*.csv       tabelle orarie per linea, calendario e direzione

Richiede solo la libreria standard di Python. Leaflet è incluso in tools/vendor.
"""

import base64
import csv
import io
import json
import re
import sys
import zipfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = Path(__file__).resolve().parent / "vendor" / "leaflet"
TEMPLATE = Path(__file__).resolve().parent / "template.html"

GIORNI = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
GIORNI_IT = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]


def leggi(zf, nome):
    try:
        raw = zf.read(nome)
    except KeyError:
        return []
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))


def minuti(hms):
    h, m, s = (int(x) for x in hms.strip().split(":"))
    return h * 60 + m + (1 if s >= 30 else 0)


def fmt(mins):
    return f"{mins // 60:02d}:{mins % 60:02d}"


def gtfs_date(s):
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


# --- nomi fermata --------------------------------------------------------------

RE_INDIRIZZO = re.compile(r",\s*\d{5}\s+(.+?)\s+[A-Z]{2},\s*Italia\s*$")


def pulisci_nome(nome):
    """Restituisce (nome breve, località) da nomi come
    'GUIDONIA | Via Maremmana Via Campanella # f12245' o
    'Via Dante Alighieri, 4, 00011 Tivoli Terme RM, Italia'."""
    nome = nome.strip()
    loc = ""
    m = RE_INDIRIZZO.search(nome)
    if m:
        loc = m.group(1)
        nome = nome[: m.start()]
    if " | " in nome:
        citta, nome = nome.split(" | ", 1)
        loc = loc or citta.strip().title()
    nome = re.sub(r"\s*#\s*f\d+\s*", " ", nome).strip(" ,")
    return nome, loc


# --- geometria -----------------------------------------------------------------

def rdp(punti, eps):
    """Douglas-Peucker iterativo su coordinate (lat, lon)."""
    if len(punti) < 3:
        return punti
    tieni = [False] * len(punti)
    tieni[0] = tieni[-1] = True
    stack = [(0, len(punti) - 1)]
    while stack:
        a, b = stack.pop()
        (y1, x1), (y2, x2) = punti[a], punti[b]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5
        dmax, idx = 0.0, -1
        for i in range(a + 1, b):
            y0, x0 = punti[i]
            if norm == 0:
                d = ((x0 - x1) ** 2 + (y0 - y1) ** 2) ** 0.5
            else:
                d = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / norm
            if d > dmax:
                dmax, idx = d, i
        if dmax > eps and idx > 0:
            tieni[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [p for p, k in zip(punti, tieni) if k]


def polyline(punti):
    """Codifica Google Encoded Polyline (precisione 1e-5)."""
    out, py, px = [], 0, 0
    for lat, lon in punti:
        y, x = round(lat * 1e5), round(lon * 1e5)
        for v in (y - py, x - px):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        py, px = y, x
    return "".join(out)


# --- unione delle sequenze di fermate -----------------------------------------

def unisci(base, seq):
    """Supersequenza comune di `base` e `seq` costruita tramite LCS: entrambe ne restano
    sottosequenze, così ogni corsa trova le sue fermate nell'ordine giusto."""
    n, m = len(base), len(seq)
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            lcs[i][j] = lcs[i + 1][j + 1] + 1 if base[i] == seq[j] else max(lcs[i + 1][j], lcs[i][j + 1])
    out, i, j = [], 0, 0
    while i < n and j < m:
        if base[i] == seq[j]:
            out.append(base[i]); i += 1; j += 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            out.append(base[i]); i += 1
        else:
            out.append(seq[j]); j += 1
    out.extend(base[i:])
    out.extend(seq[j:])
    return out


def incorpora(merged, seq):
    """Posizioni di `seq` dentro `merged` (di cui è sottosequenza), scelte in modo greedy.
    Gestisce anche le circolari che passano più volte dalla stessa fermata."""
    out, j = [], 0
    for x in seq:
        while merged[j] != x:
            j += 1
        out.append(j)
        j += 1
    return out


# --- calendari ------------------------------------------------------------------

def descrivi_servizio(c, eccezioni):
    giorni = [i for i, g in enumerate(GIORNI) if c[g] == "1"]
    if giorni == list(range(5)):
        etichetta = "Lunedì–Venerdì"
    elif giorni == list(range(6)):
        etichetta = "Lunedì–Sabato"
    elif giorni == list(range(7)):
        etichetta = "Tutti i giorni"
    elif len(giorni) == 1:
        etichetta = GIORNI_IT[giorni[0]]
    else:
        etichetta = ", ".join(GIORNI_IT[i] for i in giorni)
    if c["sunday"] == "1" and len(giorni) == 1:
        etichetta = "Domenica e festivi"
    return etichetta


def date_attive(c, eccezioni):
    d, fine = gtfs_date(c["start_date"]), gtfs_date(c["end_date"])
    attive = set()
    while d <= fine:
        if c[GIORNI[d.weekday()]] == "1":
            attive.add(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    for e in eccezioni:
        if e["exception_type"] == "1":
            attive.add(e["date"])
        else:
            attive.discard(e["date"])
    return attive


# --- main ---------------------------------------------------------------------------

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    zip_path = Path(args[0]) if args else next(ROOT.glob("*.zip"))
    out_dir = Path(args[1]) if len(args) > 1 else ROOT / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        agency = leggi(zf, "agency.txt")
        feed_info = leggi(zf, "feed_info.txt")
        routes = leggi(zf, "routes.txt")
        trips = leggi(zf, "trips.txt")
        stops = leggi(zf, "stops.txt")
        stop_times = leggi(zf, "stop_times.txt")
        shapes = leggi(zf, "shapes.txt")
        calendar = leggi(zf, "calendar.txt")
        calendar_dates = leggi(zf, "calendar_dates.txt")

    # Calendari
    ecc = defaultdict(list)
    for e in calendar_dates:
        ecc[e["service_id"]].append(e)
    servizi = {}
    tutte_date = set()
    for c in calendar:
        sid = c["service_id"]
        attive = date_attive(c, ecc[sid])
        tutte_date |= attive
        servizi[sid] = {
            "label": descrivi_servizio(c, ecc[sid]),
            "dal": c["start_date"],
            "al": c["end_date"],
            "esclusi": sorted(e["date"] for e in ecc[sid] if e["exception_type"] == "2"),
            "date": sorted(attive),
            "giorni": len(attive),
            "scol": "SCL" in sid,
        }
    for sid in {e["service_id"] for e in calendar_dates} - servizi.keys():
        attive = sorted(e["date"] for e in ecc[sid] if e["exception_type"] == "1")
        tutte_date |= set(attive)
        servizi[sid] = {"label": sid, "dal": attive[0], "al": attive[-1], "esclusi": [],
                        "date": attive, "giorni": len(attive)}

    # Fermate
    stop_idx, stops_out = {}, []
    for s in stops:
        if s.get("location_type", "0") not in ("", "0"):
            continue
        nome, loc = pulisci_nome(s["stop_name"])
        stop_idx[s["stop_id"]] = len(stops_out)
        stops_out.append([s["stop_id"], nome, loc, round(float(s["stop_lat"]), 6), round(float(s["stop_lon"]), 6)])

    # Percorsi (shapes) semplificati
    pts = defaultdict(list)
    for p in shapes:
        pts[p["shape_id"]].append((int(p["shape_pt_sequence"]), float(p["shape_pt_lat"]), float(p["shape_pt_lon"])))
    shapes_out, shape_km = {}, {}
    for sid, lst in pts.items():
        lst.sort()
        shapes_out[sid] = polyline(rdp([(a, b) for _, a, b in lst], 0.00002))

    # Orari per corsa
    st_by_trip = defaultdict(list)
    for r in stop_times:
        st_by_trip[r["trip_id"]].append(r)
    for lst in st_by_trip.values():
        lst.sort(key=lambda r: int(r["stop_sequence"]))
    for t in trips:
        lst = st_by_trip.get(t["trip_id"])
        if lst and lst[-1].get("shape_dist_traveled"):
            try:
                shape_km.setdefault(t["shape_id"], float(lst[-1]["shape_dist_traveled"]))
            except ValueError:
                pass

    # Tabelle orarie: linea -> servizio -> direzione
    gruppi = defaultdict(list)
    for t in trips:
        if t["trip_id"] in st_by_trip:
            gruppi[(t["route_id"], t["service_id"], t.get("direction_id") or "0")].append(t)

    route_by_id = {r["route_id"]: r for r in routes}

    def ordina_linea(r):
        n = r["route_short_name"]
        m = re.match(r"^(\D*)(\d+)(?:_(\d+))?", n)
        return (m.group(1), int(m.group(2)), int(m.group(3) or 0)) if m else (n, 0, 0)

    csv_dir = out_dir / "orari"
    csv_dir.mkdir(exist_ok=True)
    for f in csv_dir.glob("*.csv"):
        f.unlink()

    linee = []
    for r in sorted(routes, key=ordina_linea):
        rid = r["route_id"]
        tabelle = []
        shape_ids = set()
        for (g_rid, svc, dirz), tlist in sorted(gruppi.items()):
            if g_rid != rid:
                continue
            # sequenze di fermate (con occorrenza) per corsa
            seqs = {t["trip_id"]: tuple(x["stop_id"] for x in st_by_trip[t["trip_id"]]) for t in tlist}
            pattern = sorted(set(seqs.values()), key=lambda p: (-len(p), p))
            merged = list(pattern[0])
            for p in pattern[1:]:
                merged = unisci(merged, list(p))
            posizioni = {p: incorpora(merged, p) for p in pattern}
            principali = [False] * len(merged)
            corse = []
            for t in tlist:
                shape_ids.add(t["shape_id"])
                orari = [None] * len(merged)
                for i, x in zip(posizioni[seqs[t["trip_id"]]], st_by_trip[t["trip_id"]]):
                    orari[i] = minuti(x["departure_time"] or x["arrival_time"])
                    if x.get("timepoint") == "1":
                        principali[i] = True
                corse.append({"id": t["trip_id"], "shape": t["shape_id"], "hs": t["trip_headsign"],
                              "o": orari})
            corse.sort(key=lambda c: min(v for v in c["o"] if v is not None))
            # capolinea sempre "principali"
            for c in corse:
                idx = [i for i, v in enumerate(c["o"]) if v is not None]
                principali[idx[0]] = principali[idx[-1]] = True
            heads = [c["hs"] for c in corse if c["hs"]]
            titolo = max(set(heads), key=heads.count) if heads else r["route_long_name"]
            tabelle.append({
                "svc": svc, "dir": dirz, "titolo": titolo,
                "fermate": [stop_idx[k] for k in merged],
                "princ": [1 if p else 0 for p in principali],
                "corse": [{"id": c["id"], "shape": c["shape"], "o": c["o"]} for c in corse],
            })
            # CSV
            nome_csv = f"Linea_{r['route_short_name']}_{svc}_dir{dirz}.csv"
            with open(csv_dir / nome_csv, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow([f"Linea {r['route_short_name']} – {titolo}", servizi.get(svc, {}).get("label", svc)]
                           + [""] * (len(corse) - 1))
                w.writerow(["Fermata", "Località"] + [c["id"] for c in corse])
                for i, k in enumerate(merged):
                    s = stops_out[stop_idx[k]]
                    w.writerow([s[1], s[2]] + [fmt(c["o"][i]) if c["o"][i] is not None else "" for c in corse])

        if not tabelle:
            continue
        km = [shape_km[s] for s in shape_ids if s in shape_km]
        linee.append({
            "id": rid,
            "n": r["route_short_name"],
            "nome": r["route_long_name"],
            "area": r.get("route_desc") or "",
            "col": "#" + (r.get("route_color") or "666666").lstrip("#"),
            "shapes": sorted(shape_ids),
            "km": round(max(km), 1) if km else None,
            "tab": tabelle,
        })

    fi = feed_info[0] if feed_info else {}
    ag = agency[0] if agency else {}
    dati = {
        "agenzia": {"nome": ag.get("agency_name", ""), "url": ag.get("agency_url", ""),
                    "tel": ag.get("agency_phone", "")},
        "feed": {"dal": fi.get("feed_start_date") or min(tutte_date), "al": fi.get("feed_end_date") or max(tutte_date),
                 "versione": fi.get("feed_version", ""), "file": zip_path.name},
        "servizi": servizi,
        "fermate": stops_out,
        "shapes": shapes_out,
        "shape_km": {k: round(v, 1) for k, v in shape_km.items()},
        "linee": linee,
    }

    html = TEMPLATE.read_text(encoding="utf-8")
    css = (VENDOR / "leaflet.css").read_text(encoding="utf-8")
    for img in ("layers.png", "layers-2x.png"):
        b64 = base64.b64encode((VENDOR / "images" / img).read_bytes()).decode()
        css = css.replace(f"url(images/{img})", f"url(data:image/png;base64,{b64})")
    html = html.replace("/*LEAFLET_CSS*/", css)
    html = html.replace("/*LEAFLET_JS*/", (VENDOR / "leaflet.js").read_text(encoding="utf-8").replace("</script", "<\\/script"))
    payload = json.dumps(dati, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace("/*DATI_GTFS*/null", payload)
    if "--frammento" not in sys.argv:
        testa, corpo = html.split('<div class="app">', 1)
        html = ('<!doctype html>\n<html lang="it">\n<head>\n<meta charset="utf-8">\n' + testa
                + '</head>\n<body>\n<div class="app">' + corpo + "</body>\n</html>\n")
    (out_dir / "index.html").write_text(html, encoding="utf-8")

    n_corse = sum(len(t["corse"]) for l in linee for t in l["tab"])
    print(f"{len(linee)} linee, {n_corse} corse, {len(stops_out)} fermate, {len(shapes_out)} percorsi")
    print(f"Scritto {out_dir / 'index.html'} ({(out_dir / 'index.html').stat().st_size / 1024:.0f} KB)")
    print(f"Scritti {len(list(csv_dir.glob('*.csv')))} CSV in {csv_dir}")


if __name__ == "__main__":
    main()
