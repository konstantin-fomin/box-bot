from __future__ import annotations

import html
from dataclasses import asdict, dataclass
from typing import Any, Literal

from aiogram.types import Message

from .. import database
from ..config import Config
from ..database import Box, STATUS_LABELS, STATUSES
from ..qr import make_qr_file


AiActionName = Literal["create_box", "add_items", "update_status", "delete_box", "search"]


@dataclass(frozen=True)
class AiCommand:
    action: AiActionName
    box_id: int | None = None
    box_code: str | None = None
    room: str | None = None
    status: str | None = None
    items: list[str] | None = None
    query: str | None = None


@dataclass(frozen=True)
class AiApplyResult:
    created: list[Box]
    updated: list[Box]
    deleted: list[str]
    search_results: list[tuple[str, list[Box]]]

    @property
    def has_changes(self) -> bool:
        return bool(self.created or self.updated or self.deleted)


def ai_commands_to_dicts(commands: list[AiCommand]) -> list[dict[str, Any]]:
    return [asdict(command) for command in commands]


def ai_commands_from_dicts(values: list[dict[str, Any]]) -> list[AiCommand]:
    commands: list[AiCommand] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        command = _command_from_dict(value)
        if command is not None:
            commands.append(command)
    return commands


def mutating_commands(commands: list[AiCommand]) -> list[AiCommand]:
    return [command for command in commands if command.action != "search"]


def search_commands(commands: list[AiCommand]) -> list[AiCommand]:
    return [command for command in commands if command.action == "search"]


def format_ai_confirmation(commands: list[AiCommand], boxes: list[Box]) -> str:
    box_labels = {box.id: f"{box.code} · {box.room}" for box in boxes}
    lines = ["Я поняла так:"]
    for command in commands:
        lines.append(f"• {_format_command(command, box_labels)}")
    lines.append("")
    lines.append("Применить это действие?")
    return "\n".join(lines)


def format_ai_apply_result(result: AiApplyResult) -> str:
    if not result.has_changes and not result.search_results:
        return "Ничего не изменила."

    lines: list[str] = []
    if result.has_changes:
        lines.append("Готово:")
        lines.extend(f"• Создана коробка {html.escape(box.code)} · {html.escape(box.room)}" for box in result.created)
        lines.extend(f"• Обновлена коробка {html.escape(box.code)} · {html.escape(box.room)}" for box in result.updated)
        lines.extend(f"• Удалена коробка {html.escape(code)}" for code in result.deleted)

    for query, boxes in result.search_results:
        if lines:
            lines.append("")
        if boxes:
            lines.append(f"По запросу «{html.escape(query)}» найдено коробок: {len(boxes)}")
            lines.extend(f"• {html.escape(box.code)} · {html.escape(box.room)}" for box in boxes[:5])
        else:
            lines.append(f"По запросу «{html.escape(query)}» ничего не найдено.")

    return "\n".join(lines)


async def apply_ai_commands(
    config: Config,
    household_id: int,
    commands: list[AiCommand],
) -> AiApplyResult:
    boxes = await database.list_boxes(config.database_path, household_id=household_id, limit=100)
    boxes_by_id = {box.id: box for box in boxes}
    boxes_by_code = {box.code.casefold(): box for box in boxes}
    created: list[Box] = []
    updated: list[Box] = []
    deleted: list[str] = []
    search_results: list[tuple[str, list[Box]]] = []

    for command in commands:
        if command.action == "create_box":
            if not command.room or not command.items:
                continue
            box = await database.create_box(
                config.database_path,
                household_id=household_id,
                prefix=_room_prefix(command.room),
                room=command.room,
                items=command.items,
                photo_file_ids=[],
            )
            created.append(box)
            boxes_by_id[box.id] = box
            boxes_by_code[box.code.casefold()] = box
            continue

        if command.action == "search":
            if command.query:
                found = await database.search_boxes(config.database_path, command.query, household_id)
                search_results.append((command.query, found))
            continue

        box = _find_box(command, boxes_by_id, boxes_by_code)
        if box is None:
            continue

        if command.action == "add_items":
            if not command.items:
                continue
            if await database.add_items(config.database_path, box.id, household_id, command.items):
                refreshed = await database.get_box_by_id(config.database_path, box.id, household_id)
                if refreshed is not None:
                    updated.append(refreshed)
                    boxes_by_id[refreshed.id] = refreshed
                    boxes_by_code[refreshed.code.casefold()] = refreshed
        elif command.action == "update_status":
            if command.status not in STATUSES:
                continue
            if await database.update_status(config.database_path, box.id, household_id, command.status):
                refreshed = await database.get_box_by_id(config.database_path, box.id, household_id)
                if refreshed is not None:
                    updated.append(refreshed)
                    boxes_by_id[refreshed.id] = refreshed
                    boxes_by_code[refreshed.code.casefold()] = refreshed
        elif command.action == "delete_box":
            if await database.delete_box(config.database_path, box.id, household_id):
                deleted.append(box.code)
                boxes_by_id.pop(box.id, None)
                boxes_by_code.pop(box.code.casefold(), None)

    return AiApplyResult(
        created=created,
        updated=updated,
        deleted=deleted,
        search_results=search_results,
    )


