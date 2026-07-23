"""Tests for wbj.providers.edgar.EdgarProvider."""

import json
from pathlib import Path

import httpx

from wbj.providers.cache import Cache
from wbj.providers.edgar import EDGAR_USER_AGENT, EdgarProvider

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "edgar"


def _load_fixture(name: str):
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text())


def _make_provider(tmp_path, handler):
    """Build an EdgarProvider wired to a MockTransport-backed httpx.Client."""
    cache = Cache(tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return EdgarProvider(settings=None, cache=cache, client=client)


def _capturing_handler(fixture_name, captured):
    """Return a handler that records the request and replies with a fixture."""

    def handler(request):
        captured.setdefault("requests", []).append(request)
        return httpx.Response(200, json=_load_fixture(fixture_name))

    return handler


# --- availability ------------------------------------------------------------


def test_available_is_always_true_no_api_key_needed(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json={}))
    assert p.available is True


# --- User-Agent header on every request --------------------------------------


def test_cik_for_sends_required_user_agent_header(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("tickers_sample", captured))

    p.cik_for("NVDA")

    req = captured["requests"][0]
    assert req.headers.get("user-agent") == EDGAR_USER_AGENT


def test_companyfacts_sends_required_user_agent_header(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("companyfacts_sample", captured))

    p.companyfacts(1045810)

    req = captured["requests"][0]
    assert req.headers.get("user-agent") == EDGAR_USER_AGENT


def test_filing_acceptance_times_sends_required_user_agent_header(tmp_path):
    captured = {}

    def handler(request):
        captured.setdefault("requests", []).append(request)
        return httpx.Response(
            200,
            json={
                "filings": {
                    "recent": {
                        "form": ["10-K"],
                        "acceptanceDateTime": ["2025-02-26T20:15:00.000Z"],
                        "accessionNumber": ["0001045810-25-000023"],
                    }
                }
            },
        )

    p = _make_provider(tmp_path, handler)
    p.filing_acceptance_times(1045810)

    req = captured["requests"][0]
    assert req.headers.get("user-agent") == EDGAR_USER_AGENT


# --- cik_for -------------------------------------------------------------


def test_cik_for_maps_nvda_to_correct_cik(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json=_load_fixture("tickers_sample")))

    assert p.cik_for("NVDA") == 1045810


def test_cik_for_is_case_insensitive(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json=_load_fixture("tickers_sample")))

    assert p.cik_for("nvda") == 1045810


def test_cik_for_unknown_ticker_returns_none(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json=_load_fixture("tickers_sample")))

    assert p.cik_for("ZZZZ") is None


def test_cik_for_uses_correct_url(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("tickers_sample", captured))

    p.cik_for("NVDA")

    req = captured["requests"][0]
    assert req.url.scheme == "https"
    assert req.url.host == "www.sec.gov"
    assert req.url.path == "/files/company_tickers.json"


