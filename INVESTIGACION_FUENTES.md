# Fuentes de datos: apartamentos en arriendo en Medellín y el sur del Valle de Aburrá

_Zona: Medellín (sur, El Poblado, Belén, Guayabal), Sabaneta, Envigado, Itagüí y La Estrella._
_Investigación y pruebas técnicas: 14-nov-2026, con `curl` sin navegador y un User-Agent normal de Chrome._

## Leyenda
- **Extracción**: cómo se sacan los datos.
- **robots**: ✅ los listados están permitidos · ⛔ el robots.txt prohíbe las páginas de inmuebles (no las raspamos).
- **Prioridad**: 1 = implementar primero.

---

## A. Portales grandes (agregan muchas inmobiliarias)

| # | Sitio | URL de prueba | Resultado | Extracción | robots | Prioridad |
|---|---|---|---|---|---|---|
| 1 | **Fincaraíz** | `fincaraiz.com.co/arriendo/apartamentos/sabaneta/antioquia` (+ `/pagina2`, `/pagina3`…) | 200 · 2.961 resultados | JSON en `<script id="__NEXT_DATA__">` → `props.pageProps.fetchResult.searchFast.data` (21 por página) | ✅ | **1** |
| 2 | **Metrocuadrado** | `metrocuadrado.com/apartamentos/arriendo/sabaneta/` | 200 · 2.399 resultados | JSON escapado en `self.__next_f.push(...)` (Next.js RSC). Campos: `mvalorarriendo`, `marea`, `mnrocuartos`, `mnrobanos`, `mbarrio`, `lat`, `lon`. **Problema: la paginación es interna (`?page=2` no funciona)** | ✅ | **1** |
| 3 | **Properati** (Lifull) | `properati.com.co/s/sabaneta-antioquia/apartamento/arriendo` | Con `curl`: 401. **En Chrome: 2.118 resultados** | JSON-LD (`Apartment`, `GeoCoordinates`), 30 por página. Requiere navegador real (Playwright) | ✅ | **2** |
| 4 | **Mitula** (Lifull, agregador) | `casas.mitula.com.co/casas/arriendo-apartamentos-sabaneta` | Con `curl`: 401. **En Chrome: 6.142 resultados** | JSON-LD (`Apartment`, `GeoCoordinates`). Incluye anuncios de particulares y de inmobiliarias pequeñas publicados vía Proppit. Requiere Playwright | ✅ (prohíbe `/find?`, `/api/`) | 3 (muchos duplicados) |
| 5 | **Ciencuadras** (Grupo Bolívar) | `ciencuadras.com/arriendo/apartamento/antioquia/sabaneta` | 200 · **solo 3 resultados en Sabaneta y 263 en Medellín** | JSON-LD `schema.org`. Su API interna (`api-backend.ciencuadras.com`) exige un token de autenticación, así que no la usamos | ✅ (evitar `?q=`, `/search/`) | 3 (poco inventario) |
| 6 | **PuntoPropiedad** (también `icasas.com.co`, que redirige aquí) | `puntopropiedad.com/arriendo/apartamentos/sabaneta` | 200 | Objetos `"price":2900000.0` en scripts / JSON-LD. Rutas por municipio: `/envigado`, `/estrella`, `/bello`… | ✅ | **2** |
| 7 | **Espacio Urbano** (portal local de Medellín) | `espaciourbano.com/arriendos.asp?sZona=1` | 200 | HTML clásico (ASP) → BeautifulSoup | ✅ | **2** |
| 8 | **Mercado Libre Inmuebles** | `api.mercadolibre.com/sites/MCO/search?category=MCO1459` | Sin token: 403. La web muestra una página anti-bot. | **API oficial** con OAuth (crear app en developers.mercadolibre.com.co) | — | 3 |
| 9 | **Aptuno** (arriendo sin fiador) | `aptuno.com` | 200, pero es una SPA en React (HTML vacío) | API GraphQL interna, prohibida en robots (`/graphql/`). El sitemap trae casi todo Bogotá (solo 3 URLs de Medellín). | ⛔ API | ✗ Poco inventario local |
| 10 | **La Haus** | `lahaus.com` | 200, con Cloudflare y captcha | Verificado en Chrome: "Compra vivienda nueva con LaHaus", **no tiene arriendos** | — | ✗ |
| 11 | **Habi** | `habi.co` | 200 (Next.js) | Compraventa, no arriendo | — | ✗ |

