import pytest
import os
import tkinter as tk
from unittest.mock import MagicMock, patch
import pandas as pd
from io import BytesIO

from views import VentasView, StockView, ReportesView
from database import agregar_producto, obtener_producto_por_id, agregar_cliente, obtener_cliente_por_id, obtener_producto_por_nombre, registrar_venta, DB_FILE
from models import Producto, Cliente, Venta, DetalleVenta
from datetime import datetime, timedelta

# Importar la fixture de base de datos desde el otro archivo de pruebas
from test_database import db_conn

def test_flujo_completo_de_venta(db_conn, monkeypatch):
    """
    Prueba de integración que simula un ciclo de venta completo:
    1. Asignar cliente.
    2. Añadir productos al carrito.
    3. Finalizar la venta en efectivo.
    4. Verificar la reducción de stock.
    """
    # --- 1. SETUP ---
    # Crear productos y cliente en la BD de prueba
    id_prod_1 = agregar_producto(Producto(nombre="Coca Cola 1L", precio_venta=500, cantidad_stock=20))
    id_prod_2 = agregar_producto(Producto(nombre="Papas Fritas 300g", precio_venta=350, cantidad_stock=15))
    id_cliente = agregar_cliente(Cliente(nombre="Cliente de Prueba", dni="99888777"))

    # --- AISLAMIENTO DE LA GUI ---
    # Evitamos que el método que construye los widgets se ejecute.
    monkeypatch.setattr(VentasView, "create_widgets", lambda self: None)
    # Simulamos las variables de Tkinter que se crean en el __init__
    monkeypatch.setattr(tk, "StringVar", MagicMock)
    monkeypatch.setattr(tk, "BooleanVar", MagicMock)
    monkeypatch.setattr(tk, "DoubleVar", MagicMock)

    
    # Instanciamos la vista. Ahora solo contendrá nuestra lógica, sin widgets.
    ventas_view = VentasView(None) 
    # Como create_widgets está desactivado, creamos manualmente los atributos
    # que la prueba necesita como Mocks.
    ventas_view.client_label_var = MagicMock()
    ventas_view.total_var = MagicMock()
    ventas_view.cart_tree = MagicMock() # Necesario para update_cart_display
    ventas_view.search_var = MagicMock() # Necesario para add_product_to_sale
    ventas_view.suggestions_popup = MagicMock() # Necesario para add_product_to_sale
    ventas_view.search_entry = MagicMock() # Necesario para add_product_to_sale (focus_set)


    # Mockear (simular) las ventanas de diálogo que la vista intenta abrir
    monkeypatch.setattr('views.SelectClientDialog', lambda *args, **kwargs: MagicMock(result=id_cliente))
    # Simulamos que el diálogo de cantidad siempre devuelve (2, 0) -> 2 unidades, 0% descuento
    monkeypatch.setattr('views.QuantityDialog', lambda *args, **kwargs: MagicMock(result=(2, 0)))
    
    # Mock mejorado para PaymentWindow: simula la finalización real de la venta.
    # VERSIÓN SIMPLIFICADA: Ejecuta la lógica directamente en el __init__.
    class MockPaymentWindow:
        def __init__(self, parent, venta_obj, callback):
            venta_obj.forma_pago = "Efectivo" # Asigna la forma de pago
            id_venta_nueva = registrar_venta(venta_obj)
            if callback:
                # El callback original (cancel_sale) no necesita el id
                callback()

    monkeypatch.setattr('views.PaymentWindow', MockPaymentWindow)
    # Mockear messagebox para evitar que aparezcan ventanas emergentes
    monkeypatch.setattr('views.messagebox', MagicMock())

    # --- 2. ACCIONES (Simulación de la Interfaz) ---
    # Simular la asignación de un cliente
    ventas_view.asignar_cliente()
    assert ventas_view.cliente_seleccionado is not None
    assert ventas_view.cliente_seleccionado.id_cliente == id_cliente
    ventas_view.client_label_var.set.assert_called_with("Cliente: Cliente de Prueba (DNI: 99888777)")

    # Simular añadir el primer producto al carrito
    prod1 = obtener_producto_por_id(id_prod_1)
    ventas_view.add_product_to_sale(prod1)

    # Simular añadir el segundo producto al carrito
    prod2 = obtener_producto_por_id(id_prod_2)
    ventas_view.add_product_to_sale(prod2)

    # Verificar el estado interno del carrito y el total
    assert len(ventas_view.current_sale_items) == 2
    assert ventas_view.current_sale_items[id_prod_1].cantidad == 2
    assert ventas_view.current_sale_items[id_prod_2].cantidad == 2
    # Total esperado: (2 * 500) + (2 * 350) = 1000 + 700 = 1700
    ventas_view.total_var.set.assert_called_with("$1700.00")

    # Simular la finalización de la venta
    # Esta llamada ahora instanciará nuestro MockPaymentWindow, que completará la venta.
    # El método `finalize_sale` internamente usa `self.cancel_sale` como callback.
    # Lo reemplazamos con un mock para verificar que se llama.
    ventas_view.cancel_sale = MagicMock() # Reemplazamos el método real con un mock
    ventas_view.finalize_sale() # Llamamos al método sin argumentos

    # --- 3. VERIFICACIÓN (Estado final de la BD) ---
    # Verificar que el stock se haya reducido correctamente
    prod1_actualizado = obtener_producto_por_id(id_prod_1)
    prod2_actualizado = obtener_producto_por_id(id_prod_2)
    assert prod1_actualizado.cantidad_stock == 18 # 20 - 2
    assert prod2_actualizado.cantidad_stock == 13 # 15 - 2 
    # Verificamos que el callback (cancel_sale) fue llamado, asegurando que el flujo terminó.
    ventas_view.cancel_sale.assert_called_once()


