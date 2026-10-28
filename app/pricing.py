"""
Item price list — the single source of truth for what each thali costs.

Keys are "item" for plain items and "item:design" for items with several designs.
The frontend shows these prices (via /api/config) but the bill is always computed here.
Anything not listed costs nothing; the defaults the recipient sees for missing designs are all free.
"""

from dataclasses import dataclass
from typing import Any

FREE = 0
TEN = 1000  # paise

# key: (display name, price in paise)
CATALOG: dict[str, tuple[str, int]] = {
    # Plain items
    "dry-fruits": ("Dry Fruits", TEN),
    # Diya — all free
    "diya:mitti": ("Mitti Diya", FREE),
    "diya:golden": ("Golden Deepak", FREE),
    "diya:rangoli": ("Rangoli Diya", FREE),
    # Mithai
    "mithai:laddoo": ("Besan Laddoo", FREE),
    "mithai:soan-papdi": ("Soan Papdi", FREE),
    "mithai:jalebi": ("Jalebi", TEN),
    "mithai:kaju-katli": ("Kaju Katli", TEN),
    "mithai:gulab-jamun": ("Gulab Jamun", TEN),
    "mithai:rasgulla": ("Rasgulla", TEN),
    # Patakhe
    "patakhe:phuljhadi": ("Phuljhadi", FREE),
    "patakhe:pencil": ("Colour Pencil", FREE),
    "patakhe:anaar": ("Anaar", TEN),
    "patakhe:chakri": ("Chakri", TEN),
    "patakhe:rocket": ("Rocket", TEN),
    "patakhe:sutli-bomb": ("Sutli Bomb", TEN),
    # Phool
    "phool:genda": ("Genda", FREE),
    "phool:gulab": ("Gulab", FREE),
    "phool:kamal": ("Kamal", TEN),
    "phool:mogra": ("Mogra", TEN),
    "phool:mala": ("Genda Mala", TEN),
    "phool:pankhudi": ("Gulab Pankhudi", TEN),
    # Chocolate
    "chocolate:milk": ("Milk Chocolate", FREE),
    "chocolate:dark": ("Dark Chocolate", FREE),
    "chocolate:box": ("Gift Box", TEN),
    "chocolate:truffles": ("Truffles", TEN),
    "chocolate:coins": ("Chocolate Coins", TEN),
    # Sajavat
    "sajavat:kandil": ("Akash Kandil", FREE),
    "sajavat:toran": ("Toran", FREE),
    "sajavat:rangoli": ("Rangoli", TEN),
    "sajavat:lights": ("Fairy Lights", TEN),
    "sajavat:candles": ("Candles", TEN),
    "sajavat:charan": ("Lakshmi Charan", TEN),
    # Pooja samagri — all free
    "pooja:moli": ("Moli", FREE),
    "pooja:roli": ("Roli Tika", FREE),
    "pooja:akshat": ("Akshat", FREE),
    "pooja:haldi-kumkum": ("Haldi Kumkum", FREE),
    "pooja:agarbatti": ("Agarbatti", FREE),
    "pooja:kapoor": ("Kapoor", FREE),
    "pooja:kalash": ("Kalash", FREE),
    "pooja:ghanti": ("Ghanti", FREE),
}


@dataclass(frozen=True)
class BillLine:
    key: str
    name: str
    price_paise: int

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "name": self.name, "price_paise": self.price_paise}


def bill_lines(content: dict[str, Any]) -> list[BillLine]:
    """One line per chosen item/design on the thali, in the order the sender added them."""
    lines: list[BillLine] = []
    styles: dict[str, list[str]] = content.get("styles") or {}
    for item in content.get("items") or []:
        designs = styles.get(item)
        keys = [f"{item}:{d}" for d in designs] if designs else [item]
        for key in keys:
            name, price = CATALOG.get(key, (key.split(":")[-1].replace("-", " ").title(), FREE))
            lines.append(BillLine(key, name, price))
    return lines


def public_prices() -> dict[str, int]:
    """Prices in whole rupees for the frontend."""
    return {key: price // 100 for key, (_, price) in CATALOG.items()}
