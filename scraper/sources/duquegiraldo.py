"""Duque Giraldo (duquegiraldo.com): Softinm con interfaz propia (Laravel). robots.txt: "Disallow:" vacío.

De dónde salen los datos
- Listado: /propiedades/arriendo?clase={Apartamento|Apartaestudio}&c={código municipio}
  &price_f=0&price_t=2000000&page=N (códigos del propio buscador: 4 Envigado, 8 Itagüí,
  28 La Estrella, 29 Medellín, 30 Sabaneta). El sitio distingue Apartaestudio como clase propia.
  Orden por defecto "Más recientes". ~20 tarjetas por página; se sigue a la página N+1 solo si
  el paginador la enlaza.
- Tarjeta: enlace /propiedad/{tipo}-en-arriendo-{barrio}-{municipio}-{código}, código, título
  "Apartamento - Itagüí", área, alcobas, baños, garajes, precio y foto. El barrio sale del slug
  (sin tildes); detalle() lo trae limpio de la ficha solo si faltara.
- Contacto: el WhatsApp (botón "Contactar con un asesor" / flotante) y el "Telefono:" del pie de
  página se leen del HTML del propio listado en cada corrida (no hay asesor por anuncio).
- detalle(url): galería completa (solo fotos con el prefijo del inmueble), estrato, barrio y el
  WhatsApp del botón de la ficha. 404 → {"disponible": False}.
- Sin fecha de publicación ni coordenadas → publicado/lat/lon = None.
- Inventario en rango pequeño (~25): se lee completo cada corrida (~10 peticiones).
"""
import html as htmllib
import re

import config
from scraper.http import GONE, a_int, get

NOMBRE = "duquegiraldo"
FUENTE = "Duque Giraldo"
BASE = "https://duquegiraldo.com"
# (municipio, código del buscador, slug en la URL de la ficha). El sur primero; Medellín al final
MUNICIPIOS = [("Itagüí", 8, "itagui"), ("Sabaneta", 30, "sabaneta"), ("Envigado", 4, "envigado"),
              ("La Estrella", 28, "la-estrella"), ("Medellín", 29, "medellin")]
CLASES = ["Apartamento", "Apartaestudio"]
MAX_PAGINAS = 5
MINUSCULAS = (" De ", " Del ", " La ", " Las ", " Los ", " El ", " Y ", " En ")


def _bonito(s):
    s = re.sub(r"\s+", " ", s or "").strip(" ,.-")
    if not s:
        return None
    s = s.title()
    for p in MINUSCULAS:
        s = s.replace(p, p.lower())
    return s


def _digitos(s):
    d = re.sub(r"\D", "", s or "")
    return d if len(d) >= 10 else None


def _contacto(texto):
    """WhatsApp y teléfono que el sitio muestra (botón de WhatsApp y 'Telefono:' del pie)."""
    wa = re.search(r"api\.whatsapp\.com/send\?phone=\+?(\d{10,13})", texto)
    tel = re.search(r"Telefono:\s*(?:<br\s*/?>)?\s*(?:<span>)?\s*([\d\s()+-]{7,})", texto)
    return (_digitos(tel.group(1)) if tel else None), (wa.group(1) if wa else None)


def _tarjetas(texto):
    i = texto.find("cont-list-properties")
    if i >= 0:
        texto = texto[i:]
    partes = re.split(r'(?=<a href="https://duquegiraldo\.com/propiedad/)', texto)
    return [p for p in partes[1:] if 'class="property-card"' in p[:400]]


