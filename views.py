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
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

config = configparser.ConfigParser()
config.read(resource_path('config.ini'))

MP_ACCESS_TOKEN = config.get('MercadoPago', 'AccessToken', fallback='NO_CONFIGURADO')
MODO_PRUEBA_PAGOS = config.getboolean('MercadoPago', 'ModoPrueba', fallback=True)

NOMBRE_NEGOCIO = config.get('Negocio', 'Nombre', fallback='EasySt System')

PERMITIR_STOCK_NEGATIVO = config.getboolean('Negocio', 'PermitirStockNegativo', fallback=False)

PRINTER_VENDOR_ID = config.get('Impresora', 'idVendor', fallback=None)
PRINTER_PRODUCT_ID = config.get('Impresora', 'idProduct', fallback=None)
PRINTER_PROFILE = config.get('Impresora', 'profile', fallback=None)

class ToolTip:
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
        if self.tooltip_window:
            self.tooltip_window.destroy()

        x, y, _, _ = self.widget.bbox("insert") if event is None else (event.x, event.y, 0, 0)
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25

        self.tooltip_window = tk.Toplevel(self.widget)
        self.tooltip_window.wm_overrideredirect(True)
        self.tooltip_window.wm_geometry(f"+{x}+{y}")

        label = ttk.Label(self.tooltip_window, text=text, justify='left',
                         background="#ffffe0", relief='solid', borderwidth=1,
                         padding=(5, 3))
        label.pack(ipadx=1)
        if not isinstance(self.widget, ttk.Treeview):
            self.show_tooltip(self.text)

    def hide_tooltip(self, event=None):
        if self.tooltip_window:
            self.tooltip_window.destroy()
        self.tooltip_window = None

    def on_treeview_motion(self, event):
        region = self.widget.identify_region(event.x, event.y)
        if region == "heading":
            column_id = self.widget.identify_column(event.x)
            column_index = int(column_id.replace('#', '')) - 1
            column_name = self.widget['columns'][column_index]
            
            if column_name in self.header_tooltips:
                self.hide_tooltip()
                self.show_tooltip(self.header_tooltips[column_name], event)
                return
        self.hide_tooltip()

