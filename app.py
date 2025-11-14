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

# --- ¡CAMBIO #1! ---
# Se agregó "SUP" (Supervisor) y se reordenaron los números
ROLES_ORDEN = {"J": 1, "SUP": 2, "C": 3, "V": 4, "VD": 5, "TR": 6, "Otro": 99, "Sistema": 100}

AGENT_PATTERN = re.compile(
    r'^(R\d+)\s+(VD|TR|J|C|V)\s*-?\s+(.+)$',
    re.IGNORECASE
)

# --- ¡CAMBIO #2! ---
# 'parse_agent_name' ahora clasifica correctamente a los agentes especiales
def parse_agent_name(name):
    if not name:
        return "No Asignado", "Otro", "Sin Nombre"
    
    name_upper = name.upper().strip() # Usar una versión limpia

    # --- NUEVA LÓGICA DE CLASIFICACIÓN ---
    if name_upper == "CAMILA":
        return "Canal Digital", "J", name # J = Jefe
    
    if name_upper == "FRANCO":
        return "Canal Digital", "SUP", name # SUP = Supervisor
    
    if name_upper == "ANA PAULA":
        return "Jefe de Venta", "J", name # J = Jefe

    if name_upper == "MAITE":
        return "Jefe de Venta", "J", name # J = Jefe
    
    # --- LÓGICA ANTERIOR (para el resto) ---
    # Ya no se excluye a Camila/Franco aquí
    match = AGENT_PATTERN.match(name)
    if match:
        tienda = match.group(1).upper()
        rol = match.group(2).upper()
        nombre = match.group(3).strip()
        return tienda, rol, nombre
    else:
        # Esto ahora capturará a "Santiago" y otros sin patrón
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
        print("No hay token, no se pueden obtener datos.")
        return None
    conversations_url = f"{HIBOT_BASE_URL}/conversations"
    headers = {"Authorization": f"Bearer {token}"}
    
    # --- Rango de fechas dinámico para el MES EN CURSO ---
    ahora = datetime.datetime.now()
    fecha_desde = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    fecha_hasta = ahora
    
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
            return data
        else:
            print("Respuesta exitosa pero no se encontraron conversaciones.")
            return []
    except requests.exceptions.RequestException as e:
        print(f"Error al obtener conversaciones de HIBOT: {e}")
        return None

