from __future__ import annotations

import hashlib
import json
import logging
from typing import AsyncIterator

from offline_rag.agent.tools import TOOL_DEFS, RISKY_TOOLS, execute_tool

logger = logging.getLogger(__name__)

MAX_STEPS = 15

AGENT_SYSTEM = """\
You are a universal AI assistant with tool access. You run entirely on the user's local \
machine via Ollama — no cloud, no subscription.

## Tools available
- web_search      — Search DuckDuckGo for anything
- web_fetch       — Read any webpage in full
- run_python      — Execute Python code on the user's Windows machine
- run_shell       — Execute PowerShell on the user's Windows machine
- read_file       — Read any file
- write_file      — Write content to a file (creates or overwrites)
- list_files      — List files in a directory
- move_file       — Move or rename a file
- delete_file     — Delete a file or directory
- make_slides     — Create a PowerPoint (.pptx) or PDF presentation
- rag_search      — Search the user's personal indexed documents
- index_documents — Add documents to the searchable index

## How to behave
1. Be decisive. Make reasonable assumptions rather than asking clarifying questions.
2. If you don't know how to do something, use web_search + web_fetch to learn, then do it.
3. Work step by step. Use tools to gather information before acting.
4. The system automatically asks the user before executing run_python, run_shell, \
write_file, move_file, or delete_file — you do not need to warn the user yourself.
5. Show brief reasoning before each tool call so the user can follow along.
6. When answering from web results, cite the source URL.
7. If a tool fails, adapt and try a different approach.
8. Use rag_search first when the user asks about their own documents.

## Environment
- Windows 11, PowerShell 5.1 available
- Python available as `python`
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
            return f'🔍 Searching: *{args.get("query", "")}*'
        case "web_fetch":
            return f'📄 Fetching: {args.get("url", "")}'
        case "read_file":
            return f'📂 Reading: `{args.get("path", "")}`'
        case "list_files":
            return f'📁 Listing: `{args.get("path", "")}`'
        case "make_slides":
            return f'📊 Creating presentation: *{args.get("title", "")}*'
        case "rag_search":
            return f'🔎 Searching your documents: *{args.get("query", "")}*'
        case "install_package":
            return f'📦 Installing: `{args.get("package", "")}`'
        case "index_documents":
            paths = ", ".join(args.get("paths", []))
            return f'📥 Indexing: {paths}'
        case _:
            return f'⚙️ Running: `{name}`'


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

        async for delta, tc_list in ollama.chat_stream_with_tools(model, history, TOOL_DEFS):
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

            # Non-risky — execute immediately
            yield f"\n\n{_tool_progress(tool_name, tool_args)}\n"
            try:
                result = await execute_tool(
                    tool_name, tool_args,
                    config=config, store=store, ollama=ollama,
                )
            except Exception as exc:
                result = f"Error executing {tool_name}: {exc}"
                logger.exception("Tool %s failed", tool_name)

            display = result if len(result) <= 4000 else result[:4000] + "\n...[truncated]"
            yield f"\n```\n{display}\n```\n"

            history.append({"role": "tool", "content": result, "name": tool_name})
            content_acc = ""

    yield "\n\n⚠️ Reached the step limit. The task may be incomplete — let me know how to continue."
