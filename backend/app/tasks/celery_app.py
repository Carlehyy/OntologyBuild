from celery import Celery
from app.config import settings

celery_app = Celery("ontoprompt",
                    broker=settings.redis_url,
                    backend=settings.redis_url,
                    # Every task sent with ``.delay()`` must be imported by the
                    # worker process.  Importing a task only in the API process
                    # registers its name on the producer, but leaves the worker
                    # unable to consume it ("Received unregistered task").
                    include=[
                        "app.tasks.extraction",
                        "app.tasks.audit",
                        "app.tasks.v2.pipeline_run",
                        "app.tasks.v2.mapping_apply",
                    ])

# broker 不可用时快速失败 (默认会长时间重试, 导致 API 请求阻塞)
celery_app.conf.task_publish_retry = False
celery_app.conf.broker_connection_timeout = 3
