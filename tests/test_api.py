"""Pruebas de la API y de las conversiones (unittest de la biblioteca estándar).

    python3 -m unittest -v tests.test_api      (desde la raíz del proyecto)

Usan la base real (storage/cie.db, sólo lectura) y el cliente de pruebas de
Flask; los contadores van a /tmp.
"""
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app import api, datos  # noqa: E402


def cliente(**cfg):
    c = dict(api.CONFIG_DEFECTO)
    c.update(cfg)
    tmp = Path(tempfile.mkdtemp()) / "contadores.json"
    return api.crear_app(config=c, contadores=tmp).test_client()


class Servicio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = cliente()

    def test_salud(self):
        r = self.c.get("/api/v1/salud")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json["estado"], "ok")
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_meta_licencias(self):
        m = self.c.get("/api/v1/meta").json
        self.assertEqual(m["cie11"]["version"], "2026-01")
        self.assertIn("CC BY-ND 3.0 IGO", m["cie11"]["licencia"])
        self.assertIn("conteos", m)
        self.assertGreater(m["conteos"]["cie10_total"], 14000)

    def test_openapi_cubre_las_rutas(self):
        spec = self.c.get("/api/v1/openapi.json").json
        self.assertTrue(spec["openapi"].startswith("3."))
        app = api.crear_app(config=dict(api.CONFIG_DEFECTO), contadores=Path(tempfile.mkdtemp()) / "c.json")
        reglas = {r.rule.replace("<path:", "{").replace("<", "{").replace(">", "}")
                  for r in app.url_map.iter_rules() if r.rule.startswith("/api/v1/")}
        documentadas = {p.replace("{sentido}", "") for p in spec["paths"]}
        for regla in reglas:
            if regla == "/api/v1/convertir/{sentido}/{codigo}":
                self.assertIn("/api/v1/convertir/cie10-a-cie11/{codigo}", spec["paths"])
                continue
            self.assertIn(regla, documentadas, f"{regla} no está en openapi.json")

    def test_docs_y_paginas(self):
        for u in ("/", "/api/", "/api/docs/", "/app.js", "/estilos.css", "/docs.js"):
            self.assertEqual(self.c.get(u).status_code, 200, u)
        self.assertEqual(self.c.get("/api/docs").status_code, 301)
        self.assertEqual(self.c.get("/api").status_code, 301)


