import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sys
import os
import configparser
import hashlib
from datetime import datetime
from PIL import Image, ImageTk  
from database import (inicializar_bd, verificar_usuario, cambiar_contrasena_usuario, 
                      get_persistent_path, crear_backup_seguro)
from views import StockView, VentasView, ClientesView, ReportesView, resource_path

class LoginWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Iniciar Sesión - EasySt")
        self.geometry("700x400") 
        self.resizable(False, False)
        self.configure(bg="#FFFFFF") 

        # Centrar la ventana
        self.eval('tk::PlaceWindow . center')

        # --- Estilo para el botón de login ---
        style = ttk.Style(self)
        style.theme_use("clam") # Usar un tema que permita personalizar colores
        style.configure("Accent.TButton",
            font=("Helvetica", 12, "bold"),
            padding=(10, 10),
            background="#4CAF50", # Verde
            foreground="#FFFFFF" 
        )
        style.map("Accent.TButton",
            background=[("active", "#3e8e41")]
        )

        # --- Contenedor principal dividido en dos ---
        main_frame = tk.Frame(self, bg="#FFFFFF")
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)

        # --- Lado Izquierdo: Imagen ---
        try:
            image_path = resource_path('mesa de trabajo.png')
            # Verificamos explícitamente si el archivo existe antes de intentar abrirlo
            if not os.path.exists(image_path):
                raise FileNotFoundError(f"El archivo no se encontró en la ruta esperada:\n{image_path}")

            original_image = Image.open(image_path)
            resized_image = original_image.resize((300, 300), Image.Resampling.LANCZOS)
            self.logo_image = ImageTk.PhotoImage(resized_image)
            image_label = tk.Label(main_frame, image=self.logo_image, bg="#FFFFFF")
            image_label.pack(side="left", fill="both", expand=True, padx=(0, 20))
        except Exception as e:
            # Mostramos una advertencia clara en lugar de solo imprimir en consola
            messagebox.showwarning("Error de Imagen", f"No se pudo cargar 'mesa de trabajo.png'.\n\nError: {e}\n\nAsegúrese de que el archivo esté en la misma carpeta que la aplicación.")

        # --- Lado Derecho: Formulario de Login ---
        login_frame = ttk.Frame(main_frame)
        login_frame.pack(side="right", fill="y", expand=False, padx=(20, 0))

        ttk.Label(login_frame, text="Usuario:", font=("Helvetica", 12)).pack(pady=(50, 5), anchor="w")
        self.user_entry = ttk.Entry(login_frame, font=("Helvetica", 12), width=30)
        self.user_entry.pack(pady=5, fill="x")

        ttk.Label(login_frame, text="Contraseña:", font=("Helvetica", 12)).pack(pady=(10, 5), anchor="w")
        self.pass_entry = ttk.Entry(login_frame, show="*", font=("Helvetica", 12), width=30)
        self.pass_entry.pack(pady=5, fill="x")
        self.pass_entry.bind("<Return>", self.attempt_login) # Iniciar sesión con Enter

        # Creamos el botón y le aplicamos el estilo verde que ya definimos
        login_button = ttk.Button(
            login_frame, text="Ingresar", 
            command=self.attempt_login, style="Accent.TButton"
        )
        login_button.pack(pady=20, fill="x", ipady=5)

        self.user_entry.focus()

    def attempt_login(self, event=None):
        # Obtenemos los datos de los campos de entrada y también los limpiamos con .strip()
        self.entered_username = self.user_entry.get().strip() # Guardamos el usuario antes de destruir
        entered_user = self.user_entry.get().strip()
        entered_pass = self.pass_entry.get().strip()

        if not entered_user or not entered_pass:
            messagebox.showwarning("Campos Vacíos", "Por favor, ingrese usuario y contraseña.")
            return

        rol_usuario = verificar_usuario(entered_user, entered_pass)

        if rol_usuario:
            self.logged_in = True # Establecer una bandera de éxito
            self.user_role = rol_usuario # Guardar el rol del usuario
            self.destroy() # Cierra la ventana de login
        else:
            messagebox.showerror("Error de Acceso", "Usuario o contraseña incorrectos.")

