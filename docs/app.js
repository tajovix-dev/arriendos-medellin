"use strict";

/* =========================================================
   Utilidades
   ========================================================= */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const NF = new Intl.NumberFormat("es-CO");
const pesos = (n) => "$" + NF.format(Math.round(n));
const corto = (n) => "$" + (n / 1e6).toLocaleString("es-CO", { maximumFractionDigits: 2 }) + " M";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const urlOk = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* sin almacenamiento */ } },
};
const ancho = () => window.matchMedia("(min-width: 1100px)").matches;
const DIA = 86400000;

function hace(fechaIso) {
  if (!fechaIso || isNaN(new Date(fechaIso))) return "";
  const min = Math.max(0, Math.round((Date.now() - new Date(fechaIso)) / 60000));
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `hace ${h} h`;
  const d = Math.round(h / 24);
  return d === 1 ? "hace 1 día" : `hace ${d} días`;
}

/* =========================================================
   Estado
   ========================================================= */
let TODOS = [];
let META = {};
let COB = {};            // cobertura de cada dato (0..1)
let BASE_NUEVO = "";
let PRESUPUESTO = 2500000;
let PRECIO_TOPE = 4000000;
let favs = new Set(LS.get("favs", []));
let descartados = new Set(LS.get("ocultos", []));

const DEF = () => ({
  q: "", municipios: [], pmin: 0, pmax: PRESUPUESTO, hab: 0, banos: 0, area: 0, estratos: [],
  fuentes: [], novedad: 0, bajaron: false, dup: true, favs: false,
  descartados: false, conAdmin: false, orden: "nuevo", zona: false, buenPrecio: false, sinAlertas: false, barrios: [], recoBarrio: "", soloSur: true, conVideo: false, reciente: 0, sinAntiguos: false, banosSinDato: false,
});
let S = null;
let vista = LS.get("vista", "split");

let RES = [];            // resultado filtrado (lista)
let pintados = 0;
const LOTE = 30;

/* =========================================================
   Lógica de datos
   ========================================================= */
/** Fecha para ordenar por "más recientes": la de publicación del portal; si no la da, cuándo apareció aquí. */
const fechaRef = (it) => it.publicado || (it.visto1 || "").slice(0, 10);
/** Días desde la publicación en el portal (null si el portal no da la fecha). */
function diasPublicado(it) {
  if (!it.publicado) return null;
  return Math.max(0, Math.floor((Date.now() - new Date(it.publicado + "T12:00:00-05:00")) / DIA));
}
const DIAS_REVISAR = 30, DIAS_ANTIGUO = 60;
/** "alto" = muy probablemente ya no está; "medio" = conviene confirmar. */
function antiguedad(it) {
  const d = diasPublicado(it);
  if (d == null || d <= DIAS_REVISAR) return null;
  return d > DIAS_ANTIGUO ? "alto" : "medio";
}
function haceLargo(d) {
  if (d <= 1) return d === 1 ? "hace 1 día" : "hoy";
  if (d < 14) return `hace ${d} días`;
  if (d < 60) return `hace ${Math.round(d / 7)} semanas`;
  const m = Math.round(d / 30);
  return m < 12 ? `hace ${m} meses` : "hace más de un año";
}
/** Reciente = publicado en el portal hace N días o menos (sin fecha: visto aquí por primera vez hace N días). */
function esReciente(it, dias) {
  const d = diasPublicado(it);
  if (d != null) return d <= dias;
  return !it.ini && !!it.visto1 && Date.now() - new Date(it.visto1) <= dias * DIA;
}
function esNuevo(it, dias) {
  if (it.ini || !it.visto1 || it.visto1 <= BASE_NUEVO) return false;
  if (Date.now() - new Date(it.visto1) > dias * DIA) return false;
  if (it.publicado && Date.now() - new Date(it.publicado + "T00:00:00-05:00") > (dias + 2) * DIA) return false;
  return true;
}
function bajada(it) {
  const h = it.hist || [];
  if (h.length < 2) return 0;
  const max = Math.max(...h.map((x) => x[1]));
  return max > it.precio ? max - it.precio : 0;
}
const precioTotal = (it) => it.precio + (S.conAdmin && it.admin ? it.admin : 0);
/** Buen precio = al menos 15% más barato que similares y sin alertas. */
// Buen precio solo si se puede confiar: sin alertas, ubicado al menos en su barrio y con fotos
const esBuenPrecio = (it) => it.dif != null && it.dif <= -0.15 && !it.alertas && it.ubic !== "municipio" && (it.nf || (it.img ? 1 : 0)) >= 2;
/** Oportunidad: descuento claro (sin premiar lo sospechoso), con fotos y reciente. */
function puntajeOportunidad(it) {
  if (it.dif == null || it.alertas) return -99;
  // Los publicados hace mucho bajan: un precio bajo y viejo suele ser uno que ya se arrendó
  const ant = antiguedad(it), pen = ant === "alto" ? 4 : ant === "medio" ? 1 : 0;
  return Math.min(-it.dif, 0.3) * 10 + ((it.nf || 0) >= 5 ? 1 : 0) + (esNuevo(it, 7) || esReciente(it, 7) ? 1 : 0) + (it.disp ? 0.3 : 0) - pen;
}

function cobertura() {
  const campos = ["hab", "banos", "area", "estrato", "admin", "lat", "publicado", "barrio", "img"];
  const n = TODOS.length || 1;
  const c = {};
  for (const k of campos) c[k] = TODOS.filter((x) => x[k] != null && x[k] !== "").length / n;
  return c;
}
const MIN_COB = 0.15;

