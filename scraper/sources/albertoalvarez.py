"""Alberto Álvarez: JSON-LD (ItemList de Apartment con Offer), 9 por página, ?page=N."""
import re

import config
from scraper.http import GONE, get, json_ld, a_int

NOMBRE = "albertoalvarez"
BASE = "https://albertoalvarez.com"
TEL_INMOBILIARIA = "576044449898"   # línea general publicada en albertoalvarez.com
WA_INMOBILIARIA = "573218459022"    # WhatsApp publicado en albertoalvarez.com


def _items(html):
    for j in json_ld(html):
        grafo = j.get("@graph", [j]) if isinstance(j, dict) else j
        for b in grafo:
            if isinstance(b, dict) and b.get("@type") == "ItemList":
                return [e.get("item") or {} for e in b.get("itemListElement") or []]
    return []


def _normalizar(item, municipio):
    oferta = item.get("offers") or {}
    url = item.get("url") or ""
    m = re.search(r"/apartamento/([A-Z]{0,3}\d*-\d+)/", url)
    dire = (item.get("address") or {}).get("streetAddress") or ""
    barrio = dire.replace(municipio, "").strip() or None
    img = item.get("image")
    disp = oferta.get("availability")
    return {
        # Contacto público de la inmobiliaria (aparece en su sitio)
        "tel": TEL_INMOBILIARIA,
        "wa": WA_INMOBILIARIA,
        "fotos": [img] if img else [],
        "disponible": (disp.rstrip("/").split("/")[-1] == "InStock") if disp else None,
        "id": f"aa-{m.group(1) if m else url}",
        "fuente": "Alberto Álvarez",
        "url": url,
        "titulo": item.get("name"),
        "precio": a_int(oferta.get("price")),
        "admin": None,
        "area": a_int((item.get("floorSize") or {}).get("value")),
        "hab": a_int(item.get("numberOfRooms")),
        "banos": a_int(item.get("numberOfBathroomsTotal")),
        "parq": None,
        "estrato": None,
        "municipio": municipio,
        "barrio": barrio,
        "lat": None,
        "lon": None,
        "img": item.get("image"),
        "anunciante": "Alberto Álvarez",
        "publicado": None,
    }