class App(tk.Tk):
    def __init__(self, user_role: str):
        super().__init__()
        self.current_user = None # Guardaremos el nombre del usuario logueado
        self.user_role = user_role

        self.title("EasySt - Sistema de Gestión")
        self.geometry("1280x720")
        self.resizable(True, True) # La ventana ahora se puede redimensionar
        self.configure(bg="#FFFFFF") # Fondo blanco para la ventana principal

        self.setup_styles()
        self.create_main_layout(self.user_role)
        self.after(100, lambda: self.show_view(VentasView)) # Cargar la vista inicial de forma segura

    def setup_styles(self):
        style = ttk.Style(self)
        
        # Colores
        BG_COLOR = "#F5F5F5" # Gris muy claro
        SIDEBAR_COLOR = "#E8E8E8" # Gris un poco más oscuro
        ACCENT_COLOR = "#4CAF50" # Verde
        TEXT_COLOR = "#000000" # Negro
        WHITE = "#FFFFFF"

        style.theme_use("clam")

        # Estilo para los botones de la barra lateral
        style.configure("Sidebar.TButton",
            font=("Helvetica", 12),
            padding=(10, 15),
            background=SIDEBAR_COLOR,
            foreground=TEXT_COLOR,
            borderwidth=0,
            anchor="w",
            wraplength=180  # Permite que el texto se divida en varias líneas si supera los 180px
        )
        style.map("Sidebar.TButton",
            background=[("active", ACCENT_COLOR)],
            foreground=[("active", WHITE)]
        )
        
        # Estilo para botones de acción principales (verdes)
        style.configure("Accent.TButton",
            font=("Helvetica", 12, "bold"),
            padding=(10, 10),
            background=ACCENT_COLOR,
            foreground=WHITE
        )
        style.map("Accent.TButton",
            background=[("active", "#3e8e41")] # Un verde un poco más oscuro al presionar
        )
        
        # Estilo para botones deshabilitados
        style.configure("Disabled.TButton", foreground="grey")

    def create_main_layout(self, user_role):
        # Barra lateral
        self.sidebar_frame = ttk.Frame(self, width=200, style="TFrame")
        self.sidebar_frame.pack(side="left", fill="y")
        self.sidebar_frame.configure(style="TFrame")
        self.sidebar_frame.pack_propagate(False) # Evita que el frame cambie de tamaño

        # --- Botones de navegación con control de roles ---
        ttk.Button(self.sidebar_frame, text="Ventas", style="Sidebar.TButton", command=lambda: self.show_view(VentasView)).pack(fill="x", pady=2)
        ttk.Button(self.sidebar_frame, text="Stock / Productos", style="Sidebar.TButton", command=lambda: self.show_view(StockView)).pack(fill="x", pady=2)
        ttk.Button(self.sidebar_frame, text="Clientes", style="Sidebar.TButton", command=lambda: self.show_view(ClientesView)).pack(fill="x", pady=2)

        # --- Funciones solo para Administradores ---
        if user_role == "Administrador":
            ttk.Button(self.sidebar_frame, text="Reportes", style="Sidebar.TButton", command=lambda: self.show_view(ReportesView)).pack(fill="x", pady=2)
            
            # Separador visual para las funciones de administración
            ttk.Separator(self.sidebar_frame, orient='horizontal').pack(fill='x', pady=10, padx=20)

            ttk.Button(self.sidebar_frame, text="Crear Copia de Seguridad", style="Sidebar.TButton", command=self.create_backup).pack(fill="x", pady=2)
            ttk.Button(self.sidebar_frame, text="Restaurar Copia", style="Sidebar.TButton", command=self.restore_backup).pack(fill="x", pady=2)

        else:
            # Si no es admin, el botón de reportes aparece deshabilitado
            ttk.Button(self.sidebar_frame, text="Reportes", style="Disabled.TButton", state="disabled").pack(fill="x", pady=2)


        # --- Frame inferior para logo y botón de cambiar contraseña ---
        bottom_frame = ttk.Frame(self.sidebar_frame)
        bottom_frame.pack(side="bottom", fill="x", pady=10)

        # Botón para cambiar contraseña
        ttk.Button(bottom_frame, text="Cambiar Contraseña", command=self.open_change_password_window).pack(fill="x", padx=10, pady=5)

        # Logo
        try:
            logo_path = resource_path('mesa de trabajo.png')
            if not os.path.exists(logo_path):
                raise FileNotFoundError("Logo no encontrado")

            original_logo = Image.open(logo_path)
            resized_logo = original_logo.resize((40, 40), Image.Resampling.LANCZOS)
            self.app_logo = ImageTk.PhotoImage(resized_logo)
            logo_label = ttk.Label(bottom_frame, image=self.app_logo)
            logo_label.pack(pady=10)
        except Exception as e:
            print(f"Advertencia: No se pudo cargar el logo en la app principal: {e}") # Silencioso

        # Contenedor principal para las vistas
        self.main_container = ttk.Frame(self, style="TFrame")
        self.main_container.pack(side="right", fill="both", expand=True)

    def show_view(self, ViewClass):
        """
        Limpia el contenedor principal y muestra la vista (clase) pasada como argumento.
        """
        # Limpiar el contenedor principal antes de mostrar una nueva vista
        for widget in self.main_container.winfo_children():
            widget.destroy()
        
        view = ViewClass(self.main_container) # Crea una instancia de la vista (ej. ReportesView)
        view.pack(side="top", fill="both", expand=True)

        # Si la vista que estamos mostrando tiene un método 'on_view_enter', lo llamamos.
        if hasattr(view, 'on_view_enter'):
            view.on_view_enter()

    def open_change_password_window(self):
        """Abre la ventana para cambiar la contraseña del usuario actual."""
        ChangePasswordWindow(self, self.current_user)

    def create_backup(self):
        """Abre un diálogo para guardar una copia de la base de datos."""
        # Obtenemos la ruta de la BD en el momento de la ejecución para asegurar
        try: # El try/except ahora envuelve toda la operación.
            # Sugerir un nombre de archivo con la fecha actual
            default_filename = f"backup_easyst_{datetime.now().strftime('%Y-%m-%d')}.db"
            
            # Pedir al usuario la ubicación para guardar el archivo
            backup_path = filedialog.asksaveasfilename(
                title="Guardar Copia de Seguridad como...",
                defaultextension=".db",
                initialfile=default_filename,
                filetypes=[("Archivos de Base de Datos", "*.db"), ("Todos los archivos", "*.*")]
            )

            if backup_path:
                if crear_backup_seguro(backup_path):
                    messagebox.showinfo("Copia de Seguridad Creada", f"La copia de seguridad se ha guardado con éxito en:\n{backup_path}")
                else:
                    messagebox.showerror("Error al Crear Copia", "No se pudo crear la copia de seguridad. Revise los registros para más detalles.")

        except Exception as e:
            messagebox.showerror("Error al Crear Copia", f"No se pudo crear la copia de seguridad:\n{e}")

    def restore_backup(self):
        """Restaura la base de datos desde un archivo de copia de seguridad."""
        # Advertencia MUY importante para el usuario
        confirm = messagebox.askyesno(
            "¡ADVERTENCIA! Restaurar Copia de Seguridad",
            "Está a punto de reemplazar TODOS los datos actuales (productos, ventas, clientes, etc.) "
            "con los datos de un archivo de copia de seguridad.\n\n"
            "Esta acción NO SE PUEDE DESHACER.\n\n"
            "¿Está seguro de que desea continuar?",
            icon='warning'
        )

        if not confirm:
            return

        backup_path = filedialog.askopenfilename(
            title="Seleccionar archivo de Copia de Seguridad para restaurar",
            filetypes=[("Archivos de Base de Datos", "*.db"), ("Todos los archivos", "*.*")]
        )

        if backup_path:
            try:
                import shutil # Importamos shutil aquí porque solo se usa en este bloque
                db_file_path = get_persistent_path('easyst.db')
                shutil.copyfile(backup_path, db_file_path)
                messagebox.showinfo("Restauración Exitosa", "La base de datos ha sido restaurada.\n\nLa aplicación debe reiniciarse para aplicar los cambios. Por favor, ciérrela y vuelva a abrirla.")
                self.destroy() # Cierra la aplicación para forzar el reinicio
            except Exception as e:
                messagebox.showerror("Error de Restauración", f"No se pudo restaurar la base de datos:\n{e}")


