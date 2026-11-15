"""Verificación de fotos.

Algunos portales publican enlaces de fotos que ya no abren (por ejemplo, Rentola copia
fotos de otro portal cuyo servidor ahora responde "Acceso denegado"). Antes de publicar se
comprueba que las fotos carguen:

- Se prueba la foto principal y la primera de la galería de cada anuncio (caché en
  data/fotos_estado.json: las buenas se vuelven a probar a los 14 días, las malas a los 3).
- Si un servidor falla en casi todas sus pruebas, se descartan todas sus fotos.
- Las fotos que fallan se quitan; la portada pasa a ser la primera foto que sí abre.
  Si ninguna abre, el anuncio queda "sin fotos" (no se dice que tiene fotos que no cargan).

Solo cambia lo publicado; los datos guardados conservan los enlaces originales.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

RAIZ = Path(__file__).resolve().parent.parent
F_CACHE = RAIZ / "data" / "fotos_estado.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
DIAS_OK, DIAS_MAL = 14, 3
MAX_PRUEBAS = 2500       # pruebas nuevas por corrida
HILOS = 6
LIMITE_SEG = 300         # tope de tiempo por corrida


def _host(u):
    return urlparse(u).netloc.lower()


def _probar(u):
    """True si la URL devuelve una imagen."""
    try:
        r = requests.get(u, headers={"User-Agent": UA, "Range": "bytes=0-2047"}, timeout=(4, 8), stream=True)
        if r.status_code not in (200, 206):
            r.close()
            return False
        inicio = next(r.iter_content(64), b"")
        r.close()
        # Algunos servidores no marcan bien el tipo: se mira el contenido (como hace el navegador)
        return (inicio.startswith(b"\xff\xd8") or inicio.startswith(b"\x89PNG") or inicio.startswith(b"GIF8")
                or (inicio[:4] == b"RIFF" and inicio[8:12] == b"WEBP") or inicio[4:8] == b"ftyp"
                or inicio.lstrip().startswith(b"<svg"))
    except requests.RequestException:
        return None  # sin respuesta (red): no se concluye nada


def verificar(items, galeria, log=print):
    """Devuelve {id: [fotos que abren]} y ajusta img/nf de cada anuncio (dicts publicados)."""
    try:
        cache = json.loads(F_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache = {}
    ahora = datetime.now(timezone.utc)

    def vigente(u):
        c = cache.get(u)
        if not c:
            return False
        dias = DIAS_OK if c[0] else DIAS_MAL
        return datetime.fromisoformat(c[1]) > ahora - timedelta(days=dias)

    # Fotos de cada anuncio (portada primero, sin repetir)
    fotos = {}
    for it in items:
        lista = []
        for u in [it.get("img")] + list((galeria.get(it["id"]) or {}).get("f") or []):
            if u and u.startswith("http") and u not in lista:
                lista.append(u)
        fotos[it["id"]] = lista

    # 1) Probar portada y primera de la galería
    pendientes = []
    for lista in fotos.values():
        for u in lista[:2] + lista[-1:]:  # portada, primera y última (detecta galerías rotas)
            if not vigente(u) and u not in pendientes:
                pendientes.append(u)
    pendientes = pendientes[:min(MAX_PRUEBAS, 900)]
    t0 = time.time()
    fin = t0 + LIMITE_SEG
    with ThreadPoolExecutor(HILOS) as ex:
        for u, ok in zip(pendientes, ex.map(_probar, pendientes)):
            if ok is not None:
                cache[u] = [ok, ahora.isoformat(timespec="seconds")]

    # 1b) Si alguna foto probada de un anuncio falló, se prueban TODAS las de ese anuncio
    #     (a veces solo la portada sirve y el resto está en otra dirección)
    sospechosos = []
    for lista in fotos.values():
        probadas = [u for u in lista[:2] + lista[-1:] if u in cache]
        if any(not cache[u][0] for u in probadas):
            sospechosos += [u for u in lista if not vigente(u) and u not in sospechosos]
    sospechosos = sospechosos[:1500]
    if sospechosos and time.time() < fin:
        with ThreadPoolExecutor(HILOS) as ex:
            for u, ok in zip(sospechosos, ex.map(_probar, sospechosos)):
                if ok is not None:
                    cache[u] = [ok, ahora.isoformat(timespec="seconds")]
    pendientes += sospechosos

    # 2) Servidores caídos: casi todas sus pruebas fallan
    por_host = {}
    for u, (ok, _) in cache.items():
        h = por_host.setdefault(_host(u), [0, 0])
        h[0 if ok else 1] += 1
    caidos = {h for h, (bien, mal) in por_host.items() if mal >= 5 and mal >= 9 * bien}

    def sirve(u):
        if _host(u) in caidos:
            return False
        c = cache.get(u)
        return c[0] if c else True  # no probada: se asume que sí (su servidor funciona)

    # 3) Portada: la primera foto que abre (probando más si hace falta)
    #    Varias rondas: si la nueva portada tampoco abre, se prueba la siguiente
    extra = []
    for _ in range(40):
        if time.time() > fin or len(extra) > 1500:
            break
        ronda = []
        for it in items:
            buenas = [u for u in fotos[it["id"]] if sirve(u)]
            if buenas and buenas[0] not in cache:
                ronda.append(buenas[0])
        if not ronda:
            break
        with ThreadPoolExecutor(HILOS) as ex:
            for u, ok in zip(ronda, ex.map(_probar, ronda)):
                if ok is not None:
                    cache[u] = [bool(ok), ahora.isoformat(timespec="seconds")]
                else:  # sin respuesta: se da por buena pero se vuelve a probar mañana
                    cache[u] = [True, (ahora - timedelta(days=DIAS_OK - 1)).isoformat(timespec="seconds")]
        extra += ronda

    salida, sin, quitadas = {}, 0, 0
    for it in items:
        buenas = [u for u in fotos[it["id"]] if sirve(u)]
        quitadas += len(fotos[it["id"]]) - len(buenas)
        salida[it["id"]] = buenas
        it.pop("nf", None)
        if buenas:
            it["img"] = buenas[0]
            if len(buenas) > 1 or (galeria.get(it["id"]) or {}).get("f"):
                it["nf"] = len(buenas)
        else:
            it["img"] = None
            if fotos[it["id"]]:
                sin += 1
    F_CACHE.parent.mkdir(parents=True, exist_ok=True)
    # Limpiar entradas viejas para que el archivo no crezca sin límite
    limite = (ahora - timedelta(days=60)).isoformat()
    cache = {u: c for u, c in cache.items() if c[1] >= limite}
    tmp = F_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    tmp.replace(F_CACHE)  # escritura atómica: un corte a mitad no deja el archivo dañado
    log(f"Fotos: {len(pendientes) + len(extra)} probadas en {time.time() - t0:.0f} s, "
        f"servidores caídos: {', '.join(sorted(caidos)) or 'ninguno'}; "
        f"{quitadas} fotos quitadas, {sin} anuncios quedaron sin fotos que abran")
    return salida
