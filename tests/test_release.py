import json
import zipfile
import io
import pytest
from copy import deepcopy

from fastapi.testclient import TestClient
from app import main, store
from app.engine import evaluate
from app.config import ROOT
from app.release import build_gate

MIXED = (ROOT / 'sample_data/mixed.csv').read_text()
DAY = '2026-11-03'


@pytest.fixture
def mixed():
    return evaluate({'dataset': 'mixed'})


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.sqlite'))
    return TestClient(main.app)


# ---- 保留来源差异，不压平 ----

def test_legacy_layout_compatible():
    s = evaluate({'dataset': 'legacy'})
    assert s['layout'] == 'legacy'
    assert all(r['source'] == 'legacy' and r['legacy_compat'] for r in s['records'])
    assert len(s['records']) == 47


def test_legacy_records_not_lost_by_new_fields():
    s = evaluate({'dataset': 'legacy'})
    assert s['counts']['input'] == len(s['records']) + len(s['quarantine'])
    assert s['balance']['accepted'] == 47
    # 旧门店不做新凭证校验，也不能被要求补造渠道与原因。
    assert s['refund_evidence']
    assert all(r['code'] == 'LEGACY_NO_EVIDENCE' for r in s['refund_evidence'])


def test_combined_keeps_source_per_row(mixed):
    assert mixed['layout'] == 'combined'
    sources = {r['source'] for r in mixed['records']}
    assert sources == {'legacy', 'new'}
    assert {r['source'] for r in mixed['point_ledger']['lots']} == {'legacy', 'new'}


def test_source_summary_reconciles_to_total(mixed):
    rows = {r['source']: r for r in mixed['publish']['sources']}
    for field in ('issued', 'remaining', 'spent', 'refunded', 'expired'):
        assert rows['legacy'][field] + rows['new'][field] == rows['total'][field]
    assert rows['legacy']['issued'] == 1580 and rows['new']['issued'] == 250
    assert rows['total']['remaining'] == 1150


def test_mixed_balance_conserved(mixed):
    b = mixed['balance']
    assert b['issued'] == 1830 and b['remaining'] == 1150
    assert b['spent'] == 900 and b['refunded'] == 230 and b['expired'] == 10
    assert b['points_balanced'] and b['row_balanced']


# ---- 新格式退货可追渠道/原因/原消费 ----

def test_new_refund_evidence_traced(mixed):
    row = next(r for r in mixed['refund_evidence'] if r['id'] == 'NR1')
    assert row['source'] == 'new'
    assert row['channel'] == '客服'
    assert row['reason'] == '质量问题'
    assert row['ref'] == 'NS1' and row['original_points'] == 170
    assert row['traced'] and row['original_source'] == 'new'
    assert row['code'] == 'EVIDENCE_OK'
    assert mixed['evidence_issues'] == []


def test_missing_reason_blocks_publish():
    s = evaluate({'csv_text': MIXED.replace('客服,质量问题', '客服,')})
    assert any('EVIDENCE_MISSING' in code for code in s['evidence_issues'])
    assert s['publish']['publishable'] is False
    assert s['publish']['status'] == '待复核'
    # 凭证缺陷只阻断发布，不改变记账算术。
    assert s['balance']['points_balanced'] is True
    assert s['balance']['remaining'] == 1150


def test_bad_channel_blocks_publish():
    s = evaluate({'csv_text': MIXED.replace(',new,客服,质量问题', ',new,专卖店,质量问题')})
    assert any('EVIDENCE_CHANNEL' in code for code in s['evidence_issues'])
    assert s['publish']['publishable'] is False


def test_unresolvable_original_blocks_publish():
    s = evaluate({'csv_text': MIXED.replace(',NS1,常规,1,new,客服,质量问题', ',NX9,常规,1,new,客服,质量问题')})
    assert any('QUARANTINED:REFUND_REFERENCE' in code for code in s['evidence_issues'])
    assert s['publish']['publishable'] is False


# ---- 同一批积分重跑不重复记账 ----

def test_cross_source_duplicate_only_counted_once():
    duplicate = ('E1,00001,EARN,100,2026-10-01T09:00:00+08:00,'
                 '2026-11-01T00:00:00+08:00,,常规,1,new,,')
    s = evaluate({'csv_text': MIXED + duplicate + '\n'})
    assert s['balance']['issued'] == 1830
    assert s['balance']['remaining'] == 1150
    assert s['balance']['row_balanced'] is True
    assert [q['code'] for q in s['quarantine']] == ['CROSS_SOURCE_DUPLICATE']
    kept = next(r for r in s['records'] if r['id'] == 'E1')
    assert kept['source'] == 'new'


def test_rerun_keeps_digest(mixed):
    again = evaluate({'dataset': 'mixed'})
    assert again['manifest']['run_id'] == mixed['manifest']['run_id']
    assert again['balance'] == mixed['balance']


# ---- 发布与闸门 ----

def test_publish_requires_balance(mixed, tmp_path):
    s = deepcopy(mixed)
    s['balance']['points_balanced'] = False
    assert build_gate(s)['publishable'] is False


def test_publish_endpoint_ok(client):
    with client:
        run = client.post('/api/runs', json={'dataset': 'mixed'}).json()
        response = client.post(f"/api/runs/{run['manifest']['run_id']}/publish", json={})
        assert response.status_code == 200
        body = response.json()
        assert body['day'] == DAY
        assert body['release_revision'] == 1
        assert body['gate']['publishable'] is True
        assert body['balance']['remaining'] == 1150
        active = client.get(f'/api/releases/{DAY}/active').json()
        assert active['release_id'] == body['release_id']


