"""Optional real-data adapter: Mouser Electronics (the Search API).

THIS IS NOT THE PATH THIS PROJECT RUNS ON. The synthetic generator remains the
default, for the same reason it always was: a live API makes the project
un-runnable for whoever clones it, and returns different data every day, which
would destroy the reproducibility every number in the README depends on.

Unlike `nexar_adapter.py`, which is a stub whose methods raise, this one is
built: `parts()` really calls Mouser and really produces documents shaped for
the Mongo `parts` collection. It exists to show the seam is a working seam and
not just a type signature.

What it maps, and what it cannot
--------------------------------
`generate_catalog.py` emits eleven fields per part. Mouser supplies most of
them directly and none of the following is guessed at:

- `mpn`, `manufacturer`, `description`, `package` come straight off the record.
- `lifecycle_status` maps Mouser's `LifecycleStatus` onto the four values this
  project uses. Mouser leaves it blank for ordinary in-production parts, so
  blank is read as Active.
- `authorized_stock` is Mouser's own stock plus factory stock -- both are
  authorized-channel supply, which is the quantity the risk rule is asking
  about.
- `category` comes from mapping Mouser's category strings onto the thirteen
  categories this project's `datasheet_specs` shapes are built around. Parts
  that do not map are skipped rather than filed under a wrong category.
- `datasheet_specs` is parsed out of Mouser's `ProductAttributes`, which are
  free-text name/value pairs ("Resistance": "10 kOhms"). Every category has its
  own builder below, mirroring the ones in `generate_catalog.py` field for
  field. A field with no attribute behind it is omitted, never filled with a
  placeholder: the alternates ranking compares only the numeric fields two
  parts *share*, so a sparse part is ranked on what it does have instead of
  being scored against invented numbers.

Two fields genuinely are not in the feed, and pretending otherwise would be the
dishonest part:

- `date_introduced` is set to "". Mouser does not publish an introduction date.
  The consequence is specific and worth knowing: the "aged part" rule in
  `app/risk/rules.py` reads a falsy date as "unknown" and does not fire, so
  real parts score a few points lower than synthetic ones on that one rule.
- `is_defense_grade` is inferred from military part-number markers (JAN
  prefixes, M39014 / M38510 slash sheets, MIL-DTL, D38999) and from the
  connector category. That is a real signal, but it is a heuristic, not a flag
  Mouser hands you.

`price_tier` is derived from the largest quantity break and is only consumed by
the synthetic price generator. Feeding a real catalog into that generator gives
you real part numbers with invented price history, which is worse than either
one alone -- do not read anything into a forecast produced that way.

Rate limits
-----------
Mouser meters by calls per day and per minute. Calls here are spaced, retried
with backoff on 429 and 5xx, and paginated fifty at a time (the API ceiling).
Pulling a catalog the size of the synthetic one is thousands of records and
will take a while; that is the API's shape, not a bug here.

The key travels in the query string because that is what the API requires. It
is never logged, and no error message here includes the request URL.

See docs/data-sources.md.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterator, Protocol

# Allow `python ml-service/data/adapters/mouser_adapter.py` to find the app package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import get_settings  # noqa: E402

log = logging.getLogger("partscope.mouser")

MOUSER_SEARCH_URL = "https://api.mouser.com/api/v1/search/keyword"

# Mouser's own ceiling; asking for more is rejected rather than truncated.
MAX_RECORDS_PER_CALL = 50
# Keyword search does not page indefinitely, and one keyword monopolising the
# whole catalog would leave the other categories empty.
MAX_RECORDS_PER_KEYWORD = 1000

REQUEST_TIMEOUT_S = 30.0
PAUSE_BETWEEN_CALLS_S = 1.0
MAX_ATTEMPTS = 4
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class CatalogSource(Protocol):
    """The interface both the generator and this adapter satisfy."""

    def parts(self) -> Iterator[dict]:
        """Yield catalog documents shaped for the Mongo `parts` collection."""
        ...

    def price_history(self, mpns: list[str]) -> Iterator[tuple]:
        """Yield (part_mpn, week_start, broker_id, price, qty, lead_time) rows."""
        ...


class MouserAPIError(RuntimeError):
    """The API refused, or answered with something that is not a search result."""


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------
# Mouser attribute values are free text with the unit attached: "10 kOhms",
# "±100 PPM/C", "-40 C to +85 C". Everything below turns those into the SI base
# number, which each spec builder then scales into the unit its field is named
# for (capacitance_pf, rds_on_mohm, and so on).

_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")

_SI_PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
              "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}

# Unit tokens whose first letter collides with an SI prefix but is part of the
# unit itself. Without this, "±100 ppm" reads as 100 pico-something.
_NO_PREFIX_UNITS = frozenset({"ppm", "ppb", "mm", "mil", "mile", "meter", "metre"})

# Only these separate the two halves of a range. A bare hyphen is handled
# separately because it is also a minus sign, and "-40 C" must not split.
_RANGE_SPLIT_RE = re.compile(r"\s+to\s+|\s*[~–—]\s*|\s*\.{2,}\s*", re.IGNORECASE)
_HYPHEN_RANGE_RE = re.compile(r"(?<=[A-Za-z%°])\s*-\s*(?=[\d.])")


def _si_value(text: str) -> float | None:
    """Parse one Mouser value into its SI base unit. "10 kOhms" -> 10000.0."""
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    try:
        value = float(match.group().replace(",", ""))
    except ValueError:
        return None

    unit = text[match.end():].strip().lstrip("°").strip()
    if not unit:
        return value
    # First token only, and only up to any slash: "PPM/C" is ppm, "V/us" is V.
    unit = unit.split()[0].split("/")[0].strip("().,")
    if not unit:
        return value

    lowered = unit.lower()
    if lowered in _NO_PREFIX_UNITS or lowered.rstrip("s") in _NO_PREFIX_UNITS:
        return value
    # A prefix needs a unit after it: "V" is volts, "mV" is millivolts.
    if len(unit) > 1 and unit[0] in _SI_PREFIX:
        return value * _SI_PREFIX[unit[0]]
    return value


def _si_range(text: str) -> tuple[float | None, float | None]:
    """Split "-40 C to +85 C" or "2.7V-5.5V" into its two ends."""
    halves = _RANGE_SPLIT_RE.split(text, maxsplit=1)
    if len(halves) != 2:
        halves = _HYPHEN_RANGE_RE.split(text, maxsplit=1)
    if len(halves) != 2:
        return None, None
    return _si_value(halves[0]), _si_value(halves[1])


def _money(text: str | None) -> float | None:
    """Parse a Mouser price break. "$1.23" -> 1.23, and "1,23 €" -> 1.23.

    The decimal separator follows the account's locale rather than the API, so
    whichever of comma and dot appears last is the decimal point.
    """
    if not text:
        return None
    digits = re.sub(r"[^\d.,]", "", text)
    if not digits:
        return None
    if "," in digits and digits.rfind(",") > digits.rfind("."):
        digits = digits.replace(".", "").replace(",", ".")
    else:
        digits = digits.replace(",", "")
    try:
        return float(digits)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Attribute lookup
# ---------------------------------------------------------------------------

def _norm_attr(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _index_attributes(part: dict) -> dict[str, str]:
    """Mouser's ProductAttributes list -> {normalized name: value}.

    First value wins. Mouser repeats an attribute name across packaging
    variants of the same part, and the variants do not disagree on the
    electrical figures this project reads.
    """
    attrs: dict[str, str] = {}
    for attribute in part.get("ProductAttributes") or []:
        name = _norm_attr(attribute.get("AttributeName") or "")
        value = (attribute.get("AttributeValue") or "").strip()
        if name and value and value not in ("-", "--", "N/A", "TBD"):
            attrs.setdefault(name, value)
    return attrs


def _text(attrs: dict[str, str], names: tuple[str, ...]) -> str | None:
    """First attribute matching any candidate name, exact match preferred.

    Candidates are given specific-first, because the substring pass below will
    happily match "voltagerating" from "voltage" and the specific name is the
    one that means what the caller asked for.
    """
    for name in names:
        value = attrs.get(name)
        if value:
            return value
    # Mouser's attribute names drift by category -- "Voltage Rating" here,
    # "Voltage Rating DC" there -- so accept a key that contains the candidate.
    for name in names:
        for key, value in attrs.items():
            if name in key and value:
                return value
    return None


def _num(attrs: dict[str, str], names: tuple[str, ...],
         scale: float = 1.0, cast: type = float) -> float | int | None:
    """Look up an attribute and convert it into the field's own unit."""
    raw = _text(attrs, names)
    if raw is None:
        return None
    value = _si_value(raw)
    if value is None:
        return None
    value *= scale
    if cast is int:
        return int(round(value))
    # Scaling through the SI base leaves float noise: 100 nF -> 100000.0000001.
    return round(value, 6)