def generar_texto_ticket(venta_obj: Venta):
    ticket_content = f"         *** {NOMBRE_NEGOCIO} ***\n\n"
    ticket_content += f"Fecha: {datetime.strptime(venta_obj.fecha_venta, '%Y-%m-%d %H:%M:%S').strftime('%d/%m/%Y %H:%M')}\n"
    ticket_content += f"Venta ID: {venta_obj.id_venta}\n"
    ticket_content += "----------------------------------------\n"
    ticket_content += "{:<5} {:<12} {:>7} {:>6} {:>7}\n".format("Cant", "Producto", "P.Unit", "Desc.", "Subt.")
    ticket_content += "----------------------------------------\n"

    for detalle in venta_obj.detalles:
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
        self.status_var = tk.StringVar()
        self.progress_bar = None
        self.status_label = None

        self.create_widgets()
        self.cargar_productos()

    def create_widgets(self):
        controls_frame = ttk.Frame(self)
        controls_frame.pack(side="top", fill="x", padx=10, pady=10)

        ttk.Label(controls_frame, text="Buscar por nombre:").pack(side="left", padx=(0, 5))
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(controls_frame, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True)
        search_entry.bind("<Return>", lambda event: self.cargar_productos())

        self.poco_stock_var = tk.BooleanVar()
        poco_stock_check = ttk.Checkbutton(
            controls_frame, 
            text="Mostrar solo con poco stock", 
            variable=self.poco_stock_var,
            command=self.cargar_productos
        )
        poco_stock_check.pack(side="left", padx=10)

        ttk.Button(controls_frame, text="Buscar", command=self.cargar_productos).pack(side="left", padx=5)
        ttk.Button(controls_frame, text="Importar desde Excel", command=self.importar_desde_excel).pack(side="right", padx=5)
        ttk.Button(controls_frame, text="Añadir Producto", command=self.abrir_ventana_producto).pack(side="right", padx=5)
        self.gestionar_lotes_btn = ttk.Button(controls_frame, text="Gestionar Lotes", command=self.abrir_ventana_gestion_lotes, state="disabled")
        self.gestionar_lotes_btn.pack(side="right", padx=5)

        tree_frame = ttk.Frame(self)
        tree_frame.pack(side="top", fill="both", expand=True, padx=10, pady=(0, 10))

        self.tree = ttk.Treeview(
            tree_frame, 
            columns=("ID", "Nombre", "Stock Total", "Lotes", "Vencimiento Próximo"), 
            show="headings"
        )
        
        self.tree.heading("ID", text="ID")
        self.tree.heading("Nombre", text="Nombre")
        self.tree.heading("Stock Total", text="Stock Total")
        self.tree.heading("Lotes", text="Nº de Lotes")
        self.tree.heading("Vencimiento Próximo", text="Vencimiento Próximo")

        self.tree.column("ID", width=50, anchor="center")
        self.tree.column("Nombre", width=350)
        self.tree.column("Stock Total", width=100, anchor="center")
        self.tree.column("Lotes", width=100, anchor="center")
        self.tree.column("Vencimiento Próximo", width=150, anchor="center")

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.tag_configure('poco_stock', background='#FFEBEE')
        self.tree.tag_configure('vencido', background='#FFCDD2', foreground='black')
        self.tree.tag_configure('proximo_vencer', background='#FFF9C4', foreground='black')

        self.tree.bind("<<TreeviewSelect>>", self.on_product_select)
        self.tree.bind("<Double-1>", self.abrir_ventana_edicion_producto)

    def cargar_productos(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        nombre = self.search_var.get()
        poco_stock = self.poco_stock_var.get()

        try:
            productos = obtener_productos(nombre_like=nombre, solo_poco_stock=poco_stock)
            for prod in productos:
                num_lotes = prod.num_lotes 
                vencimiento_proximo = "N/A"

                tags = ()
                if prod.cantidad_stock <= 5:
                    tags = ('poco_stock',)

                if prod.vencimiento_proximo:
                    vencimiento_proximo = datetime.strptime(prod.vencimiento_proximo, "%Y-%m-%d").strftime("%d/%m/%Y")
                    fecha_mas_cercana = datetime.strptime(prod.vencimiento_proximo, "%Y-%m-%d").date()
                    hoy = datetime.now().date()
                    dias_restantes = (fecha_mas_cercana - hoy).days
                    if dias_restantes < 0:
                        tags += ('vencido',)
                    elif dias_restantes <= 20:
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
        
        self.gestionar_lotes_btn.config(state="disabled")

    def abrir_ventana_producto(self):
        ProductFormWindow(self, callback=self.cargar_productos, producto_a_editar=None)

    def abrir_ventana_edicion_producto(self, event=None):
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
        self.gestionar_lotes_btn.config(state="normal" if self.tree.selection() else "disabled")

    def importar_desde_excel(self):
        filepath = filedialog.askopenfilename(
            title="Seleccionar archivo de Excel para importar",
            filetypes=[("Archivos de Excel", "*.xlsx"), ("Todos los archivos", "*.*")]
        )

        if not filepath:
            return

        self.start_import_feedback()

        threading.Thread(target=self._perform_excel_import, args=(filepath,)).start()

    def start_import_feedback(self):
        self.status_frame = ttk.Frame(self, padding=(10, 5))
        self.status_frame.pack(side="bottom", fill="x")
        
        self.status_label = ttk.Label(self.status_frame, textvariable=self.status_var)
        self.status_label.pack(side="left", padx=(0, 10))
        
        self.progress_bar = ttk.Progressbar(self.status_frame, orient="horizontal", mode="indeterminate")
        self.progress_bar.pack(fill="x", expand=True)
        self.progress_bar.start()
        
        self.status_var.set("Importando desde Excel, por favor espere...")
        for child in self.winfo_children():
            if isinstance(child, ttk.Frame):
                for btn in child.winfo_children():
                    if isinstance(btn, ttk.Button):
                        btn.config(state="disabled")

    def stop_import_feedback(self):
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

                    codigo_barras = str(row['codigo_barras']) if 'codigo_barras' in row and pd.notna(row['codigo_barras']) else None
                    cantidad_stock = int(row['cantidad_stock']) if 'cantidad_stock' in row and pd.notna(row['cantidad_stock']) else 0
                    fecha_vencimiento = None
                    if 'fecha_vencimiento' in row and pd.notna(row['fecha_vencimiento']):
                        if isinstance(row['fecha_vencimiento'], datetime):
                            fecha_vencimiento = row['fecha_vencimiento'].strftime('%Y-%m-%d')
                        else:
                            fecha_vencimiento = str(row['fecha_vencimiento'])

                    producto_existente = obtener_producto_por_nombre(nombre)

                    if producto_existente:
                        if agregar_lote(producto_existente.id_producto, cantidad_stock, fecha_vencimiento, codigo_barras):
                            exitosos += 1
                        else:
                            raise Exception("No se pudo añadir el lote al producto existente.")
                    else:
                        volumen = float(row['volumen']) if 'volumen' in row and pd.notna(row['volumen']) else None
                        descripcion = str(row['descripcion']) if 'descripcion' in row and pd.notna(row['descripcion']) else None
                        
                        nuevo_producto = Producto(
                            nombre=nombre,
                            precio_venta=precio_venta,
                            cantidad_stock=cantidad_stock,
                            codigo_barras=codigo_barras,
                            volumen=volumen,
                            descripcion=descripcion,
                            fecha_vencimiento=fecha_vencimiento
                        )
                        if agregar_producto(nuevo_producto):
                            exitosos += 1
                        else:
                            raise Exception("Error al crear el nuevo producto en la BD.")

                except pd.errors.DatabaseError as db_err:
                    messagebox.showerror("Error Crítico de Base de Datos", f"La importación se ha detenido debido a un error de base de datos:\n{db_err}\n\nNo se importarán más filas.", parent=self)
                    break

                except Exception as e:
                    fallidos += 1
                    errores_detalle.append(f"Fila {index + 2}: {row.get('nombre', 'Sin Nombre')} - Error: {e}")

            mensaje_final = f"Importación completada.\n\nLotes importados/creados: {exitosos}\nFilas con errores: {fallidos}"
            if fallidos > 0:
                mensaje_final += "\n\nDetalle de errores:\n" + "\n".join(errores_detalle[:5])
            
            self.after(0, lambda: messagebox.showinfo("Resumen de Importación", mensaje_final, parent=self))
            self.after(0, self.cargar_productos)

        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error al Importar", f"No se pudo procesar el archivo de Excel:\n{e}", parent=self))
        finally:
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

        self.tree.tag_configure('vencido', background='#FFDDDD')

    def cargar_clientes(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        query = self.search_var.get()
        solo_con_deuda = self.con_deuda_var.get()
        try:
            clientes = obtener_clientes(nombre_o_dni=query, solo_con_deuda=solo_con_deuda)
            for cliente in clientes:
                tags = ()
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
        self.on_client_select()

    def on_client_select(self, event=None):
        self.edit_client_btn.config(state="normal" if self.tree.selection() else "disabled")

    def abrir_ventana_cliente(self):
        ClientFormWindow(self, callback=self.cargar_clientes, cliente_a_editar=None)

    def abrir_ventana_edicion_cliente(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        
        id_cliente = int(selection[0])
        cliente_a_editar = obtener_cliente_por_id(id_cliente)
        if not cliente_a_editar:
            messagebox.showerror("Error", "No se pudo encontrar el cliente para editar.")
            return
        
        ClientFormWindow(self, callback=self.cargar_clientes, cliente_a_editar=cliente_a_editar)

    def abrir_detalle_cuenta_cliente(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        
        id_cliente = int(selection[0])
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

        dialog = PaymentDialog(self, cliente=cliente)
        monto_pago = dialog.result

        if monto_pago:
            fecha_pago_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if realizar_pago_cliente(id_cliente, monto_pago, fecha_pago_str):
                messagebox.showinfo("Pago Registrado", "El pago se ha registrado con éxito.")
                self.cargar_clientes()


class VentasView(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.cliente_seleccionado = None

        self.current_sale_items = {}
        self.total_var = tk.StringVar(value="$0.00")

        self.search_thread = None
        self.search_lock = threading.Lock()
        self.create_widgets()

    def on_view_enter(self):
        self.search_entry.focus_set()

    def create_widgets(self):
        search_frame = ttk.Frame(self, padding=10)
        search_frame.pack(side="top", fill="x")

        client_frame = ttk.Frame(self, padding=(10,0,10,10))
        client_frame.pack(side="top", fill="x")
        self.client_label_var = tk.StringVar(value="Cliente: Consumidor Final")
        ttk.Label(client_frame, textvariable=self.client_label_var, font=("Helvetica", 11, "italic")).pack(side="left")
        ttk.Button(client_frame, text="Buscar/Asignar Cliente", command=self.asignar_cliente).pack(side="right")

        ttk.Label(search_frame, text="Buscar producto (nombre o código):", font=("Helvetica", 12)).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var, font=("Helvetica", 14))
        self.search_entry.pack(side="left", fill="x", expand=True, padx=10)
        
        self.search_entry.bind("<KeyRelease>", self.on_key_release)
        self.search_entry.bind("<Return>", self.handle_enter)
        self.search_entry.focus_set()

        self.suggestions_popup = tk.Toplevel()
        self.suggestions_popup.overrideredirect(True)
        self.suggestions_popup.withdraw()

        self.suggestions_listbox = tk.Listbox(
            self.suggestions_popup, 
            font=("Helvetica", 12),
            selectbackground="#4CAF50",
            selectforeground="white",
            borderwidth=1,
            relief="solid"
        )
        self.suggestions_listbox.pack(fill="both", expand=True)

        self.suggested_products = []

        self.suggestions_listbox.bind("<Double-Button-1>", self.select_from_suggestions)
        self.suggestions_listbox.bind("<Return>", self.select_from_suggestions)
        self.search_entry.bind("<Down>", self.move_selection_down)
        self.search_entry.bind("<Up>", self.move_selection_up)
        self.search_entry.bind("<Escape>", lambda e: self.hide_suggestions())
        self.bind_all("<Button-1>", self.check_focus, add="+")

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
        self.cart_tree.bind("<Double-1>", self.edit_cart_item_quantity)
        self.cart_tree.bind("<Delete>", self.eliminar_item_del_carrito)

        summary_frame = ttk.Frame(self, padding=20)
        summary_frame.pack(side="bottom", fill="x")

        ttk.Label(summary_frame, text="TOTAL:", font=("Helvetica", 24, "bold")).pack(side="left")
        ttk.Label(summary_frame, textvariable=self.total_var, font=("Helvetica", 24, "bold"), foreground="#4CAF50").pack(side="left", padx=10)

        ttk.Button(summary_frame, text="Finalizar Venta", command=self.finalize_sale, style="Accent.TButton").pack(side="right")
        ttk.Button(summary_frame, text="Cancelar Venta", command=self.cancel_sale).pack(side="right", padx=10)

    def add_product_to_sale(self, product=None):
        if not product:
            return

        dialog = QuantityDialog(self, product)
        result = dialog.result

        if not result:
            self.search_var.set("")
            self.hide_suggestions()
            self.search_entry.focus_set()
            return

        cantidad, descuento = result

        if product.id_producto in self.current_sale_items:
            detalle_existente = self.current_sale_items[product.id_producto]
            detalle_existente.cantidad += cantidad
            detalle_existente.descuento = descuento
            detalle_existente.subtotal = detalle_existente.calcular_subtotal()
        else:
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
        product_ids = list(self.current_sale_items.keys())
        
        if not product_ids:
            for item in self.cart_tree.get_children():
                self.cart_tree.delete(item)
            self.total_var.set("$0.00")
            return

        products_info = {p.id_producto: p for p in obtener_productos_por_ids(product_ids)}

        for item in self.cart_tree.get_children():
            self.cart_tree.delete(item)

        total = 0
        for id_prod, detalle in self.current_sale_items.items():
            product = products_info.get(id_prod)
            if not product: continue
            
            detalle.subtotal = detalle.calcular_subtotal()
            self.cart_tree.insert("", "end", values=(
                product.nombre,
                detalle.cantidad,
                f"${detalle.precio_unitario:.2f}",
                f"{detalle.descuento:.1f}%",
                f"${detalle.subtotal:.2f}"
            ), iid=id_prod)
            total += detalle.subtotal
        
        self.total_var.set(f"${total:.2f}")

    def finalize_sale(self):
        if not self.current_sale_items:
            messagebox.showwarning("Venta Vacía", "No hay productos en el carrito.")
            return

        venta = Venta(fecha_venta=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        venta.id_cliente = self.cliente_seleccionado.id_cliente if self.cliente_seleccionado else None
        venta.detalles = list(self.current_sale_items.values())
        venta.calcular_total()

        PaymentWindow(self, venta_obj=venta, callback=self.cancel_sale)

    def cancel_sale(self):
        self.current_sale_items.clear()
        self.update_cart_display()
        self.search_var.set("")
        self.cliente_seleccionado = None
        self.client_label_var.set("Cliente: Consumidor Final")
        self.hide_suggestions()

    def check_focus(self, event):
        widget_under_mouse = self.winfo_containing(event.x_root, event.y_root)
        if widget_under_mouse != self.search_entry and widget_under_mouse != self.suggestions_listbox:
            if self.suggestions_popup.winfo_viewable():
                self.hide_suggestions()

    def asignar_cliente(self):
        dialog = SelectClientDialog(self)
        selected_client_id = dialog.result

        if selected_client_id:
            self.cliente_seleccionado = obtener_cliente_por_id(selected_client_id)
            self.client_label_var.set(f"Cliente: {self.cliente_seleccionado.nombre} (DNI: {self.cliente_seleccionado.dni or 'N/A'})")
        else:
            self.cliente_seleccionado = None
            self.client_label_var.set("Cliente: Consumidor Final")

    def eliminar_item_del_carrito(self, event=None):
        selection = self.cart_tree.selection()
        if not selection:
            return

        if messagebox.askyesno("Confirmar Eliminación", "¿Está seguro de que desea quitar este producto del carrito?"):
            for item_id_str in selection:
                item_id = int(item_id_str)
                if item_id in self.current_sale_items:
                    del self.current_sale_items[item_id]
            
            self.update_cart_display()

    def edit_cart_item_quantity(self, event=None):
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

        dialog = QuantityDialog(self, producto, initial_quantity=detalle_actual.cantidad, initial_discount=detalle_actual.descuento) # type: ignore
        result = dialog.result

        if not result:
            return

        nueva_cantidad, nuevo_descuento = result

        if nueva_cantidad == 0:
            del self.current_sale_items[item_id]
        elif not PERMITIR_STOCK_NEGATIVO and nueva_cantidad > producto.cantidad_stock: # type: ignore
            if messagebox.askyesno("Stock Insuficiente", f"No se puede vender {nueva_cantidad} unidades. Stock disponible: {producto.cantidad_stock}.\n\n¿Desea vender el stock disponible ({producto.cantidad_stock})?", parent=self):
                nueva_cantidad = producto.cantidad_stock
            else:
                return
        else:
            self.current_sale_items[item_id].cantidad = nueva_cantidad
            self.current_sale_items[item_id].descuento = nuevo_descuento
            self.current_sale_items[item_id].subtotal = self.current_sale_items[item_id].calcular_subtotal()

        self.update_cart_display()

    def _perform_search_in_thread(self, query):
        results = obtener_productos(nombre_like=query)
        
        with self.search_lock:
            if query == self.search_var.get():
                self.after(0, self._update_suggestions_ui, results)

    def _update_suggestions_ui(self, results):
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
        if event.keysym in ("Up", "Down", "Return", "Escape"):
            return

        current_query = self.search_var.get()
        if len(current_query) < 2:
            self.hide_suggestions()
            return

        if self.search_thread and self.search_thread.is_alive():
            pass

        self.search_thread = threading.Thread(target=self._perform_search_in_thread, args=(current_query,))
        self.search_thread.daemon = True
        self.search_thread.start()

    def handle_enter(self, event=None):
        if self.suggestions_popup.winfo_viewable() and self.suggestions_listbox.curselection():
            self.select_from_suggestions()
        else:
            query = self.search_var.get().strip()
            if query:
                producto_encontrado = obtener_producto_por_codigo_barras(query)
                
                if producto_encontrado:
                    self.add_product_to_sale(producto_encontrado)
                else:
                    productos_flexibles = obtener_productos(nombre_like=query)
                    if len(productos_flexibles) == 1:
                        self.add_product_to_sale(productos_flexibles[0])
                    else:
                        messagebox.showwarning("No Encontrado", f"No se encontró un producto único con '{query}'.\nUse las flechas para seleccionar de la lista o sea más específico.")
                
                self.search_var.set("")

    def select_from_suggestions(self, event=None):
        selected_indices = self.suggestions_listbox.curselection()
        if not selected_indices:
            return
        
        selected_product = self.suggested_products[selected_indices[0]]
        self.add_product_to_sale(selected_product)

    def show_suggestions(self):
        if not self.suggestions_popup.winfo_viewable():
            x = self.search_entry.winfo_rootx()
            y = self.search_entry.winfo_rooty() + self.search_entry.winfo_height()
            width = self.search_entry.winfo_width()
            
            self.suggestions_popup.geometry(f"{width}x150+{x}+{y}")
            self.suggestions_popup.deiconify()

    def hide_suggestions(self):
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

        self.title("Editar Producto" if self.producto_a_editar else "Añadir Nuevo Producto")

        self.geometry("450x350")
        self.resizable(False, False)

        self.grab_set()
        self.transient(parent)

        self.create_form_widgets()
        if self.producto_a_editar:
            self.cargar_datos_producto()

    def create_form_widgets(self):
        form_frame = ttk.Frame(self, padding="10")
        form_frame.pack(fill="both", expand=True)

        self.vars = {
            "codigo_barras": tk.StringVar(),
            "nombre": tk.StringVar(),
            "precio_venta": tk.DoubleVar(value=0.0),
            "cantidad_stock": tk.IntVar(value=1),
            "fecha_vencimiento": tk.StringVar(),
            "volumen": tk.DoubleVar(value=0.0),
            "descripcion": tk.StringVar()
        }

        ttk.Label(form_frame, text="Nombre (*):").grid(row=0, column=0, sticky="w", pady=5)
        nombre_entry = ttk.Entry(form_frame, textvariable=self.vars["nombre"])
        nombre_entry.grid(row=0, column=1, sticky="ew", pady=5)
        if self.producto_a_editar:
            nombre_entry.focus_set()
        else:
            nombre_entry.bind("<Return>", self.buscar_producto_existente)

        ttk.Label(form_frame, text="Precio Venta (*):").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(form_frame, textvariable=self.vars["precio_venta"]).grid(row=1, column=1, sticky="ew", pady=5)

        ttk.Label(form_frame, text="Contenido (gr, ml, etc.):").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(form_frame, textvariable=self.vars["volumen"]).grid(row=2, column=1, sticky="ew", pady=5)

        ttk.Label(form_frame, text="Descripción:").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Entry(form_frame, textvariable=self.vars["descripcion"]).grid(row=3, column=1, sticky="ew", pady=5)

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

        button_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        button_frame.pack(fill="x")

        ttk.Button(button_frame, text="Guardar", command=self.guardar_producto).pack(side="right")
        ttk.Button(button_frame, text="Cancelar", command=self.destroy).pack(side="right", padx=10)

    def buscar_producto_existente(self, event=None):
        nombre = self.vars["nombre"].get().strip()
        if not nombre:
            return

        producto_existente = obtener_producto_por_nombre(nombre)
        if producto_existente:
            if messagebox.askyesno("Producto Existente", 
                                   f"El producto '{nombre}' ya existe.\n\n"
                                   "¿Desea añadir un nuevo lote a este producto en lugar de crear uno nuevo?",
                                   parent=self):
                self.destroy()
                LoteManagementWindow(self.parent, producto=producto_existente, callback=self.callback)

    def cargar_datos_producto(self):
        p = self.producto_a_editar
        self.vars["nombre"].set(p.nombre or "")
        self.vars["precio_venta"].set(p.precio_venta or 0.0)
        self.vars["volumen"].set(p.volumen or 0.0)
        self.vars["descripcion"].set(p.descripcion or "")
        self.vars["codigo_barras"].set("")

    def guardar_producto(self):
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
            self.producto_a_editar.nombre = nombre
            self.producto_a_editar.precio_venta = precio
            self.producto_a_editar.volumen = self.vars["volumen"].get() if self.vars["volumen"].get() > 0 else None
            self.producto_a_editar.descripcion = self.vars["descripcion"].get().strip() or None

            if actualizar_producto(self.producto_a_editar):
                messagebox.showinfo("í‰xito", f"Producto '{self.producto_a_editar.nombre}' actualizado con éxito.", parent=self)
                self.destroy()
                self.callback()
            else:
                messagebox.showerror("Error de Base de Datos", "No se pudo actualizar el producto.", parent=self)
        else:
            producto_existente = obtener_producto_por_nombre(nombre)
            if producto_existente:
                messagebox.showerror("Error", f"Ya existe un producto con el nombre '{nombre}'.\nUse la ventana 'Gestionar Lotes' para añadirle stock.", parent=self)
                return

            nuevo_producto = Producto(
                nombre=nombre,
                precio_venta=precio,
                cantidad_stock=stock_inicial,
                volumen=self.vars["volumen"].get() if self.vars["volumen"].get() > 0 else None,
                codigo_barras=codigo_barras_lote,
                descripcion=self.vars["descripcion"].get().strip() or None,
                fecha_vencimiento=fecha_venc_str
            )

            if agregar_producto(nuevo_producto):
                messagebox.showinfo("í‰xito", f"Producto '{nuevo_producto.nombre}' y su lote inicial han sido agregados.", parent=self)
                self.destroy()
                self.callback()
            else:
                messagebox.showerror("Error de Base de Datos", "No se pudo guardar el producto.", parent=self)

class LoteManagementWindow(tk.Toplevel):
    def __init__(self, parent, producto: Producto, callback):
        super().__init__(parent)
        self.parent = parent
        self.producto = producto
        self.callback = callback

        self.title(f"Gestionar Lotes de: {self.producto.nombre}")
        self.geometry("750x400")
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)

        self.create_widgets()
        self.cargar_lotes()

    def create_widgets(self):
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill="both", expand=True)

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
                lote.get('codigo_barras', 'N/A') or "N/A"
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
        self.callback()
        self.destroy()

class LoteFormDialog(simpledialog.Dialog):
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
        return self.cantidad_entry

    def apply(self):
        try:
            cantidad = self.cantidad_var.get()
            fecha_str = self.fecha_var.get().strip()
            codigo_str = self.codigo_var.get().strip()

            if cantidad < 0:
                messagebox.showwarning("Dato Inválido", "La cantidad no puede ser negativa.", parent=self)
                return

            if fecha_str:
                datetime.strptime(fecha_str, "%Y-%m-%d")
            else:
                fecha_str = None

            codigo_str = codigo_str if codigo_str else None

            self.result = (cantidad, fecha_str, codigo_str)
        except ValueError:
            messagebox.showwarning("Formato Incorrecto", "La fecha debe tener el formato AAAA-MM-DD.", parent=self)
        except tk.TclError:
            messagebox.showwarning("Dato Inválido", "La cantidad debe ser un número entero.", parent=self)


class PaymentWindow(tk.Toplevel):
    def __init__(self, parent, venta_obj: Venta, callback):
        super().__init__(parent)
        self.parent = parent
        self.venta = venta_obj
        self.callback = callback

        self.title("Finalizar Venta")
        self.geometry("400x380")
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)

        self.create_widgets()

    def create_widgets(self):
        main_frame = ttk.Frame(self, padding=20)
        main_frame.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        ttk.Label(main_frame, text="Total a Pagar:", font=("Helvetica", 14)).grid(row=0, column=0, columnspan=2, pady=(0, 5))
        ttk.Label(main_frame, text=f"${self.venta.total:.2f}", font=("Helvetica", 28, "bold"), foreground="#4CAF50").grid(row=1, column=0, columnspan=2, pady=(0, 20))

        ttk.Button(main_frame, text="Efectivo", command=self.pay_cash, style="Accent.TButton").grid(row=2, column=0, sticky="ew", padx=(0, 5), pady=5)
        ttk.Button(main_frame, text="Tarjeta (Débito/Crédito)", command=self.pay_card).grid(row=2, column=1, sticky="ew", padx=(5, 0), pady=5)
        ttk.Button(main_frame, text="Billetera Virtual (QR)", command=self.pay_qr).grid(row=3, column=0, sticky="ew", padx=(0, 5), pady=5)
        ttk.Button(main_frame, text="Transferencia Bancaria", command=self.pay_transfer).grid(row=3, column=1, sticky="ew", padx=(5, 0), pady=5)

        fiar_button = ttk.Button(main_frame, text="Fiar (Anotar en Libreta)", command=self.pay_credit, state="normal" if self.venta.id_cliente else "disabled")
        fiar_button.grid(row=4, column=0, columnspan=2, sticky="ew", pady=5)

        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_columnconfigure(1, weight=1)

    def pay_cash(self):
        self.venta.forma_pago = "Efectivo"
        self.complete_sale()

    def pay_card(self):
        messagebox.showinfo(
            "Terminal de Pago",
            "Por favor, utilice el terminal de pago para completar la transacción.",
            parent=self
        )

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
            return

        try:
            if self.venta.total <= 0:
                messagebox.showerror("Error", "No se puede generar un pago para una venta con total de $0.00.", parent=self)
                return
            
            if not MP_ACCESS_TOKEN or MP_ACCESS_TOKEN == 'TU_ACCESS_TOKEN_VA_AQUI' or MP_ACCESS_TOKEN == 'NO_CONFIGURADO':
                messagebox.showerror("Configuración Requerida", 
                                     "El Access Token de Mercado Pago no está configurado.\n\n"
                                     "Por favor, edite el archivo 'config.ini' y añada su credencial.", parent=self)
                return

            sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

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
            
            if order_response.get("status") not in [200, 201]:
                error_info = order_response.get("response", {})
                error_message = error_info.get("message", "Error desconocido de la API.")
                messagebox.showerror("Error de Mercado Pago", f"No se pudo crear la orden de pago:\n{error_message}\n\nVerifique que su Access Token sea correcto y esté activado para producción.", parent=self)
                return
            
            qr_data_string = order_response["response"].get("qr_data")
            if not qr_data_string:
                messagebox.showerror("Error de Mercado Pago", "La API no devolvió los datos del QR.", parent=self)

            self.show_qr_window(qr_data_string)

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
        messagebox.showinfo(
            "Pago por Transferencia",
            "Por favor, realice la transferencia y muestre el comprobante.",
            parent=self
        )

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
        id_venta_nueva = registrar_venta(self.venta)
        if id_venta_nueva:
            self.venta.id_venta = id_venta_nueva
            messagebox.showinfo("Venta Registrada", f"Venta registrada con éxito.\nMétodo de pago: {self.venta.forma_pago}.", parent=self)

            if messagebox.askyesno("Imprimir Ticket", "¿Desea imprimir el ticket de venta?", parent=self):
                self.print_ticket()

            self.destroy()
            self.callback()
        else:
            messagebox.showerror("Error de Base de Datos", "No se pudo registrar la venta. El stock no ha sido modificado.", parent=self)

    def show_qr_window(self, qr_data):
        qr_window = tk.Toplevel(self)
        qr_window.title("Escanear para Pagar")
        qr_window.resizable(False, False)
        qr_window.grab_set()

        qr_img = qrcode.make(qr_data)
        qr_img = qr_img.resize((300, 300))
        
        photo = ImageTk.PhotoImage(qr_img)

        qr_label = ttk.Label(qr_window, image=photo)
        qr_label.image = photo
        qr_label.pack(padx=20, pady=20)

        ttk.Label(qr_window, text="Pídele al cliente que escanee el código con la app de Mercado Pago.", wraplength=300).pack(pady=(0, 10))


    def print_ticket(self):
        ticket_content = generar_texto_ticket(self.venta)

        try:
            if not all([PRINTER_VENDOR_ID, PRINTER_PRODUCT_ID]):
                raise ValueError("La configuración de la impresora (idVendor, idProduct) no está definida en config.ini.")

            vendor_id = int(PRINTER_VENDOR_ID, 16)
            product_id = int(PRINTER_PRODUCT_ID, 16)

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


class QuantityDialog(simpledialog.Dialog):
    def __init__(self, parent, product, initial_quantity=1, initial_discount=0):
        self.product = product
        self.initial_quantity = initial_quantity
        self.initial_discount = initial_discount
        super().__init__(parent, title=f"Cantidad: {product.nombre}")

    def body(self, master):
        ttk.Label(master, text="Cantidad:").grid(row=0, sticky="w")
        self.quantity_var = tk.IntVar(value=self.initial_quantity)
        self.quantity_entry = ttk.Entry(master, textvariable=self.quantity_var)
        self.quantity_entry.grid(row=0, column=1)
        self.quantity_entry.select_range(0, tk.END)

        ttk.Label(master, text="Descuento (%):").grid(row=1, sticky="w")
        self.discount_var = tk.DoubleVar(value=self.initial_discount)
        ttk.Entry(master, textvariable=self.discount_var).grid(row=1, column=1)
        
        return self.quantity_entry

    def on_ok(self, event=None):
        try:
            qty = self.quantity_var.get()
            discount = self.discount_var.get()

            if qty <= 0:
                messagebox.showerror("Error", "La cantidad debe ser mayor que 0.", parent=self)
                return
            if discount < 0 or discount > 100:
                messagebox.showerror("Error", "El descuento debe estar entre 0 y 100.", parent=self)
                return

            if not PERMITIR_STOCK_NEGATIVO and qty > self.product.cantidad_stock:
                if messagebox.askyesno("Stock Insuficiente", f"Stock disponible: {self.product.cantidad_stock}.\n\n¿Desea vender el máximo disponible ({self.product.cantidad_stock})?", parent=self):
                    qty = self.product.cantidad_stock
                else:
                    return

            self.result = (qty, discount)
            self.destroy()
        except tk.TclError:
             messagebox.showerror("Error", "Por favor ingrese números válidos.", parent=self)
    
    def buttonbox(self):
        box = ttk.Frame(self)
        w = ttk.Button(box, text="OK", width=10, command=self.on_ok, default=tk.ACTIVE)
        w.pack(side=tk.LEFT, padx=5, pady=5)
        w = ttk.Button(box, text="Cancel", width=10, command=self.cancel)
        w.pack(side=tk.LEFT, padx=5, pady=5)
        self.bind("<Return>", self.on_ok)
        self.bind("<Escape>", self.cancel)
        box.pack()

class ClientFormWindow(tk.Toplevel):
    def __init__(self, parent, callback, cliente_a_editar=None):
        super().__init__(parent)
        self.callback = callback
        self.cliente_a_editar = cliente_a_editar

        self.title("Editar Cliente" if cliente_a_editar else "Añadir Cliente")
        self.geometry("350x250")
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)

        self.create_widgets()
        if self.cliente_a_editar:
            self.cargar_datos_cliente()

    def create_widgets(self):
        main_frame = ttk.Frame(self, padding=20)
        main_frame.pack(fill="both", expand=True)

        self.vars = {
            "nombre": tk.StringVar(),
            "dni": tk.StringVar(),
            "fecha_limite_pago": tk.StringVar()
        }

        ttk.Label(main_frame, text="Nombre y Apellido (*):").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(main_frame, textvariable=self.vars["nombre"]).grid(row=0, column=1, sticky="ew", pady=5)

        ttk.Label(main_frame, text="DNI (Opcional):").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(main_frame, textvariable=self.vars["dni"]).grid(row=1, column=1, sticky="ew", pady=5)

        ttk.Label(main_frame, text="Fecha Límite Pago (AAAA-MM-DD):").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(main_frame, textvariable=self.vars["fecha_limite_pago"]).grid(row=2, column=1, sticky="ew", pady=5)

        main_frame.columnconfigure(1, weight=1)

        btn_frame = ttk.Frame(self, padding=(0,0,0,10))
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="Guardar", command=self.guardar_cliente).pack(side="right", padx=20)
        ttk.Button(btn_frame, text="Cancelar", command=self.destroy).pack(side="right")

    def cargar_datos_cliente(self):
        c = self.cliente_a_editar
        self.vars["nombre"].set(c.nombre)
        self.vars["dni"].set(c.dni or "")
        self.vars["fecha_limite_pago"].set(c.fecha_limite_pago or "")

    def guardar_cliente(self):
        nombre = self.vars["nombre"].get().strip()
        if not nombre:
            messagebox.showerror("Error", "El nombre es obligatorio.", parent=self)
            return
        
        fecha_limite = self.vars["fecha_limite_pago"].get().strip() or None
        if fecha_limite:
            try:
                datetime.strptime(fecha_limite, "%Y-%m-%d")
            except ValueError:
                messagebox.showerror("Error", "Formato de fecha inválido. Use AAAA-MM-DD.", parent=self)
                return

        dni = self.vars["dni"].get().strip() or None

        if self.cliente_a_editar:
            self.cliente_a_editar.nombre = nombre
            self.cliente_a_editar.dni = dni
            self.cliente_a_editar.fecha_limite_pago = fecha_limite
            if actualizar_cliente(self.cliente_a_editar):
                messagebox.showinfo("í‰xito", "Cliente actualizado.", parent=self)
                self.destroy()
                self.callback()
            else:
                messagebox.showerror("Error", "No se pudo actualizar el cliente.", parent=self)
        else:
            nuevo_cliente = Cliente(nombre=nombre, dni=dni, fecha_limite_pago=fecha_limite)
            if agregar_cliente(nuevo_cliente):
                messagebox.showinfo("í‰xito", "Cliente creado.", parent=self)
                self.destroy()
                self.callback()
            else:
                messagebox.showerror("Error", "No se pudo crear el cliente.", parent=self)


