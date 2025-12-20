import tkinter as tk
from tkinter import ttk, messagebox
from tkinter import simpledialog
from tkinter import filedialog
import os
import webbrowser
import mercadopago
import qrcode
from PIL import Image, ImageTk
import configparser
import sys

import threading
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from escpos.printer import Usb
from database import (obtener_productos, agregar_producto, obtener_producto_por_codigo_barras, registrar_venta, obtener_producto_por_id, actualizar_producto, obtener_clientes, agregar_cliente, actualizar_cliente, obtener_cliente_por_id, realizar_pago_cliente, obtener_ventas_por_rango_de_fechas, obtener_lotes_por_producto, actualizar_lote, agregar_lote, obtener_movimientos_cliente, obtener_pagos_recibidos_por_rango, inicializar_bd, obtener_sugerencias_reposicion, obtener_producto_por_nombre, obtener_venta_por_id, obtener_productos_por_ids)
from models import Producto, Venta, DetalleVenta, Cliente
from datetime import datetime, timedelta
from collections import defaultdict
def resource_path(relative_path):
    """ Obtiene la ruta absoluta al recurso, funciona para desarrollo y para PyInstaller """
    try:
        # PyInstaller crea una carpeta temporal y guarda la ruta en _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

# --- Cargar configuración desde config.ini ---
config = configparser.ConfigParser()
config.read(resource_path('config.ini'))

# Credenciales y configuración de Mercado Pago
MP_ACCESS_TOKEN = config.get('MercadoPago', 'AccessToken', fallback='NO_CONFIGURADO')
MODO_PRUEBA_PAGOS = config.getboolean('MercadoPago', 'ModoPrueba', fallback=True)

# Datos del negocio
NOMBRE_NEGOCIO = config.get('Negocio', 'Nombre', fallback='EasySt System')

# Nueva configuración para stock negativo
PERMITIR_STOCK_NEGATIVO = config.getboolean('Negocio', 'PermitirStockNegativo', fallback=False)

# Configuración de la impresora
PRINTER_VENDOR_ID = config.get('Impresora', 'idVendor', fallback=None)
PRINTER_PRODUCT_ID = config.get('Impresora', 'idProduct', fallback=None)
PRINTER_PROFILE = config.get('Impresora', 'profile', fallback=None)

class ToolTip:
    """
    Crea una ayuda emergente (Tooltip) para un widget dado.
    Ahora puede manejar un Treeview y mostrar tooltips en cabeceras específicas.
    """
    def __init__(self, widget, text=None, header_tooltips=None):
        self.widget = widget
        self.text = text
        self.header_tooltips = header_tooltips or {}
        self.tooltip_window = None
        
        if isinstance(widget, ttk.Treeview) and self.header_tooltips:
            self.widget.bind("<Motion>", self.on_treeview_motion)
            self.widget.bind("<Leave>", self.hide_tooltip)
        else:
            self.widget.bind("<Enter>", self.show_tooltip)
            self.widget.bind("<Leave>", self.hide_tooltip)

    def show_tooltip(self, text, event=None):
        """Muestra el tooltip en la posición correcta."""
        # Si ya hay un tooltip visible, lo destruimos primero para evitar duplicados.
        if self.tooltip_window:
            self.tooltip_window.destroy()

        x, y, _, _ = self.widget.bbox("insert") if event is None else (event.x, event.y, 0, 0)
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25

        # Crea la ventana del tooltip
        self.tooltip_window = tk.Toplevel(self.widget)
        self.tooltip_window.wm_overrideredirect(True) # Sin bordes ni barra de título
        self.tooltip_window.wm_geometry(f"+{x}+{y}")

        label = ttk.Label(self.tooltip_window, text=text, justify='left',
                         background="#ffffe0", relief='solid', borderwidth=1,
                         padding=(5, 3))
        label.pack(ipadx=1)
        # Para widgets que no son Treeview, usamos el texto por defecto
        if not isinstance(self.widget, ttk.Treeview):
            self.show_tooltip(self.text)

    def hide_tooltip(self, event=None):
        """Oculta el tooltip."""
        if self.tooltip_window:
            self.tooltip_window.destroy()
        self.tooltip_window = None

    def on_treeview_motion(self, event):
        region = self.widget.identify_region(event.x, event.y)
        if region == "heading":
            column_id = self.widget.identify_column(event.x)
            # Los IDs de columna tienen formato #1, #2, etc. Necesitamos el nombre.
            column_index = int(column_id.replace('#', '')) - 1
            column_name = self.widget['columns'][column_index]
            
            if column_name in self.header_tooltips:
                self.hide_tooltip() # Ocultar cualquier tooltip anterior
                self.show_tooltip(self.header_tooltips[column_name], event)
                return
        self.hide_tooltip()

def generar_texto_ticket(venta_obj: Venta):
    """Construye el contenido en texto plano del ticket a partir de un objeto Venta."""
    # El nombre del negocio ahora se lee desde config.ini
    ticket_content = f"         *** {NOMBRE_NEGOCIO} ***\n\n"
    ticket_content += f"Fecha: {datetime.strptime(venta_obj.fecha_venta, '%Y-%m-%d %H:%M:%S').strftime('%d/%m/%Y %H:%M')}\n"
    ticket_content += f"Venta ID: {venta_obj.id_venta}\n"
    ticket_content += "----------------------------------------\n"
    ticket_content += "{:<5} {:<12} {:>7} {:>6} {:>7}\n".format("Cant", "Producto", "P.Unit", "Desc.", "Subt.")
    ticket_content += "----------------------------------------\n"

    for detalle in venta_obj.detalles:
        # Necesitamos obtener el nombre del producto para cada detalle
        producto = obtener_producto_por_id(detalle.id_producto)
        if not producto: continue
        nombre_prod = producto.nombre[:12] if producto.nombre else "N/A"
        linea = "{:<5} {:<12} {:>7.2f} {:>5.1f}% {:>7.2f}\n".format(
            detalle.cantidad, nombre_prod, detalle.precio_unitario, detalle.descuento, detalle.subtotal
        )
        ticket_content += linea
    
    ticket_content += "----------------------------------------\n"
    ticket_content += f"TOTAL: ${venta_obj.total:.2f}\n\n"
    ticket_content += "       ¡Gracias por su compra!\n"
    return ticket_content


class StockView(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)

        self.parent = parent
        # --- Inicialización de variables de estado ---
        self.status_var = tk.StringVar()
        self.progress_bar = None # Se creará en create_widgets
        self.status_label = None # Se creará en create_widgets

        self.create_widgets()
        self.cargar_productos()

    def create_widgets(self):
        # --- Frame de Controles (búsqueda, filtros, botones) ---
        controls_frame = ttk.Frame(self)
        controls_frame.pack(side="top", fill="x", padx=10, pady=10)

        # Búsqueda
        ttk.Label(controls_frame, text="Buscar por nombre:").pack(side="left", padx=(0, 5))
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(controls_frame, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True)
        search_entry.bind("<Return>", lambda event: self.cargar_productos()) # Buscar al presionar Enter

        # Filtro de poco stock
        self.poco_stock_var = tk.BooleanVar()
        poco_stock_check = ttk.Checkbutton(
            controls_frame, 
            text="Mostrar solo con poco stock", 
            variable=self.poco_stock_var,
            command=self.cargar_productos
        )
        poco_stock_check.pack(side="left", padx=10)

        # Botones de acción
        ttk.Button(controls_frame, text="Buscar", command=self.cargar_productos).pack(side="left", padx=5)
        ttk.Button(controls_frame, text="Importar desde Excel", command=self.importar_desde_excel).pack(side="right", padx=5)
        ttk.Button(controls_frame, text="Añadir Producto", command=self.abrir_ventana_producto).pack(side="right", padx=5)
        self.gestionar_lotes_btn = ttk.Button(controls_frame, text="Gestionar Lotes", command=self.abrir_ventana_gestion_lotes, state="disabled")
        self.gestionar_lotes_btn.pack(side="right", padx=5)

        # --- Treeview para mostrar los productos ---
        tree_frame = ttk.Frame(self)
        tree_frame.pack(side="top", fill="both", expand=True, padx=10, pady=(0, 10))

        self.tree = ttk.Treeview(
            tree_frame, 
            columns=("ID", "Nombre", "Stock Total", "Lotes", "Vencimiento Próximo"), 
            show="headings"
        )
        
        # Definir encabezados
        self.tree.heading("ID", text="ID")
        self.tree.heading("Nombre", text="Nombre")
        self.tree.heading("Stock Total", text="Stock Total")
        self.tree.heading("Lotes", text="Nº de Lotes")
        self.tree.heading("Vencimiento Próximo", text="Vencimiento Próximo")

        # Definir ancho de columnas
        self.tree.column("ID", width=50, anchor="center")
        self.tree.column("Nombre", width=350)
        self.tree.column("Stock Total", width=100, anchor="center")
        self.tree.column("Lotes", width=100, anchor="center")
        self.tree.column("Vencimiento Próximo", width=150, anchor="center")

        # Scrollbar
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Añadir tag para colorear filas con poco stock
        self.tree.tag_configure('poco_stock', background='#FFEBEE') # Un rojo muy claro
        self.tree.tag_configure('vencido', background='#FFCDD2', foreground='black') # Rojo claro
        self.tree.tag_configure('proximo_vencer', background='#FFF9C4', foreground='black') # Amarillo claro

        # Evento para editar producto con doble clic
        self.tree.bind("<<TreeviewSelect>>", self.on_product_select)
        self.tree.bind("<Double-1>", self.abrir_ventana_edicion_producto)

    def cargar_productos(self):
        """Limpia el treeview y carga los productos desde la BD."""
        # Limpiar vista anterior
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Obtener filtros
        nombre = self.search_var.get()
        poco_stock = self.poco_stock_var.get()

        # Cargar productos
        try:
            productos = obtener_productos(nombre_like=nombre, solo_poco_stock=poco_stock)
            for prod in productos:
                # Usamos los valores pre-calculados desde la base de datos. prod.num_lotes ya viene listo.
                num_lotes = prod.num_lotes 
                vencimiento_proximo = "N/A"

                tags = ()
                # Ahora también marcamos como 'poco_stock' si es negativo
                if prod.cantidad_stock <= 5:
                    tags = ('poco_stock',)

                # Lógica de vencimiento
                if prod.vencimiento_proximo:
                    vencimiento_proximo = datetime.strptime(prod.vencimiento_proximo, "%Y-%m-%d").strftime("%d/%m/%Y")
                    fecha_mas_cercana = datetime.strptime(prod.vencimiento_proximo, "%Y-%m-%d").date()
                    hoy = datetime.now().date()
                    dias_restantes = (fecha_mas_cercana - hoy).days
                    if dias_restantes < 0:
                        tags += ('vencido',)
                    elif dias_restantes <= 20: # Límite de 20 días
                        tags += ('proximo_vencer',)
                self.tree.insert("", "end", values=(
                    prod.id_producto,
                    prod.nombre,
                    prod.cantidad_stock,
                    num_lotes,
                    vencimiento_proximo
                ), tags=tags, iid=prod.id_producto)
        except Exception as e:
            messagebox.showerror("Error de Base de Datos", f"No se pudieron cargar los productos: {e}")
        
        # Deshabilitar el botón de lotes si no hay nada seleccionado
        self.gestionar_lotes_btn.config(state="disabled")

    def abrir_ventana_producto(self):
        # El 'callback' es la función que se ejecutará cuando el formulario se guarde con éxito.
        # En este caso, recargará la lista de productos.
        ProductFormWindow(self, callback=self.cargar_productos, producto_a_editar=None)

    def abrir_ventana_edicion_producto(self, event=None):
        """Abre la ventana del formulario para editar el producto seleccionado."""
        selection = self.tree.selection()
        if not selection:
            return
        
        id_producto_str = selection[0]
        id_producto = int(id_producto_str)

        producto_a_editar = obtener_producto_por_id(id_producto)
        if not producto_a_editar:
            messagebox.showerror("Error", "No se pudo encontrar el producto para editar.")
            return
        
        ProductFormWindow(self, callback=self.cargar_productos, producto_a_editar=producto_a_editar)

    def abrir_ventana_gestion_lotes(self):
        selection = self.tree.selection()
        if not selection:
            return
        
        id_producto = int(selection[0])
        producto = obtener_producto_por_id(id_producto)
        if not producto:
            messagebox.showerror("Error", "No se pudo encontrar el producto seleccionado.")
            return
        
        LoteManagementWindow(self, producto=producto, callback=self.cargar_productos)

    def on_product_select(self, event=None):
        """Habilita el botón de 'Gestionar Lotes' si se selecciona un producto."""
        self.gestionar_lotes_btn.config(state="normal" if self.tree.selection() else "disabled")

    def importar_desde_excel(self):
        """Abre un diálogo para seleccionar un archivo Excel e importar productos."""
        filepath = filedialog.askopenfilename(
            title="Seleccionar archivo de Excel para importar",
            filetypes=[("Archivos de Excel", "*.xlsx"), ("Todos los archivos", "*.*")]
        )

        if not filepath:
            return

        # Deshabilitar botones y mostrar barra de progreso
        self.start_import_feedback()

        # Iniciar la importación en un hilo para no congelar la UI
        threading.Thread(target=self._perform_excel_import, args=(filepath,)).start()

    def start_import_feedback(self):
        """Prepara la UI para el proceso de importación."""
        # Crear y mostrar la barra de progreso y la etiqueta de estado
        self.status_frame = ttk.Frame(self, padding=(10, 5))
        self.status_frame.pack(side="bottom", fill="x")
        
        self.status_label = ttk.Label(self.status_frame, textvariable=self.status_var)
        self.status_label.pack(side="left", padx=(0, 10))
        
        self.progress_bar = ttk.Progressbar(self.status_frame, orient="horizontal", mode="indeterminate")
        self.progress_bar.pack(fill="x", expand=True)
        self.progress_bar.start()
        
        self.status_var.set("Importando desde Excel, por favor espere...")
        # Deshabilitar botones para evitar acciones conflictivas
        for child in self.winfo_children():
            if isinstance(child, ttk.Frame): # Buscamos el controls_frame
                for btn in child.winfo_children():
                    if isinstance(btn, ttk.Button):
                        btn.config(state="disabled")

    def stop_import_feedback(self):
        """Restaura la UI después de la importación."""
        self.status_var.set("")
        if hasattr(self, 'status_frame') and self.status_frame.winfo_exists():
            self.status_frame.destroy()

        for child in self.winfo_children():
            if isinstance(child, ttk.Frame):
                for btn in child.winfo_children():
                    if isinstance(btn, ttk.Button):
                        btn.config(state="normal")

    def _perform_excel_import(self, filepath):
        try:
            df = pd.read_excel(filepath, engine='openpyxl')

            # Verificar que las columnas obligatorias existan
            required_columns = ['nombre', 'precio_venta']
            if not all(col in df.columns for col in required_columns):
                messagebox.showerror(
                    "Error de Formato",
                    f"El archivo de Excel debe contener las columnas obligatorias: {', '.join(required_columns)}",
                    parent=self
                )
                return

            exitosos = 0
            fallidos = 0
            errores_detalle = []

            for index, row in df.iterrows():
                try:
                    nombre = row['nombre']
                    precio_venta = float(row['precio_venta'])

                    if pd.isna(nombre) or nombre.strip() == "" or pd.isna(precio_venta) or precio_venta <= 0:
                        raise ValueError("Nombre o precio de venta inválido o faltante.")

                    # Obtener datos opcionales, manejando valores nulos (NaN) de pandas
                    codigo_barras = str(row['codigo_barras']) if 'codigo_barras' in row and pd.notna(row['codigo_barras']) else None
                    cantidad_stock = int(row['cantidad_stock']) if 'cantidad_stock' in row and pd.notna(row['cantidad_stock']) else 0
                    fecha_vencimiento = None
                    if 'fecha_vencimiento' in row and pd.notna(row['fecha_vencimiento']):
                        if isinstance(row['fecha_vencimiento'], datetime):
                            fecha_vencimiento = row['fecha_vencimiento'].strftime('%Y-%m-%d')
                        else:
                            fecha_vencimiento = str(row['fecha_vencimiento'])

                    # --- LÓGICA DE IMPORTACIÓN MODIFICADA ---
                    # Cada fila es un lote nuevo. Primero, vemos si el producto (por nombre) ya existe.
                    producto_existente = obtener_producto_por_nombre(nombre)

                    if producto_existente:
                        # Si el producto existe, solo añadimos un nuevo lote.
                        if agregar_lote(producto_existente.id_producto, cantidad_stock, fecha_vencimiento, codigo_barras):
                            exitosos += 1
                        else:
                            raise Exception("No se pudo añadir el lote al producto existente.")
                    else:
                        # Si el producto no existe, lo creamos con este primer lote.
                        volumen = float(row['volumen']) if 'volumen' in row and pd.notna(row['volumen']) else None
                        descripcion = str(row['descripcion']) if 'descripcion' in row and pd.notna(row['descripcion']) else None
                        
                        nuevo_producto = Producto(
                            nombre=nombre,
                            precio_venta=precio_venta,
                            cantidad_stock=cantidad_stock, # Cantidad del primer lote
                            codigo_barras=codigo_barras, # Código del primer lote
                            volumen=volumen,
                            descripcion=descripcion,
                            fecha_vencimiento=fecha_vencimiento # Vencimiento del primer lote
                        )
                        if agregar_producto(nuevo_producto):
                            exitosos += 1
                        else:
                            raise Exception("Error al crear el nuevo producto en la BD.")

                except pd.errors.DatabaseError as db_err: # Error específico de BD
                    messagebox.showerror("Error Crítico de Base de Datos", f"La importación se ha detenido debido a un error de base de datos:\n{db_err}\n\nNo se importarán más filas.", parent=self)
                    # Detenemos el bucle si hay un error de BD
                    break

                except Exception as e:
                    fallidos += 1
                    errores_detalle.append(f"Fila {index + 2}: {row.get('nombre', 'Sin Nombre')} - Error: {e}")

            # Mostrar resumen
            mensaje_final = f"Importación completada.\n\nLotes importados/creados: {exitosos}\nFilas con errores: {fallidos}"
            if fallidos > 0:
                mensaje_final += "\n\nDetalle de errores:\n" + "\n".join(errores_detalle[:5]) # Mostrar los primeros 5 errores
            
            # Actualizar la UI desde el hilo principal
            self.after(0, lambda: messagebox.showinfo("Resumen de Importación", mensaje_final, parent=self))
            self.after(0, self.cargar_productos)

        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error al Importar", f"No se pudo procesar el archivo de Excel:\n{e}", parent=self))
        finally:
            # Siempre restaurar la UI, incluso si hay un error
            self.after(0, self.stop_import_feedback)

