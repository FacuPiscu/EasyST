import sqlite3
import hashlib
import configparser
import sys
import os
from models import Producto, Venta, Cliente, DetalleVenta
from datetime import datetime, timedelta
import unicodedata

def get_persistent_path(filename):
    if getattr(sys, 'frozen', False):
        application_path = os.path.dirname(sys.executable)
    else:
        application_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(application_path, filename)

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

DB_FILE = get_persistent_path('easyst.db')
SQL_SCRIPT = """
CREATE TABLE IF NOT EXISTS productos (
    id_producto INTEGER PRIMARY KEY NOT NULL, 
    nombre TEXT NOT NULL,
    precio_venta REAL NOT NULL,
    volumen REAL,
    codigo_barras TEXT UNIQUE,
    descripcion TEXT,
    stock_sin_lote INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stock (
    id_stock INTEGER PRIMARY KEY AUTOINCREMENT,
    id_producto INTEGER NOT NULL, 
    cantidad INTEGER NOT NULL,
    fecha_vencimiento TEXT,
    codigo_barras TEXT,
    FOREIGN KEY (id_producto) REFERENCES productos(id_producto) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cliente (
    id_cliente INTEGER PRIMARY KEY, 
    nombre TEXT NOT NULL,
    dni TEXT UNIQUE,    
    fecha_limite_pago TEXT
);

CREATE TABLE IF NOT EXISTS ventas (
    id_venta INTEGER PRIMARY KEY,
    fecha_venta TEXT NOT NULL,
    total REAL,
    forma_pago TEXT,
    observaciones TEXT,
    ruta_pdf_ticket TEXT,
    id_cliente INTEGER,
    FOREIGN KEY (id_cliente) REFERENCES cliente(id_cliente)
);

CREATE TABLE IF NOT EXISTS detalle_venta (
    id_detalle INTEGER PRIMARY KEY AUTOINCREMENT,
    id_venta INTEGER NOT NULL,
    id_producto INTEGER NOT NULL,
    cantidad INTEGER NOT NULL,
    precio_unitario REAL NOT NULL,
    descuento REAL NOT NULL DEFAULT 0,
    estado TEXT NOT NULL DEFAULT 'Completada',
    subtotal REAL NOT NULL,
    FOREIGN KEY (id_venta) REFERENCES ventas(id_venta),
    FOREIGN KEY (id_producto) REFERENCES productos(id_producto)
);

CREATE TABLE IF NOT EXISTS usuarios (
    id_usuario INTEGER PRIMARY KEY,
    nombre_usuario TEXT UNIQUE NOT NULL,
    contrasena_hash TEXT NOT NULL,
    rol TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS movimientos_cuenta_cliente (
    id_movimiento INTEGER PRIMARY KEY AUTOINCREMENT,
    id_cliente INTEGER NOT NULL,
    id_venta INTEGER,
    fecha TEXT NOT NULL,
    tipo_movimiento TEXT NOT NULL,
    monto REAL NOT NULL,
    FOREIGN KEY (id_cliente) REFERENCES cliente(id_cliente) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_productos_nombre ON productos (nombre);

CREATE INDEX IF NOT EXISTS idx_cliente_nombre ON cliente (nombre);

CREATE INDEX IF NOT EXISTS idx_ventas_fecha ON ventas (fecha_venta);

CREATE INDEX IF NOT EXISTS idx_detalle_venta_id_venta ON detalle_venta (id_venta);
CREATE INDEX IF NOT EXISTS idx_detalle_venta_id_producto ON detalle_venta (id_producto);
CREATE INDEX IF NOT EXISTS idx_movimientos_id_cliente ON movimientos_cuenta_cliente (id_cliente);
"""

LATEST_SCHEMA_VERSION = 5

MIGRATIONS = {
    2: """
       ALTER TABLE cliente DROP COLUMN saldo_deudor;
       CREATE TABLE IF NOT EXISTS movimientos_cuenta_cliente (id_movimiento INTEGER PRIMARY KEY AUTOINCREMENT, id_cliente INTEGER NOT NULL, id_venta INTEGER, fecha TEXT NOT NULL, tipo_movimiento TEXT NOT NULL, monto REAL NOT NULL, FOREIGN KEY (id_cliente) REFERENCES cliente(id_cliente) ON DELETE CASCADE);
       CREATE INDEX IF NOT EXISTS idx_movimientos_id_cliente ON movimientos_cuenta_cliente (id_cliente);
    """,
    3: """
       ALTER TABLE detalle_venta ADD COLUMN estado TEXT NOT NULL DEFAULT 'Completada';
    """,
    4: """
       ALTER TABLE productos ADD COLUMN stock_sin_lote INTEGER NOT NULL DEFAULT 0;
    """,
    5: """
       ALTER TABLE stock ADD COLUMN codigo_barras TEXT;
    """,
}

