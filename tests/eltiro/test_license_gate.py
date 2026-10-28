"""The gate accepts registry authority only, independent of cached license copies."""

from datetime import date
from decimal import Decimal

import pytest

from datumoj.eltiro.license_gate import LicenseGateError, check_rows
from datumoj.eltiro.testing.samples import make_demand
from tests.eltiro.test_executor import consumer
from tests.eltiro.test_package import MARKER, sources


def test_gate_checks_all_inputs_and_allowlists_columns():
    rows = [{"citation_id": "row-0", "symbol": "gdp", "period": date(2020, 1, 1),
             "value": Decimal("1.25"), "note": MARKER, "series": MARKER, "unit": MARKER,
             "future_text_column": MARKER, "license": {"outflow": "raw_ok"}}]
    inputs = [{"source_id": source, "table_key": "table", "obs_id": "obs", "release_id": "R1", "snapshot_sha256": "ab" * 32}
              for source in ("intl-wdi", "intl-imf-weo")]
    citations = [{"citation_id": "row-0", "citation": {"inputs": inputs}, "steps": [MARKER]}]
    demand = make_demand(accept_outflow=["derived_only"])
    config = consumer(("intl-wdi", "intl-imf-weo"), accept_outflow=["derived_only"])
    rows, index, summary = check_rows(rows, citations, registry=sources({"intl-wdi": "raw_ok", "intl-imf-weo": "derived_only"}),
                                      demand=demand, consumer=config)
    assert rows[0] == {"citation_id": "row-0", "symbol": "gdp", "period": date(2020, 1, 1), "value": Decimal("1.25")}
    assert len(index["row-0"].inputs) == 2 and summary.outflow == "derived_only"
    with pytest.raises(LicenseGateError) as exc:
        check_rows(rows, citations, registry=sources({"intl-wdi": "raw_ok"}), demand=demand, consumer=config)
    assert exc.value.code == "no_outflow" and exc.value.sources == ("intl-imf-weo",)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "repeated_row"])
def test_gate_refuses_incomplete_or_ambiguous_citations(kind):
    rows = [{"citation_id": "row-0"}]
    citations = []
    if kind != "missing":
        citations = [{"citation_id": "row-0", "citation": {"inputs": [{"source_id": "intl-wdi", "table_key": "t",
                      "obs_id": "o", "snapshot_sha256": "ab" * 32, "release_id": "R"}]}}]
        if kind == "duplicate":
            citations *= 2
        else:
            rows *= 2
    with pytest.raises(LicenseGateError, match="invalid_lineage"):
        check_rows(rows, citations, registry=sources({"intl-wdi": "raw_ok"}),
                   demand=make_demand(accept_outflow=["raw_ok"]), consumer=consumer(accept_outflow=["raw_ok"]))
