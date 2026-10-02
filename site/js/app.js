// Same-scale comparison map: two MapLibre panels kept at identical metres per pixel.
import * as maplibregl from '../vendor/maplibre/maplibre-gl.mjs';
import { buildStyle, palette, CYCLE_LABELS } from './style.js';
import { metersPerPixel, zoomForMpp, fitMpp, offsetLngLat, EARTH_RADIUS_M } from './scale.js';

const url = (p) => new URL(p, document.baseURI).href;
const CITIES = ['ka', 'ulm'];
const OTHER = { ka: 'ulm', ulm: 'ka' };
const GROUPS = [
  { id: 'buildings', label: 'Gebäude', on: true },
  { id: 'green', label: 'Grün/Flächen', on: true },
  { id: 'relief', label: 'Relief/Höhenlinien', on: true },
  { id: 'roads', label: 'Straßen', on: true },
  { id: 'cycle', label: 'Radinfrastruktur', on: false },
  { id: 'tram', label: 'Tram/Bahn', on: true },
  { id: 'bus', label: 'Bus', on: false },
  { id: 'rings', label: 'Radien', on: true },
  { id: 'kitas', label: 'Kitas Ü3', on: true },
  { id: 'districts', label: 'Stadtteilgrenzen', on: false },
  { id: 'names', label: 'Namen', on: true },
];
// Heavy sources are switched on only after the first render, keeping first paint fast.
const LAZY = ['buildings', 'relief'];
const STORE_KEY = 'k-u-map:v1';

const fmt = (v, digits = 1) => new Intl.NumberFormat('de-DE', { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(v);

function loadPrefs() {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) ?? '{}');
  } catch {
    return {};
  }
}
function savePrefs(prefs) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(prefs));
  } catch {
    // Storage can be unavailable (private mode); preferences are a convenience only.
  }
}

const darkQuery = matchMedia('(prefers-color-scheme: dark)');
const prefs = loadPrefs();
const state = {
  theme: darkQuery.matches ? 'dark' : 'light',
  visible: Object.fromEntries(GROUPS.map((g) => [g.id, prefs.visible?.[g.id] ?? g.on])),
  lazyReady: false,
  couple: false,
  network: '2026',
  radius: prefs.radius ?? '2',
  mppDefault: 1,
  mppMax: 1,
  syncing: false,
};
const maps = {};
const lastCenter = {};
let meta;
let metrics;

function effectiveVisible() {
  // Keep lazy groups hidden (and thus unrequested) until the base map has rendered.
  if (state.lazyReady) return state.visible;
  return { ...state.visible, ...Object.fromEntries(LAZY.map((g) => [g, false])) };
}

function styleFor(city) {
  return buildStyle({ theme: state.theme, city, visible: effectiveVisible(), network: state.network, url });
}

// ------------------------------------------------------------------ scale sync

function mppOf(map) {
  return metersPerPixel(map.getZoom(), map.getCenter().lat);
}

function panelSize() {
  const el = document.getElementById('map-ka');
  return { w: el.clientWidth, h: el.clientHeight };
}

function updateLimits() {
  const { w, h } = panelSize();
  state.mppDefault = fitMpp(meta.frame.width_m, meta.frame.height_m, w, h);
  // Zoom out at most until the whole data extent is in view.
  state.mppMax = fitMpp(meta.frame.width_m + 2 * meta.frame.margin_m, meta.frame.height_m + 2 * meta.frame.margin_m, w, h);
}

function constrainFor(city) {
  return (lngLat, zoom) => {
    const [w, s, e, n] = meta.cities[city].data_bbox;
    const lng = Math.min(e, Math.max(w, lngLat.lng));
    const lat = Math.min(n, Math.max(s, lngLat.lat));
    // The limit is in metres per pixel, not zoom levels, so both panels stop at
    // the same ground scale even though their latitudes differ.
    const minZoom = zoomForMpp(state.mppMax, lat);
    return { center: new maplibregl.LngLat(lng, lat), zoom: Math.min(19, Math.max(minZoom, zoom)) };
  };
}

