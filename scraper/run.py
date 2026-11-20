"""Corre los scrapers, fusiona con los datos guardados y genera la carpeta pública.

Datos maestros (se guardan en git, un registro por línea para diffs pequeños):
    data/anuncios.jsonl   todos los anuncios conocidos (incluye inactivos, para no
                          volver a marcarlos como "nuevos" si reaparecen)
    data/galeria.jsonl    fotos / video / recorrido 3D por anuncio
    data/estado.json      cursores de cada fuente
    data/corridas.json    historial de corridas

Salida pública (se genera en cada corrida; no se guarda en git):
    docs/data/listings.json      solo anuncios activos y disponibles
    docs/data/meta.json          resumen
    docs/data/galeria/NN.json    galerías repartidas en 64 archivos (carga bajo demanda)

Uso:
    python -m scraper.run                 # corrida normal
    python -m scraper.run --rapido        # 1 página por fuente/municipio (para probar)
    python -m scraper.run --solo rentola  # solo una fuente
"""
import argparse
import json
import re
import shutil
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import config
from scraper import analisis, fotos, noticias, normalizar, seleccion, ubicacion
from scraper.sources import (fincaraiz, rentola, albertoalvarez, metrocuadrado, ciencuadras, espaciourbano,
                             arrendamientosenvigado, softin_livewire, escala, santafe, nutibara, doomos,
                             softinm, duquegiraldo, wasi_sitios, vicasa, viviendo)

FUENTES = [fincaraiz, rentola, albertoalvarez, metrocuadrado,
           # Portales e inmobiliarias agregados el 2026-11-16 (prioridad: el sur del valle)
           ciencuadras, espaciourbano, arrendamientosenvigado, softin_livewire, escala, santafe, nutibara, doomos,
           # Inmobiliarias originales que antes solo llegaban copiadas por Rentola
           softinm, duquegiraldo, wasi_sitios, vicasa,
           viviendo]   # portal de Domus: Maxibienes, Portada y otras (sin teléfono: lo da tras un formulario)
# Fuentes cuyo listado no trae galería completa: se visita el detalle de los nuevos
T0 = time.time()  # inicio de la corrida (topes de tiempo)
CON_DETALLE = {"Rentola": rentola, "Alberto Álvarez": albertoalvarez,
               "Ciencuadras": ciencuadras, "Espacio Urbano": espaciourbano, "Arrendamientos Santa Fe": santafe,
               "Arrendamientos Nutibara": nutibara, "Doomos": doomos,
               # Misma plantilla para 4 inmobiliarias
               "Inmobiliaria La Estrella": softin_livewire, "Arrendamientos Villacruz": softin_livewire,
               "Arrendamientos El Castillo": softin_livewire, "Livin Inmobiliaria": softin_livewire,
               # Plantilla Softinm clásica (5 inmobiliarias) y Duque Giraldo: galería, estrato y contacto de la ficha
               "Arrendamientos Sukasa": softinm, "Mi Lugar Inmobiliario": softinm, "Arrendamientos La Aldea": softinm,
               "Survivienda": softinm, "Su Vivienda": softinm, "Duque Giraldo": duquegiraldo,
               "Viviendo": viviendo}

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data"
PUB = RAIZ / "docs" / "data"
F_ANUNCIOS = DATA / "anuncios.jsonl"
F_GALERIA = DATA / "galeria.jsonl"
F_ESTADO = DATA / "estado.json"
F_CORRIDAS = DATA / "corridas.json"
F_HISTORIAL = DATA / "historial_mercado.json"
SHARDS = 64

TZ = timezone(timedelta(hours=-5))  # Colombia
CAMPOS_GALERIA = ("fotos", "video", "tour")
DET_VERSION = {**{f: 1 for f in CON_DETALLE}, "Rentola": 3, "Alberto Álvarez": 4, "Viviendo": 2}  # súbelo (por portal) cuando el detalle extraiga campos nuevos


def ahora():
    return datetime.now(TZ).replace(microsecond=0)


def leer_json(path, defecto):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return defecto


def leer_jsonl(path):
    out = {}
    if not path.exists():
        return out
    for linea in path.read_text(encoding="utf-8").splitlines():
        if linea.strip():
            o = json.loads(linea)
            out[o["id"]] = o
    return out


def escribir(path, texto):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    tmp.replace(path)


