import sqlite3, json, hashlib
from datetime import datetime, timezone
from pathlib import Path
from app.errors import BusinessError

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, "
    "result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')",
    "CREATE TABLE IF NOT EXISTS releases (id TEXT PRIMARY KEY, day TEXT NOT NULL, run_id TEXT NOT NULL, "
    "digest TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, payload TEXT NOT NULL, "
    "status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL)",
)
# 同一天只允许一条 active 发布；数据库层兜底，避免中断留下双活。
ACTIVE_UNIQUE = "CREATE UNIQUE INDEX IF NOT EXISTS releases_one_active ON releases(day) WHERE status='active'"


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    for statement in SCHEMA:
        con.execute(statement)
    con.execute(ACTIVE_UNIQUE)
    return con


def save(path, result):
    run_id = result['manifest']['run_id']
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result) VALUES(?,?,?)',
                    (run_id, result['manifest']['sha256'], json.dumps(result, ensure_ascii=False)))
    return get(path, run_id)


def get(path, run_id):
    with connect(path) as con:
        row = con.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
    if not row:
        raise BusinessError('NOT_FOUND', '批次不存在', 404)
    result = json.loads(row['result'])
    # 旧版本持久化的批次没有来源与发布字段：用不可变快照按当前规则重算，
    # 批号不变、原始输入不变，旧批次因此仍可打开、复核与发布。
    if 'publish' not in result and result.get('input_snapshot'):
        from app.engine import evaluate
        result = evaluate({'dataset': result.get('source', 'dirty')},
                          snapshot=result['input_snapshot'])
    result.update(revision=row['revision'], status=row['status'])
    return result


def history(path):
    with connect(path) as con:
        return [dict(r) for r in con.execute(
            'SELECT id,revision,status FROM runs ORDER BY rowid DESC LIMIT 100')]


def review(path, run_id, revision):
    with connect(path) as con:
        cur = con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',
                          ('已复核', run_id, revision))
        if cur.rowcount != 1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?', (run_id,)).fetchone():
                raise BusinessError('NOT_FOUND', '批次不存在', 404)
            raise BusinessError('STALE_VERSION', '复核版本已更新，请刷新', 409)
    return get(path, run_id)


def _release_id(day, run_id, digest):
    return hashlib.sha256(f'{day}|{run_id}|{digest}'.encode()).hexdigest()[:20]


def _activate(con, release_id, day):
    """在事务内切换 active：先落新版本，再让旧版本退位。"""
    con.execute("UPDATE releases SET status='superseded' WHERE day=? AND status='active' AND id<>?",
                (day, release_id))
    con.execute("UPDATE releases SET status='active' WHERE id=?", (release_id,))


def _content(payload):
    """记账内容指纹：排除版本与时间戳，只比较账与证据本身。"""
    body = {k: v for k, v in payload.items() if k not in ('revision', 'generated_at')}
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


def publish(path, result, payload):
    # 闸门在存储层再校验一次：任何调用路径都不能把未对平的账写成发布版。
    if not (payload.get('gate') or {}).get('publishable'):
        raise BusinessError('RELEASE_BLOCKED', '余额对账未通过，禁止发布', 409)
    day = payload['day']
    run_id = payload['run_id']
    digest = payload['sha256']
    release_id = _release_id(day, run_id, digest)
    created = datetime.now(timezone.utc).isoformat()
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    con = connect(path)
    try:
        con.execute('BEGIN IMMEDIATE')
        existing = con.execute('SELECT id,payload,status,revision FROM releases WHERE id=?', (release_id,)).fetchone()
        if existing is not None:
            # 同一批积分重跑重发：记账内容一致就复用同一条发布，不产生第二条记录。
            if _content(json.loads(existing['payload'])) != _content(payload):
                raise BusinessError('RELEASE_IMMUTABLE', '同一发布号内容不一致，拒绝覆盖', 409)
            if existing['status'] != 'active':
                _activate(con, release_id, day)
            if existing['payload'] != body:
                # 仅复核版本变化：更新审计字段，保持发布号与账不变。
                con.execute('UPDATE releases SET payload=?, revision=MAX(revision,?), run_id=? WHERE id=?',
                            (body, payload.get('revision', 1), run_id, release_id))
            con.commit()
            return release(path, day)
        revision = 1 + (con.execute(
            "SELECT COALESCE(MAX(revision),0) FROM releases WHERE day=?", (day,)).fetchone()[0])
        con.execute('INSERT INTO releases(id,day,run_id,digest,revision,payload,status,created_at) '
                    "VALUES(?,?,?,?,?,?,'staging',?)",
                    (release_id, day, run_id, digest, revision, body, created))
        # 非 active 先落库；只有随后切换成功并提交，新版才对外可见。
        _activate(con, release_id, day)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return release(path, day)


def release(path, day):
    with connect(path) as con:
        row = con.execute("SELECT * FROM releases WHERE day=? AND status='active'", (day,)).fetchone()
        if row is None:
            raise BusinessError('NOT_FOUND', '当天没有已发布版本', 404)
        payload = json.loads(row['payload'])
        payload.update(release_id=row['id'], release_revision=row['revision'], published_at=row['created_at'])
        return payload


def releases(path, day):
    with connect(path) as con:
        rows = [dict(r) for r in con.execute(
            'SELECT id,day,run_id,revision,status,created_at FROM releases WHERE day=? ORDER BY revision DESC',
            (day,))]
    return rows
