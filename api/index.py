"""
Vercel serverless entry — StudyStark proxy API.
Deploy: set PW_ACCESS_TOKEN in Vercel env, redeploy.

GET /api?action=topics&batch_id=...&subject_id=...&page=1
GET /api?action=token_status
GET /api?action=playlist&batch_id=...&subject_id=...&topic_id=...&schedule_id=...
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.pw_proxy import proxy_action, ACTIONS, EXTRA_ENDPOINTS

ALLOWED = ACTIONS | set(EXTRA_ENDPOINTS.keys()) | {'token_status'}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._handle()

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _handle(self):
        parsed = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        action = params.pop('action', None)

        if not action:
            self._json(400, {
                'success': False,
                'message': 'Missing action',
                'actions': sorted(ALLOWED),
            })
            return

        if action not in ALLOWED:
            self._json(400, {'success': False, 'message': f'Invalid action: {action}'})
            return

        try:
            result = proxy_action(action, **params)
            if result is None:
                self._json(502, {'success': False, 'message': 'Upstream unavailable'})
            else:
                self._json(200, result)
        except Exception as exc:
            self._json(500, {'success': False, 'message': str(exc)})

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
