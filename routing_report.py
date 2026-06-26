"""
Jugos del Día — Generador diario de reporte de rutas y despacho.

Lee la hoja de Google Sheets "Jugos del dia test 2", filtra la pestaña
correspondiente a la fecha de hoy, procesa solo los pedidos APROBADOS,
agrupa por sector de Guayaquil, asigna los 3 vehículos disponibles según
peso acumulado por ruta y escribe el reporte en el mismo Drive.

Flota disponible (fija):
  - Furgoneta    : hasta  900 kg
  - Camión 2.5T  : hasta 2500 kg
  - Camión 3.5T  : hasta 3500 kg
"""

from __future__ import annotations

import os
import sys
from datetime import date
from typing import NamedTuple

from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ── Configuración ────────────────────────────────────────────────────────────

SPREADSHEET_ID = "1U9OkfQnyeXRTtozQFBkb55r2_TMfGItXXu9EmgegTbg"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Pesos de referencia por gaveta (kg)
GAVETA_KG = {
    "360 ml": 12, "360ml": 12,
    "500 ml": 27, "500ml": 27,
    "1,5 l": 20,  "1.5 l": 20, "1,5l": 20, "1.5l": 20,
    "5 l": 30,    "5l": 30,
}

# Flota (de menor a mayor capacidad)
FLEET = [
    {"name": "Furgoneta",   "max_kg": 900},
    {"name": "Camión 2.5T", "max_kg": 2500},
    {"name": "Camión 3.5T", "max_kg": 3500},
]

# Palabras clave por sector de Guayaquil
SECTOR_KEYWORDS: dict[str, list[str]] = {
    "Samborondón": ["samborondón", "samborondon", "business center", "village plaza"],
    "Norte": [
        "orellana", "lapentti", "bombero", "febres-cordero", "américas", "americas",
        "tanca marengo", "samuel cisneros", "carlos luis plaza", "domingo comín",
        "isidro ayora", "benjamín rosales", "benjamin rosales", "amazonas",
        "joaquín orrantia", "joaquin orrantia", "ernesto albán", "ernesto alban",
        "quito",
    ],
    "Centro": [
        "cuenca", "pedro carbo", "rumichaca", "escobedo", "pichincha",
        "malecón", "malecon", "boyacá", "boyaca", "loja", "clemente ballén",
        "clemente ballen", "garaicoa", "9 de octubre", "chile",
    ],
    "Sur": ["25 de julio", "guasmo", "pradera", "ferroviaria", "puerto lisa"],
}


# ── Modelos ───────────────────────────────────────────────────────────────────

class Order(NamedTuple):
    row: int
    address: str
    client: str
    product_code: str
    product_name: str
    size: str
    quantity: int
    billed: float
    hour: str
    status: str
    sector: str
    weight_kg: float


class Route(NamedTuple):
    vehicle: str
    max_kg: float
    orders: list

    @property
    def total_kg(self) -> float:
        return sum(o.weight_kg for o in self.orders)

    @property
    def total_billed(self) -> float:
        return sum(o.billed for o in self.orders)


# ── Autenticación ─────────────────────────────────────────────────────────────

def get_credentials():
    sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if sa_file and os.path.exists(sa_file):
        return service_account.Credentials.from_service_account_file(sa_file, scopes=SCOPES)
    token = os.environ.get("GOOGLE_ACCESS_TOKEN")
    if token:
        return Credentials(token=token)
    import google.auth
    creds, _ = google.auth.default(scopes=SCOPES)
    return creds


# ── Lectura ───────────────────────────────────────────────────────────────────

def find_sheet_for_today(svc, today: date) -> str | None:
    months = ["enero","febrero","marzo","abril","mayo","junio",
              "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    candidates = [
        f"{today.day} de {months[today.month-1]} de {today.year}",
        today.strftime("%Y-%m-%d"),
        today.strftime("%d/%m/%Y"),
    ]
    meta = svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for sheet in meta.get("sheets", []):
        title = sheet["properties"]["title"].lower()
        if any(c in title for c in candidates):
            return sheet["properties"]["title"]
    return None


def read_orders(svc, sheet_name: str) -> list:
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"'{sheet_name}'!A:Z"
    ).execute()
    rows = result.get("values", [])
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    col = {n: i for i, n in enumerate(header)}

    def g(row, key, default=""):
        i = col.get(key)
        return (row[i].strip() if i < len(row) else default) if i is not None else default

    orders = []
    for i, row in enumerate(rows[1:], 2):
        raw_cant = g(row, "cantidad gavetas")
        raw_fact = g(row, "facturado") or g(row, "facturado ")
        size, qty, bill = _parse_fields(g(row, "productos"), raw_cant, raw_fact)
        addr = g(row, "direccion") or g(row, "dirección")
        orders.append(Order(
            row=i, address=addr, client=g(row, "cliente"),
            product_code=g(row, "productos"),
            product_name=g(row, "presentacion") or g(row, "presentación"),
            size=size, quantity=qty, billed=bill,
            hour=g(row, "hora comprometida"),
            status=g(row, "estado"),
            sector=_sector(addr),
            weight_kg=GAVETA_KG.get(size.strip().lower(), 15) * qty,
        ))
    return orders


