"""Metrocuadrado: objetos de resultados dentro del payload RSC de Next.js.

Limitación conocida: la paginación es interna (no responde a parámetros de URL),
así que por ahora solo se lee la primera página (~60 anuncios) por municipio.
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


def scrape(estado, conocidos, log):
    for municipio, slugs in config.MUNICIPIOS.items():
        slug = slugs.get(NOMBRE)
        if not slug:
            continue
        html = get(f"{BASE}/apartamentos/arriendo/{slug}/")
        if not html:
            continue
        resultados = _resultados(rsc_payload(html))
        primera_vez = not estado.get(slug)
        estado[slug] = {"visto": True}
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
        log(f"  metrocuadrado/{slug}: {len(resultados)} leídos, {nuevos} nuevos")
