#!/usr/bin/env python3
"""
SC Precinct Map  –  Data Build Pipeline
=======================================
Converts TIGER shapefiles to GeoJSON and aggregates OpenElections
CSV files into contest-slice JSON files expected by index.html.

Run from the project root:
    python build_data.py

Outputs (all under ./data/):
    data/census/tl_2020_45_county20.geojson
    data/Voting_Precincts.geojson
    data/precinct_centroids.geojson
    data/contests/manifest.json
    data/contests/<contest_type>_<year>.json   (one per contest)

Each contest JSON contains BOTH:
  • County-level aggregate rows  (county = "Richland")
  • Precinct-level rows          (county = "Richland - Forest Acres 1")

The JS applyCountyContest() uses the " - " separator to distinguish the
two types: county rows colour the county fill layer, precinct rows colour
the precinct overlay.  NHGIS VTD NAME20 fields match OE precinct names
at ~99%, so almost every precinct gets individual coloring.
"""

import csv
import io
import json
import os
import re
import traceback
import zipfile

try:
    import shapefile  # pip install pyshp
except ImportError:
    raise SystemExit("Missing dependency: run  pip install pyshp")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_SRC  = os.path.join(BASE_DIR, 'Data')          # canonical capitalised source dir
DATA_OUT  = os.path.join(BASE_DIR, 'data')          # lowercase output dir expected by JS

SHP_COUNTY   = os.path.join(DATA_SRC, 'census', 'tl_2020_45_county20',
                             'tl_2020_45_county20.shp')
SHP_VTD      = os.path.join(DATA_SRC, 'census', 'tl_2020_45_vtd20',
                             'tl_2020_45_vtd20.shp')

# District shapefiles (inside zips)
DISTRICT_ZIPS = [
    # (zip_path, base_name, scope, district_number_field, label)
    (os.path.join(DATA_SRC, 'census', 'tl_2022_45_cd118.zip'),
     'tl_2022_45_cd118', 'congressional', 'CD118FP',
     'Congressional District', 'sc_cd118_tileset.geojson'),
    (os.path.join(DATA_SRC, 'census', 'tl_2024_45_sldl.zip'),
     'tl_2024_45_sldl',  'state_house',   'SLDLST',
     'State House District', 'sc_state_house_2022_lines_tileset.geojson'),
    (os.path.join(DATA_SRC, 'census', 'tl_2024_45_sldu.zip'),
     'tl_2024_45_sldu',  'state_senate',  'SLDUST',
     'State Senate District', 'sc_state_senate_2022_lines_tileset.geojson'),
]

# District-scope offices: which OE office string → (scope, contest_type)
DISTRICT_OFFICE_MAP = {
    'u.s. house':  ('congressional', 'us_house'),
    'us house':    ('congressional', 'us_house'),
    'state house': ('state_house',   'state_house'),
    'state house of representatives': ('state_house', 'state_house'),
    'state senate': ('state_senate', 'state_senate'),
}

ELECTION_FILES = {
    2008: os.path.join(DATA_SRC, 'openelections-data-sc', '2008',
                       '20081104__sc__general__precinct.csv'),
    2016: os.path.join(DATA_SRC, 'openelections-data-sc', '2016',
                       '20161108__sc__general__precinct.csv'),
    2018: os.path.join(DATA_SRC, 'openelections-data-sc', '2018',
                       '20181106__sc__general__precinct.csv'),
    2020: os.path.join(DATA_SRC, 'openelections-data-sc', '2020',
                       '20201103__sc__general__precinct.csv'),
    2022: os.path.join(DATA_SRC, 'openelections-data-sc', '2022',
                       '20221108__sc__general__precinct.csv'),
    2024: os.path.join(DATA_SRC, 'openelections-data-sc', '2024',
                       '20241105__sc__general__precinct.csv'),
}

# Local override: if you generated an OpenElections-style precinct file from ELSTATS,
# prefer it for aggregation.
_ELSTATS_2024_OE = os.path.join(DATA_SRC, '20241105__sc__general__precinct__from_elstats.csv')
if os.path.exists(_ELSTATS_2024_OE):
    ELECTION_FILES[2024] = _ELSTATS_2024_OE

