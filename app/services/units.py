"""Unit conversion. Everything is stored in square metres / metres."""
from typing import Literal

AreaUnit = Literal["sq_m", "sq_km", "ha", "acre", "sq_ft"]
LengthUnit = Literal["m", "km", "ft", "mi"]

AREA_FACTORS = {
    "sq_m": 1.0,
    "sq_km": 1e-6,
    "ha": 1e-4,
    "acre": 1 / 4046.8564224,
    "sq_ft": 10.763910416709722,
}
LENGTH_FACTORS = {
    "m": 1.0,
    "km": 1e-3,
    "ft": 3.280839895013123,
    "mi": 1 / 1609.344,
}


def convert(value: float | None, factor: float, digits: int = 6) -> float | None:
    return None if value is None else round(value * factor, digits)