/** Filtra; `sin` = nombre de un filtro a ignorar (para conteos por faceta). */
function filtrar(sin = null) {
  const q = S.q.trim().toLowerCase();
  const pmax = S.pmax || Infinity;
  return TODOS.filter((it) => {
    if (S.favs && sin !== "favs") return favs.has(it.id) && (S.descartados || !descartados.has(it.id));
    if (sin !== "precio") {
      const p = precioTotal(it);
      if (p < (S.pmin || 0) || p > pmax) return false;
    }
    if (sin !== "municipio" && S.municipios.length && !S.municipios.includes(it.municipio)) return false;
    if (sin !== "barrio" && S.barrios.length) {
      // Un barrio elegido solo restringe su propio municipio: los demás municipios elegidos se ven completos
      const munB = new Set(S.barrios.map((k) => k.split("|")[0]));
      const dentro = S.barrios.includes(claveBarrio(it));
      if (S.municipios.length ? (munB.has(it.municipio) && !dentro) : !dentro) return false;
    }
    if (sin !== "sur" && S.soloSur && !esDelSur(it)) return false;
    if (sin !== "conVideo" && S.conVideo && !it.v) return false;
    if (sin !== "reciente" && S.reciente && !esReciente(it, S.reciente)) return false;
    if (S.sinAntiguos && antiguedad(it) === "alto") return false;
    if (sin !== "reco" && S.recoBarrio) {
      const r = (infoBarrio(it) || {}).recomendacion;
      if (S.recoBarrio === "solo_si" && r !== "recomendado") return false;
      if (S.recoBarrio === "ocultar_no" && r === "no recomendado") return false;
    }
    if (sin !== "fuente" && S.fuentes.length && !S.fuentes.includes(it.fuente)) return false;
    if (sin !== "hab" && S.hab && !(dato(it, "hab").v >= S.hab)) return false;
    if (sin !== "banos" && S.banos) {
      const b = dato(it, "banos").v;
      if (b == null ? !S.banosSinDato : b < S.banos) return false;
    }
    if (sin !== "area" && S.area && !(dato(it, "area").v >= S.area)) return false;
    if (sin !== "estrato" && S.estratos.length && !S.estratos.includes(dato(it, "estrato").v)) return false;
    if (sin !== "novedad" && S.novedad && !esNuevo(it, S.novedad)) return false;
    if (sin !== "bajaron" && S.bajaron && !bajada(it)) return false;
    if (!S.descartados && descartados.has(it.id)) return false;
    if (S.descartados && !descartados.has(it.id)) return false;  // vista "mis descartados"
    if (sin !== "buenPrecio" && S.buenPrecio && !esBuenPrecio(it)) return false;
    if (sin !== "sinAlertas" && S.sinAlertas && it.alertas) return false;
    if (q && !`${it.barrio || ""} ${it.municipio} ${it.titulo || ""} ${it.anunciante || ""}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

function contar(lista, fn) {
  const m = new Map();
  const vistos = new Set();
  for (const it of lista) {
    const k = fn(it);
    if (k == null) continue;
    // Un mismo apartamento publicado en varios portales cuenta una sola vez
    const g = S && S.dup && it.dup != null ? `${k}|d${it.dup}` : null;
    if (g) { if (vistos.has(g)) continue; vistos.add(g); }
    m.set(k, (m.get(k) || 0) + 1);
  }
  return m;
}

function ordenar(lista) {
  const ppm = (x) => (x.area ? x.precio / x.area : Infinity);
  const f = {
    nuevo: (a, b) => fechaRef(b).localeCompare(fechaRef(a)) || (b.visto1 || "").localeCompare(a.visto1 || ""),
    precio: (a, b) => precioTotal(a) - precioTotal(b),
    "-precio": (a, b) => precioTotal(b) - precioTotal(a),
    m2: (a, b) => ppm(a) - ppm(b),
    "-area": (a, b) => (b.area || 0) - (a.area || 0),
    bajada: (a, b) => bajada(b) - bajada(a),
    oportunidad: (a, b) => puntajeOportunidad(b) - puntajeOportunidad(a) || (a.dif ?? 9) - (b.dif ?? 9),
    recomendados: (a, b) => afinidad(b) - afinidad(a),
  }[S.orden] || (() => 0);
  return lista.sort(f);
}

/** Qué versión del mismo apartamento se muestra como principal (tarjeta y mapa):
 *  la que permite contactar gratis, luego la que no es de un portal que cobra, luego la más completa. */
const PORTALES_DE_PAGO = new Set(["Rentola"]);
// Muestran anuncios de otras inmobiliarias (feed de Wasi / bolsa compartida): mejor el original
const INTERMEDIARIOS = new Set(["Doomos", "Livin Inmobiliaria"]);
const NOMBRE_PORTAL = { "metrocuadrado.com": "Metrocuadrado", "ciencuadras.com": "Ciencuadras", "properati.com.co": "Properati",
  "fincaraiz.com.co": "Fincaraíz", "puntopropiedad.com": "Puntopropiedad", "espaciourbano.com": "Espacio Urbano",
  "viviendo.la": "Viviendo" };
// Portales que muestran el teléfono solo después de llenar un formulario
const CONTACTO_CON_FORMULARIO = new Set(["Viviendo"]);
function portalDe(u) {
  try { const d = new URL(u).hostname.replace(/^www\./, ""); return NOMBRE_PORTAL[d] || d; } catch { return "el portal original"; }
}
/** Nombre del sitio donde se ve (y se contacta) el anuncio. */
function sitioDe(it) { return it.sitio || it.fuente; }
/** Rentola sin página original conocida: contactar ahí cuesta. */
function esDePago(it) { return PORTALES_DE_PAGO.has(it.fuente) && !it.urlPago; }
/* ---------- Versiones: el mismo apartamento publicado en varias páginas ----------
   Se muestra como principal la que creemos ORIGINAL; las demás quedan como versiones que se
   pueden elegir, cada una con su página, su fecha y si trae contacto. */
// Inmobiliarias que leemos en su propia web (la fuente primaria de sus anuncios)
const INMOBILIARIAS_DIRECTAS = new Set(["Escala Inmobiliaria", "Alberto Álvarez", "Arrendamientos Santa Fe",
  "Arrendamientos Nutibara", "Arrendamientos Envigado", "Arrendamientos Villacruz", "Arrendamientos El Castillo",
  "Inmobiliaria La Estrella", "Arrendamientos Sukasa", "Mi Lugar Inmobiliario", "Arrendamientos La Aldea", "Survivienda",
  "Su Vivienda", "Duque Giraldo", "La Palma Inmobiliaria", "Urve Inmobiliaria", "Alnago", "Inmobiliaria Vicasa"]);
/** 3 web de la inmobiliaria · 2 portal donde publica · 1 intermediario · 0 intermediario que cobra. */
// Portales y agregadores (varias inmobiliarias publican ahí): no son la web propia de nadie
const DOMINIOS_PORTAL = new Set(["metrocuadrado.com", "ciencuadras.com", "properati.com.co", "fincaraiz.com.co",
  "puntopropiedad.com", "espaciourbano.com", "viviendo.la", "webinmobiliaria.com.co", "doomos.com.co"]);
function dominioDe(u) { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } }
/** Rentola copió el anuncio de la web propia de una inmobiliaria: su enlace lleva al original. */
function copiaDeWebPropia(it) { return PORTALES_DE_PAGO.has(it.fuente) && it.urlPago && !DOMINIOS_PORTAL.has(dominioDe(it.url)); }
function nivelOrigen(it) {
  if (copiaDeWebPropia(it)) return 3;
  if (PORTALES_DE_PAGO.has(it.fuente)) return 0;
  if (INTERMEDIARIOS.has(it.fuente)) return 1;
  // Alberto Álvarez también muestra la bolsa compartida de otras inmobiliarias (códigos que no son AA-)
  if (it.fuente === "Alberto Álvarez" && !/^aa-AA-/.test(it.id)) return 2;
  return INMOBILIARIAS_DIRECTAS.has(it.fuente) ? 3 : 2;
}
function tipoOrigen(it) {
  const n = nivelOrigen(it);
  if (copiaDeWebPropia(it)) return "web de la inmobiliaria (la encontramos gracias a Rentola)";
  if (n === 0) return it.urlPago ? `copiado por ${it.fuente}; el enlace lleva a la página original` : `${it.fuente} cobra por contactar`;
  if (n === 1) return "intermediario: muestra anuncios de otras inmobiliarias";
  if (n === 3) return "web de la inmobiliaria";
  if (it.fuente === "Alberto Álvarez") return "bolsa compartida en la web de Alberto Álvarez";
  return "portal donde se publicó";
}
const elegidas = new Set(LS.get("versionesElegidas", []));
let GRUPOS = {};   // dup → todas sus versiones (aunque algún filtro esconda alguna)
/** Orden de las versiones: la original primero (nivel, luego la publicada antes, luego la más completa). */
function ordenarVersiones(v) {
  const completo = (it) => ["hab", "banos", "area", "estrato", "barrio", "anunciante"].filter((k) => it[k] != null && it[k] !== "").length
    + (it.tel || it.wa ? 3 : 0) + Math.min(it.nf || 0, 10) / 10;
  return v.slice().sort((a, b) => nivelOrigen(b) - nivelOrigen(a)
    || (a.publicado || "9999").localeCompare(b.publicado || "9999")
    || completo(b) - completo(a));
}
function originalDe(it) { return it.dup != null && GRUPOS[it.dup] ? GRUPOS[it.dup][0] : it; }
function agrupar(lista) {
  if (!S.dup) { lista.forEach((x) => (x._otras = [])); return lista; }
  // El grupo conserva el lugar de su primera aparición en el orden elegido
  const grupos = new Map();
  const out = [];
  for (const it of lista) {
    if (it.dup == null) { out.push(it); continue; }
    const g = grupos.get(it.dup);
    if (g) g.push(it);
    else { const nuevo = [it]; grupos.set(it.dup, nuevo); out.push(nuevo); }
  }
  return out.map((x) => {
    if (!Array.isArray(x)) { x._otras = []; return x; }
    // Con que una versión pase los filtros basta: es el mismo apartamento, y se muestra la original
    // del grupo completo (o la que la persona eligió), aunque un filtro haya dejado fuera esa versión
    const orden = GRUPOS[x[0].dup] || ordenarVersiones(x);
    const principal = orden.find((o) => elegidas.has(o.id)) || orden[0];
    for (const o of orden) o._otras = [];
    principal._otras = orden.filter((o) => o !== principal);
    return principal;
  });
}
function elegirVersion(id) {
  const it = buscarItem(id);
  if (!it || it.dup == null) return;
  for (const o of GRUPOS[it.dup] || []) elegidas.delete(o.id);
  if (it !== originalDe(it)) elegidas.add(id);   // la original no hace falta recordarla
  LS.set("versionesElegidas", [...elegidas].slice(-300));
}
/** "11 nov 2:35 p. m." con la hora solo si la fuente la da. */
function fechaHora(fecha, hora) {
  if (!fecha) return "";
  if (!hora) return fechaCorta(fecha);
  const [h, m] = hora.split(":").map(Number);
  return `${fechaCorta(fecha)} ${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "a. m." : "p. m."}`;
}
function fechaVersion(v) {
  const pub = v.publicado
    ? `${PORTALES_DE_PAGO.has(v.fuente) ? `${v.fuente} lo copió el` : "Publicado el"} ${fechaHora(v.publicado.slice(0, 10), v.pub_h)}`
    : `${esc(v.fuente)} no da la fecha`;
  const visto = v.visto1 ? ` · lo vimos por primera vez el ${fechaHora(v.visto1.slice(0, 10), v.visto1.slice(11, 16))}` : "";
  return pub + visto;
}
/** Selector de versiones (tarjeta: fichas compactas; detalle: lista completa). */
function versionesHtml(it, completo) {
  const todas = it.dup != null ? GRUPOS[it.dup] || [] : [];
  if (!S.dup || todas.length < 2) return "";
  const orig = todas[0];
  const filas = todas.map((v) => {
    const sel = v === it;
    const etq = v === orig ? `<span class="v-etq orig" title="Lo consideramos el original: ${esc(tipoOrigen(v))}">Original</span>` : `<span class="v-etq">Copia</span>`;
    const contacto = v.tel || v.wa ? `<span class="v-tel" title="Trae teléfono o WhatsApp">📞</span>` : "";
    if (!completo) {
      return `<button class="v-chip ${sel ? "sel" : ""}" data-version="${esc(v.id)}" aria-pressed="${sel}" title="${esc(`${sitioDe(v)} · ${tipoOrigen(v)} · ${pesos(v.precio)}`)}">${esc(sitioDe(v))}${v === orig ? " ★" : ""}${contacto}</button>`;
    }
    return `<li class="v-fila ${sel ? "sel" : ""}">
      <button class="v-elegir" data-version="${esc(v.id)}" aria-pressed="${sel}">
        <span class="v-cab"><b>${esc(sitioDe(v))}</b> ${etq} ${contacto}</span>
        <span class="v-tipo">${esc(tipoOrigen(v))}</span>
        <span class="v-dato">${pesos(v.precio)}${v.precio !== it.precio ? ` <span class="aviso-txt">(precio distinto)</span>` : ""}</span>
        <span class="v-fecha">${fechaVersion(v)}</span>
      </button>
      <a class="v-abrir" href="${esc(urlOk(v.url))}" target="_blank" rel="noopener noreferrer">Abrir ↗</a>
    </li>`;
  }).join("");
  if (!completo) return `<div class="versiones-t"><span class="tenue">En ${todas.length} páginas:</span> ${filas}</div>`;
  return `<p class="tenue">El mismo apartamento está publicado en ${todas.length} páginas. Mostramos como principal la que creemos original
    (★): primero la web de la inmobiliaria, luego el portal donde se publicó y de último los intermediarios. Toca otra versión para verla.</p>
    <ul class="v-lista">${filas}</ul>`;
}

/* =========================================================
   Opciones dinámicas
   ========================================================= */
function opcionesPrecio() {
  const pasos = [];
  for (let v = 500000; v <= PRECIO_TOPE; v += 250000) pasos.push(v);
  return pasos;
}
function opcionesOrden() {
  const o = [["nuevo", "Más recientes"], ["precio", "Menor precio"], ["-precio", "Mayor precio"]];
  if (COB.area >= MIN_COB) o.push(["-area", "Mayor área"]);
  if (TODOS.some((x) => bajada(x))) o.push(["bajada", "Mayor bajada de precio"]);
  if (TODOS.some((x) => x.dif != null)) o.push(["oportunidad", "Más barato vs. similares"]);
  if (favs.size) o.push(["recomendados", "Recomendados para ti"]);
  return o;
}
const pct = (x) => Math.round(x * 100) + "%";

/* =========================================================
   Render: controles
   ========================================================= */
function chipsMunicipio(lista) {
  const c = contar(lista, (x) => x.municipio);
  const todos = META.municipios || [...new Set(TODOS.map((x) => x.municipio))];
  return todos.map((m) => {
    const on = S.municipios.includes(m);
    const n = c.get(m) || 0;
    return `<button class="chip ${on ? "on" : ""}" data-toggle="municipios" data-v="${esc(m)}" aria-pressed="${on}" ${!n && !on ? "disabled" : ""}>${esc(m)} <span class="n">${NF.format(n)}</span></button>`;
  }).join("");
}

/** Selector compacto de habitaciones o baños (con cuántos hay en cada opción). */
/** Celular: municipio como lista desplegable (cabe junto a habitaciones, baños y recientes). */
function selectorMunicipio(sel) {
  if (!sel) return;
  const c = contar(filtrarCache("municipio"), (x) => x.municipio);
  const todos = META.municipios || [...new Set(TODOS.map((x) => x.municipio))];
  const varios = S.municipios.length > 1;
  sel.innerHTML = `<option value="">Municipio</option>`
    + (varios ? `<option value="__varios" selected disabled>${S.municipios.length} municipios</option>` : "")
    + todos.map((m) => `<option value="${esc(m)}" ${!varios && S.municipios[0] === m ? "selected" : ""}>${esc(m)}${S.municipios[0] === m && !varios ? "" : ` (${NF.format(c.get(m) || 0)})`}</option>`).join("");
  sel.classList.toggle("on", S.municipios.length > 0);
}
function selectorCuarto(sel, campo) {
  if (!sel) return;
  const l = filtrarCache(campo);
  const ops = campo === "hab" ? [1, 2, 3, 4] : [1, 2, 3];
  const nom = campo === "hab" ? ["Habitaciones", "hab"] : ["Baños", "baños"];
  const cuenta = (v) => contarAptos(l.filter((x) => { const d = dato(x, campo).v; return d == null ? campo === "banos" && S.banosSinDato : d >= v; }));
  sel.dataset.campo = campo;
  const etq0 = ancho() ? `${nom[0]}: ${campo === "hab" ? "todas" : "todos"}` : (campo === "hab" ? "Hab." : "Baños");
  sel.innerHTML = `<option value="0">${etq0}</option>`
    + ops.map((v) => `<option value="${v}" ${S[campo] === v ? "selected" : ""}>${v}+ ${nom[1]}${S[campo] === v ? "" : ` (${NF.format(cuenta(v))})`}</option>`).join("");
  sel.value = String(S[campo] || 0);
  sel.classList.toggle("on", !!S[campo]);
}
function segmentoHab(lista) {
  const ops = [[0, "Todas"], [1, "1+"], [2, "2+"], [3, "3+"], [4, "4+"]];
  return ops.map(([v, t]) => {
    const n = v ? contarAptos(lista.filter((x) => dato(x, "hab").v >= v)) : contarAptos(lista);
    return `<button class="${S.hab === v ? "on" : ""}" data-set="hab" data-v="${v}" title="${NF.format(n)} apartamentos">${t}</button>`;
  }).join("");
}

/** Convierte lo que escribe la persona en pesos: "1.500.000", "1500000", "1,5" o "1.5" (millones), "1500" (miles). */
function leerPrecio(txt) {
  let t = String(txt || "").trim().toLowerCase().replace(/\$|\s|cop/g, "");
  if (!t) return 0;
  t = t.replace(/([.,]\d{3})[.,]\d{1,2}$/, "$1");                   // "1.200.000,00" → "1.200.000"
  if (/^\d{1,2}([.,]\d{1,2})?(m|mill(ones)?)?$/.test(t) && parseFloat(t.replace(",", ".")) <= 20) {
    return Math.round(parseFloat(t.replace(",", ".")) * 1e6);         // "1,5" → 1.500.000
  }
  const n = parseInt(t.replace(/\D/g, ""), 10) || 0;
  return Math.min(n < 10000 ? n * 1000 : n, 50_000_000);                                     // "1500" → 1.500.000
}
function campoPrecio(inp, tipo) {
  if (!inp) return;
  const val = tipo === "min" ? S.pmin : S.pmax;
  inp.dataset.campo = tipo === "min" ? "pmin" : "pmax";
  if (document.activeElement !== inp) inp.value = val ? "$" + NF.format(val) : "";
}

/* =========================================================
   Barrios (dependen del municipio elegido)
   ========================================================= */
/** Normaliza nombres de barrio: sin tildes, sin mayúsculas y sin "de/del" ("Loma de San José" = "LOMA SAN JOSE"). */
const normB = (s) => (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
  .replace(/[^a-z0-9ñ ]+/g, " ").replace(/\b(de|del)\b/g, " ").replace(/\s+/g, " ").trim();
const NOMBRE_BARRIO = new Map();   // clave "Municipio|barrio normalizado" → nombre para mostrar
let textoBuscaBarrio = "";

/** Clave del barrio de un anuncio (null si no informa barrio o el "barrio" es el mismo municipio). */
/* ---------- Noticias por barrio (cada 3 días) y selección con IA ---------- */
const NOTICIAS = new Map();   // clave de barrio → {noticias:[], ia:{}}
let NOTICIAS_FECHA = null, IA_SEL = null;
const CAT_NOTICIA = {
  extorsion: ["💰", "Extorsión"], homicidio: ["⚠️", "Homicidio"], hurto: ["👜", "Hurto"], microtrafico: ["💊", "Microtráfico"],
  rina_violencia: ["👊", "Riña / violencia"], accidente: ["🚑", "Accidente"], deslizamiento_inundacion: ["🌧", "Deslizamiento / inundación"],
  servicios_agua_luz: ["🚰", "Agua / luz"], obras_movilidad: ["🚧", "Obras / movilidad"], positivo: ["✨", "Buena noticia"], otro: ["📰", "Otra"],
};
const NEG_NOTICIA = new Set(["extorsion", "homicidio", "hurto", "microtrafico", "rina_violencia", "deslizamiento_inundacion", "servicios_agua_luz"]);
function cargarNoticias(d) {
  if (!d || !d.barrios) return;
  NOTICIAS_FECHA = d.actualizado;
  for (const [k, v] of Object.entries(d.barrios)) {
    const [m, nombre] = k.split("|");
    NOTICIAS.set(`${m}|${normB(nombre)}`, v);
  }
}
/** Noticias negativas de los últimos 30 días (para el aviso en la tarjeta). */
function noticiasRecientes(clave) {
  const n = NOTICIAS.get(clave);
  if (!n) return [];
  const lim = new Date(Date.now() - 30 * DIA).toISOString().slice(0, 10);
  return (n.noticias || []).filter((x) => NEG_NOTICIA.has(x.categoria) && (x.gravedad || 0) >= 2 && (x.fecha || "") >= lim);
}
function seccionNoticias(clave) {
  const n = NOTICIAS.get(clave);
  const cuando = NOTICIAS_FECHA ? `revisadas ${hace(NOTICIAS_FECHA)}` : "";
  if (!n) return NOTICIAS_FECHA ? `<h3>📰 Noticias recientes</h3><p class="tenue">Sin noticias relevantes en los últimos 30 días (${cuando}).</p>` : "";
  const ia = n.ia;
  const alerta = ia ? { alta: "alta", media: "media", baja: "baja", ninguna: "ninguna" }[ia.nivel_alerta] || "" : "";
  return `<h3>📰 Noticias recientes <span class="tenue n-cuando">${cuando}</span></h3>
    ${ia ? `<div class="ia-barrio alerta-${alerta}"><b>🤖 Análisis automático (${esc(fechaCorta(ia.fecha))})</b>
      <p>${esc(ia.resumen_reciente || "")}</p>
      ${ia.sugerencia && ia.sugerencia !== "mantener" ? `<p class="ia-sug">${ia.sugerencia === "mas_precaucion" ? "⚠ Sugiere más precaución" : "✓ Sugiere que ha mejorado"}: ${esc(ia.motivo || "")}</p>` : ""}
      <p class="tenue ia-nota">Generado con IA a partir de las noticias de abajo. Verifica antes de decidir.</p></div>` : ""}
    <ul class="noticias-b">${(n.noticias || []).map((x) => {
      const c = CAT_NOTICIA[x.categoria] || CAT_NOTICIA.otro;
      return `<li><span class="nb-ico" title="${esc(c[1])}">${c[0]}</span><span><a href="${esc(urlOk(x.link))}" target="_blank" rel="noopener noreferrer">${esc(x.titulo)}</a>
        <span class="tenue">${esc(x.fuente || "")} · ${x.fecha ? esc(fechaCorta(x.fecha)) : ""}${x.gravedad >= 2 ? ` · <b class="grave">${esc(c[1])}</b>` : ""}</span></span></li>`;
    }).join("")}</ul>`;
}
const imperdible = (id) => (IA_SEL && IA_SEL.imperdibles || []).find((x) => x.id === id);
function claveBarrio(it) {
  if (it._kb !== undefined) return it._kb;
  const b = normB(it.barrio);
  it._kb = b && b !== normB(it.municipio) ? `${it.municipio}|${b}` : null;
  return it._kb;
}

/** Elige el nombre más usado de cada barrio y lo pone en formato Título si venía en MAYÚSCULAS. */
function prepararBarrios() {
  const variantes = new Map();
  for (const it of TODOS) {
    const k = claveBarrio(it);
    if (!k) continue;
    const m = variantes.get(k) || new Map();
    m.set(it.barrio.trim(), (m.get(it.barrio.trim()) || 0) + 1);
    variantes.set(k, m);
  }
  // "san jose" → "San Jose"; conectores en minúscula ("Loma de San José")
  const titulo = (s) => s.toLowerCase().replace(/\s+/g, " ")
    .replace(/(^|\s|-)(\p{L})/gu, (_, a, b) => a + b.toUpperCase())
    .replace(/\s(De|Del|La|Las|Los|El|Y)\s/g, (w) => w.toLowerCase());
  const tieldes = (n) => (n.match(/[áéíóúñ]/gi) || []).length;
  for (const [k, m] of variantes) {
    // Prefiere la variante más usada; a igualdad, la que tiene tildes y minúsculas
    const ordenadas = [...m.entries()].sort((a, b) => b[1] - a[1] || tieldes(b[0]) - tieldes(a[0]) || (a[0] === a[0].toUpperCase()) - (b[0] === b[0].toUpperCase()));
    const conTilde = ordenadas.find(([n]) => tieldes(n) > 0);
    NOMBRE_BARRIO.set(k, titulo((conTilde || ordenadas[0])[0]));
  }
  S.barrios = (S.barrios || []).filter((k) => NOMBRE_BARRIO.has(k));
}

/* ---------- Información de barrios (investigación: seguridad, pros y contras) ---------- */
const INFO_BARRIO = new Map();   // "Municipio|barrio normalizado" → ficha
const CONTEXTO_MUN = new Map();  // municipio → contexto general
const ETQ_SEG = { "alta": "Seguridad alta", "media-alta": "Seguridad media-alta", "media": "Seguridad media", "media-baja": "Seguridad media-baja", "baja": "Seguridad baja", "sin datos": "Seguridad: sin datos" };

function cargarInfoBarrios(data) {
  for (const mun of data.municipios || []) {
    CONTEXTO_MUN.set(mun.municipio, mun);
    for (const b of mun.barrios || []) {
      const ficha = { ...b, municipio: mun.municipio };
      for (const nombre of [b.nombre, ...(b.alias || [])]) INFO_BARRIO.set(`${mun.municipio}|${normB(nombre)}`, ficha);
    }
  }
}
const infoBarrio = (it) => { const k = claveBarrio(it); return k ? INFO_BARRIO.get(k) : null; };
const claseSeg = (s) => "s-" + (s || "sin datos").replace(/\s+/g, "-");
const etiquetaSeg = (s) => `<span class="seg ${claseSeg(s)}">${esc(ETQ_SEG[s] || "Seguridad: " + s)}</span>`;

/* Recomendación: ícono + texto (nunca solo color) */
const RECO = {
  "recomendado": { ico: "✅", txt: "Barrio recomendado", cls: "r-si" },
  "con precaución": { ico: "⚠️", txt: "Con precaución", cls: "r-prec" },
  "no recomendado": { ico: "⛔", txt: "No recomendado", cls: "r-no" },
};
const recoDe = (f) => RECO[(f && f.recomendacion) || ""] || { ico: "❔", txt: "Sin evaluar", cls: "r-nd" };
const insigniaReco = (f, corta = false) => { const r = recoDe(f); return `<span class="reco ${r.cls}" title="${esc((f && f.razon_recomendacion) || r.txt)}">${r.ico}${corta ? "" : ` ${r.txt}`}</span>`; };
const TERRENO = { "plano": "Plano", "ondulado": "Ondulado", "lomas fuertes": "Lomas fuertes" };
const PROBLEMA = {
  agua: ["💧", "Cortes de agua"], luz: ["⚡", "Cortes de luz"], ruido: ["🔊", "Ruido"], acceso: ["🚧", "Acceso difícil"],
  altura: ["⬆️", "Zona alta"], deslizamiento: ["⚠️", "Riesgo de deslizamiento"], inundacion: ["🌊", "Riesgo de inundación"],
  trafico: ["🚗", "Tráfico"], obras: ["🔨", "Obras"], basuras: ["🗑️", "Basuras"], otro: ["•", "Otro"],
};
const NIVEL_TXT = { bajo: "Bajo", medio: "Medio", alto: "Alto" };
/** Íconos de los problemas importantes (nivel medio o alto) para mostrar en la tarjeta. */
function iconosProblemas(f) {
  const imp = (f.problemas || []).filter((p) => p.nivel !== "bajo");
  const vistos = new Set();
  return imp.filter((p) => !vistos.has(p.tipo) && vistos.add(p.tipo)).slice(0, 5)
    .map((p) => `<span class="ico-prob ${p.nivel}" title="${esc(`${PROBLEMA[p.tipo][1]} (${p.nivel}): ${p.detalle}`)}">${PROBLEMA[p.tipo][0]}</span>`).join("");
}

/** Línea en la tarjeta que abre la ficha del barrio. */
function lineaBarrio(it) {
  const f = infoBarrio(it);
  if (!f) return "";
  const partes = [];
  if (f.terreno) partes.push(`⛰ ${esc(TERRENO[f.terreno] || f.terreno)}`);
  if (f.seguridad) partes.push(etiquetaSeg(f.seguridad));
  return `<button class="btn-barrio-info" data-ficha="${esc(claveBarrio(it))}" title="${esc(f.razon_recomendacion || "Ver qué se dice de este barrio")}">
    ${insigniaReco(f)}<span class="tenue">${partes.join(" · ")}</span>${iconosProblemas(f)}${noticiasRecientes(claveBarrio(it)).length ? `<span class="badge-noticias" title="Noticias negativas en los últimos 30 días">📰 ${noticiasRecientes(claveBarrio(it)).length}</span>` : ""}<span class="ver-mas">Detalles →</span></button>`;
}

function abrirFichaBarrio(clave) {
  const f = INFO_BARRIO.get(clave);
  if (!f) return;
  const ctx = CONTEXTO_MUN.get(f.municipio);
  const lista = (arr, cls) => (arr && arr.length ? `<ul class="${cls}">${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="tenue">Sin información.</p>`);
  // Con los demás filtros activos (precio, habitaciones, recientes…), como lo verás al abrirlo
  const n = contarAptos(filtrarCache("barrio").filter((x) => claveBarrio(x) === clave));
  $("#dlgBarrioTitulo").textContent = `${NOMBRE_BARRIO.get(clave) || f.nombre}, ${f.municipio}`;
  const r = recoDe(f);
  const ns = f.noticias_seguridad;
  const celdaN = (n, etq) => `<div class="n-cel ${n >= 3 ? "alto" : n >= 1 ? "medio" : ""}"><strong>${n ?? "–"}</strong><span>${etq}</span></div>`;
  $("#fichaBarrio").innerHTML = `
    <div class="fb-reco ${r.cls}"><span class="fb-ico">${r.ico}</span><div><strong>${r.txt}</strong>${f.razon_recomendacion ? `<div>${esc(f.razon_recomendacion)}</div>` : ""}</div></div>
    <div class="fb-seg">${etiquetaSeg(f.seguridad)}${f.terreno ? `<span class="chip-t">⛰ ${esc(TERRENO[f.terreno] || f.terreno)}</span>` : ""}<span class="conf">Confianza de la información: ${esc(f.confianza || "—")}</span>${f.comuna ? `<span class="conf">· ${esc(f.comuna)}</span>` : ""}</div>
    <p>${esc(f.resumen || "")}</p>
    ${(f.problemas || []).length ? `<h3>Problemas a tener en cuenta</h3><ul class="problemas">${f.problemas
      .slice().sort((a, b) => ({ alto: 0, medio: 1, bajo: 2 }[a.nivel] - { alto: 0, medio: 1, bajo: 2 }[b.nivel]))
      .map((p) => `<li><span class="p-ico">${PROBLEMA[p.tipo][0]}</span><span><b>${PROBLEMA[p.tipo][1]}</b> <span class="nivel ${p.nivel}">${NIVEL_TXT[p.nivel]}</span><br>${esc(p.detalle)}</span></li>`).join("")}</ul>` : ""}
    ${f.accesos ? `<h3>Cómo se llega</h3><p>${esc(f.accesos)}</p>` : ""}
    ${(f.opiniones || []).length ? `<h3>Lo que dicen los residentes</h3><div class="opiniones">${f.opiniones.map((o) =>
      `<blockquote>${esc(o.texto)}${o.fuente ? ` <a href="${esc(urlOk(o.fuente))}" target="_blank" rel="noopener noreferrer">${o.origen ? esc(o.origen) : "fuente"}</a>` : ""}</blockquote>`).join("")}</div>` : ""}
    ${(f.temas_maps || []).length ? `<p class="temas">Temas frecuentes en reseñas: ${f.temas_maps.map(esc).join(" · ")}</p>` : ""}
    ${(f.conjuntos_maps || []).length ? `<h3>Conjuntos del barrio en Google Maps</h3><ul class="conjuntos">${f.conjuntos_maps.map((c) =>
      `<li><a href="${esc(urlOk(c.url))}" target="_blank" rel="noopener noreferrer">${esc(c.nombre)}</a>${c.calificacion ? ` <span class="estrellas">★ ${esc(c.calificacion)}</span>` : ""}${c.resenas ? ` <span class="tenue">(${esc(c.resenas)} reseñas)</span>` : ""}</li>`).join("")}</ul>` : ""}
    ${f.terreno_nota ? `<h3>Terreno</h3><p>${esc(f.terreno_nota)}</p>` : ""}
    ${(f.cerca || []).length ? `<h3>Qué hay cerca</h3><ul class="cerca">${f.cerca.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    ${seccionNoticias(clave)}
    ${ns ? `<h3>Noticias de seguridad (${esc(ns.periodo || "2024-2026")})</h3>
      <div class="n-grid">${celdaN(ns.extorsion, "Extorsión / vacunas")}${celdaN(ns.sicariato_homicidios, "Sicariato / homicidios")}${celdaN(ns.hurtos, "Hurtos")}${celdaN(ns.microtrafico, "Microtráfico")}</div>
      ${ns.detalle ? `<p class="n-det">${esc(ns.detalle)}</p>` : ""}
      <p class="aviso-legal" style="margin-top:4px">Cantidad aproximada de noticias distintas encontradas sobre el barrio; 0 significa que no se encontraron, no que no ocurran.</p>` : ""}
    <h3>Lo bueno</h3>${lista(f.positivos, "pos")}
    <h3>Lo malo / a tener en cuenta</h3>${lista(f.negativos, "neg")}
    ${ctx && ctx.contexto_municipio ? `<h3>Contexto de ${esc(f.municipio)}</h3><p class="contexto">${esc(ctx.contexto_municipio)}</p>` : ""}
    ${(f.fuentes || []).length ? `<h3>Fuentes de esta investigación</h3><div class="fuentes-b">${f.fuentes.map((u, i) => {
      let sitio = u; try { sitio = new URL(u).hostname.replace(/^www\./, ""); } catch { /* url rara */ }
      return `<div>${i + 1}. <a href="${esc(urlOk(u))}" target="_blank" rel="noopener noreferrer" title="${esc(u)}">${esc(sitio)}</a> <span class="tenue">↗</span></div>`;
    }).join("")}</div>` : ""}
    ${n ? `<button class="btn ver-aptos" data-filtrar-barrio="${esc(clave)}">Ver ${n} apartamento${n === 1 ? "" : "s"} en este barrio</button>` : ""}
    <p class="aviso-legal">Resumen de noticias, datos públicos y opiniones de residentes (${esc(INFO_FECHA)}). Es una referencia general: la seguridad cambia por cuadra y por hora. Visita la zona de día y de noche antes de decidir.</p>`;
  $("#dlgBarrio").showModal();
}
let INFO_FECHA = "";

/* ---------- "Solo el sur" ---------- */
// Municipios del sur del Valle de Aburrá + el sur de Medellín
const MUNICIPIOS_SUR = new Set(["Sabaneta", "Envigado", "Itagüí", "La Estrella"]);
/* "Solo el sur": los 4 municipios completos + estas comunas y corregimientos de Medellín
   (barrios oficiales de cada una). */
const SUR_MEDELLIN = [
  ["Comuna 14 · El Poblado", ["El Poblado", "Barrio Colombia", "Ciudad del Río", "Villa Carlota", "Castropol", "Lalinde", "Las Lomas", "Altos del Poblado", "El Tesoro", "Los Naranjos", "Los Balsos", "San Lucas", "El Diamante", "El Castillo", "Alejandría", "La Florida", "Manila", "Astorga", "Patio Bonito", "La Aguacatala", "Santa María de los Ángeles", "Provenza", "Loma de los Parra", "Loma del Indio"]],
  ["Comuna 15 · Guayabal", ["Guayabal", "Tenche", "Trinidad", "Santa Fe", "Shellmar", "Campo Amor", "Cristo Rey", "La Colina", "Parque Juan Pablo II"]],
  ["Comuna 16 · Belén", ["Belén", "Fátima", "Rosales", "Granada", "San Bernardo", "Las Playas", "Diego Echavarría", "La Mota", "La Hondonada", "El Rincón", "Loma de los Bernal", "La Gloria", "La Palma", "Los Alpes", "Las Violetas", "Las Mercedes", "Nueva Villa de Aburrá", "Miravalle", "El Nogal", "Los Almendros", "Malibú", "Rodeo Alto", "Altavista"]],
  ["Corregimiento San Antonio de Prado", ["San Antonio de Prado"]],
  ["Corregimiento Altavista", ["Altavista"]],
];
const RE_SUR_MED = new RegExp("\\b(" + [...new Set(SUR_MEDELLIN.flatMap(([, bs]) => bs))]
  .map((b) => normB(b).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")\\b");
const RE_COMUNA_SUR = /comuna 1[456]\b|el poblado|guayabal|belen|san antonio de prado|altavista|corregimiento [78]0\b/;
function esDelSur(it) {
  if (MUNICIPIOS_SUR.has(it.municipio)) return true;
  if (it.municipio !== "Medellín") return false;
  if (it.barrio && RE_SUR_MED.test(normB(it.barrio))) return true;
  const f = infoBarrio(it);
  if (f && f.comuna && RE_COMUNA_SUR.test(normB(f.comuna))) return true;
  // Sin barrio reconocible pero con ubicación exacta: al sur de Medellín (El Poblado,
  // Guayabal, Belén) quedan por debajo de esta latitud, sin contar la montaña oriental
  const generico = !it.barrio || normB(it.barrio) === "medellin";
  return generico && !it.aprox && !!it.lat && it.lat < 6.215 && it.lon < -75.555;
}

/** "Barrio, Municipio" con el nombre de barrio unificado (o solo el municipio si no hay barrio). */
function lugarDe(it) {
  const k = claveBarrio(it);
  return k ? `${NOMBRE_BARRIO.get(k) || it.barrio}, ${it.municipio}` : it.municipio;
}

/** Si quitas un municipio, se quitan también sus barrios elegidos. */
function podarBarrios() {
  if (S.municipios.length) S.barrios = S.barrios.filter((k) => S.municipios.includes(k.split("|")[0]));
}

function seccionBarrios() {
  const lista = filtrarCache("barrio");
  const conteo = contar(lista, claveBarrio);
  const muns = S.municipios.length ? S.municipios : (META.municipios || [...new Set(TODOS.map((x) => x.municipio))]);
  const grupos = muns.map((m) => {
    const filas = [...NOMBRE_BARRIO.keys()]
      .filter((k) => k.startsWith(m + "|") && (conteo.get(k) || S.barrios.includes(k)))
      .sort((a, b) => (conteo.get(b) || 0) - (conteo.get(a) || 0) || NOMBRE_BARRIO.get(a).localeCompare(NOMBRE_BARRIO.get(b)));
    if (!filas.length) return "";
    return `<div class="grupo-barrios"><div class="mun-barrios">${esc(m)}</div>${filas.map((k) => {
      const on = S.barrios.includes(k);
      const nombre = NOMBRE_BARRIO.get(k);
      return `<button class="fila-barrio ${on ? "on" : ""}" data-toggle="barrios" data-v="${esc(k)}" data-nombre="${esc(normB(nombre))}" aria-pressed="${on}">
        <span class="caja">${on ? "✓" : ""}</span>${INFO_BARRIO.get(k) ? insigniaReco(INFO_BARRIO.get(k), true) : ""}<span class="nb">${esc(nombre)}</span>${INFO_BARRIO.get(k) && INFO_BARRIO.get(k).terreno === "lomas fuertes" ? `<span class="mini-t-lomas" title="Lomas fuertes">⛰</span>` : ""}<span class="n">${conteo.get(k) || 0}</span></button>`;
    }).join("")}</div>`;
  }).join("");
  const ayuda = S.municipios.length
    ? `Barrios de ${S.municipios.join(", ")} con los filtros actuales. Puedes marcar varios.`
    : "Elige primero un municipio para ver solo sus barrios. Puedes marcar varios.";
  const sinBarrio = contarAptos(lista.filter((x) => !claveBarrio(x)));
  return `<div class="seccion" id="secBarrios"><h3>Barrio ${S.barrios.length ? `<span class="contador">${S.barrios.length}</span>` : ""}</h3>
    <p class="ayuda">${ayuda}${sinBarrio ? ` ${sinBarrio} anuncios no informan barrio.` : ""}</p>
    <input type="search" class="busca-barrio" id="buscaBarrio" placeholder="Buscar barrio…" value="${esc(textoBuscaBarrio)}" autocomplete="off">
    <div class="lista-barrios" id="listaBarrios">${grupos || `<p class="ayuda">No hay barrios con estos filtros.</p>`}</div>
    ${S.barrios.length ? `<button class="enlace" data-limpiar-barrios>Quitar barrios elegidos</button>` : ""}
  </div>`;
}

/** Filtra la lista visible de barrios mientras escribes (sin volver a dibujar el panel). */
function filtrarListaBarrios() {
  const q = normB(textoBuscaBarrio);
  $$("#listaBarrios .grupo-barrios").forEach((g) => {
    let visibles = 0;
    g.querySelectorAll(".fila-barrio").forEach((f) => {
      const ok = !q || f.dataset.nombre.includes(q);
      f.hidden = !ok;
      if (ok) visibles++;
    });
    g.hidden = !visibles;
  });
}

/** Cuenta apartamentos distintos (un mismo apartamento publicado en varios portales cuenta una vez). */
function contarAptos(lista) {
  if (!S.dup) return lista.length;
  return new Set(lista.map((x) => (x.dup != null ? "d" + x.dup : x.id))).size;
}
function chipReciente() {
  // Activo con cualquier periodo (7, 15 o 30 días); si está apagado, lo enciende con 15 días
  const dias = S.reciente || 15;
  const n = contarAptos(filtrarCache("reciente").filter((x) => esReciente(x, dias)));
  const on = !!S.reciente;
  return `<button class="chip chip-sur ${on ? "on" : ""}" data-set="reciente" data-v="${on ? 0 : 15}" aria-pressed="${on}" title="${on ? `Publicados en los últimos ${dias} días · toca para quitar` : "Publicados en los últimos 15 días: los que más probablemente siguen disponibles"}">🕒 ${on && dias !== 15 ? `≤ ${dias} días` : "Recientes"} <span class="n">${NF.format(n)}</span></button>`;
}

function renderBarra(base) {
  $("#rapMunicipios").innerHTML = chipReciente() + chipsMunicipio(filtrarCache("municipio"));
  const nSur = contarAptos(filtrarCache("sur").filter(esDelSur));
  $("#swSur").checked = !!S.soloSur;
  $("#nSur").textContent = NF.format(nSur);
  $("#switchSur").classList.toggle("on", !!S.soloSur);
  const nb = S.barrios.length;
  $("#rapBarrios").innerHTML = `Barrios${nb ? ` <span class="contador">${nb}</span>` : " ▾"}`;
  $("#rapBarrios").classList.toggle("on", nb > 0);
  campoPrecio($("#rapPmin"), "min");
  campoPrecio($("#rapPmax"), "max");
  selectorCuarto($("#rapHab"), "hab");
  selectorCuarto($("#rapBanos"), "banos");
  selectorMunicipio($("#rapMuni"));
  if ($("#q") !== document.activeElement) $("#q").value = S.q;
}

function seccion(titulo, cuerpo, ayuda = "", clase = "") {
  return `<div class="seccion ${clase}"><h3>${titulo}</h3>${ayuda ? `<p class="ayuda">${ayuda}</p>` : ""}${cuerpo}</div>`;
}
function ayudaCobertura(campo) {
  const c = COB[campo];
  return c < 0.95 ? `Dato disponible en ${pct(c)} de los anuncios. Al filtrar se ocultan los que no lo tienen.` : "";
}

/* ---------- Panel de filtros: secciones plegables con resumen de lo elegido ---------- */
const PANEL_ABIERTO = new Set();
const PANEL_CERRADO = new Set();
function interruptor(campo, titulo, ayuda, on) {
  return `<label class="fila-switch"><span class="fs-txt">${titulo}${ayuda ? `<small>${ayuda}</small>` : ""}</span>
    <input type="checkbox" role="switch" data-campo="${campo}" ${on ? "checked" : ""}><span class="sw" aria-hidden="true"></span></label>`;
}
function acordeon(clave, icono, titulo, valor, cuerpo, activo) {
  const abierto = PANEL_ABIERTO.has(clave) && !PANEL_CERRADO.has(clave);
  return `<details class="acord ${activo ? "activo" : ""}" data-sec="${clave}" ${abierto ? "open" : ""}>
    <summary><span class="ac-ico" aria-hidden="true">${icono}</span><span class="ac-tit">${titulo}</span>
      <span class="ac-val">${valor}</span><span class="ac-flecha" aria-hidden="true"></span></summary>
    <div class="ac-cuerpo">${cuerpo}</div></details>`;
}
function renderPanel() {
  const cuerpo = $("#panelCuerpo");
  const scroll = cuerpo.scrollTop;
  const partes = [];
  const seg = (campo, ops, actual) => `<div class="segmento ancho">${ops.map(([v, t]) =>
    `<button class="${actual === v ? "on" : ""}" data-set="${campo}" data-v="${v}">${t}</button>`).join("")}</div>`;

  // 📍 Ubicación
  {
    const valor = [S.soloSur ? "Solo el sur" : "", S.municipios.length ? S.municipios.join(", ") : "", S.barrios.length ? `${S.barrios.length} barrio${S.barrios.length > 1 ? "s" : ""}` : ""].filter(Boolean).join(" · ") || "Todas";
    const c = interruptor("soloSur", "Solo el sur", "Sabaneta, Envigado, Itagüí, La Estrella y el sur de Medellín", S.soloSur)
      + `<div class="sub-t">Municipio</div><div class="grupo">${chipsMunicipio(filtrarCache("municipio"))}</div>`
      + seccionBarrios();
    partes.push(acordeon("ubic", "📍", "Ubicación", esc(valor), c, !!(S.soloSur || S.municipios.length || S.barrios.length)));
  }
  // 💲 Precio
  {
    const valor = S.pmin && S.pmax ? `${corto(S.pmin)} – ${corto(S.pmax)}` : S.pmax ? `Hasta ${corto(S.pmax)}` : S.pmin ? `Desde ${corto(S.pmin)}` : "Cualquiera";
    const rapidos = [1000000, 1200000, 1500000, 1800000, 2000000].filter((v) => v <= PRECIO_TOPE);
    let c = `<div class="fila2">
        <label class="campo">Mínimo<input class="in-precio" data-campo="pmin" id="pnPmin" inputmode="decimal" enterkeyhint="done" list="sugPrecio" placeholder="Sin mínimo" autocomplete="off"></label>
        <label class="campo">Máximo<input class="in-precio" data-campo="pmax" id="pnPmax" inputmode="decimal" enterkeyhint="done" list="sugPrecio" placeholder="Máximo" autocomplete="off"></label></div>
      <div class="grupo chips-precio">${rapidos.map((v) => `<button class="chip ${S.pmax === v && !S.pmin ? "on" : ""}" data-set="pmax" data-v="${v}">Hasta ${corto(v)}</button>`).join("")}</div>`;
    if (COB.admin > 0.03) c += interruptor("conAdmin", "Sumar la administración", "Solo cuando el anuncio la informa", S.conAdmin);
    partes.push(acordeon("precio", "💲", "Precio", esc(valor), c, !!(S.pmin || (S.pmax && S.pmax !== PRESUPUESTO))));
  }
  // 🛏 Tamaño
  {
    const valor = [S.hab ? `${S.hab}+ hab` : "", S.banos ? `${S.banos}+ baños` : "", S.area ? `${S.area}+ m²` : ""].filter(Boolean).join(" · ") || "Cualquiera";
    const lb = filtrarCache("banos");
    const nSin = contarAptos(lb.filter((x) => dato(x, "banos").v == null));
    let c = `<div class="sub-t">Habitaciones</div>${seg("hab", [[0, "Todas"], [1, "1+"], [2, "2+"], [3, "3+"], [4, "4+"]], S.hab)}
      <div class="sub-t">Baños</div>${seg("banos", [[0, "Todos"], [1, "1+"], [2, "2+"], [3, "3+"]], S.banos)}`;
    if (S.banos) c += interruptor("banosSinDato", "Incluir los que no dicen cuántos baños", `${NF.format(nSin)} anuncios no lo informan`, S.banosSinDato);
    if (COB.area >= MIN_COB) {
      c += `<div class="sub-t">Área mínima</div>${seg("area", [[0, "Cualquiera"], [40, "40 m²"], [50, "50 m²"], [60, "60 m²"], [80, "80 m²"]], S.area)}`;
    }
    partes.push(acordeon("tam", "🛏", "Tamaño", valor, c, !!(S.hab || S.banos || S.area)));
  }
  // 🏷 Estrato
  if (COB.estrato >= MIN_COB) {
    const cE = contar(filtrarCache("estrato"), (x) => dato(x, "estrato").v);
    const presentes = [...new Set(TODOS.map((x) => dato(x, "estrato").v).filter((e) => e >= 1 && e <= 6))].sort((a, b) => a - b);
    const c = `<div class="grupo">${presentes.map((e) => {
      const on = S.estratos.includes(e); const n = cE.get(e) || 0;
      return `<button class="chip ${on ? "on" : ""}" data-toggle="estratos" data-v="${e}" aria-pressed="${on}" ${!n && !on ? "disabled" : ""}>Estrato ${e} <span class="n">${n}</span></button>`;
    }).join("")}</div><p class="nota-p">Puedes elegir varios.</p>`;
    partes.push(acordeon("estrato", "🏷", "Estrato", S.estratos.length ? S.estratos.slice().sort().join(", ") : "Cualquiera", c, S.estratos.length > 0));
  }
  // 🕒 Publicación
  {
    const nBaj = contarAptos(filtrarCache("bajaron").filter((x) => bajada(x)));
    const nHoy = contarAptos(filtrarCache("novedad").filter((x) => esNuevo(x, 1)));
    const valor = [S.reciente ? `Últimos ${S.reciente} días` : "", S.novedad === 1 ? "Nuevos hoy" : "", S.bajaron ? "Bajaron de precio" : ""].filter(Boolean).join(" · ") || "Cualquier fecha";
    const c = `<div class="sub-t">Publicado en los últimos</div>${seg("reciente", [[0, "Cualquiera"], [7, "7 días"], [15, "15 días"], [30, "30 días"]], S.reciente)}`
      + interruptor("novedad", "Solo los nuevos de hoy", `${nHoy} aparecieron en las últimas 24 h`, S.novedad === 1)
      + interruptor("bajaron", "Solo los que bajaron de precio", `${nBaj} en tus resultados`, S.bajaron)
      + `<p class="nota-p">Solo se muestran anuncios de los últimos ${META.publicacion_max_dias || 60} días.</p>`;
    partes.push(acordeon("pub", "🕒", "Publicación", valor, c, !!(S.reciente || S.novedad || S.bajaron)));
  }
  // 🛡 Confianza
  {
    const tieneDif = TODOS.some((x) => x.dif != null);
    const nBuen = tieneDif ? contarAptos(filtrarCache("buenPrecio").filter(esBuenPrecio)) : 0;
    const nAl = contarAptos(filtrarCache("sinAlertas").filter((x) => x.alertas));
    const valor = [S.recoBarrio === "solo_si" ? "Barrios ✅" : S.recoBarrio === "ocultar_no" ? "Sin barrios ⛔" : "", S.buenPrecio ? "Buen precio" : "", S.sinAlertas ? "Sin alertas" : ""].filter(Boolean).join(" · ") || "Todo";
    let c = "";
    if (INFO_BARRIO.size) {
      c += `<div class="sub-t">Barrio según nuestra investigación</div>${seg("recoBarrio", [["", "Todos"], ["ocultar_no", "Sin ⛔"], ["solo_si", "Solo ✅"]], S.recoBarrio)}
        <p class="nota-p">✅ recomendado · ⚠️ con precaución · ⛔ no recomendado (seguridad, terreno y servicios).</p>`;
    }
    if (tieneDif) c += interruptor("buenPrecio", "Solo buen precio", `15% o más barato que apartamentos parecidos · ${nBuen} en tus resultados`, S.buenPrecio);
    c += interruptor("sinAlertas", "Ocultar anuncios sospechosos", `Precio muy bajo, sin fotos o fotos repetidas · ${nAl} en tus resultados`, S.sinAlertas);
    partes.push(acordeon("conf", "🛡", "Confianza", valor, c, !!(S.recoBarrio || S.buenPrecio || S.sinAlertas)));
  }
  // ⚙ Más opciones
  {
    const cf = contar(filtrarCache("fuente"), (x) => x.fuente);
    const fuentes = [...new Set(TODOS.map((x) => x.fuente))].sort();
    const nVid = contarAptos(filtrarCache("conVideo").filter((x) => x.v));
    const valor = [S.conVideo ? "Con video" : "", S.fuentes.length ? S.fuentes.join(", ") : "", !S.dup ? "Sin agrupar" : ""].filter(Boolean).join(" · ") || "—";
    const c = interruptor("conVideo", "Solo con video", `${nVid} en tus resultados`, S.conVideo)
      + `<div class="sub-t">Portal</div><div class="grupo">${fuentes.map((f) => {
        const on = S.fuentes.includes(f); const n = cf.get(f) || 0;
        return `<button class="chip ${on ? "on" : ""}" data-toggle="fuentes" data-v="${esc(f)}" aria-pressed="${on}" ${!n && !on ? "disabled" : ""}>${esc(f)} <span class="n">${NF.format(n)}</span></button>`;
      }).join("")}</div>`
      + interruptor("dup", "Juntar el mismo apartamento de varios portales", "Una sola tarjeta con enlaces a los demás", S.dup);
    partes.push(acordeon("mas", "⚙", "Más opciones", esc(valor), c, !!(S.conVideo || S.fuentes.length || !S.dup)));
  }

  cuerpo.innerHTML = partes.join("");
  campoPrecio($("#pnPmin"), "min");
  campoPrecio($("#pnPmax"), "max");
  filtrarListaBarrios();
  cuerpo.scrollTop = scroll;
}
// Recordar qué secciones abrió o cerró la persona
$("#panelCuerpo").addEventListener("toggle", (e) => {
  const d = e.target.closest && e.target.closest("details.acord");
  if (!d) return;
  const k = d.dataset.sec;
  if (d.open) { PANEL_ABIERTO.add(k); PANEL_CERRADO.delete(k); } else { PANEL_ABIERTO.delete(k); PANEL_CERRADO.add(k); }
}, true);

function activosLista() {
  const a = [];
  if (S.favs) return [[`★ Viendo tus ${favs.size} favoritos (sin otros filtros)`, () => (S.favs = false)]];
  if (S.q) a.push([`“${S.q}”`, () => (S.q = "")]);
  S.municipios.forEach((m) => a.push([m, () => { S.municipios = S.municipios.filter((x) => x !== m); podarBarrios(); }, true]));
  S.barrios.forEach((k) => a.push([`Barrio: ${NOMBRE_BARRIO.get(k) || k.split("|")[1]}`, () => (S.barrios = S.barrios.filter((x) => x !== k))]));
  if (S.pmin || (S.pmax && S.pmax !== PRESUPUESTO)) {
    const t = S.pmin && S.pmax ? `${corto(S.pmin)} – ${corto(S.pmax)}` : S.pmax ? `Hasta ${corto(S.pmax)}` : `Desde ${corto(S.pmin)}`;
    a.push([t + (S.conAdmin ? " (con adm.)" : ""), () => { S.pmin = 0; S.pmax = PRESUPUESTO; }]);
  }
  if (S.hab) a.push([`${S.hab}+ hab`, () => (S.hab = 0), true]);
  if (S.banos) a.push([`${S.banos}+ baños${S.banosSinDato ? " (y sin dato)" : ""}`, () => (S.banos = 0), true]);
  if (S.area) a.push([`${S.area}+ m²`, () => (S.area = 0)]);
  S.estratos.forEach((e) => a.push([`Estrato ${e}`, () => (S.estratos = S.estratos.filter((x) => x !== e))]));
  S.fuentes.forEach((f) => a.push([f, () => (S.fuentes = S.fuentes.filter((x) => x !== f))]));
  if (S.novedad) a.push([`Nuevos ${S.novedad === 1 ? "24 h" : S.novedad + " días"}`, () => (S.novedad = 0)]);
  if (S.bajaron) a.push(["Bajaron de precio", () => (S.bajaron = false)]);
  if (S.favs) a.push(["Favoritos", () => (S.favs = false)]);
  if (S.descartados) a.push(["Viendo descartados", () => (S.descartados = false)]);
  if (S.buenPrecio) a.push(["Buen precio", () => (S.buenPrecio = false)]);
  if (S.recoBarrio) a.push([S.recoBarrio === "solo_si" ? "Solo barrios ✅" : "Sin barrios ⛔", () => (S.recoBarrio = "")]);
  if (S.soloSur) a.push(["🧭 Solo el sur", () => (S.soloSur = false), true]);
  if (S.conVideo) a.push(["▶ Con video", () => (S.conVideo = false)]);
  if (S.reciente) a.push([`🕒 Publicados ≤ ${S.reciente} días`, () => (S.reciente = 0), S.reciente === 15]);
  if (S.sinAntiguos) a.push([`Sin los de +${DIAS_ANTIGUO} días`, () => (S.sinAntiguos = false)]);
  if (S.sinAlertas) a.push(["Sin alertas", () => (S.sinAlertas = false)]);
  return a;
}
let ACTIVOS = [];
function renderActivos() {
  ACTIVOS = activosLista();
  const cont = $("#activos");
  cont.hidden = !ACTIVOS.length;
  cont.classList.toggle("solo-barra", ACTIVOS.length > 0 && ACTIVOS.every((x) => x[2]));
  cont.innerHTML = ACTIVOS.map(([t, , enBarra], i) => `<button class="pastilla ${enBarra ? "en-barra" : ""}" data-quitar="${i}">${esc(t)} <i aria-label="Quitar">✕</i></button>`).join("")
    + (ACTIVOS.length > 1 ? `<button class="enlace" data-quitar="todo">Limpiar todo</button>` : "");
  const enBarra = (S.q ? 1 : 0) + S.municipios.length + S.barrios.length + (S.pmin || (S.pmax && S.pmax !== PRESUPUESTO) ? 1 : 0)
    + (S.hab ? 1 : 0) + (S.banos ? 1 : 0) + (S.reciente === 15 ? 1 : 0) + (S.soloSur ? 1 : 0) + (S.favs ? 1 : 0);
  const n = ancho() ? Math.max(0, ACTIVOS.length - enBarra) : ACTIVOS.length;
  $("#contFiltros").hidden = !n;
  $("#contFiltros").textContent = n;
  medirArriba();
}

/** Alto de la cabecera + barra de filtros (fija) para que el mapa ocupe exactamente el resto. */
if (window.ResizeObserver) {
  const ro = new ResizeObserver(() => medirArriba());
  window.addEventListener("DOMContentLoaded", () => { ro.observe($("#barraFiltros")); ro.observe($(".cabecera")); });
  if (document.readyState !== "loading") { ro.observe($("#barraFiltros")); ro.observe($(".cabecera")); }
}
function medirArriba() {
  $("#q").placeholder = ancho() ? "Buscar barrio o inmobiliaria" : "Buscar barrio…";
  // En celular la barra de filtros no es fija: el mapa solo descuenta la cabecera
  const fija = getComputedStyle($("#barraFiltros")).position === "sticky";
  const h = $(".cabecera").offsetHeight + (fija ? $("#barraFiltros").offsetHeight : 0);
  document.documentElement.style.setProperty("--arriba", h + "px");
  if (mapa) setTimeout(() => mapa.invalidateSize(), 50);
}

function renderResumen(antesDeZona = RES) {
  if (S.descartados && !descartados.size) S.descartados = false;
  if (!opcionesOrden().some(([v]) => v === S.orden)) S.orden = "nuevo";
  $("#total").textContent = `${NF.format(RES.length)} apartamento${RES.length === 1 ? "" : "s"}`;
  const at = [];
  const nNuevos = contarAptos(filtrarCache("novedad").filter((x) => esNuevo(x, 1)));
  if (nNuevos || S.novedad === 1) at.push(`<button class="atajo nuevo ${S.novedad === 1 ? "on" : ""}" data-atajo="nuevos">● ${nNuevos} nuevo${nNuevos === 1 ? "" : "s"} hoy</button>`);
  const nBaj = contarAptos(filtrarCache("bajaron").filter((x) => bajada(x)));
  if (nBaj || S.bajaron) at.push(`<button class="atajo bajo ${S.bajaron ? "on" : ""}" data-atajo="bajaron">↓ ${nBaj} bajaron</button>`);
  if (antesDeZona.length) {
    const pr = antesDeZona.map(precioTotal).sort((a, b) => a - b);
    at.push(`<span class="atajo dato-info" title="Precio mediano de los resultados">Mediana ${corto(pr[Math.floor(pr.length / 2)])}</span>`);
  }
  if (favs.size || S.favs) at.unshift(`<button class="atajo fav ${S.favs ? "on" : ""}" data-atajo="favs" aria-pressed="${!!S.favs}" title="${S.favs ? "Volver a todos los resultados" : "Ver solo tus favoritos (sin otros filtros)"}">★ ${S.favs ? "Viendo favoritos" : "Favoritos"} <span class="n">${favs.size}</span></button>`);
  if (descartados.size) at.push(`<button class="atajo ${S.descartados ? "on" : ""}" data-atajo="descartados" aria-pressed="${!!S.descartados}" title="Mostrar u ocultar los que marcaste como «No me interesa»">✕ ${descartados.size} descartado${descartados.size === 1 ? "" : "s"}</button>`);
  $("#atajos").innerHTML = at.join("");
  $("#verResultados").textContent = `Ver ${NF.format(RES.length)} apartamento${RES.length === 1 ? "" : "s"}`;
  $("#orden").innerHTML = opcionesOrden().map(([v, t]) => `<option value="${v}" ${S.orden === v ? "selected" : ""}>${t}</option>`).join("");
  $("#avisoZona").hidden = !(S.zona && mapaVisible());
}

/* =========================================================
   Render: tarjetas
   ========================================================= */
/* Iconos simples (trazo) para la ficha */
const ICO = {
  hab: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 18v-6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v6M3 18h18M3 18v2m18-2v2M6 10V7a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3m0 0V7a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3"/></svg>',
  banos: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h16v2a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5v-2zM6 12V6a2 2 0 0 1 3.5-1.3M8 19l-1 2m10-2 1 2"/></svg>',
  area: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h16v16H4zM4 9h3M4 14h3M9 4v3M14 4v3"/></svg>',
  parq: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 16V11l2-5h10l2 5v5M5 16h14M5 16v2m14-2v2M5 11h14M8 13.5h.01M16 13.5h.01"/></svg>',
  estrato: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h16M6 20V10m4 10V6m4 14v-8m4 8V8"/></svg>',
  m2: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M16 7.5c0-1.4-1.8-2.5-4-2.5s-4 1.1-4 2.5S9.8 10 12 10s4 1.1 4 2.5S14.2 15 12 15s-4-1.1-4-2.5"/></svg>',
};
const plural = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`;

/** "2 hab · 2 baños · 45 m²" siempre con los tres datos ("s/d" = sin dato). */
function specsCortas(it) {
  const h = dato(it, "hab").v, b = dato(it, "banos").v, a = dato(it, "area").v;
  return [
    h ? `${h} hab` : "hab s/d",
    b ? `${b} ${b === 1 ? "baño" : "baños"}` : "baños s/d",
    a ? `${a} m²` : "m² s/d",
  ].join(" · ");
}

/** Valor de un dato; si este portal no lo trae, lo toma del mismo apartamento publicado en otro portal. */
function dato(it, k) {
  const ok = (v) => v != null && v !== "" && v !== 0; // 0 = el portal no lo informó
  if (ok(it[k])) return { v: it[k] };
  if (ok(it["_" + k])) return { v: it["_" + k], de: it["_" + k + "_de"] };
  const o = (it._otras || []).find((x) => ok(x[k]));
  return o ? { v: o[k], de: o.fuente } : { v: null };
}
/** Una vez al cargar: completa datos faltantes con los del mismo apartamento en otro portal. */
function completarDuplicados() {
  // Portales que cobran por contactar (Rentola): si se sabe de qué página copiaron el anuncio,
  // todos los enlaces llevan a esa página original, que es gratis y tiene el contacto
  for (const x of TODOS) {
    if (PORTALES_DE_PAGO.has(x.fuente) && x.orig && /^https?:\/\//.test(x.orig)) {
      x.urlPago = x.url;
      x.url = x.orig;
      x.sitio = `${portalDe(x.orig)} (vía ${x.fuente})`;
    }
  }
  const G = {};
  for (const x of TODOS) if (x.dup != null) (G[x.dup] ||= []).push(x);
  GRUPOS = {};
  for (const [d, v] of Object.entries(G)) GRUPOS[d] = ordenarVersiones(v);
  const ok = (v) => v != null && v !== "" && v !== 0;
  for (const x of TODOS) {
    if (x.dup == null) continue;
    for (const k of ["hab", "banos", "area", "estrato", "parq", "piso"]) {
      if (ok(x[k])) continue;
      const o = G[x.dup].find((y) => y !== x && ok(y[k]));
      if (o) { x["_" + k] = o[k]; x["_" + k + "_de"] = o.fuente; }
    }
  }
}

/** Ficha de 3 datos principales, siempre en el mismo orden para comparar fácil. */
function ficha(it) {
  const celda = (ico, d, fmt, uno, varios) => {
    if (d.v == null) return `<div class="dato vacio" title="El portal no informa este dato">${ICO[ico]}<strong>Sin dato</strong><span>${varios}</span></div>`;
    const nota = d.de ? ` title="Dato tomado de ${esc(d.de)}"` : "";
    return `<div class="dato"${nota}>${ICO[ico]}<strong>${fmt(d.v)}${d.de ? "<sup>*</sup>" : ""}</strong><span>${d.v === 1 ? uno : varios}</span></div>`;
  };
  return `<div class="ficha">
    ${celda("hab", dato(it, "hab"), (v) => v, "habitación", "habitaciones")}
    ${celda("banos", dato(it, "banos"), (v) => v, "baño", "baños")}
    ${celda("area", dato(it, "area"), (v) => `${v} m²`, "área", "área")}
    ${(() => {
      const d = dato(it, "estrato");
      if (d.v == null && it.estr_b) {
        return `<div class="dato estimado" title="El portal no informa el estrato. ${it.estr_b} es el estrato de la mayoría de anuncios cercanos que sí lo dicen (${it.estr_n}). Confírmalo con el anunciante.">${ICO.estrato}<strong>≈${it.estr_b}</strong><span>${/barrio/.test(it.estr_n || "") ? "típico barrio" : "típico zona"}</span></div>`;
      }
      return celda("estrato", d, (v) => v, "estrato", "estrato");
    })()}
  </div>`;
}

/** Datos secundarios: solo los que el portal (o el mismo anuncio en otro portal) informa. */
function extras(it) {
  const e = [];
  const parq = dato(it, "parq"), piso = dato(it, "piso");
  if (parq.v) e.push(`<span class="extra">${ICO.parq}${plural(parq.v, "parqueadero", "parqueaderos")}</span>`);
  if (piso.v) e.push(`<span class="extra">Piso ${piso.v}</span>`);
  if (it.admin) e.push(`<span class="extra">Adm. ${pesos(it.admin)}</span>`);
  else if (it.admin_incl) e.push(`<span class="extra">Adm. incluida</span>`);

  const amen = (it.amen || []).slice(0, 4);
  return `<div class="extras">${e.join("")}</div>${amen.length ? `<div class="amen">${amen.map((a) => `<span>${esc(a)}</span>`).join("")}</div>` : ""}`;
}

/** Etiqueta corta junto al precio: "▼ 14% bajo similares" (el detalle va en el tooltip). */
function chipVs(it) {
  if (it.dif == null || !it.comp) return "";
  const p = Math.round(Math.abs(it.dif) * 100);
  if (p < 5) return "";
  const barato = it.dif < 0;
  const sospechoso = (it.alertas || []).includes("precio_bajo");
  const ayuda = `Precio típico de ${it.comp.txt}: ${pesos(it.ref)} (comparado con ${it.comp.n} anuncios)`;
  return `<span class="vs-chip ${barato && !sospechoso ? "barato" : "caro"}" title="${esc(ayuda)}">${barato ? "▼" : "▲"} ${p}% ${barato ? "bajo" : "sobre"} similares</span>`;
}

/** Fila de botones: llamar, WhatsApp, mapa y ver anuncio. */
function accionesTarjeta(it) {
  const tel = soloDigitos(it.tel), wa = soloDigitos(it.wa);
  const telIntl = tel ? (tel.startsWith("57") ? tel : "57" + tel) : "";
  const waIntl = wa ? (wa.startsWith("57") ? wa : "57" + wa) : "";
  const msg = encodeURIComponent(`Hola, vi este apartamento en arriendo (${pesos(it.precio)}, ${lugarDe(it)}) y quisiera más información: ${it.url}`);
  return `<div class="botones-t">
    ${tel ? `<a class="b-tel" href="tel:+${telIntl}" title="Llamar al ${esc(formatoTel(tel))}">📞 ${esc(formatoTel(tel))}</a>` : ""}
    ${wa ? `<a class="b-wa" href="https://wa.me/${waIntl}?text=${msg}" target="_blank" rel="noopener noreferrer">WhatsApp</a>` : ""}
    ${it.lat && it.lon ? `<button class="b-mapa" data-acc="mapa" title="Ver en el mapa">Mapa</button>` : ""}
    <a class="ver" href="${esc(urlOk(it.url))}" target="_blank" rel="noopener noreferrer" title="${esc(it.url)}">Ver anuncio ↗</a>
  </div>`;
}

/** Línea pequeña: de qué portal viene, dónde más está y cómo contactar si no hay teléfono. */
function lineaOrigen(it) {
  const varias = S.dup && it.dup != null && (GRUPOS[it.dup] || []).length > 1;
  const partes = [`Sacado de <a href="${esc(urlOk(it.url))}" target="_blank" rel="noopener noreferrer">${esc(sitioDe(it))}</a>`,
    esc(tipoOrigen(it))];
  if (varias) partes.push(it === originalDe(it) ? "<b>original</b>" : "copia");
  if (esDePago(it)) partes.push("⚠ Rentola cobra por contactar");
  else if (!soloDigitos(it.tel) && !soloDigitos(it.wa)) partes.push(CONTACTO_CON_FORMULARIO.has(it.fuente) ? "el teléfono lo dan tras llenar un formulario" : "teléfono en el anuncio");
  else if (it.ofi) partes.push("línea general de la inmobiliaria (da el código del anuncio)");
  else if (it.anunciante && !/persona natural/i.test(it.anunciante)) partes.push(esc(it.anunciante));
  const otraConTel = !soloDigitos(it.tel) && !soloDigitos(it.wa) && varias && GRUPOS[it.dup].find((v) => v.tel || v.wa);
  if (otraConTel) partes.push(`📞 la versión de ${esc(sitioDe(otraConTel))} trae teléfono`);
  return `<div class="origen">${partes.join(" · ")}</div>${versionesHtml(it, false)}`;
}

/** Canon, administración y total (si se conoce la administración). */
function bloquePrecio(it, baj) {
  const antes = baj ? `<span class="antes" title="Precio anterior">${pesos(it.precio + baj)}</span>` : "";
  if (!it.admin) {
    return `<div class="fila-precio"><span class="precio">${pesos(it.precio)}</span><span class="mes">/mes</span>${antes}</div>`;
  }
  return `<div class="fila-precio"><span class="precio">${pesos(it.precio + it.admin)}</span><span class="mes">/mes total</span>${antes}</div>
    <div class="desglose">Canon ${pesos(it.precio)} + administración ${pesos(it.admin)}</div>`;
}

function tarjeta(it) {
  const nuevo = esNuevo(it, 2);
  const baj = bajada(it);
  const fav = favs.has(it.id), desc = descartados.has(it.id);
  const nf = it.nf || (it.img ? 1 : 0);
  // Solo se muestra lo que el portal informa: fecha de publicación y disponibilidad
  const dPub = diasPublicado(it), ant = antiguedad(it);
  // Siempre una fecha: la de publicación del portal o, si el portal no la da, desde cuándo está en esta página
  const visto = !it.publicado && it.visto1 ? it.visto1.slice(0, 10) : null;
  const publicado = it.publicado && !ant
    ? `<span class="publicado" title="${PORTALES_DE_PAGO.has(it.fuente) ? `Fecha en que ${esc(it.fuente)} copió el anuncio, no la del original` : `Publicado el ${fechaCorta(it.publicado)} según ${esc(it.fuente)}`}">📅 ${PORTALES_DE_PAGO.has(it.fuente) ? `${esc(it.fuente)} lo copió` : "Publicado"} ${haceLargo(dPub)} · ${fechaHora(it.publicado.slice(0, 10), it.pub_h)}</span>`
    : !it.publicado
    ? `<span class="publicado sin-fecha" title="${esc(it.fuente)} no informa la fecha de publicación">📅 ${visto ? `En la página desde ${fechaCorta(visto)}` : `${esc(it.fuente)} no da la fecha`}</span>` : "";
  const avisoAnt = ant === "alto"
    ? `<div class="aviso-antiguo alto" title="Publicado el ${fechaCorta(it.publicado)} según ${esc(it.fuente)}">⚠ Publicado ${haceLargo(dPub)}${it.disp ? ". El portal aún lo muestra, pero" : ":"} lo más probable es que ya no esté disponible. Confirma antes de ir.</div>`
    : ant === "medio"
    ? `<div class="aviso-antiguo" title="Publicado el ${fechaCorta(it.publicado)} según ${esc(it.fuente)}">Publicado ${haceLargo(dPub)}: confirma que siga disponible.</div>` : "";
  const disp = it.disp === true && ant !== "alto"
    ? `<span class="disp" title="El portal lo reportó como disponible ${hace(it.visto)}">✓ Disponible</span>` : "";
  return `<article class="tarjeta ${desc ? "descartada" : ""} ${ant === "alto" ? "vieja" : ""}" data-id="${esc(it.id)}">
    <div class="foto" data-acc="visor" role="button" tabindex="0" aria-label="Ver fotos${it.v ? " y video" : ""}">
      ${it.img ? `<img src="${esc(urlOk(it.img))}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="portadaRota(this)" onload="ajustarFoto(this)">` : `<span class="sin-foto">🏠</span>`}
      <span class="etiquetas">
        ${imperdible(it.id) ? `<span class="etq imperdible" title="${esc(imperdible(it.id).razon)}">⭐ IMPERDIBLE</span>` : ""}
        ${nuevo ? `<span class="etq nuevo">NUEVO</span>` : ""}
        ${ant === "alto" ? `<span class="etq antiguo">ANTIGUO</span>` : ""}
        ${baj ? `<span class="etq bajo">↓ ${corto(baj)}</span>` : ""}
        ${esBuenPrecio(it) ? `<span class="etq buen">BUEN PRECIO</span>` : ""}
      </span>
      ${nf > 1 ? `<button class="g-nav ant" data-acc="g-ant" aria-label="Foto anterior">${CHEV.ant}</button><button class="g-nav sig" data-acc="g-sig" aria-label="Foto siguiente">${CHEV.sig}</button>` : ""}
      <span class="medios">
        ${nf ? `<span class="medio" data-cont>📷 ${nf}</span>` : ""}
        ${it.v ? `<span class="medio video">▶ Video</span>` : ""}
        ${it.t ? `<span class="medio">360° Recorrido</span>` : ""}
      </span>
    </div>
    <span class="acciones">
      <button class="acc" data-acc="compartir" aria-label="Compartir enlace a este apartamento" title="Compartir / copiar enlace">${ICO_COMPARTIR}</button>
      <button class="acc ${fav ? "fav-on" : ""}" data-acc="fav" aria-label="${fav ? "Quitar de favoritos" : "Guardar en favoritos"}" title="${fav ? "Quitar de favoritos" : "Guardar"}">${fav ? "★" : "☆"}</button>
      <button class="acc" data-acc="desc" aria-label="${desc ? "Restaurar" : "Descartar"}" title="${desc ? "Restaurar" : "No me interesa"}">${desc ? "↺" : "✕"}</button>
    </span>
    <div class="info">
      ${bloquePrecio(it, baj)}${chipVs(it)}
      <div class="lugar" title="${esc(it.barrio || "")}">📍 ${esc(lugarDe(it))}</div>
      ${ficha(it)}
      ${extras(it)}
      <button class="ver-detalles" data-acc="visor">Ver todos los detalles →</button>
      ${publicado || disp ? `<div class="estado">${[publicado, disp].filter(Boolean).join(" · ")}</div>` : ""}
      ${avisoAnt}
      ${lineaBarrio(it)}
      ${bloqueAlertas(it)}
      ${accionesTarjeta(it)}
      ${lineaOrigen(it)}
    </div>
  </article>`;
}

/* ---------- Contacto del anuncio (solo lo que el portal publica) ---------- */
const soloDigitos = (s) => String(s || "").replace(/\D/g, "");
function formatoTel(d) {
  // 573017203405 → 301 720 3405 · 576044449898 → (604) 444 9898
  let n = d.startsWith("57") && d.length > 10 ? d.slice(2) : d;
  if (n.length === 10 && n.startsWith("3")) return `${n.slice(0, 3)} ${n.slice(3, 6)} ${n.slice(6)}`;
  if (n.length === 10 && n.startsWith("60")) return `(${n.slice(0, 3)}) ${n.slice(3, 6)} ${n.slice(6)}`;
  return n;
}
function filaContacto(it) {
  const tel = soloDigitos(it.tel), wa = soloDigitos(it.wa);
  const quien = it.anunciante && !/persona natural/i.test(it.anunciante) ? ` · ${esc(it.anunciante)}` : "";
  if (!tel && !wa) {
    if (esDePago(it)) return `<div class="contacto sin-tel">⚠ Rentola cobra por contactar y aún no sabemos de qué página copió este anuncio</div>`;
    if (CONTACTO_CON_FORMULARIO.has(it.fuente)) return `<div class="contacto sin-tel">📞 ${esc(sitioDe(it))} da el teléfono después de llenar un formulario en su página${quien}</div>`;
    return `<div class="contacto sin-tel">📞 Teléfono en el anuncio de ${esc(sitioDe(it))}${quien}</div>`;
  }
  const telIntl = tel ? (tel.startsWith("57") ? tel : "57" + tel) : "";
  const waIntl = wa ? (wa.startsWith("57") ? wa : "57" + wa) : "";
  const msg = encodeURIComponent(`Hola, vi este apartamento en arriendo (${pesos(it.precio)}, ${lugarDe(it)}) y quisiera más información: ${it.url}`);
  return `<div class="contacto">
    ${tel ? `<a class="tel" href="tel:+${telIntl}" title="Llamar">📞 ${esc(formatoTel(tel))}</a>` : ""}
    ${wa ? `<a class="wa" href="https://wa.me/${waIntl}?text=${msg}" target="_blank" rel="noopener noreferrer" title="Escribir por WhatsApp">WhatsApp</a>` : ""}
    ${quien ? `<span class="tenue">${quien.replace(" · ", "")}</span>` : ""}
  </div>`;
}

/** "18% más barato que similares" con la referencia en el tooltip. */
function lineaVs(it) {
  if (it.dif == null || !it.comp) return "";
  const p = Math.round(Math.abs(it.dif) * 100);
  const ayuda = `Precio típico de ${it.comp.txt}: ${pesos(it.ref)} (comparado con ${it.comp.n} anuncios)`;
  if (p < 5) return `<div class="vs normal" title="${esc(ayuda)}"><span class="chip-vs">Precio normal</span><span class="ref">para ${esc(it.comp.txt)}</span></div>`;
  const barato = it.dif < 0;
  // Si el precio es sospechosamente bajo no se presenta como algo bueno (va en gris, junto a la alerta)
  const clase = barato && !(it.alertas || []).includes("precio_bajo") ? "barato" : "caro";
  return `<div class="vs ${clase}" title="${esc(ayuda)}">
    <span class="chip-vs">${barato ? "▼" : "▲"} ${p}% ${barato ? "más barato" : "más caro"}</span>
    <span class="ref">que ${esc(it.comp.txt)} (típico ${corto(it.ref)})</span></div>`;
}

const TEXTO_ALERTA = {
  precio_bajo: "Precio muy por debajo de apartamentos parecidos. Verifica antes de pagar cualquier depósito.",
  sin_fotos: "El anuncio no tiene fotos.",
  foto_repetida: "La foto principal aparece en otro anuncio diferente.",
  area_dudosa: "El área no parece coherente con el tipo de apartamento.",
};
function bloqueAlertas(it) {
  if (!it.alertas) return "";
  const corto = { precio_bajo: "Precio sospechosamente bajo", sin_fotos: "Sin fotos", foto_repetida: "Foto repetida en otros anuncios", area_dudosa: "Área dudosa" };
  const detalle = it.alertas.map((a) => TEXTO_ALERTA[a] || a).join(" ");
  return `<details class="alerta"><summary>⚠ ${esc(it.alertas.map((a) => corto[a] || a).join(" · "))}</summary>${esc(detalle)}</details>`;
}

/* =========================================================
   Recomendaciones (aprenden de tus favoritos y descartados)
   ========================================================= */
let PERFIL = null;
function calcularPerfil() {
  const fav = TODOS.filter((x) => favs.has(x.id));
  if (!fav.length) { PERFIL = null; return; }
  const med = (arr) => { const a = arr.filter((v) => v != null).sort((x, y) => x - y); return a.length ? a[Math.floor(a.length / 2)] : null; };
  const cuenta = (arr) => arr.reduce((m, v) => (v ? m.set(v, (m.get(v) || 0) + 1) : m), new Map());
  const desc = TODOS.filter((x) => descartados.has(x.id));
  PERFIL = {
    n: fav.length,
    precio: med(fav.map((x) => x.precio)),
    area: med(fav.map((x) => x.area)),
    hab: med(fav.map((x) => x.hab)),
    municipios: cuenta(fav.map((x) => x.municipio)),
    barrios: cuenta(fav.map((x) => (x.barrio || "").toLowerCase())),
    barriosNo: cuenta(desc.map((x) => (x.barrio || "").toLowerCase())),
  };
}
const cacheAfinidad = new Map();
function afinidad(it) {
  if (!PERFIL) return 0;
  if (cacheAfinidad.has(it.id)) return cacheAfinidad.get(it.id);
  const P = PERFIL;
  let s = 0;
  s += 2 * ((P.municipios.get(it.municipio) || 0) / P.n);
  const b = (it.barrio || "").toLowerCase();
  if (b) s += 2.5 * ((P.barrios.get(b) || 0) / P.n) - 1.5 * Math.min(1, (P.barriosNo.get(b) || 0) / 3);
  if (P.precio) s += 2 * Math.max(0, 1 - Math.abs(it.precio - P.precio) / P.precio / 0.35);
  if (P.area && it.area) s += 1 * Math.max(0, 1 - Math.abs(it.area - P.area) / P.area / 0.4);
  if (P.hab && it.hab) s += it.hab === P.hab ? 1.2 : Math.abs(it.hab - P.hab) === 1 ? 0.4 : -0.5;
  if (it.dif != null) s += Math.max(-0.5, Math.min(0.8, -it.dif * 2));
  if (it.alertas) s -= 2;
  cacheAfinidad.set(it.id, s);
  return s;
}
function razonAfinidad(it) {
  const r = [];
  if (PERFIL.municipios.has(it.municipio)) r.push(it.municipio);
  if (it.barrio && PERFIL.barrios.has(it.barrio.toLowerCase())) r.push(`barrio ${it.barrio}`);
  if (PERFIL.hab && it.hab === PERFIL.hab) r.push(`${it.hab} hab`);
  if (PERFIL.precio && Math.abs(it.precio - PERFIL.precio) / PERFIL.precio < 0.15) r.push("precio parecido");
  return r.length ? `Como tus favoritos: ${r.slice(0, 3).join(", ")}` : "Parecido a tus favoritos";
}

function miniTarjeta(it, extra = "") {
  return `<button class="mini-t" data-abrir="${esc(it.id)}" title="Ver fotos y detalles">
    ${it.img ? `<img src="${esc(urlOk(it.img))}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.opacity=0">` : `<span class="sin-foto-m">🏠</span>`}
    <span class="mi"><strong>${pesos(it.precio)}</strong>
      <span class="t">${esc(lugarDe(it))}</span>
      <span class="t">${esc(specsCortas(it))}</span>
      ${extra}</span></button>`;
}

/** Resumen del día + mejores oportunidades, sobre los resultados filtrados. */
function renderResumenDia(lista) {
  const cont = $("#resumenDia");
  if (LS.get("ocultarResumen", false) || !lista.length) {
    cont.innerHTML = LS.get("ocultarResumen", false) ? `<button class="enlace" data-resumen="mostrar">Mostrar resumen del día</button>` : "";
    return;
  }
  const nuevos = lista.filter((x) => esNuevo(x, 1));
  const bajaron = lista.filter((x) => bajada(x));
  const buenos = lista.filter(esBuenPrecio);
  const recientes = lista.filter((x) => esNuevo(x, 3) && esBuenPrecio(x));
  const top = (recientes.length >= 3 ? recientes : lista.filter((x) => esBuenPrecio(x) && antiguedad(x) !== "alto"))
    .slice().sort((a, b) => puntajeOportunidad(b) - puntajeOportunidad(a)).slice(0, 8);
  const titTop = recientes.length >= 3 ? "Mejores oportunidades de los últimos 3 días" : "Mejores oportunidades en tus resultados";
  cont.innerHTML = `<section class="bloque" aria-label="Resumen del día">
    <div class="bloque-cab"><h2>Resumen de hoy</h2><button class="enlace" data-resumen="ocultar">Ocultar</button></div>
    <div class="cifras">
      <span><b>${nuevos.length}</b> nuevos en 24 h</span>
      <span><b>${bajaron.length}</b> bajaron de precio</span>
      <span><b>${buenos.length}</b> con buen precio</span>
    </div>
    ${bloqueImperdibles(lista)}
    ${top.length ? `<div class="razon" style="margin-bottom:6px">${titTop}</div>
    <div class="carrusel">${top.map((x) => miniTarjeta(x, `<span class="bueno">▼ ${Math.round(-x.dif * 100)}% vs. similares</span>`)).join("")}</div>` : ""}
  </section>`;
}

function bloqueImperdibles(lista) {
  if (!IA_SEL || !(IA_SEL.imperdibles || []).length) return "";
  const porId = new Map(TODOS.map((x) => [x.id, x]));
  const its = IA_SEL.imperdibles.map((s) => ({ s, x: porId.get(s.id) })).filter((o) => o.x);
  if (!its.length) return "";
  return `<div class="razon imp-tit">⭐ Imperdibles de hoy según la IA <span class="tenue">(${esc(hace(IA_SEL.fecha))})</span></div>
    <div class="carrusel">${its.map(({ s, x }) => miniTarjeta(x, `<span class="razon imp-razon">${esc(s.razon)}</span>`)).join("")}</div>
    ${(IA_SEL.zonas || []).length ? `<div class="imp-zonas"><div class="imp-sub">📍 Mejores zonas para buscar ahora</div>
      <div class="imp-chips">${IA_SEL.zonas.map((z) => `<span class="imp-chip" title="${esc(z.razon)}">${esc(z.barrio)} <small>${esc(z.municipio)}</small></span>`).join("")}</div></div>` : ""}
    ${IA_SEL.consejo ? `<div class="imp-consejo">💡 ${esc(IA_SEL.consejo)}</div>` : ""}`;
}
function renderRecomendados(lista) {
  const cont = $("#recomendados");
  if (!PERFIL) { cont.innerHTML = ""; return; }
  const candidatos = lista.filter((x) => !favs.has(x.id) && !descartados.has(x.id) && !x.alertas && antiguedad(x) !== "alto");
  const top = candidatos.slice().sort((a, b) => afinidad(b) - afinidad(a)).slice(0, 8);
  if (!top.length) { cont.innerHTML = ""; return; }
  cont.innerHTML = `<section class="bloque" aria-label="Recomendados para ti">
    <div class="bloque-cab"><h2>Recomendados para ti</h2><span class="razon">Según tus ${PERFIL.n} favorito${PERFIL.n > 1 ? "s" : ""} ★ y descartados ✕</span></div>
    <div class="carrusel" style="margin-top:8px">${top.map((x) => miniTarjeta(x, `<span class="razon">${esc(razonAfinidad(x))}</span>`)).join("")}</div>
  </section>`;
}

/* =========================================================
   Vista "Análisis de mercado" (se recalcula con tus filtros)
   ========================================================= */
let MERCADO = { historial: {} };
const mediana = (arr) => { const a = arr.filter((v) => v != null && !isNaN(v)).sort((x, y) => x - y); if (!a.length) return null; const m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; };
const cuantil = (arr, q) => { const a = arr.slice().sort((x, y) => x - y); return a.length ? a[Math.min(a.length - 1, Math.floor(q * a.length))] : null; };
const ppmDe = (x) => (x.area >= 18 && x.area <= 400 ? x.precio / x.area : null);
const tipAttr = (lineas) => `data-tip="${esc(lineas.join("|"))}"`;

function barrasH(filas, fmt) {
  const max = Math.max(...filas.map((f) => f.v), 1);
  return `<div class="barras">${filas.map((f) => `<div class="fila-b" ${tipAttr(f.tip)}>
      <span class="nom" title="${esc(f.nombre)}">${esc(f.nombre)} ${f.n != null ? `<small>(${f.n})</small>` : ""}</span>
      <span class="pista"><span class="barra" style="width:calc(${(f.v / max) * 100}% - 64px)"></span><span class="val">${fmt(f.v)}</span></span>
    </div>`).join("")}</div>`;
}

function ticks(max) {
  const paso = Math.pow(10, Math.floor(Math.log10(max / 4 || 1)));
  const opciones = [1, 2, 2.5, 5, 10].map((m) => m * paso);
  const p = opciones.find((o) => max / o <= 5) || opciones[opciones.length - 1];
  const t = [];
  for (let v = 0; v <= max + p * 0.001; v += p) t.push(v);
  if (t[t.length - 1] < max) t.push(t[t.length - 1] + p);
  return t;
}

/** Columnas SVG (una serie): datos [{etq, v, tip}] */
function columnas(datos, fmtEje, cadaEtq = 1, refX = null) {
  const W = 640, H = 200, iz = 44, ab = 22, ar = 8;
  const t = ticks(Math.max(...datos.map((d) => d.v), 1));
  const max = t[t.length - 1];
  const banda = (W - iz) / datos.length;
  const ancho = Math.min(24, banda - 2);
  const y = (v) => ar + (H - ar - ab) * (1 - v / max);
  let s = `<svg class="svg-graf" viewBox="0 0 ${W} ${H}" role="img">`;
  for (const v of t) s += `<line class="rejilla" x1="${iz}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text x="${iz - 6}" y="${y(v) + 4}" text-anchor="end">${fmtEje(v)}</text>`;
  datos.forEach((d, i) => {
    const x = iz + i * banda + (banda - ancho) / 2;
    const h = (H - ar - ab) * (d.v / max);
    const r = Math.min(4, h);
    if (d.v > 0) s += `<rect class="hit" x="${iz + i * banda}" y="${ar}" width="${banda}" height="${H - ar - ab}" ${tipAttr(d.tip)}/>`
      + `<path class="col" d="M${x},${y(0)} V${y(d.v) + r} Q${x},${y(d.v)} ${x + r},${y(d.v)} H${x + ancho - r} Q${x + ancho},${y(d.v)} ${x + ancho},${y(d.v) + r} V${y(0)} Z" pointer-events="none"/>`;
    if (i % cadaEtq === 0) s += `<text x="${x + ancho / 2}" y="${H - 6}" text-anchor="middle">${esc(d.etq)}</text>`;
  });
  if (refX != null) {
    const xr = iz + refX * banda;
    s += `<line x1="${xr}" x2="${xr}" y1="${ar}" y2="${y(0)}" stroke="var(--texto)" stroke-width="1.5" stroke-dasharray="0"/><text x="${xr + 4}" y="${ar + 10}" style="fill:var(--texto)">Tu presupuesto</text>`;
  }
  s += `<line class="eje" x1="${iz}" x2="${W}" y1="${y(0)}" y2="${y(0)}"/></svg>`;
  return s;
}

/** Líneas SVG (varias series): series [{nombre, color, puntos:[{x:"YYYY-MM-DD", v}]}] */
function lineas(series, fmtEje) {
  const W = 640, H = 220, iz = 52, ab = 22, ar = 10, de = 92;
  const fechas = [...new Set(series.flatMap((s) => s.puntos.map((p) => p.x)))].sort();
  const vals = series.flatMap((s) => s.puntos.map((p) => p.v));
  const minV = Math.min(...vals.filter((v) => v != null)), maxV = Math.max(...vals.filter((v) => v != null));
  const paso = Math.max(100000, Math.ceil(((maxV - minV) * 1.3 || maxV * 0.2) / 4 / 100000) * 100000);
  const lo = Math.max(0, Math.floor((minV - paso * 0.5) / paso) * paso);
  const t = [];
  for (let v = lo; v <= maxV + paso * 0.5; v += paso) t.push(v);
  const hi = t[t.length - 1];
  const x = (f) => iz + (W - iz - de) * (fechas.length > 1 ? fechas.indexOf(f) / (fechas.length - 1) : 0.5);
  const y = (v) => ar + (H - ar - ab) * (1 - (v - lo) / (hi - lo || 1));
  let s = `<svg class="svg-graf" viewBox="0 0 ${W} ${H}" role="img">`;
  for (const v of t) s += `<line class="rejilla" x1="${iz}" x2="${W - de}" y1="${y(v)}" y2="${y(v)}"/><text x="${iz - 6}" y="${y(v) + 4}" text-anchor="end">${fmtEje(v)}</text>`;
  const cada = Math.max(1, Math.ceil(fechas.length / 6));
  fechas.forEach((f, i) => { if (i % cada === 0 || i === fechas.length - 1) s += `<text x="${x(f)}" y="${H - 6}" text-anchor="middle">${fechaCorta(f)}</text>`; });
  const finales = [];
  for (const se of series) {
    const pts = se.puntos.filter((p) => p.v != null);
    if (!pts.length) continue;
    s += `<polyline class="linea" style="stroke:${se.color}" points="${pts.map((p) => `${x(p.x)},${y(p.v)}`).join(" ")}"/>`;
    for (const p of pts) s += `<circle class="pto" cx="${x(p.x)}" cy="${y(p.v)}" r="4" style="fill:${se.color}" ${tipAttr([se.nombre, `${fechaCorta(p.x)}: ${pesos(p.v)}`])}/>`;
    const u = pts[pts.length - 1];
    finales.push({ y: y(u.v), x: x(u.x), txt: `${se.nombre} ${corto(u.v)}` });
  }
  // Etiquetas al final de cada línea (sin solaparse: se separan mínimo 13px con línea guía)
  finales.sort((a, b) => a.y - b.y);
  for (let i = 1; i < finales.length; i++) if (finales[i].y - finales[i - 1].y < 13) finales[i].ly = (finales[i - 1].ly ?? finales[i - 1].y) + 13;
  for (const f of finales) {
    const ly = f.ly ?? f.y;
    if (f.ly != null) s += `<line x1="${f.x + 5}" y1="${f.y}" x2="${W - de + 6}" y2="${ly}" stroke="var(--eje)" stroke-width="1"/>`;
    s += `<text class="etq-fin" x="${W - de + 8}" y="${ly + 4}">${esc(f.txt)}</text>`;
  }
  s += `<line class="eje" x1="${iz}" x2="${W - de}" y1="${H - ab}" y2="${H - ab}"/></svg>`;
  return s;
}

function renderAnalisis(lista) {
  const cont = $("#colAnalisis");
  if (!lista.length) { cont.innerHTML = `<div class="vacio"><p class="vacio-titulo">No hay datos con estos filtros</p></div>`; return; }
  const precios = lista.map(precioTotal);
  const nuevos7 = contarAptos(lista.filter((x) => esNuevo(x, 7)));
  const baj = contarAptos(lista.filter((x) => bajada(x)));
  const buenos = contarAptos(lista.filter(esBuenPrecio));
  const kpi = (l, v, s = "") => `<div class="kpi"><div class="k-l">${l}</div><div class="k-v">${v}</div>${s ? `<div class="k-s">${s}</div>` : ""}</div>`;
  let h = `<p class="an-intro">Análisis de <b>${NF.format(lista.length)}</b> apartamentos que coinciden con tus filtros (sin contar repetidos entre portales). Se recalcula en cada actualización y cada vez que cambias un filtro.</p>`;
  h += `<div class="kpis">
    ${kpi("Precio mediano", corto(mediana(precios)), `La mitad cuesta menos de esto · rango típico ${corto(cuantil(precios, 0.25))} – ${corto(cuantil(precios, 0.75))}`)}
    ${kpi("Nuevos en 7 días", NF.format(nuevos7))}
    ${kpi("Bajaron de precio", NF.format(baj))}
    ${kpi("Con buen precio", NF.format(buenos), "≥15% más baratos que similares")}
  </div><div class="graficos">`;

  // 1. Precio mediano por municipio
  const porMun = {};
  for (const x of lista) (porMun[x.municipio] ||= []).push(x);
  const filasMun = Object.entries(porMun).filter(([, l]) => l.length >= 3).map(([m, l]) => {
    const p = l.map(precioTotal);
    return { nombre: m, n: l.length, v: mediana(p), tip: [m, `Mediana ${pesos(mediana(p))}`, `Rango típico ${corto(cuantil(p, 0.25))} – ${corto(cuantil(p, 0.75))}`, `${l.length} apartamentos`] };
  }).sort((a, b) => a.v - b.v);
  if (filasMun.length) h += `<div class="graf"><h3>Precio mediano por municipio</h3><p class="sub">De menor a mayor · entre paréntesis, cuántos anuncios</p>${barrasH(filasMun, corto)}</div>`;

  // 2. Tabla municipio × habitaciones
  const habs = [1, 2, 3, 4];
  const filasT = Object.entries(porMun).sort((a, b) => b[1].length - a[1].length).map(([m, l]) => {
    const celdas = habs.map((hb) => { const g = l.filter((x) => (hb === 4 ? x.hab >= 4 : x.hab === hb)); return g.length >= 3 ? `<td title="${g.length} anuncios">${corto(mediana(g.map(precioTotal)))}</td>` : `<td class="vacio-c" title="Menos de 3 anuncios">–</td>`; });
    return `<tr><td>${esc(m)}</td>${celdas.join("")}<td>${l.length}</td></tr>`;
  });
  h += `<div class="graf"><h3>Precio mediano según habitaciones</h3><p class="sub">“–” = menos de 3 anuncios para calcularlo</p>
    <div class="desplazable"><table class="tabla-an"><tr><th>Municipio</th><th>1 hab</th><th>2 hab</th><th>3 hab</th><th>4+ hab</th><th>Anuncios</th></tr>${filasT.join("")}</table></div></div>`;

  // 3. Barrios por arriendo mediano
  const porBarrio = new Map();
  for (const x of lista) {
    const k = claveBarrio(x);
    if (k) porBarrio.set(k, [...(porBarrio.get(k) || []), x]);
  }
  const filasB = [...porBarrio].filter(([, l]) => l.length >= 5).map(([k, l]) => {
    const nombre = `${NOMBRE_BARRIO.get(k) || l[0].barrio}, ${k.split("|")[0]}`;
    const p = l.map(precioTotal);
    const f = INFO_BARRIO.get(k);
    return { nombre: (f ? recoDe(f).ico + " " : "") + nombre, n: l.length, v: mediana(p), tip: [nombre, `Arriendo mediano ${pesos(mediana(p))}`, `Rango típico ${corto(cuantil(p, 0.25))} – ${corto(cuantil(p, 0.75))}`, f ? recoDe(f).txt : "", `${l.length} apartamentos`].filter(Boolean) };
  }).sort((a, b) => a.v - b.v);
  if (filasB.length >= 3) {
    const n = Math.min(10, Math.floor(filasB.length / 2) || 1);
    h += `<div class="graf"><h3>Barrios más económicos</h3><p class="sub">Arriendo mediano · barrios con 5 o más anuncios · el ícono es la recomendación del barrio</p>${barrasH(filasB.slice(0, n), corto)}</div>`;
    h += `<div class="graf"><h3>Barrios más costosos</h3><p class="sub">Arriendo mediano · barrios con 5 o más anuncios</p>${barrasH(filasB.slice(-n).reverse(), corto)}</div>`;
  }

  // 4. Distribución de precios
  const paso = 250000;
  const minP = Math.floor(Math.min(...precios) / paso) * paso, maxP = Math.floor(Math.max(...precios) / paso) * paso + paso;
  const cubetas = [];
  for (let v = minP; v < maxP; v += paso) {
    const n = precios.filter((p) => p >= v && p < v + paso).length;
    cubetas.push({ etq: corto(v).replace("$", "").replace(" M", ""), v: n, tip: [`${corto(v)} – ${corto(v + paso)}`, `${n} apartamentos`] });
  }
  const iPres = S.pmax ? (S.pmax - minP) / paso : null;
  h += `<div class="graf"><h3>¿Cuántos hay en cada rango de precio?</h3><p class="sub">Apartamentos por rango de $250.000 (en millones)</p>${columnas(cubetas, (v) => NF.format(v), Math.ceil(cubetas.length / 10), iPres != null && iPres > 0 && iPres < cubetas.length ? iPres : null)}</div>`;

  // 5. Nuevos por día (según la fecha de publicación del portal)
  const hoy = new Date();
  const dias = [];
  for (let i = 29; i >= 0; i--) { const d = new Date(hoy - i * DIA); dias.push(d.toLocaleDateString("en-CA", { timeZone: "America/Bogota" })); }
  const porDia = {};
  for (const x of lista) { const f = x.publicado || (!x.ini && x.visto1 ? x.visto1.slice(0, 10) : null); if (f) porDia[f] = (porDia[f] || 0) + 1; }
  const datosD = dias.map((f) => ({ etq: fechaCorta(f), v: porDia[f] || 0, tip: [fechaCorta(f), `${porDia[f] || 0} publicados`] }));
  h += `<div class="graf"><h3>Publicaciones por día (últimos 30 días)</h3><p class="sub">Según la fecha de publicación que informa cada portal</p>${columnas(datosD, (v) => NF.format(v), 5)}</div>`;

  // 6. Evolución del precio mediano (historial diario guardado en cada actualización)
  const hist = MERCADO.historial || {};
  const fechas = Object.keys(hist).sort();
  const muns = (META.municipios || []).filter((m) => fechas.some((f) => hist[f][m]));
  const colores = ["var(--serie-1)", "var(--serie-2)", "var(--serie-3)", "var(--serie-4)", "var(--serie-5)"];
  const series = muns.slice(0, 5).map((m, i) => ({ nombre: m, color: colores[i], puntos: fechas.map((f) => ({ x: f, v: hist[f][m] ? hist[f][m].precio : null })) }));
  h += `<div class="graf"><h3>Evolución del precio mediano por municipio</h3><p class="sub">Todos los anuncios disponibles de cada día (no depende de tus filtros)</p>`;
  if (fechas.length >= 2) {
    h += lineas(series, corto) + `<div class="leyenda-g">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.nombre)}</span>`).join("")}</div>`;
  } else {
    h += `<p class="nota-g">Este gráfico se llena solo: cada actualización guarda el precio mediano del día. Hoy hay ${fechas.length} día registrado; la línea aparece desde el segundo día.</p>`;
  }
  if (fechas.length) {
    h += `<details class="datos-g"><summary>Ver datos en tabla</summary><div class="desplazable"><table class="tabla-an"><tr><th>Fecha</th>${muns.map((m) => `<th>${esc(m)}</th>`).join("")}</tr>
      ${fechas.slice(-14).reverse().map((f) => `<tr><td>${fechaCorta(f)}</td>${muns.map((m) => `<td>${hist[f][m] ? corto(hist[f][m].precio) : "–"}</td>`).join("")}</tr>`).join("")}</table></div></details>`;
  }
  h += `</div></div>`;
  cont.innerHTML = h;
}

/* Tooltip compartido para gráficos (data-tip="línea1|línea2") */
(() => {
  const tip = $("#tip");
  document.addEventListener("mouseover", (e) => {
    const t = e.target.closest("[data-tip]");
    if (!t) { tip.hidden = true; return; }
    const [titulo, ...resto] = t.dataset.tip.split("|");
    tip.innerHTML = `<b>${esc(titulo)}</b>${resto.map(esc).join("<br>")}`;
    tip.hidden = false;
  });
  document.addEventListener("mousemove", (e) => {
    if (tip.hidden) return;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.min(window.innerWidth - w - 8, e.clientX + 14) + "px";
    tip.style.top = (e.clientY + h + 20 > window.innerHeight ? e.clientY - h - 12 : e.clientY + 16) + "px";
  });
})();

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
function fechaCorta(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MESES[m - 1]}${y !== new Date().getFullYear() ? " " + y : ""}`;
}

/* =========================================================
   Galerías (se cargan bajo demanda por fragmentos)
   ========================================================= */
const fragmentos = new Map();
function fragmentoGaleria(id) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (Math.imul(h, 31) + id.charCodeAt(i)) >>> 0;
  return h % (META.shards || 64);
}
/** Portada de una tarjeta que no abre: se intenta con la siguiente foto; si no hay, "sin foto". */
/** Foto vertical o cuadrada: se muestra completa (sin recortar) con un fondo difuminado de la misma foto. */
function ajustarFoto(img) {
  const caja = img.closest(".foto");
  if (!caja || !img.naturalWidth) return;
  const vertical = img.naturalWidth / img.naturalHeight < 1.25;
  caja.classList.toggle("contener", vertical);
  if (vertical) caja.style.setProperty("--fondo", `url("${img.currentSrc || img.src}")`);
}
window.ajustarFoto = ajustarFoto;
function sinFotoTarjeta(card) {
  card.querySelector(".foto img")?.replaceWith(Object.assign(document.createElement("span"), { className: "sin-foto", textContent: "🏠" }));
  card.querySelectorAll("[data-cont], .g-nav").forEach((x) => x.remove());
}
function portadaRota(img) {
  FOTOS_MALAS.add(img.getAttribute("src"));
  img.style.opacity = 0;
  const card = img.closest(".tarjeta");
  if (card) moverFotoTarjeta(card, 0); else img.remove();
}
window.portadaRota = portadaRota;
const CHEV = {
  ant: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>',
  sig: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg>',
};
/** Fotos que no abrieron en esta visita (enlace roto en el portal): se saltan. */
const FOTOS_MALAS = new Set();
async function galeria(it) {
  const n = fragmentoGaleria(it.id);
  if (!fragmentos.has(n)) {
    fragmentos.set(n, fetch(`data/galeria/${String(n).padStart(2, "0")}.json`)
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .catch(() => { fragmentos.delete(n); return {}; }));
  }
  const g = (await fragmentos.get(n))[it.id] || {};
  const fotos = (g.f && g.f.length ? g.f : it.img ? [it.img] : []).filter((u) => /^https?:\/\//i.test(u) && !FOTOS_MALAS.has(u));
  return { fotos, video: g.v || null, tour: g.t || null };
}
function idYoutube(url) {
  const m = /(?:youtube(?:-nocookie)?\.com\/(?:embed\/|watch\?v=|shorts\/)|youtu\.be\/)([\w-]{11})/.exec(url || "");
  return m ? m[1] : null;
}
const buscarItem = (id) => RES.find((x) => x.id === id) || TODOS.find((x) => x.id === id);

/* ---------- Compartir: enlace que abre la ficha del apartamento en esta página ---------- */
const ICO_COMPARTIR = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7"/><path d="M12 15V3"/><path d="M7 8l5-5 5 5"/></svg>`;
function enlaceAnuncio(it) {
  return `${location.origin}${location.pathname}#a=${encodeURIComponent(it.id)}`;
}
async function compartirAnuncio(it) {
  if (!it) return;
  const url = enlaceAnuncio(it);
  const texto = `${pesos(it.precio)} · ${[specsCortas(it), lugarDe(it)].filter(Boolean).join(" · ")}`;
  if (navigator.share && ES_TACTIL) {
    try { await navigator.share({ title: "Apartamento en arriendo", text: texto, url }); return; }
    catch (e) { if (e && e.name === "AbortError") return; }  // el usuario cerró el menú de compartir
  }
  let ok = false;
  try { await navigator.clipboard.writeText(url); ok = true; }
  catch {
    const ta = document.createElement("textarea");
    ta.value = url; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;opacity:0;top:0;left:0";
    document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
  }
  toast(ok ? "🔗 Enlace copiado: al abrirlo se ve este apartamento" : `Copia este enlace: ${url}`);
}
/** Si la página se abrió con #a=<id>, abre directo la ficha de ese apartamento. */
function abrirDesdeEnlace() {
  const m = location.hash.match(/^#a=(.+)$/);
  if (!m) return;
  const id = decodeURIComponent(m[1]);
  history.replaceState(history.state, "", location.pathname + location.search);  // limpia el #a= de la barra
  const it = TODOS.find((x) => x.id === id);
  if (it) abrirVisor(it);
  else toast("Ese apartamento ya no está disponible (se arrendó o el portal lo retiró)");
}
window.addEventListener("hashchange", abrirDesdeEnlace);

async function moverFotoTarjeta(card, paso) {
  const it = buscarItem(card.dataset.id);
  if (!it) return;
  const { fotos } = await galeria(it);
  if (!card.isConnected) return;                      // la tarjeta se redibujó mientras cargaba
  if (!fotos.length) { sinFotoTarjeta(card); return; }
  if (fotos.length < 2) { card.querySelectorAll(".g-nav").forEach((b) => b.remove()); if (paso) return; }
  const i = ((+card.dataset.i || 0) + paso + fotos.length) % fotos.length;
  card.dataset.i = i;
  let img = card.querySelector(".foto img");
  if (!img) {
    img = document.createElement("img");
    img.referrerPolicy = "no-referrer";
    card.querySelector(".foto").prepend(img);
    card.querySelector(".sin-foto")?.remove();
  }
  img.style.opacity = 1;
  img.onerror = () => {
    // Cada error quita una foto para siempre, así que esto termina
    FOTOS_MALAS.add(fotos[i]);
    img.style.opacity = 0;
    moverFotoTarjeta(card, paso > 0 ? 0 : paso);
  };
  img.onload = () => ajustarFoto(img);
  img.src = fotos[i];
  const c = card.querySelector("[data-cont]");
  if (c) c.textContent = `📷 ${i + 1}/${fotos.length}`;
  if (fotos.length > 1) new Image().src = fotos[(i + (paso || 1) + fotos.length) % fotos.length]; // precarga la siguiente
}

/* ---------- Avisos cortos con "Deshacer" ---------- */
let tToast = null;
function toast(msg, deshacer, extra) {
  const el = $("#toast");
  if (!el) return;
  el.innerHTML = `<span>${esc(msg)}</span>${extra ? `<button class="toast-btn" data-t="extra">${esc(extra[0])}</button>` : ""}${deshacer ? `<button class="toast-btn" data-t="deshacer">Deshacer</button>` : ""}`;
  el.hidden = false;
  el.onclick = (e) => {
    const b = e.target.closest("[data-t]");
    if (!b) return;
    if (b.dataset.t === "deshacer") deshacer();
    if (b.dataset.t === "extra") extra[1]();
    el.hidden = true;
  };
  clearTimeout(tToast);
  tToast = setTimeout(() => (el.hidden = true), 6000);
}


/* ---------- Ficha completa del apartamento (junto a las fotos) ---------- */
function parecidos(it, n = 4) {
  const kb = claveBarrio(it);
  return TODOS.filter((x) => x.id !== it.id && (it.dup == null || x.dup !== it.dup) && !descartados.has(x.id)
      && x.municipio === it.municipio && (kb ? claveBarrio(x) === kb : true)
      && Math.abs((x.hab || 0) - (it.hab || 0)) <= 1 && Math.abs(x.precio - it.precio) <= 300000)
    .sort((a, b) => Math.abs(a.precio - it.precio) - Math.abs(b.precio - it.precio)).slice(0, n);
}
function detalleHTML(it) {
  const fav = favs.has(it.id), desc = descartados.has(it.id);
  const sec = (titulo, cuerpo) => cuerpo ? `<section class="det-sec"><h3>${titulo}</h3>${cuerpo}</section>` : "";
  // Precio y comparación
  let comp = "";
  if (it.dif != null && it.comp) {
    const p = Math.round(Math.abs(it.dif) * 100);
    const barato = it.dif < 0;
    const sosp = (it.alertas || []).includes("precio_bajo");
    comp = `<p class="det-comp ${p < 5 ? "" : barato && !sosp ? "bueno" : "caro"}">${p < 5 ? "En el precio típico" : `${p}% ${barato ? "más barato" : "más caro"} que el típico`}
      de <b>${esc(it.comp.txt)}</b>: ${pesos(it.ref)} <span class="tenue">(comparado con ${it.comp.n} anuncios)</span></p>`;
  }
  const adm = it.admin ? `Administración: ${pesos(it.admin)}` : it.admin_incl ? "Administración incluida" : "Administración: el portal no la informa";
  // Publicación
  const dPub = diasPublicado(it);
  const pub = [
    it.publicado ? `Publicado el ${fechaCorta(it.publicado)} (${haceLargo(dPub)}) en ${esc(it.fuente)}` : `${esc(it.fuente)} no informa la fecha de publicación`,
    it.disp === true ? `<span class="bueno">✓ El portal lo reporta disponible</span>` : "",
    antiguedad(it) ? `<span class="aviso-txt">Lleva ${haceLargo(dPub).replace("hace ", "")} publicado: confirma que siga disponible.</span>` : "",
  ].filter(Boolean).map((x) => `<li>${x}</li>`).join("");
  // Ubicación
  const ubic = `<p>📍 <b>${esc(lugarDe(it))}</b></p>
    ${it.aprox ? `<p class="tenue">${it.ubic === "municipio" ? "El portal no da la dirección ni el barrio: en el mapa aparece en el centro del municipio." : "El portal solo dice el barrio: en el mapa aparece en el punto general del barrio."}</p>` : ""}
    ${it.lat && it.lon ? `<button class="btn-sec" data-acc="mapa">Ver en el mapa</button>` : ""}`;
  // Barrio
  const f = infoBarrio(it);
  const nb = NOTICIAS.get(claveBarrio(it));
  const barrio = f ? `${lineaBarrio(it)}${f.razon_recomendacion ? `<p class="det-razon">${esc(f.razon_recomendacion)}</p>` : ""}${nb && nb.ia && nb.ia.resumen_reciente ? `<p class="det-ia">🤖 <b>Lo reciente (${esc(fechaCorta(nb.ia.fecha))}):</b> ${esc(nb.ia.resumen_reciente)}</p>` : ""}` : "";
  // Estrato estimado
  const notaEstrato = !it.estrato && it.estr_b ? `<p class="tenue det-nota">≈${it.estr_b}: estrato estimado (${esc(it.estr_n || "")}); el portal no lo informa.</p>` : "";
  const notaOtro = ["hab", "banos", "area", "estrato"].some((k) => !it[k] && it["_" + k]) ? `<p class="tenue det-nota">* Dato tomado del mismo apartamento publicado en otro portal.</p>` : "";
  // Alertas completas
  const alertas = it.alertas ? `<div class="alerta-det">${it.alertas.map((a) => `<p>⚠ ${esc(TEXTO_ALERTA[a] || a)}</p>`).join("")}</div>` : "";
  // Parecidos
  const par = parecidos(it);
  const parHtml = par.length ? `<div class="det-parecidos">${par.map((x) => `<button class="lg-fila" data-abrir="${esc(x.id)}">
      ${x.img ? `<img src="${esc(urlOk(x.img))}" alt="" referrerpolicy="no-referrer" loading="lazy" onerror="this.remove()">` : `<span class="lg-sin">🏠</span>`}
      <span class="lg-txt"><strong>${pesos(x.precio)}</strong><span>${esc(specsCortas(x))}</span><span class="tenue">${esc(lugarDe(x))}</span>
      <span class="lg-fecha">${x.publicado ? `Publicado ${haceLargo(diasPublicado(x))}` : "Sin fecha de publicación"}</span></span></button>`).join("")}</div>` : "";
  return `<div class="detalle-ficha" data-id="${esc(it.id)}">
    <div class="det-cab">
      <div>${bloquePrecio(it, bajada(it))}<p class="tenue det-adm">${adm}</p>
        <p class="det-fecha ${antiguedad(it) ? "vieja" : ""}">📅 ${it.publicado ? `Publicado ${haceLargo(diasPublicado(it))} · ${fechaCorta(it.publicado)}` : `${esc(it.fuente)} no informa la fecha de publicación${it.visto1 ? ` · en esta página desde ${fechaCorta(it.visto1.slice(0, 10))}` : ""}`}</p></div>
      <div class="det-acc">
        <button class="acc-det ${fav ? "on" : ""}" data-acc="fav" aria-label="${fav ? "Quitar de favoritos" : "Guardar en favoritos"}">${fav ? "★ Guardado" : "☆ Guardar"}</button>
        <button class="acc-det" data-acc="compartir" aria-label="Compartir enlace a este apartamento">${ICO_COMPARTIR} Compartir</button>
        <button class="acc-det" data-acc="desc" aria-label="${desc ? "Restaurar" : "No me interesa"}">${desc ? "↺ Restaurar" : "✕ No me interesa"}</button>
      </div>
    </div>
    ${imperdible(it.id) ? `<div class="det-imperdible">⭐ <b>Imperdible según la IA:</b> ${esc(imperdible(it.id).razon)}</div>` : ""}
    ${comp}
    ${it.titulo ? `<p class="det-titulo">${esc(it.titulo)}</p>` : ""}
    ${sec("Características", ficha(it) + extras(it).replace('<div class="amen">', '<div class="amen todas">') + notaEstrato + notaOtro)}
    ${(it.amen || []).length > 4 ? `<div class="amen todas">${it.amen.slice(4).map((a) => `<span>${esc(a)}</span>`).join("")}</div>` : ""}
    ${sec("Ubicación", ubic)}
    ${sec("El barrio", barrio)}
    ${sec("Revisa antes de pagar", alertas)}
    ${sec("Publicación", `<ul class="det-lista">${pub}</ul>`)}
    ${sec("Contacto", accionesTarjeta(it) + lineaOrigen(it).replace(/<div class="versiones-t">[\s\S]*$/, ""))}
    ${sec("Dónde está publicado", versionesHtml(it, true))}
    ${sec("Parecidos en la zona", parHtml)}
  </div>`;
}
function pintarDetalle(it) {
  const d = $("#visorDetalle");
  if (!d || !it) return;
  d.innerHTML = detalleHTML(it);
  d.scrollTop = 0;
}

/* ---------- Visor a pantalla completa ---------- */
const V = { it: null, items: [], i: 0 };
async function abrirVisor(it, inicio = 0) {
  V.it = it;
  pintarDetalle(it);
  $("#visorCuerpo").scrollTop = 0;
  V.items = [];
  V.i = 0;
  $("#visorPrecio").textContent = pesos(it.precio);
  $("#visorLugar").textContent = [
    lugarDe(it),
    specsCortas(it),
    it.parq && plural(it.parq, "parqueadero", "parqueaderos"),
    it.estrato && `estrato ${it.estrato}`,
  ].filter(Boolean).join(" · ");
  $("#visorMedio").innerHTML = `<span class="cargando">Cargando fotos…</span>`;
  $("#visorMinis").innerHTML = "";
  $("#visorEnlaces").innerHTML = "";
  $("#visorCont").textContent = "";
  $("#visor").hidden = false;
  focoPrevio = document.activeElement;
  if (!(history.state && history.state.visor)) history.pushState({ visor: 1 }, "");
  setTimeout(() => $("#visorCerrar")?.focus({ preventScroll: true }), 30);
  document.body.style.overflow = "hidden";
  const g = await galeria(it);
  if (V.it !== it) return;
  V.items = g.fotos.map((u) => ({ tipo: "foto", u }));
  const yt = idYoutube(g.video);
  if (yt) V.items.push({ tipo: "youtube", id: yt });
  else if (g.video && /^https?:\/\/.+\.(mp4|webm)(\?|$)/i.test(g.video)) V.items.push({ tipo: "mp4", u: g.video });
  V.i = Math.min(inicio, Math.max(0, V.items.length - 1));
  $("#visorEnlaces").innerHTML =
    (g.tour ? `<a class="sec" href="${esc(urlOk(g.tour))}" target="_blank" rel="noopener noreferrer">360° Recorrido virtual</a>` : "")
    + `<a href="${esc(urlOk(it.url))}" target="_blank" rel="noopener noreferrer">Ver anuncio en ${esc(sitioDe(it))}</a>`;
  $("#visorMinis").innerHTML = V.items.map((x, i) => x.tipo === "foto"
    ? `<button class="mini" data-mini="${i}" aria-label="Foto ${i + 1}"><img src="${esc(x.u)}" alt="" loading="lazy" referrerpolicy="no-referrer" data-u="${esc(x.u)}"></button>`
    : `<button class="mini video" data-mini="${i}" aria-label="Video">▶ Video</button>`).join("");
  $$("#visorMinis img").forEach((im) => { im.onerror = () => quitarFotoVisor(im.dataset.u); });
  pintarVisor();
}
/** Quita del visor una foto que no abre y vuelve a pintar. */
function quitarFotoVisor(u) {
  FOTOS_MALAS.add(u);
  if ($("#visor").hidden) return;
  const k = V.items.findIndex((x) => x.u === u);
  if (k < 0) return;
  V.items.splice(k, 1);
  if (V.i > k || V.i >= V.items.length) V.i = Math.max(0, V.i - 1);
  $("#visorMinis").innerHTML = V.items.map((x, i) => x.tipo === "foto"
    ? `<button class="mini" data-mini="${i}" aria-label="Foto ${i + 1}"><img src="${esc(x.u)}" alt="" loading="lazy" referrerpolicy="no-referrer" data-u="${esc(x.u)}"></button>`
    : `<button class="mini video" data-mini="${i}" aria-label="Video">▶ Video</button>`).join("");
  $$("#visorMinis img").forEach((im) => { im.onerror = () => quitarFotoVisor(im.dataset.u); });
  pintarVisor();
}
function pintarVisor() {
  const n = V.items.length;
  const medio = $("#visorMedio");
  $("#visorAnt").hidden = $("#visorSig").hidden = n < 2;
  if (!n) { medio.innerHTML = `<span class="cargando">Este anuncio no tiene fotos.</span>`; return; }
  const x = V.items[V.i];
  if (x.tipo === "foto") {
    medio.innerHTML = `<img src="${esc(x.u)}" alt="Foto ${V.i + 1}" referrerpolicy="no-referrer">`;
    medio.querySelector("img").onerror = () => quitarFotoVisor(x.u);
    zoomReset();
    const sig = V.items[(V.i + 1) % n];
    if (sig.tipo === "foto") new Image().src = sig.u;
  } else if (x.tipo === "youtube") {
    medio.innerHTML = `<iframe src="https://www.youtube-nocookie.com/embed/${x.id}?autoplay=1&rel=0" title="Video del apartamento" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe>`;
  } else {
    medio.innerHTML = `<video src="${esc(x.u)}" controls autoplay playsinline></video>`;
  }
  const fotos = V.items.filter((y) => y.tipo === "foto").length;
  $("#visorCont").textContent = x.tipo === "foto" ? `${V.i + 1} / ${fotos}` : "Video";
  $$("#visorMinis .mini").forEach((b, i) => b.classList.toggle("on", i === V.i));
  $(`#visorMinis .mini[data-mini="${V.i}"]`)?.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
}
function moverVisor(paso) {
  if (V.items.length < 2) return;
  V.i = (V.i + paso + V.items.length) % V.items.length;
  pintarVisor();
}
const modoFoto = () => $("#visor").classList.contains("modo-foto");
function entrarModoFoto() {
  $("#visor").classList.add("modo-foto");
  $("#visorVolver").hidden = false;
  history.pushState({ visor: 1, foto: 1 }, "");
}
function salirModoFoto(desdeHistorial) {
  if (!modoFoto()) return;
  $("#visor").classList.remove("modo-foto");
  $("#visorVolver").hidden = true;
  if (!desdeHistorial && history.state && history.state.foto) { esperandoAtras = true; history.back(); }
}
let focoPrevio = null;
function cerrarVisor(desdeHistorial) {
  if ($("#visor").hidden) return;
  const pasos = history.state && history.state.foto ? 2 : 1;   // ficha + foto en grande
  $("#visor").classList.remove("modo-foto");
  $("#visorVolver").hidden = true;
  $("#visor").hidden = true;
  $("#visorMedio").innerHTML = ""; // detiene el video
  $("#visorMinis").innerHTML = "";
  document.body.style.overflow = "";
  V.it = null; V.items = [];
  if (!desdeHistorial && history.state && history.state.visor) { esperandoAtras = true; history.go(-pasos); }
  focoPrevio?.focus?.({ preventScroll: true }); focoPrevio = null;
}
// Botón "atrás" del celular: cierra el visor en vez de salir de la página
let esperandoAtras = false;
window.addEventListener("popstate", () => {
  if (esperandoAtras) { esperandoAtras = false; return; }  // es el "atrás" que hizo el propio cierre
  if (!$("#visor").hidden && modoFoto()) { salirModoFoto(true); return; }  // de foto grande a la ficha
  cerrarVisor(true);
});

function pintarLote() {
  const trozo = RES.slice(pintados, pintados + LOTE);
  if (!trozo.length) return;
  $("#tarjetas").insertAdjacentHTML("beforeend", trozo.map(tarjeta).join(""));
  if (!pintados) $$("#tarjetas .foto img").forEach((im, k) => { if (k < 6) { im.loading = "eager"; im.fetchPriority = "high"; } });
  pintados += trozo.length;
}

function renderLista() {
  $("#tarjetas").innerHTML = "";
  pintados = 0;
  pintarLote();
  const vacio = !RES.length;
  $("#vacio").hidden = !vacio;
  if (vacio) {
    const sug = [];
    if (S.pmax && S.pmax < PRECIO_TOPE) sug.push("subir el precio máximo");
    if (S.pmin) sug.push("bajar el precio mínimo");
    if (S.estratos.length) sug.push("quitar estratos");
    if (S.fuentes.length) sug.push("agregar portales");
    if (S.area) sug.push("bajar el área mínima");
    if (S.conVideo || S.buenPrecio || S.sinAlertas) sug.push("quitar opciones de «Más filtros»");
    if (S.hab) sug.push("bajar las habitaciones");
    if (S.municipios.length) sug.push("agregar otros municipios");
    if (S.novedad || S.reciente) sug.push("ampliar la fecha de publicación");
    if (S.soloSur) sug.push("apagar «Solo el sur»");
    if (S.barrios.length) sug.push("quitar barrios");
    if (S.q) sug.push("borrar la búsqueda");
    if (S.banos) sug.push("bajar los baños");
    if (S.favs) sug.push("salir de Favoritos");
    $("#vacioSugerencia").textContent = sug.length ? `Prueba ${sug.join(", ")}.` : "";
  }
}

/* =========================================================
   Mapa
   ========================================================= */
let mapa = null, grupo = null, capaAprox = null, capaSel = null, marcadores = new Map(), ultimoEncuadre = "";
const mapaVisible = () => vista === "mapa" || (vista === "split" && ancho());

function iniciarMapa() {
  if (mapa || !window.L) return;
  mapa = L.map("mapa", { zoomControl: true, preferCanvas: false }).setView([6.2, -75.59], 12);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(mapa);
  grupo = L.markerClusterGroup({
    showCoverageOnHover: false, maxClusterRadius: 50, spiderfyOnMaxZoom: false, chunkedLoading: true,
    // El clic/toque en un grupo lo maneja "clusterclick" (zoom, abanico o lista según el caso)
    zoomToBoundsOnClick: false,
    iconCreateFunction: (c) => L.divIcon({ html: `<div>${c.getChildCount()}</div>`, className: "cluster", iconSize: [40, 40] }),
  });
  // Computador: resumen al pasar el mouse
  grupo.on("clustermouseover", (e) => {
    if (ES_TACTIL) return;
    e.layer.bindTooltip(resumenGrupo(e.layer), { direction: "top", offset: [0, -18], className: "tip-grupo", opacity: 1 }).openTooltip();
  });
  grupo.on("clustermouseout", (e) => e.layer.unbindTooltip());
  // Clic o toque en un grupo:
  //  - grupo grande (o computador): acerca y separa
  //  - mismo edificio / zoom máximo: abre en abanico
  //  - celular con 8 o menos: lista tocable (con botón "Acercar al grupo")
  grupo.on("clusterclick", (e) => {
    const c = e.layer;
    const b = c.getBounds();
    const mismoPunto = b.getNorthEast().distanceTo(b.getSouthWest()) < 15;
    if (mismoPunto || (ES_TACTIL && c.getChildCount() <= 8)) {
      // Todos en el mismo edificio (o pocos, en celular): lista clara en vez de pines encimados
      grupoActivo = mismoPunto ? null : c;
      const its = itemsGrupo(c);
      const bs = [...new Set(its.map((x) => x.barrio).filter(Boolean))];
      abrirLista(c.getLatLng(), its, mismoPunto
        ? (bs.length > 1
          ? { titulo: `${its.length} apartamentos en el mismo punto`, nota: `El portal los ubica en el mismo punto, pero dicen barrios distintos (${bs.join(", ")}). La ubicación puede no ser exacta.` }
          : { titulo: `${its.length} apartamentos en el mismo edificio`, nota: "Tienen la misma dirección en el mapa." })
        : { zoom: true });
    } else if (mapa.getZoom() >= mapa.getMaxZoom()) {
      c.spiderfy();
    } else {
      c.zoomToBounds({ padding: [40, 40] });
    }
  });
  mapa.addLayer(grupo);
  capaAprox = L.layerGroup().addTo(mapa);
  capaSel = L.layerGroup().addTo(mapa);   // apartamento pedido que no está en los filtros actuales
  mapa.on("moveend", () => { if (!S.zona) return; clearTimeout(tZona); tZona = setTimeout(() => render({ soloLista: true }), 250); });
  // Con una ventana abierta en el mapa se ocultan los controles flotantes para que no la tapen
  mapa.on("popupopen", () => document.body.classList.add("popup-abierto"));
  mapa.on("popupclose", () => document.body.classList.remove("popup-abierto"));
}

// Pantalla táctil (celular). Con ?tactil=1 en la URL se simula, para probar desde el computador.
const ES_TACTIL = window.matchMedia("(hover: none)").matches || new URLSearchParams(location.search).has("tactil");
let grupoActivo = null;
const itemsGrupo = (cluster) => cluster.getAllChildMarkers().map((m) => m.it).filter(Boolean);

/** Resumen corto de un grupo: cuántos, rango de precios, barrios y habitaciones. */
function resumenGrupo(cluster) {
  const its = itemsGrupo(cluster);
  const precios = its.map((x) => x.precio).sort((a, b) => a - b);
  const barrios = contar(its, (x) => (claveBarrio(x) ? NOMBRE_BARRIO.get(claveBarrio(x)) : x.municipio));
  const top = [...barrios].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([b, n]) => `${b}${n > 1 ? ` (${n})` : ""}`);
  const habs = its.map((x) => x.hab).filter(Boolean).sort((a, b) => a - b);
  const rangoHab = habs.length ? (habs[0] === habs[habs.length - 1] ? `${habs[0]} hab` : `${habs[0]} a ${habs[habs.length - 1]} hab`) : "";
  const buenos = its.filter(esBuenPrecio).length;
  return `<div class="rg">
    <b>${its.length} apartamentos</b>
    <div>${precios[0] === precios[precios.length - 1] ? corto(precios[0]) : `${corto(precios[0])} – ${corto(precios[precios.length - 1])}`}${rangoHab ? ` · ${rangoHab}` : ""}</div>
    <div class="tenue">${esc(top.join(", "))}${barrios.size > 3 ? "…" : ""}</div>
    ${buenos ? `<div class="bueno">▼ ${buenos} con buen precio</div>` : ""}
    <div class="tenue rg-ayuda">Clic para acercar y separar</div></div>`;
}

/** Lista tocable de apartamentos (grupo del mismo edificio, zona aproximada o grupo pequeño). */
function listaItems(lista, { titulo, nota, zoom } = {}) {
  const its = [...lista].sort((a, b) => a.precio - b.precio);
  return `<div class="lg">
    <div class="lg-cab"><b>${esc(titulo || `${its.length} apartamentos aquí`)}</b>${nota ? `<div class="lg-nota">${esc(nota)}</div>` : ""}</div>
    ${its.map((x) => `<button class="lg-fila" data-abrir="${esc(x.id)}">
      ${x.img ? `<img src="${esc(urlOk(x.img))}" alt="" referrerpolicy="no-referrer" loading="lazy">` : `<span class="lg-sin">🏠</span>`}
      <span class="lg-txt"><strong>${pesos(x.precio)}</strong>${esBuenPrecio(x) ? ` <em>▼ buen precio</em>` : ""}
        <span>${esc(specsCortas(x))}</span>
        <span class="tenue">${esc(lugarDe(x))} · ${esc(x.fuente)}${x.publicado ? ` · ${haceLargo(diasPublicado(x))}` : ""}</span>
        ${antiguedad(x) === "alto" ? `<span class="lg-viejo">⚠ ${haceLargo(diasPublicado(x))}: probablemente ya no está</span>` : ""}</span></button>`).join("")}
    ${zoom ? `<button class="btn lg-zoom" data-zoom-grupo>Acercar y separar</button>` : ""}</div>`;
}
function abrirLista(latlng, its, opciones) {
  L.popup({ maxWidth: 300, minWidth: 260, className: "popup-grupo", autoPanPadding: [20, 20] })
    .setLatLng(latlng).setContent(listaItems(its, opciones)).openOn(mapa);
}

function popup(it) {
  return `<div class="popup">${it.img ? `<img src="${esc(urlOk(it.img))}" alt="" referrerpolicy="no-referrer" data-abrir="${esc(it.id)}" style="cursor:pointer" onload="ajustarPopup()" onerror="this.remove(); ajustarPopup()">` : ""}
    <div class="p-info">${bloquePrecio(it, bajada(it))}
    <div class="lugar">📍 ${esc(lugarDe(it))}</div>
    ${antiguedad(it) ? `<div class="aviso-antiguo ${antiguedad(it) === "alto" ? "alto" : ""}">⚠ Publicado ${haceLargo(diasPublicado(it))}${antiguedad(it) === "alto" ? ": probablemente ya no está" : ""}</div>` : ""}
    ${it.aprox ? `<div class="aviso-aprox">${it.ubic === "municipio" ? "Sin dirección ni barrio: ubicado en el centro del municipio" : "Ubicación aproximada: punto general del barrio (el portal no da la dirección)"}</div>` : ""}
    ${ficha(it)}${extras(it)}
    <div class="popup-botones"><button class="btn" data-abrir="${esc(it.id)}">Ver detalles y fotos</button>
    <a class="ver" href="${esc(urlOk(it.url))}" target="_blank" rel="noopener noreferrer">Ver en ${esc(sitioDe(it))}</a></div></div></div>`;
}

function renderMapa(lista) {
  if (mapa) { mapa.closePopup(); grupoActivo = null; }
  if (!mapaVisible()) return;
  iniciarMapa();
  if (!mapa) return;
  grupo.clearLayers();
  capaAprox.clearLayers();
  capaSel.clearLayers();
  marcadores.clear();
  const capas = [];
  const zonas = new Map(); // punto aproximado -> anuncios
  for (const it of lista) {
    if (!(it.lat && it.lon)) continue;
    if (it.aprox) {
      const k = `${it.lat.toFixed(4)},${it.lon.toFixed(4)}`;
      if (!zonas.has(k)) zonas.set(k, []);
      zonas.get(k).push(it);
      continue;
    }
    const clase = esNuevo(it, 2) ? "nuevo" : bajada(it) ? "bajo" : "";
    const m = L.marker([it.lat, it.lon], {
      icon: L.divIcon({ className: `pin ${clase}`, html: `<span>${corto(it.precio).replace("$", "").replace(" M", "M")}</span>`, iconSize: null }),
      riseOnHover: true,
    }).bindPopup(() => popup(it), { maxWidth: 260, minWidth: 240 });
    if (!ES_TACTIL) {
      m.on("mouseover", () => resaltarTarjeta(it.id, true));
      m.on("mouseout", () => resaltarTarjeta(it.id, false));
    }
    m.it = it; // para el resumen y la lista de los grupos
    marcadores.set(it.id, m);
    capas.push(m);
  }
  grupo.addLayers(capas);
  const puntos = capas.map((m) => m.getLatLng());
  for (const its of zonas.values()) {
    const ll = L.latLng(its[0].lat, its[0].lon);
    puntos.push(ll);
    const n = its.length;
    const tam = n === 1 ? 30 : Math.min(64, 34 + Math.round(Math.sqrt(n) * 4));
    const m = L.marker(ll, {
      icon: L.divIcon({ className: "zona-aprox", html: `<div>≈${n}</div>`, iconSize: [tam, tam] }),
      zIndexOffset: -500,
    });
    const soloMuni = its.every((x) => x.ubic === "municipio");
    const conBarrio = its.find((x) => x.barrio && x.ubic === "barrio");
    const nombre = soloMuni || !conBarrio ? its[0].municipio : lugarDe(conBarrio);
    const opciones = soloMuni ? {
      titulo: `${n} sin ubicación precisa en ${nombre}`,
      nota: `El portal no da la dirección y no dice el barrio (o no se pudo ubicar). Se muestran en el centro de ${nombre}.`,
    } : {
      titulo: `${n} en ${nombre} (ubicación aproximada)`,
      nota: "El portal solo dice el barrio, no la dirección. Se muestran en el punto general del barrio.",
    };
    m.on("click", () => abrirLista(ll, its, opciones));
    if (!ES_TACTIL) m.bindTooltip(`<b>${esc(opciones.titulo)}</b><div class="tenue">Clic para ver la lista</div>`, { direction: "top", className: "tip-grupo", offset: [0, -tam / 2] });
    if (soloMuni) m.options.icon.options.className += " solo-muni";
    m.aprox = its;
    for (const it of its) marcadores.set(it.id, m);
    capaAprox.addLayer(m);
  }
  const sin = lista.length - capas.length - [...zonas.values()].reduce((a, v) => a + v.length, 0);
  $("#sinGeo").textContent = sin ? `${NF.format(sin)} sin ubicación` : "";
  const clave = S.municipios.join("|") + "#" + S.barrios.join("|") + "#" + vista;
  if (puntos.length && clave !== ultimoEncuadre && !S.zona) {
    ultimoEncuadre = clave;
    const b = L.latLngBounds(puntos);
    // Primero se mide el contenedor (al cambiar de vista estaba oculto) y luego se encuadra
    mapa.invalidateSize();
    mapa.fitBounds(b.pad(0.05), { maxZoom: 15 });
    clearTimeout(tEncuadre);
    tEncuadre = setTimeout(() => { mapa.invalidateSize(); mapa.fitBounds(b.pad(0.05), { maxZoom: 15 }); }, 120);
  }
  setTimeout(() => mapa.invalidateSize(), 60);
}

function resaltarTarjeta(id, on) {
  const el = document.querySelector(`.tarjeta[data-id="${CSS.escape(id)}"]`);
  if (el) el.classList.toggle("resaltada", on);
}
/** Al pasar el mouse por una tarjeta: resalta su pin, o el grupo/zona donde está, y marca el punto. */
let focoMapa = null, iconoActivo = null;
function resaltarPin(id, on) {
  if (iconoActivo) { iconoActivo.classList.remove("activo"); iconoActivo = null; }
  if (focoMapa) { focoMapa.remove(); focoMapa = null; }
  if (!on || !mapa || !mapaVisible()) return;
  const m = marcadores.get(id);
  if (!m) return;
  // Pin suelto, o el grupo (círculo con número) que lo contiene, o la zona aproximada
  const visible = m.aprox ? m : (grupo.getVisibleParent(m) || m);
  if (visible._icon) { visible._icon.classList.add("activo"); iconoActivo = visible._icon; }
  // Anillo en el punto exacto, si está dentro de un grupo
  if (visible !== m || m.aprox) {
    focoMapa = L.circleMarker(m.getLatLng(), { radius: 14, className: "foco-mapa", interactive: false }).addTo(mapa);
  }
}
let tEncuadre = null, tZona = null;
/** Cuando carga la foto de la ventanita del mapa, se reacomoda para que se vea completa. */
window.ajustarPopup = () => { const p = mapa && mapa._popup; if (p && p.isOpen()) p.update(); };
function irAlMapa(id, intento = 0) {
  if (!mapaVisible()) cambiarVista("mapa");
  if (!mapa) return;
  clearTimeout(tEncuadre);
  mapa.invalidateSize();
  const it = buscarItem(id);
  // El marcador del anuncio, o el de su mismo apartamento en otro portal (repetidos agrupados)
  let m = marcadores.get(id);
  if (!m && it && it.dup != null) {
    const otro = TODOS.find((x) => x.dup === it.dup && marcadores.has(x.id));
    if (otro) m = marcadores.get(otro.id);
  }
  if (!m) {
    // No está en los resultados actuales (lo abriste desde "Parecidos", el resumen o recomendados):
    // se muestra igual, con un marcador propio, sin cambiar tus filtros
    if (!it || !it.lat || !it.lon) { toast("Este apartamento no tiene ubicación en el mapa"); return; }
    capaSel.clearLayers();
    const ll = L.latLng(it.lat, it.lon);
    const sel = L.marker(ll, {
      icon: L.divIcon({ className: "pin pin-sel", html: `<span>${corto(it.precio).replace("$", "").replace(" M", "M")}</span>`, iconSize: null }),
      zIndexOffset: 2000,
    }).bindPopup(() => `<div class="aviso-fuera">No está en tus filtros actuales</div>${popup(it)}`, { maxWidth: 260, minWidth: 240 });
    capaSel.addLayer(sel);
    mapa.setView(ll, Math.max(mapa.getZoom(), 16));
    setTimeout(() => sel.openPopup(), 50);
    return;
  }
  if (!m.aprox && !m.__parent && intento < 10) { setTimeout(() => irAlMapa(id, intento + 1), 250); return; }
  if (m.aprox) {
    const x = m.aprox.find((y) => y.id === id) || it;
    mapa.setView(m.getLatLng(), Math.max(mapa.getZoom(), 15));
    L.popup({ maxWidth: 260, minWidth: 240 }).setLatLng(m.getLatLng()).setContent(popup(x)).openOn(mapa);
    return;
  }
  grupo.zoomToShowLayer(m, () => m.openPopup());
}

/* =========================================================
   Render principal
   ========================================================= */
let cacheFiltros = new Map();
function filtrarCache(sin) {
  if (!cacheFiltros.has(sin)) cacheFiltros.set(sin, filtrar(sin));
  return cacheFiltros.get(sin);
}

function render(opts = {}) {
  LS.set("filtros2", S);
  cacheFiltros = new Map();
  const base = filtrarCache(null);
  const agrupados = agrupar(ordenar(base.slice()));
  let lista = agrupados;
  if (S.zona && mapa && mapaVisible()) {
    const b = mapa.getBounds();
    lista = agrupados.filter((x) => x.lat && x.lon && b.contains([x.lat, x.lon]));
  }
  RES = lista;
  renderResumen(agrupados);
  renderLista();
  if (opts.soloLista) return;
  renderResumenDia(agrupados);
  renderRecomendados(agrupados);
  renderBarra();
  renderActivos();
  if ($("#panel").classList.contains("abierto")) renderPanel();
  renderMapa(agrupados);
  if (document.body.dataset.vista === "analisis") renderAnalisis(agrupados);
}

/* =========================================================
   Vista
   ========================================================= */
function cambiarVista(v) {
  vista = v;
  LS.set("vista", v);
  const efectiva = !ancho() && v === "split" ? "lista" : v;
  document.body.dataset.vista = efectiva;
  $$("#vistas button").forEach((b) => b.classList.toggle("on", b.dataset.vista === v));
  $("#flotante").textContent = efectiva === "lista" || efectiva === "split" ? "🗺  Ver mapa" : "☰  Ver lista";
  if (mapaVisible()) { iniciarMapa(); renderMapa(agrupar(ordenar(filtrarCache(null).slice()))); }
  if (efectiva === "analisis" && S) { renderAnalisis(agrupar(ordenar(filtrarCache(null).slice()))); window.scrollTo(0, 0); }
  if (S && S.zona) render({ soloLista: true });
}

/* =========================================================
   Panel y diálogo
   ========================================================= */
function abrirPanel() {
  renderPanel();
  $("#panel").classList.add("abierto");
  $("#panel").setAttribute("aria-hidden", "false");
  $("#velo").hidden = false;
}
function cerrarPanel() {
  $("#panel").classList.remove("abierto");
  $("#panel").setAttribute("aria-hidden", "true");
  $("#velo").hidden = true;
}

function tablaFuentes() {
  const c = META.corridas || [];
  const porF = {};
  for (const x of TODOS) {
    const f = (porF[x.fuente] ||= { total: 0, fotos: 0, geo: 0, fecha: 0, nuevos: 0 });
    f.total++; if ((x.nf || 0) > 1) f.fotos++; if (x.lat) f.geo++; if (x.publicado) f.fecha++; if (esNuevo(x, 1)) f.nuevos++;
  }
  let h = `<h3>Anuncios visibles (activos y disponibles)</h3><table><tr><th>Portal</th><th class="n">Anuncios</th><th class="n">Con galería</th><th class="n">Con mapa</th><th class="n">Con fecha</th><th class="n">Nuevos 24 h</th></tr>`;
  for (const [f, v] of Object.entries(porF).sort((a, b) => b[1].total - a[1].total)) {
    h += `<tr><td>${esc(f)}</td><td class="n">${NF.format(v.total)}</td><td class="n">${pct(v.fotos / v.total)}</td><td class="n">${pct(v.geo / v.total)}</td><td class="n">${pct(v.fecha / v.total)}</td><td class="n">${v.nuevos}</td></tr>`;
  }
  h += `</table><p class="tenue" style="font-size:.8rem">Se ocultan los anuncios que el portal marca como no disponibles y los que no aparecen hace más de ${META.dias_inactivo || 10} días.</p>`;
  h += `<h3>Últimas actualizaciones</h3><table><tr><th>Fecha</th><th>Portal</th><th class="n">Leídos</th><th class="n">Nuevos</th><th class="n">Bajaron</th><th>Estado</th></tr>`;
  for (const r of c.slice(-6).reverse()) {
    for (const [f, v] of Object.entries(r.fuentes || {})) {
      h += `<tr><td>${esc(r.ts.slice(5, 16).replace("T", " "))}</td><td>${esc(f)}</td><td class="n">${v.leidos}</td><td class="n">${v.nuevos}</td><td class="n">${v.bajaron}</td>
        <td class="${v.error || (!v.leidos && !v.omitida) ? "err" : "ok"}">${v.error ? esc(v.error) : v.leidos ? "OK" : v.omitida ? "Sin consulta esta vez" : "0 leídos: posible bloqueo"}</td></tr>`;
    }
  }
  h += `</table>`;
  $("#tablaFuentes").innerHTML = h;
}

/* =========================================================
   Eventos
   ========================================================= */
function aplicarCambio(campo, valor) {
  if (campo === "soloSur") { setSoloSur(!!valor); return; }
  if (campo === "novedad" && typeof valor === "boolean") valor = valor ? 1 : 0;
  if (campo === "pmin" || campo === "pmax") valor = leerPrecio(valor);
  if (campo === "pmin" && valor >= PRECIO_TOPE) {
    toast(`El mínimo no puede ser ${pesos(valor)}: la página solo tiene arriendos hasta ${pesos(PRECIO_TOPE)}`);
    valor = 0;
  }
  else if (["hab", "banos", "area", "novedad", "reciente"].includes(campo)) valor = +valor;
  S[campo] = valor;
  if (campo === "pmin" && S.pmax && S.pmin > S.pmax) S.pmax = 0;
  if (campo === "pmax" && S.pmax && S.pmin > S.pmax) S.pmin = 0;
  render();
}

function actualizarRecomendaciones() {
  calcularPerfil();
  cacheAfinidad.clear();
  renderRecomendados(agrupar(ordenar(filtrarCache(null).slice())));
  $("#orden").innerHTML = opcionesOrden().map(([v, t]) => `<option value="${v}" ${S.orden === v ? "selected" : ""}>${t}</option>`).join("");
}

document.addEventListener("click", (e) => {
  const tv = e.target.closest("[data-version]");
  if (tv) {
    const id = tv.dataset.version;
    elegirVersion(id);
    const enDetalle = tv.closest(".detalle-ficha");
    render();
    if (enDetalle) { const it = buscarItem(id); if (it) abrirVisor(it); }
    return;
  }
  const t = e.target.closest("[data-ficha],[data-filtrar-barrio],[data-toggle],[data-set],[data-quitar],[data-atajo],[data-acc],[data-vista],[data-abrir],[data-resumen]");
  if (!t) return;
  if (t.dataset.ficha) {
    abrirFichaBarrio(t.dataset.ficha);
    return;
  }
  if (t.dataset.filtrarBarrio) {
    const k = t.dataset.filtrarBarrio;
    const mun = k.split("|")[0];
    S.municipios = [mun];
    S.barrios = [k];
    $("#dlgBarrio").close();
    if (document.body.dataset.vista === "analisis") cambiarVista(ancho() ? "split" : "lista");
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }
  if (t.dataset.abrir) {
    const it = buscarItem(t.dataset.abrir);
    if (mapa) mapa.closePopup();
    if (it) abrirVisor(it);
    return;
  }
  if (t.dataset.resumen) {
    LS.set("ocultarResumen", t.dataset.resumen === "ocultar");
    renderResumenDia(agrupar(ordenar(filtrarCache(null).slice())));
    return;
  }
  if (t.dataset.toggle) {
    const k = t.dataset.toggle;
    const v = k === "estratos" ? +t.dataset.v : t.dataset.v;
    S[k] = S[k].includes(v) ? S[k].filter((x) => x !== v) : [...S[k], v];
    if (k === "municipios") podarBarrios();
    render();
  } else if (t.dataset.set) {
    aplicarCambio(t.dataset.set, t.dataset.v);
  } else if (t.dataset.quitar) {
    if (t.dataset.quitar === "todo") { limpiarTodo(); return; }
    else ACTIVOS[+t.dataset.quitar][1]();
    render();
  } else if (t.dataset.atajo) {
    const a = t.dataset.atajo;
    if (a === "nuevos") S.novedad = S.novedad === 1 ? 0 : 1;
    else if (a === "favs") { S.favs = !S.favs; window.scrollTo({ top: 0 }); }
    else if (a === "descartados") S.descartados = !S.descartados;
    else if (a === "bajaron") S.bajaron = !S.bajaron;
    render();
  } else if (t.dataset.vista && t.closest("#vistas")) {
    cambiarVista(t.dataset.vista);
  } else if (t.dataset.acc && t.closest(".detalle-ficha")) {
    const id = t.closest(".detalle-ficha").dataset.id;
    const it = buscarItem(id);
    if (t.dataset.acc === "fav") {
      favs.has(id) ? favs.delete(id) : favs.add(id);
      LS.set("favs", [...favs]);
      actualizarRecomendaciones();
      render();
      pintarDetalle(it);
      toast(favs.has(id) ? "★ Guardado en favoritos" : "Quitado de favoritos");
    } else if (t.dataset.acc === "desc") {
      descartados.has(id) ? descartados.delete(id) : descartados.add(id);
      LS.set("ocultos", [...descartados]);
      actualizarRecomendaciones();
      const ahora = descartados.has(id);
      if (ahora) cerrarVisor();
      render();
      if (!ahora) pintarDetalle(it);
      if (ahora) toast("Descartado: ya no aparecerá", () => {
        descartados.delete(id); LS.set("ocultos", [...descartados]); actualizarRecomendaciones(); render();
      });
    } else if (t.dataset.acc === "mapa") {
      cerrarVisor();
      setTimeout(() => irAlMapa(id), 60);    } else if (t.dataset.acc === "compartir") {
      compartirAnuncio(it);
    }
  } else if (t.dataset.acc) {
    const card = t.closest(".tarjeta");
    if (!card) return;
    const id = card.dataset.id;
    if (t.dataset.acc === "compartir") {
      e.stopPropagation();
      compartirAnuncio(buscarItem(id));
    } else if (t.dataset.acc === "g-ant" || t.dataset.acc === "g-sig") {
      e.stopPropagation();
      moverFotoTarjeta(card, t.dataset.acc === "g-sig" ? 1 : -1);
    } else if (t.dataset.acc === "visor") {
      const it = buscarItem(id);
      if (it) abrirVisor(it, +card.dataset.i || 0);
    } else if (t.dataset.acc === "fav") {
      favs.has(id) ? favs.delete(id) : favs.add(id);
      LS.set("favs", [...favs]);
      const on = favs.has(id);
      t.classList.toggle("fav-on", on); t.textContent = on ? "★" : "☆";
      t.title = on ? "Quitar de favoritos" : "Guardar";
      t.setAttribute("aria-label", on ? "Quitar de favoritos" : "Guardar en favoritos");
      if (S.favs && !on) {
        const idx = RES.findIndex((x) => x.id === id);
        card.remove();
        RES = RES.filter((x) => x.id !== id);
        if (idx > -1 && idx < pintados) pintados--;
      }
      actualizarRecomendaciones();
      renderResumen();
      toast(on ? "★ Guardado en favoritos" : "Quitado de favoritos", () => {
        on ? favs.delete(id) : favs.add(id);
        LS.set("favs", [...favs]); actualizarRecomendaciones(); render({ soloLista: false });
      }, on ? ["Ver favoritos", () => { S.favs = true; render(); window.scrollTo({ top: 0 }); }] : null);
    } else if (t.dataset.acc === "desc") {
      descartados.has(id) ? descartados.delete(id) : descartados.add(id);
      LS.set("ocultos", [...descartados]);
      actualizarRecomendaciones();
      const ahora = descartados.has(id);
      if (!S.descartados && ahora) {
        const sig = card.nextElementSibling;
        card.remove();
        sig?.querySelector("button, a")?.focus({ preventScroll: true });
      } else if (S.descartados) {
        // En la vista de descartados: al restaurar, el anuncio sale de esta vista
        if (!descartados.size) S.descartados = false;
        render();
        return;
      } else card.outerHTML = tarjeta(RES.find((x) => x.id === id));
      const idx = RES.findIndex((x) => x.id === id);
      const antes = RES.length;
      RES = RES.filter((x) => S.descartados || !descartados.has(x.id));
      if (RES.length < antes && idx > -1 && idx < pintados) pintados--;
      renderResumen();
      if (ahora) toast("Descartado: ya no aparecerá", () => {
        descartados.delete(id); LS.set("ocultos", [...descartados]); actualizarRecomendaciones(); render();
      });
    } else if (t.dataset.acc === "mapa") {
      irAlMapa(id);
    }
  }
});

document.addEventListener("change", (e) => {
  const t = e.target;
  if (!t.dataset || !t.dataset.campo) return;
  aplicarCambio(t.dataset.campo, t.type === "checkbox" ? t.checked : t.value);
});

let tq;
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.classList && e.target.classList.contains("in-precio")) e.target.blur();
});
let tPrecio = null;
document.addEventListener("input", (e) => {
  const t = e.target;
  if (!t.classList || !t.classList.contains("in-precio")) return;
  clearTimeout(tPrecio);
  tPrecio = setTimeout(() => { if (leerPrecio(t.value) !== S[t.dataset.campo]) aplicarCambio(t.dataset.campo, t.value); }, 600);
});
$("#q").addEventListener("input", (e) => { clearTimeout(tq); tq = setTimeout(() => { S.q = e.target.value; render(); }, 220); });
$("#orden").addEventListener("change", (e) => { S.orden = e.target.value; render({ soloLista: true }); LS.set("filtros2", S); });
$("#zona").addEventListener("change", (e) => { S.zona = e.target.checked; render({ soloLista: true }); });
$("#quitarZona").addEventListener("click", () => { S.zona = false; $("#zona").checked = false; render({ soloLista: true }); });
function limpiarTodo() {
  S = { ...DEF(), orden: S.orden, dup: S.dup, conAdmin: S.conAdmin };
  $("#zona").checked = false;
  textoBuscaBarrio = "";
  $("#q").value = "";
  render();
}
$("#limpiarVacio").addEventListener("click", limpiarTodo);
$("#abrirPanel").addEventListener("click", abrirPanel);
$("#rapBarrios").addEventListener("click", () => {
  PANEL_ABIERTO.add("ubic"); PANEL_CERRADO.delete("ubic");
  abrirPanel();
  const sec = $("#secBarrios");
  if (sec) { sec.scrollIntoView({ block: "start" }); setTimeout(() => $("#buscaBarrio")?.focus(), 250); }
});
document.addEventListener("input", (e) => {
  if (e.target.id === "buscaBarrio") { textoBuscaBarrio = e.target.value; filtrarListaBarrios(); }
});
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-limpiar-barrios]")) { S.barrios = []; render(); }
  if (e.target.closest("[data-zoom-grupo]") && grupoActivo) {
    mapa.closePopup();
    const b = grupoActivo.getBounds();
    if (b.getNorthEast().distanceTo(b.getSouthWest()) < 15 || mapa.getZoom() >= mapa.getMaxZoom()) grupoActivo.spiderfy();
    else grupoActivo.zoomToBounds({ padding: [40, 40] });
    grupoActivo = null;
  }
});
$("#cerrarPanel").addEventListener("click", cerrarPanel);
$("#velo").addEventListener("click", cerrarPanel);
$("#verResultados").addEventListener("click", cerrarPanel);
$("#limpiar").addEventListener("click", limpiarTodo);
$("#flotante").addEventListener("click", () => {
  const ef = document.body.dataset.vista;
  cambiarVista(ef === "lista" || ef === "split" ? "mapa" : ancho() ? "split" : "lista");
});
$("#abrirAnalisis").addEventListener("click", () => cambiarVista(document.body.dataset.vista === "analisis" ? "lista" : "analisis"));
function listaSur() {
  const conAptos = new Map();
  for (const it of TODOS) if (esDelSur(it)) {
    const k = it.municipio === "Medellín" ? "Medellín" : it.municipio;
    conAptos.set(k, (conAptos.get(k) || 0) + 1);
  }
  const munis = [...MUNICIPIOS_SUR].map((m) => `<li><b>${esc(m)}</b> · todo el municipio <span class="tenue">(${NF.format(conAptos.get(m) || 0)} apartamentos)</span></li>`).join("");
  const med = SUR_MEDELLIN.map(([com, bs]) => `<div class="sur-com"><h4>${esc(com)}</h4><p>${bs.map(esc).join(" · ")}</p></div>`).join("");
  $("#listaSur").innerHTML = `<p class="tenue">Al activar <b>Solo el sur</b> se muestran:</p>
    <ul class="sur-munis">${munis}</ul>
    <h3>Y en Medellín, solo estas comunas <span class="tenue">(${NF.format(conAptos.get("Medellín") || 0)} apartamentos)</span></h3>${med}
    <p class="tenue sur-nota">Quedan fuera el resto de Medellín (Laureles, Robledo, Centro, Buenos Aires, La América…). Si un anuncio de Medellín no dice barrio, entra solo si su ubicación exacta queda en El Poblado, Guayabal o Belén.</p>`;
}
function setSoloSur(on) {
  S.soloSur = on;
  if (on) {
    // Se quitan municipios y barrios elegidos que no son del sur (si no, quedarían 0 resultados)
    S.municipios = S.municipios.filter((m) => MUNICIPIOS_SUR.has(m) || m === "Medellín");
    S.barrios = S.barrios.filter((k) => {
      const [mu, b] = k.split("|");
      return MUNICIPIOS_SUR.has(mu) || (mu === "Medellín" && RE_SUR_MED.test(b));
    });
  }
  render();
}
$("#swSur").addEventListener("change", (e) => setSoloSur(e.target.checked));
$("#rapMuni").addEventListener("change", (e) => {
  const v = e.target.value;
  S.municipios = v && v !== "__varios" ? [v] : [];
  podarBarrios();
  render();
});
$("#infoSur").addEventListener("click", () => { listaSur(); $("#dlgSur").showModal(); });
$("#cerrarSur").addEventListener("click", () => $("#dlgSur").close());
$("#abrirFuentes").addEventListener("click", () => { tablaFuentes(); $("#dlgFuentes").showModal(); });
$("#cerrarFuentes").addEventListener("click", () => $("#dlgFuentes").close());
$("#cerrarBarrio").addEventListener("click", () => $("#dlgBarrio").close());
document.addEventListener("keydown", (e) => {
  if (!$("#visor").hidden) {
    if (e.key === "Escape") modoFoto() ? salirModoFoto() : cerrarVisor();
    else if (e.key === "ArrowRight") moverVisor(1);
    else if (e.key === "ArrowLeft") moverVisor(-1);
    else if (e.key === "Tab") {
      // El foco no se escapa del visor
      const f = [...$("#visor").querySelectorAll("button:not([hidden]), a[href]")].filter((x) => x.offsetParent);
      if (f.length) {
        const a = f[0], z = f[f.length - 1];
        if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
        else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
      }
    }
    return;
  }
  if (e.key === "Escape") cerrarPanel();
  if ((e.key === "Enter" || e.key === " ") && e.target.matches(".foto[data-acc='visor']")) {
    e.preventDefault();
    e.target.click();
  }
});