# Offices to include (lower-cased raw value → contest_type key)
OFFICE_MAP = {
    'president':                            'president',
    'u.s. senate':                          'us_senate',
    'us senate':                            'us_senate',
    'u.s. house':                           'us_house',
    'us house':                             'us_house',
    'governor and lieutenant governor':     'governor',
    'governor':                             'governor',
    'state house':                          'state_house',
    'state house of representatives':       'state_house',
    'state senate':                         'state_senate',
    'attorney general':                     'attorney_general',
    'secretary of state':                   'secretary_of_state',
    'state treasurer':                      'state_treasurer',
    'comptroller general':                  'comptroller_general',
    'commissioner of agriculture':          'commissioner_agriculture',
}

# Contest types to *skip* – district races are noisy at the county level.
# Remove from this set if you want them included.
SKIP_DISTRICT_OFFICES = {'us_house', 'state_house', 'state_senate'}

# Coloring thresholds – positive margin_pct means Republican wins.
_COLORS = [
    (40, 'R', '#67000d'), (30, 'R', '#a50f15'), (20, 'R', '#cb181d'),
    (10, 'R', '#ef3b2c'), ( 5, 'R', '#fc8a6a'), ( 0, 'R', '#fcbba1'),
    ( 0, 'T', '#f0f0f0'),
    ( 0, 'D', '#c6dbef'), ( 5, 'D', '#9ecae1'), (10, 'D', '#6baed6'),
    (20, 'D', '#4292c6'), (30, 'D', '#2171b5'), (40, 'D', '#08519c'),
    (999,'D', '#08306b'),
]

# Priority order for manifest display (lower = shown first in dropdown)
_PRIORITY = {
    'president': 0, 'governor': 1, 'us_senate': 2, 'attorney_general': 3,
    'secretary_of_state': 4, 'state_treasurer': 5, 'comptroller_general': 6,
    'commissioner_agriculture': 7, 'us_house': 8, 'state_senate': 9,
    'state_house': 10,
}

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def normalize(s: str) -> str:
    """Mirror JS normalizeCountyName: keep a-z 0-9 space period hyphen, upper."""
    s = re.sub(r'[^a-zA-Z0-9 .\-]', '', str(s))
    return re.sub(r'\s+', ' ', s).strip().upper()


def margin_color(signed_pct: float) -> str:
    if abs(signed_pct) < 0.001:
        return '#f0f0f0'
    party = 'R' if signed_pct > 0 else 'D'
    absp  = abs(signed_pct)
    # Walk from highest threshold down to 0
    best = '#f0f0f0'
    for thresh, p, color in sorted(_COLORS, reverse=True, key=lambda x: x[0]):
        if p == party and absp >= thresh:
            best = color
            break
    return best


def write_json(obj, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, separators=(',', ':'))
    rel = os.path.relpath(path, BASE_DIR)
    print(f'  wrote  {rel}')


def shp_to_geojson_features(shp_path: str, augment_fn=None) -> list:
    sf     = shapefile.Reader(shp_path)
    fields = [f[0] for f in sf.fields[1:]]
    feats  = []
    for sr in sf.iterShapeRecords():
        props = {k: v for k, v in zip(fields, sr.record)}
        if augment_fn:
            props.update(augment_fn(props))
        feats.append({
            'type': 'Feature',
            'properties': props,
            'geometry': sr.shape.__geo_interface__,
        })
    return feats


def shp_from_zip(zip_path: str, base_name: str, augment_fn=None) -> list:
    """Read shapefile from inside a zip archive, return GeoJSON feature list."""
    with zipfile.ZipFile(zip_path) as z:
        sf = shapefile.Reader(
            shp=io.BytesIO(z.read(f'{base_name}.shp')),
            dbf=io.BytesIO(z.read(f'{base_name}.dbf')),
            shx=io.BytesIO(z.read(f'{base_name}.shx')),
        )
        fields = [f[0] for f in sf.fields[1:]]
        feats  = []
        for sr in sf.iterShapeRecords():
            props = {k: v for k, v in zip(fields, sr.record)}
            if augment_fn:
                props.update(augment_fn(props))
            feats.append({
                'type': 'Feature',
                'properties': props,
                'geometry': sr.shape.__geo_interface__,
            })
    return feats

