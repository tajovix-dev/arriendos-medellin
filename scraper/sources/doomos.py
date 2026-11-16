"""Doomos: listado HTML (Next.js App Router) con los datos en el payload RSC ("initialData").

De dónde salen los datos
- https://www.doomos.com.co/arriendo/apartamentos/{municipio} trae los 20 anuncios publicados más
  recientemente (orden por "published_at" descendente). La paginación y los filtros (precio,
  orden) solo funcionan contra api.doomos.com.co/api/, que robots.txt prohíbe, así que NO se
  usan. Variantes de ruta probadas sin éxito (404 o mismos resultados): /apartaestudios/...,
  /hasta-2000000, /2-habitaciones, ?page=2, ?sort=...
- Con 20 por municipio y corrida (cada 6 h) alcanza para ver lo nuevo del sur: Sabaneta, Envigado,
  Itagüí y La Estrella publican ~5-15 apartamentos por día. Medellín: también 1 petición
  (página 1), aunque allí entran más anuncios por día de los que caben.
- Doomos se alimenta de Wasi: importa una vez al día (published_at ≈ 21:25 UTC). En los anuncios
  nuevos esa fecha coincide (o va 1 día detrás) con la fecha de las fotos subidas a Wasi, así que
  se usa como fecha de publicación. Excepción: las fechas de importación masiva (2026-08-31, la
  carga inicial) y cualquier otra fecha que acumule muchísimos anuncios distintos (ver
  _fechas_masivas) se tratan como no confiables → publicado=None.
- La "company" de la ficha es siempre "Wasi" (el operador), no la inmobiliaria: anunciante=None.
- Coordenadas: muchos anuncios traen el centroide del municipio. Las coordenadas repetidas en 3 o
  más anuncios distintos se descartan (y se recuerdan en el estado).
- Barrio: del título, solo con patrones claros ("sector X", "barrio X", "en X - Municipio"...).
  detalle(url) trae "address_zone" (zona/barrio escrita por la inmobiliaria) y parqueaderos.

Limitaciones: solo 20 anuncios por municipio y corrida (los más recientes), muchos por encima de
2.000.000; la galería del listado y de la ficha está recortada a 6 fotos; sin teléfono público.
"""
import collections
import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scraper.http import GONE, get, rsc_payload, a_int

NOMBRE = "doomos"
FUENTE = "Doomos"
BASE = "https://www.doomos.com.co"

# Sur primero (prioridad del usuario); Medellín solo la primera página
MUNICIPIOS = {
    "Sabaneta": "sabaneta",
    "Envigado": "envigado",
    "Itagüí": "itagui",
    "La Estrella": "la-estrella/departamento-de-antioquia",
    "Medellín": "medellin",
}
MIN, MAX = 300_000, 2_000_000
FECHAS_MASIVAS = {"2026-08-31"}   # carga inicial desde Wasi (fecha de importación, no de publicación)
UMBRAL_MASIVA = 150               # anuncios distintos con la misma fecha → importación masiva

_NO_APTO = re.compile(r"\b(local|bodega|oficina|consultorio|habitaci[oó]n|cuarto|casa|finca|lote)\b", re.I)
_APTO = re.compile(r"\b(apartamento|apto|apartaestudio|aparta\s*estudio|loft|duplex|d[uú]plex|penthouse)\b", re.I)


def _sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower().strip()


def _initial_data(html):
    p = rsc_payload(html)
    i = p.find('"initialData":')
    if i < 0:
        return None
    try:
        d, _ = json.JSONDecoder().raw_decode(p, i + len('"initialData":'))
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


_MINUSC = {"de", "del", "la", "las", "los", "el", "y", "en"}
_GENERICAS = re.compile(r"\b(apartamento|apto|apartaestudio|arriendo|arrienda|renta|rento|alquiler|alquila|"
                        r"venta|hermoso|hermosa|excelente|amoblado|moderno|estrenar|municipio|unidad cerrada|"
                        r"cerca|cod|para|con|piso|zona plana|parte plana|parte alta|vista)\b", re.I)


def _titulo_barrio(s):
    s = re.sub(r"\s+", " ", s).strip(" .,-–:")
    if not s or len(s) < 3 or len(s.split()) > 4 or _GENERICAS.search(s) or re.search(r"\d", s):
        return None
    pal = s.lower().split(" ")
    return " ".join([pal[0].capitalize()] + [p if p in _MINUSC else p.capitalize() for p in pal[1:]])


def _clave(s):
    k = re.sub(r"[^a-z0-9 ]", " ", _sin_tildes(s))
    k = re.sub(r"\b(de|del|no|nro)\b", " ", k)
    return " " + re.sub(r"\s+", " ", k).strip() + " "


_GAZ = None


