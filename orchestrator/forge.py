"""Forja del orquestador: PR en borrador, estado del CI y registro de fallos.

Backend por defecto: Forgejo (API v1, token FORGEJO_TOKEN). El borrador se expresa con el prefijo "WIP: " del título, que es
lo que Forgejo reconoce. Sin dependencias fuera de la biblioteca estándar. El token solo viaja en la cabecera Authorization
(de la API) o en variables de entorno de git; nunca en la línea de comandos.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

WIP = "WIP: "
GOOD = ("success", "warning")      # "warning" de Forgejo no es un fallo
BAD = ("failure", "error")


class ForgeError(Exception):
    pass


class Forgejo:
    def __init__(self, url: str, repo: str, token: str, timeout: int = 30):
        if not url.startswith(("https://", "http://")) or "/" not in repo:
            raise ForgeError("forge_url y forge_repo (dueño/repo) son obligatorios en [git]")
        self.url, self.repo, self.token, self.timeout = url.rstrip("/"), repo, token, timeout

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        req = urllib.request.Request(f"{self.url}/api/v1{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"token {self.token}", "Content-Type": "application/json",
                                              "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except (ValueError, TypeError):
                return e.code, {"message": raw[:200].decode("utf-8", "replace")}
        except (urllib.error.URLError, OSError) as e:
            raise ForgeError(f"no alcanzo la forja {self.url}: {e}") from e

    def git_env(self) -> dict:
        """Autenticación de git por HTTPS sin dejar el token en argv ni en el remoto."""
        return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
                "GIT_CONFIG_VALUE_0": f"Authorization: token {self.token}", "GIT_TERMINAL_PROMPT": "0"}

    def pr_create_draft(self, base: str, head: str, title: str, body: str) -> tuple[bool, str]:
        code, data = self.call("POST", f"/repos/{self.repo}/pulls", {"base": base, "head": head, "title": WIP + title, "body": body})
        if code in (200, 201):
            return True, str(data.get("html_url", "")) if isinstance(data, dict) else ""
        return False, f"{code}: {data.get('message', data) if isinstance(data, dict) else data}"

    def _find_pr(self, head: str) -> dict | None:
        for page in range(1, 6):
            code, data = self.call("GET", f"/repos/{self.repo}/pulls?state=open&limit=50&page={page}")
            if code != 200 or not isinstance(data, list) or not data:
                return None
            for pr in data:
                if pr.get("head", {}).get("ref") == head:
                    return pr
        return None

    def pr_ready(self, head: str) -> tuple[bool, str]:
        pr = self._find_pr(head)
        if not pr:
            return False, f"no hay PR abierto desde {head}"
        title = str(pr.get("title", ""))
        if not title.upper().startswith(WIP.strip().upper()):
            return True, "ya estaba listo"
        new = title[len(WIP.strip()):].lstrip() if title.upper().startswith("WIP:") else title
        code, data = self.call("PATCH", f"/repos/{self.repo}/pulls/{pr['number']}", {"title": new})
        return code in (200, 201), f"{code}"

    def ci_state(self, sha: str) -> tuple[str, list[str]]:
        """('success'|'failure'|'pending', líneas con el contexto, la descripción y el enlace de cada comprobación)."""
        code, data = self.call("GET", f"/repos/{self.repo}/commits/{urllib.parse.quote(sha)}/status")
        if code != 200 or not isinstance(data, dict):
            return "pending", []
        statuses = data.get("statuses") or []
        if not statuses:
            return "pending", []          # el CI aún no se ha registrado para este commit
        states = [s.get("status") or s.get("state") for s in statuses]
        lines = [f"{s.get('context')}: {s.get('status') or s.get('state')} — {s.get('description', '')} {s.get('target_url', '')}".strip()
                 for s in statuses]
        if any(s in BAD for s in states):
            return "failure", [ln for ln, s in zip(lines, states) if s in BAD]
        if all(s in GOOD for s in states):
            return "success", []
        return "pending", []
