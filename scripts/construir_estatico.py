#!/usr/bin/python3
"""Arma la versión para hosting compartido (Apache + PHP, sin Python).

    python3 scripts/construir_estatico.py [--destino dist] [--marca marca.json] [--url-publica URL]

Deja en DESTINO un sitio que se sube tal cual a cualquier carpeta del hosting:

    index.html, app.js, estilos.css, marca.css...   el micrositio, con la marca aplicada
    api/index.html, api/docs/index.html             portada y documentación de la API
    api/v1/.htaccess                                rutas de la API (php/htaccess)
    api/v1/index.php                                la API (php/index.php)
    api/v1/cie.db                                   la base (storage/cie.db; sólo lectura)
    api/v1/{textos,meta,openapi}.json               lo que la API PHP no recalcula
    .htaccess, router.php                           Apache / `php -S` para probar
    sitemap.xml                                     si la marca tiene url_publica e indexar

meta.json y openapi.json salen de la app Python (mismo texto en las dos
versiones); textos.json, de app/datos.py. Necesita storage/cie.db (scripts/etl.py).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app import datos, marca as marca_mod  # noqa: E402

RAIZ_HTACCESS = r"""# Generado por scripts/construir_estatico.py. Raíz del conversor en Apache.
Options -Indexes -MultiViews
DirectoryIndex index.html
DirectorySlash Off
<IfModule mod_rewrite.c>
  RewriteEngine On
  # /api y /api/docs sin barra → con barra (las páginas usan rutas relativas)
  RewriteCond %{THE_REQUEST} \s(/[^?\s]*/api(/docs)?)[\s?]
  RewriteRule ^api(/docs)?$ %1/ [R=301,L,NE]
</IfModule>
<IfModule mod_deflate.c>
  AddOutputFilterByType DEFLATE text/html text/css application/javascript application/json image/svg+xml
</IfModule>
"""


def construir(destino: Path, ruta_marca: str | None, url_publica: str | None) -> None:
    m = marca_mod.cargar(ruta_marca)
    if url_publica is not None:
        m["url_publica"] = url_publica.rstrip("/")
    if not datos.DB.exists():
        sys.exit(f"falta {datos.DB}: correr antes scripts/etl.py")
    if destino.exists():
        shutil.rmtree(destino)
    (destino / "api" / "docs").mkdir(parents=True)
    (destino / "api" / "v1").mkdir()
    micro = RAIZ / "micrositio"

    for f in micro.iterdir():
        if f.suffix != ".html" and f.is_file():
            shutil.copy2(f, destino / f.name)
    paginas = {"index.html": ("index.html", ""), "api.html": ("api/index.html", "api/"),
               "docs.html": ("api/docs/index.html", "api/docs/")}
    for origen, (salida, ruta_publica) in paginas.items():
        texto = (micro / origen).read_text(encoding="utf-8")
        (destino / salida).write_text(marca_mod.pagina(texto, m, ruta_publica), encoding="utf-8")
    (destino / "marca.css").write_text(marca_mod.css(m), encoding="utf-8")

    v1 = destino / "api" / "v1"
    shutil.copy2(RAIZ / "php" / "index.php", v1 / "index.php")
    shutil.copy2(RAIZ / "php" / "htaccess", v1 / ".htaccess")
    shutil.copy2(RAIZ / "php" / "router.php", destino / "router.php")
    (destino / ".htaccess").write_text(RAIZ_HTACCESS, encoding="utf-8")
    shutil.copy2(datos.DB, v1 / "cie.db")

    meta = datos.meta()
    textos = {
        "version_datos": meta.get("version_datos", "0"),
        "version_cie11": meta.get("cie11_version") or "",
        "navegador_oms": datos.NAVEGADOR_OMS,
        "rotulos": datos.ROTULOS, "explicacion": datos.EXPLICACION, "aviso": datos.AVISO,
    }
    (v1 / "textos.json").write_text(json.dumps(textos, ensure_ascii=False, indent=1), encoding="utf-8")

    # meta y openapi, tal como los da la app Python, con la marca y la URL pública
    from app.api import crear_app
    with tempfile.TemporaryDirectory() as tmp:
        import os
        anterior = os.environ.get("CIE_MARCA")
        if ruta_marca:
            os.environ["CIE_MARCA"] = str(Path(ruta_marca).resolve())
        try:
            app = crear_app(config={**_config_abierta()}, contadores=Path(tmp) / "c.json")
            cliente = app.test_client()
            r = cliente.get("/api/v1/meta")
            assert r.status_code == 200, r.status_code
            (v1 / "meta.json").write_bytes(_sin_gzip(r))
        finally:
            if anterior is None:
                os.environ.pop("CIE_MARCA", None)
            else:
                os.environ["CIE_MARCA"] = anterior
    base = marca_mod.base_api(m)
    spec = (RAIZ / "app" / "openapi.json").read_text(encoding="utf-8")
    spec = spec.replace("{{BASE}}", base)
    spec = json.loads(spec)
    spec["servers"] = [{"url": base if m["url_publica"] else ".", "description": "Este servidor"}]
    (v1 / "openapi.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # sitemap.xml, sólo si va a ser indexable y tiene URL pública
    if m["url_publica"] and m["indexar"]:
        from datetime import date
        urls = "".join(f"<url><loc>{m['url_publica']}/{r}</loc><lastmod>{date.today().isoformat()}</lastmod></url>"
                       for r in ("", "api/", "api/docs/"))
        (destino / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n', encoding="utf-8")

    pesos = sum(f.stat().st_size for f in destino.rglob("*") if f.is_file())
    print(f"listo: {destino} ({len(list(destino.rglob('*')))} archivos, {pesos / 1e6:.1f} MB)")


def _config_abierta() -> dict:
    from app.api import CONFIG_DEFECTO
    return dict(CONFIG_DEFECTO)


def _sin_gzip(r) -> bytes:
    cuerpo = r.get_data()
    if r.headers.get("Content-Encoding") == "gzip":
        import gzip
        cuerpo = gzip.decompress(cuerpo)
    return cuerpo


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--destino", default=str(RAIZ / "dist"))
    ap.add_argument("--marca", default=None, help="JSON de marca (por defecto marca.json o $CIE_MARCA)")
    ap.add_argument("--url-publica", default=None, help="URL donde va a quedar publicado (pisa la de la marca)")
    a = ap.parse_args()
    construir(Path(a.destino).resolve(), a.marca, a.url_publica)
