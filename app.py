import datetime
import os
import re
import requests
from collections import defaultdict
from functools import wraps

# --- Modificado para la autenticación ---
from flask import Flask, jsonify, Response, request, send_from_directory
from flask_cors import CORS

# --- 1. CONFIGURACIÓN DE HIBOT ---
HIBOT_BASE_URL = "https://pdn.api.hibot.us/api_external"
HIBOT_APP_ID = os.environ.get("HIBOT_APP_ID")
HIBOT_APP_SECRET = os.environ.get("HIBOT_APP_SECRET")

# --- 2. CONFIGURACIÓN DE SEGURIDAD DEL DASHBOARD ---
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "password")
# -----------------------------------------------------------

app = Flask(__name__)
CORS(app, resources={r"/api/datos": {"origins": "*"}})

# --- 3. LÓGICA DE AUTENTICACIÓN ---

def check_auth(username, password):
    return username == DASHBOARD_USER and password == DASHBOARD_PASS

def authenticate():
    return Response(
    'Acceso denegado.\n'
    'Debes iniciar sesión para ver esta página.', 401,
    {'WWW-Authenticate': 'Basic realm="Login Requerido"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# --- 4. LÓGICA DE PARSEO DE AGENTES (MODIFICADA) ---

ROLES_ORDEN = {"J": 1, "SUP": 2, "C": 3, "V": 4, "VD": 5, "TR": 6, "Otro": 99, "Sistema": 100}

AGENT_PATTERN = re.compile(
    r'^(R\d+)\s+(VD|TR|J|C|V)\s*-?\s+(.+)$',
    re.IGNORECASE
)

# --- ¡NUEVO! FUNCIÓN DE ORDEN PARA BOTONES ---
def get_sort_key(tienda_name):
    # 1. Tiendas R (numéricamente)
    if tienda_name.startswith('R') and tienda_name[1:].isdigit():
        return (1, int(tienda_name[1:]))
    
    # 2. Tiendas especiales
    if tienda_name == "Canal Digital":
        return (2, 0)
    if tienda_name == "Jefe de Venta":
        return (3, 0)
    if tienda_name == "No Asignado":
        return (4, 0)
        
    # 5. Cualquier otra cosa
    return (5, tienda_name)

def parse_agent_name(name):
    if not name:
        return "No Asignado", "Otro", "Sin Nombre"
    
    name_upper = name.upper().strip()

    if name_upper == "CAMILA":
        return "Canal Digital", "J", name
    
    if name_upper == "FRANCO":
        return "Canal Digital", "SUP", name
    
    if name_upper == "ANA PAULA":
        return "Jefe de Venta", "J", name

    if name_upper == "MAITE":
        return "Jefe de Venta", "J", name
    
    match = AGENT_PATTERN.match(name)
    if match:
        tienda = match.group(1).upper()
        rol = match.group(2).upper()
        nombre = match.group(3).strip()
        return tienda, rol, nombre
    else:
        print(f"Nombre de agente no coincide con el patrón: '{name}'. Asignado a 'No Asignado'.")
        return "No Asignado", "Otro", name

# --- 5. LÓGICA DE DATOS HIBOT ---

def get_hibot_token():
    login_url = f"{HIBOT_BASE_URL}/login"
    print(f"Intentando autenticarse en: {login_url}")
    
    if not HIBOT_APP_ID or not HIBOT_APP_SECRET:
        print("Error: Las variables HIBOT_APP_ID o HIBOT_APP_SECRET no están configuradas.")
        return None
    try:
        payload = {"appId": HIBOT_APP_ID, "appSecret": HIBOT_APP_SECRET}
        response = requests.post(login_url, json=payload)
        response.raise_for_status()
        token = response.json().get('token')
        print("Token de HIBOT obtenido exitosamente.")
        return token
    except requests.exceptions.RequestException as e:
        print(f"Error al autenticarse en HIBOT: {e}")
        return None

def fetch_hibot_template_data(token):
    if not token:
        print("No
