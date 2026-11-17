"""Viviendo (viviendo.la): portal de Domus que reúne inventario de muchas inmobiliarias
(Maxibienes, Portada, Mattis, Gómez y Asociados, Novabienes…).

De dónde salen los datos: la API JSON pública que usa la propia página (sin login ni claves;
el sitio no tiene robots.txt):
- Listado: /api/properties?biz=1&type={1 apartamento|3 apartaestudio}&city={código DANE}
  &rentmin=…&rentmax=…&page=N  → 12 por página, con `total` y `last_page`.
- Ficha:   /api/properties/{viviendo_code} → galería completa, descripción, comodidades y
  brokers[] (asesor: nombre y celular, solo en algunas inmobiliarias). Si el código no existe
  responde 200 con {"code":"6"} → se marca como no disponible.

El inventario en presupuesto cabe entero en una corrida (~20 páginas el sur y ~35 Medellín,
≈600 anuncios), así que se barre todo cada vez, el sur primero; si se agota el tope de
peticiones, lo que falta es Medellín y se completa en la siguiente corrida (cursor por ciudad).

Limitaciones:
- No hay fecha de publicación real: `date_published` viene vacío y `created_at` refleja cargas
  masivas de la inmobiliaria (hay miles del 2023-10 que siguen vivos) → publicado=None.
  Se guarda `actualizado` (updated_at) solo como dato informativo.
- La API da `company_id` pero no el nombre de la inmobiliaria (no hay endpoint público con
  los nombres) → anunciante=None.
- tel/wa del asesor (brokers[]) cuando la inmobiliaria lo carga: la API pública lo entrega; la
  página lo muestra tras el formulario de contacto (decisión del usuario: usarlo).
- Muchos anuncios traen barrio "Sin Barrio" o "." → barrio=None.
- Algunas inmobiliarias ponen una coordenada por defecto (centro del municipio u oficina; una
  misma se veía en Medellín, Itagüí, La Estrella y Sabaneta): las que se repiten en 4+ anuncios
  de municipios o direcciones distintas se descartan (lat/lon=None). detalle() no devuelve
  lat/lon para no volver a llenarlas.
"""
import json
import re

import config
from scraper.http import GONE, get, a_int

NOMBRE = "viviendo"
BASE = "https://viviendo.la"
API = BASE + "/api/properties"

# Código DANE de cada municipio (parámetro city de la API); el sur primero
CIUDADES = [("Sabaneta", 5631), ("Envigado", 5266), ("Itagüí", 5360), ("La Estrella", 5380), ("Medellín", 5001)]
TIPOS = {1: "Apartamento", 3: "Apartaestudio"}
MAX_PETICIONES = 120   # por corrida (listado); la vuelta completa usa ~56
BARRIOS_VACIOS = {"", ".", "sin barrio", "sin definir", "n/a", "na", "0"}