def inicializar_bd(conexion: sqlite3.Connection | None = None):
    conn_provided = conexion is not None
    conn = conexion if conn_provided else _get_db_connection()

    try:
        cursor = conn.cursor()

        cursor.executescript(SQL_SCRIPT)
        conn.commit()

        cursor.execute("PRAGMA user_version")
        current_version = cursor.fetchone()[0]
        
        if current_version == 0:
            print(f"Base de datos nueva detectada. Estableciendo esquema a la versión {LATEST_SCHEMA_VERSION}.")
            cursor.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")
            conn.commit()
        elif current_version < LATEST_SCHEMA_VERSION:
            print(f"Versión de la BD: {current_version}. Actualizando a: {LATEST_SCHEMA_VERSION}...")
            for version in range(current_version + 1, LATEST_SCHEMA_VERSION + 1):
                if version in MIGRATIONS:
                    print(f"Aplicando migración para la versión {version}...")
                    cursor.executescript(MIGRATIONS[version])
                    print(f"Migración a la versión {version} completada.")
                    cursor.execute(f"PRAGMA user_version = {version}")
                    conn.commit()

        _crear_usuario_admin_default(conn)
        print(f"Base de datos '{DB_FILE}' conectada y verificada con éxito. Versión del esquema: {LATEST_SCHEMA_VERSION}.")
    except sqlite3.Error as e:
        print(f"Ocurrió un error en SQLite: {e}")
    finally:
        if not conn_provided and conn:
            conn.close()

def _crear_usuario_admin_default(conexion: sqlite3.Connection):
    cursor = conexion.cursor()
    cursor.execute("SELECT COUNT(id_usuario) FROM usuarios")
    if cursor.fetchone()[0] == 0:
        nombre_usuario = "admin"
        contrasena = "admin"
        contrasena_hash = hashlib.sha256(contrasena.encode()).hexdigest()
        rol = "Administrador"
        cursor.execute(
            "INSERT INTO usuarios (nombre_usuario, contrasena_hash, rol) VALUES (?, ?, ?)",
            (nombre_usuario, contrasena_hash, rol)
        )
        conexion.commit()
        print("="*50)
        print("¡ATENCIÓN! Se ha creado el usuario administrador por defecto.")
        print(f"Usuario: {nombre_usuario}")
        print(f"Contraseña: {contrasena}")
        print("Por favor, cámbiela en una futura sección de 'Usuarios'.")
        print("="*50)

