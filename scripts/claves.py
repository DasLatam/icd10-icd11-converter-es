#!/usr/bin/python3
"""Claves de API (X-API-Key). Apagadas por defecto: la API es abierta.

    claves.py nueva "Hospital X" [--plan basico] [--por-minuto 600] [--por-dia 50000]
    claves.py listar
    claves.py desactivar <prefijo-del-hash>
    claves.py obligatorias si|no        # exigir clave en todo /api/v1 salvo /salud

La clave se muestra UNA vez; en storage/config.json (permisos 600) sólo queda su
SHA-256. Después de cambiar algo, reiniciar el servicio:
    reiniciar serve.py para que tome los cambios
"""
import argparse
import hashlib
import json
import os
import secrets
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from app.api import CONFIG, CONFIG_DEFECTO  # noqa: E402


def leer():
    cfg = json.loads(json.dumps(CONFIG_DEFECTO))
    if CONFIG.exists():
        cfg.update(json.loads(CONFIG.read_text()))
    return cfg


def guardar(cfg):
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG)


ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
sub = ap.add_subparsers(dest="cmd", required=True)
n = sub.add_parser("nueva")
n.add_argument("nombre")
n.add_argument("--plan", default="basico")
n.add_argument("--por-minuto", type=int, default=600)
n.add_argument("--por-dia", type=int, default=50000)
sub.add_parser("listar")
d = sub.add_parser("desactivar")
d.add_argument("prefijo")
o = sub.add_parser("obligatorias")
o.add_argument("valor", choices=["si", "no"])
a = ap.parse_args()

cfg = leer()
if a.cmd == "nueva":
    clave = "cie_" + secrets.token_urlsafe(32)
    h = hashlib.sha256(clave.encode()).hexdigest()
    cfg["claves"][h] = {"nombre": a.nombre, "plan": a.plan, "por_minuto": a.por_minuto,
                        "por_dia": a.por_dia, "activa": True, "alta": date.today().isoformat()}
    guardar(cfg)
    print(f"Clave para {a.nombre} (se muestra sólo ahora):\n{clave}\nhash {h[:12]}…")
elif a.cmd == "listar":
    print(f"claves obligatorias: {'sí' if cfg['claves_obligatorias'] else 'no'}")
    for h, i in cfg["claves"].items():
        print(f"{h[:12]}  {'activa ' if i.get('activa', True) else 'inactiva'}  {i['nombre']}  "
              f"plan={i.get('plan')} {i.get('por_minuto')}/min {i.get('por_dia')}/día  alta {i.get('alta')}")
elif a.cmd == "desactivar":
    hs = [h for h in cfg["claves"] if h.startswith(a.prefijo)]
    if len(hs) != 1:
        sys.exit(f"el prefijo coincide con {len(hs)} claves")
    cfg["claves"][hs[0]]["activa"] = False
    guardar(cfg)
    print(f"desactivada {hs[0][:12]}")
elif a.cmd == "obligatorias":
    cfg["claves_obligatorias"] = a.valor == "si"
    guardar(cfg)
    print(f"claves obligatorias: {a.valor}")
