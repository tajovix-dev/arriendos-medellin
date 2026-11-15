"""Cliente de IA: Gemini (varias claves que se turnan) y Groq de respaldo.

Las claves NUNCA van en el repositorio:
- En GitHub Actions vienen de los secretos GEMINI_API_KEYS (separadas por coma) y GROQ_API_KEY.
- En el computador se leen de un archivo .env (ignorado por git).
Si no hay claves, las funciones devuelven None y la página funciona igual sin la parte de IA.
"""
import json
import os
import re
import time
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
MODELO_GEMINI = "gemini-2.5-flash"
MODELO_GROQ = "openai/gpt-oss-120b"
# Cuotas gratuitas reales (medidas): Gemini 2.5 Flash ≈ 20 pedidos/día y 10/minuto por clave
GEMINI_MAX_DIA_POR_CLAVE = 19
GEMINI_SEG_ENTRE_PEDIDOS = 7      # por clave (≈ 8 por minuto)
GROQ_MAX_DIA = 300
MAX_LLAMADAS_CORRIDA = 35         # tope por corrida (4 corridas al día) para no gastar la cuota en una sola
F_USO = RAIZ / "data" / "ia_uso.json"   # solo guarda los últimos 4 caracteres de cada clave


def _cargar_env():
    f = RAIZ / ".env"
    if f.exists():
        for linea in f.read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.strip().startswith("#"):
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_cargar_env()
_CLAVES = [k.strip() for k in os.environ.get("GEMINI_API_KEYS", "").split(",") if k.strip()]
_GROQ = os.environ.get("GROQ_API_KEY", "").strip()
_malas = set()          # claves que no sirven o sin cuota (en esta corrida)
_ultimo = {}            # última vez que se usó cada clave (para respetar el límite por minuto)
_turno = 0
USO = {"gemini": 0, "groq": 0, "fallos": 0, "omitidas_por_cuota": 0}


def _hoy():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=-8))).date().isoformat()   # la cuota de Google se reinicia en hora del Pacífico


def _leer_uso():
    try:
        u = json.loads(F_USO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        u = {}
    if u.get("fecha") != _hoy():
        u = {"fecha": _hoy(), "gemini": {}, "groq": 0, "agotadas": []}
    return u


def _guardar_uso():
    try:
        F_USO.parent.mkdir(parents=True, exist_ok=True)
        F_USO.write_text(json.dumps(_U, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


_U = _leer_uso()
_id = lambda k: k[-4:]


def resumen_cuota():
    """Cuánto queda hoy (para el registro de cada corrida)."""
    libres = sum(max(0, GEMINI_MAX_DIA_POR_CLAVE - _U["gemini"].get(_id(k), 0)) for k in _CLAVES
                 if _id(k) not in _U["agotadas"] and k not in _malas)
    return {"gemini_restantes_hoy": libres, "groq_restantes_hoy": max(0, GROQ_MAX_DIA - _U.get("groq", 0)) if _GROQ else 0,
            "usadas_esta_corrida": USO["gemini"] + USO["groq"]}


def disponible():
    return bool(_CLAVES or _GROQ)


def _json_de(texto):
    """Extrae el primer objeto JSON del texto (algunos modelos agregan texto alrededor)."""
    texto = (texto or "").strip()
    try:
        return json.loads(texto)
    except ValueError:
        m = re.search(r"\{.*\}", texto, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
    return None


def _gemini(prompt, sistema):
    global _turno
    for _ in range(len(_CLAVES)):
        k = _CLAVES[_turno % len(_CLAVES)]
        _turno += 1
        if k in _malas or _id(k) in _U["agotadas"] or _U["gemini"].get(_id(k), 0) >= GEMINI_MAX_DIA_POR_CLAVE:
            continue
        espera = GEMINI_SEG_ENTRE_PEDIDOS - (time.time() - _ultimo.get(k, 0))
        if espera > 0:
            time.sleep(espera)
        _ultimo[k] = time.time()
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{MODELO_GEMINI}:generateContent",
                params={"key": k},
                json={
                    "systemInstruction": {"parts": [{"text": sistema}]},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json",
                                         "thinkingConfig": {"thinkingBudget": 0}},   # sin "pensar": mucho más rápido
                },
                timeout=(10, 90),
            )
        except requests.RequestException:
            continue
        _U["gemini"][_id(k)] = _U["gemini"].get(_id(k), 0) + 1
        if r.status_code == 429:
            if "PerDay" in r.text or "per day" in r.text.lower():
                _U["agotadas"].append(_id(k))      # cuota del día agotada: descansa hasta mañana
            else:
                _ultimo[k] = time.time() + 30      # límite por minuto: esperar un poco
            _guardar_uso()
            continue
        if r.status_code in (400, 401, 403):
            _malas.add(k)            # clave inválida o sin permiso
            if "API key not valid" in r.text and _id(k) not in _U["agotadas"]:
                _U["agotadas"].append(_id(k))
            continue
        if not r.ok:
            continue
        try:
            texto = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, ValueError):
            continue
        datos = _json_de(texto)
        if datos is not None:
            USO["gemini"] += 1
            return datos
    return None


def _groq(prompt, sistema):
    if not _GROQ or _U.get("groq", 0) >= GROQ_MAX_DIA:
        return None
    for intento in range(2):
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {_GROQ}"},
                json={
                    "model": MODELO_GROQ, "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": prompt}],
                },
                timeout=(10, 90),
            )
        except requests.RequestException:
            time.sleep(3)
            continue
        _U["groq"] = _U.get("groq", 0) + 1
        if r.status_code == 429:
            time.sleep(min(30, int(float(r.headers.get("retry-after", 10) or 10))))
            continue
        if not r.ok:
            return None
        datos = _json_de(r.json()["choices"][0]["message"]["content"])
        if datos is not None:
            USO["groq"] += 1
            return datos
    return None


def preguntar_json(prompt, sistema="Responde solo con JSON válido, en español."):
    """Devuelve un dict con la respuesta del modelo, o None si ninguna IA respondió."""
    if not disponible():
        return None
    if USO["gemini"] + USO["groq"] >= MAX_LLAMADAS_CORRIDA:
        USO["omitidas_por_cuota"] += 1
        return None
    datos = _gemini(prompt, sistema) if _CLAVES else None
    if datos is None:
        datos = _groq(prompt, sistema)
    if datos is None:
        USO["fallos"] += 1
    _guardar_uso()
    return datos
