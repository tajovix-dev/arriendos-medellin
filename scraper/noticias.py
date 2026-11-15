"""Noticias por barrio (cada 3 días) + investigación automática con IA.

1. Busca en Google Noticias (RSS gratis, sin clave) cada barrio investigado, últimos 30 días.
2. La IA clasifica las noticias nuevas: ¿es de este barrio? categoría y gravedad.
3. Si aparece algo nuevo y relevante, la IA vuelve a investigar el barrio: resumen reciente,
   nivel de alerta y si convendría cambiar la recomendación ✅ ⚠️ ⛔.
Guarda todo en data/noticias.json y publica docs/noticias_barrios.json para la página.
"""
import hashlib
import json
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from scraper import ia

RAIZ = Path(__file__).resolve().parent.parent
F_DATOS = RAIZ / "data" / "noticias.json"
F_INFO = RAIZ / "docs" / "barrios_info.json"
F_PUB = RAIZ / "docs" / "noticias_barrios.json"
CADA_DIAS = 3
VENTANA_DIAS = 30          # noticias que se buscan
GUARDAR_DIAS = 120         # cuánto se conservan
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
CATEGORIAS = ["extorsion", "homicidio", "hurto", "microtrafico", "rina_violencia", "accidente",
              "deslizamiento_inundacion", "servicios_agua_luz", "obras_movilidad", "positivo", "otro"]
MAX_POR_BARRIO = 8         # noticias nuevas por barrio que pasan a la IA (las más recientes)
LIMITE_SEG = 900           # tope de tiempo para la búsqueda + IA en una corrida
CLAVES = ("extorsi", "vacuna", "homicid", "asesin", "sicari", "muert", "baleado", "balacera", "disparo", "hurt", "robo",
          "atraco", "ladr", "microtr", "droga", "captur", "banda", "combo", "riña", "desliz", "inund", "emergencia",
          "derrumbe", "agua", "luz", "energía", "corte", "racionamiento", "obra", "vía", "cierre", "accidente", "seguridad",
          "inseguridad", "policía", "denuncia", "invasión", "ruido", "valorización")
NEGATIVAS = {"extorsion", "homicidio", "hurto", "microtrafico", "rina_violencia", "deslizamiento_inundacion", "servicios_agua_luz"}


def _ahora():
    return datetime.now(timezone(timedelta(hours=-5)))


def _leer():
    try:
        return json.loads(F_DATOS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ultima": None, "barrios": {}}


def _guardar(d):
    F_DATOS.parent.mkdir(parents=True, exist_ok=True)
    tmp = F_DATOS.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(F_DATOS)


def _barrios():
    info = json.loads(F_INFO.read_text(encoding="utf-8"))
    out = []
    for m in info.get("municipios", []):
        for b in m.get("barrios", []):
            out.append({"municipio": m["municipio"], "nombre": b["nombre"], "info": b})
    return out


def _buscar(nombre, municipio, dias):
    q = f'"{nombre}" {municipio} when:{dias}d'
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "es-419", "gl": "CO", "ceid": "CO:es-419"})
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=(5, 20))
        if not r.ok:
            return None
        root = ET.fromstring(r.content)
    except (requests.RequestException, ET.ParseError):
        return None
    items = []
    for it in root.findall(".//item"):
        titulo = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        try:
            fecha = parsedate_to_datetime(it.findtext("pubDate")).date().isoformat()
        except (TypeError, ValueError):
            fecha = None
        fuente = (it.findtext("source") or "").strip()
        if titulo and link:
            items.append({"id": hashlib.md5(link.encode()).hexdigest()[:12], "titulo": titulo, "link": link,
                          "fecha": fecha, "fuente": fuente})
    return items


def _interesa(titulo, nombre):
    t = titulo.lower()
    return nombre.lower() in t or any(k in t for k in CLAVES)


