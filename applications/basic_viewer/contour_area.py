"""Vector differences and conservative cell integration, batched in native code."""
from dataclasses import dataclass
import math

import numpy as np
import shapely


class InvalidContour(ValueError):
    pass


@dataclass
class Region:
    geometry: shapely.Geometry

    @property
    def area(self):
        return float(self.geometry.area)

    @property
    def rings(self):
        return [shapely.get_coordinates(ring).tolist()
                for ring in shapely.get_rings(shapely.get_parts(self.geometry))]


def signed_area(points):
    if not len(points):
        return 0.0
    ox, oy = points[0]
    return math.fsum((ax - ox) * (by - oy) - (bx - ox) * (ay - oy)
                     for (ax, ay), (bx, by) in zip(points, list(points[1:]) + [points[0]])) / 2


def region(path):
    return Region(path)


def polygon(points):
    coordinates = np.asarray(points, dtype=np.float64)
    if len(points) < 3 or not np.isfinite(coordinates).all():
        raise InvalidContour("Contour needs at least three finite vertices")
    geometry = shapely.Polygon(coordinates)
    if not geometry.is_valid or geometry.area <= 0:
        raise InvalidContour("Contour must enclose one simple non-zero region")
    return geometry, geometry.area


def difference(base, comparison):
    b, base_area = polygon(base)
    c, comp_area = polygon(comparison)
    inward, outward = Region(b.difference(c)), Region(c.difference(b))
    if not math.isclose(inward.area - outward.area, base_area - comp_area,
                        rel_tol=1e-8, abs_tol=1e-7 * max(1, base_area, comp_area)):
        raise InvalidContour("Contour subtraction failed area conservation")
    return inward, outward


def _split_edges(starts, ends, axis):
    """Split at integer grid lines in traversal order, without a global sort."""
    delta = ends - starts
    low, high = np.minimum(starts[:, axis], ends[:, axis]), np.maximum(starts[:, axis], ends[:, axis])
    first = np.floor(low).astype(np.int64) + 1
    counts = np.maximum(0, np.ceil(high).astype(np.int64) - first)
    segments = counts + 1
    edge = np.repeat(np.arange(len(starts)), segments)
    step = np.arange(segments.sum()) - np.repeat(np.cumsum(segments) - segments, segments)
    forward = delta[edge, axis] > 0
    boundary = np.where(forward, first[edge] + step, np.ceil(high[edge]) - 1 - step)
    denominator = np.where(delta[edge, axis] != 0, delta[edge, axis], 1)
    end_time = np.where(step < counts[edge], (boundary - starts[edge, axis]) / denominator, 1.0)
    start_time = np.where(step > 0, np.roll(end_time, 1), 0.0)
    return starts[edge] + delta[edge] * start_time[:, None], starts[edge] + delta[edge] * end_time[:, None]


def area_grid(shapes, extent, bins):
    """Sum exact covered areas in bounded batches; never raster-sample geometry."""
    scale = bins / (2 * extent)
    coverage = np.zeros((bins, bins + 1), dtype=np.float64)
    moments = np.zeros_like(coverage)
    expected = 0.0
    for offset in range(0, len(shapes), 256):
        geometries = shapely.orient_polygons(np.asarray(shapes[offset:offset + 256], dtype=object))
        expected += float(shapely.area(geometries).sum())
        rings = shapely.get_rings(shapely.get_parts(geometries))
        coordinates, identities = shapely.get_coordinates(rings, return_index=True)
        if not len(coordinates):
            continue
        coordinates = (coordinates + [extent, -extent]) * [scale, -scale]
        if not np.isfinite(coordinates).all() or coordinates.min() < 0 or coordinates.max() > bins:
            raise ValueError("Area grid does not contain the full difference region")
        active = (identities[:-1] == identities[1:]) & (coordinates[:-1, 1] != coordinates[1:, 1])
        starts, ends = coordinates[:-1][active], coordinates[1:][active]
        starts, ends = _split_edges(starts, ends, 1)
        delta = ends - starts
        first = np.floor(np.minimum(starts[:, 0], ends[:, 0])).astype(np.int64)
        last = np.ceil(np.maximum(starts[:, 0], ends[:, 0])).astype(np.int64) - 1
        counts = np.maximum(1, last - first + 1)
        edge = np.repeat(np.arange(len(starts)), counts)
        col = first[edge] + np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
        dx = delta[edge, 0]
        denominator = np.where(dx != 0, dx, 1)
        ta = np.clip((col - starts[edge, 0]) / denominator, 0, 1)
        tb = np.clip((col + 1 - starts[edge, 0]) / denominator, 0, 1)
        a = np.where(dx == 0, 0, np.minimum(ta, tb))
        b = np.where(dx == 0, 1, np.maximum(ta, tb))
        row = np.floor((starts[edge, 1] + ends[edge, 1]) / 2).astype(np.int64)
        # Exterior rings reverse winding after screen-Y inversion; holes reverse too.
        cover = -delta[edge, 1] * (b - a)
        moment = (starts[edge, 0] + dx * ((a + b) / 2) - col) * cover
        cell = row * (bins + 1) + col
        np.add.at(coverage.ravel(), cell, cover)
        np.add.at(moments.ravel(), cell, moment)
    grid = (moments - np.cumsum(coverage, axis=1))[:, :bins] / scale ** 2
    if grid.min() < -1e-7:
        raise ArithmeticError("Invalid integrated cell area")
    np.maximum(grid, 0, out=grid)
    if not math.isclose(float(grid.sum()), expected, rel_tol=1e-8, abs_tol=1e-7):
        raise ArithmeticError("Area grid failed conservation")
    return grid


def area_cells(shape, extent, bins):
    grid = area_grid([shape.geometry], extent, bins).ravel()
    return {int(i): float(grid[i]) for i in np.flatnonzero(grid)}
