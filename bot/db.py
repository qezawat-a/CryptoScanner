"""Neon Postgres (ya SQLite local) — settings + signal history.
Mesle XT vali minimal: 2 table. DATABASE_URL nabashe -> scanner.db local.
"""

import logging
import os
import re
import time

logger = logging.getLogger("db")

from sqlalchemy import create_engine, Column, Integer, String, Float, Text
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=False)


class Signal(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(Float, nullable=False)
    symbol = Column(String(32), nullable=False)
    direction = Column(String(16), nullable=False)
    confidence = Column(Integer, default=0)
    strength = Column(Float, default=0.0)
    price = Column(Float, default=0.0)
    source = Column(String(32), default="")
    verdict = Column(Text, default="")
    rejected = Column(Integer, default=0)


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return "sqlite:///scanner.db"
    if url.startswith("mysql://"):
        url = url.replace("mysql://", "mysql+pymysql://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://") and "+psycopg2" not in url and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    if "+psycopg2://" in url and "channel_binding=" in url:
        url = re.sub(r"[?&]channel_binding=[^&]*", "", url).rstrip("?&")
        url = url.replace("?&", "?")
    return url


class DB:
    def __init__(self, url: str = None):
        raw = url if url is not None else os.getenv("DATABASE_URL", "")
        self.url = _normalize_url(raw)
        self.is_sqlite = self.url.startswith("sqlite")
        kw = {"pool_pre_ping": True} if not self.is_sqlite else {}
        self._engine = create_engine(self.url, **kw)
        Base.metadata.create_all(self._engine)
        self._mk = sessionmaker(bind=self._engine)
        kind = "SQLite-local" if self.is_sqlite else "Neon-Postgres"
        logger.info(f"DB ready: {kind}")

    # ---------- settings ----------

    def all_settings(self) -> dict:
        s = self._mk()
        try:
            return {r.key: r.value for r in s.query(Setting).all()}
        finally:
            s.close()

    def get_setting(self, key: str, default=None):
        s = self._mk()
        try:
            r = s.query(Setting).filter(Setting.key == key).first()
            return r.value if r else default
        finally:
            s.close()

    def set_setting(self, key: str, value) -> None:
        s = self._mk()
        try:
            r = s.query(Setting).filter(Setting.key == key).first()
            if r:
                r.value = str(value)
            else:
                s.add(Setting(key=key, value=str(value)))
            s.commit()
        finally:
            s.close()

    def has_any_setting(self) -> bool:
        s = self._mk()
        try:
            return s.query(Setting).count() > 0
        finally:
            s.close()

    # ---------- signals ----------

    def log_signal(self, symbol: str, direction: str, confidence: int = 0,
                   strength: float = 0.0, price: float = 0.0,
                   source: str = "", verdict: str = "", rejected: bool = False) -> None:
        s = self._mk()
        try:
            s.add(Signal(ts=time.time(), symbol=symbol, direction=direction,
                         confidence=int(confidence or 0), strength=float(strength or 0),
                         price=float(price or 0), source=source or "",
                         verdict=(verdict or "")[:500], rejected=1 if rejected else 0))
            s.commit()
        finally:
            s.close()

    def recent_signals(self, limit: int = 8) -> list:
        s = self._mk()
        try:
            rows = s.query(Signal).order_by(Signal.id.desc()).limit(limit).all()
            return [{"id": r.id, "ts": r.ts, "symbol": r.symbol, "direction": r.direction,
                     "confidence": r.confidence, "strength": r.strength, "price": r.price,
                     "source": r.source, "rejected": bool(r.rejected)} for r in rows]
        finally:
            s.close()
