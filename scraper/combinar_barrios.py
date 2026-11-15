"""Combina las investigaciones por municipio (inv_*.json) en docs/barrios_info.json.

Uso: python -m scraper.combinar_barrios <carpeta_con_inv_json>

- Normaliza valores (seguridad, recomendación, terreno).
- Agrega alias: el nombre sin el texto entre paréntesis y variantes conocidas,
  para que coincida con cómo escriben el barrio los portales.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "docs" / "barrios_info.json"
ORDEN = ["Sabaneta", "Envigado", "Itagüí", "La Estrella", "Medellín"]

SEGURIDAD = {"alta", "media-alta", "media", "media-baja", "baja", "sin datos"}
RECO = {"recomendado", "con precaución", "no recomendado"}
TERRENO = {"plano", "ondulado", "lomas fuertes"}
TIPOS_PROBLEMA = {"agua", "luz", "ruido", "acceso", "altura", "deslizamiento", "inundacion", "trafico", "obras", "basuras", "otro"}

# Variantes de nombre que usan los portales → nombre de la investigación
ALIAS_EXTRA = {
    ("Itagüí", "Suramérica"): ["Suramérica Itagui", "Suramerica Itagüí", "Suramericana"],
    ("Itagüí", "Santa María"): ["Santa Maria No. 1", "Santa Maria No. 2", "Santa Maria No. 3", "Santa Maria la Nueva", "Santa María Nº 1", "Santa María Nº 2", "Santa María Nº 3"],
    ("Itagüí", "Simón Bolívar"): ["Simon Bolivar Itagui", "Simón Bolívar Itagui"],
    ("Itagüí", "Pilsen"): ["Glorieta Pilsen"],
    ("Itagüí", "Centro"): ["Centro Itagui", "Centro Itagüí"],
    ("Sabaneta", "Parque"): ["Parque Principal", "Centro", "Zona Centro"],
    ("Sabaneta", "Pan de Azúcar"): ["Sector Pan de Azúcar", "Vereda Pan de Azucar"],
    ("Sabaneta", "Prados de Sabaneta"): ["Prados De"],
    ("Sabaneta", "Restrepo Naranjo"): ["Manuel Restrepo"],
    ("Envigado", "La Sebastiana"): ["Sebastiana"],
    ("Envigado", "Zona Centro"): ["Centro"],
    ("La Estrella", "La Ferrería"): ["La Ferreira", "Ferreria"],
    ("La Estrella", "La Tablaza"): ["Tablaza"],
    ("La Estrella", "Ancón San Martín"): ["Ancon", "Ancón"],
    ("La Estrella", "La Inmaculada"): ["La Inmaculada 1"],
    ("Medellín", "Loma de Los Bernal"): ["La Loma de Los Bernal"],
    ("Medellín", "Ciudad del Río"): ["Poblado Ciudad del Rio Suroriente"],
    ("Medellín", "Calasanz"): ["Calasanz Parte Alta"],
}


def norm(txt):
    return (txt or "").strip().lower()


def limpiar_barrio(municipio, b):
    nombre = (b.get("nombre") or "").strip()
    base = re.sub(r"\s*\(.*?\)\s*", " ", nombre).strip()
    alias = set(b.get("alias") or [])
    if base and base != nombre:
        alias.add(nombre)
    for (m, n), extras in ALIAS_EXTRA.items():
        if m == municipio and norm(n) == norm(base):
            alias.update(extras)
    seg = norm(b.get("seguridad"))
    reco = norm(b.get("recomendacion")).replace("precaucion", "precaución")
    ter = norm(b.get("terreno"))
    out = {
        "nombre": base or nombre,
        "alias": sorted(a for a in alias if a and norm(a) != norm(base)),
        "seguridad": seg if seg in SEGURIDAD else "sin datos",
        "confianza": norm(b.get("confianza")) or "baja",
        "recomendacion": reco if reco in RECO else None,
        "razon_recomendacion": b.get("razon_recomendacion"),
        "terreno": ter if ter in TERRENO else None,
        "terreno_nota": b.get("terreno_nota"),
        "cerca": [c for c in (b.get("cerca") or []) if isinstance(c, str)],
        "noticias_seguridad": b.get("noticias_seguridad"),
        "problemas": [
            {"tipo": p.get("tipo") if p.get("tipo") in TIPOS_PROBLEMA else "otro",
             "nivel": p.get("nivel") if p.get("nivel") in ("bajo", "medio", "alto") else "medio",
             "detalle": p.get("detalle")}
            for p in (b.get("problemas") or []) if isinstance(p, dict) and p.get("detalle")
        ],
        "accesos": b.get("accesos"),
        "opiniones": [
            {"texto": o["texto"], "fuente": o.get("fuente") if str(o.get("fuente", "")).startswith("http") else None}
            for o in (b.get("opiniones") or []) if isinstance(o, dict) and o.get("texto")
        ][:3],
        "comuna": b.get("comuna"),
        "positivos": [p for p in (b.get("positivos") or []) if isinstance(p, str)],
        "negativos": [n for n in (b.get("negativos") or []) if isinstance(n, str)],
        "resumen": b.get("resumen"),
        "fuentes": [u for u in (b.get("fuentes") or []) if isinstance(u, str) and u.startswith("http")],
    }
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def agregar_maps(municipios, carpeta):
    """Suma conjuntos y opiniones de Google Maps (maps_opiniones.json) a cada barrio."""
    f = carpeta / "maps_opiniones.json"
    if not f.exists():
        return 0
    try:
        datos = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    idx = {(m["municipio"], norm(b["nombre"])): b for m in municipios for b in m["barrios"]}
    n = 0
    for r in datos:
        b = idx.get((r.get("municipio"), norm(r.get("barrio"))))
        if not b:
            continue
        conj = [c for c in (r.get("conjuntos") or []) if isinstance(c, dict) and c.get("nombre")][:3]
        if conj:
            b["conjuntos_maps"] = conj
        ops = [{"texto": o["texto"], "fuente": o.get("fuente"), "origen": "Google Maps"}
               for o in (r.get("opiniones") or []) if isinstance(o, dict) and o.get("texto")]
        if ops:
            b["opiniones"] = (b.get("opiniones") or []) + ops[:3]
            n += 1
        if r.get("temas"):
            b["temas_maps"] = r["temas"][:6]
    return n


def main():
    carpeta = Path(sys.argv[1])
    municipios = []
    for f in sorted(carpeta.glob("inv_*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        m = d["municipio"]
        municipios.append({
            "municipio": m,
            "contexto_municipio": d.get("contexto_municipio"),
            "barrios": [limpiar_barrio(m, b) for b in d.get("barrios", [])],
        })
    print("Barrios con opiniones de Google Maps:", agregar_maps(municipios, carpeta))
    municipios.sort(key=lambda x: ORDEN.index(x["municipio"]) if x["municipio"] in ORDEN else 99)
    salida = {"fecha": date.today().strftime("%d-%m-%Y"), "municipios": municipios}
    SALIDA.write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
    for m in municipios:
        cuenta = {}
        for b in m["barrios"]:
            cuenta[b.get("recomendacion", "sin evaluar")] = cuenta.get(b.get("recomendacion", "sin evaluar"), 0) + 1
        print(f"{m['municipio']}: {len(m['barrios'])} barrios {cuenta}")


if __name__ == "__main__":
    main()
