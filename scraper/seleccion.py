"""Selección con IA: "imperdibles" del momento y mejores zonas.

Se le pasa a la IA una tabla corta con los mejores candidatos (ya filtrados por reglas
objetivas: precio frente a similares, sin alertas, con fotos, recientes) y la investigación
de cada barrio. La IA solo puede elegir entre esos anuncios (se valida) y explica por qué.
Una llamada por corrida. Si no hay IA disponible, no se publica nada y la página sigue igual.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scraper import ia

RAIZ = Path(__file__).resolve().parent.parent
F_INFO = RAIZ / "docs" / "barrios_info.json"


def _info_barrios():
    try:
        d = json.loads(F_INFO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for m in d.get("municipios", []):
        for b in m.get("barrios", []):
            out[(m["municipio"], b["nombre"].lower())] = b
    return out


# Al usuario le interesa el sur del valle: los 4 municipios y el sur de Medellín (mismo criterio que
# el interruptor «Solo el sur» de la página)
SUR_MUNICIPIOS = {"Sabaneta", "Envigado", "Itagüí", "La Estrella"}
_SUR_MED = re.compile(r"comuna 1[456]|el poblado|guayabal|bel[eé]n|san antonio de prado|altavista|"
                      r"(provenza|manila|astorga|castropol|lalinde|las lomas|los balsos|el tesoro|los naranjos|"
                      r"la aguacatala|ciudad del r[ií]o|barrio colombia|villa carlota|patio bonito|san lucas|el diamante|"
                      r"tenche|trinidad|santa fe|shellmar|campo amor|cristo rey|la colina|f[aá]tima|rosales|granada|"
                      r"san bernardo|las playas|la mota|loma de los bernal|la gloria|los alpes|las mercedes|malib[uú]|"
                      r"rodeo alto|nueva villa de aburr[aá]|miravalle|el nogal|los almendros)", re.I)


def es_del_sur(v, b):
    if v["municipio"] in SUR_MUNICIPIOS:
        return True
    if v["municipio"] != "Medellín":
        return False
    return bool(_SUR_MED.search(v.get("barrio") or "") or _SUR_MED.search(b.get("comuna") or ""))


def _dias(pub):
    if not pub:
        return None
    try:
        return (datetime.now(timezone(timedelta(hours=-5))).date() - datetime.fromisoformat(pub[:10]).date()).days
    except ValueError:
        return None


F_INV = RAIZ / "docs" / "data" / "investigaciones.json"


def _investigacion():
    """id → [veredicto, puntaje, disponible, barrio, sector, parte] según scraper/investigar.py."""
    try:
        d = json.loads(F_INV.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    res, por_id = d.get("res") or {}, d.get("por_id") or {}
    out = dict(res)
    for i, k in por_id.items():
        if k in res:
            out[i] = res[k]
    return out


def _txt_inv(r):
    if not r or not r[0]:
        return ""
    ver = {"r": "recomendable", "c": "con reservas", "n": "no recomendable"}.get(r[0], "")
    return (f" | investigación: {ver} {r[1]}/10" + (f", barrio {r[3]}" if r[3] else "")
            + (f", sector {r[4]}" if r[4] else ""))


def generar(visibles, destino, log=print):
    if not ia.disponible():
        return
    info = _info_barrios()
    inv = _investigacion()
    cand = []
    vistos_dup = set()
    for v in visibles:
        if v.get("alertas") or v.get("dif") is None or v["dif"] > -0.05 or v.get("ubic") == "municipio":
            continue
        if (v.get("nf") or 0) < 3:
            continue
        d = _dias(v.get("publicado"))
        if d is not None and d > 30:
            continue
        if v.get("dup") is not None:
            if v["dup"] in vistos_dup:
                continue
            vistos_dup.add(v["dup"])
        r = inv.get(v["id"])
        if r and (r[2] == 0 or r[0] == "n"):
            continue   # la investigación lo encontró no publicado o no recomendable
        b = info.get((v["municipio"], (v.get("barrio") or "").lower()), {})
        if not es_del_sur(v, b):
            continue
        cand.append((v, b, d))
    cand.sort(key=lambda x: x[0]["dif"])
    cand = cand[:40]
    if len(cand) < 3:
        log("Selección IA: muy pocos candidatos, se omite")
        return
    # Si los candidatos son los mismos de la última vez (mismos anuncios y precios), no se gasta IA
    import hashlib
    huella = hashlib.md5("|".join(f'{v["id"]}:{v["precio"]}:{(inv.get(v["id"]) or [""])[0]}' for v, _, _ in cand).encode()).hexdigest()[:12]
    try:
        previo = json.loads(Path(destino).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previo = {}
    if previo.get("huella") == huella and previo.get("imperdibles"):
        log("Selección IA: mismos candidatos que la última vez, se reutiliza (0 consultas)")
        return
    filas = []
    for v, b, d in cand:
        filas.append(
            f'{v["id"]} | {v["precio"]:,} | {v.get("hab") or "?"} hab {v.get("banos") or "?"} baños {v.get("area") or "?"} m² '
            f'| estrato {v.get("estrato") or v.get("estr_b") or "?"} | {v.get("barrio") or "?"}, {v["municipio"]} '
            f'| {round(v["dif"] * 100)}% vs similares | {d if d is not None else "?"} días publicado | {v.get("nf")} fotos '
            f'| barrio: {b.get("recomendacion", "sin investigar")}, seguridad {b.get("seguridad", "?")}, terreno {b.get("terreno", "?")}'
            f'{_txt_inv(inv.get(v["id"]))}'
        )
    prompt = (
        "Eres un asesor de arriendos en el Valle de Aburrá (Colombia). Presupuesto máximo $2.000.000 COP. "
        "La persona busca en el SUR del valle (Sabaneta, La Estrella, Envigado, Itagüí y el sur de Medellín).\n"
        "Estos son los mejores candidatos de hoy (id | canon | tamaño | estrato | ubicación | precio frente a apartamentos parecidos "
        "| días publicado | fotos | lo que sabemos del barrio):\n" + "\n".join(filas) + "\n\n"
        "Elige hasta 6 'imperdibles' equilibrando precio, tamaño, seguridad del barrio y que sean recientes. "
        "Evita barrios 'no recomendado' salvo que la oferta sea excepcional (dilo). "
        "Si un candidato trae 'investigación' (visita a la ficha original, sector y conjunto), úsala: prefiere los "
        "'recomendable'. Barrio es la zona general y sector las cuadras alrededor: no los confundas. "
        "Luego indica las 3 mejores zonas (barrio, municipio) para buscar ahora según esta lista.\n"
        'Responde JSON: {"imperdibles":[{"id":"...","razon":"máx 140 caracteres, concreta"}],'
        '"zonas":[{"barrio":"...","municipio":"...","razon":"máx 140 caracteres"}],'
        '"consejo":"1 frase útil para hoy"}'
    )
    r = ia.preguntar_json(prompt)
    if not r:
        log("Selección IA: sin respuesta (cuota o error); se mantiene la anterior")
        return
    validos = {v["id"] for v, _, _ in cand}
    imp = [{"id": x["id"], "razon": str(x.get("razon", ""))[:160]} for x in r.get("imperdibles", [])
           if isinstance(x, dict) and x.get("id") in validos][:6]
    zonas = [{"barrio": str(z.get("barrio", ""))[:60], "municipio": str(z.get("municipio", ""))[:30], "razon": str(z.get("razon", ""))[:160]}
             for z in r.get("zonas", []) if isinstance(z, dict)][:3]
    salida = {"fecha": datetime.now(timezone(timedelta(hours=-5))).isoformat(timespec="minutes"), "huella": huella,
              "imperdibles": imp, "zonas": zonas, "consejo": str(r.get("consejo", ""))[:200]}
    Path(destino).write_text(json.dumps(salida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"Selección IA: {len(imp)} imperdibles, {len(zonas)} zonas · cuota: {ia.resumen_cuota()}")
