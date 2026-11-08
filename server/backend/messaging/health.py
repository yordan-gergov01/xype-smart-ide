"""Broker reachability check. A long-lived connection arrives with the message
topology; until then each check opens and closes its own."""

import aio_pika
from aio_pika.exceptions import AMQPError

from backend.core.settings import BackendSettings
from common_lib.errors import DependencyUnavailableError


class BrokerProbe:
    """RabbitMQ accepts a connection to the vhost with this service's user."""

    name = "broker"

    def __init__(self, settings: BackendSettings) -> None:
        self._settings = settings

    async def check(self) -> None:
        try:
            connection = await aio_pika.connect(
                host=self._settings.rabbitmq_host,
                port=self._settings.rabbitmq_port,
                virtualhost=self._settings.rabbitmq_vhost,
                login=self._settings.rabbitmq_user,
                password=self._settings.rabbitmq_password.get_secret_value(),
                timeout=self._settings.readiness_timeout_seconds,
            )
            await connection.close()
        except (AMQPError, OSError):
            raise DependencyUnavailableError(self.name) from None
