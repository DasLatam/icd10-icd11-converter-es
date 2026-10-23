"""Consultas sobre storage/cie.db (sólo lectura). Sin Flask: lo usan la API y las pruebas."""
from __future__ import annotations

import difflib
import re
import sqlite3
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "storage" / "cie.db"

_local = threading.local()

ROTULOS = {
    "equivalente": "Equivalente",
    "aproximado": "Aproximado",
    "uno_a_varios": "Uno a varios",
    "combinacion": "Combinación (postcoordinada)",
    "agrupacion": "Agrupación",
    "relacionado": "Relacionado (tabla inversa)",
    "sin_equivalente": "Sin equivalente",
}
EXPLICACION = {
    "equivalente": "Correspondencia uno a uno que la OMS da en los dos sentidos.",
    "aproximado": "Destino principal de la tabla oficial, pero no es una correspondencia exacta "
                  "(más amplio, más específico o sin ida y vuelta).",
    "uno_a_varios": "El código de origen se reparte en varios códigos de destino según la tabla oficial; "
                    "hay que elegir el que corresponde al caso.",
    "combinacion": "El destino combina un código base con códigos de extensión de la CIE-11.",
    "agrupacion": "El destino es un grupo o capítulo, no un código para codificar.",
    "relacionado": "La tabla oficial en el otro sentido lleva este código al consultado.",
    "sin_equivalente": "La tabla oficial no trae correspondencia.",
}
# Al cambiar de versión de la CIE-11 se cambia acá (y en VERSION_CIE11 de scripts/etl.py).
NAVEGADOR_OMS = "https://icd.who.int/browse/2026-01/mms/es#"
AVISO = ("Equivalencias según las tablas oficiales de la OMS; no reemplazan la validación clínica "
         "ni de codificación.")


def con() -> sqlite3.Connection:
    c = getattr(_local, "con", None)
    if c is None:
        c = sqlite3.connect(f"file:{DB}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        c.row_factory = sqlite3.Row
        _local.con = c
    return c


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t.lower()).strip()


@lru_cache(maxsize=1)
def meta() -> dict:
    m = {r["clave"]: r["valor"] for r in con().execute("SELECT clave, valor FROM meta")}
    m["fuentes"] = [dict(r) for r in con().execute("SELECT * FROM fuentes ORDER BY archivo")]
    return m


# ---------------------------------------------------------------- códigos

def codigo10(texto: str) -> str:
    t = re.sub(r"\s", "", (texto or "")).upper().replace("–", "-")
    if re.fullmatch(r"[A-Z]\d\d[0-9X]", t):
        t = t[:3] if t[3] == "X" else f"{t[:3]}.{t[3]}"
    if re.fullmatch(r"[A-Z]\d\d\.X", t):
        t = t[:3]
    return t


def codigo11(texto: str) -> str:
    return re.sub(r"\s", "", (texto or "")).upper()


ES_CODIGO = re.compile(r"^[A-Za-z0-9]{1,4}(\.[A-Za-z0-9]{0,4})?$")


def _cap10(cod: str) -> dict | None:
    r = con().execute("SELECT codigo, titulo FROM cie10 WHERE codigo=?", (cod,)).fetchone()
    return {"codigo": r["codigo"], "titulo": r["titulo"]} if r else None


def _cap11(num: str) -> dict | None:
    r = con().execute("SELECT id, titulo FROM cie11 WHERE tipo='capitulo' AND capitulo=?", (num,)).fetchone()
    return {"codigo": num, "titulo": r["titulo"], "id": r["id"]} if r else None


