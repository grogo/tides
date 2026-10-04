#!/usr/bin/env python3
"""
honolulu_tides.py - Fetch NOAA tide data for Honolulu, HI into a CSV or text file.

Data source: NOAA CO-OPS Data API
    https://api.tidesandcurrents.noaa.gov/api/prod/
Station:     1612340 (Honolulu, HI)

Examples
--------
  # 24 hours of hourly predictions starting 2026-10-04 at midnight -> CSV
  python3 honolulu_tides.py 2026-10-04

  # 12 hours starting at 06:30, one sample every 15 minutes, text output
  python3 honolulu_tides.py 2026-10-04 06:30 --duration 12 --interval 15 --format txt

  # Only the high/low tide events for the next 3 days
  python3 honolulu_tides.py 2026-10-04 --duration 3d --interval hilo -o highs_lows.csv

  # Observed (measured) water levels instead of predictions, in metres
  python3 honolulu_tides.py 2026-09-01 --product water_level --units metric

Slope file
----------
Every run also writes <output-stem>_slope.csv alongside the main output
(e.g. honolulu_tides_2026-10-04_0000.csv -> honolulu_tides_2026-10-04_0000_slope.csv).
It holds, for each timepoint, the rate of rise/fall of the water level in
height-units per hour plus a rising/falling/steady label:

    date,time,height_ft,slope_ft_per_hr,trend

For evenly spaced data the slope is a central difference (one-sided at the
first and last rows). For 'hilo' output the slope at a high or low is zero by
definition, so the file instead reports the average rate of rise/fall between
the previous event and this one. Use --no-slope to skip it, or
--slope-output PATH to choose the name.

Only the Python standard library is required.
"""

import argparse
import csv
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

API_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
STATION_ID = "1612340"          # Honolulu, HI
STATION_NAME = "Honolulu, HI"
APPLICATION = "honolulu_tides_cli"

# Intervals accepted by the NOAA API for the "predictions" product.
MINUTE_INTERVALS = {"1", "5", "6", "10", "15", "30", "60"}
VALID_INTERVALS = MINUTE_INTERVALS | {"h", "hilo"}


# --------------------------------------------------------------------------- #
# Command-line parsing
# --------------------------------------------------------------------------- #
def parse_date(text):
    """Accept YYYY-MM-DD, YYYY/MM/DD, or YYYYMMDD."""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(
        f"invalid date '{text}' (expected YYYY-MM-DD)")


