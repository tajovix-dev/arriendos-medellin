"""Inmobiliarias con la plataforma clásica de Softinm (ASP.NET; fotos en /fotos/foto_*_ss.jpg).

Una sola fuente para varios sitios (como softin_livewire): Arrendamientos Sukasa, Mi Lugar
Inmobiliario, Arrendamientos La Aldea, Survivienda y Su Vivienda. Son las inmobiliarias ORIGINALES
de anuncios que antes solo llegaban vía Rentola. Su robots.txt es "Disallow:" vacío (todo permitido;
revisado el 2026-11-16).

De dónde salen los datos
- Listado. Hay dos plantillas de URL y dos de HTML, según el sitio:
  * "nuevo" (Sukasa, Mi Lugar, La Aldea): {base}/inmuebles/Arriendo/clases_Apartamento_Apartaestudio/
    municipios_{Itagüí|Sabaneta|Envigado|La-Estrella|Medellín}/precio_0_2000000/
    Si no hay resultados el sitio redirige a /no-se-encontro-informacion (= 0 anuncios).
  * "viejo" (Su Vivienda): {base}/inmuebles/Arriendo/{Apartamento|Apartaestudio}/{Municipio}/precio_0_2000000/
    (aquí "clases_A_B" no funciona; el filtro de clase y municipio sí).
  * "todo" (Survivienda): su buscador ignora municipio y clase en la URL (probado: cualquier
    combinación devuelve lo mismo), así que se pide una sola vez /inmuebles/Arriendo/precio_0_2000000/
    y se filtra por la tarjeta.
  Páginas siguientes: la misma URL + "2", "3"... (16 tarjetas por página en la plantilla Sukasa,
  12 en la de Mi Lugar/La Aldea); se sigue solo si la página enlaza a la siguiente.
- Tarjeta: enlace /inmueble/{código}, "Apartamento - {código}" (tipo), ubicación "Barrio, Municipio"
  o "Municipio, Barrio[, Urbanización]", precio, área, alcobas, baños, garajes (algunas) y foto.
  El municipio se toma de la tarjeta (no de la URL) y se descartan los que no son de los 5.
- detalle(url): galería completa (solo las fotos con el prefijo del inmueble; las de "Relacionadas"
  se excluyen), estrato y barrio (tabla "Detalles", cuando la plantilla la tiene) y el contacto que
  la PROPIA ficha muestra: el botón de WhatsApp "...sobre el inmueble {código}..." y el teléfono del
  recuadro "Contáctanos" (fa-phone). Su Vivienda no tiene recuadro: se usa el teléfono y el
  WhatsApp del pie de página de la ficha. Todo se lee del HTML en cada visita, nada fijo en código.
  Ficha que ya no existe: 302 a /Paginas/ o /no-se-encontro-informacion → {"disponible": False}.
- No hay fecha de publicación ni coordenadas en ningún sitio → publicado/lat/lon = None.
- Inventario en rango pequeño (decenas por sitio): se lee completo cada corrida (~26 peticiones).
"""
import html as htmllib
import re
import time
import unicodedata
from urllib.parse import urljoin, urlparse

import requests

import config
from scraper import http
from scraper.http import a_int, get

NOMBRE = "softinm"
SITIOS = [
    # (corto, base, nombre visible, modo de URL)
    ("sukasa", "https://sukasainmobiliaria.com", "Arrendamientos Sukasa", "nuevo"),
    ("milugar", "https://milugarinmobiliario.com", "Mi Lugar Inmobiliario", "nuevo"),
    ("aldea", "https://www.arrendamientoslaaldea.com.co", "Arrendamientos La Aldea", "nuevo"),
    ("survivienda", "https://www.survivienda.com", "Survivienda", "todo"),
    ("suvivienda", "https://suvivienda.com.co", "Su Vivienda", "viejo"),
]
# El sur primero; Medellín al final. (nombre, slug formato nuevo, slug formato viejo)
MUNICIPIOS = [("Itagüí", "Itagüí", "Itagüí"), ("Sabaneta", "Sabaneta", "Sabaneta"),
              ("Envigado", "Envigado", "Envigado"), ("La Estrella", "La-Estrella", "La-Estrella"),
              ("Medellín", "Medellín", "Medellín")]
