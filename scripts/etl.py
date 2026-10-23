#!/usr/bin/python3
"""ETL del conversor CIE-10 ↔ CIE-11: baja las fuentes oficiales, las valida y
arma storage/cie.db (SQLite con FTS5).

    python3 scripts/etl.py [--bajar] [--sin-validar]

Fuentes (detalle, licencias y hashes en storage/fuentes/INDICE.md):

- CIE-11 MMS en español: «SimpleTabulation-ICD-11-MMS-es.zip» de la OMS
  (descarga abierta, sin cuenta), versión 2026-01.
- Tablas de correspondencia CIE-10 ↔ CIE-11 de la OMS: «mapping.zip» de la misma
  versión. Los títulos de esas tablas vienen en inglés: sólo se usan los códigos.
- CIE-10 en español: catálogo CATCIE10 de la DGIS (Secretaría de Salud de México),
  que mantiene el CEMECE, centro colaborador de la OMS/OPS para la familia de
  clasificaciones en español. Trae las actualizaciones de la OMS hasta 2021+
  (COVID-19, U10, U12...). Los títulos vienen EN MAYÚSCULAS y así se muestran: no
  se tocan.
- Títulos en español de los grupos (bloques) de la CIE-10: «Lista de tres
  caracteres» del Volumen 1 de la CIE-10 (OPS, edición 2018). El PDF tiene una
  marca de agua de distribución restringida: queda sólo como fuente local, no se
  publica; se extraen únicamente los títulos de grupo.

Nada se traduce ni se reescribe: se normaliza el formato del código (A000 → A00.0,
I10X → I10) y se sacan los guiones de sangría de los títulos de la CIE-11 («- - -»),
que son presentación del archivo, no parte del título.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = RAIZ / "storage" / "fuentes"
DB = RAIZ / "storage" / "cie.db"
VALIDACION = RAIZ / "storage" / "validacion.json"

VERSION_CIE11 = "2026-01"
DESCARGAS = {
    "SimpleTabulation-ICD-11-MMS-es.zip":
        f"https://icdcdn.who.int/static/releasefiles/{VERSION_CIE11}/SimpleTabulation-ICD-11-MMS-es.zip",
    "mapping.zip":
        f"https://icdcdn.who.int/static/releasefiles/{VERSION_CIE11}/mapping.zip",
    "dgis_sectorial_Catalogos_egresos_2018_2024.zip":
        "http://www.dgis.salud.gob.mx/descargas/datosabiertos/egresos/sectorial_Catalogos_egresos_2018_2024.zip",
    "CIE-10_2018_VOL1.pdf":
        "https://repositoriodeis.minsal.cl/ContenidoSitioWeb2020/uploads/2020/12/CIE-10_2018_VOL1.pdf",
}
DESCRIPCION = {
    "SimpleTabulation-ICD-11-MMS-es.zip": "OMS · CIE-11 MMS en español, tabulación simple (TXT y XLSX)",
    "mapping.zip": "OMS · tablas de correspondencia CIE-10 ↔ CIE-11 (TXT y XLSX)",
    "dgis_sectorial_Catalogos_egresos_2018_2024.zip": "DGIS México · catálogos de egresos 2018-2024 (CATCIE10.csv)",
    "CIE-10_2018_VOL1.pdf": "OPS/OMS · CIE-10 Volumen 1, edición 2018 (sólo títulos de grupos)",
}


# ---------------------------------------------------------------- utilidades

def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para buscar. No se muestra nunca."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t.lower()).strip()


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def bajar(forzar: bool) -> None:
    FUENTES.mkdir(parents=True, exist_ok=True)
    for nombre, url in DESCARGAS.items():
        destino = FUENTES / nombre
        if destino.exists() and not forzar:
            continue
        print(f"bajando {url}", flush=True)
        pedido = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (cie-etl)"})
        with urllib.request.urlopen(pedido, timeout=300) as r:
            datos = r.read()
        tmp = destino.with_suffix(destino.suffix + ".part")
        tmp.write_bytes(datos)
        tmp.replace(destino)


def indice_fuentes() -> list[dict]:
    """Hash, tamaño y fecha de cada archivo → fuentes.json e INDICE.md."""
    previo = {}
    j = FUENTES / "fuentes.json"
    if j.exists():
        previo = {x["archivo"]: x for x in json.loads(j.read_text())}
    filas = []
    for ruta in sorted(FUENTES.iterdir()):
        if ruta.suffix in (".json", ".md") or ruta.name.endswith(".part"):
            continue
        h = sha256(ruta)
        anterior = previo.get(ruta.name, {})
        filas.append({
            "archivo": ruta.name,
            "descripcion": DESCRIPCION.get(ruta.name, anterior.get("descripcion", "")),
            "url": DESCARGAS.get(ruta.name, anterior.get("url", "")),
            "bytes": ruta.stat().st_size,
            "sha256": h,
            "bajado": anterior.get("bajado") if anterior.get("sha256") == h and anterior.get("bajado")
            else datetime.fromtimestamp(ruta.stat().st_mtime).strftime("%Y-%m-%d"),
        })
    j.write_text(json.dumps(filas, ensure_ascii=False, indent=1) + "\n")
    md = ["# Fuentes originales", "",
          "Archivos tal como se bajaron (no se editan). Los arma y verifica `scripts/etl.py`;",
          "este índice se regenera en cada corrida. Licencias y uso: ver `../../README.md`.", "",
          "| Archivo | Qué es | Bytes | Bajado | SHA-256 |", "|---|---|---:|---|---|"]
    for f in filas:
        url = f" ([origen]({f['url']}))" if f["url"] else ""
        md.append(f"| `{f['archivo']}` | {f['descripcion']}{url} | {f['bytes']:,} | {f['bajado']} | `{f['sha256']}` |")
    md += ["", "- `who_icd11-license.pdf`: términos de uso de las Clasificaciones de la OMS "
           "(https://icd.who.int/en/docs/icd11-license.pdf).",
           "- `minsal_cl_Lista-Tabular-CIE-10.xls`: lista tabular del MINSAL/DEIS de Chile (2018). Se bajó para "
           "comparar y **no se usa**: sólo códigos válidos, sin jerarquía y sin las actualizaciones COVID.",
           "- `CIE-10_2018_VOL1.pdf` lleva marca de agua de distribución restringida de la OPS: queda sólo acá, "
           "no se publica ni se sirve.", ""]
    (FUENTES / "INDICE.md").write_text("\n".join(md))
    return filas


# ---------------------------------------------------------------- CIE-11

def _lin_id(uri: str) -> str:
    """http://id.who.int/icd/release/11/2026-01/mms/761947693/unspecified → 761947693/unspecified"""
    m = re.search(r"/mms/(.+)$", uri.strip())
    return m.group(1) if m else ""