def _get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def _normalizar_texto(texto: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn').lower()

def obtener_productos(nombre_like=None, solo_poco_stock=False, umbral_stock=5):
    conn = _get_db_connection()
    cursor = conn.cursor()
    try:
        query = """ 
            SELECT 
                p.*,
                (IFNULL(s.total_lotes, 0) + p.stock_sin_lote) as cantidad_stock,
                s.num_lotes,
                s.vencimiento_proximo
            FROM
                productos p
            LEFT JOIN 
                (SELECT 
                    id_producto, 
                    SUM(cantidad) as total_lotes,
                    COUNT(CASE WHEN cantidad > 0 THEN 1 END) as num_lotes,
                    MIN(CASE WHEN cantidad > 0 THEN fecha_vencimiento END) as vencimiento_proximo
                 FROM stock 
                 GROUP BY id_producto) s ON p.id_producto = s.id_producto
            ORDER BY p.nombre;
        """
        cursor.execute(query)
        filas = cursor.fetchall()

        productos = []
        for fila in filas:
            prod_dict = dict(fila)
            producto = Producto(
                id_producto=prod_dict['id_producto'],
                nombre=prod_dict['nombre'],
                precio_venta=prod_dict['precio_venta'],
                volumen=prod_dict['volumen'],
                codigo_barras=prod_dict['codigo_barras'],
                descripcion=prod_dict['descripcion'],
                cantidad_stock=prod_dict.get('cantidad_stock', 0),
                stock_sin_lote=prod_dict['stock_sin_lote']
            )
            producto.num_lotes = prod_dict.get('num_lotes', 0) or 0
            producto.vencimiento_proximo = prod_dict.get('vencimiento_proximo')
            productos.append(producto)

        if nombre_like:
            productos = [p for p in productos if _normalizar_texto(nombre_like) in _normalizar_texto(p.nombre)]

        if solo_poco_stock:
            productos = [p for p in productos if p.cantidad_stock <= umbral_stock]

        return productos
    except sqlite3.Error as e:
        print(f"Error al obtener productos: {e}")
        return []

def agregar_producto(producto: Producto):
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO productos (nombre, precio_venta, volumen, codigo_barras, descripcion, stock_sin_lote) VALUES (?, ?, ?, ?, ?, ?)",
                (producto.nombre, producto.precio_venta, producto.volumen, producto.codigo_barras, producto.descripcion, 0)
            )
            id_producto_nuevo = cursor.lastrowid
    
            cursor.execute(
                "INSERT INTO stock (id_producto, cantidad, fecha_vencimiento) VALUES (?, ?, ?)",
                (id_producto_nuevo, producto.cantidad_stock, producto.fecha_vencimiento if hasattr(producto, 'fecha_vencimiento') else None)
            )
            return id_producto_nuevo
    except sqlite3.Error as e:
        print(f"Error al agregar producto: {e}")
        return None

def obtener_clientes(nombre_o_dni=None, solo_con_deuda=False):
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        
        query = """
            SELECT 
                c.*,
                (
                    IFNULL((
                        SELECT SUM(dv.cantidad * p.precio_venta)
                        FROM movimientos_cuenta_cliente m
                        JOIN detalle_venta dv ON m.id_venta = dv.id_venta
                        JOIN productos p ON dv.id_producto = p.id_producto
                        WHERE m.id_cliente = c.id_cliente AND m.tipo_movimiento = 'DEUDA'
                    ), 0) 
                    - 
                    IFNULL((
                        SELECT SUM(monto)
                        FROM movimientos_cuenta_cliente m
                        WHERE m.id_cliente = c.id_cliente AND m.tipo_movimiento = 'PAGO'
                    ), 0)
                ) as saldo_calculado
            FROM cliente c
            ORDER BY c.nombre;
        """
        cursor.execute(query)
        filas = cursor.fetchall()

        clientes = []
        for fila in filas:
            c = Cliente(
                id_cliente=fila['id_cliente'],
                nombre=fila['nombre'],
                dni=fila['dni'],
                fecha_limite_pago=fila['fecha_limite_pago'],
                saldo_deudor=fila['saldo_calculado']
            )
            clientes.append(c)

        if nombre_o_dni:
            termino_busqueda = _normalizar_texto(nombre_o_dni)
            clientes = [c for c in clientes if 
                        termino_busqueda in _normalizar_texto(c.nombre) or 
                        (c.dni and termino_busqueda in c.dni)]

        if solo_con_deuda:
            clientes = [c for c in clientes if c.saldo_deudor > 0]

        return clientes

def obtener_cliente_por_id(id_cliente):
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cliente WHERE id_cliente = ?", (id_cliente,))
        fila = cursor.fetchone()
        if fila:
            cliente = Cliente(**dict(fila))
            cliente.saldo_deudor = obtener_saldo_deudor_cliente(id_cliente)
            return cliente
        return None

def agregar_cliente(cliente: Cliente):
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO cliente (nombre, dni, fecha_limite_pago) VALUES (?, ?, ?)",
                (cliente.nombre, cliente.dni, cliente.fecha_limite_pago)
            )
            conn.commit()
            
        return cursor.lastrowid
    except sqlite3.Error as e:
        print(f"Error al agregar cliente: {e}")
        return None

def actualizar_cliente(cliente: Cliente):
    conn = _get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE cliente SET nombre = ?, dni = ?, fecha_limite_pago = ? WHERE id_cliente = ?",
                (cliente.nombre, cliente.dni, cliente.fecha_limite_pago, cliente.id_cliente)
            )
        return True
    except sqlite3.Error as e:
        conn.rollback()
        print(f"Error al actualizar cliente: {e}")
        return False

