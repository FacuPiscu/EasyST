import sqlite3
import hashlib
import configparser
import sys
import os
from models import Producto, Venta, Cliente, DetalleVenta
from datetime import datetime, timedelta
import unicodedata

def get_persistent_path(filename):
    """
    Obtiene una ruta persistente para archivos de datos como la base de datos.
    - En modo desarrollo, usa la carpeta actual.
    - En modo empaquetado (PyInstaller), usa la carpeta donde está el .exe.
    """
    if getattr(sys, 'frozen', False):
        # Estamos en modo empaquetado (.exe)
        application_path = os.path.dirname(sys.executable)
    else:
        # Estamos en modo desarrollo (ejecutando el .py)
        application_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(application_path, filename)

def resource_path(relative_path):
    """ Obtiene la ruta absoluta al recurso, funciona para desarrollo y para PyInstaller """
    try:
        # PyInstaller crea una carpeta temporal y guarda la ruta en _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

# Nombre del archivo de la base de datos
DB_FILE = get_persistent_path('easyst.db')
# El código SQL para la creación de las tablas
SQL_SCRIPT = """
-- 1. TABLA PRODUCTOS
CREATE TABLE IF NOT EXISTS productos (
    id_producto INTEGER PRIMARY KEY NOT NULL, 
    nombre TEXT NOT NULL,
    precio_venta REAL NOT NULL,
    volumen REAL,
    codigo_barras TEXT UNIQUE,
    descripcion TEXT,
    stock_sin_lote INTEGER NOT NULL DEFAULT 0 -- Para manejar ventas negativas
);

-- 2. TABLA STOCK
-- Se modifica para soportar lotes con fechas de vencimiento.
-- Un producto puede tener varias entradas en esta tabla, una por cada lote/vencimiento.
CREATE TABLE IF NOT EXISTS stock (
    id_stock INTEGER PRIMARY KEY AUTOINCREMENT,
    id_producto INTEGER NOT NULL, 
    cantidad INTEGER NOT NULL,
    fecha_vencimiento TEXT, -- Formato YYYY-MM-DD
    codigo_barras TEXT,
    FOREIGN KEY (id_producto) REFERENCES productos(id_producto) ON DELETE CASCADE
);

-- 3. TABLA CLIENTE
CREATE TABLE IF NOT EXISTS cliente (
    id_cliente INTEGER PRIMARY KEY, 
    nombre TEXT NOT NULL,
    dni TEXT UNIQUE,    
    fecha_limite_pago TEXT
);

-- 4. TABLA VENTAS
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

-- 5. TABLA DETALLE_VENTA
CREATE TABLE IF NOT EXISTS detalle_venta (
    id_detalle INTEGER PRIMARY KEY AUTOINCREMENT,
    id_venta INTEGER NOT NULL,
    id_producto INTEGER NOT NULL,
    cantidad INTEGER NOT NULL,
    precio_unitario REAL NOT NULL,
    descuento REAL NOT NULL DEFAULT 0,
    estado TEXT NOT NULL DEFAULT 'Completada', -- 'Completada' o 'Pendiente de Stock'
    subtotal REAL NOT NULL,
    FOREIGN KEY (id_venta) REFERENCES ventas(id_venta),
    FOREIGN KEY (id_producto) REFERENCES productos(id_producto)
);

-- 6. TABLA USUARIOS
CREATE TABLE IF NOT EXISTS usuarios (
    id_usuario INTEGER PRIMARY KEY,
    nombre_usuario TEXT UNIQUE NOT NULL,
    contrasena_hash TEXT NOT NULL,
    rol TEXT NOT NULL -- 'Administrador' o 'Cajero'
);

-- 7. TABLA MOVIMIENTOS CUENTA CLIENTE (NUEVA)
-- Reemplaza el campo 'saldo_deudor' para un control detallado.
CREATE TABLE IF NOT EXISTS movimientos_cuenta_cliente (
    id_movimiento INTEGER PRIMARY KEY AUTOINCREMENT,
    id_cliente INTEGER NOT NULL,
    id_venta INTEGER, -- NULL para pagos, con valor para deudas
    fecha TEXT NOT NULL,
    tipo_movimiento TEXT NOT NULL, -- 'DEUDA' o 'PAGO'
    monto REAL NOT NULL,
    FOREIGN KEY (id_cliente) REFERENCES cliente(id_cliente) ON DELETE CASCADE
);
-- 7. ÍNDICES PARA OPTIMIZAR BÚSQUEDAS
-- Aceleran las búsquedas a medida que la base de datos crece.

-- Índice para buscar productos por nombre
CREATE INDEX IF NOT EXISTS idx_productos_nombre ON productos (nombre);

-- Índice para buscar clientes por nombre
CREATE INDEX IF NOT EXISTS idx_cliente_nombre ON cliente (nombre);

-- Índice para filtrar ventas por fecha (muy importante para los reportes)
CREATE INDEX IF NOT EXISTS idx_ventas_fecha ON ventas (fecha_venta);

-- Índices en claves foráneas para acelerar las uniones de tablas (joins)
CREATE INDEX IF NOT EXISTS idx_detalle_venta_id_venta ON detalle_venta (id_venta);
CREATE INDEX IF NOT EXISTS idx_detalle_venta_id_producto ON detalle_venta (id_producto);
CREATE INDEX IF NOT EXISTS idx_movimientos_id_cliente ON movimientos_cuenta_cliente (id_cliente);
"""

