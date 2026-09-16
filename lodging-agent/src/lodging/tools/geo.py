"""Geometría de zonas (regla 10): point-in-polygon, distancias y áreas con Shapely, en código. El modelo solo narra.

Coordenadas en grados (lat, lng). Para distancias en metros se proyecta a un plano local equirectangular
centrado en la zona: suficiente para zonas de barrio/ciudad (error < 1% a menos de ~50 km).
"""

from __future__ import annotations

import math

from shapely.geometry import Point, Polygon

from lodging.schemas import Zone

_M_PER_DEG_LAT = 110_540.0
_M_PER_DEG_LNG_EQ = 111_320.0


class ZoneGeometry:
    def __init__(self, zone: Zone):
        self.zone = zone
        lats = [p.lat for p in zone.polygon]
        lngs = [p.lng for p in zone.polygon]
        self.lat0 = sum(lats) / len(lats)
        self.lng0 = sum(lngs) / len(lngs)
        self._kx = _M_PER_DEG_LNG_EQ * math.cos(math.radians(self.lat0))
        poly = Polygon([self._xy(p.lat, p.lng) for p in zone.polygon])
        if not poly.is_valid:
            poly = poly.buffer(0)  # arregla auto-intersecciones simples
        self.polygon = poly

    def _xy(self, lat: float, lng: float) -> tuple[float, float]:
        return ((lng - self.lng0) * self._kx, (lat - self.lat0) * _M_PER_DEG_LAT)

    def status(self, lat: float, lng: float) -> tuple[bool, float]:
        """(dentro_de_zona_o_buffer, distancia_en_metros_al_borde; 0 si está adentro)."""
        pt = Point(*self._xy(lat, lng))
        if self.polygon.contains(pt) or self.polygon.touches(pt):
            return True, 0.0
        dist = float(self.polygon.exterior.distance(pt))
        return dist <= self.zone.buffer_m, round(dist)

    @property
    def area_km2(self) -> float:
        return round(self.polygon.area / 1e6, 2)

    @property
    def centroid(self) -> tuple[float, float]:
        c = self.polygon.centroid
        return (self.lat0 + c.y / _M_PER_DEG_LAT, self.lng0 + c.x / self._kx)


def zone_status(zone: Zone, lat: float | None, lng: float | None) -> tuple[bool | None, float | None]:
    """None cuando no hay coordenadas: ausencia de evidencia, no descarte."""
    if lat is None or lng is None:
        return None, None
    return ZoneGeometry(zone).status(lat, lng)
