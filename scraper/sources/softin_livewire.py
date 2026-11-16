"""Inmobiliarias con la plantilla Laravel/Livewire de Softin (una sola fuente para varios sitios).

Sitios: Inmobiliaria La Estrella, Arrendamientos Villacruz, Arrendamientos El Castillo y Livin
Inmobiliaria. Su robots.txt solo prohíbe /img/, /login, /@/documentos/ y /facturas/ (no se piden).

De dónde salen los datos
- Listado: {base}/resultados?gestion=Arriendo&tipo={Apartamentos|Apartaestudios}
  &rango-precio=300000-2000000&s=municipio-en-{slug}. El filtro de precio SÍ funciona por URL con el
  formato "min-max" (probado; con otros formatos se ignora). 36 tarjetas por página: enlace
  /detalle-propiedad/{slug}-{código}, "Apartamento en Arriendo - BARRIO", precio, área, alcobas,
  baños, parqueaderos y foto.
- Si hay pocos resultados la página rellena con "También te puede interesar" (otros municipios /
  precios): solo se toman las N primeras tarjetas, con N = hijos "results-*" del snapshot del
  componente properties-results-livewire (los de relleno son "relateds-*").
- Más de 36 resultados (solo pasa en Medellín): POST {base}/livewire/update con el evento normal
  "load-more" del propio componente (sin tocar el snapshot), hasta 3 veces, pausa ≥1,5 s. Si falla,
  se queda con la primera página.
- detalle(url): `let property= {...}` de la ficha → galería completa, lat/lon, estrato, baños,
  parqueaderos. created_at viene vacío ("0000-00-00"), así que publicado=None. Una ficha que ya no
  existe redirige (302) a la portada → {"disponible": False}.
- No hay orden por fecha (solo precio/área/alcobas), pero con el filtro de precio el inventario en
  rango es pequeño (~decenas por sitio) y se lee completo en cada corrida (~40-50 peticiones).
"""
import html as htmllib
import json
import re
import time
from urllib.parse import urlparse

import requests

import config
from scraper import http
from scraper.http import GONE, a_int, get

NOMBRE = "softin_livewire"
SITIOS = [
    # (corto, base, nombre visible)
    ("estrella", "https://www.inmobiliarialaestrella.com", "Inmobiliaria La Estrella"),
    ("villacruz", "https://www.arrendamientosvillacruz.com.co", "Arrendamientos Villacruz"),
    ("castillo", "https://www.arrendamientoselcastillo.com.co", "Arrendamientos El Castillo"),
    ("livin", "https://www.livinmobiliaria.com", "Livin Inmobiliaria"),
]
# El sur primero; Medellín al final
MUNICIPIOS = [("La Estrella", "la-estrella"), ("Sabaneta", "sabaneta"), ("Envigado", "envigado"),
              ("Itagüí", "itagui"), ("Medellín", "medellin")]
TIPOS = ["Apartamentos", "Apartaestudios"]
POR_PAGINA = 36
MAX_LOAD_MORE = 3


def _pausa(url):
    dominio = urlparse(url).netloc
    espera = config.PAUSA_SEGUNDOS - (time.time() - http._ultimo.get(dominio, 0))
    if espera > 0:
        time.sleep(espera)
    http._ultimo[dominio] = time.time()


def _snapshot(texto):
    for crudo in re.findall(r'wire:snapshot="([^"]*)"', texto):
        s = htmllib.unescape(crudo)
        if '"properties-results-livewire"' in s:
            return s
    return None


def _n_resultados(snap):
    try:
        hijos = json.loads(snap)["memo"]["children"]
    except (ValueError, KeyError, TypeError):
        return None, None
    if not isinstance(hijos, dict):
        return 0, 0
    return (sum(1 for k in hijos if k.startswith("results-")),
            sum(1 for k in hijos if k.startswith("relateds-")))