# Versión actual del esquema de la base de datos.
# Incrementar este número cada vez que se modifique la estructura de las tablas.
LATEST_SCHEMA_VERSION = 5

# --- MIGRACIONES ---
# Aquí se definen los cambios para pasar de una versión del esquema a la siguiente.
# La clave del diccionario es la versión a la que se va a migrar.
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
    """
    Usa una conexión existente para crear las tablas si no existen y ejecutar
    las migraciones necesarias para actualizar el esquema a la última versión.
    Si no se provee una conexión, crea una nueva y la cierra al finalizar.
    """
    conn_provided = conexion is not None
    conn = conexion if conn_provided else _get_db_connection()

    try:
        cursor = conn.cursor()

        # 1. Crear las tablas si la base de datos es nueva
        cursor.executescript(SQL_SCRIPT)
        conn.commit()

        # 2. Obtener la versión actual del esquema de la BD
        cursor.execute("PRAGMA user_version")
        current_version = cursor.fetchone()[0]
        
        # 3. Si la base de datos es nueva (versión 0), establecer la versión más reciente directamente.
        if current_version == 0:
            print(f"Base de datos nueva detectada. Estableciendo esquema a la versión {LATEST_SCHEMA_VERSION}.")
            cursor.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")
            conn.commit()
        # Si la base de datos es antigua (pero no nueva), aplicar migraciones pendientes.
        # La condición 'current_version > 0' evita que se intenten migraciones en una BD recién creada.
        elif current_version < LATEST_SCHEMA_VERSION:
            print(f"Versión de la BD: {current_version}. Actualizando a: {LATEST_SCHEMA_VERSION}...")
            for version in range(current_version + 1, LATEST_SCHEMA_VERSION + 1):
                if version in MIGRATIONS:
                    print(f"Aplicando migración para la versión {version}...")
                    cursor.executescript(MIGRATIONS[version])
                    print(f"Migración a la versión {version} completada.")
                    # Actualizar la versión DESPUÉS de cada migración exitosa
                    cursor.execute(f"PRAGMA user_version = {version}")
                    conn.commit()

        # 4. Crear el usuario admin por defecto si es necesario
        _crear_usuario_admin_default(conn)
        print(f"Base de datos '{DB_FILE}' conectada y verificada con éxito. Versión del esquema: {LATEST_SCHEMA_VERSION}.")
    except sqlite3.Error as e:
        print(f"Ocurrió un error en SQLite: {e}")
    finally:
        # Solo cerramos la conexión si la creamos dentro de esta función.
        if not conn_provided and conn:
            conn.close()

def _crear_usuario_admin_default(conexion: sqlite3.Connection):
    """
    Crea el usuario administrador por defecto si la tabla de usuarios está vacía.
    Usa la conexión existente para evitar problemas transaccionales.
    """
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
    """Crea y retorna una conexión a la base de datos."""
    conn = sqlite3.connect(DB_FILE)
    # Esto permite acceder a las columnas por su nombre
    conn.row_factory = sqlite3.Row
    return conn

def _normalizar_texto(texto: str) -> str:
    """
    Elimina acentos y convierte a minúsculas para búsquedas flexibles.
    Ej: 'Café' -> 'cafe'
    """
    return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn').lower()

