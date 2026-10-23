#!/usr/bin/python3
"""Conversor CIE-10 ↔ CIE-11: micrositio + API /api/v1 en un solo proceso.

    /usr/bin/python3 serve.py 8135

Servidor WSGI de Werkzeug con un pool fijo de 32 hilos (aguanta 100 consultas
simultáneas, ver scripts/carga.py). Para dejarlo corriendo, un servicio de
systemd o un @reboot en cron; detrás de un proxy con HTTPS si sale a internet. Sin registro de pedidos: sólo contadores agregados en
storage/contadores.json.
"""
import logging
import os
import signal
import sys
from pathlib import Path

# Pocas arenas de malloc: con muchos hilos glibc abre una por hilo y la memoria
# se infla. Tiene que estar antes de arrancar, así que se re-ejecuta una vez.
if os.environ.get("MALLOC_ARENA_MAX") != "2":
    os.execve(sys.executable, [sys.executable, *sys.argv], {**os.environ, "MALLOC_ARENA_MAX": "2"})

sys.path.insert(0, str(Path(__file__).resolve().parent))

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

from werkzeug.serving import BaseWSGIServer  # noqa: E402

from app.api import crear_app  # noqa: E402

puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 8135
logging.getLogger("werkzeug").setLevel(logging.ERROR)   # nada de IPs ni consultas en el log

app = crear_app()


class ServidorConPool(BaseWSGIServer):
    """Un pool fijo de hilos en vez de un hilo nuevo por pedido: con hilos
    efímeros cada uno abría su conexión SQLite y la memoria crecía sin techo
    (300 MB → 790 MB en 5000 pedidos). Con 32 hilos fijos, 32 conexiones."""
    multithread = True
    request_queue_size = 256
    pool = ThreadPoolExecutor(max_workers=32, thread_name_prefix="cie")

    def process_request(self, request, client_address):
        self.pool.submit(self._atender, request, client_address)

    def _atender(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:  # noqa: BLE001
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


servidor = ServidorConPool("0.0.0.0", puerto, app)


def terminar(*_):
    app.extensions["cie_contadores"].volcar()
    sys.exit(0)


signal.signal(signal.SIGTERM, terminar)
print(f"cie: micrositio y API en :{puerto}", flush=True)
try:
    servidor.serve_forever()
finally:
    app.extensions["cie_contadores"].volcar()
