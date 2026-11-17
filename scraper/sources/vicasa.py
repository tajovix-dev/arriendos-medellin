"""Inmobiliaria Vicasa (inmobiliariavicasa.com, sedes Santa María-Itagüí y San Antonio de Prado).

De dónde salen los datos
- Listado: propiedades-en-arrendamiento.asp?start=1, 21, 41... (20 tarjetas por página, todo el
  inventario de arriendo, ~50 inmuebles; sin filtros por URL). Tarjeta: enlace inmueble.asp?Id=N,
  "APARTAMENTO en Arrendamiento", "ANTIOQUIA -> MUNICIPIO -> BARRIO", precio, área, alcobas,
  baños, foto principal y la descripción del anuncio.
- Ficha inmueble.asp?Id=N: galería de fotos. Solo se visita la de anuncios nuevos (ids vistos en
  `estado`, podados a los que siguen en el listado).
- Contacto: muchos anuncios dicen en su propia descripción "INFORMACION WHATSAPP 300...": ese
  número es el WhatsApp del anuncio. Teléfono: la línea de servicio publicada en el sitio.
- robots.txt prohíbe /imagenes/, /img/, /adminweb/, etc.: no se pide ninguna de esas rutas (las
  fotos se guardan como enlaces a /imagenes/..., que el sitio muestra en sus fichas).

Limitaciones
- Sin coordenadas ni estrato ni fecha de publicación (las fotos se llaman "WhatsApp Image
  AAAA-MM-DD", que es la fecha de la foto en el teléfono, no la de publicación: publicado=None).
- Solo APARTAMENTO, APARTAMENTO DUPLEX y APARTAESTUDIO ("UN SOLO AMBIENTE", "APARTA LOCAL" y
  similares se descartan).
- San Antonio de Prado (corregimiento de Medellín) se guarda como Medellín, barrio "San Antonio de Prado".
- Sin detalle(): la galería se toma aquí la primera vez que aparece el anuncio.
"""
import html as htmllib
import re
import unicodedata
from urllib.parse import quote

import config
from scraper.http import GONE, a_int, get

NOMBRE = "vicasa"
BASE = "https://www.inmobiliariavicasa.com"
FUENTE = "Inmobiliaria Vicasa"
# "Línea de Servicio: (4) 448 23 34" / "PBX: (4) 448 23 34" en https://www.inmobiliariavicasa.com/
TEL_VICASA = "576044482334"
MAX_PAGINAS = 6
MAX_FICHAS = 40


def _llano(s):
    s = unicodedata.normalize("NFD", s or "")
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn")).strip().lower()


CIUDADES = {"medellin": "Medellín", "sabaneta": "Sabaneta", "envigado": "Envigado", "itagui": "Itagüí",
            "la estrella": "La Estrella", "san antonio de prado": "Medellín"}
TIPOS = {"apartamento": "Apartamento", "apartamento duplex": "Apartamento", "apartaestudio": "Apartaestudio"}


def _bonito(s):
    s = re.sub(r"\s+", " ", s or "").strip(" .,-")
    if not s:
        return None
    s = s.title()
    for p in (" De ", " Del ", " La ", " Las ", " Los ", " El ", " Y "):
        s = s.replace(p, p.lower())
    return s


def _abs(ruta):
    ruta = htmllib.unescape(ruta).lstrip("/")
    return f"{BASE}/{quote(ruta, safe='/()._-')}"


def _tarjetas(texto):
    out = []
    for b in texto.split('<div class="item col-md-4">')[1:]:
        m = re.search(r'href="(inmueble\.asp\?Id=(\d+))', b)
        tipo = re.search(r"<h3>\s*<a[^>]*>\s*([^<]+?)\s+en Arrendamiento", b)
        loc = re.search(r"<small>([^<]+)</small>", b)
        if not (m and tipo and loc):
            continue
        partes = [x.strip() for x in htmllib.unescape(loc.group(1)).split("->")]
        precio = re.search(r'<div class="price">.*?<span>\s*\$\s*([\d.,]+)', b, re.S)
        desc = re.search(r"<p>(.*?)</p>", b, re.S)
        desc = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", desc.group(1)))).strip() if desc else ""
        img = re.search(r'<img src="(imagenes/[^"]+)"', b)

        def ico(nombre):
            x = re.search(r'icon-' + nombre + r'"></i>\s*([\d.,]*)', b)
            return a_int(x.group(1)) if x and x.group(1) else None

        out.append({
            "id": m.group(2), "url": f"{BASE}/inmueble.asp?Id={m.group(2)}",
            "tipo": TIPOS.get(_llano(tipo.group(1))),
            "ciudad": partes[1] if len(partes) > 1 else "", "barrio": partes[2] if len(partes) > 2 else "",
            "precio": a_int(precio.group(1)) if precio else None, "desc": desc,
            "area": ico("area"), "hab": ico("bedrooms"), "banos": ico("bathrooms"),
            "img": _abs(img.group(1)) if img else None,
        })
    return out


