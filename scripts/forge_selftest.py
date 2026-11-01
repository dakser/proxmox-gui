#!/usr/bin/env python3
"""Autoprueba de orchestrator/forge.py contra un Forgejo falso en 127.0.0.1 (sin red externa)."""
import json, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "orchestrator"))
import forge  # noqa: E402

S = {"prs": [], "status": {}, "calls": [], "auth": set()}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def _body(self):
        n = int(self.headers.get("Content-Length") or 0); return json.loads(self.rfile.read(n) or b"{}")
    def handle_any(self, m):
        S["auth"].add(self.headers.get("Authorization")); S["calls"].append((m, self.path))
        p = self.path.split("?")[0]
        if m == "POST" and p == "/api/v1/repos/o/r/pulls":
            b = self._body()
            if any(x["head"]["ref"] == b["head"] for x in S["prs"]):
                return self._send(409, {"message": "ya existe"})
            S["prs"].append({"number": len(S["prs"]) + 1, "title": b["title"], "head": {"ref": b["head"]}, "html_url": "http://x/pr"})
            return self._send(201, S["prs"][-1])
        if m == "GET" and p == "/api/v1/repos/o/r/pulls":
            return self._send(200, S["prs"] if "page=1" in self.path else [])
        if m == "PATCH" and p.startswith("/api/v1/repos/o/r/pulls/"):
            n = int(p.rsplit("/", 1)[1]); S["prs"][n - 1]["title"] = self._body()["title"]; return self._send(201, {})
        if m == "GET" and p.startswith("/api/v1/repos/o/r/commits/") and p.endswith("/status"):
            sha = p.split("/")[-2]; return self._send(200, {"statuses": S["status"].get(sha, [])})
        self._send(404, {"message": "no previsto"})
    def do_GET(self): self.handle_any("GET")
    def do_POST(self): self.handle_any("POST")
    def do_PATCH(self): self.handle_any("PATCH")

def check(name, cond):
    print(("ok    " if cond else "FALLA ") + name)
    if not cond: check.bad += 1
check.bad = 0

srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
f = forge.Forgejo(f"http://127.0.0.1:{srv.server_port}", "o/r", "TOK")
ok, _ = f.pr_create_draft("master", "integ", "feat: x", "cuerpo")
check("PR en borrador lleva el prefijo WIP", ok and S["prs"][0]["title"] == "WIP: feat: x")
ok, msg = f.pr_create_draft("master", "integ", "feat: x", "cuerpo")
check("PR repetido se informa como no creado", not ok and "409" in msg)
ok, _ = f.pr_ready("integ")
check("pr_ready quita el prefijo WIP", ok and S["prs"][0]["title"] == "feat: x")
check("pr_ready sin PR devuelve error", not f.pr_ready("otra")[0])
check("pr_ready es idempotente", f.pr_ready("integ")[0] and S["prs"][0]["title"] == "feat: x")
check("sin registros de CI = pendiente", f.ci_state("aaa") == ("pending", []))
S["status"]["bbb"] = [{"context": "ci/shell", "status": "success"}, {"context": "ci/backend", "status": "pending"}]
check("uno pendiente = pendiente", f.ci_state("bbb")[0] == "pending")
S["status"]["ccc"] = [{"context": "ci/shell", "status": "success"}, {"context": "ci/backend", "status": "warning"}]
check("success+warning = verde", f.ci_state("ccc")[0] == "success")
S["status"]["ddd"] = [{"context": "ci/shell", "status": "success"}, {"context": "ci/scans", "status": "failure", "description": "boom", "target_url": "http://x/run/3"}]
st, lines = f.ci_state("ddd")
check("un fallo = fallo y se informa cuál", st == "failure" and len(lines) == 1 and "ci/scans" in lines[0] and "http://x/run/3" in lines[0])
S["status"]["eee"] = [{"context": "ci/a", "status": "error"}, {"context": "ci/b", "status": "pending"}]
check("error con otro pendiente = fallo", f.ci_state("eee")[0] == "failure")
check("todas las llamadas llevan el token", S["auth"] == {"token TOK"})
env = f.git_env()
check("git_env no mete el token en argv, solo en variables", env["GIT_CONFIG_VALUE_0"] == "Authorization: token TOK" and env["GIT_CONFIG_KEY_0"] == "http.extraHeader")
try:
    forge.Forgejo("http://127.0.0.1:1", "o/r", "T", timeout=2).ci_state("x"); check("forja caída lanza ForgeError", False)
except forge.ForgeError:
    check("forja caída lanza ForgeError", True)
try:
    forge.Forgejo("", "r", "T"); check("configuración incompleta se rechaza", False)
except forge.ForgeError:
    check("configuración incompleta se rechaza", True)
sys.exit(1 if check.bad else 0)