function syncFrom(src) {
  if (state.syncing) return;
  const a = maps[src];
  const b = maps[OTHER[src]];
  if (!a || !b) return;
  state.syncing = true;
  try {
    const mpp = mppOf(a);
    let center = b.getCenter();
    const prev = lastCenter[src];
    if (state.couple && prev) {
      const c = a.getCenter();
      const east = ((c.lng - prev.lng) * Math.PI / 180) * EARTH_RADIUS_M * Math.cos((prev.lat * Math.PI) / 180);
      const north = ((c.lat - prev.lat) * Math.PI / 180) * EARTH_RADIUS_M;
      const [lng, lat] = offsetLngLat(center.lng, center.lat, east, north);
      center = new maplibregl.LngLat(lng, lat);
    }
    b.jumpTo({ center, zoom: zoomForMpp(mpp, center.lat) });
  } finally {
    state.syncing = false;
  }
  lastCenter[src] = a.getCenter();
  lastCenter[OTHER[src]] = b.getCenter();
  scheduleReadout();
}

let readoutPending = false;
function scheduleReadout() {
  if (readoutPending) return;
  readoutPending = true;
  requestAnimationFrame(() => {
    readoutPending = false;
    const ka = maps.ka;
    const ulm = maps.ulm;
    if (!ka || !ulm) return;
    const mpp = mppOf(ka);
    const factor = state.mppDefault / mpp;
    const { w } = panelSize();
    document.getElementById('readout').textContent =
      `1 px = ${fmt(mpp, mpp < 10 ? 2 : 1)} m · Zoomfaktor ×${fmt(factor, 2)} · Bildbreite ${fmt((mpp * w) / 1000, 1)} km`;
    const el = document.getElementById('readout');
    el.title = `MapLibre-Zoom Karlsruhe ${fmt(ka.getZoom(), 3)}, Ulm ${fmt(ulm.getZoom(), 3)} `
      + `(Δ ${fmt(ulm.getZoom() - ka.getZoom(), 3)}: Breitengrad-Korrektur)`;
    // Exposed for the smoke test: both panels' ground resolution.
    document.body.dataset.mppKa = mppOf(ka).toFixed(6);
    document.body.dataset.mppUlm = mppOf(ulm).toFixed(6);
  });
}

function resetView() {
  updateLimits();
  for (const city of CITIES) {
    const [lng, lat] = meta.cities[city].center;
    maps[city].jumpTo({ center: [lng, lat], zoom: zoomForMpp(state.mppDefault, lat) });
    lastCenter[city] = maps[city].getCenter();
  }
  scheduleReadout();
}

// ------------------------------------------------------------------ maps

function createMap(city) {
  const c = meta.cities[city];
  const { w, h } = panelSize();
  const mpp = fitMpp(meta.frame.width_m, meta.frame.height_m, w, h);
  const map = new maplibregl.Map({
    container: `map-${city}`,
    style: styleFor(city),
    center: c.center,
    zoom: zoomForMpp(mpp, c.center[1]),
    attributionControl: { compact: true },
    dragRotate: false,
    pitchWithRotate: false,
    touchPitch: false,
    maxPitch: 0,
    renderWorldCopies: false,
    transformConstrain: constrainFor(city),
    fadeDuration: 150,
  });
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-left');
  map.on('move', () => syncFrom(city));
  map.on('click', (e) => showPopup(map, e));
  for (const layer of ['tram-lines', 'bus-lines', 'tram-stops', 'bus-stops', 'refpoints', 'kita-dots']) {
    map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
  }
  maps[city] = map;
  lastCenter[city] = map.getCenter();
  return map;
}

function applyStyles(cities = CITIES) {
  for (const city of cities) maps[city]?.setStyle(styleFor(city));
}

function setGroupVisibility(group, on) {
  state.visible[group] = on;
  savePrefs({ ...loadPrefs(), visible: state.visible });
  if (LAZY.includes(group) && !state.lazyReady) return;
  for (const map of Object.values(maps)) {
    for (const layer of map.getStyle().layers) {
      if (layer.metadata?.group === group) map.setLayoutProperty(layer.id, 'visibility', on ? 'visible' : 'none');
    }
  }
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);