def _tarjetas(texto, base, n):
    """Primeras n tarjetas (en orden del DOM) antes del bloque "te puede interesar"."""
    corte = texto.find("te puede interesar")
    if corte >= 0:
        texto = texto[:corte]
    pos = []
    vistos = set()
    for m in re.finditer(r'href="(' + re.escape(base) + r'/detalle-propiedad/([a-z0-9-]+))"', texto):
        if m.group(2) not in vistos:
            vistos.add(m.group(2))
            pos.append((m.start(), m.group(1), m.group(2)))
    out = []
    for i, (p, url, slug) in enumerate(pos[:n]):
        fin = pos[i + 1][0] if i + 1 < len(pos) else len(texto)
        # el bloque de la tarjeta empieza un poco antes del enlace (foto) y sigue con la info
        out.append((url, slug, texto[max(0, p - 50):fin]))
    return out


def _normalizar(url, slug, b, corto, fuente, municipio):
    m_c = re.search(r"-(\d+)$", slug)
    codigo = m_c.group(1) if m_c else slug
    titulo = re.search(r'<h4 class="cursor"[^>]*>([^<]+)</h4>', b)
    titulo = htmllib.unescape(titulo.group(1)).strip() if titulo else None
    barrio = titulo.split(" - ", 1)[1].strip() if titulo and " - " in titulo else None
    if barrio:
        barrio = barrio.title()
        for p in (" De ", " Del ", " La ", " Las ", " Los ", " El ", " Y "):
            barrio = barrio.replace(p, p.lower())
    precio = re.search(r'<div class="price">.*?<p>\s*\$?\s*([\d.,]+)\s*</p>', b, re.S)
    area = re.search(r'<div class="size">.*?<small>\s*([\d.,]+)', b, re.S)
    info = [htmllib.unescape(x).strip().lower() for x in re.findall(r"<small>([^<]+)</small>", b)]

    def cuenta(*claves):
        for x in info:
            m = re.match(r"(\d+)\s*(.*)", x)
            if m and any(m.group(2).startswith(c) for c in claves):
                return int(m.group(1))
        return None

    img = re.search(r'<img src="(https?://[^"]+)"', b)
    img = img.group(1) if img and "/img/websites/" not in img.group(1) else None
    tipo = "Apartaestudio" if titulo and titulo.lower().startswith("apartaestudio") else "Apartamento"
    return {
        "id": f"lw-{corto}-{codigo}",
        "fuente": fuente,
        "url": url,
        "titulo": f"{tipo} en arriendo en {barrio}, {municipio}" if barrio else titulo,
        "precio": a_int(precio.group(1)) if precio else None,
        "admin": None,
        "area": a_int(area.group(1).split(",")[0]) if area else None,
        "hab": cuenta("alcoba", "habitaci"),
        "banos": cuenta("baño", "bano", "ba"),
        "parq": cuenta("parq", "garaje"),
        "estrato": None,
        "municipio": municipio,
        "barrio": barrio,
        "lat": None,
        "lon": None,
        "img": img,
        "fotos": [img] if img else [],
        "anunciante": fuente,
        "publicado": None,
        "tel": None,
        "wa": None,
    }


def _load_more(base, url_listado, texto, snap):
    """Siguiente tanda de tarjetas con el evento normal "load-more" del componente."""
    tok = re.search(r'data-csrf="([^"]+)"', texto) or re.search(r'name="csrf-token" content="([^"]+)"', texto)
    if not tok or not snap:
        return None, None
    _pausa(base)
    payload = {"_token": tok.group(1), "components": [{
        "snapshot": snap, "updates": {},
        "calls": [{"path": "", "method": "__dispatch", "params": ["load-more", {}]}]}]}
    try:
        r = http._session.post(f"{base}/livewire/update", json=payload, timeout=(5, 30), headers={
            "X-Livewire": "", "Content-Type": "application/json", "Referer": url_listado})
        if r.status_code != 200:
            return None, None
        c = r.json()["components"][0]
        return c["effects"].get("html") or "", c["snapshot"]
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return None, None


