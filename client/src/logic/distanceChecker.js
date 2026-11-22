/**
 * Distance Checker — Voice Khata
 *
 * Compares the browser’s geolocation with the shop’s lat/lng.
 * If the distance exceeds `distance_limit_km`, shows a warning.
 * Handles permission denied gracefully (no blocking).
 */

/**
 * Haversine distance between two lat/lng points.
 * @returns {number} Distance in kilometres.
 */
export function haversineKm(lat1, lng1, lat2, lng2) {
  const R = 6371; // Earth radius in km
  const toRad = (deg) => (deg * Math.PI) / 180;

  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);

  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;

  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/**
 * Check if the user is within `limitKm` of the shop.
 *
 * @param {number} shopLat
 * @param {number} shopLng
 * @param {number} limitKm
 * @returns {Promise<{ok:boolean, distance?:number, limit:number, reason?:string}>}
 */
export async function checkDistance(shopLat, shopLng, limitKm) {
  // No shop location configured → skip check
  if (!shopLat && !shopLng) {
    return { ok: true, reason: 'no_shop_location', limit: limitKm };
  }

  // Geolocation API not available
  if (typeof navigator === 'undefined' || !navigator.geolocation) {
    return { ok: true, reason: 'no_geolocation_api', limit: limitKm };
  }

  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const dist = haversineKm(
          shopLat, shopLng,
          pos.coords.latitude, pos.coords.longitude,
        );
        resolve({
          ok: dist <= limitKm,
          distance: Math.round(dist * 10) / 10, // 1 decimal
          limit: limitKm,
        });
      },
      (err) => {
        // Permission denied or timeout → don't block the user
        resolve({
          ok: true,
          reason: 'permission_denied',
          error: err.message,
          limit: limitKm,
        });
      },
      { enableHighAccuracy: false, timeout: 5000, maximumAge: 60000 },
    );
  });
}
