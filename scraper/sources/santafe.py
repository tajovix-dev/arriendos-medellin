"""Arrendamientos Santa Fe (arrendamientossantafe.com).

De dónde salen los datos: la propia página pide JSON a /apisf/properties (listado, 12 por página,
con canon, barrio, ciudad, área, alcobas, baños, coordenadas y foto principal) y a
/apisf/property/{codigo} (ficha: galería, estrato, parqueaderos). robots.txt PERMITE explícitamente
esas dos rutas (y prohíbe el resto de /apisf/ y /api/, que no se usan).

Cada corrida recorre todo el listado de arriendos de Apartamento y "Aparta Estudio" con
precio_max=2.000.000 (~22 + 1 peticiones) y filtra los 5 municipios. `detalle(url)` añade galería,
estrato y parqueaderos.
Limitaciones: el sitio no publica fecha de publicación ni valor de administración, así que
`publicado` y `admin` van en None; tampoco muestra teléfono por anuncio.
"""
import json
import re
import unicodedata

import config
from scraper.http import GONE, get, a_int

NOMBRE = "santafe"
FUENTE = "Arrendamientos Santa Fe"
BASE = "https://arrendamientossantafe.com"
TIPOS = ["Apartamento", "Aparta Estudio"]   # "Apartaestudio" devuelve 0; el sitio usa "Aparta Estudio"
MAX_PAGINAS = 60                              # tope de seguridad por tipo

_MUNICIPIOS = {"MEDELLIN": "Medellín", "SABANETA": "Sabaneta", "ENVIGADO": "Envigado",
               "ITAGUI": "Itagüí", "LA ESTRELLA": "La Estrella"}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


def _municipio(ciudad):
    return _MUNICIPIOS.get(_norm(ciudad))


def _barrio(s):
    s = re.sub(r"\s+", " ", (s or "").strip())
    if not s:
        return None
    return s.title() if (s.isupper() or s.islower()) else s


def _coord(v, lo, hi):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if lo < v < hi else None


def _normalizar(r, municipio):
    codigo = str(r.get("codigo") or r.get("id"))
    img = r.get("image")
    return {
        "id": f"sf-{codigo}",
        "fuente": FUENTE,
        "url": BASE + (r.get("url") or f"/propiedad/{codigo}"),
        "titulo": r.get("title"),
        "precio": a_int(r.get("price_raw")),
        "admin": None,
        "area": a_int(r.get("size")),
        "hab": a_int(r.get("bedrooms")),
        "banos": a_int(r.get("bathrooms")),
        "parq": None,
        "estrato": None,
        "municipio": municipio,
        "barrio": _barrio(r.get("location")),
        "lat": _coord(r.get("lat"), 6.0, 6.45),
        "lon": _coord(r.get("lng"), -75.8, -75.4),
        "img": img,
        "fotos": [img] if img else [],
        "anunciante": FUENTE,
        "publicado": None,
        "tel": None,
        "wa": None,
    }


def detalle(url):
    """Galería completa, estrato y parqueaderos desde /apisf/property/{codigo}."""
    m = re.search(r"/propiedad/([A-Za-z0-9-]+)", url)
    if not m:
        return {}
    txt = get(f"{BASE}/apisf/property/{m.group(1)}", si_404=GONE)
    if txt == GONE:
        return {"disponible": False}
    if txt is None:
        raise IOError("sin respuesta")
    d = json.loads(txt)
    fotos = [u for u in ([d.get("imagen_principal")] + list(d.get("galeria") or [])) if u]
    out = {"fotos": list(dict.fromkeys(fotos))}
    e = a_int(d.get("estrato"))
    if e and 1 <= e <= 6:
        out["estrato"] = e
    if d.get("parqueaderos") is not None or d.get("garajes") is not None:
        out["parq"] = (a_int(d.get("parqueaderos")) or 0) + (a_int(d.get("garajes")) or 0)
    if (d.get("oferta") or "Arriendo") != "Arriendo":
        out["disponible"] = False
    return out


def scrape(estado, conocidos, log):
    inicial = not estado.get("inicializado")
    vistos = set()
    por_mun = {}
    for tipo in TIPOS:
        pagina = 1
        while pagina <= MAX_PAGINAS:
            url = (f"{BASE}/apisf/properties?oferta=Arriendo&tipo={tipo.replace(' ', '%20')}"
                   f"&precio_max={config.PRECIO_MAX_GUARDAR}&page={pagina}")
            txt = get(url, si_404=GONE)
            if txt is None or txt == GONE:
                log(f"  santafe/{tipo}: falló la página {pagina}")
                break
            d = json.loads(txt)
            for r in d.get("results") or []:
                municipio = _municipio(r.get("ciudad"))
                if not municipio or (r.get("oferta") or "Arriendo") != "Arriendo":
                    continue
                l = _normalizar(r, municipio)
                if l["id"] in vistos or not l["precio"]:
                    continue
                if not (config.PRECIO_MIN_GUARDAR <= l["precio"] <= config.PRECIO_MAX_GUARDAR):
                    continue
                vistos.add(l["id"])
                c = por_mun.setdefault(municipio, [0, 0])
                c[0] += 1
                if l["id"] not in conocidos:
                    c[1] += 1
                if inicial:
                    l["_inicial"] = True
                yield l
            if not d.get("has_more"):
                break
            pagina += 1
    for m, (n, nuevos) in sorted(por_mun.items()):
        log(f"  santafe/{m}: {n} leídos, {nuevos} nuevos")
    estado["inicializado"] = True
