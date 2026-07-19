import json
import os
from typing import Any, Callable, Dict, Optional

import requests


class RoutingWorkerError(RuntimeError):
    """Raised when a configured routing worker cannot serve a request."""


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_worker_map(raw: Optional[str]) -> Dict[str, str]:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("IMOSQUE_ROUTING_WORKER_MAP harus berupa objek JSON") from exc
    if not isinstance(loaded, dict):
        raise ValueError("IMOSQUE_ROUTING_WORKER_MAP harus berupa objek JSON")
    return {
        str(dataset_id): str(base_url).rstrip("/")
        for dataset_id, base_url in loaded.items()
        if str(dataset_id).strip() and str(base_url).strip()
    }


class RoutingWorkerGateway:
    """Route interactive pathfinding to regional workers with a local fallback.

    The gateway deliberately keeps remote dispatch out of ``RoutingUseCases``.
    Dataset and admin operations can remain in the API process while route-heavy
    requests move to independently scalable workers.
    """

    def __init__(
        self,
        *,
        default_url: Optional[str] = None,
        worker_map: Optional[Dict[str, str]] = None,
        fallback_local: Optional[bool] = None,
        connect_timeout_seconds: Optional[float] = None,
        read_timeout_seconds: Optional[float] = None,
        internal_token: Optional[str] = None,
        session: Any = requests,
    ):
        configured_default = (
            default_url
            if default_url is not None
            else os.getenv("IMOSQUE_ROUTING_WORKER_URL", "")
        )
        self.default_url = str(configured_default or "").strip().rstrip("/")
        self.worker_map = worker_map if worker_map is not None else _parse_worker_map(
            os.getenv("IMOSQUE_ROUTING_WORKER_MAP")
        )
        self.worker_map = {
            str(dataset_id): str(url).rstrip("/")
            for dataset_id, url in self.worker_map.items()
        }
        self.fallback_local = (
            _env_flag("IMOSQUE_ROUTING_REMOTE_FALLBACK", True)
            if fallback_local is None
            else bool(fallback_local)
        )
        self.connect_timeout_seconds = max(
            0.1,
            float(
                connect_timeout_seconds
                if connect_timeout_seconds is not None
                else os.getenv("IMOSQUE_ROUTING_CONNECT_TIMEOUT_SECONDS", "2")
            ),
        )
        self.read_timeout_seconds = max(
            0.5,
            float(
                read_timeout_seconds
                if read_timeout_seconds is not None
                else os.getenv("IMOSQUE_ROUTING_READ_TIMEOUT_SECONDS", "25")
            ),
        )
        self.internal_token = (
            internal_token
            if internal_token is not None
            else os.getenv("IMOSQUE_ROUTING_INTERNAL_TOKEN", "")
        )
        self._session = session

    @property
    def remote_enabled(self) -> bool:
        return bool(self.default_url or self.worker_map)

    def resolve_url(self, dataset_id: str) -> Optional[str]:
        did = str(dataset_id or "")
        exact = self.worker_map.get(did)
        if exact:
            return exact
        # A trailing '*' enables a compact region/prefix mapping without
        # silently treating every key as a prefix.
        prefix_matches = [
            (key[:-1], url)
            for key, url in self.worker_map.items()
            if key.endswith("*") and did.startswith(key[:-1])
        ]
        if prefix_matches:
            return max(prefix_matches, key=lambda item: len(item[0]))[1]
        return self.default_url or None

    def status(self) -> Dict[str, Any]:
        return {
            "remote_enabled": self.remote_enabled,
            "configured_workers": len(self.worker_map) + bool(self.default_url),
            "local_fallback": self.fallback_local,
        }

    def dispatch(
        self,
        *,
        endpoint: str,
        dataset_id: str,
        payload: Dict[str, Any],
        local_call: Callable[[], Dict[str, Any]],
    ) -> Dict[str, Any]:
        base_url = self.resolve_url(dataset_id)
        if not base_url:
            return local_call()

        headers = {"Content-Type": "application/json"}
        if self.internal_token:
            headers["X-Internal-Token"] = self.internal_token
        try:
            response = self._session.post(
                f"{base_url}/{endpoint.lstrip('/')}",
                json=payload,
                headers=headers,
                timeout=(self.connect_timeout_seconds, self.read_timeout_seconds),
            )
            if not response.ok:
                detail = None
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        detail = body.get("detail")
                except ValueError:
                    pass
                message = str(detail or f"HTTP {response.status_code}")
                if response.status_code < 500 or not self.fallback_local:
                    raise RoutingWorkerError(f"Routing worker menolak request: {message}")
                return local_call()
            result = response.json()
            if not isinstance(result, dict):
                raise RoutingWorkerError("Routing worker mengembalikan payload yang tidak valid")
            result.setdefault("routing_worker", {})
            if isinstance(result["routing_worker"], dict):
                result["routing_worker"].update(mode="remote", dataset_id=dataset_id)
            return result
        except RoutingWorkerError:
            raise
        except (requests.RequestException, ValueError) as exc:
            if self.fallback_local:
                return local_call()
            raise RoutingWorkerError(f"Routing worker tidak tersedia: {exc}") from exc