def obtener_productos(nombre_like=None, solo_poco_stock=False, umbral_stock=5):
    """
    Obtiene una lista de objetos Producto desde la base de datos.
    Permite filtrar por nombre y por bajo stock.
    """
    conn = _get_db_connection()
    cursor = conn.cursor()
    try:
        # Consulta optimizada que ahora también calcula el número de lotes y el vencimiento próximo.
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
            # Asignamos los nuevos campos al objeto Producto
            # El constructor de Producto ya maneja estos campos, pero nos aseguramos de que se pasen.
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
            # Asignamos los campos calculados por separado para mayor claridad
            producto.num_lotes = prod_dict.get('num_lotes', 0) or 0
            producto.vencimiento_proximo = prod_dict.get('vencimiento_proximo')
            productos.append(producto)

        # Filtrado en Python después de obtener los datos para manejar acentos correctamente
        if nombre_like:
            productos = [p for p in productos if _normalizar_texto(nombre_like) in _normalizar_texto(p.nombre)]

        if solo_poco_stock:
            productos = [p for p in productos if p.cantidad_stock <= umbral_stock]

        return productos
    except sqlite3.Error as e:
        print(f"Error al obtener productos: {e}")
        return []

def agregar_producto(producto: Producto):
    """
    Agrega un nuevo producto y su stock a la base de datos dentro de una transacción.
    Retorna el ID del nuevo producto.
    """
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO productos (nombre, precio_venta, volumen, codigo_barras, descripcion, stock_sin_lote) VALUES (?, ?, ?, ?, ?, ?)",
                (producto.nombre, producto.precio_venta, producto.volumen, producto.codigo_barras, producto.descripcion, 0) # Inicia en 0
            )
            id_producto_nuevo = cursor.lastrowid
    
            # Insertar el lote inicial en la tabla stock. La fecha de vencimiento es opcional.
            cursor.execute(
                "INSERT INTO stock (id_producto, cantidad, fecha_vencimiento) VALUES (?, ?, ?)",
                (id_producto_nuevo, producto.cantidad_stock, producto.fecha_vencimiento if hasattr(producto, 'fecha_vencimiento') else None)
            )
            return id_producto_nuevo
    except sqlite3.Error as e: # type: ignore
        print(f"Error al agregar producto: {e}")
        return None