def ficha10(fila: sqlite3.Row, completa: bool = True) -> dict:
    d = {
        "sistema": "CIE-10",
        "codigo": fila["codigo"],
        "titulo": fila["titulo"],
        "tipo": fila["tipo"],
        "nivel": fila["nivel"],
        "valido_para_codificar": bool(fila["valido"]),
        "capitulo": _cap10(fila["capitulo"]) if fila["capitulo"] else None,
        "grupo": _cap10(fila["grupo"]) if fila["grupo"] else None,
        "padre": fila["padre"],
    }
    if completa:
        d["ruta"] = ancestros10(fila["codigo"])
        d["en_tablas_oms"] = bool(fila["en_tabla_oms"])
        d["daga"] = bool(fila["daga"])
        d["asterisco"] = bool(fila["asterisco"])
        if fila["modificacion"] and fila["modificacion"] != "NO":
            d["historial_fuente"] = fila["modificacion"]
        if "CEMECE" in (fila["modificacion"] or ""):
            d["uso_nacional"] = "Código agregado por acuerdo del CEMECE (México); no es de la OMS."
    return d


def ficha11(fila: sqlite3.Row, completa: bool = True) -> dict:
    d = {
        "sistema": "CIE-11",
        "codigo": fila["codigo"],
        "id": fila["id"],
        "titulo": fila["titulo"],
        "tipo": fila["tipo"],
        "nivel": fila["nivel"],
        "capitulo": _cap11(fila["capitulo"]),
        "uri": fila["uri_linearizacion"],
        "uri_fundacion": fila["uri_fundacion"],
        "residual": bool(fila["residual"]),
        "hoja": bool(fila["hoja"]),
        "navegador_oms": NAVEGADOR_OMS + fila["id"],
    }
    if completa:
        d["ruta"] = ancestros11(fila["id"])
        d["nota_codificacion"] = fila["nota"]
        if fila["capitulo"] == "X":
            d["extension"] = "Código de extensión: sólo se usa combinado con un código base."
    return d


def buscar_codigo10(cod: str) -> sqlite3.Row | None:
    c = codigo10(cod)
    return con().execute("SELECT * FROM cie10 WHERE codigo=?", (c,)).fetchone()


def buscar_codigo11(cod: str) -> sqlite3.Row | None:
    c = codigo11(cod)
    r = con().execute("SELECT * FROM cie11 WHERE codigo=?", (c,)).fetchone()
    if r is None:
        r = con().execute("SELECT * FROM cie11 WHERE id=?", (cod.strip(),)).fetchone()
    if r is None:
        r = con().execute("SELECT * FROM cie11 WHERE bloque_id=?", (cod.strip(),)).fetchone()
    return r


def ancestros10(cod: str) -> list[dict]:
    ruta, vistos = [], set()
    r = con().execute("SELECT padre FROM cie10 WHERE codigo=?", (cod,)).fetchone()
    p = r["padre"] if r else None
    while p and p not in vistos:
        vistos.add(p)
        f = con().execute("SELECT codigo, titulo, tipo, padre FROM cie10 WHERE codigo=?", (p,)).fetchone()
        if not f:
            break
        ruta.insert(0, {"codigo": f["codigo"], "titulo": f["titulo"], "tipo": f["tipo"]})
        p = f["padre"]
    return ruta


def ancestros11(ident: str) -> list[dict]:
    ruta, vistos = [], set()
    r = con().execute("SELECT padre FROM cie11 WHERE id=?", (ident,)).fetchone()
    p = r["padre"] if r else None
    while p and p not in vistos:
        vistos.add(p)
        f = con().execute("SELECT id, codigo, bloque_id, titulo, tipo, padre, capitulo FROM cie11 WHERE id=?",
                          (p,)).fetchone()
        if not f:
            break
        ruta.insert(0, {"codigo": f["codigo"] or (f["capitulo"] if f["tipo"] == "capitulo" else None),
                        "id": f["id"], "titulo": f["titulo"], "tipo": f["tipo"]})
        p = f["padre"]
    return ruta


def hijos10(cod: str) -> list[dict]:
    return [ficha10(r, completa=False) for r in con().execute(
        "SELECT * FROM cie10 WHERE padre=? ORDER BY orden", (cod,))]


