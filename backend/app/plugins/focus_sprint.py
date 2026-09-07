from __future__ import annotations

import time
from dataclasses import dataclass

from .base import Plugin, PluginTurnResult, TurnContext


@dataclass
class SprintSession:
    started_at: float
    target_seconds: int = 900  # 15 min default
    checkins: int = 0


class FocusSprintPlugin(Plugin):
    """Voice coach for timed focus sprints (10-25 min) with deterministic time tracking."""

    id = "focus_sprint"
    name = "Focus Sprint"
    description = "15-minute voice focus sprint tracker with deterministic phase guidance"

    def __init__(self) -> None:
        self.sessions: dict[str, SprintSession] = {}

    def get_modes(self) -> list[dict[str, str]]:
        return [
            {"id": "15m", "label": "15m Sprint"},
            {"id": "25m", "label": "25m Pomodoro"},
            {"id": "debrief", "label": "Debrief"},
        ]

    async def system_prompt(self, conversation_id: str) -> str:
        return (
            "You are a sharp, supportive voice coach for a focused work sprint. "
            "Reply strictly in 1-2 concise spoken sentences suitable for voice. "
            "Help the user stay entirely on their single task. "
            "Do not allow distractions or finishing early. "
            "If they haven't stated their sprint goal yet, ask for it directly. "
            "Acknowledge their progress with brief encouraging clarity."
        )

    async def before_turn(self, ctx: TurnContext) -> PluginTurnResult:
        now = time.monotonic()
        session = self.sessions.get(ctx.conversation_id)

        target_seconds = 900
        if ctx.active_mode == "25m":
            target_seconds = 1500
        elif ctx.active_mode == "debrief":
            target_seconds = 300

        if session is None or session.target_seconds != target_seconds:
            session = SprintSession(started_at=now, target_seconds=target_seconds)
            self.sessions[ctx.conversation_id] = session

        elapsed = int(now - session.started_at)
        elapsed_m = elapsed // 60
        elapsed_s = elapsed % 60
        target_m = session.target_seconds // 60
        target_s = session.target_seconds % 60

        if ctx.active_mode == "debrief":
            prompt_context = (
                f"[DEBRIEF MODE: Session ended. Elapsed: {elapsed_m:02d}:{elapsed_s:02d}. "
                "Ask the user to summarize what they achieved and what blockers occurred.]"
            )
            mode_label = f"SPRINT // DEBRIEF [{elapsed_m:02d}:{elapsed_s:02d}]"
        elif elapsed < session.target_seconds:
            prompt_context = (
                f"[FOCUS SPRINT: {elapsed_m:02d}:{elapsed_s:02d} / {target_m:02d}:{target_s:02d} elapsed. "
                f"Mode: {ctx.active_mode}. "
                "Do NOT conclude the sprint early. Keep the user focused on their task. "
                "Be concise and encourage staying in the zone.]"
            )
            mode_label = f"SPRINT // {elapsed_m:02d}:{elapsed_s:02d} / {target_m:02d}:00"
        else:
            prompt_context = (
                f"[FOCUS SPRINT COMPLETED: {target_m}m target reached! "
                "Congratulate the user briefly and suggest moving to the debrief or taking a short rest.]"
            )
            mode_label = "SPRINT // FINISHED"

        return PluginTurnResult(
            prompt_context=prompt_context,
            mode_label=mode_label,
            metadata={"elapsed": elapsed, "target": session.target_seconds},
        )

    async def after_turn(self, ctx: TurnContext, assistant_response: str) -> None:
        session = self.sessions.get(ctx.conversation_id)
        if session:
            session.checkins += 1

    async def on_conversation_reset(self, conversation_id: str) -> None:
        self.sessions.pop(conversation_id, None)
