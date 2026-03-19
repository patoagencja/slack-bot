import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
HTML_PATH = os.path.join(os.path.dirname(__file__), "status.html")
class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/botsebolstatus":
            try:
                with open(HTML_PATH, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"brak pliku status.html")
        elif self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Sebol dziala")
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, format, *args):
        pass
def start_status_server_thread():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), StatusHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"[STATUS] Serwer dziala na porcie {port}")
