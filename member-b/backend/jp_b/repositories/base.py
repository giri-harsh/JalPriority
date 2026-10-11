from typing import Optional, List, Dict, Any
from abc import ABC, abstractmethod

class PlansRepository(ABC):
    @abstractmethod
    def save(self, plan: dict) -> None: pass
    
    @abstractmethod
    def get(self, plan_id: str) -> Optional[dict]: pass
    
    @abstractmethod
    def list_by_scenario(self, scenario_id: Optional[str] = None, status: Optional[str] = None, latest: bool = False) -> List[dict]: pass

class LedgerRepository(ABC):
    @abstractmethod
    def save(self, service_area_id: str, area_id: str, window_index: int, data: dict) -> None: pass
    
    @abstractmethod
    def get_all_for_service_area(self, service_area_id: str) -> List[dict]: pass

    @abstractmethod
    def replace_window(self, service_area_id: str, window_index: int, rows: List[dict]) -> None: pass

class EventsRepository(ABC):
    @abstractmethod
    def save(self, event: dict) -> None: pass
    
    @abstractmethod
    def list_events(self, scenario_id: Optional[str] = None, name: Optional[str] = None, limit: Optional[int] = None) -> List[dict]: pass

class AuditRepository(ABC):
    @abstractmethod
    def save(self, entry: dict) -> None: pass
    
    @abstractmethod
    def list_entries(self, plan_id: Optional[str] = None, limit: int = 50) -> List[dict]: pass

class WeightsRepository(ABC):
    @abstractmethod
    def save(self, service_area_id: str, data: dict) -> None: pass
    
    @abstractmethod
    def get(self, service_area_id: str) -> Optional[dict]: pass
