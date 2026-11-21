from app.coerce import instant
from app.reasons import reject


def _key(source, ident):
    return (source, ident)


def replay(rows, lots, as_of):
    live = {_key(r.get('source', 'legacy'), r['id']): dict(r, remaining=0) for r in lots}
    spends = {}
    refunds = []
    ledger = []
    errors = []
    expired = 0
    priority = {'EARN': 0, 'SPEND': 1, 'REFUND': 2}

    def find_spend(source, ref, member):
        """先按同来源原单号精确匹配；跨来源时仅在全局唯一且同会员时兜底。"""
        exact = spends.get(_key(source, ref))
        if exact is not None:
            return exact
        candidates = [s for s in spends.values() if s['id'] == ref and s['member'] == member]
        return candidates[0] if len(candidates) == 1 else None

    def expire(at):
        nonlocal expired
        for lot in live.values():
            if instant(lot['expires']) <= at and lot['remaining']:
                amount = lot['remaining']
                lot['remaining'] = 0
                expired += amount
                ledger.append({'id': lot['id'], 'member': lot['member'], 'action': 'EXPIRE',
                               'points': amount, 'at': at.isoformat(), 'source': lot.get('source', 'legacy')})

    ordered = sorted(rows, key=lambda r: (instant(r['at']), priority[r['type']], r.get('source', 'legacy'), r['id']))
    for row in ordered:
        at = instant(row['at'])
        expire(at)
        member = row['member']
        amount = row['points']
        source = row.get('source', 'legacy')
        if row['type'] == 'EARN':
            live[_key(source, row['id'])]['remaining'] = amount
            ledger.append({'id': row['id'], 'member': member, 'action': 'EARN', 'points': amount,
                           'at': row['at'], 'source': source,
                           'channel': row.get('channel', ''), 'reason': row.get('reason', '')})
            continue
        if row['type'] == 'SPEND':
            available = sorted(
                (r for r in live.values() if r['member'] == member and r['remaining'] > 0),
                key=lambda r: (r['expires'], r['at'], r.get('source', 'legacy'), r['id']))
            if sum(r['remaining'] for r in available) < amount:
                errors.append((row, 'INSUFFICIENT_POINTS', '余额不足，消费未落账'))
                continue
            allocations = []
            rest = amount
            for lot in available:
                used = min(rest, lot['remaining'])
                lot['remaining'] -= used
                rest -= used
                allocations.append({'lot': lot['id'], 'source': lot.get('source', 'legacy'),
                                    'points': used, 'refunded': 0, 'expires': lot['expires']})
                if rest == 0:
                    break
            spends[_key(source, row['id'])] = {
                'id': row['id'], 'member': member, 'points': amount, 'refunded': 0,
                'allocations': allocations, 'at': row['at'], 'source': source,
                'channel': row.get('channel', ''),
            }
            ledger.append({'id': row['id'], 'member': member, 'action': 'SPEND', 'points': amount,
                           'at': row['at'], 'source': source,
                           'channel': row.get('channel', ''), 'reason': row.get('reason', '')})
            continue
        original = find_spend(source, row.get('ref', ''), member)
        if original is None or original['member'] != member:
            errors.append((row, 'REFUND_REFERENCE', '退款未关联同会员有效消费'))
            continue
        if amount > original['points'] - original['refunded']:
            errors.append((row, 'REFUND_CAP', '累计退款超过原消费'))
            continue
        restored = 0
        expired_refund = 0
        allocations = []
        rest = amount
        for allocation in reversed(original['allocations']):
            count = min(rest, allocation['points'] - allocation['refunded'])
            if not count:
                continue
            allocation['refunded'] += count
            rest -= count
            lot = live[_key(allocation.get('source', 'legacy'), allocation['lot'])]
            is_expired = instant(lot['expires']) <= at
            if is_expired:
                expired_refund += count
            else:
                lot['remaining'] += count
                restored += count
            allocations.append({'lot': lot['id'], 'source': lot.get('source', 'legacy'),
                                'points': count, 'expired': is_expired})
            if not rest:
                break
        original['refunded'] += amount
        refunds.append({'id': row['id'], 'member': member, 'ref': row['ref'], 'points': amount,
                        'restored': restored, 'expired_refund': expired_refund,
                        'allocations': allocations, 'line': row['_line'], 'source': source,
                        'channel': row.get('channel', ''), 'reason': row.get('reason', '')})
        ledger.append({'id': row['id'], 'member': member, 'action': 'REFUND', 'points': restored,
                       'at': row['at'], 'source': source,
                       'channel': row.get('channel', ''), 'reason': row.get('reason', '')})
    expire(instant(as_of))
    return {'lots': list(live.values()), 'spends': list(spends.values()), 'refunds': refunds,
            'ledger': ledger, 'errors': errors, 'expired': expired}


def apply(state):
    result = replay(state['records'], state['lots'], state['context']['as_of'])
    rejected = {(r.get('source', 'legacy'), r['id']) for r, _, _ in result.pop('errors')}
    # 错误事件独立重放后按编号补充原因，保证失败消费不扣任何积分。
    detailed = replay(state['records'], state['lots'], state['context']['as_of'])['errors']
    for row, code, detail in detailed:
        reject(state, row, code, detail)
    state['records'] = [r for r in state['records'] if (r.get('source', 'legacy'), r['id']) not in rejected]
    state['point_ledger'] = result
    state['views']['consumption'] = {
        'title': '消费与到期',
        'rows': result['ledger'],
        'metrics': {'消费笔': len(result['spends']), '自然到期': result['expired'],
                    '可用积分': sum(x['remaining'] for x in result['lots'])},
    }
