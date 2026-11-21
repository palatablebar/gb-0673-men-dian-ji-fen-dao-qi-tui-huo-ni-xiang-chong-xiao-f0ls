import csv, io
from app.errors import BusinessError
from app.reasons import reject
from app.config import SOURCE_LABELS
from app.evidence import normalize_source

BASE_FIELDS = ['id', 'member', 'type', 'points', 'at', 'expires', 'ref', 'rule', 'version']
OPTIONAL_FIELDS = ['source', 'channel', 'reason']
KNOWN_FIELDS = set(BASE_FIELDS) | set(OPTIONAL_FIELDS)


def _header(original, aliases):
    header = [aliases.get(v.strip(), v.strip()) for v in original]
    if len(set(header)) != len(header):
        raise BusinessError('BAD_HEADER', '表头存在重复字段')
    if any(name not in KNOWN_FIELDS for name in header):
        raise BusinessError('BAD_HEADER', '存在未定义字段')
    if any(name not in header for name in BASE_FIELDS):
        raise BusinessError('BAD_HEADER', '字段或顺序不匹配')
    return header


def read_source(state, fields, aliases):
    raw = state['raw_bytes']
    if not raw:
        raise BusinessError('EMPTY_FILE', '请选择非空文件')
    if not raw.strip(b'\xef\xbb\xbf\r\n \t'):
        raise BusinessError('BLANK_FILE', '文件只有空行')
    text = None
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise BusinessError('BAD_ENCODING', '文件编码无法识别')
    stream = csv.reader(io.StringIO(text, newline=''), strict=True)
    try:
        original = [v.strip() for v in next(stream)]
        header = _header(original, aliases)
        state['counts'] = {'input': 0, 'blank': 0, 'repeated_header': 0, 'duplicate': 0}
        # 旧格式没有来源列；带渠道/原因列的文件默认视为新门店，逐行来源可覆盖。
        default_source = 'new' if ({'channel', 'reason'} & set(header)) else 'legacy'
        records = []
        for cells in stream:
            line = stream.line_num
            state['counts']['input'] += 1
            if not cells or not any(v.strip() for v in cells):
                state['counts']['blank'] += 1
                continue
            if [v.strip() for v in cells] == original:
                state['counts']['repeated_header'] += 1
                continue
            row = {name: '' for name in KNOWN_FIELDS}
            row.update(dict(zip(header, cells)))
            row['_line'] = line
            if len(cells) != len(header):
                reject(state, row, 'COLUMN_COUNT', f'需要{len(header)}列，实际{len(cells)}列')
                continue
            if not row.get('id', '').strip():
                reject(state, row, 'MISSING_ID', '编号不能为空')
                continue
            row = {name: (value.strip() if name in KNOWN_FIELDS else value)
                   for name, value in row.items()}
            raw_source = row.get('source') or default_source
            try:
                row['source'] = normalize_source(raw_source)
            except BusinessError as error:
                reject(state, row, error.code, error.message)
                continue
            # 旧记录补齐兼容字段，不因新字段缺失而丢失原记录。
            row['source_label'] = SOURCE_LABELS[row['source']]
            row['legacy_compat'] = row['source'] == 'legacy'
            records.append(row)
    except csv.Error:
        raise BusinessError('BROKEN_CSV', 'CSV引号或末行损坏')
    if not state['counts']['input']:
        raise BusinessError('HEADER_ONLY', '文件只有表头')
    # 同一来源内编号唯一；不同来源允许复用编号，重跑不会重复记账。
    by_key = {}
    for row in records:
        by_key.setdefault((row['source'], row['id']), []).append(row)
    selected = []
    for rows in by_key.values():
        identities = {tuple(sorted((k, v) for k, v in r.items() if k != '_line')) for r in rows}
        if len(identities) > 1:
            for row in rows:
                reject(state, row, 'DUPLICATE_CONFLICT', '同一来源同一编号存在不同原值')
        else:
            selected.append(rows[0])
            state['counts']['duplicate'] += len(rows) - 1
    state['records'] = selected
    state['encoding'] = encoding
    state['layout'] = 'combined' if set(header) & set(OPTIONAL_FIELDS) else 'legacy'
    state['views'][state['context']['keys'][0]] = {
        'rows': [dict(r) for r in selected],
        'metrics': {'采信行': len(selected), '隔离行': len(state['quarantine']), '重复行': state['counts']['duplicate']},
        'title': '原始证据',
    }