function showPopup(map, e) {
  const r = 6;
  const box = [[e.point.x - r, e.point.y - r], [e.point.x + r, e.point.y + r]];
  const layers = ['refpoints', 'kita-dots', 'tram-stops', 'bus-stops', 'tram-lines', 'bus-lines',
    'cycle-track', 'cycle-path', 'cycle-lane', 'cycle-street', 'cycle-busway']
    .filter((id) => map.getLayer(id) && map.getLayoutProperty(id, 'visibility') !== 'none');
  const feats = map.queryRenderedFeatures(box, { layers });
  if (!feats.length) return;
  const f = feats[0];
  const p = f.properties;
  let html;
  if (f.layer.id === 'refpoints') {
    html = `<strong>${esc(p.name)}</strong>`;
  } else if (f.layer.id === 'kita-dots') {
    const link = p.osm ? ` · <a href="${esc(p.osm)}" target="_blank" rel="noopener">in OSM ansehen</a>` : '';
    html = `<strong>${esc(p.name || 'Kita ohne Namen')}</strong><br>`
      + `${p.status === 'confirmed' ? 'Ü3 bestätigt' : 'Ü3 nicht bestätigt'}: ${esc(p.reason)}`
      + `<br><small>Quelle: ${esc(p.source)}${link}</small>`;
  } else if (f.layer.id.endsWith('-stops')) {
    html = `<strong>${esc(p.name)}</strong><br>Linien: ${esc(p.routes)}`;
  } else if (f.layer.id.endsWith('-lines')) {
    const seen = new Set();
    const lines = feats.filter((x) => x.layer.id.endsWith('-lines') && !seen.has(x.properties.ref) && seen.add(x.properties.ref));
    html = lines.slice(0, 6).map((x) => `<span class="popup-line" style="background:${esc(x.properties.color)};color:${esc(x.properties.text_color)}">${esc(x.properties.ref)}</span>${esc(x.properties.name)}`).join('<br>');
  } else {
    html = `${esc(CYCLE_LABELS[p.kind] ?? p.kind)}${p.sides > 1 ? ' (beidseitig)' : ''}`;
  }
  new maplibregl.Popup({ closeButton: true, maxWidth: '260px' }).setLngLat(e.lngLat).setHTML(html).addTo(map);
}

// ------------------------------------------------------------------ UI

function buildChips() {
  const box = document.getElementById('chips');
  const p = palette(state.theme);
  const swatch = {
    buildings: p.building, green: p.landuse.park, relief: p.contour, roads: p.road.primary,
    cycle: p.cycle.track, tram: '#ed1b24', bus: '#1aa8be', rings: p.rings, districts: p.district, names: p.tier2,
    kitas: p.kita,
  };
  box.replaceChildren(...GROUPS.map((g) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'chip';
    b.dataset.group = g.id;
    b.setAttribute('aria-pressed', String(state.visible[g.id]));
    b.innerHTML = `<span class="sw" style="background:${swatch[g.id]}"></span>${g.label}`;
    b.addEventListener('click', () => {
      const on = b.getAttribute('aria-pressed') !== 'true';
      b.setAttribute('aria-pressed', String(on));
      setGroupVisibility(g.id, on);
    });
    return b;
  }));
}

function buildNetworkSwitch() {
  const nets = meta.networks.ulm;
  const el = document.getElementById('network-switch');
  const note = document.getElementById('network-note');
  const swu = meta.gtfs.swu ?? {};
  const validTo = swu.end ? `${swu.end.slice(6, 8)}.${swu.end.slice(4, 6)}.${swu.end.slice(0, 4)}` : '?';
  if (nets.includes('2027')) {
    el.hidden = false;
    el.replaceChildren(...[['2026', 'Netz 2026'], ['2027', 'Netz ab 2027']].map(([id, label]) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.setAttribute('aria-pressed', String(state.network === id));
      b.textContent = label;
      b.addEventListener('click', () => {
        state.network = id;
        for (const x of el.children) x.setAttribute('aria-pressed', String(x === b));
        applyStyles(['ulm']);
        renderCompare();
      });
      return b;
    }));
    note.textContent = 'Ulm/Neu-Ulm: Der SWU-Fahrplan enthält bereits das gemeinsame Stadtnetz ab 1.1.2027 – umschaltbar oben.';
  } else {
    el.hidden = false;
    el.innerHTML = '<span class="chip" aria-disabled="true" title="Das gemeinsame Ulm/Neu-Ulmer Stadtnetz ab 1.1.2027 ist noch nicht als GTFS veröffentlicht.">Ulm: Netz 2026</span>';
    note.textContent = `Ulm/Neu-Ulm zeigt das aktuelle SWU-Netz (GTFS-Stand ${swu.version ?? '?'}, gültig bis ${validTo}). `
      + 'Das neue gemeinsame Stadtnetz ab 1.1.2027 ist noch nicht als GTFS veröffentlicht; sobald SWU es veröffentlicht, '
      + 'erkennt der monatliche Neubau es automatisch und bietet „Netz 2026 / Netz ab 2027“ an.';
  }
}

