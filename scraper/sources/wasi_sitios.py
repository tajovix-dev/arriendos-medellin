"""Inmobiliarias con web de Wasi (una sola fuente para varios sitios).

Sitios
- La Palma Inmobiliaria (lapalmainmobiliaria.com.co) y Urve Inmobiliaria (urve.com.co): plantilla
  estándar de Wasi. robots.txt solo prohíbe /get-pdf/ (no se pide).
- Alnago (alnago.com): interfaz propia (Next.js) sobre inventario Wasi (mismos códigos). robots.txt
  prohíbe /api/ y /_next/: no se usan; solo sitemap.xml y las fichas /es/inmueble/{código}.
- NO se incluye Inmobiliaria Proteger: su robots.txt bloquea bots de IA y declara ai-train=no.

De dónde salen los datos
- Plantilla Wasi: {base}/search?business_type[0]=for_rent&for_rent=1&order_by=created_at&order=desc
  &max_price=2000000&page=N (12 tarjetas por página, el filtro de precio funciona; tipos y
  municipios se filtran aquí). Tarjeta: enlace /{tipo}-{arriendo|alquiler}-{barrio}-{ciudad}/{código},
  "Ciudad, Antioquia, Colombia", precio, alcobas, baños, parqueaderos, área y foto principal.
  La ficha (solo se visita la de anuncios nuevos; queda en caché en `estado`) da zona/barrio,
  estrato, coordenadas (JSON-LD geo), galería (enlaces a image.wasi.co) y el asesor del anuncio
  (enlace tel: del bloque "Contáctanos" y el wa.me del botón de WhatsApp de la ficha).
- Alnago: sitemap.xml lista ~270 fichas (venta y arriendo mezclados) con lastmod. Cada ficha trae
  JSON-LD (precio, ciudad, coordenadas, fotos) y el objeto "property" del payload RSC de la página
  (tipo, operación, ciudad, barrio, alcobas, baños, garajes, área). Se guarda en `estado` lo visto
  de cada código (lastmod, fecha de foto y, si es un apartamento en arriendo, sus datos) y solo se
  visitan códigos nuevos o con lastmod cambiado, del más nuevo al más viejo, dentro del presupuesto.
- Teléfonos: el del asesor de la ficha cuando la ficha lo muestra; si no, la línea de la oficina
  visible en el sitio (constantes abajo, con la página donde se ve).

Fecha de publicación (publicado)
- Wasi no muestra fecha. El nombre de las fotos sí trae la hora de carga (g5025...20261111105729 o
  g_..._1794667830-...). Eso NO siempre es la fecha del anuncio: hay anuncios con fotos copiadas de
  otro más viejo o fotos cambiadas meses después. Como los códigos Wasi son un contador global (se
  asignan al crear el anuncio), se toma la fecha de la foto principal solo si es coherente con el
  orden de los códigos: se calcula la cadena no decreciente más larga (código ↑ ⇒ fecha ↑) con
  todos los anuncios vistos de los tres sitios y solo los de esa cadena, con un vecino más viejo
  a ≤ 30 días, reciben `publicado`. El resto queda en None.

Limitaciones
- San Antonio de Prado (corregimiento de Medellín) se guarda como Medellín, barrio "San Antonio de Prado".
- Presupuesto ~150 peticiones y ~3,5 min por corrida para todo el módulo: en la primera corrida
  Alnago no se alcanza a leer completo; sigue en las siguientes (del código más nuevo al más viejo).
- Sin detalle(): todo lo de la ficha se toma aquí la primera vez; en corridas siguientes se
  vuelve a emitir lo del listado/caché (sin fotos, que el sistema ya guardó).
"""
import base64
import html as htmllib
import json
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone

import config
from scraper.http import GONE, a_int, get, json_ld, rsc_payload

NOMBRE = "wasi_sitios"