def _compact(fields: dict) -> dict:
    """Drop absent fields. An omitted spec is honest; a zero is a claim."""
    return {key: value for key, value in fields.items() if value is not None}


def _temp_range(attrs: dict[str, str]) -> dict | None:
    low = _num(attrs, ("minimumoperatingtemperature", "operatingtemperaturemin"))
    high = _num(attrs, ("maximumoperatingtemperature", "operatingtemperaturemax"))
    if low is None or high is None:
        span_low, span_high = _si_range(
            _text(attrs, ("operatingtemperaturerange", "operatingtemperature",
                          "temperaturerange")) or ""
        )
        low = low if low is not None else span_low
        high = high if high is not None else span_high
    return _compact({"min_c": low, "max_c": high}) or None


def _voltage_range(attrs: dict[str, str], min_names: tuple[str, ...],
                   max_names: tuple[str, ...],
                   combined_names: tuple[str, ...]) -> dict | None:
    low = _num(attrs, min_names)
    high = _num(attrs, max_names)
    if low is None or high is None:
        span_low, span_high = _si_range(_text(attrs, combined_names) or "")
        low = low if low is not None else span_low
        high = high if high is not None else span_high
    return _compact({"min_v": low, "max_v": high}) or None


# ---------------------------------------------------------------------------
# Spec builders -- one per category
# ---------------------------------------------------------------------------
# These mirror the `_specs_*` functions in generate_catalog.py field for field,
# so the two sources produce interchangeable documents. Signature is uniform:
# (attrs, part) -> dict, where `part` is the raw Mouser record for the handful
# of cases where the answer is in the category or description rather than in an
# attribute.

