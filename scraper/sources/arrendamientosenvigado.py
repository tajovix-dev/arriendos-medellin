"""Arrendamientos Envigado S.A. (arrendamientosenvigadosa.com.co): sitemap + fichas públicas.

De dónde salen los datos
- robots.txt prohíbe /api/, así que NO se usa la API JSON del buscador. El inventario sale de
  /sitemap.xml (URLs /inmueble/{tipo}-arriendo-{municipio}-{barrio}-{code}); se toman solo
  apartamento/apartaestudio en arriendo de los 5 municipios.
- El precio y los datos solo están en la ficha HTML (Next.js): JSON-LD Product (precio, foto, sku),
  la lista de características renderizada (área, habitaciones, baños, parqueadero, estrato),
  "ubicacion" ("Envigado, La Paz") y la galería ("fotos":[…]) en el payload RSC.
- La ficha también trae ~9 inmuebles "Otros resultados" como objetos completos (code, precio, área,
  barrio, lat/lng, estrato, imagen). Se usan para refrescar la caché de ESOS codes (cada uno con su
  propio code, nunca mezclados con el inmueble de la ficha); es la única fuente de lat/lon, porque la
  ficha propia no publica coordenadas.

Estrategia (≈1 + 90 peticiones por corrida)
- estado["c"] = {code: [precio, día]} (fuera de rango) o [precio, día, área, hab, baños, parq,
  estrato, barrio, lat, lon] (en rango); día = días desde 2020-01-01. Se poda lo que ya no está en
  el sitemap.
- Se abren primero las fichas nuevas (codes más altos primero), luego las en rango con más de 3 días
  sin revisar y las fuera de rango con más de 10. Los 4 municipios del sur van antes que Medellín
  (Medellín solo con el presupuesto sobrante).
- En cada corrida se emiten TODOS los en rango que siguen en el sitemap (desde la caché si no se
  visitaron), para que sigan "vistos". Si el code sale del sitemap, deja de emitirse.
- Sin fecha de publicación (publicado=None). La carga inicial (~430 fichas) tarda unas 5 corridas;
  los codes que ya existían en el primer sitemap se marcan _inicial.
"""
import html as htmllib
import json
import re
from datetime import date

import config
from scraper.http import GONE, a_int, get, json_ld, rsc_payload

NOMBRE = "arrendamientosenvigado"
FUENTE = "Arrendamientos Envigado"
BASE = "https://arrendamientosenvigadosa.com.co"
TEL = "6044446868"          # PBX publicado en el pie de página del sitio
WA = "573166333336"         # WhatsApp publicado en el sitio (botón de cada ficha)
MUNICIPIOS = {"medellin": "Medellín", "envigado": "Envigado", "sabaneta": "Sabaneta",
              "itagui": "Itagüí", "la-estrella": "La Estrella"}
MAX_FICHAS = 90
DIAS_EN_RANGO = 3
DIAS_FUERA = 10
_URL = re.compile(r"/inmueble/(apartamento|apartaestudio)-arriendo-(medellin|envigado|sabaneta|itagui|la-estrella)-(.+)-(\d+)$")


def _hoy():
    return (date.today() - date(2020, 1, 1)).days


def _en_rango(p):
    return p is not None and config.PRECIO_MIN_GUARDAR <= p <= config.PRECIO_MAX_GUARDAR


def _sitemap():
    xml = get(f"{BASE}/sitemap.xml")
    if not xml:
        return None
    out = {}
    for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
        m = _URL.search(u)
        if m:
            out[m.group(4)] = (u, m.group(1), MUNICIPIOS[m.group(2)], m.group(3))
    return out


def _num(v):
    try:
        return float(v) if v not in (None, "", "$undefined") else None
    except (TypeError, ValueError):
        return None


def _entrada(precio, dia, area=None, hab=None, banos=None, parq=None, estrato=None, barrio=None, lat=None, lon=None):
    if not _en_rango(precio):
        return [precio, dia]
    if lat is not None and not (6.0 < lat < 6.45 and -75.8 < (lon or 0) < -75.4):
        lat = lon = None
    return [precio, dia, area, hab, banos, parq, estrato, barrio,
            round(lat, 6) if lat else None, round(lon, 6) if lon else None]


def _similares(texto):
    """Objetos completos de otros inmuebles que la ficha muestra ("Otros resultados")."""
    dec = json.JSONDecoder()
    for m in re.finditer(r'\{"id":"\d+","code":"\d+","idInmobiliaria"', texto):
        try:
            obj, _ = dec.raw_decode(texto, m.start())
        except json.JSONDecodeError:
            continue
        yield obj


def _ficha(html, code):
    """Datos del inmueble propio de la ficha (no de los similares)."""
    precio = None
    img = None
    for j in json_ld(html):
        for b in (j if isinstance(j, list) else [j]):
            if isinstance(b, dict) and b.get("@type") == "Product" and str(b.get("sku")) == code:
                precio = a_int((b.get("offers") or {}).get("price"))
                im = b.get("image")
                img = im[0] if isinstance(im, list) and im else im

    def caract(nombre):
        m = re.search(r'<span class="flex-1">' + nombre + r'</span><span class="font-medium">(\d+)', html)
        return int(m.group(1)) if m else None

    texto = rsc_payload(html)
    ubic = None
    fotos = []
    i = texto.find(f'"code":"{code}","url":"/inmueble/')
    if i >= 0:
        ini = texto.rfind('"ubicacion":"', 0, i)
        if ini >= 0 and i - ini < 300:
            ubic = json.loads(texto[ini + 12: texto.find('"', ini + 13) + 1])
        j = texto.find('"fotos":[', i)
        if j >= 0 and j - i < 3000:
            try:
                fotos, _ = json.JSONDecoder().raw_decode(texto, j + 8)
            except json.JSONDecodeError:
                fotos = []
    barrio = ubic.split(",", 1)[1].strip() if ubic and "," in ubic else None
    if precio is None:
        m = re.search(r'Precio de .{0,400}?\$\$?([\d.]+)', texto, re.S)
        precio = a_int(m.group(1)) if m else None
    fotos = [f for f in fotos if isinstance(f, str) and f.startswith("http")]
    return {
        "precio": precio,
        "area": caract(r"[^<]{0,3}rea construida") or caract(r"[^<]{0,3}rea"),
        "hab": caract("Habitaciones"),
        "banos": caract(r"Ba[^<]{1,2}os"),
        "parq": caract("Parqueaderos?"),
        "estrato": caract("Estrato"),
        "barrio": htmllib.unescape(barrio) if barrio else None,
        "fotos": fotos or ([img] if img else []),
    }, texto


