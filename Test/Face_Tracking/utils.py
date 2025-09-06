"""Utility functions for coordinate mapping and other helpers.

This module exposes a single function to map values from one range
to another while clamping at the ends. This is useful for
converting normalized (0–1) face coordinates into servo degrees
(0–180) while respecting optional inversion.
"""

from __future__ import annotations

def map_range_clamped(value: float, in_min: float, in_max: float,
                      out_min: float, out_max: float) -> float:
    """Map ``value`` from the range (``in_min`` … ``in_max``) to
    (``out_min`` … ``out_max``), clamping the result if the input
    falls outside the source range.

    If ``in_max`` equals ``in_min`` the function returns the midpoint
    of the output range to avoid division by zero.

    :param value: The value to map.
    :param in_min: Lower bound of the input range.
    :param in_max: Upper bound of the input range.
    :param out_min: Lower bound of the output range.
    :param out_max: Upper bound of the output range.
    :returns: The mapped value within the output range.
    """
    if in_max == in_min:
        return (out_min + out_max) / 2.0
    # Normalize to 0..1 then clamp
    t = (value - in_min) / (in_max - in_min)
    t = max(0.0, min(1.0, t))
    return out_min + t * (out_max - out_min)