TIPOS_OK = {"apartamento": "Apartamento", "apartaestudio": "Apartaestudio", "apto-loft": "Apartamento"}
MAX_PAGINAS = 6
MINUSCULAS = (" De ", " Del ", " La ", " Las ", " Los ", " El ", " Y ", " En ")


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


MUNI_NORM = {_norm(m): m for m, _, _ in MUNICIPIOS}


def _bonito(s):
    s = re.sub(r"\s+", " ", s).strip(" ,.-")
    if not s:
        return None
    s = s.title()
    for p in MINUSCULAS:
        s = s.replace(p, p.lower())
    return s


def _ubicacion(texto):
    """'Villa Paula, Itagüí' / 'Itagüí, San Pio, Ed. X' / 'La Mina, La Florida, Envigado' -> (municipio, barrio)."""
    partes = [p.strip() for p in texto.split(",") if p.strip()]
    for i, p in enumerate(partes):
        muni = MUNI_NORM.get(_norm(p))
        if not muni:
            continue
        # el barrio es la parte vecina al municipio (después si va primero, antes si va al final)
        vecinos = partes[i + 1:] if i == 0 else list(reversed(partes[:i]))
        for v in vecinos:
            if _norm(v) != _norm(muni):
                return muni, _bonito(v)
        return muni, None
    return None, None


def _foto(u):
    if not u:
        return None
    return "https:" + u if u.startswith("//") else u


def _cuenta(m):
    if not m:
        return None
    return 1 if m.group(1).startswith("Un") else int(m.group(1))


def _tarjetas(texto):
    """[(código, href, bloque)] en orden del DOM; el bloque va hasta la siguiente tarjeta."""
    pos = []
    vistos = set()
    for m in re.finditer(r"""href=["']([^"']*/inmueble/(\d+)[^"']*)["']""", texto):
        if m.group(2) not in vistos:
            vistos.add(m.group(2))
            pos.append((m.start(), m.group(2), htmllib.unescape(m.group(1))))
    out = []
    for i, (p, cod, href) in enumerate(pos):
        fin = pos[i + 1][0] if i + 1 < len(pos) else min(len(texto), p + 6000)
        out.append((cod, href, texto[max(0, p - 200):fin]))
    return out


