#!/usr/bin/python3
"""Genera app/openapi.json (OpenAPI 3.0) a partir de esta descripción.

    /usr/bin/python3 scripts/openapi.py

Si se agrega o cambia una ruta en app/api.py, cambiarla acá y regenerar
(las pruebas comparan las rutas de los dos lados).
"""
import json
from pathlib import Path

DESTINO = Path(__file__).resolve().parent.parent / "app" / "openapi.json"


def p(nombre, donde, desc, tipo="string", requerido=False, ejemplo=None, **extra):
    d = {"name": nombre, "in": donde, "description": desc, "required": requerido or donde == "path",
         "schema": {"type": tipo, **extra}}
    if ejemplo is not None:
        d["example"] = ejemplo
    return d


ERR = {"$ref": "#/components/responses/Error"}
R429 = {"$ref": "#/components/responses/Limite"}


def ok(desc, esquema, ejemplo=None):
    c = {"schema": {"$ref": f"#/components/schemas/{esquema}"}}
    if ejemplo:
        c["example"] = ejemplo
    return {"200": {"description": desc, "headers": {"ETag": {"schema": {"type": "string"}}},
                    "content": {"application/json": c}},
            "304": {"description": "Sin cambios (If-None-Match coincide con el ETag)."},
            "400": ERR, "404": ERR, "429": R429}


BUSQUEDA = [
    p("q", "query", "Código (o parte, p. ej. «E11» o «5A1») o texto a buscar. Sin tildes ni mayúsculas; "
      "tolera errores de tipeo razonables. Hasta 100 caracteres.", requerido=True, ejemplo="diabetes"),
    p("capitulo", "query", "Filtra por capítulo (CIE-10: número romano, p. ej. IV; CIE-11: 01-26, V, X)."),
    p("tipo", "query", "Filtra por tipo de entidad."),
    p("solo_validos", "query", "Sólo códigos para codificar (CIE-10: válidos; CIE-11: hojas).", "boolean"),
    p("pagina", "query", "Página (desde 1).", "integer", minimum=1, maximum=500, default=1),
    p("por_pagina", "query", "Resultados por página (1-50).", "integer", minimum=1, maximum=50, default=20),
]