class PaymentDialog(simpledialog.Dialog):
    def __init__(self, parent, cliente: Cliente):
        self.cliente = cliente
        super().__init__(parent, title=f"Registrar Pago: {cliente.nombre}")

    def body(self, master):
        ttk.Label(master, text=f"Saldo Deudor Actual: ${self.cliente.saldo_deudor:.2f}").grid(row=0, columnspan=2, pady=10)
        
        ttk.Label(master, text="Monto a Pagar ($):").grid(row=1, column=0, sticky="w")
        self.amount_var = tk.DoubleVar(value=0.0)
        self.entry = ttk.Entry(master, textvariable=self.amount_var)
        self.entry.grid(row=1, column=1, sticky="ew")
        self.entry.focus_set()
        self.entry.select_range(0, tk.END)

        ttk.Button(master, text="Pagar Totalidad", command=self.set_total_payment).grid(row=2, column=1, sticky="e", pady=5)
        return self.entry

    def set_total_payment(self):
        self.amount_var.set(self.cliente.saldo_deudor)

    def on_ok(self, event=None):
        try:
            monto = self.amount_var.get()
            if monto <= 0:
                messagebox.showerror("Error", "El monto debe ser positivo.", parent=self)
                return
            if monto > self.cliente.saldo_deudor:
                if not messagebox.askyesno("Confirmación", "El monto es mayor a la deuda actual. ¿Desea registrarlo como saldo a favor?"):
                    return
            
            self.result = monto
            self.destroy() # Close ONLY if valid
        except tk.TclError:
            messagebox.showerror("Error", "Ingrese un número válido.", parent=self)
            
    def buttonbox(self):
        box = ttk.Frame(self)
        w = ttk.Button(box, text="OK", width=10, command=self.on_ok, default=tk.ACTIVE)
        w.pack(side=tk.LEFT, padx=5, pady=5)
        w = ttk.Button(box, text="Cancel", width=10, command=self.cancel)
        w.pack(side=tk.LEFT, padx=5, pady=5)
        self.bind("<Return>", self.on_ok)
        self.bind("<Escape>", self.cancel)
        box.pack()

