from __future__ import annotations

import hashlib
import json
import logging
from typing import AsyncIterator

from offline_rag.agent.tools import TOOL_DEFS, RISKY_TOOLS, STREAMING_TOOLS, execute_tool, execute_tool_streaming

logger = logging.getLogger(__name__)

MAX_STEPS = 15

AGENT_SYSTEM = """\
You are a universal AI assistant with tool access running on the user's local machine.

## MOST IMPORTANT RULES — follow these exactly
1. **Act immediately.** When the user asks you to do something, call the right tool NOW. \
Do NOT say "I'll do that", "Let me...", "Sure!", or "I'll start by...". Just call the tool.
2. **Never ask for permission** for non-risky actions. Just do it.
3. **Never describe what you're about to do** — the tool output speaks for itself.
4. After tool results come back, give a short summary of what happened and what's next.
5. If a task has multiple steps, execute the first tool call immediately with no preamble.

## Risky actions (system will ask user to confirm — you do NOT need to warn them)
run_python · run_shell · write_file · move_file · delete_file · install_package · delete_skill

## Tools
- web_search      — DuckDuckGo search
- web_fetch       — Read any webpage
- run_python      — Execute Python on user's Windows machine ⚠ needs confirmation
- run_shell       — Execute PowerShell ⚠ needs confirmation
- read_file       — Read any file
- write_file      — Create/overwrite a file ⚠ needs confirmation
- list_files      — List files in a directory
- move_file       — Move or rename ⚠ needs confirmation
- delete_file     — Delete ⚠ needs confirmation
- make_slides     — Create .pptx or .pdf presentation
- rag_search      — Search the user's indexed documents
- index_documents — Index documents so they become searchable (streams live progress)
- learn_skill     — Research + save a new Python skill permanently
- list_skills     — List saved skills
- delete_skill    — Delete a skill ⚠ needs confirmation

## Other rules
- If you don't know how to do something, web_search + web_fetch to learn it, then learn_skill.
- When you learn a new skill, call it immediately to complete the task.
- Cite source URLs when answering from web results.
- Use rag_search first when the user asks about their own documents.
- If a tool fails, try a different approach without asking the user.

## Environment
- Windows 11, PowerShell 5.1, Python via miniconda at C:/Users/Neel/miniconda3
- User home: C:/Users/Neel
"""

# Pending confirmations: hash(original_messages) → {"tool", "args", "history"}
_pending: dict[str, dict] = {}


def _conv_key(messages: list[dict]) -> str:
    normalized = [{"role": m["role"], "content": m.get("content", "")} for m in messages]
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()[:16]


def _is_affirmative(text: str) -> bool:
    t = text.lower().strip().rstrip("!.,")
    return any(
        t.startswith(w)
        for w in [
            "yes", "y", "ok", "sure", "go", "proceed", "confirm",
            "do it", "yeah", "yep", "yup", "affirmative", "run it",
            "execute", "please", "sounds good", "looks good",
        ]
    )


def _tool_progress(name: str, args: dict) -> str:
    match name:
        case "web_search":
            label = f'Searching web: "{args.get("query", "")}"'
            icon = "🔍"
        case "web_fetch":
            label = f'Reading: {args.get("url", "")}'
            icon = "📄"
        case "read_file":
            label = f'Reading file: {args.get("path", "")}'
            icon = "📂"
        case "list_files":
            label = f'Listing: {args.get("path", "")}'
            icon = "📁"
        case "make_slides":
            label = f'Creating presentation: {args.get("title", "")}'
            icon = "📊"
        case "rag_search":
            label = f'Searching your documents: "{args.get("query", "")}"'
            icon = "🔎"
        case "install_package":
            label = f'Installing: {args.get("package", "")}'
            icon = "📦"
        case "index_documents":
            paths = ", ".join(args.get("paths", []))
            label = f'Indexing: {paths}'
            icon = "📥"
        case "learn_skill":
            label = f'Learning new skill: {args.get("name", "")}'
            icon = "🧠"
        case "list_skills":
            label = "Listing saved skills"
            icon = "📋"
        case "delete_skill":
            label = f'Deleting skill: {args.get("name", "")}'
            icon = "🗑️"
        case "run_python":
            label = f'Running Python: {args.get("description", "code")}'
            icon = "🐍"
        case "run_shell":
            label = f'Running shell: {args.get("description", args.get("command", ""))[:60]}'
            icon = "💻"
        case "write_file":
            label = f'Writing file: {args.get("path", "")}'
            icon = "✏️"
        case _:
            label = f'Running: {name}'
            icon = "⚙️"
    return f'```ui\n{{"type":"activity","icon":"{icon}","label":{json.dumps(label)}}}\n```'


