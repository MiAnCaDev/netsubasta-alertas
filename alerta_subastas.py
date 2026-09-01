#!/usr/bin/env python3
"""
Alerta de vehículos nuevos en netsubasta.com según filtros configurables.

Uso:
  python3 alerta_subastas.py              # Solo vehículos nuevos
  python3 alerta_subastas.py --all        # Todos los que cumplen filtros
  python3 alerta_subastas.py --init       # Marcar actuales como vistos (sin mostrar)
  python3 alerta_subastas.py --reset      # Borrar historial de vistos

Crontab (cada 30 min):
  */30 * * * * cd /ruta/Subastas && /usr/bin/python3 alerta_subastas.py >> logs/alertas.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import escape, unescape
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "config.env"
USER_AGENT = "Mozilla/5.0 (compatible; NetsubastaAlert/1.0)"


@dataclass
class Config:
    base_url: str = "https://netsubasta.com"
    tipos: list[str] = field(default_factory=lambda: ["10", "13"])
    estados: list[str] = field(default_factory=list)
    ubicaciones: list[str] = field(default_factory=list)
    seen_file: Path = field(default_factory=lambda: SCRIPT_DIR / "data" / "seen_ids.json")
    detail_delay: float = 0.5
    fetch_details: bool = True
    telegram_title: str = "SERVIDOR HP"
    telegram_desc_max: int = 120


@dataclass
class Vehicle:
    vehicle_id: str
    auction_id: str
    title: str
    url: str
    location: str
    registration: str
    kilometers: str
    price: str
    estado: str
    combustible: str
    extras: str
    typology_id: str
    # Detalle (opcional)
    marca: str = ""
    modelo: str = ""
    descripcion: str = ""
    precio_detalle: str = ""


def load_config(path: Path) -> Config:
    cfg = Config()
    if not path.exists():
        return cfg

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip().upper()] = value.strip()

    if "BASE_URL" in values:
        cfg.base_url = values["BASE_URL"].rstrip("/")
    if "TIPOS" in values:
        cfg.tipos = [x.strip() for x in values["TIPOS"].split(",") if x.strip()]
    if "ESTADOS" in values:
        cfg.estados = [x.strip() for x in values["ESTADOS"].split(",") if x.strip()]
    if "UBICACIONES" in values:
        cfg.ubicaciones = [x.strip() for x in values["UBICACIONES"].split(",") if x.strip()]
    if "SEEN_FILE" in values:
        cfg.seen_file = SCRIPT_DIR / values["SEEN_FILE"]
    if "DETAIL_DELAY" in values:
        cfg.detail_delay = float(values["DETAIL_DELAY"])
    if "FETCH_DETAILS" in values:
        cfg.fetch_details = values["FETCH_DETAILS"].lower() in ("1", "true", "yes", "si", "sí")
    if "TELEGRAM_TITLE" in values:
        cfg.telegram_title = values["TELEGRAM_TITLE"]
    if "TELEGRAM_DESC_MAX" in values:
        cfg.telegram_desc_max = int(values["TELEGRAM_DESC_MAX"])

    return cfg


def normalize(text: str) -> str:
    text = unescape(text).strip().lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def fetch_html(url: str, data: bytes | None = None, timeout: int = 30) -> str:
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_tags(tag_text: str) -> tuple[str, str, str]:
    """Parsea 'Averiados, Diésel, V.F.U.' -> estado, combustible, extras."""
    parts = [p.strip() for p in tag_text.split(",") if p.strip()]
    estado = parts[0] if parts else ""
    combustible = parts[1] if len(parts) > 1 else ""
    extras = ", ".join(parts[2:]) if len(parts) > 2 else ""
    return estado, combustible, extras


def estado_coincide(estado_listado: str, permitidos: list[str]) -> bool:
    est_norm = normalize(estado_listado.rstrip("s"))  # Averiados -> averiado
    for allowed in permitidos:
        allowed_norm = normalize(allowed)
        if est_norm == allowed_norm or est_norm.startswith(allowed_norm):
            return True
        # Coincidencia parcial: "con danos exteriores"
        if allowed_norm in normalize(estado_listado):
            return True
    return False


def ubicacion_coincide(location: str, permitidas: list[str]) -> bool:
    loc_norm = normalize(location)
    return any(normalize(u) == loc_norm for u in permitidas)


def extract_vehicle_id(url: str) -> str:
    match = re.search(r"-(\d+)/?$", url) or re.search(r"-(\d+)(?:\?|$)", url)
    if match:
        return match.group(1)
    match = re.search(r"/(\d+)/?$", url)
    return match.group(1) if match else url


def parse_listing_row(row_html: str, typology_id: str) -> Vehicle | None:
    url_match = re.search(r'href="(https://netsubasta\.com/vehiculos/[^"]+)"', row_html)
    name_match = re.search(r'class="name">\s*<a[^>]*>([^<]+)</a>\s*([^<]*)', row_html, re.S)
    if not url_match or not name_match:
        return None

    auction_match = re.search(r'data-auction="(\d+)"', row_html)
    loc_match = re.search(r'class="location">([^<]+)', row_html)
    reg_match = re.search(r'class="register">([^<]+)', row_html)
    km_match = re.search(r'class="km">([^<]+)', row_html)
    price_match = re.search(r'class="amount">.*?(\d[\d\.]*)\s*&euro;', row_html, re.S)

    url = url_match.group(1)
    tag_text = name_match.group(2).strip()
    estado, combustible, extras = parse_tags(tag_text)

    return Vehicle(
        vehicle_id=extract_vehicle_id(url),
        auction_id=auction_match.group(1) if auction_match else "",
        title=unescape(name_match.group(1).strip()),
        url=url,
        location=unescape(loc_match.group(1).strip()) if loc_match else "",
        registration=unescape(reg_match.group(1).strip()) if reg_match else "",
        kilometers=unescape(km_match.group(1).strip()) if km_match else "",
        price=price_match.group(1).replace(".", "") + " €" if price_match else "",
        estado=estado,
        combustible=combustible,
        extras=extras,
        typology_id=typology_id,
    )


def fetch_listings(cfg: Config, typology_id: str) -> list[Vehicle]:
    url = f"{cfg.base_url}/vehiculos"
    data = urllib.parse.urlencode({"typologies_id": typology_id, "submit": "1"}).encode()
    html = fetch_html(url, data=data)
    rows = re.findall(r'<tr data-auction="\d+"[^>]*>.*?</tr>', html, re.S)
    vehicles: list[Vehicle] = []
    seen_ids: set[str] = set()

    for row in rows:
        vehicle = parse_listing_row(row, typology_id)
        if vehicle and vehicle.vehicle_id not in seen_ids:
            seen_ids.add(vehicle.vehicle_id)
            vehicles.append(vehicle)

    return vehicles


def parse_detail_field(html: str, label: str) -> str:
    pattern = rf'<div class="label">{re.escape(label)}:</div>\s*(?:<[^>]+>)?([^<]+)'
    match = re.search(pattern, html)
    return unescape(match.group(1).strip()) if match else ""


def fetch_vehicle_details(cfg: Config, vehicle: Vehicle) -> None:
    try:
        html = fetch_html(vehicle.url)
    except urllib.error.URLError as exc:
        vehicle.descripcion = f"(No se pudo cargar detalle: {exc})"
        return

    vehicle.marca = parse_detail_field(html, "Marca")
    vehicle.modelo = parse_detail_field(html, "Modelo")
    vehicle.precio_detalle = parse_detail_field(html, "Precio de salida")

    desc_match = re.search(
        r'<h5>Más información:</h5>.*?<dd><p>(.*?)</p></dd>',
        html,
        re.S,
    )
    if desc_match:
        vehicle.descripcion = unescape(re.sub(r"<[^>]+>", " ", desc_match.group(1)))
        vehicle.descripcion = re.sub(r"\s+", " ", vehicle.descripcion).strip()

    hidden = re.search(r'itemprop="description" class="hidden">([^<]+)', html)
    if hidden and not vehicle.descripcion:
        vehicle.descripcion = unescape(hidden.group(1).strip())


def filter_vehicles(cfg: Config, vehicles: list[Vehicle]) -> list[Vehicle]:
    filtered: list[Vehicle] = []
    for v in vehicles:
        if not ubicacion_coincide(v.location, cfg.ubicaciones):
            continue
        if not estado_coincide(v.estado, cfg.estados):
            continue
        filtered.append(v)
    return filtered


def load_seen(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_seen(path: Path, seen: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")


def typology_label(tipo_id: str) -> str:
    return {"10": "Motos y scooters", "13": "Turismos y vehículos comerciales", "7": "Camiones"}.get(
        tipo_id, f"Tipo {tipo_id}"
    )


def vehicle_to_dict(vehicle: Vehicle) -> dict[str, str]:
    precio = vehicle.precio_detalle or vehicle.price
    return {
        "vehicle_id": vehicle.vehicle_id,
        "auction_id": vehicle.auction_id,
        "title": vehicle.title,
        "url": vehicle.url,
        "location": vehicle.location,
        "registration": vehicle.registration,
        "kilometers": vehicle.kilometers,
        "price": precio,
        "estado": vehicle.estado,
        "combustible": vehicle.combustible,
        "extras": vehicle.extras,
        "typology": typology_label(vehicle.typology_id),
        "typology_id": vehicle.typology_id,
        "marca": vehicle.marca,
        "modelo": vehicle.modelo,
        "descripcion": vehicle.descripcion,
    }


def truncate_text(text: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def format_telegram_html(cfg: Config, vehicles: list[Vehicle]) -> str:
    lines = [
        f"<b>{escape(cfg.telegram_title)}</b>",
        f"SUBASTAS DISPONIBLES ({len(vehicles)})",
        "",
    ]
    for vehicle in vehicles:
        precio = vehicle.precio_detalle or vehicle.price or "—"
        lines.extend(
            [
                f"<b>{escape(vehicle.title)}</b>",
                (
                    f"{escape(vehicle.location)} | {escape(vehicle.estado)} | "
                    f"{escape(precio)}"
                ),
                (
                    f"{escape(vehicle.kilometers or '—')} | "
                    f"{escape(vehicle.registration or '—')} | "
                    f"{escape(vehicle.combustible or '—')}"
                ),
            ]
        )
        if vehicle.descripcion:
            desc = truncate_text(vehicle.descripcion, cfg.telegram_desc_max)
            lines.append(escape(desc))
        lines.append(f'<a href="{escape(vehicle.url, quote=True)}">Ver ficha</a>')
        lines.append("")
    message = "\n".join(lines).strip()
    if len(message) > 4000:
        message = message[:3997].rstrip() + "..."
    return message


def log(message: str, *, json_mode: bool = False) -> None:
    if json_mode:
        print(message, file=sys.stderr)
    else:
        print(message)


def print_vehicle(vehicle: Vehicle) -> None:
    tipo = typology_label(vehicle.typology_id)
    print("=" * 72)
    print(f"NUEVO: {vehicle.title}")
    print(f"  ID:         {vehicle.vehicle_id}")
    print(f"  Tipo:       {tipo}")
    print(f"  URL:        {vehicle.url}")
    print(f"  Ubicación:  {vehicle.location}")
    print(f"  Estado:     {vehicle.estado}")
    print(f"  Combustible:{vehicle.combustible or '—'}")
    if vehicle.marca or vehicle.modelo:
        print(f"  Marca:      {vehicle.marca or '—'}")
        print(f"  Modelo:     {vehicle.modelo or '—'}")
    print(f"  Matricul.:  {vehicle.registration or '—'}")
    print(f"  Kilómetros: {vehicle.kilometers or '—'}")
    precio = vehicle.precio_detalle or vehicle.price
    print(f"  Precio:     {precio or '—'}")
    if vehicle.extras:
        print(f"  Extras:     {vehicle.extras}")
    if vehicle.descripcion:
        desc = vehicle.descripcion
        if len(desc) > 300:
            desc = desc[:297] + "..."
        print(f"  Comentarios:{desc}")
    print()


def collect_matching(cfg: Config) -> list[Vehicle]:
    all_vehicles: dict[str, Vehicle] = {}

    for tipo in cfg.tipos:
        try:
            listings = fetch_listings(cfg, tipo)
        except urllib.error.URLError as exc:
            print(f"Error al obtener listado tipo {tipo}: {exc}", file=sys.stderr)
            continue
        for vehicle in filter_vehicles(cfg, listings):
            all_vehicles[vehicle.vehicle_id] = vehicle

    return list(all_vehicles.values())


def main() -> int:
    parser = argparse.ArgumentParser(description="Alerta de vehículos en netsubasta.com")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Ruta al archivo de configuración")
    parser.add_argument("--all", action="store_true", help="Mostrar todos los vehículos que cumplen filtros")
    parser.add_argument("--init", action="store_true", help="Marcar vehículos actuales como vistos sin mostrar")
    parser.add_argument("--reset", action="store_true", help="Borrar historial de vehículos vistos")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    if args.reset:
        if cfg.seen_file.exists():
            cfg.seen_file.unlink()
        print("Historial de vehículos vistos borrado.")
        return 0

    print(f"[{now}] Buscando vehículos en {cfg.base_url}...")
    print(f"  Tipos: {', '.join(cfg.tipos)} | Ubicaciones: {len(cfg.ubicaciones)} | Estados: {len(cfg.estados)}")

    matching = collect_matching(cfg)
    seen = load_seen(cfg.seen_file)

    if args.init:
        for v in matching:
            seen[v.vehicle_id] = now
        save_seen(cfg.seen_file, seen)
        print(f"Inicializado: {len(matching)} vehículos marcados como vistos.")
        return 0

    if args.all:
        to_show = matching
        header = f"Encontrados {len(to_show)} vehículos que cumplen los filtros:"
    else:
        to_show = [v for v in matching if v.vehicle_id not in seen]
        header = f"¡{len(to_show)} vehículo(s) nuevo(s)!"

    print(header)

    if not to_show:
        if not args.all:
            print("No hay novedades.")
        return 0

    for i, vehicle in enumerate(to_show):
        if cfg.fetch_details:
            fetch_vehicle_details(cfg, vehicle)
            if i < len(to_show) - 1 and cfg.detail_delay > 0:
                time.sleep(cfg.detail_delay)
        print_vehicle(vehicle)
        if not args.all:
            seen[vehicle.vehicle_id] = now

    if not args.all:
        save_seen(cfg.seen_file, seen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
