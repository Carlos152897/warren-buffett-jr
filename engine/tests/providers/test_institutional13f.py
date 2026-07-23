"""Tests for wbj.providers.institutional13f.Form13FProvider."""

import io
import zipfile
from pathlib import Path

import httpx

from wbj.providers.cache import Cache
from wbj.providers.institutional13f import EDGAR_USER_AGENT, Form13FProvider

_COVERPAGE = (
    "ACCESSION_NUMBER\tREPORTCALENDARORQUARTER\tFILINGMANAGER_NAME\n"
    "0001-A\t31-MAR-2026\tVanguard Group Inc\n"
    "0001-B\t31-MAR-2026\tBlackRock Inc\n"
    "0001-C\t31-MAR-2026\tSmall Fund LLC\n"
)

_INFOTABLE = (
    "ACCESSION_NUMBER\tNAMEOFISSUER\tCUSIP\tVALUE\tSSHPRNAMT\n"
    "0001-A\tNVIDIA CORP\t67066G104\t500000\t3000\n"
    "0001-B\tNVIDIA CORP\t67066G104\t300000\t2000\n"
    "0001-B\tNVIDIA CORP\t67066G104\t50000\t100\n"  # second lot, same filer
    "0001-C\tAPPLE INC\t037833100\t900000\t5000\n"  # different CUSIP, must be excluded
)


def _build_dataset_zip(path: Path, coverpage: str = _COVERPAGE, infotable: str = _INFOTABLE) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("COVERPAGE.tsv", coverpage)
        z.writestr("INFOTABLE.tsv", infotable)


def _make_provider(tmp_path, handler=None):
    cache = Cache(tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(handler or (lambda r: httpx.Response(404))))
    return Form13FProvider(settings=None, cache=cache, client=client)


# --- availability --------------------------------------------------------


def test_available_is_always_true(tmp_path):
    p = _make_provider(tmp_path)
    assert p.available is True


# --- holders: cusip/dataset guard clauses --------------------------------


def test_holders_returns_none_when_cusip_missing(tmp_path):
    p = _make_provider(tmp_path)
    assert p.holders(None, "NVDA") is None
    assert p.holders("", "NVDA") is None


def test_holders_returns_none_when_dataset_unavailable(tmp_path, monkeypatch):
    p = _make_provider(tmp_path)
    monkeypatch.setattr(p, "_current_dataset_path", lambda: None)
    assert p.holders("67066G104", "NVDA") is None


# --- holders: aggregation/ranking -----------------------------------------