def _normalizar(b, municipio, slug_muni, tel, wa):
    url = re.match(r'<a href="([^"]+)"', b).group(1)
    m = re.search(r"/propiedad/(apartamento|apartaestudio)-en-arriendo-(.*?)-?" + re.escape(slug_muni) + r"-(\d+)$", url)
    if not m:
        return None   # otro tipo de inmueble u otro municipio
    tipo, barrio, codigo = m.group(1).title(), m.group(2).replace("-", " "), m.group(3)
    barrio = _bonito(barrio)

    def feat(clase):
        f = re.search(r'feature-icon ' + clase + r'"></span>\s*<span class="feature-value">\s*([\d.,]+)', b)
        return a_int(f.group(1).split(",")[0]) if f else None

    precio = re.search(r"<label>\s*\$\s*([\d.,]+)\s*</label>", b)
    img = re.search(r'<img src="(https?://[^"]+)"', b)
    img = img.group(1) if img else None
    return {
        "id": f"dg-{codigo}",
        "fuente": FUENTE,
        "url": url,
        "titulo": f"{tipo} en arriendo en {barrio}, {municipio}" if barrio else f"{tipo} en arriendo en {municipio}",
        "precio": a_int(precio.group(1)) if precio else None,
        "admin": None,
        "area": feat("area"),
        "hab": feat("bed"),
        "banos": feat("bath"),
        "parq": feat("garage"),
        "estrato": None,
        "municipio": municipio,
        "barrio": barrio,
        "lat": None,
        "lon": None,
        "img": img,
        "fotos": [img] if img else [],
        "anunciante": FUENTE,
        "publicado": None,
        "tel": tel,
        "wa": wa,
    }


def scrape(estado, conocidos, log):
    primera = not estado.get("ok")
    pmin, pmax = config.PRECIO_MIN_GUARDAR, config.PRECIO_MAX_GUARDAR
    vistos = set()
    resumen = {}
    fallos = 0
    for municipio, cod, slug in MUNICIPIOS:
        r = resumen.setdefault(municipio, [0, 0])
        for clase in CLASES:
            for pag in range(1, MAX_PAGINAS + 1):
                url = (f"{BASE}/propiedades/arriendo?clase={clase}&c={cod}"
                       f"&price_f=0&price_t={pmax}&page={pag}")
                texto = get(url)
                if not texto:
                    fallos += 1
                    break
                tel, wa = _contacto(texto)
                tarjetas = _tarjetas(texto)
                for b in tarjetas:
                    l = _normalizar(b, municipio, slug, tel, wa)
                    if not l or l["id"] in vistos or l["precio"] is None:
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
                if not tarjetas or f"page={pag + 1}" not in texto:
                    break
    log(f"  {NOMBRE}: " + ", ".join(f"{m} {a} ({b} nuevos)" for m, (a, b) in resumen.items())
        + (f" [{fallos} páginas sin respuesta]" if fallos else ""))
    estado["ok"] = True


def detalle(url):
    """Galería completa, estrato, barrio y WhatsApp del botón de la ficha."""
    t = get(url, si_404=GONE)
    if t == GONE:
        return {"disponible": False}
    if not t:
        raise IOError("sin respuesta")
    if "list-featured-data" not in t:
        raise IOError("ficha sin datos")
    todas = []
    for f in re.findall(r"""(https?://[^"'\s)]+/real_estate_pictures/foto_\d+_\d+_\d+_ss\.(?:jpe?g|png|webp))""", t, re.I):
        if f not in todas:
            todas.append(f)
    fotos = []
    og = re.search(r'property="og:image" content="([^"]+)"', t)
    ref = og.group(1) if og and "/foto_" in og.group(1) else (todas[0] if todas else None)
    if ref:
        pref = re.search(r"/(foto_\d+_\d+_)", ref)
        fotos = [f for f in todas if pref and "/" + pref.group(1) in f]
        if ref in fotos:   # la principal primero
            fotos.remove(ref)
            fotos.insert(0, ref)

    def dato(nombre):
        m = re.search(r"<li><strong>\s*" + nombre + r":?\s*</strong>\s*([^<]+?)\s*</li>", t)
        return htmllib.unescape(m.group(1)).strip() if m else None

    estrato = a_int(dato("Estrato"))
    tel, _ = _contacto(t)
    wa = re.search(r'api\.whatsapp\.com/send\?phone=\+?(\d{10,13})[^"]*"[^>]*>\s*Contactar con un asesor', t)
    out = {
        "fotos": fotos,
        "estrato": estrato if estrato and 1 <= estrato <= 6 else None,
        "barrio": _bonito(dato("Barrio")),
        "banos": a_int(dato("Baños")),
        "tel": tel,
        "wa": wa.group(1) if wa else None,
    }
    return {k: v for k, v in out.items() if v not in (None, "", [])}
