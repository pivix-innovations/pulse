from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine
)
from contextlib import asynccontextmanager
from config.database import DatabaseSettings
from typing import AsyncIterator
from sqlalchemy import text

class Database:
    """Manages the application's database engine and sessions."""
    
    def __init__(self, settings: DatabaseSettings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
    
    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Database has not been initialized")
        return self._engine
    
    async def initialize(self) -> None:
        """Create the engine and session factory"""
        if self._engine is not None:
            return
        self._engine = create_async_engine(
            self._settings.url,
            echo=self._settings.echo,
            pool_size=self._settings.pool_size,
            max_overflow=self._settings.max_overflow,
            pool_timeout=self._settings.pool_timeout,
            pool_recycle=self._settings.pool_recycle,
            pool_pre_ping=self._settings.pool_pre_ping
        )
        
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False
        )
        
    async def shutdown(self) -> None:
        if self._engine is None:
            return
        await self._engine.dispose()
        self._engine = None
        self._session_factory = None
    
    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Provide a transactional session"""
        if self._session_factory is None:
            raise RuntimeError("Database is not initialized")
        
        async with self._session_factory() as session:
            yield session
    
    async def health_check(self) -> bool:
        """verify database connectivity"""
        try:
            async with self.session() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False