def test_holders_aggregates_by_filer_and_ranks_by_value(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset.zip"
    _build_dataset_zip(dataset)
    p = _make_provider(tmp_path)
    monkeypatch.setattr(p, "_current_dataset_path", lambda: dataset)

    result = p.holders("67066G104", "NVDA")

    assert result == [
        {"holder": "Vanguard Group Inc", "shares": 3000, "dateReported": "2026-03-31",
         "change": None, "symbol": "NVDA"},
        {"holder": "BlackRock Inc", "shares": 2100, "dateReported": "2026-03-31",
         "change": None, "symbol": "NVDA"},
    ]
    # Apple's row (different CUSIP) and Small Fund LLC must not leak in.
    assert all(row["holder"] != "Small Fund LLC" for row in result)


def test_holders_returns_empty_list_when_cusip_not_in_dataset(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset.zip"
    _build_dataset_zip(dataset)
    p = _make_provider(tmp_path)
    monkeypatch.setattr(p, "_current_dataset_path", lambda: dataset)

    assert p.holders("00000A000", "GHOST") == []


def test_holders_caches_result_and_skips_dataset_lookup_on_second_call(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset.zip"
    _build_dataset_zip(dataset)
    p = _make_provider(tmp_path)
    calls = {"n": 0}

    def fake_path():
        calls["n"] += 1
        return dataset

    monkeypatch.setattr(p, "_current_dataset_path", fake_path)

    first = p.holders("67066G104", "NVDA")
    second = p.holders("67066G104", "NVDA")

    assert first == second
    assert calls["n"] == 1


def test_holders_malformed_zip_returns_none(tmp_path, monkeypatch):
    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_text("not a zip file")
    p = _make_provider(tmp_path)
    monkeypatch.setattr(p, "_current_dataset_path", lambda: bad_zip)

    assert p.holders("67066G104", "NVDA") is None


# --- dataset discovery/download -------------------------------------------


def test_latest_dataset_href_parses_first_zip_link(tmp_path):
    index_html = (
        '<a href="/files/structureddata/data/form-13f-data-sets/'
        '01mar2026-31may2026_form13f.zip">latest</a>'
        '<a href="/files/structureddata/data/form-13f-data-sets/'
        '2023q4_form13f.zip">older</a>'
    )
    p = _make_provider(tmp_path, lambda r: httpx.Response(200, text=index_html))

    href = p._latest_dataset_href()

    assert href == "/files/structureddata/data/form-13f-data-sets/01mar2026-31may2026_form13f.zip"


def test_latest_dataset_href_sends_user_agent(tmp_path):
    captured = {}

    def handler(request):
        captured["request"] = request
        return httpx.Response(200, text="")

    p = _make_provider(tmp_path, handler)
    p._latest_dataset_href()

    assert captured["request"].headers.get("user-agent") == EDGAR_USER_AGENT


def test_current_dataset_path_downloads_and_caches_to_disk(tmp_path):
    fake_zip_bytes = io.BytesIO()
    with zipfile.ZipFile(fake_zip_bytes, "w") as z:
        z.writestr("COVERPAGE.tsv", _COVERPAGE)
        z.writestr("INFOTABLE.tsv", _INFOTABLE)
    zip_bytes = fake_zip_bytes.getvalue()

    index_html = (
        '<a href="/files/structureddata/data/form-13f-data-sets/test_window.zip">latest</a>'
    )

    def handler(request):
        if "form-13f-data-sets/test_window.zip" in str(request.url):
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(200, text=index_html)

    p = _make_provider(tmp_path, handler)

    path = p._current_dataset_path()

    assert path is not None
    assert path.name == "test_window.zip"
    assert path.read_bytes() == zip_bytes


def test_current_dataset_path_reuses_existing_local_file_without_refetching(tmp_path):
    calls = {"n": 0}
    index_html = (
        '<a href="/files/structureddata/data/form-13f-data-sets/test_window.zip">latest</a>'
    )

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, text=index_html)

    p = _make_provider(tmp_path, handler)
    dataset_dir = p._dataset_dir()
    existing = dataset_dir / "test_window.zip"
    existing.write_bytes(b"already-downloaded")

    path = p._current_dataset_path()

    assert path == existing
    assert path.read_bytes() == b"already-downloaded"
    assert calls["n"] == 1  # only the index page, never the (large) zip itself


def test_current_dataset_path_deletes_stale_prior_windows(tmp_path):
    fake_zip_bytes = io.BytesIO()
    with zipfile.ZipFile(fake_zip_bytes, "w") as z:
        z.writestr("COVERPAGE.tsv", _COVERPAGE)
    zip_bytes = fake_zip_bytes.getvalue()

    index_html = (
        '<a href="/files/structureddata/data/form-13f-data-sets/new_window.zip">latest</a>'
    )

    def handler(request):
        if "new_window.zip" in str(request.url):
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(200, text=index_html)

    p = _make_provider(tmp_path, handler)
    dataset_dir = p._dataset_dir()
    stale = dataset_dir / "old_window.zip"
    stale.write_bytes(b"stale-data")

    p._current_dataset_path()

    assert not stale.exists()
    assert (dataset_dir / "new_window.zip").exists()


def test_latest_dataset_href_returns_none_on_no_match(tmp_path):
    p = _make_provider(tmp_path, lambda r: httpx.Response(200, text="<html>nothing here</html>"))
    assert p._latest_dataset_href() is None


def test_current_dataset_path_returns_none_on_index_fetch_failure(tmp_path):
    p = _make_provider(tmp_path, lambda r: httpx.Response(500))
    assert p._current_dataset_path() is None