# ---------------------------------------------------------------------------
# Step 1 – County GeoJSON
# ---------------------------------------------------------------------------




def build_county_geojson():
    print('\n=== County GeoJSON ===')
    feats = shp_to_geojson_features(SHP_COUNTY)
    gj    = {'type': 'FeatureCollection', 'features': feats}
    write_json(gj, os.path.join(DATA_OUT, 'census', 'tl_2020_45_county20.geojson'))
    print(f'  {len(feats)} county features')
    return {f['properties']['COUNTYFP20']: f['properties']['NAME20']
            for f in feats}     # COUNTYFP20 → name


# ---------------------------------------------------------------------------
# Step 2 – Precinct (VTD) GeoJSON  +  centroids
# ---------------------------------------------------------------------------

def build_precinct_geojson(county_fp_map: dict):
    print('\n=== Precinct GeoJSON ===')

    def augment(props):
        fips   = str(props.get('COUNTYFP20', '')).zfill(3)
        cname  = county_fp_map.get(fips, fips)
        prec   = str(props.get('NAME20', props.get('NAMELSAD20', ''))).strip()
        norm   = normalize(f'{cname} - {prec}')
        return {
            'county_nam':    cname,
            'prec_id':       prec,
            'precinct_norm': norm,
            'county_norm':   normalize(cname),
        }

    feats = shp_to_geojson_features(SHP_VTD, augment_fn=augment)
    gj    = {'type': 'FeatureCollection', 'features': feats}
    write_json(gj, os.path.join(DATA_OUT, 'Voting_Precincts.geojson'))
    print(f'  {len(feats)} precinct features')

    # -- Centroids -------------------------------------------------------
    print('\n=== Precinct Centroids ===')
    centroids = []
    for f in feats:
        p   = f['properties']
        lat = float(p.get('INTPTLAT20', 0))
        lon = float(p.get('INTPTLON20', 0))
        if lat == 0 and lon == 0:
            continue
        centroids.append({
            'type': 'Feature',
            'properties': {
                'county_nam':    p['county_nam'],
                'prec_id':       p['prec_id'],
                'precinct_norm': p['precinct_norm'],
                'county_norm':   p['county_norm'],
            },
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
        })
    write_json({'type': 'FeatureCollection', 'features': centroids},
               os.path.join(DATA_OUT, 'precinct_centroids.geojson'))
    print(f'  {len(centroids)} centroid points')


# Non-geographic precinct buckets – never map to geometry
_NON_GEO = re.compile(
    r'^(FAILSAFE|FAILSAFE PROVISIONAL|PROVISIONAL|ABSENTEE|CURBSIDE|'
    r'ONE STOP|MAIL ABSENTEE|VOTE CENTER|VOTECENTER|EARLY VOT|EV|OS |OS-|OS_)',
    re.IGNORECASE,
)

def is_non_geo(precinct: str) -> bool:
    p = precinct.strip().upper()
    if not p:
        return True
    if _NON_GEO.match(p):
        return True
    return False


_NO_LEADING_ZEROS = re.compile(r'\bNO\.?\s*0+(\d+)\b', re.IGNORECASE)
_NUM_SLASH_NUM = re.compile(r'\b0*([0-9]{1,3})\s*/\s*([0-9]{1,2})\b')
_NUM_SLASH_ALPHA = re.compile(r'\b0*([0-9]{1,3})\s*/\s*([A-Z]{1,2})\b', re.IGNORECASE)
_LEADING_ZERO_NUM_ALPHA = re.compile(r'\b0+([0-9]+)([A-Z]{1,2})\b', re.IGNORECASE)
_LEADING_ZERO_NUM = re.compile(r'\b0+([0-9]+)\b')

