"""来源识别与新门店退货依据的受控取值。"""
from app.errors import BusinessError

CHANNELS = {'门店POS', '小程序', 'APP', '网页', '客服'}
REFUND_REASONS = {'质量问题', '七天无理由', '发错货', '少件漏发', '价格调整', '其他'}

SOURCE_ALIASES = {
    'legacy': 'legacy', 'old': 'legacy', '旧': 'legacy', '旧门店': 'legacy', '旧店': 'legacy', 'l': 'legacy',
    'new': 'new', '新': 'new', '新门店': 'new', '新店': 'new', 'n': 'new',
}


def normalize_source(value):
    text = str(value or '').strip()
    source = SOURCE_ALIASES.get(text.lower())
    if source is None:
        raise BusinessError('SOURCE_UNKNOWN', '来源只能为旧门店或新门店')
    return source


def channel(value):
    text = str(value or '').strip()
    if not text:
        raise BusinessError('EVIDENCE_MISSING', '兑换渠道缺失')
    if text not in CHANNELS:
        raise BusinessError('EVIDENCE_CHANNEL', '兑换渠道不在受控取值内')
    return text


def reason(value):
    text = str(value or '').strip()
    if not text:
        raise BusinessError('EVIDENCE_MISSING', '退货原因缺失')
    if text not in REFUND_REASONS:
        raise BusinessError('EVIDENCE_REASON', '退货原因不在受控取值内')
    return text