async def send_ai_result_cards(
    message: Message,
    result: AiApplyResult,
    bot_username: str,
) -> None:
    from ..handlers.boxes import box_code_html, send_box_card

    for box in result.created:
        await send_box_card(message, box)
        await message.answer_photo(
            photo=make_qr_file(bot_username, box.code),
            caption=f"QR-код для коробки {box_code_html(box.code)}.",
            parse_mode="HTML",
        )
    for box in result.updated:
        await send_box_card(message, box)
    for _, boxes in result.search_results:
        for box in boxes[:5]:
            await send_box_card(message, box)


def _command_from_dict(value: dict[str, Any]) -> AiCommand | None:
    action = value.get("action")
    if action not in ("create_box", "add_items", "update_status", "delete_box", "search"):
        return None

    status = _optional_string(value.get("status"))
    if status is not None and status not in STATUSES:
        status = None

    return AiCommand(
        action=action,
        box_id=value.get("box_id") if isinstance(value.get("box_id"), int) else None,
        box_code=_optional_string(value.get("box_code")),
        room=_optional_string(value.get("room")),
        status=status,
        items=_items_from_value(value.get("items")),
        query=_optional_string(value.get("query")),
    )


def _format_command(command: AiCommand, box_labels: dict[int, str]) -> str:
    box = _box_label(command, box_labels)
    if command.action == "create_box":
        items = ", ".join(command.items or [])
        return f"создать коробку «{html.escape(command.room or '')}»: {html.escape(items)}"
    if command.action == "add_items":
        items = ", ".join(command.items or [])
        return f"добавить в {html.escape(box)}: {html.escape(items)}"
    if command.action == "update_status":
        status = STATUS_LABELS.get(command.status or "", command.status or "")
        return f"сменить статус {html.escape(box)} на {html.escape(status)}"
    if command.action == "delete_box":
        return f"удалить {html.escape(box)}"
    if command.action == "search":
        return f"найти: {html.escape(command.query or '')}"
    return "неизвестное действие"


def _box_label(command: AiCommand, box_labels: dict[int, str]) -> str:
    if command.box_id is not None and command.box_id in box_labels:
        return box_labels[command.box_id]
    if command.box_code:
        return command.box_code
    if command.box_id is not None:
        return f"#{command.box_id}"
    return "коробку"


def _find_box(
    command: AiCommand,
    boxes_by_id: dict[int, Box],
    boxes_by_code: dict[str, Box],
) -> Box | None:
    if command.box_id is not None:
        box = boxes_by_id.get(command.box_id)
        if box is not None:
            return box
    if command.box_code:
        return boxes_by_code.get(command.box_code.casefold())
    return None


def _items_from_value(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    items = [_optional_string(item) for item in value]
    clean_items = [item for item in items if item]
    return clean_items or None


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.lower() == "null":
        return None
    return value


def _room_prefix(room: str) -> str:
    from ..handlers.boxes import room_prefix

    return room_prefix(room)
