// pgui-guard: aplica en OpenCode las mismas reglas que .claude/hooks/guard.py aplica en
// Claude Code (sala limpia, escritura por rol, sin git de escritura). Traduce cada llamada de
// herramienta de OpenCode al formato del hook de Claude Code y llama al mismo guard.py, de modo
// que solo hay una implementación de las reglas. Si guard.py bloquea (código 2), la herramienta
// no se ejecuta y el modelo recibe el motivo.
//
// Falla cerrado: dentro del modo autónomo (PGUI_ROLE definido), si guard.py no se puede
// ejecutar, la herramienta se bloquea.
import { spawnSync } from "node:child_process"
import path from "node:path"

const WRITE_TOOLS = new Set(["write", "edit", "multiedit"])

// apply_patch / patch: extrae las rutas de las cabeceras "*** Add|Update|Delete File: ruta"
// y "*** Move to: ruta".
function patchPaths(text) {
  const out = []
  for (const line of String(text || "").split("\n")) {
    const m = line.match(/^\*\*\*\s+(?:(?:Add|Update|Delete)\s+File|Move\s+to):\s*(.+)$/)
    if (m) out.push(m[1].trim())
  }
  return out
}

function toClaude(tool, args) {
  const a = args || {}
  const file = a.filePath ?? a.file_path ?? a.path
  if (WRITE_TOOLS.has(tool)) return [{ tool_name: "Write", tool_input: { ...a, file_path: file } }]
  if (tool === "patch" || tool === "apply_patch") {
    const text = a.patchText ?? a.patch ?? a.input ?? ""
    const files = patchPaths(text)
    const calls = files.map((f) => ({ tool_name: "Write", tool_input: { file_path: f } }))
    // El texto del parche también pasa por la regla de sala limpia.
    calls.push({ tool_name: "Patch", tool_input: { text } })
    return calls
  }
  switch (tool) {
    case "bash": return [{ tool_name: "Bash", tool_input: { command: a.command ?? "" } }]
    case "read": return [{ tool_name: "Read", tool_input: { ...a, file_path: file } }]
    case "grep": return [{ tool_name: "Grep", tool_input: { ...a, path: a.path ?? "" } }]
    case "glob":
    case "list": return [{ tool_name: "Glob", tool_input: { ...a, path: a.path ?? "" } }]
    case "webfetch": return [{ tool_name: "WebFetch", tool_input: a }]
    case "websearch": return [{ tool_name: "WebSearch", tool_input: a }]
    default: return [{ tool_name: tool, tool_input: a }]
  }
}

export const PgUiGuard = async ({ directory, worktree }) => {
  const root = worktree || directory
  const guard = path.join(root, ".claude", "hooks", "guard.py")
  const inAutonomousMode = Boolean(process.env.PGUI_ROLE)

  return {
    "tool.execute.before": async (input, output) => {
      for (const call of toClaude(input.tool, output.args)) {
        const r = spawnSync("python3", [guard], {
          input: JSON.stringify(call),
          env: { ...process.env, CLAUDE_PROJECT_DIR: root },
          cwd: root,
          encoding: "utf8",
          timeout: 15000,
        })
        if (r.status === 2) {
          throw new Error((r.stderr || "BLOQUEADO por guard.py").trim())
        }
        if (r.status !== 0 && inAutonomousMode) {
          throw new Error(`pgui-guard: no se pudo ejecutar guard.py (${r.error?.message || r.stderr || r.status}); herramienta bloqueada`)
        }
      }
    },
  }
}
