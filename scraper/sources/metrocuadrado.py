"""Metrocuadrado: objetos de resultados dentro del payload RSC de Next.js.

La paginación es interna (no responde a parámetros de URL): cada búsqueda devuelve como mucho
~50 anuncios y su total en "totalHits". Por eso se filtra en el servidor por canon
(?search=form&leaseRange=min,max) y, si una búsqueda tiene más de lo que devuelve, se parte en
rangos de precio más pequeños y luego por número de habitaciones (&roomList=N), hasta que cada
pedazo quepa. Así se cubre todo lo que está en el presupuesto. Trae teléfono y WhatsApp públicos.
Limitación: no da fecha de publicación.
"""
import json
import re

import config
from scraper.http import get, rsc_payload, a_int

NOMBRE = "metrocuadrado"
BASE = "https://www.metrocuadrado.com"


def _resultados(texto):
    i = texto.find('"initialResults"')
    if i < 0:
        return []
    j = texto.find('"results":[', i)
    if j < 0:
        return []
    dec = json.JSONDecoder()
    try:
        lista, _ = dec.raw_decode(texto, j + len('"results":'))
        return lista
    except json.JSONDecodeError:
        return []


def _normalizar(r, municipio):
    data = r.get("data") or {}
    link = r.get("link") or data.get("murldetalle") or ""
    foto = data.get("mprimerafotoinmueble")
    img = r.get("imageLink") or (f"https://multimedia.metrocuadrado.com/{r.get('midinmueble')}/{foto}_p.jpg" if foto else None)
    admin = a_int(data.get("mvaloradministracion"))
    loc = r.get("localizacion") or {}
    mid = r.get("midinmueble")
    # Tamaño grande "_x.jpg": el archivo original a veces es .jpg y a veces .jpeg (sin sufijo falla)
    fotos = list(dict.fromkeys(f"https://multimedia.metrocuadrado.com/{mid}/{g}_x.jpg"
                               for g in (r.get("mgaleriainmueble") or []) if isinstance(g, str)))
    tel = re.sub(r"\D", "", str(r.get("contactPhone") or "")) or None
    wa = re.sub(r"\D", "", str(r.get("whatsapp") or "")) or None
    return {
        "tel": tel,
        "wa": wa,
        "fotos": fotos,
        "id": f"m2-{r.get('midinmueble')}",
        "fuente": "Metrocuadrado",
        "url": BASE + link if link.startswith("/") else link,
        "titulo": r.get("title"),
        "precio": a_int(r.get("mvalorarriendo")),
        "admin": admin or None,
        "area": a_int(r.get("marea") or r.get("mareac")),
        "hab": a_int(r.get("mnrocuartos")),
        "banos": a_int(r.get("mnrobanos")),
        "parq": a_int(r.get("mnrogarajes")),
        "estrato": a_int(r.get("estrato")),
        "municipio": municipio,
        "barrio": r.get("mnombrecomunbarrio") or r.get("mbarrio"),
        "lat": loc.get("lat"),
        "lon": loc.get("lon"),
        "img": img,
        "anunciante": r.get("OwnerType"),
        "publicado": None,
    }


MAX_PETICIONES = 80   # por corrida (todos los municipios); el sur va primero
ANCHO_MIN = 50_000    # por debajo de este rango de precio se parte por habitaciones


def _buscar(slug, lo, hi, cuartos=None):
    url = f"{BASE}/apartamentos/arriendo/{slug}/?search=form&leaseRange={lo},{hi}" + (f"&roomList={cuartos}" if cuartos else "")
    html = get(url)
    if not html:
        return None, 0
    texto = rsc_payload(html)
    m = re.search(r'"totalHits"\s*:\s*(\d+)', texto)
    res = _resultados(texto)
    return res, int(m.group(1)) if m else len(res)


def _barrer(slug, presupuesto):
    """Todos los anuncios del municipio con canon en rango, partiendo las búsquedas que no caben."""
    pendientes = [(config.PRECIO_MIN_GUARDAR, config.PRECIO_MAX_GUARDAR, None)]
    vistos, incompleto = {}, False
    while pendientes:
        if presupuesto[0] <= 0:
            incompleto = True
            break
        lo, hi, cuartos = pendientes.pop(0)
        presupuesto[0] -= 1
        res, total = _buscar(slug, lo, hi, cuartos)
        if res is None:
            incompleto = True
            continue
        for r in res:
            if isinstance(r, dict) and r.get("midinmueble"):
                vistos[r["midinmueble"]] = r
        if total > len(res):
            if hi - lo > ANCHO_MIN and cuartos is None:
                mitad = (lo + hi) // 2 // 10_000 * 10_000
                pendientes += [(lo, mitad, None), (mitad + 1, hi, None)]
            elif cuartos is None:
                pendientes += [(lo, hi, n) for n in (1, 2, 3, 4)]
            else:
                incompleto = True  # mismo precio y habitaciones con más de 50: se queda lo que hay
    return list(vistos.values()), incompleto


def scrape(estado, conocidos, log):
    presupuesto = [MAX_PETICIONES]
    # El sur primero: si se acaba el presupuesto, lo que falta es Medellín
    orden = sorted(config.MUNICIPIOS.items(), key=lambda kv: kv[0] == "Medellín")
    for municipio, slugs in orden:
        slug = slugs.get(NOMBRE)
        if not slug:
            continue
        antes = presupuesto[0]
        resultados, incompleto = _barrer(slug, presupuesto)
        if not resultados:
            continue
        primera_vez = not estado.get(slug) or estado[slug].get("v") != 2
        estado[slug] = {"visto": True, "v": 2}
        nuevos = 0
        for r in resultados:
            if not isinstance(r, dict) or not r.get("midinmueble"):
                continue
            l = _normalizar(r, municipio)
            if primera_vez:
                l["_inicial"] = True
            if l["id"] not in conocidos:
                nuevos += 1
            yield l
        log(f"  metrocuadrado/{slug}: {len(resultados)} leídos, {nuevos} nuevos, {antes - presupuesto[0]} peticiones"
            + (" (incompleto)" if incompleto else ""))
