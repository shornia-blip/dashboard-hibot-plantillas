import datetime
import os
import re
import requests  # Importación clave
from collections import defaultdict
from functools import wraps  # Para la autenticación

# --- Modificado para la autenticación ---
from flask import Flask, jsonify, Response, request, send_from_directory
from flask_cors import CORS

# --- 1. CONFIGURACIÓN DE HIBOT ---
# Lee los secretos de HIBOT desde las Variables de Entorno de Render
HIBOT_BASE_URL = "https://pdn.api.hibot.us/api_external"
HIBOT_APP_ID = os.environ.get("HIBOT_APP_ID")
HIBOT_APP_SECRET = os.environ.get("HIBOT_APP_SECRET")

# --- 2. ¡NUEVA CONFIGURACIÓN DE SEGURIDAD DEL DASHBOARD! ---
# Lee el usuario y contraseña del dashboard desde las Variables de Entorno de Render
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "password")
# -----------------------------------------------------------

# Inicializa la aplicación Flask
app = Flask(__name__)
# Configura CORS
CORS(app, resources={r"/api/datos": {"origins": "*"}})

# --- 3. LÓGICA DE AUTENTICACIÓN (NUEVO) ---

def check_auth(username, password):
    """Verifica el usuario y contraseña."""
    return username == DASHBOARD_USER and password == DASHBOARD_PASS

def authenticate():
    """Envía una respuesta 401 para pedir autenticación."""
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

# --- 4. LÓGICA DE PARSEO DE AGENTES ---
# Define los roles y su orden
ROLES_ORDEN = {"J": 1, "C": 2, "V": 3, "VD": 4, "TR": 5, "Otro": 99}
# Compila la expresión regular para extraer los datos del agente
AGENT_PATTERN = re.compile(
    r'^(R\d+)\s+(VD|TR|J|C|V)\s*-?\s+(.+)$',
    re.IGNORECASE
)

def parse_agent_name(name):
    """
    Analiza el nombre de un agente y devuelve tienda, rol y nombre.
    """
    if not name:
        return "No Asignado", "Otro", "Sin Nombre"
        
    # Excluir agentes de supervisión
    if name.upper() in ["CAMILA", "FRANCO"]:
        return None, None, None # Retorna None para ser filtrado

    match = AGENT_PATTERN.match(name)
    if match:
        tienda = match.group(1).upper()
        rol = match.group(2).upper()
        nombre = match.group(3).strip()
        return tienda, rol, nombre
    else:
        # Si no coincide, va a "No Asignado"
        print(f"Nombre de agente no coincide con el patrón: '{name}'. Asignado a 'No Asignado'.")
        return "No Asignado", "Otro", name

# --- 5. LÓGICA DE DATOS HIBOT ---

