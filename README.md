# tides

Command-line tool that fetches NOAA tide data for Honolulu, HI (CO-OPS station
1612340) into a CSV or text file, and writes a companion `*_slope.csv` with the
rate of rise/fall of the water level at each timepoint.

Requires Python 3 only (standard library, no dependencies).

## Usage

```
python3 honolulu_tides.py DATE [TIME] [-d DURATION] [-i INTERVAL] [-o FILE] [-f csv|txt]
```

| Argument | Meaning | Default |
|---|---|---|
| `DATE` | start date, `YYYY-MM-DD` (Honolulu local) | required |
| `TIME` | start time, `HH:MM` 24-hour local | `00:00` |
| `-d, --duration` | how long to fetch: hours, or with `m`/`h`/`d` suffix (`36h`, `7d`, `90m`) | `24` |
| `-i, --interval` | sample spacing: `1,5,6,10,15,30,60` minutes, `h` (hourly), or `hilo` (high/low events only) | `60` |
| `-p, --product` | `predictions` (tide tables) or `water_level` (observed, past dates only) | `predictions` |
| `-u, --units` | `english` (feet) or `metric` (metres) | `english` |
| `--datum` | vertical datum (`MLLW`, `MSL`, `MHHW`, ...) | `MLLW` |
| `-f, --format` | `csv` or `txt`; inferred from `-o` extension if omitted | `csv` |
| `-o, --output` | output path, `-` for stdout | `honolulu_tides_<date>_<time>.<ext>` |
| `--slope-output` | path for the slope CSV | `<output-stem>_slope.csv` |
| `--no-slope` | skip the slope file | |
| `-q, --quiet` | suppress progress messages | |

### Examples

```sh
# 24 hours of hourly predictions from midnight
python3 honolulu_tides.py 2026-10-04

# 12 hours from 06:30, every 15 minutes, as a text report
python3 honolulu_tides.py 2026-10-04 06:30 -d 12 -i 15 -f txt

# Just the high/low events for the next 3 days
python3 honolulu_tides.py 2026-10-04 -d 3d -i hilo -o highs_lows.csv

# Observed water levels in metres
python3 honolulu_tides.py 2026-09-01 -p water_level -u metric
```

## Output

Main CSV:

```
date,time,height_ft
2026-10-04,00:00,0.660
2026-10-04,01:00,0.593
```

Slope CSV (`*_slope.csv`), height units per hour plus a trend label:

```
date,time,height_ft,slope_ft_per_hr,trend
2026-10-04,00:00,0.660,-0.0670,falling
2026-10-04,01:00,0.593,-0.0790,falling
```

For evenly spaced data the slope is a central difference (one-sided at the
first and last rows). For `hilo` output, where the slope at a high or low is
zero by definition, the file reports the average rate of rise/fall between the
previous event and the current one.

## Data source

[NOAA CO-OPS Data API](https://api.tidesandcurrents.noaa.gov/api/prod/),
station 1612340, times in station local time (HST, UTC-10).
