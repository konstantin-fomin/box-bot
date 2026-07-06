from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from ..database import Box, STATUS_LABELS
from .ai_actions import AiCommand, ai_commands_from_dicts


class BoxAiParser(Protocol):
    async def parse_message(self, text: str, boxes: list[Box]) -> list[AiCommand]:
        ...

    async def parse_voice_message(self, audio: bytes, mime_type: str, boxes: list[Box]) -> list[AiCommand]:
        ...

    async def parse_items_voice(self, audio: bytes, mime_type: str) -> list[str]:
        ...


class GeminiBoxAiParser:
    def __init__(self, api_key: str, model: str) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def parse_message(self, text: str, boxes: list[Box]) -> list[AiCommand]:
        response_text = await asyncio.to_thread(self._generate_text_content, text, boxes)
        return parse_ai_commands_response(response_text)

    async def parse_voice_message(self, audio: bytes, mime_type: str, boxes: list[Box]) -> list[AiCommand]:
        response_text = await asyncio.to_thread(self._generate_voice_content, audio, mime_type, boxes)
        return parse_ai_commands_response(response_text)

    async def parse_items_voice(self, audio: bytes, mime_type: str) -> list[str]:
        response_text = await asyncio.to_thread(self._generate_items_voice_content, audio, mime_type)
        return parse_items_response(response_text)

    def _generate_text_content(self, text: str, boxes: list[Box]) -> str:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model,
            contents=_text_user_prompt(text, boxes),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        return response.text or "[]"

    def _generate_voice_content(self, audio: bytes, mime_type: str, boxes: list[Box]) -> str:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model,
            contents=[
                _voice_user_prompt(boxes),
                types.Part.from_bytes(data=audio, mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        return response.text or "[]"

    def _generate_items_voice_content(self, audio: bytes, mime_type: str) -> str:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model,
            contents=[
                "Распознай голосовое сообщение и верни JSON-массив строк с вещами для коробки.",
                types.Part.from_bytes(data=audio, mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(
                system_instruction=ITEMS_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        return response.text or "[]"


SYSTEM_PROMPT = """
Ты помощник Telegram-бота для учёта коробок при переезде.
Разбирай русские сообщения пользователя в строгий JSON-массив действий.
Не добавляй markdown, пояснения или текст вне JSON.

Доступные действия:
{"action":"search","query":"что искать"}
{"action":"create_box","room":"комната или категория","items":["вещь 1","вещь 2"]}
{"action":"add_items","box_id":123,"box_code":"KITCH-01","items":["вещь 1","вещь 2"]}
{"action":"update_status","box_id":123,"box_code":"KITCH-01","status":"with_me|store|send_if_needed"}
{"action":"delete_box","box_id":123,"box_code":"KITCH-01"}

Для add_items, update_status и delete_box используй только box_id из списка текущих коробок.
Если уверенного совпадения с коробкой нет, верни [].
Если пользователь спрашивает где лежит вещь, хочет найти коробку или пишет короткий запрос без команды,
верни search. Если пользователь перечисляет комнату и вещи для новой коробки, верни create_box.
Сохраняй названия вещей без сокращений и пересказа.

Статусы:
with_me — с собой
store — хранить
send_if_needed — прислать по необходимости
""".strip()


ITEMS_SYSTEM_PROMPT = """
Ты помощник Telegram-бота для учёта коробок при переезде.
Распознай голосовое сообщение как список вещей для одной коробки.
Верни строгий JSON-массив строк. Не добавляй markdown, пояснения или текст вне JSON.
Сохраняй бренды, цвета, получателей и уточнения в названии вещи.
Если вещей нет, верни [].
""".strip()


def parse_ai_commands_response(response_text: str) -> list[AiCommand]:
    payload = json.loads(_strip_json_fence(response_text))
    if not isinstance(payload, list):
        raise ValueError("Gemini должен вернуть JSON-массив")
    return ai_commands_from_dicts(payload)


def parse_items_response(response_text: str) -> list[str]:
    payload = json.loads(_strip_json_fence(response_text))
    if not isinstance(payload, list):
        raise ValueError("Gemini должен вернуть JSON-массив")
    items = [_optional_string(item) for item in payload]
    return [item for item in items if item]


def _text_user_prompt(text: str, boxes: list[Box]) -> str:
    return (
        "Текущие коробки для сопоставления действий:\n"
        f"{json.dumps(_boxes_payload(boxes), ensure_ascii=False)}\n\n"
        "Сообщение пользователя:\n"
        f"{text}"
    )


def _voice_user_prompt(boxes: list[Box]) -> str:
    return (
        "Текущие коробки для сопоставления действий:\n"
        f"{json.dumps(_boxes_payload(boxes), ensure_ascii=False)}\n\n"
        "Распознай голосовое сообщение и верни JSON-массив действий."
    )


def _boxes_payload(boxes: list[Box]) -> list[dict[str, Any]]:
    return [
        {
            "id": box.id,
            "code": box.code,
            "room": box.room,
            "status": box.status,
            "status_label": STATUS_LABELS.get(box.status, box.status),
            "items": list(box.items),
        }
        for box in boxes
    ]


def _strip_json_fence(value: str) -> str:
    value = value.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.lower() == "null":
        return None
    return value