def get_hibot_token():
    """
    Se autentica contra la API de HIBOT y obtiene un token de acceso.
    """
    login_url = f"{HIBOT_BASE_URL}/login"
    print(f"Intentando autenticarse en: {login_url}")
    
    # NUEVA VERIFICACIÓN DE SEGURIDAD
    if not HIBOT_APP_ID or not HIBOT_APP_SECRET:
        print("Error: Las variables HIBOT_APP_ID o HIBOT_APP_SECRET no están configuradas en el entorno de Render.")
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
    """
    Obtiene el historial de conversaciones usando el endpoint POST /conversations.
    """
    if not token:
        print("No hay token, no se pueden obtener datos.")
        return None # Devuelve None para que la función principal maneje el error

    conversations_url = f"{HIBOT_BASE_URL}/conversations"
    headers = {"Authorization": f"Bearer {token}"}
    
    # --- ¡CORRECCIÓN DE FECHA! ---
    # Tus datos de HIBOT están en 2025. Forzamos el rango a esa fecha.
    # Cuando tus datos en HIBOT sean actuales (ej. 2024), 
    # podemos volver a usar las líneas de 'datetime.datetime.now()'
    
    # fecha_hasta = datetime.datetime.now()
    # fecha_desde = fecha_hasta - datetime.timedelta(days=30)
    
    # Rango de fechas "forzado" para que coincida con tus datos de 2025:
    fecha_hasta = datetime.datetime(2025, 10, 30) # 30 de Octubre, 2025
    fecha_desde = datetime.datetime(2025, 9, 29) # 29 de Septiembre, 2025
    # ---------------------------
    
    timestamp_hasta = int(fecha_hasta.timestamp() * 1000)
    timestamp_desde = int(fecha_desde.timestamp() * 1000)

    payload = {"from": timestamp_desde, "to": timestamp_hasta, "channelType": "WHATSAPP"}
    
    print(f"Obteniendo conversaciones desde {fecha_desde} hasta {fecha_hasta}...")
    try:
        response = requests.post(conversations_url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        
        if data and isinstance(data, list):
            print(f"Se obtuvieron {len(data)} conversaciones para procesar.")
            # Descomenta esta línea si necesitas depurar la estructura de nuevo
            # print(data[0]) 
            return data
        else:
            print("Respuesta exitosa pero no se encontraron conversaciones.")
            return []
    except requests.exceptions.RequestException as e:
        print(f"Error al obtener conversaciones de HIBOT: {e}")
        return None # Devuelve None para que la función principal maneje el error

def process_data(raw_data):
    """
    Procesa la lista de conversaciones para calcular datos de dirección y agentes.
    """
    print("Procesando datos (Lógica de Agente + Dirección)...")
    
    # Para los gráficos de IN vs OUT
    uso_diario = defaultdict(lambda: defaultdict(int))
    uso_acumulado = defaultdict(int)
    todas_las_fechas = set()
    
    # Para la tabla de Agentes (solo OUT)
    agentes_data = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    uso_hoy_tienda = defaultdict(int)
    
    # ¡IMPORTANTE! Usamos la fecha "forzada" (o una fecha dentro de ese rango) 
    # para la columna "Límite de Usos Hoy".
    hoy_str = datetime.datetime(2025, 10, 29).strftime('%d/%m') # '29/10'

    for conv in raw_data:
        direccion = conv.get('direction', 'IN')
        created_timestamp = conv.get('created')
        
        if not created_timestamp:
            continue
            
        try:
            # Convertir timestamp (1759152081170) a fecha legible (29/10)
            fecha_dt = datetime.datetime.fromtimestamp(created_timestamp / 1000)
            fecha_str = fecha_dt.strftime('%d/%m') # Formato Día/Mes
        except Exception:
            continue

        # 1. Contar para los gráficos (IN vs OUT)
        uso_diario[fecha_str][direccion] += 1
        uso_acumulado[direccion] += 1
        todas_las_fechas.add(fecha_str)
        
        # 2. Contar para la tabla de agentes (SOLO 'OUT')
        if direccion == 'OUT':
            agent = conv.get('agent')
            if agent and agent.get('name'):
                agent_name = agent.get('name')
                
                # Parsear el nombre
                tienda, rol, nombre = parse_agent_name(agent_name)
                
                # Si es None, es un agente excluido (Camila, Franco)
                if tienda is None:
                    continue
                
                # Contar total de usos por agente
                agentes_data[tienda][rol][nombre] += 1
                
                # Contar usos de HOY por tienda (para el límite)
                if fecha_str == hoy_str:
                    uso_hoy_tienda[tienda] += 1

    # 3. Formatear datos para los gráficos
    labels_fechas = sorted(list(todas_las_fechas), key=lambda d: datetime.datetime.strptime(d, '%d/%m'))
    labels_plantillas = ['IN', 'OUT'] # Fijo

    datos_diario_procesados = defaultdict(lambda: defaultdict(int))
    for fecha in labels_fechas:
        for plantilla in labels_plantillas:
            datos_diario_procesados[fecha][plantilla] = uso_diario[fecha].get(plantilla, 0)
            
    direccion_data = {
        'diario': {
            'fechas': labels_fechas,
            'plantillas': labels_plantillas,
            'datos': datos_diario_procesados
        },
        'acumulado': {
            'plantillas': labels_plantillas,
            'conteo': [uso_acumulado.get(p, 0) for p in labels_plantillas]
        }
    }

    # 4. Formatear datos para la tabla de agentes
    tabla_agentes_final = []
    for tienda, roles in agentes_data.items():
        agentes_ordenados = []
        for rol, nombres in roles.items():
            for nombre, total_usos in nombres.items():
                agentes_ordenados.append({
                    "rol": rol,
                    "nombre": nombre,
                    "total_usos": total_usos
                })
        
        # Ordenar agentes por ROL (J, C, V...)
        agentes_ordenados.sort(key=lambda x: (ROLES_ORDEN.get(x['rol'], 99), x['nombre']))
        
        tabla_agentes_final.append({
            "tienda": tienda,
            "total_tienda_hoy": uso_hoy_tienda.get(tienda, 0),
            "limite_diario": 20,
            "agentes": agentes_ordenados
        })
    
    # Ordenar tiendas (R1, R11, R2, No Asignado...)
    tabla_agentes_final.sort(key=lambda x: (
        0 if x['tienda'].startswith('R') else 1,
        int(x['tienda'][1:]) if x['tienda'].startswith('R') and x['tienda'][1:].isdigit() else 99,
        x['tienda']
    ))
    
    datos_finales = {
        "direccion_data": direccion_data,
        "agente_data": tabla_agentes_final
    }
    
    print("\n--- JSON A ENVIAR (DEBUG) ---")
    print(f"direccion_data keys: {list(datos_finales['direccion_data'].keys())}")
    print(f"agente_data items: {len(datos_finales['agente_data'])}")
    print("-----------------------------\n")

    return jsonify(datos_finales)


# --- 6. ENDPOINTS DE LA API (Modificados con seguridad) ---

@app.route('/')
@requires_auth  # <-- ¡SEGURIDAD AÑADIDA!
def home():
    """
    Ruta raíz. Ahora sirve tu index.html de forma segura.
    """
    # Asume que index.html está en la misma carpeta raíz
    # (Lo cual es correcto en tu repositorio de GitHub)
    print(f"Usuario '{request.authorization.username}' autenticado. Sirviendo index.html...")
    return send_from_directory('.', 'index.html')

@app.route('/api/datos')
@requires_auth  # <-- ¡SEGURIDAD AÑADIDA!
def get_dashboard_data():
    """
    Endpoint principal que el frontend llamará.
    """
    print(f"\n[PETICIÓN RECIBIDA] /api/datos por usuario '{request.authorization.username}'")
    token = get_hibot_token()
    if not token:
        return jsonify({"error": "Fallo al obtener el token de HIBOT"}), 500
    
    raw_data = fetch_hibot_template_data(token)
    if raw_data is None:
        return jsonify({"error": "Fallo al obtener los datos de HIBOT"}), 500
        
    datos_procesados = process_data(raw_data)
    print("Enviando datos procesados al frontend.")
    return datos_procesados

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    print(f"Iniciando servidor Flask en el puerto {port}")
    pass # Gunicorn llamará a 'app'

