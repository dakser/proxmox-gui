#!/usr/bin/env python3
"""Orquestador del modo autónomo de proxmox-gui (docs/plan.md). Sin LLM: decide ejecutando comandos.

Comandos:
  init                 Verifica el kit (archivos, autopruebas, manifiesto y tareas) sin tocar nada.
  seal                 Sella el material de aceptación: guarda su huella (hacer dos veces: debe coincidir).
  verify               Comprueba que la huella sellada sigue igual.
  run [--phase F3]     Ejecuta todas las fases seguidas: tareas, puertas y revisiones. Cada fase aprobada se integra en
                       integracion/v0.1.1 (nunca en master); al final deja un PR listo. Solo se detiene ante un bloqueo real.
  dry-run              Muestra la secuencia de sesiones sin llamar a ningún agente.
  status               Estado por fase y tarea, gasto acumulado.
  once TAREA           Ejecuta una sola tarea (útil para depurar).
  reset TAREA          Devuelve una tarea a pendiente (por ejemplo, tras arreglar un bloqueo).

Solo usa la biblioteca estándar de Python 3.11+.
Códigos de salida: 0 terminado, 2 uso incorrecto, 3 detenido (bloqueo, presupuesto o puerta).
"""
from __future__ import annotations
import os as _os_cap  # KIT: tope de salida de OpenCode (el razonamiento cuenta dentro)
_os_cap.environ.setdefault("OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX", "64000")

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE_FILE = HERE / "state.json"
LOG_DIR = HERE / "logs"
sys.path.insert(0, str(ROOT / "scripts"))
import accept_check  # noqa: E402  (verificador del manifiesto; parte del kit)

# Material de aceptación: solo lo escribe el autor de pruebas; su huella se sella y el implementador no lo toca.
PROTECTED = ("tests/acceptance/", "backend/tests/acceptance/", "frontend/tests/acceptance/",
             "deploy/tests/acceptance/", "lab/scenarios/", "gates.toml")
# Kit: lo cambia solo el humano con un commit "KIT: ..." (misma lista que scripts/lock_check.py y .claude/hooks/guard.py).
FROZEN = ("docs/spec/", "docs/plan.md", "orchestrator/", ".claude/", ".opencode/", "opencode.json",
          "AGENTS.md", "CLAUDE.md", "Makefile", ".github/", ".gitleaks.toml",
          "scripts/gate.py", "scripts/gate_selftest.py", "scripts/lock_check.py", "scripts/accept_check.py",
          "scripts/accept_check_selftest.py", "scripts/guard_selftest.py", "scripts/release-sign.sh",
          "deploy/release-signers", "lab/lib.sh", "lab/run.sh", "lab/selftest.sh", "lab/selftest/",
          "reports/lab2/")
# What each role may leave changed in the working tree after its session.
ROLE_WRITES = {
    "test_author": ("backend/tests/acceptance/", "frontend/tests/acceptance/", "deploy/tests/acceptance/",
                    "lab/scenarios/", "docs/BLOCKERS.md", "docs/TRACEABILITY.md"),
    "reviewer": ("reports/",),
    "security_reviewer": ("reports/",),
}
RATE_LIMIT_RE = re.compile(
    r"(usage|rate|subscription|weekly|monthly|daily|hourly|plan)[ _-]?limit|quota|limit (reached|exceeded)"
    r"|too many requests|\b429\b|insufficient[ _](credits|balance|quota)|out of credits"
    r"|exceeded your", re.I)
# Pruebas que no pueden fallar o que se desactivan: el autor de pruebas no puede entregarlas.
WEAK_RE = re.compile(r"pytest\.(skip|mark\.(skip|xfail))|\bxfail\b|\b(it|test|describe)\.(skip|todo)\b|\bassert True\b"
                     r"|expect\(true\)\.toBe\(true\)|expect\(1\)\.toBe\(1\)|\bTODO\b|\bFIXME\b")
# Errores de importación que significan "esta API todavía no existe" (lo repara el implementador).
MISSING_API_RE = re.compile(r"ModuleNotFoundError|ImportError|cannot import name|Failed to resolve import|"
                            r"Cannot find module|does not provide an export|is not a function|is not defined")
BROKEN_TEST_RE = re.compile(r"SyntaxError|Transform failed|Unexpected token|IndentationError|TabError")
IGNORED = ("orchestrator/state.json", "orchestrator/logs/", "reports/lab/", "reports/prompts/")
DOC_STUBS = {
    "docs/BLOCKERS.md": "# Bloqueos\n\n",
    "docs/TRACEABILITY.md": "# Trazabilidad\n\n| Regla | Archivo | Prueba | Estado |\n| --- | --- | --- | --- |\n",
}
REVIEW_FILES = {"reviewer": "review-{phase}.md", "security_reviewer": "review-sec-{phase}.md"}

_AGENT_CHILDREN: list = []  # sesiones de agente vivas (cada una en su propio grupo de procesos)


def _forward_term(signum, frame):  # noqa: ARG001
    """Las sesiones corren en su propio grupo: al detener el orquestador, detenerlas también."""
    for p in list(_AGENT_CHILDREN):
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    sys.exit(128 + signum)


class Halt(Exception):
    """Stops the run; the message goes to the notification."""


@dataclass
class Task:
    id: str
    phase: str
    title: str
    role: str
    acceptance: bool
    hard: bool = False

    @property
    def slug(self) -> str:
        return self.id.lower().replace(".", "_")


@dataclass
class SessionResult:
    ok: bool
    text: str
    cost: float
    log: Path


def starts(path: str, prefixes) -> bool:
    return any(path == p.rstrip("/") or path.startswith(p) for p in prefixes)


def tail(text: str, n: int = 80) -> str:
    return "\n".join(text.splitlines()[-n:])


