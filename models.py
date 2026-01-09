

class Producto:
    def __init__(self, nombre, precio_venta, volumen=None, codigo_barras=None, descripcion=None, id_producto=None, cantidad_stock=0, stock_sin_lote=0, fecha_vencimiento=None):
        self.id_producto = id_producto
        self.nombre = nombre
        self.precio_venta = precio_venta
        self.volumen = volumen
        self.codigo_barras = codigo_barras
        self.descripcion = descripcion
        self.cantidad_stock = cantidad_stock
        self.stock_sin_lote = stock_sin_lote
        self.num_lotes = 0
        self.vencimiento_proximo = None
        self.fecha_vencimiento = fecha_vencimiento

    def __repr__(self):
        return (f"Producto(id={self.id_producto}, nombre='{self.nombre}', "
                f"precio=${self.precio_venta}, stock={self.cantidad_stock}, sin_lote={self.stock_sin_lote})")


class Cliente:
    def __init__(self, nombre, dni=None, id_cliente=None, saldo_deudor=0.0, fecha_limite_pago=None):
        self.id_cliente = id_cliente
        self.nombre = nombre
        self.dni = dni
        self.saldo_deudor = saldo_deudor
        self.fecha_limite_pago = fecha_limite_pago

    def __repr__(self):
        return f"Cliente(id={self.id_cliente}, nombre='{self.nombre}', deuda=${self.saldo_deudor})"


class Venta:
    def __init__(self, fecha_venta, id_cliente=None, total=0.0, forma_pago=None, observaciones=None, id_venta=None):
        self.id_venta = id_venta
        self.fecha_venta = fecha_venta
        self.id_cliente = id_cliente
        self.total = total
        self.forma_pago = forma_pago
        self.observaciones = observaciones
        self.detalles = []

    def calcular_total(self):
        self.total = sum(detalle.subtotal for detalle in self.detalles)
        return self.total

    def __repr__(self):
        return f"Venta(id={self.id_venta}, fecha='{self.fecha_venta}', total=${self.total})"


class DetalleVenta:
    def __init__(self, id_producto, cantidad, precio_unitario, id_venta=None, id_detalle=None, descuento=0.0, estado="Completada", subtotal=None):
        self.id_detalle = id_detalle
        self.id_venta = id_venta
        self.id_producto = id_producto
        self.cantidad = cantidad
        self.precio_unitario = precio_unitario
        self.descuento = descuento
        self.estado = estado
        self.subtotal = subtotal if subtotal is not None else self.calcular_subtotal()

    def calcular_subtotal(self):
        subtotal_bruto = self.cantidad * self.precio_unitario
        return subtotal_bruto * (1 - self.descuento / 100)

    def __repr__(self):
        return (f"DetalleVenta(prod_id={self.id_producto}, cant={self.cantidad}, "
                f"subtotal=${self.subtotal:.2f}, estado='{self.estado}')")