def _specs_mcu(attrs: dict[str, str], part: dict) -> dict:
    return _compact({
        "core": _text(attrs, ("coreprocessor", "processorcore", "core", "cpucore")),
        "flash_kb": _num(attrs, ("programmemorysize", "flashsize",
                                 "programmemory", "memorysize"), 1e-3),
        "ram_kb": _num(attrs, ("ramsize", "datarammemorysize", "ram"), 1e-3),
        "max_freq_mhz": _num(attrs, ("maximumclockfrequency", "clockfrequency",
                                     "cpuspeed", "speed"), 1e-6),
        "gpio_count": _num(attrs, ("numberofios", "numberofiolines",
                                   "numberofinputsoutputs"), cast=int),
        "adc_bits": _num(attrs, ("adcresolution", "resolution",
                                 "dataconverters"), cast=int),
        "operating_voltage": _voltage_range(
            attrs,
            ("supplyvoltagemin", "operatingsupplyvoltagemin", "voltagesupplymin"),
            ("supplyvoltagemax", "operatingsupplyvoltagemax", "voltagesupplymax"),
            ("supplyvoltage", "operatingsupplyvoltage", "voltagesupply"),
        ),
        "temp_range": _temp_range(attrs),
    })


def _specs_opamp(attrs: dict[str, str], part: dict) -> dict:
    rail = _text(attrs, ("railtorail", "outputtype", "amplifiertype"))
    return _compact({
        "channels": _num(attrs, ("numberofchannels", "numberofcircuits",
                                 "numberofamplifiers"), cast=int),
        "gbw_mhz": _num(attrs, ("gainbandwidthproduct", "gainbandwidth",
                                "bandwidth"), 1e-6),
        # Slew rate is already quoted in V/us, which is the unit this field is
        # named for, so it needs no scaling.
        "slew_rate_v_us": _num(attrs, ("slewrate",)),
        "input_offset_uv": _num(attrs, ("inputoffsetvoltage", "offsetvoltage"), 1e6),
        "supply_voltage": _voltage_range(
            attrs,
            ("supplyvoltagemin", "operatingsupplyvoltagemin", "voltagesupplymin"),
            ("supplyvoltagemax", "operatingsupplyvoltagemax", "voltagesupplymax"),
            ("supplyvoltage", "operatingsupplyvoltage", "voltagesupply"),
        ),
        "quiescent_current_ua": _num(attrs, ("currentquiescent", "quiescentcurrent",
                                             "supplycurrent"), 1e6),
        "rail_to_rail": None if rail is None else bool(re.search(
            r"rail|rrio|rro|rri", rail, re.IGNORECASE)),
    })


def _specs_capacitor(attrs: dict[str, str], part: dict) -> dict:
    dielectric = _text(attrs, ("dielectric", "dielectricmaterial",
                               "temperaturecoefficient"))
    # Tantalum lives in its own Mouser category and often has no dielectric
    # attribute at all, so the category is the more reliable answer.
    haystack = f"{part.get('Category', '')} {part.get('Description', '')}".lower()
    if "tantalum" in haystack:
        dielectric = "Tantalum"
    return _compact({
        "capacitance_pf": _num(attrs, ("capacitance",), 1e12),
        "tolerance_pct": _num(attrs, ("capacitancetolerance", "tolerance")),
        "voltage_rating_v": _num(attrs, ("voltageratingdc", "dcvoltagerating",
                                         "voltagerating", "ratedvoltage")),
        "dielectric": dielectric,
        "esr_mohm": _num(attrs, ("equivalentseriesresistance", "esr"), 1e3),
        "temp_range": _temp_range(attrs),
    })


def _specs_resistor(attrs: dict[str, str], part: dict) -> dict:
    return _compact({
        "resistance_ohm": _num(attrs, ("resistance",)),
        "tolerance_pct": _num(attrs, ("resistancetolerance", "tolerance")),
        "power_rating_w": _num(attrs, ("powerrating", "powerdissipation", "power")),
        "tcr_ppm": _num(attrs, ("temperaturecoefficient", "tcr")),
        "voltage_rating_v": _num(attrs, ("maximumworkingvoltage", "workingvoltage",
                                         "voltagerating")),
        "temp_range": _temp_range(attrs),
    })