def _clasificar(lote):
    """lote: [(clave, barrio, municipio, item)] → asigna relevante/categoria/gravedad/resumen."""
    lineas = [f'{i}. Barrio "{b}" ({m}) — "{it["titulo"]}" ({it["fuente"]}, {it["fecha"]})' for i, (_, b, m, it) in enumerate(lote)]
    prompt = (
        "Eres analista de seguridad y calidad de vida para alguien que busca arriendo en el Valle de Aburrá (Colombia).\n"
        "Para cada titular decide si trata de verdad sobre ESE barrio de ESE municipio (no del estadio, un equipo de fútbol, "
        "una persona con ese apellido u otro lugar con el mismo nombre) y clasifícalo.\n"
        f"Categorías: {', '.join(CATEGORIAS)}. Gravedad 0 (nada) a 3 (muy grave para vivir ahí). "
        "Un accidente de tránsito o un hecho aislado de fútbol es gravedad 0-1 para vivir en el barrio.\n"
        'Responde JSON: {"items":[{"i":0,"relevante":true,"categoria":"hurto","gravedad":2,"resumen":"máx 120 caracteres"}]}\n\n'
        + "\n".join(lineas)
    )
    r = ia.preguntar_json(prompt, tarea="ligero")
    if not r or not isinstance(r.get("items"), list):
        return False
    for x in r["items"]:
        try:
            i = int(x.get("i"))
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(lote):
            it = lote[i][3]
            it.pop("pendiente", None)
            it["relevante"] = bool(x.get("relevante"))
            it["categoria"] = x.get("categoria") if x.get("categoria") in CATEGORIAS else "otro"
            it["gravedad"] = max(0, min(3, int(x.get("gravedad") or 0)))
            it["resumen"] = str(x.get("resumen") or "")[:160]
    return True


def _investigar(b, noticias):
    info = b["info"]
    lista = "\n".join(f'- {n["fecha"]} [{n["categoria"]}, gravedad {n["gravedad"]}] {n["titulo"]} ({n["fuente"]})' for n in noticias)
    prompt = (
        f'Barrio "{b["nombre"]}" en {b["municipio"]} (Valle de Aburrá, Colombia). Investigación previa:\n'
        f'- Recomendación: {info.get("recomendacion")} — {info.get("razon_recomendacion", "")}\n'
        f'- Seguridad: {info.get("seguridad")}; terreno: {info.get("terreno")}\n'
        f'- Resumen: {info.get("resumen", "")[:600]}\n\n'
        f"Noticias recientes relevantes (últimos {GUARDAR_DIAS} días):\n{lista}\n\n"
        "Criterios: los accidentes de tránsito aislados, eventos deportivos o noticias generales del municipio NO cambian "
        "la seguridad del barrio. Sugiere 'mas_precaucion' solo ante delitos que se repiten (extorsión/vacunas, homicidios o "
        "sicariato, hurtos frecuentes, microtráfico, combos) o riesgos reales para vivir (deslizamientos, cortes de agua "
        "frecuentes). Una sola noticia leve no basta. Si las noticias son positivas o neutras: 'mantener' o 'mejora'. "
        "Distingue lo que ya se sabía (investigación previa) de lo nuevo.\n"
        "Con base SOLO en esto, para alguien que piensa arrendar ahí, responde JSON:\n"
        '{"resumen_reciente":"2-3 frases claras sobre lo que está pasando ahora",'
        '"nivel_alerta":"ninguna|baja|media|alta",'
        '"sugerencia":"mantener|mas_precaucion|mejora",'
        '"recomendacion_sugerida":"recomendado|precaucion|no recomendado",'
        '"motivo":"una frase"}'
    )
    r = ia.preguntar_json(prompt)
    if not r:
        return None
    r["fecha"] = _ahora().date().isoformat()
    return r


def _ponerse_al_dia(d, maximo=8):
    """Investiga barrios con noticias graves que aún no tienen análisis (o tienen uno viejo)."""
    if not ia.disponible():
        return 0
    barrios = {f'{b["municipio"]}|{b["nombre"]}': b for b in _barrios()}
    limite = (_ahora() - timedelta(days=GUARDAR_DIAS)).date().isoformat()
    hechos = 0
    for clave, reg in d.get("barrios", {}).items():
        if hechos >= maximo or clave not in barrios:
            continue
        graves = [n for n in reg.get("noticias", {}).values() if n.get("relevante") and n.get("categoria") in NEGATIVAS and n.get("gravedad", 0) >= 2]
        if not graves:
            continue
        if reg.get("ia") and (reg["ia"].get("fecha") or "") >= max(n.get("fecha") or "" for n in graves):
            continue
        rel = sorted([n for n in reg["noticias"].values() if n.get("relevante") and (n.get("fecha") or "") >= limite],
                     key=lambda n: n.get("fecha") or "", reverse=True)[:15]
        r = _investigar(barrios[clave], rel)
        if not r:
            break   # sin cuota: se sigue en la próxima corrida
        reg["ia"] = r
        hechos += 1
    return hechos