function svgLine(color, { width = 3, dash = null, opacity = 1 } = {}) {
  const d = dash ? `stroke-dasharray="${dash}"` : '';
  return `<svg width="34" height="12" aria-hidden="true"><line x1="2" y1="6" x2="32" y2="6" stroke="${color}" stroke-width="${width}" stroke-opacity="${opacity}" ${d} stroke-linecap="butt"/></svg>`;
}
function svgDot(fill, stroke) {
  return `<svg width="34" height="12" aria-hidden="true"><circle cx="17" cy="6" r="4" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/></svg>`;
}

function buildLegend() {
  const p = palette(state.theme);
  const items = [
    [svgLine(p.cycle.track, { width: 3 }), CYCLE_LABELS.track],
    [svgLine(p.cycle.path, { width: 2.4, dash: '4 2' }), CYCLE_LABELS.path],
    [svgLine(p.cycle.lane, { width: 2.6, dash: '6 3' }), CYCLE_LABELS.lane],
    [svgLine(p.cycle.street, { width: 8, opacity: 0.45 }), CYCLE_LABELS.street],
    [svgLine(p.cycle.busway, { width: 3, dash: '1.5 3' }), CYCLE_LABELS.busway],
    [svgLine('#ed1b24', { width: 3.5 }), 'Tram/Stadtbahn-Linie (Linienfarbe)'],
    [svgLine('#1aa8be', { width: 2 }), 'Buslinie (Linienfarbe)'],
    [svgDot(p.stop, p.stopStroke), 'Haltestelle'],
    [svgLine(p.rail, { width: 2.5 }), 'Eisenbahn'],
    [svgLine(p.border, { width: 2, dash: '6 3 2 3' }), 'Landesgrenze (BW/BY, BW/RP)'],
    [svgLine(p.district, { width: 1.5, dash: '4 3' }), 'Stadtteilgrenze'],
    [svgLine(p.rings, { width: 1.5, dash: '6 4' }), 'Radius um Marktplatz/Münster (Luftlinie, Rad 15 km/h)'],
    [svgDot(p.kita, p.kita), `Kita/Kindergarten mit Ü3-Gruppen, bestätigt (nur im ${meta.kitas.radius_km}-km-Kreis)`],
    [svgDot(p.halo, p.kita), 'Kita, Ü3 nicht bestätigt (keine Altersangabe)'],
    [svgLine(p.contour, { width: 1.2 }), 'Höhenlinie 10 m (fett: 50 m)'],
    [`<svg width="34" height="12" aria-hidden="true"><text x="2" y="10" font-size="10" font-weight="700" fill="${p.tier2}">ABC</text></svg>`, 'Bevorzugte Stadtteile'],
    [`<svg width="34" height="12" aria-hidden="true"><text x="2" y="10" font-size="10" font-weight="600" fill="${p.tier1}">ABC</text></svg>`, 'Weiteres Interesse'],
  ];
  const k = meta.kitas;
  document.getElementById('kita-note').textContent =
    `Kitas Ü3: Einrichtungen, die Kinder von 3–6 Jahren aufnehmen, im ${k.radius_km}-km-Kreis um Marktplatz bzw. Münster `
    + '(OpenStreetMap; Krippen, Tagespflege und Horte ausgeblendet). „Bestätigt“ heißt: Altersangabe, „Kindergarten“ im Namen'
    + (k.official_list_ka ? ' oder Kategorie „Kindergärten“ der Stadt Karlsruhe, deren Liste eingearbeitet ist.'
      : '; die Liste der Stadt Karlsruhe war beim Erstellen nicht erreichbar.')
    + ' Ohne Gewähr: Altersgruppen und freie Plätze beim Träger erfragen.';
  document.getElementById('legend').innerHTML = items
    .map(([svg, label]) => `<div class="legend-item">${svg}<span>${label}</span></div>`)
    .join('');
}

const KIND_LABELS = { track: CYCLE_LABELS.track, path: CYCLE_LABELS.path, lane: CYCLE_LABELS.lane, street: CYCLE_LABELS.street, busway: CYCLE_LABELS.busway };

