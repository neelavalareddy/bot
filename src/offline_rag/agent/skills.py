from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Names the model must not reuse for learned skills
_RESERVED: frozenset[str] = frozenset({
    "web_search", "web_fetch", "run_python", "run_shell",
    "read_file", "write_file", "list_files", "move_file", "delete_file",
    "make_slides", "install_package", "rag_search", "index_documents",
    "learn_skill", "list_skills", "delete_skill",
})

_VALID_NAME = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


class SkillRegistry:
    """Persistent store for learned Python skills.

    Each skill is a JSON file in ``skills_dir`` that contains:
    - name: snake_case identifier (also the function name in the code)
    - description: one-line summary
    - parameters: JSON Schema object used as the Ollama tool parameter spec
    - code: complete Python source that defines a function named ``name``
    """

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._skills: dict[str, dict] = {}
        self._load_all()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_all(self) -> None:
        for f in sorted(self.skills_dir.glob("*.json")):
            try:
                skill = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(skill.get("name"), str):
                    self._skills[skill["name"]] = skill
            except Exception:
                pass

    def _save(self, skill: dict) -> None:
        path = self.skills_dir / f"{skill['name']}.json"
        path.write_text(json.dumps(skill, indent=2, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        code: str,
    ) -> str:
        if name in _RESERVED:
            return f"❌ '{name}' is a reserved tool name. Choose a different skill name."
        if not _VALID_NAME.match(name):
            return (
                f"❌ '{name}' is not a valid skill name. "
                "Use letters, digits, and underscores only, starting with a letter."
            )
        if f"def {name}" not in code:
            return (
                f"❌ The code must define a function named exactly `{name}`. "
                f"Expected `def {name}(...)` in the code."
            )

        overwrite = name in self._skills
        skill = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "code": code,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._skills[name] = skill
        self._save(skill)

        action = "updated" if overwrite else "learned"
        return (
            f"✅ Skill `{name}` {action} and saved permanently.\n"
            f"You can now call `{name}` as a tool in this and future sessions."
        )

    def delete(self, name: str) -> str:
        if name not in self._skills:
            return f"Skill '{name}' not found."
        del self._skills[name]
        path = self.skills_dir / f"{name}.json"
        path.unlink(missing_ok=True)
        return f"Skill '{name}' deleted."

    def list_all(self) -> str:
        if not self._skills:
            return "No skills learned yet. Use `learn_skill` to teach me new capabilities."
        lines: list[str] = []
        for s in self._skills.values():
            created = s.get("created_at", "")[:10]
            lines.append(f"- **{s['name']}** — {s['description']}  *(saved {created})*")
        return f"**{len(lines)} learned skill(s):**\n\n" + "\n".join(lines)

    # ------------------------------------------------------------------
    # Tool definitions (injected into the agent's tool list each step)
    # ------------------------------------------------------------------

    def tool_defs(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": s["name"],
                    "description": f"[Learned skill] {s['description']}",
                    "parameters": s["parameters"],
                },
            }
            for s in self._skills.values()
        ]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(self, name: str, args: dict) -> str:
        skill = self._skills.get(name)
        if not skill:
            return f"Skill '{name}' not found. It may have been deleted."

        # Write a runner script: skill code + a __main__ block that calls the function
        args_json = json.dumps(args, ensure_ascii=False)
        runner = (
            f"{skill['code']}\n\n"
            f"if __name__ == '__main__':\n"
            f"    import json as _json, sys as _sys\n"
            f"    _args = _json.loads({repr(args_json)})\n"
            f"    try:\n"
            f"        _result = {name}(**_args)\n"
            f"        if _result is not None:\n"
            f"            print(str(_result))\n"
            f"    except Exception as _exc:\n"
            f"        print(f'Error: {{_exc}}', file=_sys.stderr)\n"
            f"        _sys.exit(1)\n"
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(runner)
            tmp = Path(f.name)

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(tmp),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path.home()),
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            except asyncio.TimeoutError:
                proc.kill()
                return f"Timeout: skill `{name}` ran for more than 60 seconds."

            out = stdout.decode(errors="replace")
            err = stderr.decode(errors="replace")
            if err:
                out += f"\nSTDERR:\n{err}"
            if proc.returncode != 0:
                out += f"\nExit code: {proc.returncode}"
            return out or "(no output)"
        finally:
            tmp.unlink(missing_ok=True)