def actualizar(log=print, forzar=False):
    d = _leer()
    hoy = _ahora()
    if not forzar and d.get("ultima") and datetime.fromisoformat(d["ultima"]) > hoy - timedelta(days=CADA_DIAS):
        log(f"Noticias: la última búsqueda fue {d['ultima'][:10]} (se buscan cada {CADA_DIAS} días)")
        n = _ponerse_al_dia(d, maximo=8)
        if n:
            _guardar(d)
            log(f"Noticias: {n} barrios pendientes investigados con IA")
        publicar(d)
        return
    barrios = _barrios()
    t0 = time.time()
    nuevas_por_barrio, lote, fallos = {}, [], 0
    for b in barrios:
        if time.time() - t0 > LIMITE_SEG * 0.5:
            log("Noticias: tope de tiempo en la búsqueda; el resto sigue en la próxima")
            break
        clave = f'{b["municipio"]}|{b["nombre"]}'
        reg = d["barrios"].setdefault(clave, {"noticias": {}})
        items = _buscar(b["nombre"], b["municipio"], VENTANA_DIAS)
        time.sleep(1.0)
        if items is None:
            fallos += 1
            items = []
        nuevas = [it for it in items if it["id"] not in reg["noticias"]]
        interesan = {it["id"] for it in nuevas if _interesa(it["titulo"], b["nombre"])}
        for it in nuevas:
            # de las que no interesan solo se guarda la fecha (para no volver a revisarlas)
            reg["noticias"][it["id"]] = it if it["id"] in interesan else {"fecha": it.get("fecha"), "descartada": True}
        # A la IA solo pasan las que pueden importar para vivir ahí, y como máximo las 8 más recientes
        utiles = sorted([it for it in nuevas if it["id"] in interesan], key=lambda x: x.get("fecha") or "", reverse=True)[:MAX_POR_BARRIO]
        # las que quedaron sin clasificar en una corrida anterior (sin cuota o sin tiempo) vuelven a la fila
        pend = [n for n in reg["noticias"].values() if n.get("pendiente") and n not in utiles][:MAX_POR_BARRIO]
        for it in utiles + pend:
            it["pendiente"] = True
            lote.append((clave, b["nombre"], b["municipio"], it))
            nuevas_por_barrio.setdefault(clave, []).append(it)
    _guardar(d)   # se guarda lo buscado aunque la IA falle o se acabe el tiempo
    # Clasificar con IA en lotes
    clasificadas = 0
    if ia.disponible():
        for i in range(0, len(lote), 30):
            if time.time() - t0 > LIMITE_SEG:
                break
            if _clasificar(lote[i:i + 30]):
                clasificadas += len(lote[i:i + 30])
    # Investigar los barrios con novedades relevantes
    investigados = 0
    limite = (hoy - timedelta(days=GUARDAR_DIAS)).date().isoformat()
    if ia.disponible():
        por_clave = {f'{b["municipio"]}|{b["nombre"]}': b for b in barrios}
        for clave, reg in d["barrios"].items():
            if time.time() - t0 > LIMITE_SEG or clave not in por_clave:
                continue
            graves = [n for n in reg.get("noticias", {}).values() if n.get("relevante") and n.get("categoria") in NEGATIVAS and n.get("gravedad", 0) >= 2]
            if not graves:
                continue
            ultima = max(n.get("fecha") or "" for n in graves)
            if reg.get("ia") and (reg["ia"].get("fecha") or "") >= ultima:
                continue   # ya analizado con estas noticias
            rel = sorted([n for n in d["barrios"][clave]["noticias"].values() if n.get("relevante") and (n.get("fecha") or "") >= limite],
                         key=lambda n: n.get("fecha") or "", reverse=True)[:15]
            r = _investigar(por_clave[clave], rel)
            if r:
                d["barrios"][clave]["ia"] = r
                investigados += 1
    # Limpiar viejas y compactar las que no importan
    for reg in d["barrios"].values():
        reg["noticias"] = {k: (n if ("relevante" in n or n.get("pendiente") or n.get("descartada")) else {"fecha": n.get("fecha"), "descartada": True})
                           for k, n in reg["noticias"].items() if (n.get("fecha") or "9999") >= limite}
    d["ultima"] = hoy.isoformat(timespec="seconds")
    _guardar(d)
    publicar(d)
    log(f"Noticias: {len(lote)} nuevas en {len(nuevas_por_barrio)} barrios, {clasificadas} clasificadas con IA, "
        f"{investigados} barrios investigados de nuevo, {fallos} búsquedas fallidas · IA usada: {ia.USO}")


def publicar(d=None):
    d = d or _leer()
    out = {"actualizado": d.get("ultima"), "barrios": {}}
    for clave, reg in d.get("barrios", {}).items():
        rel = sorted([n for n in reg.get("noticias", {}).values() if n.get("relevante")],
                     key=lambda n: n.get("fecha") or "", reverse=True)[:12]
        if rel or reg.get("ia"):
            out["barrios"][clave] = {
                "noticias": [{k: n.get(k) for k in ("fecha", "titulo", "fuente", "link", "categoria", "gravedad", "resumen")} for n in rel],
                "ia": reg.get("ia"),
            }
    F_PUB.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
