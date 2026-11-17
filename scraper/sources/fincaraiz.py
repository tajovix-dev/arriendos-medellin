"""Fincaraíz: JSON en __NEXT_DATA__ → props.pageProps.fetchResult.searchFast.

Estrategia por municipio:
1. "Nuevos": páginas ordenadas por más recientes (ordenListado=3). Se detiene
   cuando una página ya no trae anuncios desconocidos.
2. "Barrido": recorre en rotación todas las páginas (cursor guardado en el
   estado) para refrescar precios y detectar los que siguen activos.
"""
import config
from scraper.http import get, next_data, a_int

NOMBRE = "fincaraiz"
BASE = "https://www.fincaraiz.com.co"


def _url(slug, pagina):
    pag = f"/pagina{pagina}" if pagina > 1 else ""
    return (f"{BASE}/arriendo/apartamentos/{slug}/antioquia/"
            f"hasta-{config.PRECIO_MAX_GUARDAR}{pag}?ordenListado=3")


def _ficha(item, campo):
    for t in item.get("technicalSheet") or []:
        if t.get("field") == campo:
            return t.get("value")
    return None


# Comodidades que vale la pena mostrar (en orden de importancia para arrendar)
COMODIDADES = [
    ("ascensor", "Ascensor"), ("porter", "Portería"), ("vigilan", "Vigilancia"), ("piscina", "Piscina"),
    ("gimnasio", "Gimnasio"), ("balc", "Balcón"), ("terraza", "Terraza"), ("parqueadero visit", "Parqueadero visitantes"),
    ("zona de ropa", "Zona de ropas"), ("calentador", "Calentador"), ("amoblad", "Amoblado"), ("mascota", "Acepta mascotas"),
    ("unidad cerrada", "Unidad cerrada"), ("conjunto cerrado", "Unidad cerrada"), ("cancha", "Canchas"),
    ("juegos infantiles", "Juegos infantiles"), ("bbq", "Zona BBQ"), ("salón comunal", "Salón comunal"),
]


def comodidades(nombres):
    """Normaliza la lista de comodidades del portal a nombres cortos y conocidos."""
    out = []
    bajos = [n.lower() for n in nombres]
    for clave, etiqueta in COMODIDADES:
        if any(clave in n for n in bajos) and etiqueta not in out:
            out.append(etiqueta)
    return out or None


def _normalizar(item, municipio):
    loc = item.get("locations") or {}
    barrio = (loc.get("location_main") or {}).get("name")
    precio = (item.get("price") or {}).get("amount")
    area = item.get("m2") or a_int(_ficha(item, "m2Built")) or a_int(_ficha(item, "m2apto"))
    fotos = [x.get("image") for x in (item.get("images") or []) if isinstance(x, dict) and x.get("image")]
    img = item.get("img") or (fotos[0] if fotos else None)
    video = (item.get("youtube") or "").strip() or None
    tour = item.get("tour3d") if isinstance(item.get("tour3d"), str) and item.get("tour3d").startswith("http") else None
    # El portal informa el estado: activo / arrendado / eliminado / pausado
    disponible = bool(item.get("active")) and not item.get("sold") and not item.get("deleted") and not item.get("pausd")
    admin = a_int(((item.get("commonExpenses") or {}) if isinstance(item.get("commonExpenses"), dict) else {}).get("amount"))
    facilidades = [f.get("name") for f in (item.get("facilities") or []) if isinstance(f, dict) and f.get("name")]
    return {
        "admin_incl": True if item.get("include_administration") else None,
        "piso": a_int(item.get("floor")) or None,
        "amen": comodidades(facilidades),
        "fotos": fotos,
        "video": video,
        "tour": tour,
        "disponible": disponible,
        "id": f"fr-{item['id']}",
        "fuente": "Fincaraíz",
        "url": BASE + item["link"] if item.get("link", "").startswith("/") else item.get("link"),
        "titulo": item.get("title"),
        "precio": a_int(precio),
        "admin": a_int(_ficha(item, "commonExpenses")) or admin or None,
        "area": a_int(area),
        "hab": a_int(_ficha(item, "bedrooms")) or a_int(item.get("bedrooms")) or None,
        "banos": a_int(_ficha(item, "bathrooms")) or a_int(item.get("bathrooms")) or None,
        "parq": a_int(_ficha(item, "garage")) or a_int(item.get("garage")) or None,
        "estrato": a_int(_ficha(item, "stratum")) or a_int(item.get("stratum")) or None,
        "municipio": municipio,
        "barrio": barrio,
        "lat": item.get("latitude"),
        "lon": item.get("longitude"),
        "img": img,
        "anunciante": (item.get("owner") or {}).get("name"),
        "cod": str(item.get("code2") or "").strip() or None,   # código del anunciante (empareja con su web)
        "publicado": (item.get("created_at") or "")[:10] or None,
    }


def _pagina(slug, n):
    html = get(_url(slug, n))
    if not html:
        return None, 0  # fallo temporal
    data = next_data(html)
    try:
        sf = data["props"]["pageProps"]["fetchResult"]["searchFast"]
    except (TypeError, KeyError):
        return [], 0
    return sf.get("data") or [], (sf.get("paginatorInfo") or {}).get("lastPage") or 0


def scrape(estado, conocidos, log):
    for municipio, slugs in config.MUNICIPIOS.items():
        slug = slugs.get(NOMBRE)
        if not slug:
            continue
        est = estado.setdefault(slug, {"cursor": 1})
        ultima = 0
        nuevos = 0
        leidas = set()
        # 1) Más recientes
        for n in range(1, config.FINCARAIZ_PAGINAS_NUEVOS + 1):
            items, u = _pagina(slug, n)
            if items is None:
                break  # fallo temporal
            ultima = u or ultima
            leidas.add(n)
            if not items:
                break
            desconocidos = 0
            for it in items:
                l = _normalizar(it, municipio)
                if l["id"] not in conocidos:
                    desconocidos += 1
                yield l
            nuevos += desconocidos
            if desconocidos == 0 and n >= 2:
                break
            if ultima and n >= ultima:
                break
        # 2) Barrido rotativo por TODAS las páginas (1..última), saltando las ya leídas en
        #    esta corrida. Así se refrescan precios, fotos y datos de los anuncios viejos.
        cursor = est.get("cursor", 1)
        if not ultima or cursor > ultima:
            cursor = 1
        for _ in range(config.FINCARAIZ_PAGINAS_BARRIDO):
            while cursor in leidas and (not ultima or cursor <= ultima):
                cursor += 1
            if ultima and cursor > ultima:
                cursor = 1
                est["ciclos"] = est.get("ciclos", 0) + 1
                break
            items, u = _pagina(slug, cursor)
            ultima = u or ultima
            if items is None:
                break  # fallo temporal: la próxima corrida sigue desde esta página
            leidas.add(cursor)
            cursor += 1
            if not items:
                cursor = 1
                est["ciclos"] = est.get("ciclos", 0) + 1
                break
            for it in items:
                l = _normalizar(it, municipio)
                # En el barrido aparecen anuncios viejos: no son "nuevos" de verdad
                l["_inicial"] = True
                yield l
        est["cursor"] = cursor
        est["paginas"] = ultima or est.get("paginas", 0)
        log(f"  fincaraiz/{slug}: {nuevos} nuevos, cursor {cursor}/{ultima}")