def _parse_fields(col3, col_cant, col_fact):
    """El sheet tiene el tamaño en la columna 'Cantidad Gavetas' y la
    cantidad numérica en 'Facturado'. Detecta y normaliza."""
    is_size = any(u in col_cant.lower() for u in ("ml", " l", "l"))
    if is_size:
        size = col_cant.strip().lower()
        try:
            qty = int(float(col_fact.replace("$", "").replace(",", ".")))
        except (ValueError, AttributeError):
            qty = 0
        try:
            bill = float(col_fact.replace("$", "").replace(",", "."))
        except (ValueError, AttributeError):
            bill = 0.0
    else:
        size = col3.strip().lower()
        try:
            qty = int(float(col_cant))
        except (ValueError, AttributeError):
            qty = 0
        try:
            bill = float(col_fact.replace("$", "").replace(",", "."))
        except (ValueError, AttributeError):
            bill = 0.0
    return size, qty, bill


def _sector(addr: str) -> str:
    a = addr.lower()
    for sec, kws in SECTOR_KEYWORDS.items():
        if any(k in a for k in kws):
            return sec
    return "Centro"


# ── Asignación de vehículos ───────────────────────────────────────────────────

def assign_vehicles(orders: list) -> tuple:
    """Asigna exactamente 3 vehículos. Primero intenta colocar sectores
    completos en el vehículo de menor capacidad suficiente. Si un sector
    no cabe en ningún vehículo disponible, distribuye pedido a pedido.
    Los que no caben se retornan como 'unassigned' y se reportan."""
    sec_map: dict = {}
    for o in orders:
        sec_map.setdefault(o.sector, []).append(o)
    for s in sec_map:
        sec_map[s].sort(key=lambda o: _h2m(o.hour))

    cap = {v["name"]: v["max_kg"] for v in FLEET}
    slots: dict = {v["name"]: [] for v in FLEET}
    unassigned = []

    for sec, sec_orders in sorted(sec_map.items(),
                                   key=lambda x: sum(o.weight_kg for o in x[1]),
                                   reverse=True):
        w = sum(o.weight_kg for o in sec_orders)
        placed = False
        for v in sorted(FLEET, key=lambda x: x["max_kg"]):
            if cap[v["name"]] >= w:
                slots[v["name"]].extend(sec_orders)
                cap[v["name"]] -= w
                placed = True
                break
        if not placed:
            for o in sec_orders:
                p2 = False
                for v in sorted(FLEET, key=lambda x: x["max_kg"]):
                    if cap[v["name"]] >= o.weight_kg:
                        slots[v["name"]].append(o)
                        cap[v["name"]] -= o.weight_kg
                        p2 = True
                        break
                if not p2:
                    unassigned.append(o)

    routes = [Route(v["name"], v["max_kg"],
                    sorted(slots[v["name"]], key=lambda o: _h2m(o.hour)))
              for v in FLEET]
    return routes, unassigned


def _h2m(s: str) -> int:
    p = s.lower().replace("h", ":").split(":")
    try:
        return int(p[0]) * 60 + (int(p[1]) if len(p) > 1 else 0)
    except (ValueError, IndexError):
        return 0


# ── Reporte ───────────────────────────────────────────────────────────────────

