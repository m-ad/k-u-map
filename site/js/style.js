// MapLibre style for one panel. Every layer carries metadata.group so the layer
// chips can toggle a group in both panels; groups absent from `visible` are shown.

const PALETTES = {
  light: {
    bg: '#f3f0e8',
    landuse: {
      forest: '#cddbbd', park: '#d4e6c1', grass: '#dfe9cd', allotments: '#dde5c6', farmland: '#ebead7',
      cemetery: '#d2dcca', residential: '#eae5dc', commercial: '#eedfda', industrial: '#e3dfe7',
      sport: '#d6e7cb', wetland: '#d5e2de',
    },
    water: '#a6cae3', waterway: '#7fb0d9', waterLabel: '#2b6593',
    building: '#c6bcaf', buildingLine: '#b5aa9c',
    casing: '#cbc4b8', tunnelOpacity: 0.45,
    road: {
      motorway: '#f1bd82', trunk: '#f5cf9b', primary: '#fae0ae', secondary: '#fdefc9', tertiary: '#ffffff',
      minor: '#ffffff', service: '#ffffff', pedestrian: '#f6f3ee', path: '#9c958c', track: '#b4a387',
    },
    rail: '#77726c', railDash: '#f3f0e8', tramInfra: '#8d6c6c',
    border: '#7b2f8a', district: '#8a6db3', municipality: '#4c3d70',
    tier2Fill: '#f59e0b', tier1Fill: '#14b8a6',
    text: '#2b2824', halo: '#f8f6f1', tier2: '#a14a00', tier1: '#0b6b6b', quarter: '#5d5751', town: '#38342f',
    rings: '#34312d', ringLabel: '#34312d',
    ref: { landmark: '#c2410c', station: '#1d4ed8', zoo: '#15803d' },
    cycle: { track: '#13803b', path: '#5fa312', lane: '#0a87b0', street: '#7c3aed', busway: '#d0267a' },
    contour: '#9b7650', contourOpacity: 0.55, contourLabel: '#76573a',
    hillshade: { shadow: '#3d3220', highlight: '#ffffff', accent: '#4a3f2a', exaggeration: 0.45 },
    stop: '#ffffff', stopStroke: '#2b2824', lineCasing: '#ffffff',
    kita: '#a21caf',
  },
  dark: {
    bg: '#15171a',
    landuse: {
      forest: '#1d291e', park: '#203020', grass: '#212a1e', allotments: '#232b1f', farmland: '#1b1d19',
      cemetery: '#1f2820', residential: '#1b1c1f', commercial: '#241d1f', industrial: '#1f1e25',
      sport: '#1e2e22', wetland: '#1b2625',
    },
    water: '#163049', waterway: '#2c5c86', waterLabel: '#8fbbe0',
    building: '#393b41', buildingLine: '#45484e',
    casing: '#0d0f12', tunnelOpacity: 0.4,
    road: {
      motorway: '#8b5d2e', trunk: '#7a5832', primary: '#6a5538', secondary: '#55504a', tertiary: '#47494e',
      minor: '#3c3e44', service: '#33353a', pedestrian: '#2c2e33', path: '#5b5955', track: '#4c4436',
    },
    rail: '#8d8882', railDash: '#15171a', tramInfra: '#a07f7f',
    border: '#d19bff', district: '#a78bfa', municipality: '#c9bcff',
    tier2Fill: '#fbbf24', tier1Fill: '#2dd4bf',
    text: '#e7e5e4', halo: '#101114', tier2: '#fbbf24', tier1: '#5eead4', quarter: '#b5afa8', town: '#d6d3d1',
    rings: '#d4d4d4', ringLabel: '#e5e5e5',
    ref: { landmark: '#fb923c', station: '#60a5fa', zoo: '#4ade80' },
    cycle: { track: '#4ade80', path: '#a3e635', lane: '#22d3ee', street: '#c4b5fd', busway: '#f472b6' },
    contour: '#c9a47c', contourOpacity: 0.4, contourLabel: '#d8b48c',
    hillshade: { shadow: '#000000', highlight: '#c8c8c8', accent: '#1a1a1a', exaggeration: 0.5 },
    stop: '#15171a', stopStroke: '#e7e5e4', lineCasing: '#15171a',
    kita: '#f0abfc',
  },
};