def _gazetteer():
    """{municipio: [(clave, nombre)]} con los barrios (y alias) de docs/barrios_info.json, más largos primero."""
    global _GAZ
    if _GAZ is None:
        _GAZ = {}
        try:
            ruta = Path(__file__).resolve().parents[2] / "docs" / "barrios_info.json"
            info = json.loads(ruta.read_text(encoding="utf-8"))
            for m in info.get("municipios") or []:
                pares = []
                for b in m.get("barrios") or []:
                    nombre = b.get("nombre") or ""
                    for n in [nombre] + list(b.get("alias") or []):
                        n = re.sub(r"\(.*?\)", "", n)
                        k = _clave(n)
                        if len(k.strip()) >= 4 and k.strip() != _sin_tildes(m.get("municipio")):
                            pares.append((k, nombre))
                _GAZ[m.get("municipio")] = sorted(set(pares), key=lambda p: -len(p[0]))
        except (OSError, ValueError):
            pass
    return _GAZ


def _barrio(titulo, municipio):
    """Barrio desde el título: primero barrios conocidos del municipio; si no, "sector/barrio X"."""
    t = re.sub(r"\s+", " ", titulo or "")
    # "cerca al parque / al CC Mayorca / a la clínica…" son referencias, no el barrio
    t = re.sub(r"(?i)\bcerca(?:no)?\s+(?:a|al|a la|de|del)\s+[^,\-–(]+", " ", t)
    k = _clave(t)
    for clave, nombre in _gazetteer().get(municipio, []):
        if clave in k:
            return nombre
    m = re.search(r"\b(?:sector|barrio|urbanizaci[oó]n|urb\.)\s+([^,\-–(|/.]+)", t, re.I)
    if not m:
        return None
    cand = re.sub(r"\b(cod|c[oó]digo)\b.*$", "", m.group(1), flags=re.I)
    b = _titulo_barrio(cand)
    if b and _sin_tildes(b) in ("la estrella", "estrella", "medellin", "envigado", "sabaneta", "itagui"):
        return None
    return b


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fechas_masivas(estado, items):
    """Cuenta anuncios distintos por fecha de publicación (podado a 120 fechas)."""
    cont = estado.setdefault("fechas", {})
    vistos = set(estado.get("ids_fecha") or [])
    for x in items:
        if x.get("id") in vistos:
            continue
        vistos.add(x.get("id"))
        f = (x.get("published_at") or "")[:10]
        if f:
            cont[f] = cont.get(f, 0) + 1
    for f in sorted(cont)[:-120]:
        del cont[f]
    estado["ids_fecha"] = sorted(vistos)[-600:]
    return FECHAS_MASIVAS | {f for f, n in cont.items() if n >= UMBRAL_MASIVA}


def _chico(v):
    n = a_int(v)
    return n if n is not None and 0 <= n <= 10 else None


def _fecha_fotos(urls):
    """Fecha más antigua de las fotos en Wasi, sacada del nombre del archivo:
    'gr<id><AAAAMMDDhhmmss>.jpg' o 'gr_<texto>_<unix>-<n>_<n>.jpeg'."""
    fechas = []
    for u in urls:
        nombre = u.rsplit("/", 1)[-1]
        m = re.search(r"(20[12]\d)(0[1-9]|1[0-2])([0-3]\d)([01]\d|2[0-3])[0-5]\d[0-5]\d\.\w+$", nombre)
        if m:
            try:
                fechas.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except ValueError:
                pass
            continue
        m = re.search(r"_(1[5-9]\d{8})-\d+_\d+\.\w+$", nombre)
        if m:
            fechas.append((datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc) - timedelta(hours=5)).date())
    return min(fechas) if fechas else None


def _publicado(f, masivas, fotos):
    """published_at de Doomos (fecha en que lo importó de Wasi), salvo importación masiva o
    anuncio reimportado: si las fotos se subieron a Wasi más de 30 días antes, el anuncio es
    viejo aunque Doomos lo muestre como nuevo → None (no se sabe la fecha real)."""
    if not f or f in masivas:
        return None
    ff = _fecha_fotos(fotos)
    if ff and (date.fromisoformat(f) - ff).days > 30:
        return None
    return f


