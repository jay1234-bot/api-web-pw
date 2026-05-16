"""StudyStark.com API client — mirrors their data-api.php + playlist + video-slides."""
import logging
import os
import requests

logger = logging.getLogger(__name__)

ORIGIN = os.environ.get('STUDYSTARK_ORIGIN', 'https://studystark.com')
DATA_API = os.environ.get('DATA_API_URL', f'{ORIGIN}/data-api.php')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    'X-Requested-With': 'SPA-Client',
    'Referer': f'{ORIGIN}/',
    'Accept': '*/*',
}

PLAY_HEADERS = {
    **HEADERS,
    'Referer': f'{ORIGIN}/play.php',
}


def get_token():
    t = os.environ.get('PW_ACCESS_TOKEN') or os.environ.get('ACCESS_TOKEN')
    return t.strip() if t else None


def _get(url, params, headers=None):
    try:
        r = requests.get(url, params=params, headers=headers or HEADERS, timeout=60)
        if r.status_code == 200:
            return r.json()
        logger.warning('GET %s -> %s', url, r.status_code)
    except Exception as e:
        logger.error('GET %s failed: %s', url, e)
    return None


def data_api(action, token=None, **kwargs):
    params = {'action': action}
    params.update({k: v for k, v in kwargs.items() if v is not None and v != ''})
    tok = token or get_token()
    if tok:
        params['token'] = tok
    return _get(DATA_API, params)


def get_batches():
    return data_api('batches')


def get_batch_details(batch_id, token=None):
    return data_api('batch_details', token, batch_id=batch_id)


def get_subjects(batch_id, token=None):
    data = get_batch_details(batch_id, token)
    if data and data.get('success'):
        return data.get('data', {}).get('subjects', [])
    return []


def get_topics(batch_id, subject_id, token=None):
    all_items = []
    page = 1
    while True:
        data = data_api('topics', token, batch_id=batch_id, subject_id=subject_id, page=page)
        if not (data and data.get('success')):
            break
        items = data.get('data', [])
        if not items:
            break
        all_items.extend(items)
        pag = data.get('paginate', {})
        if page * pag.get('limit', 20) >= pag.get('totalCount', len(all_items)):
            break
        page += 1
    return all_items


def get_content(batch_id, subject_id, topic_id, content_type, token=None):
    all_items = []
    page = 1
    while True:
        data = data_api(
            'content', token,
            batch_id=batch_id, subject_id=subject_id, topic_id=topic_id,
            content_type=content_type, page=page,
        )
        if not (data and data.get('success')):
            break
        items = data.get('data', [])
        if not items:
            break
        all_items.extend(items)
        pag = data.get('paginate', {})
        if page * pag.get('limit', 20) >= pag.get('totalCount', len(all_items)):
            break
        page += 1
    return all_items


def get_today_schedule(batch_id, token=None):
    data = data_api('today_schedule', token, batch_id=batch_id)
    if data and data.get('success'):
        return data.get('data', [])
    return []


def get_playlist(batch_id, subject_id, topic_id, schedule_id, token=None, play_type='Lecture'):
    params = {
        'batch_id': batch_id,
        'subject_id': subject_id,
        'topic_id': topic_id,
        'schedule_id': schedule_id,
        'play_type': play_type,
    }
    tok = token or get_token()
    if tok:
        params['token'] = tok
    return _get(f'{ORIGIN}/playlist-api.php', params, PLAY_HEADERS)


def get_video_slides(batch_id, subject_id, video_id, token=None):
    params = {'batch_id': batch_id, 'subject_id': subject_id, 'video_id': video_id}
    tok = token or get_token()
    if tok:
        params['token'] = tok
    return _get(f'{ORIGIN}/video-slides.php', params, PLAY_HEADERS)


def play_url(batch_id, subject_id, topic_id, schedule_id, token=None):
    from urllib.parse import urlencode
    q = {
        'batch_id': batch_id,
        'subject_id': subject_id,
        'topic_id': topic_id,
        'schedule_id': schedule_id,
    }
    tok = token or get_token()
    if tok:
        q['token'] = tok
    return f'{ORIGIN}/play.php?{urlencode(q)}'
