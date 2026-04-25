#!/usr/bin/env python3
"""
Minimal HTTP server for MQTT broker configuration.
Serves a mobile-friendly web form; on submit, writes the mqtt section
of config.json and signals completion via a threading.Event.
"""

import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

CONFIG_FILE = '/userapp/smarthome-switch/config.json'
PORT = 8080

_HTML_FORM = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NanoKVM — MQTT Setup</title>
<style>
  *, *::before, *::after { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f5f5f7;
    margin: 0; padding: 24px 16px;
    color: #1c1c1e;
  }
  .card {
    background: #fff;
    border-radius: 16px;
    padding: 28px 24px;
    max-width: 420px;
    margin: 0 auto;
    box-shadow: 0 2px 12px rgba(0,0,0,.10);
  }
  h1 { font-size: 22px; margin: 0 0 6px; }
  .sub { color: #636366; font-size: 14px; margin: 0 0 24px; line-height: 1.5; }
  label { display: block; font-size: 13px; font-weight: 600;
          color: #636366; margin-bottom: 4px; margin-top: 16px; }
  label:first-of-type { margin-top: 0; }
  input[type=text], input[type=number], input[type=password] {
    width: 100%; padding: 12px 14px;
    border: 1.5px solid #d1d1d6;
    border-radius: 10px;
    font-size: 16px;
    background: #fafafa;
    outline: none;
    transition: border-color .15s;
  }
  input:focus { border-color: #007aff; background: #fff; }
  .hint { font-size: 12px; color: #8e8e93; margin-top: 4px; }
  .row { display: flex; gap: 12px; }
  .row > div { flex: 1; }
  .row > div:last-child { max-width: 110px; }
  button {
    margin-top: 28px; width: 100%;
    padding: 15px;
    background: #007aff;
    color: #fff;
    border: none; border-radius: 12px;
    font-size: 17px; font-weight: 600;
    cursor: pointer;
    transition: background .15s;
  }
  button:hover { background: #0062cc; }
  button:active { background: #004999; }
</style>
</head>
<body>
<div class="card">
  <h1>MQTT Setup</h1>
  <p class="sub">
    Enter your MQTT broker details. Leave username and password blank
    for anonymous connections (common with the Mosquitto add-on defaults).
  </p>
  <form method="POST" action="/save">
    <div class="row">
      <div>
        <label for="broker">Broker address</label>
        <input id="broker" type="text" name="broker"
               value="homeassistant.local"
               placeholder="homeassistant.local" autocapitalize="none">
        <div class="hint">Hostname or IP of your MQTT broker</div>
      </div>
      <div>
        <label for="port">Port</label>
        <input id="port" type="number" name="port" value="1883" min="1" max="65535">
      </div>
    </div>

    <label for="username">Username <span style="font-weight:400;color:#8e8e93">(optional)</span></label>
    <input id="username" type="text" name="username"
           placeholder="Leave blank for anonymous" autocapitalize="none" autocorrect="off">

    <label for="password">Password <span style="font-weight:400;color:#8e8e93">(optional)</span></label>
    <input id="password" type="password" name="password" placeholder="Leave blank for anonymous">

    <button type="submit">Save &amp; Continue</button>
  </form>
</div>
</body>
</html>
"""

_HTML_SUCCESS = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NanoKVM — MQTT Setup</title>
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f5f5f7; margin: 0; padding: 40px 16px;
    text-align: center; color: #1c1c1e;
  }
  .check { font-size: 72px; line-height: 1; margin-bottom: 16px; }
  h1 { font-size: 24px; margin: 0 0 10px; }
  p { color: #636366; font-size: 15px; max-width: 300px; margin: 0 auto; line-height: 1.5; }
</style>
</head>
<body>
  <div class="check">✓</div>
  <h1>Configured!</h1>
  <p>MQTT settings saved. You can close this page — the NanoKVM will continue setup automatically.</p>
</body>
</html>
"""


class _ConfigHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress console noise

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(_HTML_FORM.encode())

    def do_POST(self):
        if self.path != '/save':
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8', errors='replace')
        params = urllib.parse.parse_qs(body, keep_blank_values=True)

        def _get(key, default=''):
            vals = params.get(key)
            return vals[0].strip() if vals else default

        broker = _get('broker', 'homeassistant.local') or 'homeassistant.local'
        try:
            port = int(_get('port', '1883'))
            if not (1 <= port <= 65535):
                port = 1883
        except ValueError:
            port = 1883
        username = _get('username', '')
        password = _get('password', '')

        # Load existing config so we only update the mqtt section
        try:
            with open(CONFIG_FILE) as f:
                config = json.load(f)
        except Exception:
            config = {}

        config['mqtt'] = {
            'broker':   broker,
            'port':     port,
            'username': username,
            'password': password,
        }

        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)

        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(_HTML_SUCCESS.encode())

        if hasattr(self.server, '_done_event'):
            self.server._done_event.set()


class ConfigServer:
    """Lightweight HTTP server for one-time MQTT credential collection."""

    def __init__(self, port: int = PORT):
        self._port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._done = threading.Event()

    def start(self):
        self._server = HTTPServer(('', self._port), _ConfigHandler)
        self._server._done_event = self._done
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the form is submitted. Returns True when done."""
        return self._done.wait(timeout=timeout)

    @property
    def is_done(self) -> bool:
        return self._done.is_set()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None