def test_importacion_desde_excel(db_conn, monkeypatch):
    """
    Prueba de integración para la importación de productos desde un archivo Excel.
    Simula la selección de un archivo y verifica que la BD se actualice correctamente.
    """
    # --- 1. SETUP ---
    # Crear un producto que ya existe para probar la adición de lotes
    id_prod_existente = agregar_producto(Producto(nombre="Galletas de Chocolate", precio_venta=180, cantidad_stock=50))

    # Crear un DataFrame de pandas que simule nuestro archivo Excel
    df_simulado = pd.DataFrame({
        'nombre': ['Jugo de Naranja 1L', 'Galletas de Chocolate'],
        'precio_venta': [250.0, 180.0],
        'cantidad_stock': [12, 30],
        'fecha_vencimiento': ['2025-10-15', '2024-11-30']
    })
    # Convertir el DataFrame a un objeto de bytes en formato Excel
    excel_buffer = BytesIO()
    df_simulado.to_excel(excel_buffer, index=False)
    excel_buffer.seek(0)

    # Mockear filedialog para que devuelva una ruta de archivo falsa
    monkeypatch.setattr('views.filedialog.askopenfilename', lambda **kwargs: "fake_path.xlsx")
    # Mockear pd.read_excel para que lea nuestro DataFrame en memoria en lugar de un archivo real
    monkeypatch.setattr('pandas.read_excel', lambda *args, **kwargs: df_simulado)
    # Mockear messagebox para evitar ventanas emergentes
    monkeypatch.setattr('views.messagebox', MagicMock())

    # --- AISLAMIENTO DE LA GUI ---    
    # Evitamos que los métodos que construyen la interfaz gráfica se ejecuten en el __init__.
    monkeypatch.setattr(StockView, "create_widgets", lambda self: None)
    monkeypatch.setattr(StockView, "cargar_productos", lambda self: None)
    
    # Mockear los métodos que interactúan con la UI (start/stop feedback) para evitar errores de hilos
    # y la necesidad de un mainloop de Tkinter.
    monkeypatch.setattr(StockView, "start_import_feedback", lambda self: None)
    monkeypatch.setattr(StockView, "stop_import_feedback", lambda self: None)

    # Simulamos las variables de Tkinter que se usan en la vista
    monkeypatch.setattr(tk, "StringVar", MagicMock)
    monkeypatch.setattr(tk, "BooleanVar", MagicMock)

    # Instanciamos la vista de Stock sin su parte gráfica
    stock_view = StockView(None)
    # Ahora que la vista está creada, podemos añadir los mocks necesarios
    # para el método que vamos a probar (`importar_desde_excel`).
    stock_view.tree = MagicMock()
    stock_view.poco_stock_var = MagicMock() # Usado por cargar_productos
    stock_view.search_var = MagicMock()     # Usado por cargar_productos
    # Mockeamos 'after' para que no falle al intentar actualizar la UI desde el hilo
    stock_view.after = MagicMock()

    # --- 2. ACCIÓN ---    
    # En lugar de llamar a importar_desde_excel (que inicia un hilo),
    # llamamos directamente a la función que hace el trabajo.
    # Esto hace la prueba más simple, rápida y evita problemas de concurrencia.
    stock_view._perform_excel_import("fake_path.xlsx")
    

    # --- 3. VERIFICACIÓN ---
    # Verificar que el producto nuevo se creó
    prod_jugo = obtener_producto_por_nombre("Jugo de Naranja 1L")
    assert prod_jugo is not None
    assert prod_jugo.cantidad_stock == 12

    # Verificar que al producto existente se le añadió el nuevo lote
    prod_galletas = obtener_producto_por_id(id_prod_existente)
    assert prod_galletas.cantidad_stock == 80 # 50 originales + 30 importados