def _sin_tildes(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower().strip()


def _barrio(valor):
    v = (valor or "").strip(" .-")
    if _sin_tildes(v) in BARRIOS_VACIOS or v.isdigit() or len(v) < 3:
        return None
    return v.title() if v.isupper() or v.islower() else v


def _coord(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None
    if 6.0 < lat < 6.45 and -75.8 < lon < -75.4:
        return lat, lon
    return None, None


def _url_media(u):
    return u if isinstance(u, str) and u.startswith("http") else None


def _normalizar(x, municipio):
    code = x.get("viviendo_code")
    pu = x.get("property_url") or f"inmuebles/x/{code}"
    url = pu if pu.startswith("http") else f"{BASE}/{pu.lstrip('/')}"
    barrio = _barrio(x.get("neighborhood"))
    tipo = TIPOS.get(x.get("type_code")) or (x.get("type") or "Apartamento").capitalize()
    lat, lon = _coord(x.get("latitude"), x.get("longitude"))
    img = _url_media(x.get("photo"))
    estrato = a_int(x.get("stratum"))
    return {
        "id": f"vv-{code}",
        "fuente": "Viviendo",
        "url": url,
        "titulo": f"{tipo} en arriendo en {municipio}" + (f", {barrio}" if barrio else ""),
        "precio": a_int(x.get("rent")),
        "admin": a_int(x.get("administration")) or None,
        "area": a_int(x.get("built_area")) or a_int(x.get("private_area")) or None,
        "hab": a_int(x.get("bedrooms")),
        "banos": a_int(x.get("bathrooms")),
        "parq": a_int(x.get("parking")),
        "estrato": estrato if estrato and 1 <= estrato <= 6 else None,
        "municipio": municipio,
        "barrio": barrio,
        "lat": lat,
        "lon": lon,
        "img": img,
        "fotos": [img] if img else [],
        "video": _url_media(x.get("video")),
        "tour": _url_media(x.get("tour3d")),
        "anunciante": None,   # la API solo da company_id, sin nombre
        "publicado": None,    # date_published vacío; created_at no es fecha de publicación
        "actualizado": (x.get("updated_at") or "")[:10] or None,
        "tel": None,
        "wa": None,
    }


def _codigo(url_o_id):
    m = re.search(r"(\d+)/?$", str(url_o_id))
    return m.group(1) if m else None


def _digitos(v):
    d = re.sub(r"\D", "", str(v or ""))
    return d if len(d) >= 7 and set(d) != {"0"} else None


def detalle(url):
    """Ficha completa: galería, comodidades y asesor (nombre, celular) cuando la API lo trae."""
    codigo = _codigo(url)
    if not codigo:
        return {}
    txt = get(f"{API}/{codigo}", si_404=GONE)
    if txt == GONE:
        return {"disponible": False}
    if txt is None:
        raise IOError("sin respuesta")
    j = json.loads(txt)
    d = j.get("data")
    if str(j.get("code")) == "6":
        return {"disponible": False}   # "No existe o el código es incorrecto"
    if not isinstance(d, dict) or not d:
        raise IOError(f"respuesta inesperada (code {j.get('code')})")
    out = {}
    estado = (d.get("status") or "").strip().lower()
    if estado:
        out["disponible"] = estado == "disponible"
    galeria = sorted((g for g in d.get("gallery") or [] if isinstance(g, dict)), key=lambda g: g.get("order") or 0)
    out["fotos"] = list(dict.fromkeys(u for u in (_url_media(g.get("imageurl")) for g in galeria) if u))
    # lat/lon no se devuelven aquí: son las mismas del listado, y allá se descartan las
    # coordenadas genéricas (centro de municipio u oficina repetidas en muchos anuncios)
    estrato = a_int(d.get("stratum"))
    out["estrato"] = estrato if estrato and 1 <= estrato <= 6 else None
    out["banos"] = a_int(d.get("bathrooms"))
    out["parq"] = a_int(d.get("parking"))
    out["area"] = a_int(d.get("built_area")) or None
    out["admin"] = a_int(d.get("administration")) or None
    out["barrio"] = _barrio(d.get("neighborhood"))
    try:
        from scraper.sources.fincaraiz import comodidades
        out["amen"] = comodidades([a.get("name") for a in d.get("amenities") or [] if isinstance(a, dict) and a.get("name")])
    except Exception:
        pass
    if _url_media(d.get("video")):
        out["video"] = d["video"]
    if _url_media(d.get("tour3d")):
        out["tour"] = d["tour3d"]
    # Asesor del anuncio (brokers[]): la misma API pública lo entrega (sin clave ni cuenta); la página
    # lo muestra tras el formulario "Enviar y ver teléfonos". Solo algunas inmobiliarias lo cargan.
    for b in d.get("brokers") or []:
        if not isinstance(b, dict):
            continue
        tel = _digitos(b.get("phone")) or _digitos(b.get("phone_2"))
        wa = _digitos(b.get("whatsapp"))
        nombre = (b.get("name") or "").strip()
        if tel or wa:
            if nombre:
                out["anunciante"] = nombre.title() if nombre.isupper() else nombre
            out["tel"] = tel
            out["wa"] = wa   # solo si la API lo marca como WhatsApp (no se deduce del celular)
            break
    return out


def _base_direccion(dire):
    """Dirección sin interior/apto, para reconocer varios apartamentos del mismo edificio."""
    d = _sin_tildes(dire)
    d = re.split(r"\b(?:int|interior|apto|apt|apartamento|ap|torre|piso|unidad|casa|bloque|bl|etapa|local)\b", d)[0]
    return "-".join(str(int(n)) for n in re.findall(r"\d+", d)[:3])   # "Cra 42 #40 DD Sur -08" → 42-40-8


def _coords_genericas(items, previas):
    """Coordenadas que la inmobiliaria pone por defecto (centro del municipio u oficina): se
    repiten en 4+ anuncios de municipios distintos o de 3+ direcciones distintas. Varios
    apartamentos de un mismo edificio (misma dirección base) sí conservan su coordenada."""
    grupos = {}
    for l, x in items:
        if l["lat"] is not None:
            grupos.setdefault((round(l["lat"], 4), round(l["lon"], 4)), []).append((l, x))
    genericas = {tuple(c) for c in previas}
    for c, g in grupos.items():
        if len(g) >= 4 and (len({l["municipio"] for l, _ in g}) > 1
                            or len({_base_direccion(x.get("address")) or l["id"] for l, x in g}) >= 3):
            genericas.add(c)
    return genericas


def scrape(estado, conocidos, log):
    primera_vez = estado.get("v") != 1
    # Barrido circular: normalmente cabe entero en una corrida; si se acaba el tope (o falla la
    # red), la siguiente corrida sigue desde la misma ciudad/tipo/página y da la vuelta.
    tareas = [(m, c, t) for m, c in CIUDADES for t in TIPOS]
    inicio, pag_inicio = (estado.get("pos") or [0, 1])[:2]
    inicio = inicio if 0 <= inicio < len(tareas) else 0
    presupuesto = MAX_PETICIONES
    vistos, items, pets = set(), [], {}
    pos, pend_mun, hechas = None, set(), 0
    for k in range(len(tareas)):
        idx = (inicio + k) % len(tareas)
        municipio, ciudad, tipo = tareas[idx]
        pagina = int(pag_inicio) if k == 0 else 1
        ultima = None
        while ultima is None or pagina <= ultima:
            if presupuesto <= 0:
                pos = [idx, pagina]
                break
            presupuesto -= 1
            pets[municipio] = pets.get(municipio, 0) + 1
            url = (f"{API}?biz=1&type={tipo}&city={ciudad}&rentmin={config.PRECIO_MIN_GUARDAR}"
                   f"&rentmax={config.PRECIO_MAX_GUARDAR}&page={pagina}")
            txt = get(url, si_404=GONE)
            try:
                j = json.loads(txt) if txt and txt != GONE else None
            except json.JSONDecodeError:
                j = None
            if not isinstance(j, dict):
                pos = [idx, pagina]   # fallo temporal: se retoma aquí
                break
            ultima = int(j.get("last_page") or 1)
            for x in j.get("data") or []:
                if not isinstance(x, dict) or not x.get("viviendo_code"):
                    continue
                if x.get("biz_code") not in (None, 1) or x.get("type_code") not in TIPOS:
                    continue
                if _sin_tildes(x.get("city")) != _sin_tildes(municipio):
                    continue   # la API filtra por ciudad, pero se confirma
                l = _normalizar(x, municipio)
                if l["id"] in vistos or not l["precio"]:
                    continue
                if not (config.PRECIO_MIN_GUARDAR <= l["precio"] <= config.PRECIO_MAX_GUARDAR):
                    continue
                vistos.add(l["id"])
                items.append((l, x))
            pagina += 1
        if not pos:
            hechas += 1
        if pos:
            pend_mun = {m for m, _, _ in (tareas[(idx + i) % len(tareas)] for i in range(len(tareas) - k))}
            break
    genericas = _coords_genericas(items, estado.get("genericas") or [])
    resumen = {}
    for l, _ in items:
        if l["lat"] is not None and (round(l["lat"], 4), round(l["lon"], 4)) in genericas:
            l["lat"] = l["lon"] = None
        if primera_vez:
            l["_inicial"] = True
        r = resumen.setdefault(l["municipio"], [0, 0])
        r[0] += 1
        r[1] += l["id"] not in conocidos
        yield l
    for municipio, _ in CIUDADES:
        leidos, nuevos = resumen.get(municipio, [0, 0])
        log(f"  viviendo/{municipio}: {leidos} leídos, {nuevos} nuevos, {pets.get(municipio, 0)} peticiones"
            + (" (incompleto: sigue en la próxima corrida)" if municipio in pend_mun else ""))
    # La carga inicial termina cuando se completa una vuelta entera (en una o varias corridas)
    if primera_vez:
        estado["hechas"] = estado.get("hechas", 0) + hechas
        if pos is None or estado["hechas"] >= len(tareas):
            estado["v"] = 1
            estado.pop("hechas", None)
    estado["pos"] = pos or [0, 1]
    estado["genericas"] = sorted(list(c) for c in genericas)[:60]