def _wa(desc):
    """WhatsApp que el propio anuncio publica en su descripción ("INFORMACION WHATSAPP 3002739867")."""
    # También con las erratas del sitio ("WTHASAPP", "WHTASAP"); números incompletos se ignoran
    m = re.search(r"\bw[ht][a-z]*ap+\b\D{0,15}(3\d{2})\s*(\d{3})\s*(\d{4})\b", desc, re.I)
    return "57" + "".join(m.groups()) if m else None


def _galeria(texto):
    fotos = []
    for r in re.findall(r'<img src="(/imagenes/[Ii]nmuebles/[^"]+)"', texto):
        u = _abs(r)
        if u not in fotos:
            fotos.append(u)
    return fotos


def scrape(estado, conocidos, log):
    primera = not estado.get("ok")
    vistos = set(estado.get("vistos", []))
    pmin, pmax = config.PRECIO_MIN_GUARDAR, config.PRECIO_MAX_GUARDAR
    tarjetas, ids, completo = [], set(), True
    for p in range(MAX_PAGINAS):
        texto = get(f"{BASE}/propiedades-en-arrendamiento.asp?start={1 + 20 * p}", si_404=GONE)
        if not texto or texto == GONE:
            completo = False
            break
        nuevas = [t for t in _tarjetas(texto) if t["id"] not in ids]
        if not nuevas:   # pasada la última página el sitio repite la última
            break
        for t in nuevas:
            ids.add(t["id"])
        tarjetas += nuevas
    resumen = {}
    fichas = 0
    for t in tarjetas:
        municipio = CIUDADES.get(_llano(t["ciudad"]))
        if not t["tipo"] or not municipio or not t["precio"]:
            continue
        id_ = f"vc-{t['id']}"
        if not (pmin <= t["precio"] <= pmax) and id_ not in conocidos:
            continue
        if _llano(t["ciudad"]) == "san antonio de prado":
            barrio = "San Antonio de Prado"
        else:
            barrio = _bonito(t["barrio"])
            if barrio and _llano(barrio) in (_llano(t["ciudad"]), _llano(municipio)):
                barrio = None
        fotos = []
        if t["id"] not in vistos and fichas < MAX_FICHAS:
            ficha = get(t["url"], si_404=GONE)
            fichas += 1
            if ficha == GONE:
                continue
            if ficha:
                fotos = _galeria(ficha)
                vistos.add(t["id"])
        img = t["img"]
        l = {
            "id": id_, "fuente": FUENTE, "url": t["url"],
            "titulo": f"{t['tipo']} en arriendo en {barrio}, {municipio}" if barrio else f"{t['tipo']} en arriendo en {municipio}",
            "precio": t["precio"], "admin": None, "area": t["area"] or None, "hab": t["hab"], "banos": t["banos"],
            "parq": None, "estrato": None, "municipio": municipio, "barrio": barrio, "lat": None, "lon": None,
            "img": fotos[0] if fotos else img, "fotos": fotos or ([img] if img else []),
            "anunciante": FUENTE, "publicado": None, "tel": TEL_VICASA, "wa": _wa(t["desc"]),
        }
        if pmin <= l["precio"] <= pmax:
            r = resumen.setdefault(municipio, [0, 0])
            r[0] += 1
            if id_ not in conocidos:
                r[1] += 1
        if primera:
            l["_inicial"] = True
        yield l
    if completo:
        vistos &= ids
    estado["vistos"] = sorted(vistos)
    estado["ok"] = True
    log(f"  {NOMBRE}: {len(tarjetas)} en el listado, {fichas} fichas visitadas; "
        + (", ".join(f"{m} {a} ({b} nuevos)" for m, (a, b) in resumen.items()) or "0"))