def test_sugerencia_reposicion_stock(db_conn, monkeypatch):
    """
    Prueba de integración para la funcionalidad de sugerencia de reposición de stock.
    Simula ventas y luego verifica que las sugerencias generadas sean correctas.
    """
    # --- 1. SETUP: Crear productos y simular historial de ventas ---
    # Producto 1: Alta rotación, quedará con stock negativo
    id_leche = agregar_producto(Producto(nombre="Leche 1L", precio_venta=250, cantidad_stock=10))
    # Producto 2: Stock suficiente, sin ventas recientes
    agregar_producto(Producto(nombre="Pan Lactal", precio_venta=400, cantidad_stock=30))
    # Producto 3: Poca rotación, stock bajo
    id_azucar = agregar_producto(Producto(nombre="Azúcar 1kg", precio_venta=300, cantidad_stock=15))

    # Simular una venta grande de Leche y una pequeña de Azúcar hace 10 días
    # Simular una venta grande de Leche y una pequeña de Azúcar hace 5 días
    fecha_venta = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    venta_pasada = Venta(fecha_venta=fecha_venta, total=0, forma_pago="Efectivo")
    # Se venden 25 de Leche (stock 10 -> queda -15)
    venta_pasada.detalles.append(DetalleVenta(id_producto=id_leche, cantidad=25, precio_unitario=250))
    # --- AISLAMIENTO DE LA GUI ---
    # Evitamos que los métodos que construyen las pestañas se ejecuten en el __init__
    monkeypatch.setattr(ReportesView, "create_sales_report_widgets", lambda self, parent: None)
    monkeypatch.setattr(ReportesView, "on_period_change", lambda self, event=None: None)

    # Simulamos las variables de Tkinter que se crean en el __init__
    monkeypatch.setattr(tk, "StringVar", MagicMock)
    monkeypatch.setattr(tk, "BooleanVar", MagicMock)

    # --- 2. ACCIÓN: Simular la generación del reporte ---
    # Instanciamos la vista. El __init__ ahora solo creará el notebook.
    reportes_view = ReportesView(None)
    # Ahora, construimos manualmente solo la pestaña de sugerencias.
    # Esto creará los widgets necesarios como `sugerencias_tree`.
    reportes_view.create_restock_suggestion_widgets(reportes_view.reposicion_frame)
    
    # Simular que el usuario configura los parámetros en la UI
    dias_analisis = 30
    dias_cobertura = 15
    reportes_view.dias_analisis_var.set(str(dias_analisis))
    reportes_view.dias_cobertura_var.set(str(dias_cobertura))

    # Simular clic en el botón "Generar Sugerencias"
    reportes_view.generar_sugerencias()

    # --- 3. VERIFICACIÓN: Comprobar los resultados en la tabla ---
    # En lugar de verificar el widget, verificamos los datos que se generaron,
    # que es el resultado real de la lógica de negocio.
    sugerencias = reportes_view.sugerencias_data
    # Solo se debe sugerir Leche, porque Azúcar tiene stock suficiente (10 > 5)
    assert len(sugerencias) == 1

    # Convertimos la lista de tuplas a un diccionario para facilitar la búsqueda
    datos_sugeridos = {item[1]: item for item in sugerencias} # Clave: nombre del producto

    assert "Leche 1L" in datos_sugeridos
    assert "Pan Lactal" not in datos_sugeridos # El pan no necesita reposición
    assert "Azúcar 1kg" not in datos_sugeridos # El azúcar tiene stock suficiente

    # Verificación detallada para el producto "Leche 1L"
    # Formato: (id, nombre, stock_actual, ventas_periodo, venta_diaria, stock_sugerido, a_comprar)
    leche_sugerencia = datos_sugeridos["Leche 1L"]
    stock_actual_leche = float(leche_sugerencia[2])
    cantidad_a_comprar_leche = int(leche_sugerencia[6])
    ventas_periodo_leche = int(leche_sugerencia[3])

    assert stock_actual_leche == -15 # 10 iniciales - 25 vendidos
    assert ventas_periodo_leche == 25
    
    # Cálculo manual para verificar la sugerencia:
    # Venta diaria promedio (calculada por la BD) será aprox. 2.5 (25 unidades / 10 días).
    # Stock objetivo = Venta diaria * dias_cobertura (15) = 2.5 * 15 = 37.5
    # Cantidad a comprar = Stock objetivo (37.5) - Stock actual (-15) = 52.5, que se redondea a 53.
    # Cálculo manual para verificar la sugerencia:
    # Venta diaria promedio (calculada por la BD) será 25 / 30 = 0.8333
    # Stock objetivo = Venta diaria * dias_cobertura (15) = 0.8333 * 15 = 12.5
    # Cantidad a comprar = Stock objetivo (12.5) - Stock actual (-15) = 27.5, que se redondea a 28.
    assert cantidad_a_comprar_leche == 28, "La cantidad a comprar sugerida no es la esperada."