def _normalizar(x, municipio, masivas, coords_malas):
    lat, lon = _num(x.get("latitude")), _num(x.get("longitude"))
    if (x.get("latitude"), x.get("longitude")) in coords_malas or not (
            lat and 5.9 < lat < 6.5 and lon and -75.9 < lon < -75.3):
        lat = lon = None
    imgs = sorted((i for i in x.get("images") or [] if isinstance(i, dict) and i.get("url")),
                  key=lambda i: (not i.get("is_cover"), i.get("sort_order") or 0))
    fotos = list(dict.fromkeys(i["url"] for i in imgs if (i.get("type") or "photo") == "photo"))
    f = (x.get("published_at") or "")[:10] or None
    titulo = re.sub(r"\s+", " ", x.get("title") or "").strip() or None
    area = _num(x.get("area_m2"))
    return {
        "id": f"dm-{x.get('id')}",
        "fuente": FUENTE,
        "url": f"{BASE}/propiedad/{x.get('slug')}",
        "titulo": titulo,
        "precio": int(_num(x.get("price")) or 0) or None,
        "admin": None,
        "area": int(area) if area else None,
        "hab": _chico(x.get("bedrooms")),
        "banos": _chico(x.get("bathrooms")),
        "parq": None,
        "estrato": None,
        "municipio": municipio,
        "barrio": _barrio(titulo, municipio),
        "lat": lat,
        "lon": lon,
        "img": fotos[0] if fotos else None,
        "fotos": fotos,
        "anunciante": None,
        "publicado": _publicado(f, masivas, fotos),
        "tel": None,
        "wa": None,
    }


def _es_apto(x):
    if ((x.get("property_type") or {}).get("name") or "Apartamento") != "Apartamento":
        return False
    if ((x.get("listing_type") or {}).get("name") or "Arriendo") != "Arriendo":
        return False
    t = x.get("title") or ""
    return not (_NO_APTO.search(t) and not _APTO.search(t))


def scrape(estado, conocidos, log):
    inicial = not estado.get("corridas")
    paginas = {}
    for municipio, slug in MUNICIPIOS.items():
        html = get(f"{BASE}/arriendo/apartamentos/{slug}", si_404=GONE)
        if not html or html == GONE:
            log(f"  doomos/{slug}: sin respuesta")
            continue
        d = _initial_data(html)
        if d is None:
            log(f"  doomos/{slug}: la página no trae initialData")
            continue
        paginas[municipio] = (slug, d)

    todos = [x for _, d in paginas.values() for x in d.get("data") or [] if isinstance(x, dict)]
    masivas = _fechas_masivas(estado, todos)
    # Coordenadas genéricas (centroide del municipio): repetidas en 3+ anuncios distintos
    cnt = collections.Counter((x.get("latitude"), x.get("longitude")) for x in todos if x.get("latitude"))
    malas = {tuple(c) for c in estado.get("coords_genericas") or []} | {c for c, n in cnt.items() if n >= 3}
    estado["coords_genericas"] = [list(c) for c in sorted(malas)][-50:]

    for municipio, (slug, d) in paginas.items():
        leidos = nuevos = 0
        for x in d.get("data") or []:
            if not isinstance(x, dict) or not x.get("id") or not x.get("slug"):
                continue
            if _sin_tildes((x.get("city") or {}).get("name")) != _sin_tildes(municipio) or not _es_apto(x):
                continue
            l = _normalizar(x, municipio, masivas, malas)
            if not l["precio"] or not MIN <= l["precio"] <= MAX:
                continue
            if inicial:
                l["_inicial"] = True
            leidos += 1
            if l["id"] not in conocidos:
                nuevos += 1
            yield l
        log(f"  doomos/{slug.split('/')[0]}: {len(d.get('data') or [])} leídos, {leidos} en rango, {nuevos} nuevos "
            f"(total en el sitio {d.get('total')})")
    estado["corridas"] = estado.get("corridas", 0) + 1


def detalle(url):
    """Zona/barrio ("address_zone"), parqueaderos y área desde la ficha."""
    html = get(url, si_404=GONE)
    if html == GONE:
        return {"disponible": False}
    if html is None:
        raise IOError("sin respuesta")
    p = rsc_payload(html)
    i = p.find('"parking_spots"')
    if i < 0:
        raise IOError("ficha sin datos")
    j = p.rfind('{"id":', 0, i)
    try:
        o, _ = json.JSONDecoder().raw_decode(p, j)
    except (json.JSONDecodeError, ValueError):
        raise IOError("ficha sin datos")
    out = {}
    if o.get("status") and o.get("status") != "active" or o.get("is_published") is False:
        out["disponible"] = False
    zona = (o.get("address_zone") or "").strip()
    if zona and not re.search(r"\d", zona):
        city = (o.get("city") or {}).get("name") or ""
        b = _titulo_barrio(zona)
        if b and _sin_tildes(b) != _sin_tildes(city):
            out["barrio"] = b
    if a_int(o.get("parking_spots")) is not None:
        out["parq"] = a_int(o.get("parking_spots"))
    area = _num(o.get("area_m2"))
    if area:
        out["area"] = int(area)
    fotos = [im.get("url") for im in sorted(o.get("images") or [], key=lambda im: im.get("sort_order") or 0)
             if isinstance(im, dict) and im.get("url")]
    if fotos:
        out["fotos"] = list(dict.fromkeys(fotos))
    return out