def _specs_logic(attrs: dict[str, str], part: dict) -> dict:
    family = _text(attrs, ("logicfamily", "family", "series"))
    if family:
        # Mouser writes the family with the series prefix attached ("74LVC");
        # the generator stores the family alone, so strip it back.
        family = re.sub(r"^(?:sn)?74", "", family.strip(), flags=re.IGNORECASE) or family
    return _compact({
        "logic_family": family,
        "function": _text(attrs, ("logicfunction", "logictype", "function", "type")),
        "channels": _num(attrs, ("numberofcircuits", "numberofelements",
                                 "numberofgates", "numberofchannels"), cast=int),
        "propagation_delay_ns": _num(attrs, ("propagationdelaytime",
                                             "propagationdelay"), 1e9),
        "supply_voltage": _voltage_range(
            attrs,
            ("supplyvoltagemin", "voltagesupplymin", "operatingsupplyvoltagemin"),
            ("supplyvoltagemax", "voltagesupplymax", "operatingsupplyvoltagemax"),
            ("supplyvoltage", "voltagesupply", "operatingsupplyvoltage"),
        ),
        "output_current_ma": _num(attrs, ("outputcurrent", "outputcurrenthigh",
                                          "currentoutput"), 1e3),
    })


def _specs_zener(attrs: dict[str, str], part: dict) -> dict:
    return _compact({
        "zener_voltage_v": _num(attrs, ("zenervoltage", "nominalzenervoltage",
                                        "voltagezener", "breakdownvoltage")),
        "power_dissipation_mw": _num(attrs, ("powerdissipation", "power"), 1e3),
        "tolerance_pct": _num(attrs, ("zenervoltagetolerance", "tolerance")),
        "reverse_leakage_ua": _num(attrs, ("reverseleakagecurrent",
                                           "leakagecurrent"), 1e6),
        "forward_voltage_v": _num(attrs, ("forwardvoltage",)),
        "temp_range": _temp_range(attrs),
    })


def _specs_fet(attrs: dict[str, str], part: dict) -> dict:
    polarity = _text(attrs, ("transistorpolarity", "channelmode", "channeltype",
                             "polarity", "fettype"))
    channel = None
    if polarity:
        channel = "P-Channel" if re.search(r"\bp", polarity, re.IGNORECASE) \
            else "N-Channel"
    return _compact({
        "channel_type": channel,
        "vds_max_v": _num(attrs, ("drainsourcevoltagevdss", "drainsourcevoltage",
                                  "drainsourcebreakdownvoltage", "vdss")),
        "id_continuous_a": _num(attrs, ("continuousdraincurrentid",
                                        "currentcontinuousdrain", "draincurrent")),
        "rds_on_mohm": _num(attrs, ("drainsourceonresistancerdson",
                                    "onresistance", "rdson"), 1e3),
        "vgs_threshold_v": _num(attrs, ("gatethresholdvoltage", "gatesourcevoltage",
                                        "vgsth")),
        "gate_charge_nc": _num(attrs, ("totalgatecharge", "gatecharge", "qg"), 1e9),
        "temp_range": _temp_range(attrs),
    })


def _specs_regulator(attrs: dict[str, str], part: dict) -> dict:
    return _compact({
        "topology": _text(attrs, ("topology", "regulatortopology",
                                  "outputtype", "type")),
        "output_voltage_v": _num(attrs, ("outputvoltage", "voltageoutput",
                                         "outputvoltagefixed")),
        "input_voltage_max_v": _num(attrs, ("maximuminputvoltage", "inputvoltagemax",
                                            "voltageinputmax", "inputvoltage")),
        "output_current_max_a": _num(attrs, ("maximumoutputcurrent", "outputcurrent",
                                             "currentoutput")),
        "dropout_mv": _num(attrs, ("dropoutvoltage",), 1e3),
        "efficiency_pct": _num(attrs, ("efficiency",)),
        "quiescent_current_ua": _num(attrs, ("quiescentcurrent",
                                             "currentquiescent"), 1e6),
        "temp_range": _temp_range(attrs),
    })


def _specs_fpga(attrs: dict[str, str], part: dict) -> dict:
    return _compact({
        "logic_elements": _num(attrs, ("numberoflogicelementscells",
                                       "numberoflogicelements", "numberoflogicblocks",
                                       "logiccells"), cast=int),
        # Quoted in bits; the generator's field is kilobits.
        "block_ram_kb": _num(attrs, ("totalrambits", "rambits",
                                     "embeddedmemory"), 1e-3),
        "dsp_blocks": _num(attrs, ("numberofdspblocks", "dspblocks",
                                   "numberofmultipliers"), cast=int),
        "user_io_count": _num(attrs, ("numberofuserios", "numberofios",
                                      "numberofinputsoutputs"), cast=int),
        "speed_grade": _text(attrs, ("speedgrade", "grade")),
        "core_voltage_v": _num(attrs, ("corevoltage", "voltagesupplycore",
                                       "supplyvoltagecore")),
        "temp_range": _temp_range(attrs),
    })