function renderCompare() {
  if (!metrics) return;
  const r = state.radius;
  const ka = metrics.cities.ka.radii[r];
  const ulm = metrics.cities.ulm.radii[r];
  const ulmNet = ulm.stops[state.network] ? state.network : Object.keys(ulm.stops)[0];
  const kaNet = Object.keys(ka.stops)[0];
  const rows = [
    ['group', 'Bebauung'],
    ['row', 'Gebäudegrundfläche', 'Anteil der Kreisfläche', ka.building_share_pct, ulm.building_share_pct, '%'],
    ['row', 'Gebäude', 'Anzahl (OSM)', ka.building_count, ulm.building_count, '', 0],
    ['group', 'Öffentlicher Verkehr'],
    ['row', 'Straßenbahn-Strecke', 'Gleisachsen, Doppelgleis einfach', ka.tram_km, ulm.tram_km, 'km'],
    ['row', 'Haltestellen', 'Tram + Bus (GTFS)', ka.stops[kaNet]?.total ?? 0, ulm.stops[ulmNet]?.total ?? 0, '', 0],
    ['row', 'davon mit Tram/Stadtbahn', '', ka.stops[kaNet]?.tram ?? 0, ulm.stops[ulmNet]?.tram ?? 0, '', 0],
    ['group', 'Radinfrastruktur (km, je Straßenseite)'],
    ['row', 'Gesamt', '', ka.cycle_km_total, ulm.cycle_km_total, 'km'],
    ...Object.keys(KIND_LABELS).map((k) => ['row', KIND_LABELS[k], '', ka.cycle_km[k], ulm.cycle_km[k], 'km']),
    ...(ka.kitas && ulm.kitas ? [
      ['group', 'Kitas mit Ü3-Gruppen'],
      ['row', 'Ü3 bestätigt', 'Altersangabe, Name oder Stadt-Kategorie', ka.kitas.confirmed, ulm.kitas.confirmed, '', 0],
      ['row', 'Ü3 nicht bestätigt', 'Kita ohne Altersangabe', ka.kitas.probable, ulm.kitas.probable, '', 0],
    ] : []),
    ['group', 'Topografie'],
    ['row', 'Höhenspanne', '5.–95. Perzentil', ka.elevation_m.range_p5_p95, ulm.elevation_m.range_p5_p95, 'm', 0],
    ['row', 'Fläche > 30 m über Zentrum', 'Anstieg vom Zentrum aus', ka.elevation_m.share_30m_above_center_pct, ulm.elevation_m.share_30m_above_center_pct, '%'],
    ['text', 'Tiefster / höchster Punkt', '', `${fmt(ka.elevation_m.min, 0)} / ${fmt(ka.elevation_m.max, 0)} m`, `${fmt(ulm.elevation_m.min, 0)} / ${fmt(ulm.elevation_m.max, 0)} m`],
  ];
  const html = [`<div class="row head" role="row"><div role="columnheader">Kreis ${r} km</div><div class="ka" role="columnheader">Karlsruhe</div><div class="ulm" role="columnheader">Ulm / Neu-Ulm</div></div>`];
  for (const row of rows) {
    if (row[0] === 'group') {
      html.push(`<div class="row group" role="row"><div role="rowheader">${row[1]}</div></div>`);
      continue;
    }
    const [, label, sub, a, b, unit, digits = 1] = row;
    const lab = `<div class="label" role="rowheader">${label}${sub ? `<small>${sub}</small>` : ''}</div>`;
    if (row[0] === 'text') {
      html.push(`<div class="row" role="row">${lab}<div class="cell ka" role="cell">${a}</div><div class="cell ulm" role="cell">${b}</div></div>`);
      continue;
    }
    const max = Math.max(a, b) || 1;
    const cell = (v, cls) => `<div class="cell ${cls}" role="cell"><span>${fmt(v, digits)}${unit ? ` ${unit}` : ''}</span><div class="bar"><i style="width:${(100 * v) / max}%"></i></div></div>`;
    html.push(`<div class="row" role="row">${lab}${cell(a, 'ka')}${cell(b, 'ulm')}</div>`);
  }
  document.getElementById('compare-table').innerHTML = html.join('');
  const ck = metrics.cities.ka.center;
  const cu = metrics.cities.ulm.center;
  document.getElementById('compare-center').textContent =
    `Kreise um ${ck.name} (${fmt(ck.elevation_m, 0)} m ü. NHN) und ${cu.name} (${fmt(cu.elevation_m, 0)} m ü. NHN), `
    + `Fläche je ${fmt(ka.area_km2, 1)} km². Ulm: ${ulmNet === '2027' ? 'Netz ab 2027' : 'Netz 2026'}.`;
}

