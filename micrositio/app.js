/* Conversor CIE-10 ↔ CIE-11 — micrositio. Sin dependencias.
   Rutas relativas: funciona igual en :8135/ o publicado en una subcarpeta. */
(() => {
  "use strict";
  const API = "api/v1/";
  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, attrs = {}, ...hijos) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const h of hijos.flat(Infinity)) if (h !== null && h !== undefined && h !== false)
      n.append(h instanceof Node ? h : document.createTextNode(String(h)));
    return n;
  };
  const SIMBOLO = {equivalente: "●", aproximado: "≈", uno_a_varios: "⇉", combinacion: "⊕",
                   agrupacion: "▢", relacionado: "↔", sin_equivalente: "∅"};
  const NOMBRE = {cie10: "CIE-10", cie11: "CIE-11"};
  const otro = s => s === "cie10" ? "cie11" : "cie10";

  const entrada = $("#buscador"), lista = $("#sugerencias"), limpiar = $("#limpiar");
  const resultado = $("#resultado"), bienvenida = $("#bienvenida"), estado = $("#estado");
  let de = "cie10", activo = -1, opciones = [], pedido = null, temporizador = null, ultimaQ = "";

  // ------------------------------------------------------------ utilidades
  async function api(ruta, opciones = {}) {
    const r = await fetch(API + ruta, {headers: {"Accept": "application/json"}, ...opciones});
    let datos = null;
    try { datos = await r.json(); } catch (_) { /* sin cuerpo */ }
    if (!r.ok) {
      const e = new Error(datos && datos.error ? datos.error.mensaje : `Error ${r.status}`);
      e.estado = r.status; throw e;
    }
    return datos;
  }
  function tostada(texto) {
    const t = $("#tostada");
    t.textContent = texto; t.hidden = false;
    clearTimeout(tostada.t); tostada.t = setTimeout(() => { t.hidden = true; }, 2200);
  }
  async function copiar(texto, que = "Código") {
    try { await navigator.clipboard.writeText(texto); }
    catch (_) {
      const a = el("textarea", {class: "oculto"}); a.value = texto; document.body.append(a);
      a.select(); document.execCommand("copy"); a.remove();
    }
    tostada(`${que} copiado: ${texto}`);
  }
  const codigoDe = x => x.codigo || x.id;
  // Los id de la CIE-11 pueden traer barra (761947693/unspecified): va tal cual,
  // porque Apache rechaza %2F en la ruta salvo que el servidor lo habilite.
  const ruta_ = c => encodeURIComponent(c).replace(/%2F/gi, "/");
  const capituloTexto = c => c ? `Cap. ${c.codigo} · ${c.titulo}` : "";

  // ------------------------------------------------------------ sentido
  function ponerSentido(nuevo, {foco = false, conservar = false} = {}) {
    de = nuevo;
    for (const b of document.querySelectorAll(".sentido [role=radio]")) {
      const si = b.dataset.de === de;
      b.setAttribute("aria-checked", si ? "true" : "false");
      b.tabIndex = si ? 0 : -1;
    }
    $("#rotulo-buscador").textContent = `Código o palabra de la ${NOMBRE[de]}`;
    entrada.placeholder = de === "cie10"
      ? "Escribí un código CIE-10 (E11.9) o una palabra (diabetes)"
      : "Escribí un código CIE-11 (5A11) o una palabra (diabetes)";
    if (!conservar) { entrada.value = ""; cerrarLista(); limpiar.hidden = true; }
    if (foco) entrada.focus();
  }
  document.querySelectorAll(".sentido [role=radio]").forEach(b => {
    b.addEventListener("click", () => { ponerSentido(b.dataset.de, {foco: true}); });
    b.addEventListener("keydown", ev => {
      if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(ev.key)) {
        ev.preventDefault(); ponerSentido(otro(de)); $(`.sentido [data-de=${de}]`).focus();
      }
    });
  });
  $("#invertir").addEventListener("click", () => ponerSentido(otro(de), {foco: true}));

  // ------------------------------------------------------------ sugerencias (combobox)
  function cerrarLista() {
    lista.hidden = true; lista.replaceChildren(); opciones = []; activo = -1;
    entrada.setAttribute("aria-expanded", "false"); entrada.removeAttribute("aria-activedescendant");
  }
  function marcar(i) {
    opciones.forEach((o, j) => o.setAttribute("aria-selected", j === i ? "true" : "false"));
    activo = i;
    if (i >= 0) { entrada.setAttribute("aria-activedescendant", opciones[i].id); opciones[i].scrollIntoView({block: "nearest"}); }
    else entrada.removeAttribute("aria-activedescendant");
  }
  async function sugerir(q) {
    if (pedido) pedido.abort();
    pedido = new AbortController();
    const sistema = de;
    try {
      const r = await fetch(`${API}${sistema}/buscar?q=${encodeURIComponent(q)}&por_pagina=12`,
                            {signal: pedido.signal});
      const d = await r.json();
      if (!r.ok) throw new Error(d.error ? d.error.mensaje : "error");
      if (q !== entrada.value.trim() || sistema !== de) return;
      lista.replaceChildren();
      if (d.correccion) lista.append(el("li", {class: "corr", role: "presentation"}, `Mostrando resultados para «${d.correccion}»`));
      opciones = d.resultados.map((x, i) => {
        const li = el("li", {id: `op-${i}`, role: "option", "aria-selected": "false"},
          el("span", {class: "cod"}, x.codigo || (x.tipo === "bloque" ? "bloque" : "—")),
          el("span", {class: "tit"}, x.titulo),
          el("span", {class: "cat"}, [x.grupo ? `${x.grupo.codigo} · ` : "", capituloTexto(x.capitulo)].join("")));
        li.addEventListener("mousedown", ev => ev.preventDefault());
        li.addEventListener("click", () => elegir(x));
        return li;
      });
      lista.append(...opciones);
      if (!opciones.length) lista.append(el("li", {class: "nada", role: "presentation"}, `Nada en la ${NOMBRE[sistema]} para «${q}».`));
      lista.hidden = false; entrada.setAttribute("aria-expanded", "true");
      estado.textContent = d.total ? `${d.total} resultados. Flechas para recorrer, Enter para elegir.` : "Sin resultados.";
      opciones._datos = d.resultados;
      marcar(opciones.length ? 0 : -1);
    } catch (e) {
      if (e.name !== "AbortError") { cerrarLista(); estado.textContent = "No se pudo buscar."; }
    }
  }
  entrada.addEventListener("input", () => {
    const q = entrada.value.trim();
    limpiar.hidden = !entrada.value;
    clearTimeout(temporizador);
    if (!q) { cerrarLista(); return; }
    temporizador = setTimeout(() => sugerir(q), 140);
  });
  entrada.addEventListener("keydown", ev => {
    if (ev.key === "ArrowDown") { ev.preventDefault(); if (lista.hidden && entrada.value.trim()) sugerir(entrada.value.trim()); else if (opciones.length) marcar((activo + 1) % opciones.length); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); if (opciones.length) marcar((activo - 1 + opciones.length) % opciones.length); }
    else if (ev.key === "Enter") {
      ev.preventDefault();
      if (!lista.hidden && activo >= 0 && opciones._datos) elegir(opciones._datos[activo]);
      else if (entrada.value.trim()) convertir(de, entrada.value.trim());
    }
    else if (ev.key === "Escape") { if (!lista.hidden) cerrarLista(); else { entrada.value = ""; limpiar.hidden = true; } }
  });
  entrada.addEventListener("blur", () => setTimeout(cerrarLista, 120));
  limpiar.addEventListener("click", () => { entrada.value = ""; limpiar.hidden = true; cerrarLista(); entrada.focus(); });
  document.querySelectorAll(".ejemplo").forEach(b => b.addEventListener("click", () => {
    entrada.value = b.dataset.q; limpiar.hidden = false; entrada.focus(); sugerir(b.dataset.q);
  }));

  function elegir(x) {
    cerrarLista();
    entrada.value = codigoDe(x);
    convertir(de, codigoDe(x));
  }

  // ------------------------------------------------------------ conversión
  function ruta(items) {
    if (!items || !items.length) return null;
    return el("ol", {class: "ruta", "aria-label": "Ubicación en la clasificación"},
      items.map(r => el("li", {}, r.codigo ? el("b", {}, r.codigo) : null, r.codigo ? " " : "", r.titulo)));
  }

  async function convertir(sistema, codigo, {historial = true} = {}) {
    const sentido = sistema === "cie10" ? "cie10-a-cie11" : "cie11-a-cie10";
    resultado.hidden = false; bienvenida.hidden = true;
    resultado.classList.add("cargando");
    try {
      const d = await api(`convertir/${sentido}/${ruta_(codigo)}`);
      pintar(sistema, d);
      const url = new URL(location.href);
      url.search = `?de=${sistema}&q=${encodeURIComponent(codigoDe(d.origen))}`;
      if (historial && url.href !== location.href) history.pushState({de: sistema, q: codigoDe(d.origen)}, "", url);
      document.title = `${d.origen.codigo || ""} ${d.origen.titulo || ""} · ${NOMBRE[sistema]} → ${NOMBRE[otro(sistema)]}`.trim();
      ultimaQ = codigoDe(d.origen);
    } catch (e) {
      resultado.replaceChildren(el("div", {class: "vacio"},
        e.estado === 404 ? `No encontré «${codigo}» en la ${NOMBRE[sistema]}. Probá escribir una palabra y elegir de la lista.`
                         : `No se pudo consultar: ${e.message}`));
    } finally {
      resultado.classList.remove("cargando");
    }
  }

  function tarjetaOrigen(sistema, o, d) {
    const acciones = el("div", {class: "acciones"},
      o.codigo ? el("button", {type: "button", class: "boton", onclick: () => copiar(o.codigo)}, "⧉ Copiar código") : null,
      el("button", {type: "button", class: "boton", onclick: () => copiar(location.href, "Enlace")}, "🔗 Copiar enlace"),
      el("button", {type: "button", class: "boton", onclick: () => abrirFicha(sistema, codigoDe(o))}, "Ver ficha"),
      o.navegador_oms ? el("a", {class: "boton", href: o.navegador_oms, target: "_blank", rel: "noopener"}, "Ver en la OMS ↗") : null);
    return el("article", {class: "origen", "aria-labelledby": "origen-tit"},
      el("div", {class: "sistema"}, `${NOMBRE[sistema]} · código elegido`),
      el("div", {class: "cod"}, o.codigo || "(sin código)"),
      el("p", {class: "tit", id: "origen-tit"}, o.titulo || "Sin título en español en la fuente"),
      ruta(o.ruta),
      o.uso_nacional ? el("p", {class: "nota alerta"}, o.uso_nacional) : null,
      o.extension ? el("p", {class: "nota"}, o.extension) : null,
      o.nota && !o.titulo ? el("p", {class: "nota"}, o.nota) : null,
      d.heredada_de ? el("p", {class: "nota alerta"}, `${d.heredada_de.motivo} (${d.heredada_de.codigo || ""} ${d.heredada_de.titulo || ""})`) : null,
      acciones);
  }

  function tarjetaEq(sistemaDestino, e, sugerencia = false) {
    const c = e.correspondencia;
    const tipo = sugerencia ? "sugerencia" : c.tipo;
    const rotulo = sugerencia
      ? el("span", {class: "rotulo t-sugerencia"}, el("i", {"aria-hidden": "true"}, "?"), "Sugerencia automática · no oficial")
      : el("span", {class: `rotulo t-${tipo}`, title: c.explicacion}, el("i", {"aria-hidden": "true"}, SIMBOLO[tipo] || ""), c.rotulo);
    const partes = e.postcoordinacion ? el("ul", {class: "partes", "aria-label": "Combinación"},
      el("li", {}, "Combinación: ", el("code", {}, e.postcoordinacion.cluster)),
      e.postcoordinacion.partes.map(p => el("li", {}, el("code", {}, p.codigo), " ", p.titulo || "", p.extension ? " (extensión)" : ""))) : null;
    const cod = codigoDe(e);
    const titulo = e.titulo || "Sin título en español en la fuente";
    return el("li", {class: `eq${!sugerencia && c.principal ? " principal" : ""}`},
      el("div", {class: "cab"},
        el("span", {class: "cod"}, e.codigo || "(agrupación)"),
        rotulo,
        !sugerencia && c.principal ? el("span", {class: "estrella"}, "principal") : null,
        !sugerencia && c.ida_y_vuelta && c.tipo !== "equivalente" ? el("span", {class: "estrella"}, "ida y vuelta") : null),
      sugerencia ? null : el("div", {class: "puntaje", title: "Orden de presentación, no es una medida clínica"},
        el("span", {class: "oculto"}, "Cercanía:"), el("span", {class: "barra", "aria-hidden": "true"}, barra(c.puntaje)), `${c.puntaje}/100`),
      el("p", {class: "tit"}, titulo),
      el("div", {class: "meta"}, capituloTexto(e.capitulo), ruta((e.ruta || []).slice(1))),
      partes,
      !sugerencia && c.observaciones ? el("p", {class: "obs"}, c.observaciones) : null,
      el("div", {class: "acciones"},
        cod ? el("button", {type: "button", class: "boton", onclick: () => abrirFicha(sistemaDestino, cod),
                            "aria-label": `Ver ficha de ${e.codigo || ""} ${titulo}`}, "Ver ficha") : null,
        cod ? el("button", {type: "button", class: "boton primario", onclick: () => invertir(sistemaDestino, cod),
                            "aria-label": `Ver los análogos ${NOMBRE[otro(sistemaDestino)]} de ${e.codigo || titulo}`},
                 `⇄ Sus análogos ${NOMBRE[otro(sistemaDestino)]}`) : null,
        e.codigo ? el("button", {type: "button", class: "boton", onclick: () => copiar(e.codigo),
                                 "aria-label": `Copiar el código ${e.codigo}`}, "⧉ Copiar") : null));
  }
  function barra(p) { const s = el("span"); s.style.width = `${Math.max(4, Math.min(100, p))}%`; return s; }

  function pintar(sistema, d) {
    const destino = otro(sistema);
    const partes = [tarjetaOrigen(sistema, d.origen, d)];
    partes.push(el("div", {class: "flecha"},
      el("h2", {}, `Análogos en la ${NOMBRE[destino]}`), el("span", {class: "linea"}),
      el("span", {class: "cuenta"}, d.total === 1 ? "1 resultado" : `${d.total} resultados`)));
    if (d.equivalencias.length) {
      partes.push(el("ul", {class: "equivalencias"}, d.equivalencias.map(e => tarjetaEq(destino, e))));
    } else {
      partes.push(el("div", {class: "vacio"},
        el("span", {class: "rotulo t-sin_equivalente"}, el("i", {"aria-hidden": "true"}, "∅"), "Sin equivalente"),
        el("p", {}, d.sin_equivalente_motivo || "Las tablas oficiales no traen correspondencia.")));
      const s = d.sugerencias_automaticas;
      if (s && s.resultados.length) {
        partes.push(el("section", {class: "sugerencias-auto", "aria-label": "Sugerencias automáticas no oficiales"},
          el("h3", {}, "Lo más parecido por texto — SUGERENCIA AUTOMÁTICA, NO OFICIAL"),
          el("p", {class: "obs"}, s.aviso),
          el("ul", {class: "equivalencias"}, s.resultados.map(x => tarjetaEq(destino, x, true)))));
      }
    }
    resultado.replaceChildren(...partes);
    estado.textContent = `${d.origen.codigo || ""}: ${d.total} análogos en la ${NOMBRE[destino]}.`;
  }

  function invertir(sistema, codigo) {
    ponerSentido(sistema);
    entrada.value = codigo; limpiar.hidden = false;
    convertir(sistema, codigo).then(() => resultado.scrollIntoView({behavior: "smooth", block: "start"}));
  }

  // ------------------------------------------------------------ ficha
  const dialogo = $("#ficha");
  $("#ficha-cerrar").addEventListener("click", () => dialogo.close());
  dialogo.addEventListener("click", ev => { if (ev.target === dialogo) dialogo.close(); });
  let volverA = null;
  dialogo.addEventListener("close", () => { if (volverA) volverA.focus(); });

  async function abrirFicha(sistema, codigo) {
    volverA = document.activeElement;
    const cuerpo = $("#ficha-cuerpo");
    cuerpo.replaceChildren(el("p", {}, "Cargando…"));
    $("#ficha-titulo").textContent = `${NOMBRE[sistema]} · ${codigo}`;
    if (!dialogo.open) dialogo.showModal();
    try {
      const f = await api(`${sistema}/${ruta_(codigo)}`);
      const filas = [
        ["Código", f.codigo || "—"], ["Título", f.titulo], ["Tipo", f.tipo],
        ["Capítulo", f.capitulo ? `${f.capitulo.codigo} · ${f.capitulo.titulo}` : "—"],
      ];
      if (f.grupo) filas.push(["Grupo", `${f.grupo.codigo} · ${f.grupo.titulo}`]);
      if (sistema === "cie10") filas.push(["Para codificar", f.valido_para_codificar ? "Sí" : "No (tiene subdivisiones o es una agrupación)"]);
      if (f.uri) filas.push(["URI (OMS)", f.uri]);
      if (f.historial_fuente) filas.push(["Historial en la fuente", f.historial_fuente]);
      const dl = el("dl", {}, filas.map(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
      const hijos = (f.hijos || []).length ? [el("h3", {}, `Contiene (${f.hijos.length})`),
        el("ul", {class: "hijos"}, f.hijos.map(h => el("li", {}, el("button", {type: "button", onclick: () => abrirFicha(sistema, codigoDe(h))},
          el("code", {}, h.codigo || "bloque"), el("span", {}, h.titulo)))))] : [];
      cuerpo.replaceChildren(
        ruta(f.ruta) || "", dl,
        f.nota_codificacion ? el("p", {class: "nota"}, el("b", {}, "Nota de codificación: "), f.nota_codificacion) : "",
        f.uso_nacional ? el("p", {class: "nota alerta"}, f.uso_nacional) : "",
        ...hijos,
        el("div", {class: "acciones"},
          el("button", {type: "button", class: "boton primario", onclick: () => { dialogo.close(); invertir(sistema, codigoDe(f)); }},
             `⇄ Ver sus análogos ${NOMBRE[otro(sistema)]}`),
          f.codigo ? el("button", {type: "button", class: "boton", onclick: () => copiar(f.codigo)}, "⧉ Copiar código") : "",
          f.navegador_oms ? el("a", {class: "boton", href: f.navegador_oms, target: "_blank", rel: "noopener"}, "Ver en la OMS ↗") : ""));
    } catch (e) {
      cuerpo.replaceChildren(el("p", {}, `No se pudo abrir la ficha: ${e.message}`));
    }
  }

  // ------------------------------------------------------------ enlace compartible
  function desdeUrl() {
    const p = new URLSearchParams(location.search);
    const d = p.get("de") === "cie11" ? "cie11" : "cie10";
    const q = (p.get("q") || "").trim().slice(0, 100);
    ponerSentido(d);
    if (q) { entrada.value = q; limpiar.hidden = false; convertir(d, q, {historial: false}); }
    else { resultado.hidden = true; bienvenida.hidden = false; }
  }
  window.addEventListener("popstate", desdeUrl);
  desdeUrl();

  api("meta").then(m => { $("#version").textContent = `Datos: CIE-11 ${m.cie11.version} · tablas ${m.tablas_de_correspondencia.version}`; }).catch(() => {});
})();
