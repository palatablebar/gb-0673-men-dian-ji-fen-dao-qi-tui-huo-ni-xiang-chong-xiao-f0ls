"""来源合并、发布闸门与可发布账载荷。

旧门店只提供积分、时间与原单号；新门店额外提供兑换渠道与退货原因。
这里不做字段压平：来源作为一等字段贯穿台账，旧字段以兼容标记补齐，
新门店退货依据独立校验，任一项不通过都不能发布。
"""
from datetime import datetime, timezone, timedelta

from app.config import SOURCE_LABELS
from app.errors import BusinessError

DAY_TZ = timezone(timedelta(hours=8))


def business_day(as_of):
    try:
        value = datetime.fromisoformat(str(as_of).replace('Z', '+00:00'))
        if value.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError):
        raise BusinessError('BAD_TIME', '结算时点缺少有效偏移')
    return value.astimezone(DAY_TZ).date().isoformat()


def source_summary(state):
    ledger = state.get('point_ledger') or {}
    lots = ledger.get('lots', [])
    spends = ledger.get('spends', [])
    refunds = ledger.get('refunds', [])
    expiry = {}
    for entry in ledger.get('ledger', []):
        if entry.get('action') == 'EXPIRE':
            src = entry.get('source', 'legacy')
            expiry[src] = expiry.get(src, 0) + entry.get('points', 0)
    rows = []
    for source in ('legacy', 'new'):
        source_lots = [x for x in lots if x.get('source', 'legacy') == source]
        source_spends = [x for x in spends if x.get('source', 'legacy') == source]
        source_refunds = [x for x in refunds if x.get('source', 'legacy') == source]
        rows.append({
            'source': source,
            'source_label': SOURCE_LABELS[source],
            'issued': sum(x['issued'] for x in source_lots),
            'remaining': sum(x['remaining'] for x in source_lots),
            'spent': sum(x['points'] for x in source_spends),
            'refunded': sum(x['points'] for x in source_refunds),
            'expired': expiry.get(source, 0) + sum(x['expired_refund'] for x in source_refunds),
        })
    rows.append({
        'source': 'total',
        'source_label': '合计',
        'issued': sum(x['issued'] for x in rows),
        'remaining': sum(x['remaining'] for x in rows),
        'spent': sum(x['spent'] for x in rows),
        'refunded': sum(x['refunded'] for x in rows),
        'expired': sum(x['expired'] for x in rows),
    })
    return rows


def build_gate(state):
    balance = state.get('balance') or {}
    refund_issues = state.get('refund_issues') or []
    evidence_issues = state.get('evidence_issues') or []
    checks = [
        {'code': 'ROW_BALANCED', 'label': '行守恒', 'ok': bool(balance.get('row_balanced')),
         'detail': '输入%d = 采纳%d + 隔离%d + 其余' % (balance.get('input', 0), balance.get('accepted', 0), balance.get('quarantined', 0))},
        {'code': 'POINTS_BALANCED', 'label': '积分守恒', 'ok': bool(balance.get('points_balanced')),
         'detail': '发放%d = 可用%d + 消费%d − 退款%d + 到期%d' % (balance.get('issued', 0), balance.get('remaining', 0), balance.get('spent', 0), balance.get('refunded', 0), balance.get('expired', 0))},
        {'code': 'REFUND_ARITHMETIC', 'label': '退货回拨', 'ok': not refund_issues,
         'detail': '全部回拨批次可复核' if not refund_issues else '未通过：' + '、'.join(refund_issues)},
        {'code': 'REFUND_EVIDENCE', 'label': '新门店退货依据', 'ok': not evidence_issues,
         'detail': '渠道、原因、原消费均可追' if not evidence_issues else '依据缺失：' + '、'.join(evidence_issues)},
    ]
    publishable = all(item['ok'] for item in checks)
    return {
        'day': business_day(state['context']['as_of']),
        'status': '可发布' if publishable else '待复核',
        'publishable': publishable,
        'checks': checks,
        'sources': state.get('source_summary') or source_summary(state),
        'refund_evidence': state.get('refund_evidence', []),
    }


def release_payload(result):
    """不可变发布快照：只包含已通过闸门的账与证据。"""
    gate = result.get('publish') or {}
    return {
        'day': gate.get('day'),
        'run_id': result['manifest']['run_id'],
        'revision': result.get('revision', 1),
        'sha256': result['manifest']['sha256'],
        'rule_version': result['manifest']['rule_version'],
        'generated_at': result['manifest']['generated_at'],
        'gate': {'status': gate.get('status'), 'publishable': gate.get('publishable'), 'checks': gate.get('checks', [])},
        'balance': result.get('balance', {}),
        'sources': gate.get('sources', []),
        'refund_evidence': gate.get('refund_evidence', []),
        'ledger': result.get('merged_ledger', []),
    }