def obtener_producto_por_codigo_barras(codigo_barras):
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM productos WHERE codigo_barras = ?", (codigo_barras,))
        fila = cursor.fetchone()
        if fila:
            producto = Producto(**dict(fila))
            producto.cantidad_stock = obtener_stock_total_lotes(producto.id_producto) + producto.stock_sin_lote
            return producto
        return None

def obtener_producto_por_id(id_producto):
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM productos WHERE id_producto = ?", (id_producto,))
        fila = cursor.fetchone()
        if fila:
            producto = Producto(**dict(fila))
            producto.cantidad_stock = obtener_stock_total_lotes(producto.id_producto) + producto.stock_sin_lote
            return producto
        return None

def obtener_producto_por_nombre(nombre):
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM productos WHERE nombre = ?", (nombre,))
        fila = cursor.fetchone()
        if fila:
            producto = Producto(**dict(fila))
            producto.cantidad_stock = obtener_stock_total_lotes(producto.id_producto) + producto.stock_sin_lote
            return producto
        return None

def actualizar_producto(producto: Producto):
    conn = _get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE productos 
                   SET nombre = ?, precio_venta = ?, volumen = ?, codigo_barras = ?, descripcion = ?, stock_sin_lote = ?
                   WHERE id_producto = ?""",
                (producto.nombre, producto.precio_venta, producto.volumen, producto.codigo_barras, producto.descripcion, producto.stock_sin_lote, producto.id_producto)
            )
            return True
    except sqlite3.Error as e:
        print(f"Error al actualizar producto: {e}")
        conn.rollback()
        return False

def registrar_venta(venta: 'Venta'):
    conn = _get_db_connection()
    try:
        with conn:
            config = configparser.ConfigParser()
            config.read(resource_path('config.ini'))
            PERMITIR_STOCK_NEGATIVO = config.getboolean('Negocio', 'PermitirStockNegativo', fallback=False)

            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO ventas (fecha_venta, total, forma_pago, observaciones, id_cliente) VALUES (?, ?, ?, ?, ?)",
                (venta.fecha_venta, venta.total, venta.forma_pago, venta.observaciones, venta.id_cliente)
            )
            id_venta_nueva = cursor.lastrowid

            if venta.forma_pago == 'Libreta' and venta.id_cliente is not None:
                cursor.execute(
                    """INSERT INTO movimientos_cuenta_cliente 
                       (id_cliente, id_venta, fecha, tipo_movimiento, monto) 
                       VALUES (?, ?, ?, ?, ?)""",
                    (venta.id_cliente, id_venta_nueva, venta.fecha_venta, 'DEUDA', venta.total)
                )

            for detalle in venta.detalles:
                detalle.estado = "Completada"
                cursor.execute(
                    "INSERT INTO detalle_venta (id_venta, id_producto, cantidad, precio_unitario, descuento, subtotal, estado) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (id_venta_nueva, detalle.id_producto, detalle.cantidad, detalle.precio_unitario, detalle.descuento, detalle.subtotal, detalle.estado)
                )

                cursor.execute("SELECT stock_sin_lote FROM productos WHERE id_producto = ?", (detalle.id_producto,))
                stock_sin_lote_actual = cursor.fetchone()[0]

                stock_en_lotes = _obtener_stock_total_lotes_con_cursor(cursor, detalle.id_producto)
                cantidad_a_vender = detalle.cantidad
                stock_total_disponible = stock_en_lotes + stock_sin_lote_actual

                if not PERMITIR_STOCK_NEGATIVO and stock_total_disponible < cantidad_a_vender:
                    raise sqlite3.IntegrityError(
                        f"Stock insuficiente para el producto ID {detalle.id_producto}. Se requieren {cantidad_a_vender} y hay {stock_total_disponible}."
                    )
                
                cantidad_a_reducir_de_lotes = min(cantidad_a_vender, stock_en_lotes)
                if cantidad_a_reducir_de_lotes > 0:
                    _reducir_stock_de_lotes(cursor, detalle.id_producto, cantidad_a_reducir_de_lotes)

                diferencia = cantidad_a_vender - stock_en_lotes
                if diferencia > 0:
                    cursor.execute(
                        "UPDATE productos SET stock_sin_lote = stock_sin_lote - ? WHERE id_producto = ?",
                        (diferencia, detalle.id_producto)
                    )

            return id_venta_nueva
    except sqlite3.IntegrityError:
        raise
    except sqlite3.Error as e: 
        print(f"Error al registrar la venta: {e}")
        conn.rollback()
        return None

def _reducir_stock_de_lotes(cursor, id_producto, cantidad_a_descontar):
    cursor.execute(
        """SELECT id_stock, cantidad FROM stock WHERE id_producto = ? AND cantidad > 0
           ORDER BY IFNULL(fecha_vencimiento, '9999-12-31') ASC, id_stock ASC""",
        (id_producto,)
    )
    lotes_disponibles = cursor.fetchall()

    for lote in lotes_disponibles:
        if cantidad_a_descontar <= 0:
            break

        id_lote_actual = lote['id_stock']
        cantidad_en_lote = lote['cantidad']

        if cantidad_a_descontar >= cantidad_en_lote:
            cursor.execute("UPDATE stock SET cantidad = 0 WHERE id_stock = ?", (id_lote_actual,))
            cantidad_a_descontar -= cantidad_en_lote
        elif cantidad_a_descontar > 0:
            nueva_cantidad = cantidad_en_lote - cantidad_a_descontar
            cursor.execute("UPDATE stock SET cantidad = ? WHERE id_stock = ?", (nueva_cantidad, id_lote_actual))
            cantidad_a_descontar = 0
        
        if cantidad_a_descontar == 0:
            break

def obtener_lotes_por_producto(id_producto):
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id_stock, cantidad, fecha_vencimiento, codigo_barras FROM stock WHERE id_producto = ? AND cantidad != 0 ORDER BY fecha_vencimiento ASC",
            (id_producto,)
        )
        filas = cursor.fetchall()
        return [dict(fila) for fila in filas]

def _obtener_stock_total_lotes_con_cursor(cursor: sqlite3.Cursor, id_producto: int) -> int:
    cursor.execute(
        "SELECT IFNULL(SUM(cantidad), 0) FROM stock WHERE id_producto = ?",
        (id_producto,)
    )
    return cursor.fetchone()[0]

def obtener_stock_total_lotes(id_producto):
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT IFNULL(SUM(cantidad), 0) FROM stock WHERE id_producto = ?",
            (id_producto,)
        )
        total = cursor.fetchone()[0]
        return total

def actualizar_lote(id_stock, cantidad, fecha_vencimiento, codigo_barras):
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE stock SET cantidad = ?, fecha_vencimiento = ?, codigo_barras = ? WHERE id_stock = ?",
                (cantidad, fecha_vencimiento, codigo_barras, id_stock)
            )
            return True
    except sqlite3.Error as e:
        print(f"Error al actualizar el lote: {e}")
        return False

def agregar_lote(id_producto, cantidad, fecha_vencimiento, codigo_barras=None):
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT stock_sin_lote FROM productos WHERE id_producto = ?", (id_producto,))
            stock_deuda = cursor.fetchone()[0]

            cantidad_restante_lote = cantidad
            if stock_deuda < 0:
                a_saldar = abs(stock_deuda)
                if cantidad_restante_lote >= a_saldar:
                    cursor.execute("UPDATE productos SET stock_sin_lote = 0 WHERE id_producto = ?", (id_producto,))
                    cantidad_restante_lote -= a_saldar
                else:
                    cursor.execute("UPDATE productos SET stock_sin_lote = stock_sin_lote + ? WHERE id_producto = ?", (cantidad_restante_lote, id_producto))
                    cantidad_restante_lote = 0

            if cantidad_restante_lote > 0:
                query_buscar_lote = "SELECT id_stock, cantidad FROM stock WHERE id_producto = ?"
                params_buscar_lote = [id_producto]

                if fecha_vencimiento is not None:
                    query_buscar_lote += " AND fecha_vencimiento = ?"
                    params_buscar_lote.append(fecha_vencimiento)
                else:
                    query_buscar_lote += " AND fecha_vencimiento IS NULL"
                
                cursor.execute(
                    query_buscar_lote,
                    tuple(params_buscar_lote)
                )
                lote_existente = cursor.fetchone()

                if lote_existente:
                    nueva_cantidad = lote_existente['cantidad'] + cantidad_restante_lote
                    cursor.execute("UPDATE stock SET cantidad = ? WHERE id_stock = ?", (nueva_cantidad, lote_existente['id_stock']))
                else:
                    cursor.execute(
                        "INSERT INTO stock (id_producto, cantidad, fecha_vencimiento, codigo_barras) VALUES (?, ?, ?, ?)",
                        (id_producto, cantidad_restante_lote, fecha_vencimiento, codigo_barras)
                    )
            conn.commit()
            return True
    except sqlite3.Error as e:
        print(f"Error al agregar el lote: {e}")
        return False

def _procesar_ventas_pendientes_post_stock(cursor: sqlite3.Cursor, id_producto: int):
    try:
        stock_disponible = obtener_stock_total_lotes(id_producto, cursor.connection)
        if stock_disponible <= 0:
            return

        detalles_pendientes = obtener_detalles_venta_pendientes(id_producto, cursor.connection)

        for detalle in detalles_pendientes:
            if stock_disponible <= 0:
                break 

            cantidad_a_surtir = min(stock_disponible, detalle.cantidad)

            if cantidad_a_surtir == detalle.cantidad:
                _reducir_stock_de_lotes(cursor, id_producto, cantidad_a_surtir)
                cursor.execute("UPDATE detalle_venta SET estado = 'Completada' WHERE id_detalle = ?", (detalle.id_detalle,))
            else:
                _reducir_stock_de_lotes(cursor, id_producto, cantidad_a_surtir)
                nueva_cantidad_pendiente = detalle.cantidad - cantidad_a_surtir
                cursor.execute("UPDATE detalle_venta SET cantidad = ? WHERE id_detalle = ?", (nueva_cantidad_pendiente, detalle.id_detalle))
            
            stock_disponible -= cantidad_a_surtir

    except sqlite3.Error as e:
        print(f"Error procesando ventas pendientes para el producto {id_producto}: {e}")

def obtener_detalles_venta_pendientes(id_producto, conn=None):
    should_close = False
    if conn is None:
        conn = _get_db_connection()
        should_close = True

    cursor = conn.cursor()
    cursor.execute("""
        SELECT dv.* FROM detalle_venta dv
        JOIN ventas v ON dv.id_venta = v.id_venta
        WHERE dv.id_producto = ? AND dv.estado = 'Pendiente de Stock' AND dv.cantidad > 0
        ORDER BY v.fecha_venta ASC
    """, (id_producto,))
    filas = cursor.fetchall()
    
    if should_close:
        conn.close()
        
    return [DetalleVenta(**dict(fila)) for fila in filas]

def realizar_pago_cliente(id_cliente, monto_pago, fecha_pago):
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO movimientos_cuenta_cliente 
                   (id_cliente, fecha, tipo_movimiento, monto) 
                   VALUES (?, ?, ?, ?)""",
                (id_cliente, fecha_pago, 'PAGO', monto_pago)
            )
            conn.commit()
            return True
    except sqlite3.Error as e:
        print(f"Error al registrar el pago del cliente: {e}")
        return False

