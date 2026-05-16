from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from datetime import timedelta, datetime
import requests
import re
import json
import asyncio
import aiohttp
import logging
import base64
import urllib3
from concurrent.futures import ThreadPoolExecutor
import functools
from urllib.parse import urlparse
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from base64 import b64encode, b64decode
import os
import pickle

def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.isfile(env_path):
        return
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

_load_dotenv()

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
app.secret_key = 'rarestudy'
app.permanent_session_lifetime = timedelta(days=7)

HEADERS_TEMPLATE = {
    'Host': 'api.penpencil.co',
    'client-id': '5eb393ee95fab7468a79d189',
    'client-version': '1910',
    'user-agent': 'Mozilla/5.0 (Linux; Android 12; M2101K6P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Mobile Safari/537.36',
    'randomid': '72012511-256c-4e1c-b4c7-29d67136af37',
    'client-type': 'ADMIN',
    'content-type': 'application/json; charset=utf-8',
}

BATCHES_FILE = 'mybatches.json'
TOKEN_API_URL = os.environ.get(
    'TOKEN_API_URL',
    'https://pwlogintoken-b99dfabfaab6.herokuapp.com/update-login-token',
)
DATA_API_URL = os.environ.get('DATA_API_URL', 'https://studystark.com/data-api.php')
USE_DATA_API = os.environ.get('USE_DATA_API', 'true').lower() not in ('0', 'false', 'no')
DATA_API_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    'X-Requested-With': 'SPA-Client',
    'Referer': os.environ.get('DATA_API_REFERER', 'https://studystark.com/'),
}

# Optimized thread pool with proper limits
executor = ThreadPoolExecutor(max_workers=100)

def encrypt(url):
    key = "364hs33391682025".encode('utf-8')[:16]
    iv = "hsukna3643339168".encode('utf-8')[:16]
    padded_text = pad(url.encode('utf-8'), AES.block_size)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    ciphertext = cipher.encrypt(padded_text)
    encrypt = b64encode(ciphertext).decode('utf-8')
    return encrypt

def decrypt(encrypted_url):
    try:
        key = "364hs33391682025".encode('utf-8')[:16]
        iv = "hsukna3643339168".encode('utf-8')[:16]
        ciphertext = b64decode(encrypted_url.encode('utf-8'))
        cipher = AES.new(key, AES.MODE_CBC, iv)
        padded_plaintext = cipher.decrypt(ciphertext)
        plaintext = unpad(padded_plaintext, AES.block_size)
        return plaintext.decode('utf-8')
    except:
        return None

def encode_params(batch_id, subject_id=None, chapter_id=None, url=None, parent_id=None, child_id=None):
    params = {'b': batch_id}
    if subject_id: params['s'] = subject_id
    if chapter_id: params['c'] = chapter_id
    if url: params['url'] = url
    if parent_id: params['parentId'] = parent_id
    if child_id: params['childId'] = child_id
    
    json_params = json.dumps(params)
    base64_params = base64.urlsafe_b64encode(json_params.encode()).decode().rstrip('=')
    encrypted_params = encrypt(base64_params)
    url_safe_encrypted = encrypted_params.replace('/', '_').replace('+', '-').replace('=', '')
    
    return url_safe_encrypted

def decode_params(encrypted_encoded):
    try:
        encrypted_encoded = encrypted_encoded.replace('_', '/').replace('-', '+')
        padding = 4 - len(encrypted_encoded) % 4
        if padding != 4:
            encrypted_encoded += '=' * padding
        
        decrypted_params = decrypt(encrypted_encoded)
        if decrypted_params is None:
            return {}
        
        padding = 4 - len(decrypted_params) % 4
        if padding != 4:
            decrypted_params += '=' * padding
        json_params = base64.urlsafe_b64decode(decrypted_params).decode()
        return json.loads(json_params)
    except Exception as e:
        logging.error(f"Error decoding parameters: {e}")
        return {}

