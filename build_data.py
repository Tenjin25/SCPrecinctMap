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
    shapefile = None

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_SRC  = os.path.join(BASE_DIR, 'Data')          # canonical capitalised source dir
DATA_OUT  = os.path.join(BASE_DIR, 'data')          # lowercase output dir expected by JS
PRECINCT_ALIASES_PATH = os.path.join(BASE_DIR, 'precinct_aliases.json')
BLOCK_ASSIGN_ZIP_PATH = os.path.join(DATA_SRC, 'BlockAssign_ST45_SC.zip')
if not os.path.exists(BLOCK_ASSIGN_ZIP_PATH):
    _fallback_block_assign = os.path.join(os.path.dirname(BASE_DIR), 'Data', 'BlockAssign_ST45_SC.zip')
    if os.path.exists(_fallback_block_assign):
        BLOCK_ASSIGN_ZIP_PATH = _fallback_block_assign

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

# Optional: ELSTATS "search export" conversions (county + precinct where inferable).
_ELSTATS_SEARCH_2010_OE = os.path.join(DATA_SRC, '_tmpdata', '20101102__sc__general__precinct__from_elstats_search.csv')
if os.path.exists(_ELSTATS_SEARCH_2010_OE):
    ELECTION_FILES[2010] = _ELSTATS_SEARCH_2010_OE

_ELSTATS_SEARCH_2012_OE = os.path.join(DATA_SRC, '_tmpdata', '20121106__sc__general__precinct__from_elstats_search.csv')
if os.path.exists(_ELSTATS_SEARCH_2012_OE):
    ELECTION_FILES[2012] = _ELSTATS_SEARCH_2012_OE

_ELSTATS_SEARCH_2008_OE = os.path.join(DATA_SRC, '_tmpdata', '20081104__sc__general__precinct__from_elstats_search.csv')
if os.path.exists(_ELSTATS_SEARCH_2008_OE):
    ELECTION_FILES[2008] = _ELSTATS_SEARCH_2008_OE

_LOCAL_2006_OE = os.path.join(DATA_SRC, '_tmpdata', '20061107__sc__general__precinct__local.csv')
if os.path.exists(_LOCAL_2006_OE):
    ELECTION_FILES[2006] = _LOCAL_2006_OE

_ELSTATS_SEARCH_2014_OE = os.path.join(DATA_SRC, '_tmpdata', '20141104__sc__general__precinct__from_elstats_search.csv')
if os.path.exists(_ELSTATS_SEARCH_2014_OE):
    ELECTION_FILES[2014] = _ELSTATS_SEARCH_2014_OE

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


def normalize_party(party: str) -> str:
    """
    Normalize party strings across sources into the short codes used by the app.
    Examples: "DEMOCRAT" -> "DEM", "REPUBLICAN" -> "REP".
    """
    p = (party or '').strip().upper()
    if not p:
        return ''
    if p in {'DEM', 'DEMOCRAT', 'DEMOCRATIC', 'D'}:
        return 'DEM'
    if p in {'REP', 'REPUBLICAN', 'R', 'GOP'}:
        return 'REP'
    if p in {'LIB', 'LIBERTARIAN'}:
        return 'LIB'
    if p in {'GRN', 'GREEN'}:
        return 'GRN'
    if p in {'CON', 'CONSTITUTION'}:
        return 'CON'
    if p in {'WFP', 'WORKING FAMILIES', 'WORKINGFAMILIES'}:
        return 'WFP'
    return p


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
    if shapefile is None:
        raise RuntimeError("Missing dependency: run pip install pyshp")
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
    r'ONE STOP|MAIL ABSENTEE|VOTE CENTER|VOTECENTER|EARLY VOT|'
    r'EV$|EV[-_ ]|EV[0-9]|OS[-_ ]|OS[0-9]|OS[A-Z]{1,8}[-_ ]?[0-9])',
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
_APOS = re.compile(r"[’'`]")

