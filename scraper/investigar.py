"""Investigación automática de cada apartamento nuevo del sur.

Hace lo mismo que se hacía a mano en el chat, en tres etapas, cuidando la cuota de IA:

1. RECOLECTAR (sin IA). Cada anuncio nuevo del sur entra a una cola (agrupando sus versiones).
   - Abre la ficha original de cada versión: si sigue disponible, fecha de publicación y de
     modificación, descripción, si tiene video.
   - Lee la descripción: portería, vigilancia, unidad cerrada, "en casa", ascensor, piscina,
     parqueadero y el nombre del conjunto o edificio.
   - Ubicación real: usa el pin exacto (no el punto general del barrio). Si el mismo punto lo usan
     anuncios de inmobiliarias distintas, es un punto genérico y se avisa. El barrio real sale de
     OpenStreetMap (Nominatim), no de la etiqueta del portal.
   - Qué hay cerca (OpenStreetMap/Overpass): supermercados, salud, parques, colegios, centros
     comerciales, paraderos, la estación de metro más cercana y si hay una cárcel cerca.
   - Todo queda en un "expediente" estructurado (data/investigacion/estado.json).

2. INVESTIGAR LO QUE FALTA (IA con búsqueda en Google, en cola y con caché).
   - Barrio: solo si no tenemos su ficha (docs/barrios_info.json) ni lo investigamos antes.
   - Conjunto o edificio: una vez por conjunto (opiniones, problemas, noticias).
   Las fuentes que se guardan son las que devuelve Google, no las que escriba el modelo.

3. OPINAR (una sola solicitud por tanda). Con los expedientes completos, un solo pedido evalúa
   todos los apartamentos de la tanda según el perfil de búsqueda y devuelve, para cada uno,
   veredicto, puntaje, a favor, en contra y preguntas para la inmobiliaria.
   Gemini primero (contexto grande); si no hay cuota, Groq en tandas pequeñas.

Cuota: un tope diario propio (IA_MAX_DIA) y nunca deja a la actualización normal sin margen.
Salida para la página: docs/data/investigaciones.json.

Uso:
    python -m scraper.investigar                  # cola normal
    python -m scraper.investigar --url URL [URL]  # investigar ya esos anuncios
"""
import argparse
import html
import json
import math
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from scraper import ia

RAIZ = Path(__file__).resolve().parent.parent
F_LISTINGS = RAIZ / "docs" / "data" / "listings.json"
F_BARRIOS = RAIZ / "docs" / "barrios_info.json"
F_METRO = RAIZ / "data" / "metro_estaciones.json"
D_INV = RAIZ / "data" / "investigacion"
F_ESTADO = D_INV / "estado.json"
F_GEO = D_INV / "geo.json"
F_SALIDA = RAIZ / "docs" / "data" / "investigaciones.json"

COL = timezone(timedelta(hours=-5))

# Lo que busca el usuario (se usa para que la IA opine, no para filtrar la cola)
PERFIL = ("Arriendo para vivir. Prioridades: 1) zona segura; 2) conjunto con portería o vigilancia; "
          "3) cerca a comercio y servicios; 4) buen transporte (metro o rutas frecuentes). "
          "Presupuesto hasta $1.700.000, 2 o más habitaciones y 2 baños.")

# Límites (cuota gratuita: ver scraper/ia.py)
MAX_RECOLECTAR = 120         # expedientes nuevos por corrida (sin IA: ~5 s cada uno)
MAX_BUSQUEDAS_CORRIDA = 6    # barrios + conjuntos investigados con IA por corrida
IA_MAX_DIA = 60              # de ~190 pedidos/día de Gemini (5 claves × 2 modelos × 19); la actualización usa ≤30
MARGEN_ACTUALIZACION = 40    # pedidos de Gemini que siempre quedan libres para la actualización
LOTE_GEMINI = 40             # anuncios por pedido de opinión en Gemini
LOTE_GROQ = 6                # Groq gratis tiene poco límite de tokens por minuto
DIAS_REVISAR = 5             # se vuelve a abrir la ficha (¿sigue disponible?) cada tantos días
DIAS_CONJUNTO = 60           # una investigación de conjunto o barrio vale este tiempo
SEG_LIMITE = 20 * 60         # tiempo máximo de la corrida
EXP_VERSION = 3              # sube cuando cambia el formato del expediente (fuerza revisarlos)
DIAS_COLA = 10               # entran a la cola los vistos por primera vez en estos días (o los del perfil)
PERFIL_FILTRO = {"precio_max": 1_700_000, "hab_min": 2, "banos_min": 2}

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0 Safari/537.36", "Accept-Language": "es-CO,es;q=0.9"}
UA_OSM = {"User-Agent": "arriendos-medellin/1.0 (github.com/tajovix-dev/arriendos-medellin)"}
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]

SUR = {"Sabaneta", "Envigado", "Itagüí", "La Estrella"}
SUR_MED = re.compile(r"\b(poblado|guayabal|belen|aguacatala|ciudad del rio|castropol|lalinde|loma de los bernal|"
                     r"la mota|rodeo|san antonio de prado|cristo rey|campo amor|santa fe|la colina|el rincon|"
                     r"los alpes|las playas|malibu|fatima|rosales|granada|la gloria|las violetas|los almendros|"
                     r"nueva villa|manila|patio bonito|el diamante|alejandria|la florida|astorga|tesoro|los balsos|"
                     r"san lucas|las lomas|provenza|santa maria de los angeles|loma de los parra|altavista|"
                     r"trinidad|shellmar|tenche|la hondonada|diego echavarria|san bernardo|miravalle)\b")

T0 = time.time()


def ahora():
    return datetime.now(COL)