def _quitar_sangria(t: str) -> str:
    return re.sub(r"^(?:-\s)+", "", t.strip().strip('"')).strip()


def leer_cie11(zip_ruta: Path) -> tuple[list[dict], str]:
    with zipfile.ZipFile(zip_ruta) as z:
        nombre = next(n for n in z.namelist() if n.endswith(".txt") and "SimpleTabulation" in n)
        texto = z.read(nombre).decode("utf-8-sig")
    lineas = texto.split("\r\n")
    cab = lineas[0].split("\t")
    version = next((c.split(":", 1)[1].strip() for c in cab if c.startswith("Version:")), "")
    # Las notas de codificación pueden traer saltos de línea sin comillas: se
    # juntan líneas hasta completar las 20 columnas.
    filas, acum = [], ""
    for ln in lineas[1:]:
        if not ln and not acum:
            continue
        acum = f"{acum}\n{ln}" if acum else ln
        if len(acum.split("\t")) >= 20:
            filas.append(acum)
            acum = ""
    salida = []
    for f in filas:
        c = next(csv.reader(io.StringIO(f.replace("\n", " ")), delimiter="\t"))
        c = [x.replace(" ", "\n") for x in c]
        lin = _lin_id(c[1])
        salida.append({
            "id": lin,
            "uri_fundacion": c[0] or None,
            "uri_linearizacion": c[1],
            "codigo": c[2] or None,
            "bloque_id": c[3] or None,
            "titulo": _quitar_sangria(c[5]),
            "tipo": {"chapter": "capitulo", "block": "bloque", "category": "categoria"}[c[6]],
            "nivel_en_tipo": int(c[7] or 0),
            "residual": c[8] == "True",
            "capitulo": c[9],
            "hoja": c[11] == "True",
            "nota": c[18].strip().strip('"') or None,
            "padre_fundacion": c[19].strip() or None,
            "agrupaciones": [g for g in c[13:18] if g],
            "sangria": len(re.match(r"^((?:-\s)*)", c[5].strip().strip('"')).group(1)) // 2,
        })
    return salida, version


# ---------------------------------------------------------------- CIE-10

def _codigo10(clave: str) -> str:
    clave = clave.strip().upper()
    if len(clave) == 4 and clave[3] == "X":
        return clave[:3]
    if len(clave) == 4:
        return f"{clave[:3]}.{clave[3]}"
    return clave


def leer_cie10(zip_ruta: Path) -> list[dict]:
    with zipfile.ZipFile(zip_ruta) as z:
        nombre = next(n for n in z.namelist() if n.upper().endswith("CATCIE10.CSV"))
        crudo = z.read(nombre)
    texto = crudo.decode("cp1252")
    lector = csv.DictReader(io.StringIO(texto), delimiter="|")
    salida = {}
    for r in lector:
        clave = (r["CLAVE"] or "").strip()
        if not clave or r["RUBRICA_TYPE"].strip() == "B":   # B = código dado de baja
            continue
        cod = _codigo10(clave)
        if not re.fullmatch(r"[A-Z]\d\d(\.\d)?", cod):     # p. ej. «888», relleno del catálogo
            continue
        previo = salida.get(cod)
        # A09 y A09X quedan los dos como «A09»: gana el vigente (VALID) y, si
        # ninguno lo es, la categoría sin X (la X es la forma vieja de una
        # categoría sin subdivisiones).
        if previo and (previo["valido"] or r["VALID"].strip() != "SI") and clave.endswith("X"):
            continue
        salida[cod] = ({
            "codigo": cod,
            "clave_dgis": clave,
            "titulo": re.sub(r"\s{2,}", " ", r["NOMBRE"].strip()),
            "capitulo": r["CLAVE_CAPITULO"].strip(),
            "capitulo_titulo": r["CAPITULO"].strip(),
            "valido": r["VALID"].strip() == "SI",
            "rubrica": r["RUBRICA_TYPE"].strip(),
            "modificacion": r["YEAR_MODIFI"].strip(),
            "anio_aplicacion": r["YEAR_APLICACION"].strip(),
            "daga": r.get("DAGA", "").strip() == "SI",
            "asterisco": r.get("ASTERISCO", "").strip() == "SI",
        })
    return list(salida.values())


