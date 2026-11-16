"""Espacio Urbano (espaciourbano.com): portal clásico de Medellín (ASP, HTML en latin-1).

De dónde salen los datos
- Listado por municipio y tipo: /resumen_ciudad_arriendos.asp?pCiudad={id}&pTipoInmueble={1|4}&offset=0,50,…
  (tipo 1 = apartamento, 4 = apartaestudio; 50 anuncios por página). Cada tarjeta trae id, barrio,
  línea "APARTAMENTO · EN ARRIENDO · BARRIO · MUNICIPIO · 86 M² · 1 PARQUEADERO", alcobas, baños,
  parqueaderos, área, precio ("Precio $1,900,000") y la foto principal /fotosA/{id}_1.jpg.
- Ficha (detalle): /Ficha.asp?xId={id} → galería completa (/FotosA…/FotosD…), estrato, administración
  (si la ponen), nombre del agente/inmobiliaria y su teléfono/WhatsApp, que el portal muestra
  públicamente en botones flotantes. Un id inexistente redirige (302) a index.asp → {"disponible": False}.

Estrategia (≈100 peticiones por corrida)
- El sitio no tiene filtro de precio ni de orden por URL (probado), y Medellín tiene ~112 páginas
  de apartamentos. El orden es "más reciente primero" (por id, con algunos anuncios renovados que suben).
- 1) Novedades: en cada lista se leen páginas desde offset 0 hasta una página sin ningún id mayor al
  máximo visto en la corrida anterior (tope por lista).
- Medellín (~125 páginas) NO se barre: solo sus 2 páginas más recientes por lista y corrida (prioridad
  del usuario al sur). Sus anuncios más viejos dejan de "verse" y caducan solos.
- 2) Barrido rotativo: con el resto del presupuesto se recorren todas las páginas de las listas del sur
  con un cursor global en `estado`, para refrescar el "visto" de los anuncios viejos (~2 corridas
  por ciclo completo, muy por debajo de DIAS_INACTIVO).
- El listado no da fecha de publicación → publicado=None. Sin coordenadas en ninguna página.
- Nota técnica: el sitio no declara charset; scraper.http.get() decodificaría como UTF-8 y dañaría
  las tildes, así que aquí se usa scraper.http._session con la misma pausa por dominio.
"""
import html as htmllib
import re
import time
from urllib.parse import urlparse

import requests

import config
from scraper import http
from scraper.http import GONE, a_int

NOMBRE = "espaciourbano"
FUENTE = "Espacio Urbano"
BASE = "https://www.espaciourbano.com"

# (municipio visible, pCiudad, nCiudad ya codificado como lo usa el sitio)
CIUDADES = [
    ("Envigado", 10001, "Envigado"),
    ("Sabaneta", 10005, "Sabaneta"),
    ("Itagüí", 10002, "Itag%FCi"),
    ("La Estrella", 10003, "La%20Estrella"),
    ("Medellín", 10000, "Medellin"),
]
TIPOS = [1, 4]              # 1 apartamento, 4 apartaestudio
POR_PAGINA = 50
MAX_PETICIONES = 100        # por corrida (novedades + barrido)
MAX_NOVEDADES_LISTA = 8     # páginas de novedades por lista y corrida
# Prioridad al sur: Medellín (~125 páginas) solo se lee en sus páginas más recientes, sin barrido
SOLO_RECIENTES = {"Medellín": 2}


def _listas():
    for municipio, cid, ncity in CIUDADES:
        for t in TIPOS:
            yield f"{cid}-{t}", municipio, cid, ncity, t


def _url_lista(cid, ncity, tipo, offset):
    return (f"{BASE}/resumen_ciudad_arriendos.asp?pCiudad={cid}&pTipoInmueble={tipo}"
            f"&nCiudad={ncity}&offset={offset}")


def _get(url, intentos=3, seguir=True):
    """GET decodificado en latin-1 con la pausa por dominio de scraper.http.

    Devuelve (texto, status_final) o (None, None) si falló la red; GONE en 404/410 o si
    redirige a la portada (así responde el sitio a un anuncio que ya no existe)."""
    dominio = urlparse(url).netloc
    for i in range(intentos):
        espera = config.PAUSA_SEGUNDOS - (time.time() - http._ultimo.get(dominio, 0))
        if espera > 0:
            time.sleep(espera)
        http._ultimo[dominio] = time.time()
        try:
            r = http._session.get(url, timeout=(5, 20), allow_redirects=seguir)
            if r.status_code in (301, 302) and "index.asp" in r.headers.get("Location", "").lower():
                return GONE
            if r.status_code == 200:
                return r.content.decode("latin-1", errors="replace")
            if r.status_code in (404, 410):
                return GONE
            if r.status_code == 429:
                time.sleep(min(int(r.headers.get("Retry-After", 30) or 30), 60))
            if r.status_code in (401, 403):
                raise http.BloqueadoError(f"{r.status_code} en {url}")
        except requests.RequestException:
            pass
        time.sleep(3 * (i + 1))
    return None