## B. Inmobiliarias del sur con sitio propio

Muchas inmobiliarias usan el mismo software, así que **un scraper por plataforma sirve para varias**.

### B1. Plataforma SoftInm (URLs `/inmueble/{id}-{tipo}-{gestion}-{ciudad}-{barrio}-...`)
Las páginas se generan en el servidor (HTML) y el slug de la URL ya trae tipo, municipio, barrio y alcobas.

| Sitio | Zona fuerte | Rutas | Notas |
|---|---|---|---|
| **Mavebienes** (`mavebienes.com`) | **Sabaneta** (Aves María, San José, Alto de las Flores) | `/inmueble/1012-apartamento-arriendo-sabaneta-aves-maria-...` | Muy enfocada en Sabaneta. **Prioridad 1 entre las inmobiliarias.** |
| **Su Vivienda** (`suvivienda.com.co`) | Envigado, Itagüí, Medellín | `/inmuebles/Arriendo/Apartamento/Envigado/` | Listados por municipio con filtros `PrecioIni` y `PrecioFin` |
| **Merino Hermanos** (`merinohermanos.com`) | La Estrella, Medellín | `/ver_inmueble/apartamento_en_la_estrella_en_la_aldea_269859` | Detrás de Cloudflare (por ahora responde 200) |
| **Arrendamientos Ayurá** (`arrendamientosayura.com`) | Envigado | Next.js con `"price":4500000` embebido | Mezcla SoftInm con un frontend en Next.js |

### B2. Plataforma SIMI (simi.com.co)
| Sitio | Zona fuerte | Extracción |
|---|---|---|
| **Arrendamientos Envigado S.A.** (`arrendamientosenvigadosa.com.co`) | Envigado, Itagüí, Sabaneta (tiene oficinas en los tres) | Next.js; JSON con `"precio"` embebido; detalle en `/inmueble/apartamento-arriendo-{ciudad}-{barrio}-{id}`. robots prohíbe `/api/`, así que solo se usan las páginas públicas. |
| **Inmobiliaria La Estrella** (`inmobiliarialaestrella.com`) | La Estrella, Sabaneta | JSON en la página con `"price":"$ 3.500.000"` (texto, hay que limpiarlo) |
| **Maxibienes** (`maxibienes.com`) | Medellín y el sur | HTML; búsqueda en `/resultados-de-la-busqueda-de-inmueble` |

### B3. Next.js propio
| Sitio | Zona fuerte | Extracción | robots |
|---|---|---|---|
| **Alberto Álvarez** (`albertoalvarez.com`), la inmobiliaria más antigua de Medellín | Envigado, Sabaneta, Itagüí, La Estrella, Medellín | `/inmuebles/arrendamientos/apartamento/sabaneta` → 200; **321 apartamentos en Sabaneta**; paginación `?page=2` … `?page=36`; JSON-LD con `"price":2600000, "priceCurrency":"COP"` | ✅ · **Prioridad 1** |
| **Arrendamientos Las Vegas** (`arrendamientoslasvegas.com`) | Envigado, Sabaneta, La Estrella | Next.js (misma plantilla que Uribienes) | ⛔ prohíbe `/inmuebles/`. Usar su perfil en Fincaraíz. |
| **Uribienes** (`uribienes.com`) | Medellín y el sur | Igual que Las Vegas | ⛔ prohíbe `/inmuebles/[0-9]+` |

### B4. Solo publican en portales (sin sitio útil)
Grupo Santamaría, Escobar Mejía y Propiedades y Proyectos tienen el dominio estacionado o redirigen a una página genérica. Escala Inmobiliaria, Arrendamientos JG, Alquiventas y Conaltura no tienen sitio de arriendo propio o no responde. Su inventario se obtiene filtrando **por inmobiliaria en Fincaraíz**, que tiene perfiles como `fincaraiz.com.co/inmobiliarias/grupo-santamaria/174680279/arriendo/...` y `/arrendamientos-las-vegas/174829196/...`.

