from io import BytesIO

import qrcode
from aiogram.types import BufferedInputFile


def make_box_deeplink(bot_username: str, box_code: str) -> str:
    return f"https://t.me/{bot_username}?start=box_{box_code}"


def make_qr_file(bot_username: str, box_code: str) -> BufferedInputFile:
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(make_box_deeplink(bot_username, box_code))
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return BufferedInputFile(buffer.getvalue(), filename=f"{box_code}.png")