def _specs_sram(attrs: dict[str, str], part: dict) -> dict:
    return _compact({
        "density_kbit": _num(attrs, ("memorysize", "density", "memorydensity"), 1e-3),
        "organization": _text(attrs, ("memoryorganization", "organization",
                                      "memoryconfiguration")),
        "access_time_ns": _num(attrs, ("maximumaccesstime", "accesstime"), 1e9),
        "interface": _text(attrs, ("memoryinterface", "interfacetype", "interface")),
        "supply_voltage": _voltage_range(
            attrs,
            ("supplyvoltagemin", "voltagesupplymin", "operatingsupplyvoltagemin"),
            ("supplyvoltagemax", "voltagesupplymax", "operatingsupplyvoltagemax"),
            ("supplyvoltage", "voltagesupply", "operatingsupplyvoltage"),
        ),
        "temp_range": _temp_range(attrs),
    })


def _specs_flash(attrs: dict[str, str], part: dict) -> dict:
    return _compact({
        "density_mbit": _num(attrs, ("memorysize", "density", "memorydensity"), 1e-6),
        "interface": _text(attrs, ("memoryinterface", "interfacetype", "interface")),
        "max_clock_mhz": _num(attrs, ("maximumclockfrequency", "clockfrequency",
                                      "frequency", "speed"), 1e-6),
        "erase_cycles": _num(attrs, ("endurance", "erasecycles",
                                     "writecycles"), cast=int),
        "page_size_bytes": _num(attrs, ("pagesize",), cast=int),
        "data_retention_years": _num(attrs, ("dataretention",), cast=int),
        "temp_range": _temp_range(attrs),
    })


def _specs_connector(attrs: dict[str, str], part: dict) -> dict:
    gender = _text(attrs, ("contactgender", "connectorgender", "gender",
                           "contacttype"))
    if gender:
        # The generator's vocabulary is Pin/Socket; Mouser says Male/Female
        # about as often.
        lowered = gender.lower()
        if "female" in lowered or "socket" in lowered:
            gender = "Socket"
        elif "male" in lowered or "pin" in lowered:
            gender = "Pin"
    return _compact({
        "shell_size": _num(attrs, ("shellsize",), cast=int),
        "contact_count": _num(attrs, ("numberofcontacts", "numberofpositions",
                                      "contactcount"), cast=int),
        "contact_gender": gender,
        "shell_plating": _text(attrs, ("shellplating", "shellfinish",
                                       "plating", "finish")),
        "service_rating_v": _num(attrs, ("servicerating", "voltagerating")),
        "mating_cycles": _num(attrs, ("matingcycles", "durability"), cast=int),
        "mil_spec": _text(attrs, ("militaryspecification", "milspec",
                                  "standard", "series")),
        "temp_range": _temp_range(attrs),
    })


def _specs_oscillator(attrs: dict[str, str], part: dict) -> dict:
    return _compact({
        "frequency_mhz": _num(attrs, ("nominalfrequency", "outputfrequency",
                                      "frequency"), 1e-6),
        "frequency_tolerance_ppm": _num(attrs, ("frequencytolerance", "tolerance")),
        "load_capacitance_pf": _num(attrs, ("loadcapacitance",), 1e12),
        "esr_ohm": _num(attrs, ("equivalentseriesresistance", "esr")),
        "stability_ppm": _num(attrs, ("frequencystability", "stability")),
        "output_type": _text(attrs, ("outputtype", "output", "logictype")),
        "temp_range": _temp_range(attrs),
    })


# ---------------------------------------------------------------------------
# Category mapping
# ---------------------------------------------------------------------------
# The genuinely hard part, and the one the Nexar stub declined to attempt.
# Mouser's taxonomy is far finer than this project's thirteen categories, so
# the mapping is many-to-one and deliberately incomplete: a part that does not
# match anything here is skipped, because filing it under a wrong category
# would put it in the alternates pool for parts it cannot substitute for.

# Checked in order, first hit wins. Order matters where a Mouser category name
# contains two of these words.
CATEGORY_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("circular mil", "mil spec connector", "mil-spec connector",
      "circular connector", "d38999"), "MIL-Spec Circular Connectors"),
    (("fpga", "field programmable gate array"), "FPGAs"),
    (("sram",), "SRAM"),
    (("microcontroller", "mcu", "microprocessor"), "Microcontrollers"),
    (("nor flash", "nand flash", "flash memory", "serial flash",
      "flash"), "Flash Memory"),
    (("crystal", "oscillator", "resonator"), "Crystal Oscillators"),
    (("zener",), "Zener Diodes"),
    (("mosfet", "power fet", "fet - single", "transistor - fet"), "Power FETs"),
    (("voltage regulator", "ldo", "switching regulator", "linear regulator",
      "dc dc converter", "dc-dc"), "Voltage Regulators"),
    (("operational amplifier", "op amp", "op-amp"), "Operational Amplifiers"),
    (("logic gate", "gates and inverters", "logic - gates",
      "inverter"), "Logic Gates"),
    (("capacitor",), "Fixed Capacitors"),
    (("resistor",), "Fixed Resistors"),
)

