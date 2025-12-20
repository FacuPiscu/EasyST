from database import obtener_clientes
import traceback

try:
    print("Importing database...")
    import database
    print("Database imported.")
    
    print("Checking function...")
    print(database.obtener_clientes)
    print("Function exists.")
    
except Exception as e:
    print(f"Error: {e}")
    traceback.print_exc()