def escribir_jsonl(path, registros):
    lineas = [json.dumps(r, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
              for _, r in sorted(registros.items())]
    escribir(path, "\n".join(lineas) + "\n")


def shard(id_):
    """Hash simple, idéntico al de app.js (fragmentoGaleria)."""
    h = 0
    for ch in id_:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h % SHARDS


def valido(l):
    p = l.get("precio")
    return bool(l.get("id") and l.get("url") and p and config.PRECIO_MIN_GUARDAR <= p <= config.PRECIO_MAX_GUARDAR)


def guardar_galeria(galeria, id_, datos):
    """Guarda fotos/video/tour. No reemplaza una galería más completa por una más pobre."""
    fotos = [f for f in (datos.get("fotos") or []) if isinstance(f, str) and f.startswith("http")]
    previa = galeria.get(id_, {"id": id_})
    if len(fotos) >= len(previa.get("f", [])):
        if fotos:
            previa["f"] = fotos
    if datos.get("video"):
        previa["v"] = datos["video"]
    if datos.get("tour"):
        previa["t"] = datos["tour"]
    if len(previa) > 1:
        galeria[id_] = previa
    return previa


def resumen_galeria(item, gal):
    """Copia al anuncio el número de fotos y si tiene video/3D (para mostrarlo sin cargar la galería)."""
    for k in ("nf", "v", "t"):
        item.pop(k, None)
    if gal.get("f"):
        item["nf"] = len(gal["f"])
    if gal.get("v"):
        item["v"] = 1
    if gal.get("t"):
        item["t"] = 1


def fusionar(anuncios, galeria, nuevo, ts):
    """Actualiza o crea un anuncio. Devuelve 'nuevo', 'bajo', 'subio', 'retirado' o None."""
    inicial = nuevo.pop("_inicial", False)
    media = {k: nuevo.pop(k, None) for k in CAMPOS_GALERIA}
    disponible = nuevo.pop("disponible", None)
    previo = anuncios.get(nuevo["id"])
    limpio = {k: v for k, v in nuevo.items() if v not in (None, "", [])}

    if disponible is False:
        # El portal dice que ya no está disponible: se marca y deja de mostrarse
        if previo:
            previo["disp"] = False
            previo["visto"] = ts
            return "retirado"
        return None

    gal = guardar_galeria(galeria, nuevo["id"], media)
    if previo is None:
        limpio.update({"visto1": ts, "visto": ts, "hist": [[ts[:10], nuevo["precio"]]]})
        if inicial:
            limpio["ini"] = 1  # descubierto en la carga inicial, no es una oferta nueva
        if disponible is True:
            limpio["disp"] = True
        resumen_galeria(limpio, gal)
        anuncios[nuevo["id"]] = limpio
        return "nuevo"

    evento = None
    if nuevo["precio"] != previo.get("precio"):
        evento = "bajo" if nuevo["precio"] < previo.get("precio", 0) else "subio"
        previo.setdefault("hist", []).append([ts[:10], nuevo["precio"]])
        previo["cambio"] = ts
    for k, v in limpio.items():
        previo[k] = v
    previo["visto"] = ts
    if disponible is True:
        previo["disp"] = True
    resumen_galeria(previo, gal)
    return evento


def enriquecer(anuncios, galeria, ts, log):
    """Visita el detalle de los anuncios que aún no lo tienen (galería, fecha, disponibilidad).

    Cada portal va en su propio hilo: la pausa entre peticiones es por dominio, así que visitar
    varios portales a la vez no aumenta la carga sobre ninguno y multiplica lo que se alcanza."""
    from concurrent.futures import ThreadPoolExecutor
    fin = T0 + config.MINUTOS_MAX_DETALLE * 60
    with ThreadPoolExecutor(max_workers=len(CON_DETALLE)) as ex:
        trabajos = [(f, ex.submit(_enriquecer_fuente, f, m, anuncios, galeria, fin)) for f, m in CON_DETALLE.items()]
        res = {}
        for fuente, t in trabajos:
            try:
                res[fuente], lineas = t.result()
            except Exception as e:  # un portal con fallo inesperado no tumba la corrida
                res[fuente], lineas = {"detalles": 0, "retirados": 0, "error": f"{type(e).__name__}: {e}"}, []
                traceback.print_exc()
            for linea in lineas:
                log(linea)
    return res


def _enriquecer_fuente(fuente, modulo, anuncios, galeria, fin):
    lineas = []
    log = lineas.append
    # det < DET_VERSION: nunca visitado o visitado antes de que se extrajeran los campos actuales
    pendientes = [a for a in list(anuncios.values())
                  if a.get("fuente") == fuente and (a.get("det") or 0) < DET_VERSION[fuente] and a.get("disp") is not False
                  and a.get("precio", 0) <= config.PRECIO_MAX_GUARDAR]
    # Primero el sur (lo que busca el usuario), luego lo visto más recientemente y lo más nuevo
    pendientes.sort(key=lambda a: (seleccion.es_del_sur(a, {}), a.get("visto", "")[:10], a.get("visto1", "")), reverse=True)
    hechos = retirados = 0
    for a in pendientes[: config.DETALLE_MAX_POR_FUENTE]:
        if time.time() > fin:
            log("   tope de tiempo alcanzado: el resto de detalles sigue en la próxima corrida")
            break
        try:
            d = modulo.detalle(a["url"])
        except Exception as e:  # un detalle fallido no detiene la corrida
            log(f"   detalle {a['id']}: {type(e).__name__}")
            continue
        a["det"] = DET_VERSION[fuente]
        hechos += 1
        if d.get("disponible") is False:
            a["disp"] = False
            retirados += 1
            continue
        if d.get("disponible") is True:
            a["disp"] = True
        if d.get("publicado") and not a.get("publicado"):
            a["publicado"] = d["publicado"]
        # Datos que el listado no trae y el detalle sí (solo si faltan)
        for k in ("banos", "parq", "estrato", "lat", "lon", "mascotas", "amen", "piso", "area", "hab", "admin", "anunciante", "tel", "wa", "barrio", "orig", "pub_h"):
            if d.get(k) not in (None, "", []) and a.get(k) in (None, "", [], 0):
                a[k] = d[k]
        resumen_galeria(a, guardar_galeria(galeria, a["id"], d))
    res = {"detalles": hechos, "retirados": retirados, "pendientes": max(0, len(pendientes) - hechos)}
    log(f"   detalle {fuente}: {hechos} visitados, {retirados} ya no disponibles, {res['pendientes']} pendientes")
    return res, lineas


# Recuadro del Valle de Aburrá: coordenadas fuera de aquí son errores del portal
ZONA = {"lat": (6.0, 6.45), "lon": (-75.8, -75.4)}


# Línea pública de arriendos de cada inmobiliaria (publicada en su propia web/fichas) para los
# anuncios que no traen asesor: así siempre hay a quién llamar o escribir sin pasar por terceros
CONTACTO_OFICINA = {
    "Arrendamientos Santa Fe": ("6044484015", "573044403848"),     # fichas /propiedad/{codigo}
    "Arrendamientos Nutibara": (None, "573117803657"),             # anutibara.com/contacto
    "Arrendamientos Villacruz": ("6042506000", "573116943863"),    # bloque "Asesor" de las fichas
    "Arrendamientos El Castillo": ("6042385343", "573116962731"),
    "Inmobiliaria La Estrella": ("6042500646", "573217158566"),
    "Livin Inmobiliaria": ("6044444989", "573184444845"),
    "Escala Inmobiliaria": ("3009122101", "573009122101"),         # si aún no se conoce el asesor
}


def limpiar(anuncios):
    """Correcciones idempotentes sobre datos ya guardados."""
    for a in anuncios.values():
        lat, lon = a.get("lat"), a.get("lon")
        if lat is not None and lon is not None:
            if not (ZONA["lat"][0] < lat < ZONA["lat"][1] and ZONA["lon"][0] < lon < ZONA["lon"][1]):
                a.pop("lat", None)
                a.pop("lon", None)
        if a.get("estrato") is not None and not (1 <= a["estrato"] <= 6):
            a.pop("estrato")  # valor erróneo del portal (p. ej. 110)
        ofi = CONTACTO_OFICINA.get(a.get("fuente"))
        if ofi:
            if not a.get("tel") and not a.get("wa"):
                a["tel"], a["wa"] = ofi
            if (a.get("tel"), a.get("wa")) == ofi:
                a["ofi"] = 1   # es la línea general de la inmobiliaria, no la del asesor
            else:
                a.pop("ofi", None)
        if a.get("fuente") == "Alberto Álvarez" and not a.get("tel"):
            a["tel"], a["wa"] = albertoalvarez.TEL_INMOBILIARIA, albertoalvarez.WA_INMOBILIARIA
        if a.get("fuente") == "Rentola":
            area = a.get("area")
            if area and area >= 250 and area % 10 == 0 and 20 <= area // 10 <= 200:
                a["area"] = area // 10
            if a.get("barrio") and rentola._NO_BARRIO.search(a["barrio"]):
                a.pop("barrio")


def _dist_m(a, b):
    """Distancia aproximada en metros entre dos anuncios (lat/lon)."""
    import math
    dy = (a["lat"] - b["lat"]) * 111_320
    dx = (a["lon"] - b["lon"]) * 111_320 * math.cos(math.radians(a["lat"]))
    return math.hypot(dx, dy)


def _clave_punto(it):
    return (round(it["lat"], 4), round(it["lon"], 4)) if it.get("lat") and it.get("lon") else None


def _norm_barrio(it):
    b = (it.get("barrio") or "").lower().strip()
    b = re.sub(r"[áàä]", "a", b); b = re.sub(r"[éè]", "e", b); b = re.sub(r"[íì]", "i", b)
    b = re.sub(r"[óò]", "o", b); b = re.sub(r"[úùü]", "u", b)
    muni = (it.get("municipio") or "").lower()
    if not b or b in (muni, "medellin", "itagui", "la estrella", "sabaneta", "envigado") or re.search(r"\d|unnamed", b):
        return ""
    return b


def _fotos_clave(it, galeria):
    """Nombres de archivo de las fotos: Rentola reutiliza las fotos de otros portales."""
    urls = [it.get("img")] + list((galeria.get(it["id"]) or {}).get("f") or [])
    out = set()
    for u in urls:
        if u:
            nombre = u.split("?")[0].rstrip("/").split("/")[-1]
            if len(nombre) >= 12 and not re.fullmatch(r"(?i)[a-z_\-]*\d{0,3}\.(jpe?g|png|webp)", nombre):
                out.add(nombre)
    return out


def clave_url(u):
    """Sitio + número del inmueble: la misma ficha aunque cambie el texto de la URL, www o mayúsculas."""
    p = urlparse(u)
    ruta = unquote(p.path).rstrip("/")
    m = re.search(r"(\d+-M\d+|\d{4,})$", ruta) or re.search(r"/inmueble/(\d{3,})(?:[/-]|$)", ruta)
    if not m and len(ruta) < 8:
        return None  # p. ej. sitios con el número tras "#": sin clave segura no se empareja
    return f'{p.netloc.lower().removeprefix("www.")}|{m.group(1) if m else ruta.lower()}'


# Código que el anunciante pone en Fincaraíz (code2) -> id del mismo anuncio en su propia web.
# Formatos verificados en el portal: Escala "10547191", Alberto Álvarez "AA-96990", Bemsa (bolsa de
# Alberto Álvarez) "786-49986", Arrendamientos Envigado "1143-80669".
def _ids_por_codigo(it):
    c = str(it.get("cod") or "").strip()
    nom = (it.get("anunciante") or "").lower()
    if not c:
        return []
    if "escala" in nom:
        return [f"es-{c}"]
    if "alberto alvarez" in nom or "alberto álvarez" in nom or "bemsa" in nom:
        return [f"aa-{c}"]
    if "arrendamientos envigado" in nom and "-" in c:
        return [f"ae-{c.split('-')[-1]}"]
    # Webs Wasi: el código en el portal es el mismo de su ficha (verificado: Alnago 10560886)
    if c.isdigit():
        if "alnago" in nom:
            return [f"ws-alnago-{c}"]
        if "la palma" in nom:
            return [f"ws-lapalma-{c}"]
        if "urve" in nom:
            return [f"ws-urve-{c}"]
    return []


def _tel_clave(it):
    """Últimos 10 dígitos del teléfono o WhatsApp del anuncio (para comparar entre portales)."""
    t = re.sub(r"\D", "", str(it.get("tel") or it.get("wa") or ""))
    return t[-10:] if len(t) >= 7 else None


def _sitio(it):
    u = it.get("orig") if it.get("fuente") == "Rentola" else it.get("url")
    if not u:
        return None
    return urlparse(u).netloc.lower().removeprefix("www.") or None


def marcar_duplicados(items, galeria=None):
    """Agrupa anuncios que son el mismo apartamento publicado en varias fuentes.

    Evidencia, de la más segura a la menos segura (se aplica en ese orden):
    0. el intermediario dice cuál es el original (Rentola sourceUrl) o el código del anunciante
       en el portal coincide con el de su propia web (Fincaraíz code2);
    1. canon, habitaciones y área (±2 m²) iguales y una foto compartida;
    2. lo mismo y ubicación exacta a menos de 250 m (la más cercana primero);
    3. dos copias de Rentola con el mismo título y barrio.
    Nunca se juntan dos anuncios distintos de la misma fuente (salvo copias de Rentola): suelen ser
    apartamentos distintos del mismo edificio, con las mismas fotos de zonas comunes.
    """
    galeria = galeria or {}
    for it in items:
        it.pop("dup", None)
    por_id = {it["id"]: it for it in items}
    aristas = []
    por_url = {clave_url(it["url"]): it for it in items if it.get("url") and it.get("fuente") != "Rentola"}
    por_url.pop(None, None)
    copias = {}
    for it in items:
        o = it.get("orig") and por_url.get(clave_url(it["orig"]))
        if o:
            aristas.append((0, it["id"], o["id"]))
        elif it.get("orig") and clave_url(it["orig"]):
            # dos copias de Rentola del mismo original (el original no lo tenemos)
            k = clave_url(it["orig"])
            if k in copias:
                aristas.append((0, it["id"], copias[k]))
            copias.setdefault(k, it["id"])
        for oid in _ids_por_codigo(it):
            o = por_id.get(oid)
            if o and o.get("municipio") == it.get("municipio"):
                aristas.append((0, it["id"], oid))
    # Mismo apartamento con otro nombre de inmobiliaria: la descripción trae el código de su propia web
    # (lo detecta scraper/investigar.py y lo deja en data/investigacion/alias.json)
    try:
        alias = json.loads((RAIZ / "data" / "investigacion" / "alias.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        alias = []
    for a, b in alias:
        if a in por_id and b in por_id and por_id[a].get("precio") == por_id[b].get("precio"):
            aristas.append((0, a, b))
    cand = {}
    for it in items:
        if it.get("area") and it.get("hab") and it.get("precio"):
            cand.setdefault((it["precio"], it["hab"]), []).append(it)
    fotos = {}
    for lista in cand.values():
        for i, a in enumerate(lista):
            for b in lista[i + 1:]:
                if abs(a["area"] - b["area"]) > 2:
                    continue
                if a.get("banos") and b.get("banos") and a["banos"] != b["banos"]:
                    continue
                for x in (a, b):
                    if x["id"] not in fotos:
                        fotos[x["id"]] = _fotos_clave(x, galeria)
                antes = len(aristas)
                if fotos[a["id"]] & fotos[b["id"]]:
                    aristas.append((1, a["id"], b["id"]))
                elif a.get("lat") and b.get("lat") and not a.get("aprox") and not b.get("aprox"):
                    d = _dist_m(a, b)
                    if d <= 250:
                        aristas.append((2 + d / 1000, a["id"], b["id"]))
                elif a["fuente"] == b["fuente"] == "Rentola":
                    ta, tb = _norm_barrio({"barrio": a.get("titulo")}), _norm_barrio({"barrio": b.get("titulo")})
                    if len(ta) > 20 and ta == tb and _norm_barrio(a) and _norm_barrio(a) == _norm_barrio(b):
                        aristas.append((3, a["id"], b["id"]))
                # Mismo anunciante (mismo teléfono) publicando el mismo apartamento en otro portal: canon,
                # habitaciones, baños, área (±1 m²) y municipio iguales. Los portales ubican distinto la
                # dirección y guardan sus propias copias de las fotos, así que esto lo detecta mejor.
                if (len(aristas) == antes and a["fuente"] != b["fuente"] and abs(a["area"] - b["area"]) <= 1
                        and a.get("banos") and a.get("banos") == b.get("banos") and a.get("municipio") == b.get("municipio")
                        and _tel_clave(a) and _tel_clave(a) == _tel_clave(b)):
                    aristas.append((2.5, a["id"], b["id"]))
    aristas.sort(key=lambda e: e[0])

    padre = {}

    def raiz(x):
        while padre.get(x, x) != x:
            padre[x] = padre.get(padre[x], padre[x])
            x = padre[x]
        return x

    # Por grupo: sitios de origen y rango de áreas. Cada anuncio cuenta por la web donde está
    # publicado de verdad (en Rentola, la página de donde lo copió): dos fichas distintas del mismo
    # sitio son dos apartamentos distintos. Rentola sin original conocido no restringe.
    # Una copia de Rentola cuyo original sí tenemos es ese mismo anuncio: no cuenta como sitio aparte.
    ligados = {a for peso, a, b in aristas if peso == 0 and por_id[a].get("fuente") == "Rentola"
               and por_id[b].get("fuente") != "Rentola"}
    fuentes = {it["id"]: (set() if it["id"] in ligados else {_sitio(it)} - {None}) for it in items}
    rango = {it["id"]: (it.get("area") or 0, it.get("area") or 0) for it in items}
    for peso, a, b in aristas:
        ra, rb = raiz(a), raiz(b)
        if ra == rb or fuentes[ra] & fuentes[rb]:
            continue  # ya juntos, o juntaría dos anuncios distintos de la misma fuente
        lo = min(x for x in (rango[ra][0], rango[rb][0]) if x) if (rango[ra][0] or rango[rb][0]) else 0
        hi = max(rango[ra][1], rango[rb][1])
        if peso > 0 and lo and hi - lo > 2:
            continue  # el grupo se estiraría a áreas distintas
        padre[ra] = rb
        fuentes[rb] |= fuentes[ra]
        rango[rb] = (lo, hi)
    miembros = {}
    for it in items:
        miembros.setdefault(raiz(it["id"]), []).append(it)
    n = 0
    for lista in miembros.values():
        if len(lista) > 1:
            n += 1
            for x in lista:
                x["dup"] = n
    return n


def estimar_estrato(items):
    """Si el portal no da el estrato, se indica el típico del barrio (estr_b) cuando al menos 3
    anuncios del mismo barrio lo informan y 2 de cada 3 coinciden. Se muestra como estimado."""
    por_barrio = {}
    for it in items:
        b = _norm_barrio(it)
        if b and it.get("estrato"):
            por_barrio.setdefault((it["municipio"], b), []).append(it["estrato"])
    n = 0
    for it in items:
        it.pop("estr_b", None)
        it.pop("estr_n", None)
        b = _norm_barrio(it)
        if it.get("estrato") or not b:
            continue
        vals = por_barrio.get((it["municipio"], b), [])
        if len(vals) >= 3:
            moda = max(set(vals), key=vals.count)
            if vals.count(moda) / len(vals) >= 2 / 3:
                it["estr_b"] = moda
                it["estr_n"] = f"{vals.count(moda)} de {len(vals)} del barrio"
                n += 1
    # Si no, por cercanía: anuncios con estrato real a menos de 300 m (ubicación exacta)
    con = [x for x in items if x.get("estrato") and x.get("lat") and not x.get("aprox")]
    m = 0
    for it in items:
        if it.get("estrato") or it.get("estr_b") or not it.get("lat") or it.get("aprox"):
            continue
        vals = [x["estrato"] for x in con if x["id"] != it["id"] and abs(x["lat"] - it["lat"]) < 0.003
                and _dist_m(x, it) <= 300]
        if len(vals) >= 3:
            moda = max(set(vals), key=vals.count)
            if vals.count(moda) / len(vals) >= 2 / 3:
                it["estr_b"] = moda
                it["estr_n"] = f"{vals.count(moda)} de {len(vals)} a menos de 300 m"
                m += 1
    print(f"Estrato estimado: {n} por barrio, {m} por cercanía")


def publicar(anuncios, galeria, corridas, ts, con_ia=True):
    """Genera docs/data con solo lo que debe verse en la página."""
    limite_inactivo = (ahora() - timedelta(days=config.DIAS_INACTIVO)).isoformat()
    visibles = []
    for a in anuncios.values():
        if a.get("disp") is False:
            continue  # el portal dice que ya no está disponible
        if a.get("visto", "") < limite_inactivo:
            continue  # no se ha vuelto a ver: probablemente ya se arrendó
        visibles.append({k: v for k, v in a.items() if k not in ("det",)})
    # Verifica ubicaciones: los que no tienen dirección exacta van al punto de su barrio
    normalizar.normalizar(visibles, log=print)
    ubicacion.ubicar(visibles, log=print)
    normalizar.unificar_barrios(visibles)
    visibles.sort(key=lambda x: x.get("visto1", ""), reverse=True)


    # Solo se publican los que están dentro del tope de precio (el análisis de arriba
    # usa todos los guardados para que las medianas reflejen el mercado completo)
    visibles = [v for v in visibles if v.get("precio", 0) <= config.PRECIO_MAX_GUARDAR]
    # ...y que no lleven más de PUBLICACION_MAX_DIAS publicados (si el portal da la fecha)
    limite_pub = (ahora() - timedelta(days=config.PUBLICACION_MAX_DIAS)).date().isoformat()
    antes = len(visibles)
    visibles = [v for v in visibles if not v.get("publicado") or v["publicado"][:10] >= limite_pub]
    print(f"Sin publicar por antigüedad (> {config.PUBLICACION_MAX_DIAS} días): {antes - len(visibles)}")
    estimar_estrato(visibles)
    # Comprueba que las fotos abran: las que no, se quitan (y no se dice que tiene fotos)
    fotos_ok = fotos.verificar(visibles, galeria, log=print)
    # La misma foto en dos tamaños (_s/_l) o en dos servidores cuenta una vez
    por_id = {v["id"]: v for v in visibles}
    for id_, lista in fotos_ok.items():
        orden, mejor = [], {}
        for u in lista:
            k = re.sub(r"_(s|m|l|p|x|360)(\.\w+)?$", "", u)            # mismo archivo en otro tamaño
            k = re.sub(r"^.*/(property-[\w-]+\.jpg)$", r"\1", k)   # misma foto en otro servidor
            if k not in mejor:
                orden.append(k)
                mejor[k] = u
            elif re.search(r"_(l|x)(\.\w+)?$", u):                      # se queda con la versión grande
                mejor[k] = u
        unicas = [mejor[k] for k in orden]
        fotos_ok[id_] = unicas
        if unicas and id_ in por_id:
            por_id[id_]["img"] = unicas[0]   # la portada en tamaño grande, no la miniatura del listado
        if id_ in por_id and por_id[id_].get("nf"):
            por_id[id_]["nf"] = len(unicas)
    marcar_duplicados(visibles, galeria)
    # Análisis automático sobre lo que de verdad se publica (fotos ya verificadas):
    # precio vs. similares, alertas e historial del mercado
    en_rango = [v for v in visibles if v.get("precio", 0) >= config.PRECIO_MIN_GUARDAR]
    hist = analisis.analizar(en_rango, leer_json(F_HISTORIAL, {}), ts[:10])
    escribir(F_HISTORIAL, json.dumps(hist, ensure_ascii=False, indent=1, sort_keys=True))
    escribir(PUB / "mercado.json", json.dumps({"historial": hist}, ensure_ascii=False, separators=(",", ":")))
    marcar_duplicados(visibles, galeria)

    # listings.json: un anuncio por línea
    cuerpo = ",\n".join(json.dumps(v, ensure_ascii=False, separators=(",", ":")) for v in visibles)
    escribir(PUB / "listings.json", f'{{"generado":"{ts}","items":[\n{cuerpo}\n]}}\n')
    # Selección con IA (imperdibles y mejores zonas): solo en las corridas que raspan
    if con_ia:
        try:
            seleccion.generar(visibles, PUB / "ia.json", log=print)
        except Exception as e:  # la IA nunca debe tumbar la publicación
            print(f"Selección IA: error {type(e).__name__}")
    noticias.publicar()

    # Galerías por fragmentos
    ids_visibles = {v["id"] for v in visibles}
    partes = [dict() for _ in range(SHARDS)]
    for id_, g in galeria.items():
        if id_ in ids_visibles:
            d = {k: g[k] for k in ("v", "t") if k in g}
            if fotos_ok.get(id_):
                d["f"] = fotos_ok[id_]
            if d:
                partes[shard(id_)][id_] = d
    carpeta = PUB / "galeria"
    if carpeta.exists():
        shutil.rmtree(carpeta)
    for i, p in enumerate(partes):
        escribir(carpeta / f"{i:02d}.json", json.dumps(p, ensure_ascii=False, separators=(",", ":")))

    escribir(PUB / "meta.json", json.dumps({
        "generado": ts,
        "total": len(visibles),
        "presupuesto_defecto": config.PRESUPUESTO_DEFECTO,
        "precio_max": config.PRECIO_MAX_GUARDAR,
        "publicacion_max_dias": config.PUBLICACION_MAX_DIAS,
        "dias_inactivo": config.DIAS_INACTIVO,
        "municipios": list(config.MUNICIPIOS),
        "shards": SHARDS,
        "corridas": corridas[-30:],
    }, ensure_ascii=False, indent=1))
    return len(visibles)


def migrar_si_hace_falta():
    """Si existe el formato anterior (docs/data/listings.json) y no el nuevo, lo convierte."""
    viejo = PUB / "listings.json"
    if F_ANUNCIOS.exists() or not viejo.exists():
        return
    try:
        items = json.loads(viejo.read_text(encoding="utf-8")).get("items", [])
    except json.JSONDecodeError:
        return
    anuncios = {}
    for it in items:
        it.pop("activo", None)
        it.pop("dup", None)
        anuncios[it["id"]] = it
    escribir_jsonl(F_ANUNCIOS, anuncios)
    meta = leer_json(PUB / "meta.json", {})
    if meta.get("corridas"):
        escribir(F_CORRIDAS, json.dumps(meta["corridas"], ensure_ascii=False, indent=1))
    print(f"Migrados {len(anuncios)} anuncios al formato nuevo")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rapido", action="store_true")
    ap.add_argument("--solo", help="NOMBRE de una fuente (p. ej. fincaraiz, ciencuadras, espaciourbano)")
    ap.add_argument("--sin-detalle", action="store_true", help="no visitar páginas de detalle")
    args = ap.parse_args()

    if args.rapido:
        config.FINCARAIZ_PAGINAS_NUEVOS = 1
        config.FINCARAIZ_PAGINAS_BARRIDO = 0
        config.RENTOLA_PAGINAS = 1
        config.ALBERTOALVAREZ_PAGINAS = 1
        config.DETALLE_MAX_POR_FUENTE = 5

    migrar_si_hace_falta()
    t0 = time.time()
    ts = ahora().isoformat()
    anuncios = leer_jsonl(F_ANUNCIOS)
    galeria = leer_jsonl(F_GALERIA)
    estado = leer_json(F_ESTADO, {})
    corridas = leer_json(F_CORRIDAS, [])
    resumen = {}

    def log(msg):
        print(msg, flush=True)

    for fuente in FUENTES:
        if args.solo and fuente.NOMBRE != args.solo:
            continue
        log(f"== {fuente.NOMBRE}")
        if fuente is wasi_sitios:
            # El sitemap de Alnago está incompleto: Fincaraíz da el código de cada anuncio que Alnago publica allí
            reciente = (ahora() - timedelta(days=config.DIAS_INACTIVO)).isoformat()
            estado.setdefault(fuente.NOMBRE, {})["extra_alnago"] = sorted(
                {a["cod"] for a in anuncios.values() if a.get("fuente") == "Fincaraíz" and str(a.get("cod") or "").isdigit()
                 and "alnago" in (a.get("anunciante") or "").lower() and a.get("visto", "") >= reciente
                 and a.get("precio", 0) <= config.PRECIO_MAX_GUARDAR})
        cont = {"leidos": 0, "nuevos": 0, "bajaron": 0, "retirados": 0, "error": None}
        conocidos = set(anuncios)
        try:
            for l in fuente.scrape(estado.setdefault(fuente.NOMBRE, {}), conocidos, log):
                cont["leidos"] += 1
                if not valido(l):
                    prev = anuncios.get(l.get("id"))
                    if prev and l.get("precio"):
                        # cambió a un precio fuera del rango: se guarda para que no se publique el viejo
                        prev["precio"] = l["precio"]
                        prev["visto"] = ts
                    continue
                ev = fusionar(anuncios, galeria, l, ts)
                conocidos.add(l["id"])
                if ev == "nuevo":
                    cont["nuevos"] += 1
                elif ev == "bajo":
                    cont["bajaron"] += 1
                elif ev == "retirado":
                    cont["retirados"] += 1
        except Exception as e:  # una fuente caída no detiene a las demás
            cont["error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        if estado[fuente.NOMBRE].pop("_omitida", False):
            cont["omitida"] = True  # la fuente decidió no consultar en esta corrida (no es una caída)
        resumen[fuente.NOMBRE] = cont
        log(f"   {cont}")
        escribir(F_ESTADO, json.dumps(estado, ensure_ascii=False, indent=1))

    detalle = {} if args.sin_detalle else enriquecer(anuncios, galeria, ts, log)
    limpiar(anuncios)

    # Borra lo que lleva demasiado tiempo sin verse
    limite_borrar = (ahora() - timedelta(days=config.DIAS_BORRAR)).isoformat()
    for id_ in [i for i, a in anuncios.items() if a.get("visto", "") < limite_borrar]:
        anuncios.pop(id_)
        galeria.pop(id_, None)
    for id_ in [i for i in galeria if i not in anuncios]:
        galeria.pop(id_)

    corridas.append({"ts": ts, "seg": round(time.time() - t0), "fuentes": resumen, "detalle": detalle})
    corridas = corridas[-60:]
    escribir_jsonl(F_ANUNCIOS, anuncios)
    escribir_jsonl(F_GALERIA, galeria)
    escribir(F_CORRIDAS, json.dumps(corridas, ensure_ascii=False, indent=1))
    # Noticias por barrio (cada 3 días) e investigación automática con IA
    try:
        noticias.actualizar(log=log)
    except Exception as e:
        log(f"Noticias: error {type(e).__name__}: {e}")
    n = publicar(anuncios, galeria, corridas, ts)
    log(f"Listo: {n} visibles de {len(anuncios)} guardados ({time.time() - t0:.0f}s)")
    if resumen and all(v["error"] for v in resumen.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