# --- 2. ACCIONES (Simulación de la Interfaz) ---
    # Simular la asignación de un cliente
    ventas_view.asignar_cliente()
    assert ventas_view.cliente_seleccionado is not None
    assert ventas_view.cliente_seleccionado.id_cliente == id_cliente
    ventas_view.client_label_var.set.assert_called_with("Cliente: Cliente de Prueba (DNI: 99888777)")

    # Simular añadir el primer producto al carrito
    prod1 = obtener_producto_por_id(id_prod_1)
    ventas_view.add_product_to_sale(prod1)

    # Simular añadir el segundo producto al carrito
    prod2 = obtener_producto_por_id(id_prod_2)
    ventas_view.add_product_to_sale(prod2)

    # Verificar el estado interno del carrito y el total
    assert len(ventas_view.current_sale_items) == 2
    assert ventas_view.current_sale_items[id_prod_1].cantidad == 2
    assert ventas_view.current_sale_items[id_prod_2].cantidad == 2
    # Total esperado: (2 * 500) + (2 * 350) = 1000 + 700 = 1700
    ventas_view.total_var.set.assert_called_with("$1700.00")

    # Simular la finalización de la venta
    # Esta llamada ahora instanciará nuestro MockPaymentWindow, que completará la venta.
    # El método `finalize_sale` internamente usa `self.cancel_sale` como callback.
    # Lo reemplazamos con un mock para verificar que se llama.
    ventas_view.cancel_sale = MagicMock() # Reemplazamos el método real con un mock
    ventas_view.finalize_sale() # Llamamos al método sin argumentos

    # --- 3. VERIFICACIÓN (Estado final de la BD) ---
    # Verificar que el stock se haya reducido correctamente
    prod1_actualizado = obtener_producto_por_id(id_prod_1)
    prod2_actualizado = obtener_producto_por_id(id_prod_2)
    assert prod1_actualizado.cantidad_stock == 18 # 20 - 2
    assert prod2_actualizado.cantidad_stock == 13 # 15 - 2 
    # Verificamos que el callback (cancel_sale) fue llamado, asegurando que el flujo terminó.
    ventas_view.cancel_sale.assert_called_once()


