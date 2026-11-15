"""Servidor local para ver la página en http://localhost:8765 sin caché del navegador.

Uso: python -m scraper.servir [puerto]
"""
import functools
import http.server
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


class SinCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # Siempre la versión más reciente de los archivos (útil mientras se prueba)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):
        pass  # sin ruido en la consola


if __name__ == "__main__":
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    manejador = functools.partial(SinCache, directory=str(DOCS))
    with http.server.ThreadingHTTPServer(("127.0.0.1", puerto), manejador) as srv:
        print(f"Página en http://localhost:{puerto}  (Ctrl+C para detener)")
        srv.serve_forever()