def _anuncio(code, info, entrada, fotos=None):
    url, tipo, municipio, slug_barrio = info
    precio, _, area, hab, banos, parq, estrato, barrio, lat, lon = entrada
    barrio = barrio or slug_barrio.replace("-", " ").title()
    t = "Apartaestudio" if tipo == "apartaestudio" else "Apartamento"
    return {
        "id": f"ae-{code}",
        "fuente": FUENTE,
        "url": url,
        "titulo": f"{t} en arriendo en {barrio}, {municipio}",
        "precio": precio,
        "admin": None,
        "area": area or None,
        "hab": hab,
        "banos": banos,
        "parq": parq,
        "estrato": estrato if estrato and 1 <= estrato <= 6 else None,
        "municipio": municipio,
        "barrio": barrio,
        "lat": lat,
        "lon": lon,
        "img": fotos[0] if fotos else None,
        "fotos": fotos or None,
        "anunciante": "Arrendamientos Envigado",
        "publicado": None,
        "tel": TEL,
        "wa": WA,
    }


def scrape(estado, conocidos, log):
    mapa = _sitemap()
    if not mapa:
        log("  arrendamientosenvigado: sin sitemap (fallo temporal)")
        return
    cache = estado.setdefault("c", {})
    if "max_inicial" not in estado:
        estado["max_inicial"] = max(int(c) for c in mapa)
    max_inicial = estado["max_inicial"]
    for code in [c for c in cache if c not in mapa]:
        del cache[code]
    hoy = _hoy()
    fotos_corrida = {}

    def vencido(code):
        e = cache.get(code)
        if e is None:
            return True
        return hoy - e[1] >= (DIAS_EN_RANGO if _en_rango(e[0]) else DIAS_FUERA)

    # Prioridad al sur: Medellín solo con el presupuesto que sobre
    def med(c):
        return mapa[c][2] == "Medellín"
    nuevos = sorted((c for c in mapa if c not in cache), key=lambda c: (med(c), -int(c)))
    viejos = sorted((c for c in mapa if c in cache and vencido(c)), key=lambda c: (med(c), cache[c][1]))
    orden = [c for c in nuevos + viejos if not med(c)] + [c for c in nuevos + viejos if med(c)]
    visitas = gone = 0
    for code in orden:
        if visitas >= MAX_FICHAS:
            break
        if not vencido(code):
            continue   # ya refrescado en esta corrida como "similar" de otra ficha
        visitas += 1
        html = get(mapa[code][0], si_404=GONE)
        if html == GONE:
            cache.pop(code, None)
            mapa.pop(code, None)
            gone += 1
            continue
        if html is None:
            continue
        d, texto = _ficha(html, code)
        if d["precio"] is None:
            continue
        previa = cache.get(code) or []
        lat = previa[8] if len(previa) > 8 else None
        lon = previa[9] if len(previa) > 9 else None
        cache[code] = _entrada(d["precio"], hoy, d["area"], d["hab"], d["banos"], d["parq"],
                               d["estrato"], d["barrio"], lat, lon)
        fotos_corrida[code] = d["fotos"]
        # Inmuebles similares: refrescan la caché de su propio code
        for s in _similares(texto):
            sc = str(s.get("code"))
            if sc == code or sc not in mapa or s.get("modo") != "arriendo":
                continue
            if str(s.get("tipo", "")).lower() not in ("apartamento", "apartaestudio"):
                continue
            p = a_int(s.get("precio"))
            if p is None:
                continue
            area = a_int(s.get("area"))
            cache[sc] = _entrada(p, hoy, area, a_int(s.get("habitaciones")), a_int(s.get("banos")),
                                 a_int(s.get("garajes")), a_int(s.get("estrato")), s.get("barrio") or None,
                                 _num(s.get("lat")), _num(s.get("lng")))
            if sc not in fotos_corrida and s.get("imagen") and not s.get("fotoNoDisponible"):
                fotos_corrida[sc] = [s["imagen"]]

    emitidos = n_nuevos = 0
    por_mun = {}
    for code, e in cache.items():
        if code not in mapa or not _en_rango(e[0]) or len(e) < 10:
            continue
        l = _anuncio(code, mapa[code], e, fotos_corrida.get(code))
        if int(code) <= max_inicial:
            l["_inicial"] = True
        emitidos += 1
        por_mun[l["municipio"]] = por_mun.get(l["municipio"], 0) + 1
        if l["id"] not in conocidos:
            n_nuevos += 1
        yield l
    pend = sum(1 for c in mapa if c not in cache)
    log(f"  arrendamientosenvigado: sitemap {len(mapa)}, {visitas} fichas, {gone} ya no existen, "
        f"{emitidos} en rango ({n_nuevos} nuevos) {por_mun}, {pend} sin revisar aún")
