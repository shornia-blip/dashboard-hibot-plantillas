import datetime
import re
import requests # <-- ¡ESTA LÍNEA FALTABA!
from collections import defaultdict
from flask import Flask, jsonify, current_app
from flask_cors import CORS

# --- 1. CONFIGURACIÓN DE HIBOT ---
# ¡REEMPLAZA ESTO CON TUS CREDENCIALES REALES!
HIBOT_BASE_URL = "https://pdn.api.hibot.us/api_external"
HIBOT_APP_ID = "6749f162ea4755c8d8df65f8"
HIBOT_APP_SECRET = "260903b7-bdbb-44d7-acaf-bad9decea3a8"
# ------------------------------------

# --- 2. LÓGICA DE NEGOCIO ---
# Patrón para nombres de agente: R[numero] [Rol] - [Nombre]
# El guion y el espacio son opcionales para manejar casos como "R11 V ALBERTO"
AGENT_PATTERN = re.compile(r'^(R\d+)\s+(VD|TR|J|C|V)\s*-?\s+(.+)$', re.IGNORECASE)

# Lista de agentes a ignorar
AGENTES_IGNORADOS = {'Camila', 'FRANCO'}

# Límite diario de conversaciones OUT por tienda
LIMITE_DIARIO_TIENDA = 20

# -----------------------------

app = Flask(__name__)
CORS(app) # Habilitar CORS para todas las rutas

def get_hibot_token():
    """Se autentica contra la API de HIBOT y obtiene un token de acceso."""
    login_url = f"{HIBOT_BASE_URL}/login"
    current_app.logger.info(f"Intentando autenticarse en: {login_url}")
    
    try:
        payload = {"appId": HIBOT_APP_ID, "appSecret": HIBOT_APP_SECRET}
        response = requests.post(login_url, json=payload)
        response.raise_for_status()
        token = response.json().get('token')
        current_app.logger.info("Token de HIBOT obtenido exitosamente.")
        return token
        
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"Error al autenticarse en HIBOT: {e}")
        return None

def fetch_hibot_template_data(token):
    """Obtiene el historial de conversaciones usando el endpoint POST /conversations."""
    if not token:
        current_app.logger.warning("No hay token, no se pueden obtener datos.")
        return []

    conversations_url = f"{HIBOT_BASE_URL}/conversations"
    headers = {"Authorization": f"Bearer {token}"}
    
    fecha_hasta = datetime.datetime.now()
    fecha_desde = fecha_hasta - datetime.timedelta(days=30)
    
    timestamp_hasta = int(fecha_hasta.timestamp() * 1000)
    timestamp_desde = int(fecha_desde.timestamp() * 1000)

    payload = {
        "from": timestamp_desde,
        "to": timestamp_hasta,
        "channelType": "WHATSAPP" 
    }
    
    current_app.logger.info(f"Obteniendo conversaciones desde {fecha_desde} hasta {fecha_hasta}...")

    try:
        response = requests.post(conversations_url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        
        if data and isinstance(data, list):
            current_app.logger.info(f"Se obtuvieron {len(data)} conversaciones para procesar.")
            current_app.logger.debug(f"--- ESTRUCTURA DE UNA CONVERSACIÓN (DEBUG) ---\n{data[0]}\n----------------------------------------------")
        elif not data:
            current_app.logger.info("Respuesta exitosa pero no se encontraron conversaciones en este rango.")
            return []
            
        return data
        
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"Error al obtener conversaciones de HIBOT: {e}")
        return []

def parse_agent_name(agent_name):
    """Analiza el nombre de un agente y devuelve su tienda, rol y nombre."""
    if not agent_name:
        return 'No Asignado', 'N/A', 'Sin Agente'
        
    match = AGENT_PATTERN.match(agent_name)
    if match:
        tienda = match.group(1).upper()
        rol = match.group(2).upper()
        nombre = match.group(3).title()
        return tienda, rol, nombre
    else:
        current_app.logger.warning(f"Nombre de agente no coincide con el patrón: '{agent_name}'. Asignado a 'No Asignado'.")
        return 'No Asignado', 'N/A', agent_name