# Líneas de oficina publicadas en cada sitio (se usan solo si la ficha no muestra un asesor)
LAPALMA_TEL = "576044481900"   # "TELÉFONO +576044481900" en el pie de https://lapalmainmobiliaria.com.co/search
LAPALMA_WA = "573244290040"    # wa.me/573244290040 en https://lapalmainmobiliaria.com.co/search
URVE_TEL = "573137397226"      # "TELÉFONO +573137397226" en el pie de https://urve.com.co/search
URVE_WA = "573137397226"       # wa.me/573137397226 en https://urve.com.co/search
ALNAGO_TEL = "573015513657"    # "telephone" del JSON-LD RealEstateAgent en https://alnago.com/es/inmueble/{código}
ALNAGO_WA = "573015513657"     # botón "Escribir por WhatsApp" (wa.me/573015513657) de cada ficha de alnago.com

SITIOS = [
    # (corto, base, nombre visible, tel oficina, wa oficina)
    ("lapalma", "https://lapalmainmobiliaria.com.co", "La Palma Inmobiliaria", LAPALMA_TEL, LAPALMA_WA),
    ("urve", "https://urve.com.co", "Urve Inmobiliaria", URVE_TEL, URVE_WA),
]
ALNAGO = ("alnago", "https://alnago.com", "Alnago")

MAX_PETICIONES = 145
MAX_SEGUNDOS = 210
MAX_PAGINAS = 12
SUR = ("Sabaneta", "Envigado", "Itagüí", "La Estrella")


def _llano(s):
    s = unicodedata.normalize("NFD", s or "")
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn")).strip().lower()


CIUDADES = {"medellin": "Medellín", "sabaneta": "Sabaneta", "envigado": "Envigado", "itagui": "Itagüí",
            "la estrella": "La Estrella", "san antonio de prado": "Medellín"}


def _municipio(ciudad):
    return CIUDADES.get(_llano(ciudad))


def _bonito(s):
    s = re.sub(r"\s+", " ", htmllib.unescape(s or "")).strip(" .,-")
    if not s:
        return None
    s = s.title()
    for p in (" De ", " Del ", " La ", " Las ", " Los ", " El ", " Y "):
        s = s.replace(p, p.lower())
    return s


def _barrio(barrio, ciudad, municipio):
    if _llano(ciudad) == "san antonio de prado":
        return "San Antonio de Prado"
    b = _bonito((barrio or "").split(",")[0])   # "Buenos Aires,Medellin" -> "Buenos Aires"
    if b and _llano(b) in (_llano(municipio), _llano(ciudad)):
        return None
    return b


def _tipo(t):
    t = _llano(t)
    if t.startswith("apartaestudio"):
        return "Apartaestudio"
    if t.startswith("apartamento"):
        return "Apartamento"
    return None


def _tel(d):
    d = re.sub(r"\D", "", d or "")
    if len(d) == 10 and d[0] in "36":
        d = "57" + d
    return d if len(d) == 12 and d.startswith("57") else None


def _fecha_foto(url):
    """Fecha (YYYY-MM-DD) de carga escrita en el nombre de la foto de image.wasi.co, o None."""
    m = re.search(r"image\.wasi\.co/([A-Za-z0-9+/=_-]+)", url or "")
    if not m:
        return None
    try:
        crudo = m.group(1).replace("-", "+").replace("_", "/")
        clave = json.loads(base64.b64decode(crudo + "=" * (-len(crudo) % 4)))["key"]
    except (ValueError, KeyError, TypeError):
        return None
    nombre = clave.rsplit("/", 1)[-1]
    m = re.match(r"g\d+?(20\d{2})(\d{2})(\d{2})\d{6}\.", nombre)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = re.search(r"_(1[4-9]\d{8})-\d+_\d+\.", nombre)
    if m:
        return datetime.fromtimestamp(int(m.group(1)), timezone(timedelta(hours=-5))).date().isoformat()
    return None


