"""Regenera docs/data a partir de data/ sin volver a raspar los portales.

Uso: python -m scraper.publicar
"""
from scraper.run import F_ANUNCIOS, F_CORRIDAS, F_GALERIA, ahora, leer_json, leer_jsonl, limpiar, publicar

if __name__ == "__main__":
    corridas = leer_json(F_CORRIDAS, [])
    # La fecha mostrada es la de la última corrida real, no la de esta regeneración
    ts = corridas[-1]["ts"] if corridas else ahora().isoformat()
    anuncios = leer_jsonl(F_ANUNCIOS)
    limpiar(anuncios)
    n = publicar(anuncios, leer_jsonl(F_GALERIA), corridas, ts, con_ia=False)
    print(f"Página regenerada: {n} anuncios visibles")