// Visor: botones, miniaturas y deslizar con el dedo
$("#visorCerrar").addEventListener("click", () => (modoFoto() ? salirModoFoto() : cerrarVisor()));
$("#visorVolver").addEventListener("click", () => salirModoFoto());
$("#visorAnt").addEventListener("click", () => moverVisor(-1));
$("#visorSig").addEventListener("click", () => moverVisor(1));
$("#visorMinis").addEventListener("click", (e) => {
  const b = e.target.closest("[data-mini]");
  if (b) { V.i = +b.dataset.mini; pintarVisor(); }
});
// (tocar el fondo de la foto ya no cierra la ficha: se cierra con ✕, Escape o "atrás")
/* ---------- Zoom de la foto en la ficha ---------- */
const Z = { s: 1, x: 0, y: 0 };
const zoomImg = () => $("#visorMedio img");
function zoomAplicar() {
  const img = zoomImg();
  if (!img) return;
  if (Z.s <= 1.01) { Z.s = 1; Z.x = 0; Z.y = 0; }
  // no dejar que la foto se salga del todo de la pantalla
  const r = img.getBoundingClientRect(), base = { w: r.width / (img._s || 1), h: r.height / (img._s || 1) };
  const mx = (base.w * (Z.s - 1)) / 2, my = (base.h * (Z.s - 1)) / 2;
  Z.x = Math.max(-mx, Math.min(mx, Z.x)); Z.y = Math.max(-my, Math.min(my, Z.y));
  img._s = Z.s;
  img.style.transform = `translate(${Z.x}px, ${Z.y}px) scale(${Z.s})`;
  $("#visorEscena").classList.toggle("con-zoom", Z.s > 1);
}
function zoomReset() { Z.s = 1; Z.x = 0; Z.y = 0; const img = zoomImg(); if (img) { img._s = 1; img.style.transform = ""; } $("#visorEscena").classList.remove("con-zoom"); }
function zoomEn(escala, cx, cy) {
  const img = zoomImg();
  if (!img) return;
  const r = img.getBoundingClientRect();
  const ox = cx - (r.left + r.width / 2), oy = cy - (r.top + r.height / 2);
  const nueva = Math.max(1, Math.min(4, escala));
  const f = nueva / Z.s;
  Z.x = Z.x - ox * (f - 1); Z.y = Z.y - oy * (f - 1); Z.s = nueva;
  zoomAplicar();
}
const escena = $("#visorEscena");
// Computador: rueda del mouse y doble clic
escena.addEventListener("wheel", (e) => {
  if (!zoomImg()) return;
  e.preventDefault();
  zoomEn(Z.s * (e.deltaY < 0 ? 1.2 : 1 / 1.2), e.clientX, e.clientY);
}, { passive: false });
escena.addEventListener("dblclick", (e) => { if (e.target.closest("button")) return; if (zoomImg()) zoomEn(Z.s > 1 ? 1 : 2.5, e.clientX, e.clientY); });
let arrastre = null;
escena.addEventListener("mousedown", (e) => { if (Z.s > 1) { arrastre = { x: e.clientX - Z.x, y: e.clientY - Z.y }; e.preventDefault(); } });
window.addEventListener("mousemove", (e) => { if (arrastre) { Z.x = e.clientX - arrastre.x; Z.y = e.clientY - arrastre.y; zoomAplicar(); } });
window.addEventListener("mouseup", () => (arrastre = null));
// Celular: pellizcar, doble toque, arrastrar con zoom, deslizar sin zoom
let toque = null, ultimoToque = 0;
const dist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
escena.addEventListener("touchstart", (e) => {
  if (e.target.closest("button")) { toque = null; return; }   // las flechas no cuentan como gesto de zoom
  if (e.touches.length === 2) {
    toque = { pinza: dist(e.touches), s: Z.s, cx: (e.touches[0].clientX + e.touches[1].clientX) / 2, cy: (e.touches[0].clientY + e.touches[1].clientY) / 2 };
  } else if (e.touches.length === 1) {
    const t = e.touches[0];
    toque = { x0: t.clientX, y0: t.clientY, px: t.clientX - Z.x, py: t.clientY - Z.y, movio: false };
  }
}, { passive: true });
escena.addEventListener("touchmove", (e) => {
  if (!toque) return;
  if (e.touches.length === 2 && toque.pinza) {
    e.preventDefault();
    zoomEn(toque.s * (dist(e.touches) / toque.pinza), toque.cx, toque.cy);
  } else if (e.touches.length === 1 && Z.s > 1 && toque.px != null) {
    e.preventDefault();
    const t = e.touches[0];
    Z.x = t.clientX - toque.px; Z.y = t.clientY - toque.py; toque.movio = true;
    zoomAplicar();
  }
}, { passive: false });
escena.addEventListener("touchend", (e) => {
  if (!toque) return;
  if (toque.x0 != null && e.changedTouches.length) {
    const t = e.changedTouches[0];
    const dx = t.clientX - toque.x0, dy = t.clientY - toque.y0;
    const ahora = Date.now();
    if (Math.abs(dx) < 10 && Math.abs(dy) < 10) {
      if (ahora - ultimoToque < 300) { zoomEn(Z.s > 1 ? 1 : 2.5, t.clientX, t.clientY); ultimoToque = 0; }
      else ultimoToque = ahora;
    } else if (Z.s === 1 && Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy)) {
      moverVisor(dx < 0 ? 1 : -1);   // sin zoom: deslizar cambia de foto
    }
  }
  if (e.touches.length === 0) toque = null;
});