export const FONTS = {
  regular: ['Noto Sans Regular'],
  semibold: ['Noto Sans SemiBold'],
  bold: ['Noto Sans Bold'],
  italic: ['Noto Sans Italic'],
};

export const CYCLE_LABELS = {
  track: 'Radweg, baulich getrennt',
  path: 'Gemeinsamer Geh-/Radweg',
  lane: 'Radfahr-/Schutzstreifen',
  street: 'Fahrradstraße',
  busway: 'Busspur, Rad frei',
};

export function palette(theme) {
  return PALETTES[theme] ?? PALETTES.light;
}

const BASE = 1.5;
const z = (stops) => ['interpolate', ['exponential', BASE], ['zoom'], ...stops.flat()];

// Value of an exponential zoom ramp at zoom `zz` (clamped at the ends).
function ramp(stops, zz) {
  if (zz <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    const [z0, v0] = stops[i - 1];
    const [z1, v1] = stops[i];
    if (zz <= z1) {
      const t = (BASE ** (zz - z0) - 1) / (BASE ** (z1 - z0) - 1);
      return v0 + (v1 - v0) * t;
    }
  }
  return stops[stops.length - 1][1];
}

// MapLibre only allows a zoom interpolation as the outermost expression, so
// data-dependent ramps (width by road class, ...) are flattened: one zoom stop
// per breakpoint, each holding a data expression built by `fn(zoom)`.
function byZoom(zooms, fn) {
  const out = ['interpolate', ['exponential', BASE], ['zoom']];
  for (const zz of [...new Set(zooms)].sort((a, b) => a - b)) out.push(zz, fn(zz));
  return out;
}
const zoomsOf = (...ramps) => ramps.flatMap((r) => r.map(([zz]) => zz));

function vis(group, visible) {
  return visible[group] === false ? 'none' : 'visible';
}

/**
 * Build the style for one panel.
 *
 * @param {object} o
 * @param {'light'|'dark'} o.theme
 * @param {string} o.city - 'ka' or 'ulm'
 * @param {Record<string, boolean>} o.visible - group -> shown
 * @param {string} o.network - transit network to show for Ulm ('2026' / '2027')
 * @param {(path: string) => string} o.url - resolves a site-relative path to an absolute URL
 */
