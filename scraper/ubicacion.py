"""Verificación de ubicaciones.

Muchos portales no dan la dirección: ponen todos los anuncios de un sector (o de todo el
municipio) en el mismo punto, aunque digan barrios distintos. Aquí:

1. Se detectan esos puntos compartidos (ubicación aproximada).
2. Se toma el barrio que dice el propio anuncio (campo barrio o, en Rentola, el título).
   No se inventa: si el anuncio no dice barrio, queda en el centro del municipio.
3. El punto general de cada barrio es la mediana de los anuncios con ubicación exacta de
   ese barrio (si hay 3 o más y están agrupados). Si no, se usa OpenStreetMap (Nominatim,
   caché en data/geo_barrios.json), pero solo si hay apartamentos a menos de 1,5 km de
   ese punto (evita homónimos rurales y centros de corregimientos enormes).
4. Los anuncios aproximados se mueven al punto de su barrio; los "exactos" que quedan a
   más de 3 km del barrio que declaran se marcan como dudosos y también se mueven.

Solo cambia lo que se publica; los datos guardados conservan la coordenada original.
"""
import json
import math
import re
import statistics
import time
import unicodedata
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
F_CACHE = RAIZ / "data" / "geo_barrios.json"
F_INFO = RAIZ / "docs" / "barrios_info.json"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "buscador-arriendos-valle-aburra/1.0 (uso personal, no comercial)"
VIEWBOX = "-75.75,6.40,-75.45,6.05"
MAX_CONSULTAS = 250          # consultas nuevas a OSM por corrida (1 por segundo)
PUNTO_COMPARTIDO = 5         # anuncios en el mismo punto exacto → ubicación aproximada
DUDOSA_M = 3000              # coordenada "exacta" a más de esto del barrio declarado

TIPOS_ZONA = {"suburb", "neighbourhood", "quarter", "village", "hamlet", "residential",
              "city_district", "isolated_dwelling", "locality", "borough", "town"}
GENERICOS = {"medellin", "sabaneta", "envigado", "itagui", "la estrella", "antioquia",
             "colombia", "sin barrio", "centro", "unnamed road", "valle de aburra"}


def norm(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"^(barrio|urbanizacion|urb\.?|sector|vereda)\s+", "", s.strip())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(de|del)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def dist_m(a_lat, a_lon, b_lat, b_lon):
    dy = (a_lat - b_lat) * 111_320
    dx = (a_lon - b_lon) * 111_320 * math.cos(math.radians(a_lat))
    return math.hypot(dx, dy)


def _clave_punto(it):
    return (round(it["lat"], 4), round(it["lon"], 4)) if it.get("lat") and it.get("lon") else None