def _fechas_coherentes(puntos):
    """{código: fecha} solo para las fechas coherentes con el orden de los códigos (ver docstring)."""
    hoy = date.today().isoformat()
    pts = sorted((c, f) for c, f in puntos.items() if f and f <= hoy)
    n = len(pts)
    if n < 3:
        return {}
    # Cadena no decreciente más larga (O(n²), n ~ unos cientos)
    largo, previo = [1] * n, [-1] * n
    for i in range(n):
        for j in range(i):
            if pts[j][1] <= pts[i][1] and largo[j] + 1 > largo[i]:
                largo[i], previo[i] = largo[j] + 1, j
    i = max(range(n), key=lambda k: largo[k])
    cadena = []
    while i >= 0:
        cadena.append(i)
        i = previo[i]
    cadena.reverse()
    out = {}
    for k in range(1, len(cadena)):
        c, f = pts[cadena[k]]
        f_viejo = pts[cadena[k - 1]][1]
        if (date.fromisoformat(f) - date.fromisoformat(f_viejo)).days <= 30:
            out[c] = f
    return out


class _Presupuesto:
    def __init__(self):
        self.n = 0
        self.t0 = time.time()

    def queda(self):
        return self.n < MAX_PETICIONES and time.time() - self.t0 < MAX_SEGUNDOS

    def get(self, url):
        self.n += 1
        return get(url, si_404=GONE)


# ---------------------------------------------------------------- plantilla Wasi (/search)

def _tarjetas(texto, base):
    out = []
    for b in texto.split('<div class="item ')[1:]:
        m = re.search(r'href="(' + re.escape(base) + r'/([a-z0-9-]+)/(\d+))"', b)
        if not m:
            continue
        ubic = re.search(r'class="ubicacion[^"]*">\s*([^<]+)', b)
        precio = re.search(r'<p class="[^"]*t8-title">\s*\$\s*([\d.,]+)', b)

        def dato(patron):
            x = re.search(r"<strong>(\d+)</strong>\s*" + patron, b)
            return int(x.group(1)) if x else None

        img = re.search(r'<img[^>]+src="(https://image\.wasi\.co/[^"]+)"', b)
        out.append({
            "codigo": int(m.group(3)), "url": m.group(1), "slug": m.group(2),
            "ciudad": ubic.group(1).split(",")[0].strip() if ubic else "",
            "precio": a_int(precio.group(1)) if precio else None,
            "hab": dato("Alcoba"), "banos": dato("Ba"), "parq": dato("Parq"), "area": dato("\\S*rea"),
            "img": img.group(1) if img else None,
        })
    return out


def _ficha_wasi(texto):
    """Datos de la ficha estándar de Wasi."""
    def campo(nombre):
        m = re.search(r"<strong>" + nombre + r":</strong>\s*([^<]*)</li>", texto)
        return htmllib.unescape(m.group(1)).strip() if m else None

    d = {"zona": campo("Zona(?: / barrio)?"), "ciudad": campo("Ciudad"), "tipo": campo("Tipo de inmueble"),
         "estrato": a_int(campo("Estrato"))}
    if d["estrato"] and not 1 <= d["estrato"] <= 6:
        d["estrato"] = None
    lat = lon = None
    for j in json_ld(texto):
        g = j.get("geo") if isinstance(j, dict) else None
        if isinstance(g, dict):
            try:
                lat, lon = float(g.get("latitude")), float(g.get("longitude"))
            except (TypeError, ValueError):
                pass
    if not (lat and 6.0 < lat < 6.45 and lon and -75.8 < lon < -75.4):
        lat = lon = None
    d["lat"], d["lon"] = lat, lon
    # Asesor del anuncio: primer tel: del bloque de contacto (antes del pie) y botón de WhatsApp
    m = re.search(r'class="text-primary" href="tel:([+\d]+)"', texto)
    d["tel"] = _tel(m.group(1)) if m else None
    m = re.search(r"wa\.me/(\d+)\?text=C", texto)
    d["wa"] = _tel(m.group(1)) if m else None
    fotos = []
    for u in re.findall(r'<a[^>]+href="(https://image\.wasi\.co/[^"]+)"', texto):
        if u not in fotos:
            fotos.append(u)
    d["fotos"] = fotos
    return d


