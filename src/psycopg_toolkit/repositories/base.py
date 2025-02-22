import logging
from typing import TypeVar, Generic, List, Optional, Dict, Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.sql import SQL

from ..exceptions import (
    RecordNotFoundError,
    OperationError
)
from ..utils import PsycopgHelper

# Generic type variables for model and primary key
T = TypeVar('T')
K = TypeVar('K')

logger = logging.getLogger(__name__)


class BaseRepository(Generic[T, K]):
    """
    Generic base repository implementing common database operations.

    This class provides a foundation for implementing the repository pattern with
    PostgreSQL databases. It includes basic CRUD operations and uses generics to
    ensure type safety with different model and primary key types.

    Type Parameters:
        T: The model type this repository handles. Must be a Pydantic model.
        K: The primary key type (e.g., UUID, int, str).

    Attributes:
        db_connection (AsyncConnection): Active database connection.
        table_name (str): Name of the database table.
        model_class (type[T]): The Pydantic model class for type T.
        primary_key (str): Name of the primary key column in the table.

    Example:
        ```python
        class DocumentRepository(BaseRepository[Document, UUID]):
            def __init__(self, db_connection: AsyncConnection):
                super().__init__(
                    db_connection=db_connection,
                    table_name="documents",
                    model_class=Document,
                    primary_key="document_id"
                )

        # Or with an integer primary key
        class UserRepository(BaseRepository[User, int]):
            def __init__(self, db_connection: AsyncConnection):
                super().__init__(
                    db_connection=db_connection,
                    table_name="users",
                    model_class=User,
                    primary_key="user_id"
                )
        ```
    """

    def __init__(
            self,
            db_connection: AsyncConnection,
            table_name: str,
            model_class: type[T],
            primary_key: str = "id"
    ):
        """
        Initialize the base repository.

        Args:
            db_connection (AsyncConnection): Active database connection to use for operations.
            table_name (str): Name of the database table being accessed.
            model_class (type[T]): The Pydantic model class used for type T.
            primary_key (str, optional): Name of the primary key column. Defaults to "id".

        Note:
            The model_class should be a Pydantic model that matches the database schema.
            The primary_key should match the actual primary key column name in the database.
            The primary key type K is inferred from the model's type hints.
        """
        self.db_connection = db_connection
        self.table_name = table_name
        self.model_class = model_class
        self.primary_key = primary_key

    async def create(self, item: T) -> T:
        """
        Create a new record in the database.

        Args:
            item (T): The model instance to create in the database.

        Returns:
            T: The created model instance with any database-generated fields.

        Raises:
            HTTPException: If the creation fails or database error occurs.
            ValueError: If the database operation doesn't return a result.

        Example:
            ```python
            new_item = ItemModel(name="test", description="test item")
            created_item = await repo.create(new_item)
            ```
        """
        try:
            data = item.model_dump()
            insert_query = PsycopgHelper.build_insert_query(self.table_name, data)

            async with self.db_connection.cursor(row_factory=dict_row) as cur:
                await cur.execute(insert_query + SQL(" RETURNING *"), list(data.values()))
                result = await cur.fetchone()
                if not result:
                    raise OperationError(f"Failed to create {self.table_name} record")
                return self.model_class(**result)
        except Exception as e:
            logger.error(f"Error in create: {e}")
            if isinstance(e, OperationError):
                raise
            raise OperationError(f"Failed to create record: {str(e)}") from e

    async def create_bulk(self, items: List[T], batch_size: int = 100) -> List[T]:
        """
        Create multiple records in batches.

        Args:
            items (List[T]): List of model instances to create.
            batch_size (int, optional): Number of records per batch. Defaults to 100.

        Returns:
            List[T]: List of created model instances with database-generated fields.

        Raises:
            HTTPException: If the bulk creation fails or database error occurs.

        Example:
            ```python
            items = [ItemModel(name=f"item_{i}") for i in range(500)]
            created_items = await repo.create_bulk(items, batch_size=50)
            ```

        Note:
            Uses database transactions to ensure all-or-nothing batch operations.
            Large lists are automatically processed in batches for better performance.
        """
        all_results = []
        try:
            async with self.db_connection.transaction():
                for i in range(0, len(items), batch_size):
                    batch = items[i:i + batch_size]
                    data_list = [item.model_dump() for item in batch]
                    if not data_list:
                        continue

                    batch_insert_query = PsycopgHelper.build_insert_query(
                        self.table_name,
                        data_list[0],
                        batch_size=len(data_list)
                    )
                    batch_values = [val for data in data_list for val in data.values()]

                    async with self.db_connection.cursor(row_factory=dict_row) as cur:
                        full_query = batch_insert_query + SQL(" RETURNING *")
                        await cur.execute(full_query, batch_values)
                        results = await cur.fetchall()
                        all_results.extend([self.model_class(**row) for row in results])
            return all_results
        except Exception as e:
            logger.error(f"Error in create_bulk: {e}")
            raise OperationError(f"Failed to create records in bulk: {str(e)}") from e

    async def get_by_id(self, record_id: K) -> Optional[T]:
        """
        Retrieve a record by its ID.

        Args:
            record_id (K): The unique identifier of the record.

        Returns:
            Optional[T]: The found model instance or None if not found.

        Raises:
            HTTPException: If the database query fails.

        Example:
            ```python
            # With UUID primary key
            item = await repo.get_by_id(uuid.UUID('...'))

            # With integer primary key
            user = await user_repo.get_by_id(123)
            ```
        """
        try:
            select_query = PsycopgHelper.build_select_query(
                self.table_name,
                where_clause={self.primary_key: record_id}
            )
            async with self.db_connection.cursor(row_factory=dict_row) as cur:
                await cur.execute(select_query, [record_id])
                result = await cur.fetchone()
                if not result:
                    raise RecordNotFoundError(f"Record with id {record_id} not found")
                return self.model_class(**result)
        except Exception as e:
            logger.error(f"Error in get_by_id: {e}")
            if isinstance(e, RecordNotFoundError):
                raise
            raise OperationError(f"Failed to get record: {str(e)}") from e

    async def get_all(self) -> List[T]:
        """
        Retrieve all records from the table.

        Returns:
            List[T]: List of all model instances in the table.

        Raises:
            HTTPException: If the database query fails.

        Example:
            ```python
            all_items = await repo.get_all()
            for item in all_items:
                print(f"Item: {item.name}")
            ```

        Warning:
            Use with caution on large tables as it loads all records into memory.
        """
        try:
            async with self.db_connection.cursor(row_factory=dict_row) as cur:
                await cur.execute(f"SELECT * FROM {self.table_name}")
                rows = await cur.fetchall()
                return [self.model_class(**row) for row in rows]
        except Exception as e:
            logger.error(f"Error in get_all: {e}")
            raise OperationError(f"Failed to get all records: {str(e)}") from e

    async def update(self, record_id: K, data: Dict[str, Any]) -> T:
        """
        Update a record by its ID.

        Args:
            record_id (K): The unique identifier of the record to update.
            data (Dict[str, Any]): Dictionary of fields and values to update.

        Returns:
            T: The updated model instance.

        Raises:
            HTTPException: If the update fails or database error occurs.
            ValueError: If the record is not found.

        Example:
            ```python
            # With UUID primary key
            updated_doc = await doc_repo.update(
                uuid.UUID('...'),
                {"name": "new name"}
            )

            # With integer primary key
            updated_user = await user_repo.update(
                123,
                {"email": "new@email.com"}
            )
            ```
        """
        try:
            update_query = PsycopgHelper.build_update_query(
                self.table_name,
                data,
                where_clause={self.primary_key: record_id}
            )
            values = list(data.values()) + [record_id]
            async with self.db_connection.cursor(row_factory=dict_row) as cur:
                await cur.execute(update_query + SQL(" RETURNING *"), values)
                result = await cur.fetchone()
                if not result:
                    raise RecordNotFoundError(f"Record with id {record_id} not found")
                if result:
                    return self.model_class(**result)
                raise ValueError(f"Failed to update {self.table_name} record")
        except Exception as e:
            logger.error(f"Error in update: {e}")
            if isinstance(e, RecordNotFoundError):
                raise
            raise OperationError(f"Failed to update record: {str(e)}") from e

    async def delete(self, record_id: K) -> None:
        """
        Delete a record by its ID.

        Args:
            record_id (K): The unique identifier of the record to delete.

        Raises:
            HTTPException: If the deletion fails or database error occurs.

        Example:
            ```python
            # With UUID primary key
            await doc_repo.delete(uuid.UUID('...'))

            # With integer primary key
            await user_repo.delete(123)
            ```

        Note:
            This is a hard delete. Consider implementing soft delete if needed.
        """
        try:
            delete_query = PsycopgHelper.build_delete_query(
                self.table_name,
                where_clause={self.primary_key: record_id}
            )
            async with self.db_connection.cursor() as cur:
                await cur.execute(delete_query, [record_id])
                if cur.rowcount == 0:
                    raise RecordNotFoundError(f"Record with id {record_id} not found")
        except Exception as e:
            logger.error(f"Error in delete: {e}")
            if isinstance(e, RecordNotFoundError):
                raise
            raise OperationError(f"Failed to delete record: {str(e)}") from e

    async def exists(self, record_id: K) -> bool:
        """
        Check if a record exists by its ID.

        Args:
            record_id (K): The unique identifier to check.

        Returns:
            bool: True if the record exists, False otherwise.

        Raises:
            HTTPException: If the database query fails.

        Example:
            ```python
            # With UUID primary key
            if await doc_repo.exists(uuid.UUID('...')):
                print("Document exists")

            # With integer primary key
            if await user_repo.exists(123):
                print("User exists")
            ```
        """
        try:
            async with self.db_connection.cursor() as cur:
                await cur.execute(
                    f"SELECT 1 FROM {self.table_name} WHERE {self.primary_key} = %s",
                    [record_id]
                )
                return bool(await cur.fetchone())
        except Exception as e:
            logger.error(f"Error in exists: {e}")
            raise OperationError(f"Failed to check record existence: {str(e)}") from e