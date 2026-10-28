"""Request / response shapes. These mirror the thali the React app builds (src/lib/thali.ts)."""

import re
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

NAME_MAX = 30
NOTE_MAX = 300

# Item and design ids from the frontend, e.g. "mithai", "kaju-katli".
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,30}$")]


class ThaliContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)

    sender: str = Field(alias="from", min_length=1, max_length=NAME_MAX)
    to: str = Field(min_length=1, max_length=NAME_MAX)
    items: list[Slug] = Field(min_length=1, max_length=20)
    styles: dict[Slug, list[Slug]] = Field(default_factory=dict, max_length=20)
    note: str = Field(default="", max_length=NOTE_MAX)

    @field_validator("items")
    @classmethod
    def _unique_items(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    @field_validator("styles")
    @classmethod
    def _limit_styles(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        cleaned = {}
        for item, ids in v.items():
            ids = list(dict.fromkeys(ids))
            if not 1 <= len(ids) <= 12:
                raise ValueError(f"'{item}' must have between 1 and 12 designs")
            cleaned[item] = ids
        return cleaned


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thali: ThaliContent
    phone: str = Field(description="Sender's mobile number — Cashfree requires one on every order")
    email: EmailStr = Field(max_length=254, description="Where the receipt and invoice are sent")
    # Whole rupees. Omit for the minimum shagun; limits are checked against server settings.
    amount: int | None = Field(default=None, ge=1)

    @field_validator("email")
    @classmethod
    def _lower_email(cls, v: str) -> str:
        return v.lower()

    @field_validator("phone")
    @classmethod
    def _indian_mobile(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v)
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if not re.fullmatch(r"[6-9]\d{9}", digits):
            raise ValueError("Enter a valid 10-digit Indian mobile number")
        return digits


class CheckoutResponse(BaseModel):
    order_id: str
    payment_session_id: str
    cashfree_mode: Literal["sandbox", "production"]
    amount: Decimal
    currency: str


OrderId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]{2,12}_[A-Za-z0-9]{8,40}$")]
PublicId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{6,16}$")]


class VerifyRequest(BaseModel):
    order_id: OrderId


class VerifyResponse(BaseModel):
    status: Literal["paid", "pending", "failed"]
    thali_id: str | None = None
    share_path: str | None = None


class PublicThali(BaseModel):
    id: str
    thali: ThaliContent


class PublicConfig(BaseModel):
    amount: Decimal  # the minimum / default shagun
    min_amount: int
    max_amount: int
    preset_amounts: list[int]
    currency: str
    cashfree_mode: Literal["sandbox", "production"]
    payments_enabled: bool
    receipts_enabled: bool
