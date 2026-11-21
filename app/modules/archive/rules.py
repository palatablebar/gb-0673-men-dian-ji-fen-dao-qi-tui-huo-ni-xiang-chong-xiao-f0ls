import hashlib, json

from app.release import build_gate


def decide(state):
    gate = build_gate(state)
    balances = {}
    for lot in state['point_ledger']['lots']:
        balances[lot['member']] = balances.get(lot['member'], 0) + lot['remaining']
    rows = [{'member': m, 'remaining': n} for m, n in sorted(balances.items())]
    return {
        'status': '可归档' if gate['publishable'] else '待复核',
        'publishable': gate['publishable'],
        'day': gate['day'],
        'checks': gate['checks'],
        'members': len(rows),
        'seal': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
        'balances': rows,
    }


def apply(state):
    state['archive'] = decide(state)
    state['views']['archive'] = {
        'title': '期末归档',
        'rows': state['archive']['balances'],
        'metrics': {'会员数': state['archive']['members'],
                    '可用积分': state['balance']['remaining'],
                    '状态': state['archive']['status']},
    }