# Checked before the patterns. These are catalog line items Mouser groups with
# components but this project's catalog is not about: assemblies, adjustable
# parts, and anything you would not put on a BOM as a single component.
CATEGORY_EXCLUSIONS = ("network", "array", "potentiometer", "trimmer", "variable",
                       "kit", "accessor", "tool", "evaluation", "development",
                       "module", "assembly", "cable", "adapter", "socket")

# Phrases that contain an exclusion word without being one. A Field
# Programmable Gate Array is an FPGA, not a resistor array.
EXCLUSION_ALLOWANCES = ("gate array",)


def _map_category(part: dict) -> str | None:
    """Map a Mouser category onto one of this project's thirteen, or None."""
    raw = (part.get("Category") or "").lower()
    if not raw:
        return None
    excludable = raw
    for phrase in EXCLUSION_ALLOWANCES:
        excludable = excludable.replace(phrase, "")
    if any(word in excludable for word in CATEGORY_EXCLUSIONS):
        return None
    for patterns, category in CATEGORY_PATTERNS:
        if any(pattern in raw for pattern in patterns):
            return category
    return None


# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------

# Checked in order; "not recommended" has to be tested before "recommended"
# would ever match, and "end of life" before a bare "life".
LIFECYCLE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("not recommended", "NRND"),
    ("nrnd", "NRND"),
    ("last time buy", "EOL"),
    ("end of life", "EOL"),
    ("eol", "EOL"),
    ("obsolete", "Obsolete"),
    ("discontinued", "Obsolete"),
    ("inactive", "Obsolete"),
    ("not for new designs", "Obsolete"),
)

# Military part-number systems. A screened part is re-identified under the
# qualifying spec, which is exactly the marker to look for.
DEFENSE_MARKERS = ("MIL-DTL", "MIL-PRF", "MIL-STD", "MIL-C-", "M39014", "M38510",
                   "M55342", "D38999", "ESCC", "DSCC", "RAD-HARD", "RADIATION")
DEFENSE_MPN_PREFIXES = ("JAN", "JANTX", "JANTXV", "M39014", "M38510", "D38999")

# Geometric midpoints between the tier medians in generate_price_history.py
# (~$0.02, $0.25, $1.80, $10, $180). Anything dearer is premium.
PRICE_TIER_BOUNDS: tuple[tuple[float, str], ...] = (
    (0.07, "jellybean"), (0.67, "low"), (4.2, "mid"), (42.0, "high"),
)


def _lifecycle(part: dict) -> str:
    """Map Mouser's lifecycle string onto the four values this project uses.

    Mouser leaves the field blank for ordinary in-production parts, so blank is
    Active rather than unknown.
    """
    raw = (part.get("LifecycleStatus") or "").strip().lower()
    if not raw:
        return "Active"
    for pattern, status in LIFECYCLE_PATTERNS:
        if pattern in raw:
            return status
    return "Active"


def _authorized_stock(part: dict) -> int:
    """Stock reachable through the authorized channel.

    Distributor stock plus factory stock, because the risk rule is asking
    whether the part can be bought without going to a broker, and either one
    answers yes.
    """
    total = 0
    for field in ("Availability", "FactoryStock"):
        value = _si_value(str(part.get(field) or ""))
        if value and value > 0:
            total += int(value)
    return total


def _is_defense_grade(mpn: str, category: str, part: dict) -> bool:
    """Infer defense grade from military part numbering. A heuristic, not a flag."""
    if category == "MIL-Spec Circular Connectors":
        return True
    upper = mpn.upper()
    if upper.startswith(DEFENSE_MPN_PREFIXES):
        return True
    haystack = f"{upper} {(part.get('Description') or '').upper()}"
    return any(marker in haystack for marker in DEFENSE_MARKERS)


def _package(attrs: dict[str, str]) -> str:
    """The case or package, cleaned to the bare code.

    Mouser writes an 0603 as "0603 (1608 Metric)"; the generator writes "0603",
    and the matcher compares the two as strings.
    """
    raw = _text(attrs, ("packagecase", "supplierdevicepackage", "package",
                        "mountingstyle"))
    if not raw:
        return ""
    return raw.split("(")[0].strip().upper()


def _price_tier(part: dict) -> str:
    """Bucket the part by unit price at the largest quantity break.

    The largest break is the closest thing Mouser publishes to a base price:
    the small-quantity breaks carry the handling margin.
    """
    breaks = []
    for row in part.get("PriceBreaks") or []:
        price = _money(row.get("Price"))
        if price and price > 0:
            breaks.append((row.get("Quantity") or 0, price))
    if not breaks:
        return "mid"
    breaks.sort()
    unit = breaks[-1][1]
    for bound, tier in PRICE_TIER_BOUNDS:
        if unit < bound:
            return tier
    return "premium"