def obtener_clientes(nombre_o_dni=None, solo_con_deuda=False):
    """
    Obtiene una lista de objetos Cliente, opcionalmente filtrando por nombre/DNI y/o si tienen deuda.
    OPTIMIZADO: Calcula los saldos en la misma consulta SQL para evitar el problema N+1.
    """
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Consulta optimizada con subconsultas para calcular el saldo al vuelo
        query = """
            SELECT 
                c.*,
                (
                    -- Subconsulta para Deudas (con lógica inflacionaria: precio actual)
                    IFNULL((
                        SELECT SUM(dv.cantidad * p.precio_venta)
                        FROM movimientos_cuenta_cliente m
                        JOIN detalle_venta dv ON m.id_venta = dv.id_venta
                        JOIN productos p ON dv.id_producto = p.id_producto
                        WHERE m.id_cliente = c.id_cliente AND m.tipo_movimiento = 'DEUDA'
                    ), 0) 
                    - 
                    -- Subconsulta para Pagos
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
            # Creamos el objeto Cliente usando los datos de la fila
            # Mapeamos explícitamente 'saldo_calculado' al atributo 'saldo_deudor'
            c = Cliente(
                id_cliente=fila['id_cliente'],
                nombre=fila['nombre'],
                dni=fila['dni'],
                fecha_limite_pago=fila['fecha_limite_pago'],
                saldo_deudor=fila['saldo_calculado']
            )
            clientes.append(c)

        # --- BÚSQUEDA FLEXIBLE (SIN ACENTOS) ---
        # Mantenemos el filtrado en Python para soportar la normalización de acentos
        if nombre_o_dni:
            termino_busqueda = _normalizar_texto(nombre_o_dni)
            clientes = [c for c in clientes if 
                        termino_busqueda in _normalizar_texto(c.nombre) or 
                        (c.dni and termino_busqueda in c.dni)]

        # Filtrado por deuda
        if solo_con_deuda:
            clientes = [c for c in clientes if c.saldo_deudor > 0]

        return clientes

def obtener_cliente_por_id(id_cliente):
    """Busca y retorna un objeto Cliente por su ID."""
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
    """Agrega un nuevo cliente a la base de datos."""
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
    """Actualiza los datos de un cliente en la base de datos."""
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
    """Busca y retorna un objeto Producto por su código de barras."""
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM productos WHERE codigo_barras = ?", (codigo_barras,))
        fila = cursor.fetchone()
        if fila:
            producto = Producto(**dict(fila))
            # Obtenemos el stock total por separado
            producto.cantidad_stock = obtener_stock_total_lotes(producto.id_producto) + producto.stock_sin_lote
            return producto
        return None

def obtener_producto_por_id(id_producto):
    """Busca y retorna un objeto Producto por su ID."""
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM productos WHERE id_producto = ?", (id_producto,))
        fila = cursor.fetchone()
        if fila:
            producto = Producto(**dict(fila))
            # Obtenemos el stock total por separado
            producto.cantidad_stock = obtener_stock_total_lotes(producto.id_producto) + producto.stock_sin_lote
            return producto
        return None

def obtener_producto_por_nombre(nombre):
    """Busca y retorna un objeto Producto por su nombre exacto."""
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        # Usamos una comparación exacta y sensible a mayúsculas/minúsculas
        cursor.execute("SELECT * FROM productos WHERE nombre = ?", (nombre,))
        fila = cursor.fetchone()
        if fila:
            producto = Producto(**dict(fila))
            # Obtenemos el stock total por separado
            producto.cantidad_stock = obtener_stock_total_lotes(producto.id_producto) + producto.stock_sin_lote
            return producto
        return None

def actualizar_producto(producto: Producto):
    """
    Actualiza los datos de un producto y su stock en la base de datos.
    """
    conn = _get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            # Actualizar tabla productos
            cursor.execute(
                """UPDATE productos 
                   SET nombre = ?, precio_venta = ?, volumen = ?, codigo_barras = ?, descripcion = ?, stock_sin_lote = ?
                   WHERE id_producto = ?""",
                (producto.nombre, producto.precio_venta, producto.volumen, producto.codigo_barras, producto.descripcion, producto.stock_sin_lote, producto.id_producto)
            )
            # La gestión de lotes (stock) se hará desde una ventana dedicada,
            # por lo que aquí solo actualizamos los datos generales del producto.
            # La cantidad de stock ya no se actualiza directamente aquí.
            return True
    except sqlite3.Error as e:
        print(f"Error al actualizar producto: {e}")
        conn.rollback()
        return False

def registrar_venta(venta: 'Venta'):
    """
    Registra una venta completa (cabecera y detalles) y actualiza el stock
    de los productos vendidos dentro de una única transacción.
    """
    # Cargar la configuración aquí para acceder a la opción de stock negativo
    conn = _get_db_connection()
    try:
        with conn:
            config = configparser.ConfigParser()
            config.read(resource_path('config.ini'))
            PERMITIR_STOCK_NEGATIVO = config.getboolean('Negocio', 'PermitirStockNegativo', fallback=False)

            cursor = conn.cursor()
            # 1. Insertar la cabecera de la venta
            cursor.execute(
                "INSERT INTO ventas (fecha_venta, total, forma_pago, observaciones, id_cliente) VALUES (?, ?, ?, ?, ?)",
                (venta.fecha_venta, venta.total, venta.forma_pago, venta.observaciones, venta.id_cliente)
            )
            id_venta_nueva = cursor.lastrowid

            # Si la venta es en cuenta corriente, registrar el movimiento de DEUDA
            if venta.forma_pago == 'Libreta' and venta.id_cliente is not None:
                cursor.execute(
                    """INSERT INTO movimientos_cuenta_cliente 
                       (id_cliente, id_venta, fecha, tipo_movimiento, monto) 
                       VALUES (?, ?, ?, ?, ?)""",
                    (venta.id_cliente, id_venta_nueva, venta.fecha_venta, 'DEUDA', venta.total)
                )


            # 2. Insertar los detalles y actualizar el stock
            for detalle in venta.detalles:
                # Siempre se registra el detalle como completado
                detalle.estado = "Completada"
                cursor.execute(
                    "INSERT INTO detalle_venta (id_venta, id_producto, cantidad, precio_unitario, descuento, subtotal, estado) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (id_venta_nueva, detalle.id_producto, detalle.cantidad, detalle.precio_unitario, detalle.descuento, detalle.subtotal, detalle.estado)
                )

                # Obtenemos el stock_sin_lote usando el cursor actual para garantizar consistencia transaccional
                cursor.execute("SELECT stock_sin_lote FROM productos WHERE id_producto = ?", (detalle.id_producto,))
                stock_sin_lote_actual = cursor.fetchone()[0]

                stock_en_lotes = _obtener_stock_total_lotes_con_cursor(cursor, detalle.id_producto)
                cantidad_a_vender = detalle.cantidad
                stock_total_disponible = stock_en_lotes + stock_sin_lote_actual

                if not PERMITIR_STOCK_NEGATIVO and stock_total_disponible < cantidad_a_vender:
                    # El rollback es automático gracias al 'with conn:'
                    raise sqlite3.IntegrityError(
                        f"Stock insuficiente para el producto ID {detalle.id_producto}. Se requieren {cantidad_a_vender} y hay {stock_total_disponible}."
                    )
                
                # Reducir de los lotes lo que se pueda
                cantidad_a_reducir_de_lotes = min(cantidad_a_vender, stock_en_lotes)
                if cantidad_a_reducir_de_lotes > 0:
                    _reducir_stock_de_lotes(cursor, detalle.id_producto, cantidad_a_reducir_de_lotes)

                # Si la venta supera el stock en lotes, la diferencia se va al stock_sin_lote
                diferencia = cantidad_a_vender - stock_en_lotes
                if diferencia > 0:
                    cursor.execute(
                        "UPDATE productos SET stock_sin_lote = stock_sin_lote - ? WHERE id_producto = ?",
                        (diferencia, detalle.id_producto)
                    )

            return id_venta_nueva
    except sqlite3.IntegrityError:
        # Si es un IntegrityError (lanzado por nosotros por falta de stock),
        # simplemente lo relanzamos para que la capa superior lo maneje.
        raise
    except sqlite3.Error as e: # Capturamos otros errores de SQLite
        print(f"Error al registrar la venta: {e}")
        conn.rollback()
        return None

def _reducir_stock_de_lotes(cursor, id_producto, cantidad_a_descontar):
    """Función interna para descontar stock de lotes (FIFO/FEFO). No maneja transacciones."""
    # 1. Obtener todos los lotes del producto, ordenados por vencimiento (los más antiguos primero)
    cursor.execute(
        """SELECT id_stock, cantidad FROM stock WHERE id_producto = ? AND cantidad > 0
           ORDER BY IFNULL(fecha_vencimiento, '9999-12-31') ASC, id_stock ASC""",
        (id_producto,)
    )
    lotes_disponibles = cursor.fetchall()

    # 2. Iterar sobre los lotes y descontar la cantidad
    for lote in lotes_disponibles:
        if cantidad_a_descontar <= 0:
            break # Ya se descontó todo

        id_lote_actual = lote['id_stock']
        cantidad_en_lote = lote['cantidad']

        if cantidad_a_descontar >= cantidad_en_lote:
            # El lote se vacía completamente
            cursor.execute("UPDATE stock SET cantidad = 0 WHERE id_stock = ?", (id_lote_actual,))
            cantidad_a_descontar -= cantidad_en_lote
        elif cantidad_a_descontar > 0:
            # El lote tiene stock suficiente, se descuenta parcialmente
            nueva_cantidad = cantidad_en_lote - cantidad_a_descontar
            cursor.execute("UPDATE stock SET cantidad = ? WHERE id_stock = ?", (nueva_cantidad, id_lote_actual))
            cantidad_a_descontar = 0
        
        if cantidad_a_descontar == 0:
            break # Salimos del bucle SOLO si ya no queda nada por descontar.

def obtener_lotes_por_producto(id_producto):
    """Obtiene todos los lotes de stock para un producto específico."""
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id_stock, cantidad, fecha_vencimiento, codigo_barras FROM stock WHERE id_producto = ? AND cantidad != 0 ORDER BY fecha_vencimiento ASC",
            (id_producto,)
        )
        filas = cursor.fetchall()
        # Devolvemos una lista de diccionarios para facilitar su manejo
        return [dict(fila) for fila in filas]

def _obtener_stock_total_lotes_con_cursor(cursor: sqlite3.Cursor, id_producto: int) -> int:
    """
    Función interna que calcula el stock total en lotes usando un cursor existente.
    Esto es crucial para la consistencia transaccional.
    """
    cursor.execute(
        "SELECT IFNULL(SUM(cantidad), 0) FROM stock WHERE id_producto = ?",
        (id_producto,)
    )
    return cursor.fetchone()[0]

def obtener_stock_total_lotes(id_producto):
    """Calcula y devuelve la suma total de stock de TODOS LOS LOTES para un producto."""
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT IFNULL(SUM(cantidad), 0) FROM stock WHERE id_producto = ?",
            (id_producto,)
        )
        total = cursor.fetchone()[0]
        return total

def actualizar_lote(id_stock, cantidad, fecha_vencimiento, codigo_barras):
    """Actualiza un lote de stock específico."""
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
    """
    Agrega un nuevo lote de stock. Si ya existe un lote para el mismo producto
    y con la misma fecha de vencimiento (y código de barras si se provee), actualiza la cantidad.
    Primero salda la deuda de 'stock_sin_lote' si existe.
    """
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            # 1. Obtener el stock "virtual" negativo (deuda)
            cursor.execute("SELECT stock_sin_lote FROM productos WHERE id_producto = ?", (id_producto,))
            stock_deuda = cursor.fetchone()[0]

            cantidad_restante_lote = cantidad
            if stock_deuda < 0:
                # Hay una deuda que saldar
                a_saldar = abs(stock_deuda)
                if cantidad_restante_lote >= a_saldar:
                    # El lote cubre toda la deuda
                    cursor.execute("UPDATE productos SET stock_sin_lote = 0 WHERE id_producto = ?", (id_producto,))
                    cantidad_restante_lote -= a_saldar
                else:
                    # El lote cubre parte de la deuda
                    cursor.execute("UPDATE productos SET stock_sin_lote = stock_sin_lote + ? WHERE id_producto = ?", (cantidad_restante_lote, id_producto))
                    cantidad_restante_lote = 0

            # 2. Si queda cantidad en el lote, se agrega al stock físico
            if cantidad_restante_lote > 0:
                # Buscar lote existente con la misma fecha de vencimiento para consolidar
                # Manejamos correctamente los valores NULL en fecha_vencimiento.
                # El operador '=' no funciona con NULL, se debe usar 'IS'. Lo mismo para codigo_barras.
                
                query_buscar_lote = "SELECT id_stock, cantidad FROM stock WHERE id_producto = ?"
                params_buscar_lote = [id_producto]

                if fecha_vencimiento is not None:
                    query_buscar_lote += " AND fecha_vencimiento = ?"
                    params_buscar_lote.append(fecha_vencimiento)
                else:
                    query_buscar_lote += " AND fecha_vencimiento IS NULL"
                
                cursor.execute(
                    query_buscar_lote, # La consulta SQL
                    tuple(params_buscar_lote) # Los parámetros que coinciden con la consulta
                )
                lote_existente = cursor.fetchone()

                if lote_existente:
                    # Si encontramos un lote con la misma fecha, simplemente actualizamos su cantidad.
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
    """
    Función auxiliar que se ejecuta después de añadir stock para cumplir con
    los pedidos pendientes de un producto. REUTILIZA el cursor de la función que la llama.
    """
    try:
        stock_disponible = obtener_stock_total_lotes(id_producto, cursor.connection)
        if stock_disponible <= 0:
            return

        detalles_pendientes = obtener_detalles_venta_pendientes(id_producto, cursor.connection)

        for detalle in detalles_pendientes:
            if stock_disponible <= 0:
                break # No queda más stock para surtir

            cantidad_a_surtir = min(stock_disponible, detalle.cantidad)

            if cantidad_a_surtir == detalle.cantidad:
                # Se puede completar todo el detalle pendiente
                _reducir_stock_de_lotes(cursor, id_producto, cantidad_a_surtir)
                cursor.execute("UPDATE detalle_venta SET estado = 'Completada' WHERE id_detalle = ?", (detalle.id_detalle,))
            else:
                # Se puede completar una parte del detalle pendiente
                _reducir_stock_de_lotes(cursor, id_producto, cantidad_a_surtir)
                # Actualizamos la cantidad pendiente
                nueva_cantidad_pendiente = detalle.cantidad - cantidad_a_surtir
                cursor.execute("UPDATE detalle_venta SET cantidad = ? WHERE id_detalle = ?", (nueva_cantidad_pendiente, detalle.id_detalle))
            
            stock_disponible -= cantidad_a_surtir

    except sqlite3.Error as e:
        print(f"Error procesando ventas pendientes para el producto {id_producto}: {e}")
        # No hacemos rollback aquí, dejamos que la función principal lo maneje.

def obtener_detalles_venta_pendientes(id_producto, conn=None):
    """
    Obtiene los detalles de venta pendientes para un producto, ordenados por fecha.
    Permite reutilizar una conexión existente.
    """
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
    """Registra un PAGO en la cuenta corriente de un cliente."""
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
    """Calcula el saldo deudor actual de un cliente sumando deudas y restando pagos."""
    with _get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Calcular el total de las deudas a precios actualizados
        cursor.execute("""
            SELECT IFNULL(SUM(dv.cantidad * p.precio_venta), 0)
            FROM movimientos_cuenta_cliente m
            JOIN detalle_venta dv ON m.id_venta = dv.id_venta
            JOIN productos p ON dv.id_producto = p.id_producto
            WHERE m.id_cliente = ? AND m.tipo_movimiento = 'DEUDA'
        """, (id_cliente,))
        total_deudas_actualizado = cursor.fetchone()[0]

        # 2. Calcular el total de los pagos realizados
        cursor.execute("""
            SELECT IFNULL(SUM(monto), 0)
            FROM movimientos_cuenta_cliente
            WHERE id_cliente = ? AND tipo_movimiento = 'PAGO'
        """, (id_cliente,))
        total_pagos = cursor.fetchone()[0]

        # 3. El saldo es la diferencia
        saldo_final = total_deudas_actualizado - total_pagos
        return saldo_final if saldo_final is not None else 0

def obtener_pagos_recibidos_por_rango(start_date: str, end_date: str):
    """
    Calcula la suma total de los pagos de deudas de clientes recibidos
    dentro de un rango de fechas específico.
    """
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
    """
    Obtiene el historial de movimientos (deudas y pagos) de la cuenta de un cliente.
    Incluye el detalle de los productos para los movimientos de deuda.
    """
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        # Esta consulta une los movimientos con las ventas y los detalles para obtener la descripción.
        # Usamos GROUP_CONCAT para juntar todos los productos de una venta en una sola cadena.
        cursor.execute(
            """
            SELECT
                m.fecha,
                m.tipo_movimiento,
                m.id_venta,
                -- Si es un PAGO, usamos el monto fijo. Si es DEUDA, recalculamos el total con precios actuales.
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
    """Guarda la ruta del archivo PDF del ticket para una venta específica."""
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
    """
    Obtiene una única venta por su ID, incluyendo todos sus detalles.
    Es más eficiente que buscar en un rango de fechas grande.
    """
    with _get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Obtener la cabecera de la venta
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

        # 2. Crear el objeto Venta
        v_dict = dict(venta_data)
        venta = Venta(**v_dict)
        venta.ruta_pdf_ticket = v_dict.get('ruta_pdf_ticket')
        venta.nombre_cliente = v_dict['nombre_cliente'] or "Consumidor Final"

        # 3. Obtener los detalles de esa venta
        cursor.execute(
            "SELECT * FROM detalle_venta WHERE id_venta = ?",
            (id_venta,)
        )
        detalles_data = cursor.fetchall()
        venta.detalles = [DetalleVenta(**dict(d)) for d in detalles_data]

        return venta

