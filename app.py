import datetime
import os  # <-- AÑADIDO PARA LA SEGURIDAD
import re
from collections import defaultdict

from flask import Flask, jsonify
from flask_cors import CORS

# --- 1. CONFIGURACIÓN DE HIBOT ---
# ¡TUS SECRETOS AHORA SE LEEN DESDE RENDER DE FORMA SEGURA!
HIBOT_BASE_URL = "https://pdn.api.hibot.us/api_external"
HIBOT_APP_ID = os.environ.get("HIBOT_APP_ID")
HIBOT_APP_SECRET = os.environ.get("HIBOT_APP_SECRET")
# ------------------------------------

# Inicializa la aplicación Flask
app = Flask(__name__)
# Configura CORS para permitir que tu frontend llame a esta API
CORS(app, resources={r"/api/datos": {"origins": "*"}})

# --- 2. LÓGICA DE PARSEO DE AGENTES ---
# Define los roles y su orden
ROLES_ORDEN = {"J": 1, "C": 2, "V": 3, "VD": 4, "TR": 5, "Otro": 99}
# Compila la expresión regular para extraer los datos del agente
# Patrón: (R...)(V|C|J...) - (Nombre...)
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
        return []

    conversations_url = f"{HIBOT_BASE_URL}/conversations"
    headers = {"Authorization": f"Bearer {token}"}
    
    fecha_hasta = datetime.datetime.now()
    fecha_desde = fecha_hasta - datetime.timedelta(days=30)
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
            print("\n--- ESTRUCTURA DE UNA CONVERSACIÓN (DEBUG) ---")
            print(data[0])
            print("----------------------------------------------\n")
            return data
        else:
            print("Respuesta exitosa pero no se encontraron conversaciones.")
            return []
    except requests.exceptions.RequestException as e:
        print(f"Error al obtener conversaciones de HIBOT: {e}")
        return []

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
    hoy_str = datetime.datetime.now().strftime('%d/%m') # Formato '29/10'

    for conv in raw_data:
        direccion = conv.get('direction', 'IN')
        created_timestamp = conv.get('created')
        
        if not created_timestamp:
            continue
            
        try:
            # Convertir timestamp (1759152081170) a fecha legible (29/10)
            fecha_dt = datetime.datetime.fromtimestamp(created_timestamp / 1000)
            fecha_str = fecha_dt.strftime('%d/%m')
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


# --- 3. ENDPOINT DE LA API ---

@app.route('/')
def home():
    """
    Ruta raíz para verificar que el servidor está vivo.
    """
    return "El servidor del Dashboard HIBOT está funcionando."

@app.route('/api/datos')
def get_dashboard_data():
    """
    Endpoint principal que el frontend llamará.
    """
    print("\n[PETICIÓN RECIBIDA] /api/datos")
    token = get_hibot_token()
    raw_data = fetch_hibot_template_data(token)
    datos_procesados = process_data(raw_data)
    print("Enviando datos procesados al frontend.")
    return datos_procesados

if __name__ == '__main__':
    # Usamos el puerto que Render nos asigna a través de la variable PORT
    port = int(os.environ.get('PORT', 5001))
    print(f"Iniciando servidor Flask en el puerto {port}")
    # app.run(debug=True, port=port) <--- Esto es para desarrollo
    # Para producción en Render, Gunicorn llamará a la variable 'app'
    # Así que no necesitamos 'app.run()' aquí si usamos Gunicorn
    pass

