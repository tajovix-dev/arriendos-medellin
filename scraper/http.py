"""Cliente HTTP con pausa por dominio, reintentos y helpers de extracción."""
import json
import re
import time
from urllib.parse import urlparse

import requests

import config

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

_session = requests.Session()
_session.headers.update({
    "User-Agent": UA,
    "Accept-Language": "es-CO,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})
_ultimo = {}
GONE = "__ya_no_existe__"   # respuesta 404/410 (el anuncio o la página no existen)


def get(url, intentos=3, si_404=None):
    """GET con pausa entre peticiones al mismo dominio.

    Devuelve el texto; `si_404` si el servidor dice que no existe (404/410);
    None si falló la red o el servidor (fallo temporal: no concluir nada)."""
    dominio = urlparse(url).netloc
    for i in range(intentos):
        espera = config.PAUSA_SEGUNDOS - (time.time() - _ultimo.get(dominio, 0))
        if espera > 0:
            time.sleep(espera)
        _ultimo[dominio] = time.time()
        try:
            r = _session.get(url, timeout=(5, 20))
            if r.status_code == 200:
                r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
                return r.text
            if r.status_code in (404, 410):
                return si_404
            if r.status_code == 429:
                time.sleep(min(int(r.headers.get("Retry-After", 30) or 30), 60))
            if r.status_code in (401, 403):
                raise BloqueadoError(f"{r.status_code} en {url}")
        except requests.RequestException:
            pass
        time.sleep(3 * (i + 1))
    return None


class BloqueadoError(Exception):
    pass


def next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


def json_ld(html):
    """Lista de objetos JSON-LD de la página."""
    out = []
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            out.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
    return out


def rsc_payload(html):
    """Une y decodifica los fragmentos self.__next_f.push([1,"..."]) de Next.js App Router."""
    partes = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    texto = []
    for p in partes:
        try:
            texto.append(json.loads('"' + p + '"'))
        except json.JSONDecodeError:
            continue
    return "".join(texto)


def a_int(valor):
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return int(valor)
    digitos = re.sub(r"[^\d]", "", str(valor))
    return int(digitos) if digitos else None