def obtener_saldo_deudor_cliente(id_cliente):
    with _get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT IFNULL(SUM(dv.cantidad * p.precio_venta), 0)
            FROM movimientos_cuenta_cliente m
            JOIN detalle_venta dv ON m.id_venta = dv.id_venta
            JOIN productos p ON dv.id_producto = p.id_producto
            WHERE m.id_cliente = ? AND m.tipo_movimiento = 'DEUDA'
        """, (id_cliente,))
        total_deudas_actualizado = cursor.fetchone()[0]

        cursor.execute("""
            SELECT IFNULL(SUM(monto), 0)
            FROM movimientos_cuenta_cliente
            WHERE id_cliente = ? AND tipo_movimiento = 'PAGO'
        """, (id_cliente,))
        total_pagos = cursor.fetchone()[0]

        saldo_final = total_deudas_actualizado - total_pagos
        return saldo_final if saldo_final is not None else 0

def obtener_pagos_recibidos_por_rango(start_date: str, end_date: str):
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT IFNULL(SUM(monto), 0) FROM movimientos_cuenta_cliente
               WHERE tipo_movimiento = 'PAGO' AND DATE(fecha) BETWEEN ? AND ?""",
            (start_date, end_date)
        )
        total_pagos = cursor.fetchone()[0]
        return total_pagos