def test_importacion_desde_excel(db_conn, monkeypatch):
    """
    Prueba de integración para la importación de productos desde un archivo Excel.
    Simula la selección de un archivo y verifica que la BD se actualice correctamente.
    """
    # --- 1. SETUP ---
    # Crear un producto que ya existe para probar la adición de lotes
    id_prod_existente = agregar_producto(Producto(nombre="Galletas de Chocolate", precio_venta=180, cantidad_stock=50))

    # Crear un DataFrame de pandas que simule nuestro archivo Excel
    df_simulado = pd.DataFrame({
        'nombre': ['Jugo de Naranja 1L', 'Galletas de Chocolate'],
        'precio_venta': [250.0, 180.0],
        'cantidad_stock': [12, 30],
        'fecha_vencimiento': ['2025-10-15', '2024-11-30']
    })
    # Convertir el DataFrame a un objeto de bytes en formato Excel
    excel_buffer = BytesIO()
    df_simulado.to_excel(excel_buffer, index=False)
    excel_buffer.seek(0)

    # Mockear filedialog para que devuelva una ruta de archivo falsa
    monkeypatch.setattr('views.filedialog.askopenfilename', lambda **kwargs: "fake_path.xlsx")
    # Mockear pd.read_excel para que lea nuestro DataFrame en memoria en lugar de un archivo real
    monkeypatch.setattr('pandas.read_excel', lambda *args, **kwargs: df_simulado)
    # Mockear messagebox para evitar ventanas emergentes
    monkeypatch.setattr('views.messagebox', MagicMock())

    # --- AISLAMIENTO DE LA GUI ---    
    # Evitamos que los métodos que construyen la interfaz gráfica se ejecuten en el __init__.
    monkeypatch.setattr(StockView, "create_widgets", lambda self: None)
    monkeypatch.setattr(StockView, "cargar_productos", lambda self: None)
    
    # Mockear los métodos que interactúan con la UI (start/stop feedback) para evitar errores de hilos
    # y la necesidad de un mainloop de Tkinter.
    monkeypatch.setattr(StockView, "start_import_feedback", lambda self: None)
    monkeypatch.setattr(StockView, "stop_import_feedback", lambda self: None)

    # Simulamos las variables de Tkinter que se usan en la vista
    monkeypatch.setattr(tk, "StringVar", MagicMock)
    monkeypatch.setattr(tk, "BooleanVar", MagicMock)

    # Instanciamos la vista de Stock sin su parte gráfica
    stock_view = StockView(None)
    # Ahora que la vista está creada, podemos añadir los mocks necesarios
    # para el método que vamos a probar (`importar_desde_excel`).
    stock_view.tree = MagicMock()
    stock_view.poco_stock_var = MagicMock() # Usado por cargar_productos
    stock_view.search_var = MagicMock()     # Usado por cargar_productos
    # Mockeamos 'after' para que no falle al intentar actualizar la UI desde el hilo
    stock_view.after = MagicMock()

    # --- 2. ACCIÓN ---    
    # En lugar de llamar a importar_desde_excel (que inicia un hilo),
    # llamamos directamente a la función que hace el trabajo.
    # Esto hace la prueba más simple, rápida y evita problemas de concurrencia.
    stock_view._perform_excel_import("fake_path.xlsx")
    

    # --- 3. VERIFICACIÓN ---
    # Verificar que el producto nuevo se creó
    prod_jugo = obtener_producto_por_nombre("Jugo de Naranja 1L")
    assert prod_jugo is not None
    assert prod_jugo.cantidad_stock == 12

    # Verificar que al producto existente se le añadió el nuevo lote
    prod_galletas = obtener_producto_por_id(id_prod_existente)
    assert prod_galletas.cantidad_stock == 80 # 50 originales + 30 importados


