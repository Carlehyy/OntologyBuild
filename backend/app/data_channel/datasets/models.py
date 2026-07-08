import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, Integer, BigInteger, ForeignKey, Text, UniqueConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class Dataset(Base):
    __tablename__ = "v2_datasets"
    # curated 数据集按名字复用追加版本，同名双胞胎会让后续运行随机落到其中一个
    __table_args__ = (
        Index("uq_datasets_curated_name", "name", unique=True,
              sqlite_where=text("kind = 'curated'"),
              postgresql_where=text("kind = 'curated'")),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_connection_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_connections.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # structured|semi|unstructured|curated
    schema_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    latest_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class DatasetVersion(Base):
    __tablename__ = "v2_dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_no", name="uq_dataset_versions_dataset_version"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_id: Mapped[str] = mapped_column(String, ForeignKey("v2_datasets.id", ondelete="CASCADE"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rowcount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)  # MinIO s3://bucket/key
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class DatasetWriteLock(Base):
    """数据集写锁（跨进程互斥）。

    入湖合并是「读全量→内存合并→写新版」，锁不住就会互相覆盖增量。
    行本身即锁：主键冲突 = 有人持锁；acquired_at 超时视为持有者已崩溃，可被接管。
    """
    __tablename__ = "v2_dataset_write_locks"

    lock_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  default=lambda: datetime.now(timezone.utc))

class MediaItem(Base):
    __tablename__ = "v2_media_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_version_id: Mapped[str] = mapped_column(String, ForeignKey("v2_dataset_versions.id", ondelete="CASCADE"), nullable=False)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)  # pdf|docx|image|audio
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    ocr_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|processing|done|failed
    ocr_result_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