def process_data(raw_data):
    """Procesa la lista de conversaciones para calcular todos los datos del dashboard."""
    current_app.logger.info("Procesando datos (Lógica de Agente + Dirección)...")
    
    # --- Estructuras de datos para los gráficos ---
    uso_diario_direccion = defaultdict(lambda: defaultdict(int))
    uso_acumulado_direccion = defaultdict(int)
    todas_las_fechas = set()
    
    # --- Estructuras de datos para la tabla ---
    # Usamos diccionarios anidados para agrupar fácilmente
    # tiendas['R1']['V']['ALBERTO GAJDYSZ'] = 5
    tiendas_data = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    tiendas_hoy_count = defaultdict(int)
    
    fecha_de_hoy_str = datetime.datetime.now().strftime("%d/%m") # Formato "29/10"

    for conv in raw_data:
        try:
            timestamp_creacion_millis = conv.get('created')
            if not timestamp_creacion_millis:
                current_app.logger.warning(f"Conversación ID {conv.get('id')} no tiene 'created', saltando.")
                continue
                
            timestamp_creacion_sec = timestamp_creacion_millis / 1000
            fecha_dt = datetime.datetime.fromtimestamp(timestamp_creacion_sec)
            
            # --- ¡CAMBIO DE FORMATO DE FECHA! ---
            # De '2025-10-29' a '29/10'
            fecha_str = fecha_dt.strftime("%d/%m")
            # -------------------------------------

            todas_las_fechas.add(fecha_str)
            
            direction = conv.get('direction', 'IN') # Asumir IN si no hay dirección
            etiqueta_direccion = 'Iniciadas por Cliente (IN)' if direction == 'IN' else 'Iniciadas por Agente/Plantilla (OUT)'

            # 1. Llenar datos para gráficos de IN vs OUT
            uso_diario_direccion[fecha_str][etiqueta_direccion] += 1
            uso_acumulado_direccion[etiqueta_direccion] += 1

            # 2. Llenar datos para la tabla (solo conversaciones OUT)
            if direction == 'OUT':
                agent = conv.get('agent')
                if not agent or not agent.get('name'):
                    tienda, rol, nombre = 'No Asignado', 'N/A', 'Sin Agente'
                else:
                    agent_name = agent.get('name')
                    
                    # Ignorar agentes específicos
                    if agent_name in AGENTES_IGNORADOS:
                        continue
                        
                    tienda, rol, nombre = parse_agent_name(agent_name)

                # Incrementar conteo total de 30 días
                tiendas_data[tienda][rol][nombre] += 1
                
                # Incrementar conteo de HOY si la fecha coincide
                if fecha_str == fecha_de_hoy_str:
                    tiendas_hoy_count[tienda] += 1

        except Exception as e:
            current_app.logger.error(f"Error procesando conversación ID {conv.get('id')}: {e}. Datos: {conv}")

    # --- 3. Formatear datos de GRÁFICOS ---
    # Corrección para ordenar fechas "dd/mm"
    labels_fechas = sorted(list(todas_las_fechas), key=lambda d: (d.split('/')[1], d.split('/')[0]))
    
    # Asegurarse de que ambas etiquetas existan siempre
    labels_plantillas = ['Iniciadas por Cliente (IN)', 'Iniciadas por Agente/Plantilla (OUT)']
    uso_acumulado_direccion.setdefault('Iniciadas por Cliente (IN)', 0)
    uso_acumulado_direccion.setdefault('Iniciadas por Agente/Plantilla (OUT)', 0)

    direccion_data = {
        'diario': {
            'fechas': labels_fechas,
            'plantillas': labels_plantillas,
            'datos': uso_diario_direccion
        },
        'acumulado': {
            'plantillas': labels_plantillas,
            'conteo': [uso_acumulado_direccion[p] for p in labels_plantillas]
        }
    }

    # --- 4. Formatear datos de TABLA ---
    # Ordenar roles como pide el negocio: J, C, V, VD, TR, N/A
    rol_orden = {'J': 1, 'C': 2, 'V': 3, 'VD': 4, 'TR': 5, 'N/A': 6}
    
    agente_data = []
    
    # Ordenar tiendas (R1, R2, R10, No Asignado)
    tiendas_ordenadas = sorted(tiendas_data.keys(), key=lambda t: int(t[1:]) if t.startswith('R') else 999)
    
    for tienda in tiendas_ordenadas:
        agentes_list = []
        # Ordenar roles
        roles_ordenados = sorted(tiendas_data[tienda].keys(), key=lambda r: rol_orden.get(r, 99))
        
        for rol in roles_ordenados:
            # Ordenar agentes por nombre
            agentes_ordenados = sorted(tiendas_data[tienda][rol].keys())
            for nombre in agentes_ordenados:
                agentes_list.append({
                    'rol': rol,
                    'nombre': nombre,
                    'total_usos': tiendas_data[tienda][rol][nombre]
                })
        
        agente_data.append({
            'tienda': tienda,
            'agentes': agentes_list,
            'total_tienda_hoy': tiendas_hoy_count[tienda],
            'limite_diario': LIMITE_DIARIO_TIENDA
        })

    # --- 5. Combinar y Enviar ---
    final_data = {
        'direccion_data': direccion_data,
        'agente_data': agente_data
    }
    
    current_app.logger.debug(f"--- JSON A ENVIAR (DEBUG) ---\ndireccion_data keys: {final_data['direccion_data'].keys()}\nagente_data items: {len(final_data['agente_data'])}\n-----------------------------")
    current_app.logger.info("Datos procesados exitosamente.")
    return final_data


@app.route('/api/datos')
def get_dashboard_data():
    """Endpoint principal que el frontend llamará."""
    current_app.logger.info("\n[PETICIÓN RECIBIDA] /api/datos")
    token = get_hibot_token()
    raw_data = fetch_hibot_template_data(token)
    
    if not raw_data:
        # Enviar datos vacíos para que el frontend no falle
        current_app.logger.warning("No se obtuvieron datos crudos, enviando estructura vacía.")
        return jsonify({
            'direccion_data': {
                'diario': {'fechas': [], 'plantillas': [], 'datos': {}},
                'acumulado': {'plantillas': [], 'conteo': []}
            },
            'agente_data': []
        })
        
    datos_procesados = process_data(raw_data)
    
    current_app.logger.info("Enviando datos procesados al frontend.")
    return jsonify(datos_procesados)

if __name__ == '__main__':
    # Habilitar logging detallado
    import logging
    logging.basicConfig(level=logging.INFO)
    app.logger.setLevel(logging.INFO)
    app.logger.info(f"Iniciando servidor Flask en http://127.0.0.1:5001")
    app.run(debug=True, port=5001)