def normalize_precinct_label(name: str) -> str:
    """
    Normalize precinct label strings so they better match TIGER VTD naming.

    Key fix: convert "No. 01" -> "No. 1" (leading zeros break precinct_norm matching).
    """
    s = (name or '').strip()
    if not s:
        return ''
    # Make punctuation consistent across sources (VTD vs ELSTATS vs OE).
    # Apostrophes often appear in results feeds but not in boundary files.
    s = _APOS.sub('', s)
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


def load_precinct_polygon_index() -> tuple[set[str], dict[str, str]] | tuple[None, None]:
    """
    Returns:
        precinct_norm_set: Set of normalized polygon precinct keys (upper).
        precinct_display_by_norm: Map precinct_norm -> "County - Precinct" display key.
    """
    path = os.path.join(DATA_OUT, 'Voting_Precincts.geojson')
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding='utf-8') as fh:
            gj = json.load(fh)
        norm_set: set[str] = set()
        display_by_norm: dict[str, str] = {}
        for f in gj.get('features', []):
            p = (f or {}).get('properties') or {}
            n = (p.get('precinct_norm') or '').strip().upper()
            if not n:
                continue
            norm_set.add(n)
            county = str(p.get('county_nam') or '').strip()
            prec = str(p.get('prec_id') or '').strip()
            display = f'{county} - {prec}'.strip()
            if display:
                display_by_norm[n] = display
        return norm_set, display_by_norm
    except Exception:
        return None, None


def load_precinct_aliases(precinct_display_by_norm: dict[str, str] | None = None) -> dict[str, str]:
    """
    Optional manual overrides to map result keys to polygon keys.

    File format: JSON object { "COUNTY - PRECINCT_FROM": "COUNTY - PRECINCT_TO" }.
    Keys and values are normalized via normalize().
    """
    path = PRECINCT_ALIASES_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        precinct_display_by_norm = precinct_display_by_norm or {}
        for k, v in raw.items():
            if not isinstance(k, str) or not isinstance(v, str):
                continue
            if k.startswith('_'):
                continue
            nk = normalize(k)
            nv = normalize(v)
            if not (nk and nv):
                continue
            # Store the canonical display key if it exists in polygons; otherwise keep the raw target.
            out[nk] = precinct_display_by_norm.get(nv, v.strip())
        return out
    except Exception:
        return {}


_DIR_PREFIX = re.compile(r'^(N|S|E|W)\s+', re.IGNORECASE)
_MT_PREFIX = re.compile(r'^MT\s+', re.IGNORECASE)
_ST_PREFIX = re.compile(r'^ST\s+', re.IGNORECASE)
_TRAILING_NUM = re.compile(r'^(.*\D)\s+(\d+[A-Z]{0,2})$', re.IGNORECASE)

_ONES = {
    0: 'Zero', 1: 'One', 2: 'Two', 3: 'Three', 4: 'Four', 5: 'Five',
    6: 'Six', 7: 'Seven', 8: 'Eight', 9: 'Nine', 10: 'Ten', 11: 'Eleven',
    12: 'Twelve', 13: 'Thirteen', 14: 'Fourteen', 15: 'Fifteen',
    16: 'Sixteen', 17: 'Seventeen', 18: 'Eighteen', 19: 'Nineteen',
}
_TENS = {
    20: 'Twenty', 30: 'Thirty', 40: 'Forty', 50: 'Fifty',
    60: 'Sixty', 70: 'Seventy', 80: 'Eighty', 90: 'Ninety',
}
_WORD_TO_NUM = {v.lower(): k for k, v in {**_ONES, **_TENS}.items()}

