import json
import os
import threading
import uuid
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional


class RealtimePublisherUnavailable(RuntimeError):
    pass


class RealtimeEventPublisher:
    """Lazy Kafka publisher so the core API remains usable without Kafka.

    Kafka is only enabled when ``IMOSQUE_KAFKA_BOOTSTRAP_SERVERS`` is set.
    The optional client dependency lives in ``requirements-realtime.txt`` and
    is not required for local/offline routing.
    """

    def __init__(
        self,
        *,
        bootstrap_servers: Optional[str] = None,
        topic: Optional[str] = None,
        producer_factory: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ):
        self.bootstrap_servers = (
            bootstrap_servers
            if bootstrap_servers is not None
            else os.getenv("IMOSQUE_KAFKA_BOOTSTRAP_SERVERS", "")
        ).strip()
        self.topic = (
            topic if topic is not None else os.getenv("IMOSQUE_KAFKA_LOCATION_TOPIC", "imosque.location.v1")
        ).strip()
        self._producer_factory = producer_factory
        self._producer = None
        self._lock = threading.Lock()
        self._pseudonym_secret = os.getenv("IMOSQUE_EVENT_PSEUDONYM_SECRET", "").encode("utf-8")

    @property
    def enabled(self) -> bool:
        return bool(self.bootstrap_servers and self.topic)

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "topic": self.topic if self.enabled else None,
            "producer_initialized": self._producer is not None,
            "pseudonym_secret_configured": bool(self._pseudonym_secret),
        }

    def _pseudonymize(self, value: Any) -> str:
        raw = str(value or "").encode("utf-8")
        if self._pseudonym_secret:
            digest = hmac.new(self._pseudonym_secret, raw, hashlib.sha256).hexdigest()
            return f"hmac-sha256:{digest}"
        # Raw device/session identifiers must never enter the traffic stream.
        # Production should configure a secret to resist dictionary attacks.
        return f"sha256:{hashlib.sha256(raw).hexdigest()}"

    def _create_producer(self):
        config = {
            "bootstrap.servers": self.bootstrap_servers,
            "client.id": os.getenv("IMOSQUE_KAFKA_CLIENT_ID", "imosque-api"),
            "enable.idempotence": True,
            "acks": "all",
            "compression.type": os.getenv("IMOSQUE_KAFKA_COMPRESSION", "lz4"),
            "linger.ms": int(os.getenv("IMOSQUE_KAFKA_LINGER_MS", "10")),
        }
        if self._producer_factory is not None:
            return self._producer_factory(config)
        try:
            from confluent_kafka import Producer
        except ImportError as exc:
            raise RealtimePublisherUnavailable(
                "Kafka dikonfigurasi tetapi dependency confluent-kafka belum terpasang"
            ) from exc
        return Producer(config)

    def _get_producer(self):
        if self._producer is not None:
            return self._producer
        with self._lock:
            if self._producer is None:
                self._producer = self._create_producer()
        return self._producer

    def publish_location(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            raise RealtimePublisherUnavailable("Ingestion realtime Kafka belum dikonfigurasi")

        event_id = str(uuid.uuid4())
        occurred_at = payload.get("occurred_at")
        if isinstance(occurred_at, datetime):
            occurred_at = occurred_at.astimezone(timezone.utc).isoformat()
        event = {
            **payload,
            "event_id": event_id,
            "occurred_at": occurred_at,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 1,
        }
        event["user_id"] = self._pseudonymize(payload.get("user_id"))
        event["session_id"] = self._pseudonymize(payload.get("session_id"))
        partition_key = str(
            event.get("region_id")
            or event.get("dataset_id")
            or event.get("session_id")
            or event.get("user_id")
        )
        producer = self._get_producer()
        try:
            producer.produce(
                self.topic,
                key=partition_key.encode("utf-8"),
                value=json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            )
            producer.poll(0)
        except BufferError as exc:
            raise RealtimePublisherUnavailable("Buffer Kafka sedang penuh") from exc
        return {
            "status": "accepted",
            "event_id": event_id,
            "topic": self.topic,
            "partition_key": partition_key,
        }


realtime_event_publisher = RealtimeEventPublisher()