class Conversion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = cliente()

    def conv(self, sentido, codigo):
        r = self.c.get(f"/api/v1/convertir/{sentido}/{codigo}")
        self.assertEqual(r.status_code, 200, r.data[:200])
        return r.json

    def test_casos_conocidos_10a11(self):
        casos = {"E11": ("5A11", "equivalente"), "U07.1": ("RA01.0", "equivalente"),
                 "I10": ("BA00.Z", None), "J45.9": ("CA23.32", None), "F32.9": ("6A7Z", None),
                 "A00": ("1A00", None)}
        for cod, (esperado, tipo) in casos.items():
            d = self.conv("cie10-a-cie11", cod)
            primero = d["equivalencias"][0]
            self.assertEqual(primero["codigo"], esperado, cod)
            if tipo:
                self.assertEqual(primero["correspondencia"]["tipo"], tipo, cod)
            self.assertTrue(primero["uri"].startswith("http://id.who.int/icd/release/11/"), "falta la URI")

    def test_casos_conocidos_11a10(self):
        for cod, esperado in {"5A11": "E11", "BA00.Z": "I10", "RA01.0": "U07.1", "CA23.3": "J45.9"}.items():
            d = self.conv("cie11-a-cie10", cod)
            self.assertEqual(d["equivalencias"][0]["codigo"], esperado, cod)

    def test_titulos_en_espanol_sin_tocar(self):
        d = self.conv("cie10-a-cie11", "E11")
        self.assertEqual(d["origen"]["titulo"], "DIABETES MELLITUS TIPO 2")
        self.assertEqual(d["equivalencias"][0]["titulo"], "Diabetes mellitus tipo 2")
        self.assertEqual(d["origen"]["capitulo"]["codigo"], "IV")
        self.assertEqual(d["origen"]["grupo"]["codigo"], "E10-E14")

    def test_formas_del_codigo(self):
        for forma in ("E119", "e11.9", "E11.9", " E11.9 "):
            self.assertEqual(self.conv("cie10-a-cie11", forma.strip())["origen"]["codigo"], "E11.9", forma)
        self.assertEqual(self.conv("cie10-a-cie11", "I10X")["origen"]["codigo"], "I10")
        self.assertEqual(self.conv("cie11-a-cie10", "761947693/unspecified")["origen"]["codigo"], "BA00.Z")

    def test_uno_a_varios_ordenado(self):
        d = self.conv("cie10-a-cie11", "A01.0")
        puntajes = [e["correspondencia"]["puntaje"] for e in d["equivalencias"]]
        self.assertEqual(puntajes, sorted(puntajes, reverse=True))
        self.assertTrue(any(e["correspondencia"]["principal"] for e in d["equivalencias"]))

    def test_postcoordinacion(self):
        d = self.conv("cie10-a-cie11", "A00.0")
        e = d["equivalencias"][0]
        self.assertEqual(e["postcoordinacion"]["cluster"], "1A00&XN8P1")
        self.assertTrue(e["postcoordinacion"]["partes"][1]["extension"])

    def test_heredada_del_padre(self):
        d = self.conv("cie10-a-cie11", "W00.4")
        self.assertIsNotNone(d["heredada_de"])
        self.assertEqual(d["heredada_de"]["codigo"], "W00")
        self.assertTrue(d["equivalencias"])

    def test_sin_equivalente_y_sugerencia_separada(self):
        d = self.conv("cie11-a-cie10", "1B70")
        self.assertTrue(d["sin_equivalente"])
        self.assertEqual(d["equivalencias"], [])
        s = d.get("sugerencias_automaticas")
        self.assertIsNotNone(s)
        self.assertIn("NO OFICIAL", s["aviso"])
        for x in s["resultados"]:
            self.assertNotIn("correspondencia", x)
        self.assertNotIn("sugerencias_automaticas",
                         self.c.get("/api/v1/convertir/cie11-a-cie10/1B70?sin_sugerencias=1").json)

    def test_inexistente(self):
        r = self.c.get("/api/v1/convertir/cie10-a-cie11/Z99.99")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json["error"]["codigo"], "codigo_inexistente")
        r = self.c.get("/api/v1/convertir/otra-cosa/E11")
        self.assertEqual(r.status_code, 404)

    def test_lote(self):
        r = self.c.post("/api/v1/convertir/lote", json={"sentido": "cie10-a-cie11",
                                                        "codigos": ["E11.9", "I10", "XXX", "hola mundo"]})
        self.assertEqual(r.status_code, 200)
        d = r.json
        self.assertEqual(d["total"], 4)
        self.assertEqual(d["con_error"], 2)
        self.assertEqual(d["resultados"][0]["equivalencias"][0]["codigo"], "5A11")
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_lote_rechaza_datos_extra_y_topes(self):
        r = self.c.post("/api/v1/convertir/lote", json={"sentido": "cie10-a-cie11", "codigos": ["E11"],
                                                        "paciente": {"dni": "1"}})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json["error"]["codigo"], "campos_no_admitidos")
        r = self.c.post("/api/v1/convertir/lote", json={"sentido": "cie10-a-cie11", "codigos": ["E11"] * 101})
        self.assertEqual(r.status_code, 413)
        r = self.c.post("/api/v1/convertir/lote", data="x", content_type="text/plain")
        self.assertEqual(r.status_code, 415)
        r = self.c.post("/api/v1/convertir/lote", data="x" * 70000, content_type="application/json")
        self.assertEqual(r.status_code, 413)


