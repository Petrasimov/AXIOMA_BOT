"""
qr.py — Генерация QR-кода для адреса оплаты

Кошельки читают простой адрес строкой — обёртки вида
"ethereum:0x..." поддерживаются не всеми, поэтому кодируем
адрес как есть.

Генерация синхронная и упирается в CPU, поэтому уносим её
в отдельный поток: иначе она подвесит весь event loop,
включая long-polling и рассылку уведомлений.
"""

import asyncio
import io
import logging

logger = logging.getLogger(__name__)


def _render(data: str) -> bytes:
    """Рисует QR-код и отдаёт PNG байтами."""
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=3,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color='black', back_color='white')

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


async def make_qr(data: str) -> bytes | None:
    """Асинхронная обёртка. None если что-то пошло не так —
    оплата должна работать и без картинки.
    """
    try:
        return await asyncio.to_thread(_render, data)
    except Exception as e:
        logger.error(f'[QR] Не удалось сгенерировать QR-код: {e}')
        return None