def obtener_movimientos_cliente(id_cliente):
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                m.fecha,
                m.tipo_movimiento,
                m.id_venta,
                CASE
                    WHEN m.tipo_movimiento = 'PAGO' THEN m.monto
                    ELSE (SELECT SUM(dv.cantidad * p.precio_venta) FROM detalle_venta dv JOIN productos p ON dv.id_producto = p.id_producto WHERE dv.id_venta = m.id_venta)
                END AS monto_actualizado,
                (SELECT GROUP_CONCAT(p.nombre || ' (x' || dv.cantidad || ')', ', ')
                 FROM detalle_venta dv
                 JOIN productos p ON dv.id_producto = p.id_producto
                 WHERE dv.id_venta = m.id_venta) as detalle_productos
            FROM movimientos_cuenta_cliente m
            WHERE m.id_cliente = ? ORDER BY m.fecha DESC, m.id_movimiento DESC""",
            (id_cliente,)
        )
        return [dict(fila) for fila in cursor.fetchall()]

def actualizar_ruta_pdf(id_venta, ruta_pdf):
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE ventas SET ruta_pdf_ticket = ? WHERE id_venta = ?", (ruta_pdf, id_venta))
            conn.commit()
            return True
    except sqlite3.Error as e:
        print(f"Error al actualizar la ruta del PDF: {e}")
        return False

def obtener_venta_por_id(id_venta: int):
    with _get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """SELECT v.*, c.nombre as nombre_cliente 
               FROM ventas v 
               LEFT JOIN cliente c ON v.id_cliente = c.id_cliente
               WHERE v.id_venta = ?""",
            (id_venta,)
        )
        venta_data = cursor.fetchone()

        if not venta_data:
            return None

        v_dict = dict(venta_data)
        venta = Venta(**v_dict)
        venta.ruta_pdf_ticket = v_dict.get('ruta_pdf_ticket')
        venta.nombre_cliente = v_dict['nombre_cliente'] or "Consumidor Final"

        cursor.execute(
            "SELECT * FROM detalle_venta WHERE id_venta = ?",
            (id_venta,)
        )
        detalles_data = cursor.fetchall()
        venta.detalles = [DetalleVenta(**dict(d)) for d in detalles_data]

        return venta

def obtener_ventas_por_rango_de_fechas(start_date: str, end_date: str):
    with _get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """SELECT v.*, c.nombre as nombre_cliente 
               FROM ventas v 
               LEFT JOIN cliente c ON v.id_cliente = c.id_cliente
               WHERE DATE(v.fecha_venta) BETWEEN ? AND ?
               ORDER BY v.fecha_venta DESC""",
            (start_date, end_date)
        )
        ventas_data = cursor.fetchall()

        ventas_dict = {}
        for v_data in ventas_data:
            v_dict = dict(v_data)
            id_venta = v_dict['id_venta']
            venta = Venta(
                id_venta=id_venta,
                fecha_venta=v_dict['fecha_venta'],
                total=v_dict['total'],
                forma_pago=v_dict['forma_pago'],
                id_cliente=v_dict['id_cliente']
            )
            venta.ruta_pdf_ticket = v_dict.get('ruta_pdf_ticket')
            venta.nombre_cliente = v_dict['nombre_cliente'] or "Consumidor Final"
            ventas_dict[id_venta] = venta

        if ventas_dict:
            ids_ventas = tuple(ventas_dict.keys())
            placeholders = ','.join('?' for _ in ids_ventas)
            query = f"SELECT id_detalle, id_venta, id_producto, cantidad, precio_unitario, descuento, estado, subtotal FROM detalle_venta WHERE id_venta IN ({placeholders})"
            cursor.execute(query, ids_ventas)
            detalles_data = cursor.fetchall()
            for d_data in detalles_data:
                detalle_dict = dict(d_data)
                ventas_dict[d_data['id_venta']].detalles.append(DetalleVenta(**detalle_dict))

        return list(ventas_dict.values())

def verificar_usuario(nombre_usuario: str, contrasena: str):
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        
        contrasena_hash = hashlib.sha256(contrasena.encode()).hexdigest()
        
        cursor.execute(
            "SELECT rol FROM usuarios WHERE nombre_usuario = ? AND contrasena_hash = ?",
            (nombre_usuario, contrasena_hash)
        )
        resultado = cursor.fetchone()
        return resultado['rol'] if resultado else None

def cambiar_contrasena_usuario(nombre_usuario: str, contrasena_actual: str, nueva_contrasena: str):
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            contrasena_actual_hash = hashlib.sha256(contrasena_actual.encode()).hexdigest()
            cursor.execute(
                "SELECT id_usuario FROM usuarios WHERE nombre_usuario = ? AND contrasena_hash = ?",
                (nombre_usuario, contrasena_actual_hash)
            )
            usuario = cursor.fetchone()
            
            if not usuario:
                return False

            nueva_contrasena_hash = hashlib.sha256(nueva_contrasena.encode()).hexdigest()
            cursor.execute(
                "UPDATE usuarios SET contrasena_hash = ? WHERE nombre_usuario = ?",
                (nueva_contrasena_hash, nombre_usuario)
            )
            return True
    except sqlite3.Error as e:
        print(f"Error al cambiar la contraseña: {e}")
        return False

def obtener_productos_por_ids(product_ids: list):
    if not product_ids:
        return []

    with _get_db_connection() as conn:
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in product_ids)
        query = f"""
            SELECT
                p.id_producto, p.nombre, p.precio_venta, p.volumen, p.codigo_barras, p.descripcion, p.stock_sin_lote,
                (IFNULL(SUM(s.cantidad), 0) + p.stock_sin_lote) as cantidad_stock
            FROM
                productos p
            LEFT JOIN
                stock s ON p.id_producto = s.id_producto
            WHERE p.id_producto IN ({placeholders})
            GROUP BY p.id_producto, p.nombre, p.precio_venta, p.volumen, p.codigo_barras, p.descripcion, p.stock_sin_lote
        """
        cursor.execute(query, product_ids)
        filas = cursor.fetchall()
        return [Producto(**dict(fila)) for fila in filas]

def obtener_sugerencias_reposicion(dias_analisis=30, dias_cobertura=15):
    fecha_inicio = datetime.now() - timedelta(days=dias_analisis)
    fecha_inicio_str = fecha_inicio.strftime('%Y-%m-%d %H:%M:%S')

    query = """
    SELECT
        p.id_producto, p.nombre, (IFNULL(total_stock.stock_lotes, 0) + p.stock_sin_lote) AS stock_actual,
        COALESCE(v.total_vendido, 0) AS ventas_periodo,
        COALESCE(CAST(v.total_vendido AS REAL) / ?, 0) AS venta_diaria_prom,
        COALESCE(CAST(v.total_vendido AS REAL) / ?, 0) * ? AS stock_sugerido,
        ROUND(MAX(0, (COALESCE(CAST(v.total_vendido AS REAL) / ?, 0) * ?) - (IFNULL(total_stock.stock_lotes, 0) + p.stock_sin_lote))) AS cantidad_a_comprar
    FROM
        productos p
    LEFT JOIN (
        SELECT id_producto, SUM(cantidad) as stock_lotes FROM stock GROUP BY id_producto
    ) total_stock ON p.id_producto = total_stock.id_producto
    LEFT JOIN (
        SELECT dv.id_producto, SUM(dv.cantidad) AS total_vendido, MIN(v.fecha_venta) as primera_venta
        FROM detalle_venta dv
        JOIN ventas v ON dv.id_venta = v.id_venta
        WHERE v.fecha_venta >= ?
        GROUP BY dv.id_producto
    ) v ON p.id_producto = v.id_producto    
    GROUP BY p.id_producto, p.nombre, p.stock_sin_lote
    HAVING cantidad_a_comprar > 0 OR (stock_actual < 5 AND ventas_periodo > 0) 
    ORDER BY
        cantidad_a_comprar DESC;
    """
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            dias_analisis_float = max(float(dias_analisis), 1.0)
            params = (dias_analisis_float, dias_analisis_float, dias_cobertura, dias_analisis_float, dias_cobertura, fecha_inicio_str)
            cursor.execute(query, params)
            sugerencias = cursor.fetchall()
            return sugerencias
    except sqlite3.Error as e:
        print(f"Error al obtener sugerencias de reposición: {e}")
        return []

def crear_backup_seguro(ruta_backup: str):
    conn_origen = _get_db_connection()
    conn_destino = sqlite3.connect(ruta_backup)

    try:
        with conn_destino:
            conn_origen.backup(conn_destino, pages=1, progress=None)
        return True
    except sqlite3.Error as e:
        print(f"Error durante el backup de SQLite: {e}")
        return False
    finally:
        conn_origen.close()
        conn_destino.close()
