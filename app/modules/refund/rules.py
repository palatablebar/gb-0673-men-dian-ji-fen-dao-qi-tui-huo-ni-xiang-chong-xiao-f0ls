from app.errors import BusinessError
from app.evidence import channel, reason
from app.config import SOURCE_LABELS

REQUIRED_EVIDENCE = ('channel', 'reason', 'ref')


def audit_refunds(ledger):
    spends = {}
    for spend in ledger['spends']:
        spends[(spend.get('source', 'legacy'), spend['id'])] = spend
        spends.setdefault(spend['id'], spend)
    issues = []
    rows = []
    for refund in ledger['refunds']:
        original = spends.get((refund.get('source', 'legacy'), refund.get('ref'))) \
            or spends.get(refund.get('ref'))
        allocated = sum(x['points'] for x in refund['allocations'])
        valid = (original is not None and allocated == refund['points']
                 and refund['restored'] + refund['expired_refund'] == refund['points']
                 and original['refunded'] <= original['points'])
        row = {k: v for k, v in refund.items() if k != 'allocations'}
        row.update({
            'original_points': original['points'] if original else None,
            'original_member': original['member'] if original else '',
            'code': 'VERIFIED' if valid else 'REFUND_UNBALANCED',
        })
        rows.append(row)
        if not valid:
            issues.append(row['id'])
    return rows, issues


def refund_evidence(state):
    """逐笔核验新门店退货依据；旧门店记录标记兼容而不是伪造依据。"""
    spends = {(s.get('source', 'legacy'), s['id']): s for s in state['point_ledger']['spends']}
    issues = []
    rows = []
    for refund in state['point_ledger']['refunds']:
        source = refund.get('source', 'legacy')
        ref = refund.get('ref', '')
        original = spends.get((source, ref))
        if original is None:
            candidates = [s for s in state['point_ledger']['spends']
                          if s['id'] == ref and s['member'] == refund['member']]
            original = candidates[0] if len(candidates) == 1 else None
        row = {
            'id': refund['id'], 'source': source, 'source_label': SOURCE_LABELS.get(source, source),
            'member': refund['member'], 'ref': ref, 'points': refund['points'],
            'original_points': original['points'] if original else None,
            'original_at': original['at'] if original else '',
            'original_source': original.get('source', 'legacy') if original else '',
            'channel': refund.get('channel', ''), 'reason': refund.get('reason', ''),
            'traced': original is not None,
        }
        if source == 'new':
            missing = []
            try:
                row['channel'] = channel(refund.get('channel'))
            except BusinessError as error:
                missing.append(error.code)
                row['channel'] = refund.get('channel', '') or '（缺失）'
            try:
                row['reason'] = reason(refund.get('reason'))
            except BusinessError as error:
                missing.append(error.code)
                row['reason'] = refund.get('reason', '') or '（缺失）'
            if original is None:
                missing.append('EVIDENCE_ORIGINAL')
            elif original.get('source') == 'new':
                # 原消费同为新门店时，渠道必须同时可读，证据链才算闭合。
                try:
                    row['original_channel'] = channel(original.get('channel'))
                except BusinessError:
                    missing.append('EVIDENCE_ORIGINAL_CHANNEL')
                    row['original_channel'] = original.get('channel', '') or '（缺失）'
            row['code'] = 'EVIDENCE_OK' if not missing else ','.join(missing)
            row['legacy_compat'] = False
            issues.extend(f'{refund["id"]}:{code}' for code in missing)
        else:
            row['code'] = 'LEGACY_NO_EVIDENCE'
            row['legacy_compat'] = True
        rows.append(row)
    # 被记账阶段隔离的新门店退货仍然要留证：不能因为没进账就从依据里消失。
    for item in state['quarantine']:
        raw = item.get('raw') or {}
        if raw.get('source') != 'new' or raw.get('type') != 'REFUND' or item['code'] == 'CROSS_SOURCE_DUPLICATE':
            continue
        rows.append({
            'id': item.get('id', ''), 'source': 'new', 'source_label': SOURCE_LABELS['new'],
            'member': raw.get('member', ''), 'ref': raw.get('ref', ''), 'points': raw.get('points', ''),
            'original_points': None, 'original_at': '', 'original_source': '',
            'channel': raw.get('channel', '') or '（缺失）', 'reason': raw.get('reason', '') or '（缺失）',
            'traced': False, 'legacy_compat': False,
            'code': f"QUARANTINED:{item['code']}",
        })
        issues.append(f"{item.get('id', '')}:QUARANTINED:{item['code']}")
    return rows, issues


def apply(state):
    rows, issues = audit_refunds(state['point_ledger'])
    state['refund_audit'] = rows
    state['refund_issues'] = issues
    evidence_rows, evidence_issues = refund_evidence(state)
    state['refund_evidence'] = evidence_rows
    state['evidence_issues'] = evidence_issues
    state['views']['refund'] = {
        'title': '退货回拨',
        'rows': rows,
        'evidence': evidence_rows,
        'metrics': {'退款笔': len(rows), '恢复积分': sum(x['restored'] for x in rows),
                    '过期不恢复': sum(x['expired_refund'] for x in rows),
                    '依据待补': len(evidence_issues)},
    }
