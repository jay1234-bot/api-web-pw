"""StudyStark / PenPencil proxy — shared by Flask app and Vercel serverless API."""
import os
import logging
import requests

logger = logging.getLogger(__name__)

DATA_API_URL = os.environ.get('DATA_API_URL', 'https://studystark.com/data-api.php')
STUDYSTARK_ORIGIN = os.environ.get('STUDYSTARK_ORIGIN', 'https://studystark.com')
USE_DATA_API = os.environ.get('USE_DATA_API', 'true').lower() not in ('0', 'false', 'no')

DATA_API_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    'X-Requested-With': 'SPA-Client',
    'Referer': f'{STUDYSTARK_ORIGIN}/',
}

PLAY_HEADERS = {
    'User-Agent': DATA_API_HEADERS['User-Agent'],
    'Referer': f'{STUDYSTARK_ORIGIN}/play.php',
    'X-Requested-With': 'SPA-Client',
}

# data-api.php actions
ACTIONS = frozenset({
    'batches', 'batch_details', 'topics', 'content', 'today_schedule',
})

# Extra upstream endpoints (same origin as StudyStark)
EXTRA_ENDPOINTS = {
    'playlist': '/playlist-api.php',
    'video_slides': '/video-slides.php',
}


def get_access_token():
    """Token from env — set once on Vercel; refresh when StudyStark key expires."""
    token = os.environ.get('PW_ACCESS_TOKEN') or os.environ.get('ACCESS_TOKEN')
    return token.strip() if token else None


def is_configured():
    return bool(get_access_token())


def call_data_api(action, token=None, **params):
    if not USE_DATA_API or action not in ACTIONS:
        return None
    query = {'action': action}
    for key, value in params.items():
        if value is not None and value != '':
            query[key] = value
    tok = token or get_access_token()
    if tok:
        query['token'] = tok
    try:
        response = requests.get(
            DATA_API_URL, params=query, headers=DATA_API_HEADERS, timeout=60,
        )
        if response.status_code == 200:
            return response.json()
        logger.warning('Data API %s HTTP %s', action, response.status_code)
    except Exception as exc:
        logger.error('Data API %s failed: %s', action, exc)
    return None


def call_upstream(endpoint_key, token=None, **params):
    path = EXTRA_ENDPOINTS.get(endpoint_key)
    if not path:
        return None
    query = dict(params)
    tok = token or get_access_token()
    if tok:
        query['token'] = tok
    try:
        url = f'{STUDYSTARK_ORIGIN.rstrip("/")}{path}'
        response = requests.get(url, params=query, headers=PLAY_HEADERS, timeout=60)
        if response.status_code == 200:
            return response.json()
    except Exception as exc:
        logger.error('Upstream %s failed: %s', endpoint_key, exc)
    return None


def proxy_action(action, **params):
    """Unified proxy entry for API routes."""
    token = params.pop('token', None) or get_access_token()

    if action == 'playlist':
        return call_upstream(
            'playlist', token,
            batch_id=params.get('batch_id'),
            subject_id=params.get('subject_id'),
            topic_id=params.get('topic_id'),
            schedule_id=params.get('schedule_id'),
            play_type=params.get('play_type', 'Lecture'),
        )

    if action == 'video_slides':
        return call_upstream(
            'video_slides', token,
            batch_id=params.get('batch_id'),
            subject_id=params.get('subject_id'),
            video_id=params.get('video_id'),
        )

    if action == 'token_status':
        tok = get_access_token()
        if not tok:
            return {'success': False, 'configured': False, 'message': 'PW_ACCESS_TOKEN not set'}
        probe = call_data_api('topics', token=tok, batch_id=params.get('batch_id', 'x'),
                              subject_id=params.get('subject_id', 'x'), page=1)
        ok = bool(probe and probe.get('success'))
        return {
            'success': ok,
            'configured': True,
            'valid': ok,
            'message': 'Token OK' if ok else (probe or {}).get('message', 'Token invalid or expired'),
        }

    return call_data_api(action, token, **params)


def fetch_all_topics(batch_id, subject_id, token=None):
    all_chapters = []
    page = 1
    while True:
        data = call_data_api(
            'topics', token, batch_id=batch_id, subject_id=subject_id, page=page,
        )
        if not (data and data.get('success')):
            break
        chapters = data.get('data', [])
        if not chapters:
            break
        all_chapters.extend(chapters)
        paginate = data.get('paginate', {})
        total = paginate.get('totalCount', len(all_chapters))
        limit = paginate.get('limit', 20)
        if page * limit >= total:
            break
        page += 1
    return all_chapters


def fetch_all_content(batch_id, subject_id, topic_id, content_type, token=None):
    combined = []
    page = 1
    while True:
        data = call_data_api(
            'content', token,
            batch_id=batch_id, subject_id=subject_id, topic_id=topic_id,
            content_type=content_type, page=page,
        )
        if not (data and data.get('success')):
            break
        items = data.get('data', [])
        if not items:
            break
        combined.extend(items)
        paginate = data.get('paginate', {})
        total = paginate.get('totalCount', len(combined))
        limit = paginate.get('limit', 20)
        if page * limit >= total:
            break
        page += 1
    return combined


def build_studystark_play_url(batch_id, subject_id, topic_id, schedule_id, token=None):
    """Deep link to StudyStark player (uses their UI + stream resolution)."""
    from urllib.parse import urlencode
    q = {
        'batch_id': batch_id,
        'subject_id': subject_id,
        'topic_id': topic_id,
        'schedule_id': schedule_id,
    }
    tok = token or get_access_token()
    if tok:
        q['token'] = tok
    return f'{STUDYSTARK_ORIGIN}/play.php?{urlencode(q)}'
