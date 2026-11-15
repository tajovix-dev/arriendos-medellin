# Apartamentos en arriendo: Medellín y el sur del Valle de Aburrá

Página personal que reúne ofertas de arriendo de varios portales y se actualiza sola cada 6 horas.

- **Fuentes activas**: Fincaraíz, Rentola, Alberto Álvarez y Metrocuadrado (detalle en [INVESTIGACION_FUENTES.md](INVESTIGACION_FUENTES.md)).
- **Municipios**: Medellín, Sabaneta, Envigado, Itagüí y La Estrella.
- **Precio**: solo apartamentos con canon de hasta **$2.000.000** (`PRECIO_MAX_GUARDAR` en `config.py`).
- **Página**: `docs/` (HTML estático, publicado con GitHub Pages vía Actions).
- **Actualización**: `.github/workflows/actualizar.yml` (cron cada 6 h).

## Qué muestra la página

- **Lista + mapa** lado a lado en pantallas grandes; en el celular, un botón alterna entre lista y mapa.
- **Filtros dinámicos**: municipio, precio y habitaciones siempre visibles; baños, área, estrato, portal y fecha en "Más filtros". Solo aparecen los filtros con datos suficientes (≥15 % de los anuncios), con conteos que cambian según lo que tengas filtrado.
- **Fotos y videos**: flechas en cada tarjeta y visor a pantalla completa con miniaturas, teclado (← →, Esc), deslizar con el dedo, video de YouTube y recorrido 360° cuando el portal lo tiene.
- **Fecha de publicación** y **disponibilidad**: solo si el portal las informa. Si el portal marca un anuncio como no disponible, no se muestra.
- **Etiquetas**: NUEVO (primera vez visto en 48 h), ↓ bajó de precio, "También en…" para repetidos entre portales.
- Favoritos (★) y descartados (✕) se guardan en tu navegador.

## Análisis automático (gratis, en cada actualización)

| Función | Cómo funciona |
|---|---|
| **Precio vs. similares** | Compara cada apartamento con la mediana de los que tienen las mismas habitaciones en su barrio (o municipio si hay pocos), con un ajuste moderado por área. Muestra "▼ 18% más barato que 2 hab en San Isidro (típico $2,4 M)". **BUEN PRECIO** = al menos 15% más barato y sin alertas. |
| **Alertas** | "⚠ Revisa con cuidado" cuando el precio está más de 40% por debajo de similares, no hay fotos, la foto principal aparece en otro anuncio distinto o el área no es coherente. No acusan: solo avisan. |
| **Resumen de hoy** | Nuevos en 24 h, bajadas de precio, cuántos tienen buen precio y un carrusel con las mejores oportunidades (descuento claro, con fotos, recientes). |
| **Recomendados para ti** | Aprende de tus favoritos y descartados (municipio, barrio, habitaciones, precio, área) y muestra los más parecidos, con el motivo. También como orden "Recomendados para ti". |
| **Vista Análisis** | Precio mediano por municipio, tabla por habitaciones, barrios más económicos y costosos por m², distribución de precios, publicaciones por día y evolución del precio mediano (se llena con cada actualización). Todo se recalcula con tus filtros. |

El cálculo está en `scraper/analisis.py`; el historial diario se guarda en `data/historial_mercado.json`.

## Estructura

```
config.py                  ← municipios, topes de precio, páginas por corrida
scraper/run.py             ← corre las fuentes, fusiona, visita detalles y genera la página
scraper/publicar.py        ← regenera docs/data sin volver a raspar
scraper/sources/*.py       ← un scraper por fuente
data/anuncios.jsonl        ← todos los anuncios conocidos (un registro por línea)
data/galeria.jsonl         ← fotos / video / recorrido de cada anuncio
data/estado.json           ← cursores de cada fuente
data/corridas.json         ← historial de corridas
docs/index.html, app.js, styles.css   ← la página
docs/data/                 ← generado en cada corrida (no se guarda en git)
```

## Probar en tu PC

```powershell
pip install -r requirements.txt
python -m scraper.run --rapido     # prueba corta (1 página por fuente)
python -m scraper.run              # corrida completa (~12-15 min)
python -m scraper.servir           # abre http://localhost:8765 (sin caché)
```

## Cómo funciona la actualización

| Fuente | Por corrida | Fotos | Fecha | Disponibilidad |
|---|---|---|---|---|
| Fincaraíz | Hasta 12 páginas "más recientes" por municipio + 15 de barrido rotativo | Galería completa, YouTube, 360° | ✅ | ✅ activo / arrendado / pausado |
| Rentola | 25 páginas por municipio (barrido rotativo) + detalle de 150 anuncios | Galería desde el detalle | ✅ | ✅ |
| Alberto Álvarez | 20 páginas por municipio + detalle de 150 anuncios | Galería desde el detalle | — | ✅ |
| Metrocuadrado | Primera página por municipio (~60) | Galería completa | — | — |

Un anuncio que no se vuelve a ver en 10 días deja de mostrarse (probablemente ya se arrendó) y se borra a los 30.

## Publicar en GitHub (una sola vez)

1. Crea un repositorio y sube este proyecto.
2. **Settings → Pages → Source: GitHub Actions**.
3. **Actions → Actualizar apartamentos → Run workflow** para la primera corrida.
