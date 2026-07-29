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
JPEG_EXIF_PREFIX = b"Exif\x00\x00"
JPEG_ORIENTATION_BY_ROTATION = {0: 1, 90: 6, 180: 3, 270: 8}
JPEG_SOI = b"\xff\xd8"
LIVE_ROTATION_EDGE_CROP_PIXELS = 32


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
    edge_crop_pixels: int = LIVE_ROTATION_EDGE_CROP_PIXELS,
) -> bytes | None:
    """Rotate one live JPEG frame after cropping the unstable source edge."""

    try:
        from PIL import Image, ImageOps
    except ImportError:
        LOGGER.warning("Pillow is not installed; dropping rotated XHome live image frame")
        return None

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source)
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
            return output.getvalue()
    except Exception as err:  # noqa: BLE001
        LOGGER.debug("XHome live image rotation failed: %s", err)
        return None


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


def _image_format(image: Any, content_type: str) -> str:
    """Return a Pillow output format for the source image."""

    if image.format:
        return str(image.format)
    if content_type == "image/png":
        return "PNG"
    if content_type == "image/webp":
        return "WEBP"
    return "JPEG"


def set_jpeg_exif_orientation(image_bytes: bytes, rotation: int) -> bytes:
    """Set a JPEG EXIF orientation tag without re-encoding image pixels."""

    orientation = JPEG_ORIENTATION_BY_ROTATION.get(rotation)
    if orientation is None or not image_bytes.startswith(JPEG_SOI):
        return image_bytes
    updated = _replace_jpeg_exif_orientation(image_bytes, orientation)
    if updated is not None:
        return updated
    return _insert_jpeg_exif_orientation(image_bytes, orientation)


def _replace_jpeg_exif_orientation(image_bytes: bytes, orientation: int) -> bytes | None:
    """Return JPEG bytes with an updated EXIF orientation tag, if present."""

    offset = 2
    while offset + 4 <= len(image_bytes):
        if image_bytes[offset] != 0xFF:
            return None
        marker = image_bytes[offset + 1]
        if marker == 0xDA:
            return None
        length = int.from_bytes(image_bytes[offset + 2 : offset + 4], "big", signed=False)
        segment_start = offset + 4
        segment_end = offset + 2 + length
        if segment_end > len(image_bytes):
            return None
        if marker == 0xE1 and image_bytes[segment_start : segment_start + len(JPEG_EXIF_PREFIX)] == JPEG_EXIF_PREFIX:
            return _replace_tiff_orientation(image_bytes, segment_start + len(JPEG_EXIF_PREFIX), segment_end, orientation)
        offset = segment_end
    return None


def _replace_tiff_orientation(
    image_bytes: bytes,
    tiff_start: int,
    segment_end: int,
    orientation: int,
) -> bytes | None:
    """Return JPEG bytes with the TIFF IFD0 orientation value replaced."""

    if tiff_start + 8 > segment_end:
        return None
    endian_tag = image_bytes[tiff_start : tiff_start + 2]
    if endian_tag == b"II":
        byteorder = "little"
    elif endian_tag == b"MM":
        byteorder = "big"
    else:
        return None
    ifd_offset = int.from_bytes(image_bytes[tiff_start + 4 : tiff_start + 8], byteorder, signed=False)
    ifd_start = tiff_start + ifd_offset
    if ifd_start + 2 > segment_end:
        return None
    entry_count = int.from_bytes(image_bytes[ifd_start : ifd_start + 2], byteorder, signed=False)
    entries_start = ifd_start + 2
    for index in range(entry_count):
        entry_start = entries_start + (index * 12)
        entry_end = entry_start + 12
        if entry_end > segment_end:
            return None
        tag = int.from_bytes(image_bytes[entry_start : entry_start + 2], byteorder, signed=False)
        field_type = int.from_bytes(image_bytes[entry_start + 2 : entry_start + 4], byteorder, signed=False)
        count = int.from_bytes(image_bytes[entry_start + 4 : entry_start + 8], byteorder, signed=False)
        if tag == 0x0112 and field_type == 3 and count == 1:
            updated = bytearray(image_bytes)
            updated[entry_start + 8 : entry_start + 10] = orientation.to_bytes(2, byteorder, signed=False)
            return bytes(updated)
    return None


def _insert_jpeg_exif_orientation(image_bytes: bytes, orientation: int) -> bytes:
    """Insert a minimal EXIF orientation segment after the JPEG SOI marker."""

    tiff = (
        b"MM\x00\x2a"
        + (8).to_bytes(4, "big")
        + (1).to_bytes(2, "big")
        + (0x0112).to_bytes(2, "big")
        + (3).to_bytes(2, "big")
        + (1).to_bytes(4, "big")
        + orientation.to_bytes(2, "big")
        + b"\x00\x00"
        + (0).to_bytes(4, "big")
    )
    payload = JPEG_EXIF_PREFIX + tiff
    segment = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    return image_bytes[:2] + segment + image_bytes[2:]


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