def _num_to_words(n: int) -> str | None:
    if n < 0 or n > 99:
        return None
    if n in _ONES:
        return _ONES[n]
    tens = (n // 10) * 10
    ones = n % 10
    if ones == 0:
        return _TENS.get(tens)
    t = _TENS.get(tens)
    o = _ONES.get(ones)
    if not t or not o:
        return None
    return f'{t}-{o}'

def _words_to_num(token: str) -> int | None:
    if not token:
        return None
    parts = re.split(r'[-\s]+', token.strip().lower())
    parts = [p for p in parts if p]
    if not parts:
        return None
    if len(parts) == 1:
        return _WORD_TO_NUM.get(parts[0])
    if len(parts) == 2:
        a = _WORD_TO_NUM.get(parts[0])
        b = _WORD_TO_NUM.get(parts[1])
        if a in _TENS and b in _ONES and b < 10:
            return int(a + b)
    return None

def _digits_to_words_variant(s: str) -> str:
    out = []
    for tok in (s or '').split():
        if tok.isdigit():
            w = _num_to_words(int(tok))
            out.append(w or tok)
        else:
            out.append(tok)
    return ' '.join(out)

def _words_to_digits_variant(s: str) -> str:
    out = []
    for tok in (s or '').split():
        num = _words_to_num(tok)
        out.append(str(num) if num is not None else tok)
    return ' '.join(out)

def _singularize_variant(s: str) -> str:
    toks = []
    for tok in (s or '').split():
        if len(tok) > 3 and tok.endswith('s') and not tok.endswith('ss'):
            toks.append(tok[:-1])
        else:
            toks.append(tok)
    return ' '.join(toks)

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

    # Hyphen/space normalization for common "Fifty-Two" / "Fifty Two" differences.
    _add(s.replace('-', ' '))

    # Expand common street abbreviations.
    _add(re.sub(r'\bRd\.?\b', 'Road', s, flags=re.IGNORECASE))
    _add(re.sub(r'\bHwy\.?\b', 'Highway', s, flags=re.IGNORECASE))

    # Singular/plural variants ("Springs" vs "Spring", "Wrights" vs "Wright").
    _add(_singularize_variant(s))

    # French-y suffix normalization ("Pointe" vs "Point").
    _add(re.sub(r'\bPointe\b', 'Point', s, flags=re.IGNORECASE))

    # Convert small numbers between digits and words ("52" <-> "Fifty-Two").
    _add(_digits_to_words_variant(s))
    _add(_words_to_digits_variant(s))

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


def aggregate_all(
    rows: list,
    precinct_norm_set: set[str] | None = None,
    precinct_aliases: dict[str, str] | None = None,
) -> tuple[dict, dict]:
    """
    Build both county-level and precinct-level aggregates.

    Returns:
        county_agg : {county_title: {dem,rep,other,...}}
        precinct_agg: {"County - Precinct Name": {dem,rep,other,...}}
    """
    county_agg   = {}
    precinct_agg = {}
    precinct_aliases = precinct_aliases or {}

    # If the input includes explicit county-level rows (precinct blank), use those for county totals
    # to avoid double counting when precinct-level rows are also present.
    #
    # Do this per-county (not globally): some sources can be mixed (county rows for some counties
    # but only precinct rows for others).
    counties_with_explicit_rows = set()
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        county_raw = (r.get('county') or '').strip()
        if not county_raw:
            continue
        prec_raw = (r.get('precinct') or '').strip()
        if not prec_raw:
            counties_with_explicit_rows.add(normalize(county_raw))

    for row in rows:
        county_raw = (row.get('county') or '').strip()
        prec_raw   = (row.get('precinct') or '').strip()
        if not county_raw:
            continue
        ct = county_raw.title()          # "Richland"
        county_norm = normalize(county_raw)
        party = normalize_party(row.get('party') or '')
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

        # County aggregate:
        # - If this county has explicit county rows, only count those (precinct blank).
        # - Otherwise, fall back to summing precinct rows.
        if (county_norm in counties_with_explicit_rows and not prec_raw) or (county_norm not in counties_with_explicit_rows):
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
            # Manual alias mapping (results -> polygon).
            try:
                nkey = normalize(prec_key)
                if nkey in precinct_aliases:
                    prec_key = precinct_aliases[nkey]
            except Exception:
                pass
            _add(precinct_agg, prec_key)

    # Some boundary files contain a single combined precinct where the results feed splits it
    # into numbered parts (e.g., "BELFAIR 1" + "BELFAIR 2" but polygon is "BELFAIR").
    # If the combined polygon exists, add a combined row so the precinct overlay can color it.
    if precinct_norm_set and precinct_agg:
        base_groups: dict[str, dict] = {}
        have_norm = {normalize(k) for k in precinct_agg.keys()}
        for key, node in precinct_agg.items():
            m = re.match(r'^(.*?)\s*-\s*(.+?)\s+(\d+[A-Z]{0,2})$', key, flags=re.IGNORECASE)
            if not m:
                continue
            county = m.group(1).strip().title()
            base_label = m.group(2).strip()
            base_key = f'{county} - {base_label}'
            base_norm = normalize(base_key)
            if base_norm not in precinct_norm_set:
                continue
            if base_norm in have_norm:
                continue
            acc = base_groups.get(base_key)
            if not acc:
                acc = {'dem': 0, 'rep': 0, 'other': 0, 'dem_cand': '', 'rep_cand': ''}
                base_groups[base_key] = acc
            acc['dem'] += int(node.get('dem') or 0)
            acc['rep'] += int(node.get('rep') or 0)
            acc['other'] += int(node.get('other') or 0)
            if not acc['dem_cand'] and node.get('dem_cand'):
                acc['dem_cand'] = node.get('dem_cand')
            if not acc['rep_cand'] and node.get('rep_cand'):
                acc['rep_cand'] = node.get('rep_cand')
        for base_key, acc in base_groups.items():
            precinct_agg[base_key] = acc

    return county_agg, precinct_agg


def build_election_data():
    print('\n=== County/Precinct Contest JSONs ===')
    contests_dir = os.path.join(DATA_OUT, 'contests')
    os.makedirs(contests_dir, exist_ok=True)
    manifest_entries = []
    precinct_norm_set, precinct_display_by_norm = load_precinct_polygon_index()
    precinct_aliases = load_precinct_aliases(precinct_display_by_norm)

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
            county_agg, precinct_agg = aggregate_all(ct_rows, precinct_norm_set, precinct_aliases)
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

    # Rebuild manifest by scanning the directory so partial CSV availability doesn't hide older slices.
    scanned_entries: list[dict] = []
    try:
        for fn in os.listdir(contests_dir):
            if not fn.endswith('.json'):
                continue
            if fn == 'manifest.json':
                continue
            path = os.path.join(contests_dir, fn)
            try:
                with open(path, encoding='utf-8') as fh:
                    payload = json.load(fh)
                y = int(payload.get('year') or 0)
                ct = str(payload.get('contest_type') or '').strip()
                rows = payload.get('rows') or []
                if y <= 0 or not ct or not isinstance(rows, list):
                    continue
                scanned_entries.append({
                    'year': y,
                    'contest_type': ct,
                    'file': fn,
                    'rows': len(rows),
                })
            except Exception:
                continue
    except Exception:
        scanned_entries = manifest_entries

    scanned_entries.sort(key=lambda e: (-e['year'], _PRIORITY.get(e['contest_type'], 99)))
    write_json({'files': scanned_entries}, os.path.join(contests_dir, 'manifest.json'))
    print(f'\n  manifest: {len(scanned_entries)} contest(s)')


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
                party = normalize_party(row.get('party') or '')
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


def _geom_bbox(coords) -> tuple[float, float, float, float]:
    minx = miny = float('inf')
    maxx = maxy = float('-inf')
    stack = [coords]
    while stack:
        cur = stack.pop()
        if not cur:
            continue
        if isinstance(cur[0], (int, float)) and len(cur) >= 2:
            x, y = float(cur[0]), float(cur[1])
            if x < minx: minx = x
            if y < miny: miny = y
            if x > maxx: maxx = x
            if y > maxy: maxy = y
        else:
            stack.extend(cur)
    return (minx, miny, maxx, maxy)


def _point_in_ring(x: float, y: float, ring: list) -> bool:
    # Ray casting algorithm; ring is list[[x,y], ...]
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        intersect = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-20) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