SPEC_BUILDERS = {
    "Microcontrollers": _specs_mcu,
    "Operational Amplifiers": _specs_opamp,
    "Fixed Capacitors": _specs_capacitor,
    "Fixed Resistors": _specs_resistor,
    "Logic Gates": _specs_logic,
    "Zener Diodes": _specs_zener,
    "Power FETs": _specs_fet,
    "Voltage Regulators": _specs_regulator,
    "FPGAs": _specs_fpga,
    "SRAM": _specs_sram,
    "Flash Memory": _specs_flash,
    "MIL-Spec Circular Connectors": _specs_connector,
    "Crystal Oscillators": _specs_oscillator,
}

# One keyword per category, chosen to hit the Mouser category the mapping above
# knows about. Keyword search returns neighbours too; `_map_category` discards
# whatever landed outside the thirteen.
DEFAULT_KEYWORDS: tuple[str, ...] = (
    "thick film chip resistor",
    "mlcc ceramic capacitor",
    "tantalum capacitor",
    "logic gate",
    "operational amplifier",
    "ldo voltage regulator",
    "switching voltage regulator",
    "power mosfet",
    "zener diode",
    "arm microcontroller",
    "8-bit microcontroller",
    "crystal oscillator",
    "nor flash memory",
    "sram memory",
    "circular mil spec connector",
    "fpga",
)


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

