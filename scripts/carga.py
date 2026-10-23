#!/usr/bin/python3
"""Prueba de carga: N consultas simultáneas contra la API viva.

    /usr/bin/python3 scripts/carga.py [--url http://127.0.0.1:8135] [--hilos 100] [--pedidos 1000]

Cada hilo simula un cliente distinto con X-Forwarded-For (127.0.0.1 es proxy de
confianza), para no chocar con el límite por IP. Sale con 1 si hubo algún error.
"""
import argparse
import json
import random
import statistics
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8135")
ap.add_argument("--hilos", type=int, default=100)
ap.add_argument("--pedidos", type=int, default=1000)
a = ap.parse_args()

RUTAS = [
    "/api/v1/convertir/cie10-a-cie11/E11.9", "/api/v1/convertir/cie10-a-cie11/I10",
    "/api/v1/convertir/cie10-a-cie11/J45.9", "/api/v1/convertir/cie10-a-cie11/A01.0",
    "/api/v1/convertir/cie11-a-cie10/5A11", "/api/v1/convertir/cie11-a-cie10/BA00.Z",
    "/api/v1/cie10/buscar?q=diabetes", "/api/v1/cie11/buscar?q=asma",
    "/api/v1/cie10/buscar?q=neumonia%20bacteriana", "/api/v1/cie11/buscar?q=hipertencion",
    "/api/v1/cie10/E11", "/api/v1/cie11/CA23/hijos", "/api/v1/salud", "/api/v1/meta",
]
LOTE = json.dumps({"sentido": "cie10-a-cie11", "codigos": ["E11.9", "I10", "U07.1", "F32.9", "J45.9"]}).encode()

tiempos, errores = [], []
barrera = threading.Barrier(a.hilos)
lock = threading.Lock()


def uno(i):
    if i < a.hilos:
        barrera.wait()          # los primeros 100 salen exactamente a la vez
    ip = f"10.{i % 250}.{(i // 250) % 250}.{random.randint(1, 250)}"
    if i % 10 == 0:
        req = urllib.request.Request(a.url + "/api/v1/convertir/lote", data=LOTE, method="POST",
                                     headers={"Content-Type": "application/json", "X-Forwarded-For": ip})
    else:
        req = urllib.request.Request(a.url + random.choice(RUTAS), headers={"X-Forwarded-For": ip})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
            if r.status != 200:
                raise RuntimeError(r.status)
    except Exception as e:  # noqa: BLE001
        with lock:
            errores.append(f"{req.full_url}: {e}")
        return
    with lock:
        tiempos.append(time.monotonic() - t0)


t0 = time.monotonic()
with ThreadPoolExecutor(max_workers=a.hilos) as ex:
    list(ex.map(uno, range(a.pedidos)))
total = time.monotonic() - t0
tiempos.sort()
print(f"{a.pedidos} pedidos, {a.hilos} simultáneos, {total:.1f} s ({a.pedidos / total:.0f}/s)")
if tiempos:
    print(f"latencia: mediana {statistics.median(tiempos) * 1000:.0f} ms · "
          f"p95 {tiempos[int(len(tiempos) * .95) - 1] * 1000:.0f} ms · máx {tiempos[-1] * 1000:.0f} ms")
print(f"errores: {len(errores)}")
for e in errores[:10]:
    print("  ", e)
raise SystemExit(1 if errores else 0)
