"""
Generic agentic loop for MeznaQuantFX AI agents.

Implements the standard Claude tool-use agent pattern:
  1. Claude receives task + tools
  2. Claude calls tool(s) it needs
  3. We execute the tools and return results
  4. Claude reasons over results, calls more tools or produces final answer
  5. Repeat until stop_reason == "end_turn" or max_iterations reached

Design principles:
  - Tool failures are returned to Claude as error results (not raised).
    Claude can decide whether to retry with different params or conclude.
  - Prompt caching on system prompt: billed once per 5-min TTL even across
    many iterations of the loop within the same window.
  - Max iterations guard: prevents runaway loops on pathological inputs.
  - Full message history is maintained for multi-step reasoning.
  - Never raises: all errors produce a structured error result.

Usage:
    result = await run_agent(
        client=anthropic_client,
        model="claude-sonnet-4-5",
        system_prompt="You are...",
        tools=[tool_def1, tool_def2],
        task="What caused our stat_arb to underperform this week?",
        tool_executor=my_executor_fn,
        max_iterations=15,
        timeout_secs=120.0,
    )
    # result["answer"]  — Claude's final narrative
    # result["iterations"] — how many tool-call rounds were needed
    # result["tools_called"] — list of tool names called (for logging)
"""

import asyncio
import json
import time
from typing import Any, Callable, Awaitable

import anthropic
import structlog

log = structlog.get_logger()

_DEFAULT_MAX_ITERATIONS = 12
_DEFAULT_TIMEOUT_SECS = 120.0


async def run_agent(
    client: anthropic.AsyncAnthropic,
    model: str,
    system_prompt: str,
    tools: list[dict],
    task: str,
    tool_executor: Callable[[str, dict], Awaitable[Any]],
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    timeout_secs: float = _DEFAULT_TIMEOUT_SECS,
    extra_context: str | None = None,
) -> dict:
    """
    Run an agentic loop until Claude produces a final answer.

    Args:
        client:         AsyncAnthropic instance
        model:          Model string (e.g. "claude-sonnet-4-5")
        system_prompt:  System instructions for the agent
        tools:          List of tool definitions (Anthropic tool format)
        task:           The user task / question
        tool_executor:  Async callable: (tool_name: str, tool_input: dict) → Any
        max_iterations: Hard cap on tool-call rounds (default 12)
        timeout_secs:   Wall-clock timeout for the entire agent run (default 120s)
        extra_context:  Optional additional context appended to the initial message

    Returns:
        {
            "answer":       str   — Claude's final narrative answer
            "iterations":   int   — number of tool-call rounds used
            "tools_called": list  — tool names called (in order)
            "timed_out":    bool  — True if wall-clock timeout was hit
            "error":        str | None — error description if something failed
        }
    """
    start_time = time.monotonic()
    tools_called: list[str] = []

    initial_content = task
    if extra_context:
        initial_content = f"{task}\n\n---\nAdditional context:\n{extra_context}"

    messages: list[dict] = [{"role": "user", "content": initial_content}]

    for iteration in range(max_iterations):
        # Wall-clock guard
        elapsed = time.monotonic() - start_time
        remaining = timeout_secs - elapsed
        if remaining <= 5.0:
            log.warning("agent.timeout_approaching", iteration=iteration, elapsed=elapsed)
            return {
                "answer": _extract_last_text(messages),
                "iterations": iteration,
                "tools_called": tools_called,
                "timed_out": True,
                "error": f"Wall-clock timeout after {elapsed:.1f}s",
            }

        try:
            response = await asyncio.wait_for(
                client.messages.create(
                    model=model,
                    max_tokens=4096,
                    # Cache system prompt — billed once per 5-min TTL across all iterations
                    system=[
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    tools=tools,
                    messages=messages,
                ),
                timeout=min(remaining - 2.0, 60.0),
            )
        except asyncio.TimeoutError:
            return {
                "answer": _extract_last_text(messages),
                "iterations": iteration,
                "tools_called": tools_called,
                "timed_out": True,
                "error": "Claude API call timed out",
            }
        except anthropic.APIError as exc:
            log.error("agent.api_error", iteration=iteration, error=str(exc))
            return {
                "answer": _extract_last_text(messages),
                "iterations": iteration,
                "tools_called": tools_called,
                "timed_out": False,
                "error": f"Anthropic API error: {str(exc)[:100]}",
            }

        # Append Claude's response to history
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Claude has produced its final answer
            answer = _extract_text_from_content(response.content)
            log.info(
                "agent.completed",
                iterations=iteration + 1,
                tools_called=tools_called,
                answer_len=len(answer),
            )
            return {
                "answer": answer,
                "iterations": iteration + 1,
                "tools_called": tools_called,
                "timed_out": False,
                "error": None,
            }

        if response.stop_reason == "tool_use":
            # Execute each tool Claude requested, collect results
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                tools_called.append(tool_name)

                log.info(
                    "agent.tool_call",
                    iteration=iteration,
                    tool=tool_name,
                    input_keys=list(tool_input.keys()),
                )

                try:
                    tool_result = await asyncio.wait_for(
                        tool_executor(tool_name, tool_input),
                        timeout=30.0,  # Per-tool timeout
                    )
                    result_content = (
                        json.dumps(tool_result)
                        if not isinstance(tool_result, str)
                        else tool_result
                    )
                    is_error = False
                except asyncio.TimeoutError:
                    result_content = json.dumps({"error": f"Tool {tool_name} timed out after 30s"})
                    is_error = True
                    log.warning("agent.tool_timeout", tool=tool_name, iteration=iteration)
                except Exception as exc:
                    result_content = json.dumps({"error": f"Tool execution failed: {str(exc)[:100]}"})
                    is_error = True
                    log.error("agent.tool_error", tool=tool_name, error=str(exc))

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_content,
                    **({"is_error": True} if is_error else {}),
                })

            messages.append({"role": "user", "content": tool_results})

        else:
            # Unexpected stop reason
            log.warning("agent.unexpected_stop_reason", stop_reason=response.stop_reason)
            break

    # Max iterations reached — return whatever text we have
    log.warning("agent.max_iterations_reached", max_iterations=max_iterations, tools_called=tools_called)
    return {
        "answer": _extract_last_text(messages),
        "iterations": max_iterations,
        "tools_called": tools_called,
        "timed_out": False,
        "error": f"Max iterations ({max_iterations}) reached without final answer",
    }


def _extract_text_from_content(content: list) -> str:
    """Extract text from the last assistant content block list."""
    return " ".join(
        block.text for block in content
        if hasattr(block, "text") and block.type == "text"
    ).strip()


def _extract_last_text(messages: list[dict]) -> str:
    """Walk messages in reverse and return the last text we got from Claude."""
    for msg in reversed(messages):
        if msg["role"] != "assistant":
            continue
        content = msg["content"]
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for block in content:
                if hasattr(block, "text") and block.type == "text":
                    return block.text
    return "No answer produced yet."
