from fastapi import Request
from fastapi.templating import Jinja2Templates
from app.config import ROOT, META, SOURCE_LABELS
from app.engine import evaluate
from app.errors import BusinessError
from app import store

templates = Jinja2Templates(directory=str(ROOT/'app/templates'))


def active_release(day):
    from app.main import DB_PATH
    try:
        return store.release(DB_PATH, day)
    except BusinessError:
        return None


def render(request: Request, key):
    from app.main import DB_PATH
    run_id = request.query_params.get('run_id')
    if run_id:
        state = store.get(DB_PATH, run_id)
    else:
        state = store.save(DB_PATH, evaluate({'dataset': request.query_params.get('dataset', 'dirty')}))
    day = (state.get('publish') or {}).get('day')
    release = active_release(day) if day else None
    return templates.TemplateResponse(request=request, name=f'{key}.html', context={
        'meta': META, 'key': key, 'state': state, 'view': state['views'][key],
        'release': release, 'release_history': store.releases(DB_PATH, day) if day else [],
        'source_labels': SOURCE_LABELS,
    })