def _point_in_polygon(x: float, y: float, poly: list) -> bool:
    # poly = [outer_ring, hole1, hole2, ...]
    if not poly:
        return False
    outer = poly[0]
    if not _point_in_ring(x, y, outer):
        return False
    for hole in poly[1:]:
        if _point_in_ring(x, y, hole):
            return False
    return True


def _point_in_geometry(x: float, y: float, geom: dict) -> bool:
    gtype = (geom or {}).get('type')
    coords = (geom or {}).get('coordinates')
    if not gtype or coords is None:
        return False
    if gtype == 'Polygon':
        return _point_in_polygon(x, y, coords)
    if gtype == 'MultiPolygon':
        for poly in coords:
            if _point_in_polygon(x, y, poly):
                return True
        return False
    return False


def _parse_district_num(raw) -> str:
    s = ('' if raw is None else str(raw)).strip()
    if not s:
        return ''
    try:
        return str(int(s))
    except ValueError:
        return s


def _district_sort_key(dnum: str) -> tuple[int, int | str]:
    """Stable tiebreak for district ids: numeric first, then lexical."""
    s = (dnum or '').strip()
    try:
        return (0, int(s))
    except ValueError:
        return (1, s)


def load_block_assignment_precinct_weights() -> dict[str, dict[str, dict[str, float]]]:
    """
    Build precinct_norm -> district-share weights from Census block assignment files.

    Uses:
      Data/BlockAssign_ST45_SC.zip
        - BlockAssign_ST45_SC_VTD.txt (BLOCKID -> COUNTYFP + VTD DISTRICT)
        - BlockAssign_ST45_SC_CD.txt
        - BlockAssign_ST45_SC_SLDL.txt
        - BlockAssign_ST45_SC_SLDU.txt
    """
    precincts_path = os.path.join(DATA_OUT, 'Voting_Precincts.geojson')
    if not (os.path.exists(BLOCK_ASSIGN_ZIP_PATH) and os.path.exists(precincts_path)):
        return {}

    # Index precinct polygons by (countyfp, vtdst20) so block assignments can map back
    # to the same precinct_norm keys used by contest rows.
    vtd_to_precincts: dict[tuple[str, str], set[str]] = {}
    try:
        with open(precincts_path, encoding='utf-8') as fh:
            precincts = json.load(fh) or {}
        for feat in precincts.get('features', []) or []:
            props = (feat or {}).get('properties') or {}
            pn = (props.get('precinct_norm') or '').strip().upper()
            countyfp = str(props.get('COUNTYFP20') or '').strip().zfill(3)
            vtd = str(props.get('VTDST20') or '').strip().zfill(6)
            if not (pn and countyfp and vtd):
                continue
            vtd_to_precincts.setdefault((countyfp, vtd), set()).add(pn)
    except Exception:
        return {}
    if not vtd_to_precincts:
        return {}

    members = {
        'congressional': 'BlockAssign_ST45_SC_CD.txt',
        'state_house': 'BlockAssign_ST45_SC_SLDL.txt',
        'state_senate': 'BlockAssign_ST45_SC_SLDU.txt',
    }

    out: dict[str, dict[str, dict[str, float]]] = {k: {} for k in members.keys()}
    try:
        with zipfile.ZipFile(BLOCK_ASSIGN_ZIP_PATH) as z:
            names = {n: True for n in z.namelist()}
            if 'BlockAssign_ST45_SC_VTD.txt' not in names:
                return {}
            for req in members.values():
                if req not in names:
                    return {}

            # BLOCKID -> (COUNTYFP, VTDST20)
            block_to_vtd: dict[str, tuple[str, str]] = {}
            with z.open('BlockAssign_ST45_SC_VTD.txt') as fh:
                reader = csv.DictReader(io.TextIOWrapper(fh, encoding='utf-8-sig', newline=''), delimiter='|')
                for row in reader:
                    block = (row.get('BLOCKID') or '').strip()
                    countyfp = (row.get('COUNTYFP') or '').strip().zfill(3)
                    vtd = (row.get('DISTRICT') or '').strip().zfill(6)
                    if not (block and countyfp and vtd):
                        continue
                    if (countyfp, vtd) in vtd_to_precincts:
                        block_to_vtd[block] = (countyfp, vtd)
            if not block_to_vtd:
                return {}

            for scope, member in members.items():
                counts_by_precinct: dict[str, dict[str, int]] = {}
                with z.open(member) as fh:
                    reader = csv.DictReader(io.TextIOWrapper(fh, encoding='utf-8-sig', newline=''), delimiter='|')
                    for row in reader:
                        block = (row.get('BLOCKID') or '').strip()
                        dnum = _parse_district_num(row.get('DISTRICT'))
                        if not (block and dnum):
                            continue
                        vtd_key = block_to_vtd.get(block)
                        if not vtd_key:
                            continue
                        precincts_for_vtd = vtd_to_precincts.get(vtd_key)
                        if not precincts_for_vtd:
                            continue
                        for pn in precincts_for_vtd:
                            node = counts_by_precinct.setdefault(pn, {})
                            node[dnum] = int(node.get(dnum) or 0) + 1

                mapped = {}
                for pn, counts in counts_by_precinct.items():
                    total = sum(int(v) for v in counts.values())
                    if total <= 0:
                        continue
                    shares = {}
                    for dnum, cnt in counts.items():
                        shares[dnum] = float(cnt) / float(total)
                    mapped[pn] = shares
                out[scope] = mapped
    except Exception:
        return {}

    return out


