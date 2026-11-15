"""Análisis automático (sin costo): se ejecuta en cada actualización.

1. Precio frente a similares: compara cada apartamento con otros parecidos
   (mismo barrio o municipio, mismas habitaciones, ajustado por área) y guarda
   cuánto más barato o caro está.
2. Alertas: señales que conviene revisar antes de pagar nada (precio demasiado
   bajo, sin fotos, fotos usadas en otros anuncios distintos, área dudosa).
3. Historial del mercado: mediana diaria por municipio para ver tendencias.
"""
import re
import unicodedata
from collections import defaultdict
from statistics import median

MIN_GRUPO_BARRIO = 6     # comparables mínimos para usar el barrio
MIN_GRUPO_MUNICIPIO = 8  # comparables mínimos para usar el municipio
AREA_VALIDA = (18, 400)


def _norm(txt):
    txt = unicodedata.normalize("NFKD", (txt or "").lower())
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    txt = re.sub(r"[^a-z0-9 ]+", " ", txt)
    txt = re.sub(r"\b(de|del)\b", " ", txt)  # "Loma de San José" = "Loma San Jose" (igual que en app.js)
    return re.sub(r"\s+", " ", txt).strip()


def _hab(it):
    h = it.get("hab")
    if not h:
        return None
    return min(int(h), 4)  # 4 = "4 o más"


def _unicos(items):
    """Un solo anuncio por grupo de repetidos entre portales (para no contar doble)."""
    vistos, out = set(), []
    for it in items:
        d = it.get("dup")
        if d is not None:
            if d in vistos:
                continue
            vistos.add(d)
        out.append(it)
    return out


def _area_ok(it):
    a = it.get("area")
    return bool(a and AREA_VALIDA[0] <= a <= AREA_VALIDA[1])


def _txt_hab(h):
    return "4+ hab" if h == 4 else f"{h} hab"


def precio_vs_similares(items):
    """Agrega a cada anuncio: ref (precio esperado), dif (-0.18 = 18 % más barato) y comp (con qué se comparó).

    El precio esperado es la mediana de apartamentos con las mismas habitaciones en el
    mismo barrio (o municipio si el barrio tiene pocos), ajustada de forma moderada por
    área (raíz cuadrada, con topes) para no exagerar con áreas grandes o mal digitadas.
    """
    base = _unicos(items)
    precios = defaultdict(list)
    areas = defaultdict(list)
    for it in base:
        m, b, h = it["municipio"], _norm(it.get("barrio")), _hab(it)
        claves = [("m", m, None, h), ("m", m, None, None)]
        e = it.get("estrato") or it.get("estr_b")
        if e and h:
            claves.append(("e", m, e, h))
        if b:
            claves += [("b", m, b, h), ("b", m, b, None)]
        for c in claves:
            precios[c].append(it["precio"])
            if _area_ok(it):
                areas[c].append(it["area"])

    for it in items:
        for k in ("ref", "dif", "comp", "_area_rara"):
            it.pop(k, None)
        m, b, barrio, h = it["municipio"], _norm(it.get("barrio")), it.get("barrio"), _hab(it)
        opciones = []
        if h:
            if b:
                opciones.append((("b", m, b, h), MIN_GRUPO_BARRIO, f"{_txt_hab(h)} en {barrio}"))
            e = it.get("estrato") or it.get("estr_b")
            if e:
                opciones.append((("e", m, e, h), MIN_GRUPO_MUNICIPIO, f"{_txt_hab(h)} estrato {e} en {m}"))
            opciones.append((("m", m, None, h), MIN_GRUPO_MUNICIPIO, f"{_txt_hab(h)} en {m}"))
        else:
            if b:
                opciones.append((("b", m, b, None), MIN_GRUPO_BARRIO, f"apartamentos en {barrio}"))
            opciones.append((("m", m, None, None), MIN_GRUPO_MUNICIPIO, f"apartamentos en {m}"))
        for clave, minimo, desc in opciones:
            lista = precios.get(clave, [])
            if len(lista) < minimo:
                continue
            ref = median(lista)
            ajuste = ""
            areas_g = areas.get(clave, [])
            if _area_ok(it) and len(areas_g) >= minimo:
                razon = it["area"] / median(areas_g)
                if 0.4 <= razon <= 2.5:
                    ref *= min(max(razon, 0.6), 1.6) ** 0.5
                    ajuste = " de área parecida"
                else:
                    it["_area_rara"] = True  # área poco creíble para ese tipo de apartamento
            it["ref"] = int(round(ref, -4))
            it["dif"] = round((it["precio"] - ref) / ref, 3)
            it["comp"] = {"txt": desc + ajuste, "n": len(lista)}
            break


def alertas(items):
    """Señales para revisar con cuidado. No acusan: solo avisan."""
    por_foto = defaultdict(list)
    for it in items:
        if it.get("img"):
            por_foto[it["img"]].append(it)
    for it in items:
        al = []
        if it.get("dif") is not None and it["dif"] <= -0.40:
            al.append("precio_bajo")
        if not it.get("img"):
            al.append("sin_fotos")
        ar, hb = it.get("area"), it.get("hab")
        incoherente = bool(ar and hb and ((hb == 1 and ar > 120) or (hb >= 3 and ar < 40)))
        if it.pop("_area_rara", False) or incoherente or (ar and not (AREA_VALIDA[0] <= ar <= AREA_VALIDA[1])):
            al.append("area_dudosa")
        otros = [o for o in por_foto.get(it.get("img"), [])
                 if o is not it and (o.get("dup") is None or o.get("dup") != it.get("dup"))]
        distintos = [o for o in otros
                     if o.get("municipio") != it.get("municipio")
                     or abs(o["precio"] - it["precio"]) > 0.15 * it["precio"]
                     or (o.get("area") and it.get("area") and abs(o["area"] - it["area"]) > 5)]
        if distintos:
            al.append("foto_repetida")
        if al:
            it["alertas"] = al
        else:
            it.pop("alertas", None)


def historial(items, hist, fecha):
    """Guarda la mediana del día por municipio (y total). hist: {fecha: {municipio: {...}}}."""
    base = _unicos(items)
    grupos = defaultdict(list)
    for it in base:
        grupos[it["municipio"]].append(it)
        grupos["Todos"].append(it)
    dia = {}
    for m, lista in grupos.items():
        ppm = [x["precio"] / x["area"] for x in lista if _area_ok(x)]
        dia[m] = {
            "n": len(lista),
            "precio": int(median(x["precio"] for x in lista)),
            "m2": int(median(ppm)) if ppm else None,
        }
    hist[fecha] = dia
    # Conserva 1 año
    for f in sorted(hist)[:-366]:
        hist.pop(f)
    return hist


def analizar(items, hist, fecha):
    precio_vs_similares(items)
    alertas(items)
    return historial(items, hist, fecha)
