"""Small, testable helpers for payment proofs, Minecraft metadata, and reports."""

from __future__ import annotations

from io import BytesIO
import re
from urllib.parse import urlparse

import requests


RAZORPAY_CURRENCY = "INR"
MAX_PAYMENT_PROOF_BYTES = 5 * 1024 * 1024
MAX_PAYMENT_PROOF_PIXELS = 40_000_000
MOJANG_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def razorpay_event_is_captured(
    event: str,
    entity: dict,
    expected_amount: int,
    expected_order_id: str,
    expected_currency: str = RAZORPAY_CURRENCY,
) -> bool:
    """Accept only a captured payment tied to the exact order and amount."""
    if event != "payment.captured":
        return False
    if not isinstance(entity, dict):
        return False
    if entity.get("status") != "captured" or entity.get("captured") is not True:
        return False
    if str(entity.get("order_id") or "") != str(expected_order_id):
        return False
    if str(entity.get("currency") or "").upper() != expected_currency.upper():
        return False
    try:
        return int(entity.get("amount", -1)) == int(expected_amount)
    except (TypeError, ValueError):
        return False


def fetch_latest_minecraft_server(timeout: int = 15) -> dict:
    """Fetch the latest Mojang release metadata, validating its download source."""
    manifest_response = requests.get(MOJANG_MANIFEST_URL, timeout=timeout)
    manifest_response.raise_for_status()
    manifest = manifest_response.json()
    release_id = (manifest.get("latest") or {}).get("release")
    versions = manifest.get("versions")
    if not release_id or not isinstance(versions, list):
        raise ValueError("Mojang returned an incomplete version manifest")

    version = next(
        (
            item for item in versions
            if item.get("id") == release_id and item.get("type") == "release"
        ),
        None,
    )
    if not version:
        raise ValueError("Latest stable Minecraft release was not in the manifest")

    version_url = version.get("url")
    parsed_version_url = urlparse(version_url or "")
    if (
        parsed_version_url.scheme != "https"
        or parsed_version_url.hostname != "piston-meta.mojang.com"
    ):
        raise ValueError("Mojang version metadata used an unexpected URL")

    detail_response = requests.get(version_url, timeout=timeout)
    detail_response.raise_for_status()
    details = detail_response.json()
    server = (details.get("downloads") or {}).get("server") or {}
    download_url = server.get("url")
    parsed_download_url = urlparse(download_url or "")
    if (
        parsed_download_url.scheme != "https"
        or parsed_download_url.hostname != "piston-data.mojang.com"
    ):
        raise ValueError("Mojang server download used an unexpected URL")

    sha1 = str(server.get("sha1") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", sha1):
        raise ValueError("Mojang did not provide a valid server JAR checksum")

    try:
        java_major = int((details.get("javaVersion") or {}).get("majorVersion"))
    except (TypeError, ValueError):
        raise ValueError("Mojang did not provide a valid Java runtime version")
    if not 8 <= java_major <= 30:
        raise ValueError("Mojang returned an unsupported Java runtime version")

    return {
        "version": release_id,
        "java_major": java_major,
        "url": download_url,
        "sha1": sha1,
    }


def ocr_payment_proof(data: bytes, timeout: int = 15) -> str:
    """OCR a bounded PNG/JPEG/WebP image; OCR output is never payment authority."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("The attachment is empty or invalid")
    if len(data) > MAX_PAYMENT_PROOF_BYTES:
        raise ValueError("Payment proof images must be 5 MiB or smaller")

    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError("Pillow is not installed") from exc

    try:
        with Image.open(BytesIO(data)) as probe:
            if probe.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Upload a PNG, JPEG, or WebP image")
            if probe.width * probe.height > MAX_PAYMENT_PROOF_PIXELS:
                raise ValueError("Payment proof image dimensions are too large")
            probe.verify()
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("The attachment is not a readable PNG, JPEG, or WebP image") from exc

    try:
        import pytesseract
    except ImportError as exc:
        raise RuntimeError("pytesseract is not installed") from exc
    try:
        return pytesseract.image_to_string(
            image,
            config="--psm 6",
            timeout=timeout,
        )[:5000].strip()
    except Exception as exc:
        if "tesseract is not installed" in str(exc).lower():
            raise RuntimeError("The Tesseract OCR system package is not installed") from exc
        raise RuntimeError(f"OCR could not read the image: {exc}") from exc


def render_vps_users_banner(rows: list[dict], page: int, total_pages: int) -> bytes:
    """Render a credential-free PNG summary of VPS owners for admin reporting."""
    from PIL import Image, ImageDraw, ImageFont

    width = 1100
    row_height = 76
    header_height = 170
    footer_height = 56
    height = header_height + len(rows) * row_height + footer_height
    image = Image.new("RGB", (width, height), "#0b1020")
    draw = ImageDraw.Draw(image)

    def load_font(size: int, bold: bool = False):
        candidates = (
            [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            ]
            if bold
            else [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            ]
        )
        for path in candidates:
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    title_font = load_font(32, bold=True)
    subtitle_font = load_font(16)
    name_font = load_font(20, bold=True)
    detail_font = load_font(14)
    count_font = load_font(17, bold=True)

    draw.rounded_rectangle((24, 24, width - 24, header_height - 18), radius=20, fill="#151d34")
    draw.text((50, 44), "SVM VPS USERS", font=title_font, fill="#f4f7ff")
    draw.text(
        (52, 91),
        f"Owner overview • Page {page}/{max(1, total_pages)} • Passwords and credentials are not included",
        font=subtitle_font,
        fill="#a8b5d1",
    )
    draw.line((50, 132, width - 50, 132), fill="#34415f", width=2)

    for index, row in enumerate(rows):
        top = header_height + index * row_height
        bottom = top + row_height - 8
        draw.rounded_rectangle(
            (28, top, width - 28, bottom),
            radius=14,
            fill="#121a2d" if index % 2 == 0 else "#101728",
        )

        avatar_box = (44, top + 8, 96, top + 60)
        avatar_bytes = row.get("avatar")
        avatar_drawn = False
        if avatar_bytes:
            try:
                with Image.open(BytesIO(avatar_bytes)) as avatar_source:
                    avatar = avatar_source.convert("RGB").resize((52, 52))
                mask = Image.new("L", (52, 52), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, 51, 51), fill=255)
                image.paste(avatar, (avatar_box[0], avatar_box[1]), mask)
                avatar_drawn = True
            except Exception:
                avatar_drawn = False
        if not avatar_drawn:
            draw.ellipse(avatar_box, fill="#30466f")
            initials = (row.get("name") or "?").strip()[:1].upper()
            bbox = draw.textbbox((0, 0), initials, font=count_font)
            draw.text(
                (
                    avatar_box[0] + (52 - (bbox[2] - bbox[0])) / 2,
                    avatar_box[1] + (52 - (bbox[3] - bbox[1])) / 2 - bbox[1],
                ),
                initials,
                font=count_font,
                fill="#ffffff",
            )

        name = str(row.get("name") or "Unknown user")[:44]
        user_id = str(row.get("user_id") or "unknown")
        draw.text((112, top + 10), name, font=name_font, fill="#f0f4ff")
        draw.text((114, top + 40), f"Discord ID: {user_id}", font=detail_font, fill="#92a0bd")

        counts = (
            f"{int(row.get('vps_count', 0))} VPS"
            f"   •   {int(row.get('running', 0))} running"
            f"   •   {int(row.get('stopped', 0))} stopped"
            f"   •   {int(row.get('suspended', 0))} suspended"
        )
        draw.text((530, top + 24), counts, font=detail_font, fill="#c5d2ec")
        draw.text(
            (width - 62, top + 21),
            str(int(row.get("vps_count", 0))),
            font=count_font,
            fill="#68d6c3",
            anchor="ra",
        )

    draw.text(
        (42, height - footer_height + 14),
        "SVM v11.2 • Admin report • VPS owner data only",
        font=detail_font,
        fill="#8090b0",
    )
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()