def _sitio_wasi(corto, base, fuente, tel_of, wa_of, cache, pres, conocidos, puntos, log):
    """Lee el listado y las fichas nuevas. Devuelve lista de (anuncio, código, municipio)."""
    tarjetas, completo = [], True
    for p in range(1, MAX_PAGINAS + 1):
        url = (f"{base}/search?business_type%5B0%5D=for_rent&for_rent=1&order_by=created_at&order=desc"
               f"&max_price={config.PRECIO_MAX_GUARDAR}&page={p}")
        texto = pres.get(url)
        if not texto or texto == GONE:
            completo = False
            break
        t = _tarjetas(texto, base)
        if not t:
            break
        tarjetas += t
    else:
        completo = False
    for t in tarjetas:
        f = _fecha_foto(t["img"])
        if f:
            puntos[t["codigo"]] = f
    pmin, pmax = config.PRECIO_MIN_GUARDAR, config.PRECIO_MAX_GUARDAR
    utiles = []
    for t in tarjetas:
        tipo = _tipo(t["slug"])
        municipio = _municipio(t["ciudad"])
        if not tipo or not municipio or not t["precio"]:
            continue
        id_ = f"ws-{corto}-{t['codigo']}"
        if not (pmin <= t["precio"] <= pmax) and id_ not in conocidos:
            continue
        utiles.append((t, tipo, municipio, id_))
    # Fichas nuevas: primero el sur, luego lo más nuevo
    utiles.sort(key=lambda x: (x[2] in SUR, x[0]["codigo"]), reverse=True)
    salida = []
    for t, tipo, municipio, id_ in utiles:
        c = str(t["codigo"])
        fotos = []
        if c not in cache and pres.queda():
            texto = pres.get(t["url"])
            if texto == GONE:
                continue
            if texto:
                d = _ficha_wasi(texto)
                fotos = d["fotos"]
                cache[c] = {"b": _barrio(d["zona"], d["ciudad"] or t["ciudad"], municipio),
                            "e": d["estrato"], "la": d["lat"], "lo": d["lon"], "t": d["tel"], "w": d["wa"]}
        x = cache.get(c) or {}
        if x:
            barrio = x.get("b")
        else:
            # Ficha aún sin visitar: barrio desde el enlace (/{tipo}-{negocio}-{barrio}-{ciudad}/)
            resto = re.sub(r"^[a-z]+-(arriendo|alquiler)-", "", t["slug"])
            slug_c = re.sub(r"[^a-z0-9]+", "-", _llano(t["ciudad"]))
            barrio = _barrio(resto[: -len(slug_c) - 1].replace("-", " ") if resto.endswith("-" + slug_c) else "",
                             t["ciudad"], municipio)
        img = t["img"]
        salida.append(({
            "id": id_, "fuente": fuente, "url": t["url"],
            "titulo": f"{tipo} en arriendo en {barrio}, {municipio}" if barrio else f"{tipo} en arriendo en {municipio}",
            "precio": t["precio"], "admin": None, "area": t["area"] or None, "hab": t["hab"],
            "banos": t["banos"], "parq": t["parq"], "estrato": x.get("e"), "municipio": municipio,
            "barrio": barrio, "lat": x.get("la"), "lon": x.get("lo"),
            "img": fotos[0] if fotos else img, "fotos": fotos or ([img] if img else []),
            "anunciante": fuente, "publicado": None,
            "tel": x.get("t") or tel_of, "wa": x.get("w") or wa_of,
        }, t["codigo"], municipio))
    if completo:
        vigentes = {str(t["codigo"]) for t in tarjetas}
        for c in list(cache):
            if c not in vigentes:
                del cache[c]
    return salida


# ---------------------------------------------------------------- Alnago

