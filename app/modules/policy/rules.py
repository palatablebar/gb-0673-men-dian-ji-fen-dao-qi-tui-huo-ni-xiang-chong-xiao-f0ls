from app.coerce import number, instant
from app.errors import BusinessError
from app.reasons import reject

BUSINESS_KEYS = ('id', 'member', 'type', 'points', 'at', 'ref')


def normalize(row, context):
    value = number(row['points'])
    version = number(row['version'])
    if value <= 0 or value % 1 or version < 1 or version % 1:
        raise BusinessError('INVALID_RANGE', '积分与版本须为正整数')
    if row['type'] not in {'EARN', 'SPEND', 'REFUND'}:
        raise BusinessError('BAD_TYPE', '积分事件类型无效')
    if not row['member'].strip() or row['rule'] not in context['rules']:
        raise BusinessError('UNKNOWN_RULE', '会员或权益规则不存在')
    at = instant(row['at'])
    rule = context['rules'][row['rule']]
    if not instant(rule['start']) <= at < instant(rule['end']):
        raise BusinessError('RULE_EXPIRED', '事件不在权益有效窗')
    if at > instant(context['as_of']):
        raise BusinessError('AFTER_CUTOFF', '事件晚于结算时点')
    # 渠道与退货原因是新门店的凭证字段：参与记账但不在这里拒绝，
    # 缺失时由退货依据核验标记并阻断发布，避免凭证缺陷改变余额。
    return {**row, 'points': int(value), 'version': int(version), 'at': at.isoformat(),
            'channel': (row.get('channel') or '').strip(),
            'reason': (row.get('reason') or '').strip()}



def apply(state):
    # 先统一时区与数值，再做跨来源去重：同一事件若两路重复投递只记一次。
    normalized = []
    for row in state['records']:
        try:
            normalized.append(normalize(row, state['context']))
        except BusinessError as e:
            reject(state, row, e.code, e.message)
    seen = {}
    good = []
    for row in normalized:
        key = tuple(row.get(k) for k in BUSINESS_KEYS)
        other = seen.get(key)
        if other is None:
            seen[key] = len(good)
            good.append(row)
            continue
        index = seen[key]
        keep, drop = (row, good[index]) if row['source'] == 'new' else (good[index], row)
        good[index] = keep
        reject(state, drop, 'CROSS_SOURCE_DUPLICATE', '同一事件在两路来源重复出现，只记一次')
    state['records'] = good
    state['views']['policy'] = {
        'title': '权益校验',
        'rows': good,
        'metrics': {'有效事件': len(good), '规则版本': len(state['context']['rules'])},
    }
