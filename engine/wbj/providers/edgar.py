"""SEC EDGAR provider: ticker->CIK lookup, XBRL company facts, filing metadata.

Tier-1 per Cerebro/shared/SOURCE_HIERARCHY.md ("Regulatory filing and filing
acceptance metadata" ranks first). No API key is required — `EdgarProvider`
is always `available`. SEC's fair-access policy requires a descriptive
`User-Agent` identifying the requester on every request
(https://www.sec.gov/os/webmaster-faq#developers); `EDGAR_USER_AGENT` is
sent on every call via `wbj.providers.base.Provider.get_json`'s `headers`
pass-through.

Endpoints:
- `https://www.sec.gov/files/company_tickers.json` — ticker -> CIK map,
  one global payload (not per-ticker), refreshed roughly monthly by SEC,
  so cached for up to 30 days under a fixed global cache entry.
- `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json` — all
  XBRL (dei/us-gaap/...) facts reported by the company across filings.
  Cached per-CIK for up to 1 day.
- `https://data.sec.gov/submissions/CIK{cik:010d}.json` — filing history
  including `acceptanceDateTime`, used to determine filing recency.
  Cached per-CIK for up to 1 day.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from wbj.providers.base import Provider

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

EDGAR_USER_AGENT = "warren-buffett-jr victor@infusioninvestments.com"
_EDGAR_HEADERS = {"User-Agent": EDGAR_USER_AGENT}

# The tickers map is one global, ticker-independent payload, so it is
# cached under a fixed pseudo-ticker rather than the caller's ticker —
# looking up a second ticker must reuse the same cache entry.
_GLOBAL_CACHE_TICKER = "_GLOBAL"

_MAX_AGE_TICKERS = 30
_MAX_AGE_COMPANYFACTS = 1
_MAX_AGE_SUBMISSIONS = 1
# A filed Form 4 never changes after acceptance, so once parsed it is
# cached indefinitely (in practice: until the cache dir is cleared).
_MAX_AGE_FORM4 = 3650
_FORM4_FETCH_LIMIT = 25

_TRANSACTION_CODE_LABELS = {
    "P": "P-Purchase",
    "S": "S-Sale",
    "A": "A-Award",
    "G": "G-Gift",
    "M": "M-Exercise",
    "F": "F-InKind",
    "D": "D-Disposition",
    "C": "C-Conversion",
    "X": "X-Exercise",
}


def _cik_cache_key(cik: int) -> str:
    return f"CIK{cik:010d}"


class EdgarProvider(Provider):
    """SEC EDGAR data provider (no API key required)."""

    @property
    def available(self) -> bool:
        """Always True — EDGAR requires no API key, only a User-Agent header."""
        return True

    def cik_for(self, ticker: str) -> int | None:
        """Look up the CIK for `ticker` via SEC's company_tickers.json map.

        Returns None if the ticker isn't found or the payload is malformed.
        """
        payload = self.get_json(
            TICKERS_URL,
            {},
            "tickers",
            _GLOBAL_CACHE_TICKER,
            max_age_days=_MAX_AGE_TICKERS,
            headers=_EDGAR_HEADERS,
        )
        if not isinstance(payload, dict):
            return None

        ticker_upper = ticker.upper()
        for entry in payload.values():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("ticker", "")).upper() != ticker_upper:
                continue
            cik = entry.get("cik_str")
            try:
                return int(cik)
            except (TypeError, ValueError):
                return None
        return None

    def companyfacts(self, cik: int) -> dict | None:
        """Fetch all XBRL company facts (dei/us-gaap/...) for `cik`."""
        payload = self.get_json(
            COMPANYFACTS_URL.format(cik=cik),
            {},
            "companyfacts",
            _cik_cache_key(cik),
            max_age_days=_MAX_AGE_COMPANYFACTS,
            headers=_EDGAR_HEADERS,
        )
        return payload if isinstance(payload, dict) else None

    def _submissions(self, cik: int) -> dict | None:
        """Fetch (cache-first) the raw submissions payload for `cik`."""
        payload = self.get_json(
            SUBMISSIONS_URL.format(cik=cik),
            {},
            "submissions",
            _cik_cache_key(cik),
            max_age_days=_MAX_AGE_SUBMISSIONS,
            headers=_EDGAR_HEADERS,
        )
        return payload if isinstance(payload, dict) else None

    def filing_acceptance_times(self, cik: int) -> list[dict] | None:
        """Return recent filings' form/acceptanceDateTime/accessionNumber.

        Derived from `https://data.sec.gov/submissions/CIK{cik}.json`'s
        `filings.recent` arrays. Returns None if the payload is malformed
        or lacks the expected `filings.recent` structure.
        """
        payload = self._submissions(cik)
        if payload is None:
            return None

        recent = payload.get("filings", {}).get("recent")
        if not isinstance(recent, dict):
            return None

        forms = recent.get("form", [])
        accept_times = recent.get("acceptanceDateTime", [])
        accession_numbers = recent.get("accessionNumber", [])

        return [
            {
                "form": form,
                "acceptanceDateTime": accepted,
                "accessionNumber": accession,
            }
            for form, accepted, accession in zip(
                forms, accept_times, accession_numbers, strict=False
            )
        ]

    def form4_filings(self, cik: int, limit: int = _FORM4_FETCH_LIMIT) -> list[dict]:
        """Metadata (accession/filingDate/primaryDocument) for the most
        recent `limit` Form 4 (insider) filings, newest first.

        Only looks at `filings.recent` (SEC's most-recent-filings window),
        so a company with very heavy Form 4 activity may have older
        filings excluded — acceptable for the recency window this project
        cares about (see `CLAUDE.md` re-run triggers).
        """
        payload = self._submissions(cik)
        if payload is None:
            return []

        recent = payload.get("filings", {}).get("recent")
        if not isinstance(recent, dict):
            return []

        forms = recent.get("form", [])
        accession_numbers = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        accept_times = recent.get("acceptanceDateTime", [])
        primary_docs = recent.get("primaryDocument", [])

        rows = [
            {
                "accessionNumber": accession,
                "filingDate": filing_date,
                "acceptanceDateTime": accepted,
                "primaryDocument": primary_doc,
            }
            for form, accession, filing_date, accepted, primary_doc in zip(
                forms, accession_numbers, filing_dates, accept_times, primary_docs,
                strict=False,
            )
            if form == "4"
        ]
        rows.sort(key=lambda r: r["filingDate"] or "", reverse=True)
        return rows[:limit]

    def _fetch_form4_xml(self, cik: int, accession: str, primary_document: str) -> str | None:
        """Fetch the raw Form 4 `ownershipDocument` XML for one filing.

        `primaryDocument` from submissions.json points at the XSLT-styled
        HTML view (e.g. `xslF345X06/wk-form4_123.xml`); the machine-
        readable XML with the same basename lives at the accession root.
        Cached indefinitely per accession, since a filed Form 4 never
        changes. Returns None on any fetch failure (never raises).
        """
        cik_key = _cik_cache_key(cik)
        cache_key = f"form4_xml_{accession}"
        age = self.cache.age_days(cik_key, cache_key)
        if age is not None and age <= _MAX_AGE_FORM4:
            cached = self.cache.get(cik_key, cache_key)
            return cached.get("xml") if isinstance(cached, dict) else None

        basename = primary_document.rsplit("/", 1)[-1]
        url = ARCHIVES_URL.format(
            cik=int(cik), accession=accession.replace("-", ""), document=basename
        )
        try:
            response = self.client.get(url, headers=_EDGAR_HEADERS)
        except httpx.TransportError:
            return None
        if response.status_code >= 400:
            return None

        self.cache.put(cik_key, cache_key, {"xml": response.text})
        return response.text

    def form4_transactions(self, cik: int, ticker: str, limit: int = _FORM4_FETCH_LIMIT) -> list[dict]:
        """Open-market insider buy/sell rows parsed from the most recent
        Form 4 filings, in the same shape as `FMPProvider.insider_trades`
        (`reportingName`, `transactionType`, `securitiesTransacted`,
        `price`, ...) so downstream code (e.g. `wbj.brief`'s >$1M
        highlights) works unchanged regardless of source.

        Non-derivative transactions only (options/derivatives excluded);
        every SEC transaction code is included (P/S/A/G/...) — callers
        that only care about open-market buys/sells already filter on
        `transactionType` starting with "P"/"S".
        """
        rows: list[dict] = []
        for filing in self.form4_filings(cik, limit=limit):
            xml_text = self._fetch_form4_xml(
                cik, filing["accessionNumber"], filing["primaryDocument"]
            )
            if not xml_text:
                continue
            rows.extend(
                _parse_form4_xml(
                    xml_text, ticker=ticker,
                    filing_date=filing["filingDate"],
                    accepted_date=filing["acceptanceDateTime"],
                )
            )

        rows.sort(key=lambda r: r.get("transactionDate") or "", reverse=True)
        return rows


def _text(el: ET.Element | None, path: str) -> str | None:
    """`el.find(path).text`, tolerating a missing element or empty text."""
    if el is None:
        return None
    found = el.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _number(el: ET.Element | None, path: str) -> float | None:
    value = _text(el, path)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_form4_xml(
    xml_text: str, *, ticker: str, filing_date: str | None, accepted_date: str | None
) -> list[dict]:
    """Parse one Form 4 `ownershipDocument` XML into FMP-shaped insider
    trade rows (non-derivative transactions only). Returns `[]` for
    malformed XML rather than raising — a single bad filing shouldn't
    break the whole packet build."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    owner = root.find("reportingOwner/reportingOwnerId/rptOwnerName")
    reporting_name = owner.text.strip() if owner is not None and owner.text else None

    rows: list[dict] = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(tx, "transactionCoding/transactionCode")
        shares = _number(tx, "transactionAmounts/transactionShares/value")
        price = _number(tx, "transactionAmounts/transactionPricePerShare/value")
        shares_owned = _number(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
        transaction_date = _text(tx, "transactionDate/value")

        rows.append({
            "symbol": ticker,
            "filingDate": filing_date,
            "transactionDate": transaction_date or filing_date,
            "reportingName": reporting_name,
            "transactionType": _TRANSACTION_CODE_LABELS.get(code, code or "?"),
            "securitiesTransacted": shares or 0,
            "price": price or 0,
            "securitiesOwned": shares_owned,
            "formType": "4",
            "acceptedDate": accepted_date,
            "source": "SEC_EDGAR",
        })

    return rows