## C. Descartadas

| Sitio | Motivo |
|---|---|
| Trovit | Agregador de Lifull (mismo inventario que Mitula y Properati). Devuelve 401 con `curl` y la extensión de Chrome no permite abrirlo |
| Nuroa, OLX Colombia | No responden (OLX salió de Colombia) |
| Clasificados El Colombiano | 410 (servicio cerrado) |
| Red Inmobiliaria MLS (`red.redinmobiliariamls.com`) | Redirige a `medellin.xposureapp.com`; los precios están mezclados (venta como "rent") y hay muchos duplicados |
| Facebook Marketplace / grupos | Requieren login y sus términos prohíben el scraping |

---

## D. Hallazgos verificados con Chrome (14-nov-2026)

**Inventario de apartamentos en arriendo en Sabaneta por fuente** (incluye duplicados entre portales):

| Fuente | Sabaneta | Notas |
|---|---|---|
| Mitula | 6.142 | Agregador: suma Properati, Proppit y otros |
| Fincaraíz | 2.961 | |
| Metrocuadrado | 2.399 | |
| Properati | 2.118 | |
| Alberto Álvarez | 321 | Sitio propio |
| Ciencuadras | 3 | Casi sin oferta en el sur (263 en Medellín) |

**Detalles técnicos:**
- **Fincaraíz**: la página se arma completa en el servidor y no llama a APIs al paginar. Cada anuncio en `searchFast.data` trae: `id`, `title`, `price.amount`, `price.admin_included`, `m2`, `latitude`, `longitude`, `created_at`, `updated_at`, `link`, `owner.name` (inmobiliaria), `locations` (barrio, comuna, zona) y `technicalSheet` (habitaciones, baños, estrato, antigüedad…). **Es la fuente más completa.**
- **Metrocuadrado**: no hay llamadas XHR/fetch. La paginación (`rc-pagination`) usa acciones internas de Next.js, y `?page=N` devuelve los mismos ~55 anuncios. Hay dos opciones:
  1. **Segmentar la búsqueda** por barrio, número de habitaciones o rangos de precio, para que cada página traiga anuncios distintos con HTTP simple.
  2. Usar **Playwright** y hacer clic en la paginación.
  - No usamos claves de API internas que no sean nuestras.
- **Properati y Mitula**: bloquean `curl` (401), pero en Chrome cargan normal y traen JSON-LD con coordenadas. Su robots.txt permite los listados. Se pueden usar con **Playwright**.
- **Ciencuadras**: su backend (`api-backend.ciencuadras.com`) requiere un token. Además tiene muy poca oferta en el sur, así que baja de prioridad.
- **No se pudieron abrir con la extensión** (bloqueados por la política de dominios de la extensión): Mercado Libre, Trovit, Aptuno y PuntoPropiedad. PuntoPropiedad sí responde 200 con `curl`.

---

## E. Búsqueda ampliada (14-nov-2026, segunda ronda)

### E1. Agregadores y portales nuevos
| Sitio | Sabaneta | Extracción | robots | Prioridad |
|---|---|---|---|---|
| **Rentola** (`rentola.co.com/arriendo/apartamento/sabaneta`) ⭐ | **6.254 anuncios** (188 nuevos) | `curl` 200 · JSON-LD con 21 `Apartment` + `GeoCoordinates` por página · paginación `?page=N` · reúne Properati, Metrocuadrado, Wasi y otros. La extensión de Chrome no permite abrirlo. | ✅ (prohíbe `/ads-map/`, `/api/events`) | **1** (máxima cobertura con HTTP simple) |
| **Arriendo.com** (`arriendo.com/co/sabaneta/`) | Sin conteo | WordPress + JSON-LD (`CollectionPage`) | ✅ con crawl-delay de 3–5 s; prohíbe `/buscador/` y `?q=` | 3 |
| **Colombia Move** (`colombiamove.com/ciudad/sabaneta/...`) | 0 activos | Next.js + JSON-LD. Dueños directos, pero muy poco inventario | — | ✗ por ahora |
| **Yumblin** (clasificados) | — | Devuelve 429 (límite de peticiones) | — | ✗ |
| **Encuentra24** | — | 403 (Cloudflare) y sin presencia visible en Medellín | — | ✗ |

