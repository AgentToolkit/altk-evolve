import datetime
import logging
import sqlite3
import threading

from kaizen.schema.core import Namespace
from kaizen.schema.exceptions import NamespaceAlreadyExistsException

logger = logging.getLogger(__name__)


def adapt_datetime_epoch(time: datetime.datetime) -> int:
    """Adapt datetime.datetime to Unix timestamp."""
    return int(time.timestamp())


def convert_timestamp(time: bytes) -> datetime.datetime:
    """Convert Unix epoch timestamp to datetime.datetime object."""
    return datetime.datetime.fromtimestamp(int.from_bytes(time), datetime.UTC)

sqlite3.register_adapter(datetime.datetime, adapt_datetime_epoch)
sqlite3.register_converter("timestamp", convert_timestamp)

class SQLiteManager:
    """A database for any resources that can't be generalized across backends."""
    def __init__(self, db_path: str = 'katas.sqlite.db'):
        self.db_path = db_path

    def _create_namespace_table(self):
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute("""
                    CREATE TABLE IF NOT EXISTS namespaces (
                        id           TEXT PRIMARY KEY,
                        created_at   TIMESTAMP NOT NULL
                    )
                """
                )
                self.connection.execute("COMMIT")
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to create namespaces table: {e}")
                raise

    def create_namespace(
        self,
        namespace_id: str
    ) -> Namespace:
        created_at = datetime.datetime.now(datetime.timezone.utc)
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute(
                    """
                    INSERT INTO namespaces (
                        id, created_at
                    )
                    VALUES (?, ?)
                """,
                    (
                        namespace_id,
                        created_at
                    ),
                )
                self.connection.execute("COMMIT")
            except sqlite3.IntegrityError as e:
                raise NamespaceAlreadyExistsException(f'Namespace "{namespace_id}" already exists.') from e
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to create namespace: {e}")
                raise
        return Namespace(
            id=namespace_id,
            created_at=created_at
        )

    def get_namespace(self, namespace_id: str) -> Namespace | None:
        with self._lock:
            cursor: sqlite3.Cursor = self.connection.cursor()
            cursor.row_factory = Namespace.row_factory
            cursor.execute("""
                    SELECT id, created_at
                    FROM namespaces
                    WHERE id = ?
                """,
                (namespace_id,),
            )
            return cursor.fetchone()

    def search_namespaces(
        self,
        limit: int = 10,
    ) -> list[Namespace]:
        with self._lock:
            cursor: sqlite3.Cursor = self.connection.cursor()
            cursor.row_factory = Namespace.row_factory
            cursor.execute("""
                    SELECT id, created_at
                    FROM namespaces
                    LIMIT ?
                """,
                [limit],
            )
            return cursor.fetchall()

    def delete_namespace(self, namespace_id: str):
        with self._lock:
            self.connection.execute("BEGIN")
            self.connection.execute("DELETE FROM namespaces WHERE id = ?", (namespace_id,))
            self.connection.execute("COMMIT")

    def reset(self) -> None:
        """Drop and recreate every table."""
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute("DROP TABLE IF EXISTS namespaces")
                self.connection.execute("COMMIT")
                self._create_namespace_table()
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to reset tables: {e}")
                raise

    def close(self) -> None:
        if self.connection:
            self.connection.close()
            self.connection = None

    def __enter__(self) -> "SQLiteManager":
        self.connection: sqlite3.Connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._create_namespace_table()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        self.close()