def _normalizar(cod, href, b, base, corto, fuente):
    tipo = re.search(r">\s*([A-Za-z-]+)\s*-\s*(?:C(?:ó|&#243;)digo:\s*)?" + cod + r"\s*<", b)
    tipo = TIPOS_OK.get(tipo.group(1).lower()) if tipo else None
    if not tipo:
        return None
    loc = (re.search(r'class="proerty_text">\s*<h4>\s*([^<]+?)\s*</h4>', b)
           or re.search(r"fa-map-marker'></i>\s*([^<]+?)\s*<", b))
    municipio, barrio = _ubicacion(htmllib.unescape(loc.group(1))) if loc else (None, None)
    if not municipio:
        return None
    precio = (re.search(r'class="pull-md-left">\s*\$\s*([\d.,]+)', b)
              or re.search(r"listing-price'>.*?\$\s*([\d.,]+)", b, re.S))
    area = re.search(r"(\d+)(?:[.,]\d+)?\s*m\s*<sup>2", b)
    # "2 Alcobas", "Una Alcoba", "1.0 Baños", "Un Baño", "Un Garaje"
    hab = re.search(r"\b(\d+|Una?)\s*Alcobas?", b)
    banos = re.search(r"\b(\d+|Una?)(?:\.\d+)?\s*Ba(?:ñ|&#241;)os?\b", b)
    parq = re.search(r"\b(\d+|Una?)\s*Garajes?\b", b)
    img = re.search(r"""src=["']((?:https?:)?//[^"']+/foto_[^"']+_ss\.(?:jpe?g|png|webp))["']""", b, re.I)
    img = _foto(img.group(1)) if img else None
    # URL canónica de la ficha: /inmueble/{código} (Sukasa/Survivienda/Su Vivienda) o /inmueble/{código}/Arriendo
    if re.search(r"/inmueble/\d+-", href):
        url = f"{base}/inmueble/{cod}"
    else:
        url = urljoin(base + "/", href).rstrip("/")
    return {
        "id": f"sfm-{corto}-{cod}",
        "fuente": fuente,
        "url": url,
        "titulo": f"{tipo} en arriendo en {barrio}, {municipio}" if barrio else f"{tipo} en arriendo en {municipio}",
        "precio": a_int(precio.group(1)) if precio else None,
        "admin": None,
        "area": a_int(area.group(1)) if area else None,
        "hab": _cuenta(hab),
        "banos": _cuenta(banos),
        "parq": _cuenta(parq),
        "estrato": None,
        "municipio": municipio,
        "barrio": barrio,
        "lat": None,
        "lon": None,
        "img": img,
        "fotos": [img] if img else [],
        "anunciante": fuente,
        "publicado": None,
        # el contacto lo pone detalle() desde la propia ficha
        "tel": None,
        "wa": None,
    }


def _urls(base, modo):
    """[(municipio pedido o None, url)]"""
    if modo == "todo":
        return [(None, f"{base}/inmuebles/Arriendo/precio_0_2000000/")]
    out = []
    for muni, nuevo, viejo in MUNICIPIOS:
        if modo == "nuevo":
            out.append((muni, f"{base}/inmuebles/Arriendo/clases_Apartamento_Apartaestudio/municipios_{nuevo}/precio_0_2000000/"))
        else:
            for clase in ("Apartamento", "Apartaestudio"):
                out.append((muni, f"{base}/inmuebles/Arriendo/{clase}/{viejo}/precio_0_2000000/"))
    return out


def _hay_siguiente(texto, n):
    return re.search(r"""href=["'](?:[^"']*/)?%d["']""" % n, texto) is not None


def scrape(estado, conocidos, log):
    primera = not estado.get("ok")
    pmin, pmax = config.PRECIO_MIN_GUARDAR, config.PRECIO_MAX_GUARDAR
    for corto, base, fuente, modo in SITIOS:
        vistos = set()
        resumen = {m: [0, 0] for m, _, _ in MUNICIPIOS}
        fallos = 0
        for _pedido, url in _urls(base, modo):
            for pag in range(1, MAX_PAGINAS + 1):
                texto = get(url if pag == 1 else f"{url}{pag}")
                if not texto:
                    fallos += 1
                    break
                tarjetas = _tarjetas(texto)
                nuevas = 0
                for cod, href, b in tarjetas:
                    l = _normalizar(cod, href, b, base, corto, fuente)
                    if not l or l["id"] in vistos or l["precio"] is None:
                        continue
                    vistos.add(l["id"])
                    nuevas += 1
                    if not (pmin <= l["precio"] <= pmax):
                        continue
                    r = resumen[l["municipio"]]
                    r[0] += 1
                    if l["id"] not in conocidos:
                        r[1] += 1
                    if primera:
                        l["_inicial"] = True
                    yield l
                if not tarjetas or not nuevas or not _hay_siguiente(texto, pag + 1):
                    break
        partes = ", ".join(f"{m} {a} ({b} nuevos)" for m, (a, b) in resumen.items() if a) or "0 anuncios"
        log(f"  {NOMBRE}/{corto}: {partes}" + (f" [{fallos} páginas sin respuesta]" if fallos else ""))
    estado["ok"] = True


