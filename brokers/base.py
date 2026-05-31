"""Broker adapter contract for execution providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BrokerAdapter(ABC):
    """Stable trading interface used by strategy and execution code."""

    broker_type = "generic"

    @abstractmethod
    def connect(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_account_info(self) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def place_order(self, **kwargs) -> Any:
        raise NotImplementedError

    @abstractmethod
    def modify_order(self, order_id: int, **kwargs) -> bool:
        raise NotImplementedError

    @abstractmethod
    def close_order(self, order_id: int, **kwargs) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_symbol_info(self, symbol: str) -> Any:
        raise NotImplementedError
