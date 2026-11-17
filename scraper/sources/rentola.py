"""Rentola: agregador (reúne Properati, Metrocuadrado, Wasi…). JSON-LD con
RealEstateListing + Offer + GeoCoordinates, 21 por página, ?page=N.

No permite ordenar por fecha ni filtrar por precio en la URL, así que se hace
un barrido rotativo: cada corrida avanza RENTOLA_PAGINAS páginas por municipio.
"""
import re
from datetime import datetime, timedelta, timezone

import config
from scraper.http import GONE, get, json_ld, a_int

NOMBRE = "rentola"
BASE = "https://rentola.co.com"


def _items(html):
    for j in json_ld(html):
        bloques = j if isinstance(j, list) else [j]
        for b in bloques:
            if not isinstance(b, dict):
                continue
            me = b.get("mainEntity")
            if isinstance(me, dict) and me.get("@type") == "ItemList":
                return [e.get("item") or {} for e in me.get("itemListElement") or []]
    return []


def _total_paginas(html):
    m = re.search(r"([\d.]+) apartamentos disponibles", html)
    if not m:
        return 0
    total = int(m.group(1).replace(".", ""))
    return (total + 20) // 21


def _hora_colombia(iso):
    """Hora (HH:MM, Colombia) en que Rentola creó su copia del anuncio."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=-5))).strftime("%H:%M")
    except (AttributeError, ValueError):
        return None


def _fecha_colombia(iso):
    """'2026-11-04T16:42:39Z' (UTC) → '2026-11-04' en hora de Colombia (UTC-5)."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(timezone(timedelta(hours=-5))).date().isoformat()
    except ValueError:
        return iso[:10]


_NO_BARRIO = re.compile(
    r"^(\d|calle|carrera|cra|cr\b|kr|cl\b|cll|diagonal|dg|transversal|tv|avenida|av\b|circular|ant$|antioquia|colombia|valle de aburr|rap del)",
    re.I)


def _barrio(direccion, municipio):
    """'Calle 56 Sur, Las Lomitas, 055457 Sabaneta, ANT, Colombia' → 'Las Lomitas'."""
    for parte in (p.strip() for p in direccion.split(",")):
        if not parte or _NO_BARRIO.search(parte) or parte.lower() == municipio.lower():
            continue
        return parte
    return None


def _disponible(availability):
    """schema.org: InStock = disponible; OutOfStock/SoldOut/Discontinued = no disponible; vacío = sin dato."""
    if not availability:
        return None
    return availability.rstrip("/").split("/")[-1] in ("InStock", "InStoreOnly", "OnlineOnly", "LimitedAvailability", "PreOrder")


def _origen(html):
    m = re.search(r'\\?"sourceUrl\\?"\s*:\s*\\?"(https?://[^"\\]+)', html)
    return m.group(1) if m else None


def detalle(url):
    """Visita la página del anuncio: galería completa, fecha y disponibilidad."""
    html = get(url, si_404=GONE)
    if html == GONE:
        return {"disponible": False}  # 404/410: el anuncio ya no existe
    if html is None:
        raise IOError("sin respuesta")  # fallo temporal: se reintenta en otra corrida
    for j in json_ld(html):
        if isinstance(j, dict) and j.get("@type") == "RealEstateListing":
            oferta = j.get("offers") or {}
            fotos = j.get("image") or []
            if isinstance(fotos, str):
                fotos = [fotos]
            disp = _disponible(oferta.get("availability"))
            if re.search(r'\\?"status\\?"\s*:\s*\\?"(inactive|rented|archived|deleted)', html):
                disp = False
            apto = oferta.get("itemOffered") or {}
            banos = a_int(apto.get("numberOfBathroomsTotal"))
            if not banos:
                m = re.search(r'\\?"bathrooms\\?"\s*:\s*(\d+)', html)
                banos = int(m.group(1)) if m else None
            return {
                "fotos": [f for f in fotos if isinstance(f, str) and f.startswith("http")],
                "publicado": _fecha_colombia(j.get("datePosted") or oferta.get("validFrom")),
                "pub_h": _hora_colombia(j.get("datePosted") or oferta.get("validFrom")),
                "disponible": disp,
                "banos": banos or None,
                # Rentola copia anuncios de otros portales y cobra por contactar; la página original
                # (Metrocuadrado, Ciencuadras, Properati, inmobiliarias...) es gratis y trae el contacto
                "orig": _origen(html),
            }
    return {}