def test_sugerencia_reposicion_stock(db_conn, monkeypatch):
    """
    Prueba de integración para la funcionalidad de sugerencia de reposición de stock.
    Simula ventas y luego verifica que las sugerencias generadas sean correctas.
    """
    # --- 1. SETUP: Crear productos y simular historial de ventas ---
    # Producto 1: Alta rotación, quedará con stock negativo
    id_leche = agregar_producto(Producto(nombre="Leche 1L", precio_venta=250, cantidad_stock=10))
    # Producto 2: Stock suficiente, sin ventas recientes
    agregar_producto(Producto(nombre="Pan Lactal", precio_venta=400, cantidad_stock=30))
    # Producto 3: Poca rotación, stock bajo
    id_azucar = agregar_producto(Producto(nombre="Azúcar 1kg", precio_venta=300, cantidad_stock=15))

    # Simular una venta grande de Leche y una pequeña de Azúcar hace 10 días
    # Simular una venta grande de Leche y una pequeña de Azúcar hace 5 días
    fecha_venta = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    venta_pasada = Venta(fecha_venta=fecha_venta, total=0, forma_pago="Efectivo")
    # Se venden 25 de Leche (stock 10 -> queda -15)
    venta_pasada.detalles.append(DetalleVenta(id_producto=id_leche, cantidad=25, precio_unitario=250))
    # Se venden 5 de Azúcar (stock 15 -> queda 10)
    venta_pasada.detalles.append(DetalleVenta(id_producto=id_azucar, cantidad=5, precio_unitario=300))
    venta_pasada.calcular_total()
    registrar_venta(venta_pasada)

    # Mockear messagebox para evitar ventanas emergentes
    monkeypatch.setattr('views.messagebox', MagicMock())

    # --- AISLAMIENTO DE LA GUI ---
    # Evitamos que los métodos que construyen las pestañas se ejecuten en el __init__
    monkeypatch.setattr(ReportesView, "create_sales_report_widgets", lambda self, parent: None)
    monkeypatch.setattr(ReportesView, "on_period_change", lambda self, event=None: None)

    # Simulamos las variables de Tkinter que se crean en el __init__
    monkeypatch.setattr(tk, "StringVar", MagicMock)
    monkeypatch.setattr(tk, "BooleanVar", MagicMock)

    # --- 2. ACCIÓN: Simular la generación del reporte ---
    # Instanciamos la vista. El __init__ ahora solo creará el notebook.
    reportes_view = ReportesView(None)
    # Ahora, construimos manualmente solo la pestaña de sugerencias.
    # Esto creará los widgets necesarios como `sugerencias_tree`.
    reportes_view.create_restock_suggestion_widgets(reportes_view.reposicion_frame)
    
    # Simular que el usuario configura los parámetros en la UI
    dias_analisis = 30
    dias_cobertura = 15
    reportes_view.dias_analisis_var.get.return_value = str(dias_analisis)
    reportes_view.dias_cobertura_var.get.return_value = str(dias_cobertura)

    # Simular clic en el botón "Generar Sugerencias"
    reportes_view.generar_sugerencias()

    # --- 3. VERIFICACIÓN: Comprobar los resultados en la tabla ---
    # En lugar de verificar el widget, verificamos los datos que se generaron,
    # que es el resultado real de la lógica de negocio.
    sugerencias = reportes_view.sugerencias_data
    # Solo se debe sugerir Leche, porque Azúcar tiene stock suficiente (10 > 5)
    assert len(sugerencias) == 1

    # Convertimos la lista de tuplas a un diccionario para facilitar la búsqueda
    datos_sugeridos = {item[1]: item for item in sugerencias} # Clave: nombre del producto

    assert "Leche 1L" in datos_sugeridos
    assert "Pan Lactal" not in datos_sugeridos # El pan no necesita reposición
    assert "Azúcar 1kg" not in datos_sugeridos # El azúcar tiene stock suficiente

    # Verificación detallada para el producto "Leche 1L"
    # Formato: (id, nombre, stock_actual, ventas_periodo, venta_diaria, stock_sugerido, a_comprar)
    leche_sugerencia = datos_sugeridos["Leche 1L"]
    stock_actual_leche = float(leche_sugerencia[2])
    cantidad_a_comprar_leche = int(leche_sugerencia[6])
    ventas_periodo_leche = int(leche_sugerencia[3])

    assert stock_actual_leche == -15 # 10 iniciales - 25 vendidos
    assert ventas_periodo_leche == 25
    
    # Cálculo manual para verificar la sugerencia:
    # Venta diaria promedio (calculada por la BD) será 25 / 30 = 0.8333
    # Stock objetivo = Venta diaria * dias_cobertura (15) = 0.8333 * 15 = 12.5
    # Cantidad a comprar = Stock objetivo (12.5) - Stock actual (-15) = 27.5, que se redondea a 28.
    assert cantidad_a_comprar_leche == 28, "La cantidad a comprar sugerida no es la esperada."