def test_publish_blocked_when_gate_fails(client):
    with client:
        bad = MIXED.replace('客服,质量问题', '客服,')
        run = client.post('/api/runs', json={'csv_text': bad}).json()
        response = client.post(f"/api/runs/{run['manifest']['run_id']}/publish", json={})
        assert response.status_code == 409
        assert response.json()['code'] == 'RELEASE_BLOCKED'
        assert client.get(f'/api/releases/{DAY}/active').status_code == 404


def test_republish_same_run_is_idempotent(client):
    with client:
        run = client.post('/api/runs', json={'dataset': 'mixed'}).json()
        first = client.post(f"/api/runs/{run['manifest']['run_id']}/publish", json={}).json()
        rerun = client.post(f"/api/runs/{run['manifest']['run_id']}/rerun", json={}).json()
        assert rerun['manifest']['run_id'] == run['manifest']['run_id']
        second = client.post(f"/api/runs/{run['manifest']['run_id']}/publish", json={}).json()
        assert second['release_id'] == first['release_id']
        assert second['release_revision'] == 1
        history = client.get(f'/api/releases/{DAY}').json()['releases']
        assert len(history) == 1


def test_stale_publish_revision_rejected(client):
    with client:
        run = client.post('/api/runs', json={'dataset': 'mixed'}).json()
        rid = run['manifest']['run_id']
        assert client.post(f'/api/runs/{rid}/review', json={'revision': 1}).status_code == 200
        stale = client.post(f'/api/runs/{rid}/publish', json={'revision': 1})
        assert stale.status_code == 409
        assert stale.json()['code'] == 'STALE_VERSION'
        assert client.post(f'/api/runs/{rid}/publish', json={'revision': 2}).status_code == 200


def test_new_day_release_supersedes_old(client):
    with client:
        first = client.post('/api/runs', json={'dataset': 'mixed'}).json()
        client.post(f"/api/runs/{first['manifest']['run_id']}/publish", json={})
        second = client.post('/api/runs', json={'dataset': 'clean'}).json()
        body = client.post(f"/api/runs/{second['manifest']['run_id']}/publish", json={}).json()
        assert body['release_revision'] == 2
        history = client.get(f'/api/releases/{DAY}').json()['releases']
        statuses = {r['revision']: r['status'] for r in history}
        assert statuses == {1: 'superseded', 2: 'active'}


def test_interrupted_publish_keeps_old_release(client, monkeypatch):
    with client:
        first = client.post('/api/runs', json={'dataset': 'mixed'}).json()
        old = client.post(f"/api/runs/{first['manifest']['run_id']}/publish", json={}).json()
        second = client.post('/api/runs', json={'dataset': 'clean'}).json()

        real_activate = store._activate

        def broken(con, release_id, day):
            con.execute("INSERT INTO releases(id,day,run_id,digest,revision,payload,status,created_at) "
                        "VALUES('broken',?,?,?,9,'{}','staging','now')",
                        (day, second['manifest']['run_id'], second['manifest']['sha256']))
            raise RuntimeError('中断')

        monkeypatch.setattr(store, '_activate', broken)
        with pytest.raises(RuntimeError):
            store.publish(main.DB_PATH, store.get(main.DB_PATH, second['manifest']['run_id']),
                          {'day': DAY, 'run_id': second['manifest']['run_id'],
                           'sha256': second['manifest']['sha256'], 'gate': {'publishable': True},
                           'sources': [], 'refund_evidence': [], 'ledger': [], 'balance': {},
                           'revision': 1, 'rule_version': 'x', 'generated_at': 'x'})
        monkeypatch.setattr(store, '_activate', real_activate)
        active = client.get(f'/api/releases/{DAY}/active').json()
        assert active['release_id'] == old['release_id']
        assert active['release_revision'] == 1
        assert all(r['id'] != 'broken' for r in client.get(f'/api/releases/{DAY}').json()['releases'])


# ---- 页面与导出 ----

def test_pages_show_source_evidence_balance_status(client):
    with client:
        run = client.post('/api/runs', json={'dataset': 'mixed'}).json()
        rid = run['manifest']['run_id']
        for key in ('reconcile', 'refund', 'archive'):
            html = client.get(f'/{key}?run_id={rid}').text
            assert '来源' in html and '退货依据' in html
            assert '可用余额' in html or '会员余额' in html
            assert '可发布' in html


def test_export_contains_ledger_and_evidence(client):
    with client:
        run = client.post('/api/runs', json={'dataset': 'mixed'}).json()
        rid = run['manifest']['run_id']
        published = client.post(f'/api/runs/{rid}/publish', json={}).json()
        response = client.get(f"/api/export/{rid}")
        z = zipfile.ZipFile(io.BytesIO(response.content))
        assert {'manifest.json', 'evidence.json', 'ledger.csv', 'refund_evidence.csv'} <= set(z.namelist())
        ledger = z.read('ledger.csv').decode()
        evidence = z.read('refund_evidence.csv').decode()
        assert 'NR1' in ledger and 'new' in ledger
        assert '客服' in evidence and '质量问题' in evidence
        manifest = json.loads(z.read('manifest.json'))
        assert manifest['release']['day'] == DAY
        assert manifest['release']['release_id'] == published['release_id']


def test_source_visible_on_every_page(client):
    with client:
        run = client.post('/api/runs', json={'dataset': 'mixed'}).json()
        rid = run['manifest']['run_id']
        for key in main.MODULES:
            html = client.get(f'/{key}?run_id={rid}').text
            assert '新旧合并' in html