def _ficha_alnago(texto):
    listing = None
    for j in json_ld(texto):
        if isinstance(j, dict) and j.get("@type") == "RealEstateListing":
            listing = j
    prop = None
    payload = rsc_payload(texto)
    i = payload.find('"property":{"slug"')
    if i >= 0:
        try:
            prop, _ = json.JSONDecoder().raw_decode(payload, i + len('"property":'))
        except json.JSONDecodeError:
            prop = None
    if not listing and not prop:
        return None
    listing = listing or {}
    prop = prop or {}
    oferta = listing.get("offers") or {}
    sobre = listing.get("about") or {}
    fotos = listing.get("image") or []
    fotos = [f for f in ([fotos] if isinstance(fotos, str) else fotos) if isinstance(f, str) and f.startswith("http")]
    coord = prop.get("coordenadas") or {}
    geo = sobre.get("geo") or {}
    try:
        lat = float(coord.get("lat") or geo.get("latitude"))
        lon = float(coord.get("lng") or geo.get("longitude"))
    except (TypeError, ValueError):
        lat = lon = None
    if not (lat and 6.0 < lat < 6.45 and lon and -75.8 < lon < -75.4):
        lat = lon = None
    funcion = str(oferta.get("businessFunction") or "")
    operacion = _llano(prop.get("operacion")) or ("arriendo" if funcion.endswith("LeaseOut") else "")
    disp = str(oferta.get("availability") or "")
    m = re.search(r"wa\.me/(\d+)\?text=Hola%20Me%20gustaria", texto)
    return {
        "tipo": _tipo(prop.get("tipo") or listing.get("name") or ""),
        "arriendo": operacion == "arriendo",
        "ciudad": prop.get("ciudad") or (sobre.get("address") or {}).get("addressLocality") or "",
        "barrio": prop.get("barrio"),
        "precio": a_int(prop.get("precio") or oferta.get("price")),
        "admin": a_int(prop.get("administracion")) or None,
        "area": a_int(prop.get("areaPrivada") or prop.get("area") or (sobre.get("floorSize") or {}).get("value")) or None,
        "hab": a_int(prop.get("habitaciones") if prop.get("habitaciones") is not None else sobre.get("numberOfRooms")),
        "banos": a_int(prop.get("banos") if prop.get("banos") is not None else sobre.get("numberOfBathroomsTotal")),
        "parq": a_int(prop.get("garajes") if prop.get("garajes") is not None else prop.get("parqueaderos")),
        "lat": lat, "lon": lon, "fotos": fotos,
        "wa": _tel(m.group(1)) if m else None,
        "agotado": bool(disp) and not disp.rstrip("/").endswith("InStock"),
    }