def hijos11(ident: str) -> list[dict]:
    return [ficha11(r, completa=False) for r in con().execute(
        "SELECT * FROM cie11 WHERE padre=? ORDER BY orden", (ident,))]


def capitulos10() -> list[dict]:
    return [ficha10(r, completa=False) for r in con().execute(
        "SELECT * FROM cie10 WHERE tipo='capitulo' ORDER BY orden")]


def capitulos11() -> list[dict]:
    return [ficha11(r, completa=False) for r in con().execute(
        "SELECT * FROM cie11 WHERE tipo='capitulo' ORDER BY orden")]


# ---------------------------------------------------------------- búsqueda

@lru_cache(maxsize=2)
def _vocabulario(sistema: str) -> dict[str, list[str]]:
    por_letra: dict[str, list[str]] = {}
    for r in con().execute("SELECT palabra FROM vocabulario WHERE sistema=? ORDER BY veces DESC", (sistema,)):
        por_letra.setdefault(r[0][:1], []).append(r[0])
    return por_letra


def _corregir(sistema: str, palabras: list[str]) -> list[str] | None:
    vocab = _vocabulario(sistema)
    salida, cambio = [], False
    for p in palabras:
        if len(p) < 4:
            salida.append(p)
            continue
        candidatos = vocab.get(p[:1], [])
        if p in candidatos:
            salida.append(p)
            continue
        cerca = difflib.get_close_matches(p, candidatos, n=1, cutoff=0.75)
        if not cerca and len(p) > 4:
            # el error puede estar en la primera letra
            todas = [w for lista in vocab.values() for w in lista if abs(len(w) - len(p)) <= 2]
            cerca = difflib.get_close_matches(p, todas, n=1, cutoff=0.8)
        if cerca:
            salida.append(cerca[0])
            cambio = cambio or cerca[0] != p
        else:
            salida.append(p)
    return salida if cambio else None


def buscar(sistema: str, q: str, capitulo: str | None = None, tipo: str | None = None,
           solo_validos: bool = False, pagina: int = 1, por_pagina: int = 20) -> dict:
    tabla = "cie10" if sistema == "cie10" else "cie11"
    clave = "codigo" if sistema == "cie10" else "id"
    texto = (q or "").strip()
    filtros, params = [], []
    if capitulo:
        filtros.append("t.capitulo = ?")
        params.append(capitulo.upper() if sistema == "cie10" else capitulo.upper().zfill(2)
                      if capitulo.isdigit() else capitulo.upper())
    if tipo:
        filtros.append("t.tipo = ?")
        params.append(tipo)
    if solo_validos:
        filtros.append("t.valido = 1" if sistema == "cie10" else "t.hoja = 1")
    where_extra = (" AND " + " AND ".join(filtros)) if filtros else ""
    desde = (pagina - 1) * por_pagina
    correccion = None
    modo = "texto"

    if ES_CODIGO.match(texto) and re.search(r"\d", texto):
        modo = "codigo"
        if sistema == "cie10":
            pref = codigo10(texto).replace(".", "")
            cond = "t.codigo_compacto LIKE ? || '%'"
        else:
            pref = codigo11(texto)
            cond = "t.codigo LIKE ? || '%'"
        base = f"FROM {tabla} t WHERE {cond}{where_extra}"
        total = con().execute(f"SELECT count(*) {base}", [pref, *params]).fetchone()[0]
        filas = con().execute(f"SELECT t.* {base} ORDER BY length(t.codigo), t.orden LIMIT ? OFFSET ?",
                              [pref, *params, por_pagina, desde]).fetchall()
    else:
        palabras = re.findall(r"[a-z0-9]+", normalizar(texto))[:8]
        if not palabras:
            return {"total": 0, "pagina": pagina, "por_pagina": por_pagina, "resultados": [], "modo": modo}

        def consultar(pal):
            expr = " AND ".join(f'"{p}"*' for p in pal)
            base = (f"FROM {tabla}_fts f JOIN {tabla} t ON t.{clave} = f.{clave} "
                    f"WHERE {tabla}_fts MATCH ?{where_extra}")
            tot = con().execute(f"SELECT count(*) {base}", [expr, *params]).fetchone()[0]
            # primero lo que empieza igual, después los códigos que se pueden usar
            orden = ("bm25(" + tabla + "_fts) + (CASE WHEN f.texto LIKE ? THEN -5 ELSE 0 END) "
                     "+ (CASE t.tipo WHEN 'categoria' THEN -1 WHEN 'subcategoria' THEN 0 ELSE 1 END)")
            fil = con().execute(
                f"SELECT t.* {base} ORDER BY {orden}, t.orden LIMIT ? OFFSET ?",
                [expr, *params, f"%{pal[0]}%", por_pagina, desde]).fetchall()
            return tot, fil

        total, filas = consultar(palabras)
        if total == 0:
            corregidas = _corregir(sistema, palabras)
            if corregidas:
                total, filas = consultar(corregidas)
                if total:
                    correccion = " ".join(corregidas)
                    modo = "texto_corregido"
    fichar = ficha10 if sistema == "cie10" else ficha11
    return {
        "total": total, "pagina": pagina, "por_pagina": por_pagina, "modo": modo,
        "correccion": correccion,
        "resultados": [fichar(r, completa=False) for r in filas],
    }