$("#tarjetas").addEventListener("mouseover", (e) => { const c = e.target.closest(".tarjeta"); if (c && !c.contains(e.relatedTarget)) resaltarPin(c.dataset.id, true); });
$("#tarjetas").addEventListener("mouseout", (e) => { const c = e.target.closest(".tarjeta"); if (c && !c.contains(e.relatedTarget)) resaltarPin(c.dataset.id, false); });

new IntersectionObserver((ent) => { if (ent[0].isIntersecting) pintarLote(); }, { rootMargin: "800px" }).observe($("#centinela"));

let anchoPrevio = ancho();
window.addEventListener("resize", () => {
  if (ancho() !== anchoPrevio) { anchoPrevio = ancho(); cambiarVista(vista); }
  medirArriba();
});

/* =========================================================
   Arranque
   ========================================================= */
function esqueletos() {
  $("#tarjetas").innerHTML = Array.from({ length: 6 }, () =>
    `<div class="esqueleto"><div class="e-foto"></div><div class="e-l" style="width:50%"></div><div class="e-l" style="width:80%"></div></div>`).join("");
}

async function iniciar() {
  esqueletos();
  if (!ancho() && vista === "split") vista = "split"; // se resuelve en cambiarVista
  const [d, m, mer] = await Promise.all([
    fetch("data/listings.json", { cache: "no-cache" }).then((r) => r.json()),
    fetch("data/meta.json", { cache: "no-cache" }).then((r) => r.json()).catch(() => ({})),
    fetch("data/mercado.json", { cache: "no-cache" }).then((r) => r.json()).catch(() => ({ historial: {} })),
    fetch("barrios_info.json", { cache: "no-cache" }).then((r) => r.json()).then((b) => { INFO_FECHA = b.fecha || ""; cargarInfoBarrios(b); }).catch(() => null),
    fetch("noticias_barrios.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).then(cargarNoticias).catch(() => null),
    fetch("data/ia.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).then((d) => { IA_SEL = d; }).catch(() => null),
  ]);
  TODOS = d.items || [];
  completarDuplicados();
  META = m;
  MERCADO = mer;
  COB = cobertura();
  calcularPerfil();
  BASE_NUEVO = (m.corridas && m.corridas[0] && m.corridas[0].ts) || "";
  PRESUPUESTO = m.presupuesto_defecto || PRESUPUESTO;
  PRECIO_TOPE = m.precio_max || PRECIO_TOPE;
  S = { ...DEF(), ...LS.get("filtros2", {}) };
  if (S.orden === "m2") S.orden = "nuevo"; // orden por precio/m² eliminado
  // El interés es el sur: se enciende «Solo el sur» una vez también para quien ya tenía filtros guardados
  if (!LS.get("surPorDefecto", false)) { S.soloSur = true; LS.set("surPorDefecto", true); }
  // Si el tope de precio cambió, un filtro guardado por encima se ajusta al nuevo
  if (!S.pmax || S.pmax > PRECIO_TOPE) S.pmax = Math.min(PRESUPUESTO, PRECIO_TOPE);
  if (S.pmin >= PRECIO_TOPE) S.pmin = 0;
  if (S.pmax && S.pmin > S.pmax) S.pmin = 0;
  for (const k of ["municipios", "barrios", "estratos", "fuentes"]) if (!Array.isArray(S[k])) S[k] = [];
  if (S.pmin >= PRECIO_TOPE) S.pmin = 0;
  prepararBarrios();
  $("#zona").checked = !!S.zona;
  const sinActualizar = (Date.now() - new Date(d.generado)) / 36e5 > 13;
  $("#actualizado").textContent = `${NF.format(TODOS.length)} disponibles · actualizado ${hace(d.generado)} · cada 6 h${sinActualizar ? " · ⚠ lleva más de 12 h sin actualizarse" : ""}`;
  cambiarVista(vista);
  render();
  abrirDesdeEnlace();
}

iniciar().catch((e) => {
  console.error(e);
  $("#tarjetas").innerHTML = "";
  $("#actualizado").textContent = "No se pudieron cargar los datos. Revisa tu conexión y recarga la página.";
});