class ChangePasswordWindow(tk.Toplevel):
    """Ventana modal para cambiar la contraseña del usuario."""
    def __init__(self, parent, username):
        super().__init__(parent)
        self.parent = parent
        self.username = username

        self.title("Cambiar Contraseña")
        self.geometry("400x250")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.create_widgets()

    def create_widgets(self):
        main_frame = ttk.Frame(self, padding=20)
        main_frame.pack(fill="both", expand=True)

        # Contraseña Actual
        ttk.Label(main_frame, text="Contraseña Actual:").grid(row=0, column=0, sticky="w", pady=5)
        self.current_pass_entry = ttk.Entry(main_frame, show="*")
        self.current_pass_entry.grid(row=0, column=1, sticky="ew", pady=5)
        self.current_pass_entry.focus_set()

        # Nueva Contraseña
        ttk.Label(main_frame, text="Nueva Contraseña:").grid(row=1, column=0, sticky="w", pady=5)
        self.new_pass_entry = ttk.Entry(main_frame, show="*")
        self.new_pass_entry.grid(row=1, column=1, sticky="ew", pady=5)

        # Confirmar Nueva Contraseña
        ttk.Label(main_frame, text="Confirmar Contraseña:").grid(row=2, column=0, sticky="w", pady=5)
        self.confirm_pass_entry = ttk.Entry(main_frame, show="*")
        self.confirm_pass_entry.grid(row=2, column=1, sticky="ew", pady=5)
        self.confirm_pass_entry.bind("<Return>", self.save_password)

        main_frame.columnconfigure(1, weight=1)

        # Botones
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=3, column=0, columnspan=2, pady=(20, 0))
        ttk.Button(button_frame, text="Guardar Cambios", command=self.save_password, style="Accent.TButton").pack(side="right")
        ttk.Button(button_frame, text="Cancelar", command=self.destroy).pack(side="right", padx=10)

    def save_password(self, event=None):
        current_pass = self.current_pass_entry.get()
        new_pass = self.new_pass_entry.get()
        confirm_pass = self.confirm_pass_entry.get()

        # Validaciones
        if not all([current_pass, new_pass, confirm_pass]):
            messagebox.showerror("Campos Vacíos", "Por favor, complete todos los campos.", parent=self)
            return

        if new_pass != confirm_pass:
            messagebox.showerror("Error", "Las nuevas contraseñas no coinciden.", parent=self)
            return

        if len(new_pass) < 4:
            messagebox.showwarning("Contraseña Débil", "La nueva contraseña debe tener al menos 4 caracteres.", parent=self)
            return

        # Intentar cambiar la contraseña en la base de datos
        success = cambiar_contrasena_usuario(self.username, current_pass, new_pass)

        if success:
            messagebox.showinfo("Éxito", "La contraseña se ha cambiado correctamente.", parent=self)
            self.destroy()
        else:
            messagebox.showerror("Error", "La contraseña actual es incorrecta.", parent=self)


