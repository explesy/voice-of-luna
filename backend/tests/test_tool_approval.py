import asyncio

from app.conversation_service import approve_pending_tool, tool_args_hash, wait_for_tool_approval


def test_tool_approval_is_bound_to_arguments() -> None:
    async def exercise() -> None:
        arguments = {"title": "One", "body": "Body"}
        task = asyncio.create_task(wait_for_tool_approval("conv", "github.create_issue", arguments, "explesy/voice-of-luna"))
        await asyncio.sleep(0)
        from app.conversation_service import _pending_tool_approvals
        request = next(iter(_pending_tool_approvals.values()))
        assert not approve_pending_tool(request["id"], tool_args_hash("github.create_issue", {"title": "Changed", "body": "Body"}))
        assert approve_pending_tool(request["id"], request["args_hash"])
        assert await task
    asyncio.run(exercise())