def obtener_ventas_por_rango_de_fechas(start_date: str, end_date: str):
    """
    Obtiene todas las ventas dentro de un rango de fechas.
    Las fechas deben estar en formato 'YYYY-MM-DD'.
    Retorna una lista de objetos Venta, cada uno con sus detalles.
    """
    with _get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Obtener las cabeceras de las ventas para la fecha dada
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
            # Creamos el objeto Venta y lo guardamos en un diccionario
            venta = Venta(
                id_venta=id_venta,
                fecha_venta=v_dict['fecha_venta'],
                total=v_dict['total'],
                forma_pago=v_dict['forma_pago'],
                id_cliente=v_dict['id_cliente']
            )
            venta.ruta_pdf_ticket = v_dict.get('ruta_pdf_ticket') # Usamos .get() para evitar errores si la columna no existe
            venta.nombre_cliente = v_dict['nombre_cliente'] or "Consumidor Final"
            ventas_dict[id_venta] = venta

        # 2. Obtener todos los detalles de esas ventas en una sola consulta
        if ventas_dict:
            ids_ventas = tuple(ventas_dict.keys())
            placeholders = ','.join('?' for _ in ids_ventas)
            query = f"SELECT id_detalle, id_venta, id_producto, cantidad, precio_unitario, descuento, estado, subtotal FROM detalle_venta WHERE id_venta IN ({placeholders})"
            cursor.execute(query, ids_ventas)
            detalles_data = cursor.fetchall()
            for d_data in detalles_data:
                detalle_dict = dict(d_data)
                # El constructor de DetalleVenta espera id_detalle, lo pasamos
                # y el constructor se encargará de los demás campos.
                ventas_dict[d_data['id_venta']].detalles.append(DetalleVenta(**detalle_dict))

        return list(ventas_dict.values())