def test_creacion_backup(db_conn, monkeypatch, tmp_path):
    """
    Prueba la funcionalidad de crear una copia de seguridad.
    Simula la selección de un archivo y verifica que la copia se cree correctamente.
    """
    # --- 1. SETUP ---
    # La fixture db_conn ya nos da una base de datos funcionando.
    # tmp_path nos da un directorio temporal para guardar el backup.
    
    ruta_backup_simulada = tmp_path / "backup_test.db"

    # Mockear filedialog para que devuelva nuestra ruta simulada sin abrir un diálogo real.
    monkeypatch.setattr('easyst.filedialog.asksaveasfilename', lambda **kwargs: str(ruta_backup_simulada))
    
    # Mockear database.crear_backup_seguro para espiarlo y verificar que se llama.
    mock_backup = MagicMock(return_value=True)
    monkeypatch.setattr('easyst.crear_backup_seguro', mock_backup)
    
    # Mockear messagebox para evitar ventanas emergentes.
    mock_messagebox = MagicMock()
    monkeypatch.setattr('easyst.messagebox', mock_messagebox)

    # --- 2. ACCIÓN ---
    from easyst import App
    # En lugar de instanciar la clase App (lo que causa problemas con Tkinter),
    # llamamos directamente al método `create_backup` de la clase.
    # Pasamos `None` como `self` porque el método no lo usa internamente.
    App.create_backup(None)

    # --- 3. VERIFICACIÓN ---
    # Verificar que se intentó crear el backup en la ruta correcta.
    mock_backup.assert_called_once_with(str(ruta_backup_simulada))
    # Verificar que se mostró un mensaje de éxito.
    mock_messagebox.showinfo.assert_called_once()


def test_generacion_texto_ticket(db_conn):
    """
    Prueba la función auxiliar `generar_texto_ticket` para asegurar que el formato
    del ticket de texto plano sea correcto.
    """
    # --- 1. SETUP ---
    from views import generar_texto_ticket

    # Crear productos y una venta compleja para el ticket.
    id_prod1 = agregar_producto(Producto(nombre="Producto Largo Nombre", precio_venta=100, cantidad_stock=10))
    id_prod2 = agregar_producto(Producto(nombre="Item B", precio_venta=25.5, cantidad_stock=10))

    venta_obj = Venta(id_venta=123, fecha_venta="2024-05-21 15:30:00", total=251.0)
    venta_obj.detalles.append(DetalleVenta(id_producto=id_prod1, cantidad=2, precio_unitario=100, descuento=0, subtotal=200.0))
    venta_obj.detalles.append(DetalleVenta(id_producto=id_prod2, cantidad=2, precio_unitario=25.5, descuento=0, subtotal=51.0))
    # Recalculamos el total después de añadir todos los detalles para asegurar que es correcto.
    # El total real es 200.0 + 51.0 = 251.0
    venta_obj.calcular_total()

    # --- 2. ACCIÓN ---
    texto_ticket = generar_texto_ticket(venta_obj)

    # --- 3. VERIFICACIÓN ---
    assert "Venta ID: 123" in texto_ticket
    assert "Fecha: 21/05/2024 15:30" in texto_ticket    
    assert "TOTAL: $251.00" in texto_ticket
    assert "¡Gracias por su compra!" in texto_ticket

    # Verificación robusta de los productos, ignorando espacios de formato
    lineas_de_productos = [
        line.strip() for line in texto_ticket.split('\n') 
        if "Producto Lar" in line or "Item B" in line
    ]
    assert any("Producto Lar" in linea for linea in lineas_de_productos)
    assert any("Item B" in linea for linea in lineas_de_productos)