def _fmt(d: date) -> str:
    m = ["enero","febrero","marzo","abril","mayo","junio",
         "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    return f"{d.day} de {m[d.month-1]} de {d.year}"


def build_report(today: date, routes, unassigned, approved, rejected) -> list:
    R: list = []
    cap_total = sum(v["max_kg"] for v in FLEET)
    demand = sum(o.weight_kg for o in approved)
    overflow = max(0.0, demand - cap_total)

    R += [
        [f"REPORTE DE RUTAS Y DESPACHO — {_fmt(today)}"],
        ["Generado automáticamente | routing_report.py"],
        [""],
        ["RESUMEN GENERAL"],
        ["Total pedidos",        str(len(approved) + len(rejected))],
        ["Aprobados",            str(len(approved))],
        ["No aprobados",         str(len(rejected))],
        ["Demanda total",        f"{demand:.0f} kg"],
        ["Capacidad total flota",f"{cap_total} kg"],
        ["⚠ EXCESO DE DEMANDA" if overflow > 0 else "✔ Toda la demanda cubierta",
         f"{overflow:.0f} kg sin vehículo" if overflow > 0 else ""],
        ["Facturación total",    f"${sum(o.billed for o in approved):.2f}"],
    ]

    for rt in routes:
        if not rt.orders:
            continue
        uso = rt.total_kg / rt.max_kg * 100
        R += [
            [""],
            [f"══ VEHÍCULO: {rt.vehicle} — {rt.total_kg:.0f}/{int(rt.max_kg)} kg ({uso:.0f}%) — ${rt.total_billed:.2f}"],
            ["Hora","Dirección","Cliente","Producto","Presentación","Gavetas","Peso kg","Facturado","Sector"],
        ]
        for o in rt.orders:
            R.append([o.hour, o.address, o.client, o.product_name, o.size,
                      str(o.quantity), f"{o.weight_kg:.0f}", f"${o.billed:.2f}", o.sector])
        R.append(["SUBTOTAL","","","","",
                  str(sum(o.quantity for o in rt.orders)),
                  f"{rt.total_kg:.0f}", f"${rt.total_billed:.2f}",""])

    if unassigned:
        R += [
            [""],
            ["⚠ PEDIDOS SIN VEHÍCULO — capacidad de flota excedida"],
            ["Hora","Dirección","Cliente","Producto","Presentación","Gavetas","Peso kg","Facturado","Sector"],
        ]
        for o in unassigned:
            R.append([o.hour, o.address, o.client, o.product_name, o.size,
                      str(o.quantity), f"{o.weight_kg:.0f}", f"${o.billed:.2f}", o.sector])

    R += [
        [""],
        ["══ RESUMEN FINAL"],
        ["Vehículo","Paradas","Peso kg","Cap kg","Uso %","Facturación"],
    ]
    for rt in routes:
        uso = f"{rt.total_kg/rt.max_kg*100:.0f}%" if rt.orders else "0%"
        R.append([rt.vehicle, str(len(rt.orders)), f"{rt.total_kg:.0f}",
                  str(int(rt.max_kg)), uso, f"${rt.total_billed:.2f}"])
    R.append(["GRAN TOTAL", str(sum(len(r.orders) for r in routes)),
              f"{sum(r.total_kg for r in routes):.0f}", str(cap_total), "",
              f"${sum(r.total_billed for r in routes):.2f}"])
    if unassigned:
        R.append(["⚠ Sin vehículo", str(len(unassigned)),
                  f"{sum(o.weight_kg for o in unassigned):.0f}", "", "",
                  f"${sum(o.billed for o in unassigned):.2f}"])

    if rejected:
        R += [
            [""],
            ["══ PEDIDOS EXCLUIDOS (NO APROBADOS)"],
            ["Hora","Dirección","Cliente","Producto","Presentación","Gavetas","Facturado","Estado"],
        ]
        for o in rejected:
            R.append([o.hour, o.address, o.client, o.product_name, o.size,
                      str(o.quantity), f"${o.billed:.2f}", o.status])

    R += [
        [""],
        ["══ NOTAS TÉCNICAS"],
        ["Pesos/gaveta", "360 ml=12 kg | 500 ml=27 kg | 1.5 L=20 kg (estimado)"],
        ["Flota",        "Furgoneta ≤900 kg | Camión 2.5T ≤2500 kg | Camión 3.5T ≤3500 kg"],
        ["Si demanda > 6900 kg", "Los pedidos que no caben aparecen en seción SIN VEHÍCULO"],
    ]
    return R


# ── Escritura ─────────────────────────────────────────────────────────────────

def write_report(svc, sheet_name: str, rows: list) -> None:
    existing = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"'{sheet_name}'!A:A"
    ).execute()
    start_row = len(existing.get("values", [])) + 3
    svc.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{sheet_name}'!A{start_row}",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()
    print(f"✔ Reporte escrito en '{sheet_name}' desde fila {start_row}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    today = date.today()
    print(f"Fecha: {_fmt(today)}")

    svc = build("sheets", "v4", credentials=get_credentials())

    sheet_name = find_sheet_for_today(svc, today)
    if not sheet_name:
        print(f"⚠ No hay hoja para {_fmt(today)} en el spreadsheet.")
        sys.exit(1)
    print(f"Hoja: '{sheet_name}'")

    all_orders = read_orders(svc, sheet_name)
    approved = [o for o in all_orders if o.status.lower() == "aprobado"]
    rejected = [o for o in all_orders if o.status.lower() != "aprobado"]
    print(f"Aprobados: {len(approved)} | No aprobados: {len(rejected)}")

    if not approved:
        print("Sin pedidos aprobados hoy.")
        sys.exit(0)

    routes, unassigned = assign_vehicles(approved)
    for r in routes:
        print(f"  {r.vehicle}: {len(r.orders)} paradas — {r.total_kg:.0f} kg — ${r.total_billed:.2f}")
    if unassigned:
        print(f"  ⚠ Sin vehículo: {len(unassigned)} pedido(s) — {sum(o.weight_kg for o in unassigned):.0f} kg")

    write_report(svc, sheet_name, build_report(today, routes, unassigned, approved, rejected))
    print("✔ Listo.")


if __name__ == "__main__":
    main()
