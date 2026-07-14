from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence, Tuple

class MosqueRepository(ABC):
    @abstractmethod
    def save_mosques(self, dataset_id: str, mosques: List[Dict[str, Any]]) -> None:
        pass

    @abstractmethod
    def get_mosques(self, dataset_id: str, limit: int = 1000, offset: int = 0, kabko: Optional[str] = None) -> List[Dict[str, Any]]:
        pass
        
    @abstractmethod
    def count_mosques(self, dataset_id: str, kabko: Optional[str] = None) -> int:
        pass

    @abstractmethod
    def get_mosque_by_id(self, dataset_id: str, mosque_id: str) -> Optional[Dict[str, Any]]:
        pass
        
    @abstractmethod
    def get_mosques_in_bounds(
        self,
        dataset_id: str,
        bounds: tuple[float, float, float, float],
        limit: int = 600,
        anchors: Optional[Sequence[Tuple[float, float]]] = None,
    ) -> List[Dict[str, Any]]:
        pass
        
    @abstractmethod
    def get_nearest_mosques(self, dataset_id: str, lat: float, lon: float, radius_km: float, limit: int = 100) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def delete_mosque(self, dataset_id: str, mosque_id: str) -> bool:
        pass

    @abstractmethod
    def delete_all_mosques(self, dataset_id: str) -> None:
        pass

    @abstractmethod
    def delete_mosques_bulk(self, dataset_id: str, mosque_ids: list[str]) -> bool:
        pass

    @abstractmethod
    def create_mosque(self, dataset_id: str, data: Dict[str, Any]) -> str:
        pass

    @abstractmethod
    def update_mosque(self, dataset_id: str, mosque_id: str, data: Dict[str, Any]) -> bool:
        pass
