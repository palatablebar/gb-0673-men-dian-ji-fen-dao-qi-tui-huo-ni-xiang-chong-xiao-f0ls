REASONS = {
    'COLUMN_COUNT':'列数错误', 'MISSING_ID':'编号缺失', 'DUPLICATE_CONFLICT':'重复编号冲突',
    'MISSING_VALUE':'数值缺失', 'BAD_NUMBER':'数值格式错误', 'BAD_TIME':'时间格式错误',
    'BAD_UNIT':'单位不受支持', 'UNKNOWN_REFERENCE':'引用对象不存在', 'INVALID_RANGE':'范围错误',
    'VERSION_CONFLICT':'版本冲突', 'EMPTY_FILE':'空文件', 'BLANK_FILE':'全空行',
    'HEADER_ONLY':'仅有表头', 'STALE_VERSION':'复核版本已更新', 'NOT_FOUND':'批次不存在',
    'SOURCE_UNKNOWN':'来源无法识别', 'EVIDENCE_MISSING':'退货依据缺失',
    'EVIDENCE_CHANNEL':'兑换渠道无效', 'EVIDENCE_REASON':'退货原因无效',
    'EVIDENCE_ORIGINAL':'退货原消费缺失', 'EVIDENCE_ORIGINAL_CHANNEL':'原消费渠道缺失',
    'CROSS_SOURCE_DUPLICATE':'两路来源重复投递已合并',
    'RELEASE_BLOCKED':'余额对账未通过，禁止发布',
    'RELEASE_IMMUTABLE':'已发布版本不可覆盖',
}
def reject(state, record, code, detail):
    state['quarantine'].append({'line':record.get('_line',0),'id':record.get('id',''),
       'code':code,'detail':detail,'raw':{k:v for k,v in record.items() if not k.startswith('_')}})