def _titulo_barrio(b):
    b = htmllib.unescape(b or "").strip(" -·")
    if not b:
        return None
    t = b.title()
    for p in (" De ", " Del ", " La ", " Las ", " Los ", " El ", " Y "):
        t = t.replace(p, p.lower())
    return t


def _num_icono(bloque, icono):
    m = re.search(r'fa-' + icono + r'"[^>]*></i>\s*<span[^>]*>\s*(\d+)', bloque)
    return int(m.group(1)) if m else None


def _tarjetas(texto, municipio):
    """Anuncios de una página de listado."""
    out = []
    partes = re.split(r'<div class="row" align="center">', texto)
    for b in partes[1:]:
        m = re.search(r'Ficha\.asp\?xId=(\d+)', b)
        if not m:
            continue
        aid = m.group(1)
        linea = re.search(r'<h3>\s*((?:APARTAMENTO|APARTAESTUDIO)[^<]*)</h3>', b)
        if not linea:
            continue
        campos = [c.strip() for c in htmllib.unescape(linea.group(1)).split("·")]
        tipo = "Apartaestudio" if campos[0].upper().startswith("APARTAESTUDIO") else "Apartamento"
        # El barrio sale de la línea "TIPO · EN ARRIENDO · BARRIO · MUNICIPIO · …" (el texto de
        # "Anuncio {id} - …" a veces es un título libre del anunciante)
        m_b = re.search(r'Anuncio\s+\d+\s*-\s*([^<]+)</p>', b)
        barrio = _titulo_barrio(campos[2] if len(campos) > 3 else (m_b.group(1) if m_b else None))
        m_p = re.search(r'Precio\s*\$\s*([\d,.]+)', b)
        precio = a_int(m_p.group(1)) if m_p else None
        m_a = re.search(r'(\d+)\s*M²', linea.group(1)) or re.search(r'>\s*(\d+)\s*M2<', b)
        area = int(m_a.group(1)) if m_a else None
        m_img = re.search(r'<img src="([^"]+fotos[A-D]/[^"]+)"', b, re.I)
        img = m_img.group(1) if m_img else f"{BASE}/fotosA/{aid}_1.jpg"
        out.append({
            "id": f"eu-{aid}",
            "fuente": FUENTE,
            "url": f"{BASE}/Ficha.asp?xId={aid}",
            "titulo": f"{tipo} en arriendo en {barrio}, {municipio}" if barrio else f"{tipo} en arriendo en {municipio}",
            "precio": precio,
            "admin": None,
            "area": area,
            "hab": _num_icono(b, "bed"),
            "banos": _num_icono(b, "bath"),
            "parq": _num_icono(b, "car-side"),
            "estrato": None,
            "municipio": municipio,
            "barrio": barrio,
            "lat": None,
            "lon": None,
            "img": img,
            "fotos": [img],
            "anunciante": None,
            "publicado": None,
            "tel": None,
            "wa": None,
        })
    return out


def _en_rango(l):
    p = l.get("precio")
    return p is not None and config.PRECIO_MIN_GUARDAR <= p <= config.PRECIO_MAX_GUARDAR


def detalle(url):
    """Galería completa, estrato, administración, anunciante y contacto público de la ficha."""
    m = re.search(r"xId=(\d+)", url)
    if not m:
        return {}
    aid = m.group(1)
    texto = _get(f"{BASE}/Ficha.asp?xId={aid}", seguir=False)
    if texto == GONE:
        return {"disponible": False}
    if texto is None:
        raise IOError("sin respuesta")
    if f'value="{aid}"' not in texto and f"digo {aid}" not in texto:
        raise IOError("ficha inesperada")
    fotos = list(dict.fromkeys(
        BASE + u for u in re.findall(r'<img src="(/Fotos[A-Z]/' + aid + r'_\d+\.jpg)"', texto, re.I)))

    def celda(etiqueta):
        mm = re.search(r'<td>\s*' + etiqueta + r'[^<]*</td>\s*<td>([^<]*)</td>', texto, re.I)
        return htmllib.unescape(mm.group(1)).strip() if mm else None

    estrato = a_int(celda("Estrato"))
    admin = a_int(celda("Administraci"))
    parq_txt = celda("Parqueaderos")
    out = {
        "fotos": fotos,
        "estrato": estrato if estrato and 1 <= estrato <= 6 else None,
    }
    if admin and admin > 1000:
        out["admin"] = admin
    if parq_txt is not None and re.match(r"\s*\d", parq_txt):
        out["parq"] = a_int(parq_txt.split()[0])
    # Agente / inmobiliaria (tarjeta "Agente Inmobiliario")
    i = texto.find("Agente Inmobiliario</h2>")
    if i >= 0:
        bloque = texto[i:i + 2500]
        nombre = re.search(r'<h4 class="card-title">([^<]+)</h4>', bloque)
        empresa = re.search(r'<p class="card-text">([^<]+)</p>', bloque)
        partes = [htmllib.unescape(x.group(1)).strip() for x in (empresa, nombre) if x and x.group(1).strip()]
        if partes:
            out["anunciante"] = partes[0]
    m_wa = re.search(r'api\.whatsapp\.com/send\?phone=(\d+)', texto)
    m_tel = re.search(r'href="tel:\+?(\d+)"', texto)
    if m_wa:
        out["wa"] = m_wa.group(1)
    if m_tel:
        out["tel"] = m_tel.group(1)
    return out


