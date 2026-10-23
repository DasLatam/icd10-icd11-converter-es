"""Marca blanca: nombre, lema, colores y pie del micrositio, desde un JSON.

El código no trae ninguna marca. Lo que se ve (nombre, lema, sigla del logo,
colores, línea extra del pie, URL pública e indexación) sale de `marca.json`
en la raíz del proyecto, o del archivo que diga la variable `CIE_MARCA`.
Cualquier clave que falte toma el valor por defecto de abajo.

Las páginas del micrositio llevan marcadores `{{clave}}` que se reemplazan al
servirlas (Flask) o al construir la versión estática (scripts/construir_estatico.py).
Los colores van a `marca.css`, un archivo aparte: la CSP no admite estilos en línea.
"""
from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

DEFECTO = {
    "nombre": "Conversor CIE-10 ↔ CIE-11",
    "lema": "en español · tablas oficiales de la OMS",
    "logo": "CIE",
    "descripcion": "Conversor visual entre CIE-10 y CIE-11 en español, con las tablas "
                   "oficiales de correspondencia de la OMS, y una API REST para sistemas de salud.",
    "url_publica": "",          # p. ej. https://ejemplo.org/cie  (canonical y ejemplos de la API)
    "indexar": False,           # False = <meta name="robots" content="noindex">
    "pie_html": "",             # HTML extra al final del pie (créditos, enlaces propios)
    "colores": {
        "acento": "#0b6e8a",
        "acento_claro": "#e3f2f7",
        "fondo": "#f5f8fa",
        "tinta": "#14232e",
        "foco": "#f0a202",
    },
}


def cargar(ruta: str | Path | None = None) -> dict:
    ruta = Path(ruta or os.environ.get("CIE_MARCA") or RAIZ / "marca.json")
    m = json.loads(json.dumps(DEFECTO))
    if ruta.exists():
        propia = json.loads(ruta.read_text(encoding="utf-8"))
        colores = {**m["colores"], **(propia.pop("colores", None) or {})}
        m.update({k: v for k, v in propia.items() if not k.startswith("_")})
        m["colores"] = colores
    m["url_publica"] = (m.get("url_publica") or "").rstrip("/")
    return m


def css(m: dict) -> str:
    c = m["colores"]
    color = re.compile(r"^#[0-9a-fA-F]{3,8}$|^[a-z]+$|^rgba?\([0-9., %]+\)$")
    variables = {
        "--acento": c.get("acento"), "--acento-claro": c.get("acento_claro"),
        "--fondo": c.get("fondo"), "--tinta": c.get("tinta"), "--foco": c.get("foco"),
    }
    lineas = [f"  {k}:{v};" for k, v in variables.items() if v and color.match(str(v))]
    return "/* Generado desde marca.json: no editar a mano. */\n:root{\n" + "\n".join(lineas) + "\n}\n"


def pagina(texto: str, m: dict, ruta_publica: str = "") -> str:
    """Reemplaza los {{marcadores}} de una página del micrositio."""
    canonical = ""
    if m["url_publica"]:
        canonical = f'<link rel="canonical" href="{html.escape(m["url_publica"] + "/" + ruta_publica)}">'
    valores = {
        "nombre": html.escape(m["nombre"]),
        "lema": html.escape(m["lema"]),
        "logo": html.escape(m["logo"]),
        "descripcion": html.escape(m["descripcion"]),
        "robots": "" if m["indexar"] else '<meta name="robots" content="noindex,nofollow">',
        "canonical": canonical,
        "pie_extra": f"<p>{m['pie_html']}</p>" if m.get("pie_html") else "",
    }
    return re.sub(r"\{\{(\w+)\}\}", lambda x: valores.get(x.group(1), x.group(0)), texto)


def base_api(m: dict, respaldo: str = "https://tu-servidor") -> str:
    return (m["url_publica"] or respaldo).rstrip("/")