def leer_grupos_ops(pdf: Path) -> list[dict]:
    """Títulos de grupo de la «Lista de tres caracteres» del Vol. 1 de la OPS."""
    txt = FUENTES.parent / "tmp_vol1.txt"
    subprocess.run(["pdftotext", "-layout", str(pdf), str(txt)], check=True)
    lineas = txt.read_text().splitlines()
    txt.unlink()
    # La marca de agua sale como fragmentos sueltos que se repiten cientos de veces.
    frecuencia = Counter(l.strip() for l in lineas)
    ruido = {k for k, v in frecuencia.items() if v > 150 and not re.match(r"^[A-Z]\d\d", k)}
    ini = next(i for i, l in enumerate(lineas) if "LISTA DE TRES CARACTERES" in l)
    fin = next(i for i, l in enumerate(lineas[ini:], ini) if "lista tabular de inclusiones" in l.lower())
    grupos, acum, capitulo = [], [], False
    rango = re.compile(r"\(([A-Z]\d\d)[–-]([A-Z]\d\d)\)\s*$")
    for l in lineas[ini:fin]:
        s = l.strip()
        if not s or s in ruido or len(s) < 3 or s.isdigit() or (s.isupper() and not re.search(r"\d", s)):
            continue                                         # marca de agua, encabezados, folios
        if not l.startswith("   ") or l.startswith("    "):   # sangría 3 = títulos
            acum = []
            continue
        if re.match(r"^[A-Z]\d\d\s", s):                       # una categoría
            acum = []
            continue
        if s.startswith("Capítulo "):
            capitulo, acum = True, []
            continue
        acum.append(s)
        m = rango.search(s)
        if m:
            titulo = " ".join(acum)
            titulo = re.sub(r"-\s+(?=[a-záéíóúñ])", "", titulo)   # cortes de sílaba
            titulo = rango.sub("", titulo).strip()
            if capitulo:
                capitulo = False                              # era el título del capítulo
            elif titulo:
                grupos.append({"codigo": f"{m.group(1)}-{m.group(2)}", "titulo": titulo})
            acum = []
    # Si un rango se repite (U82-U85 aparece como sección y como grupo), queda el
    # último, que es el más específico.
    unicos = {}
    for g in grupos:
        unicos[g["codigo"]] = g
    return list(unicos.values())


# ---------------------------------------------------------------- tablas OMS

def leer_mapeo(zip_ruta: Path) -> dict:
    datos = {}
    with zipfile.ZipFile(zip_ruta) as z:
        for clave, nombre in (("uno", "10To11MapToOneCategory.txt"),
                              ("varios", "10To11MapToMultipleCategories.txt"),
                              ("inversa", "11To10MapToOneCategory.txt")):
            n = next(x for x in z.namelist() if x.endswith(nombre) and "foundation" not in x)
            texto = z.read(n).decode("utf-8-sig")
            filas = list(csv.reader(io.StringIO(texto), delimiter="\t"))
            datos[clave] = filas[1:]
            datos[clave + "_cab"] = filas[0]
    return datos


# ---------------------------------------------------------------- base