# --- ¡FUNCIÓN 'process_data' MODIFICADA! ---
def process_data(raw_data):
    print("Procesando datos (Lógica de Agente + Dirección)...")
    
    uso_acumulado = defaultdict(int)
    agentes_data = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    uso_hoy_tienda = defaultdict(int)
    datos_diarios_por_tienda = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    tiendas_set = set()
    todas_las_fechas = set()
    
    hoy_str = datetime.datetime.now().strftime('%d/%m')

    for conv in raw_data:
        direccion = conv.get('direction', 'IN')
        created_timestamp = conv.get('created')
        
        if not created_timestamp:
            continue
            
        try:
            fecha_dt = datetime.datetime.fromtimestamp(created_timestamp / 1000)
            fecha_str = fecha_dt.strftime('%d/%m') # Formato Día/Mes
        except Exception:
            continue
            
        todas_las_fechas.add(fecha_str)

        # 1. Contar para tarjetas (Acumulado Total)
        uso_acumulado[direccion] += 1
        
        # 2. Determinar la tienda para ESTA conversación
        agent = conv.get('agent')
        tienda_actual = "No Asignado" 
        rol_actual = "Sistema"
        nombre_actual = "Conversación Automática"

        if agent and agent.get('name'):
            agent_name = agent.get('name')
            
            # --- ¡CAMBIO #3! ---
            # Lógica simplificada. 'parse_agent_name' ahora hace todo el trabajo.
            # El 'if tienda is None:' ya no es necesario.
            tienda, rol, nombre = parse_agent_name(agent_name)
            
            tienda_actual = tienda
            rol_actual = rol
            nombre_actual = nombre
        
        tiendas_set.add(tienda_actual)

        # 3. Contar para Gráficos (Diario por Tienda)
        datos_diarios_por_tienda[tienda_actual][fecha_str][direccion] += 1
        # También sumar al total (Esto hace que el gráfico "Total" siempre sea la suma de todo)
        datos_diarios_por_tienda["Total"][fecha_str][direccion] += 1

        # 4. Contar para la tabla de agentes (SOLO 'OUT')
        if direccion == 'OUT':
            
            # --- ¡CAMBIO #4! ---
            # Se eliminó el filtro 'if tienda_actual != "Supervisión":'
            # Ahora la tabla contará a TODOS, incluyendo "Canal Digital"
            
            # Contar total de usos por agente
            agentes_data[tienda_actual][rol_actual][nombre_actual] += 1
            
            # Contar usos de HOY por tienda
            if fecha_str == hoy_str:
                uso_hoy_tienda[tienda_actual] += 1

    # --- POST-PROCESAMIENTO ---

    # Quitar "Supervisión" si ya no existe, o añadir "Canal Digital", etc.
    # 'tiendas_disponibles' ahora se basa en 'tiendas_set' que incluye todo
    labels_fechas = sorted(list(todas_las_fechas), key=lambda d: datetime.datetime.strptime(d, '%d/%m'))
    tiendas_disponibles = sorted(list(tiendas_set))

    # 1. Formatear datos para Tarjetas
    resumen_acumulado = {
        'plantillas': ['IN', 'OUT'],
        'conteo': [uso_acumulado.get('IN', 0), uso_acumulado.get('OUT', 0)]
    }

    # 2. Formatear datos para Gráficos
    datos_graficos_final = {}
    
    # Asegurarnos de que "Total" esté, y las nuevas tiendas
    tiendas_a_procesar = ["Total"] + tiendas_disponibles
    # Evitar duplicados si "Total" estuviera en el set
    tiendas_a_procesar = sorted(list(set(tiendas_a_procesar)), key=lambda x: (x != 'Total', x)) 
    
    for tienda in tiendas_a_procesar:
        datos_IN = []
        datos_OUT = []
        for fecha in labels_fechas:
            datos_IN.append(datos_diarios_por_tienda[tienda][fecha].get('IN', 0))
            datos_OUT.append(datos_diarios_por_tienda[tienda][fecha].get('OUT', 0))
        
        datos_graficos_final[tienda] = {
            "fechas": labels_fechas,
            "IN": datos_IN,
            "OUT": datos_OUT
        }

    # 3. Formatear datos para la tabla de agentes
    tabla_agentes_final = []
    # Filtrar 'Supervisión' de la tabla si quedó vacío, pero incluir las nuevas tiendas
    tiendas_tabla = [t for t in tiendas_set if t != "Supervisión"] 
    
    for tienda in sorted(tiendas_tabla):
        roles = agentes_data.get(tienda)
        if not roles: continue # Si la tienda no tuvo usos OUT, no la agregar

        agentes_ordenados = []
        for rol, nombres in roles.items():
            for nombre, total_usos in nombres.items():
                agentes_ordenados.append({
                    "rol": rol,
                    "nombre": nombre,
                    "total_usos": total_usos
                })
        
        # Ordenar por el nuevo diccionario ROLES_ORDEN
        agentes_ordenados.sort(key=lambda x: (ROLES_ORDEN.get(x['rol'], 99), x['nombre']))
        
        tabla_agentes_final.append({
            "tienda": tienda,
            "total_tienda_hoy": uso_hoy_tienda.get(tienda, 0),
            "limite_diario": 20,
            "agentes": agentes_ordenados
        })
    
    # Ordenar la tabla final (Lógica de tiendas)
    tabla_agentes_final.sort(key=lambda x: (
        1 if x['tienda'] == "No Asignado" else 0,
        x['tienda'] # Ordenar alfabéticamente el resto (Canal Digital, Jefe de Venta, R1, R2...)
    ))
    
    # 4. Compilar JSON final
    datos_finales = {
        "resumen_acumulado": resumen_acumulado,
        "tabla_agentes": tabla_agentes_final,
        # Enviar las tiendas filtradas para los botones (sin "Supervisión")
        "tiendas_disponibles": [t for t in tiendas_disponibles if t != "Supervisión"],
        "datos_diarios_por_tienda": datos_graficos_final
    }
    
    print("\n--- JSON A ENVIAR (DEBUG) ---")
    print(f"Tiendas disponibles para filtros: {datos_finales['tiendas_disponibles']}")
    print("-----------------------------\n")

    return jsonify(datos_finales)


# --- 6. ENDPOINTS DE LA API ---

@app.route('/')
@requires_auth
def home():
    print(f"Usuario '{request.authorization.username}' autenticado. Sirviendo index.html...")
    # Asumimos que el HTML se llama 'index.html' y está en la raíz
    return send_from_directory('.', 'index.html') 

@app.route('/api/datos')
@requires_auth
def get_dashboard_data():
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
    # Gunicorn llamará a 'app', esto es solo para debug local
    app.run(debug=True, port=port)