def _leer_cache():
    try:
        return json.loads(F_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _guardar_cache(cache):
    F_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = F_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(F_CACHE)


def _barrios_investigados():
    """Nombres de barrio de la investigación (para reconocerlos en los títulos)."""
    try:
        d = json.loads(F_INFO.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for m in d.get("municipios", []):
        for b in m.get("barrios", []):
            out.setdefault(m["municipio"], set()).add(b["nombre"])
    return out


class Geo:
    def __init__(self, log=print):
        self.cache = _leer_cache()
        self.log = log
        self.consultas = 0
        self.fallos = 0
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA

    def _buscar(self, q):
        if self.consultas >= MAX_CONSULTAS:
            return None
        self.consultas += 1
        time.sleep(1.1)  # política de uso de Nominatim: máximo 1 consulta por segundo
        try:
            r = self.s.get(NOMINATIM, params={"q": q + ", Antioquia, Colombia", "format": "jsonv2",
                                              "limit": 5, "viewbox": VIEWBOX, "bounded": 1}, timeout=(5, 10))
            if not r.ok:
                self.fallos += 1
                if r.status_code in (403, 429) or self.fallos >= 3:
                    self.consultas = MAX_CONSULTAS  # OSM no responde bien: se sigue en otra corrida
                return None
            return r.json()
        except (requests.RequestException, ValueError):
            self.fallos += 1
            if self.fallos >= 3:
                self.consultas = MAX_CONSULTAS
            return None

    def lugar(self, nombre, municipio):
        """Punto de un barrio (o del municipio si nombre es None) según OpenStreetMap."""
        clave = f"{municipio}|{norm(nombre) if nombre else ''}"
        if clave in self.cache:
            return self.cache[clave]
        res = self._buscar(f"{nombre}, {municipio}" if nombre else municipio)
        if res is None:
            return None  # sin respuesta: se reintenta en otra corrida
        muni = norm(municipio)
        mejor = None
        for x in res:
            if muni not in norm(x.get("display_name")):
                continue
            tipo = x.get("addresstype") or x.get("type")
            nom = norm(x.get("name"))
            if nombre is None:
                if tipo in ("town", "city", "municipality", "village"):
                    mejor = {"lat": float(x["lat"]), "lon": float(x["lon"]), "tipo": "municipio"}
                    break
                continue
            if tipo in TIPOS_ZONA and (norm(nombre) in nom or nom in norm(nombre)):
                mejor = {"lat": float(x["lat"]), "lon": float(x["lon"]), "tipo": "zona"}
                break
            if mejor is None and nom.startswith(norm(nombre)):
                # lugar con el mismo nombre dentro del municipio (parque, iglesia del barrio…)
                mejor = {"lat": float(x["lat"]), "lon": float(x["lon"]), "tipo": "lugar"}
        self.cache[clave] = mejor
        return mejor


def _barrio_valido(b, municipio):
    n = norm(b)
    return bool(n) and n not in GENERICOS and n != norm(municipio) and not re.search(r"\d", n) and len(n) >= 3


def ubicar(items, log=print, consultar=True):
    """Marca y corrige las ubicaciones de los anuncios (modifica los dicts recibidos)."""
    geo = Geo(log)
    if not consultar:
        geo.consultas = MAX_CONSULTAS

    # 1) Puntos compartidos = ubicación aproximada
    #    Además: si en el mismo punto exacto hay anuncios que dicen barrios distintos,
    #    esa coordenada no es la dirección real (el portal la reutiliza) → también aproximada.
    cuenta, barrios_pto = {}, {}
    for it in items:
        k = _clave_punto(it)
        if k:
            cuenta[k] = cuenta.get(k, 0) + 1
            b_ = norm(it.get("barrio"))
            if b_ and b_ != norm(it["municipio"]):
                barrios_pto.setdefault(k, set()).add(b_)
    for it in items:
        it.pop("aprox", None)
        it.pop("ubic", None)
        k = _clave_punto(it)
        it["_aprox"] = bool(k and (cuenta[k] >= PUNTO_COMPARTIDO or len(barrios_pto.get(k, ())) > 1))

    # 2) Catálogo de barrios por municipio (lo que dicen los portales + la investigación)
    catalogo = {m: {norm(n): n for n in ns} for m, ns in _barrios_investigados().items()}
    for it in items:
        if it["fuente"] != "Rentola" and _barrio_valido(it.get("barrio"), it["municipio"]):
            catalogo.setdefault(it["municipio"], {}).setdefault(norm(it["barrio"]), it["barrio"].strip())

    # 3) Barrio de cada anuncio. Rentola: su campo "barrio" es una referencia del mapa
    #    (vía, glorieta, colegio), así que se busca el barrio en el título.
    for it in items:
        b = None
        if it["fuente"] == "Rentola":
            texto = " " + norm(f"{it.get('titulo') or ''} {it.get('barrio') or ''}") + " "
            for n, original in sorted(catalogo.get(it["municipio"], {}).items(), key=lambda kv: -len(kv[0])):
                if n not in GENERICOS and len(n) >= 4 and f" {n} " in texto:
                    b = original
                    break
            if not b:
                # Formato "Apartamento en Arriendo, <barrio>, <municipio>": se toma el texto del anuncio
                m = re.search(r"arriendo\s*,\s*([^,]{3,40}?)\s*,\s*([^,]+)", it.get("titulo") or "", re.I)
                if m and norm(m.group(2)).startswith(norm(it["municipio"])[:5]) and _barrio_valido(m.group(1), it["municipio"]):
                    b = m.group(1).strip().title()
            it["barrio"] = b  # se descarta la referencia del mapa si no es un barrio del anuncio
        elif _barrio_valido(it.get("barrio"), it["municipio"]):
            b = it["barrio"]
        it["_b"] = (it["municipio"], norm(b)) if b else None

    # 3b) Ya con el barrio de cada anuncio: si a menos de 15 m hay anuncios que dicen barrios
    #     distintos, esa coordenada no es la dirección real (el portal la reutiliza) → aproximada.
    con_pto = [it for it in items if it.get("lat") and it.get("lon") and not it["_aprox"]]
    con_pto.sort(key=lambda x: x["lat"])
    for i, a_ in enumerate(con_pto):
        ka = a_["_b"][1] if a_.get("_b") else ""
        for b_ in con_pto[i + 1:]:
            if b_["lat"] - a_["lat"] > 0.00015:   # ~16 m en latitud: ya no hay más cerca
                break
            kb = b_["_b"][1] if b_.get("_b") else ""
            if ka and kb and ka != kb and dist_m(a_["lat"], a_["lon"], b_["lat"], b_["lon"]) < 15:
                a_["_aprox"] = b_["_aprox"] = True

    # 4) Punto de cada barrio: mediana de anuncios exactos > OSM
    exactos = {}
    for it in items:
        if it["_b"] and not it["_aprox"] and it.get("lat"):
            exactos.setdefault(it["_b"], []).append((it["lat"], it["lon"]))
    exactos_muni = {}
    for it in items:
        if not it["_aprox"] and it.get("lat"):
            exactos_muni.setdefault(it["municipio"], []).append((it["lat"], it["lon"]))
    nombres = {}
    for it in items:
        if it["_b"]:
            nombres.setdefault(it["_b"], it["barrio"])
    puntos = {}
    for clave, nombre in nombres.items():
        osm = geo.lugar(nombre, clave[0])
        med = None
        pts = exactos.get(clave, [])
        if len(pts) >= 3:
            lat, lon = statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts)
            cerca = [p for p in pts if dist_m(lat, lon, *p) < 1500]
            if len(cerca) >= max(3, len(pts) * 0.6):
                med = {"lat": statistics.median(p[0] for p in cerca), "lon": statistics.median(p[1] for p in cerca), "tipo": "anuncios"}
        # El punto de OSM solo vale si hay apartamentos (con ubicación exacta) cerca: así se
        # descartan homónimos rurales (vereda El Salado) o centros de corregimientos enormes.
        if osm:
            cerca_osm = [p for p in exactos_muni.get(clave[0], []) if dist_m(osm["lat"], osm["lon"], *p) < 1500]
            if not cerca_osm:
                osm = None
        if med:
            puntos[clave] = med       # donde están de verdad los anuncios exactos de ese barrio
        elif osm:
            puntos[clave] = osm
    centros = {m: geo.lugar(None, m) for m in {it["municipio"] for it in items}}

    # 5) Aplicar
    n_barrio = n_muni = n_dudosa = 0
    for it in items:
        p = puntos.get(it["_b"]) if it["_b"] else None
        if it["_aprox"]:
            if p:
                it["lat"], it["lon"], it["ubic"] = round(p["lat"], 5), round(p["lon"], 5), "barrio"
                n_barrio += 1
            else:
                c = centros.get(it["municipio"])
                if c:
                    it["lat"], it["lon"] = round(c["lat"], 5), round(c["lon"], 5)
                it["ubic"] = "municipio"
                n_muni += 1
        elif p and p["tipo"] in ("zona", "anuncios") and it.get("lat"):
            if dist_m(it["lat"], it["lon"], p["lat"], p["lon"]) > DUDOSA_M:
                it["lat"], it["lon"], it["ubic"] = round(p["lat"], 5), round(p["lon"], 5), "barrio"
                n_dudosa += 1
        if it.get("ubic"):
            it["aprox"] = True
        it.pop("_aprox", None)
        it.pop("_b", None)
    _guardar_cache(geo.cache)
    log(f"Ubicaciones: {n_barrio} al punto de su barrio, {n_muni} solo con municipio, "
        f"{n_dudosa} corregidas (lejos del barrio que dicen), {geo.consultas if consultar else 0} consultas a OSM")
    return {"barrio": n_barrio, "municipio": n_muni, "dudosa": n_dudosa}
