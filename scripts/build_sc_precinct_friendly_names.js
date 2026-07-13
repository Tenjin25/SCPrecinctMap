/* eslint-disable no-console */
const fs = require('fs');
const path = require('path');

function normalizeKey(raw) {
  return String(raw || '')
    .replace(/[^a-z0-9 .-]/gi, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toUpperCase();
}

function toTitleCaseName(raw) {
  const s = String(raw || '').replace(/[_]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (!s) return '';
  const titled = s
    .toLowerCase()
    .replace(/\b([a-z])/g, (m, c) => c.toUpperCase())
    .replace(/'([A-Z])\b/g, (m, c) => `'${c.toLowerCase()}`)
    .replace(/\b([A-Z])([a-z])\b/g, (m, a, b) => `${a}${b}`)
    .replace(/\b([A-Z][a-z]*)(\d+[A-Z]?)\b/g, '$1 $2')
    .replace(/\bNo\.\s*(\d+)/gi, 'No. $1');
  return applyNameOverride(titled);
}

function applyNameOverride(raw) {
  const name = String(raw || '').replace(/\s+/g, ' ').trim();
  const key = normalizeKey(name);
  const overrides = {
    'FOUR HOLE': 'Four Holes',
  };
  return overrides[key] || name;
}

function normalizeAliasNameCandidate(raw) {
  const s = String(raw || '').trim().toUpperCase();
  if (!s) return '';
  const cleaned = s.replace(/[_]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (!cleaned) return '';
  if (/VOTING\s*DISTRICT/i.test(cleaned)) return '';
  if (/^\d+$/.test(cleaned)) return '';
  return cleaned;
}

function isCodeLikeToken(raw) {
  const s = String(raw || '').trim().toUpperCase();
  if (!s) return true;
  const compact = s.replace(/[^A-Z0-9]/g, '');
  if (!compact) return true;
  if (/[0-9]/.test(compact) && compact.length <= 6) return true;
  if (compact.length <= 4 && /^[A-Z]+$/.test(compact)) return true;
  return false;
}

function scoreNameCandidate(raw) {
  const s = normalizeAliasNameCandidate(raw);
  if (!s) return -1e9;
  const letters = (s.match(/[A-Z]/g) || []).length;
  const digits = (s.match(/[0-9]/g) || []).length;
  const spaces = (s.match(/\s/g) || []).length;
  let score = 0;
  score += letters * 2.2;
  score -= digits * 0.9;
  score += spaces * 1.0;
  score += Math.min(24, s.length);
  if (/VOTING\s*DISTRICT/i.test(s)) score -= 1000;
  if (/^(EARLY|ABSENTEE|PROVISIONAL|ONE\s+STOP|MAIL)/i.test(s)) score -= 20;
  return score;
}

function pickBestName(props) {
  const candidates = [
    props.precinct_full_name,
    props.precinct_desc,
    props.enr_desc,
    props.ENR_DESC,
    props.NAME20,
    props.NAMELSAD20,
    props.prec_id,
    props.PREC_ID,
    props.VTDST20,
  ];
  let best = '';
  let bestScore = -1e9;
  for (const raw of candidates) {
    const cleaned = normalizeAliasNameCandidate(raw);
    if (!cleaned) continue;
    const score = scoreNameCandidate(cleaned) + (isCodeLikeToken(cleaned) ? -10 : 0);
    if (score > bestScore) {
      best = cleaned;
      bestScore = score;
    }
  }
  return toTitleCaseName(best);
}

function addCode(codes, raw) {
  const s = String(raw || '').replace(/\s+/g, ' ').trim();
  if (!s) return;
  const upper = s.toUpperCase();
  codes.add(upper);
  const compact = upper.replace(/[^A-Z0-9]/g, '');
  if (compact) codes.add(compact);
  const stripped = compact.replace(/^0+/, '');
  if (stripped) codes.add(stripped);
  if (/^\d+$/.test(compact)) {
    const n = String(parseInt(compact, 10));
    if (n !== 'NaN') {
      codes.add(n);
      codes.add(n.padStart(2, '0'));
      codes.add(n.padStart(3, '0'));
      codes.add(n.padStart(4, '0'));
      codes.add(n.padStart(6, '0'));
    }
  }
}

function main() {
  const repoRoot = path.resolve(__dirname, '..');
  const precinctPath = process.argv[2]
    ? path.resolve(process.argv[2])
    : path.join(repoRoot, 'data', 'Voting_Precincts.geojson');
  const centroidPath = process.argv[3]
    ? path.resolve(process.argv[3])
    : path.join(repoRoot, 'data', 'precinct_centroids.geojson');
  const friendlyPath = process.argv[4]
    ? path.resolve(process.argv[4])
    : path.join(repoRoot, 'data', 'precinct_friendly_names.json');

  const precincts = JSON.parse(fs.readFileSync(precinctPath, 'utf8'));
  const friendlyByCounty = {};
  const displayByNorm = new Map();

  for (const feature of precincts.features || []) {
    const props = feature.properties || {};
    const countyName = String(props.county_nam || props.COUNTYNAME || props.County || '').trim();
    const countyNorm = normalizeKey(props.county_norm || countyName);
    const rawName = String(props.NAME20 || props.NAMELSAD20 || props.prec_id || '').replace(/\s+/g, ' ').trim();
    const friendly = pickBestName(props) || toTitleCaseName(rawName);
    const precinctNorm = normalizeKey(props.precinct_norm || `${countyName} - ${rawName}`);
    const displayName = countyName && friendly ? `${countyName} - ${friendly}` : friendly;
    const codes = new Set();
    addCode(codes, rawName);
    addCode(codes, props.prec_id);
    addCode(codes, props.PREC_ID);
    addCode(codes, props.VTDST20);
    addCode(codes, props.GEOID20);

    if (countyNorm && friendly) {
      if (!friendlyByCounty[countyNorm]) friendlyByCounty[countyNorm] = {};
      for (const code of codes) {
        if (code) friendlyByCounty[countyNorm][code] = friendly;
      }
    }
    if (precinctNorm) displayByNorm.set(precinctNorm, { friendly, displayName });

    feature.properties = {
      ...props,
      county_nam: countyName || props.county_nam || '',
      county_norm: countyNorm,
      prec_id: rawName || props.prec_id || '',
      precinct_code: String(props.VTDST20 || '').trim(),
      precinct_full_name: friendly,
      precinct_display_name: displayName,
      precinct_norm: precinctNorm,
    };
  }

  if (fs.existsSync(centroidPath)) {
    const centroids = JSON.parse(fs.readFileSync(centroidPath, 'utf8'));
    for (const feature of centroids.features || []) {
      const props = feature.properties || {};
      const precinctNorm = normalizeKey(props.precinct_norm);
      const display = displayByNorm.get(precinctNorm) || {};
      feature.properties = {
        ...props,
        precinct_full_name: display.friendly || props.precinct_full_name || '',
        precinct_display_name: display.displayName || props.precinct_display_name || '',
      };
    }
    fs.writeFileSync(centroidPath, JSON.stringify(centroids), 'utf8');
  }

  const friendlyOut = {
    version: 1,
    generated_at: new Date().toISOString(),
    generated_from: [path.relative(repoRoot, precinctPath).replace(/\\/g, '/')],
    counties: friendlyByCounty,
  };

  fs.writeFileSync(precinctPath, JSON.stringify(precincts), 'utf8');
  fs.writeFileSync(friendlyPath, JSON.stringify(friendlyOut), 'utf8');
  console.log(`Wrote ${Object.keys(friendlyByCounty).length} counties -> ${path.relative(repoRoot, friendlyPath)}`);
  console.log(`Updated ${path.relative(repoRoot, precinctPath)} and ${path.relative(repoRoot, centroidPath)}`);
}

if (require.main === module) main();
