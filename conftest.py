import pytest
import sqlite3
from unittest.mock import patch
from database import inicializar_bd

@pytest.fixture
def db_conn():
    """
    Fixture de pytest para crear y manejar una conexión a una base de datos SQLite en memoria.
    - Crea una base de datos en memoria para cada prueba.
    - Inicializa el esquema de la base de datos.
    - Proporciona el objeto de conexión a la prueba.
    - Cierra la conexión después de que la prueba finaliza.
    """
    # Usamos patch para asegurarnos de que cualquier llamada a _get_db_connection()
    # dentro del código de la aplicación use nuestra conexión de prueba en memoria.
    # Nota: _get_db_connection es la función que usa database.py para conectar.
    with patch('database._get_db_connection') as mock_conectar:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row # Importante: configurar row_factory como lo hace la app real
        mock_conectar.return_value = conn
        inicializar_bd(conn)
        yield conn
        conn.close()