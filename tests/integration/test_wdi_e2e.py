from datumoj.rikolto import wiring
from datumoj.rikolto.parsers.wdi import WDIParser
from datumoj.rikolto.testing.fakes import FakeRegistry
from tests.crawlers.test_wdi import SPEC
from tests.parsers.test_wdi import sample, single_page


def test_wdi_sql_citations(tmp_path):
    deps = wiring.build(tmp_path, {'registry': FakeRegistry([SPEC]), 'vectors': None})
    try:
        data = single_page()
        s = sample(data)
        deps.raw.put(data)
        deps.manifest.record_snapshot(s.snapshot, s.meta)
        result = WDIParser().parse(s.snapshot, data, s.meta)
        for code in {o.table_key for o in result.observations}:
            deps.observations.write_table(SPEC.id, code, [o for o in result.observations if o.table_key == code])
        sql = "SELECT area_code, value, obs_id FROM observations WHERE table_key = 'NY.GDP.MKTP.CD' AND time_label = '2023' ORDER BY area_code"
        answer = deps.tools.query_sql(sql)['data']
        assert [row[0]['content'] for row in answer['rows']] == ['JPN', 'USA']
        assert len(answer['provenance']) == 2
        assert all(p['citation']['snapshot_sha256'] == s.snapshot.sha256 for p in answer['provenance'])
    finally:
        for name in ('manifest', 'chunks', 'keyword', 'catalog', 'change_log'):
            getattr(deps, name).close()