def test_cik_for_shares_cache_across_different_tickers(tmp_path):
    """The tickers map is one global payload; looking up a second ticker
    must not re-hit the network if the first lookup already cached it."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=_load_fixture("tickers_sample"))

    p = _make_provider(tmp_path, handler)

    assert p.cik_for("NVDA") == 1045810
    assert p.cik_for("AAPL") == 320193
    assert calls["n"] == 1


# --- companyfacts ----------------------------------------------------------


def test_companyfacts_uses_correct_url(tmp_path):
    captured = {}
    p = _make_provider(tmp_path, _capturing_handler("companyfacts_sample", captured))

    p.companyfacts(1045810)

    req = captured["requests"][0]
    assert req.url.host == "data.sec.gov"
    assert req.url.path == "/api/xbrl/companyfacts/CIK0001045810.json"


def test_companyfacts_returns_payload(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json=_load_fixture("companyfacts_sample")))

    result = p.companyfacts(1045810)

    assert result == _load_fixture("companyfacts_sample")


def test_companyfacts_parses_shares_outstanding(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json=_load_fixture("companyfacts_sample")))

    facts = p.companyfacts(1045810)
    shares = facts["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"]

    latest = shares[-1]
    assert latest["val"] == 24390000000
    assert latest["accn"] == "0001045810-25-000023"
    assert latest["fy"] == 2024
    assert latest["fp"] == "FY"
    assert latest["form"] == "10-K"
    assert latest["filed"] == "2025-02-26"


def test_companyfacts_parses_us_gaap_revenue_concept(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json=_load_fixture("companyfacts_sample")))

    facts = p.companyfacts(1045810)
    revenues = facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"]

    latest = revenues[-1]
    assert latest["val"] == 130497000000
    assert latest["end"] == "2025-01-26"
    assert latest["form"] == "10-K"


def test_companyfacts_parses_us_gaap_cash_concept(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json=_load_fixture("companyfacts_sample")))

    facts = p.companyfacts(1045810)
    cash = facts["facts"]["us-gaap"]["CashAndCashEquivalentsAtCarryingValue"]["units"]["USD"]

    latest = cash[-1]
    assert latest["val"] == 8589000000


def test_companyfacts_returns_none_on_malformed_payload(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json=[1, 2, 3]))

    assert p.companyfacts(1045810) is None


# --- filing_acceptance_times -------------------------------------------------


def test_filing_acceptance_times_uses_correct_url(tmp_path):
    captured = {}

    def handler(request):
        captured.setdefault("requests", []).append(request)
        return httpx.Response(200, json={"filings": {"recent": {}}})

    p = _make_provider(tmp_path, handler)
    p.filing_acceptance_times(1045810)

    req = captured["requests"][0]
    assert req.url.host == "data.sec.gov"
    assert req.url.path == "/submissions/CIK0001045810.json"


def test_filing_acceptance_times_returns_form_accession_and_time(tmp_path):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "filings": {
                    "recent": {
                        "form": ["10-K", "10-Q"],
                        "acceptanceDateTime": [
                            "2025-02-26T20:15:00.000Z",
                            "2024-11-20T20:05:00.000Z",
                        ],
                        "accessionNumber": [
                            "0001045810-25-000023",
                            "0001045810-24-000123",
                        ],
                    }
                }
            },
        )

    p = _make_provider(tmp_path, handler)
    result = p.filing_acceptance_times(1045810)

    assert result == [
        {
            "form": "10-K",
            "acceptanceDateTime": "2025-02-26T20:15:00.000Z",
            "accessionNumber": "0001045810-25-000023",
        },
        {
            "form": "10-Q",
            "acceptanceDateTime": "2024-11-20T20:05:00.000Z",
            "accessionNumber": "0001045810-24-000123",
        },
    ]


def test_filing_acceptance_times_missing_filings_returns_none(tmp_path):
    p = _make_provider(tmp_path, lambda request: httpx.Response(200, json={}))

    assert p.filing_acceptance_times(1045810) is None


# --- caching: distinct keys, correct max_age_days ---------------------------


def test_cik_for_and_companyfacts_use_distinct_cache_entries(tmp_path):
    cache = Cache(tmp_path)

    def handler(request):
        if "company_tickers" in request.url.path:
            return httpx.Response(200, json=_load_fixture("tickers_sample"))
        return httpx.Response(200, json=_load_fixture("companyfacts_sample"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    p = EdgarProvider(settings=None, cache=cache, client=client)

    p.cik_for("NVDA")
    p.companyfacts(1045810)

    # tickers map cached under a fixed global key regardless of ticker
    assert cache.get("_GLOBAL", "tickers") == _load_fixture("tickers_sample")
    assert cache.get("CIK0001045810", "companyfacts") == _load_fixture("companyfacts_sample")


# --- form4_filings -----------------------------------------------------------

_SUBMISSIONS_WITH_FORM4 = {
    "filings": {
        "recent": {
            "form": ["10-K", "4", "4"],
            "accessionNumber": [
                "0001045810-25-000023",
                "0001197647-26-000005",
                "0001197647-26-000001",
            ],
            "filingDate": ["2025-02-26", "2026-07-06", "2026-01-02"],
            "acceptanceDateTime": [
                "2025-02-26T20:15:00.000Z",
                "2026-07-06T18:00:00.000Z",
                "2026-01-02T18:00:00.000Z",
            ],
            "primaryDocument": [
                "nvda-10k.htm",
                "xslF345X06/wk-form4_1.xml",
                "xslF345X06/wk-form4_2.xml",
            ],
        }
    }
}

_FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
    <reportingOwner>
        <reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <transactionDate><value>2026-07-01</value></transactionDate>
            <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
            <transactionAmounts>
                <transactionShares><value>10000</value></transactionShares>
                <transactionPricePerShare><value>150.5</value></transactionPricePerShare>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction><value>90000</value></sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>"""