def parse_time(text):
    """Accept HH:MM, HHMM, or HH (24-hour local time)."""
    for fmt in ("%H:%M", "%H%M", "%H"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(
        f"invalid time '{text}' (expected HH:MM, 24-hour)")


def parse_duration(text):
    """
    Accept a duration as a number of hours (e.g. '24', '1.5') or a number with
    a unit suffix: m = minutes, h = hours, d = days (e.g. '90m', '36h', '7d').
    """
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([mhd]?)\s*", text, re.IGNORECASE)
    if not match:
        raise argparse.ArgumentTypeError(
            f"invalid duration '{text}' (examples: 24, 36h, 7d, 90m)")
    value = float(match.group(1))
    unit = match.group(2).lower() or "h"
    seconds = {"m": 60, "h": 3600, "d": 86400}[unit] * value
    if seconds <= 0:
        raise argparse.ArgumentTypeError("duration must be greater than zero")
    return timedelta(seconds=seconds)


def parse_interval(text):
    value = text.strip().lower()
    if value in ("hourly", "hour"):
        value = "h"
    if value not in VALID_INTERVALS:
        raise argparse.ArgumentTypeError(
            f"invalid interval '{text}' (choose one of: "
            f"{', '.join(sorted(MINUTE_INTERVALS, key=int))} minutes, h, hilo)")
    return value


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=f"Fetch NOAA tide data for {STATION_NAME} "
                    f"(station {STATION_ID}) into a CSV or text file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Intervals: 1, 5, 6, 10, 15, 30, 60 (minutes), 'h' (hourly) or\n"
            "'hilo' (high/low tide events only; predictions product only).\n"
            "Durations: hours by default; add a suffix for minutes (m), hours (h)\n"
            "or days (d), e.g. 36h, 7d, 90m.\n"
            "All times are Honolulu local time (HST, UTC-10)."
        ),
    )
    parser.add_argument("date", type=parse_date,
                        help="start date, YYYY-MM-DD (local)")
    parser.add_argument("time", type=parse_time, nargs="?",
                        default=parse_time("00:00"),
                        help="start time, HH:MM 24-hour local (default 00:00)")
    parser.add_argument("-d", "--duration", type=parse_duration, default="24",
                        help="how long to fetch, in hours or with m/h/d suffix "
                             "(default 24)")
    parser.add_argument("-i", "--interval", type=parse_interval, default="60",
                        help="sample spacing: minutes (1,5,6,10,15,30,60), "
                             "'h', or 'hilo' (default 60)")
    parser.add_argument("-p", "--product",
                        choices=["predictions", "water_level"],
                        default="predictions",
                        help="'predictions' (tide tables, default) or "
                             "'water_level' (observed measurements)")
    parser.add_argument("-u", "--units", choices=["english", "metric"],
                        default="english",
                        help="english = feet (default), metric = metres")
    parser.add_argument("--datum", default="MLLW",
                        help="vertical datum, e.g. MLLW (default), MSL, MHHW, STND")
    parser.add_argument("-f", "--format", choices=["csv", "txt"],
                        help="output format; inferred from the output file "
                             "extension if omitted (default csv)")
    parser.add_argument("-o", "--output",
                        help="output file path ('-' for stdout). Default: "
                             "honolulu_tides_<date>_<time>.<csv|txt>")
    parser.add_argument("--slope-output", metavar="PATH",
                        help="where to write the slope CSV (default: "
                             "<output-stem>_slope.csv)")
    parser.add_argument("--no-slope", action="store_true",
                        help="do not write the slope CSV file")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="suppress progress messages")
    return parser


# --------------------------------------------------------------------------- #
# NOAA API
# --------------------------------------------------------------------------- #
def noaa_timestamp(dt):
    """NOAA expects 'yyyyMMdd HH:mm'."""
    return dt.strftime("%Y%m%d %H:%M")


def build_query(start, end, interval, product, units, datum):
    params = {
        "station": STATION_ID,
        "product": product,
        "datum": datum,
        "units": units,
        "time_zone": "lst_ldt",          # station local time
        "format": "json",
        "application": APPLICATION,
        "begin_date": noaa_timestamp(start),
        "end_date": noaa_timestamp(end),
    }
    if product == "predictions":
        params["interval"] = interval
    elif interval == "h":
        # Observed data supports hourly via interval=h; other spacings are
        # thinned locally after download (API only returns 6-minute data).
        params["interval"] = "h"
    return params


def fetch_noaa(params, timeout=30):
    url = API_URL + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": APPLICATION})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"error: NOAA API returned HTTP {exc.code}: {exc.reason}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"error: could not reach NOAA API: {exc.reason}")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        raise SystemExit("error: NOAA API returned a non-JSON response:\n" + payload[:500])

    if "error" in data:
        message = data["error"].get("message", str(data["error"]))
        raise SystemExit(f"error: NOAA API: {message.strip()}")
    return data, url


def extract_rows(data, product):
    """
    Normalise the API response into a list of dicts with keys
    'time' (datetime), 'height' (float or None), and 'type' ('H', 'L' or '').
    """
    records = data.get("predictions" if product == "predictions" else "data", [])
    rows = []
    for record in records:
        when = datetime.strptime(record["t"], "%Y-%m-%d %H:%M")
        raw = record.get("v", "")
        try:
            height = float(raw)
        except (TypeError, ValueError):
            height = None                 # missing observation
        tide_type = record.get("type", "")
        rows.append({"time": when, "height": height, "type": tide_type})
    return rows


