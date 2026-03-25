"""
geometry.py — Shared geometric utility functions.

Used across the pipeline for common operations like distance calculations,
point interpolation, and path manipulations.
"""

from __future__ import annotations

import math
from typing import List, Tuple

Point = Tuple[float, float]
Path2D = List[Point]


def distance(a: Point, b: Point) -> float:
    """Euclidean distance between two points."""
    return math.hypot(b[0] - a[0], b[1] - a[1])


def lerp(a: Point, b: Point, t: float) -> Point:
    """Linear interpolation between two points."""
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def path_length(path: Path2D) -> float:
    """Total arc length of a path."""
    return sum(distance(path[i], path[i + 1]) for i in range(len(path) - 1))


def closest_point_on_segment(p: Point, a: Point, b: Point) -> Tuple[Point, float]:
    """Return the closest point on segment AB to point P, and the distance."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-12:
        return a, distance(p, a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    proj = (ax + t * dx, ay + t * dy)
    return proj, distance(p, proj)


def bbox(path: Path2D) -> Tuple[float, float, float, float]:
    """Return (min_x, min_y, max_x, max_y) bounding box of a path."""
    xs = [p[0] for p in path]
    ys = [p[1] for p in path]
    return min(xs), min(ys), max(xs), max(ys)


def centroid(path: Path2D) -> Point:
    """Return centroid of a path (simple average of vertices)."""
    n = len(path)
    if n == 0:
        return (0.0, 0.0)
    return (sum(p[0] for p in path) / n, sum(p[1] for p in path) / n)


def offset_point(origin: Point, direction: Point, distance_px: float) -> Point:
    """Return a point offset from origin along the given direction vector by distance_px."""
    dx, dy = direction
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return origin
    ux, uy = dx / length, dy / length
    return (origin[0] + ux * distance_px, origin[1] + uy * distance_px)


def perpendicular(v: Point) -> Point:
    """Return a unit vector perpendicular (90° CCW) to v."""
    dx, dy = v
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return (0.0, 1.0)
    return (-dy / length, dx / length)
