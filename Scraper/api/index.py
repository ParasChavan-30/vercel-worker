from http.server import BaseHTTPRequestHandler
import json
import os

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Read environment variables set in Vercel
        remote_api_url = os.environ.get("REMOTE_API_URL")
        worker_id = os.environ.get("WORKER_ID", "vercel_platform4")
        
        # Simple health check response
        response_data = {
            "status": "online",
            "platform": "vercel-python",
            "worker_id": worker_id,
            "connected_to": remote_api_url
        }

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response_data).encode('utf-8'))
