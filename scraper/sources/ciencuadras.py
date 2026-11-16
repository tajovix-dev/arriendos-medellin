"""Ciencuadras: listados HTML (Angular SSR) con el estado embebido en <script id="result-state">.

De dónde salen los datos
- URL de listado permitida por robots.txt:
  /arriendo/{apartamento|apartaestudio}/{municipio}/desde-X/hasta-Y
  El estado viene con entidades (&q; &a; ...) y una clave "results-<ruta>" con total y results[]
  (se ignoran los "highlights", que son destacados pagados de otras búsquedas).
- Cada URL devuelve solo 20 resultados y la paginación no responde a la URL (el orden "más
  reciente" tampoco: se hace en el navegador). Por eso se parte el rango de precio en bandas
  y se subdivide cada banda mientras tenga más de 20 anuncios. Las bandas calculadas se
  guardan en el estado y se reutilizan; una banda que creció por encima de 20 se vuelve a
  partir y las bandas vecinas que quedaron muy pequeñas se juntan.
- Sur (Sabaneta, Envigado, Itagüí, La Estrella): barrido completo de todas las bandas en cada
  corrida (unas 60-90 peticiones). Medellín: solo una petición por tipo (los 20 primeros que
  ordena el portal, por relevancia, no por fecha).
- "createdAt" coincide con "publicationDate" de la ficha. Aunque termina en "Z", las horas
  (07-22 h) corresponden a la hora de Colombia, así que se toma la fecha tal cual.
- detalle(url): galería completa (gallery-state → flatPhotos) de la ficha.

Limitaciones
- Si una banda no tiene anuncios, el portal ignora el filtro y devuelve todo el inventario: se
  detecta porque los precios devueltos quedan fuera de la banda, y se trata como vacía.
- Si un mismo precio exacto (p. ej. 1.500.000) tiene más de 20 anuncios no se puede partir
  más por precio: de esa banda solo se ven 20 (los que el portal pone primero).
- Si Cloudflare responde con un reto (no hay result-state), se registra y se termina sin error.
"""
import json
import re
import unicodedata

from scraper.http import GONE, get, a_int

NOMBRE = "ciencuadras"
FUENTE = "Ciencuadras"
BASE = "https://www.ciencuadras.com"

SUR = {"Sabaneta": "sabaneta", "Envigado": "envigado", "Itagüí": "itagui", "La Estrella": "la-estrella"}
MEDELLIN = {"Medellín": "medellin"}
TIPOS = ("apartamento", "apartaestudio")
MIN, MAX = 300_000, 2_000_000
POR_PAGINA = 20
MAX_PETICIONES = 140          # tope de seguridad por corrida
JUNTAR_HASTA = 14             # bandas vecinas se juntan si suman hasta este total

_ENT = [("&q;", '"'), ("&s;", "'"), ("&l;", "<"), ("&g;", ">"), ("&a;", "&")]


def _sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower().strip()