ESQUEMA = """
CREATE TABLE meta (clave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE fuentes (archivo TEXT PRIMARY KEY, descripcion TEXT, url TEXT, bytes INT, sha256 TEXT, bajado TEXT);
CREATE TABLE cie10 (
  codigo TEXT PRIMARY KEY, codigo_compacto TEXT, titulo TEXT NOT NULL, tipo TEXT NOT NULL,
  nivel INT, capitulo TEXT, grupo TEXT, padre TEXT, valido INT, daga INT, asterisco INT,
  modificacion TEXT, anio_aplicacion TEXT, en_tabla_oms INT, orden INT);
CREATE TABLE cie11 (
  id TEXT PRIMARY KEY, codigo TEXT, bloque_id TEXT, titulo TEXT NOT NULL, tipo TEXT NOT NULL,
  nivel INT, capitulo TEXT, padre TEXT, residual INT, hoja INT, nota TEXT,
  uri_fundacion TEXT, uri_linearizacion TEXT, orden INT);
CREATE TABLE equivalencias (
  sentido TEXT NOT NULL, origen TEXT NOT NULL, destino TEXT, destino_id TEXT,
  tipo TEXT NOT NULL, principal INT, ida_y_vuelta INT, postcoordinacion TEXT,
  prioridad INT, puntaje INT, tabla TEXT, observaciones TEXT);
CREATE INDEX cie10_padre ON cie10(padre);
CREATE INDEX cie10_cap ON cie10(capitulo, orden);
CREATE INDEX cie10_compacto ON cie10(codigo_compacto);
CREATE INDEX cie11_codigo ON cie11(codigo);
CREATE INDEX cie11_padre ON cie11(padre);
CREATE INDEX cie11_cap ON cie11(capitulo, orden);
CREATE INDEX cie11_bloque ON cie11(bloque_id);
CREATE INDEX eq_origen ON equivalencias(sentido, origen, prioridad);
CREATE INDEX eq_destino ON equivalencias(sentido, destino);
CREATE VIRTUAL TABLE cie10_fts USING fts5(codigo UNINDEXED, texto, tokenize='unicode61 remove_diacritics 1');
CREATE VIRTUAL TABLE cie11_fts USING fts5(id UNINDEXED, texto, tokenize='unicode61 remove_diacritics 1');
CREATE TABLE vocabulario (sistema TEXT, palabra TEXT, veces INT, PRIMARY KEY (sistema, palabra)) WITHOUT ROWID;
"""

ROMANOS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII",
           "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX", "XXI", "XXII"]


