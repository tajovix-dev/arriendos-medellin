"""Configuración del buscador de apartamentos.

Cambia estos valores y la próxima corrida (GitHub Actions cada 6 h) los usa.
"""

# Municipios a vigilar. Clave = nombre que se muestra en la página.
# Cada fuente tiene su propio "slug" para la URL.
MUNICIPIOS = {
    "Medellín":    {"fincaraiz": "medellin",    "rentola": None,          "albertoalvarez": "medellin",    "metrocuadrado": "medellin"},
    "Sabaneta":    {"fincaraiz": "sabaneta",    "rentola": "sabaneta",    "albertoalvarez": "sabaneta",    "metrocuadrado": "sabaneta"},
    "Envigado":    {"fincaraiz": "envigado",    "rentola": "envigado",    "albertoalvarez": "envigado",    "metrocuadrado": "envigado"},
    "Itagüí":      {"fincaraiz": "itagui",      "rentola": "itagui",      "albertoalvarez": "itagui",      "metrocuadrado": "itagui"},
    "La Estrella": {"fincaraiz": "la-estrella", "rentola": "la-estrella", "albertoalvarez": "la-estrella", "metrocuadrado": "la-estrella"},
}
# Nota: Rentola tiene unos 33.700 anuncios solo en Medellín. Por eso Medellín
# va desactivado en Rentola (None) y se cubre con Fincaraíz, Alberto Álvarez y
# Metrocuadrado.

# Canon máximo que se guarda. Es un tope de almacenamiento, no el filtro de la
# página. Fincaraíz ya filtra por este valor en la URL; en las demás fuentes se
# descartan al leerlas. Los anuncios más caros que ya estaban guardados no se
# publican, pero se siguen usando para calcular las medianas del mercado.
PRECIO_MAX_GUARDAR = 2_000_000
PRECIO_MIN_GUARDAR = 300_000  # descarta errores y anuncios de habitaciones sueltas

# Antigüedad máxima de la publicación (según la fecha que da el portal). Los más viejos
# no se publican: lo más probable es que ya estén arrendados. Los anuncios sin fecha
# (Alberto Álvarez, Metrocuadrado) sí se publican.
PUBLICACION_MAX_DIAS = 60

# Presupuesto por defecto en la página web
PRESUPUESTO_DEFECTO = 2_000_000

# Límite de páginas por corrida (para no sobrecargar los sitios ni pasar del
# tiempo de GitHub Actions)
FINCARAIZ_PAGINAS_NUEVOS = 12   # páginas "más recientes" por municipio (se detiene antes si ya no hay nuevos)
FINCARAIZ_PAGINAS_BARRIDO = 15  # páginas extra por municipio del barrido completo rotativo
RENTOLA_PAGINAS = 25            # páginas por municipio y corrida (barrido rotativo, no permite ordenar por fecha)
ALBERTOALVAREZ_PAGINAS = 20     # páginas por municipio y corrida (9 anuncios por página)

# Páginas de detalle por fuente y corrida (Rentola y Alberto Álvarez no traen la
# galería completa en el listado; se visitan primero los anuncios más nuevos)
DETALLE_MAX_POR_FUENTE = 400
MINUTOS_MAX_DETALLE = 30   # tope (desde el inicio de la corrida) para visitar detalles; el paso se corta a los 48 min

# Pausa entre peticiones al mismo sitio (segundos)
PAUSA_SEGUNDOS = 1.5

# Un anuncio que no se vuelve a ver en este número de días se marca como inactivo
DIAS_INACTIVO = 10
# Y se borra del archivo después de este número de días sin verse
DIAS_BORRAR = 30
