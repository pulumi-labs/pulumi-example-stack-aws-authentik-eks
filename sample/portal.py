# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""A protected onboarding page; oauth2-proxy owns authentication and authorization."""
# Run: python -B sample/portal.py (inside its pod, behind oauth2-proxy).

from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from http import HTTPStatus


class Portal(BaseHTTPRequestHandler):
    """Serve only the protected onboarding page on pod loopback."""

    def do_GET(self) -> None:
        """Display the authenticated identity after the proxy's access checks."""
        if self.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        username = self.headers.get("X-Forwarded-Preferred-Username", "")
        email = self.headers.get("X-Forwarded-Email", "")
        if not username or not email:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        page = (
            "<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Team onboarding</title><h1>Team onboarding</h1>"
            f"<p>Signed in as <strong>{escape(username)}</strong> "
            f"({escape(email)})</p><h2>Your first day</h2>"
            "<ol><li>Read the team handbook.</li>"
            "<li>Meet your onboarding partner.</li>"
            "<li>Request access to the team workspace.</li></ol>"
            "<p>This checklist is available to onboarding-members only.</p>"
            "<a href='/oauth2/sign_out'>Clear this application's session</a></html>"
        ).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(page)


if __name__ == "__main__":
    with HTTPServer(("127.0.0.1", 8080), Portal) as server:
        server.serve_forever()
