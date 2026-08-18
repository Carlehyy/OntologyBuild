from celery import Celery
from app.config import settings

celery_app = Celery("ontoprompt",
                    broker=settings.redis_url,
                    backend=settings.redis_url,
                    # Every task sent with ``.delay()`` must be imported by the
                    # worker process.  Importing a task only in the API process
                    # registers its name on the producer, but leaves the worker
                    # unable to consume it ("Received unregistered task").
                    # dataset_import 与 UI 手动运行已迁至 NATS executor
                    # （app.data_channel.pipeline_tasks.nats_executor），不在
                    # 此注册；其余任务在 Celery 退役第二阶段处理。
                    include=[
                        "app.tasks.v2.pipeline_run",
                        "app.tasks.v2.mapping_apply",
                        "app.tasks.v2.connection_sync",
                        "app.tasks.v2.dataset_event_processing",
                    ])

# broker 不可用时快速失败 (默认会长时间重试, 导致 API 请求阻塞)
celery_app.conf.task_publish_retry = False
celery_app.conf.broker_connection_timeout = 3