def armar(args) -> dict:
    fuentes = indice_fuentes()
    cie11, version11 = leer_cie11(FUENTES / "SimpleTabulation-ICD-11-MMS-es.zip")
    cie10 = leer_cie10(FUENTES / "dgis_sectorial_Catalogos_egresos_2018_2024.zip")
    grupos = leer_grupos_ops(FUENTES / "CIE-10_2018_VOL1.pdf")
    mapeo = leer_mapeo(FUENTES / "mapping.zip")
    version_mapeo = mapeo["uno_cab"][-1]

    tmp = DB.with_suffix(".db.tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(ESQUEMA)

    # ---------- CIE-11
    por_fundacion = {}
    for e in cie11:
        if e["uri_fundacion"] and "/" not in e["id"]:
            por_fundacion[e["uri_fundacion"]] = e["id"]
    ids11 = {e["id"] for e in cie11}
    por_codigo11 = {e["codigo"]: e["id"] for e in cie11 if e["codigo"]}
    for orden, e in enumerate(cie11):
        padre = por_fundacion.get(e["padre_fundacion"]) if e["padre_fundacion"] else None
        if padre is None and e["padre_fundacion"]:
            m = re.search(r"/entity/(\d+)$", e["padre_fundacion"])
            padre = m.group(1) if m and m.group(1) in ids11 else None
        e["padre"] = padre
        con.execute("INSERT INTO cie11 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            e["id"], e["codigo"], e["bloque_id"], e["titulo"], e["tipo"], e["sangria"],
            e["capitulo"], padre, int(e["residual"]), int(e["hoja"]), e["nota"],
            e["uri_fundacion"], e["uri_linearizacion"], orden))
        con.execute("INSERT INTO cie11_fts VALUES (?,?)",
                    (e["id"], normalizar(f'{e["codigo"] or ""} {e["titulo"]}')))

    # ---------- CIE-10: capítulos, grupos, categorías y subcategorías
    capitulos = {}
    for c in cie10:
        capitulos.setdefault(c["capitulo"], c["capitulo_titulo"])
    rangos_cap = defaultdict(list)
    for c in cie10:
        rangos_cap[c["capitulo"]].append(c["codigo"][:3])
    filas10 = []
    for cap in sorted(capitulos, key=lambda r: ROMANOS.index(r) if r in ROMANOS else 99):
        filas10.append({"codigo": cap, "titulo": capitulos[cap], "tipo": "capitulo", "nivel": 0,
                        "capitulo": cap, "grupo": None, "padre": None})

    def grupo_de(cod3: str) -> str | None:
        # el grupo más chico que contiene la categoría (los grupos se anidan: C00-C97 > C00-C14)
        candidatos = [g for g in grupos if g["codigo"][:3] <= cod3 <= g["codigo"][4:]]
        if not candidatos:
            return None
        return min(candidatos, key=lambda g: (int(g["codigo"][5:]) - int(g["codigo"][1:3])) +
                   (ord(g["codigo"][4]) - ord(g["codigo"][0])) * 100)["codigo"]

    def grupo_padre(g: dict) -> str | None:
        contienen = [o for o in grupos if o is not g and o["codigo"][:3] <= g["codigo"][:3]
                     and g["codigo"][4:] <= o["codigo"][4:]]
        if not contienen:
            return None
        return min(contienen, key=lambda o: (ord(o["codigo"][4]) - ord(o["codigo"][0])) * 100 +
                   int(o["codigo"][5:]) - int(o["codigo"][1:3]))["codigo"]

    cap_de_cod3 = {c["codigo"][:3]: c["capitulo"] for c in cie10}
    for g in grupos:
        cap = cap_de_cod3.get(g["codigo"][:3])
        filas10.append({"codigo": g["codigo"], "titulo": g["titulo"], "tipo": "grupo",
                        "nivel": None, "capitulo": cap, "grupo": grupo_padre(g),
                        "padre": grupo_padre(g) or cap})
    for c in cie10:
        cod = c["codigo"]
        es_sub = "." in cod
        filas10.append({**c, "tipo": "subcategoria" if es_sub else "categoria",
                        "grupo": grupo_de(cod[:3]),
                        "padre": cod[:3] if es_sub else (grupo_de(cod[:3]) or c["capitulo"])})
    # nivel: profundidad real
    idx10 = {f["codigo"]: f for f in filas10}

    def profundidad(f):
        n, p = 0, f["padre"]
        while p and p in idx10 and n < 10:
            n, p = n + 1, idx10[p]["padre"]
        return n
    codigos_oms10 = {r[2] for r in mapeo["uno"]} | {r[2] for r in mapeo["varios"]} | \
        {r[4] for r in mapeo["inversa"]}
    for orden, f in enumerate(sorted(filas10, key=lambda f: (
            ROMANOS.index(f["capitulo"]) if f["capitulo"] in ROMANOS else 99,
            0 if f["tipo"] == "capitulo" else 1,
            f["codigo"][:3] if f["tipo"] != "capitulo" else "",
            0 if f["tipo"] == "grupo" else 1,
            -(int(f["codigo"][5:]) - int(f["codigo"][1:3]) + (ord(f["codigo"][4]) - ord(f["codigo"][0])) * 100)
            if f["tipo"] == "grupo" else 0,
            f["codigo"]))):
        f["nivel"] = profundidad(f)
        con.execute("INSERT INTO cie10 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            f["codigo"], f["codigo"].replace(".", ""), f["titulo"], f["tipo"], f["nivel"],
            f["capitulo"], f.get("grupo"), f["padre"],
            int(f.get("valido", False)), int(f.get("daga", False)), int(f.get("asterisco", False)),
            f.get("modificacion") or None, f.get("anio_aplicacion") or None,
            int(f["codigo"] in codigos_oms10), orden))
        con.execute("INSERT INTO cie10_fts VALUES (?,?)",
                    (f["codigo"], normalizar(f'{f["codigo"]} {f["codigo"].replace(".", "")} {f["titulo"]}')))

    # ---------- equivalencias oficiales
    # 11→10 (una por entidad CIE-11) y su inversa para saber si hay ida y vuelta
    inv = {}
    for r in mapeo["inversa"]:
        lid = _lin_id(r[0])
        inv[lid] = r[4].strip()
    uno = {}
    for r in mapeo["uno"]:
        uno.setdefault(r[2], r)
    varios = defaultdict(list)
    for r in mapeo["varios"]:
        varios[r[2]].append(r)

    def destino11(r) -> tuple[str | None, str | None, str | None]:
        """(código base, id de linearización, clúster postcoordinado)"""
        lid = _lin_id(r[8])
        lid = lid.replace(f"11/{VERSION_CIE11}/mms/", "")
        cod = r[9].strip() or None
        cluster = None
        if cod and re.search(r"[&/]", cod):
            cluster = cod
            cod = re.split(r"[&/]", cod)[0]
        if lid not in ids11 and cod in por_codigo11:
            lid = por_codigo11[cod]
        return cod, lid if lid in ids11 else None, cluster

    sin_equivalente_10 = []
    origenes10 = sorted(set(uno) | set(varios))
    for cod10 in origenes10:
        principal = uno.get(cod10)
        filas = varios.get(cod10) or ([principal] if principal else [])
        vistos, destinos = set(), []
        for r in filas:
            d = destino11(r)
            clave = d[2] or d[1] or d[0]
            if not clave or clave in vistos:
                continue
            vistos.add(clave)
            destinos.append((r, d))
        if principal:
            dp = destino11(principal)
            clave_p = dp[2] or dp[1] or dp[0]
            if clave_p and clave_p not in vistos:
                destinos.insert(0, (principal, dp))
        else:
            clave_p = None
        if not destinos:
            sin_equivalente_10.append(cod10)
            con.execute("INSERT INTO equivalencias VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                "10a11", cod10, None, None, "sin_equivalente", 0, 0, None, 1, 0,
                "10To11MapToOneCategory", "La tabla oficial no trae destino para este código."))
            continue
        modificada = (principal or filas[0])[0] == "modifiedcategory"
        filas_eq = []
        for r, (cod11, lid, cluster) in destinos:
            es_principal = (cluster or lid or cod11) == clave_p
            vuelta = bool(lid) and inv.get(lid) == cod10
            agrupacion = not cod11
            if len(destinos) == 1:
                tipo = "equivalente" if vuelta and not modificada and not cluster else "aproximado"
            else:
                tipo = "uno_a_varios"
            if cluster:
                tipo_final = "combinacion" if len(destinos) == 1 else tipo
            else:
                tipo_final = tipo
            puntaje = (100 if tipo_final == "equivalente" else
                       (90 if vuelta else 75) if es_principal else (60 if vuelta else 50))
            if agrupacion:
                puntaje -= 15
            obs = []
            if modificada:
                obs.append("La OMS ajustó el código CIE-10 para la correspondencia (modified category): "
                           "el destino es más amplio o más específico que el origen.")
            if cluster:
                obs.append("Requiere postcoordinación: el destino combina un código base con códigos "
                           "de extensión de la CIE-11.")
            if agrupacion:
                obs.append("El destino es una agrupación de la CIE-11 (no lleva código).")
            filas_eq.append([("10a11", cod10, cod11, lid, tipo_final, int(es_principal), int(vuelta),
                              cluster, 0, puntaje,
                              "10To11MapToOneCategory" if es_principal else "10To11MapToMultipleCategories",
                              " ".join(obs) or None)])
        filas_eq.sort(key=lambda x: (-x[0][9], -x[0][5]))
        for prio, (fila,) in enumerate(filas_eq, 1):
            fila = list(fila)
            fila[8] = prio
            con.execute("INSERT INTO equivalencias VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", fila)

    # principal 10→11 por destino, para la ida y vuelta de 11→10
    principal_de = defaultdict(set)
    for cod10, r in uno.items():
        d = destino11(r)
        if d[1]:
            principal_de[d[1]].add(cod10)
    varios_por_lid = defaultdict(set)
    for cod10, rs in varios.items():
        for r in rs:
            d = destino11(r)
            if d[1]:
                varios_por_lid[d[1]].add(cod10)

    sin_equivalente_11 = []
    for r in mapeo["inversa"]:
        lid = _lin_id(r[0])
        if lid not in ids11:
            continue
        cod10 = r[4].strip()
        filas_eq = []
        if not cod10 or cod10.lower() == "no mapping":
            sin_equivalente_11.append(lid)
            filas_eq.append(("11a10", lid, None, None, "sin_equivalente", 1, 0, None, 1, 0,
                             "11To10MapToOneCategory",
                             "La OMS indica «No Mapping»: es un concepto nuevo de la CIE-11 sin "
                             "correspondencia en la CIE-10."))
        else:
            agrupacion = "-" in cod10 or cod10 in ROMANOS
            vuelta = cod10 in principal_de.get(lid, set())
            tipo = "agrupacion" if agrupacion else ("equivalente" if vuelta else "aproximado")
            puntaje = 100 if tipo == "equivalente" else (60 if agrupacion else 80)
            filas_eq.append(("11a10", lid, cod10, cod10, tipo, 1, int(vuelta), None, 1, puntaje,
                             "11To10MapToOneCategory",
                             "El destino es un grupo o capítulo de la CIE-10, no una categoría."
                             if agrupacion else None))
        # otros análogos: códigos CIE-10 que la tabla oficial 10→11 lleva a esta entidad
        otros = sorted(o for o in varios_por_lid.get(lid, set()) - {cod10}
                       if "-" not in o and o not in ROMANOS)      # sólo categorías, no grupos
        for i, o in enumerate(otros, 2):
            filas_eq.append(("11a10", lid, o, o, "relacionado", 0, 0, None, i, 40,
                             "10To11MapToMultipleCategories",
                             "Según la tabla oficial CIE-10 → CIE-11, este código CIE-10 se lleva a la "
                             "entidad consultada (lectura inversa)."))
        for f in filas_eq:
            con.execute("INSERT INTO equivalencias VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", f)

    # ---------- vocabulario para tolerar errores de tipeo
    for sistema, tabla in (("cie10", "cie10_fts"), ("cie11", "cie11_fts")):
        cuenta = Counter()
        for (t,) in con.execute(f"SELECT texto FROM {tabla}"):
            cuenta.update(w for w in re.findall(r"[a-z0-9]+", t) if len(w) >= 4 and not w.isdigit())
        con.executemany("INSERT INTO vocabulario VALUES (?,?,?)",
                        [(sistema, w, n) for w, n in cuenta.items()])

    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {
        "version_datos": f"cie11-{VERSION_CIE11}+cie10-dgis-2025",
        "cie11_version": VERSION_CIE11,
        "cie11_version_archivo": version11,
        "tablas_version": version_mapeo,
        "cie10_fuente": "DGIS México, CATCIE10.csv (catálogos sectoriales de egresos 2018-2024, archivo del 2025-08-25)",
        "cie10_grupos_fuente": "OPS/OMS, CIE-10 Volumen 1, edición 2018: Lista de tres caracteres",
        "armado": ahora,
    }
    for k, v in meta.items():
        con.execute("INSERT INTO meta VALUES (?,?)", (k, v))
    for f in fuentes:
        con.execute("INSERT INTO fuentes VALUES (?,?,?,?,?,?)", (
            f["archivo"], f["descripcion"], f["url"], f["bytes"], f["sha256"], f["bajado"]))
    con.commit()

    informe = validar(con, mapeo, grupos)
    informe["meta"] = meta
    con.execute("INSERT INTO meta VALUES (?,?)", ("validacion", json.dumps(informe["conteos"])))
    con.commit()
    con.execute("PRAGMA optimize")
    con.execute("VACUUM")
    con.close()
    if informe["errores"] and not args.forzar:
        print("ERRORES de validación: la base nueva NO reemplaza a la anterior", file=sys.stderr)
        print(json.dumps(informe["errores"], ensure_ascii=False, indent=1), file=sys.stderr)
        sys.exit(1)
    os.replace(tmp, DB)
    VALIDACION.write_text(json.dumps(informe, ensure_ascii=False, indent=1) + "\n")
    return informe


