// gitvow: installed by `gitvow install --agent opencode`; `gitvow uninstall --agent opencode` removes this file.
// Do not edit: install rewrites it. See https://wirevow.dev/gitvow/guides/other-agents/
import { spawnSync } from "node:child_process";

const GITVOW_HOOK = "gitvow hook --agent opencode"; // "<gitvow> hook --agent opencode"
const ARGV = ["gitvow", "hook", "--agent", "opencode"];

function run(event, payload) {
  const r = spawnSync(ARGV[0], ARGV.slice(1).concat([event]), {
    input: JSON.stringify(payload),
    encoding: "utf8",
    timeout: 30000,
  });
  return { code: r.status, err: (r.stderr || "").trim(), out: (r.stdout || "").trim(), spawn: r.error };
}

export const GitvowPlugin = async ({ directory, worktree }) => {
  const cwd = worktree || directory;
  return {
    event: async ({ event }) => {
      const t = event && event.type;
      const p = (event && event.properties) || {};
      const sid = (p.info && p.info.id) || p.sessionID || "";
      if (t === "session.created") run("SessionStart", { session_id: sid, cwd });
      if (t === "session.idle") run("Stop", { session_id: sid, cwd });
    },
    "chat.message": async (input, output) => {
      const parts = (output && output.parts) || [];
      const text = parts.filter((x) => x && x.type === "text").map((x) => x.text || "").join("\n");
      run("UserPromptSubmit", { session_id: input.sessionID, cwd, prompt: text });
    },
    "tool.execute.before": async (input, output) => {
      const r = run("PreToolUse", {
        session_id: input.sessionID,
        cwd,
        tool_name: input.tool,
        tool_input: (output && output.args) || {},
        tool_use_id: input.callID,
      });
      // Fail closed: a gate that did not run is not a gate. Exit 2 is gitvow's verdict, anything else is a fault.
      if (r.spawn) throw new Error("gitvow hook did not run: " + r.spawn.message);
      if (r.code === 2) throw new Error(r.err || "blocked by gitvow");
      if (r.code !== 0) throw new Error("gitvow hook failed (" + r.code + "): " + r.err);
    },
    "tool.execute.after": async (input) => {
      run("PostToolUse", {
        session_id: input.sessionID,
        cwd,
        tool_name: input.tool,
        tool_input: input.args || {},
        tool_use_id: input.callID,
      });
    },
  };
};