def build_statewide_contests_by_district_from_slices() -> int:
    """
    Build per-district results for *statewide* contest slices (President, US Senate, etc.)
    by assigning each precinct to a district polygon and summing precinct rows from
    data/contests/*.json.

    Assignment order:
      1) Census block assignment files from Data/BlockAssign_ST45_SC.zip (preferred)
      2) Precinct centroid point-in-polygon fallback for any remaining unmatched precincts

    Outputs files to data/district_contests/{scope}_{contest_type}_{year}.json
    and (re)writes data/district_contests/manifest.json to include both district-race
    and statewide-into-district slices.
    """
    contests_manifest_path = os.path.join(DATA_OUT, 'contests', 'manifest.json')
    centroids_path = os.path.join(DATA_OUT, 'precinct_centroids.geojson')
    if not (os.path.exists(contests_manifest_path) and os.path.exists(centroids_path)):
        return 0

    district_sources = {
        'congressional': (os.path.join(DATA_OUT, 'tileset', 'sc_cd118_tileset.geojson'), 'CD118FP'),
        'state_house':   (os.path.join(DATA_OUT, 'tileset', 'sc_state_house_2022_lines_tileset.geojson'), 'SLDLST'),
        'state_senate':  (os.path.join(DATA_OUT, 'tileset', 'sc_state_senate_2022_lines_tileset.geojson'), 'SLDUST'),
    }
    for path, _ in district_sources.values():
        if not os.path.exists(path):
            return 0

    with open(contests_manifest_path, encoding='utf-8') as fh:
        contest_manifest = json.load(fh) or {}
    contest_entries = contest_manifest.get('files') or []
    if not contest_entries:
        return 0

    precinct_to_district: dict[str, dict[str, str]] = {k: {} for k in district_sources.keys()}
    if os.path.exists(centroids_path):
        with open(centroids_path, encoding='utf-8') as fh:
            centroids = json.load(fh) or {}
        centroid_points = []
        for f in centroids.get('features', []) or []:
            geom = (f or {}).get('geometry') or {}
            if geom.get('type') != 'Point':
                continue
            coords = geom.get('coordinates') or []
            if len(coords) < 2:
                continue
            props = (f or {}).get('properties') or {}
            pn = (props.get('precinct_norm') or '').strip().upper()
            if not pn:
                continue
            centroid_points.append((pn, float(coords[0]), float(coords[1])))

        for scope, (path, num_field) in district_sources.items():
            with open(path, encoding='utf-8') as fh:
                gj = json.load(fh) or {}
            districts = []
            for feat in gj.get('features', []) or []:
                geom = (feat or {}).get('geometry') or {}
                props = (feat or {}).get('properties') or {}
                dnum = _parse_district_num(props.get(num_field))
                if not dnum:
                    continue
                bbox = _geom_bbox(geom.get('coordinates'))
                districts.append((bbox, geom, dnum))

            for pn, x, y in centroid_points:
                chosen = ''
                for (minx, miny, maxx, maxy), geom, dnum in districts:
                    if x < minx or x > maxx or y < miny or y > maxy:
                        continue
                    if _point_in_geometry(x, y, geom):
                        chosen = dnum
                        break
                if chosen:
                    precinct_to_district[scope][pn] = chosen

    # Prefer block-derived fractional allocation where available.
    # Use this for all scopes so split precincts contribute proportionally
    # (centroid assignment can bias district aggregates).
    block_weight_scopes = {'congressional', 'state_house', 'state_senate'}
    block_weight_maps = load_block_assignment_precinct_weights()
    if block_weight_maps:
        for scope, mapping in block_weight_maps.items():
            if scope not in block_weight_scopes:
                continue
            if scope not in precinct_to_district:
                continue
            if not mapping:
                continue
            print(f'  block assignments ({scope}): {len(mapping)} precinct mappings')

    dist_dir = os.path.join(DATA_OUT, 'district_contests')
    os.makedirs(dist_dir, exist_ok=True)

    written = 0
    # Aggregate each contest slice into each scope
    for entry in contest_entries:
        year = entry.get('year')
        contest_type = entry.get('contest_type')
        fname = entry.get('file')
        if not (year and contest_type and fname):
            continue
        contest_path = os.path.join(DATA_OUT, 'contests', fname)
        if not os.path.exists(contest_path):
            continue

        with open(contest_path, encoding='utf-8') as fh:
            payload = json.load(fh) or {}
        rows = payload.get('rows') or []
        precinct_rows = [r for r in rows if isinstance(r, dict) and ' - ' in str(r.get('county') or '')]
        if not precinct_rows:
            continue

        for scope in district_sources.keys():
            scope_weight_map = (block_weight_maps.get(scope) or {}) if scope in block_weight_scopes else {}
            by_dist = {}
            matched = 0
            matched_weighted = 0
            dem_name = ''
            rep_name = ''
            for r in precinct_rows:
                key = (r.get('county') or '').strip()
                pn = normalize(key)
                weights = scope_weight_map.get(pn) if scope_weight_map else None
                if weights:
                    matched += 1
                    matched_weighted += 1
                    dem_votes = float(r.get('dem_votes') or 0)
                    rep_votes = float(r.get('rep_votes') or 0)
                    other_votes = float(r.get('other_votes') or 0)
                    for dnum, share in weights.items():
                        w = float(share or 0)
                        if w <= 0:
                            continue
                        if dnum not in by_dist:
                            by_dist[dnum] = {'dem': 0.0, 'rep': 0.0, 'other': 0.0, 'dem_cand': '', 'rep_cand': ''}
                        node = by_dist[dnum]
                        node['dem'] += dem_votes * w
                        node['rep'] += rep_votes * w
                        node['other'] += other_votes * w
                else:
                    dnum = precinct_to_district[scope].get(pn)
                    if not dnum:
                        continue
                    matched += 1
                    if dnum not in by_dist:
                        by_dist[dnum] = {'dem': 0.0, 'rep': 0.0, 'other': 0.0, 'dem_cand': '', 'rep_cand': ''}
                    node = by_dist[dnum]
                    node['dem'] += float(r.get('dem_votes') or 0)
                    node['rep'] += float(r.get('rep_votes') or 0)
                    node['other'] += float(r.get('other_votes') or 0)

                if not dem_name:
                    dem_name = (r.get('dem_candidate') or '').strip()
                if not rep_name:
                    rep_name = (r.get('rep_candidate') or '').strip()
                if dem_name and rep_name:
                    # Not strictly required to break, but avoids extra string checks.
                    pass

            if not by_dist:
                continue

            # Fill candidate labels (consistent within a statewide contest).
            if dem_name or rep_name:
                for node in by_dist.values():
                    if dem_name and not node['dem_cand']:
                        node['dem_cand'] = dem_name
                    if rep_name and not node['rep_cand']:
                        node['rep_cand'] = rep_name

            results = {}
            for dnum, v in by_dist.items():
                dem_votes = int(round(v['dem']))
                rep_votes = int(round(v['rep']))
                other_votes = int(round(v['other']))
                total = dem_votes + rep_votes + other_votes
                margin = rep_votes - dem_votes
                mpct = round(margin / total * 100, 4) if total else 0
                winner = 'R' if margin > 0 else ('D' if margin < 0 else 'T')
                results[str(dnum)] = {
                    'dem_votes':     dem_votes,
                    'rep_votes':     rep_votes,
                    'other_votes':   other_votes,
                    'total_votes':   total,
                    'dem_candidate': v.get('dem_cand', ''),
                    'rep_candidate': v.get('rep_cand', ''),
                    'margin':        margin,
                    'margin_pct':    mpct,
                    'winner':        winner,
                    'color':         margin_color(mpct),
                }

            out_name = f'{scope}_{contest_type}_{year}.json'
            coverage = round(matched / len(precinct_rows) * 100, 4) if precinct_rows else 0
            out_payload = {
                'general': {'results': results},
                'meta': {
                    'match_coverage_pct': coverage,
                    'precinct_rows_total': len(precinct_rows),
                    'precinct_rows_matched': matched,
                    'precinct_rows_block_weighted': matched_weighted,
                },
            }
            write_json(out_payload, os.path.join(dist_dir, out_name))
            written += 1

    # Rebuild manifest from disk so it includes both district-race and statewide-by-district slices.
    manifest_entries = []
    for fn in os.listdir(dist_dir):
        if not fn.endswith('.json') or fn == 'manifest.json':
            continue
        base = fn[:-5]
        parts = base.split('_')
        if len(parts) < 3:
            continue
        scope = parts[0] + ('' if parts[0] != 'state' else '')  # no-op, keep for readability
        # scope names include an underscore for state_house/state_senate
        if parts[0] == 'state' and len(parts) >= 4:
            scope = '_'.join(parts[0:2])
            contest_type = '_'.join(parts[2:-1])
            year = parts[-1]
        else:
            scope = parts[0]
            contest_type = '_'.join(parts[1:-1])
            year = parts[-1]
        try:
            y = int(year)
        except ValueError:
            continue

        # Best-effort row count
        rows_count = 0
        try:
            with open(os.path.join(dist_dir, fn), encoding='utf-8') as fh:
                node = json.load(fh) or {}
            results = (node.get('general') or {}).get('results') or {}
            rows_count = len(results)
        except Exception:
            rows_count = 0

        manifest_entries.append({
            'year': y,
            'contest_type': contest_type,
            'scope': scope,
            'file': fn,
            'rows': rows_count,
        })

    manifest_entries.sort(key=lambda e: (-e['year'], _PRIORITY.get(e['contest_type'], 99), e['scope']))
    write_json({'files': manifest_entries}, os.path.join(dist_dir, 'manifest.json'))
    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    try:
        has_shapes = os.path.exists(SHP_COUNTY) and os.path.exists(SHP_VTD)
        has_any_csv = any(os.path.exists(p) for p in ELECTION_FILES.values())

        if has_shapes:
            county_fp_map = build_county_geojson()
            build_precinct_geojson(county_fp_map)
            build_district_geojson()

        if has_any_csv:
            build_election_data()
            build_district_contests()

        # Always attempt this if the generated contest slices + centroids exist.
        n = build_statewide_contests_by_district_from_slices()
        if n:
            print(f'\n=== Statewide-by-District Slices ===\n  wrote  {n} file(s)')
        print('\nBuild complete.')
    except Exception as exc:
        print(f'\nBuild failed: {exc}')
        traceback.print_exc()
