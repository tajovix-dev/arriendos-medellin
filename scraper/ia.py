"""Cliente de IA: Gemini (varias claves y dos modelos con cuotas separadas) y Groq de respaldo.

Las claves NUNCA van en el repositorio:
- En GitHub Actions vienen de los secretos GEMINI_API_KEYS (separadas por coma) y GROQ_API_KEY.
- En el computador se leen de un archivo .env (ignorado por git).
Si no hay claves, las funciones devuelven None y la página funciona igual sin la parte de IA.

Optimización de cuota (plan gratuito):
- Cada modelo de Gemini tiene su propia cuota diaria por clave (≈20 pedidos/día).
- Tareas "ligeras" (clasificar titulares) usan primero Flash-Lite; las "fuertes" (investigar un
  barrio, elegir imperdibles) usan primero Flash. Si un modelo se agota, se usa el otro y al final Groq.
- Se cuenta el uso por modelo y clave (solo los últimos 4 caracteres) en data/ia_uso.json.
"""
import json
import os
import re
import time
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
FLASH = "gemini-2.5-flash"
FLASH_LITE = "gemini-3.1-flash-lite"
MODELO_GROQ = "openai/gpt-oss-120b"
ORDEN = {"fuerte": [FLASH, FLASH_LITE], "ligero": [FLASH_LITE, FLASH]}
# Cuotas gratuitas medidas (con margen): ≈20 pedidos/día y 10/minuto por clave y modelo
GEMINI_MAX_DIA = {FLASH: 19, FLASH_LITE: 19}
GEMINI_SEG_ENTRE_PEDIDOS = 7      # por clave y modelo (≈ 8 por minuto)
GROQ_MAX_DIA = 300
MAX_LLAMADAS_CORRIDA = 40         # tope por corrida (hay 4 al día)
F_USO = RAIZ / "data" / "ia_uso.json"


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
_ultimo = {}
_turno = {}
USO = {"gemini": 0, "groq": 0, "fallos": 0, "omitidas_por_cuota": 0, "por_modelo": {}}
_id = lambda k: k[-4:]


def _hoy():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=-8))).date().isoformat()   # Google reinicia la cuota en hora del Pacífico


def _leer_uso():
    try:
        u = json.loads(F_USO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        u = {}
    invalidas = u.get("invalidas", [])
    if u.get("fecha") != _hoy() or "uso" not in u:
        u = {"fecha": _hoy(), "uso": {}, "agotadas": [], "groq": 0}
    u["invalidas"] = invalidas            # una clave inválida sigue inválida otros días
    return u


_U = _leer_uso()


def _guardar_uso():
    try:
        F_USO.parent.mkdir(parents=True, exist_ok=True)
        F_USO.write_text(json.dumps(_U, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def disponible():
    return bool(_CLAVES or _GROQ)


def resumen_cuota():
    out = {}
    for m in (FLASH, FLASH_LITE):
        out[m] = sum(max(0, GEMINI_MAX_DIA[m] - _U["uso"].get(f"{m}|{_id(k)}", 0)) for k in _CLAVES
                     if _id(k) not in _U["invalidas"] and f"{m}|{_id(k)}" not in _U["agotadas"])
    out["groq"] = max(0, GROQ_MAX_DIA - _U.get("groq", 0)) if _GROQ else 0
    out["usadas_esta_corrida"] = USO["gemini"] + USO["groq"]
    return out


def _json_de(texto):
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


def _gemini(prompt, sistema, modelo):
    n = len(_CLAVES)
    for _ in range(n):
        i = _turno.get(modelo, 0)
        _turno[modelo] = i + 1
        k = _CLAVES[i % n]
        uid = f"{modelo}|{_id(k)}"
        if _id(k) in _U["invalidas"] or uid in _U["agotadas"] or _U["uso"].get(uid, 0) >= GEMINI_MAX_DIA[modelo]:
            continue
        espera = GEMINI_SEG_ENTRE_PEDIDOS - (time.time() - _ultimo.get(uid, 0))
        if espera > 0:
            time.sleep(espera)
        _ultimo[uid] = time.time()
        config = {"temperature": 0.2, "responseMimeType": "application/json"}
        if modelo == FLASH:
            config["thinkingConfig"] = {"thinkingBudget": 0}     # sin "pensar": más rápido
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent",
                params={"key": k},
                json={"systemInstruction": {"parts": [{"text": sistema}]},
                      "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      "generationConfig": config},
                timeout=(10, 90),
            )
        except requests.RequestException:
            continue
        _U["uso"][uid] = _U["uso"].get(uid, 0) + 1
        if r.status_code == 429:
            if "PerDay" in r.text or "per day" in r.text.lower():
                _U["agotadas"].append(uid)          # cuota del día de este modelo agotada
            else:
                _ultimo[uid] = time.time() + 30     # límite por minuto: esperar
            _guardar_uso()
            continue
        if r.status_code in (400, 401, 403):
            if "API key not valid" in r.text and _id(k) not in _U["invalidas"]:
                _U["invalidas"].append(_id(k))
            continue
        if not r.ok:
            continue
        try:
            partes = r.json()["candidates"][0]["content"]["parts"]
            texto = "".join(p.get("text", "") for p in partes)
        except (KeyError, IndexError, ValueError):
            continue
        datos = _json_de(texto)
        if datos is not None:
            USO["gemini"] += 1
            USO["por_modelo"][modelo] = USO["por_modelo"].get(modelo, 0) + 1
            return datos
    return None


def _groq(prompt, sistema):
    if not _GROQ or _U.get("groq", 0) >= GROQ_MAX_DIA:
        return None
    for _ in range(2):
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {_GROQ}"},
                json={"model": MODELO_GROQ, "temperature": 0.2, "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": prompt}]},
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
            USO["por_modelo"]["groq"] = USO["por_modelo"].get("groq", 0) + 1
            return datos
    return None


def preguntar_json(prompt, sistema="Responde solo con JSON válido, en español.", tarea="fuerte"):
    """Devuelve un dict con la respuesta, o None si no hay IA o cuota. tarea: 'fuerte' o 'ligero'."""
    if not disponible():
        return None
    if USO["gemini"] + USO["groq"] >= MAX_LLAMADAS_CORRIDA:
        USO["omitidas_por_cuota"] += 1
        return None
    datos = None
    for modelo in ORDEN.get(tarea, ORDEN["fuerte"]):
        if _CLAVES and datos is None:
            datos = _gemini(prompt, sistema, modelo)
    if datos is None:
        datos = _groq(prompt, sistema)
    if datos is None:
        USO["fallos"] += 1
    _guardar_uso()
    return datos