def norm(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def leer(f, defecto):
    try:
        return json.loads(Path(f).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def escribir(f, datos, compacto=False):
    Path(f).parent.mkdir(parents=True, exist_ok=True)
    txt = json.dumps(datos, ensure_ascii=False, separators=(",", ":")) if compacto else \
        json.dumps(datos, ensure_ascii=False, indent=1)
    Path(f).write_text(txt, encoding="utf-8")


def dist_m(a, b, c, d):
    p = math.radians
    return 6371000 * math.hypot((p(d) - p(b)) * math.cos(p((a + c) / 2)), p(c) - p(a))


def es_sur(it):
    return it.get("municipio") in SUR or (it.get("municipio") == "Medellín" and SUR_MED.search(norm(it.get("barrio"))))


def en_perfil(it):
    f = PERFIL_FILTRO
    return it.get("precio", 9e9) <= f["precio_max"] and (it.get("hab") or 0) >= f["hab_min"] and (it.get("banos") or 0) >= f["banos_min"]


def texto_html(raw):
    raw = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", raw)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)))


# ---------------------------------------------------------------- 1. Ficha original

def _primero(patron, txt):
    m = re.search(patron, txt)
    return m.group(1) if m else None


def abrir_ficha(it):
    """Abre la ficha de una versión y devuelve lo que dice la página (sin inventar nada)."""
    url = it["url"]
    out = {"fuente": it.get("fuente"), "url": url, "disponible": None, "publicado": it.get("publicado"),
           "modificado": None, "video": None, "desc": "", "lat": None, "lon": None}
    try:
        if it.get("fuente") == "Viviendo":
            cod = url.rstrip("/").split("/")[-1]
            d = requests.get("https://viviendo.la/api/properties/" + cod, headers=UA, timeout=25).json()
            d = d.get("data", d) if isinstance(d, dict) else {}
            out["disponible"] = d.get("status") == "Disponible"
            out["desc"] = (d.get("description") or "") + " | " + ", ".join(a.get("name", "") for a in d.get("amenities") or [])
            out["video"] = bool(d.get("video") or d.get("tour3d"))
            if str(d.get("date_published", "")).startswith("20"):
                out["publicado"] = d["date_published"][:10]
            try:
                out["lat"], out["lon"] = float(d.get("latitude")), float(d.get("longitude"))
            except (TypeError, ValueError):
                pass
            return out
        r = requests.get(url, headers=UA, timeout=25)
        raw = r.text
        plano = raw.replace("&q;", '"').replace('\\"', '"').replace("\\/", "/")
        txt = texto_html(raw)
        out["disponible"] = not ("addeletedid" in r.url or r.status_code in (404, 410) or re.search(
            r"ya no est[aá] disponible|ya no se encuentra disponible|este inmueble no est[aá] disponible|Error 404 - Detalle",
            txt, re.I))
        out["publicado"] = (_primero(r'"(?:datePosted|created_at|publicationDate)"\s*:\s*"(20\d\d-\d\d-\d\d)', plano)
                            or out["publicado"])
        out["modificado"] = _primero(r'"(?:dateModified|updated_at)"\s*:\s*"(20\d\d-\d\d-\d\d)', plano)
        hay_video = re.search(r'"(?:youtube|video|videoUrl|tour3d|matterPort)"\s*:\s*("https?:[^"]+"|\[\s*"[^"]+)', plano)
        campo_video = re.search(r'"(?:youtube|video|videoUrl|tour3d|matterPort)"\s*:', plano)
        out["video"] = True if hay_video else (False if campo_video else None)
        descs = []
        for m in re.finditer(r'"description"\s*:\s*"((?:[^"\\]|\\.){30,4000})"', raw):
            try:
                descs.append(json.loads('"' + m.group(1) + '"'))
            except ValueError:
                pass
        for marca in ("Acerca de este inmueble", "Descripción", "Descripcion"):
            i = txt.find(marca)
            if i >= 0:
                descs.append(txt[i:i + 1500])
        out["desc"] = max(descs, key=len) if descs else ""
        la = _primero(r'"(?:latitude|lat)"\s*:\s*"?(-?\d{1,2}\.\d{4,})', plano)
        lo = _primero(r'"(?:longitude|lng|lon)"\s*:\s*"?(-?\d{2,3}\.\d{4,})', plano)
        if la and lo:
            out["lat"], out["lon"] = float(la), float(lo)
    except (requests.RequestException, ValueError) as e:
        out["error"] = str(e)[:80]
    return out


RX = {
    "porteria": r"porter[ií]a|recepci[oó]n 24|seguridad 24|vigilancia 24|vigilancia las 24|celadur",
    "vigilancia": r"vigilan|cctv|circuito cerrado|c[aá]maras de seguridad|seguridad privada",
    "unidad_cerrada": r"unidad cerrada|conjunto cerrado|urbanizaci[oó]n cerrada|unidad residencial",
    "en_casa": r"\ben casa\b|piso de (?:una )?casa|apartamento en casa|casa de [2-4] pisos|segundo piso|tercer piso independiente",
    "ascensor": r"ascensor",
    "piscina": r"piscina",
    "parqueadero": r"parqueadero|garaje|parqueo",
    "loma": r"\bloma\b|pendiente|subida",
    "dice_venta": r"\ben venta\b|se vende",
}
RX_CONJ = [
    re.compile(r"(?i:urbanizaci[oó]n|unidad residencial|unidad|conjunto residencial|conjunto|edificio|torres)\s+"
               r"([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ&'\-]+(?:\s+(?:de\s+|del\s+)?[A-ZÁÉÍÓÚÑ0-9][\wÁÉÍÓÚÑáéíóúñ&'\-]+){0,3})"),
    re.compile(r"\b([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]+){0,2})\s+(?:Apartamentos|APARTAMENTOS|Residencial|Condominio)\b"),
]
NO_CONJ = {"cerrada", "cerrado", "residencial", "con", "en", "de", "la", "el", "para", "nuevo", "nueva", "moderno",
           "este", "ubicado", "excelente", "amplio", "apartamento", "arriendo", "unidad", "se", "con porteria"}


NO_SECTOR = {"residencial", "tranquilo", "tranquila", "comercial", "exclusivo", "exclusiva", "privilegiado", "estrategico",
             "central", "estrato", "familiar", "campestre", "muy", "de", "con", "excelente", "valorizado", "seguro", "bueno"}


def es_nombre_de_barrio(nombre):
    """True si el «nombre de conjunto» es en realidad un barrio o municipio (Poblado, Lomitas...)."""
    n = norm(nombre)
    if SUR_MED.fullmatch(n) or n in {norm(m) for m in SUR | {"Medellín"}}:
        return True
    ficha_barrio("Medellín", "")   # carga FICHAS
    palabras = n.split()
    return any(k == n or k == " ".join(palabras[:len(k.split())]) for (_, k) in FICHAS) or \
        palabras[0] in ("lomitas", "poblado", "poblados", "loma", "vereda", "sector", "barrio")


def leer_texto(desc):
    d = desc or ""
    dn = norm(d)
    rasgos = {k: bool(re.search(v, d, re.I)) or bool(re.search(norm(v).replace(" ", " "), dn)) for k, v in RX.items()}
    conj = None
    for rx in RX_CONJ:
        for m in rx.finditer(d):
            nombre = re.sub(r"^(?:(?:En|en|EN|La|LA|El|EL|Los|Las|De|DE|Del|Se|Un|Una)\s+)+", "", m.group(1).strip(" .,-"))
            if norm(nombre) not in NO_CONJ and len(norm(nombre)) >= 4 and not norm(nombre).startswith(("con ", "en ", "de ")):
                conj = nombre.title() if nombre.isupper() else nombre
                break
        if conj:
            break
    sector = _primero(r"(?i)\b(?:sector|barrio)\s+([A-ZÁÉÍÓÚÑa-záéíóúñ][\wáéíóúñ ]{2,30}?)(?=[,.;:\-]| con | cuenta| en |$)", d)
    sector = (sector or "").strip()
    if not sector or norm(sector).split()[0] in NO_SECTOR:
        sector = None
    if conj and es_nombre_de_barrio(conj):
        conj = None    # «Poblado», «Lomitas»...: es un barrio, no el nombre del conjunto
    return rasgos, conj, sector


# ---------------------------------------------------------------- 2. Ubicación y alrededores

GEO = None


def geo():
    global GEO
    if GEO is None:
        GEO = leer(F_GEO, {"inversa": {}, "cerca": {}})
    return GEO


def barrio_osm(lat, lon):
    k = f"{lat:.5f},{lon:.5f}"
    c = geo()["inversa"]
    if k in c:
        return c[k]
    time.sleep(1.1)   # política de Nominatim: 1 consulta por segundo
    try:
        d = requests.get("https://nominatim.openstreetmap.org/reverse",
                         params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 17, "accept-language": "es"},
                         headers=UA_OSM, timeout=20).json()
        a = d.get("address", {})
        c[k] = {"barrio": a.get("neighbourhood") or a.get("quarter") or a.get("suburb") or a.get("hamlet"),
                "via": a.get("road"), "municipio": a.get("city") or a.get("town") or a.get("village")}
    except (requests.RequestException, ValueError):
        return None
    return c[k]


F_LUGARES = D_INV / "lugares_osm.json"
BBOX_SUR = "6.09,-75.69,6.26,-75.54"
LUGARES = None


def _descargar_lugares():
    """Una sola consulta a OpenStreetMap con los lugares de todo el sur (se refresca cada 30 días)."""
    bb = BBOX_SUR
    q = f"""[out:json][timeout:240];
(
 nwr["shop"~"^(supermarket|mall|department_store)$"]({bb});
 nwr["shop"="convenience"]["name"~"D1|Ara|[EÉ]xito|Carulla|Euro|Ol[ií]mpica|Justo|Mercaldas|Consumo|Dinast[ií]a|Mercamax|Surtimax|Super Inter|Colsubsidio|Comfama|Merca|Supermercado",i]({bb});
 nwr["amenity"~"^(hospital|clinic|school|university|marketplace|prison)$"]({bb});
 nwr["leisure"="park"]["name"]({bb});
 node["highway"="bus_stop"]({bb});
 nwr["name"]["landuse"="residential"]({bb});
 nwr["name"]["building"~"^(apartments|residential)$"]({bb});
);
out center tags;"""
    for u in OVERPASS:
        try:
            r = requests.post(u, data={"data": q}, headers=UA_OSM, timeout=300)
            if not r.ok:
                continue
            out = []
            for e in r.json().get("elements", []):
                t = e.get("tags", {})
                la = e.get("lat") or (e.get("center") or {}).get("lat")
                lo = e.get("lon") or (e.get("center") or {}).get("lon")
                if la is None:
                    continue
                if t.get("highway") == "bus_stop":
                    c = "paradero"
                elif t.get("amenity") == "prison":
                    c = "carcel"
                elif t.get("shop") in ("mall", "department_store"):
                    c = "centro_comercial"
                elif t.get("shop") in ("supermarket", "convenience") or t.get("amenity") == "marketplace":
                    c = "supermercado"
                elif t.get("amenity") in ("hospital", "clinic"):
                    c = "salud"
                elif t.get("amenity") in ("school", "university"):
                    c = "colegio"
                elif t.get("leisure") == "park":
                    c = "parque"
                else:
                    c = "conjunto"
                out.append([c, round(la, 6), round(lo, 6), t.get("name") or ""])
            if len(out) > 500:
                out += _descargar_quebradas()
                escribir(F_LUGARES, {"fuente": "OpenStreetMap (Overpass), sur del Valle de Aburrá", "bbox": bb,
                                     "fecha": ahora().date().isoformat(), "lugares": out}, compacto=True)
                return True
        except (requests.RequestException, ValueError):
            pass
    return False


def _descargar_quebradas():
    """Quebradas y ríos con nombre, como puntos de su cauce (para saber cuáles pasan cerca del apartamento)."""
    q = f'[out:json][timeout:240];way["waterway"~"^(stream|river|canal)$"]["name"]({BBOX_SUR});out geom tags;'
    for u in OVERPASS:
        try:
            r = requests.post(u, data={"data": q}, headers=UA_OSM, timeout=300)
            if not r.ok:
                continue
            out = []
            for w in r.json().get("elements", []):
                nom = (w.get("tags") or {}).get("name")
                for i, g in enumerate(w.get("geometry") or []):
                    if i % 2 == 0:   # un punto cada ~2 nodos basta (cauce cada ~20-40 m)
                        out.append(["quebrada", round(g["lat"], 5), round(g["lon"], 5), nom])
            return out
        except (requests.RequestException, ValueError):
            pass
    return []


def lugares():
    global LUGARES
    if LUGARES is None:
        d = leer(F_LUGARES, {})
        try:
            viejo = (ahora().date() - datetime.fromisoformat(d.get("fecha", "2000-01-01")).date()).days > 30
        except ValueError:
            viejo = True
        sin_quebradas = not any(x[0] == "quebrada" for x in d.get("lugares", []))
        if (not d.get("lugares") or viejo or sin_quebradas) and _descargar_lugares():
            d = leer(F_LUGARES, {})
        LUGARES = d.get("lugares", [])
    return LUGARES


RADIO = {"supermercado": 800, "centro_comercial": 1500, "salud": 1200, "colegio": 800, "parque": 800,
         "paradero": 500, "carcel": 1200, "conjunto": 90, "quebrada": 300}
NOMBRE_CAT = {"supermercado": "supermercados", "centro_comercial": "centros_comerciales", "salud": "salud",
              "colegio": "colegios", "parque": "parques"}


def alrededores(lat, lon):
    """Lugares de OpenStreetMap alrededor del punto (distancias en línea recta)."""
    if not lugares():
        return None
    res = {"supermercados": [], "centros_comerciales": [], "salud": [], "colegios": [], "parques": [],
           "paraderos": 0, "carcel": None, "nombres_cercanos": [], "quebradas": {}}
    dlat = 1500 / 111000
    for c, la, lo, nom in LUGARES:
        if abs(la - lat) > dlat or abs(lo - lon) > dlat:
            continue
        d = dist_m(lat, lon, la, lo)
        if d > RADIO[c]:
            continue
        if c == "paradero":
            res["paraderos"] += 1
        elif c == "carcel":
            if not res["carcel"] or d < res["carcel"]["m"]:
                res["carcel"] = {"nombre": nom or "cárcel", "m": round(d)}
        elif c == "conjunto":
            res["nombres_cercanos"].append(nom)
        elif c == "quebrada":
            res["quebradas"][nom] = min(res["quebradas"].get(nom, 9e9), round(d))
        elif nom:
            res[NOMBRE_CAT[c]].append({"nombre": nom, "m": round(d)})
    for kk in NOMBRE_CAT.values():
        vistos, unicos = set(), []
        for x in sorted(res[kk], key=lambda x: x["m"]):
            if x["nombre"] not in vistos:
                vistos.add(x["nombre"])
                unicos.append(x)
        res[kk] = unicos[:5]
    res["nombres_cercanos"] = sorted(set(res["nombres_cercanos"]))[:5]
    res["quebradas"] = [{"nombre": n, "m": m} for n, m in sorted(res["quebradas"].items(), key=lambda x: x[1])[:4]]
    return res


ESTACIONES = None


def metro_cercano(lat, lon):
    global ESTACIONES
    if ESTACIONES is None:
        ESTACIONES = leer(F_METRO, {}).get("estaciones", [])
    if not ESTACIONES:
        return None
    d, e = min((dist_m(lat, lon, e["lat"], e["lon"]), e["nombre"]) for e in ESTACIONES)
    return {"estacion": e, "m_linea_recta": round(d)}


# ---------------------------------------------------------------- Fichas de barrio

FICHAS = None


def ficha_barrio(municipio, *nombres):
    global FICHAS
    if FICHAS is None:
        FICHAS = {}
        for m in leer(F_BARRIOS, {}).get("municipios", []):
            for b in m.get("barrios", []):
                for n in [b["nombre"]] + list(b.get("alias") or []):
                    FICHAS[(m["municipio"], norm(n))] = b
    for nom in nombres:
        nn = norm(nom)
        if not nn or nn in ("otro", "centro", norm(municipio)):
            continue
        b = FICHAS.get((municipio, nn))
        if not b:
            for (mu, k), v in FICHAS.items():
                if mu == municipio and len(k) > 4 and (k == nn or (len(nn) > 4 and (k in nn or nn in k))):
                    b = v
                    break
        if b:
            return {"nombre": b["nombre"], "seguridad": b.get("seguridad"), "recomendacion": b.get("recomendacion"),
                    "terreno": b.get("terreno"), "razon": b.get("razon_recomendacion"),
                    "negativos": (b.get("negativos") or [])[:4], "positivos": (b.get("positivos") or [])[:3]}
    return None


# ---------------------------------------------------------------- Expediente

def expediente(grupo, todos_por_pin):
    """grupo: versiones del mismo apartamento. Devuelve el expediente estructurado (sin IA)."""
    base = grupo[0]
    fichas = []
    for it in grupo[:4]:
        if it.get("fuente") == "Rentola" and it.get("orig"):
            continue   # la copia de Rentola no aporta: se abre el original
        f = abrir_ficha(it)
        f.update({"anunciante": it.get("anunciante"), "tel": it.get("tel") or it.get("wa")})
        fichas.append(f)
    disp = [f["disponible"] for f in fichas if f["disponible"] is not None]
    desc = max((f["desc"] for f in fichas), key=len, default="")
    rasgos, conj, sector = leer_texto(desc + " " + " ".join(base.get("amen") or []))

    # Ubicación: preferir el pin exacto de alguna versión
    lat = lon = None
    tipo = "sin"
    for it in grupo:
        if it.get("lat") and not it.get("aprox"):
            lat, lon, tipo = it["lat"], it["lon"], "exacta"
            break
    if lat is None:
        for f in fichas:
            if f.get("lat"):
                lat, lon, tipo = f["lat"], f["lon"], "de la ficha"
                break
    if lat is None and base.get("lat"):
        lat, lon, tipo = base["lat"], base["lon"], "punto general del barrio"
    pin_general = False
    if lat is not None and tipo != "punto general del barrio":
        otros = todos_por_pin.get(f"{lat:.5f},{lon:.5f}", set())
        propios = {norm(it.get("anunciante")) for it in grupo}
        pin_general = len(otros - propios) >= 1
        if pin_general:
            tipo = "punto genérico (lo usan varias inmobiliarias)"
    ubic = {"tipo": tipo, "lat": lat, "lon": lon}
    if lat is not None and not tipo.startswith("punto"):
        ubic.update({"osm": barrio_osm(lat, lon)})
    cerca = alrededores(lat, lon) if lat is not None else None
    metro = metro_cercano(lat, lon) if lat is not None else None

    conj_origen = "anuncio" if conj else None
    if not conj and cerca and cerca.get("nombres_cercanos") and tipo in ("exacta", "de la ficha"):
        conj, conj_origen = cerca["nombres_cercanos"][0], "mapa (a menos de 90 m del pin)"

    # Tres niveles: BARRIO (zona oficial) ≠ SECTOR (dónde queda más o menos: unas cuadras) ≠ CONJUNTO
    mun = base["municipio"]
    osm = ubic.get("osm") or {}
    barrio_portal = next((it.get("barrio") for it in grupo if it.get("barrio")), None)   # alguna versión lo trae
    fb = ficha_barrio(mun, osm.get("barrio"), barrio_portal)
    barrio = fb["nombre"] if fb else (barrio_portal or osm.get("barrio"))
    # El nombre de OpenStreetMap, si no es el mismo barrio, suele ser un sector o urbanización
    nombre_sector = sector or (osm.get("barrio") if osm.get("barrio") and norm(osm["barrio"]) != norm(barrio or "") else None)
    refs = []
    if cerca and not tipo.startswith("punto"):
        for kk in ("parques", "colegios", "centros_comerciales", "salud", "supermercados"):
            refs += [x for x in cerca.get(kk, []) if x["m"] <= 400]
        refs = [f"{x['nombre']} ({x['m']} m)" for x in sorted(refs, key=lambda x: x["m"])[:3]]
    sect = {"nombre": nombre_sector, "via": osm.get("via"), "referencias": refs,
            "quebradas": [f"{x['nombre']} ({x['m']} m)" for x in (cerca or {}).get("quebradas", [])] if cerca and not tipo.startswith("punto") else [],
            "celda": f"{round(lat / 0.004) * 0.004:.3f},{round(lon / 0.004) * 0.004:.3f}"
            if lat is not None and not tipo.startswith("punto") else None}
    # ¿El «sector» de la descripción es en realidad OTRO barrio distinto al del portal?
    fs = ficha_barrio(mun, sector) if sector else None
    otro_barrio = fs["nombre"] if fs and fb and fs["nombre"] != fb["nombre"] else None
    fo = ficha_barrio(mun, osm.get("barrio")) if osm.get("barrio") else None
    fp = ficha_barrio(mun, barrio_portal)
    pin_otro_barrio = fo["nombre"] if fo and fp and fo["nombre"] != fp["nombre"] else None
    pubs = sorted(f["publicado"] for f in fichas if f.get("publicado"))
    mods = sorted(f["modificado"] for f in fichas if f.get("modificado"))
    exp = {
        "ids": [it["id"] for it in grupo],
        "precio": base["precio"], "hab": base.get("hab"), "banos": base.get("banos"), "area": base.get("area"),
        "estrato": base.get("estrato"), "municipio": base["municipio"], "barrio_portal": barrio_portal,
        "v": EXP_VERSION, "barrio": barrio, "sector": sect, "sector_en_texto": sector,
        "texto_dice_otro_barrio": otro_barrio, "pin_en_otro_barrio": pin_otro_barrio,
        "conjunto": conj, "conjunto_origen": conj_origen,
        "disponible": (any(disp) if disp else None),
        "publicado": pubs[0] if pubs else None, "modificado": mods[-1] if mods else None,
        "en_pagina_desde": min(it.get("visto1", "9")[:10] for it in grupo),
        "video": any(f["video"] for f in fichas if f["video"]) if any(f["video"] is not None for f in fichas) else None,
        "versiones": [{k: f.get(k) for k in ("fuente", "url", "disponible", "publicado", "modificado", "anunciante", "tel")}
                      for f in fichas],
        "rasgos": rasgos, "desc": re.sub(r"\s+", " ", desc)[:700],
        "ubicacion": ubic, "metro": metro, "cerca": cerca, "barrio_ficha": fb,
        "hist": base.get("hist"),
        "fecha": ahora().isoformat(timespec="minutes"),
    }
    exp["alertas"] = alertas(exp)
    return exp


def alertas(e):
    """Avisos que salen solo de los datos (sin IA)."""
    a = []
    if e["disponible"] is False:
        a.append("Ya no está disponible en ninguna de sus páginas.")
    u = e["ubicacion"]
    if u["tipo"].startswith("punto"):
        a.append(f"Ubicación no confirmada: el mapa usa un {u['tipo']}. Pide la dirección exacta.")
    # Un sector dentro del barrio (p. ej. «Vegas de La Doctora» en La Doctora) NO es una contradicción;
    # solo se avisa cuando el texto o el pin apuntan a OTRO barrio conocido.
    if e.get("pin_en_otro_barrio"):
        a.append(f"El portal dice barrio «{e['barrio_portal']}», pero el pin cae en el barrio «{e['pin_en_otro_barrio']}».")
    if e.get("texto_dice_otro_barrio"):
        a.append(f"La descripción lo ubica en «{e['sector_en_texto']}», que es otro barrio distinto de «{e['barrio']}». Pide la dirección.")
    r = e["rasgos"]
    if r.get("en_casa"):
        a.append("Es un apartamento en una casa (no en conjunto).")
    if not (r.get("porteria") or r.get("vigilancia") or r.get("unidad_cerrada")):
        a.append("El anuncio no menciona portería, vigilancia ni unidad cerrada.")
    if r.get("dice_venta"):
        a.append("La descripción menciona «venta»: confirma que sea arriendo.")
    fecha = e.get("publicado") or e.get("en_pagina_desde")
    try:
        dias = (ahora().date() - datetime.fromisoformat(fecha[:10]).date()).days
        if dias > 45:
            a.append(f"Lleva {dias} días publicado: pregunta si sigue libre.")
    except (TypeError, ValueError):
        pass
    for q in (e.get("cerca") or {}).get("quebradas", []):
        if q["m"] <= 120:
            a.append(f"La {q['nombre']} pasa a {q['m']} m: pregunta si el edificio ha tenido problemas con crecientes.")
            break
    if e.get("cerca") and e["cerca"].get("carcel"):
        c = e["cerca"]["carcel"]
        a.append(f"Hay una cárcel a {c['m']} m ({c['nombre']}).")
    if e.get("metro") and e["metro"]["m_linea_recta"] > 2500:
        a.append(f"El metro más cercano ({e['metro']['estacion']}) está a {e['metro']['m_linea_recta'] / 1000:.1f} km en línea recta.")
    fb = e.get("barrio_ficha")
    if fb and (fb.get("seguridad") in ("baja", "media-baja") or fb.get("recomendacion") == "no recomendado"):
        a.append(f"Barrio {fb['nombre']}: seguridad {fb['seguridad']}, {fb['recomendacion']}.")
    return a


# ---------------------------------------------------------------- 3. Cuota de IA

def cuota_ok(estado, reserva=0):
    hoy = ahora().date().isoformat()
    u = estado.setdefault("uso_ia", {})
    if u.get("fecha") != hoy:
        u.clear()
        u.update({"fecha": hoy, "n": 0})
    if u["n"] + reserva >= IA_MAX_DIA:
        return False
    c = ia.resumen_cuota()
    return (c.get(ia.FLASH, 0) + c.get(ia.FLASH_LITE, 0) > MARGEN_ACTUALIZACION) or c.get("groq", 0) > 20


def contar_ia(estado):
    estado["uso_ia"]["n"] = estado["uso_ia"].get("n", 0) + 1


SISTEMA = ("Eres un investigador inmobiliario en el Valle de Aburrá (Colombia). Escribes en español claro. "
           "NUNCA inventas: solo afirmas lo que está en los datos dados o en los resultados de búsqueda. "
           "Si no encuentras algo, lo dices. Respondes SOLO con un objeto JSON válido, sin texto extra.")


def investigar_barrio(barrio, municipio):
    p = (f"Investiga el barrio o sector «{barrio}» de {municipio} (Antioquia, Colombia) para alguien que va a "
         f"arrendar allí. Busca en Google noticias de 2025 y 2026 de seguridad (hurtos, homicidios, extorsión, "
         f"microtráfico), problemas (inundaciones, deslizamientos, cortes de agua, trancones) y opiniones de "
         f"residentes. Usa solo lo que encuentres.\n"
         f"MUY IMPORTANTE: separa lo que la noticia ubica en «{barrio}» (hechos) de lo que es de otro sector o de "
         f"{municipio} en general (contexto_municipio). La calificación 'seguridad' se basa SOLO en los hechos del "
         f"barrio de 2025-2026; si no hay, es 'sin datos' (no la deduzcas del municipio). Indica el año de cada hecho.\n"
         'Devuelve JSON: {"encontrado": true|false, "seguridad": "alta|media-alta|media|media-baja|baja|sin datos", '
         '"resumen": "2 frases sobre el barrio", "hechos": [{"fecha": "AAAA-MM-DD o aprox", "que": "...", "medio": "..."}], '
         '"contexto_municipio": ["máx 3, con fecha"], "problemas": ["solo del barrio, con año"], "positivos": ["..."]}')
    return ia.preguntar_json(p, SISTEMA, tarea="fuerte", buscar=True)


def investigar_sector(e):
    """El SECTOR: las pocas cuadras alrededor del apartamento (no el barrio entero)."""
    s = e["sector"]
    donde = "; ".join(x for x in [
        f"sector «{s['nombre']}»" if s.get("nombre") else "",
        f"sobre o cerca de la vía «{s['via']}»" if s.get("via") else "",
        f"cerca de {', '.join(s['referencias'])}" if s.get("referencias") else "",
        f"quebradas a menos de 300 m: {', '.join(s['quebradas'])}" if s.get("quebradas") else "ninguna quebrada con nombre a menos de 300 m",
        f"barrio {e.get('barrio')}" if e.get("barrio") else ""] if x)
    p = (f"Investiga el SECTOR (unas pocas cuadras, no el barrio entero) donde queda un apartamento en {e['municipio']} "
         f"(Antioquia, Colombia): {donde}. Busca en Google noticias de 2025 y 2026 que mencionen esas calles, ese "
         f"sector o esos lugares: seguridad (hurtos, homicidios, expendios de droga, extorsión), invasiones, bares o "
         f"ruido, quebradas, inundaciones, deslizamientos, obras. Usa solo lo que encuentres.\n"
         f"MUY IMPORTANTE: separa (1) lo que pasa en ESE sector o esas calles, (2) lo que es del barrio en general "
         f"y (3) lo del municipio. Un hecho va en hechos_sector SOLO si la noticia nombra esas calles, esos lugares, "
         f"ese sector o esas quebradas cercanas; una noticia de otra quebrada u otro sector NO es del sector. "
         f"Si no hay nada del sector, dilo (encontrado=false).\n"
         'Devuelve JSON: {"encontrado": true|false, "resumen": "2 frases sobre el sector", '
         '"hechos_sector": [{"fecha": "...", "que": "...", "medio": "..."}], "riesgos": ["quebrada, loma, ruido... con fuente"], '
         '"del_barrio_no_del_sector": ["máx 3"], "del_municipio": ["máx 2"]}')
    return ia.preguntar_json(p, SISTEMA, tarea="fuerte", buscar=True)


def investigar_conjunto(nombre, barrio, municipio):
    p = (f"Investiga el conjunto o edificio residencial «{nombre}» en {barrio or ''} {municipio} (Antioquia, Colombia). "
         f"Busca en Google: reseñas y opiniones de residentes (Google Maps, foros, redes), quejas (seguridad, "
         f"administración, ruido, parqueaderos, zonas comunes que no existen), noticias y su dirección. "
         f"Si no encuentras ese conjunto exacto, dilo (encontrado=false) y no uses información de otro: un nombre "
         f"parecido pero distinto (otra palabra, otro municipio) NO es el mismo conjunto.\n"
         'Devuelve JSON: {"encontrado": true|false, "direccion": "... o null", "calificacion": "ej. 4,5 de 86 reseñas o null", '
         '"a_favor": ["..."], "quejas": ["..."], "noticias": [{"fecha": "...", "que": "...", "medio": "..."}], '
         '"resumen": "2 frases"}')
    return ia.preguntar_json(p, SISTEMA, tarea="fuerte", buscar=True)


def resumen_para_ia(clave, e, estado):
    """Versión compacta del expediente para el pedido de opinión."""
    c = e.get("cerca") or {}
    cerca = {k: [f"{x['nombre']} ({x['m']} m)" for x in c.get(k, [])[:3]]
             for k in ("supermercados", "centros_comerciales", "salud", "parques", "colegios")} if c else None
    if c:
        cerca["paraderos_500m_segun_osm_incompleto"] = c.get("paraderos")
    conj = estado["conjuntos"].get(clave_conj(e)) if e.get("conjunto") else None
    binv = estado["barrios"].get(clave_barrio(e))
    sinv = estado["sectores"].get(clave_sector(e)) if clave_sector(e) else None
    return {
        "id": clave, "precio": e["precio"], "hab": e["hab"], "banos": e["banos"], "area": e["area"], "estrato": e["estrato"],
        "municipio": e["municipio"], "barrio": e.get("barrio"), "barrio_portal": e["barrio_portal"],
        "sector": {k: v for k, v in (e.get("sector") or {}).items() if k != "celda" and v},
        "tipo_ubicacion": e["ubicacion"]["tipo"],
        "disponible": e["disponible"], "publicado": e["publicado"] or f"sin fecha; en la página desde {e['en_pagina_desde']}",
        "rasgos": {k: v for k, v in e["rasgos"].items() if v}, "conjunto": e.get("conjunto"),
        "descripcion": e["desc"][:450], "metro": e["metro"], "cerca": cerca,
        "ficha_barrio": e.get("barrio_ficha"),
        "investigacion_barrio": {k: (binv or {}).get(k) for k in ("seguridad", "resumen", "hechos", "problemas")} if binv else None,
        "contexto_municipio_no_es_del_barrio": (binv or {}).get("contexto_municipio"),
        "investigacion_sector": {k: (sinv or {}).get(k) for k in ("encontrado", "resumen", "hechos_sector", "riesgos")} if sinv else None,
        "investigacion_conjunto": {k: (conj or {}).get(k) for k in ("encontrado", "calificacion", "a_favor", "quejas", "resumen")} if conj else None,
        "alertas_automaticas": e["alertas"],
    }


def opinar(lote, estado):
    p = ("Perfil de quien busca:\n" + PERFIL + "\n\n"
         "Para CADA apartamento de la lista, da tu opinión basada SOLO en sus datos. Distancias 'm' son en línea recta. "
         "Distingue tres niveles: barrio (zona general), sector (las cuadras alrededor) y conjunto. Lo que pasa en el "
         "sector pesa más que lo del barrio en general. Pesa mucho la portería/vigilancia. "
         "Lo que esté en 'contexto_municipio_no_es_del_barrio' NO es del barrio: no lo cuentes como problema del barrio. "
         "Un hecho de hace más de 2 años pesa poco. "
         "Un anuncio no disponible es 'no recomendable'.\n"
         'Devuelve JSON: {"opiniones": [{"id": "...", "veredicto": "recomendable|con reservas|no recomendable", '
         '"puntaje": 0-10, "resumen": "1-2 frases", "a_favor": ["máx 3"], "en_contra": ["máx 3"], '
         '"preguntar": ["máx 3 preguntas para la inmobiliaria"]}]}\n\nApartamentos:\n'
         + json.dumps(lote, ensure_ascii=False, separators=(",", ":")))
    d = ia.preguntar_json(p, SISTEMA, tarea="fuerte")
    contar_ia(estado)
    return {o.get("id"): o for o in (d or {}).get("opiniones", []) if isinstance(o, dict)}


def clave_barrio(e):
    b = e.get("barrio") or e.get("barrio_portal")
    return f"{e['municipio']}|{norm(b)}" if b else None


def clave_sector(e):
    """Un sector con nombre se investiga por nombre; sin nombre, por celda de ~450 m alrededor del pin."""
    s = e.get("sector") or {}
    if s.get("nombre"):
        return f"{e['municipio']}|{norm(e.get('barrio'))}|{norm(s['nombre'])}"
    if s.get("celda") and (s.get("via") or s.get("referencias")):
        return f"{e['municipio']}|celda|{s['celda']}"
    return None


def clave_conj(e):
    return f"{e['municipio']}|{norm(e['conjunto'])}" if e.get("conjunto") else None


# ---------------------------------------------------------------- Orquestación

def huella(grupo):
    return "|".join(sorted(it["id"] for it in grupo)) + f"|{grupo[0]['precio']}"


def principal(urls=()):
    estado = leer(F_ESTADO, {})
    for k in ("grupos", "barrios", "sectores", "conjuntos"):
        estado.setdefault(k, {})
    L = leer(F_LISTINGS, {}).get("items", [])
    por_id = {x["id"]: x for x in L}
    grupos = {}
    for x in L:
        grupos.setdefault(str(x["dup"]) if x.get("dup") is not None else x["id"], []).append(x)
    # clave estable del grupo: el id más antiguo (el dup cambia de número entre corridas)
    G = {}
    for v in grupos.values():
        v.sort(key=lambda it: (it.get("fuente") == "Rentola", it.get("visto1", "")))
        G[min(it["id"] for it in v)] = v
    por_pin = {}
    for x in L:
        if x.get("lat") and not x.get("aprox"):
            por_pin.setdefault(f"{x['lat']:.5f},{x['lon']:.5f}", set()).add(norm(x.get("anunciante")))

    # Cola: pedidos explícitos primero, luego los nuevos del sur (más recientes primero)
    pedidos = []
    for u in urls:
        u = u.strip()
        hit = next((k for k, v in G.items() if any(it["url"] == u or u.rstrip("/").endswith(it["url"].rstrip("/").split("/")[-1])
                                                   for it in v)), None)
        if hit:
            pedidos.append(hit)
        else:
            print(f"  No está en nuestros datos (fuera del sur, de precio o aún no leído): {u}")
    limite_viejo = (ahora() - timedelta(days=DIAS_REVISAR)).isoformat()
    cola = []
    reciente = (ahora() - timedelta(days=DIAS_COLA)).isoformat()
    for k, v in G.items():
        if not any(es_sur(it) for it in v):
            continue
        if k not in estado["grupos"] and not (max(it.get("visto1", "") for it in v) >= reciente or en_perfil(v[0])):
            continue
        prev = estado["grupos"].get(k)
        if (prev is None or prev.get("huella") != huella(v) or prev.get("exp", {}).get("fecha", "") < limite_viejo
                or prev.get("exp", {}).get("v") != EXP_VERSION):
            cola.append(k)
    cola.sort(key=lambda k: max(it.get("visto1", "") for it in G[k]), reverse=True)   # más nuevos primero
    cola.sort(key=lambda k: k in estado["grupos"])                                     # nunca investigados primero
    cola = pedidos + [k for k in cola if k not in pedidos]
    print(f"Cola: {len(cola)} apartamentos del sur por investigar o revisar ({len(pedidos)} pedidos)")

    # 1. Recolectar
    hechos = []
    for k in cola[:max(MAX_RECOLECTAR, len(pedidos))]:
        if time.time() - T0 > SEG_LIMITE * 0.6 and k not in pedidos:
            break
        try:
            e = expediente(G[k], por_pin)
        except Exception as ex:   # un anuncio raro no debe tumbar la corrida
            print(f"  ! {k}: {ex}")
            continue
        g = estado["grupos"].setdefault(k, {})
        cambio = g.get("huella") != huella(G[k])
        g.update({"huella": huella(G[k]), "exp": e})
        if cambio or k in pedidos:
            g.pop("opinion", None)
        hechos.append(k)
        print(f"  ✓ {k}: {e['municipio']}/{e['barrio_portal']} · {e['ubicacion']['tipo']} · "
              f"{'disponible' if e['disponible'] else 'NO disponible' if e['disponible'] is False else '¿?'} · {len(e['alertas'])} alertas")
    escribir(F_GEO, geo(), compacto=True)
    escribir(F_ESTADO, estado)

    # 2. Investigar barrios sin ficha y conjuntos nuevos (en cola, con caché)
    vence = (ahora() - timedelta(days=DIAS_CONJUNTO)).isoformat()
    tareas = []
    for k in hechos:
        e = estado["grupos"][k]["exp"]
        if e["disponible"] is False:
            continue
        cb = clave_barrio(e)
        if cb and not e.get("barrio_ficha") and estado["barrios"].get(cb, {}).get("fecha", "") < vence:
            tareas.append(("barrio", cb, e))
        cc = clave_conj(e)
        if cc and estado["conjuntos"].get(cc, {}).get("fecha", "") < vence:
            tareas.append(("conjunto", cc, e))
        cs = clave_sector(e)
        if (cs and en_perfil({"precio": e["precio"], "hab": e["hab"], "banos": e["banos"]})
                and estado["sectores"].get(cs, {}).get("fecha", "") < vence):
            tareas.append(("sector", cs, e))
    # primero lo que cumple el perfil del usuario
    tareas.sort(key=lambda t: not en_perfil({"precio": t[2]["precio"], "hab": t[2]["hab"], "banos": t[2]["banos"]}))
    vistos, n = set(), 0
    for tipo, clave, e in tareas:
        # siempre queda al menos 1 pedido para la opinión de la tanda
        if clave in vistos or n >= MAX_BUSQUEDAS_CORRIDA or not ia.disponible() or not cuota_ok(estado, reserva=1):
            continue
        vistos.add(clave)
        nombre = clave.split("|", 1)[1]
        if tipo == "barrio":
            d = investigar_barrio(e.get("barrio") or e["barrio_portal"], e["municipio"])
            dest = estado["barrios"]
        elif tipo == "sector":
            d = investigar_sector(e)
            dest = estado["sectores"]
        else:
            d = investigar_conjunto(e["conjunto"], e.get("barrio") or e["barrio_portal"], e["municipio"])
            dest = estado["conjuntos"]
        contar_ia(estado)
        n += 1
        if d:
            d["fecha"] = ahora().isoformat(timespec="minutes")
            dest[clave] = d
            print(f"  🔎 {tipo} {nombre}: {'encontrado' if d.get('encontrado') else 'sin información'} · {len(d.get('_fuentes', []))} fuentes")
    escribir(F_ESTADO, estado)

    # 3. Opinar: una solicitud por tanda (los disponibles sin opinión)
    pend = [k for k, g in estado["grupos"].items() if k in G and "opinion" not in g and g["exp"]["disponible"] is not False]
    pend.sort(key=lambda k: (k not in pedidos, not en_perfil(G[k][0]), k not in hechos))   # primero lo que busca el usuario
    if pend and ia.disponible() and cuota_ok(estado):
        lote = [resumen_para_ia(k, estado["grupos"][k]["exp"], estado) for k in pend[:LOTE_GEMINI]]
        ops = opinar(lote, estado)
        if not ops and cuota_ok(estado):      # sin Gemini: Groq en tandas pequeñas
            for i in range(0, min(len(lote), LOTE_GROQ * 3), LOTE_GROQ):
                if not cuota_ok(estado):
                    break
                ops.update(opinar(lote[i:i + LOTE_GROQ], estado))
        for k, o in ops.items():
            if k in estado["grupos"]:
                o["fecha"] = ahora().isoformat(timespec="minutes")
                estado["grupos"][k]["opinion"] = o
        print(f"Opiniones: {len(ops)} de {len(lote)} enviados")

    # Limpiar grupos que ya no están en la página
    for k in [k for k in estado["grupos"] if k not in G]:
        del estado["grupos"][k]
    escribir(F_ESTADO, estado)
    publicar(estado, G)
    print(f"Listo en {round(time.time() - T0)} s. Uso de IA hoy en esta función: {estado['uso_ia'].get('n', 0) if 'uso_ia' in estado else 0}")


def publicar(estado, G):
    """docs/data/investigaciones.json: lo que muestra la página (compacto)."""
    items, por_id = {}, {}
    for k, g in estado["grupos"].items():
        e = g.get("exp")
        if not e:
            continue
        conj = estado["conjuntos"].get(clave_conj(e)) if e.get("conjunto") else None
        binv = estado["barrios"].get(clave_barrio(e)) if not e.get("barrio_ficha") else None
        sinv = estado["sectores"].get(clave_sector(e)) if e.get("sector") and clave_sector(e) else None
        c = e.get("cerca") or {}
        items[k] = {
            "fecha": e["fecha"], "disponible": e["disponible"], "publicado": e["publicado"], "modificado": e["modificado"],
            "video": e["video"], "versiones": e["versiones"], "rasgos": [r for r, v in e["rasgos"].items() if v],
            "conjunto": e.get("conjunto"), "barrio": e.get("barrio"),
            "sector": {k: v for k, v in (e.get("sector") or {}).items() if k != "celda"} if isinstance(e.get("sector"), dict) else None,
            "sector_inv": {kk: sinv.get(kk) for kk in ("encontrado", "resumen", "hechos_sector", "riesgos", "del_barrio_no_del_sector", "_fuentes", "fecha")} if sinv else None,
            "ubic": {"tipo": e["ubicacion"]["tipo"], "barrio": (e["ubicacion"].get("osm") or {}).get("barrio"),
                     "via": (e["ubicacion"].get("osm") or {}).get("via")},
            "metro": e["metro"],
            "cerca": {kk: c.get(kk) for kk in ("supermercados", "centros_comerciales", "salud", "parques", "colegios", "paraderos", "carcel")} if c else None,
            "barrio_ficha": e.get("barrio_ficha"),
            "barrio_inv": {kk: binv.get(kk) for kk in ("seguridad", "resumen", "hechos", "problemas", "contexto_municipio", "_fuentes", "fecha")} if binv else None,
            "conjunto_inv": {kk: conj.get(kk) for kk in ("encontrado", "direccion", "calificacion", "a_favor", "quejas", "noticias", "resumen", "_fuentes", "fecha")} if conj else None,
            "alertas": e["alertas"], "opinion": g.get("opinion"),
        }
        for i in e["ids"]:
            por_id[i] = k
    escribir(F_SALIDA, {"fecha": ahora().isoformat(timespec="minutes"), "perfil": PERFIL, "por_id": por_id, "items": items},
             compacto=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", nargs="*", default=[], help="anuncios a investigar ya")
    ap.add_argument("--max", type=int, default=None, help="máximo de expedientes en esta corrida")
    a = ap.parse_args()
    if a.max is not None:
        MAX_RECOLECTAR = a.max
    principal(a.url)