spec = {
    "openapi": "3.0.3",
    "info": {
        "title": "API del conversor CIE-10 ↔ CIE-11 (español)",
        "version": "1.0.0",
        "description": (
            "Consulta de la CIE-10 y la CIE-11 (MMS 2026-01) en español y de las **tablas oficiales de "
            "correspondencia de la OMS** entre las dos.\n\n"
            "- Sólo datos de clasificación: **no envíe datos de pacientes**. El servicio no guarda el "
            "contenido de las consultas (sólo contadores agregados).\n"
            "- Las equivalencias son las de la OMS; no reemplazan la validación clínica ni de codificación. "
            "Lo que no es oficial (sugerencias por texto cuando no hay equivalencia) viene separado, en "
            "`sugerencias_automaticas`, y rotulado.\n"
            "- Servidor Python: límite de pedidos por IP (cabeceras `X-RateLimit-*`, 429 con `Retry-After`) y "
            "claves de API opcionales con la cabecera `X-API-Key`. La versión PHP para hosting compartido "
            "no lleva límite propio ni claves (lo controla el hosting).\n"
            "- Respuestas con `ETag` y `Cache-Control`: los datos sólo cambian con cada versión "
            "(`X-Datos-Version`).\n\n"
            "CIE-11: © OMS 2019, CC BY-ND 3.0 IGO (https://icd.who.int/browse11). CIE-10 en español: "
            "© OMS/OPS; catálogo de la Secretaría de Salud de México (DGIS). Ver `/api/v1/meta`."),
        "license": {"name": "Datos: CC BY-ND 3.0 IGO (CIE-11, OMS)",
                    "url": "https://creativecommons.org/licenses/by-nd/3.0/igo/"},
    },
    "servers": [{"url": "/", "description": "Este servidor"}],
    "tags": [
        {"name": "Conversión", "description": "Equivalencias oficiales entre CIE-10 y CIE-11."},
        {"name": "CIE-10", "description": "Clasificación CIE-10 en español."},
        {"name": "CIE-11", "description": "CIE-11 MMS 2026-01 en español."},
        {"name": "Servicio", "description": "Estado, versiones y licencias."},
    ],
    "paths": {
        "/api/v1/convertir/cie10-a-cie11/{codigo}": {"get": {
            "tags": ["Conversión"], "summary": "Análogos CIE-11 de un código CIE-10",
            "description": "Todas las correspondencias oficiales, ordenadas de la más parecida a la menos "
                           "(`puntaje`, `prioridad`). Acepta E11.9, E119, e11.9; I10X = I10. Si la "
                           "subcategoría no tiene tabla propia (p. ej. el 4.º carácter de W00-Y34), se usa "
                           "la de su categoría y se avisa en `heredada_de`.",
            "parameters": [p("codigo", "path", "Código CIE-10.", ejemplo="E11.9"),
                           p("sin_sugerencias", "query", "No calcular sugerencias automáticas.", "boolean")],
            "responses": ok("Conversión", "Conversion")}},
        "/api/v1/convertir/cie11-a-cie10/{codigo}": {"get": {
            "tags": ["Conversión"], "summary": "Análogos CIE-10 de un código CIE-11",
            "description": "Destino de la tabla oficial 11→10 más los códigos CIE-10 que la tabla 10→11 "
                           "lleva a esta entidad (`relacionado`). Acepta también el id de linearización "
                           "(p. ej. 761947693/unspecified).",
            "parameters": [p("codigo", "path", "Código CIE-11 o id.", ejemplo="5A11"),
                           p("sin_sugerencias", "query", "No calcular sugerencias automáticas.", "boolean")],
            "responses": ok("Conversión", "Conversion")}},
        "/api/v1/convertir/lote": {"post": {
            "tags": ["Conversión"], "summary": "Conversión por lote (hasta 100 códigos)",
            "description": "Sólo se aceptan los campos `sentido`, `codigos` y `sin_sugerencias` (por defecto "
                           "true). Cualquier otro campo da 400: el servicio no recibe datos de pacientes. "
                           "Cuenta como 5 pedidos para el límite.",
            "requestBody": {"required": True, "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/PedidoLote"},
                "example": {"sentido": "cie10-a-cie11", "codigos": ["E11.9", "I10", "J45.9", "U07.1"]}}}},
            "responses": {"200": {"description": "Resultados en el mismo orden",
                                  "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Lote"}}}},
                          "400": ERR, "413": ERR, "415": ERR, "429": R429}}},
        "/api/v1/cie10/buscar": {"get": {
            "tags": ["CIE-10"], "summary": "Buscar en la CIE-10 por código o texto",
            "parameters": BUSQUEDA, "responses": ok("Resultados paginados", "Busqueda")}},
        "/api/v1/cie11/buscar": {"get": {
            "tags": ["CIE-11"], "summary": "Buscar en la CIE-11 por código o texto",
            "parameters": BUSQUEDA, "responses": ok("Resultados paginados", "Busqueda")}},
        "/api/v1/cie10/capitulos": {"get": {"tags": ["CIE-10"], "summary": "Capítulos de la CIE-10",
                                            "responses": ok("Capítulos", "Capitulos")}},
        "/api/v1/cie11/capitulos": {"get": {"tags": ["CIE-11"], "summary": "Capítulos de la CIE-11",
                                            "responses": ok("Capítulos", "Capitulos")}},
        "/api/v1/cie10/{codigo}": {"get": {
            "tags": ["CIE-10"], "summary": "Ficha de un código CIE-10 (con ruta e hijos)",
            "description": "También capítulos (IV) y grupos (E10-E14).",
            "parameters": [p("codigo", "path", "Código CIE-10.", ejemplo="E11")],
            "responses": ok("Ficha", "Entidad")}},
        "/api/v1/cie10/{codigo}/hijos": {"get": {
            "tags": ["CIE-10"], "summary": "Hijos directos",
            "parameters": [p("codigo", "path", "Código CIE-10.", ejemplo="E11")],
            "responses": ok("Hijos", "Hijos")}},
        "/api/v1/cie10/{codigo}/ancestros": {"get": {
            "tags": ["CIE-10"], "summary": "Ancestros (capítulo → grupo → categoría)",
            "parameters": [p("codigo", "path", "Código CIE-10.", ejemplo="E11.9")],
            "responses": ok("Ancestros", "Ancestros")}},
        "/api/v1/cie11/{codigo}": {"get": {
            "tags": ["CIE-11"], "summary": "Ficha de un código CIE-11 (con URI, ruta e hijos)",
            "parameters": [p("codigo", "path", "Código CIE-11, id de linearización o BlockId.", ejemplo="5A11")],
            "responses": ok("Ficha", "Entidad")}},
        "/api/v1/cie11/{codigo}/hijos": {"get": {
            "tags": ["CIE-11"], "summary": "Hijos directos",
            "parameters": [p("codigo", "path", "Código CIE-11 o id.", ejemplo="CA23")],
            "responses": ok("Hijos", "Hijos")}},
        "/api/v1/cie11/{codigo}/ancestros": {"get": {
            "tags": ["CIE-11"], "summary": "Ancestros (capítulo → bloques → categoría)",
            "parameters": [p("codigo", "path", "Código CIE-11 o id.", ejemplo="BA00.Z")],
            "responses": ok("Ancestros", "Ancestros")}},
        "/api/v1/meta": {"get": {"tags": ["Servicio"], "summary": "Versiones, fuentes, conteos, atribución y licencias",
                                 "responses": {"200": {"description": "Metadatos"}}}},
        "/api/v1/salud": {"get": {"tags": ["Servicio"], "summary": "Estado del servicio (sin caché)",
                                  "responses": {"200": {"description": "Funciona"},
                                                "503": {"description": "La base no responde"}}}},
        "/api/v1/openapi.json": {"get": {"tags": ["Servicio"], "summary": "Esta especificación",
                                         "responses": {"200": {"description": "OpenAPI 3"}}}},
    },
    "components": {
        "securitySchemes": {"ClaveApi": {"type": "apiKey", "in": "header", "name": "X-API-Key",
                                         "description": "Opcional: sólo si el servicio se configura con claves."}},
        "responses": {
            "Error": {"description": "Error estructurado",
                      "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
            "Limite": {"description": "Límite de pedidos superado",
                       "headers": {"Retry-After": {"schema": {"type": "integer"}}},
                       "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
        },
        "schemas": {
            "Error": {"type": "object", "properties": {"error": {"type": "object", "properties": {
                "estado": {"type": "integer"}, "codigo": {"type": "string", "example": "codigo_inexistente"},
                "mensaje": {"type": "string"}, "detalle": {}}}}},
            "Referencia": {"type": "object", "properties": {
                "codigo": {"type": "string", "nullable": True}, "id": {"type": "string"},
                "titulo": {"type": "string"}, "tipo": {"type": "string"}}},
            "Entidad": {"type": "object", "description": "Código de la CIE-10 o la CIE-11",
                        "properties": {
                            "sistema": {"type": "string", "enum": ["CIE-10", "CIE-11"]},
                            "codigo": {"type": "string", "nullable": True, "example": "5A11"},
                            "id": {"type": "string", "description": "Sólo CIE-11: id de linearización"},
                            "titulo": {"type": "string", "description": "Tal como lo publica la fuente"},
                            "tipo": {"type": "string",
                                     "enum": ["capitulo", "grupo", "bloque", "categoria", "subcategoria"]},
                            "nivel": {"type": "integer"},
                            "capitulo": {"$ref": "#/components/schemas/Referencia"},
                            "grupo": {"$ref": "#/components/schemas/Referencia"},
                            "ruta": {"type": "array", "items": {"$ref": "#/components/schemas/Referencia"}},
                            "uri": {"type": "string", "description": "CIE-11: URI de linearización de la OMS"},
                            "uri_fundacion": {"type": "string"},
                            "valido_para_codificar": {"type": "boolean"},
                            "nota_codificacion": {"type": "string", "nullable": True},
                            "navegador_oms": {"type": "string"},
                        }},
            "Correspondencia": {"type": "object", "properties": {
                "tipo": {"type": "string", "enum": ["equivalente", "aproximado", "uno_a_varios", "combinacion",
                                                   "agrupacion", "relacionado", "sin_equivalente"]},
                "rotulo": {"type": "string"}, "explicacion": {"type": "string"},
                "principal": {"type": "boolean", "description": "Es el destino de la tabla «a una categoría»"},
                "ida_y_vuelta": {"type": "boolean", "description": "La tabla del otro sentido vuelve al origen"},
                "prioridad": {"type": "integer"},
                "puntaje": {"type": "integer", "description": "Orden de presentación (0-100). No es una medida clínica."},
                "tabla_oms": {"type": "string"}, "observaciones": {"type": "string", "nullable": True}}},
            "Equivalencia": {"allOf": [{"$ref": "#/components/schemas/Entidad"}, {"type": "object", "properties": {
                "correspondencia": {"$ref": "#/components/schemas/Correspondencia"},
                "postcoordinacion": {"type": "object", "properties": {
                    "cluster": {"type": "string", "example": "1A00&XN8P1"},
                    "partes": {"type": "array", "items": {"type": "object"}}}}}}]},
            "Conversion": {"type": "object", "properties": {
                "sentido": {"type": "string"}, "origen": {"$ref": "#/components/schemas/Entidad"},
                "equivalencias": {"type": "array", "items": {"$ref": "#/components/schemas/Equivalencia"}},
                "total": {"type": "integer"}, "sin_equivalente": {"type": "boolean"},
                "sin_equivalente_motivo": {"type": "string"},
                "heredada_de": {"type": "object", "nullable": True},
                "sugerencias_automaticas": {"type": "object", "description":
                                            "Sólo si no hay equivalencia oficial. NO es oficial."},
                "aviso": {"type": "string"}}},
            "PedidoLote": {"type": "object", "required": ["sentido", "codigos"], "additionalProperties": False,
                           "properties": {
                               "sentido": {"type": "string", "enum": ["cie10-a-cie11", "cie11-a-cie10"]},
                               "codigos": {"type": "array", "maxItems": 100, "items": {"type": "string", "maxLength": 40}},
                               "sin_sugerencias": {"type": "boolean", "default": True}}},
            "Lote": {"type": "object", "properties": {
                "sentido": {"type": "string"}, "total": {"type": "integer"}, "con_error": {"type": "integer"},
                "resultados": {"type": "array", "items": {"type": "object"}}}},
            "Busqueda": {"type": "object", "properties": {
                "sistema": {"type": "string"}, "q": {"type": "string"}, "total": {"type": "integer"},
                "pagina": {"type": "integer"}, "por_pagina": {"type": "integer"},
                "modo": {"type": "string", "enum": ["codigo", "texto", "texto_corregido"]},
                "correccion": {"type": "string", "nullable": True},
                "resultados": {"type": "array", "items": {"$ref": "#/components/schemas/Entidad"}}}},
            "Capitulos": {"type": "object", "properties": {
                "capitulos": {"type": "array", "items": {"$ref": "#/components/schemas/Entidad"}}}},
            "Hijos": {"type": "object", "properties": {
                "codigo": {"type": "string"}, "hijos": {"type": "array", "items": {"$ref": "#/components/schemas/Entidad"}}}},
            "Ancestros": {"type": "object", "properties": {
                "codigo": {"type": "string"},
                "ancestros": {"type": "array", "items": {"$ref": "#/components/schemas/Referencia"}}}},
        },
    },
}

# ejemplos de uso, los muestra /api/docs
spec["x-ejemplos"] = {
    "curl": [
        "curl -s {{BASE}}/api/v1/convertir/cie10-a-cie11/E11.9",
        "curl -s '{{BASE}}/api/v1/cie11/buscar?q=asma&por_pagina=5'",
        "curl -s -X POST -H 'Content-Type: application/json' \\\n  -d '{\"sentido\":\"cie10-a-cie11\",\"codigos\":[\"E11.9\",\"I10\",\"U07.1\"]}' \\\n  {{BASE}}/api/v1/convertir/lote",
    ],
    "python": (
        "import requests\n"
        "BASE = \"{{BASE}}/api/v1\"\n"
        "r = requests.get(f\"{BASE}/convertir/cie10-a-cie11/E11.9\", timeout=10)\n"
        "r.raise_for_status()\n"
        "for e in r.json()[\"equivalencias\"]:\n"
        "    print(e[\"codigo\"], e[\"titulo\"], e[\"correspondencia\"][\"rotulo\"])\n\n"
        "lote = requests.post(f\"{BASE}/convertir/lote\", json={\n"
        "    \"sentido\": \"cie11-a-cie10\", \"codigos\": [\"5A11\", \"BA00.Z\"]}, timeout=10).json()\n"),
    "javascript": (
        "const BASE = '{{BASE}}/api/v1';\n"
        "const r = await fetch(`${BASE}/convertir/cie11-a-cie10/${encodeURIComponent('5A11')}`);\n"
        "if (!r.ok) throw new Error((await r.json()).error.mensaje);\n"
        "const datos = await r.json();\n"
        "datos.equivalencias.forEach(e =>\n"
        "  console.log(e.codigo, e.titulo, e.correspondencia.rotulo));\n"),
}

if __name__ == "__main__":
    DESTINO.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n")
    print(f"escrito {DESTINO} ({len(spec['paths'])} rutas)")
