"""API REST /api/v1 y micrositio del conversor CIE-10 ↔ CIE-11 (Flask).

Seguridad pensada para sistemas de salud: sólo datos de clasificación. No se
registra el contenido de las consultas (sólo contadores agregados por ruta y
día), el lote acepta únicamente códigos, hay límite de tasa por IP, tope de
lote, consultas parametrizadas, cabeceras de seguridad y claves de API
opcionales (apagadas por defecto, ver storage/config.json y scripts/claves.py).
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from datetime import date
from pathlib import Path

from flask import Flask, Response, abort, g, jsonify, redirect, request, send_from_directory

from . import datos, marca as marca_mod

RAIZ = Path(__file__).resolve().parent.parent
MICROSITIO = RAIZ / "micrositio"
CONFIG = RAIZ / "storage" / "config.json"
CONTADORES = RAIZ / "storage" / "contadores.json"
OPENAPI = Path(__file__).resolve().parent / "openapi.json"

CONFIG_DEFECTO = {
    "cors_origenes": ["*"],          # lista de orígenes permitidos; "*" = cualquiera
    "limite_por_minuto": 240,        # pedidos por IP y por minuto (sin clave)
    "lote_maximo": 100,              # códigos por pedido de lote
    "claves_obligatorias": False,    # True = sin X-API-Key válida → 401
    "claves": {},                    # {sha256(clave): {"nombre", "plan", "por_minuto", "por_dia", "activa"}}
    "proxies_confiables": ["127.0.0.1", "::1"],   # Caddy delante: se cree X-Forwarded-For sólo de estos
}


def cargar_config() -> dict:
    cfg = dict(CONFIG_DEFECTO)
    if CONFIG.exists():
        try:
            cfg.update(json.loads(CONFIG.read_text()))
        except ValueError:
            pass
    return cfg


class Error(Exception):
    def __init__(self, estado: int, codigo: str, mensaje: str, detalle=None):
        super().__init__(mensaje)
        self.estado, self.codigo, self.mensaje, self.detalle = estado, codigo, mensaje, detalle


# ---------------------------------------------------------------- límites

class Limitador:
    """Ventana deslizante de 60 s por llave (IP o clave). Sólo en memoria."""

    def __init__(self):
        self._eventos: dict[str, deque] = defaultdict(deque)
        self._dia: dict[str, tuple[str, int]] = {}
        self._lock = threading.Lock()

    def permitir(self, llave: str, por_minuto: int, por_dia: int | None = None, costo: int = 1):
        ahora = time.monotonic()
        with self._lock:
            q = self._eventos[llave]
            while q and ahora - q[0] > 60:
                q.popleft()
            if len(q) + costo > por_minuto:
                espera = int(60 - (ahora - q[0])) + 1 if q else 60
                return False, max(espera, 1), max(por_minuto - len(q), 0)
            if por_dia:
                hoy = date.today().isoformat()
                d, n = self._dia.get(llave, (hoy, 0))
                if d != hoy:
                    n = 0
                if n + costo > por_dia:
                    return False, 3600, 0
                self._dia[llave] = (hoy, n + costo)
            for _ in range(costo):
                q.append(ahora)
            if len(self._eventos) > 50000:      # no crecer sin techo
                for k in [k for k, v in self._eventos.items() if not v][:10000]:
                    del self._eventos[k]
            return True, 0, por_minuto - len(q)


class Contadores:
    """Pedidos por ruta (plantilla, sin parámetros), estado y día. Nada más."""

    def __init__(self, ruta: Path):
        self.ruta = ruta
        self._lock = threading.Lock()
        self._pend: dict[str, int] = defaultdict(int)
        self._ultimo = time.monotonic()

    def sumar(self, plantilla: str, estado: int):
        with self._lock:
            self._pend[f"{date.today().isoformat()}|{plantilla}|{estado // 100}xx"] += 1
            if time.monotonic() - self._ultimo > 60:
                self._volcar()

    def _volcar(self):
        try:
            datos_ = json.loads(self.ruta.read_text()) if self.ruta.exists() else {}
        except ValueError:
            datos_ = {}
        for k, n in self._pend.items():
            datos_[k] = datos_.get(k, 0) + n
        # se guardan 90 días
        corte = date.fromordinal(date.today().toordinal() - 90).isoformat()
        datos_ = {k: v for k, v in datos_.items() if k[:10] >= corte}
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(datos_, indent=0, sort_keys=True))
        tmp.replace(self.ruta)
        self._pend.clear()
        self._ultimo = time.monotonic()

    def volcar(self):
        with self._lock:
            self._volcar()


# ---------------------------------------------------------------- app

def crear_app(config: dict | None = None, contadores: Path | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.json.ensure_ascii = False
    app.json.sort_keys = False
    cfg = config or cargar_config()
    marca = marca_mod.cargar()
    app.config["CIE"] = cfg
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    limitador = Limitador()
    cuenta = Contadores(contadores or CONTADORES)
    app.extensions["cie_contadores"] = cuenta
    version = datos.meta().get("version_datos", "0")

    # ------------------------------------------------ antes / después

    def ip_cliente() -> str:
        ip = request.remote_addr or "?"
        if ip in cfg["proxies_confiables"]:
            reenviada = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            if reenviada:
                ip = reenviada
        return ip

    @app.before_request
    def antes():
        g.t0 = time.monotonic()
        if not request.path.startswith("/api/v1/") or request.method == "OPTIONS":
            return None
        clave = request.headers.get("X-API-Key", "")
        plan = None
        if clave:
            h = hashlib.sha256(clave.encode()).hexdigest()
            for hh, info in cfg["claves"].items():
                if hmac.compare_digest(hh, h) and info.get("activa", True):
                    plan = info
                    break
            if plan is None:
                raise Error(401, "clave_invalida", "La clave de API no es válida o está desactivada.")
        elif cfg["claves_obligatorias"] and request.path != "/api/v1/salud":
            raise Error(401, "clave_requerida", "Este servicio requiere la cabecera X-API-Key.")
        costo = 1
        if request.path == "/api/v1/convertir/lote" and request.method == "POST":
            costo = 5
        if plan:
            llave = "k:" + hashlib.sha256(clave.encode()).hexdigest()[:16]
            ok, espera, resto = limitador.permitir(llave, plan.get("por_minuto", cfg["limite_por_minuto"]),
                                                   plan.get("por_dia"), costo)
            g.limite = plan.get("por_minuto", cfg["limite_por_minuto"])
        else:
            ok, espera, resto = limitador.permitir("ip:" + ip_cliente(), cfg["limite_por_minuto"], None, costo)
            g.limite = cfg["limite_por_minuto"]
        g.resto = resto
        if not ok:
            e = Error(429, "demasiados_pedidos",
                      f"Se superó el límite de pedidos. Reintentar en {espera} s.")
            e.reintentar = espera
            raise e
        return None

    def cors(resp: Response) -> Response:
        origen = request.headers.get("Origin")
        permitidos = cfg["cors_origenes"]
        if "*" in permitidos:
            resp.headers["Access-Control-Allow-Origin"] = "*"
        elif origen and origen in permitidos:
            resp.headers["Access-Control-Allow-Origin"] = origen
            resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key, If-None-Match"
        resp.headers["Access-Control-Expose-Headers"] = "ETag, X-RateLimit-Limit, X-RateLimit-Remaining, Retry-After"
        resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    @app.after_request
    def despues(resp: Response):
        h = resp.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["Referrer-Policy"] = "no-referrer"
        h["X-Frame-Options"] = "DENY"
        h["Cross-Origin-Resource-Policy"] = "cross-origin"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        h["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
            h["Strict-Transport-Security"] = "max-age=31536000"
        if request.path.startswith("/api/"):
            cors(resp)
            if hasattr(g, "limite"):
                h["X-RateLimit-Limit"] = str(g.limite)
                h["X-RateLimit-Remaining"] = str(max(g.resto, 0))
            h["X-Datos-Version"] = version
            # ETag y caché: los datos son fijos por versión
            if request.method == "GET" and resp.status_code == 200 and not resp.direct_passthrough:
                cuerpo = resp.get_data()
                etag = '"' + hashlib.sha256(version.encode() + cuerpo).hexdigest()[:32] + '"'
                h["ETag"] = etag
                h["Cache-Control"] = ("no-store" if request.path == "/api/v1/salud"
                                      else "public, max-age=86400")
                pedidos = [x.strip() for x in request.headers.get("If-None-Match", "").split(",")]
                if etag in pedidos or f"W/{etag}" in pedidos:
                    resp.status_code = 304
                    resp.set_data(b"")
                    resp.headers.pop("Content-Length", None)
                    h.pop("Content-Type", None)
            elif resp.status_code >= 400 or request.method == "POST":
                h["Cache-Control"] = "no-store"
        elif resp.status_code == 200 and request.path.startswith(("/static/", "/docs-static/")):
            h["Cache-Control"] = "public, max-age=3600"
        # compresión gzip
        if (resp.status_code == 200 and not resp.direct_passthrough
                and "gzip" in request.headers.get("Accept-Encoding", "")
                and "Content-Encoding" not in h
                and (resp.mimetype or "").split("/")[0] in ("text", "application")
                and resp.mimetype not in ("application/octet-stream",)):
            cuerpo = resp.get_data()
            if len(cuerpo) > 800:
                resp.set_data(gzip.compress(cuerpo, 6))
                h["Content-Encoding"] = "gzip"
                h["Vary"] = ", ".join(filter(None, [h.get("Vary"), "Accept-Encoding"]))
        regla = request.url_rule.rule if request.url_rule else "(sin ruta)"
        if regla.startswith("/api/"):
            cuenta.sumar(f"{request.method} {regla}", resp.status_code)
        return resp

    # ------------------------------------------------ errores

    def error_json(estado, codigo, mensaje, detalle=None, reintentar=None):
        cuerpo = {"error": {"estado": estado, "codigo": codigo, "mensaje": mensaje}}
        if detalle is not None:
            cuerpo["error"]["detalle"] = detalle
        resp = jsonify(cuerpo)
        resp.status_code = estado
        if reintentar:
            resp.headers["Retry-After"] = str(reintentar)
        return resp

    @app.errorhandler(Error)
    def _err(e: Error):
        return error_json(e.estado, e.codigo, e.mensaje, e.detalle, getattr(e, "reintentar", None))

    def html_marca(archivo: str, ruta_publica: str = "") -> Response:
        texto = (MICROSITIO / archivo).read_text(encoding="utf-8")
        return Response(marca_mod.pagina(texto, marca, ruta_publica), mimetype="text/html")

    @app.errorhandler(404)
    def _404(e):
        if request.path.startswith("/api/"):
            return error_json(404, "ruta_inexistente", "No existe esa ruta de la API. Ver /api/v1/openapi.json.")
        return html_marca("index.html"), 404

    @app.errorhandler(405)
    def _405(e):
        return error_json(405, "metodo_no_permitido", f"Método {request.method} no permitido en esta ruta.")

    @app.errorhandler(413)
    def _413(e):
        return error_json(413, "pedido_demasiado_grande", "El cuerpo del pedido supera los 64 KB.")

    @app.errorhandler(500)
    def _500(e):
        return error_json(500, "error_interno", "Error interno. Reintentar más tarde.")

    # ------------------------------------------------ utilidades

    def entero(nombre, defecto, minimo, maximo):
        v = request.args.get(nombre)
        if v is None or v == "":
            return defecto
        try:
            n = int(v)
        except ValueError:
            raise Error(400, "parametro_invalido", f"«{nombre}» tiene que ser un número entero.")
        if not minimo <= n <= maximo:
            raise Error(400, "parametro_invalido", f"«{nombre}» tiene que estar entre {minimo} y {maximo}.")
        return n

    def booleano(nombre):
        return request.args.get(nombre, "").lower() in ("1", "true", "si", "sí", "yes")

    def validar_codigo(c: str, sistema: str) -> str:
        c = (c or "").strip()
        if not c or len(c) > 40 or not re.fullmatch(r"[A-Za-z0-9.\-/&–]+", c):
            raise Error(400, "codigo_invalido", f"«{c[:40]}» no tiene forma de código {sistema}.")
        return c

    # ------------------------------------------------ rutas: servicio

    @app.route("/api/v1/salud")
    def salud():
        try:
            n = datos.con().execute("SELECT count(*) FROM cie10").fetchone()[0]
            m = datos.con().execute("SELECT count(*) FROM cie11").fetchone()[0]
        except Exception:
            return error_json(503, "base_no_disponible", "La base de datos no responde.")
        return jsonify({"estado": "ok", "version_datos": version, "cie10": n, "cie11": m})

    @app.route("/api/v1/meta")
    def meta():
        m = datos.meta()
        conteos = json.loads(m.get("validacion", "{}"))
        return jsonify({
            "servicio": marca["nombre"],
            "version_api": "1.0.0",
            "version_datos": version,
            "cie11": {
                "version": m.get("cie11_version"), "archivo": m.get("cie11_version_archivo"),
                "idioma": "es", "linearizacion": "MMS (estadísticas de mortalidad y morbilidad)",
                "fuente": "OMS, ICD-11 MMS release, tabulación simple en español",
                "url": "https://icd.who.int/browse/2026-01/mms/es",
                "licencia": "CC BY-ND 3.0 IGO",
                "atribucion": "Clasificación Internacional de Enfermedades, Undécima Revisión (CIE-11), "
                              "Organización Mundial de la Salud (OMS) 2019 https://icd.who.int/browse11. "
                              "Bajo licencia Creative Commons Atribución-SinDerivadas 3.0 IGO "
                              "(CC BY-ND 3.0 IGO).",
            },
            "tablas_de_correspondencia": {
                "version": m.get("tablas_version"), "fuente": "OMS, mapping.zip de la versión 2026-01",
                "url": "https://icdcdn.who.int/static/releasefiles/2026-01/mapping.zip",
                "licencia": "Términos de uso de las Clasificaciones de la OMS (CC BY-ND 3.0 IGO)",
            },
            "cie10": {
                "fuente": m.get("cie10_fuente"),
                "url": "http://www.dgis.salud.gob.mx/contenidos/basesdedatos/da_egresoshosp_gobmx.html",
                "grupos": m.get("cie10_grupos_fuente"),
                "idioma": "es",
                "edicion": "CIE-10 de la OMS con actualizaciones hasta 2021 (traducción OPS/CEMECE)",
                "atribucion": "Clasificación Estadística Internacional de Enfermedades y Problemas "
                              "Relacionados con la Salud, Décima Revisión (CIE-10). © OMS; edición en "
                              "español de la OPS. Catálogo: Secretaría de Salud de México, DGIS (datos abiertos).",
                "nota": "Los títulos se muestran tal como los publica la fuente (en mayúsculas).",
            },
            "conteos": conteos,
            "armado": m.get("armado"),
            "aviso": datos.AVISO,
            "datos_personales": "Este servicio no recibe ni guarda datos de pacientes. "
                                "No envíe información personal: sólo códigos o texto de búsqueda.",
        })

    @app.route("/api/v1/openapi.json")
    def openapi():
        base = marca_mod.base_api(marca, request.host_url) + "/api/v1"
        texto = OPENAPI.read_text(encoding="utf-8").replace("{{BASE}}/api/v1", base).replace(
            "{{BASE}}", base[:-len("/api/v1")])
        return Response(texto, mimetype="application/json")

    # ------------------------------------------------ clasificaciones

    def params_busqueda():
        q = request.args.get("q", "").strip()
        if not q:
            raise Error(400, "falta_q", "Falta el parámetro «q» (código o texto a buscar).")
        if len(q) > 100:
            raise Error(400, "q_demasiado_largo", "«q» admite hasta 100 caracteres.")
        tipo = request.args.get("tipo") or None
        return dict(q=q, capitulo=request.args.get("capitulo") or None, tipo=tipo,
                    solo_validos=booleano("solo_validos"),
                    pagina=entero("pagina", 1, 1, 500), por_pagina=entero("por_pagina", 20, 1, 50))

    @app.route("/api/v1/cie10/buscar")
    def buscar10():
        p = params_busqueda()
        if p["tipo"] and p["tipo"] not in ("capitulo", "grupo", "categoria", "subcategoria"):
            raise Error(400, "parametro_invalido", "«tipo» admite capitulo, grupo, categoria o subcategoria.")
        return jsonify({"sistema": "CIE-10", "q": p["q"], **datos.buscar("cie10", **p)})

    @app.route("/api/v1/cie11/buscar")
    def buscar11():
        p = params_busqueda()
        if p["tipo"] and p["tipo"] not in ("capitulo", "bloque", "categoria"):
            raise Error(400, "parametro_invalido", "«tipo» admite capitulo, bloque o categoria.")
        return jsonify({"sistema": "CIE-11", "q": p["q"], **datos.buscar("cie11", **p)})

    @app.route("/api/v1/cie10/capitulos")
    def caps10():
        return jsonify({"sistema": "CIE-10", "capitulos": datos.capitulos10()})

    @app.route("/api/v1/cie11/capitulos")
    def caps11():
        return jsonify({"sistema": "CIE-11", "capitulos": datos.capitulos11()})

    def fila10(codigo):
        f = datos.buscar_codigo10(validar_codigo(codigo, "CIE-10"))
        if f is None:
            raise Error(404, "codigo_inexistente", f"El código CIE-10 «{codigo}» no existe en la fuente.")
        return f

    def fila11(codigo):
        f = datos.buscar_codigo11(validar_codigo(codigo, "CIE-11"))
        if f is None:
            raise Error(404, "codigo_inexistente", f"El código CIE-11 «{codigo}» no existe en la versión 2026-01.")
        return f

    @app.route("/api/v1/cie10/<codigo>")
    def ficha10(codigo):
        f = fila10(codigo)
        d = datos.ficha10(f)
        d["hijos"] = datos.hijos10(f["codigo"])
        d["equivalencias"] = f"/api/v1/convertir/cie10-a-cie11/{f['codigo']}"
        return jsonify(d)

    @app.route("/api/v1/cie10/<codigo>/hijos")
    def hijos10(codigo):
        f = fila10(codigo)
        return jsonify({"codigo": f["codigo"], "hijos": datos.hijos10(f["codigo"])})

    @app.route("/api/v1/cie10/<codigo>/ancestros")
    def ancestros10(codigo):
        f = fila10(codigo)
        return jsonify({"codigo": f["codigo"], "ancestros": datos.ancestros10(f["codigo"])})

    @app.route("/api/v1/cie11/<path:codigo>/hijos")
    def hijos11(codigo):
        f = fila11(codigo)
        return jsonify({"codigo": f["codigo"], "id": f["id"], "hijos": datos.hijos11(f["id"])})

    @app.route("/api/v1/cie11/<path:codigo>/ancestros")
    def ancestros11(codigo):
        f = fila11(codigo)
        return jsonify({"codigo": f["codigo"], "id": f["id"], "ancestros": datos.ancestros11(f["id"])})

    @app.route("/api/v1/cie11/<path:codigo>")
    def ficha11(codigo):
        f = fila11(codigo)
        d = datos.ficha11(f)
        d["hijos"] = datos.hijos11(f["id"])
        d["equivalencias"] = f"/api/v1/convertir/cie11-a-cie10/{f['codigo'] or f['id']}"
        return jsonify(d)

    # ------------------------------------------------ conversión

    SENTIDOS = {"cie10-a-cie11": "CIE-10", "cie11-a-cie10": "CIE-11"}

    @app.route("/api/v1/convertir/<sentido>/<path:codigo>")
    def convertir(sentido, codigo):
        if sentido not in SENTIDOS:
            raise Error(404, "sentido_invalido", "El sentido es cie10-a-cie11 o cie11-a-cie10.")
        validar_codigo(codigo, SENTIDOS[sentido])
        r = datos.convertir(sentido, codigo, con_sugerencias=not booleano("sin_sugerencias"))
        if r is None:
            raise Error(404, "codigo_inexistente", f"El código {SENTIDOS[sentido]} «{codigo}» no existe en la fuente.")
        return jsonify(r)

    @app.route("/api/v1/convertir/lote", methods=["POST"])
    def lote():
        if not request.is_json:
            raise Error(415, "se_espera_json", "El cuerpo tiene que ser JSON (Content-Type: application/json).")
        cuerpo = request.get_json(silent=True)
        if not isinstance(cuerpo, dict):
            raise Error(400, "json_invalido", "El cuerpo no es un objeto JSON válido.")
        extra = set(cuerpo) - {"sentido", "codigos", "sin_sugerencias"}
        if extra:
            raise Error(400, "campos_no_admitidos",
                        "Sólo se aceptan «sentido», «codigos» y «sin_sugerencias». "
                        "No envíe datos de pacientes.", sorted(extra)[:10])
        sentido = cuerpo.get("sentido")
        if sentido not in SENTIDOS:
            raise Error(400, "sentido_invalido", "«sentido» tiene que ser cie10-a-cie11 o cie11-a-cie10.")
        codigos = cuerpo.get("codigos")
        if not isinstance(codigos, list) or not codigos:
            raise Error(400, "codigos_invalidos", "«codigos» tiene que ser una lista no vacía de códigos.")
        if len(codigos) > cfg["lote_maximo"]:
            raise Error(413, "lote_demasiado_grande", f"Hasta {cfg['lote_maximo']} códigos por pedido.")
        resultados = []
        for c in codigos:
            if not isinstance(c, str) or not re.fullmatch(r"[A-Za-z0-9.\-/&]{1,40}", c.strip()):
                resultados.append({"codigo": c if isinstance(c, str) and len(c) <= 40 else None,
                                   "error": {"codigo": "codigo_invalido",
                                             "mensaje": "No tiene forma de código (no se aceptan textos libres)."}})
                continue
            r = datos.convertir(sentido, c.strip(), con_sugerencias=not cuerpo.get("sin_sugerencias", True))
            if r is None:
                resultados.append({"codigo": c, "error": {"codigo": "codigo_inexistente",
                                                          "mensaje": "No existe en la fuente."}})
            else:
                resultados.append({"codigo": c, **r})
        return jsonify({"sentido": sentido, "total": len(resultados),
                        "con_error": sum(1 for r in resultados if "error" in r),
                        "resultados": resultados, "aviso": datos.AVISO})

    # ------------------------------------------------ micrositio y documentación

    # Las páginas de la API van CON barra final (/api/, /api/docs/), igual que en la
    # versión PHP: en Apache una carpeta reescrita sin barra no se puede servir (403).
    # Las rutas relativas de las páginas cuentan con esa barra; sin ella, 301.
    @app.route("/api/docs")
    def docs_sin_barra():
        return redirect("docs/", 301)

    @app.route("/api/docs/")
    def docs():
        return html_marca("docs.html", "api/docs/")

    @app.route("/api")
    def api_sin_barra():
        return redirect("api/", 301)

    @app.route("/api/")
    def api_raiz():
        return html_marca("api.html", "api/")

    @app.route("/")
    def inicio():
        return html_marca("index.html")

    @app.route("/marca.css")
    def marca_css():
        return Response(marca_mod.css(marca), mimetype="text/css")

    @app.route("/<path:archivo>")
    def estatico(archivo):
        if archivo.startswith("api/"):
            abort(404)
        if archivo.endswith(".html"):
            if not (MICROSITIO / archivo).is_file() or "/" in archivo:
                abort(404)
            return html_marca(archivo)
        return send_from_directory(MICROSITIO, archivo)

    return app