class SelectClientDialog(simpledialog.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Seleccionar Cliente")
    
    def body(self, master):
        self.geometry("400x400")
        
        top_frame = ttk.Frame(master)
        top_frame.pack(fill="x", pady=5)
        
        ttk.Label(top_frame, text="Buscar:").pack(side="left")
        self.search_var = tk.StringVar()
        entry = ttk.Entry(top_frame, textvariable=self.search_var)
        entry.pack(side="left", fill="x", expand=True, padx=5)
        entry.bind("<KeyRelease>", self.cargar_clientes)

        self.listbox = tk.Listbox(master, height=15)
        self.listbox.pack(fill="both", expand=True, pady=5)
        self.listbox.bind("<Double-Button-1>", self.on_select)
        
        self.clientes_en_lista = []
        self.cargar_clientes()
        
        ttk.Button(master, text="Crear Nuevo Cliente", command=self.add_new_client).pack(fill="x", pady=5)
        
        return entry

    def cargar_clientes(self, event=None):
        query = self.search_var.get()
        self.listbox.delete(0, tk.END)
        self.clientes_en_lista = obtener_clientes(nombre_o_dni=query)
        
        for c in self.clientes_en_lista:
            self.listbox.insert(tk.END, f"{c.nombre} (DNI: {c.dni or 'N/A'})")

    def on_select(self, event=None):
        idx = self.listbox.curselection()
        if idx:
            self.result = self.clientes_en_lista[idx[0]].id_cliente
            self.destroy()

    def add_new_client(self):
        def on_created():
            self.cargar_clientes()
            # Optionally auto-select the new client here if desired
        
        ClientFormWindow(self, callback=on_created, cliente_a_editar=None)
    
    def buttonbox(self):
        box = ttk.Frame(self)
        w = ttk.Button(box, text="Seleccionar", width=10, command=self.on_select, default=tk.ACTIVE)
        w.pack(side=tk.LEFT, padx=5, pady=5)
        w = ttk.Button(box, text="Cerrar", width=10, command=self.cancel)
        w.pack(side=tk.LEFT, padx=5, pady=5)
        self.bind("<Return>", self.on_select)
        self.bind("<Escape>", self.cancel)
        box.pack()


class ClientAccountDetailWindow(tk.Toplevel):
    def __init__(self, parent, cliente: Cliente, callback):
        super().__init__(parent)
        self.cliente = cliente
        self.callback = callback
        
        self.title(f"Estado de Cuenta: {cliente.nombre}")
        self.geometry("700x500")
        self.grab_set()

        self.create_widgets()
        self.cargar_movimientos()

    def create_widgets(self):
        info_frame = ttk.Frame(self, padding=10)
        info_frame.pack(fill="x")
        
        ttk.Label(info_frame, text=f"Cliente: {self.cliente.nombre}", font=("Helvetica", 14, "bold")).pack(anchor="w")
        ttk.Label(info_frame, text=f"DNI: {self.cliente.dni or 'N/A'}").pack(anchor="w")
        self.saldo_label = ttk.Label(info_frame, text="Saldo Deudor: Calculando...", font=("Helvetica", 12))
        self.saldo_label.pack(anchor="w", pady=5)

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(tree_frame, columns=("Fecha", "Tipo", "Monto", "Detalle"), show="headings")
        self.tree.heading("Fecha", text="Fecha")
        self.tree.heading("Tipo", text="Movimiento")
        self.tree.heading("Monto", text="Monto")
        self.tree.heading("Detalle", text="Detalles")
        
        self.tree.column("Fecha", width=150)
        self.tree.column("Tipo", width=100)
        self.tree.column("Monto", width=100)
        self.tree.column("Detalle", width=300)
        
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self.abrir_detalle_venta)

        ttk.Button(self, text="Cerrar", command=self.on_close).pack(pady=10)

    def cargar_movimientos(self):
        pass # Implementation remains as needed, referencing database.py methods
        movimientos = obtener_movimientos_cliente(self.cliente.id_cliente)
        
        total_deuda = 0
        total_pagado = 0
        
        for item in self.tree.get_children():
            self.tree.delete(item)

        for mov in movimientos:
            monto = mov['monto_actualizado']
            tipo = mov['tipo_movimiento']
            
            if tipo == 'DEUDA':
                total_deuda += monto
            elif tipo == 'PAGO':
                total_pagado += monto
            
            detalle_texto = mov['detalle_productos'] if tipo == 'DEUDA' else "Pago Registrado"

            if tipo == 'DEUDA':
                fecha_fmt = datetime.strptime(mov['fecha'], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M")
            else: 
                try: 
                    fecha_fmt = datetime.strptime(mov['fecha'], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M")
                except:
                     fecha_fmt = mov['fecha']

            self.tree.insert("", "end", values=(
                fecha_fmt,
                tipo,
                f"${monto:.2f}",
                detalle_texto
            ), tags=('pago' if tipo == 'PAGO' else 'deuda',), iid=str(mov['id_venta']) if mov['id_venta'] else f"pago_{mov['fecha']}")

        self.tree.tag_configure('pago', foreground='green')
        self.tree.tag_configure('deuda', foreground='red')
        
        saldo = total_deuda - total_pagado
        self.saldo_label.config(text=f"Saldo Deudor Actual: ${saldo:.2f}", foreground="red" if saldo > 0 else "black")

    def on_close(self):
        self.callback()
        self.destroy()

    def abrir_detalle_venta(self, event):
        selection = self.tree.selection()
        if not selection: return
        
        item_id = selection[0]
        if item_id.startswith("pago_"): return 
        
        try:
            id_venta = int(item_id)
            venta = obtener_venta_por_id(id_venta)
            if venta:
                SaleDetailWindow(self, venta)
        except ValueError:
            pass


class ReportesView(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        
        self.sales_frame = ttk.Frame(notebook)
        notebook.add(self.sales_frame, text="Reporte de Ventas")
        self.create_sales_report_widgets()

        self.restock_frame = ttk.Frame(notebook)
        notebook.add(self.restock_frame, text="Sugerencias de Reposición")
        self.create_restock_suggestion_widgets()

    def create_sales_report_widgets(self):
        controls_frame = ttk.Frame(self.sales_frame, padding=10)
        controls_frame.pack(fill="x")
        
        ttk.Label(controls_frame, text="Período:").pack(side="left")
        self.periodo_var = tk.StringVar(value="Hoy")
        periodo_cb = ttk.Combobox(controls_frame, textvariable=self.periodo_var, values=["Hoy", "Esta Semana", "Este Mes", "Año Actual"], state="readonly")
        periodo_cb.pack(side="left", padx=5)
        periodo_cb.bind("<<ComboboxSelected>>", self.on_period_change)
        
        ttk.Button(controls_frame, text="Actualizar", command=self.cargar_reporte).pack(side="left", padx=5)
        ttk.Button(controls_frame, text="Ver Detalle Venta", command=self.mostrar_detalle_venta).pack(side="left", padx=5)
        ttk.Button(controls_frame, text="Exportar a Excel", command=self.exportar_a_excel).pack(side="right", padx=5)

        content_frame = ttk.Frame(self.sales_frame)
        content_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.tree = ttk.Treeview(content_frame, columns=("ID", "Fecha", "Cliente", "Total", "Pago"), show="headings", height=10)
        self.tree.heading("ID", text="ID Venta")
        self.tree.heading("Fecha", text="Fecha")
        self.tree.heading("Cliente", text="Cliente")
        self.tree.heading("Total", text="Total")
        self.tree.heading("Pago", text="Forma Pago")
        self.tree.column("ID", width=60, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)
        
        scrollbar = ttk.Scrollbar(content_frame, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.bind("<Double-1>", self.mostrar_detalle_venta)

        stats_frame = ttk.Frame(self.sales_frame, padding=10, relief="sunken", borderwidth=1)
        stats_frame.pack(fill="x", padx=10, pady=10)
        
        self.total_ventas_var = tk.StringVar(value="$0.00")
        self.total_pagos_recibidos_var = tk.StringVar(value="$0.00")
        
        ttk.Label(stats_frame, text="Total Ventas en Período:", font=("Helvetica", 12, "bold")).grid(row=0, column=0, sticky="w", padx=20)
        ttk.Label(stats_frame, textvariable=self.total_ventas_var, font=("Helvetica", 12)).grid(row=0, column=1, sticky="w")
        
        ttk.Label(stats_frame, text="Pagos de Cta. Cte. Recibidos:", font=("Helvetica", 12, "bold")).grid(row=0, column=2, sticky="w", padx=20)
        ttk.Label(stats_frame, textvariable=self.total_pagos_recibidos_var, font=("Helvetica", 12)).grid(row=0, column=3, sticky="w")

        self.fig = Figure(figsize=(5, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.sales_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=10)

        self.cargar_reporte()

    def on_period_change(self, event=None):
        self.cargar_reporte()

    def cargar_reporte(self):
        periodo = self.periodo_var.get()
        now = datetime.now()
        
        if periodo == "Hoy":
            start_date = now.strftime("%Y-%m-%d")
            end_date = now.strftime("%Y-%m-%d")
        elif periodo == "Esta Semana":
            start_date = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
            end_date = now.strftime("%Y-%m-%d")
        elif periodo == "Este Mes":
            start_date = now.strftime("%Y-%m-01")
            end_date = now.strftime("%Y-%m-%d")
        else: # Año actual
            start_date = now.strftime("%Y-01-01")
            end_date = now.strftime("%Y-%m-%d")

        self.ventas_actuales = obtener_ventas_por_rango_de_fechas(start_date, end_date)
        
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        total_periodo = 0
        for venta in self.ventas_actuales:
            self.tree.insert("", "end", iid=venta.id_venta, values=(
                venta.id_venta,
                datetime.strptime(venta.fecha_venta, "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M"),
                venta.nombre_cliente,
                f"${venta.total:.2f}",
                venta.forma_pago
            ))
            total_periodo += venta.total
        
        self.total_ventas_var.set(f"${total_periodo:.2f}")

        total_pagos_recibidos = obtener_pagos_recibidos_por_rango(start_date, end_date)
        self.total_pagos_recibidos_var.set(f"${total_pagos_recibidos:.2f}")

        self.actualizar_grafico()

    def mostrar_detalle_venta(self, event=None):
        selection = self.tree.selection()
        if not selection: return
        
        id_venta = int(selection[0])
        venta = next((v for v in self.ventas_actuales if v.id_venta == id_venta), None)
        
        if venta:
            SaleDetailWindow(self, venta)

    def actualizar_grafico(self):
        self.ax.clear()
        
        if not self.ventas_actuales:
            self.canvas.draw()
            return
            
        fechas = [datetime.strptime(v.fecha_venta, "%Y-%m-%d %H:%M:%S").date() for v in self.ventas_actuales]
        totales = defaultdict(float)
        
        for v in self.ventas_actuales:
             fecha = datetime.strptime(v.fecha_venta, "%Y-%m-%d %H:%M:%S").date()
             totales[fecha] += v.total
             
        fechas_unicas = sorted(list(totales.keys()))
        montos = [totales[f] for f in fechas_unicas]
        
        self.ax.plot(fechas_unicas, montos, marker='o', linestyle='-')
        self.ax.set_title("Ventas por Día")
        self.ax.grid(True)
        self.fig.autofmt_xdate()
        
        self.canvas.draw()

    def exportar_a_excel(self):
        if not self.ventas_actuales:
             messagebox.showwarning("Sin Datos", "No hay ventas para exportar.")
             return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel Files", "*.xlsx")],
            title="Guardar Reporte de Ventas"
        )
        
        if not filepath: return
        
        data = []
        for v in self.ventas_actuales:
            data.append({
                "ID Venta": v.id_venta,
                "Fecha": v.fecha_venta,
                "Cliente": v.nombre_cliente,
                "Total": v.total,
                "Forma Pago": v.forma_pago
            })
            
        df = pd.DataFrame(data)
        try:
            df.to_excel(filepath, index=False)
            messagebox.showinfo("Exportar", "Reporte exportado con éxito.")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo exportar: {e}")

    def create_restock_suggestion_widgets(self):
        f = ttk.Frame(self.restock_frame, padding=10)
        f.pack(fill="x")
        
        ttk.Label(f, text="Días de Análisis (Ventas pasadas):").pack(side="left")
        self.dias_analisis_entry = ttk.Entry(f, width=5)
        self.dias_analisis_entry.pack(side="left", padx=5)
        self.dias_analisis_entry.insert(0, "30")

        ttk.Label(f, text="Días de Cobertura (Futuro):").pack(side="left")
        self.dias_cobertura_entry = ttk.Entry(f, width=5)
        self.dias_cobertura_entry.pack(side="left", padx=5)
        self.dias_cobertura_entry.insert(0, "15")

        ttk.Button(f, text="Generar Sugerencias", command=self.generar_sugerencias).pack(side="left", padx=10)
        ttk.Button(f, text="Exportar", command=self.exportar_sugerencias_a_excel).pack(side="right")

        self.tree_sugg = ttk.Treeview(self.restock_frame, columns=("Producto", "Stock Actual", "Ventas Periodo", "Venta Prom/Dia", "Stock Sugerido", "A Comprar"), show="headings")
        self.tree_sugg.heading("Producto", text="Producto")
        self.tree_sugg.heading("Stock Actual", text="Stock Actual")
        self.tree_sugg.heading("Ventas Periodo", text="Ventas (Período)")
        self.tree_sugg.heading("Venta Prom/Dia", text="Prom. Venta/Día")
        self.tree_sugg.heading("Stock Sugerido", text="Stock Sugerido")
        self.tree_sugg.heading("A Comprar", text="Cant. a Reponer")
        
        self.tree_sugg.pack(fill="both", expand=True, padx=10, pady=10)

        self.sugerencias_data = []

    def generar_sugerencias(self):
        try:
            dias_an = int(self.dias_analisis_entry.get())
            dias_cob = int(self.dias_cobertura_entry.get())
        except ValueError:
            messagebox.showerror("Error", "Ingrese números válidos para los días.")
            return

        for item in self.tree_sugg.get_children():
            self.tree_sugg.delete(item)

        self.sugerencias_data = obtener_sugerencias_reposicion(dias_an, dias_cob)
        
        for row in self.sugerencias_data:
            self.tree_sugg.insert("", "end", values=(
                row['nombre'],
                row['stock_actual'],
                row['ventas_periodo'],
                f"{row['venta_diaria_prom']:.2f}",
                f"{row['stock_sugerido']:.2f}",
                int(row['cantidad_a_comprar'])
            ))

    def exportar_sugerencias_a_excel(self):
        if not self.sugerencias_data:
             messagebox.showwarning("Sin Datos", "Genere las sugerencias primero.")
             return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel Files", "*.xlsx")],
            title="Guardar Sugerencias de Reposición"
        )
        
        if not filepath: return
        
        data = [dict(row) for row in self.sugerencias_data]
        df = pd.DataFrame(data)
        try:
            df.to_excel(filepath, index=False)
            messagebox.showinfo("Exportar", "Sugerencias exportadas con éxito.")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo exportar: {e}")

class SaleDetailWindow(tk.Toplevel):
    def __init__(self, parent, venta: Venta):
        super().__init__(parent)
        self.venta = venta
        self.title(f"Detalle de Venta #{venta.id_venta}")
        self.geometry("600x400")
        
        self.create_widgets()
        
    def create_widgets(self):
        info_frame = ttk.Frame(self, padding=10)
        info_frame.pack(fill="x")
        
        ttk.Label(info_frame, text=f"Fecha: {datetime.strptime(self.venta.fecha_venta, '%Y-%m-%d %H:%M:%S').strftime('%d/%m/%Y %H:%M')}").pack(anchor="w")
        ttk.Label(info_frame, text=f"Cliente: {self.venta.nombre_cliente}").pack(anchor="w")
        ttk.Label(info_frame, text=f"Forma de Pago: {self.venta.forma_pago}").pack(anchor="w")
        ttk.Label(info_frame, text=f"Total: ${self.venta.total:.2f}", font=("Helvetica", 12, "bold")).pack(anchor="w", pady=5)
        
        if self.venta.ruta_pdf_ticket:
            ttk.Button(info_frame, text="Ver Ticket PDF (Generado)", command=self.abrir_ticket_pdf).pack(anchor="w")

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(fill="both", expand=True)

        tree = ttk.Treeview(tree_frame, columns=("Prod", "Cant", "Precio", "Subt"), show="headings")
        tree.heading("Prod", text="Producto")
        tree.heading("Cant", text="Cant")
        tree.heading("Precio", text="P. Unit")
        tree.heading("Subt", text="Subtotal")
        
        tree.pack(fill="both", expand=True)
        
        for d in self.venta.detalles:
             prod = obtener_producto_por_id(d.id_producto)
             nombre = prod.nombre if prod else f"ID {d.id_producto}"
             tree.insert("", "end", values=(
                 nombre, d.cantidad, f"${d.precio_unitario:.2f}", f"${d.subtotal:.2f}"
             ))

        ttk.Button(self, text="Cerrar", command=self.destroy).pack(pady=10)

    def abrir_ticket_pdf(self):
        if self.venta.ruta_pdf_ticket and os.path.exists(self.venta.ruta_pdf_ticket):
            webbrowser.open(self.venta.ruta_pdf_ticket)
        else:
            messagebox.showerror("Error", "El archivo del ticket no existe.")


class MainApplication(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Sistema de Gestión - {NOMBRE_NEGOCIO}")
        self.geometry("1100x700")
        
        self.style = ttk.Style(self)
        self.style.theme_use('clam')
        self.config_styles()

        self.create_widgets()
        
        inicializar_bd()

    def config_styles(self):
        self.style.configure("TFrame", background="#f0f0f0")
        self.style.configure("TLabel", background="#f0f0f0", font=("Helvetica", 10))
        self.style.configure("TButton", font=("Helvetica", 10, "bold"), padding=6)
        self.style.configure("Accent.TButton", foreground="white", background="#4CAF50", font=("Helvetica", 10, "bold"))
        self.configure(background="#f0f0f0")

    def create_widgets(self):
        main_notebook = ttk.Notebook(self)
        main_notebook.pack(fill="both", expand=True)

        self.ventas_view = VentasView(main_notebook)
        main_notebook.add(self.ventas_view, text="Ventas (F1)")

        self.stock_view = StockView(main_notebook)
        main_notebook.add(self.stock_view, text="Control de Stock (F2)")

        self.clientes_view = ClientesView(main_notebook)
        main_notebook.add(self.clientes_view, text="Clientes (F3)")

        self.reportes_view = ReportesView(main_notebook)
        main_notebook.add(self.reportes_view, text="Reportes y Estadísticas (F4)")

        self.bind("<F1>", lambda e: main_notebook.select(0))
        self.bind("<F2>", lambda e: main_notebook.select(1))
        self.bind("<F3>", lambda e: main_notebook.select(2))
        self.bind("<F4>", lambda e: main_notebook.select(3))
        
        main_notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

    def on_tab_changed(self, event):
        tab_name = event.widget.tab("current")["text"]
        if "Ventas" in tab_name:
            self.ventas_view.on_view_enter()
        elif "Stock" in tab_name:
            self.stock_view.cargar_productos()
        elif "Clientes" in tab_name:
            self.clientes_view.cargar_clientes()

if __name__ == "__main__":
    app = MainApplication()
    app.mainloop()