def thin_rows(rows, interval, product):
    """
    The water_level product only honours interval=h; for other requested
    spacings, keep every record that falls on the requested minute boundary.
    """
    if product != "water_level" or interval in ("h", "hilo"):
        return rows
    step = int(interval)
    if step <= 6:
        return rows
    return [r for r in rows
            if (r["time"].hour * 60 + r["time"].minute) % step == 0]


# --------------------------------------------------------------------------- #
# Slope (rate of rise / fall)
# --------------------------------------------------------------------------- #
STEADY_EPSILON = 1e-9


def _hours_between(a, b):
    return (b - a).total_seconds() / 3600.0


def _secant(rows, i, j):
    """Slope between rows i and j in height-units per hour, or None."""
    hi, hj = rows[i]["height"], rows[j]["height"]
    if hi is None or hj is None:
        return None
    dt = _hours_between(rows[i]["time"], rows[j]["time"])
    if dt == 0:
        return None
    return (hj - hi) / dt


def compute_slopes(rows, event_mode=False):
    """
    Return a list (same length as rows) of slopes in height-units per hour.

    event_mode=False (regular spacing): central difference for interior
        points, one-sided difference at the ends. If a neighbour is missing,
        fall back to whichever one-sided difference is available.
    event_mode=True ('hilo' data): the slope *at* a high or low is zero, so
        report the average rate of rise/fall from the previous event to this
        one. The first row has no previous event and is left blank.
    """
    n = len(rows)
    slopes = [None] * n
    if n < 2:
        return slopes

    if event_mode:
        for i in range(1, n):
            slopes[i] = _secant(rows, i - 1, i)
        return slopes

    for i in range(n):
        if 0 < i < n - 1:
            s = _secant(rows, i - 1, i + 1)
            if s is None:                       # a neighbour is missing
                s = _secant(rows, i, i + 1)
                if s is None:
                    s = _secant(rows, i - 1, i)
        elif i == 0:
            s = _secant(rows, 0, 1)
        else:
            s = _secant(rows, n - 2, n - 1)
        slopes[i] = s
    return slopes


def trend_label(slope):
    if slope is None:
        return ""
    if slope > STEADY_EPSILON:
        return "rising"
    if slope < -STEADY_EPSILON:
        return "falling"
    return "steady"


def slope_label(units):
    return "slope_ft_per_hr" if units == "english" else "slope_m_per_hr"


def write_slope_csv(stream, rows, slopes, units):
    writer = csv.writer(stream)
    writer.writerow(["date", "time", height_label(units),
                     slope_label(units), "trend"])
    for row, slope in zip(rows, slopes):
        writer.writerow([
            row["time"].strftime("%Y-%m-%d"),
            row["time"].strftime("%H:%M"),
            "" if row["height"] is None else f"{row['height']:.3f}",
            "" if slope is None else f"{slope:.4f}",
            trend_label(slope),
        ])


def default_slope_name(path, start):
    """<output-stem>_slope.csv; for stdout output, derive from default name."""
    if path == "-":
        stem = default_output_name(start, "csv")
    else:
        stem = path
    stem = re.sub(r"\.[^./\\]*$", "", stem)   # strip extension if any
    return stem + "_slope.csv"


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def height_label(units):
    return "height_ft" if units == "english" else "height_m"


def write_csv(stream, rows, units, include_type):
    writer = csv.writer(stream)
    header = ["date", "time", height_label(units)]
    if include_type:
        header.append("type")
    writer.writerow(header)
    for row in rows:
        line = [row["time"].strftime("%Y-%m-%d"),
                row["time"].strftime("%H:%M"),
                "" if row["height"] is None else f"{row['height']:.3f}"]
        if include_type:
            line.append({"H": "High", "L": "Low"}.get(row["type"], row["type"]))
        writer.writerow(line)