def _confirmation_prompt(name: str, args: dict) -> str:
    match name:
        case "run_python":
            code = args.get("code", "")
            desc = args.get("description", "")
            return (
                f"⚠️ **Run Python code?**\n\n"
                f"**What it does:** {desc}\n\n"
                f"```python\n{code}\n```\n\n"
                f"Reply **yes** to run or **no** to cancel."
            )
        case "run_shell":
            cmd = args.get("command", "")
            desc = args.get("description", "")
            return (
                f"⚠️ **Run PowerShell command?**\n\n"
                f"**What it does:** {desc}\n\n"
                f"```powershell\n{cmd}\n```\n\n"
                f"Reply **yes** to run or **no** to cancel."
            )
        case "write_file":
            path = args.get("path", "")
            desc = args.get("description", "")
            preview = args.get("content", "")[:400]
            ellipsis = "..." if len(args.get("content", "")) > 400 else ""
            return (
                f"⚠️ **Write file?**\n\n"
                f"**Path:** `{path}`\n"
                f"**What:** {desc}\n\n"
                f"**Preview:**\n```\n{preview}{ellipsis}\n```\n\n"
                f"Reply **yes** to write or **no** to cancel."
            )
        case "move_file":
            return (
                f"⚠️ **Move file?**\n\n"
                f"`{args.get('src', '')}` → `{args.get('dst', '')}`\n\n"
                f"Reply **yes** to move or **no** to cancel."
            )
        case "delete_file":
            recursive = args.get("recursive", False)
            target = "directory and all its contents" if recursive else "file"
            return (
                f"⚠️ **Delete {target}? This cannot be undone.**\n\n"
                f"`{args.get('path', '')}`\n\n"
                f"Reply **yes** to delete or **no** to cancel."
            )
        case "install_package":
            return (
                f"⚠️ **Install Python package?**\n\n"
                f"`pip install {args.get('package', '')}`\n\n"
                f"Reply **yes** to install or **no** to cancel."
            )
        case "delete_skill":
            return (
                f"⚠️ **Permanently delete learned skill?**\n\n"
                f"Skill: `{args.get('name', '')}`\n\n"
                f"Reply **yes** to delete or **no** to cancel."
            )
        case _:
            return (
                f"⚠️ **Confirm:** `{name}({json.dumps(args, ensure_ascii=False)})`\n\n"
                f"Reply **yes** to proceed or **no** to cancel."
            )


