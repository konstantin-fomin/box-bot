from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .database import Box


FONT_REGULAR = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"
FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
FONT_BOLD_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
PDF_STATUS_LABELS = {
    "with_me": "с собой",
    "store": "хранить",
    "send_if_needed": "прислать по необходимости",
}


def _register_fonts() -> None:
    if FONT_REGULAR not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT_REGULAR, FONT_PATH))
    if FONT_BOLD not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT_BOLD, FONT_BOLD_PATH))


def _box_tag(box: Box) -> str:
    return PDF_STATUS_LABELS.get(box.status, box.status)


def _items_text(box: Box) -> str:
    if not box.items:
        return "пока не указаны"
    return "<br/>".join(f"• {html.escape(item)}" for item in box.items)


def generate_boxes_pdf(boxes: list[Box], output_path: Path, *, generated_at: datetime | None = None) -> None:
    _register_fonts()
    generated_at = generated_at or datetime.now()

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="BoxTitle",
            parent=styles["Heading2"],
            fontName=FONT_BOLD,
            fontSize=12,
            leading=15,
            spaceBefore=8,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="DocTitle",
            parent=styles["Title"],
            fontName=FONT_BOLD,
            fontSize=18,
            leading=22,
            alignment=TA_CENTER,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodyCyr",
            parent=styles["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=9,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodyCyrBold",
            parent=styles["BodyText"],
            fontName=FONT_BOLD,
            fontSize=9,
            leading=12,
        )
    )

    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="Экспорт коробок",
    )

    story = [
        Paragraph("Экспорт коробок", styles["DocTitle"]),
        Paragraph(f"Дата генерации: {generated_at:%Y-%m-%d %H:%M}", styles["BodyCyr"]),
        Spacer(1, 6 * mm),
    ]

    current_room: str | None = None
    for box in boxes:
        if box.room != current_room:
            current_room = box.room
            story.append(Paragraph(f"Комната: {html.escape(current_room)}", styles["BoxTitle"]))

        rows = [
            [
                Paragraph("Код", styles["BodyCyrBold"]),
                Paragraph(html.escape(box.code), styles["BodyCyr"]),
                Paragraph("Статус", styles["BodyCyrBold"]),
                Paragraph(html.escape(PDF_STATUS_LABELS.get(box.status, box.status)), styles["BodyCyr"]),
            ],
            [
                Paragraph("Метка", styles["BodyCyrBold"]),
                Paragraph(html.escape(_box_tag(box)), styles["BodyCyr"]),
                Paragraph("Вещи", styles["BodyCyrBold"]),
                Paragraph(_items_text(box), styles["BodyCyr"]),
            ],
        ]
        table = Table(rows, colWidths=[20 * mm, 32 * mm, 22 * mm, 92 * mm], hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.4, colors.lightgrey),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 4 * mm))

    if not boxes:
        story.append(Paragraph("Коробок пока нет.", styles["BodyCyr"]))

    document.build(story)
