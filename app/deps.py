from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_session
from app.services.cashfree import CashfreeClient


def get_cashfree(request: Request) -> CashfreeClient:
    return request.app.state.cashfree


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CashfreeDep = Annotated[CashfreeClient, Depends(get_cashfree)]