# ---------------------------------------------------------------- conversión

def _destino11(e: sqlite3.Row) -> dict:
    d = {"codigo": e["destino"], "id": e["destino_id"], "titulo": None, "capitulo": None, "ruta": []}
    if e["destino_id"]:
        f = con().execute("SELECT * FROM cie11 WHERE id=?", (e["destino_id"],)).fetchone()
        if f:
            d = ficha11(f)
    if e["postcoordinacion"]:
        partes = []
        for cod in re.split(r"([&/])", e["postcoordinacion"]):
            if cod in "&/":
                continue
            f = con().execute("SELECT codigo, titulo, capitulo FROM cie11 WHERE codigo=?", (cod,)).fetchone()
            partes.append({"codigo": cod, "titulo": f["titulo"] if f else None,
                           "extension": bool(f and f["capitulo"] == "X")})
        d["postcoordinacion"] = {"cluster": e["postcoordinacion"], "partes": partes}
    return d


def _destino10(e: sqlite3.Row) -> dict:
    f = con().execute("SELECT * FROM cie10 WHERE codigo=?", (e["destino"],)).fetchone()
    if f:
        return ficha10(f)
    return {"sistema": "CIE-10", "codigo": e["destino"], "titulo": None,
            "nota": "Código de la tabla de la OMS que no figura en la CIE-10 en español de la fuente."}


def _equivalencia(e: sqlite3.Row, destino: dict) -> dict:
    return {
        **destino,
        "correspondencia": {
            "tipo": e["tipo"], "rotulo": ROTULOS[e["tipo"]], "explicacion": EXPLICACION[e["tipo"]],
            "principal": bool(e["principal"]), "ida_y_vuelta": bool(e["ida_y_vuelta"]),
            "prioridad": e["prioridad"], "puntaje": e["puntaje"], "tabla_oms": e["tabla"],
            "observaciones": e["observaciones"],
        },
    }


def sugerencias(sistema_destino: str, titulo: str, n: int = 5) -> list[dict]:
    """Búsqueda por texto en el otro sistema. NO es una equivalencia oficial."""
    palabras = [p for p in re.findall(r"[a-z0-9]+", normalizar(titulo)) if len(p) > 3][:6]
    if not palabras:
        return []
    tabla = sistema_destino
    clave = "codigo" if tabla == "cie10" else "id"
    expr = " OR ".join(f'"{p}"' for p in palabras)
    filas = con().execute(
        f"SELECT t.* FROM {tabla}_fts f JOIN {tabla} t ON t.{clave}=f.{clave} WHERE {tabla}_fts MATCH ? "
        f"AND t.tipo IN ('categoria','subcategoria') ORDER BY bm25({tabla}_fts) LIMIT ?", (expr, n)).fetchall()
    fichar = ficha10 if tabla == "cie10" else ficha11
    return [{**fichar(f, completa=False),
             "sugerencia_automatica": "Coincidencia de texto calculada por este servicio. "
                                      "NO es una equivalencia oficial de la OMS."} for f in filas]


