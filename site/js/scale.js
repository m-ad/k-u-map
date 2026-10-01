// Web-Mercator scale math. Mirrors pipeline/scale.py (tested there); keep in sync.
//
// MapLibre draws the world into 512 * 2^zoom CSS pixels, so ground metres per
// pixel at latitude phi are C * cos(phi) / (512 * 2^zoom). Equal zoom levels would
// show Ulm (48.4°N) ~1.2 % smaller than Karlsruhe (49.0°N); the app therefore
// shares one metres-per-pixel value and derives each panel's zoom from its own
// centre latitude.

export const EARTH_RADIUS_M = 6378137;
export const EARTH_CIRCUMFERENCE_M = 2 * Math.PI * EARTH_RADIUS_M;
export const WORLD_TILE_PX = 512;

const rad = (deg) => (deg * Math.PI) / 180;

export function metersPerPixel(zoom, latDeg) {
  return (EARTH_CIRCUMFERENCE_M * Math.cos(rad(latDeg))) / (WORLD_TILE_PX * 2 ** zoom);
}

export function zoomForMpp(mpp, latDeg) {
  return Math.log2((EARTH_CIRCUMFERENCE_M * Math.cos(rad(latDeg))) / (WORLD_TILE_PX * mpp));
}

export function syncedZoom(zoomSrc, latSrc, latDst) {
  return zoomSrc + Math.log2(Math.cos(rad(latDst)) / Math.cos(rad(latSrc)));
}

export function fitMpp(widthM, heightM, widthPx, heightPx) {
  return Math.max(widthM / widthPx, heightM / heightPx);
}

// Shift a centre by a metric offset (east, north) on the sphere MapLibre uses.
export function offsetLngLat(lng, lat, eastM, northM) {
  const dLat = (northM / EARTH_RADIUS_M) * (180 / Math.PI);
  const dLng = (eastM / (EARTH_RADIUS_M * Math.cos(rad(lat)))) * (180 / Math.PI);
  return [lng + dLng, lat + dLat];
}
