from importlib import import_module
from copy import deepcopy
import json, time, base64
from app.config import ROOT, MODULES, MAX_BYTES, DATASETS
from app.errors import BusinessError
from app.manifest import digest, make_manifest


def _sample_bytes(dataset):
    path = ROOT / 'sample_data' / f'{dataset}.csv'
    if not path.exists():
        raise BusinessError('BAD_DATASET', '样例名称无效')
    return path.read_bytes()


def _merged_ledger(state):
    """合并账：旧门店与新门店同列并排，来源与退货依据随行保留。"""
    rows = []
    ledger = state.get('point_ledger') or {}
    for lot in ledger.get('lots', []):
        rows.append({
            'member': lot['member'], 'source': lot.get('source', 'legacy'), 'id': lot['id'],
            'action': 'EARN', 'issued': lot['issued'], 'remaining': lot['remaining'],
            'expires': lot['expires'], 'channel': lot.get('channel', ''),
            'reason': '', 'ref': '', 'legacy_compat': lot.get('legacy_compat', True),
        })
    for spend in ledger.get('spends', []):
        rows.append({
            'member': spend['member'], 'source': spend.get('source', 'legacy'), 'id': spend['id'],
            'action': 'SPEND', 'issued': 0, 'remaining': -spend['points'],
            'expires': '', 'channel': spend.get('channel', ''),
            'reason': '', 'ref': '', 'legacy_compat': spend.get('source', 'legacy') == 'legacy',
        })
    for refund in ledger.get('refunds', []):
        rows.append({
            'member': refund['member'], 'source': refund.get('source', 'legacy'), 'id': refund['id'],
            'action': 'REFUND', 'issued': 0, 'remaining': refund['restored'],
            'expires': '', 'channel': refund.get('channel', ''), 'reason': refund.get('reason', ''),
            'ref': refund.get('ref', ''), 'legacy_compat': refund.get('source', 'legacy') == 'legacy',
        })
    order = {'EARN': 0, 'SPEND': 1, 'REFUND': 2}
    return sorted(rows, key=lambda r: (r['member'], r['source'], order[r['action']], r['id']))


def evaluate(payload=None, until=7, snapshot=None):
    payload = payload or {}
    dataset = payload.get('dataset', 'dirty')
    if dataset not in DATASETS:
        raise BusinessError('BAD_DATASET', '样例名称无效')
    if not isinstance(payload.get('overrides', {}), dict):
        raise BusinessError('BAD_CONTEXT', '参数须为对象')
    if snapshot is not None:
        raw = base64.b64decode(snapshot['bytes_b64'], validate=True)
    elif 'csv_text' in payload:
        raw = str(payload['csv_text']).encode()
    else:
        raw = _sample_bytes(dataset)
    if len(raw) > MAX_BYTES:
        raise BusinessError('TOO_LARGE', '文件超过2MB', 413)
    context = deepcopy(snapshot['context']) if snapshot is not None else json.loads(
        (ROOT / 'sample_data/context.json').read_text())
    context.update(deepcopy(payload.get('overrides', {})))
    start = time.perf_counter()
    state = {'raw_bytes': raw, 'context': context, 'records': [], 'quarantine': [],
             'counts': {}, 'views': {}, 'source': dataset}
    for index, key in enumerate(MODULES, 1):
        if index > until:
            break
        import_module(f'app.modules.{key}.rules').apply(state)
    state['input_snapshot'] = {'bytes_b64': base64.b64encode(raw).decode(), 'context': deepcopy(context)}
    state.pop('raw_bytes', None)
    state['manifest'] = make_manifest(digest(raw, context), dataset, time.perf_counter() - start)
    if until >= MODULES.index('archive') + 1:
        from app.release import build_gate
        state['merged_ledger'] = _merged_ledger(state)
        state['publish'] = build_gate(state)
    return state