def verificar_usuario(nombre_usuario: str, contrasena: str):
    """
    Verifica las credenciales del usuario contra la base de datos.
    Retorna el rol del usuario si las credenciales son correctas, de lo contrario None.
    """
    with _get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Hashear la contraseña ingresada para compararla con la almacenada
        contrasena_hash = hashlib.sha256(contrasena.encode()).hexdigest()
        
        cursor.execute(
            "SELECT rol FROM usuarios WHERE nombre_usuario = ? AND contrasena_hash = ?",
            (nombre_usuario, contrasena_hash)
        )
        resultado = cursor.fetchone()
        return resultado['rol'] if resultado else None

def cambiar_contrasena_usuario(nombre_usuario: str, contrasena_actual: str, nueva_contrasena: str):
    """
    Cambia la contraseña de un usuario si la contraseña actual es correcta.
    Retorna True si el cambio fue exitoso, False en caso contrario.
    """
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            # 1. Verificar la contraseña actual
            contrasena_actual_hash = hashlib.sha256(contrasena_actual.encode()).hexdigest()
            cursor.execute(
                "SELECT id_usuario FROM usuarios WHERE nombre_usuario = ? AND contrasena_hash = ?",
                (nombre_usuario, contrasena_actual_hash)
            )
            usuario = cursor.fetchone()
            
            if not usuario:
                return False # Contraseña actual incorrecta

            # 2. Si la contraseña es correcta, actualizar con la nueva
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
    """
    Obtiene una lista de objetos Producto por sus IDs, incluyendo el stock total.
    Optimizado para obtener múltiples productos en una sola consulta.
    """
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
    """
    Analiza las ventas de los últimos 'dias_analisis' para sugerir la compra de productos
    para cubrir los próximos 'dias_cobertura', considerando el stock actual.

    Returns:
        Una lista de tuplas con los datos de los productos a reponer.
        Formato: (id, nombre, stock_actual, ventas_periodo, venta_diaria_prom, stock_sugerido, cantidad_a_comprar)
    """
        # Fecha de inicio para el análisis de ventas
    fecha_inicio = datetime.now() - timedelta(days=dias_analisis)
    fecha_inicio_str = fecha_inicio.strftime('%Y-%m-%d %H:%M:%S')

    # Consulta SQL para obtener ventas por producto, stock actual y calcular sugerencias
    # - Obtenemos el total vendido de cada producto en el período de análisis.
    # - Calculamos la venta diaria promedio.
    # - Calculamos el stock objetivo para el período de cobertura.
    # - Calculamos la cantidad a comprar (si el stock actual es menor al objetivo).
    query = """
    SELECT
        p.id_producto, p.nombre, (IFNULL(total_stock.stock_lotes, 0) + p.stock_sin_lote) AS stock_actual,
        COALESCE(v.total_vendido, 0) AS ventas_periodo,
        -- El promedio se calcula dividiendo las ventas del período por los días de análisis.
        -- Usamos un parámetro (?) en lugar de JULIANDAY para consistencia en las pruebas.
        COALESCE(CAST(v.total_vendido AS REAL) / ?, 0) AS venta_diaria_prom,
        COALESCE(CAST(v.total_vendido AS REAL) / ?, 0) * ? AS stock_sugerido,
        ROUND(MAX(0, (COALESCE(CAST(v.total_vendido AS REAL) / ?, 0) * ?) - (IFNULL(total_stock.stock_lotes, 0) + p.stock_sin_lote))) AS cantidad_a_comprar
    FROM
        productos p
    LEFT JOIN (
        SELECT id_producto, SUM(cantidad) as stock_lotes FROM stock GROUP BY id_producto
    ) total_stock ON p.id_producto = total_stock.id_producto
    LEFT JOIN ( -- Esta subconsulta ahora se une correctamente
        SELECT dv.id_producto, SUM(dv.cantidad) AS total_vendido, MIN(v.fecha_venta) as primera_venta
        FROM detalle_venta dv
        JOIN ventas v ON dv.id_venta = v.id_venta
        WHERE v.fecha_venta >= ?
        GROUP BY dv.id_producto
    ) v ON p.id_producto = v.id_producto    
    GROUP BY p.id_producto, p.nombre, p.stock_sin_lote
    -- Sugerir si se necesita comprar O si, teniendo stock bajo (<5), ha tenido ventas en el período.
    HAVING cantidad_a_comprar > 0 OR (stock_actual < 5 AND ventas_periodo > 0) 
    ORDER BY
        cantidad_a_comprar DESC;
    """
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            # Usamos MAX(dias_analisis, 1.0) para evitar división por cero.
            dias_analisis_float = max(float(dias_analisis), 1.0)
            # Pasamos todos los parámetros necesarios para los cálculos, incluyendo los días de análisis para el promedio.
            params = (dias_analisis_float, dias_analisis_float, dias_cobertura, dias_analisis_float, dias_cobertura, fecha_inicio_str)
            cursor.execute(query, params)
            sugerencias = cursor.fetchall()
            return sugerencias
    except sqlite3.Error as e:
        print(f"Error al obtener sugerencias de reposición: {e}")
        return []

def crear_backup_seguro(ruta_backup: str):
    """
    Crea una copia de seguridad de la base de datos en uso de forma segura
    utilizando la API de backup de SQLite.

    Args:
        ruta_backup: La ruta completa del archivo donde se guardará la copia.

    Returns:
        True si el backup fue exitoso, False en caso contrario.
    """
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