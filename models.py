"""
Modulo que define las clases del modelo de datos (objetos de negocio)
de la aplicación.
"""

class Producto:
    """Representa un producto en el sistema."""
    def __init__(self, nombre, precio_venta, volumen=None, codigo_barras=None, descripcion=None, id_producto=None, cantidad_stock=0, stock_sin_lote=0, fecha_vencimiento=None):
        self.id_producto = id_producto
        self.nombre = nombre
        self.precio_venta = precio_venta
        self.volumen = volumen
        self.codigo_barras = codigo_barras
        self.descripcion = descripcion
        # cantidad_stock ahora es una propiedad calculada para reflejar el stock real total.
        # No se almacena directamente en la BD, se calcula al vuelo.
        self.cantidad_stock = cantidad_stock
        self.stock_sin_lote = stock_sin_lote  # Stock vendido sin lote asociado (negativo)
        self.num_lotes = 0  # Campo auxiliar para la vista de Stock
        self.vencimiento_proximo = None  # Campo auxiliar para la vista de Stock
        self.fecha_vencimiento = fecha_vencimiento # Usado solo al crear un nuevo producto con su lote inicial

    def __repr__(self):
        """Representación en string del objeto para debugging."""
        return (f"Producto(id={self.id_producto}, nombre='{self.nombre}', " # type: ignore
                f"precio=${self.precio_venta}, stock={self.cantidad_stock}, sin_lote={self.stock_sin_lote})") # type: ignore


class Cliente:
    """Representa un cliente en el sistema."""
    def __init__(self, nombre, dni=None, id_cliente=None, saldo_deudor=0.0, fecha_limite_pago=None):
        self.id_cliente = id_cliente
        self.nombre = nombre
        self.dni = dni
        self.saldo_deudor = saldo_deudor
        self.fecha_limite_pago = fecha_limite_pago

    def __repr__(self):
        return f"Cliente(id={self.id_cliente}, nombre='{self.nombre}', deuda=${self.saldo_deudor})"


class Venta:
    """Representa una transacción de venta completa."""
    def __init__(self, fecha_venta, id_cliente=None, total=0.0, forma_pago=None, observaciones=None, id_venta=None):
        self.id_venta = id_venta
        self.fecha_venta = fecha_venta
        self.id_cliente = id_cliente
        self.total = total
        self.forma_pago = forma_pago
        self.observaciones = observaciones
        self.detalles = []  # Lista para almacenar objetos DetalleVenta

    def calcular_total(self):
        """Calcula el total de la venta sumando los subtotales de los detalles."""
        self.total = sum(detalle.subtotal for detalle in self.detalles)
        return self.total

    def __repr__(self):
        return f"Venta(id={self.id_venta}, fecha='{self.fecha_venta}', total=${self.total})"


class DetalleVenta:
    """Representa una línea de item dentro de una venta."""
    def __init__(self, id_producto, cantidad, precio_unitario, id_venta=None, id_detalle=None, descuento=0.0, estado="Completada", subtotal=None):
        self.id_detalle = id_detalle
        self.id_venta = id_venta
        self.id_producto = id_producto
        self.cantidad = cantidad
        self.precio_unitario = precio_unitario
        self.descuento = descuento
        # Estados posibles: "Completada", "Pendiente de Stock"
        self.estado = estado
        self.subtotal = subtotal if subtotal is not None else self.calcular_subtotal()

    def calcular_subtotal(self):
        """Calcula el subtotal del detalle de venta."""
        subtotal_bruto = self.cantidad * self.precio_unitario
        return subtotal_bruto * (1 - self.descuento / 100)

    def __repr__(self):
        """Representación en string del objeto para debugging."""
        return (f"DetalleVenta(prod_id={self.id_producto}, cant={self.cantidad}, "
                f"subtotal=${self.subtotal:.2f}, estado='{self.estado}')")