def detalle(url):
    """Galería completa desde la página del anuncio (fotos property-AA-XXXX-N.jpg)."""
    html = get(url, si_404=GONE)
    if html == GONE:
        return {"disponible": False}  # 404/410: el anuncio ya no existe
    if html is None:
        raise IOError("sin respuesta")  # fallo temporal: se reintenta en otra corrida
    m = re.search(r"/apartamento/([A-Z]{0,3}\d*-\d+)/", url)
    if not m:
        return {}
    codigo = m.group(1)
    nums = sorted({int(n) for n in re.findall(rf"property-{codigo}-(\d+)\.jpg", html)})
    base = "https://s3.amazonaws.com/com.albertoalvarez.fullpublic/propertyPictures//"
    t = html.replace('\\"', '"')

    def num(clave):
        m = re.search(r'"' + clave + r'"\s*:\s*"?(-?[\d.]+)', t)
        return float(m.group(1)) if m else None

    # Nota: las banderas de comodidades (AAElevator, AAPool…) y "AllowsPets" salen iguales en
    # todos los anuncios (son configuración del sitio), así que no se usan.
    lat, lon = num("latitude"), num("longitude")
    # El estrato del propio anuncio va en el bloque "PropertyId":"AA-XXXX" con números romanos
    # ("Stratum":"III"); los demás bloques de la página son de inmuebles similares.
    estrato = None
    bloque = re.search(r'"PropertyId":"' + re.escape(codigo) + r'"(.{0,1500}?)"Price"', t)
    if bloque:
        m_e = re.search(r'"Stratum":"([IVX\d]+)"', bloque.group(1))
        romanos = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}
        if m_e:
            v = m_e.group(1)
            estrato = romanos.get(v) or (int(v) if v.isdigit() else None)
    # Se usa la dirección completa tal como la publica la página: según la inmobiliaria las
    # fotos están en servidores distintos (…albertoalvarez.fullpublic… o …mobilia.acrecer…)
    completas = {}
    for u, n in re.findall(r'(https://s3\.amazonaws\.com/[^"\'\s\\]*?property-' + re.escape(codigo) + r'-(\d+)\.jpg)', html):
        completas.setdefault(int(n), u)
    fotos = [completas.get(n) or f"{base}property-{codigo}-{n}.jpg" for n in nums]
    if not fotos:
        # Inmuebles de inmobiliarias aliadas (códigos 473-…, 786-…, AC-…): fotos en "PhotoName"
        i = t.find('"PropertyId":"' + codigo + '"')
        if i >= 0:
            # hasta el siguiente inmueble distinto (las fotos también repiten su PropertyId)
            otro = re.compile(r'"PropertyId":"(?!' + re.escape(codigo) + r'")')
            m_fin = otro.search(t, i + 20)
            bloque = t[i: m_fin.start() if m_fin else i + 40000]
            fotos = list(dict.fromkeys(re.findall(r'https://lh3\.googleusercontent\.com/[^"\\\s,]+', bloque)))
    # Fecha de publicación: "CreatedAt" del bloque del propio inmueble (no de los similares)
    publicado = None
    i = t.find('"PropertyId":"' + codigo + '"')
    if i >= 0:
        ini = t.rfind('{"Id":', 0, i)
        bloque = t[ini if ini >= 0 else i: i + 3000]
        m_c = re.search(r'"CreatedAt":"(\d{4}-\d{2}-\d{2})T(\d{2})', bloque)
        if m_c:
            from datetime import date, timedelta
            d = date.fromisoformat(m_c.group(1))
            if int(m_c.group(2)) < 5:          # UTC → hora Colombia (UTC-5)
                d -= timedelta(days=1)
            publicado = d.isoformat()
    return {
        "publicado": publicado,
        "fotos": fotos,
        "lat": lat if lat and 6.0 < lat < 6.45 else None,
        "lon": lon if lon and -75.8 < lon < -75.4 else None,
        "estrato": int(estrato) if estrato and 1 <= estrato <= 6 else None,
    }


def scrape(estado, conocidos, log):
    for municipio, slugs in config.MUNICIPIOS.items():
        slug = slugs.get(NOMBRE)
        if not slug:
            continue
        est = estado.setdefault(slug, {"cursor": 1})
        cursor = est.get("cursor", 1)
        total = est.get("paginas", 0)
        nuevos = 0
        for _ in range(config.ALBERTOALVAREZ_PAGINAS):
            if total and cursor > total:
                cursor = 1
                est["ciclos"] = est.get("ciclos", 0) + 1
                break  # ciclo completo: el siguiente arranca en la próxima corrida
            url = f"{BASE}/inmuebles/arrendamientos/apartamento/{slug}" + (f"?page={cursor}" if cursor > 1 else "")
            html = get(url, si_404=GONE)
            if html is None:
                break  # fallo temporal: la próxima corrida sigue desde esta misma página
            cursor += 1
            if html == GONE:
                cursor = 1
                est["ciclos"] = est.get("ciclos", 0) + 1
                break
            m = re.search(r"(\d+) apartamentos disponibles", html)
            if m:
                total = (int(m.group(1)) + 8) // 9
            items = _items(html)
            if not items:
                cursor = 1
                est["ciclos"] = est.get("ciclos", 0) + 1
                break
            for it in items:
                l = _normalizar(it, municipio)
                if not est.get("ciclos"):
                    l["_inicial"] = True
                if l["id"] not in conocidos:
                    nuevos += 1
                yield l
        est["cursor"] = cursor
        est["paginas"] = total
        log(f"  albertoalvarez/{slug}: {nuevos} nuevos, cursor {cursor}/{total}")
