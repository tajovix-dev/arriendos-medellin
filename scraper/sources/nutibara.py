"""Arrendamientos Nutibara (anutibara.com / arrendamientosnutibara.com).

De dónde salen los datos: la página (Nuxt) pide a su API pública
https://api.arrendamientosnutibara.com/promotion/explore/recent?limit=N para la sección
"recientes". Con un límite alto devuelve TODAS las promociones de arriendo (status "PROMOCION";
las ventas son "VENTAS" / "VENTAS - MLS"), ~530 registros en ~0,7 MB, cada uno con canon,
createdAt, tipo, barrio, sector, ciudad, coordenadas, estrato, fotos y "facilities" (Alcobas id 82,
Baños 105, Área 112). Se prefirió a /promotion/all (7 MB, sin fechas ni facilities, que la página
usa para su sitemap). api.arrendamientosnutibara.com no tiene robots.txt; el de anutibara.com solo
prohíbe /ingresar y /perfil.

Una sola petición por corrida (HORAS_ENTRE_DESCARGAS permite espaciarla; si una corrida no
descarga, deja estado["_omitida"] = True y no emite nada).
Fotos: https://assets.arrendamientosnutibara.com/spaces/images/{property.id}/720{resource}
(la URL sin prefijo de tamaño da 403; 350 es la miniatura). Ficha pública:
https://anutibara.com/{tipo}-en-arriendo/{barrio}-{ciudad}/{id}, igual que la arma el sitio.
publicado = createdAt de la promoción (fecha en que se publicó el inmueble para arriendo),
pasado a hora Colombia; se descarta cuando el mismo instante se repite en varios registros
(p. ej. 2018-04-17 15:40:10, carga masiva de la migración).
El listado solo trae la foto principal: `detalle(url)` pide /promotion/{id} y devuelve la galería
completa (y disponible=False si ya no existe o dejó de ser arriendo).
Limitaciones: no trae valor de administración ni parqueaderos; sin teléfono por anuncio;
~16 % de los anuncios no tiene fotos.
"""
import json
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timedelta

import config
from scraper.http import GONE, get, a_int

NOMBRE = "nutibara"
FUENTE = "Arrendamientos Nutibara"
API = "https://api.arrendamientosnutibara.com/promotion"
SITIO = "https://anutibara.com"
FOTOS = "https://assets.arrendamientosnutibara.com/spaces/images"
LIMITE = 3000
HORAS_ENTRE_DESCARGAS = 0     # 0 = descargar en cada corrida (la respuesta pesa ~0,7 MB)

_MUNICIPIOS = {"MEDELLIN": "Medellín", "SABANETA": "Sabaneta", "ENVIGADO": "Envigado",
               "ITAGUI": "Itagüí", "LA ESTRELLA": "La Estrella"}
_TIPOS = {"APARTAMENTO": "Apartamento", "APARTAESTUDIO": "Apartaestudio"}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


def _titulo(s):
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s.title() if s else None


def _coord(v, lo, hi):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if lo < v < hi else None


def _facility(prop, fid):
    for f in prop.get("facilities") or []:
        if (f.get("facility") or {}).get("id") == fid:
            return a_int(f.get("value"))
    return None


def _publicado(created, repetidos):
    if not created or repetidos.get(created, 0) >= 3:
        return None
    try:
        d = datetime.strptime(created[:19], "%Y-%m-%dT%H:%M:%S") - timedelta(hours=5)
    except ValueError:
        return None
    return d.date().isoformat()


def _url(x):
    p = x.get("property") or {}
    tipo = (p.get("type") or "apartamento").lower()
    barrio = (p.get("neighborhood") or "").lower().split(" ")
    ciudad = (p.get("city") or "").lower().split(" ")
    return f"{SITIO}/{tipo}-en-arriendo/{'-'.join(barrio)}-{'-'.join(ciudad)}/{x.get('id')}"