def _estado_html(html, script_id):
    m = re.search(r'<script id="' + script_id + r'"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    s = m.group(1)
    for a, b in _ENT:
        s = s.replace(a, b)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _datos(html):
    """(total, results) o None si la página no trae el estado (reto de Cloudflare, error...)."""
    d = _estado_html(html, "result-state")
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if k.startswith("results-") and isinstance(v, dict):
            data = v.get("data") or {}
            return int(data.get("total") or 0), [r for r in data.get("results") or [] if isinstance(r, dict)]
    return None


_MINUSC = {"De", "Del", "La", "Las", "Los", "El", "Y", "En"}


def _titulo(s):
    s = re.sub(r"\s+", " ", (s or "").strip())
    if not s:
        return None
    if s.isupper() or s.islower():
        s = s.title()
    pal = s.split(" ")
    return " ".join([pal[0]] + [p.lower() if p in _MINUSC else p for p in pal[1:]])


def _barrio(r, municipio):
    for k in ("neighborhood_integrator", "neighborhoodUser", "neighborhood"):
        v = (r.get(k) or "").strip()
        if v and _sin_tildes(v) not in (_sin_tildes(municipio), "otras", "otro") and len(v) > 2:
            return _titulo(v)
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _normalizar(r, municipio):
    co = r.get("coordinates") or {}
    lat, lon = _num(co.get("latitude")), _num(co.get("longitude"))
    if not (lat and 5.9 < lat < 6.5 and lon and -75.9 < lon < -75.3):
        lat = lon = None
    url = r.get("url") or ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", r.get("createdAt") or "")
    img = r.get("image")
    estrato = a_int(r.get("stratum"))
    tipo = r.get("realEstateType") or "Apartamento"
    barrio = _barrio(r, municipio)
    tel = re.sub(r"\D", "", str(r.get("contactPhone") or "")) or None
    wa = re.sub(r"\D", "", str(r.get("contactWhatsapp") or "")) or None
    return {
        "id": f"cc-{r.get('id')}",
        "fuente": FUENTE,
        "url": BASE + url if url.startswith("/") else url,
        "titulo": f"{tipo} en arriendo" + (f", {barrio}" if barrio else "") + f", {municipio}",
        "precio": a_int(r.get("rentPrice")),
        "admin": a_int(r.get("adminValue")) or None,
        "area": int(_num(r.get("area")) or 0) or None,
        "hab": a_int(r.get("rooms")),
        "banos": a_int(r.get("baths")),
        "parq": a_int(r.get("garages")),
        "estrato": estrato if estrato and 1 <= estrato <= 6 else None,
        "municipio": municipio,
        "barrio": barrio,
        "lat": lat,
        "lon": lon,
        "img": img,
        "fotos": [img] if img else [],
        "anunciante": (r.get("userName") or "").strip() or None,
        "publicado": m.group(1) if m else None,
        "tel": tel,
        "wa": wa,
    }


def _url(tipo, slug, lo, hi):
    return f"{BASE}/arriendo/{tipo}/{slug}/desde-{lo}/hasta-{hi}"


def _bandas_iniciales():
    """300k-999.999 y luego tramos de 100k hasta 2M (los cánones se concentran en 1-2M)."""
    out = [[MIN, 999_999]]
    for a in range(1_000_000, MAX, 100_000):
        out.append([a, min(a + 99_999, MAX)])
    out[-1][1] = MAX
    return out


def _partir(lo, hi):
    """Parte una banda con más de 20 anuncios. Los cánones se repiten en valores redondos
    (1.500.000, 1.600.000…), así que el valor redondo queda siempre al inicio de una banda y,
    si la banda ya es angosta, se aísla solo ese precio. None si no se puede partir más."""
    if hi - lo < 2_000:
        return None
    if hi - lo + 1 > 50_000:
        mid = (lo + hi + 1) // 2 // 10_000 * 10_000
        if not lo < mid <= hi:
            mid = (lo + hi + 1) // 2
        return [[lo, mid - 1], [mid, hi]]
    if lo % 10_000 == 0:
        return [[lo, lo], [lo + 1, hi]]
    mid = (lo + hi + 1) // 2
    return [[lo, mid - 1], [mid, hi]]


def _juntar(hojas):
    """Junta bandas vecinas pequeñas para ahorrar peticiones en la próxima corrida."""
    out = []
    for lo, hi, n in sorted(hojas):
        if out and out[-1][1] + 1 >= lo and out[-1][2] + n <= JUNTAR_HASTA:
            out[-1] = [out[-1][0], max(out[-1][1], hi), out[-1][2] + n]
        else:
            out.append([lo, hi, n])
    return out


class _Bloqueo(Exception):
    pass


def scrape(estado, conocidos, log):
    inicial = not estado.get("ciclos")
    cont = {"pet": 0}
    vistos = set()

    def pedir(url, medellin=False):
        if cont["pet"] >= MAX_PETICIONES - (0 if medellin else 2):   # 2 reservadas para Medellín
            return None
        cont["pet"] += 1
        html = get(url, si_404=GONE)
        if html is None or html == GONE:
            return None
        d = _datos(html)
        if d is None:
            raise _Bloqueo(url)
        return d

    def emitir(results, municipio, stats):
        for r in results:
            if _sin_tildes(r.get("city")) != _sin_tildes(municipio) or not r.get("id"):
                continue
            if (r.get("offerType") or "Arriendo") != "Arriendo":
                continue
            l = _normalizar(r, municipio)
            if l["id"] in vistos or not l["precio"] or not MIN <= l["precio"] <= MAX:
                continue
            vistos.add(l["id"])
            if inicial:
                l["_inicial"] = True
            stats[0] += 1
            if l["id"] not in conocidos:
                stats[1] += 1
            yield l

    bandas = estado.setdefault("bandas", {})
    completo = True
    # Si una corrida no alcanzó a terminar (tope de peticiones), la siguiente empieza por el
    # municipio que quedó a medias, para que ninguno se quede sin revisar.
    orden = list(SUR.items())
    ini = estado.get("inicio", 0) % len(orden)
    orden = orden[ini:] + orden[:ini]
    pendiente_desde = None
    try:
        # --- Sur: barrido completo por bandas de precio ---
        for municipio, slug in orden:
            stats = [0, 0]
            antes = cont["pet"]
            for tipo in TIPOS:
                clave = f"{tipo}/{slug}"
                pendientes = [h[:2] for h in bandas.get(clave) or _bandas_iniciales()]
                hojas = []
                while pendientes:
                    lo, hi = pendientes.pop(0)
                    d = pedir(_url(tipo, slug, lo, hi))
                    if d is None:   # fallo temporal o tope: se conserva la banda tal cual
                        completo = False
                        if pendiente_desde is None:
                            pendiente_desde = list(SUR).index(municipio)
                        hojas.append([lo, hi, POR_PAGINA])
                        continue
                    total, results = d
                    # Si la banda no tiene anuncios, el portal ignora el filtro y devuelve todo el
                    # inventario (miles, a cualquier precio): eso cuenta como banda vacía.
                    if any(not lo <= (a_int(r.get("rentPrice")) or 0) <= hi for r in results):
                        total, results = 0, []
                    yield from emitir(results, municipio, stats)
                    hijos = _partir(lo, hi) if total > POR_PAGINA else None
                    if hijos:
                        pendientes[0:0] = hijos
                    else:
                        hojas.append([lo, hi, total])
                bandas[clave] = _juntar(hojas)
            log(f"  ciencuadras/{slug}: {stats[0]} leídos, {stats[1]} nuevos, {cont['pet'] - antes} peticiones")
        # --- Medellín: una petición por tipo (sin barrido) ---
        for municipio, slug in MEDELLIN.items():
            stats = [0, 0]
            for tipo in TIPOS:
                d = pedir(f"{BASE}/arriendo/{tipo}/{slug}/hasta-{MAX}", medellin=True)
                if d:
                    yield from emitir(d[1], municipio, stats)
            log(f"  ciencuadras/{slug}: {stats[0]} leídos, {stats[1]} nuevos (solo primera página)")
    except _Bloqueo as e:
        log(f"  ciencuadras: la página no trae datos (¿reto de Cloudflare?) en {e}; se termina sin error")
        return
    estado["inicio"] = pendiente_desde or 0
    if completo:
        estado["ciclos"] = estado.get("ciclos", 0) + 1
    estado["peticiones"] = cont["pet"]


def detalle(url):
    """Galería completa de la ficha (gallery-state → flatPhotos)."""
    html = get(url, si_404=GONE)
    if html == GONE:
        return {"disponible": False}
    if html is None:
        raise IOError("sin respuesta")
    d = _estado_html(html, "detail-state")
    if not isinstance(d, dict):
        raise IOError("ficha sin estado (¿reto de Cloudflare?)")
    general = None
    for k, v in d.items():
        if k.startswith("detail-property-") and isinstance(v, dict):
            general = v.get("generalData")
    if not general or not general.get("propertyId"):
        # La ficha de un inmueble retirado responde 200 pero sin datos del inmueble
        return {"disponible": False}
    fotos = []
    gal = ((d.get("gallery-state") or {}).get("data") or {}).get("galleryData") or {}
    lista = [f for f in gal.get("flatPhotos") or [] if isinstance(f, dict) and f.get("url")]
    lista.sort(key=lambda f: a_int(f.get("order")) or 0)
    fotos = list(dict.fromkeys(f["url"] for f in lista if str(f.get("is360")) != "1"))
    out = {"fotos": fotos} if fotos else {}
    adm = a_int(general.get("adminValue"))
    if adm:
        out["admin"] = adm
    return out
