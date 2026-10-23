#!/usr/bin/python3
"""Chequeo de salud del conversor (para cron, un monitor o a mano).

    /usr/bin/python3 scripts/salud.py [URL]      # por defecto http://127.0.0.1:8135

Verifica salud, una conversión conocida en cada sentido, una búsqueda, la
documentación y el micrositio. Imprime una línea por control y sale con 1 si
alguno falla.
"""
import json
import sys
import time
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8135").rstrip("/")


def traer(ruta):
    t0 = time.monotonic()
    with urllib.request.urlopen(BASE + ruta, timeout=10) as r:
        cuerpo = r.read()
        return r.status, cuerpo, (time.monotonic() - t0) * 1000


CONTROLES = [
    ("salud", "/api/v1/salud", lambda d: d["estado"] == "ok"),
    ("E11 → 5A11", "/api/v1/convertir/cie10-a-cie11/E11", lambda d: d["equivalencias"][0]["codigo"] == "5A11"),
    ("RA01.0 → U07.1", "/api/v1/convertir/cie11-a-cie10/RA01.0", lambda d: d["equivalencias"][0]["codigo"] == "U07.1"),
    ("búsqueda", "/api/v1/cie11/buscar?q=asma", lambda d: d["total"] > 0),
    ("openapi", "/api/v1/openapi.json", lambda d: d["openapi"].startswith("3.")),
    ("documentación", "/api/docs/", None),
    ("micrositio", "/", None),
]

fallas = 0
for nombre, ruta, prueba in CONTROLES:
    try:
        estado, cuerpo, ms = traer(ruta)
        ok = estado == 200 and (prueba is None or prueba(json.loads(cuerpo)))
    except Exception as e:  # noqa: BLE001
        ok, ms = False, 0
        nombre += f" ({e})"
    fallas += not ok
    print(f"{'ok   ' if ok else 'FALLA'} {nombre} {ms:.0f} ms")
sys.exit(1 if fallas else 0)