### E2. Más inmobiliarias del sur (con sitio propio)
| Inmobiliaria | Zona | Plataforma | Ruta de ejemplo |
|---|---|---|---|
| **Arriendos Sabaneta** (`arriendosabaneta.com`) | Sabaneta, Envigado, Itagüí, La Estrella | **Wasi** | `/apartamentos-envigado` |
| **La Palma Inmobiliaria** (`lapalmainmobiliaria.com.co`) | Medellín, Sabaneta, Envigado, Itagüí, La Estrella | **Wasi** | `/apartamento-arriendo-el-trapiche-sabaneta/9682992` |
| **Inmobiliaria Proteger** (`inmobiliariaproteger.com`) | Envigado, Sabaneta, Itagüí, La Estrella | **Wasi** (Cloudflare) | `/apartamento-alquiler-aliadas-sabaneta/10581949` · `/s/alquiler` |
| **Vive ValleSur** (`vive-vallesur.com`) | Aburrá Sur (Envigado, Sabaneta, Itagüí, La Estrella, Caldas) | Propia + JSON-LD | `/propiedades/` (52 enlaces de arriendo) |
| **Juan Raíces** (`juanraices.com`) | Envigado (66), La Estrella (36), Sabaneta (10) | **SoftInm** + WordPress | `/inmuebles/?_sft_tipo_propiedad=apartamento` |
| **Punto Alto** (`puntoalto.com.co`) | Medellín, Envigado, Sabaneta | **SIMI** + Next.js | `/arriendos/`, `/inmueble/{id}/...` |
| **Coninsa** (`coninsa.co`) | Sabaneta (casas, oficinas, aptos) | Propia + JSON-LD | También publica en Fincaraíz y Metrocuadrado |

**Plataformas Wasi**: las URLs siguen el patrón `/{tipo}-{arriendo|alquiler}-{barrio}-{ciudad}/{id}`, así que **un solo scraper sirve para todas**. Wasi también tiene una API oficial (`api.wasi.co`), pero exige `id_company` y `wasi_token` de cada inmobiliaria. Solo sirve si una inmobiliaria nos da acceso.

**Inmobiliarias activas en el sur que solo vimos en portales** (se capturan desde Fincaraíz o Metrocuadrado con el campo `owner`): Prointegral, BEMSA, Maxibienes, Confort Gestores, RV Inmobiliaria, Millán & Asociados, Grupo Empresarial Mi, Arrendamientos Alnago, Inurbanas, Conaltura, Escala, Herencia, Uribienes, Las Vegas, Grupo Santamaría, Su Vivienda, Merino Hermanos.

### E3. Amoblados, estadías medias y extranjeros
| Sitio | Qué ofrece | Extracción |
|---|---|---|
| **Nomad Barrio** (`nomadbarrio.com/rent`) | Más de 500 amoblados mensuales sin fiador en Medellín y Envigado | `curl` 200, JSON-LD con `price` (Cloudflare) |
| **Medellín Apartments** (`medellinapartments.co`) | Arriendos largos para expats | JSON-LD con `price` |
| **Rent Medellín** (`rentmedellin.co`) | Amoblados + índice de precios por barrio 2026 | JSON-LD; útil como **referencia de precios** |
| **Flatio** | Estadías medias en Medellín | JSON-LD (Cloudflare) |
| **Nomadapto** | Amoblados en Envigado y Sabaneta | No respondió |
| **Airbnb (estadías mensuales)** | `airbnb.com/sabaneta-colombia/stays/monthly` | Responde 200, pero **sus términos prohíben el scraping**. Solo sirve como referencia manual. |

