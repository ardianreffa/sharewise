"""Sharewise: see what your photos reveal before you share them, hide faces and street signs for good,
and share clean copies. Local, no network."""

from __future__ import annotations

from pathlib import Path

TOOLSET = "sharewise"


def register(ctx):
    from . import schemas
    from .tools import Sharewise

    sw = Sharewise(ctx)
    ctx.register_tool(name="sharewise_check", toolset=TOOLSET, schema=schemas.CHECK, handler=sw.check, emoji="🔎")
    ctx.register_tool(name="sharewise_review", toolset=TOOLSET, schema=schemas.REVIEW, handler=sw.review, emoji="🖼️")
    ctx.register_tool(name="sharewise_share", toolset=TOOLSET, schema=schemas.SHARE, handler=sw.share, emoji="🛡️")
    ctx.register_tool(name="sharewise_look", toolset=TOOLSET, schema=schemas.LOOK, handler=sw.look, emoji="👁️")

    def gate(tool_name=None, args=None, **_):
        # Every sharewise_share call is validated here (unknown arguments and invalid codes are blocked), and
        # keeping location is escalated to Hermes' approval prompt. The tool refuses to run without this check.
        if tool_name != "sharewise_share":
            return None
        try:
            return sw.approval_request(args or {})
        except Exception as exc:  # fail closed
            return {"action": "block", "message": f"Sharewise could not check this request: {exc}"}

    ctx.register_hook("pre_tool_call", gate)
    ctx.register_command("sharewise", handler=sw.slash,
                         description="Literal, no AI: /sharewise check <folder> | apply <code> | detect-test <photo> | guard-test <path>")
    skill = Path(__file__).parent / "skills" / "sharewise" / "SKILL.md"
    if skill.exists():
        ctx.register_skill("sharewise", skill)