function buildCompare() {
  const tabs = document.getElementById('radius-tabs');
  const radii = Object.keys(metrics.cities.ka.radii);
  if (!radii.includes(state.radius)) state.radius = radii[0];
  tabs.replaceChildren(...radii.map((r) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'chip';
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', String(r === state.radius));
    b.setAttribute('aria-pressed', String(r === state.radius));
    b.textContent = `${r} km`;
    b.addEventListener('click', () => {
      state.radius = r;
      savePrefs({ ...loadPrefs(), radius: r });
      for (const x of tabs.children) {
        x.setAttribute('aria-pressed', String(x === b));
        x.setAttribute('aria-selected', String(x === b));
      }
      renderCompare();
    });
    return b;
  }));
  const labels = {
    crs: 'Koordinatensystem', circles: 'Kreise', building_share: 'Gebäudegrundfläche', tram_km: 'Straßenbahn',
    cycle_km: 'Radinfrastruktur', stops: 'Haltestellen', kitas: 'Kitas', elevation: 'Höhe',
  };
  document.getElementById('method-list').innerHTML = Object.entries(metrics.method)
    .map(([k, v]) => `<li><strong>${labels[k] ?? k}:</strong> ${esc(v)}</li>`).join('');
  renderCompare();
}

function buildSources() {
  const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString('de-DE') : '–');
  document.getElementById('sources').innerHTML = meta.sources.map((s) => {
    const parts = [`<a href="${esc(s.homepage)}" rel="noopener">${esc(s.name)}</a>`,
      `Lizenz: <a href="${esc(s.license_url)}" rel="noopener">${esc(s.license)}</a>`];
    if (s.attribution) parts.push(esc(s.attribution));
    if (s.data_date) parts.push(`Datenstand ${esc(s.data_date)}`);
    if (s.downloaded) parts.push(`abgerufen ${fmtDate(s.downloaded)}`);
    return `<li>${parts.join(' · ')}</li>`;
  }).join('');
  document.getElementById('build-info').textContent =
    `Karte erzeugt am ${fmtDate(meta.generated)}. Maßstab exakt gleich in der Kartenmitte; innerhalb eines `
    + '10-km-Ausschnitts weicht die Mercator-Verzerrung um höchstens ±0,1 % ab.';
}

// ------------------------------------------------------------------ start

async function main() {
  // pmtiles.js is a classic deferred script; wait for it if needed.
  if (!window.pmtiles) await new Promise((res) => window.addEventListener('load', res, { once: true }));
  const protocol = new window.pmtiles.Protocol();
  maplibregl.addProtocol('pmtiles', protocol.tile);

  meta = await (await fetch(url('data/meta.json'))).json();
  state.network = meta.networks.ulm.includes(prefs.network) ? prefs.network : meta.networks.ulm[0];
  updateLimits();
  buildChips();
  buildNetworkSwitch();
  buildLegend();
  buildSources();

  for (const city of CITIES) createMap(city);
  resetView();

  let idle = 0;
  for (const city of CITIES) {
    maps[city].once('idle', () => {
      idle += 1;
      if (idle === CITIES.length && !state.lazyReady) {
        state.lazyReady = true;
        for (const g of LAZY) setGroupVisibility(g, state.visible[g]);
        document.body.dataset.ready = 'true';
      }
    });
  }

  document.getElementById('reset').addEventListener('click', resetView);
  const couple = document.getElementById('couple');
  couple.addEventListener('click', () => {
    state.couple = couple.getAttribute('aria-pressed') !== 'true';
    couple.setAttribute('aria-pressed', String(state.couple));
    for (const city of CITIES) lastCenter[city] = maps[city].getCenter();
  });
  darkQuery.addEventListener('change', (e) => {
    state.theme = e.matches ? 'dark' : 'light';
    applyStyles();
    buildChips();
    buildLegend();
  });
  new ResizeObserver(() => {
    const before = state.mppDefault;
    updateLimits();
    if (before !== state.mppDefault) scheduleReadout();
  }).observe(document.getElementById('map-ka'));

  metrics = await (await fetch(url('data/metrics.json'))).json();
  buildCompare();
}

main().catch((err) => {
  console.error(err);
  document.getElementById('readout').textContent = 'Die Karte konnte nicht geladen werden.';
});
