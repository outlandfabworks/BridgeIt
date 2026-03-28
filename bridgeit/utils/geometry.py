"""
geometry.py — Shared geometric utility functions.

Used across the pipeline for common operations like distance calculations,
point interpolation, and path manipulations.
"""

from __future__ import annotations

import math
from typing import List, Tuple

# Type aliases make function signatures easier to read.
# A Point is just a pair of floats representing (x, y) in pixel space.
Point = Tuple[float, float]

# A Path2D is an ordered list of (x, y) points forming a polygon or polyline.
Path2D = List[Point]


def distance(a: Point, b: Point) -> float:
    """Euclidean distance between two points."""
    # math.hypot computes sqrt(dx²+dy²) without risk of overflow
    return math.hypot(b[0] - a[0], b[1] - a[1])


def lerp(a: Point, b: Point, t: float) -> Point:
    """Linear interpolation between two points."""
    # t=0.0 returns exactly point a; t=1.0 returns exactly point b;
    # t=0.5 returns the midpoint. Values outside [0,1] extrapolate.
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def path_length(path: Path2D) -> float:
    """Total arc length of a path."""
    # Sum the lengths of each consecutive segment in the polyline.
    # This does not close the loop — if the path is closed, the caller
    # should ensure path[0] == path[-1] is included.
    return sum(distance(path[i], path[i + 1]) for i in range(len(path) - 1))


def closest_point_on_segment(p: Point, a: Point, b: Point) -> Tuple[Point, float]:
    """Return the closest point on segment AB to point P, and the distance."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay

    # seg_len_sq is the squared length of the segment, used to normalise t.
    # We use the squared length to avoid an unnecessary sqrt here.
    seg_len_sq = dx * dx + dy * dy

    # If the segment is degenerate (zero length), return the start point
    if seg_len_sq < 1e-12:
        return a, distance(p, a)

    # t is the parameter in [0,1] describing how far along AB the
    # projection of P falls. 0=at A, 1=at B.
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))

    # Compute the actual projected point on the segment
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
    # Guard against empty paths to avoid division by zero
    if n == 0:
        return (0.0, 0.0)
    # The centroid is the arithmetic mean of all vertex coordinates.
    # Note: this is a vertex centroid, not the area centroid of the polygon.
    return (sum(p[0] for p in path) / n, sum(p[1] for p in path) / n)


def offset_point(origin: Point, direction: Point, distance_px: float) -> Point:
    """Return a point offset from origin along the given direction vector by distance_px."""
    dx, dy = direction

    # Compute the length of the direction vector so we can normalise it
    length = math.hypot(dx, dy)

    # If the direction vector is essentially zero, we can't move anywhere
    if length < 1e-9:
        return origin

    # Normalise the direction to a unit vector (length 1), then scale by distance
    ux, uy = dx / length, dy / length
    return (origin[0] + ux * distance_px, origin[1] + uy * distance_px)


def perpendicular(v: Point) -> Point:
    """Return a unit vector perpendicular (90° CCW) to v."""
    dx, dy = v
    length = math.hypot(dx, dy)

    # If the input vector is degenerate, return an arbitrary unit vector
    if length < 1e-9:
        return (0.0, 1.0)

    # Rotating (dx, dy) by 90° counter-clockwise gives (-dy, dx).
    # Dividing by length turns it into a unit vector.
    return (-dy / length, dx / length)
