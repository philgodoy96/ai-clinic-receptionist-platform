from sqlalchemy.orm import DeclarativeBase
from app.models import scheduling as scheduling_models  # noqa: E402,F401


class Base(DeclarativeBase):
    pass