def _alnago(cache, pres, conocidos, puntos, log, extra=()):
    corto, base, fuente = ALNAGO
    sm = pres.get(f"{base}/sitemap.xml")
    if not sm or sm == GONE:
        lista = None
    else:
        lista = dict(re.findall(r"<loc>" + re.escape(base) + r"/es/inmueble/(\d+)</loc>\s*<lastmod>([^<]+)</lastmod>", sm))
        # El sitemap de Alnago no trae todo su inventario. Los portales donde Alnago publica dan su código
        # (el mismo de la ficha /es/inmueble/{código}): esas fichas también se leen
        for c in extra:
            lista.setdefault(str(c), "portal")
    pmin, pmax = config.PRECIO_MIN_GUARDAR, config.PRECIO_MAX_GUARDAR
    fotos_de = {}
    if lista:
        for c in list(cache):
            if c not in lista:
                del cache[c]
        pendientes = [c for c in lista if c not in cache or cache[c][0] != lista[c]]
        extra_s = {str(c) for c in extra}
        # Primero las que un portal confirma activas, luego las nunca vistas (de la más nueva)
        pendientes.sort(key=lambda c: (c not in extra_s, c in cache, -int(c)))
        for c in pendientes:
            if not pres.queda():
                break
            texto = pres.get(f"{base}/es/inmueble/{c}")
            if texto == GONE:
                cache.pop(c, None)
                continue
            if not texto:
                continue
            d = _ficha_alnago(texto)
            if d is None:
                continue
            f = _fecha_foto(d["fotos"][0]) if d["fotos"] else None
            rec = None
            municipio = _municipio(d["ciudad"])
            if d["tipo"] and d["arriendo"] and municipio and d["precio"]:
                rec = {k: d[k] for k in ("tipo", "precio", "admin", "area", "hab", "banos", "parq", "lat", "lon", "wa")}
                rec.update({"m": municipio, "b": _barrio(d["barrio"], d["ciudad"], municipio),
                            "img": d["fotos"][0] if d["fotos"] else None, "x": d["agotado"]})
                fotos_de[c] = d["fotos"]
            cache[c] = [lista[c], f or "", rec]
    for c, v in cache.items():
        if v[1]:
            puntos[int(c)] = v[1]
    salida = []
    for c, (_, _, rec) in cache.items():
        if not rec:
            continue
        id_ = f"ws-{corto}-{c}"
        if not (pmin <= rec["precio"] <= pmax) and id_ not in conocidos:
            continue
        b, m = rec["b"], rec["m"]
        fotos = fotos_de.get(c) or ([rec["img"]] if rec["img"] else [])
        l = {
            "id": id_, "fuente": fuente, "url": f"{base}/es/inmueble/{c}",
            "titulo": f"{rec['tipo']} en arriendo en {b}, {m}" if b else f"{rec['tipo']} en arriendo en {m}",
            "precio": rec["precio"], "admin": rec["admin"], "area": rec["area"], "hab": rec["hab"],
            "banos": rec["banos"], "parq": rec["parq"], "estrato": None, "municipio": m, "barrio": b,
            "lat": rec["lat"], "lon": rec["lon"], "img": rec["img"], "fotos": fotos,
            "anunciante": fuente, "publicado": None, "tel": ALNAGO_TEL, "wa": rec["wa"] or ALNAGO_WA,
        }
        if rec.get("x"):
            l["disponible"] = False
        salida.append((l, int(c), m))
    pend = sum(1 for c in (lista or {}) if c not in cache or cache[c][0] != lista[c])
    if pend:
        log(f"  {NOMBRE}/{corto}: {pend} fichas del sitemap pendientes para la próxima corrida")
    return salida


def scrape(estado, conocidos, log):
    primera = not estado.get("ok")
    pres = _Presupuesto()
    puntos = {}
    cache = estado.setdefault("fichas", {})
    resultados = []
    for corto, base, fuente, tel_of, wa_of in SITIOS:
        resultados.append((corto, _sitio_wasi(corto, base, fuente, tel_of, wa_of, cache.setdefault(corto, {}),
                                              pres, conocidos, puntos, log)))
    resultados.append((ALNAGO[0], _alnago(cache.setdefault(ALNAGO[0], {}), pres, conocidos, puntos, log,
                                          extra=estado.get("extra_alnago") or ())))
    fechas = _fechas_coherentes(puntos)
    pmin, pmax = config.PRECIO_MIN_GUARDAR, config.PRECIO_MAX_GUARDAR
    for corto, lista in resultados:
        resumen = {}
        for l, codigo, municipio in lista:
            l["publicado"] = fechas.get(codigo)
            if pmin <= l["precio"] <= pmax:
                r = resumen.setdefault(municipio, [0, 0])
                r[0] += 1
                if l["id"] not in conocidos:
                    r[1] += 1
            if primera:
                l["_inicial"] = True
            yield l
        log(f"  {NOMBRE}/{corto}: " + (", ".join(f"{m} {a} ({b} nuevos)" for m, (a, b) in resumen.items()) or "0"))
    log(f"  {NOMBRE}: {pres.n} peticiones, {time.time() - pres.t0:.0f} s")
    estado["ok"] = True