def scrape(estado, conocidos, log):
    primera = not estado.get("listas")
    listas_est = estado.setdefault("listas", {})
    peticiones = 0
    vistos = set()
    resumen = {}

    def leer(clave, municipio, cid, ncity, tipo, offset):
        nonlocal peticiones
        peticiones += 1
        texto = _get(_url_lista(cid, ncity, tipo, offset))
        if texto is None or texto == GONE:
            return None, None
        m = re.search(r"P[aá]gina\s+\d+\s*/\s*(\d+)", texto)
        paginas = int(m.group(1)) if m else None
        return _tarjetas(texto, municipio), paginas

    def emitir(clave, municipio, anuncios):
        r = resumen.setdefault(municipio, [0, 0])
        for l in anuncios:
            if l["id"] in vistos:
                continue
            vistos.add(l["id"])
            r[0] += 1
            if not _en_rango(l):
                continue
            if l["id"] not in conocidos:
                r[1] += 1
            if primera:
                l["_inicial"] = True
            yield l

    # 1) Novedades: desde offset 0 hasta una página sin ids mayores al máximo anterior
    for clave, municipio, cid, ncity, tipo in _listas():
        est = listas_est.setdefault(clave, {})
        max_prev = est.get("max", 0)
        max_nuevo = max_prev
        tope = SOLO_RECIENTES.get(municipio, MAX_NOVEDADES_LISTA)
        for pagina in range(tope):
            if peticiones >= MAX_PETICIONES:
                break
            anuncios, paginas = leer(clave, municipio, cid, ncity, tipo, pagina * POR_PAGINA)
            if anuncios is None:
                break
            if paginas is not None:
                est["paginas"] = paginas
            ids = [int(l["id"][3:]) for l in anuncios]
            yield from emitir(clave, municipio, anuncios)
            if ids:
                max_nuevo = max(max_nuevo, max(ids))
            if municipio in SOLO_RECIENTES:
                if pagina + 1 >= (paginas or 0):
                    break
                continue   # Medellín: siempre sus primeras páginas (no hay barrido que refresque)
            if not ids or not max_prev or max(ids) <= max_prev or pagina + 1 >= (paginas or 0):
                break
        est["max"] = max_nuevo

    # 2) Barrido rotativo de todas las páginas de todas las listas
    orden = [x for x in _listas() if x[1] not in SOLO_RECIENTES]
    cur = estado.get("cursor") or [0, 1]   # [índice de lista, página] (la página 0 ya la leen las novedades)
    li, pg = cur
    vueltas = 0
    while peticiones < MAX_PETICIONES and vueltas <= len(orden):
        if li >= len(orden):
            li, pg = 0, 1
            estado["ciclos"] = estado.get("ciclos", 0) + 1
            break   # ciclo completo: el siguiente empieza en la próxima corrida
        clave, municipio, cid, ncity, tipo = orden[li]
        paginas = listas_est.get(clave, {}).get("paginas") or 1
        if pg >= paginas:
            li, pg = li + 1, 1
            vueltas += 1
            continue
        anuncios, _ = leer(clave, municipio, cid, ncity, tipo, pg * POR_PAGINA)
        if anuncios is None:
            break   # fallo temporal: se retoma en la misma página en la próxima corrida
        yield from emitir(clave, municipio, anuncios)
        pg += 1
        vueltas = 0
    estado["cursor"] = [li, pg]

    for municipio, (leidos, nuevos) in resumen.items():
        log(f"  espaciourbano/{municipio}: {leidos} leídos, {nuevos} nuevos en rango")
    log(f"  espaciourbano: {peticiones} peticiones, cursor {estado['cursor']}")