class Orchestrator:
    def __init__(self, cfg_path: Path):
        self.cfg = tomllib.loads(cfg_path.read_text())
        raw = tomllib.loads((HERE / "tasks.toml").read_text())["task"]
        self.tasks = [Task(**t) for t in raw]
        self.phases = list(dict.fromkeys(t.phase for t in self.tasks))
        self.state = self._load_state()
        self.dry = False
        LOG_DIR.mkdir(exist_ok=True)

    # ---------- state ----------
    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
        return {"tasks": {}, "phases": {}, "spend": {"total": 0.0}, "locked": {}, "events": []}

    def save(self) -> None:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, ensure_ascii=False))
        tmp.replace(STATE_FILE)

    def tstate(self, task: Task) -> dict:
        return self.state["tasks"].setdefault(task.id, {"status": "pending", "attempts": 0,
                                                        "tests": "pending"})

    def event(self, msg: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{stamp}] {msg}", flush=True)
        self.state["events"].append(f"{stamp} {msg}")
        self.state["events"] = self.state["events"][-500:]
        self.save()

    # ---------- shell and git ----------
    def secrets(self) -> dict:
        """KEY=VALUE pairs from [secrets] file. Only the notify/snapshot hooks receive them."""
        path = self.cfg.get("secrets", {}).get("file", "")
        out: dict = {}
        if not path or not os.path.exists(path):
            return out
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.removeprefix("export ").strip()
            out[k] = v.strip().strip('"').strip("'")
        return out

    def base_env(self) -> dict:
        """Environment for anything that runs agent-written code or agents themselves:
        never carries the hook secrets, even if someone exported them in the shell."""
        drop = set(self.secrets()) | set(self.cfg.get("secrets", {}).get("scrub", []))
        return {k: v for k, v in os.environ.items() if k not in drop}

    def sh(self, cmd: str, timeout: int = 3600, env: dict | None = None,
           with_secrets: bool = False) -> tuple[bool, str]:
        base = {**os.environ, **self.secrets()} if with_secrets else self.base_env()
        try:
            p = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL,
                               timeout=timeout, env={**base, **(env or {})})
            return p.returncode == 0, p.stdout + "\n" + p.stderr
        except subprocess.TimeoutExpired:
            return False, f"TIMEOUT tras {timeout} s: {cmd}"

    def git(self, *args: str, check: bool = True) -> str:
        g = self.cfg["git"]
        env = {**os.environ, "GIT_AUTHOR_NAME": g["author_name"], "GIT_AUTHOR_EMAIL": g["author_email"],
               "GIT_COMMITTER_NAME": g["author_name"], "GIT_COMMITTER_EMAIL": g["author_email"]}
        p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, env=env)
        if check and p.returncode != 0:
            raise Halt(f"git {' '.join(args)} falló: {p.stderr.strip()}")
        return p.stdout

    def changed_files(self) -> list[str]:
        if self.dry:
            return []
        out = self.git("status", "--porcelain", "--untracked-files=all")
        files = []
        for line in out.splitlines():
            path = line[3:].split(" -> ")[-1].strip().strip('"')
            if not starts(path, IGNORED):
                files.append(path)
        return files

    def commit(self, msg: str) -> bool:
        if self.dry:
            return True
        if not msg.startswith(("T ", "KIT:")):
            # Running tests or the gate may rewrite protected files (golden vectors, fmt...).
            # Only the test author changes them: never sweep those changes into other commits.
            touched = [f for f in self.changed_files() if starts(f, PROTECTED + FROZEN)]
            if touched:
                self.restore_paths(PROTECTED + FROZEN)
                self.event(f"aviso: se restauraron archivos protegidos modificados fuera de su rol: {touched[:10]}")
        self.git("add", "-A")
        self.git("reset", "-q", "--", *IGNORED, check=False)
        if not self.git("diff", "--cached", "--name-only").strip():
            return False
        self.git("commit", "-q", "-m", msg)
        self.wip_push()
        return True

    def wip_push(self) -> None:
        """Empuja la rama actual tras cada commit: el trabajo nunca depende de que el LXC siga vivo."""
        g = self.cfg["git"]
        if self.dry or not g.get("push_wip", True):
            return
        branch = self.git("rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
        if not branch or branch == g["main_branch"]:
            return  # nunca se empuja a la rama principal
        ok, out = self.sh(f"git push -q -u {g.get('remote', 'origin')} {shlex.quote(branch)}", timeout=300, with_secrets=True)
        if not ok:
            self.event(f"aviso: no pude empujar {branch}: {tail(out, 3).strip()}")

    def commit_reports(self, msg: str) -> bool:
        """Commit only reports/: the gate must not sweep work in progress into its commit."""
        if self.dry:
            return True
        touched = [f for f in self.changed_files() if starts(f, PROTECTED + FROZEN)]
        if touched:
            self.restore_paths(PROTECTED + FROZEN)
            self.event(f"aviso: la puerta modificó archivos protegidos y se restauraron: {touched[:10]}")
        self.git("add", "-A", "--", "reports", check=False)
        if not self.git("diff", "--cached", "--name-only").strip():
            return False
        self.git("commit", "-q", "-m", msg)
        return True

    def restore_paths(self, prefixes) -> None:
        """Undo changes (tracked and untracked) under the given prefixes."""
        if self.dry:
            return
        for f in self.changed_files():
            if starts(f, prefixes):
                tracked = self.git("ls-files", "--error-unmatch", f, check=False).strip()
                if tracked:
                    self.git("checkout", "--", f, check=False)
                else:
                    (ROOT / f).unlink(missing_ok=True)

    def discard_all(self) -> None:
        if self.dry:
            return
        self.git("reset", "-q", "--hard")
        self.git("clean", "-fdq", "-e", "node_modules", "-e", ".venv", "-e", "orchestrator/logs",
                 "-e", "orchestrator/state.json", "-e", "reports/lab", "-e", "reports/prompts")

    def protected_digest(self) -> str:
        if self.dry:
            return "dry"
        listing = self.git("ls-tree", "-r", "HEAD", "--", *[p.rstrip("/") for p in PROTECTED],
                           check=False)
        return hashlib.sha256(listing.encode()).hexdigest()

    # ---------- budget and notifications ----------
    def add_spend(self, phase: str, cost: float) -> None:
        sp = self.state["spend"]
        sp["total"] = round(sp.get("total", 0.0) + cost, 4)
        sp[phase] = round(sp.get(phase, 0.0) + cost, 4)
        self.save()

    def check_budget(self, phase: str) -> None:
        lim, sp = self.cfg["limits"], self.state["spend"]
        if sp.get("total", 0.0) >= lim["total_budget_usd"]:
            raise Halt(f"presupuesto total alcanzado ({sp['total']:.2f} USD)")
        if sp.get(phase, 0.0) >= lim["phase_budget_usd"]:
            raise Halt(f"presupuesto de {phase} alcanzado ({sp[phase]:.2f} USD)")

    def run_hook(self, section: str, var: str, value: str) -> None:
        cmd = self.cfg.get(section, {}).get("command", "")
        if cmd and not self.dry:
            ok, out = self.sh(cmd, timeout=300, env={var: value}, with_secrets=True)
            if not ok:
                self.event(f"aviso: {section} falló: {tail(out, 5)}")

    def notify(self, msg: str) -> None:
        self.event(f"NOTIFICACIÓN: {msg}")
        self.run_hook("notify", "PGUI_MSG", f"proxmox-gui: {msg}")

    # ---------- agent sessions ----------
    def model_for(self, role: str, task: Task | None, escalate: bool = False) -> str:
        m = self.cfg["models"]
        if escalate:
            return m["escalation"]
        return m[role]

    def session(self, role: str, phase: str, label: str, prompt: str, model: str) -> SessionResult:
        if len(prompt.encode()) > 100_000:  # prompt demasiado largo para un argumento (Linux: 128 KiB)
            _pd = ROOT / "reports" / "prompts"  # ignorada por git y legible para los agentes
            _pd.mkdir(parents=True, exist_ok=True)
            _pf = _pd / ("prompt-" + re.sub(r"[^A-Za-z0-9._-]", "_", str(label)) + ".md")
            _pf.write_text(prompt)
            prompt = (f"Tus instrucciones completas para esta sesión están en {_pf.relative_to(ROOT)} "
                      f"({len(prompt.encode()) // 1024} KiB): léelas enteras antes de hacer nada y síguelas como si "
                      f"estuvieran aquí. Ese archivo es solo de lectura para ti.")
        self.check_budget(phase)
        lim, agent = self.cfg["limits"], self.cfg["agent"]
        sys_file = HERE / "prompts" / f"{role}.md"
        n = len(list(LOG_DIR.glob(f"{label}-{role}-*.json"))) + 1
        log = LOG_DIR / f"{label}-{role}-{n}.json"
        self.event(f"sesión {label} · {role} · {model} · intento {n}")
        backend = self.backend_for(role)
        self.event(f"  herramienta: {backend}")
        if self.dry:
            return SessionResult(True, "(dry-run)", 0.0, log)
        if backend == "claude":
            c = agent["claude"]
            cmd = [c["bin"], "-p", prompt, "--output-format", "json",
                   "--model", model, "--max-turns", str(lim["max_turns"]),
                   "--max-budget-usd", str(lim["session_budget_usd"]),
                   "--permission-mode", c["permission_mode"],
                   "--append-system-prompt-file", str(sys_file), *c.get("extra_args", [])]
        elif backend == "opencode":
            o = agent["opencode"]
            # OpenCode has no "append system prompt" flag: the role instructions go first in
            # the message. AGENTS.md is loaded by OpenCode itself from the repository root.
            full = sys_file.read_text() + "\n\n---\n\n" + prompt
            cmd = [o["bin"], "run", "--model", model, "--format", "json", "--title", label,
                   *(["--auto"] if o.get("auto_approve", True) else []),
                   *o.get("extra_args", []), full]
        else:
            t = agent["custom"]
            cmd = [a.format(prompt=prompt, model=model, system_file=sys_file)
                   for a in t["command_template"]]
        env = {**self.base_env(), "PGUI_ROLE": role, "PGUI_TASK": label, "CLAUDE_PROJECT_DIR": str(ROOT)}
        if backend in ("claude", "opencode"):
            env.update({k: str(v) for k, v in agent[backend].get("env", {}).items()})
        head_before = self.git("rev-parse", "HEAD", check=False).strip()
        waited, wait_s = 0, int(lim.get("rate_limit_wait_s", 1800))
        max_wait = int(float(lim.get("rate_limit_max_wait_h", 96)) * 3600)
        fb = self.cfg.get("fallback", {})
        fb_map, probe_s = fb.get("models", {}), int(fb.get("probe_minutes", 60)) * 60
        orig_model = model
        while True:
            use = orig_model
            if orig_model in fb_map and time.time() < float(self.state.get("fallback_until", 0)):
                use = fb_map[orig_model]
            if "--model" in cmd:
                cmd[cmd.index("--model") + 1] = use
            if use != model:
                self.event(f"  {label}: modelo {use}")
                model = use
            stdout, stderr, code = self.run_agent(cmd, env, backend, label, lim)
            hit = self.rate_limited(stdout, stderr)
            if not hit:
                if waited:
                    self.notify(f"{label}: el proveedor volvió a responder tras {waited // 60} min; sigo")
                break
            if use == orig_model and orig_model in fb_map:
                self.state["fallback_until"] = time.time() + probe_s
                self.save()
                (self.event if waited else self.notify)(
                    f"{label}: límite de uso en Go ({hit}). Paso a {fb_map[orig_model]} "
                    f"y vuelvo a probar Go en {probe_s // 60} min.")
                continue
            if waited >= max_wait:
                self.event(f"  {label}: límite de uso tras {waited // 3600} h de espera; me rindo")
                break
            if not waited:
                self.notify(f"{label}: límite de uso del proveedor ({hit}). Espero y reintento cada "
                            f"{wait_s // 60} min, sin gastar intentos.")
            self.event(f"  {label}: límite de uso ({hit}); espero {wait_s // 60} min")
            time.sleep(wait_s)
            waited += wait_s
            self.state["fallback_until"] = 0  # al despertar, Go primero
        # Only the orchestrator commits. If an agent committed anyway, undo the commit and keep
        # its changes in the working tree, so they go through the normal verification.
        if fb_map and orig_model in fb_map and model == orig_model and self.state.get("fallback_was_on"):
            self.state["fallback_was_on"] = False
            self.notify(f"{label}: Go volvió a responder; sigo con Go")
        if model != orig_model:
            self.state["fallback_was_on"] = True
        head_after = self.git("rev-parse", "HEAD", check=False).strip()
        if head_before and head_after != head_before:
            self.git("reset", "--soft", head_before, check=False)
            self.git("reset", "-q", check=False)
            self.event(f"aviso: {label} ({backend}) hizo commits por su cuenta; se deshicieron y se conservan sus cambios")
        text, cost = stdout, 0.0
        if backend == "claude":
            try:
                data = json.loads(stdout)
                text = str(data.get("result", ""))
                cost = float(data.get("total_cost_usd") or data.get("cost_usd") or 0.0)
                if data.get("is_error"):
                    code = code or 1
            except (json.JSONDecodeError, ValueError):
                pass
        elif backend == "opencode":
            text, cost = self.parse_opencode_events(stdout)
        log.write_text(json.dumps({"backend": backend, "cmd": cmd[:3] + ["<...>"], "prompt": prompt,
                                   "exit": code, "stdout": stdout[-200000:],
                                   "stderr": stderr[-20000:], "cost": cost},
                                  ensure_ascii=False, indent=2))
        self.add_spend(phase, cost)
        return SessionResult(code == 0, text, cost, log)

    RESUME_MSG = ("La sesión anterior se interrumpió por una falla del proveedor del modelo, no por algo tuyo. "
                  "Continúa exactamente donde ibas, con la misma tarea y las mismas reglas. Antes de seguir, "
                  "anota en docs/BLOCKERS.md lo que ya hayas medido.")

    def run_agent(self, cmd: list, env: dict, backend: str, label: str, lim: dict) -> tuple[str, str, int]:
        """Runs one agent session under the watchdog. With OpenCode, a stalled stream (no events and no
        command running for session_stall_s) or a transient provider error ends the current run, and the same
        OpenCode session is resumed with --session, within the same overall time limit, up to
        session_max_resumes times."""
        timeout = int(lim["session_timeout_s"])
        deadline = time.time() + timeout
        stall_s = int(lim.get("session_stall_s", 600))
        max_resumes = int(lim.get("session_max_resumes", 3))
        outs: list = []
        errs: list = []
        cur, resumes, code = list(cmd), 0, -1
        while True:
            out, err, code, why = self._watch_agent(cur, env, backend, label, lim, deadline, timeout, stall_s)
            outs.append(out)
            errs.append(err)
            if backend != "opencode" or why not in ("stall", "transient"):
                break
            sid = self._last_session_id("\n".join(outs))
            if resumes >= max_resumes or time.time() > deadline - 120:
                errs.append(f"TIMEOUT sin eventos (interrupciones: {resumes + 1})")
                self.event(f"  {label}: sesión interrumpida otra vez; no la reanudo más")
                break
            resumes += 1
            if why == "transient":
                self.event(f"  {label}: error transitorio del proveedor; reintento en 60 s ({resumes}/{max_resumes})")
                time.sleep(60)
            if sid:
                cur = self._resume_cmd(cmd, sid, self.RESUME_MSG)
                self.event(f"  {label}: reanudo la sesión {sid} ({resumes}/{max_resumes})")
            else:
                cur = list(cmd)
                self.event(f"  {label}: sin identificador de sesión; la empiezo de nuevo ({resumes}/{max_resumes})")
        stdout = "\n".join(o for o in outs if o)
        stderr = "\n".join(e for e in errs if e).strip()
        return stdout, stderr, code

    def _watch_agent(self, cmd: list, env: dict, backend: str, label: str, lim: dict,
                     deadline: float, total_timeout: int, stall_s: int) -> tuple[str, str, int, str]:
        """One run of the agent, checked every 10 s. Returns stdout, stderr, exit code and why it ended early:
        "provider" (quota error in OpenCode's log), "timeout", "stall", "idle", "transient" or ""."""
        import tempfile
        idle_max = int(lim.get("session_idle_timeout_s", 2700))
        oclog = None
        if backend == "opencode":
            oclog = Path(self.cfg["agent"]["opencode"].get(
                "log_file", str(Path.home() / ".local/share/opencode/log/opencode.log")))

        def lsize() -> int:
            try:
                return oclog.stat().st_size if oclog else 0
            except OSError:
                return 0

        def read_log(start: int, end: int) -> str:
            try:
                with oclog.open(errors="replace") as fh:
                    fh.seek(start)
                    return fh.read(end - start)
            except OSError:
                return ""

        pos = lsize()
        extra, code, why = "", -1, ""
        with tempfile.TemporaryFile("w+") as fo, tempfile.TemporaryFile("w+") as fe:
            p = subprocess.Popen(cmd, cwd=ROOT, stdout=fo, stderr=fe, text=True, env=env,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
            _AGENT_CHILDREN.append(p)
            try:
                last = last_out = time.time()
                seen, out_size = (0, 0, pos), 0
                while True:
                    try:
                        code = p.wait(timeout=10)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    now, size = time.time(), lsize()
                    if size < pos:  # the log was rotated or truncated
                        pos = 0
                    if oclog and size > pos:
                        chunk, pos = read_log(pos, size), size
                        for line in chunk.splitlines():
                            if "level=ERROR" in line and RATE_LIMIT_RE.search(line):
                                extra = f"[opencode.log] {line[:600]}"
                                break
                    osz = os.fstat(fo.fileno()).st_size
                    if osz != out_size:
                        out_size, last_out = osz, now
                    cur = (osz, os.fstat(fe.fileno()).st_size, size)
                    if cur != seen:
                        seen, last = cur, now
                    reason = ""
                    if extra:
                        reason, why = "error del proveedor en el log de OpenCode", "provider"
                    elif now > deadline:
                        reason, extra, why = f"TIMEOUT tras {total_timeout} s", "TIMEOUT", "timeout"
                    elif (backend == "opencode" and now - last_out > stall_s
                          and not self._tool_running(p.pid)):
                        reason, why = (f"sin eventos durante {stall_s // 60} min y sin comandos en curso "
                                       f"(stream colgado)"), "stall"
                    elif now - last > idle_max:
                        reason, why = f"sin actividad durante {idle_max // 60} min", "idle"
                        extra = f"TIMEOUT sin actividad ({idle_max // 60} min)"
                    if reason:
                        self.event(f"  {label}: {reason}; detengo la sesión")
                        self.kill_group(p)
                        code = -1
                        break
            finally:
                if p.poll() is None:
                    self.kill_group(p)
                if p in _AGENT_CHILDREN:
                    _AGENT_CHILDREN.remove(p)
            # The session may have logged a provider error and exited between two checks.
            if oclog and not extra:
                time.sleep(1)  # let OpenCode flush its log
                size = lsize()
                if size < pos:
                    pos = 0
                if size > pos:
                    for line in read_log(pos, size).splitlines():
                        if "level=ERROR" in line and RATE_LIMIT_RE.search(line):
                            extra, why = f"[opencode.log] {line[:600]}", "provider"
                            self.event(f"  {label}: error del proveedor en el log de OpenCode al terminar la sesión")
                            break
            fo.seek(0)
            fe.seek(0)
            stdout, stderr = fo.read(), fe.read()
        if extra:
            stderr = (stderr + "\n" + extra).strip()
        if (not why and backend == "opencode" and code != 0
                and '"isRetryable":true' in stdout.replace(" ", "")):
            why = "transient"
            self.event(f"  {label}: la sesión terminó por un error transitorio del proveedor")
        return stdout, stderr, code, why

    @staticmethod
    def _tool_running(root_pid: int) -> bool:
        """True if the agent has a command running: any descendant process that is not OpenCode itself or a
        language server. While a tool runs (make check, a lab scenario) OpenCode writes no events."""
        ignore = ("opencode", ".opencode", "bun", "node", "rust-analyzer", "gopls", "tsserver", "pyright")
        children: dict = {}
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                stat = Path(f"/proc/{d}/stat").read_text()
                ppid = int(stat.rsplit(")", 1)[1].split()[1])
                comm = Path(f"/proc/{d}/comm").read_text().strip()
            except (OSError, ValueError, IndexError):
                continue
            children.setdefault(ppid, []).append((int(d), comm))
        stack = [root_pid]
        while stack:
            for pid, comm in children.get(stack.pop(), []):
                if not comm.startswith(ignore):
                    return True
                stack.append(pid)
        return False

    @staticmethod
    def _last_session_id(text: str) -> str:
        ids = re.findall(r'"sessionID"\s*:\s*"(ses_[A-Za-z0-9]+)"', text)
        return ids[-1] if ids else ""

    @staticmethod
    def _resume_cmd(cmd: list, sid: str, msg: str) -> list:
        """The same `opencode run` command, continuing session `sid` with `msg` instead of the original
        prompt (the last argument). --title only applies to new sessions."""
        c = list(cmd)
        if "--title" in c:
            i = c.index("--title")
            del c[i:i + 2]
        c[-1] = msg
        i = c.index("run") + 1
        return c[:i] + ["--session", sid] + c[i:]

    @staticmethod
    def kill_group(p) -> None:
        for sig, wait in ((signal.SIGTERM, 15), (signal.SIGKILL, 15)):
            try:
                os.killpg(p.pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            try:
                p.wait(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                continue

    @staticmethod
    def rate_limited(stdout: str, stderr: str) -> str:
        """Provider usage-limit error, if any. Looks only at stderr, non-JSON stdout lines and
        top-level error events, never at agent text or tool output."""
        parts = [stderr]
        for line in stdout.splitlines():
            s = line.strip()
            if not s.startswith("{"):
                parts.append(s)
                continue
            try:
                ev = json.loads(s)
            except json.JSONDecodeError:
                parts.append(s)
                continue
            if isinstance(ev, dict) and ("error" in ev or "error" in str(ev.get("type", "")).lower()):
                parts.append(json.dumps(ev.get("error", ev), ensure_ascii=False)[:2000])
        m = RATE_LIMIT_RE.search("\n".join(parts))
        return m.group(0) if m else ""

    def agent_problems(self) -> list[str]:
        """Configuration errors that would make every session of a role fail."""
        problems = []
        m, agent = self.cfg["models"], self.cfg["agent"]
        roles = ("test_author", "implementer", "reviewer", "security_reviewer")
        for role in roles:
            backend = self.backend_for(role)
            if backend not in ("claude", "opencode", "custom"):
                problems.append(f"{role}: herramienta desconocida '{backend}'")
                continue
            keys = [role] + (["escalation"] if role == "implementer" else [])
            for k in keys:
                model = str(m.get(k, ""))
                if not model or model.startswith("CAMBIAR"):
                    problems.append(f"[models] {k} sin definir (rol {role}, herramienta {backend})")
                elif backend == "opencode" and "/" not in model:
                    problems.append(f"[models] {k} = '{model}': OpenCode exige proveedor/modelo")
            if backend in ("claude", "opencode") and backend not in agent:
                problems.append(f"falta la sección [agent.{backend}] en orchestrator.toml")
            elif backend in ("claude", "opencode") and not self.dry and \
                    not shutil.which(agent[backend].get("bin", backend)):
                problems.append(f"no se encuentra el ejecutable de {backend} ({agent[backend].get('bin')})")
        return sorted(set(problems))

    def check_agents(self) -> None:
        problems = self.agent_problems()
        if problems and not self.dry:
            raise Halt("configuración de agentes: " + "; ".join(problems))
        for pr in problems:
            print(f"aviso (dry-run): {pr}")

    def backend_for(self, role: str) -> str:
        agent = self.cfg["agent"]
        return agent.get("roles", {}).get(role, agent.get("default_backend", "claude"))

    @staticmethod
    def parse_opencode_events(stdout: str) -> tuple[str, float]:
        """Best effort over `opencode run --format json` (one JSON event per line).

        The event schema is not documented in detail, so this only collects text parts and
        any numeric `cost` fields it finds. If nothing matches, the whole output is the text
        and the cost is 0; budgets then rely on the provider's own limits (see README-KIT).
        """
        texts, cost = [], 0.0

        def walk(obj):
            nonlocal cost
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == "cost" and isinstance(v, (int, float)):
                        cost += float(v)
                    elif k == "text" and isinstance(v, str):
                        texts.append(v)
                    else:
                        walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v)

        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    walk(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return ("\n".join(texts[-20:]) if texts else stdout), round(cost, 4)

    # ---------- prompts ----------
    def phase_note(self, phase: str) -> str:
        note = self.state.get("phases", {}).get(phase, {}).get("note", "")
        return f"DECISIÓN HUMANA (manda sobre cualquier otra interpretación):\n{note}\n\n" if note else ""

    def phase_text(self, phase: str) -> str:
        plan = (ROOT / "docs" / "plan.md").read_text()
        m = re.search(rf"^## {re.escape(phase)}\b.*?(?=^## |\Z)", plan, re.S | re.M)
        return m.group(0).strip() if m else f"(no encontré la sección {phase} en docs/plan.md)"

    @staticmethod
    def manifest() -> list[dict]:
        return accept_check.load(ROOT / "tests" / "acceptance" / "manifest.toml")

    def task_tests(self, task_id: str) -> list[dict]:
        return [t for t in self.manifest() if t.get("task") == task_id]

    @staticmethod
    def spec_rules(ids: list[str]) -> str:
        """Texto de las reglas R-xx citadas, tomado de docs/spec/v0.1.1.md."""
        spec = (ROOT / "docs" / "spec" / "v0.1.1.md").read_text()
        out = []
        for rid in sorted(set(ids)):
            m = re.search(rf"^- \*\*{re.escape(rid)}\*\*.*?(?=^- \*\*|^#|\Z)", spec, re.S | re.M)
            if m:
                out.append(m.group(0).strip())
        return "\n".join(out)

    def task_prompt(self, task: Task, role: str, feedback: str = "") -> str:
        tests = self.task_tests(task.id)
        parts = [f"TAREA {task.id}: {task.title}", f"Rol: {role}. Fase {task.phase}.",
                 "Trabaja solo en esta tarea. Las demás tareas de la fase ya están hechas o se harán "
                 "después; no te adelantes.",
                 f"Sección de la fase en docs/plan.md:\n\n{self.phase_text(task.phase)}"]
        rules = self.spec_rules([r for t in tests for r in t.get("rules", [])])
        if rules:
            parts.append(f"Reglas que esta tarea cierra (docs/spec/v0.1.1.md):\n\n{rules}")
        if tests:
            listing = "\n".join(f"- {t['name']} [{t['kind']}] en {', '.join(t['files'])}\n"
                                f"  demuestra: {t['proves']}\n  mutación: {t['mutation']}" for t in tests)
            if role == "test_author":
                parts.append("Escribe exactamente estas pruebas (nombre y archivo del manifiesto "
                             "tests/acceptance/manifest.toml; no cambies el manifiesto):\n" + listing)
            elif role == "implementer":
                parts.append("Pruebas de aceptación de la tarea (ya escritas; no las modifiques; deben pasar). "
                             "Después de que pasen, aplica la mutación de cada una y anota "
                             "`MUTACIÓN <nombre>: detectada` en docs/BLOCKERS.md:\n" + listing)
        if feedback:
            parts.append(f"RESULTADO DEL INTENTO ANTERIOR (corrígelo):\n\n{feedback}")
        return "\n\n".join(parts)

    # ---------- verification ----------
    def acceptance_commands(self, tests: list[dict]) -> list[str]:
        """Comandos que corren las pruebas de aceptación de una tarea (los escenarios de laboratorio los corre la puerta)."""
        def files(kind: str, strip: str = "") -> list[str]:
            out = sorted({f for t in tests if t["kind"] == kind for f in t["files"]})
            return [f.removeprefix(strip) for f in out]
        cmds = []
        if (py := files("py", "backend/")):
            cmds.append("cd backend && pytest -q " + " ".join(shlex.quote(f) for f in py))
        if (ts := files("ts", "frontend/")):
            cmds.append("cd frontend && pnpm exec vitest run " + " ".join(shlex.quote(f) for f in ts))
        if (sh := files("sh", "deploy/tests/acceptance/")):
            cmds.append("deploy/tests/acceptance/run.sh " + " ".join(shlex.quote(f) for f in sh))
        return cmds

    def run_checks(self, task: Task | None) -> tuple[bool, str]:
        lim = self.cfg["limits"]
        if self.dry:
            return True, ""
        check = self.check_command()
        if check:
            ok, out = self.sh(check, timeout=lim["check_timeout_s"])
            if not ok:
                return False, f"$ {check}\n{tail(out)}"
        if task is not None:
            tests = self.task_tests(task.id)
            for cmd in self.acceptance_commands(tests):
                ok, out = self.sh(cmd, timeout=lim["check_timeout_s"])
                if not ok:
                    return False, f"$ {cmd}\n{tail(out)}"
            if tests:
                cmd = f"python3 scripts/accept_check.py {task.phase} --mutations --task {task.id}"
                ok, out = self.sh(cmd, timeout=120)
                if not ok:
                    return False, (f"$ {cmd}\n{tail(out)}\nAplica la mutación de cada prueba y anota las líneas "
                                   "`MUTACIÓN <nombre>: detectada` en docs/BLOCKERS.md (luego restaura el código).")
        return True, ""

    def check_command(self) -> str:
        """`make check` (autopruebas del kit + scripts/check.sh); sin Makefile, solo scripts/check.sh."""
        lim = self.cfg["limits"]
        if (ROOT / "Makefile").exists() and self.sh("make -n check", timeout=60)[0]:
            return lim["check_command"]
        return "scripts/check.sh" if (ROOT / "scripts" / "check.sh").exists() else ""

    def role_violations(self, role: str) -> list[str]:
        if role == "test_author":
            # Probar sus escenarios con `make lab` deja informes en reports/lab/: son artefactos,
            # no trabajo. Se descartan sin contar como violación.
            self.restore_paths(("reports/lab/",))
        files = self.changed_files()
        frozen = [f for f in files if starts(f, FROZEN)]
        if role == "implementer":
            return frozen + [f for f in files if starts(f, PROTECTED)]
        allowed = ROLE_WRITES.get(role, ())
        return frozen + [f for f in files if not starts(f, allowed)]

    def ensure_locked(self) -> None:
        locked = self.state["locked"].get("digest")
        if locked and not self.dry and self.protected_digest() != locked:
            raise Halt("el material de aceptación cambió fuera de un commit del autor de pruebas")

    # ---------- task steps ----------
    @staticmethod
    def human_note(ts: dict) -> str:
        note = ts.get("note", "")
        return f"DECISIÓN HUMANA (manda sobre cualquier otra interpretación):\n{note}" if note else ""

    def write_tests(self, task: Task) -> None:
        ts = self.tstate(task)
        if not task.acceptance or ts["tests"] in ("done", "none"):
            return
        tests = [t for t in self.task_tests(task.id) if t["kind"] in ("py", "ts", "sh", "lab")]
        if not tests:
            ts["tests"] = "none"
            self.save()
            return
        feedback = self.human_note(ts)
        for _ in range(self.cfg["limits"]["attempts_per_task"]):
            r = self.session("test_author", task.phase, task.id,
                             self.task_prompt(task, "test_author", feedback),
                             self.model_for("test_author", task))
            if not r.ok:
                feedback = f"La sesión anterior terminó con error: {tail(r.text, 20)}"
                continue
            bad = self.role_violations("test_author")
            if bad:
                self.discard_all()
                feedback = f"Escribiste fuera de tu rol: {bad}. Esos cambios se descartaron."
                continue
            if "BLOQUEADO: dependencia" in r.text:
                raise Halt(f"{task.id}: el autor de pruebas pide una dependencia (ver docs/BLOCKERS.md)")
            ok, out = (True, "") if self.dry else self.acceptance_ready(task, tests)
            if not ok:
                self.event(f"  {task.id}: las pruebas no son válidas todavía")
                feedback = out
                continue
            self.normalize_tests(tests)
            self.commit(f"T {task.id}: pruebas de aceptación")
            self.state["locked"]["digest"] = self.protected_digest()
            ts["tests"] = "done"
            self.save()
            return
        raise Halt(f"{task.id}: el autor de pruebas no logró pruebas válidas")

    def normalize_tests(self, tests: list[dict]) -> None:
        """El implementador no puede tocar el material de aceptación, así que su estilo nunca debe hacer fallar
        `make check`: se formatea el Python con ruff y se marcan ejecutables los scripts de laboratorio y de deploy."""
        if self.dry:
            return
        files = sorted({f for t in tests for f in t["files"]})
        for f in files:
            p = ROOT / f
            if p.exists() and f.endswith(".sh"):
                os.chmod(p, 0o755)
        py = [f.removeprefix("backend/") for f in files if f.startswith("backend/") and f.endswith(".py")]
        if py:
            q = " ".join(shlex.quote(f) for f in py)
            ok, out = self.sh(f"cd backend && ruff format {q} && ruff check --fix {q}", timeout=600)
            if not ok:
                self.event(f"  aviso: ruff sobre las pruebas de aceptación: {tail(out, 5)}")

    def light_check(self, files: list[str]) -> tuple[bool, str]:
        """Sintaxis y pruebas débiles de unos archivos de aceptación concretos."""
        problems = []
        for f in files:
            p = ROOT / f
            if not p.is_file() or not starts(f, PROTECTED):
                continue
            text = p.read_text(errors="replace")
            weak = sorted({m.group(0) for m in WEAK_RE.finditer(text)})
            if weak:
                problems.append(f"{f} contiene {weak}: prueba débil o desactivada; no se aceptan (AGENTS.md).")
            if f.endswith(".sh"):
                ok, out = self.sh(f"bash -n {shlex.quote(f)}", timeout=60)
                if not ok:
                    problems.append(f"{f}: error de sintaxis de bash:\n{tail(out, 5)}")
            elif f.endswith(".py"):
                ok, out = self.sh(f"python3 -m py_compile {shlex.quote(f)}", timeout=60)
                if not ok:
                    problems.append(f"{f}: error de sintaxis de Python:\n{tail(out, 5)}")
        return (not problems, "\n".join(problems))

    def acceptance_ready(self, task: Task, tests: list[dict]) -> tuple[bool, str]:
        """Las pruebas existen con el nombre y el archivo del manifiesto, no son débiles y se pueden recoger.
        Pueden fallar por API faltante (es lo esperado: se escriben antes que el código); no por estar rotas."""
        errs = accept_check.check(tests, "F4", ROOT)
        if errs:
            return False, "El manifiesto exige estas pruebas y no están como se pidió:\n" + "\n".join(errs)
        files = sorted({f for t in tests for f in t["files"]})
        ok, out = self.light_check(files)
        if not ok:
            return False, out
        for t in tests:
            if t["kind"] == "lab":
                text = (ROOT / t["files"][0]).read_text(errors="replace")
                if "lab/lib.sh" not in text or "lab_result" not in text:
                    return False, f"{t['files'][0]}: un escenario debe cargar lab/lib.sh y escribir su informe con lab_result."
        for cmd in self.acceptance_commands([t for t in tests if t["kind"] in ("py", "ts")]):
            if cmd.startswith("cd backend"):
                cmd = cmd.replace("pytest -q", "pytest -q --collect-only", 1)
            ok, out = self.sh(cmd, timeout=self.cfg["limits"]["check_timeout_s"])
            if ok:
                if "--collect-only" not in cmd:
                    self.event(f"  {task.id}: las pruebas ya pasan sin implementación (válido solo si la regla es una invariante)")
                continue
            if BROKEN_TEST_RE.search(out) or not MISSING_API_RE.search(out) and "--collect-only" in cmd:
                return False, f"Las pruebas no se pueden recoger por errores que no son API faltante:\n$ {cmd}\n{tail(out)}"
            self.event(f"  {task.id}: pruebas pendientes de API (errores de importación esperados)")
        return True, ""

    def special_task(self, task: Task) -> None:
        """Tasks done by test_author without a per-task test list (lab scenarios of a phase)."""
        feedback = ""
        for _ in range(self.cfg["limits"]["attempts_per_task"]):
            r = self.session(task.role, task.phase, task.id, self.task_prompt(task, task.role, feedback),
                             self.model_for(task.role, task))
            if not r.ok:
                feedback = f"La sesión anterior terminó con error: {tail(r.text, 20)}"
                continue
            bad = self.role_violations(task.role)
            if bad:
                self.restore_paths(tuple(bad))
                feedback = f"Escribiste fuera de tu rol: {bad}. Esos cambios se descartaron."
                continue
            ok, out = self.validate_special(task)
            if ok:
                self.commit(f"T {task.id}: {task.title}")
                self.state["locked"]["digest"] = self.protected_digest()
                self.tstate(task)["status"] = "done"
                self.save()
                return
            feedback = out
        raise Halt(f"{task.id}: {task.role} no completó la tarea")

    def validate_special(self, task: Task) -> tuple[bool, str]:
        if self.dry:
            return True, ""
        tests = self.task_tests(task.id)
        if tests:
            ok, out = self.acceptance_ready(task, tests)
            if not ok:
                return False, out
            self.normalize_tests(tests)
        return self.run_checks(None)

    def implement(self, task: Task) -> None:
        ts = self.tstate(task)
        lim = self.cfg["limits"]
        feedback = ts.get("feedback", "")
        plan = [False] * lim["attempts_per_task"] + [True]  # last one escalates
        for escalate in plan[ts["attempts"]:]:
            ts["attempts"] += 1
            self.save()
            prompt = self.task_prompt(task, "implementer", (self.human_note(ts) + "\n\n" + feedback).strip())
            if escalate:
                blockers = (ROOT / "docs" / "BLOCKERS.md").read_text() if (ROOT / "docs" / "BLOCKERS.md").exists() else ""
                prompt += "\n\nESCALAMIENTO: intentos anteriores fallaron. Bloqueos registrados:\n" + tail(blockers, 120)
            r = self.session("implementer", task.phase, task.id, prompt,
                             self.model_for("implementer", task, escalate))
            if not r.ok:
                feedback = f"La sesión anterior terminó con error (ver {r.log.name}): {tail(r.text, 20)}"
                ts["feedback"] = feedback
                self.save()
                continue
            bad = self.role_violations("implementer")
            if bad:
                self.restore_paths(tuple(bad))
                feedback = (f"Modificaste archivos que tu rol no puede tocar: {bad}. Se revirtieron. "
                            "Haz que el código cumpla las pruebas sin cambiarlas.")
                ts["feedback"] = feedback
                continue
            ok, out = self.run_checks(task)
            if ok:
                self.ensure_locked()
                self.commit(f"{task.id}: {task.title}")
                ts.update(status="done", feedback="")
                self.save()
                return
            feedback = out
            ts["feedback"] = feedback
            self.save()
        ts["status"] = "blocked"
        self.save()
        raise Halt(f"{task.id} bloqueada tras {ts['attempts']} intentos (ver docs/BLOCKERS.md)")

    @staticmethod
    def review_touches_tests(text: str) -> bool:
        findings = "\n".join(l for l in text.splitlines() if re.match(r"\s*-\s*\[R\d+\]", l))
        return re.search(r"tests/acceptance/|lab/scenarios/|autor de pruebas", findings) is not None

    def test_fix_session(self, phase: str, label: str, what: str, source: str = "revisión") -> None:
        """Keeps the implementer's uncommitted work (a fix that did not pass `make check`) aside
        while the test author works, so it is neither blamed on nor discarded by the test author."""
        stashed = False
        if not self.dry:
            self.restore_paths(("reports/lab/",))  # artefactos de make lab
            if self.changed_files():
                self.git("stash", "push", "--include-untracked", "-m", f"orquestador: {label}", check=False)
                stashed = not self.changed_files()
                if stashed:
                    self.event(f"  {label}: guardo en git stash los cambios sin commit del implementador")
        try:
            self._test_fix_inner(phase, label, what, source)
        finally:
            if not self.dry:
                self.restore_paths(("reports/lab/",))
            if stashed:
                try:
                    self.git("stash", "pop")
                    self.event(f"  {label}: recupero los cambios del implementador")
                except Halt:
                    self.event(f"  aviso: {label}: no pude recuperar los cambios del implementador; "
                               "siguen en `git stash list`")

    def _test_fix_inner(self, phase: str, label: str, what: str, source: str) -> None:
        """Review findings about acceptance material go to the test author, never the implementer."""
        task = Task(id=label, phase=phase, title="Corrección de pruebas", role="test_author", acceptance=True)
        feedback = ""
        for _ in range(self.cfg["limits"]["attempts_per_task"]):
            prompt = (
                f"CORRECCIÓN DE PRUEBAS {label} de la fase {phase} (rol test_author). "
                + ("El revisor rechazó la fase. Corrige SOLO los hallazgos sobre las pruebas de aceptación (backend, frontend y "
                   "deploy tests/acceptance/) o lab/scenarios/: crea las pruebas que faltan y amplía las que no comprueban lo que dice "
                   "su nombre. "
                   if source == "revisión" else
                   "La puerta automática de la fase falla y el fallo apunta a tu material (escenarios de "
                   "lab/scenarios/, pruebas). Revisa el informe y el diagnóstico de BLOCKERS.md; si "
                   "el defecto está en tu material, corrígelo y prueba el escenario con el mismo comando de "
                   "la puerta. Si tu material es correcto y el fallo es del código de producción, no cambies "
                   "nada: lo corrige después el implementador. ")
                + "Nunca debilites ni borres una aserción existente ni cambies umbrales. No toques el código de producción ni "
                "gates.toml. Si un archivo es largo, escríbelo por partes con ediciones sucesivas.\n\n"
                f"{self.phase_note(phase)}{what}\n\nSección de la fase en docs/plan.md:\n\n{self.phase_text(phase)}"
                + (f"\n\nIntento anterior:\n{feedback}" if feedback else ""))
            r = self.session("test_author", phase, label, prompt, self.model_for("test_author", None))
            if not r.ok:
                feedback = f"La sesión terminó con error: {tail(r.text, 20)}"
                continue
            bad = self.role_violations("test_author")
            if bad:
                self.discard_all()
                feedback = f"Escribiste fuera de tu rol: {bad}. Esos cambios se descartaron."
                continue
            changed = self.changed_files()
            if not changed:
                self.event(f"  {label}: el autor de pruebas no cambió nada")
                return
            ok, out = (True, "") if self.dry else self.light_check(changed)
            if not ok:
                feedback = out
                continue
            if not self.dry:
                for f in changed:
                    if f.endswith(".sh") and (ROOT / f).exists():
                        os.chmod(ROOT / f, 0o755)
                py = [f.removeprefix("backend/") for f in changed if f.startswith("backend/") and f.endswith(".py")]
                if py:
                    q = " ".join(shlex.quote(f) for f in py)
                    self.sh(f"cd backend && ruff format {q} && ruff check --fix {q}", timeout=600)
            self.commit(f"T {label}: corrección de pruebas")
            self.state["locked"]["digest"] = self.protected_digest()
            self.save()
            return
        self.discard_all()
        self.event(f"  {label}: el autor de pruebas no logró corregir las pruebas; sigue el implementador")

    def fix_session(self, phase: str, label: str, what: str) -> None:
        """An implementer session that fixes gate failures or review findings."""
        task = Task(id=label, phase=phase, title=what.splitlines()[0][:80], role="implementer",
                    acceptance=False)
        prompt = (f"CORRECCIÓN {label} de la fase {phase}. Corrige lo siguiente sin modificar "
                  f"pruebas, umbrales ni archivos protegidos:\n\n{self.phase_note(phase)}{what}\n\n"
                  f"Sección de la fase en docs/plan.md:\n\n{self.phase_text(phase)}")
        self.session("implementer", phase, label, prompt, self.model_for("implementer", task))
        bad = self.role_violations("implementer")
        if bad:
            self.restore_paths(tuple(bad))
        ok, out = self.run_checks(None)
        if ok:
            self.ensure_locked()
            self.commit(f"{label}: corrección")
        else:
            self.event(f"{label}: la corrección no deja make check en verde:\n{tail(out, 20)}")

    # ---------- phase steps ----------
    def run_gate(self, phase: str, quick: bool = True) -> tuple[bool, str]:
        kind = "rápida" if quick else "completa"
        self.pending_human = False
        if self.dry:
            self.event(f"puerta {phase} {kind} (dry-run)")
            return True, ""
        self.ensure_locked()
        flag = " --quick" if quick else ""
        proc = subprocess.run(f"python3 scripts/gate.py {phase}{flag}", shell=True, cwd=ROOT, capture_output=True,
                              text=True, env=self.base_env(), stdin=subprocess.DEVNULL, timeout=200000)
        out = proc.stdout + "\n" + proc.stderr
        self.pending_human = proc.returncode == 4
        self.commit_reports(f"{phase}: informe de puerta ({kind})")
        verdict = "APROBADA" if proc.returncode == 0 else "SOLO FALTA EL NIVEL 2" if self.pending_human else "NO APROBADA"
        self.event(f"puerta {phase} ({kind}): {verdict}")
        return proc.returncode == 0, out

    def gate_has_long_steps(self, phase: str) -> bool:
        """La puerta completa (full, laboratorio y nivel 2) corre una sola vez, después de aprobada la revisión."""
        try:
            g = tomllib.loads((ROOT / "gates.toml").read_text()).get("phase", {}).get(phase, {})
        except Exception:  # noqa: BLE001
            return False
        return bool(g.get("full") or g.get("lab1") or g.get("lab2") or g.get("metrics"))

    def missing_phase_material(self, phase: str) -> list[str]:
        """Pruebas o escenarios del manifiesto (hasta esta fase) y de gates.toml que nadie ha escrito aún."""
        missing = []
        for e in accept_check.check(self.manifest(), phase, ROOT):
            missing.append(e)
        try:
            g = tomllib.loads((ROOT / "gates.toml").read_text()).get("phase", {}).get(phase, {})
        except Exception:  # noqa: BLE001
            return sorted(set(missing))
        for c in g.get("lab1", []):
            if c.startswith("lab/scenarios/") and not (ROOT / c).exists():
                missing.append(f"{c} (lo corre la puerta)")
        return sorted(set(missing))

    def ensure_phase_material(self, phase: str) -> None:
        """Material de salida de fase (escenarios de laboratorio, pruebas del manifiesto) que ninguna tarea escribió."""
        if self.dry:
            return
        missing = [m for m in self.missing_phase_material(phase)
                   if "lab/scenarios/" in m or any(t["kind"] == "lab" and t["name"] in m for t in self.manifest())]
        if not missing:
            return
        label = f"{phase}.X"
        task = Task(id=label, phase=phase, title="Material de salida de fase", role="test_author", acceptance=True)
        feedback = ""
        for _ in range(self.cfg["limits"]["attempts_per_task"]):
            prompt = (
                f"MATERIAL DE SALIDA DE LA FASE {phase} (rol test_author). La puerta automática de la fase "
                f"necesita archivos que ninguna tarea te asignó. Escríbelos:\n- " + "\n- ".join(missing) +
                "\n\nEscenarios de laboratorio: bash que carga lab/lib.sh (lee lab/lib.sh y lab/selftest/ como modelo), "
                "escribe su informe con lab_result y las claves de las métricas de la fase en gates.toml, sale con 0 solo si "
                "cumple y limpia lo que creó aunque falle. No toques el código de producción ni gates.toml.\n\n"
                f"Sección de la fase en docs/plan.md:\n\n{self.phase_text(phase)}"
                + (f"\n\nIntento anterior:\n{feedback}" if feedback else ""))
            r = self.session("test_author", phase, label, prompt, self.model_for("test_author", None))
            if not r.ok:
                feedback = f"La sesión terminó con error: {tail(r.text, 20)}"
                continue
            bad = self.role_violations("test_author")
            if bad:
                self.discard_all()
                feedback = f"Escribiste fuera de tu rol: {bad}. Esos cambios se descartaron."
                continue
            still = [m for m in self.missing_phase_material(phase) if m in missing]
            if still:
                feedback = "Siguen faltando: " + "; ".join(still)
                continue
            ok, out = self.light_check(self.changed_files())
            if not ok:
                feedback = out
                continue
            for f in self.changed_files():
                if f.endswith(".sh") and (ROOT / f).exists():
                    os.chmod(ROOT / f, 0o755)
            self.commit(f"T {label}: material de salida de fase")
            self.state["locked"]["digest"] = self.protected_digest()
            self.save()
            return
        raise Halt(f"{label}: el autor de pruebas no completó el material de salida de fase")

    def review(self, phase: str) -> tuple[bool, str]:
        """Todos los revisores de la fase (gates.toml `review`) deben aprobar; cada uno escribe su informe."""
        report = ROOT / "reports" / f"gate-{phase}.json"
        roles = ["reviewer"]
        if report.exists():
            r = json.loads(report.read_text()).get("review", ["reviewer"])
            roles = [r] if isinstance(r, str) else list(r)
        approved_all, texts = True, []
        for role in roles:
            ok, text = self._review_one(phase, role)
            approved_all &= ok
            if not ok:
                texts.append(f"[{role}]\n{text}")
                break  # el revisor de seguridad (el caro) solo ve lo que el revisor normal ya aprobó
        return approved_all, "\n\n".join(texts)

    def _review_one(self, phase: str, role: str) -> tuple[bool, str]:
        fname = REVIEW_FILES[role].format(phase=phase)
        prompt = (f"REVISIÓN de la fase {phase}. Escribe reports/{fname} con el formato "
                  f"de AGENTS.md (regla 8).\n\nSección de la fase en docs/plan.md:\n\n{self.phase_text(phase)}")
        path = ROOT / "reports" / fname
        before = path.stat().st_mtime_ns if path.exists() else None
        label = f"{phase}.review" if role == "reviewer" else f"{phase}.secreview"
        for attempt in (1, 2):
            self.session(role, phase, label, prompt, self.model_for(role, None))
            bad = self.role_violations(role)
            if bad:
                self.restore_paths(tuple(bad))
            if self.dry:
                return True, ""
            fresh = path.exists() and path.stat().st_mtime_ns != before
            head = path.read_text().strip().splitlines()[:1] if fresh else []
            if head and head[0].strip().startswith("VEREDICTO:"):
                break
            self.event(f"  {label}: el revisor no dejó un informe nuevo con veredicto "
                       f"(intento {attempt}); {'reintento en 2 min' if attempt == 1 else 'me detengo'}")
            if attempt == 1:
                time.sleep(120)
        else:
            raise Halt(f"{phase}: el revisor ({role}) no produjo informe (error del proveedor o sesión fallida); "
                       f"no cuenta como rechazo")
        text = path.read_text()
        self.commit(f"{phase}: revisión ({role})")
        approved = text.strip().splitlines()[0].strip() == "VEREDICTO: APROBADO"
        self.event(f"revisión {phase} ({role}): {'APROBADO' if approved else 'RECHAZADO'}")
        return approved, text

    def integration_branch(self) -> str:
        return self.cfg["git"].get("integration_branch", "integracion/v0.1.1")

    def ensure_integration(self) -> None:
        """Rama de integración (nunca master): recibe cada fase aprobada. Tiene un PR en borrador hacia master desde el principio,
        para que el CI corra sobre ella."""
        g, br = self.cfg["git"], self.integration_branch()
        if self.dry:
            return
        if not self.git("branch", "--list", br).strip():
            self.git("branch", br, g["main_branch"])
            self.event(f"rama de integración {br} creada desde {g['main_branch']}")
        if g.get("publish", True) and not self.state.get("pr_opened"):
            self.sh(f"git push -q -u {g.get('remote', 'origin')} {br}", timeout=300, with_secrets=True)
            body = ("Integración autónoma de v0.1.1 (docs/plan.md). Cada fase aprobada por su puerta y su revisión se fusiona aquí; "
                    "este PR queda en borrador hasta que terminen todas. **No se fusiona solo.**")
            pr = ROOT / "reports" / "prompts" / "pr-integracion.md"
            pr.parent.mkdir(parents=True, exist_ok=True)
            pr.write_text(body)
            ok, out = self.sh(f"gh pr create --draft --base {g['main_branch']} --head {br} --title "
                              f"{shlex.quote('feat: v0.1.1 (integración autónoma)')} --body-file {shlex.quote(str(pr))}",
                              timeout=300, with_secrets=True)
            self.event("PR en borrador: " + (tail(out, 2).strip() if ok else "no se pudo abrir (¿ya existe?): " + tail(out, 3)))
            self.state["pr_opened"] = True
            self.save()

    def integrate(self, phase: str) -> None:
        """Fusiona fase/Fx en la rama de integración (nunca en master) y la empuja."""
        g, br = self.cfg["git"], self.integration_branch()
        if self.dry:
            return
        self.git("checkout", "-q", br)
        self.git("merge", "-q", "--no-ff", f"fase/{phase}", "-m", f"Fase {phase} aprobada por puerta y revisión")
        if g.get("publish", True):
            ok, out = self.sh(f"git push -q -u {g.get('remote', 'origin')} {br}", timeout=300, with_secrets=True)
            if not ok:
                raise Halt(f"no pude empujar {br}: {tail(out, 5)}")

    def wait_ci(self, phase: str) -> None:
        """Espera el CI de la rama de integración. Si falla, una sesión del implementador lo corrige con el registro del fallo."""
        g, br = self.cfg["git"], self.integration_branch()
        if self.dry or not g.get("publish", True) or not g.get("wait_ci", True):
            return
        lim = self.cfg["limits"]
        for rnd in range(lim["gate_fix_rounds"] + 1):
            sha = self.git("rev-parse", "HEAD").strip()
            deadline = time.time() + int(g.get("ci_timeout_min", 45)) * 60
            verdict, run_id = "pending", ""
            while time.time() < deadline:
                ok, out = self.sh(f"gh run list --branch {shlex.quote(br)} --commit {sha} --json databaseId,status,conclusion",
                                  timeout=120, with_secrets=True)
                try:
                    runs = json.loads(out.strip().splitlines()[-1]) if ok and out.strip() else []
                except (json.JSONDecodeError, IndexError):
                    runs = []
                if runs and all(r.get("status") == "completed" for r in runs):
                    bad = [r for r in runs if r.get("conclusion") not in ("success", "skipped", "neutral")]
                    verdict, run_id = ("failure", str(bad[0]["databaseId"])) if bad else ("success", "")
                    break
                time.sleep(30)
            if verdict == "success":
                self.event(f"CI de {br} en verde tras {phase}")
                return
            if verdict == "pending":
                self.notify(f"aviso: el CI de {br} no terminó en {g.get('ci_timeout_min', 45)} min; sigo sin esperarlo")
                return
            if rnd == lim["gate_fix_rounds"]:
                raise Halt(f"el CI de {br} sigue en rojo tras {phase} ({lim['gate_fix_rounds']} rondas de corrección)")
            ok, log = self.sh(f"gh run view {shlex.quote(run_id)} --log-failed", timeout=300, with_secrets=True)
            self.git("checkout", "-q", f"fase/{phase}")
            self.fix_session(phase, f"{phase}.CI{rnd + 1}", "El CI de GitHub falló sobre la integración:\n" + tail(log, 150))
            # La corrección vive en la rama de la fase; se integra de nuevo.
            self.git("checkout", "-q", br)
            self.git("merge", "-q", "--no-ff", f"fase/{phase}", "-m", f"Fase {phase}: corrección del CI")
            self.sh(f"git push -q {g.get('remote', 'origin')} {br}", timeout=300, with_secrets=True)

    def finish(self) -> None:
        """Todas las fases integradas: marca el PR como listo y avisa lo que queda para el humano."""
        g, br = self.cfg["git"], self.integration_branch()
        deferred = self.state.get("deferred_lab2", [])
        todo = (f"Falta el nivel 2 de {', '.join(deferred)}: ejecuta docs/hardening/LAB-RC.md en prox-t1, deja reports/lab2/<fase>.json "
                "(commit `KIT: nivel 2`) y firma la release. " if deferred else "")
        if not self.dry and g.get("publish", True):
            self.sh(f"gh pr ready {shlex.quote(br)}", timeout=120, with_secrets=True)
        self.notify(f"v0.1.1 lista en {br} (PR listo hacia {g['main_branch']}). {todo}Gasto total "
                    f"{self.state['spend']['total']:.2f} USD.")

    def run_phase(self, phase: str) -> None:
        ps = self.state["phases"].setdefault(phase, {"status": "pending"})
        if ps["status"] == "done":
            return
        self.ensure_integration()
        branch, base = f"fase/{phase}", self.integration_branch()
        if not self.dry:
            current = self.git("rev-parse", "--abbrev-ref", "HEAD").strip()
            dirty = self.changed_files()
            if current != branch:
                if dirty:
                    raise Halt(f"hay cambios sin commit en {current} antes de {phase}: {dirty[:10]}")
                exists = self.git("branch", "--list", branch).strip()
                self.git("checkout", "-q", branch) if exists else self.git("checkout", "-q", "-b", branch, base)
            elif dirty and ps["status"] == "pending":
                raise Halt(f"hay cambios sin commit antes de empezar {phase}: {dirty[:10]}")
            # In a running phase, leftover work from a blocked task is kept: the next
            # implementer session continues from it.
        if ps["status"] == "pending":
            self.run_hook("snapshot", "PGUI_SNAPSHOT", f"antes-{phase.replace('.', '-')}")
            ps["status"] = "running"
            self.save()
        self.notify(f"empieza {phase}")
        for task in [t for t in self.tasks if t.phase == phase]:
            if self.tstate(task)["status"] == "done":
                continue
            if task.role == "implementer":
                self.write_tests(task)
                self.implement(task)
            else:
                self.special_task(task)
        self.close_phase(phase)
    def gate_with_fixes(self, phase: str, label: str, quick: bool = True) -> None:
        lim = self.cfg["limits"]
        ok, out = self.run_gate(phase, quick)
        rounds = 0
        while not ok:
            if getattr(self, "pending_human", False):
                # Solo falta el nivel 2, que ejecuta el humano. No detiene el trabajo: se acumula para el final.
                deferred = self.state.setdefault("deferred_lab2", [])
                if phase not in deferred:
                    deferred.append(phase)
                    self.save()
                    self.event(f"el nivel 2 de {phase} queda pendiente para la validación final (LAB-RC.md)")
                return
            rounds += 1
            if rounds > lim["gate_fix_rounds"]:
                raise Halt(f"la puerta de {phase} no pasa tras {lim['gate_fix_rounds']} rondas ({label})")
            report = ROOT / "reports" / f"gate-{phase}.json"
            detail = report.read_text()[-12000:] if report.exists() else tail(out, 120)
            why = self.gate_blames_tests(report)
            if why:
                self.event(f"  {label}{rounds}: la puerta apunta a material del autor de pruebas ({why})")
                self.test_fix_session(phase, f"{label}{rounds}T",
                                      f"Informe de la puerta:\n{self.gate_failures(report) or detail}"
                                      f"\n\nÚltimo diagnóstico en docs/BLOCKERS.md:\n{self.last_blocker()}",
                                      source="puerta")
            self.fix_session(phase, f"{label}{rounds}",
                             f"La puerta automática falló:\n{self.gate_failures(report) or detail}")
            ok, out = self.run_gate(phase, quick)

    def gate_failures(report: Path) -> str:
        """Only the failed, not-skipped steps of a gate report."""
        try:
            steps = json.loads(report.read_text()).get("steps", [])
        except (OSError, json.JSONDecodeError):
            return ""
        bad = [s for s in steps if not s.get("ok") and not s.get("skipped") and not s.get("human")]
        skipped = [s.get("cmd") or s.get("key") for s in steps if s.get("skipped")]
        out = json.dumps(bad, ensure_ascii=False, indent=1)[-10000:]
        if skipped:
            out += "\n\nNO MEDIDOS en esta corrida (la puerta se detuvo antes): " + ", ".join(map(str, skipped))
        return out

    @staticmethod
    def last_blocker() -> str:
        p = ROOT / "docs" / "BLOCKERS.md"
        if not p.exists():
            return ""
        parts = re.split(r"^## ", p.read_text(errors="replace"), flags=re.M)
        return ("## " + parts[-1]).strip()[-6000:] if len(parts) > 1 else ""

    def gate_blames_tests(self, report: Path) -> str:
        """Why a failed gate should go to the test author first, or ""."""
        try:
            data = json.loads(report.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
        steps, phase = data.get("steps", []), str(data.get("phase", ""))
        for s in steps:
            if s.get("ok") or s.get("skipped") or s.get("step") != "command":
                continue
            if re.search(r"lab/scenarios/\S+: line \d+:", s.get("tail", "")):
                return "error de bash en un escenario"
        blocker = self.last_blocker()
        m = re.match(r"## (F\d+)\b", blocker)
        # Las frases que niegan haber tocado material protegido no son culpa.
        claims = "\n".join(l for l in blocker.splitlines() if not re.search(
            r"(?i)\b(no se toc|no toqu|sin tocar|no he tocado|no se modific|no modifiqu|no se cambi|no cambi|"
            r"no culp|no es (material|defecto) del autor|no debe resolverse)", l))
        if m and m.group(1) == phase and re.search(
                r"CULPA: prueba|lab/scenarios/|tests/acceptance/|gates\.toml|autor de pruebas", claims):
            return "BLOCKERS.md culpa a material protegido"
        return ""

    def close_phase(self, phase: str) -> None:
        lim, main = self.cfg["limits"], self.cfg["git"]["main_branch"]
        self.ensure_phase_material(phase)
        self.gate_with_fixes(phase, f"{phase}.G")
        for r in range(lim["review_rounds"] + 1):
            approved, text = self.review(phase)
            if approved:
                break
            if r == lim["review_rounds"]:
                raise Halt(f"un revisor rechazó {phase} tras {lim['review_rounds']} rondas de corrección")
            if self.review_touches_tests(text):
                self.test_fix_session(phase, f"{phase}.R{r + 1}T", text)
            self.fix_session(phase, f"{phase}.R{r + 1}", f"Un revisor rechazó la fase:\n{text}")
            # A review fix may break the gate (refactors, new checks): repair it before re-review.
            self.gate_with_fixes(phase, f"{phase}.R{r + 1}G")
        if self.gate_has_long_steps(phase):
            # La puerta completa (full, laboratorio, nivel 2) corre una vez, ya aprobada la revisión.
            self.gate_with_fixes(phase, f"{phase}.F", quick=False)
        if not self.dry:
            ok, out = self.sh(f"python3 scripts/lock_check.py {main} HEAD")
            if not ok:
                raise Halt(f"lock-check falló en {phase}:\n{tail(out, 20)}")
        self.integrate(phase)
        self.wait_ci(phase)
        self.state["phases"][phase]["status"] = "done"
        self.save()
        self.run_hook("snapshot", "PGUI_SNAPSHOT", f"{phase.replace('.', '-')}-ok")
        self.notify(f"{phase} aprobada e integrada en {self.integration_branch()} "
                    f"(gasto acumulado {self.state['spend']['total']:.2f} USD)")

    # ---------- commands ----------
    def cmd_init(self) -> None:
        need = ["docs/spec/v0.1.1.md", "docs/plan.md", "AGENTS.md", "CLAUDE.md", ".claude/settings.json",
                ".claude/hooks/guard.py", "opencode.json", ".opencode/plugins/pgui-guard.js", "scripts/gate.py",
                "scripts/lock_check.py", "scripts/accept_check.py", "gates.toml", "tests/acceptance/manifest.toml",
                "Makefile", "lab/run.sh", "lab/lib.sh", "orchestrator/tasks.toml"]
        missing = [n for n in need if not (ROOT / n).exists()]
        if missing:
            raise Halt(f"faltan archivos para empezar: {missing}")
        problems = []
        tasks = {t.id: t for t in self.tasks}
        for t in self.manifest():
            tid = t.get("task")
            if tid and tid not in tasks:
                problems.append(f"{t['name']}: la tarea {tid} no existe en tasks.toml")
            elif tid and t["kind"] == "lab" and tasks[tid].role != "test_author":
                problems.append(f"{t['name']}: un escenario de laboratorio debe ser de una tarea test_author ({tid})")
            elif tid and t["kind"] in ("py", "ts", "sh") and not tasks[tid].acceptance:
                problems.append(f"{t['name']}: la tarea {tid} no tiene acceptance = true")
        for p in self.phases:
            if p not in tomllib.loads((ROOT / "gates.toml").read_text()).get("phase", {}):
                problems.append(f"la fase {p} no está en gates.toml")
        if problems:
            raise Halt("kit inconsistente: " + "; ".join(problems))
        for path, body in DOC_STUBS.items():
            if not (ROOT / path).exists():
                (ROOT / path).write_text(body)
        ok, out = self.sh("make selftest", timeout=900)
        if not ok:
            raise Halt(f"las autopruebas del kit fallan:\n{tail(out, 30)}")
        problems = self.agent_problems()
        print("Kit verificado." + (f" Avisos: {problems}" if problems else ""))
        print("Siguiente: `seal` dos veces, `dry-run`, y `run`.")

    def cmd_seal(self) -> None:
        dirty = [f for f in self.changed_files() if starts(f, PROTECTED)]
        if dirty:
            raise Halt(f"hay cambios sin commit en el material de aceptación: {dirty}")
        d = self.protected_digest()
        prev = self.state["locked"].get("digest")
        self.state["locked"]["digest"] = d
        self.save()
        print(f"huella sellada: {d}" + (" (igual a la anterior)" if prev == d else ""))

    def cmd_verify(self) -> int:
        locked = self.state["locked"].get("digest")
        now = self.protected_digest()
        print(f"sellada: {locked}\nactual:  {now}")
        return 0 if locked == now else 3

    def cmd_run(self, only_phase: str | None) -> int:
        try:
            self.check_agents()
            for phase in self.phases:
                if only_phase and phase != only_phase:
                    continue
                self.run_phase(phase)
            if not only_phase and all(self.state["phases"].get(p, {}).get("status") == "done" for p in self.phases):
                self.finish()
            return 0
        except Halt as h:
            self.notify(f"DETENIDO: {h}")
            return 3

    def cmd_once(self, task_id: str) -> int:
        task = next((t for t in self.tasks if t.id == task_id), None)
        if task is None:
            print(f"no existe la tarea {task_id}")
            return 2
        try:
            self.check_agents()
            if task.role == "implementer":
                self.write_tests(task)
                self.implement(task)
            else:
                self.special_task(task)
            return 0
        except Halt as h:
            self.notify(f"DETENIDO: {h}")
            return 3

    def cmd_status(self) -> None:
        sp = self.state["spend"]
        print(f"Gasto total: {sp.get('total', 0.0):.2f} USD")
        for phase in self.phases:
            ps = self.state["phases"].get(phase, {}).get("status", "pending")
            print(f"\n{phase}  [{ps}]  gasto {sp.get(phase, 0.0):.2f} USD")
            for t in [t for t in self.tasks if t.phase == phase]:
                st = self.state["tasks"].get(t.id, {})
                print(f"  {t.id:7} {st.get('status', 'pending'):8} pruebas:{st.get('tests', '-'):8}"
                      f" intentos:{st.get('attempts', 0)}  {t.title}")
        print("\nÚltimos eventos:")
        for e in self.state["events"][-10:]:
            print(" ", e)

    def cmd_reset(self, task_id: str) -> None:
        self.state["tasks"].pop(task_id, None)
        self.save()
        print(f"{task_id} vuelve a pendiente")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["init", "seal", "verify", "run", "dry-run", "status", "once", "reset"])
    ap.add_argument("task", nargs="?")
    ap.add_argument("--phase")
    ap.add_argument("--config", default=str(HERE / "orchestrator.toml"))
    a = ap.parse_args()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _forward_term)
    if a.command in ("run", "once"):
        import fcntl
        LOG_DIR.mkdir(exist_ok=True)
        lock = open(LOG_DIR / ".lock", "w")  # noqa: SIM115 (se mantiene abierto mientras corre)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("DETENIDO: ya hay otro orquestador corriendo (orchestrator/logs/.lock)")
            return 2
        lock.write(str(os.getpid()))
        lock.flush()
    orch = Orchestrator(Path(a.config))
    try:
        if a.command == "init":
            orch.cmd_init()
            return 0
        if a.command == "seal":
            orch.cmd_seal()
            return 0
        if a.command == "verify":
            return orch.cmd_verify()
        if a.command == "status":
            orch.cmd_status()
            return 0
        if a.command == "reset":
            if not a.task:
                return 2
            orch.cmd_reset(a.task)
            return 0
        if a.command == "once":
            return orch.cmd_once(a.task) if a.task else 2
        if a.command == "dry-run":
            orch.dry = True
            orch.state = {"tasks": {}, "phases": {}, "spend": {"total": 0.0}, "locked": {}, "events": []}
            orch.save = lambda: None  # never touch the real state
            return orch.cmd_run(a.phase)
        return orch.cmd_run(a.phase)
    except Halt as h:
        print(f"DETENIDO: {h}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
