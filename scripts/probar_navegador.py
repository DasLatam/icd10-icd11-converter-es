#!/usr/bin/python3
"""Prueba del micrositio con un navegador real (Playwright/Chromium), escritorio y celular.

    python3 scripts/probar_navegador.py [URL] [--capturas DIR] [--proxy socks5://...]

Recorre: escribir un código, ver sugerencias, elegir con el teclado, ver la tarjeta
y las equivalencias, cambiar de sentido, tocar una equivalencia («sus análogos»),
abrir la ficha, copiar el código, abrir un enlace compartido y la documentación.
Falla si hay errores de consola (incluidas violaciones de CSP).
"""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("url", nargs="?", default="http://127.0.0.1:8135/")
ap.add_argument("--capturas", default="/tmp/cie_capturas")
ap.add_argument("--proxy")
a = ap.parse_args()
BASE = a.url.rstrip("/") + "/"
CAP = Path(a.capturas)
CAP.mkdir(parents=True, exist_ok=True)

fallas = []


def paso(nombre, cond, detalle=""):
    print(("ok   " if cond else "FALLA"), nombre, detalle, flush=True)
    if not cond:
        fallas.append(nombre)


def recorrido(p, nombre, **ctx):
    lanzar = {"proxy": {"server": a.proxy}} if a.proxy else {}
    nav = p.chromium.launch(**lanzar)
    c = nav.new_context(**ctx)
    c.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE.rstrip("/"))
    pg = c.new_page()
    errores = []
    pg.on("console", lambda m: errores.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errores.append(str(e)))
    pg.goto(BASE, wait_until="networkidle")
    paso(f"{nombre}: carga la portada", pg.title().startswith("Conversor"))

    # escribir un código y ver sugerencias
    caja = pg.locator("#buscador")
    caja.click()
    caja.type("E11", delay=60)
    sug = pg.locator("#sugerencias [role=option]")
    expect(sug.first).to_be_visible(timeout=5000)
    paso(f"{nombre}: sugerencias al escribir E11", sug.count() >= 5, f"({sug.count()})")
    paso(f"{nombre}: la sugerencia muestra código y capítulo",
         "E11" in sug.first.inner_text() and "Cap." in sug.first.inner_text())
    pg.screenshot(path=str(CAP / f"{nombre}-1-sugerencias.png"))
    # bajar con flecha a E11.9 no es seguro por el orden: elegimos E11 con Enter
    caja.press("Enter")
    expect(pg.locator(".origen .cod")).to_have_text("E11", timeout=5000)
    tarjetas = pg.locator(".equivalencias .eq")
    paso(f"{nombre}: E11 → 5A11 equivalente", "5A11" in tarjetas.first.inner_text()
         and "Equivalente" in tarjetas.first.inner_text())
    paso(f"{nombre}: aviso de la OMS visible", pg.locator(".aviso").is_visible())
    paso(f"{nombre}: enlace compartible", "de=cie10" in pg.url and "q=E11" in pg.url, pg.url)
    pg.screenshot(path=str(CAP / f"{nombre}-2-E11.png"), full_page=True)

    # copiar código
    pg.locator(".origen button", has_text="Copiar código").click()
    if pg.evaluate("!!(navigator.clipboard && window.isSecureContext)"):
        copiado = pg.evaluate("navigator.clipboard.readText()")
    else:
        # http por IP (LAN): no hay API de portapapeles; la app usa execCommand
        # y avisa con la tostada.
        expect(pg.locator("#tostada")).to_be_visible()
        copiado = pg.locator("#tostada").inner_text().split(": ")[-1]
    paso(f"{nombre}: copiar código", copiado == "E11", repr(copiado))

    # tocar una equivalencia: sus análogos CIE-10 (invierte la búsqueda)
    tarjetas.first.locator("button", has_text="análogos").click()
    expect(pg.locator(".origen .cod")).to_have_text("5A11", timeout=5000)
    paso(f"{nombre}: invertir desde la tarjeta", pg.locator("#s11").get_attribute("aria-checked") == "true")
    paso(f"{nombre}: 5A11 → E11", "E11" in pg.locator(".equivalencias .eq").first.inner_text())
    pg.screenshot(path=str(CAP / f"{nombre}-3-5A11.png"), full_page=True)

    # ficha en diálogo
    pg.locator(".equivalencias .eq").first.locator("button", has_text="Ver ficha").click()
    expect(pg.locator("#ficha")).to_be_visible()
    expect(pg.locator("#ficha-cuerpo dl")).to_be_visible(timeout=5000)
    paso(f"{nombre}: ficha con ruta y datos", "Capítulo" in pg.locator("#ficha-cuerpo").inner_text())
    pg.screenshot(path=str(CAP / f"{nombre}-4-ficha.png"))
    pg.keyboard.press("Escape")
    expect(pg.locator("#ficha")).to_be_hidden()

    # cambiar de sentido con el conmutador y buscar por palabra (con error de tipeo)
    pg.locator("#s10").click()
    caja.fill("")
    caja.type("hipertencion esencial", delay=30)
    expect(sug.first).to_be_visible(timeout=5000)
    paso(f"{nombre}: tolera errores de tipeo", "I10" in pg.locator("#sugerencias").inner_text())
    pg.locator("#sugerencias [role=option]", has_text="I10").first.click()
    expect(pg.locator(".origen .cod")).to_have_text("I10", timeout=5000)
    paso(f"{nombre}: I10 → BA00.Z", "BA00.Z" in pg.locator(".equivalencias").inner_text())

    # enlace compartido directo
    pg.goto(BASE + "?de=cie11&q=RA01.0", wait_until="networkidle")
    expect(pg.locator(".origen .cod")).to_have_text("RA01.0", timeout=5000)
    paso(f"{nombre}: enlace ?de=cie11&q=RA01.0", "U07.1" in pg.locator(".equivalencias").inner_text())

    # un caso sin equivalente: sugerencias automáticas separadas
    pg.goto(BASE + "?de=cie11&q=XN8P1", wait_until="networkidle")
    expect(pg.locator(".origen .cod")).to_have_text("XN8P1", timeout=5000)
    paso(f"{nombre}: sin equivalente + sugerencia rotulada no oficial",
         pg.locator(".sugerencias-auto").count() == 1 and "NO OFICIAL" in pg.locator(".sugerencias-auto h3").inner_text())
    pg.screenshot(path=str(CAP / f"{nombre}-5-sin-equivalente.png"), full_page=True)

    # teclado: tab llega al buscador y al conmutador
    pg.goto(BASE, wait_until="networkidle")
    pg.keyboard.press("Tab")
    pg.keyboard.press("Enter")       # «Ir al buscador»
    paso(f"{nombre}: enlace de salto al buscador", pg.evaluate("document.activeElement.id") == "buscador")

    # documentación
    pg.goto(BASE + "api/docs/", wait_until="networkidle")
    expect(pg.locator(".ruta-api").first).to_be_visible(timeout=5000)
    paso(f"{nombre}: /api/docs/ arma la documentación", pg.locator(".ruta-api").count() >= 14)
    det = pg.locator(".ruta-api", has_text="cie10-a-cie11").first
    det.locator("summary").click()
    det.locator("button", has_text="Probar").click()
    expect(det.locator("pre")).to_contain_text("200", timeout=5000)
    paso(f"{nombre}: «Probar» desde la documentación", "5A11" in det.locator("pre").inner_text())
    pg.screenshot(path=str(CAP / f"{nombre}-6-docs.png"))

    paso(f"{nombre}: sin errores de consola", not errores, "; ".join(errores)[:300])
    nav.close()


with sync_playwright() as p:
    recorrido(p, "escritorio", viewport={"width": 1366, "height": 900})
    recorrido(p, "celular", viewport={"width": 390, "height": 844}, device_scale_factor=2,
              is_mobile=True, has_touch=True)

print(f"\n{'TODO OK' if not fallas else 'FALLAS: ' + ', '.join(fallas)} · capturas en {CAP}")
sys.exit(1 if fallas else 0)
