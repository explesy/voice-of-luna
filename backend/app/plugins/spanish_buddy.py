from __future__ import annotations

from .base import Plugin, PluginTurnResult, TurnContext


class SpanishBuddyPlugin(Plugin):
    """Voice companion for practicing conversational Spanish with correction & vocabulary guidance."""

    id = "spanish_buddy"
    name = "Spanish Practice"
    description = "Conversational Spanish coach with live grammar tips and vocabulary expansion"
    stt_language = "auto"
    stt_prompt = "Hola, ¿qué tal? Buenos días. Hablamos en español y ruso. Как сказать по-испански, я забыл слово."
    preferred_voice_locale = "es"

    def get_modes(self) -> list[dict[str, str]]:
        return [
            {"id": "casual", "label": "Conversación"},
            {"id": "correction", "label": "Correcciones"},
            {"id": "vocabulary", "label": "Vocabulario +1"},
        ]

    async def system_prompt(self, conversation_id: str) -> str:
        return (
            "Eres un compañero y tutor de conversación amigable y paciente para practicar español llamado Luna. "
            "Tu objetivo principal es mantener una conversación fluida en español adaptada al usuario. "
            "Habitualmente responde en español claro, natural y breve (1 a 3 frases para voz). "
            "SOPORTE BILINGÜE: Si el usuario olvida una palabra, pregunta en ruso cómo se dice algo "
            "(por ejemplo: 'как сказать...', 'что значит...', 'как будет...'), dale enseguida la palabra o frase "
            "en español con una explicación muy concisa, muestra un ejemplo de uso en español y continúa la conversación."
        )

    async def before_turn(self, ctx: TurnContext) -> PluginTurnResult:
        mode = ctx.active_mode or "casual"

        if mode == "correction":
            mode_instruction = (
                "Si detectas algún error gramatical o de vocabulario en el mensaje del usuario, "
                "responde primero con naturalidad a su idea y añade al final una corrección muy breve: "
                "'💡 Tip: se dice...'. Si no hubo errores, solo responde con fluidez."
            )
            mode_label = "ES // CORRECCIÓN"
        elif mode == "vocabulary":
            mode_instruction = (
                "Incorpora de forma natural 1 palabra o expresión idiomática interesante (nivel B2/C1) "
                "en tu respuesta y explícala brevemente con un ejemplo de 1 frase."
            )
            mode_label = "ES // VOCABULARIO"
        else:
            mode_instruction = (
                "Conversación fluida y relajada en español. Haz una pregunta de seguimiento interesante "
                "para mantener el diálogo dinámico."
            )
            mode_label = "ES // CONVERSACIÓN"

        prompt_context = (
            f"[INSTRUCCIÓN DE TUTOR: El diálogo principal es en español. Si el usuario pregunta en ruso cómo decir una palabra "
            f"o qué significa algo, dale la traducción en español, aclara brevemente y anímalo a usarla en la frase.]\n"
            f"[MODO: {mode_instruction}]"
        )

        return PluginTurnResult(
            prompt_context=prompt_context,
            mode_label=mode_label,
            metadata={"mode": mode},
        )