def scrape(estado, conocidos, log):
    primera = not estado.get("ok")
    pmin, pmax = config.PRECIO_MIN_GUARDAR, config.PRECIO_MAX_GUARDAR
    for corto, base, fuente in SITIOS:
        vistos = set()
        resumen = {}
        for municipio, slug in MUNICIPIOS:
            for tipo in TIPOS:
                url = (f"{base}/resultados?gestion=Arriendo&tipo={tipo}"
                       f"&rango-precio={pmin}-{pmax}&s=municipio-en-{slug}")
                texto = get(url)
                if not texto:
                    continue
                snap = _snapshot(texto)
                n, rel = _n_resultados(snap) if snap else (None, None)
                if n is None:
                    continue   # plantilla cambiada: mejor no adivinar qué tarjetas son del municipio
                html_cards = texto
                extra = 0
                while n and n % POR_PAGINA == 0 and not rel and extra < MAX_LOAD_MORE:
                    h, snap2 = _load_more(base, url, texto, snap)
                    if h is None:
                        break
                    n2, rel = _n_resultados(snap2)
                    extra += 1
                    if not n2 or n2 <= n:
                        break
                    html_cards, snap, n = h, snap2, n2
                r = resumen.setdefault(municipio, [0, 0])
                for u, s, b in _tarjetas(html_cards, base, n):
                    l = _normalizar(u, s, b, corto, fuente, municipio)
                    if l["id"] in vistos or l["precio"] is None:
                        continue
                    vistos.add(l["id"])
                    if not (pmin <= l["precio"] <= pmax):
                        continue
                    r[0] += 1
                    if l["id"] not in conocidos:
                        r[1] += 1
                    if primera:
                        l["_inicial"] = True
                    yield l
        log(f"  {NOMBRE}/{corto}: " + ", ".join(f"{m} {a} ({b} nuevos)" for m, (a, b) in resumen.items()))
    estado["ok"] = True


def detalle(url):
    """Galería, coordenadas y estrato desde `let property= {...}` de la ficha."""
    _pausa(url)
    try:
        r = http._session.get(url, timeout=(5, 20), allow_redirects=False)
    except requests.RequestException as e:
        raise IOError("sin respuesta") from e
    if r.status_code in (404, 410) or (r.status_code in (301, 302) and "detalle-propiedad" not in r.headers.get("Location", "")):
        return {"disponible": False}
    if r.status_code != 200:
        raise IOError(f"HTTP {r.status_code}")
    texto = r.text
    i = texto.find("let property")
    if i < 0:
        raise IOError("ficha sin datos")
    try:
        p, _ = json.JSONDecoder().raw_decode(texto, texto.find("{", i))
    except json.JSONDecodeError as e:
        raise IOError("JSON inválido") from e
    fotos = []
    for g in p.get("gallery") or []:
        im = g.get("image") if isinstance(g, dict) else g
        if isinstance(im, str) and im.startswith("http") and im not in fotos:
            fotos.append(im)
    if not fotos and isinstance(p.get("image"), str):
        fotos = [p["image"]]
    try:
        lat, lon = float(p.get("latitude")), float(p.get("longitude"))
    except (TypeError, ValueError):
        lat = lon = None
    if not (lat and 6.0 < lat < 6.45 and lon and -75.8 < lon < -75.4):
        lat = lon = None
    estrato = a_int(p.get("stratum"))
    out = {
        "fotos": fotos,
        "lat": lat,
        "lon": lon,
        "estrato": estrato if estrato and 1 <= estrato <= 6 else None,
        "banos": a_int(p.get("bathrooms")) or None,
        "parq": a_int(p.get("garage")),
    }
    v = p.get("video")
    if isinstance(v, str) and v.startswith("http"):
        out["video"] = v
    creado = str(p.get("created_at") or "")
    if re.match(r"(19|20)\d\d-\d\d-\d\d", creado):
        out["publicado"] = creado[:10]
    return out