class MouserAdapter:
    """Reads a real catalog from the Mouser Search API.

    `parts()` works. `price_history()` raises, and that is not laziness: Mouser
    publishes today's price breaks, which is a single point, not the weekly
    broker-quote time series the forecasters train on.
    """

    def __init__(self, api_key: str, keywords: tuple[str, ...] | None = None,
                 limit: int | None = None):
        if not api_key:
            raise ValueError("MouserAdapter requires an API key")
        self.api_key = api_key
        self.keywords = tuple(keywords) if keywords else DEFAULT_KEYWORDS
        self.limit = limit if limit is not None else get_settings().catalog_size
        self._last_call_at = 0.0

    # -- HTTP --------------------------------------------------------------

    def _call(self, payload: dict) -> dict:
        """POST one search request, with backoff. Never logs the key."""
        # The key goes in the query string because that is what the API wants.
        # Nothing below ever puts `url` into a log line or an exception.
        url = f"{MOUSER_SEARCH_URL}?apiKey={urllib.parse.quote(self.api_key)}"
        body = json.dumps(payload).encode("utf-8")

        # Space the calls out: Mouser meters per minute as well as per day.
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < PAUSE_BETWEEN_CALLS_S:
            time.sleep(PAUSE_BETWEEN_CALLS_S - elapsed)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            request = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                    self._last_call_at = time.monotonic()
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                self._last_call_at = time.monotonic()
                if exc.code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
                    delay = PAUSE_BETWEEN_CALLS_S * (2 ** attempt)
                    log.warning("mouser returned HTTP %s; retrying in %.0fs "
                                "(attempt %d/%d)", exc.code, delay, attempt,
                                MAX_ATTEMPTS)
                    time.sleep(delay)
                    continue
                raise MouserAPIError(f"Mouser search failed: HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                self._last_call_at = time.monotonic()
                if attempt < MAX_ATTEMPTS:
                    delay = PAUSE_BETWEEN_CALLS_S * (2 ** attempt)
                    log.warning("mouser request failed (%s); retrying in %.0fs "
                                "(attempt %d/%d)", exc.reason, delay, attempt,
                                MAX_ATTEMPTS)
                    time.sleep(delay)
                    continue
                raise MouserAPIError(f"Mouser search failed: {exc.reason}") from exc
            except json.JSONDecodeError as exc:
                raise MouserAPIError("Mouser returned a non-JSON body") from exc

        raise MouserAPIError("Mouser search failed after retries")

    def _search(self, keyword: str) -> Iterator[dict]:
        """Page through keyword search results, oldest API contract first."""
        start = 0
        while start < MAX_RECORDS_PER_KEYWORD:
            body = self._call({
                "SearchByKeywordRequest": {
                    "keyword": keyword,
                    "records": MAX_RECORDS_PER_CALL,
                    "startingRecord": start,
                    # Left empty on purpose. Filtering to in-stock parts would
                    # drop exactly the obsolete ones this project is about.
                    "searchOptions": "",
                    "searchWithYourSignUpLanguage": "",
                }
            })

            errors = body.get("Errors") or []
            if errors:
                message = "; ".join(
                    str(error.get("Message") or error.get("Code") or error)
                    for error in errors
                )
                raise MouserAPIError(f"Mouser search rejected: {message}")

            results = body.get("SearchResults") or {}
            records = results.get("Parts") or []
            if not records:
                return

            yield from records

            start += len(records)
            total = results.get("NumberOfResult") or 0
            if total and start >= total:
                return

    # -- the interface -----------------------------------------------------

    def parts(self) -> Iterator[dict]:
        """Yield catalog documents shaped for the Mongo `parts` collection.

        Stops at `limit` parts. Deduplicates by MPN, because keyword searches
        overlap and the Mongo index on `mpn` is unique.
        """
        seen: set[str] = set()
        produced = 0
        unmapped = 0

        for keyword in self.keywords:
            log.info("mouser: searching %r (%d/%d parts so far)",
                     keyword, produced, self.limit)
            for record in self._search(keyword):
                document = self._to_document(record)
                if document is None:
                    unmapped += 1
                    continue
                if document["mpn"] in seen:
                    continue
                seen.add(document["mpn"])
                produced += 1
                yield document
                if produced >= self.limit:
                    log.info("mouser: reached the limit of %d parts (%d records "
                             "skipped as unmappable)", self.limit, unmapped)
                    return

        log.info("mouser: %d parts from %d keywords (%d records skipped as "
                 "unmappable)", produced, len(self.keywords), unmapped)

    def price_history(self, mpns: list[str]) -> Iterator[tuple]:
        raise NotImplementedError(
            "Mouser has no broker price history. The API returns the current "
            "distributor price breaks for a part -- one point in time from one "
            "authorized seller -- not the weekly multi-broker quote series this "
            "project's forecasters train on. Building that history from this "
            "source means capturing prices on a schedule and accumulating them "
            "over months, and it would still be authorized pricing rather than "
            "the secondary market."
        )

    # -- mapping -----------------------------------------------------------

    def _to_document(self, record: dict) -> dict | None:
        """One Mouser record -> one catalog document, or None if unmappable."""
        mpn = (record.get("ManufacturerPartNumber") or "").strip()
        if not mpn:
            return None

        category = _map_category(record)
        if category is None:
            return None

        attrs = _index_attributes(record)
        return {
            "mpn": mpn,
            "manufacturer": (record.get("Manufacturer") or "").strip(),
            "category": category,
            "description": (record.get("Description") or "").strip(),
            "package": _package(attrs),
            "lifecycle_status": _lifecycle(record),
            "is_defense_grade": _is_defense_grade(mpn, category, record),
            "authorized_stock": _authorized_stock(record),
            "datasheet_specs": SPEC_BUILDERS[category](attrs, record),
            # Mouser does not publish an introduction date. Empty rather than
            # invented: the risk age rule reads a falsy date as unknown and
            # does not fire, which is the correct behaviour for missing data.
            "date_introduced": "",
            "price_tier": _price_tier(record),
        }


def get_catalog_source() -> tuple[object, str]:
    """Return the active data source and a one-line description of why.

    Mirrors `nexar_adapter.get_catalog_source`. The fallback is not an error
    condition: synthetic data is the intended default, and this reports which
    source is in use so it can never be ambiguous which one produced the
    numbers on screen.
    """
    from data import generate_catalog

    api_key = get_settings().mouser_api_key
    if not api_key:
        log.info(
            "MOUSER_API_KEY not set - using the synthetic generator. "
            "This is the intended default; see docs/data-sources.md."
        )
        return generate_catalog, "synthetic (seeded generator)"

    log.warning(
        "MOUSER_API_KEY is set - reading the catalog from the Mouser Search "
        "API. Results change day to day, so none of the figures quoted in the "
        "README are reproducible against this source."
    )
    return MouserAdapter(api_key), "live (Mouser Search API)"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch a parts catalog from the Mouser Search API")
    parser.add_argument("--limit", type=int, default=50,
                        help="how many parts to fetch (default 50)")
    parser.add_argument("--keyword", action="append",
                        help="override the default search keywords (repeatable)")
    parser.add_argument("--sample", type=int, default=10,
                        help="how many example parts to print")
    parser.add_argument("--write", action="store_true",
                        help="write to Mongo; the default is a dry run")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    api_key = get_settings().mouser_api_key
    if not api_key:
        print("MOUSER_API_KEY is not set. This adapter is optional -- see "
              ".env.example and docs/data-sources.md.")
        return 1

    from data.generate_catalog import summarise, write_to_mongo

    adapter = MouserAdapter(api_key, keywords=args.keyword, limit=args.limit)
    parts = list(adapter.parts())
    if not parts:
        print("no mappable parts returned")
        return 1

    print(summarise(parts))
    if args.sample:
        print("\n  sample:")
        for part in parts[:args.sample]:
            print(f"      {part['mpn']:<30} {part['manufacturer']:<20} "
                  f"{part['lifecycle_status']:<9} {part['description'][:52]}")

    if not args.write:
        print("\ndry run: nothing written")
        return 0

    inserted = write_to_mongo(parts)
    settings = get_settings()
    print(f"\nwrote {inserted} parts to mongo "
          f"({settings.mongo_uri}/{settings.mongo_db}.parts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