class ClientesView(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.create_widgets()
        self.cargar_clientes()

    def create_widgets(self):
        controls_frame = ttk.Frame(self)
        controls_frame.pack(side="top", fill="x", padx=10, pady=10)

        ttk.Label(controls_frame, text="Buscar por Nombre o DNI:").pack(side="left", padx=(0, 5))
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(controls_frame, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True)
        search_entry.bind("<Return>", lambda e: self.cargar_clientes())

        # Filtro de clientes con deuda
        self.con_deuda_var = tk.BooleanVar()
        con_deuda_check = ttk.Checkbutton(
            controls_frame, text="Mostrar solo con deuda",
            variable=self.con_deuda_var, command=self.cargar_clientes
        )
        con_deuda_check.pack(side="left", padx=10)

        ttk.Button(controls_frame, text="Buscar", command=self.cargar_clientes).pack(side="left", padx=5)
        self.edit_client_btn = ttk.Button(controls_frame, text="Editar Cliente", command=self.abrir_ventana_edicion_cliente, state="disabled")
        self.edit_client_btn.pack(side="right", padx=5)
        ttk.Button(controls_frame, text="Añadir Cliente", command=self.abrir_ventana_cliente).pack(side="right", padx=5)
        ttk.Button(controls_frame, text="Registrar Pago", command=self.registrar_pago).pack(side="right")

        tree_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        tree_frame.pack(side="top", fill="both", expand=True, padx=10, pady=(0, 10))

        self.tree = ttk.Treeview(tree_frame, columns=("ID", "Nombre", "DNI", "Saldo", "Fecha Limite"), show="headings")
        self.tree.heading("ID", text="ID")
        self.tree.heading("Nombre", text="Nombre y Apellido")
        self.tree.heading("DNI", text="DNI")
        self.tree.heading("Saldo", text="Saldo Deudor")
        self.tree.heading("Fecha Limite", text="Fecha Límite de Pago")
        self.tree.column("ID", width=50, anchor="center")
        self.tree.column("DNI", width=150, anchor="center")
        self.tree.column("Saldo", width=120, anchor="e")
        self.tree.column("Fecha Limite", width=150, anchor="center")

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self.on_client_select)
        self.tree.bind("<Double-1>", self.abrir_detalle_cuenta_cliente)

        # Tags para colorear
        self.tree.tag_configure('vencido', background='#FFDDDD') # Rojo claro para deudas vencidas

    def cargar_clientes(self):
        for item in self.tree.get_children(): # type: ignore
            self.tree.delete(item)
        
        query = self.search_var.get()
        solo_con_deuda = self.con_deuda_var.get()
        try:
            clientes = obtener_clientes(nombre_o_dni=query, solo_con_deuda=solo_con_deuda)
            for cliente in clientes:
                tags = ()
                # Alerta visual si la fecha de pago ya pasó y aún hay deuda
                if cliente.fecha_limite_pago and cliente.saldo_deudor > 0:
                    fecha_limite = datetime.strptime(cliente.fecha_limite_pago, "%Y-%m-%d").date()
                    if fecha_limite < datetime.now().date():
                        tags = ('vencido',)

                self.tree.insert("", "end", values=(
                    cliente.id_cliente,
                    cliente.nombre,
                    cliente.dni or "-",
                    f"${cliente.saldo_deudor:.2f}",
                    datetime.strptime(cliente.fecha_limite_pago, "%Y-%m-%d").strftime("%d/%m/%Y") if cliente.fecha_limite_pago else "-"
                ), iid=cliente.id_cliente, tags=tags)
        except Exception as e:
            messagebox.showerror("Error de Base de Datos", f"No se pudieron cargar los clientes: {e}")
        self.on_client_select() # Para actualizar el estado del botón de editar

    def on_client_select(self, event=None):
        self.edit_client_btn.config(state="normal" if self.tree.selection() else "disabled")

    def abrir_ventana_cliente(self):
        ClientFormWindow(self, callback=self.cargar_clientes, cliente_a_editar=None)

    def abrir_ventana_edicion_cliente(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        
        id_cliente = int(selection[0]) # El iid de la fila es el id_cliente
        cliente_a_editar = obtener_cliente_por_id(id_cliente)
        if not cliente_a_editar:
            messagebox.showerror("Error", "No se pudo encontrar el cliente para editar.")
            return
        
        ClientFormWindow(self, callback=self.cargar_clientes, cliente_a_editar=cliente_a_editar)

    def abrir_detalle_cuenta_cliente(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        
        id_cliente = int(selection[0]) # El iid de la fila es el id_cliente
        cliente = obtener_cliente_por_id(id_cliente)
        if not cliente:
            messagebox.showerror("Error", "No se pudo encontrar el cliente.")
            return
        ClientAccountDetailWindow(self, cliente=cliente, callback=self.cargar_clientes)

    def registrar_pago(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Sin Selección", "Por favor, seleccione un cliente para registrar un pago.")
            return
        
        id_cliente = int(selection[0])
        cliente = obtener_cliente_por_id(id_cliente)

        if not cliente:
            messagebox.showerror("Error", "No se pudo encontrar al cliente.")
            return

        if cliente.saldo_deudor <= 0:
            messagebox.showinfo("Sin Deuda", "El cliente seleccionado no tiene deudas pendientes.")
            return

        # Usamos el nuevo diálogo personalizado que soluciona el problema de decimales y añade "Pago Total"
        dialog = PaymentDialog(self, cliente=cliente)
        monto_pago = dialog.result

        if monto_pago:
            fecha_pago_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if realizar_pago_cliente(id_cliente, monto_pago, fecha_pago_str):
                messagebox.showinfo("Pago Registrado", "El pago se ha registrado con éxito.")
                self.cargar_clientes() # Recargar la lista para ver el saldo actualizado


class VentasView(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.cliente_seleccionado = None # Para guardar el cliente de la venta actual

        self.current_sale_items = {} # Diccionario para llevar la cuenta: {id_producto: DetalleVenta}
        self.total_var = tk.StringVar(value="$0.00") # Cambiado a StringVar para manejar el formato de moneda

        self.search_thread = None # Para manejar el hilo de búsqueda predictiva
        self.search_lock = threading.Lock() # Para sincronizar el acceso a los resultados de búsqueda
        self.create_widgets()

    def on_view_enter(self):
        """Se ejecuta cuando la vista se muestra. Enfoca el campo de búsqueda."""
        self.search_entry.focus_set()

    def create_widgets(self):
        # --- Frame Superior: Búsqueda de producto ---
        search_frame = ttk.Frame(self, padding=10)
        search_frame.pack(side="top", fill="x")

        # --- Frame para Cliente ---
        client_frame = ttk.Frame(self, padding=(10,0,10,10))
        client_frame.pack(side="top", fill="x")
        self.client_label_var = tk.StringVar(value="Cliente: Consumidor Final")
        ttk.Label(client_frame, textvariable=self.client_label_var, font=("Helvetica", 11, "italic")).pack(side="left")
        ttk.Button(client_frame, text="Buscar/Asignar Cliente", command=self.asignar_cliente).pack(side="right")

        ttk.Label(search_frame, text="Buscar producto (nombre o código):", font=("Helvetica", 12)).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var, font=("Helvetica", 14))
        self.search_entry.pack(side="left", fill="x", expand=True, padx=10)
        
        # Eventos para la búsqueda predictiva
        self.search_entry.bind("<KeyRelease>", self.on_key_release)
        self.search_entry.bind("<Return>", self.handle_enter)
        self.search_entry.focus_set()

        # --- Ventana emergente para sugerencias (más usable) ---
        self.suggestions_popup = tk.Toplevel()
        self.suggestions_popup.overrideredirect(True) # Sin bordes ni barra de título
        self.suggestions_popup.withdraw() # Oculta al inicio

        self.suggestions_listbox = tk.Listbox(
            self.suggestions_popup, 
            font=("Helvetica", 12),
            selectbackground="#4CAF50", # Fondo verde para la selección
            selectforeground="white",
            borderwidth=1,
            relief="solid"
        )
        self.suggestions_listbox.pack(fill="both", expand=True)

        # Guardar los productos sugeridos para acceder a ellos fácilmente
        self.suggested_products = []

        # --- Eventos para controlar la visibilidad y navegación ---
        self.suggestions_listbox.bind("<Double-Button-1>", self.select_from_suggestions)
        self.suggestions_listbox.bind("<Return>", self.select_from_suggestions)
        self.search_entry.bind("<Down>", self.move_selection_down)
        self.search_entry.bind("<Up>", self.move_selection_up)
        self.search_entry.bind("<Escape>", lambda e: self.hide_suggestions())
        self.bind_all("<Button-1>", self.check_focus, add="+")

        # --- Frame Central: Carrito de compras ---
        cart_frame = ttk.Frame(self, padding=10)
        cart_frame.pack(side="top", fill="both", expand=True)

        self.cart_tree = ttk.Treeview(
            cart_frame,
            columns=("Nombre", "Cantidad", "Precio", "Desc", "Subtotal"),
            show="headings"
        )
        self.cart_tree.heading("Nombre", text="Producto")
        self.cart_tree.heading("Cantidad", text="Cantidad")
        self.cart_tree.heading("Precio", text="Precio Unit.")
        self.cart_tree.heading("Desc", text="Desc. %")
        self.cart_tree.heading("Subtotal", text="Subtotal")
        self.cart_tree.column("Cantidad", width=100, anchor="center")
        self.cart_tree.column("Precio", width=120, anchor="e")
        self.cart_tree.column("Desc", width=80, anchor="center")
        self.cart_tree.column("Subtotal", width=120, anchor="e")
        self.cart_tree.pack(side="left", fill="both", expand=True)
        # Evento para editar la cantidad con doble clic
        self.cart_tree.bind("<Double-1>", self.edit_cart_item_quantity)
        # Evento para eliminar un item con la tecla Suprimir
        self.cart_tree.bind("<Delete>", self.eliminar_item_del_carrito)

        # --- Frame Inferior: Total y Acciones ---
        summary_frame = ttk.Frame(self, padding=20)
        summary_frame.pack(side="bottom", fill="x")

        ttk.Label(summary_frame, text="TOTAL:", font=("Helvetica", 24, "bold")).pack(side="left")
        ttk.Label(summary_frame, textvariable=self.total_var, font=("Helvetica", 24, "bold"), foreground="#4CAF50").pack(side="left", padx=10)

        ttk.Button(summary_frame, text="Finalizar Venta", command=self.finalize_sale, style="Accent.TButton").pack(side="right")
        ttk.Button(summary_frame, text="Cancelar Venta", command=self.cancel_sale).pack(side="right", padx=10)

    def add_product_to_sale(self, product=None):
        # Esta función ahora se llama con un producto ya encontrado
        if not product:
            return

        # Abrir el diálogo para pedir cantidad y descuento
        dialog = QuantityDialog(self, product)
        result = dialog.result

        # Si el usuario cerró el diálogo o canceló, no hacemos nada
        if not result:
            self.search_var.set("")
            self.hide_suggestions()
            self.search_entry.focus_set()
            return

        cantidad, descuento = result

        # Si el producto ya está en el carrito, sumamos la nueva cantidad
        if product.id_producto in self.current_sale_items:
            detalle_existente = self.current_sale_items[product.id_producto]
            detalle_existente.cantidad += cantidad
            # Por simplicidad, el descuento del diálogo sobreescribe el anterior
            detalle_existente.descuento = descuento
            detalle_existente.subtotal = detalle_existente.calcular_subtotal()
        else:
            # Si es un producto nuevo, creamos el detalle
            self.current_sale_items[product.id_producto] = DetalleVenta(
                id_producto=product.id_producto,
                cantidad=cantidad,
                precio_unitario=product.precio_venta,
                descuento=descuento
            )
        
        self.update_cart_display()
        self.search_var.set("")
        self.hide_suggestions()
        self.search_entry.focus_set()

    def update_cart_display(self):
        # Recopilar todos los IDs de productos en el carrito
        product_ids = list(self.current_sale_items.keys())
        
        # Si no hay productos, limpiar y salir
        if not product_ids:
            for item in self.cart_tree.get_children():
                self.cart_tree.delete(item)
            self.total_var.set("$0.00")
            return

        # Obtener todos los productos en una sola consulta
        # Esto requiere una nueva función en database.py o adaptar una existente
        # Por ahora, simularemos un diccionario de productos para evitar N+1
        # En un escenario real, se haría una consulta como:
        # SELECT id_producto, nombre, precio_venta FROM productos WHERE id_producto IN (...)
        products_info = {p.id_producto: p for p in obtener_productos_por_ids(product_ids)}

        # Limpiar vista
        for item in self.cart_tree.get_children():
            self.cart_tree.delete(item)

        total = 0
        for id_prod, detalle in self.current_sale_items.items():
            product = products_info.get(id_prod)
            if not product: continue # Si el producto no se encontró, lo ignoramos
            
            detalle.subtotal = detalle.calcular_subtotal()
            self.cart_tree.insert("", "end", values=(
                product.nombre,
                detalle.cantidad,
                f"${detalle.precio_unitario:.2f}",
                f"{detalle.descuento:.1f}%",
                f"${detalle.subtotal:.2f}"
            ), iid=id_prod) # Usamos el ID del producto como identificador de la fila
            total += detalle.subtotal
        
        self.total_var.set(f"${total:.2f}")

    def finalize_sale(self):
        if not self.current_sale_items:
            messagebox.showwarning("Venta Vacía", "No hay productos en el carrito.")
            return

        # 1. Crear el objeto Venta con los detalles
        venta = Venta(fecha_venta=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        venta.id_cliente = self.cliente_seleccionado.id_cliente if self.cliente_seleccionado else None
        venta.detalles = list(self.current_sale_items.values())
        venta.calcular_total()

        # 2. Abrir la ventana de pago, pasándole la venta y el callback para limpiar el carrito
        PaymentWindow(self, venta_obj=venta, callback=self.cancel_sale)

    def cancel_sale(self):
        self.current_sale_items.clear()
        self.update_cart_display()
        self.search_var.set("")
        self.cliente_seleccionado = None
        self.client_label_var.set("Cliente: Consumidor Final")
        self.hide_suggestions()

    def check_focus(self, event):
        """Oculta las sugerencias si se hace clic fuera de los widgets relevantes."""
        widget_under_mouse = self.winfo_containing(event.x_root, event.y_root)
        if widget_under_mouse != self.search_entry and widget_under_mouse != self.suggestions_listbox:
            if self.suggestions_popup.winfo_viewable():
                self.hide_suggestions()

    def asignar_cliente(self):
        # Abre una ventana para buscar y seleccionar un cliente
        dialog = SelectClientDialog(self)
        selected_client_id = dialog.result

        if selected_client_id:
            self.cliente_seleccionado = obtener_cliente_por_id(selected_client_id)
            self.client_label_var.set(f"Cliente: {self.cliente_seleccionado.nombre} (DNI: {self.cliente_seleccionado.dni or 'N/A'})")
        else:
            # Si el usuario cierra el diálogo sin seleccionar, reseteamos
            self.cliente_seleccionado = None
            self.client_label_var.set("Cliente: Consumidor Final")

    def eliminar_item_del_carrito(self, event=None):
        """Elimina el item seleccionado del carrito de compras."""
        selection = self.cart_tree.selection()
        if not selection:
            return

        # Preguntar por confirmación para evitar borrados accidentales
        if messagebox.askyesno("Confirmar Eliminación", "¿Está seguro de que desea quitar este producto del carrito?"):
            for item_id_str in selection:
                item_id = int(item_id_str)
                if item_id in self.current_sale_items:
                    del self.current_sale_items[item_id]
            
            # Actualizar la vista del carrito y el total
            self.update_cart_display()

    def edit_cart_item_quantity(self, event=None):
        """Permite editar la cantidad de un item en el carrito con doble clic."""
        selection = self.cart_tree.selection()
        if not selection:
            return

        item_id_str = selection[0]
        item_id = int(item_id_str)

        if item_id not in self.current_sale_items:
            return

        detalle_actual = self.current_sale_items[item_id]
        producto = obtener_producto_por_id(item_id)
        if not producto:
            messagebox.showerror("Error", "El producto ya no se encuentra en la base de datos.")
            del self.current_sale_items[item_id]
            self.update_cart_display()
            return

        # Pedir la nueva cantidad al usuario
        # Reutilizamos el diálogo que ya creamos
        dialog = QuantityDialog(self, producto, initial_quantity=detalle_actual.cantidad, initial_discount=detalle_actual.descuento) # type: ignore
        result = dialog.result

        if not result:
            return

        nueva_cantidad, nuevo_descuento = result

        if nueva_cantidad == 0:
            # Si la cantidad es 0, eliminamos el producto del carrito
            del self.current_sale_items[item_id]
        elif not PERMITIR_STOCK_NEGATIVO and nueva_cantidad > producto.cantidad_stock: # type: ignore
            if messagebox.askyesno("Stock Insuficiente", f"No se puede vender {nueva_cantidad} unidades. Stock disponible: {producto.cantidad_stock}.\n\n¿Desea vender el stock disponible ({producto.cantidad_stock})?", parent=self):
                nueva_cantidad = producto.cantidad_stock
            else:
                return
        else:
            # Actualizar la cantidad en el carrito
            self.current_sale_items[item_id].cantidad = nueva_cantidad
            self.current_sale_items[item_id].descuento = nuevo_descuento
            self.current_sale_items[item_id].subtotal = self.current_sale_items[item_id].calcular_subtotal()

        # Refrescar la vista del carrito y el total
        self.update_cart_display()


    # --- Métodos para Búsqueda Predictiva ---

    def _perform_search_in_thread(self, query):
        """Función que se ejecuta en el hilo de búsqueda."""
        # Aquí se realiza la consulta a la base de datos
        results = obtener_productos(nombre_like=query)
        
        with self.search_lock:
            # Solo actualizamos si esta es la búsqueda más reciente
            if query == self.search_var.get():
                self.after(0, self._update_suggestions_ui, results)

    def _update_suggestions_ui(self, results):
        """Actualiza la UI con los resultados de la búsqueda (ejecutado en el hilo principal)."""
        self.suggestions_listbox.delete(0, "end")
        self.suggested_products = results

        if self.suggested_products:
            for prod in self.suggested_products:
                display_name = prod.nombre
                if prod.volumen:
                    volumen_str = f"{int(prod.volumen)}" if prod.volumen == int(prod.volumen) else f"{prod.volumen}"
                    display_name += f" ({volumen_str}ml/gr)"
                self.suggestions_listbox.insert("end", f" {display_name} - ${prod.precio_venta:.2f}")
            self.show_suggestions()
        else:
            self.hide_suggestions()

    def on_key_release(self, event):
        """Se activa al escribir en el campo de búsqueda."""
        # Ignorar teclas que no modifican el texto
        if event.keysym in ("Up", "Down", "Return", "Escape"):
            return

        current_query = self.search_var.get()
        if len(current_query) < 2:
            self.hide_suggestions()
            return

        # Cancelar el hilo de búsqueda anterior si existe
        if self.search_thread and self.search_thread.is_alive():
            # No hay un método directo para "cancelar" un hilo en Python,
            # pero podemos asegurarnos de que solo el resultado de la última búsqueda sea procesado.
            # Esto se maneja con el `search_lock` y la verificación `if query == self.search_var.get()`
            pass

        # Iniciar una nueva búsqueda en un hilo separado
        self.search_thread = threading.Thread(target=self._perform_search_in_thread, args=(current_query,))
        self.search_thread.daemon = True # Permite que el programa se cierre aunque el hilo esté corriendo
        self.search_thread.start()

    def handle_enter(self, event=None):
        """Decide qué hacer al presionar Enter: añadir por código o desde sugerencias."""
        if self.suggestions_popup.winfo_viewable() and self.suggestions_listbox.curselection():
            # Si la lista de sugerencias está visible, seleccionamos de ahí
            self.select_from_suggestions()
        else:
            # Si no, intentamos buscar por código de barras y luego por nombre
            query = self.search_var.get().strip()
            if query:
                # 1. Intentar buscar por código de barras
                producto_encontrado = obtener_producto_por_codigo_barras(query)
                
                # 2. Si no se encuentra, intentar buscar por nombre exacto
                if producto_encontrado:
                    self.add_product_to_sale(producto_encontrado)
                else:
                    # 3. Fallback: búsqueda flexible por nombre
                    productos_flexibles = obtener_productos(nombre_like=query)
                    if len(productos_flexibles) == 1:
                        # Si hay un único resultado, es muy probable que sea el que el usuario quiere.
                        self.add_product_to_sale(productos_flexibles[0])
                    else:
                        # Si hay 0 o más de 1 resultado, el sistema no puede adivinar.
                        messagebox.showwarning("No Encontrado", f"No se encontró un producto único con '{query}'.\nUse las flechas para seleccionar de la lista o sea más específico.")
                
                # Limpiar siempre después del intento
                self.search_var.set("")

    def select_from_suggestions(self, event=None):
        """Añade el producto seleccionado de la lista de sugerencias."""
        selected_indices = self.suggestions_listbox.curselection()
        if not selected_indices:
            return
        
        selected_product = self.suggested_products[selected_indices[0]]
        self.add_product_to_sale(selected_product)

    def show_suggestions(self):
        """Muestra la lista de sugerencias debajo del campo de búsqueda."""
        if not self.suggestions_popup.winfo_viewable():
            # Calcular la posición en la pantalla
            x = self.search_entry.winfo_rootx()
            y = self.search_entry.winfo_rooty() + self.search_entry.winfo_height()
            width = self.search_entry.winfo_width()
            
            self.suggestions_popup.geometry(f"{width}x150+{x}+{y}") # Ancho del entry, 150px de alto
            self.suggestions_popup.deiconify() # Mostrar la ventana

    def hide_suggestions(self):
        """Oculta la lista de sugerencias."""
        self.suggestions_popup.withdraw()

    def move_selection_down(self, event):
        if not self.suggestions_listbox.winfo_viewable(): return
        current_selection = self.suggestions_listbox.curselection()
        next_selection = 0 if not current_selection else min(current_selection[0] + 1, self.suggestions_listbox.size() - 1)
        self.suggestions_listbox.selection_clear(0, "end")
        self.suggestions_listbox.selection_set(next_selection)
        self.suggestions_listbox.activate(next_selection)

    def move_selection_up(self, event):
        if not self.suggestions_listbox.winfo_viewable(): return
        current_selection = self.suggestions_listbox.curselection()
        next_selection = self.suggestions_listbox.size() - 1 if not current_selection else max(current_selection[0] - 1, 0)
        self.suggestions_listbox.selection_clear(0, "end")
        self.suggestions_listbox.selection_set(next_selection)
        self.suggestions_listbox.activate(next_selection)


class ProductFormWindow(tk.Toplevel):
    def __init__(self, parent, callback, producto_a_editar: Producto | None):
        super().__init__(parent)
        self.parent = parent
        self.callback = callback
        self.producto_a_editar = producto_a_editar

        # Cambiar título dependiendo si es para añadir o editar
        self.title("Editar Producto" if self.producto_a_editar else "Añadir Nuevo Producto")

        self.geometry("450x350")
        self.resizable(False, False)

        # Hacer la ventana modal
        self.grab_set()
        self.transient(parent)

        self.create_form_widgets()
        if self.producto_a_editar:
            self.cargar_datos_producto()

    def create_form_widgets(self):
        form_frame = ttk.Frame(self, padding="10")
        form_frame.pack(fill="both", expand=True)

        # Diccionario para guardar las variables de los Entry
        self.vars = {
            "codigo_barras": tk.StringVar(), # Ahora pertenece al lote inicial
            "nombre": tk.StringVar(),
            "precio_venta": tk.DoubleVar(value=0.0),
            "cantidad_stock": tk.IntVar(value=1), # Solo para creación (lote inicial)
            "fecha_vencimiento": tk.StringVar(), # Solo para creación (lote inicial)
            "volumen": tk.DoubleVar(value=0.0),
            "descripcion": tk.StringVar()
        }

        # --- Campos del formulario ---
        # El formulario ahora se simplifica para creación y edición de datos generales.
        # Nombre
        ttk.Label(form_frame, text="Nombre (*):").grid(row=0, column=0, sticky="w", pady=5)
        nombre_entry = ttk.Entry(form_frame, textvariable=self.vars["nombre"])
        nombre_entry.grid(row=0, column=1, sticky="ew", pady=5)
        if self.producto_a_editar:
            nombre_entry.focus_set()
        else:
            nombre_entry.bind("<Return>", self.buscar_producto_existente)

        # Precio de Venta
        ttk.Label(form_frame, text="Precio Venta (*):").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(form_frame, textvariable=self.vars["precio_venta"]).grid(row=1, column=1, sticky="ew", pady=5)

        # Volumen
        ttk.Label(form_frame, text="Contenido (gr, ml, etc.):").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(form_frame, textvariable=self.vars["volumen"]).grid(row=2, column=1, sticky="ew", pady=5)

        # Descripción
        ttk.Label(form_frame, text="Descripción:").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Entry(form_frame, textvariable=self.vars["descripcion"]).grid(row=3, column=1, sticky="ew", pady=5)

        # Campos solo para CREACIÓN de producto (lote inicial)
        if not self.producto_a_editar:
            ttk.Label(form_frame, text="--- Datos del Lote Inicial ---", font=("Helvetica", 10, "italic")).grid(row=4, column=0, columnspan=2, pady=(10,0))
            
            ttk.Label(form_frame, text="Código de Barras (Lote):").grid(row=5, column=0, sticky="w", pady=5)
            codigo_entry = ttk.Entry(form_frame, textvariable=self.vars["codigo_barras"])
            codigo_entry.grid(row=5, column=1, sticky="ew", pady=5)
            codigo_entry.focus_set()

            ttk.Label(form_frame, text="Cantidad Inicial (*):").grid(row=6, column=0, sticky="w", pady=5)
            ttk.Entry(form_frame, textvariable=self.vars["cantidad_stock"]).grid(row=6, column=1, sticky="ew", pady=5)

            ttk.Label(form_frame, text="Fecha Vencimiento (AAAA-MM-DD):").grid(row=7, column=0, sticky="w", pady=5)
            ttk.Entry(form_frame, textvariable=self.vars["fecha_vencimiento"]).grid(row=7, column=1, sticky="ew", pady=5)
            self.vars["fecha_vencimiento"].set((datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d"))


        form_frame.columnconfigure(1, weight=1)

        # --- Botones ---
        button_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        button_frame.pack(fill="x")

        ttk.Button(button_frame, text="Guardar", command=self.guardar_producto).pack(side="right")
        ttk.Button(button_frame, text="Cancelar", command=self.destroy).pack(side="right", padx=10)

    def buscar_producto_existente(self, event=None):
        """Busca un producto por nombre y si existe, sugiere añadir solo un lote."""
        nombre = self.vars["nombre"].get().strip()
        if not nombre:
            return

        producto_existente = obtener_producto_por_nombre(nombre)
        if producto_existente:
            if messagebox.askyesno("Producto Existente", 
                                   f"El producto '{nombre}' ya existe.\n\n"
                                   "¿Desea añadir un nuevo lote a este producto en lugar de crear uno nuevo?",
                                   parent=self):
                # Cerramos esta ventana y abrimos la de gestión de lotes para ese producto
                self.destroy()
                LoteManagementWindow(self.parent, producto=producto_existente, callback=self.callback)

    def cargar_datos_producto(self):
        """Carga los datos del producto en los campos del formulario."""
        p = self.producto_a_editar
        self.vars["nombre"].set(p.nombre or "")
        self.vars["precio_venta"].set(p.precio_venta or 0.0)
        self.vars["volumen"].set(p.volumen or 0.0)
        self.vars["descripcion"].set(p.descripcion or "")
        # El código de barras ya no es un atributo principal del producto
        self.vars["codigo_barras"].set("")

    def guardar_producto(self):
        # --- Validación de datos ---
        nombre = self.vars["nombre"].get().strip()
        if not nombre:
            messagebox.showerror("Error de Validación", "El campo 'Nombre' es obligatorio.", parent=self)
            return

        try:
            precio = self.vars["precio_venta"].get()
            if precio <= 0:
                raise ValueError()
        except (tk.TclError, ValueError):
            messagebox.showerror("Error de Validación", "El 'Precio' debe ser un número válido y positivo.", parent=self)
            return

        # Validación adicional para el modo creación
        if not self.producto_a_editar:
            try:
                stock_inicial = self.vars["cantidad_stock"].get()
                if stock_inicial < 0: raise ValueError()
            except (tk.TclError, ValueError):
                messagebox.showerror("Error de Validación", "La 'Cantidad Inicial' debe ser un número válido.", parent=self)
                return
            
            fecha_venc_str = self.vars["fecha_vencimiento"].get().strip() or None
            codigo_barras_lote = self.vars["codigo_barras"].get().strip() or None

        if self.producto_a_editar:
            # --- Modo Edición ---
            self.producto_a_editar.nombre = nombre
            self.producto_a_editar.precio_venta = precio
            self.producto_a_editar.volumen = self.vars["volumen"].get() if self.vars["volumen"].get() > 0 else None
            self.producto_a_editar.descripcion = self.vars["descripcion"].get().strip() or None

            if actualizar_producto(self.producto_a_editar):
                messagebox.showinfo("Éxito", f"Producto '{self.producto_a_editar.nombre}' actualizado con éxito.", parent=self)
                self.destroy()
                self.callback()
            else:
                messagebox.showerror("Error de Base de Datos", "No se pudo actualizar el producto.", parent=self)
        else:
            # --- Modo Creación ---
            # Verificar si un producto con ese nombre ya existe
            producto_existente = obtener_producto_por_nombre(nombre)
            if producto_existente:
                messagebox.showerror("Error", f"Ya existe un producto con el nombre '{nombre}'.\nUse la ventana 'Gestionar Lotes' para añadirle stock.", parent=self)
                return

            nuevo_producto = Producto(
                nombre=nombre,
                precio_venta=precio,
                cantidad_stock=stock_inicial,
                volumen=self.vars["volumen"].get() if self.vars["volumen"].get() > 0 else None,
                codigo_barras=codigo_barras_lote, # Código de barras del lote inicial
                descripcion=self.vars["descripcion"].get().strip() or None,
                fecha_vencimiento=fecha_venc_str # Vencimiento del lote inicial
            )

            if agregar_producto(nuevo_producto):
                messagebox.showinfo("Éxito", f"Producto '{nuevo_producto.nombre}' y su lote inicial han sido agregados.", parent=self)
                self.destroy()
                self.callback()
            else:
                messagebox.showerror("Error de Base de Datos", "No se pudo guardar el producto.", parent=self)

class LoteManagementWindow(tk.Toplevel):
    """Ventana para ver, añadir y editar lotes de un producto."""
    def __init__(self, parent, producto: Producto, callback):
        super().__init__(parent)
        self.parent = parent
        self.producto = producto
        self.callback = callback

        self.title(f"Gestionar Lotes de: {self.producto.nombre}")
        self.geometry("750x400") # Ancho aumentado para el código de barras
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)

        self.create_widgets()
        self.cargar_lotes()

    def create_widgets(self):
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill="both", expand=True)

        # Treeview para lotes
        self.tree = ttk.Treeview(main_frame, columns=("ID", "Cantidad", "Vencimiento", "CodigoBarras"), show="headings")
        self.tree.heading("ID", text="ID Lote")
        self.tree.heading("Cantidad", text="Cantidad")
        self.tree.heading("Vencimiento", text="Fecha de Vencimiento")
        self.tree.heading("CodigoBarras", text="Código de Barras")
        self.tree.column("ID", width=80, anchor="center")
        self.tree.column("Cantidad", width=100, anchor="center")
        self.tree.column("Vencimiento", width=150, anchor="center")
        self.tree.column("CodigoBarras", width=250)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self.editar_lote_seleccionado)

        # Botones
        btn_frame = ttk.Frame(main_frame, padding=(0, 10, 0, 0))
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="Añadir Nuevo Lote", command=self.añadir_nuevo_lote).pack(side="left")
        ttk.Button(btn_frame, text="Cerrar", command=self.on_close).pack(side="right")

    def cargar_lotes(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        lotes = obtener_lotes_por_producto(self.producto.id_producto)
        for lote in lotes:
            fecha_venc = lote['fecha_vencimiento']
            if fecha_venc:
                fecha_venc = datetime.strptime(fecha_venc, "%Y-%m-%d").strftime("%d/%m/%Y")
            else:
                fecha_venc = "Sin vencimiento"

            self.tree.insert("", "end", iid=lote['id_stock'], values=(
                lote['id_stock'],
                lote['cantidad'],
                fecha_venc,
                lote.get('codigo_barras', 'N/A') or "N/A" # Usamos .get() para seguridad
            ))

    def añadir_nuevo_lote(self):
        dialog = LoteFormDialog(self, title="Añadir Nuevo Lote")
        if dialog.result:
            cantidad, fecha_venc, codigo_barras = dialog.result
            if agregar_lote(self.producto.id_producto, cantidad, fecha_venc, codigo_barras):
                self.cargar_lotes()
            else:
                messagebox.showerror("Error", "No se pudo añadir el nuevo lote.", parent=self)

    def editar_lote_seleccionado(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        
        id_lote = int(selection[0])
        # Necesitamos obtener los datos originales para pre-llenar el formulario
        lotes = obtener_lotes_por_producto(self.producto.id_producto)
        lote_a_editar = next((l for l in lotes if l['id_stock'] == id_lote), None)
        if not lote_a_editar: return

        dialog = LoteFormDialog(self, title="Editar Lote", initial_data=lote_a_editar)
        if dialog.result:
            cantidad, fecha_venc, codigo_barras = dialog.result
            if actualizar_lote(id_lote, cantidad, fecha_venc, codigo_barras):
                self.cargar_lotes()
            else:
                messagebox.showerror("Error", "No se pudo actualizar el lote.", parent=self)

    def on_close(self):
        self.callback() # Llama a la función de recarga de la vista principal
        self.destroy()

class LoteFormDialog(simpledialog.Dialog):
    """Diálogo simple para añadir/editar un lote."""
    def __init__(self, parent, title, initial_data=None):
        self.initial_data = initial_data
        super().__init__(parent, title)

    def body(self, master):
        ttk.Label(master, text="Cantidad:").grid(row=0, sticky="w", pady=2)
        ttk.Label(master, text="Fecha Vencimiento (AAAA-MM-DD):").grid(row=1, sticky="w", pady=2)
        ttk.Label(master, text="Código de Barras:").grid(row=2, sticky="w", pady=2)

        self.cantidad_var = tk.IntVar(value=self.initial_data['cantidad'] if self.initial_data else 1)
        self.fecha_var = tk.StringVar(value=self.initial_data['fecha_vencimiento'] if self.initial_data and self.initial_data['fecha_vencimiento'] else "")
        self.codigo_var = tk.StringVar(value=self.initial_data.get('codigo_barras') if self.initial_data and self.initial_data.get('codigo_barras') else "")

        self.cantidad_entry = ttk.Entry(master, textvariable=self.cantidad_var)
        self.fecha_entry = ttk.Entry(master, textvariable=self.fecha_var)
        self.codigo_entry = ttk.Entry(master, textvariable=self.codigo_var)

        self.cantidad_entry.grid(row=0, column=1, pady=5, sticky="ew")
        self.fecha_entry.grid(row=1, column=1, pady=5, sticky="ew")
        self.codigo_entry.grid(row=2, column=1, pady=5, sticky="ew")
        
        master.columnconfigure(1, weight=1)
        return self.cantidad_entry # Foco inicial

    def apply(self):
        try:
            cantidad = self.cantidad_var.get()
            fecha_str = self.fecha_var.get().strip()
            codigo_str = self.codigo_var.get().strip()

            if cantidad < 0:
                messagebox.showwarning("Dato Inválido", "La cantidad no puede ser negativa.", parent=self)
                return

            if fecha_str:
                # Validar formato de fecha
                datetime.strptime(fecha_str, "%Y-%m-%d")
            else:
                fecha_str = None # Guardar como NULL si está vacío

            codigo_str = codigo_str if codigo_str else None

            self.result = (cantidad, fecha_str, codigo_str)
        except ValueError:
            messagebox.showwarning("Formato Incorrecto", "La fecha debe tener el formato AAAA-MM-DD.", parent=self)
        except tk.TclError:
            messagebox.showwarning("Dato Inválido", "La cantidad debe ser un número entero.", parent=self)


class PaymentWindow(tk.Toplevel):
    """Ventana modal para seleccionar el método de pago y finalizar la venta."""
    def __init__(self, parent, venta_obj: Venta, callback):
        super().__init__(parent)
        self.parent = parent
        self.venta = venta_obj
        self.callback = callback # Función para limpiar el carrito

        self.title("Finalizar Venta")
        self.geometry("400x380")
        self.resizable(False, False)
        self.grab_set() # Hacer modal
        self.transient(parent)

        self.create_widgets()

    def create_widgets(self):
        main_frame = ttk.Frame(self, padding=20)
        main_frame.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Mostrar total
        ttk.Label(main_frame, text="Total a Pagar:", font=("Helvetica", 14)).grid(row=0, column=0, columnspan=2, pady=(0, 5))
        ttk.Label(main_frame, text=f"${self.venta.total:.2f}", font=("Helvetica", 28, "bold"), foreground="#4CAF50").grid(row=1, column=0, columnspan=2, pady=(0, 20))

        # Botones de métodos de pago
        ttk.Button(main_frame, text="Efectivo", command=self.pay_cash, style="Accent.TButton").grid(row=2, column=0, sticky="ew", padx=(0, 5), pady=5)
        ttk.Button(main_frame, text="Tarjeta (Débito/Crédito)", command=self.pay_card).grid(row=2, column=1, sticky="ew", padx=(5, 0), pady=5)
        ttk.Button(main_frame, text="Billetera Virtual (QR)", command=self.pay_qr).grid(row=3, column=0, sticky="ew", padx=(0, 5), pady=5)
        ttk.Button(main_frame, text="Transferencia Bancaria", command=self.pay_transfer).grid(row=3, column=1, sticky="ew", padx=(5, 0), pady=5)

        # El botón de fiar solo se activa si hay un cliente seleccionado
        fiar_button = ttk.Button(main_frame, text="Fiar (Anotar en Libreta)", command=self.pay_credit, state="normal" if self.venta.id_cliente else "disabled")
        fiar_button.grid(row=4, column=0, columnspan=2, sticky="ew", pady=5)

        # Configurar las columnas para que se expandan uniformemente
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_columnconfigure(1, weight=1)

    def pay_cash(self):
        self.venta.forma_pago = "Efectivo"
        self.complete_sale()

    def pay_card(self):
        """Simula la interacción con un terminal de pago (POSnet)."""
        messagebox.showinfo(
            "Terminal de Pago",
            "Por favor, utilice el terminal de pago para completar la transacción.",
            parent=self
        )

        # --- SIMULACIÓN ---
        # TODO: Aquí iría el código real del SDK de tu proveedor de POS.
        # Ejemplo: terminal_sdk.iniciar_pago(self.venta.total)
        # La respuesta del SDK determinaría si el pago fue aprobado.
        pago_aprobado = messagebox.askyesno(
            "Confirmación de Pago",
            "¿El pago con tarjeta fue APROBADO?",
            parent=self
        )

        if pago_aprobado:
            self.venta.forma_pago = "Tarjeta"
            self.complete_sale()
        else:
            messagebox.showerror("Pago Fallido", "La transacción con tarjeta fue rechazada o cancelada.", parent=self)

    def pay_qr(self):
        """Genera una orden de pago con Mercado Pago y muestra el QR."""
        # Si estamos en modo de prueba, usamos la simulación anterior.
        if MODO_PRUEBA_PAGOS:
            messagebox.showinfo("Modo de Prueba", "Simulando generación de QR...", parent=self)
            pago_aprobado = messagebox.askyesno(
                "Confirmación de Pago (Prueba)",
                "¿Simular que el pago con QR fue APROBADO?",
                parent=self
            )
            if pago_aprobado:
                self.venta.forma_pago = "QR (Simulado)"
                self.complete_sale()
            else:
                messagebox.showerror("Pago Cancelado", "La simulación de pago con QR fue cancelada.", parent=self)
            return # Salimos de la función para no ejecutar el código real de la API

        # --- CÓDIGO REAL DE LA API (solo se ejecuta si MODO_PRUEBA_PAGOS es False) ---
        try:
            # --- CONFIGURACIÓN (¡IMPORTANTE!) ---
            # Validar que el total sea mayor a cero
            if self.venta.total <= 0:
                messagebox.showerror("Error", "No se puede generar un pago para una venta con total de $0.00.", parent=self)
                return
            
            # Validar que el Access Token esté configurado
            if not MP_ACCESS_TOKEN or MP_ACCESS_TOKEN == 'TU_ACCESS_TOKEN_VA_AQUI' or MP_ACCESS_TOKEN == 'NO_CONFIGURADO':
                messagebox.showerror("Configuración Requerida", 
                                     "El Access Token de Mercado Pago no está configurado.\n\n"
                                     "Por favor, edite el archivo 'config.ini' y añada su credencial.", parent=self)
                return

            sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

            # --- CREACIÓN DE LA ORDEN DE PAGO ---
            order_data = {
                "items": [
                    {
                        "title": f"Compra en EasySt - Venta #{self.venta.id_venta or 'N/A'}",
                        "quantity": 1,
                        "unit_price": self.venta.total
                    }
                ],
                "external_reference": f"VENTA-{self.venta.id_venta or 'NUEVA'}-{datetime.now().timestamp()}",
                "title": f"Compra en {NOMBRE_NEGOCIO}",
                "total_amount": self.venta.total,
                "description": f"Orden de pago para la venta #{self.venta.id_venta or 'N/A'}"
            }

            order_response = sdk.instore_order().create(order_data)
            
            # --- VERIFICACIÓN ROBUSTA DE LA RESPUESTA DE LA API ---
            if order_response.get("status") not in [200, 201]:
                error_info = order_response.get("response", {})
                error_message = error_info.get("message", "Error desconocido de la API.")
                messagebox.showerror("Error de Mercado Pago", f"No se pudo crear la orden de pago:\n{error_message}\n\nVerifique que su Access Token sea correcto y esté activado para producción.", parent=self)
                return
            
            # La cadena de datos que se convierte en QR
            qr_data_string = order_response["response"].get("qr_data")
            if not qr_data_string:
                messagebox.showerror("Error de Mercado Pago", "La API no devolvió los datos del QR.", parent=self)

            # --- MOSTRAR EL QR EN UNA VENTANA ---
            self.show_qr_window(qr_data_string)

            # --- CONFIRMACIÓN MANUAL (para este ejemplo) ---
            # En un sistema real, usarías "Webhooks" para que MP te avise automáticamente cuando el pago se completa.
            # Por ahora, seguimos confirmando manualmente.
            pago_aprobado = messagebox.askyesno(
                "Confirmación de Pago",
                "El cliente ha escaneado el QR y completado el pago?",
                parent=self
            )

            if pago_aprobado:
                self.venta.forma_pago = "QR (Mercado Pago)"
                self.complete_sale()
            else:
                messagebox.showerror("Pago Cancelado", "La transacción con QR fue cancelada.", parent=self)
        except Exception as e:
            messagebox.showerror("Error de API", f"No se pudo generar el QR de Mercado Pago:\n{e}", parent=self)

    def pay_transfer(self):
        """Simula la interacción para un pago por transferencia."""
        messagebox.showinfo(
            "Pago por Transferencia",
            "Por favor, realice la transferencia y muestre el comprobante.",
            parent=self
        )

        # --- SIMULACIÓN ---
        pago_aprobado = messagebox.askyesno(
            "Confirmación de Pago",
            "¿Se recibió la transferencia correctamente?",
            parent=self
        )

        if pago_aprobado:
            self.venta.forma_pago = "Transferencia"
            self.complete_sale()
        else:
            messagebox.showerror("Pago Fallido", "La transferencia fue rechazada o no se confirmó.", parent=self)


    def pay_credit(self):
        """Añade el total de la venta al saldo deudor del cliente."""
        if not self.venta.id_cliente:
            messagebox.showerror("Error", "No hay un cliente seleccionado para esta venta.", parent=self)
            return
        
        cliente = obtener_cliente_por_id(self.venta.id_cliente)
        confirm = messagebox.askyesno(
            "Confirmar Fiar",
            f"¿Desea añadir ${self.venta.total:.2f} a la deuda de {cliente.nombre}?",
            parent=self
        )
        if confirm:
            self.venta.forma_pago = "Libreta"
            self.complete_sale()

    def complete_sale(self):
        """Registra la venta en la BD y cierra la ventana."""
        id_venta_nueva = registrar_venta(self.venta)
        if id_venta_nueva:
            self.venta.id_venta = id_venta_nueva # Asignamos el ID a la venta para usarlo en el ticket
            messagebox.showinfo("Venta Registrada", f"Venta registrada con éxito.\nMétodo de pago: {self.venta.forma_pago}.", parent=self)

            # Preguntar si se desea imprimir el ticket
            if messagebox.askyesno("Imprimir Ticket", "¿Desea imprimir el ticket de venta?", parent=self):
                self.print_ticket()

            self.destroy()
            self.callback() # Limpia el carrito en la vista principal
        else:
            messagebox.showerror("Error de Base de Datos", "No se pudo registrar la venta. El stock no ha sido modificado.", parent=self)

    def show_qr_window(self, qr_data):
        """Crea y muestra una ventana con el código QR generado."""
        qr_window = tk.Toplevel(self)
        qr_window.title("Escanear para Pagar")
        qr_window.resizable(False, False)
        qr_window.grab_set()

        # Generar la imagen del QR
        qr_img = qrcode.make(qr_data)
        qr_img = qr_img.resize((300, 300)) # Ajustar tamaño
        
        # Convertir a formato de Tkinter
        photo = ImageTk.PhotoImage(qr_img)

        # Mostrar la imagen
        qr_label = ttk.Label(qr_window, image=photo)
        qr_label.image = photo # Guardar referencia para que no sea eliminada por el recolector de basura
        qr_label.pack(padx=20, pady=20)

        ttk.Label(qr_window, text="Pídele al cliente que escanee el código con la app de Mercado Pago.", wraplength=300).pack(pady=(0, 10))


    def print_ticket(self):
        """Intenta imprimir el ticket en una impresora térmica, si no, lo muestra en pantalla."""
        ticket_content = generar_texto_ticket(self.venta)

        try:
            # Validar que la configuración de la impresora exista
            if not all([PRINTER_VENDOR_ID, PRINTER_PRODUCT_ID]):
                raise ValueError("La configuración de la impresora (idVendor, idProduct) no está definida en config.ini.")

            # Convertir IDs a enteros hexadecimales
            vendor_id = int(PRINTER_VENDOR_ID, 16)
            product_id = int(PRINTER_PRODUCT_ID, 16)

            # --- IMPRESIÓN REAL ---
            # Debes encontrar el idVendor y idProduct de tu impresora.
            # En Windows: Administrador de Dispositivos -> Propiedades de la impresora -> Detalles -> Id. de hardware.
            # Ejemplo para una impresora Epson TM-T20:
            # p = Usb(0x04b8, 0x0e15, 0)
            p = Usb(vendor_id, product_id, 0, profile=PRINTER_PROFILE)
            p.text(ticket_content)
            p.cut()
            p.close()
            messagebox.showinfo("Impresión", "El ticket se ha enviado a la impresora.", parent=self)
        except Exception as e:
            print(f"Error de impresora: {e}")
            messagebox.showwarning("Error de Impresora", 
                "No se pudo conectar con la impresora de tickets.\n"
                f"Error: {e}\n\n"
                "Mostrando ticket en pantalla para copia de seguridad.", parent=self)


class QuantityDialog(tk.Toplevel):
    """Diálogo para pedir cantidad y descuento de un producto."""
    def __init__(self, parent, product: Producto, initial_quantity=1, initial_discount=0.0):
        super().__init__(parent)
        self.parent = parent
        self.product = product
        self.result = None

        self.title("Añadir al Carrito")
        self.geometry("350x250")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        # Variables
        self.quantity_var = tk.IntVar(value=initial_quantity)
        self.discount_var = tk.DoubleVar(value=initial_discount)

        self.create_widgets()

        # Esperar a que la ventana se cierre
        self.wait_window(self)

    def create_widgets(self):
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=self.product.nombre, font=("Helvetica", 14, "bold")).pack(pady=(0, 10))

        # Cantidad
        qty_frame = ttk.Frame(frame)
        qty_frame.pack(fill="x", pady=5)
        ttk.Label(qty_frame, text="Cantidad:", font=("Helvetica", 12)).pack(side="left")
        qty_entry = ttk.Entry(qty_frame, textvariable=self.quantity_var, font=("Helvetica", 12), width=10)
        qty_entry.pack(side="right")
        qty_entry.focus_set()
        qty_entry.selection_range(0, 'end')

        # Descuento
        disc_frame = ttk.Frame(frame)
        disc_frame.pack(fill="x", pady=5)
        ttk.Label(disc_frame, text="Descuento (%):", font=("Helvetica", 12)).pack(side="left")
        ttk.Entry(disc_frame, textvariable=self.discount_var, font=("Helvetica", 12), width=10).pack(side="right")

        # Botones
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=(20, 0))
        ttk.Button(btn_frame, text="Aceptar", command=self.on_ok, style="Accent.TButton").pack(side="right")
        ttk.Button(btn_frame, text="Cancelar", command=self.destroy).pack(side="right", padx=10)

        self.bind("<Return>", self.on_ok)
        self.bind("<Escape>", lambda e: self.destroy())

    def on_ok(self, event=None):
        try:
            cantidad = self.quantity_var.get()
            descuento = self.discount_var.get()

            if cantidad < 0 or descuento < 0 or descuento > 100:
                raise ValueError("Valores inválidos")

            # Solo validamos el stock si la configuración lo exige
            if not PERMITIR_STOCK_NEGATIVO and cantidad > self.product.cantidad_stock:
                if messagebox.askyesno("Stock Insuficiente", f"Stock disponible: {self.product.cantidad_stock} unidades.\n\n¿Desea vender el stock disponible?", parent=self):
                    cantidad = self.product.cantidad_stock
                else:
                    return

            self.result = (cantidad, descuento)
            self.destroy()
        except (tk.TclError, ValueError):
            messagebox.showerror("Entrada Inválida", "Por favor, ingrese números válidos.", parent=self)


class ClientFormWindow(tk.Toplevel):
    def __init__(self, parent, callback, cliente_a_editar: Cliente | None):
        super().__init__(parent)
        self.parent = parent
        self.callback = callback
        self.cliente_a_editar = cliente_a_editar

        self.title("Editar Cliente" if self.cliente_a_editar else "Añadir Nuevo Cliente")
        self.geometry("400x200")
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)

        self.vars = {
            "nombre": tk.StringVar(),
            "dni": tk.StringVar(),
            "fecha_limite_pago": tk.StringVar()
        }

        self.create_widgets()
        if self.cliente_a_editar:
            self.cargar_datos_cliente()
        else:
            # Sugerir fecha límite a 30 días para nuevos clientes
            self.vars["fecha_limite_pago"].set((datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"))

    def create_widgets(self):
        form_frame = ttk.Frame(self, padding="10")
        form_frame.pack(fill="both", expand=True)

        ttk.Label(form_frame, text="Nombre y Apellido (*):").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(form_frame, textvariable=self.vars["nombre"]).grid(row=0, column=1, sticky="ew", pady=5)

        ttk.Label(form_frame, text="DNI:").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(form_frame, textvariable=self.vars["dni"]).grid(row=1, column=1, sticky="ew", pady=5)

        ttk.Label(form_frame, text="Fecha Límite de Pago (AAAA-MM-DD):").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(form_frame, textvariable=self.vars["fecha_limite_pago"]).grid(row=2, column=1, sticky="ew", pady=5)

        form_frame.columnconfigure(1, weight=1)

        button_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        button_frame.pack(fill="x", side="bottom")
        ttk.Button(button_frame, text="Guardar", command=self.guardar_cliente).pack(side="right")
        ttk.Button(button_frame, text="Cancelar", command=self.destroy).pack(side="right", padx=10)

    def cargar_datos_cliente(self):
        c = self.cliente_a_editar
        self.vars["nombre"].set(c.nombre or "")
        self.vars["dni"].set(c.dni or "")
        self.vars["fecha_limite_pago"].set(c.fecha_limite_pago or "")

    def guardar_cliente(self):
        nombre = self.vars["nombre"].get().strip()
        if not nombre:
            messagebox.showerror("Error de Validación", "El campo 'Nombre y Apellido' es obligatorio.", parent=self)
            return
        
        dni = self.vars["dni"].get().strip() or None

        fecha_limite = self.vars["fecha_limite_pago"].get().strip() or None

        if self.cliente_a_editar:
            self.cliente_a_editar.nombre = nombre
            self.cliente_a_editar.dni = dni
            self.cliente_a_editar.fecha_limite_pago = fecha_limite
            if actualizar_cliente(self.cliente_a_editar):
                messagebox.showinfo("Éxito", "Cliente actualizado con éxito.", parent=self)
                self.destroy()
                self.callback()
            else:
                messagebox.showerror("Error", "No se pudo actualizar el cliente. Verifique que el DNI no esté duplicado.", parent=self)
        else:
            nuevo_cliente = Cliente(nombre=nombre, dni=dni, fecha_limite_pago=fecha_limite)
            id_nuevo = agregar_cliente(nuevo_cliente)
            if id_nuevo:
                messagebox.showinfo("Éxito", "Cliente agregado con éxito.", parent=self)
                self.destroy()
                self.callback()
            else:
                messagebox.showerror("Error", "No se pudo agregar el cliente. Verifique que el DNI no esté duplicado.", parent=self)

class PaymentDialog(tk.Toplevel):
    """Diálogo personalizado para registrar un pago, solucionando el problema de decimales."""
    def __init__(self, parent, cliente: Cliente):
        super().__init__(parent)
        self.parent = parent
        self.cliente = cliente
        self.result = None

        self.title("Registrar Pago")
        self.geometry("400x220")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.monto_var = tk.DoubleVar()
        self.cliente = obtener_cliente_por_id(cliente.id_cliente) # Recargar para tener el saldo más actual
        self.create_widgets()
        self.wait_window(self)

    def create_widgets(self):
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=f"Cliente: {self.cliente.nombre}", font=("Helvetica", 12)).pack(anchor="w")
        ttk.Label(frame, text=f"Deuda Actual: ${self.cliente.saldo_deudor:.2f}", font=("Helvetica", 14, "bold"), foreground="#D32F2F").pack(anchor="w", pady=(5, 15))

        entry_frame = ttk.Frame(frame)
        entry_frame.pack(fill="x")
        ttk.Label(entry_frame, text="Monto a Pagar:", font=("Helvetica", 11)).pack(side="left")
        monto_entry = ttk.Entry(entry_frame, textvariable=self.monto_var, font=("Helvetica", 11))
        monto_entry.pack(side="right", fill="x", expand=True)
        monto_entry.focus_set()

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=(20, 0))
        ttk.Button(btn_frame, text="Aceptar", command=self.on_ok, style="Accent.TButton").pack(side="right")
        ttk.Button(btn_frame, text="Pago Total", command=self.set_total_payment).pack(side="right", padx=10)

        self.bind("<Return>", self.on_ok)
        self.bind("<Escape>", lambda e: self.destroy())

    def set_total_payment(self):
        self.monto_var.set(round(self.cliente.saldo_deudor, 2))

    def on_ok(self, event=None):
        try:
            monto = self.monto_var.get()
            if monto <= 0 or monto > round(self.cliente.saldo_deudor, 2) + 0.001: # Pequeña tolerancia
                raise ValueError("Monto inválido")
            self.result = monto
            self.destroy()
        except (tk.TclError, ValueError):
            messagebox.showerror("Monto Inválido", f"El monto debe ser un número positivo y no puede superar la deuda de ${self.cliente.saldo_deudor:.2f}.", parent=self)

class SelectClientDialog(tk.Toplevel):
    """Diálogo para buscar y seleccionar un cliente."""
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.result = None

        self.title("Seleccionar Cliente")
        self.geometry("500x400")
        self.transient(parent)
        self.grab_set()

        self.create_widgets()
        self.cargar_clientes()

        self.wait_window(self)

    def create_widgets(self):
        search_frame = ttk.Frame(self, padding=10)
        search_frame.pack(fill="x")
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        search_entry.pack(fill="x", expand=True, side="left", padx=(0, 5))
        search_entry.bind("<KeyRelease>", lambda e: self.cargar_clientes())
        
        # Botón para añadir un nuevo cliente directamente desde el diálogo
        add_client_button = ttk.Button(search_frame, text="Añadir Nuevo Cliente", command=self.add_new_client)
        add_client_button.pack(side="right")


        tree_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=("Nombre", "DNI"), show="headings")
        self.tree.heading("Nombre", text="Nombre")
        self.tree.heading("DNI", text="DNI")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self.on_select)

    def cargar_clientes(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        clientes = obtener_clientes(nombre_o_dni=self.search_var.get())
        for cliente in clientes:
            self.tree.insert("", "end", values=(cliente.nombre, cliente.dni or "-"), iid=cliente.id_cliente)

    def on_select(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        
        self.result = int(selection[0]) # El iid de la fila es el id_cliente
        self.destroy()

    def add_new_client(self):
        # Abrimos el formulario de cliente. El callback recargará la lista en este mismo diálogo.
        ClientFormWindow(self, callback=self.cargar_clientes, cliente_a_editar=None)
        # Hacemos que la ventana de creación sea modal respecto a esta de selección
        self.wait_window()

class ClientAccountDetailWindow(tk.Toplevel):
    """Muestra el historial de movimientos (deudas y pagos) de un cliente."""
    def __init__(self, parent, cliente: Cliente, callback):
        super().__init__(parent)
        self.parent = parent
        self.cliente = cliente
        self.callback = callback # Para refrescar la lista de clientes al cerrar

        self.title(f"Estado de Cuenta - {self.cliente.nombre}")
        self.geometry("800x500")
        self.transient(parent)
        self.grab_set()

        self.create_widgets()
        self.cargar_movimientos()

        self.tree.bind("<Double-1>", self.abrir_detalle_venta)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def create_widgets(self):
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill="both", expand=True)

        # Cabecera con el saldo actual
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(header_frame, text="Saldo Deudor Actual:", font=("Helvetica", 12)).pack(side="left")
        self.saldo_label = ttk.Label(header_frame, text=f"${self.cliente.saldo_deudor:.2f}", font=("Helvetica", 14, "bold"), foreground="#D32F2F")
        self.saldo_label.pack(side="left", padx=10)

        # Treeview para los movimientos
        tree_frame = ttk.Frame(main_frame)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(tree_frame, columns=("Fecha", "Tipo", "Detalle", "Monto"), show="headings")
        self.tree.heading("Fecha", text="Fecha y Hora")
        self.tree.heading("Tipo", text="Tipo")
        self.tree.heading("Detalle", text="Detalle")
        self.tree.heading("Monto", text="Monto")

        self.tree.column("Fecha", width=150, anchor="w")
        self.tree.column("Tipo", width=80, anchor="center")
        self.tree.column("Detalle", width=400)
        self.tree.column("Monto", width=120, anchor="e")

        # Scrollbar
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Tags para colorear filas
        self.tree.tag_configure('deuda', foreground='red')
        self.tree.tag_configure('pago', foreground='green')

    def cargar_movimientos(self):
        movimientos = obtener_movimientos_cliente(self.cliente.id_cliente)
        for mov in movimientos:
            fecha_hora = datetime.strptime(mov['fecha'], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M")
            tipo = mov['tipo_movimiento']
            monto = mov['monto_actualizado']
            detalle = mov['detalle_productos'] or "Pago recibido"
            
            signo = "-" if tipo == 'DEUDA' else "+"
            tags = ('deuda',) if tipo == 'DEUDA' else ('pago',)

            # Guardamos el id_venta en el iid de la fila para poder recuperarlo
            self.tree.insert("", "end", iid=mov['id_venta'] or '', values=(fecha_hora, tipo, detalle, f"{signo} ${monto:.2f}"), tags=tags)

    def on_close(self):
        self.callback() # Llama a la función de recarga de la vista de clientes
        self.destroy()

    def abrir_detalle_venta(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return

        id_venta = selection[0]
        # Solo abrimos el detalle si es una deuda (tiene id_venta)
        if not id_venta:
            return

        # Buscamos la venta completa en la base de datos
        venta_seleccionada = obtener_venta_por_id(int(id_venta))

        if venta_seleccionada:
            SaleDetailWindow(self, venta_seleccionada)
        

class ReportesView(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        
        self.periodos = {
            "Hoy": 0,
            "Esta Semana": 1,
            "Este Mes": 2,
            "Este Año": 3
        }

        # Crear un Notebook (pestañas)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        # Crear el frame para la pestaña de Reporte de Ventas
        self.ventas_report_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.ventas_report_frame, text='Reporte de Ventas')
        self.create_sales_report_widgets(self.ventas_report_frame)

        # Crear el frame para la pestaña de Sugerencias de Reposición
        self.reposicion_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.reposicion_frame, text='Sugerencias de Compra')
        self.create_restock_suggestion_widgets(self.reposicion_frame)
        self.on_period_change()  # Cargar reporte de hoy por defecto

    def create_sales_report_widgets(self, parent_frame):
        # --- Controles ---
        controls_frame = ttk.Frame(parent_frame)
        controls_frame.pack(fill="x")
        ttk.Label(controls_frame, text="Reporte de Ventas:", font=("Helvetica", 16, "bold")).pack(side="left")

        self.periodo_var = tk.StringVar(value="Hoy")
        periodo_combo = ttk.Combobox(controls_frame, textvariable=self.periodo_var, values=list(self.periodos.keys()), state="readonly", width=15)
        periodo_combo.pack(side="left", padx=10)

        # Espaciador
        ttk.Frame(controls_frame).pack(side="left", expand=True)
        self.ver_ticket_button = ttk.Button(controls_frame, text="Ver Ticket", command=self.ver_ticket, state="disabled")
        self.ver_ticket_button.pack(side="right", padx=(0, 10))
        ttk.Button(controls_frame, text="Exportar a Excel", command=self.exportar_a_excel).pack(side="right")

        # Aquí se podrían añadir selectores de fecha en el futuro

        # --- Panel principal dividido ---
        main_pane = ttk.PanedWindow(parent_frame, orient="horizontal")
        main_pane.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Panel Izquierdo (Tablas) ---
        left_pane = ttk.PanedWindow(main_pane, orient="vertical")
        main_pane.add(left_pane, weight=2)

        # --- Panel Derecho (Estadísticas y Gráfico) ---
        right_frame = ttk.Frame(main_pane, padding=10)
        main_pane.add(right_frame, weight=1)

        # --- Contenido del Panel Izquierdo ---
        # Panel superior izquierdo: Resumen de ventas
        ventas_frame = ttk.Frame(left_pane, padding=10)
        left_pane.add(ventas_frame, weight=1)

        self.report_title_var = tk.StringVar()
        ttk.Label(ventas_frame, textvariable=self.report_title_var, font=("Helvetica", 12)).pack(anchor="w")
        self.ventas_tree = ttk.Treeview(ventas_frame, columns=("ID", "Hora", "Cliente", "Total", "Pago"), show="headings")
        self.ventas_tree.heading("ID", text="ID Venta")
        self.ventas_tree.heading("Hora", text="Hora")
        self.ventas_tree.heading("Cliente", text="Cliente")
        self.ventas_tree.heading("Total", text="Total")
        self.ventas_tree.heading("Pago", text="Forma de Pago")
        # Definimos un ancho mínimo para las columnas fijas y dejamos que "Cliente" se estire
        self.ventas_tree.column("ID", width=80, minwidth=80, anchor="center")
        self.ventas_tree.column("Hora", width=100, minwidth=100, anchor="center")
        self.ventas_tree.column("Total", width=120, minwidth=120, anchor="e")
        self.ventas_tree.column("Pago", width=150, minwidth=150, anchor="center")
        self.ventas_tree.column("Cliente", width=250, minwidth=200) # Esta columna se estirará
        
        # Scrollbars
        v_scrollbar = ttk.Scrollbar(ventas_frame, orient="vertical", command=self.ventas_tree.yview)
        h_scrollbar = ttk.Scrollbar(ventas_frame, orient="horizontal", command=self.ventas_tree.xview)
        self.ventas_tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        
        h_scrollbar.pack(side="bottom", fill="x")
        self.ventas_tree.pack(fill="both", expand=True, pady=5)
        v_scrollbar.pack(side="right", fill="y")
        self.ventas_tree.bind("<<TreeviewSelect>>", self.mostrar_detalle_venta)

        # Panel inferior izquierdo: Detalle de la venta seleccionada
        detalles_frame = ttk.Frame(left_pane, padding=10)
        left_pane.add(detalles_frame, weight=1)

        ttk.Label(detalles_frame, text="Detalle de la Venta Seleccionada", font=("Helvetica", 12)).pack(anchor="w")
        self.detalles_tree = ttk.Treeview(detalles_frame, columns=("Cant", "Producto", "P.Unit", "Desc", "Subtotal"), show="headings")
        self.detalles_tree.heading("Cant", text="Cant.")
        self.detalles_tree.heading("Producto", text="Producto")
        self.detalles_tree.heading("P.Unit", text="P. Unitario")
        self.detalles_tree.heading("Desc", text="Desc. %")
        self.detalles_tree.heading("Subtotal", text="Subtotal")
        # Definimos un ancho mínimo para las columnas fijas y dejamos que "Producto" se estire
        self.detalles_tree.column("Cant", width=60, minwidth=60, anchor="center")
        self.detalles_tree.column("P.Unit", width=100, minwidth=100, anchor="e")
        self.detalles_tree.column("Desc", width=80, minwidth=80, anchor="center")
        self.detalles_tree.column("Subtotal", width=120, minwidth=120, anchor="e")
        self.detalles_tree.column("Producto", width=300, minwidth=250) # Esta columna se estirará

        # Scrollbars
        v_scrollbar_d = ttk.Scrollbar(detalles_frame, orient="vertical", command=self.detalles_tree.yview)
        h_scrollbar_d = ttk.Scrollbar(detalles_frame, orient="horizontal", command=self.detalles_tree.xview)
        self.detalles_tree.configure(yscrollcommand=v_scrollbar_d.set, xscrollcommand=h_scrollbar_d.set)

        h_scrollbar_d.pack(side="bottom", fill="x")
        self.detalles_tree.pack(fill="both", expand=True, pady=5)
        v_scrollbar_d.pack(side="right", fill="y")

        # --- Contenido del Panel Derecho ---
        stats_frame = ttk.LabelFrame(right_frame, text="Estadísticas del Día", padding=15)
        stats_frame.pack(fill="x", pady=(0, 10))

        self.total_recaudado_var = tk.StringVar(value="Total Recaudado: $0.00")
        self.num_ventas_var = tk.StringVar(value="Nº de Ventas: 0")
        self.ticket_promedio_var = tk.StringVar(value="Ticket Promedio: $0.00")

        ttk.Label(stats_frame, textvariable=self.total_recaudado_var, font=("Helvetica", 11)).pack(anchor="w")
        ttk.Label(stats_frame, textvariable=self.num_ventas_var, font=("Helvetica", 11)).pack(anchor="w")
        ttk.Label(stats_frame, textvariable=self.ticket_promedio_var, font=("Helvetica", 11)).pack(anchor="w")

        graph_frame = ttk.LabelFrame(right_frame, text="Ventas por Método de Pago", padding=10)
        graph_frame.pack(fill="both", expand=True)

        # Crear figura para el gráfico
        self.fig = Figure(figsize=(5, 4), dpi=100)
        self.fig.patch.set_facecolor('#F0F0F0') # Color de fondo del estilo ttk
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=graph_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # Guardar las ventas cargadas para acceder a sus detalles
        self.ventas_cargadas = {}

    def create_widgets(self):
        # Este método ahora solo necesita vincular el combobox
        periodo_combo = self.ventas_report_frame.winfo_children()[0].winfo_children()[1] # Un poco frágil, pero funciona
        periodo_combo.bind("<<ComboboxSelected>>", self.on_period_change)

    def on_view_enter(self):
        """Se ejecuta cada vez que la vista se muestra."""
        self.on_period_change()

    def on_period_change(self, event=None):
        periodo_seleccionado = self.periodo_var.get()
        hoy = datetime.now()

        if periodo_seleccionado == "Hoy":
            start_date = end_date = hoy.strftime("%Y-%m-%d")
        elif periodo_seleccionado == "Esta Semana":
            start_date = (hoy - timedelta(days=hoy.weekday())).strftime("%Y-%m-%d")
            end_date = (hoy + timedelta(days=6 - hoy.weekday())).strftime("%Y-%m-%d")
        elif periodo_seleccionado == "Este Mes":
            start_date = hoy.replace(day=1).strftime("%Y-%m-%d")
            end_date = (hoy.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            end_date = end_date.strftime("%Y-%m-%d")
        elif periodo_seleccionado == "Este Año":
            start_date = hoy.replace(month=1, day=1).strftime("%Y-%m-%d")
            end_date = hoy.replace(month=12, day=31).strftime("%Y-%m-%d")
        
        self.start_date = start_date # Guardamos para usarlo en el gráfico
        self.end_date = end_date
        
        self.cargar_reporte(start_date, end_date)

    def cargar_reporte(self, start_date, end_date):
        try:
            ventas = obtener_ventas_por_rango_de_fechas(start_date, end_date)
            self.ventas_cargadas.clear()
            for item in self.ventas_tree.get_children():
                self.ventas_tree.delete(item)

            for venta in ventas:
                self.ventas_cargadas[str(venta.id_venta)] = venta
                self.ventas_tree.insert("", "end", iid=venta.id_venta, values=(
                    venta.id_venta,
                    datetime.strptime(venta.fecha_venta, "%Y-%m-%d %H:%M:%S").strftime("%H:%M:%S"),
                    venta.nombre_cliente,
                    f"${venta.total:.2f}",
                    venta.forma_pago
                ))
            self.ver_ticket_button.config(state="disabled")
            
            self.actualizar_estadisticas(ventas, start_date, end_date)
            self.actualizar_grafico(ventas)

        except Exception as e:
            messagebox.showerror("Error", f"No se pudo cargar el reporte: {e}")

    def mostrar_detalle_venta(self, event=None):
        for item in self.detalles_tree.get_children():
            self.detalles_tree.delete(item)

        selection = self.ventas_tree.selection()
        if not selection:
            return
        self.ver_ticket_button.config(state="normal")
        
        id_venta_sel = selection[0]
        venta_sel = self.ventas_cargadas.get(id_venta_sel)

        if venta_sel:
            for detalle in venta_sel.detalles:
                producto = obtener_producto_por_id(detalle.id_producto)
                self.detalles_tree.insert("", "end", values=(
                    detalle.cantidad,
                    producto.nombre if producto else "Producto no encontrado",
                    f"${detalle.precio_unitario:.2f}",
                    f"{detalle.descuento:.1f}%",
                    f"${detalle.subtotal:.2f}"
                ))

    def ver_ticket(self):
        selection = self.ventas_tree.selection()
        if not selection:
            return
        
        id_venta_sel = selection[0]
        venta_sel = self.ventas_cargadas.get(id_venta_sel)

        if venta_sel:
            ticket_content = generar_texto_ticket(venta_sel)
            messagebox.showinfo(f"Ticket Venta #{venta_sel.id_venta}", ticket_content, parent=self)
        else:
            messagebox.showwarning("Error", "No se pudo encontrar la información de la venta seleccionada.")

    def actualizar_estadisticas(self, ventas, start_date, end_date):
        # El total recaudado ahora es la suma de ventas que no son a crédito + los pagos de deudas recibidos.
        total_ventas_directas = sum(v.total for v in ventas if v.forma_pago != 'Libreta')
        total_pagos_recibidos = obtener_pagos_recibidos_por_rango(start_date, end_date)
        total_recaudado = total_ventas_directas + total_pagos_recibidos

        # El número de ventas y el ticket promedio se siguen calculando sobre el total de transacciones.
        num_ventas = len(ventas)
        total_transaccionado = sum(v.total for v in ventas) # Suma de todas las ventas, incluyendo las fiadas
        ticket_promedio = total_transaccionado / num_ventas if num_ventas > 0 else 0

        self.total_recaudado_var.set(f"Total Recaudado (Real): ${total_recaudado:.2f}")
        self.num_ventas_var.set(f"Nº de Ventas: {num_ventas}")
        self.ticket_promedio_var.set(f"Ticket Promedio: ${ticket_promedio:.2f}")

    def actualizar_grafico(self, ventas):
        self.ax.clear()

        if not ventas:
            self.ax.set_facecolor('#F0F0F0')
            self.ax.text(0.5, 0.5, "Sin datos para mostrar", ha="center", va="center")
            self.canvas.draw()
            return

        # Agrupar ventas por método de pago
        # Para el gráfico, sí queremos ver el volumen total de cada método, incluyendo "Libreta"
        metodos_pago = {}
        for v in ventas:
            metodos_pago[v.forma_pago] = metodos_pago.get(v.forma_pago, 0) + v.total

        # Añadimos los pagos recibidos como una categoría separada si existen
        total_pagos_recibidos = obtener_pagos_recibidos_por_rango(self.start_date, self.end_date)
        if total_pagos_recibidos > 0:
            metodos_pago['Pagos de Deudas'] = metodos_pago.get('Pagos de Deudas', 0) + total_pagos_recibidos

        labels = metodos_pago.keys()
        sizes = metodos_pago.values()

        self.ax.set_facecolor('white')
        self.ax.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90)
        self.ax.axis('equal')  # Asegura que el gráfico sea un círculo.
        self.fig.tight_layout()
        self.canvas.draw()

    def exportar_a_excel(self):
        if not self.ventas_cargadas:
            messagebox.showwarning("Sin Datos", "No hay datos en el reporte para exportar.")
            return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Archivos de Excel", "*.xlsx"), ("Todos los archivos", "*.*")],
            title="Guardar reporte como..."
        )

        if not filepath:
            return

        # Preparar los datos para el DataFrame
        datos_para_excel = [] # type: ignore
        
        # Obtener todos los IDs de productos únicos de las ventas cargadas
        all_product_ids = set()
        for venta in self.ventas_cargadas.values():
            for detalle in venta.detalles:
                all_product_ids.add(detalle.id_producto)
        
        # Obtener la información de todos los productos en una sola consulta
        products_info = {p.id_producto: p for p in obtener_productos_por_ids(list(all_product_ids))}

        for venta in self.ventas_cargadas.values():
            for detalle in venta.detalles:
                producto = products_info.get(detalle.id_producto)
                datos_para_excel.append({ # type: ignore
                    "ID Venta": venta.id_venta, "Fecha": venta.fecha_venta, "Cliente": venta.nombre_cliente,
                    "Forma de Pago": venta.forma_pago, "Producto": producto.nombre if producto else "N/A",
                    "Cantidad": detalle.cantidad, "Precio Unitario": detalle.precio_unitario,
                    "Descuento (%)": detalle.descuento, "Subtotal": detalle.subtotal
                })
        
        try:
            df = pd.DataFrame(datos_para_excel)
            df.to_excel(filepath, index=False, engine='openpyxl')
            messagebox.showinfo("Éxito", f"Reporte exportado con éxito a:\n{filepath}")
        except Exception as e:
            messagebox.showerror("Error al Exportar", f"No se pudo guardar el archivo de Excel:\n{e}")

    def create_restock_suggestion_widgets(self, parent_frame):
        # --- Controles para configurar el análisis ---
        controls_frame = ttk.Frame(parent_frame)
        controls_frame.pack(fill="x", pady=5)

        ttk.Label(controls_frame, text="Analizar ventas de los últimos:").pack(side="left", padx=(0, 5))
        self.dias_analisis_var = tk.StringVar(value="30")
        dias_analisis_spinbox = ttk.Spinbox(controls_frame, from_=1, to=365, textvariable=self.dias_analisis_var, width=5)
        dias_analisis_spinbox.pack(side="left", padx=5)
        ttk.Label(controls_frame, text="días").pack(side="left", padx=(0, 20))

        ttk.Label(controls_frame, text="Comprar stock para cubrir los próximos:").pack(side="left", padx=(0, 5))
        self.dias_cobertura_var = tk.StringVar(value="15")
        dias_cobertura_spinbox = ttk.Spinbox(controls_frame, from_=1, to=365, textvariable=self.dias_cobertura_var, width=5)
        dias_cobertura_spinbox.pack(side="left", padx=5)
        ttk.Label(controls_frame, text="días").pack(side="left", padx=(0, 20))

        # --- Botones de acción ---
        action_frame = ttk.Frame(parent_frame)
        action_frame.pack(fill="x", pady=(10, 5))

        generar_btn = ttk.Button(action_frame, text="Generar Sugerencias", command=self.generar_sugerencias, style="Accent.TButton")
        generar_btn.pack(side="left")

        self.exportar_sugerencias_btn = ttk.Button(action_frame, text="Exportar a Excel", command=self.exportar_sugerencias_a_excel, state="disabled")
        self.exportar_sugerencias_btn.pack(side="left", padx=10)

        # --- Tabla para mostrar los resultados ---
        self.sugerencias_tree = ttk.Treeview(parent_frame, columns=("nombre", "stock_actual", "ventas_periodo", "venta_diaria", "stock_sugerido", "a_comprar"), show="headings")
        self.sugerencias_tree.pack(fill="both", expand=True, pady=(10,0))

        # Definir encabezados
        self.sugerencias_tree.heading("nombre", text="Producto")
        self.sugerencias_tree.heading("stock_actual", text="Stock Actual")
        self.sugerencias_tree.heading("ventas_periodo", text="Ventas en Periodo")
        self.sugerencias_tree.heading("venta_diaria", text="Venta Diaria (Prom.)")
        self.sugerencias_tree.heading("stock_sugerido", text="Stock Objetivo")
        self.sugerencias_tree.heading("a_comprar", text="Cantidad a Comprar")

        # Ajustar columnas
        self.sugerencias_tree.column("nombre", width=250)
        self.sugerencias_tree.column("stock_actual", width=100, anchor="center")
        self.sugerencias_tree.column("ventas_periodo", width=120, anchor="center")
        self.sugerencias_tree.column("venta_diaria", width=140, anchor="center")
        self.sugerencias_tree.column("stock_sugerido", width=100, anchor="center")
        self.sugerencias_tree.column("a_comprar", width=120, anchor="center")
        
        self.sugerencias_data = []
        
        # --- Tooltip para la columna de venta diaria ---
        # Solución robusta: creamos un único Tooltip para el Treeview y le pasamos
        # un diccionario con los textos para cada cabecera que queramos.
        header_tips = {
            "venta_diaria": "Cantidad promedio de unidades vendidas por día.\nSe usa para proyectar la necesidad de stock."
        }
        ToolTip(self.sugerencias_tree, header_tooltips=header_tips)

    def generar_sugerencias(self):
        try:
            dias_analisis = int(self.dias_analisis_var.get())
            dias_cobertura = int(self.dias_cobertura_var.get())
        except (ValueError, TypeError):
            messagebox.showerror("Error de Entrada", "Por favor, ingrese números válidos para los días.")
            return

        for i in self.sugerencias_tree.get_children():
            self.sugerencias_tree.delete(i)

        self.sugerencias_data = obtener_sugerencias_reposicion(dias_analisis, dias_cobertura)

        if not self.sugerencias_data:
            messagebox.showinfo("Información", "No se encontraron sugerencias de reposición con los criterios actuales. ¡Tu stock está al día!")
            self.exportar_sugerencias_btn.config(state="disabled")
            return

        for item in self.sugerencias_data:
            _, nombre, stock, ventas, venta_diaria, stock_obj, a_comprar = item
            self.sugerencias_tree.insert("", "end", values=(nombre, int(stock), int(ventas), f"{venta_diaria:.2f}", int(stock_obj), int(a_comprar)))
        
        self.exportar_sugerencias_btn.config(state="normal")

    def exportar_sugerencias_a_excel(self):
        if not self.sugerencias_data:
            messagebox.showwarning("Sin Datos", "Primero debe generar las sugerencias para poder exportarlas.")
            return

        default_filename = f"sugerencia_compra_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
        filepath = filedialog.asksaveasfilename(
            defaultextension=".xlsx", filetypes=[("Archivos de Excel", "*.xlsx")],
            initialfile=default_filename, title="Guardar Reporte de Compra"
        )

        if not filepath: return

        try:
            datos_para_excel = []
            for item in self.sugerencias_data:
                _, nombre, stock, ventas, venta_diaria, stock_obj, a_comprar = item
                datos_para_excel.append({"Producto": nombre, "Stock Actual": int(stock), "Ventas Periodo": int(ventas), "Venta Diaria Promedio": round(venta_diaria, 2), "Stock Objetivo": int(stock_obj), "Cantidad a Comprar": int(a_comprar)})
            
            df = pd.DataFrame(datos_para_excel)
            df.to_excel(filepath, index=False, engine='openpyxl')
            messagebox.showinfo("Éxito", f"El reporte se ha guardado correctamente en:\n{filepath}")
        except Exception as e:
            messagebox.showerror("Error al Exportar", f"No se pudo guardar el archivo de Excel.\n\nError: {e}")

class SaleDetailWindow(tk.Toplevel):
    """Muestra el detalle completo de una venta específica."""
    def __init__(self, parent, venta: Venta):
        super().__init__(parent)
        self.venta = venta

        self.title(f"Detalle de Venta #{self.venta.id_venta}")
        self.geometry("600x400")
        self.transient(parent)
        self.grab_set()

        self.create_widgets()

    def create_widgets(self):
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill="both", expand=True)

        # Info de la venta
        # Obtener todos los IDs de productos únicos de los detalles de la venta
        all_product_ids = {detalle.id_producto for detalle in self.venta.detalles}
        # Obtener la información de todos los productos en una sola consulta
        products_info = {p.id_producto: p for p in obtener_productos_por_ids(list(all_product_ids))}

        total_actualizado = 0.0
        for detalle in self.venta.detalles:
            producto = products_info.get(detalle.id_producto)
            total_actualizado += detalle.cantidad * (producto.precio_venta if producto else detalle.precio_unitario)

        info_text = (f"Fecha: {datetime.strptime(self.venta.fecha_venta, '%Y-%m-%d %H:%M:%S').strftime('%d/%m/%Y %H:%M')} | "
                     f"Total Original: ${self.venta.total:.2f} | Total Actualizado: ${total_actualizado:.2f} | " # type: ignore
                     f"Pago: {self.venta.forma_pago}")
        ttk.Label(main_frame, text=info_text, font=("Helvetica", 10, "italic")).pack(anchor="w", pady=(0, 10))

        # Treeview para los detalles
        tree = ttk.Treeview(main_frame, columns=("Cant", "Producto", "P.Unit", "P.Actual", "Subtotal"), show="headings")
        tree.heading("Cant", text="Cant.")
        tree.heading("Producto", text="Producto")
        tree.heading("P.Unit", text="P. Original")
        tree.heading("P.Actual", text="P. Actual")
        tree.heading("Subtotal", text="Subtotal")
        tree.column("Cant", width=60, anchor="center")
        tree.column("P.Unit", width=90, anchor="e")
        tree.column("P.Actual", width=90, anchor="e")
        tree.column("Subtotal", width=110, anchor="e")
        tree.pack(fill="both", expand=True)

        # Cargar detalles
        for detalle in self.venta.detalles:
            producto = products_info.get(detalle.id_producto)
            precio_actual = producto.precio_venta if producto else detalle.precio_unitario
            subtotal_actualizado = detalle.cantidad * precio_actual

            # Colorear si el precio cambió
            tags = ()
            if precio_actual > detalle.precio_unitario:
                tags = ('aumento',)
            elif precio_actual < detalle.precio_unitario:
                tags = ('disminucion',)

            tree.insert("", "end", values=(
                detalle.cantidad,
                producto.nombre if producto else "Producto Eliminado",
                f"${detalle.precio_unitario:.2f}",
                f"${precio_actual:.2f}",
                f"${subtotal_actualizado:.2f}"
            ), tags=tags)

        tree.tag_configure('aumento', background='#FFEBEE') # Rojo claro
        tree.tag_configure('disminucion', background='#E8F5E9') # Verde claro
        tree.tag_configure('aumento', background='#FFEBEE') # Rojo claro
        tree.tag_configure('disminucion', background='#E8F5E9') # Verde claro