def verificar_licencia():
    """
    Verifica la presencia y validez de un archivo de licencia.
    El instalador debe crear 'license.key' con un contenido específico.
    """
    # El contenido que esperamos encontrar en el archivo de licencia.
    # Puedes cambiar "CLAVE-SECRETA-EASYST-2024" por cualquier otra cosa.
    CLAVE_SECRETA = "Carp0912"
    
    try:
        # --- CAMBIO IMPORTANTE ---
        # Determinar la ruta base. Si está empaquetado, es el directorio del .exe
        if getattr(sys, 'frozen', False):
            # Estamos ejecutando el .exe empaquetado
            base_path = os.path.dirname(sys.executable)
        else:
            # Estamos en modo desarrollo (ejecutando el .py)
            base_path = os.path.abspath(".")
        ruta_licencia = os.path.join(base_path, "license.key")
        with open(ruta_licencia, 'r', encoding='utf-8') as f:
            contenido = f.read().strip()
        
        # Comprueba si el contenido del archivo es igual a nuestra clave secreta.
        return contenido == CLAVE_SECRETA
    except Exception:
        return False

if __name__ == "__main__":
    # Comprobar si se pasó un argumento especial para limpiar la base de datos
    db_file_to_clean = get_persistent_path('easyst.db')
    if "--limpiar-para-build" in sys.argv:
        if os.path.exists(db_file_to_clean):
            try:
                os.remove(db_file_to_clean)
                print(f"Éxito: Se ha eliminado '{db_file_to_clean}' para preparar el empaquetado.")
            except OSError as e:
                print(f"Error: No se pudo eliminar '{db_file_to_clean}'. Causa: {e}")
        else:
            print(f"Información: El archivo '{db_file_to_clean}' no existe. No se necesita limpieza.")
        # Salimos del script después de limpiar, no queremos iniciar la app.
        sys.exit(0)

    # --- Flujo normal de la aplicación ---
    else:
        # 0. Verificamos la licencia ANTES de iniciar nada.
        if not verificar_licencia():
            # Usamos un messagebox de bajo nivel porque la app de Tkinter aún no ha sido creada.
            tk.Tk().withdraw() # Oculta la ventana raíz de Tkinter
            messagebox.showerror("Error de Activación", "La aplicación no está activada o la licencia no es válida. Por favor, instale el programa usando el instalador oficial.")
            sys.exit(1) # Cierra el programa

        # 1. Inicializamos la base de datos
        inicializar_bd()

        # 2. Mostramos la ventana de login y esperamos a que se cierre
        login_window = LoginWindow()
        login_window.logged_in = False # Inicializamos la bandera
        login_window.entered_username = None # Inicializamos la nueva variable
        login_window.user_role = None # Inicializamos el rol
        login_window.mainloop()

        # 3. Si el login fue exitoso, iniciamos la aplicación principal
        if login_window.logged_in:
            app = App(user_role=login_window.user_role) # Pasamos el rol
            app.current_user = login_window.entered_username # Usamos la variable guardada
            app.mainloop()