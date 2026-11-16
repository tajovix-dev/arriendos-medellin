"""Escala Inmobiliaria (escalainmobiliaria.com.co).

De dónde salen los datos: la propia página (SPA React) pide JSON a /api/v2/propiedades
(backend Wasi). Con tipOper=arriendo&tipoInm=2 (apartamento) y 14 (apartaestudio), limite=50,
cada registro ya trae canon, ciudad, barrio, dirección, área, alcobas, baños, parqueaderos,
estrato, coordenadas y la galería completa. robots.txt permite todo.

La API no filtra por precio ni ordena por canon (los parámetros campo/order no sirven para eso),
y el filtro de ciudad da totales incoherentes, así que se pagina todo (~21 peticiones; el orden
por defecto es id descendente = más nuevos primero) y se filtra aquí municipio y precio.
Limitaciones: no hay fecha de publicación (publicado=None); "administracion" siempre llega en 0,
así que admin=None. El teléfono del asesor solo sale en /api/v2/propiedad/{id}; no se usa.
"""
import html as _html
import json
import re
import unicodedata

import config
from scraper.http import GONE, get, a_int

NOMBRE = "escala"
FUENTE = "Escala Inmobiliaria"
BASE = "https://escalainmobiliaria.com.co"
TIPOS = {2: "Apartamento", 14: "Apartaestudio"}
LIMITE = 50
MAX_PAGINAS = 40          # tope de seguridad por tipo
MAX_FOTOS = 25

_MUNICIPIOS = {"MEDELLIN": "Medellín", "SABANETA": "Sabaneta", "ENVIGADO": "Envigado",
               "ITAGUI": "Itagüí", "LA ESTRELLA": "La Estrella",
               # corregimiento de Medellín que el sitio lista como "ciudad"
               "SAN ANTONIO DE PRADO": "Medellín"}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


def _barrio(s):
    s = re.sub(r"\s+", " ", _html.unescape(s or "").strip())
    if not s:
        return None
    return s.title() if (s.isupper() or s.islower() or s[1:].islower()) else s


def _coord(v, lo, hi):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if lo < v < hi else None


def _normalizar(x, municipio):
    pid = str(x.get("id") or x.get("codigo"))
    coords = x.get("coordenadas") or {}
    fotos = [u for u in (x.get("imagenes") or []) if u][:MAX_FOTOS]
    img = x.get("imagen") or (fotos[0] if fotos else None)
    barrio = _barrio(x.get("barrio") or x.get("zona"))
    if _norm(x.get("ciudad")) == "SAN ANTONIO DE PRADO" and not barrio:
        barrio = "San Antonio de Prado"
    estrato = a_int(x.get("estrato"))
    titulo = re.sub(r"\s+", " ", _html.unescape(x.get("_titulo") or x.get("titulo") or "")).strip()
    return {
        "id": f"es-{pid}",
        "fuente": FUENTE,
        "url": f"{BASE}/propiedad/{pid}",
        "titulo": titulo or f"{x.get('tipo') or 'Apartamento'} en {barrio or municipio}",
        "precio": a_int(x.get("precio")),
        "admin": a_int(x.get("administracion")) or None,
        "area": a_int(x.get("area")) or None,
        "hab": a_int(x.get("habitaciones")),
        "banos": a_int(x.get("banos")),
        "parq": a_int(x.get("parqueadero")),
        "estrato": estrato if estrato and 1 <= estrato <= 6 else None,
        "municipio": municipio,
        "barrio": barrio,
        "lat": _coord(coords.get("lat"), 6.0, 6.45),
        "lon": _coord(coords.get("lng"), -75.8, -75.4),
        "img": img,
        "fotos": fotos or ([img] if img else []),
        "anunciante": FUENTE,
        "publicado": None,
        "tel": None,
        "wa": None,
    }


def scrape(estado, conocidos, log):
    inicial = not estado.get("inicializado")
    vistos = set()
    por_mun = {}
    for tipo_inm, tipo in TIPOS.items():
        for pagina in range(1, MAX_PAGINAS + 1):
            url = (f"{BASE}/api/v2/propiedades?tipOper=arriendo&tipoInm={tipo_inm}"
                   f"&limite={LIMITE}&pagina={pagina}")
            txt = get(url, si_404=GONE)
            if txt is None or txt == GONE:
                log(f"  escala/{tipo}: falló la página {pagina}")
                break
            props = json.loads(txt).get("propiedades") or []
            if not props:
                break
            repetidos = 0
            for x in props:
                pid = str(x.get("id") or x.get("codigo"))
                if f"es-{pid}" in vistos:
                    repetidos += 1
                    continue
                vistos.add(f"es-{pid}")
                if (x.get("operacion") or "Arriendo") != "Arriendo":
                    continue
                if x.get("tipo") and x["tipo"] not in ("Apartamento", "Apartaestudio"):
                    continue
                municipio = _MUNICIPIOS.get(_norm(x.get("ciudad")))
                precio = a_int(x.get("precio"))
                if not municipio or not precio:
                    continue
                if not (config.PRECIO_MIN_GUARDAR <= precio <= config.PRECIO_MAX_GUARDAR):
                    continue
                l = _normalizar(x, municipio)
                c = por_mun.setdefault(municipio, [0, 0])
                c[0] += 1
                if l["id"] not in conocidos:
                    c[1] += 1
                if inicial:
                    l["_inicial"] = True
                yield l
            if repetidos == len(props) or len(props) < LIMITE:
                break  # última página (o la API repite la misma)
    for m, (n, nuevos) in sorted(por_mun.items()):
        log(f"  escala/{m}: {n} leídos, {nuevos} nuevos")
    estado["inicializado"] = True