def convertir(sentido: str, codigo: str, con_sugerencias: bool = True) -> dict | None:
    if sentido == "cie10-a-cie11":
        f = buscar_codigo10(codigo)
        cod = f["codigo"] if f else codigo10(codigo)
        filas = con().execute("SELECT * FROM equivalencias WHERE sentido='10a11' AND origen=? ORDER BY prioridad",
                              (cod,)).fetchall()
        if f is None and not filas:
            return None
        origen = ficha10(f) if f else {"sistema": "CIE-10", "codigo": cod, "titulo": None,
                                        "nota": "Código de la tabla de la OMS sin título en español en la fuente."}
        heredada = None
        if not filas and "." in cod:
            # El 4.º carácter no tiene tabla propia (p. ej. lugar de ocurrencia en W00-Y34):
            # vale la de la categoría madre.
            filas = con().execute("SELECT * FROM equivalencias WHERE sentido='10a11' AND origen=? "
                                  "ORDER BY prioridad", (cod[:3],)).fetchall()
            if filas:
                heredada = {"codigo": cod[:3], "titulo": (_cap10(cod[:3]) or {}).get("titulo"),
                            "motivo": "La tabla oficial no trae esta subcategoría; se muestra la "
                                      "correspondencia de su categoría de tres caracteres."}
        equivalencias = [_equivalencia(e, _destino11(e)) for e in filas if e["tipo"] != "sin_equivalente"]
        sistema_dest = "cie11"
    else:
        f = buscar_codigo11(codigo)
        if f is None:
            return None
        origen = ficha11(f)
        filas = con().execute("SELECT * FROM equivalencias WHERE sentido='11a10' AND origen=? ORDER BY prioridad",
                              (f["id"],)).fetchall()
        heredada = None
        if not filas and f["padre"]:
            p = con().execute("SELECT * FROM cie11 WHERE id=?", (f["padre"],)).fetchone()
            filas = con().execute("SELECT * FROM equivalencias WHERE sentido='11a10' AND origen=? "
                                  "ORDER BY prioridad", (f["padre"],)).fetchall()
            if filas and p:
                heredada = {"codigo": p["codigo"], "id": p["id"], "titulo": p["titulo"],
                            "motivo": "La tabla oficial no trae esta entidad; se muestra la de su "
                                      "entidad madre."}
        equivalencias = [_equivalencia(e, _destino10(e)) for e in filas if e["tipo"] != "sin_equivalente"]
        sistema_dest = "cie10"
    equivalencias.sort(key=lambda x: (-x["correspondencia"]["puntaje"], x["correspondencia"]["prioridad"]))
    sin = not equivalencias
    respuesta = {
        "sentido": sentido,
        "origen": origen,
        "equivalencias": equivalencias,
        "total": len(equivalencias),
        "sin_equivalente": sin,
        "heredada_de": heredada,
        "aviso": AVISO,
    }
    if sin:
        nota = next((e["observaciones"] for e in filas if e["tipo"] == "sin_equivalente"), None)
        respuesta["sin_equivalente_motivo"] = nota or "Las tablas oficiales de la OMS no traen correspondencia."
        if con_sugerencias and origen.get("titulo"):
            respuesta["sugerencias_automaticas"] = {
                "aviso": "SUGERENCIA AUTOMÁTICA NO OFICIAL: coincidencia de texto entre títulos, "
                         "sin validar. No usar para codificar sin revisión.",
                "resultados": sugerencias(sistema_dest, origen["titulo"]),
            }
    return respuesta