# ---------------------------------------------------------------- validación

# Casos conocidos, comparados a mano contra icd.who.int (CIE-11 2026-01) y las
# tablas de la OMS. Si una versión nueva los cambia, revisar y actualizar acá.
CASOS = [
    ("10a11", "E11", "5A11", "Diabetes mellitus tipo 2"),
    ("10a11", "E11.9", "5A11", None),
    ("10a11", "I10", "BA00.Z", "Hipertensión esencial, sin especificación"),
    ("10a11", "U07.1", "RA01.0", "COVID-19, virus identificado"),
    ("10a11", "U07.2", "RA01.1", None),
    ("10a11", "J45.9", "CA23.32", "Asma no especificada sin complicaciones"),
    ("10a11", "F32.9", "6A7Z", None),
    ("10a11", "A00", "1A00", "Cólera"),
    ("11a10", "5A11", "E11", None),
    ("11a10", "BA00.Z", "I10", None),
    ("11a10", "RA01.0", "U07.1", None),
    ("11a10", "CA23.3", "J45.9", None),
    ("11a10", "6A70.Z", "F32", None),
]
TITULOS10 = {"E11": "DIABETES MELLITUS TIPO 2", "I10": "HIPERTENSIÓN ESENCIAL (PRIMARIA)",
             "U07.1": "COVID-19, VIRUS IDENTIFICADO", "J45.9": "ASMA, NO ESPECIFICADO",
             "F32.9": "EPISODIO DEPRESIVO, NO ESPECIFICADO"}


