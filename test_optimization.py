import pytest
import sqlite3
import database
from models import Producto, Cliente, Venta, DetalleVenta
from unittest.mock import patch, MagicMock

# Fixture copiada de test_database.py para mantener el aislamiento
@pytest.fixture
def db_conn(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(database, "_get_db_connection", lambda: conn)
    database.inicializar_bd(conn)
    yield conn
    conn.close()

@pytest.fixture(autouse=True)
def mock_config_file():
    config_instance_mock = MagicMock()
    config_instance_mock.getboolean.return_value = True
    with patch('database.configparser.ConfigParser', return_value=config_instance_mock) as mock_parser_class:
        yield config_instance_mock

def test_obtener_clientes_optimizado_calcula_deuda_correctamente(db_conn):
    """
    Prueba que la función optimizada obtener_clientes calcula el saldo deudor
    correctamente (incluyendo la lógica inflacionaria) y filtra adecuadamente.
    """
    # 1. Setup
    p_id = database.agregar_producto(Producto(nombre="Pan Test", precio_venta=100, cantidad_stock=10))
    c1_id = database.agregar_cliente(Cliente(nombre="Cliente Con Deuda", dni="111"))
    c2_id = database.agregar_cliente(Cliente(nombre="Cliente Sin Deuda", dni="222"))

    # 2. Generar deuda para c1 (2 unidades * $100 = $200)
    venta = Venta(fecha_venta="2023-01-01", forma_pago="Libreta", id_cliente=c1_id)
    venta.detalles.append(DetalleVenta(id_producto=p_id, cantidad=2, precio_unitario=100))
    venta.calcular_total()
    database.registrar_venta(venta)

    # 3. Aumentar precio del producto (Inflación) -> Deuda debe ser 2 * 150 = 300
    p = database.obtener_producto_por_id(p_id)
    p.precio_venta = 150
    database.actualizar_producto(p)

    # 4. Verificar obtener_clientes (sin filtros)
    clientes = database.obtener_clientes()
    
    c1 = next((c for c in clientes if c.id_cliente == c1_id), None)
    c2 = next((c for c in clientes if c.id_cliente == c2_id), None)

    assert c1 is not None
    assert c2 is not None
    assert c1.saldo_deudor == 300.0, f"El saldo deudor esperado era 300.0, pero se obtuvo {c1.saldo_deudor}"
    assert c2.saldo_deudor == 0.0

    # 5. Verificar filtro solo_con_deuda
    clientes_deudores = database.obtener_clientes(solo_con_deuda=True)
    ids_deudores = [c.id_cliente for c in clientes_deudores]
    assert c1_id in ids_deudores
    assert c2_id not in ids_deudores
