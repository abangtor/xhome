"""Constants recovered from the XHome Android app."""

from __future__ import annotations

from .models import Region

API_KEY = "599dddd296b3be82bec52a8e09ead75e"
LONG_SALT = (
    "5a66sj247hfe7jxfpbtryaes6pjrzisw8ey7zjy62ki56cizwhctsfzjdny5yhwzc8rx7wt7k7xbcpw8jpzik8775b4f5ajdptib4phd5h3zpnzfrmedmatmhbphh5eiba6tm3535kdpxiidtrkb5h4wbdk57kxxnhxip443hjf2ppe283rscdarrhzesc33md3a84fahansbedrxmnf2b74k2tsx65k4xkhhf6kd6f6abtyp6654peeb4bjpiip"
)
BUNDLE_ID = "com.lancens.wxdoorbell"

REGIONS: dict[str, Region] = {
    "china": Region(
        key="china",
        server_id=0,
        rest_url="https://chniot.lancens.com:6448/",
        push_host="chnpush.lancens.com",
        native_iot_host="chniotd.lancens.com",
    ),
    "usa": Region(
        key="usa",
        server_id=1,
        rest_url="https://usaiot.lancens.com:6448/",
        push_host="usapush.lancens.com",
        native_iot_host="usaiotd.lancens.com",
    ),
    "europe": Region(
        key="europe",
        server_id=2,
        rest_url="https://euriot.lancens.com:6448/",
        push_host="eurpush.lancens.com",
        native_iot_host="euriotd.lancens.com",
    ),
    "test": Region(
        key="test",
        server_id=3,
        rest_url="https://push.lancens.com:6448/",
        push_host="push.lancens.com",
        native_iot_host="push.lancens.com",
    ),
}

REGION_ALIASES = {
    "0": "china",
    "cn": "china",
    "chn": "china",
    "china": "china",
    "default": "china",
    "1": "usa",
    "us": "usa",
    "usa": "usa",
    "2": "europe",
    "eu": "europe",
    "eur": "europe",
    "europe": "europe",
    "3": "test",
    "show": "test",
    "test": "test",
}

ROUTE_BASE_URLS = {
    "developer": "https://developer.lancens.com:8100/",
    "area": "https://iot.lancens.com:6445/",
    "show": "https://show.lancens.com:9448/",
    "weather": "http://push.lancens.com:8080/",
}


def normalize_region(region: str | int | Region) -> Region:
    if isinstance(region, Region):
        return region
    key = REGION_ALIASES.get(str(region).strip().lower())
    if not key:
        choices = ", ".join(sorted(REGION_ALIASES))
        raise ValueError(f"Unknown XHome region {region!r}. Known aliases: {choices}")
    return REGIONS[key]