def _routed_handler(submissions=None, xml_by_path=None):
    submissions = submissions or _SUBMISSIONS_WITH_FORM4
    xml_by_path = xml_by_path or {}

    def handler(request):
        if "submissions" in request.url.path:
            return httpx.Response(200, json=submissions)
        for path, xml in xml_by_path.items():
            if path in str(request.url):
                return httpx.Response(200, text=xml)
        return httpx.Response(404, text="not found")

    return handler


def test_form4_filings_filters_to_form_4_only_newest_first(tmp_path):
    p = _make_provider(tmp_path, _routed_handler())

    filings = p.form4_filings(1045810)

    assert [f["accessionNumber"] for f in filings] == [
        "0001197647-26-000005",
        "0001197647-26-000001",
    ]


def test_form4_filings_respects_limit(tmp_path):
    p = _make_provider(tmp_path, _routed_handler())

    filings = p.form4_filings(1045810, limit=1)

    assert len(filings) == 1
    assert filings[0]["accessionNumber"] == "0001197647-26-000005"


def test_form4_filings_sends_user_agent(tmp_path):
    captured = {}

    def handler(request):
        captured.setdefault("requests", []).append(request)
        return httpx.Response(200, json=_SUBMISSIONS_WITH_FORM4)

    p = _make_provider(tmp_path, handler)
    p.form4_filings(1045810)

    assert captured["requests"][0].headers.get("user-agent") == EDGAR_USER_AGENT


# --- form4_transactions -------------------------------------------------------


def test_form4_transactions_parses_open_market_sale(tmp_path):
    handler = _routed_handler(
        submissions={
            "filings": {
                "recent": {
                    "form": ["4"],
                    "accessionNumber": ["0001197647-26-000005"],
                    "filingDate": ["2026-07-06"],
                    "acceptanceDateTime": ["2026-07-06T18:00:00.000Z"],
                    "primaryDocument": ["xslF345X06/wk-form4_1.xml"],
                }
            }
        },
        xml_by_path={"wk-form4_1.xml": _FORM4_XML},
    )
    p = _make_provider(tmp_path, handler)

    rows = p.form4_transactions(1045810, "NVDA")

    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "NVDA"
    assert row["reportingName"] == "DOE JANE"
    assert row["transactionType"] == "S-Sale"
    assert row["securitiesTransacted"] == 10000
    assert row["price"] == 150.5
    assert row["securitiesOwned"] == 90000
    assert row["transactionDate"] == "2026-07-01"
    assert row["formType"] == "4"
    assert row["source"] == "SEC_EDGAR"


def test_form4_transactions_caches_xml_and_does_not_refetch(tmp_path):
    calls = {"xml": 0}

    def handler(request):
        if "submissions" in request.url.path:
            return httpx.Response(200, json=_SUBMISSIONS_WITH_FORM4)
        calls["xml"] += 1
        return httpx.Response(200, text=_FORM4_XML)

    p = _make_provider(tmp_path, handler)

    p.form4_transactions(1045810, "NVDA")
    p.form4_transactions(1045810, "NVDA")

    assert calls["xml"] == 2  # one fetch per distinct Form 4 filing found (2 filings), not per call


def test_form4_transactions_skips_filing_on_xml_fetch_failure(tmp_path):
    def handler(request):
        if "submissions" in request.url.path:
            return httpx.Response(200, json=_SUBMISSIONS_WITH_FORM4)
        return httpx.Response(500, text="server error")

    p = _make_provider(tmp_path, handler)

    assert p.form4_transactions(1045810, "NVDA") == []


def test_form4_transactions_malformed_xml_returns_empty_for_that_filing(tmp_path):
    handler = _routed_handler(
        submissions={
            "filings": {
                "recent": {
                    "form": ["4"],
                    "accessionNumber": ["0001197647-26-000005"],
                    "filingDate": ["2026-07-06"],
                    "acceptanceDateTime": ["2026-07-06T18:00:00.000Z"],
                    "primaryDocument": ["xslF345X06/wk-form4_1.xml"],
                }
            }
        },
        xml_by_path={"wk-form4_1.xml": "<not><valid xml"},
    )
    p = _make_provider(tmp_path, handler)

    assert p.form4_transactions(1045810, "NVDA") == []