### E4. Datos oficiales y de referencia (no son anuncios, pero sirven para comparar precios)
| Fuente | Qué tiene | Acceso |
|---|---|---|
| **OIME, Observatorio Inmobiliario Catastral de Medellín** (Alcaldía) | 8.066 observaciones de arriendo entre 2014 y jul-2026; precio/m² por zona (El Poblado: $53.812/m² en jul-2026) | Portal: `medellin.gov.co/es/secretaria-gestion-y-control-territorial/oime/` · tablero ArcGIS: `arcg.is/0XCTeK` · servicio REST `servidormapas/.../VM_Oferta_Comercial_Oime/MapServer` (**503 al momento de la prueba**) |
| **IGAC, transacciones inmobiliarias** (datos.gov.co `7y2j-43cv`) | 30,9 millones de transacciones desde 2015 (ventas, no arriendos) | API Socrata abierta: `datos.gov.co/resource/7y2j-43cv.json` |
| **Banco de la República / DANE** | Índice de arriendos (IPC vivienda) por ciudad | Series descargables |
| **Fincaraíz** (texto SEO de cada búsqueda) | Promedio por municipio (Sabaneta: $3.543.360, 72 m² promedio) | Ya viene en la página del listado |

### E5. Canales sin scraping viable
- **Facebook Marketplace y grupos** ("Arriendos Sabaneta", "Apartments for Rent in Medellín"): requieren login y sus términos lo prohíben. Solo revisión manual.
- **WhatsApp y Telegram** por barrio: manual.
- **Google Maps Places API** (oficial, de pago con nivel gratuito): no trae anuncios, pero sirve para **descubrir todas las inmobiliarias** de Sabaneta, Envigado, Itagüí y La Estrella con su sitio web y ampliar esta lista.

---

## Plan de implementación (scrapers)

| Orden | Scraper | Cubre | Técnica |
|---|---|---|---|
| 1 | `fincaraiz` | Portal más grande y perfiles de inmobiliarias sin sitio (Las Vegas, Santamaría…) | `__NEXT_DATA__` → JSON |
| 2 | `metrocuadrado` | Portal #2 | Payload RSC → JSON |
| 3 | `albertoalvarez` | Inmobiliaria grande del sur | JSON-LD |
| 4 | `softinm` (genérico) | Mavebienes, Su Vivienda, Merino Hermanos | HTML + slug de la URL |
| 4b | `rentola` ⭐ | 6.254 anuncios en Sabaneta; agrega Properati, Metrocuadrado y Wasi | HTTP + JSON-LD, `?page=N` |
| 4c | `wasi` (genérico) | Arriendos Sabaneta, La Palma, Proteger | HTML + slug de la URL |
| 5 | `properati` | 2.118 anuncios en Sabaneta (posiblemente ya cubierto por Rentola) | Playwright + JSON-LD |
| 6 | `puntopropiedad` | Portal mediano | JSON embebido |
| 7 | `simi` (genérico) | Arrendamientos Envigado, La Estrella, Maxibienes | JSON embebido / HTML |
| 8 | `espaciourbano` | Portal local | HTML |
| 9 | `mitula` (opcional) | Particulares vía Proppit | Playwright + JSON-LD |
| 10 | `ciencuadras` (opcional) | Poco inventario en el sur | JSON-LD |
| 11 | `mercadolibre` (opcional) | Particulares | API oficial con OAuth |

**Stack**: Python + `httpx` + `selectolax`/`BeautifulSoup` + `extruct` (JSON-LD) + SQLite. **Playwright** solo para Properati, Mitula y la paginación de Metrocuadrado.

**Modelo común**: `fuente, id_fuente, url, inmobiliaria, municipio, barrio, precio_arriendo, admon, area_m2, habitaciones, banos, parqueaderos, estrato, lat, lon, fecha_publicacion, primera_vez_visto, ultima_vez_visto`.

**Reglas**:
- Esperar entre 1 y 3 s entre peticiones y respetar robots.txt (por eso Las Vegas y Uribienes van por Fincaraíz).
- Filtrar precios de venta mezclados con arriendos (p. ej. descartar precios > 30.000.000).
- Deduplicar entre portales: la misma inmobiliaria publica el mismo apartamento en 3 o 4 sitios. Criterio: lat/lon cercanos + área ±3 m² + precio igual, o el código de la inmobiliaria.
- Uso personal o de investigación; no republicar los anuncios.