def _normalizar(x, municipio, repetidos):
    p = x.get("property") or {}
    imgs = p.get("images") or []
    imgs = sorted(imgs, key=lambda i: not i.get("main"))   # la principal primero
    fotos = [f"{FOTOS}/{p.get('id')}/720{i['resource']}" for i in imgs if i.get("resource")]
    barrio = _titulo(p.get("neighborhood"))
    tipo = _TIPOS.get(p.get("type"), "Apartamento")
    estrato = a_int(p.get("stratum"))
    return {
        "id": f"nu-{x.get('id')}",
        "fuente": FUENTE,
        "url": _url(x),
        "titulo": f"{tipo} en {barrio or municipio}",
        "precio": a_int(x.get("rentValue")),
        "admin": None,
        "area": _facility(p, 112) or None,
        "hab": _facility(p, 82),
        "banos": _facility(p, 105),
        "parq": None,
        "estrato": estrato if estrato and 1 <= estrato <= 6 else None,
        "municipio": municipio,
        "barrio": barrio,
        "lat": _coord(p.get("latitude"), 6.0, 6.45),
        "lon": _coord(p.get("longitude"), -75.8, -75.4),
        "img": fotos[0] if fotos else None,
        "fotos": fotos,
        "anunciante": FUENTE,
        "publicado": _publicado(x.get("createdAt"), repetidos),
        "tel": None,
        "wa": None,
    }


def detalle(url):
    """Galería completa (el listado solo trae la foto principal) desde /promotion/{id}."""
    m = re.search(r"/(\d+)/?$", url)
    if not m:
        return {}
    txt = get(f"{API}/{m.group(1)}", si_404=GONE)
    if txt == GONE:
        return {"disponible": False}
    if txt is None:
        raise IOError("sin respuesta")
    x = json.loads(txt).get("data") or {}
    if x.get("status") and x["status"] != "PROMOCION":
        return {"disponible": False}
    p = x.get("property") or {}
    imgs = sorted(p.get("images") or [], key=lambda i: not i.get("main"))
    out = {"fotos": [f"{FOTOS}/{p.get('id')}/720{i['resource']}" for i in imgs if i.get("resource")]}
    for clave, fid in (("hab", 82), ("banos", 105), ("area", 112)):
        v = _facility(p, fid)
        if v:
            out[clave] = v
    return out


def scrape(estado, conocidos, log):
    ahora = time.time()
    ultima = estado.get("ultima_descarga", 0)
    if HORAS_ENTRE_DESCARGAS and ahora - ultima < HORAS_ENTRE_DESCARGAS * 3600:
        estado["_omitida"] = True
        log(f"  nutibara: omitida (última descarga hace {(ahora - ultima) / 3600:.1f} h)")
        return
    estado.pop("_omitida", None)
    inicial = not estado.get("inicializado")
    txt = get(f"{API}/explore/recent?limit={LIMITE}", si_404=GONE)
    if txt is None or txt == GONE:
        log("  nutibara: falló la descarga")
        return
    datos = json.loads(txt).get("data") or []
    if len(datos) >= LIMITE:
        log(f"  nutibara: OJO, la respuesta llenó el límite ({LIMITE}); súbelo")
    repetidos = Counter(x.get("createdAt") for x in datos)
    por_mun = {}
    for x in datos:
        p = x.get("property") or {}
        if x.get("status") != "PROMOCION" or p.get("type") not in _TIPOS:
            continue
        municipio = _MUNICIPIOS.get(_norm(p.get("city")))
        precio = a_int(x.get("rentValue"))
        if not municipio or not precio:
            continue
        if not (config.PRECIO_MIN_GUARDAR <= precio <= config.PRECIO_MAX_GUARDAR):
            continue
        l = _normalizar(x, municipio, repetidos)
        c = por_mun.setdefault(municipio, [0, 0])
        c[0] += 1
        if l["id"] not in conocidos:
            c[1] += 1
        if inicial:
            l["_inicial"] = True
        yield l
    for m, (n, nuevos) in sorted(por_mun.items()):
        log(f"  nutibara/{m}: {n} leídos, {nuevos} nuevos")
    estado["ultima_descarga"] = int(ahora)
    estado["inicializado"] = True