def load_batches():
    try:
        with open(BATCHES_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def get_headers(token):
    headers = HEADERS_TEMPLATE.copy()
    headers['authorization'] = f"Bearer {token}"
    return headers

def call_data_api(action, token=None, **params):
    """Call StudyStark-style data-api proxy (topics, batch_details, content, etc.)."""
    if not USE_DATA_API:
        return None
    query = {'action': action}
    for key, value in params.items():
        if value is not None:
            query[key] = value
    if token:
        query['token'] = token
    try:
        response = requests.get(DATA_API_URL, params=query, headers=DATA_API_HEADERS, timeout=60)
        if response.status_code == 200:
            return response.json()
        logging.warning(f"Data API {action} returned HTTP {response.status_code}")
    except Exception as e:
        logging.error(f"Data API {action} failed: {e}")
    return None

def is_penpencil_token(token):
    if not token:
        return False
    try:
        headers = get_headers(token)
        response = requests.post(
            "https://api.penpencil.co/v3/oauth/verify-token",
            headers=headers,
            timeout=15,
        )
        return response.status_code == 200
    except Exception:
        return False

def map_proxy_content_items(items, batch_id, subject_id, topic_id, section):
    """Map data-api content response to template-friendly items."""
    content = []
    for item in items or []:
        if section in ('videos', 'DppVideos'):
            video_details = item.get('videoDetails') or {}
            name = item.get('topic') or item.get('name', 'Video')
            schedule_id = item.get('_id', '')
            video_id = video_details.get('_id') or video_details.get('id', '')
            embed = item.get('embedCode') or video_details.get('embedCode')
            video_url = item.get('videoUrl') or video_details.get('videoUrl')
            thumbnail = video_details.get('image') or item.get('image', '')

            if embed:
                content.append({'type': 'video', 'name': name, 'videoUrl': embed, 'thumbnail': thumbnail})
            elif video_url:
                encoded_params = encode_params(
                    batch_id=batch_id,
                    url=video_url,
                    parent_id=batch_id,
                    child_id=schedule_id,
                )
                content.append({
                    'type': 'video',
                    'name': name,
                    'videoUrl': f"/media/{encoded_params}",
                    'thumbnail': thumbnail,
                })
            else:
                play_url = url_for(
                    'watch',
                    batch_id=batch_id,
                    subject_id=subject_id,
                    topic_id=topic_id,
                    schedule_id=schedule_id,
                    video_id=video_id,
                )
                content.append({
                    'type': 'video',
                    'name': name,
                    'videoUrl': play_url,
                    'thumbnail': thumbnail,
                })
        elif section in ('notes', 'DppNotes'):
            url = item.get('url')
            if not url:
                base_url = item.get('baseUrl', '')
                key = item.get('key', '')
                if base_url and key:
                    url = base_url + key
            if not url:
                for homework in item.get('homeworkIds', []):
                    for attachment in homework.get('attachmentIds', []):
                        note_url = attachment.get('baseUrl', '') + attachment.get('key', '')
                        if note_url:
                            content.append({
                                'type': 'note',
                                'name': homework.get('topic', item.get('name', 'Note')),
                                'url': note_url,
                            })
                continue
            content.append({'type': 'note', 'name': item.get('name', item.get('topic', 'Note')), 'url': url})
    return content

def map_proxy_schedule_items(items, batch_id, subject_id=''):
    """Map today_schedule data-api response."""
    content = []
    for item in items or []:
        sid = item.get('batchSubjectId') or subject_id
        tid = (item.get('tags') or [{}])[0].get('_id', '') if item.get('tags') else ''
        mapped = map_proxy_content_items([item], batch_id, sid, tid, 'videos')
        for video in mapped:
            video['subject_name'] = item.get('subject_name') or item.get('subject', {}).get('name', '')
            if isinstance(item.get('subject'), str):
                video['subject_name'] = item.get('subject')
            video['startTime'] = item.get('startTime', '')
            content.append(video)
        if item.get('type') == 'note' and item.get('url'):
            content.append({
                'type': 'note',
                'name': item.get('name', ''),
                'url': item['url'],
                'subject_name': item.get('subject_name', ''),
            })
    return content

async def create_session():
    """Create optimized aiohttp session"""
    connector = aiohttp.TCPConnector(
        limit=100,
        limit_per_host=50,
        ttl_dns_cache=300,
        use_dns_cache=True,
        enable_cleanup_closed=True
    )
    return aiohttp.ClientSession(connector=connector)

async def fetch_data(session, url, headers, params=None, data=None, method='GET'):
    try:
        async with session.request(method, url, headers=headers, params=params, json=data) as response:
            if response.status == 200:
                return await response.json()
            else:
                logging.warning(f"HTTP {response.status} for {url}")
                return {"success": False, "data": {"message": "No Data Found"}}
    except Exception as e:
        logging.error(f"Error fetching {url}: {e}")
        return {"success": False, "data": {"message": "No Data Found"}}

def run_async_optimized(coro):
    """Simplified async runner without thread pool"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    except Exception as e:
        logging.error(f"Error in async operation: {e}")
        return None

@app.context_processor
def utility_processor():
    return dict(encode_params=encode_params)

async def process_content(session, batch_id, subject_id, schedule_id, content_type, headers):
    url = f"https://api.penpencil.co/v1/batches/{batch_id}/subject/{subject_id}/schedule/{schedule_id}/schedule-details"
    data = await fetch_data(session, url, headers=headers)
    content = []

    if data and data.get("success") and data.get("data"):
        data_item = data["data"]

        if content_type in ("videos", "DppVideos"):
            video_details = data_item.get('videoDetails', {})
            if video_details:
                name = data_item.get('topic')
                childId = data_item.get('_id')
                if video_details.get('embedCode'):
                    videoUrl = video_details.get('embedCode')
                    
            #    elif video_details.get('videoUrl'):
                else:
                    encoded_params = encode_params(
                        batch_id=batch_id,
                        url=video_details.get('videoUrl') or 'https://d1d34p8vz63oiq.cloudfront.net/682749f7-eb81-47ff-8788-c25bf7afbbd9/master.mpd',
                        parent_id=batch_id,
                        child_id=childId
                    )
                    videoUrl = f"/media/{encoded_params}"
                
                if videoUrl:
                    content.append({
                        'type': 'video', 
                        'name': name, 
                        'videoUrl': videoUrl, 
                        'thumbnail': video_details.get('image'), 
                    })
                    
        elif content_type in ("notes", "DppNotes"):
            for homework in data_item.get('homeworkIds', []):
                for attachment in homework.get('attachmentIds', []):
                    url = attachment.get('baseUrl', '') + attachment.get('key', '')
                    if url:
                        content.append({'type': 'note', 'name': homework.get('topic', ''), 'url': url})

    return {content_type: content} if content else {}

async def fetch_schedule(session, chapter_id, batch_id, subject_id, content_type, headers):
    all_schedule = []
    page = 1
    while True:
        params = {'tag': chapter_id, 'contentType': content_type, 'page': page}
        url = f"https://api.penpencil.co/v2/batches/{batch_id}/subject/{subject_id}/contents"
        data = await fetch_data(session, url, headers=headers, params=params)

        if data and data.get("success") and data.get("data"):
            for item in data["data"]:
                item['content_type'] = content_type
                all_schedule.append(item)
            page += 1
        else:
            break
    return all_schedule

async def process_chapters(session, chapter_id, batch_id, subject_id, content_type, headers):
    all_schedule = await fetch_schedule(session, chapter_id, batch_id, subject_id, content_type, headers)
    
    tasks = [process_content(session, batch_id, subject_id, item["_id"], item['content_type'], headers)
             for item in all_schedule]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    combined_content = []
    for result in results:
        if isinstance(result, dict) and result.get(content_type):
            combined_content.extend(result[content_type])
    return combined_content

async def fetch_chapter_content_async(batch_id, subject_id, chapter_id, section, token):
    if USE_DATA_API and token:
        page = 1
        combined = []
        while True:
            data = call_data_api(
                'content',
                token=token,
                batch_id=batch_id,
                subject_id=subject_id,
                topic_id=chapter_id,
                content_type=section,
                page=page,
            )
            if not (data and data.get('success')):
                break
            items = data.get('data', [])
            if not items:
                break
            combined.extend(map_proxy_content_items(items, batch_id, subject_id, chapter_id, section))
            paginate = data.get('paginate', {})
            total = paginate.get('totalCount', len(combined))
            limit = paginate.get('limit', 20)
            if page * limit >= total:
                break
            page += 1
        if combined:
            return combined

    if token and is_penpencil_token(token):
        headers = get_headers(token)
        async with await create_session() as session:
            return await process_chapters(session, chapter_id, batch_id, subject_id, section, headers)
    return []

async def fetch_subjects_async(batch_id, token):
    if USE_DATA_API:
        data = call_data_api('batch_details', token=token, batch_id=batch_id)
        if data and data.get('success'):
            subjects = data.get('data', {}).get('subjects', [])
            if subjects:
                return subjects

    if token and is_penpencil_token(token):
        headers = get_headers(token)
        async with await create_session() as session:
            url = f'https://api.penpencil.co/v3/batches/{batch_id}/details'
            data = await fetch_data(session, url, headers, method='GET')
            if data and data.get("success"):
                return data.get('data', {}).get('subjects', [])
    return []

async def fetch_chapters_async(batch_id, subject_id, token):
    if USE_DATA_API and token:
        all_chapters = []
        page = 1
        while True:
            data = call_data_api(
                'topics',
                token=token,
                batch_id=batch_id,
                subject_id=subject_id,
                page=page,
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
        if all_chapters:
            return all_chapters

    if token and is_penpencil_token(token):
        headers = get_headers(token)
        all_chapters = []
        page = 1
        async with await create_session() as session:
            while True:
                url = f"https://api.penpencil.co/v2/batches/{batch_id}/subject/{subject_id}/topics?page={page}"
                data = await fetch_data(session, url, headers, method='GET')
                if data and data.get("success"):
                    chapters = data.get("data", [])
                    if chapters:
                        all_chapters.extend(chapters)
                        page += 1
                    else:
                        break
                else:
                    break
        return all_chapters
    return []

async def get_schedule_details(batch_id, token, subject_id, schedule_id):
    headers = get_headers(token)
    async with await create_session() as session:
        url = f"https://api.penpencil.co/v1/batches/{batch_id}/subject/{subject_id}/schedule/{schedule_id}/schedule-details"
        data = await fetch_data(session, url, headers, method='GET')
        content = []
    
        if data and data.get("success") and data.get("data"):
            data_item = data["data"]
            subject_name = data_item.get('subject', {}).get('name')
            name = data_item.get('topic')
            childId = data_item.get('_id')
            startTime = (datetime.fromisoformat(data_item.get('startTime').replace("Z", "+00:00")) + timedelta(hours=5, minutes=30)).strftime("%I:%M%p").lstrip("0")
            video_details = data_item.get('videoDetails', {})
        
            if video_details:
                if video_details.get('embedCode'):
                    videoUrl = video_details.get('embedCode')
                    
           #     elif video_details.get('videoUrl'):
                else:
                    encoded_params = encode_params(
                        batch_id=batch_id,
                        url=video_details.get('videoUrl') or 'https://d1d34p8vz63oiq.cloudfront.net/682749f7-eb81-47ff-8788-c25bf7afbbd9/master.mpd',
                        parent_id=batch_id,
                        child_id=childId
                    )
                    videoUrl = f"/media/{encoded_params}"
            
                if videoUrl:
                    content.append({
                        'type': 'video', 
                        'name': name, 
                        'videoUrl': videoUrl, 
                        'thumbnail': video_details.get('image'), 
                        'subject_name': subject_name,
                        'startTime': startTime
                    })
            else:
                url = data_item.get('url') or 'https://d1d34p8vz63oiq.cloudfront.net/682749f7-eb81-47ff-8788-c25bf7afbbd9/master.mpd'
                if url:
                    encoded_params = encode_params(
                        batch_id=batch_id,
                        url=url,
                        parent_id=batch_id,
                        child_id=childId
                    )
                    videoUrl = f"/media/{encoded_params}"
            
                    if videoUrl:
                        content.append({
                            'type': 'video', 
                            'name': name, 
                            'videoUrl': videoUrl, 
                            'thumbnail': '', 
                            'subject_name': subject_name,
                            'startTime': startTime
                        })
        
            for homework in data_item.get('homeworkIds', []):
                for attachment in homework.get('attachmentIds', []):
                    url = attachment.get('baseUrl') + attachment.get('key')
                    if url:
                        content.append({'type': 'note', 'name': homework.get('topic'), 'url': url, 'subject_name': subject_name})
        
            dpp = data_item.get('dpp')
            if dpp:
                for homework in dpp.get('homeworkIds', []):
                    for attachment in homework.get('attachmentIds', []):
                        url = attachment.get('baseUrl') + attachment.get('key')
                        if url:
                            content.append({'type': 'note', 'name': homework.get('topic'), 'url': url, 'subject_name': subject_name})
        return content

async def get_todays_schedule(batch_id, token):
    if USE_DATA_API and token:
        data = call_data_api('today_schedule', token=token, batch_id=batch_id)
        if data and data.get('success'):
            schedule_data = data.get('data', [])
            if schedule_data:
                return map_proxy_schedule_items(schedule_data, batch_id)

    if token and is_penpencil_token(token):
        headers = get_headers(token)
        async with await create_session() as session:
            url = f"https://api.penpencil.co/v1/batches/{batch_id}/todays-schedule"
            data = await fetch_data(session, url, headers, method='GET')
            all_content = []

            if data and data.get("success") and data.get("data"):
                tasks = []
                for item in data['data']:
                    schedule_id = item.get('_id')
                    subject_id = item.get('batchSubjectId')
                    tasks.append(get_schedule_details(batch_id, token, subject_id, schedule_id))
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, list):
                        all_content.extend(result)
        return all_content
    return []
    

def fetch_access_token():
    """Token from .env / Vercel — auto-loaded, no key generation."""
    from lib.pw_proxy import get_access_token
    env_token = get_access_token()
    if env_token:
        return env_token

    try:
        response = requests.get(TOKEN_API_URL, timeout=50)
        if response.status_code == 200:
            data = response.json()
            return data.get('access_token') or data.get('token')
        logging.warning(f"Token API returned {response.status_code}")
    except Exception as e:
        logging.warning(f"Token fetch failed: {e}")
    return None


@app.before_request
def ensure_token():
    """Refresh session token when possible; never block page loads on token failure."""
    if request.endpoint == 'static':
        return

    token = session.get('token')
    if token and validate_token(token):
        return

    new_token = fetch_access_token()
    if new_token:
        session.permanent = True
        session['token'] = new_token
        return

    if token:
        logging.warning("Keeping existing session token after validation/refresh failed")
        return

    logging.warning("No access token available; pages load but PenPencil API calls may fail")

def validate_token(token):
    """Accept PenPencil bearer tokens or StudyStark-style proxy tokens."""
    if is_penpencil_token(token):
        return True
    if USE_DATA_API and token and len(token) > 80:
        return True
    return False

@app.route('/')
def home():
    """Home route - redirect to batches"""
    return redirect(url_for('batches'))

@app.route('/api/batches')
def get_batches():
    """Get batches with pagination and search functionality"""
    try:
        page = int(request.args.get('page', 0))
        limit = int(request.args.get('limit', 20))
        search = request.args.get('search', '').strip().lower()
        
        # Load all batches
        all_batches = load_batches()
        
        # Add encoded_id to each batch
        for batch in all_batches:
            batch['encoded_id'] = encode_params(batch['_id'])
        
        # Filter batches based on search term
        if search:
            filtered_batches = []
            for batch in all_batches:
                # Search in batch name (case insensitive)
                if search in batch.get('name', '').lower():
                    filtered_batches.append(batch)
            batches_to_paginate = filtered_batches
        else:
            batches_to_paginate = all_batches
        
        # Calculate pagination
        start_index = page * limit
        end_index = start_index + limit
        paginated_batches = batches_to_paginate[start_index:end_index]
        
        return jsonify({
            'success': True,
            'batches': paginated_batches,
            'total': len(batches_to_paginate),
            'page': page,
            'limit': limit,
            'has_more': end_index < len(batches_to_paginate)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'batches': []
        }), 500

@app.route('/api/favourite-batches', methods=['POST'])
def get_favourite_batches():
    """Get batch details for favourite batch IDs"""
    try:
        data = request.get_json()
        batch_ids = data.get('batch_ids', [])
        
        if not batch_ids:
            return jsonify({
                'success': True,
                'batches': []
            })
        
        # Load all batches
        all_batches = load_batches()
        
        # Filter batches that are in favourites and add encoded_id
        favourite_batches = []
        for batch in all_batches:
            if batch.get('_id') in batch_ids:
                batch['encoded_id'] = encode_params(batch['_id'])
                favourite_batches.append(batch)
        
        # Maintain the order of favourite_batches as per batch_ids
        ordered_batches = []
        for batch_id in batch_ids:
            for batch in favourite_batches:
                if batch.get('_id') == batch_id:
                    ordered_batches.append(batch)
                    break
        
        return jsonify({
            'success': True,
            'batches': ordered_batches
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'batches': []
        }), 500

# Update your existing batches route to work with the new API
@app.route('/batches')
def batches():
    """Render batches page template"""
    return render_template('batches.html')

# Your existing favourite routes remain the same
@app.route('/api/get-favourites')
def get_favourites():
    """Get user's favourite batch IDs from session"""
    try:
        favourites = session.get('favourite_batches', [])
        return jsonify({
            'success': True,
            'favourites': favourites
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/toggle-favourite', methods=['POST'])
def toggle_favourite():
    """Toggle batch favourite status in session"""
    try:
        data = request.get_json()
        batch_id = data.get('batch_id')
        
        if not batch_id:
            return jsonify({
                'success': False,
                'error': 'Batch ID is required'
            }), 400
        
        favourites = session.get('favourite_batches', [])
        
        if batch_id in favourites:
            favourites.remove(batch_id)
            action = 'removed'
        else:
            favourites.append(batch_id)
            action = 'added'
        
        session['favourite_batches'] = favourites
        session.permanent = True
        
        return jsonify({
            'success': True,
            'action': action,
            'message': f'Batch {action} {"to" if action == "added" else "from"} favourites'
        })
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/data/<encoded_params>')
def subjects(encoded_params):
    params = decode_params(encoded_params)
    
    batch_id = params.get('b')
    if not batch_id:
        return jsonify({"success": False, "data": {"message": "Invalid parameters"}}), 400
    
    token = session.get('token')
    subjects = run_async_optimized(fetch_subjects_async(batch_id, token))
    today_schedule = run_async_optimized(get_todays_schedule(batch_id, token))
    
    return render_template('subjects.html', subjects=subjects, today_schedule=today_schedule, batch_id=batch_id)


def encrypt_video_data(data):
    key = "6f8cce39rarestudy40fca6b3".encode('utf-8')[:16]
    iv = "4a3bfc920rarestudyb67c567".encode('utf-8')[:16]
    
    if isinstance(data, dict):
        data_str = json.dumps(data)
    else:
        data_str = str(data)
    
    padded_text = pad(data_str.encode('utf-8'), AES.block_size)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    ciphertext = cipher.encrypt(padded_text)
    return b64encode(ciphertext).decode('utf-8')

@app.route('/media/<encoded_params>')
def video_player(encoded_params):
    params = decode_params(encoded_params)
    
    url = params.get('url')
    parentId = params.get('parentId')
    childId = params.get('childId')
    
    if not all([url, parentId, childId]):
        return jsonify({"success": False, "data": {"message": "No Data Found"}}), 400
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"success": True, "data": {"message": "Media request processed"}})
    
    # Check cache first
    try:
        cache_response = requests.get(f"https://cachevideo-0c4774b4d7c4.herokuapp.com/get/cache-video?childId={childId}", verify=False)
        
        if cache_response.status_code == 200:
            cached_data = cache_response.json()
            cached_video = cached_data.get('video')
            
            if cached_video:
                # Check if URL types are different (m3u8 vs mpd)
                cached_url = cached_video.get('data', {}).get('url', '') if 'data' in cached_video else cached_video.get('url', '')
                
                if cached_url and url:
                    cached_is_m3u8 = '.m3u8' in cached_url
                    current_is_mpd = '.mpd' in url
                    cached_is_mpd = '.mpd' in cached_url
                    current_is_m3u8 = '.m3u8' in url
                    
                    # If URL type hasn't changed, check if URL is still valid
                    if not ((cached_is_m3u8 and current_is_mpd) or (cached_is_mpd and current_is_m3u8)):
                        try:
                            url_check = requests.head(cached_url, verify=False)
                            if url_check.status_code == 200:
                                logging.info(f"Using cached video data for childId: {childId}")
                                video_data = cached_video.get('data', {}) if 'data' in cached_video else cached_video
                                encrypted_video = encrypt_video_data(video_data)
                                return render_template('drmplayer.html', encrypted_video=encrypted_video)
                        except:
                            logging.info(f"Cached URL expired for childId: {childId}")
                    else:
                        logging.info(f"URL type changed for childId: {childId}")
    except:
        logging.info(f"No cache found for childId: {childId}")
    
    # Fetch new video data
    logging.info(f"Fetching new video data for childId: {childId}")
    try:
        response = requests.get(
            f"https://pwpaidtoken-dd67b476c95f.herokuapp.com/video/process?url={url}&parentId={parentId}&childId={childId}", 
            verify=False
        )
        response.raise_for_status()
        video_response = response.json()
        
        # Fix success field to be boolean True
        if 'success' in video_response:
            video_response['success'] = True
        
        video = video_response.get('data', {})
        
        # Cache the new data
        try:
            cache_payload = {"childId": childId, "video": video_response}
            requests.post("https://cachevideo-0c4774b4d7c4.herokuapp.com/data", json=cache_payload, verify=False)
            logging.info(f"Video data cached for childId: {childId}")
        except:
            logging.error(f"Failed to cache data for childId: {childId}")
        
    except Exception as e:
        logging.error(f"Error fetching video data: {e}")
        return jsonify({"success": False, "data": {"message": "No Data Found"}}), 500
    
    encrypted_video = encrypt_video_data(video)
    
    return render_template('drmplayer.html', encrypted_video=encrypted_video)


@app.route('/content/<encoded_params>')
def chapters(encoded_params):
    params = decode_params(encoded_params)
        
    batch_id = params.get('b')
    subject_id = params.get('s')
    
    if not batch_id or not subject_id:
        return jsonify({"success": False, "data": {"message": "Invalid parameters"}}), 400
    
    token = session.get('token')
    chapters = run_async_optimized(fetch_chapters_async(batch_id, subject_id, token))
    return render_template('chapters.html', chapters=chapters, batch_id=batch_id, subject_id=subject_id)

@app.route('/api/v1/<action>')
def api_v1(action):
    from lib.pw_proxy import proxy_action
    result = proxy_action(action, **request.args)
    return jsonify(result or {'success': False, 'message': 'Proxy error'})


@app.route('/api/token-status')
def api_token_status():
    from lib.pw_proxy import proxy_action
    return jsonify(proxy_action('token_status', **request.args))


@app.route('/watch')
def watch():
    from lib.pw_proxy import build_studystark_play_url
    batch_id = request.args.get('batch_id', '')
    subject_id = request.args.get('subject_id', '')
    topic_id = request.args.get('topic_id', '')
    schedule_id = request.args.get('schedule_id', '')
    video_id = request.args.get('video_id', '')
    title = request.args.get('title', 'Lecture')
    external = build_studystark_play_url(batch_id, subject_id, topic_id, schedule_id)
    return render_template(
        'watch.html',
        batch_id=batch_id,
        subject_id=subject_id,
        topic_id=topic_id,
        schedule_id=schedule_id,
        video_id=video_id,
        title=title,
        external_play_url=external,
    )


@app.route('/stream/<encoded_params>/<section>')
def section_content(encoded_params, section):
    params = decode_params(encoded_params)
        
    batch_id = params.get('b')
    subject_id = params.get('s')
    chapter_id = params.get('c')
    
    if not batch_id or not subject_id or not chapter_id:
        return jsonify({"success": False, "data": {"message": "Invalid parameters"}}), 400
    
    if section not in ['videos', 'notes', 'DppNotes', 'DppVideos']:
        section = 'videos'
    
    token = session.get('token')
    data = run_async_optimized(fetch_chapter_content_async(batch_id, subject_id, chapter_id, section, token))
    
    sections = {'videos': 'Videos', 'notes': 'Notes', 'DppNotes': 'Dpp Notes', 'DppVideos': 'Dpp Videos'}
        
    return render_template('section.html', data=data, section=section, 
                         batch_id=batch_id, subject_id=subject_id, chapter_id=chapter_id, sections=sections)

if __name__ == '__main__':
    app.run(debug=True, threaded=True)