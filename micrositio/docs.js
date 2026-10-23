/* Documentación autoalojada: arma la página a partir de openapi.json (sin CDN). */
(() => {
  "use strict";
  const raiz = document.getElementById("doc");
  const el = (tag, attrs = {}, ...hijos) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    for (const h of hijos.flat(Infinity)) if (h !== null && h !== undefined && h !== false)
      n.append(h instanceof Node ? h : document.createTextNode(String(h)));
    return n;
  };
  const base = new URL("../../", location.href);   // raíz del sitio (relativa, sirve en subcarpeta)
  const md = t => {                               // markdown mínimo: párrafos, listas, **negrita**, `código`
    const frag = document.createDocumentFragment();
    for (const bloque of (t || "").split(/\n\n+/)) {
      const lineas = bloque.split("\n");
      const enLinea = s => {
        const p = document.createDocumentFragment();
        s.split(/(\*\*[^*]+\*\*|`[^`]+`)/).forEach(x => {
          if (x.startsWith("**")) p.append(el("b", {}, x.slice(2, -2)));
          else if (x.startsWith("`")) p.append(el("code", {}, x.slice(1, -1)));
          else p.append(x);
        });
        return p;
      };
      if (lineas.every(l => l.startsWith("- "))) frag.append(el("ul", {}, lineas.map(l => el("li", {}, enLinea(l.slice(2))))));
      else frag.append(el("p", {}, enLinea(bloque)));
    }
    return frag;
  };

  function ejemploRuta(ruta, params) {
    return ruta.replace(/\{(\w+)\}/g, (_, n) => {
      const p = (params || []).find(x => x.name === n);
      return p && p.example ? encodeURIComponent(p.example) : n;
    });
  }

  function probar(metodo, ruta, op) {
    const caja = el("div");
    const params = op.parameters || [];
    const enRuta = params.filter(p => p.in === "path");
    const enQuery = params.filter(p => p.in === "query");
    const campos = {};
    const form = el("div", {class: "probar"});
    [...enRuta, ...enQuery].forEach(p => {
      const id = `p-${metodo}-${ruta}-${p.name}`.replace(/[^\w-]/g, "_");
      const inp = el("input", {id, placeholder: p.name, value: p.example !== undefined ? String(p.example) : "", "aria-label": `${p.name} (${p.in === "path" ? "en la ruta" : "parámetro"})`});
      campos[p.name] = [p, inp];
      form.append(inp);
    });
    let cuerpo = null;
    if (metodo === "post") {
      const ej = op.requestBody && op.requestBody.content["application/json"].example;
      cuerpo = el("textarea", {rows: "4", class: "probar-cuerpo", "aria-label": "Cuerpo JSON"});
      cuerpo.value = JSON.stringify(ej || {}, null, 1);
      cuerpo.style.width = "100%"; cuerpo.style.fontFamily = "ui-monospace,monospace";
    }
    const salida = el("pre", {hidden: true, "aria-live": "polite", tabindex: "0"});
    const boton = el("button", {type: "button", class: "boton primario", onclick: async () => {
      let r = ruta;
      const q = new URLSearchParams();
      for (const [n, [p, inp]] of Object.entries(campos)) {
        if (p.in === "path") r = r.replace(`{${n}}`, encodeURIComponent(inp.value.trim()).replace(/%2F/gi, "/"));
        else if (inp.value.trim()) q.set(n, inp.value.trim());
      }
      const url = new URL(r.replace(/^\//, "") + (q.toString() ? `?${q}` : ""), base);
      salida.hidden = false; salida.textContent = "…";
      try {
        const opciones = {method: metodo.toUpperCase(), headers: {"Accept": "application/json"}};
        if (cuerpo) { opciones.headers["Content-Type"] = "application/json"; opciones.body = cuerpo.value; }
        const t0 = performance.now();
        const resp = await fetch(url, opciones);
        const texto = await resp.text();
        let bonito = texto;
        try { bonito = JSON.stringify(JSON.parse(texto), null, 2); } catch (_) {}
        if (bonito.length > 20000) bonito = bonito.slice(0, 20000) + "\n… (recortado)";
        salida.textContent = `${resp.status} ${resp.statusText} · ${Math.round(performance.now() - t0)} ms\n${url.pathname}${url.search}\n\n${bonito}`;
      } catch (e) { salida.textContent = `No se pudo: ${e.message}`; }
    }}, "Probar");
    form.append(boton);
    caja.append(form);
    if (cuerpo) caja.append(cuerpo);
    caja.append(salida);
    return caja;
  }

  function tablaParams(params) {
    if (!params || !params.length) return null;
    return el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "Parámetro"), el("th", {}, "Dónde"), el("th", {}, "Tipo"), el("th", {}, "Descripción"))),
      el("tbody", {}, params.map(p => el("tr", {},
        el("td", {}, el("code", {}, p.name), p.required ? " *" : ""),
        el("td", {}, p.in === "path" ? "ruta" : "consulta"),
        el("td", {}, p.schema ? p.schema.type : ""),
        el("td", {}, p.description || "")))));
  }

  function ejemplos(x) {
    const caja = el("div");
    const pre = el("pre", {tabindex: "0"});
    const tabs = el("div", {class: "pestanas", role: "tablist", "aria-label": "Lenguaje del ejemplo"});
    const textos = {curl: x.curl.join("\n\n"), Python: x.python, JavaScript: x.javascript};
    Object.keys(textos).forEach((k, i) => {
      const b = el("button", {type: "button", role: "tab", "aria-selected": i === 0 ? "true" : "false", onclick: () => {
        tabs.querySelectorAll("button").forEach(o => o.setAttribute("aria-selected", "false"));
        b.setAttribute("aria-selected", "true"); pre.textContent = textos[k];
      }}, k);
      tabs.append(b);
    });
    pre.textContent = textos.curl;
    caja.append(tabs, pre);
    return caja;
  }

  fetch(new URL("api/v1/openapi.json", base)).then(r => r.json()).then(spec => {
    const partes = [
      el("h1", {}, spec.info.title),
      el("p", {}, `Versión ${spec.info.version} · `, el("a", {href: new URL("api/v1/openapi.json", base)}, "openapi.json"), " · ",
         el("a", {href: new URL("api/v1/meta", base)}, "versiones y licencias")),
      md(spec.info.description),
      el("h2", {}, "Ejemplos"),
      ejemplos(spec["x-ejemplos"]),
    ];
    const porTag = {};
    for (const [ruta, metodos] of Object.entries(spec.paths))
      for (const [metodo, op] of Object.entries(metodos))
        (porTag[(op.tags || ["Otros"])[0]] ||= []).push([ruta, metodo, op]);
    for (const tag of spec.tags) {
      partes.push(el("h2", {id: `tag-${tag.name}`}, tag.name), el("p", {}, tag.description));
      for (const [ruta, metodo, op] of porTag[tag.name] || []) {
        const det = el("details", {class: "ruta-api"},
          el("summary", {}, el("span", {class: `metodo ${metodo}`}, metodo.toUpperCase()), el("code", {}, ruta), el("span", {}, "— " + op.summary)),
          el("div", {class: "cuerpo"},
            op.description ? md(op.description) : null,
            tablaParams(op.parameters),
            el("p", {}, el("b", {}, "Respuestas: "), Object.entries(op.responses).map(([c, r]) => `${c} ${r.description || (r.$ref ? r.$ref.split("/").pop() : "")}`).join(" · ")),
            el("p", {}, el("b", {}, "Ejemplo: "), el("code", {}, `${metodo.toUpperCase()} ${ejemploRuta(ruta, op.parameters)}`)),
            probar(metodo, ruta, op)));
        partes.push(det);
      }
    }
    partes.push(el("h2", {}, "Esquemas"), el("pre", {tabindex: "0"}, JSON.stringify(spec.components.schemas, null, 1)));
    raiz.replaceChildren(...partes);
  }).catch(e => { raiz.replaceChildren(el("p", {}, `No se pudo leer la especificación: ${e.message}`)); });
})();