def _normalizar(item, municipio):
    oferta = item.get("offers") or {}
    apto = oferta.get("itemOffered") or {}
    geo = apto.get("geo") or {}
    dire = apto.get("address") or {}
    url = item.get("url") or oferta.get("url") or ""
    m = re.search(r"-(p[0-9a-f]+)$", url)
    barrio = _barrio(dire.get("streetAddress") or "", municipio)
    area = a_int((apto.get("floorSize") or {}).get("value"))
    # Algunos anuncios de Rentola traen el área multiplicada por 10 (600 en vez de 60 m²)
    if area and area >= 250 and area % 10 == 0 and 20 <= area // 10 <= 200:
        area //= 10
    img = item.get("image")
    return {
        "fotos": [img] if img else [],
        "disponible": _disponible(oferta.get("availability")),
        "id": f"rt-{m.group(1) if m else url}",
        "fuente": "Rentola",
        "url": url,
        "titulo": item.get("name"),
        "precio": a_int(oferta.get("price")),
        "admin": None,
        "area": area,
        "hab": a_int((apto.get("numberOfBedrooms") or {}).get("value")),
        "banos": None,
        "parq": None,
        "estrato": None,
        "municipio": municipio,
        "barrio": barrio,
        "lat": geo.get("latitude"),
        "lon": geo.get("longitude"),
        "img": item.get("image"),
        "anunciante": None,
        "publicado": _fecha_colombia(oferta.get("validFrom")),
        "pub_h": _hora_colombia(oferta.get("validFrom")),
    }


def scrape(estado, conocidos, log):
    intentos = fallos = 0
    for municipio, slugs in config.MUNICIPIOS.items():
        slug = slugs.get(NOMBRE)
        if not slug:
            continue
        est = estado.setdefault(slug, {"cursor": 1})
        cursor = est.get("cursor", 1)
        total = est.get("paginas", 0)
        nuevos = 0
        for _ in range(config.RENTOLA_PAGINAS):
            if total and cursor > total:
                cursor = 1
                est["ciclos"] = est.get("ciclos", 0) + 1
                break  # ciclo completo: el siguiente arranca en la próxima corrida
            url = f"{BASE}/arriendo/apartamento/{slug}" + (f"?page={cursor}" if cursor > 1 else "")
            html = get(url, si_404=GONE)
            intentos += 1
            if html is None:
                fallos += 1
                break  # fallo temporal: la próxima corrida sigue desde esta misma página
            cursor += 1
            if html == GONE:
                cursor = 1
                est["ciclos"] = est.get("ciclos", 0) + 1
                break
            total = _total_paginas(html) or total
            items = _items(html)
            if not items:
                cursor = 1
                est["ciclos"] = est.get("ciclos", 0) + 1
                break
            for it in items:
                l = _normalizar(it, municipio)
                if not est.get("ciclos"):
                    l["_inicial"] = True
                if l["id"] not in conocidos:
                    nuevos += 1
                yield l
        est["cursor"] = cursor
        est["paginas"] = total
        log(f"  rentola/{slug}: {nuevos} nuevos, cursor {cursor}/{total}")
    if intentos and fallos == intentos:
        # Antes quedaba como "0 leídos, sin error": que se vea en la tabla de fuentes
        raise RuntimeError("Rentola no respondió en ningún municipio (fallo temporal del sitio)")