async def run_agent(
    messages: list[dict],
    model: str,
    config,
    ollama,
    store,
    skill_registry=None,
) -> AsyncIterator[str]:
    """ReAct agent loop. Yields text chunks for streaming to the client."""

    history: list[dict] = []
    store_key = _conv_key(messages)

    # ------------------------------------------------------------------
    # Resume from a pending confirmation if the previous turn asked for one
    # ------------------------------------------------------------------
    # After we return a confirmation prompt, the next request will have:
    #   messages = [...original_msgs..., assistant_confirm, user_yes_or_no]
    # so hash(messages[:-2]) == the key we stored.
    if len(messages) >= 2 and messages[-2].get("role") == "assistant":
        candidate_key = _conv_key(messages[:-2])
        pending = _pending.get(candidate_key)
        if pending:
            del _pending[candidate_key]
            last_user = messages[-1].get("content", "")

            if _is_affirmative(last_user):
                yield "✅ **Confirmed. Executing...**\n\n"
                try:
                    result = await execute_tool(
                        pending["tool"],
                        pending["args"],
                        config=config,
                        store=store,
                        ollama=ollama,
                        skill_registry=skill_registry,
                    )
                except Exception as exc:
                    result = f"Error: {exc}"
                    logger.exception("Confirmed tool %s failed", pending["tool"])

                display = result if len(result) <= 4000 else result[:4000] + "\n...[truncated]"
                yield f"```\n{display}\n```\n\n"

                # Continue agent loop from stored history + new tool result
                history = pending["history"] + [
                    {"role": "tool", "content": result, "name": pending["tool"]}
                ]
                store_key = _conv_key(messages)  # update key for any subsequent confirmations
            else:
                yield "❌ Cancelled. Let me know if you'd like a different approach."
                return

    # ------------------------------------------------------------------
    # Build fresh history if not resuming
    # ------------------------------------------------------------------
    if not history:
        user_msgs = [m for m in messages if m.get("role") != "system"]
        history = [{"role": "system", "content": AGENT_SYSTEM}] + [
            {"role": m["role"], "content": m.get("content", "")} for m in user_msgs
        ]

    # ------------------------------------------------------------------
    # ReAct loop
    # ------------------------------------------------------------------
    for _step in range(MAX_STEPS):
        content_acc = ""
        final_tool_calls: list[dict] = []

        # Rebuild tool list each step so newly learned skills are available immediately
        all_tools = TOOL_DEFS + (skill_registry.tool_defs() if skill_registry else [])

        async for delta, tc_list in ollama.chat_stream_with_tools(model, history, all_tools):
            if tc_list is None:
                # Streaming text delta
                content_acc += delta
                yield delta
            else:
                # Stream ended — tc_list is [] (text only) or [tool calls]
                if delta:
                    content_acc += delta
                    yield delta
                final_tool_calls = tc_list

        if not final_tool_calls:
            # Pure text response — done
            history.append({"role": "assistant", "content": content_acc})
            return

        # Model wants to call tools
        assistant_msg: dict = {
            "role": "assistant",
            "content": content_acc,
            "tool_calls": final_tool_calls,
        }
        history.append(assistant_msg)

        for tc in final_tool_calls:
            fn = tc.get("function", {})
            tool_name: str = fn.get("name", "")
            raw_args = fn.get("arguments", {})
            tool_args: dict = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)

            if tool_name in RISKY_TOOLS:
                # Pause and ask for confirmation
                if content_acc:
                    yield "\n\n"
                yield _confirmation_prompt(tool_name, tool_args)
                _pending[store_key] = {
                    "tool": tool_name,
                    "args": tool_args,
                    "history": history,
                }
                return

            # Non-risky — execute (with streaming progress for supported tools)
            yield f"\n\n{_tool_progress(tool_name, tool_args)}\n"
            result = ""
            try:
                async for chunk, is_final in execute_tool_streaming(
                    tool_name, tool_args,
                    config=config, store=store, ollama=ollama,
                    skill_registry=skill_registry,
                ):
                    if is_final:
                        result = chunk
                        if chunk:
                            if tool_name in STREAMING_TOOLS:
                                # Already formatted markdown (ui blocks etc.)
                                yield f"\n{chunk}\n"
                            else:
                                display = chunk if len(chunk) <= 4000 else chunk[:4000] + "\n...[truncated]"
                                yield f"\n```\n{display}\n```\n"
                    else:
                        yield chunk  # stream progress to client
            except Exception as exc:
                result = f"Error executing {tool_name}: {exc}"
                logger.exception("Tool %s failed", tool_name)
                yield f"\n```\n{result}\n```\n"

            history.append({"role": "tool", "content": result, "name": tool_name})
            content_acc = ""

    yield "\n\n⚠️ Reached the step limit. The task may be incomplete — let me know how to continue."
