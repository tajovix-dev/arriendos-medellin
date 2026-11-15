"""Genera GUIA_BARRIOS.md (legible) a partir de docs/barrios_info.json.

Uso: python -m scraper.guia_barrios
"""
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ICO = {"recomendado": "✅ Recomendado", "con precaución": "⚠️ Con precaución", "no recomendado": "⛔ No recomendado"}
PROB = {"agua": "💧 Agua", "luz": "⚡ Luz", "ruido": "🔊 Ruido", "acceso": "🚧 Acceso", "altura": "⛰ Altura",
        "deslizamiento": "⚠ Deslizamiento", "inundacion": "🌊 Inundación", "trafico": "🚗 Tráfico",
        "obras": "🏗 Obras", "basuras": "🗑 Basuras", "otro": "• Otro"}


def main():
    d = json.loads((RAIZ / "docs" / "barrios_info.json").read_text(encoding="utf-8"))
    out = [f"# Guía de barrios: Medellín y el sur del Valle de Aburrá\n",
           f"_Investigación del {d.get('fecha', '')} a partir de noticias, datos públicos, reseñas y opiniones de residentes. "
           "Es una referencia general: la seguridad y los problemas cambian por cuadra. Visita la zona de día y de noche antes de decidir._\n"]
    for m in d["municipios"]:
        out.append(f"\n## {m['municipio']}\n")
        if m.get("contexto_municipio"):
            out.append(f"> {m['contexto_municipio']}\n")
        orden = {"recomendado": 0, "con precaución": 1, "no recomendado": 2}
        for b in sorted(m["barrios"], key=lambda x: (orden.get(x.get("recomendacion"), 3), x["nombre"])):
            out.append(f"\n### {b['nombre']} — {ICO.get(b.get('recomendacion'), '❔ Sin evaluar')}\n")
            linea = [f"Seguridad: **{b.get('seguridad', 'sin datos')}**"]
            if b.get("terreno"):
                linea.append(f"Terreno: **{b['terreno']}**")
            if b.get("comuna"):
                linea.append(b["comuna"])
            out.append(" · ".join(linea) + "\n")
            if b.get("razon_recomendacion"):
                out.append(f"\n**Por qué:** {b['razon_recomendacion']}\n")
            if b.get("resumen"):
                out.append(f"\n{b['resumen']}\n")
            if b.get("cerca"):
                out.append("\n**Cerca:** " + "; ".join(b["cerca"]) + "\n")
            if b.get("accesos"):
                out.append(f"\n**Cómo se llega:** {b['accesos']}\n")
            ns = b.get("noticias_seguridad")
            if ns:
                out.append(f"\n**Noticias de seguridad ({ns.get('periodo', '2024-2026')}):** extorsión {ns.get('extorsion', 0)} · "
                           f"sicariato/homicidios {ns.get('sicariato_homicidios', 0)} · hurtos {ns.get('hurtos', 0)} · "
                           f"microtráfico {ns.get('microtrafico', 0)}. {ns.get('detalle', '')}\n")
            if b.get("problemas"):
                out.append("\n**Problemas:**\n")
                for p in b["problemas"]:
                    out.append(f"- {PROB.get(p['tipo'], p['tipo'])} ({p['nivel']}): {p['detalle']}")
                out.append("")
            if b.get("positivos"):
                out.append("\n**Lo bueno:** " + "; ".join(b["positivos"]) + "\n")
            if b.get("negativos"):
                out.append("\n**Lo malo:** " + "; ".join(b["negativos"]) + "\n")
            if b.get("opiniones"):
                out.append("\n**Lo que dicen los residentes:**\n")
                for o in b["opiniones"]:
                    out.append(f"- _{o['texto']}_" + (f" ([{o.get('origen') or 'fuente'}]({o['fuente']}))" if o.get("fuente") else ""))
                out.append("")
            if b.get("conjuntos_maps"):
                out.append("\n**Conjuntos en Google Maps:** " + "; ".join(
                    f"{c['nombre']} ({c.get('calificacion', '?')}★, {c.get('resenas', '?')} reseñas)" for c in b["conjuntos_maps"]) + "\n")
            if b.get("fuentes"):
                out.append("\n**Fuentes:** " + " · ".join(f"[{i + 1}]({u})" for i, u in enumerate(b["fuentes"])) + "\n")
    (RAIZ / "GUIA_BARRIOS.md").write_text("\n".join(out), encoding="utf-8")
    print("GUIA_BARRIOS.md generada")


if __name__ == "__main__":
    main()