def _pausa(url):
    dominio = urlparse(url).netloc
    espera = config.PAUSA_SEGUNDOS - (time.time() - http._ultimo.get(dominio, 0))
    if espera > 0:
        time.sleep(espera)
    http._ultimo[dominio] = time.time()


def _digitos(s):
    d = re.sub(r"\D", "", s or "")
    return d if len(d) >= 10 else None


def detalle(url):
    """Galería, estrato, barrio y contacto (teléfono/WhatsApp que muestra la ficha)."""
    r = None
    for _ in range(2):
        _pausa(url)
        try:
            r = http._session.get(url, timeout=(5, 20), allow_redirects=False)
        except requests.RequestException as e:
            raise IOError("sin respuesta") from e
        # La Aldea responde 302 a la misma URL la primera vez (pone la cookie de sesión)
        if r.status_code in (301, 302) and r.headers.get("Location", "").rstrip("/") == url.rstrip("/"):
            continue
        break
    if r.status_code in (404, 410):
        return {"disponible": False}
    if r.status_code in (301, 302):
        loc = r.headers.get("Location", "")
        if "/Paginas" in loc or "no-se-encontro" in loc:
            return {"disponible": False}
        raise IOError(f"redirección inesperada a {loc}")
    if r.status_code != 200:
        raise IOError(f"HTTP {r.status_code}")
    r.encoding = "utf-8"
    t = r.text
    m_cod = re.search(r"/inmueble/(\d+)", url)
    cod = m_cod.group(1) if m_cod else ""

    # Galería: fotos con el prefijo (foto_{inmueble}_{empresa}_) de la primera foto de la ficha
    todas = []
    for f in re.findall(r"""((?:https?:)?//[^"'\s)]+/foto_\d+_\d+_\d+_ss\.(?:jpe?g|png|webp))""", t, re.I):
        f = _foto(f)
        if f not in todas:
            todas.append(f)
    fotos = []
    if todas:
        pref = re.search(r"/(foto_\d+_\d+_)", todas[0]).group(1)
        fotos = [f for f in todas if "/" + pref in f]

    def celda(nombre):
        m = re.search(r"<b>\s*" + nombre + r"\s*</b>\s*</td>\s*<td[^>]*>\s*([^<]+?)\s*</td>", t)
        return htmllib.unescape(m.group(1)).strip() if m else None

    estrato = a_int(celda("Estrato"))
    barrio = celda("Barrio")

    # Contacto: botón de WhatsApp de ESTE inmueble y teléfono del mismo recuadro
    wa = tel = None
    m_wa = re.search(r"api\.whatsapp\.com/send\?phone=\+?(\d+)&amp;text=[^\"']*inmueble\s+" + cod + r"\b", t)
    if m_wa:
        wa = m_wa.group(1)
        caja = t[max(0, m_wa.start() - 1500):m_wa.start()]
        caja = caja[caja.rfind("<div class=\"") if "<div class=\"" in caja else 0:]
        m_tel = re.search(r'fa-phone"></span>\s*([\d\s()+-]{7,})', caja)
        tel = _digitos(m_tel.group(1)) if m_tel else None
    else:
        # Sin recuadro (Su Vivienda): teléfono y WhatsApp del pie de página de la ficha
        m_w = re.search(r"""(?:wa\.me/|api\.whatsapp\.com/send\?phone=)\+?(\d{10,13})""", t)
        wa = m_w.group(1) if m_w else None
        m_t = re.search(r"Tel(?:é|&#233;)fonos?\s*</b>\s*([\d\s()+-]{7,})", t)
        tel = _digitos(m_t.group(1)) if m_t else None
    if wa and len(wa) == 10 and wa.startswith("3"):
        wa = "57" + wa
    out = {
        "fotos": fotos,
        "estrato": estrato if estrato and 1 <= estrato <= 6 else None,
        "barrio": _bonito(barrio) if barrio else None,
        "tel": tel,
        "wa": wa,
    }
    return {k: v for k, v in out.items() if v not in (None, "", [])}