def validar(con: sqlite3.Connection, mapeo: dict, grupos: list) -> dict:
    q = lambda s, *a: con.execute(s, a).fetchall()
    errores, avisos = [], []
    conteos = {
        "cie10_total": q("SELECT count(*) FROM cie10")[0][0],
        "cie10_capitulos": q("SELECT count(*) FROM cie10 WHERE tipo='capitulo'")[0][0],
        "cie10_grupos": q("SELECT count(*) FROM cie10 WHERE tipo='grupo'")[0][0],
        "cie10_categorias": q("SELECT count(*) FROM cie10 WHERE tipo='categoria'")[0][0],
        "cie10_subcategorias": q("SELECT count(*) FROM cie10 WHERE tipo='subcategoria'")[0][0],
        "cie10_validos_para_codificar": q("SELECT count(*) FROM cie10 WHERE valido=1")[0][0],
        "cie11_total": q("SELECT count(*) FROM cie11")[0][0],
        "cie11_capitulos": q("SELECT count(*) FROM cie11 WHERE tipo='capitulo'")[0][0],
        "cie11_bloques": q("SELECT count(*) FROM cie11 WHERE tipo='bloque'")[0][0],
        "cie11_categorias": q("SELECT count(*) FROM cie11 WHERE tipo='categoria'")[0][0],
        "cie11_con_codigo": q("SELECT count(*) FROM cie11 WHERE codigo IS NOT NULL")[0][0],
        "equivalencias_10a11": q("SELECT count(*) FROM equivalencias WHERE sentido='10a11'")[0][0],
        "equivalencias_11a10": q("SELECT count(*) FROM equivalencias WHERE sentido='11a10'")[0][0],
    }
    for sentido in ("10a11", "11a10"):
        conteos[f"tipos_{sentido}"] = dict(q(
            "SELECT tipo, count(*) FROM equivalencias WHERE sentido=? GROUP BY tipo", sentido))
    # cobertura sobre los códigos en español
    conteos["cobertura_cie10"] = {
        "codigos_con_codigo": q("SELECT count(*) FROM cie10 WHERE tipo IN ('categoria','subcategoria')")[0][0],
        "con_equivalencia_oficial": q(
            "SELECT count(*) FROM cie10 c WHERE tipo IN ('categoria','subcategoria') AND EXISTS("
            "SELECT 1 FROM equivalencias e WHERE e.sentido='10a11' AND e.origen=c.codigo AND e.tipo<>'sin_equivalente')")[0][0],
        "validos_con_equivalencia": q(
            "SELECT count(*) FROM cie10 c WHERE valido=1 AND EXISTS("
            "SELECT 1 FROM equivalencias e WHERE e.sentido='10a11' AND e.origen=c.codigo AND e.tipo<>'sin_equivalente')")[0][0],
    }
    conteos["cobertura_cie11"] = {
        "codigos": conteos["cie11_con_codigo"],
        "codigos_fuera_cap_X_V_26": q(
            "SELECT count(*) FROM cie11 WHERE codigo IS NOT NULL AND capitulo NOT IN ('X','V','26')")[0][0],
        "con_equivalencia_oficial": q(
            "SELECT count(*) FROM cie11 c WHERE codigo IS NOT NULL AND EXISTS("
            "SELECT 1 FROM equivalencias e WHERE e.sentido='11a10' AND e.origen=c.id AND e.tipo<>'sin_equivalente')")[0][0],
        "no_mapping_oms": q("SELECT count(*) FROM equivalencias WHERE sentido='11a10' AND tipo='sin_equivalente'")[0][0],
    }
    # integridad: todo código de las tablas existe en su clasificación
    falt10 = [r[0] for r in q(
        "SELECT DISTINCT origen FROM equivalencias WHERE sentido='10a11' AND origen NOT IN (SELECT codigo FROM cie10)")]
    falt10b = [r[0] for r in q(
        "SELECT DISTINCT destino FROM equivalencias WHERE sentido='11a10' AND destino IS NOT NULL "
        "AND destino NOT IN (SELECT codigo FROM cie10)")]
    falt11 = q("SELECT count(*) FROM equivalencias WHERE sentido='10a11' AND tipo<>'sin_equivalente' "
               "AND (destino_id IS NULL OR destino_id NOT IN (SELECT id FROM cie11))")[0][0]
    conteos["tabla_oms_cie10_sin_titulo_es"] = sorted(set(falt10) | set(falt10b))
    if falt11:
        errores.append(f"{falt11} destinos CIE-11 de la tabla no existen en la tabulación en español")
    huerfanos = q("SELECT count(*) FROM cie10 WHERE padre IS NOT NULL AND padre NOT IN (SELECT codigo FROM cie10)")[0][0]
    if huerfanos:
        errores.append(f"{huerfanos} códigos CIE-10 con padre inexistente")
    huer11 = q("SELECT count(*) FROM cie11 WHERE tipo<>'capitulo' AND (padre IS NULL OR padre NOT IN (SELECT id FROM cie11))")[0][0]
    if huer11:
        errores.append(f"{huer11} entidades CIE-11 sin padre")
    sin_grupo = q("SELECT count(*) FROM cie10 WHERE tipo='categoria' AND grupo IS NULL")[0][0]
    if sin_grupo:
        avisos.append(f"{sin_grupo} categorías CIE-10 sin grupo (título de grupo OPS no encontrado)")
    bloques_oms = {r[2] for r in mapeo["uno"] if r[0] == "block"}
    faltan_grupos = sorted(bloques_oms - {g["codigo"] for g in grupos})
    conteos["grupos_oms_sin_titulo_es"] = faltan_grupos
    if len(faltan_grupos) > 40:
        errores.append(f"faltan {len(faltan_grupos)} grupos de la OMS en la lista de la OPS")
    for t in ("cie10", "cie11"):
        if not q(f"SELECT count(*) FROM {t}")[0][0]:
            errores.append(f"tabla {t} vacía")
    if conteos["cie10_total"] < 14000 or conteos["cie11_con_codigo"] < 30000:
        errores.append("conteos por debajo de lo esperado")
    # casos conocidos
    casos = []
    for sentido, origen, esperado, titulo in CASOS:
        if sentido == "10a11":
            fila = q("SELECT e.destino, c.titulo FROM equivalencias e LEFT JOIN cie11 c ON c.id=e.destino_id "
                     "WHERE e.sentido='10a11' AND e.origen=? ORDER BY prioridad LIMIT 1", origen)
        else:
            fila = q("SELECT e.destino, c.titulo FROM equivalencias e JOIN cie11 o ON o.id=e.origen "
                     "LEFT JOIN cie10 c ON c.codigo=e.destino WHERE e.sentido='11a10' AND o.codigo=? "
                     "ORDER BY prioridad LIMIT 1", origen)
        obtenido = fila[0] if fila else (None, None)
        ok = obtenido[0] == esperado and (titulo is None or obtenido[1] == titulo)
        casos.append({"sentido": sentido, "origen": origen, "esperado": esperado,
                      "obtenido": obtenido[0], "titulo": obtenido[1], "ok": ok})
        if not ok:
            errores.append(f"caso {sentido} {origen}: esperado {esperado} «{titulo}», obtenido {obtenido}")
    for cod, tit in TITULOS10.items():
        r = q("SELECT titulo FROM cie10 WHERE codigo=?", cod)
        if not r or r[0][0] != tit:
            errores.append(f"título CIE-10 {cod}: esperado «{tit}», obtenido {r}")
    return {"conteos": conteos, "casos": casos, "errores": errores, "avisos": avisos}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bajar", action="store_true", help="volver a bajar las fuentes aunque estén")
    ap.add_argument("--forzar", action="store_true", help="instalar la base aunque falle la validación")
    args = ap.parse_args()
    bajar(args.bajar)
    informe = armar(args)
    print(json.dumps({k: informe[k] for k in ("conteos", "errores", "avisos")}, ensure_ascii=False, indent=1))
    print("casos:", sum(c["ok"] for c in informe["casos"]), "/", len(informe["casos"]))


if __name__ == "__main__":
    main()
