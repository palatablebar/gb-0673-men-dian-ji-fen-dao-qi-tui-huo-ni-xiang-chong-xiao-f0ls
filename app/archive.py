import io, csv, json, zipfile
from app.errors import BusinessError


def _csv(rows, fields):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction='ignore', lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, '') for k in fields})
    return buffer.getvalue()


def serialize(result, release=None):
    if not result.get('manifest') or not result.get('views'):
        raise BusinessError('INCOMPLETE_RUN', '批次尚未完成')
    publish = result.get('publish') or {}
    merged = result.get('merged_ledger') or []
    ledger_fields = ['member', 'source', 'id', 'action', 'issued', 'remaining', 'expires',
                     'channel', 'reason', 'ref', 'legacy_compat']
    evidence_fields = ['id', 'source', 'source_label', 'member', 'ref', 'points',
                       'original_points', 'original_at', 'original_source', 'channel', 'reason',
                       'traced', 'legacy_compat', 'code']
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(
            {**result['manifest'], 'revision': result.get('revision', 1),
             'status': result.get('status', '待复核'),
             'release': {'day': publish.get('day'), 'gate': publish.get('status'),
                         'publishable': publish.get('publishable', False),
                         'release_id': (release or {}).get('release_id'),
                         'release_revision': (release or {}).get('release_revision'),
                         'published_at': (release or {}).get('published_at')}},
            ensure_ascii=False, indent=2))
        archive.writestr('evidence.json', json.dumps(
            {'counts': result['counts'], 'views': result['views'],
             'quarantine': result['quarantine'],
             'publish': publish}, ensure_ascii=False, indent=2))
        # 原始行原样保留，兼容补列不覆盖旧字段，确保可追溯。
        archive.writestr('ledger.csv', _csv(merged, ledger_fields))
        archive.writestr('refund_evidence.csv', _csv(
            publish.get('refund_evidence', []), evidence_fields))
    return buffer.getvalue()
