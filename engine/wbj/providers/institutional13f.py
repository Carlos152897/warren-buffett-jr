"""SEC Form 13F institutional-holder lookups from SEC's bulk structured
datasets — a free fallback for FMP's plan-restricted
`institutional-ownership/extract-analytics/holder` endpoint.

Unlike Form 4 (filed by the company's own insiders, discoverable from that
company's own EDGAR submission history), "who holds this stock" is spread
across every institutional investment manager's own 13F-HR filing — there
is no per-company reverse index. SEC instead publishes a bulk structured
dataset per rolling ~3-month window (`INFOTABLE.tsv`/`COVERPAGE.tsv`/...),
listed at `INDEX_URL`. `Form13FProvider` downloads the current window once
(cached on disk, ~100MB, refreshed roughly monthly by SEC), then streams
`INFOTABLE.tsv` straight out of the zip filtering by CUSIP (a few seconds
per lookup) rather than loading the whole ~400MB file into memory.

`holders()` returns FMP-shaped rows (`holder`, `shares`, `dateReported`,
`change`, `symbol`) so it slots into `wbj.packet.builder` unchanged.
`change` (vs. prior quarter) is always None — reconstructing it would
require also parsing the previous window's dataset, not attempted here.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from wbj.providers.base import Provider

INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
_ZIP_HREF_RE = re.compile(
    r'href="(/files/structureddata/data/form-13f-data-sets/[^"]+\.zip)"'
)
EDGAR_USER_AGENT = "warren-buffett-jr victor@infusioninvestments.com"
_EDGAR_HEADERS = {"User-Agent": EDGAR_USER_AGENT}

_MAX_AGE_HOLDERS_DAYS = 30  # SEC refreshes the bulk window roughly monthly
_TOP_N_HOLDERS = 20
_DATASET_DOWNLOAD_TIMEOUT = 120.0
_PSEUDO_TICKER = "_13F"


def _parse_period(value: str | None) -> str | None:
    """`"31-MAR-2026"` -> `"2026-03-31"`; None/unparseable -> None."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


class Form13FProvider(Provider):
    """Institutional 13F holders, reconstructed from SEC's bulk datasets."""

    @property
    def available(self) -> bool:
        """Always True — no API key required, only network access to SEC."""
        return True

    def _dataset_dir(self) -> Path:
        d = self.cache.cache_dir / _PSEUDO_TICKER
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _latest_dataset_href(self) -> str | None:
        try:
            response = self.client.get(INDEX_URL, headers=_EDGAR_HEADERS)
        except httpx.TransportError:
            return None
        if response.status_code >= 400:
            return None
        match = _ZIP_HREF_RE.search(response.text)
        return match.group(1) if match else None

    def _current_dataset_path(self) -> Path | None:
        """Return the local path to the current 13F bulk dataset,
        downloading it first if not already cached. Stale windows from
        prior months are deleted once the new one lands successfully.
        Returns None on any network/parse failure (never raises)."""
        href = self._latest_dataset_href()
        if href is None:
            return None

        filename = href.rsplit("/", 1)[-1]
        dataset_dir = self._dataset_dir()
        local_path = dataset_dir / filename
        if local_path.exists():
            return local_path

        url = f"https://www.sec.gov{href}"
        tmp_path = local_path.with_suffix(".part")
        try:
            with self.client.stream(
                "GET", url, headers=_EDGAR_HEADERS, timeout=_DATASET_DOWNLOAD_TIMEOUT
            ) as response:
                if response.status_code >= 400:
                    return None
                with open(tmp_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=1 << 20):
                        f.write(chunk)
        except (httpx.TransportError, OSError):
            tmp_path.unlink(missing_ok=True)
            return None

        tmp_path.replace(local_path)

        for stale in dataset_dir.iterdir():
            if stale != local_path:
                stale.unlink(missing_ok=True)

        return local_path

    def _load_filer_meta(self, dataset_path: Path) -> dict[str, tuple[str, str | None]]:
        """`ACCESSION_NUMBER -> (FILINGMANAGER_NAME, dateReported)`."""
        meta: dict[str, tuple[str, str | None]] = {}
        with zipfile.ZipFile(dataset_path) as z, z.open("COVERPAGE.tsv") as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8", newline=""), delimiter="\t")
            header = next(reader)
            idx = {name: i for i, name in enumerate(header)}
            for row in reader:
                meta[row[idx["ACCESSION_NUMBER"]]] = (
                    row[idx["FILINGMANAGER_NAME"]],
                    _parse_period(row[idx["REPORTCALENDARORQUARTER"]]),
                )
        return meta

    def _scan_infotable(self, dataset_path: Path, cusip: str) -> list[tuple[str, float, float]]:
        """`[(accession_number, value_usd_thousands, shares), ...]` for
        every INFOTABLE.tsv row matching `cusip`."""
        rows: list[tuple[str, float, float]] = []
        with zipfile.ZipFile(dataset_path) as z, z.open("INFOTABLE.tsv") as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8", newline=""), delimiter="\t")
            header = next(reader)
            idx = {name: i for i, name in enumerate(header)}
            for row in reader:
                if row[idx["CUSIP"]] != cusip:
                    continue
                try:
                    value = float(row[idx["VALUE"]])
                    shares = float(row[idx["SSHPRNAMT"]])
                except ValueError:
                    continue
                rows.append((row[idx["ACCESSION_NUMBER"]], value, shares))
        return rows

    def holders(self, cusip: str | None, ticker: str) -> list[dict] | None:
        """Top ~20 institutional 13F holders of `cusip`, aggregated by
        filing manager and sorted by reported value (USD) descending.

        Returns None if `cusip` is missing or the dataset can't be
        fetched/parsed at all; `[]` if the dataset was read fine but no
        filer reported this CUSIP in the current window.
        """
        if not cusip:
            return None

        age = self.cache.age_days(_PSEUDO_TICKER, cusip)
        if age is not None and age <= _MAX_AGE_HOLDERS_DAYS:
            return self.cache.get(_PSEUDO_TICKER, cusip)

        dataset_path = self._current_dataset_path()
        if dataset_path is None:
            return None

        try:
            filer_meta = self._load_filer_meta(dataset_path)
            matches = self._scan_infotable(dataset_path, cusip)
        except (zipfile.BadZipFile, KeyError, OSError):
            return None

        aggregated: dict[str, dict[str, Any]] = {}
        for accession, value_thousands, shares in matches:
            name, period = filer_meta.get(accession, (accession, None))
            bucket = aggregated.setdefault(name, {"value_usd": 0.0, "shares": 0.0, "dateReported": None})
            bucket["value_usd"] += value_thousands * 1000
            bucket["shares"] += shares
            if period and (bucket["dateReported"] is None or period > bucket["dateReported"]):
                bucket["dateReported"] = period

        ranked = sorted(aggregated.items(), key=lambda kv: kv[1]["value_usd"], reverse=True)
        result = [
            {
                "holder": name,
                "shares": int(v["shares"]),
                "dateReported": v["dateReported"],
                "change": None,
                "symbol": ticker,
            }
            for name, v in ranked[:_TOP_N_HOLDERS]
        ]

        self.cache.put(_PSEUDO_TICKER, cusip, result)
        return result
