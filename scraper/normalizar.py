"""Limpieza de lo que se publica (no toca los datos guardados).

- Municipio: el portal a veces devuelve, en la búsqueda de un municipio, anuncios del vecino.
  Se toma el municipio que dice el propio anuncio (URL de Fincaraíz, título de Rentola).
- Barrio: una sola forma de escribir cada barrio ("Santamaria" = "Santa María",
  "Tablaza" = "La Tablaza", "Vereda san jose" = "San José"…). Se descartan barrios que son
  el nombre del municipio o "Comuna 3".
- Títulos: sin MAYÚSCULAS sostenidas, en español y sin caracteres dañados.
"""
import re
import unicodedata
from collections import Counter, defaultdict

import config

ALIAS = {
    "santamaria": "santa maria", "tablaza": "la tablaza", "ferreria": "la ferreria",
    "cristobal": "barrio cristobal", "aldea": "la aldea", "estrella": "", "la ferreira": "la ferreria",
}
PREFIJOS = r"^(barrio|vereda|urbanizacion|urb|sector|conjunto)\s+"


def _sin_tildes(s):
    s = unicodedata.normalize("NFD", (s or "").replace("�", "ñ"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def clave_barrio(nombre, municipio):
    """Clave canónica del barrio, o "" si no es un barrio real."""
    k = _sin_tildes(nombre).lower()
    k = re.sub(r"[^a-z0-9 ]", " ", k)
    k = re.sub(r"\b(no|nro|n|numero)\b\s*", "", k)
    k = re.sub(r"\s+", " ", k).strip()
    k = re.sub(PREFIJOS, "", k)
    k = re.sub(r"\s+", " ", re.sub(r"\b(de|del)\b", " ", k)).strip()   # "Loma de San José" = "Loma San José"
    muni = _sin_tildes(municipio).lower()
    if k.endswith(" " + muni):
        k = k[: -len(muni) - 1].strip()
    k = ALIAS.get(k, k)
    if (not k or k == muni or k in ("medellin", "sabaneta", "envigado", "itagui", "la estrella")
            or re.fullmatch(r"comuna \d+", k) or k in ("sin barrio", "unnamed road")):
        return ""
    return k


MENORES = {"de", "del", "la", "las", "los", "el", "y", "en"}


def _bonito(s):
    """'san jose' → 'San Jose'; 'LOMA DE LOS BERNAL' → 'Loma de los Bernal'."""
    s = re.sub(r"\s+", " ", (s or "").replace("�", "ñ").strip())
    palabras = s.split(" ")
    return " ".join(p.lower() if i and p.lower() in MENORES else (p[:1].upper() + p[1:].lower() if p.isalpha() else p)
                    for i, p in enumerate(palabras))


def limpiar_titulo(t):
    t = (t or "").replace("�", "ñ")
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"(?i)^apartment (for rent )?(in|at|en arriendo)\b", "Apartamento en arriendo en", t)
    t = re.sub(r"(?i)^apartment\b", "Apartamento", t)
    letras = [c for c in t if c.isalpha()]
    if letras and sum(c.isupper() for c in letras) / len(letras) > 0.7:
        t = t.capitalize()
    return t


# Slugs de municipio en las URL de Fincaraíz (…-maria-auxiliadora-sabaneta/194014771)
_SLUG_FR = {v["fincaraiz"]: m for m, v in config.MUNICIPIOS.items() if v.get("fincaraiz")}
_NOMBRES = {_sin_tildes(m).lower(): m for m in config.MUNICIPIOS}


def municipio_real(it):
    """Municipio que dice el propio anuncio (o None si no se puede saber)."""
    if it["fuente"] == "Fincaraíz":
        m = re.search(r"/([a-z0-9-]+)/\d+/?$", it.get("url") or "")
        if m:
            for slug, muni in sorted(_SLUG_FR.items(), key=lambda kv: -len(kv[0])):
                if m.group(1).endswith("-" + slug):
                    return muni
    if it["fuente"] == "Rentola":
        partes = [p.strip() for p in (it.get("titulo") or "").split(",")]
        for p in reversed(partes[1:]):
            muni = _NOMBRES.get(_sin_tildes(p).lower())
            if muni:
                return muni
    return None


def normalizar(items, log=print):
    n_muni = n_barrio = 0
    for it in items:
        it["titulo"] = limpiar_titulo(it.get("titulo"))
        m = municipio_real(it)
        if m and m != it["municipio"]:
            it["municipio"] = m
            n_muni += 1
    n_barrio = unificar_barrios(items)
    log(f"Normalización: {n_muni} municipios corregidos según el anuncio, {n_barrio} barrios unificados o descartados")


def unificar_barrios(items):
    """Misma clave de barrio → mismo nombre visible (el más usado, prefiriendo el que tiene tildes)."""
    n_barrio = 0
    # Barrios: misma clave → mismo nombre (el más usado, prefiriendo el que tiene tildes)
    nombres = defaultdict(Counter)
    for it in items:
        k = clave_barrio(it.get("barrio"), it["municipio"])
        it["_kb"] = k
        if k:
            nombres[(it["municipio"], k)][_bonito(it["barrio"])] += 1
    elegido = {}
    for clave, cnt in nombres.items():
        elegido[clave] = max(cnt.items(), key=lambda kv: (
            not _sin_tildes(kv[0]).lower().endswith(" " + _sin_tildes(clave[0]).lower()),  # sin "… Itagui" al final
            kv[0] != _sin_tildes(kv[0]), kv[1]))[0]
    for it in items:
        k = it.pop("_kb")
        nuevo = elegido.get((it["municipio"], k)) if k else None
        if (it.get("barrio") or None) != nuevo:
            n_barrio += 1
        it["barrio"] = nuevo
    return n_barrio
