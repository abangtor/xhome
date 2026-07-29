"""Image platform for the XHome Home Assistant integration."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_IMAGE_ROTATION, DEFAULT_IMAGE_ROTATION, DOMAIN, IMAGE_ROTATIONS
from .coordinator import XHomeDataUpdateCoordinator, XHomeLatestEventMedia
from .entity import XHomeEntity

LOGGER = logging.getLogger(__name__)
JPEG_SOI = b"\xff\xd8"
LIVE_ROTATION_EDGE_CROP_MAX_PIXELS = 32
LIVE_ROTATION_EDGE_CROP_STEP_PIXELS = 8


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XHome image entities."""

    coordinator: XHomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(XHomeLatestEventImage(coordinator, uid) for uid in coordinator.data.devices)


class XHomeLatestEventImage(XHomeEntity, ImageEntity):
    """Latest event image for an XHome device."""

    _attr_should_poll = False
    _attr_translation_key = "latest_event_image"

    def __init__(self, coordinator: XHomeDataUpdateCoordinator, uid: str) -> None:
        """Initialize the latest event image entity."""

        super().__init__(coordinator, uid, "latest_event_image")
        ImageEntity.__init__(self, coordinator.hass)

    @property
    def available(self) -> bool:
        """Return whether a latest event image is available."""

        media = self._media
        return super().available and media is not None and (
            media.content_type is None or media.content_type.startswith("image/")
        )

    @property
    def content_type(self) -> str:
        """Return the latest image content type."""

        media = self._media
        return media.content_type if media and media.content_type else "image/jpeg"

    @property
    def image_last_updated(self) -> datetime | None:
        """Return when the latest image event was captured."""

        media = self._media
        if media is None or media.time_stamp is None:
            return None
        timestamp = media.time_stamp / 1000 if media.time_stamp > 10_000_000_000 else media.time_stamp
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return non-sensitive latest event image metadata."""

        attrs = super().extra_state_attributes
        media = self._media
        if media is None:
            return attrs

        attrs.update(
            {
                "event_key": media.event_key,
                "event_guid": media.event_guid,
                "event_id": media.event_id,
                "event_type": media.event_type,
                "event_time": media.time,
                "event_time_stamp": media.time_stamp,
                "file_name": media.file_name,
                "expires_at": media.exp_time,
                "video_status": media.video_status,
                "video_size": media.video_size,
            }
        )
        downloaded = self.coordinator.downloaded_event_media(self.uid)
        if downloaded is not None and downloaded.event_key == media.event_key:
            attrs.update(
                {
                    "saved_image_path": downloaded.image_path,
                    "saved_at": downloaded.saved_at,
                }
            )
        return {key: value for key, value in attrs.items() if value is not None}

    async def async_image(self) -> bytes | None:
        """Return latest event image bytes."""

        image_bytes = await self.coordinator.async_get_latest_event_image(self.uid)
        if image_bytes is None:
            return None
        return rotate_image_bytes(
            image_bytes,
            image_rotation_degrees(self.coordinator.config_entry.options),
            self.content_type,
        )

    @property
    def _media(self) -> XHomeLatestEventMedia | None:
        """Return cached media for this entity."""

        return self.coordinator.latest_event_media(self.uid)


def image_rotation_degrees(options: dict[str, Any]) -> int:
    """Return a normalized latest-event image rotation value."""

    try:
        rotation = int(options.get(CONF_IMAGE_ROTATION, DEFAULT_IMAGE_ROTATION))
    except (TypeError, ValueError):
        return DEFAULT_IMAGE_ROTATION
    return rotation if rotation in IMAGE_ROTATIONS else DEFAULT_IMAGE_ROTATION


def rotate_image_bytes(image_bytes: bytes, rotation: int, content_type: str) -> bytes:
    """Rotate image bytes clockwise when configured."""

    if rotation == 0:
        return image_bytes

    try:
        from PIL import Image, ImageOps
    except ImportError:
        LOGGER.warning("Pillow is not installed; returning unrotated XHome latest event image")
        return image_bytes

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source)
            if rotation == 90:
                image = image.transpose(Image.Transpose.ROTATE_270)
            elif rotation == 180:
                image = image.transpose(Image.Transpose.ROTATE_180)
            elif rotation == 270:
                image = image.transpose(Image.Transpose.ROTATE_90)
            else:
                return image_bytes

            output = BytesIO()
            image.save(output, format=_image_format(source, content_type))
            return output.getvalue()
    except Exception as err:  # noqa: BLE001
        LOGGER.debug("XHome latest event image rotation failed: %s", err)
        return image_bytes


def rotate_live_image_bytes(
    image_bytes: bytes,
    rotation: int,
    content_type: str,
    *,
    edge_crop_pixels: int | None = None,
) -> bytes | None:
    """Rotate one live JPEG frame after cropping the unstable source edge."""

    rotated, _crop_pixels = prepare_live_image_bytes(
        image_bytes,
        rotation,
        content_type,
        edge_crop_pixels=edge_crop_pixels,
    )
    return rotated


def prepare_live_image_bytes(
    image_bytes: bytes,
    rotation: int,
    content_type: str,
    *,
    edge_crop_pixels: int | None = None,
    minimum_edge_crop_pixels: int = 0,
) -> tuple[bytes | None, int]:
    """Rotate one live JPEG frame and return the auto-detected edge crop."""

    try:
        from PIL import Image, ImageOps
    except ImportError:
        LOGGER.warning("Pillow is not installed; dropping rotated XHome live image frame")
        return None, minimum_edge_crop_pixels

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source)
            if edge_crop_pixels is None:
                edge_crop_pixels = detect_live_rotation_edge_crop(image, rotation)
            edge_crop_pixels = max(minimum_edge_crop_pixels, edge_crop_pixels)
            image = _crop_live_rotation_edge(image, rotation, edge_crop_pixels)
            if rotation == 90:
                image = image.transpose(Image.Transpose.ROTATE_270)
            elif rotation == 180:
                image = image.transpose(Image.Transpose.ROTATE_180)
            elif rotation == 270:
                image = image.transpose(Image.Transpose.ROTATE_90)
            elif rotation != 0:
                return None

            output = BytesIO()
            image.save(output, format=_image_format(source, content_type))
            return output.getvalue(), edge_crop_pixels
    except Exception as err:  # noqa: BLE001
        LOGGER.debug("XHome live image rotation failed: %s", err)
        return None, minimum_edge_crop_pixels


def detect_live_rotation_edge_crop(
    image: Any,
    rotation: int,
    *,
    max_crop_pixels: int = LIVE_ROTATION_EDGE_CROP_MAX_PIXELS,
) -> int:
    """Return an automatic crop for raw edge padding that becomes a side stripe."""

    if rotation not in {90, 270} or max_crop_pixels <= 0:
        return 0
    width, height = image.size
    if height <= LIVE_ROTATION_EDGE_CROP_STEP_PIXELS:
        return 0

    edge = "bottom" if rotation == 90 else "top"
    gray = image.convert("L")
    if width > 160:
        gray = gray.resize((160, height))
    reference_mean, _reference_std = _edge_reference_stats(gray, edge, max_crop_pixels)
    crop_pixels = _detect_unstable_edge_pixels(gray, edge, reference_mean, max_crop_pixels)
    return _round_crop_pixels(crop_pixels, max_crop_pixels)


def _crop_live_rotation_edge(image: Any, rotation: int, edge_crop_pixels: int) -> Any:
    """Crop raw live-frame padding that becomes a side stripe after rotation."""

    if edge_crop_pixels <= 0:
        return image
    width, height = image.size
    if rotation == 90 and height > edge_crop_pixels:
        return image.crop((0, 0, width, height - edge_crop_pixels))
    if rotation == 270 and height > edge_crop_pixels:
        return image.crop((0, edge_crop_pixels, width, height))
    return image


def _detect_unstable_edge_pixels(image: Any, edge: str, reference_mean: float, max_crop_pixels: int) -> int:
    """Detect rows near an edge that differ strongly from the interior image."""

    height = image.height
    crop_pixels = 0
    good_run = 0
    for offset in range(1, min(max_crop_pixels, height - 1) + 1):
        y = height - offset if edge == "bottom" else offset - 1
        mean, stddev = _row_stats(image, y)
        unstable = abs(mean - reference_mean) > 24 or (stddev < 8 and abs(mean - reference_mean) > 10)
        if unstable:
            crop_pixels = offset
            good_run = 0
        else:
            good_run += 1
            if good_run >= 4:
                break
    return crop_pixels


def _edge_reference_stats(image: Any, edge: str, max_crop_pixels: int) -> tuple[float, float]:
    """Return mean/stddev from rows safely inside the unstable edge scan area."""

    rows: list[tuple[float, float]] = []
    height = image.height
    for offset in range(max_crop_pixels + 8, min(max_crop_pixels + 32, height - 1)):
        y = height - offset if edge == "bottom" else offset - 1
        if 0 <= y < height:
            rows.append(_row_stats(image, y))
    if not rows:
        return _row_stats(image, height // 2)
    means = sorted(row[0] for row in rows)
    stddevs = sorted(row[1] for row in rows)
    middle = len(rows) // 2
    return means[middle], stddevs[middle]


def _row_stats(image: Any, y: int) -> tuple[float, float]:
    """Return grayscale mean and standard deviation for one row."""

    row = image.crop((0, y, image.width, y + 1)).tobytes()
    mean = sum(row) / len(row)
    variance = sum((value - mean) ** 2 for value in row) / len(row)
    return mean, variance**0.5


def _round_crop_pixels(crop_pixels: int, max_crop_pixels: int) -> int:
    """Round crop pixels to an MCU-friendly step."""

    rounded = ((crop_pixels + LIVE_ROTATION_EDGE_CROP_STEP_PIXELS - 1) // LIVE_ROTATION_EDGE_CROP_STEP_PIXELS)
    return min(max_crop_pixels, rounded * LIVE_ROTATION_EDGE_CROP_STEP_PIXELS)


def _image_format(image: Any, content_type: str) -> str:
    """Return a Pillow output format for the source image."""

    if image.format:
        return str(image.format)
    if content_type == "image/png":
        return "PNG"
    if content_type == "image/webp":
        return "WEBP"
    return "JPEG"


def is_decodable_jpeg(image_bytes: bytes) -> bool:
    """Return whether Pillow can fully decode a JPEG without recovery mode."""

    if not image_bytes.startswith(JPEG_SOI):
        return False
    try:
        from PIL import Image
    except ImportError:
        return True

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
    except Exception:  # noqa: BLE001
        return False
    return True