class Busqueda(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = cliente()

    def test_por_codigo_parcial(self):
        d = self.c.get("/api/v1/cie10/buscar?q=E11").json
        self.assertEqual(d["modo"], "codigo")
        self.assertEqual(d["resultados"][0]["codigo"], "E11")
        d = self.c.get("/api/v1/cie11/buscar?q=5A1").json
        self.assertTrue(all(x["codigo"].startswith("5A1") for x in d["resultados"]))

    def test_texto_sin_tildes_ni_mayusculas(self):
        a = self.c.get("/api/v1/cie10/buscar?q=HIPERTENSIÓN").json
        b = self.c.get("/api/v1/cie10/buscar?q=hipertension").json
        self.assertEqual(a["total"], b["total"])
        self.assertGreater(a["total"], 0)

    def test_errores_de_tipeo(self):
        d = self.c.get("/api/v1/cie10/buscar?q=diabetis").json
        self.assertEqual(d["modo"], "texto_corregido")
        self.assertGreater(d["total"], 0)
        d = self.c.get("/api/v1/cie11/buscar?q=neumonia").json
        self.assertGreater(d["total"], 0)

    def test_paginacion_y_filtros(self):
        p1 = self.c.get("/api/v1/cie11/buscar?q=diabetes&por_pagina=5&pagina=1").json
        p2 = self.c.get("/api/v1/cie11/buscar?q=diabetes&por_pagina=5&pagina=2").json
        self.assertEqual(len(p1["resultados"]), 5)
        self.assertNotEqual(p1["resultados"][0]["id"], p2["resultados"][0]["id"])
        d = self.c.get("/api/v1/cie10/buscar?q=diabetes&capitulo=IV&solo_validos=1").json
        self.assertTrue(all(x["capitulo"]["codigo"] == "IV" and x["valido_para_codificar"] for x in d["resultados"]))
        self.assertEqual(self.c.get("/api/v1/cie10/buscar?q=a&por_pagina=99").status_code, 400)
        self.assertEqual(self.c.get("/api/v1/cie10/buscar").status_code, 400)
        self.assertEqual(self.c.get("/api/v1/cie10/buscar?q=" + "x" * 101).status_code, 400)

    def test_inyeccion(self):
        for q in ("' OR 1=1 --", '"; DROP TABLE cie10; --', "E11%", "NEAR(a b)", "*", "\"\""):
            r = self.c.get("/api/v1/cie10/buscar", query_string={"q": q})
            self.assertIn(r.status_code, (200, 400), q)
        self.assertEqual(self.c.get("/api/v1/salud").json["cie10"], datos.con().execute(
            "SELECT count(*) FROM cie10").fetchone()[0])


class Jerarquia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = cliente()

    def test_capitulos(self):
        self.assertEqual(len(self.c.get("/api/v1/cie10/capitulos").json["capitulos"]), 22)
        self.assertGreaterEqual(len(self.c.get("/api/v1/cie11/capitulos").json["capitulos"]), 28)

    def test_hijos_y_ancestros(self):
        h = self.c.get("/api/v1/cie10/E11/hijos").json["hijos"]
        self.assertIn("E11.9", [x["codigo"] for x in h])
        a = self.c.get("/api/v1/cie10/E11.9/ancestros").json["ancestros"]
        self.assertEqual([x["codigo"] for x in a], ["IV", "E10-E14", "E11"])
        a = self.c.get("/api/v1/cie11/BA00.Z/ancestros").json["ancestros"]
        self.assertEqual(a[0]["tipo"], "capitulo")
        self.assertEqual(a[-1]["codigo"], "BA00")
        h = self.c.get("/api/v1/cie11/CA23/hijos").json["hijos"]
        self.assertTrue(any(x["codigo"] == "CA23.3" for x in h))

    def test_ficha11_tiene_codigo_titulo_y_uri(self):
        f = self.c.get("/api/v1/cie11/5A11").json
        for k in ("codigo", "titulo", "uri"):
            self.assertTrue(f[k], k)   # condición de la licencia de la OMS (1.2.3)


class Operacion(unittest.TestCase):
    def test_etag_304(self):
        c = cliente()
        r = c.get("/api/v1/cie10/E11")
        etag = r.headers["ETag"]
        self.assertIn("max-age", r.headers["Cache-Control"])
        r2 = c.get("/api/v1/cie10/E11", headers={"If-None-Match": etag})
        self.assertEqual(r2.status_code, 304)
        self.assertEqual(r2.data, b"")

    def test_gzip(self):
        c = cliente()
        r = c.get("/api/v1/convertir/cie11-a-cie10/5A11", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.headers.get("Content-Encoding"), "gzip")
        self.assertIn("equivalencias", json.loads(gzip.decompress(r.data)))

    def test_cabeceras_de_seguridad_y_cors(self):
        c = cliente()
        r = c.get("/api/v1/salud", headers={"Origin": "https://ejemplo.org"})
        for h in ("X-Content-Type-Options", "Content-Security-Policy", "Referrer-Policy", "X-Frame-Options"):
            self.assertIn(h, r.headers)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")
        c2 = cliente(cors_origenes=["https://hospital.example"])
        r = c2.get("/api/v1/salud", headers={"Origin": "https://otro.example"})
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)
        r = c2.get("/api/v1/salud", headers={"Origin": "https://hospital.example"})
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "https://hospital.example")
        r = c2.options("/api/v1/convertir/lote", headers={"Origin": "https://hospital.example",
                                                          "Access-Control-Request-Method": "POST"})
        self.assertIn(r.status_code, (200, 204))

    def test_limite_de_tasa(self):
        c = cliente(limite_por_minuto=5)
        estados = [c.get("/api/v1/salud").status_code for _ in range(7)]
        self.assertEqual(estados[:5], [200] * 5)
        self.assertEqual(estados[5], 429)
        r = c.get("/api/v1/salud")
        self.assertIn("Retry-After", r.headers)
        self.assertEqual(r.json["error"]["codigo"], "demasiados_pedidos")

    def test_claves_de_api(self):
        import hashlib
        h = hashlib.sha256(b"clave-de-prueba").hexdigest()
        c = cliente(claves_obligatorias=True, claves={h: {"nombre": "prueba", "plan": "basico", "por_minuto": 3}})
        self.assertEqual(c.get("/api/v1/cie10/E11").status_code, 401)
        self.assertEqual(c.get("/api/v1/cie10/E11", headers={"X-API-Key": "otra"}).status_code, 401)
        ok = [c.get("/api/v1/cie10/E11", headers={"X-API-Key": "clave-de-prueba"}).status_code for _ in range(4)]
        self.assertEqual(ok, [200, 200, 200, 429])
        self.assertEqual(c.get("/api/v1/salud").status_code, 200)   # salud sin clave
        # apagadas por defecto
        self.assertFalse(api.CONFIG_DEFECTO["claves_obligatorias"])

    def test_contadores_sin_contenido(self):
        tmp = Path(tempfile.mkdtemp()) / "c.json"
        app = api.crear_app(config=dict(api.CONFIG_DEFECTO), contadores=tmp)
        c = app.test_client()
        c.get("/api/v1/cie10/buscar?q=secreto-de-prueba")
        c.get("/api/v1/convertir/cie10-a-cie11/E11")
        app.extensions["cie_contadores"].volcar()
        texto = tmp.read_text()
        self.assertNotIn("secreto", texto)
        self.assertNotIn("E11", texto)
        self.assertIn("/api/v1/cie10/buscar", texto)

    def test_error_estructurado(self):
        c = cliente()
        r = c.get("/api/v1/no-existe")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(set(r.json["error"]), {"estado", "codigo", "mensaje"})
        r = c.delete("/api/v1/salud")
        self.assertEqual(r.status_code, 405)


class Integridad(unittest.TestCase):
    def test_codigos_de_la_tabla_existen(self):
        q = lambda s: datos.con().execute(s).fetchone()[0]
        self.assertEqual(q("SELECT count(*) FROM equivalencias WHERE sentido='10a11' AND tipo<>'sin_equivalente' "
                           "AND destino_id NOT IN (SELECT id FROM cie11)"), 0)
        self.assertEqual(q("SELECT count(*) FROM equivalencias WHERE sentido='11a10' "
                           "AND origen NOT IN (SELECT id FROM cie11)"), 0)
        self.assertEqual(q("SELECT count(*) FROM cie10 WHERE padre IS NOT NULL "
                           "AND padre NOT IN (SELECT codigo FROM cie10)"), 0)

    def test_validacion_del_etl(self):
        v = json.loads((RAIZ / "storage" / "validacion.json").read_text())
        self.assertEqual(v["errores"], [])
        self.assertTrue(all(c["ok"] for c in v["casos"]))


if __name__ == "__main__":
    unittest.main()
