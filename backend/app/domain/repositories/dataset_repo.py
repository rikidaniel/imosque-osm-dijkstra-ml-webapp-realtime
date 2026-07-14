from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class DatasetRepository(ABC):
    @abstractmethod
    def upsert_dataset(self, dataset_id: str, data: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    def get_dataset(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def list_datasets(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def set_active_dataset_id(self, dataset_id: str) -> None:
        pass

    @abstractmethod
    def get_active_dataset_id(self) -> str:
        pass
        
    @abstractmethod
    def save_osm_cache(self, cache_id: str, data: Dict[str, Any]) -> None:
        pass
        
    @abstractmethod
    def get_osm_cache(self, cache_id: str = "latest") -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def delete_osm_cache(self, cache_id: str) -> None:
        pass

    @abstractmethod
    def delete_dataset(self, dataset_id: str) -> bool:
        pass
