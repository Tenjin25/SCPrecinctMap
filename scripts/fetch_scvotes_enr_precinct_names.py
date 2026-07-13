#!/usr/bin/env python3
import argparse
import csv
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request, urlopen


class CountyLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        data = {k.lower(): v or "" for k, v in attrs}
        county = data.get("id", "").strip()
        value = data.get("value", "").strip()
        if county and value and re.search(r"/\d+/index\.html$", value):
            self.links.append((county, value))


def fetch_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": "SCPrecinctMap historical precinct fetcher"})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_json(url: str) -> dict:
    return json.loads(fetch_text(url))


def county_base_from_index(index_url: str) -> str:
    html = fetch_text(index_url)
    m = re.search(r'URL=\./([^"/]+)/en/summary\.html', html, re.IGNORECASE)
    if not m:
        raise RuntimeError(f"Could not find county redirect in {index_url}")
    return urljoin(index_url, f"./{m.group(1)}/")


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch historical precinct names from legacy SCVotes ENR county JSON.")
    ap.add_argument("--state-select-url", required=True, help="Legacy ENR select-county.html URL for the election")
    ap.add_argument("--year", required=True, type=int)
    ap.add_argument("--out", default="scripts/out/scvotes_enr_precinct_names.csv")
    args = ap.parse_args()

    select_html = fetch_text(args.state_select_url)
    parser = CountyLinkParser()
    parser.feed(select_html)
    if not parser.links:
        raise SystemExit(f"No county links found in {args.state_select_url}")

    rows: list[dict] = []
    state_root = re.sub(r"/\d+/\d+/en/select-county\.html.*$", "/", args.state_select_url)
    for county, rel_index in parser.links:
        index_url = urljoin(state_root, rel_index.lstrip("/"))
        county_base = county_base_from_index(index_url)
        status_url = urljoin(county_base, "json/status.json")
        status = fetch_json(status_url)
        precincts = status.get("P") or []
        statuses = status.get("S") or []
        registered = status.get("R") or []
        ballots = status.get("B") or []
        for i, precinct in enumerate(precincts):
            rows.append({
                "year": args.year,
                "county": county,
                "precinct": str(precinct).strip(),
                "status_code": statuses[i] if i < len(statuses) else "",
                "registered_voters": registered[i] if i < len(registered) else "",
                "ballots_cast": ballots[i] if i < len(ballots) else "",
                "county_index_url": index_url,
                "county_status_json": status_url,
            })

    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        fieldnames = [
            "year",
            "county",
            "precinct",
            "status_code",
            "registered_voters",
            "ballots_cast",
            "county_index_url",
            "county_status_json",
        ]
        wr = csv.DictWriter(fh, fieldnames=fieldnames)
        wr.writeheader()
        wr.writerows(rows)

    print(f"wrote {args.out}")
    print(f"counties={len(parser.links)} precinct_rows={len(rows)}")


if __name__ == "__main__":
    main()
