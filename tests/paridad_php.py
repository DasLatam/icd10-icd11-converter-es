#!/usr/bin/python3
"""Compara la API PHP (php/index.php) con la Python (app/api.py), pedido por pedido.

    python3 tests/paridad_php.py [--php /ruta/a/php] [--puerto 8197]

Arma la versión estática en una carpeta temporal (scripts/construir_estatico.py),
la sirve con `php -S` y pide lo mismo a las dos: mismo estado HTTP y mismo JSON.
Sale con 1 si alguna difiere. Necesita PHP ≥ 7.4 con pdo_sqlite e intl.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app.api import CONFIG_DEFECTO, crear_app  # noqa: E402
from scripts.construir_estatico import construir  # noqa: E402

GET = [
    "salud", "meta",
    "cie10/capitulos", "cie11/capitulos",
    "cie10/buscar?q=E11", "cie10/buscar?q=e119", "cie10/buscar?q=diabetes",
    "cie10/buscar?q=diabetes%20tipo%202&por_pagina=5", "cie10/buscar?q=depresion",
    "cie10/buscar?q=diabetis", "cie10/buscar?q=asmma", "cie10/buscar?q=neumonia&pagina=2",
    "cie10/buscar?q=fractura&capitulo=XIX&tipo=categoria", "cie10/buscar?q=fractura&solo_validos=1",
    "cie10/buscar?q=%C3%BAlcera%20g%C3%A1strica", "cie10/buscar?q=xyzzyqq",
    "cie11/buscar?q=5A1", "cie11/buscar?q=asma&por_pagina=5", "cie11/buscar?q=hipertension",
    "cie11/buscar?q=covid", "cie11/buscar?q=diabetes&capitulo=5", "cie11/buscar?q=diabetes&tipo=bloque",
    "cie11/buscar?q=nemonia",
    "cie10/E11.9", "cie10/E119", "cie10/I10", "cie10/U07.1", "cie10/A09", "cie10/A09X", "cie10/XIX",
    "cie10/C00-C97", "cie10/U99", "cie10/E11/hijos", "cie10/E11.9/ancestros", "cie10/ZZZ9",
    "cie11/5A11", "cie11/BA00.Z", "cie11/5A11/hijos", "cie11/5A11/ancestros", "cie11/XN8P1",
    "cie11/761947693%2Funspecified", "cie11/761947693/unspecified", "cie11/761947693/unspecified/ancestros", "cie11/QQ99",
    "convertir/cie10-a-cie11/E11.9", "convertir/cie10-a-cie11/I10", "convertir/cie10-a-cie11/U07.1",
    "convertir/cie10-a-cie11/W01.0", "convertir/cie10-a-cie11/A00", "convertir/cie10-a-cie11/B18.00",
    "convertir/cie10-a-cie11/U99", "convertir/cie10-a-cie11/F32", "convertir/cie10-a-cie11/C50.9",
    "convertir/cie10-a-cie11/E11.9?sin_sugerencias=1", "convertir/cie10-a-cie11/ZZZ9",
    "convertir/cie11-a-cie10/5A11", "convertir/cie11-a-cie10/BA00.Z", "convertir/cie11-a-cie10/XN8P1",
    "convertir/cie11-a-cie10/1A00", "convertir/cie11-a-cie10/6A70.0", "convertir/cie11-a-cie10/RA01.0",
    "convertir/otro/E11", "convertir/cie10-a-cie11/%3Cscript%3E",
    # errores
    "cie10/buscar", "cie10/buscar?q=a&por_pagina=99", "cie10/buscar?q=a&pagina=x",
    "cie10/buscar?q=a&tipo=raro", "cie11/buscar?q=a&tipo=grupo", "no/existe", "convertir/lote",
]
POST = [
    {"sentido": "cie10-a-cie11", "codigos": ["E11.9", "I10", "U07.1", "ZZZ9", "no vale", 3]},
    {"sentido": "cie11-a-cie10", "codigos": ["5A11", "BA00.Z"], "sin_sugerencias": False},
    {"sentido": "cie10-a-cie11", "codigos": []},
    {"sentido": "x", "codigos": ["E11"]},
    {"sentido": "cie10-a-cie11", "codigos": ["E11"], "paciente": "Juan"},
    {"sentido": "cie10-a-cie11", "codigos": ["A00"] * 101},
]


def pedir(url: str, cuerpo: bytes | None = None, tipo: str = "application/json"):
    req = urllib.request.Request(url, data=cuerpo, headers={"Content-Type": tipo} if cuerpo is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--php", default=shutil.which("php") or "php")
    ap.add_argument("--puerto", type=int, default=8197)
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="cie_paridad_"))
    proc = None
    try:
        construir(tmp / "dist", None, None)
        proc = subprocess.Popen([a.php, "-S", f"127.0.0.1:{a.puerto}", "-t", str(tmp / "dist"),
                                 str(tmp / "dist" / "router.php")],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{a.puerto}/api/v1/"
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "salud", timeout=1)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        app = crear_app(config={**CONFIG_DEFECTO, "limite_por_minuto": 100000},
                        contadores=tmp / "contadores.json")
        py = app.test_client()
        fallas = 0

        def comparar(nombre, est_py, cuerpo_py, est_php, cuerpo_php):
            nonlocal fallas
            try:
                j_php = json.loads(cuerpo_php)
            except ValueError:
                j_php = cuerpo_php[:300]
            j_py = json.loads(cuerpo_py)
            if est_py != est_php or j_py != j_php:
                fallas += 1
                print(f"✗ {nombre}: python {est_py} / php {est_php}")
                if isinstance(j_php, dict):
                    for k in sorted(set(j_py) | set(j_php)):
                        if j_py.get(k) != j_php.get(k):
                            print(f"    {k}: py={json.dumps(j_py.get(k), ensure_ascii=False)[:200]}")
                            print(f"    {k}: php={json.dumps(j_php.get(k), ensure_ascii=False)[:200]}")
                else:
                    print("   ", j_php)
            else:
                print(f"✓ {nombre} ({est_py})")

        for ruta in GET:
            r = py.get("/api/v1/" + ruta)
            est, cuerpo = pedir(base + ruta)
            comparar("GET " + ruta, r.status_code, r.get_data(), est, cuerpo)
        for c in POST:
            datos = json.dumps(c).encode()
            r = py.post("/api/v1/convertir/lote", data=datos, content_type="application/json")
            est, cuerpo = pedir(base + "convertir/lote", datos)
            comparar("POST lote " + json.dumps(c)[:60], r.status_code, r.get_data(), est, cuerpo)
        r = py.post("/api/v1/convertir/lote", data=b"hola", content_type="text/plain")
        est, cuerpo = pedir(base + "convertir/lote", b"hola", "text/plain")
        comparar("POST lote text/plain", r.status_code, r.get_data(), est, cuerpo)

        # micrositio servido por PHP
        for pagina in ("", "api/", "api/docs/", "marca.css", "app.js"):
            est, _ = pedir(f"http://127.0.0.1:{a.puerto}/{pagina}")
            if est != 200:
                fallas += 1
                print(f"✗ /{pagina}: {est}")
        est, _ = pedir(f"http://127.0.0.1:{a.puerto}/api/v1/cie.db")
        print(("✓" if est != 200 else "✗") + f" la base no se descarga por la API ({est})")
        fallas += est == 200
        print(f"\n{len(GET) + len(POST) + 1} comparaciones, {fallas} diferencias")
        return 1 if fallas else 0
    finally:
        if proc:
            proc.terminate()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