def write_text(stream, rows, units, include_type, meta):
    unit_name = "ft" if units == "english" else "m"
    stream.write(f"NOAA tide {meta['product_label']} for {STATION_NAME} "
                 f"(station {STATION_ID})\n")
    stream.write(f"Start:    {meta['start']:%Y-%m-%d %H:%M} local (HST)\n")
    stream.write(f"End:      {meta['end']:%Y-%m-%d %H:%M} local (HST)\n")
    stream.write(f"Interval: {meta['interval_label']}\n")
    stream.write(f"Datum:    {meta['datum']}    Units: {unit_name}\n")
    stream.write(f"Records:  {len(rows)}\n")
    stream.write(f"Source:   {meta['url']}\n\n")

    header = f"{'Date':<12}{'Time':<8}{'Height (' + unit_name + ')':>12}"
    if include_type:
        header += "  Tide"
    stream.write(header + "\n")
    stream.write("-" * len(header) + "\n")
    for row in rows:
        height = "   n/a" if row["height"] is None else f"{row['height']:12.3f}"
        line = f"{row['time']:%Y-%m-%d}  {row['time']:%H:%M}  {height.rjust(12)}"
        if include_type:
            line += "  " + {"H": "High", "L": "Low"}.get(row["type"], row["type"])
        stream.write(line + "\n")


def default_output_name(start, fmt):
    return f"honolulu_tides_{start:%Y-%m-%d_%H%M}.{fmt}"


def resolve_output(args, start):
    """Decide on output path and format from --output / --format."""
    fmt = args.format
    path = args.output
    if path and path != "-" and fmt is None:
        lower = path.lower()
        if lower.endswith(".csv"):
            fmt = "csv"
        elif lower.endswith((".txt", ".text")):
            fmt = "txt"
    if fmt is None:
        fmt = "csv"
    if path is None:
        path = default_output_name(start, fmt)
    return path, fmt


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    if args.product == "water_level" and args.interval == "hilo":
        raise SystemExit("error: 'hilo' interval is only available for the "
                         "predictions product")

    start = datetime.combine(args.date, args.time)
    end = start + args.duration
    if args.product == "water_level" and start > datetime.now() + timedelta(hours=1):
        raise SystemExit("error: observed water levels are not available for "
                         "future dates; use --product predictions")

    path, fmt = resolve_output(args, start)
    params = build_query(start, end, args.interval, args.product,
                         args.units, args.datum)

    log = (lambda *a, **k: None) if args.quiet else \
          (lambda *a, **k: print(*a, file=sys.stderr, **k))
    log(f"Fetching {args.product} for {STATION_NAME} "
        f"{start:%Y-%m-%d %H:%M} -> {end:%Y-%m-%d %H:%M} local, "
        f"interval {args.interval} ...")

    data, url = fetch_noaa(params)
    rows = thin_rows(extract_rows(data, args.product), args.interval, args.product)
    # The API is inclusive of end_date and may round to its own grid; clip
    # anything outside the requested window.
    rows = [r for r in rows if start <= r["time"] <= end]
    if not rows:
        raise SystemExit("error: NOAA returned no records for that window")

    include_type = args.interval == "hilo"
    interval_label = {"h": "hourly", "hilo": "high/low events"}.get(
        args.interval, f"every {args.interval} minutes")
    meta = {
        "start": start, "end": end, "url": url, "datum": args.datum,
        "interval_label": interval_label,
        "product_label": "predictions" if args.product == "predictions"
                         else "observed water levels",
    }

    if path == "-":
        stream = sys.stdout
        close = False
    else:
        stream = open(path, "w", newline="", encoding="utf-8")
        close = True
    try:
        if fmt == "csv":
            write_csv(stream, rows, args.units, include_type)
        else:
            write_text(stream, rows, args.units, include_type, meta)
    finally:
        if close:
            stream.close()

    if path != "-":
        log(f"Wrote {len(rows)} records to {path}")

    if not args.no_slope:
        slope_path = args.slope_output or default_slope_name(path, start)
        slopes = compute_slopes(rows, event_mode=include_type)
        with open(slope_path, "w", newline="", encoding="utf-8") as stream:
            write_slope_csv(stream, rows, slopes, args.units)
        note = (" (average rate between consecutive high/low events)"
                if include_type else "")
        log(f"Wrote slopes for {len(rows)} records to {slope_path}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