export function buildStyle({ theme, city, visible, network, url }) {
  const p = palette(theme);
  const pm = (name) => `pmtiles://${url(`data/${name}.pmtiles`)}`;
  const nets = ['literal', ['current', network]];
  const L = [];
  const add = (group, layer) => {
    layer.metadata = { group };
    layer.layout = { ...(layer.layout ?? {}), visibility: vis(group, visible) };
    L.push(layer);
  };

  add('base', { id: 'background', type: 'background', paint: { 'background-color': p.bg } });

  // ---- land use & water
  add('green', {
    id: 'landuse', type: 'fill', source: 'base', 'source-layer': 'landuse',
    paint: {
      'fill-color': ['match', ['get', 'class'], ...Object.entries(p.landuse).flat(), p.bg],
      'fill-antialias': false,
    },
  });
  add('base', { id: 'water', type: 'fill', source: 'base', 'source-layer': 'water', paint: { 'fill-color': p.water } });
  add('base', {
    id: 'waterway', type: 'line', source: 'base', 'source-layer': 'waterway',
    filter: ['!=', ['get', 'tunnel'], true],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': p.waterway,
      'line-width': (() => {
        const big = [[8, 1], [12, 2.5], [16, 6]];
        const small = [[8, 0], [11, 0.4], [16, 1.6]];
        return byZoom(zoomsOf(big, small), (zz) => ['match', ['get', 'class'], ['river', 'canal'], ramp(big, zz), ramp(small, zz)]);
      })(),
    },
  });

  // ---- relief: hillshade from the DEM (drawn client-side so colours follow the theme)
  add('relief', {
    id: 'hillshade', type: 'hillshade', source: 'terrain',
    paint: {
      'hillshade-shadow-color': p.hillshade.shadow,
      'hillshade-highlight-color': p.hillshade.highlight,
      'hillshade-accent-color': p.hillshade.accent,
      'hillshade-exaggeration': p.hillshade.exaggeration,
      'hillshade-method': 'multidirectional',
      'hillshade-illumination-direction': [270, 315, 0, 45],
      'hillshade-illumination-altitude': [30, 30, 30, 30],
    },
  });

  // ---- district tint for the preferred districts
  add('districts', {
    id: 'district-fill', type: 'fill', source: 'base', 'source-layer': 'districts',
    filter: ['>', ['get', 'tier'], 0],
    paint: {
      'fill-color': ['match', ['get', 'tier'], 2, p.tier2Fill, p.tier1Fill],
      'fill-opacity': theme === 'dark' ? 0.1 : 0.12,
    },
  });

  // ---- buildings
  add('buildings', {
    id: 'buildings', type: 'fill', source: 'buildings', 'source-layer': 'buildings',
    paint: {
      'fill-color': p.building,
      'fill-outline-color': ['step', ['zoom'], p.building, 15, p.buildingLine],
    },
  });

  // ---- contours (above buildings so they stay legible in dense areas)
  add('relief', {
    id: 'contours', type: 'line', source: 'contours', 'source-layer': 'contours',
    paint: {
      'line-color': p.contour,
      'line-opacity': p.contourOpacity,
      'line-width': (() => {
        const idx = [[10, 0.6], [15, 1.4]];
        const reg = [[10, 0.2], [12, 0.3], [15, 0.8]];
        return byZoom(zoomsOf(idx, reg), (zz) => ['case', ['==', ['get', 'index'], true], ramp(idx, zz), ramp(reg, zz)]);
      })(),
    },
  });

  // ---- roads: casing then fill, tunnels faded
  const roadClasses = ['motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'minor', 'service', 'pedestrian'];
  const roadRamps = [
    [['motorway', 'trunk'], [[8, 0.8], [12, 2.6], [16, 12]]],
    [['primary'], [[8, 0.6], [12, 2.2], [16, 10]]],
    [['secondary'], [[8, 0.4], [9, 0.5], [12, 1.8], [16, 9]]],
    [['tertiary'], [[8, 0.3], [10, 0.4], [12, 1.3], [16, 8]]],
    [['minor', 'pedestrian'], [[8, 0.2], [11, 0.3], [13, 0.9], [16, 6]]],
  ];
  const otherRoad = [[8, 0.2], [13, 0.3], [16, 3]];
  const roadWidth = (factor) => byZoom(zoomsOf(otherRoad, ...roadRamps.map(([, r]) => r)), (zz) => [
    'match', ['get', 'class'],
    ...roadRamps.flatMap(([cls, r]) => [cls, ramp(r, zz) * factor]),
    ramp(otherRoad, zz) * factor,
  ]);
  add('roads', {
    id: 'road-casing', type: 'line', source: 'base', 'source-layer': 'roads', minzoom: 11,
    filter: ['in', ['get', 'class'], ['literal', roadClasses]],
    layout: { 'line-cap': 'round', 'line-join': 'round', 'line-sort-key': ['coalesce', ['get', 'layer'], 0] },
    paint: {
      'line-color': p.casing,
      'line-width': roadWidth(1),
      'line-opacity': ['case', ['==', ['get', 'tunnel'], true], p.tunnelOpacity, 1],
    },
  });
  add('roads', {
    id: 'road-path', type: 'line', source: 'base', 'source-layer': 'roads', minzoom: 13,
    filter: ['in', ['get', 'class'], ['literal', ['path', 'track']]],
    layout: { 'line-join': 'round' },
    paint: {
      'line-color': ['match', ['get', 'class'], 'track', p.road.track, p.road.path],
      'line-width': z([[13, 0.4], [16, 1.2]]),
      'line-dasharray': [2, 1.5],
    },
  });
  add('roads', {
    id: 'road', type: 'line', source: 'base', 'source-layer': 'roads',
    filter: ['in', ['get', 'class'], ['literal', roadClasses]],
    layout: { 'line-cap': 'round', 'line-join': 'round', 'line-sort-key': ['coalesce', ['get', 'layer'], 0] },
    paint: {
      'line-color': ['match', ['get', 'class'], ...roadClasses.flatMap((c) => [c, p.road[c]]), p.road.minor],
      // The casing (from z11) shows as a thin rim because the fill is drawn narrower.
      'line-width': roadWidth(0.72),
      'line-opacity': ['case', ['==', ['get', 'tunnel'], true], p.tunnelOpacity, 1],
    },
  });

  // ---- rail infrastructure (OSM) and tram tracks
  add('tram', {
    id: 'rail', type: 'line', source: 'base', 'source-layer': 'rail',
    filter: ['in', ['get', 'class'], ['literal', ['rail', 'rail_service']]],
    paint: {
      'line-color': p.rail,
      'line-width': (() => {
        const main = [[8, 0.6], [13, 1.6], [16, 3]];
        const svc = [[8, 0.2], [12, 0.3], [16, 1.2]];
        return byZoom(zoomsOf(main, svc), (zz) => ['match', ['get', 'class'], 'rail', ramp(main, zz), ramp(svc, zz)]);
      })(),
      'line-opacity': ['case', ['==', ['get', 'tunnel'], true], 0.35, 1],
    },
  });
  add('tram', {
    id: 'rail-dash', type: 'line', source: 'base', 'source-layer': 'rail', minzoom: 13,
    filter: ['all', ['==', ['get', 'class'], 'rail'], ['!=', ['get', 'tunnel'], true]],
    paint: { 'line-color': p.railDash, 'line-width': z([[13, 0.6], [16, 1.6]]), 'line-dasharray': [3, 3] },
  });
  add('tram', {
    id: 'tram-infra', type: 'line', source: 'base', 'source-layer': 'rail',
    filter: ['==', ['get', 'class'], 'tram'],
    paint: {
      'line-color': p.tramInfra,
      'line-width': z([[10, 0.4], [16, 1.4]]),
      'line-opacity': ['case', ['==', ['get', 'tunnel'], true], 0.4, 0.8],
    },
  });

  // ---- cycle infrastructure: distinct style per category
  const cyc = (kind, paint, extra = {}) => add('cycle', {
    id: `cycle-${kind}`, type: 'line', source: 'cycle', 'source-layer': 'cycle',
    filter: ['==', ['get', 'kind'], kind],
    layout: { 'line-join': 'round', 'line-cap': extra.cap ?? 'butt' },
    paint,
  });
  cyc('street', { 'line-color': p.cycle.street, 'line-opacity': 0.45, 'line-width': z([[10, 2], [13, 5], [16, 14]]) });
  cyc('path', { 'line-color': p.cycle.path, 'line-width': z([[10, 0.7], [13, 1.4], [16, 2.6]]), 'line-dasharray': [2, 1] });
  cyc('busway', { 'line-color': p.cycle.busway, 'line-width': z([[10, 1], [13, 2], [16, 3.5]]), 'line-dasharray': [0.6, 1.4] }, { cap: 'round' });
  cyc('lane', { 'line-color': p.cycle.lane, 'line-width': z([[10, 0.9], [13, 1.8], [16, 3.2]]), 'line-dasharray': [3, 1.5] });
  cyc('track', { 'line-color': p.cycle.track, 'line-width': z([[10, 1], [13, 2], [16, 3.6]]) }, { cap: 'round' });

  // ---- transit lines (GTFS), tram above bus
  const lineFilter = (mode) => ['all', ['==', ['get', 'mode'], mode], ['in', ['get', 'network'], nets]];
  add('bus', {
    id: 'bus-lines', type: 'line', source: 'transit', 'source-layer': 'transit_lines', filter: lineFilter('bus'),
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: { 'line-color': ['get', 'color'], 'line-opacity': 0.85, 'line-width': z([[9, 0.8], [13, 1.8], [16, 3.4]]) },
  });
  add('tram', {
    id: 'tram-lines-casing', type: 'line', source: 'transit', 'source-layer': 'transit_lines', filter: lineFilter('tram'),
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: { 'line-color': p.lineCasing, 'line-width': z([[9, 2.2], [13, 4], [16, 7.5]]), 'line-opacity': 0.8 },
  });
  add('tram', {
    id: 'tram-lines', type: 'line', source: 'transit', 'source-layer': 'transit_lines', filter: lineFilter('tram'),
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: { 'line-color': ['get', 'color'], 'line-width': z([[9, 1.2], [13, 2.4], [16, 4.5]]) },
  });

  // ---- boundaries
  add('base', {
    id: 'state-border-halo', type: 'line', source: 'base', 'source-layer': 'border',
    paint: { 'line-color': p.border, 'line-opacity': 0.18, 'line-width': z([[8, 4], [16, 12]]) },
  });
  add('base', {
    id: 'state-border', type: 'line', source: 'base', 'source-layer': 'border',
    paint: { 'line-color': p.border, 'line-width': z([[8, 1], [16, 2.5]]), 'line-dasharray': [4, 2, 1, 2] },
  });
  add('districts', {
    id: 'district-line', type: 'line', source: 'base', 'source-layer': 'districts',
    paint: { 'line-color': p.district, 'line-width': z([[9, 0.6], [16, 2]]), 'line-dasharray': [3, 2] },
  });
  add('districts', {
    id: 'municipality-line', type: 'line', source: 'base', 'source-layer': 'municipalities',
    paint: { 'line-color': p.municipality, 'line-width': z([[8, 1], [16, 3]]), 'line-opacity': 0.7 },
  });

  // ---- distance rings
  add('rings', {
    id: 'rings', type: 'line', source: 'rings', filter: ['==', ['geometry-type'], 'LineString'],
    paint: { 'line-color': p.rings, 'line-width': 1.3, 'line-dasharray': [5, 3], 'line-opacity': 0.85 },
  });

  // ---- stops
  const stopNetFilter = ['any', ['in', 'current', ['get', 'networks']], ['in', network, ['get', 'networks']]];
  add('bus', {
    id: 'bus-stops', type: 'circle', source: 'transit', 'source-layer': 'transit_stops', minzoom: 12,
    filter: ['all', stopNetFilter, ['!', ['in', 'tram', ['get', 'modes']]]],
    paint: {
      'circle-radius': z([[12, 1.6], [16, 4]]), 'circle-color': p.stop,
      'circle-stroke-color': p.stopStroke, 'circle-stroke-width': z([[12, 0.6], [16, 1.2]]),
    },
  });
  add('tram', {
    id: 'tram-stops', type: 'circle', source: 'transit', 'source-layer': 'transit_stops', minzoom: 10,
    filter: ['all', stopNetFilter, ['in', 'tram', ['get', 'modes']]],
    paint: {
      'circle-radius': z([[10, 1.6], [13, 3], [16, 5.5]]), 'circle-color': p.stop,
      'circle-stroke-color': p.stopStroke, 'circle-stroke-width': z([[10, 0.8], [16, 1.6]]),
    },
  });

  // ---- Ü3 kindergartens within 2 km of the ring centre: filled = confirmed, hollow = not confirmed
  add('kitas', {
    id: 'kita-dots', type: 'circle', source: 'kitas', minzoom: 10,
    paint: {
      'circle-radius': z([[10, 2.2], [13, 3.5], [16, 6]]),
      'circle-color': ['match', ['get', 'status'], 'confirmed', p.kita, p.halo],
      'circle-stroke-color': p.kita, 'circle-stroke-width': z([[10, 1], [16, 2]]),
    },
  });

  // ---- labels (later layers win label collisions, so the most important come last)
  const halo = { 'text-halo-color': p.halo, 'text-halo-width': 1.4, 'text-halo-blur': 0.3 };
  add('names', {
    id: 'water-label', type: 'symbol', source: 'base', 'source-layer': 'waterway', minzoom: 11,
    filter: ['all', ['in', ['get', 'class'], ['literal', ['river', 'canal']]], ['has', 'name']],
    layout: {
      'symbol-placement': 'line', 'text-field': ['get', 'name'], 'text-font': FONTS.italic,
      'text-size': z([[11, 11], [16, 14]]), 'text-letter-spacing': 0.15, 'symbol-spacing': 400,
    },
    paint: { 'text-color': p.waterLabel, ...halo },
  });
  add('relief', {
    id: 'contour-label', type: 'symbol', source: 'contours', 'source-layer': 'contours', minzoom: 13,
    filter: ['==', ['get', 'index'], true],
    layout: {
      'symbol-placement': 'line', 'text-field': ['concat', ['to-string', ['get', 'ele']], ' m'],
      'text-font': FONTS.regular, 'text-size': 10, 'symbol-spacing': 350,
    },
    paint: { 'text-color': p.contourLabel, ...halo },
  });
  add('names', {
    id: 'road-label', type: 'symbol', source: 'base', 'source-layer': 'roads', minzoom: 14,
    filter: ['all', ['has', 'name'], ['in', ['get', 'class'], ['literal', ['primary', 'secondary', 'tertiary', 'minor', 'pedestrian']]]],
    layout: {
      'symbol-placement': 'line', 'text-field': ['get', 'name'], 'text-font': FONTS.regular,
      'text-size': z([[14, 10], [17, 12]]), 'symbol-spacing': 300,
    },
    paint: { 'text-color': p.quarter, ...halo },
  });
  add('names', {
    id: 'rail-station-label', type: 'symbol', source: 'base', 'source-layer': 'stations', minzoom: 12,
    layout: {
      'text-field': ['get', 'name'], 'text-font': FONTS.regular, 'text-size': 10.5,
      'text-variable-anchor': ['top', 'bottom', 'left', 'right'], 'text-radial-offset': 0.6,
      'text-max-width': 8,
    },
    paint: { 'text-color': p.ref.station, ...halo },
  });
  add('bus', {
    id: 'stop-label', type: 'symbol', source: 'transit', 'source-layer': 'transit_stops', minzoom: 15,
    filter: stopNetFilter,
    layout: {
      'text-field': ['get', 'name'], 'text-font': FONTS.regular, 'text-size': 10,
      'text-variable-anchor': ['top', 'bottom', 'left', 'right'], 'text-radial-offset': 0.7, 'text-max-width': 7,
    },
    paint: { 'text-color': p.quarter, ...halo },
  });
  add('kitas', {
    id: 'kita-label', type: 'symbol', source: 'kitas', minzoom: 14, filter: ['has', 'name'],
    layout: {
      'text-field': ['get', 'name'], 'text-font': FONTS.regular, 'text-size': 10, 'text-max-width': 8,
      'text-variable-anchor': ['top', 'bottom', 'left', 'right'], 'text-radial-offset': 0.8,
    },
    paint: { 'text-color': p.kita, ...halo },
  });
  add('rings', {
    id: 'ring-label', type: 'symbol', source: 'rings', filter: ['==', ['geometry-type'], 'Point'],
    layout: {
      // Short text when the rings are small on screen; the legend states "Luftlinie".
      'text-field': ['step', ['zoom'], ['get', 'short'], 12, ['get', 'label']],
      'text-font': FONTS.semibold, 'text-size': 10.5,
      'text-allow-overlap': false, 'text-ignore-placement': false, 'text-padding': 1,
      'symbol-sort-key': ['get', 'radius_km'],
    },
    paint: { 'text-color': p.ringLabel, ...halo, 'text-halo-width': 2 },
  });

  add('names', {
    id: 'town-label', type: 'symbol', source: 'labels', minzoom: 9,
    filter: ['in', ['get', 'kind'], ['literal', ['city', 'town', 'village']]],
    layout: {
      'text-field': ['get', 'name'], 'text-font': FONTS.semibold,
      'text-size': ['match', ['get', 'kind'], ['city', 'town'], 13, 11],
      'symbol-sort-key': ['match', ['get', 'kind'], 'city', 0, 'town', 1, 2],
    },
    paint: { 'text-color': p.town, ...halo },
  });
  add('names', {
    id: 'quarter-label', type: 'symbol', source: 'labels', minzoom: 13,
    filter: ['all', ['in', ['get', 'kind'], ['literal', ['quarter', 'neighbourhood']]], ['==', ['get', 'tier'], 0]],
    layout: {
      'text-field': ['get', 'name'], 'text-font': FONTS.italic, 'text-size': 10.5, 'text-max-width': 7,
      'symbol-sort-key': ['match', ['get', 'kind'], 'quarter', 0, 1],
    },
    paint: { 'text-color': p.quarter, ...halo },
  });
  add('names', {
    id: 'district-label', type: 'symbol', source: 'labels', minzoom: 10,
    filter: ['all', ['==', ['get', 'kind'], 'district'], ['==', ['get', 'tier'], 0]],
    layout: {
      'text-field': ['get', 'name'], 'text-font': FONTS.semibold, 'text-transform': 'uppercase',
      'text-letter-spacing': 0.06, 'text-size': z([[10, 9.5], [14, 12]]), 'text-max-width': 8,
      // Large districts first: they have the most room and anchor orientation.
      'symbol-sort-key': ['-', 0, ['get', 'area_km2']],
      'text-padding': 4,
    },
    paint: { 'text-color': p.quarter, ...halo },
  });

  // ---- labels of preferred districts: second only to the reference points
  add('names', {
    id: 'interest-label', type: 'symbol', source: 'labels', minzoom: 9.5,
    filter: ['>', ['get', 'tier'], 0],
    layout: {
      'text-field': ['get', 'name'],
      'text-font': ['match', ['get', 'tier'], 2, ['literal', FONTS.bold], ['literal', FONTS.semibold]],
      'text-transform': ['match', ['get', 'kind'], 'district', 'uppercase', 'none'],
      'text-letter-spacing': ['match', ['get', 'kind'], 'district', 0.06, 0.02],
      'text-size': ['interpolate', ['linear'], ['zoom'],
        10, ['match', ['get', 'tier'], 2, 11.5, 10.5],
        14, ['match', ['get', 'tier'], 2, 15, 13]],
      'text-max-width': 8,
      'symbol-sort-key': ['-', 3, ['get', 'tier']],
      'text-padding': 3,
      // Shifting is allowed so these and the landmark labels above can both
      // find room in the dense centres.
      'text-variable-anchor': ['center', 'top', 'bottom', 'left', 'right'],
      'text-radial-offset': 0.4,
    },
    paint: {
      'text-color': ['match', ['get', 'tier'], 2, p.tier2, p.tier1],
      'text-halo-color': p.halo, 'text-halo-width': 1.8, 'text-halo-blur': 0.2,
    },
  });

  // ---- reference points: always visible
  add('ref', {
    id: 'refpoints', type: 'circle', source: 'refpoints',
    paint: {
      'circle-radius': z([[9, 4], [16, 7]]),
      'circle-color': ['match', ['get', 'kind'], 'station', p.ref.station, 'zoo', p.ref.zoo, p.ref.landmark],
      'circle-stroke-color': p.halo, 'circle-stroke-width': 2,
    },
  });
  add('ref', {
    id: 'refpoints-label', type: 'symbol', source: 'refpoints',
    layout: {
      'text-field': ['get', 'name'], 'text-font': FONTS.bold, 'text-size': z([[9, 11], [16, 13]]),
      // Stations and zoos sit just west of the preferred districts in both cities,
      // so their labels try the west side first (anchor "right" = text left of point).
      // (text-variable-anchor-offset, unlike text-variable-anchor, may be data-driven.)
      'text-variable-anchor-offset': ['match', ['get', 'kind'],
        ['station', 'zoo'], ['literal', ['right', [-0.8, 0], 'left', [0.8, 0], 'bottom', [0, -0.8], 'top', [0, 0.8]]],
        ['literal', ['left', [0.8, 0], 'right', [-0.8, 0], 'top', [0, 0.8], 'bottom', [0, -0.8]]]],
      'text-justify': 'auto', 'text-max-width': 9,
    },
    paint: {
      'text-color': ['match', ['get', 'kind'], 'station', p.ref.station, 'zoo', p.ref.zoo, p.ref.landmark],
      'text-halo-color': p.halo, 'text-halo-width': 2,
    },
  });

  const fontFace = (file) => url(`fonts/${file}`);
  return {
    version: 8,
    name: `k-u-map ${city} ${theme}`,
    'font-faces': {
      'Noto Sans Regular': fontFace('noto-sans-latin-400-normal.woff2'),
      'Noto Sans SemiBold': fontFace('noto-sans-latin-600-normal.woff2'),
      'Noto Sans Bold': fontFace('noto-sans-latin-700-normal.woff2'),
      'Noto Sans Italic': fontFace('noto-sans-latin-400-italic.woff2'),
    },
    sources: {
      base: { type: 'vector', url: pm('base'), attribution: '© OpenStreetMap-Mitwirkende' },
      buildings: { type: 'vector', url: pm('buildings') },
      cycle: { type: 'vector', url: pm('cycle') },
      transit: { type: 'vector', url: pm('transit') },
      contours: { type: 'vector', url: pm('contours') },
      terrain: { type: 'raster-dem', url: pm('terrain'), encoding: 'terrarium', tileSize: 512 },
      labels: { type: 'geojson', data: url(`data/${city}/labels.geojson`) },
      refpoints: { type: 'geojson', data: url(`data/${city}/refpoints.geojson`) },
      rings: { type: 'geojson', data: url(`data/${city}/rings.geojson`) },
      kitas: { type: 'geojson', data: url(`data/${city}/kitas.geojson`) },
    },
    layers: L,
  };
}

export function layerGroups(style) {
  const groups = {};
  for (const l of style.layers) {
    const g = l.metadata?.group;
    (groups[g] ??= []).push(l.id);
  }
  return groups;
}
