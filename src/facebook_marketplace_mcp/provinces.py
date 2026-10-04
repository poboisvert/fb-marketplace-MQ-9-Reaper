"""Canadian provinces and territories for Marketplace search."""

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Province:
    code: str
    name: str
    aliases: tuple[str, ...]
    place_query: str
    place_name: str
    location_suffix: str
    radius_km: int


PROVINCES: tuple[Province, ...] = (
    Province("AB", "Alberta", ("alberta", "ab"), "Edmonton", "Edmonton, Alberta", "Alberta", 500),
    Province("BC", "British Columbia", ("british columbia", "bc"), "Vancouver", "Vancouver, British Columbia", "British Columbia", 650),
    Province("MB", "Manitoba", ("manitoba", "mb"), "Winnipeg", "Winnipeg, Manitoba", "Manitoba", 500),
    Province("NB", "New Brunswick", ("new brunswick", "nb"), "Fredericton", "Fredericton, New Brunswick", "New Brunswick", 300),
    Province("NL", "Newfoundland and Labrador", ("newfoundland", "newfoundland and labrador", "nl"), "St. John's", "St. John's, Newfoundland and Labrador", "Newfoundland and Labrador", 500),
    Province("NS", "Nova Scotia", ("nova scotia", "ns"), "Halifax", "Halifax, Nova Scotia", "Nova Scotia", 400),
    Province("NT", "Northwest Territories", ("northwest territories", "nt"), "Yellowknife", "Yellowknife, Northwest Territories", "Northwest Territories", 500),
    Province("NU", "Nunavut", ("nunavut", "nu"), "Iqaluit", "Iqaluit, Nunavut", "Nunavut", 500),
    Province("ON", "Ontario", ("ontario", "on"), "Toronto", "Toronto, Ontario", "Ontario", 650),
    Province("PE", "Prince Edward Island", ("prince edward island", "pei", "pe"), "Charlottetown", "Charlottetown, Prince Edward Island", "Prince Edward Island", 150),
    Province("QC", "Quebec", ("quebec", "québec", "qc"), "Quebec", "Quebec, Quebec", "Quebec", 650),
    Province("SK", "Saskatchewan", ("saskatchewan", "sk"), "Saskatoon", "Saskatoon, Saskatchewan", "Saskatchewan", 500),
    Province("YT", "Yukon", ("yukon", "yt"), "Whitehorse", "Whitehorse, Yukon", "Yukon", 400),
)


def resolve_province(value: str) -> Province:
    key = _fold(value)
    for province in PROVINCES:
        names = {_fold(province.code), _fold(province.name), *(_fold(alias) for alias in province.aliases)}
        if key in names:
            return province
    known = ", ".join(province.code for province in PROVINCES)
    raise ValueError(f'Unknown Canadian province "{value}". Use one of: {known}')


def same_place(name: str, expected: str) -> bool:
    return _fold(name) == _fold(expected)


def in_province(location: str, province: Province) -> bool:
    folded = _fold(location)
    suffix = _fold(province.location_suffix)
    return folded.endswith(", " + suffix) or folded.endswith(" " + suffix)


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii").lower().strip()
