"""
Pruebas para las clases del modelo de datos en models.py.
"""
import pytest
from models import Producto, Cliente, Venta, DetalleVenta

def test_producto_creacion():
    """Verifica que un objeto Producto se inicializa correctamente."""
    p = Producto(nombre="Café", precio_venta=150.5, cantidad_stock=10)
    assert p.nombre == "Café"
    assert p.precio_venta == 150.5
    assert p.cantidad_stock == 10
    assert p.id_producto is None

def test_producto_repr():
    """Verifica la representación en string del Producto."""
    p = Producto(id_producto=1, nombre="Té", precio_venta=100, cantidad_stock=20, stock_sin_lote=-5)
    expected_repr = "Producto(id=1, nombre='Té', precio=$100, stock=20, sin_lote=-5)"
    assert repr(p) == expected_repr

def test_cliente_creacion():
    """Verifica que un objeto Cliente se inicializa correctamente."""
    c = Cliente(nombre="Juan Perez", dni="12345678")
    assert c.nombre == "Juan Perez"
    assert c.dni == "12345678"
    assert c.saldo_deudor == 0.0

def test_cliente_repr():
    """Verifica la representación en string del Cliente."""
    c = Cliente(id_cliente=1, nombre="Ana Gomez", saldo_deudor=1500)
    assert repr(c) == "Cliente(id=1, nombre='Ana Gomez', deuda=$1500)"

def test_detalle_venta_calculo_subtotal():
    """Verifica que el subtotal se calcula correctamente, con y sin descuento."""
    # Sin descuento
    dv1 = DetalleVenta(id_producto=1, cantidad=3, precio_unitario=100)
    assert dv1.subtotal == 300

    # Con descuento
    dv2 = DetalleVenta(id_producto=2, cantidad=2, precio_unitario=50, descuento=10) # 10% de descuento
    assert dv2.subtotal == 90.0 # (2 * 50) * (1 - 0.10)

def test_venta_calculo_total():
    """Verifica que el total de la Venta se calcula sumando los subtotales de sus detalles."""
    venta = Venta(fecha_venta="2023-10-27")
    
    # Detalles de la venta
    detalle1 = DetalleVenta(id_producto=1, cantidad=2, precio_unitario=100) # subtotal = 200
    detalle2 = DetalleVenta(id_producto=2, cantidad=1, precio_unitario=50, descuento=20) # subtotal = 40
    
    venta.detalles.append(detalle1)
    venta.detalles.append(detalle2)
    
    # El total debe ser la suma de los subtotales
    assert venta.calcular_total() == 240.0
    assert venta.total == 240.0

def test_venta_repr():
    """Verifica la representación en string de la Venta."""
    venta = Venta(id_venta=101, fecha_venta="2023-10-27", total=500)
    assert repr(venta) == "Venta(id=101, fecha='2023-10-27', total=$500)"