def normalize_precinct_label(name: str) -> str:
    """
    Normalize precinct label strings so they better match TIGER VTD naming.

    Key fix: convert "No. 01" -> "No. 1" (leading zeros break precinct_norm matching).
    """
    s = (name or '').strip()
    if not s:
        return ''
    s = _NO_LEADING_ZEROS.sub(lambda m: f'No. {int(m.group(1))}', s)
    s = _NUM_SLASH_ALPHA.sub(lambda m: f'{int(m.group(1))}{m.group(2).upper()}', s)
    s = _NUM_SLASH_NUM.sub(lambda m: f'{int(m.group(1))}{m.group(2)}', s)
    s = _LEADING_ZERO_NUM_ALPHA.sub(lambda m: f'{int(m.group(1))}{m.group(2).upper()}', s)
    s = _LEADING_ZERO_NUM.sub(lambda m: f'{int(m.group(1))}', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s.title()


# ---------------------------------------------------------------------------
# Step 3 – District boundary GeoJSON
# ---------------------------------------------------------------------------

def build_district_geojson():
    print('\n=== District GeoJSON ===')
    tileset_dir = os.path.join(DATA_OUT, 'tileset')
    os.makedirs(tileset_dir, exist_ok=True)
    for zip_path, base_name, scope, num_field, label, out_name in DISTRICT_ZIPS:
        if not os.path.exists(zip_path):
            print(f'  SKIP {out_name}: zip not found')
            continue
        feats = shp_from_zip(zip_path, base_name)
        gj = {'type': 'FeatureCollection', 'features': feats}
        write_json(gj, os.path.join(tileset_dir, out_name))
        print(f'  {len(feats)} features  ({label})')


# ---------------------------------------------------------------------------
# Step 4 – Contest slice JSONs  +  manifest
# ---------------------------------------------------------------------------

def make_row(key: str, v: dict, year: int) -> dict:
    """Build a single result row from accumulated vote totals."""
    total  = v['dem'] + v['rep'] + v['other']
    margin = v['rep'] - v['dem']
    mpct   = round(margin / total * 100, 4) if total else 0
    winner = 'R' if margin > 0 else ('D' if margin < 0 else 'T')
    return {
        'county':         key,
        'dem_votes':      v['dem'],
        'rep_votes':      v['rep'],
        'other_votes':    v['other'],
        'total_votes':    total,
        'dem_candidate':  v['dem_cand'],
        'rep_candidate':  v['rep_cand'],
        'margin':         margin,
        'margin_pct':     mpct,
        'winner':         winner,
        'color':          margin_color(mpct),
    }


def load_precinct_norm_set() -> set[str] | None:
    path = os.path.join(DATA_OUT, 'Voting_Precincts.geojson')
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as fh:
            gj = json.load(fh)
        out = set()
        for f in gj.get('features', []):
            p = (f or {}).get('properties') or {}
            n = (p.get('precinct_norm') or '').strip().upper()
            if n:
                out.add(n)
        return out
    except Exception:
        return None


_DIR_PREFIX = re.compile(r'^(N|S|E|W)\s+', re.IGNORECASE)
_MT_PREFIX = re.compile(r'^MT\s+', re.IGNORECASE)
_ST_PREFIX = re.compile(r'^ST\s+', re.IGNORECASE)
_TRAILING_NUM = re.compile(r'^(.*\D)\s+(\d+[A-Z]{0,2})$', re.IGNORECASE)

def precinct_label_variants(label: str) -> list[str]:
    s = (label or '').strip()
    if not s:
        return []
    out = []
    seen = set()

    def _add(v: str) -> None:
        v = (v or '').strip()
        if not v:
            return
        key = v.upper()
        if key in seen:
            return
        seen.add(key)
        out.append(v)

    _add(s)

    # Expand leading direction abbreviations (e.g., "E Bennettsville" -> "East Bennettsville").
    m = _DIR_PREFIX.match(s)
    if m:
        d = m.group(1).upper()
        rest = s[m.end():].strip()
        full = {'N': 'North', 'S': 'South', 'E': 'East', 'W': 'West'}.get(d)
        if full and rest:
            _add(f'{full} {rest}')

    # Add periods for common abbreviations (helps match TIGER labels like "MT. AIRY").
    if _MT_PREFIX.match(s):
        _add(_MT_PREFIX.sub('Mt. ', s, count=1))
    if _ST_PREFIX.match(s):
        _add(_ST_PREFIX.sub('St. ', s, count=1))

    # If the label ends with a bare number/num+suffix, also try inserting "No." before it.
    if 'NO.' not in s.upper():
        m = _TRAILING_NUM.match(s)
        if m:
            _add(f'{m.group(1).strip()} No. {m.group(2).upper()}')

    # Xroads spacing.
    _add(re.sub(r'\bXROADS\b', 'X ROADS', s, flags=re.IGNORECASE))

    return out


def aggregate_all(rows: list, precinct_norm_set: set[str] | None = None) -> tuple[dict, dict]:
    """
    Build both county-level and precinct-level aggregates.

    Returns:
        county_agg : {county_title: {dem,rep,other,...}}
        precinct_agg: {"County - Precinct Name": {dem,rep,other,...}}
    """
    county_agg   = {}
    precinct_agg = {}

    for row in rows:
        county_raw = (row.get('county') or '').strip()
        prec_raw   = (row.get('precinct') or '').strip()
        if not county_raw:
            continue
        ct = county_raw.title()          # "Richland"
        party = (row.get('party') or '').strip().upper()
        votes = int(row.get('votes') or 0)
        cand  = (row.get('candidate') or '').strip()

        def _add(agg: dict, key: str) -> None:
            if key not in agg:
                agg[key] = {'dem': 0, 'rep': 0, 'other': 0,
                            'dem_cand': '', 'rep_cand': ''}
            node = agg[key]
            if party == 'DEM':
                node['dem'] += votes
                if not node['dem_cand']:
                    node['dem_cand'] = cand
            elif party == 'REP':
                node['rep'] += votes
                if not node['rep_cand']:
                    node['rep_cand'] = cand
            else:
                node['other'] += votes

        # County aggregate (always)
        _add(county_agg, ct)

        # Precinct row – only for geographic precincts
        if prec_raw and not is_non_geo(prec_raw):
            prec_label = normalize_precinct_label(prec_raw)
            prec_key = f'{ct} - {prec_label}'
            if precinct_norm_set:
                chosen = None
                for v in precinct_label_variants(prec_label):
                    cand_key = f'{ct} - {v}'
                    if normalize(cand_key) in precinct_norm_set:
                        chosen = cand_key
                        break
                if chosen:
                    prec_key = chosen
            _add(precinct_agg, prec_key)

    return county_agg, precinct_agg


def build_election_data():
    print('\n=== County/Precinct Contest JSONs ===')
    contests_dir = os.path.join(DATA_OUT, 'contests')
    os.makedirs(contests_dir, exist_ok=True)
    manifest_entries = []
    precinct_norm_set = load_precinct_norm_set()

    for year, csv_path in sorted(ELECTION_FILES.items()):
        if not os.path.exists(csv_path):
            print(f'  SKIP {year}: file not found at {csv_path}')
            continue
        print(f'\n  -- {year} --')

        with open(csv_path, encoding='utf-8', newline='') as fh:
            raw_rows = list(csv.DictReader(fh))

        # Bucket rows by contest_type
        by_contest: dict[str, list] = {}
        for row in raw_rows:
            office_raw = (row.get('office') or '').strip().lower()
            ct = OFFICE_MAP.get(office_raw)
            if not ct:
                continue
            if ct in SKIP_DISTRICT_OFFICES:
                continue
            by_contest.setdefault(ct, []).append(row)

        for ct, ct_rows in by_contest.items():
            county_agg, precinct_agg = aggregate_all(ct_rows, precinct_norm_set)
            if not county_agg:
                continue

            # Build sorted result rows:  county rows first, then precinct rows
            county_rows   = [make_row(k, v, year) for k, v in sorted(county_agg.items())]
            precinct_rows = [make_row(k, v, year) for k, v in sorted(precinct_agg.items())]
            all_rows = county_rows + precinct_rows

            fname = f'{ct}_{year}.json'
            payload = {
                'year':         year,
                'contest_type': ct,
                'rows':         all_rows,
            }
            write_json(payload, os.path.join(contests_dir, fname))
            print(f'    {ct}: {len(county_rows)} counties + {len(precinct_rows)} precincts')
            manifest_entries.append({
                'year':         year,
                'contest_type': ct,
                'file':         fname,
                'rows':         len(all_rows),
            })

    # Sort: newest year first, then by priority within a year
    manifest_entries.sort(key=lambda e: (-e['year'], _PRIORITY.get(e['contest_type'], 99)))
    write_json({'files': manifest_entries},
               os.path.join(contests_dir, 'manifest.json'))
    print(f'\n  manifest: {len(manifest_entries)} contest(s)')


# ---------------------------------------------------------------------------
# Step 5 – District contest slice JSONs  +  manifest
# ---------------------------------------------------------------------------

def build_district_contests():
    """
    Aggregate OE CSV rows by district number for each scope and output
    district_contests/{scope}_{contest_type}_{year}.json  +  manifest.

    Payload structure (matches JS pickDistrictSliceRow / renderDistrictHover):
      {
        "general": {
          "results": {
            "1": { dem_votes, rep_votes, other_votes, total_votes,
                   dem_candidate, rep_candidate, margin, margin_pct,
                   winner, color },
            "2": { ... },
            ...
          }
        },
        "meta": { "match_coverage_pct": <float> }
      }
    """
    print('\n=== District Contest JSONs ===')
    dist_dir = os.path.join(DATA_OUT, 'district_contests')
    os.makedirs(dist_dir, exist_ok=True)
    manifest_entries = []

    for year, csv_path in sorted(ELECTION_FILES.items()):
        if not os.path.exists(csv_path):
            continue
        with open(csv_path, encoding='utf-8', newline='') as fh:
            raw_rows = list(csv.DictReader(fh))

        # Bucket by (scope, contest_type)
        by_scope: dict[tuple, list] = {}
        for row in raw_rows:
            office_raw = (row.get('office') or '').strip().lower()
            mapping = DISTRICT_OFFICE_MAP.get(office_raw)
            if not mapping:
                continue
            by_scope.setdefault(mapping, []).append(row)

        for (scope, ct), d_rows in by_scope.items():
            # Aggregate by district number
            agg: dict[str, dict] = {}
            for row in d_rows:
                dist_raw = (row.get('district') or '').strip()
                if not dist_raw:
                    continue
                try:
                    dist_key = str(int(dist_raw))   # strip leading zeros
                except ValueError:
                    continue
                party = (row.get('party') or '').strip().upper()
                votes = int(row.get('votes') or 0)
                cand  = (row.get('candidate') or '').strip()
                if dist_key not in agg:
                    agg[dist_key] = {'dem': 0, 'rep': 0, 'other': 0,
                                     'dem_cand': '', 'rep_cand': ''}
                node = agg[dist_key]
                if party == 'DEM':
                    node['dem'] += votes
                    if not node['dem_cand']:
                        node['dem_cand'] = cand
                elif party == 'REP':
                    node['rep'] += votes
                    if not node['rep_cand']:
                        node['rep_cand'] = cand
                else:
                    node['other'] += votes

            if not agg:
                continue

            results = {}
            for dist_key, v in agg.items():
                total  = v['dem'] + v['rep'] + v['other']
                margin = v['rep'] - v['dem']
                mpct   = round(margin / total * 100, 4) if total else 0
                winner = 'R' if margin > 0 else ('D' if margin < 0 else 'T')
                results[dist_key] = {
                    'dem_votes':      v['dem'],
                    'rep_votes':      v['rep'],
                    'other_votes':    v['other'],
                    'total_votes':    total,
                    'dem_candidate':  v['dem_cand'],
                    'rep_candidate':  v['rep_cand'],
                    'margin':         margin,
                    'margin_pct':     mpct,
                    'winner':         winner,
                    'color':          margin_color(mpct),
                }

            fname = f'{scope}_{ct}_{year}.json'
            payload = {
                'general': {'results': results},
                'meta':    {'match_coverage_pct': 100},
            }
            write_json(payload, os.path.join(dist_dir, fname))
            print(f'    {scope}/{ct} {year}: {len(results)} district(s)')
            manifest_entries.append({
                'year':         year,
                'contest_type': ct,
                'scope':        scope,
                'file':         fname,
                'rows':         len(results),
            })

    manifest_entries.sort(key=lambda e: (-e['year'], _PRIORITY.get(e['contest_type'], 99)))
    write_json({'files': manifest_entries},
               os.path.join(dist_dir, 'manifest.json'))
    print(f'\n  manifest: {len(manifest_entries)} district contest(s)')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    try:
        county_fp_map = build_county_geojson()
        build_precinct_geojson(county_fp_map)
        build_district_geojson()
        build_election_data()
        build_district_contests()
        print('\nBuild complete.')
    except Exception as exc:
        print(f'\nBuild failed: {exc}')
        traceback.print_exc()
