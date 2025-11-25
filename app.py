from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv
from datetime import datetime, date

# Importar el sistema de autenticación

from auth import (
    login_manager, User, authenticate_user, create_user, 
    permission_required, role_required, get_all_users,
    toggle_user_status, update_user_role, update_user_password,
    update_last_login, update_own_password, get_user_profile  # 👈 Nuevos
)
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'cambiar-esta-clave-secreta-en-produccion')

# Configurar Flask-Login
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = '⚠️ Por favor inicia sesión para acceder'
login_manager.login_message_category = 'error'

DATABASE_URL = os.getenv('DATABASE_URL')

def get_db_connection():
    """Conexión a la base de datos"""
    try:
        db_url_clean = DATABASE_URL.replace('&channel_binding=require', '')
        conn = psycopg2.connect(db_url_clean, cursor_factory=psycopg2.extras.RealDictCursor)
        return conn
    except Exception as e:
        print(f"Error de conexión: {e}")
        return None

def registrar_auditoria(conn, equipo_id, usuario_id, usuario_nombre, campo, valor_anterior, valor_nuevo, accion='UPDATE'):
    """Registra un cambio en la tabla de auditoría"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO equipos_auditoria 
            (equipo_id, usuario_id, usuario_nombre, campo_modificado, valor_anterior, valor_nuevo, accion)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (equipo_id, usuario_id, usuario_nombre, campo, str(valor_anterior) if valor_anterior else '', str(valor_nuevo) if valor_nuevo else '', accion))
        cursor.close()
    except Exception as e:
        print(f"Error al registrar auditoría: {e}")

# ============================================
# RUTAS DE AUTENTICACIÓN
# ============================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Página de login"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = authenticate_user(username, password)
        
        if user:
            login_user(user)
            update_last_login(user.id)
            flash(f'✅ Bienvenido {user.username}!', 'success')
            
            # Redirigir a la página que intentaban acceder o al dashboard
            next_page = request.args.get('next')
            return redirect(next_page if next_page else url_for('index'))
        else:
            flash('❌ Usuario o contraseña incorrectos', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Cerrar sesión"""
    logout_user()
    flash('👋 Has cerrado sesión exitosamente', 'success')
    return redirect(url_for('login'))

# ============================================
# RUTAS PRINCIPALES (REQUIEREN AUTENTICACIÓN)
# ============================================

@app.route('/')
@login_required
def index():
    """Página principal - Dashboard"""
    conn = get_db_connection()
    if not conn:
        return "Error de conexión a la base de datos", 500
    
    cursor = conn.cursor()
    
    # Métricas
    cursor.execute("SELECT COUNT(*) as total FROM solicitudes")
    total_solicitudes = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as total FROM equipos")
    total_equipos = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as total FROM solicitudes WHERE estado = 'Pendiente'")
    pendientes = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as total FROM equipos WHERE en_garantia = true")
    en_garantia = cursor.fetchone()['total']
    
    # Estados de solicitudes
    cursor.execute("""
        SELECT estado, COUNT(*) as cantidad 
        FROM solicitudes 
        GROUP BY estado
    """)
    estados = cursor.fetchall()
    
    # Categorías de archivos
    cursor.execute("""
        SELECT categoria, COUNT(*) as cantidad 
        FROM archivos_adjuntos 
        GROUP BY categoria
    """)
    categorias = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    return render_template('dashboard.html', 
                         total_solicitudes=total_solicitudes,
                         total_equipos=total_equipos,
                         pendientes=pendientes,
                         en_garantia=en_garantia,
                         estados=estados,
                         categorias=categorias)

@app.route('/solicitudes')
@login_required
def solicitudes():
    """Página de solicitudes"""
    conn = get_db_connection()
    if not conn:
        return "Error de conexión a la base de datos", 500
    
    cursor = conn.cursor()
    cursor.execute("""
    SELECT 
        s.id,
        s.fecha_solicitud,
        s.email_solicitante,
        s.quien_completa,
        s.area_solicitante,
        s.solicitante,
        s.nivel_urgencia,
        s.logistica_cargo,
        s.equipo_corresponde_a,
        s.motivo_solicitud,
        s.estado,
        s.ost 
    FROM solicitudes s
    ORDER BY s.fecha_solicitud DESC
""")
    solicitudes = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return render_template('solicitudes.html', solicitudes=solicitudes)


@app.route('/equipos')
@login_required
def equipos():
    """Página de equipos (solo muestra equipos NO eliminados)"""
    conn = get_db_connection()
    if not conn:
        return "Error de conexión a la base de datos", 500
    
    cursor = conn.cursor()
    
    # Obtener equipos NO eliminados
    cursor.execute("""
        SELECT id, cliente, ost, estado, fecha_ingreso, remito,
               tipo_equipo, marca, modelo, numero_serie, accesorios,
               observacion_ingreso, prioridad, fecha_envio, proveedor,
               detalles_reparacion, horas_trabajo, reingreso, 
               informe AS informe_tecnico,
               costo AS costo_reparacion, 
               precio AS precio_cliente, 
               ov AS numero_ov, 
               estado_ov, fecha_entrega, remito_entrega
        FROM equipos
        WHERE eliminado = FALSE  -- 👈 CLAVE: Solo equipos NO eliminados
        ORDER BY fecha_ingreso DESC
    """)
    equipos = cursor.fetchall()
    
    # Obtener archivos para las fotos (hacer JOIN con equipos NO eliminados)
    cursor.execute("""
        SELECT e.numero_serie, a.categoria, a.url_cloudinary
        FROM archivos_adjuntos a
        INNER JOIN equipos e ON a.equipo_id = e.id
        WHERE e.numero_serie IS NOT NULL 
        AND e.eliminado = FALSE  -- 👈 Solo archivos de equipos activos
    """)
    archivos = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    return render_template('equipos.html', equipos=equipos, archivos=archivos)

@app.route('/archivos')
@login_required
def archivos():
    """Página de archivos adjuntos"""
    conn = get_db_connection()
    if not conn:
        return "Error de conexión a la base de datos", 500
    
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.id, a.solicitud_id, a.equipo_id, a.nombre_archivo,
               a.url_cloudinary, a.tipo_archivo, a.categoria,
               a.tamano_bytes, a.fecha_subida,
               e.ost, e.numero_serie
        FROM archivos_adjuntos a
        LEFT JOIN equipos e ON a.equipo_id = e.id
        ORDER BY a.fecha_subida DESC
    """)
    archivos = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return render_template('archivos.html', archivos=archivos)

# ============================================
# GESTIÓN DE USUARIOS (SOLO ADMIN)
# ============================================

@app.route('/usuarios')
@permission_required('manage_users')
def usuarios():
    """Página de gestión de usuarios (solo admin)"""
    users = get_all_users()
    return render_template('usuarios.html', users=users)

@app.route('/perfil')
@login_required
def perfil():
    """Página de perfil del usuario"""
    from auth import get_user_profile
    user_data = get_user_profile(current_user.id)
    return render_template('perfil.html', user_data=user_data)

@app.route('/api/perfil/cambiar-password', methods=['POST'])
@login_required
def cambiar_mi_password():
    """API para que un usuario cambie su propia contraseña"""
    from auth import update_own_password
    try:
        data = request.json
        update_own_password(
            current_user.id,
            data['current_password'],
            data['new_password']
        )
        return jsonify({'success': True, 'message': 'Contraseña actualizada correctamente'})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================
# AUDITORÍA (SOLO ADMIN)
# ============================================


@app.route('/auditoria')
@permission_required('view_audit')
def auditoria():
    """Página de auditoría de cambios (solo admin)"""
    conn = get_db_connection()
    if not conn:
        return "Error de conexión a la base de datos", 500
    
    cursor = conn.cursor()
    
    # Obtener el equipo_id si se filtra
    equipo_id = request.args.get('equipo_id', type=int)
    
    if equipo_id:
        cursor.execute("""
            SELECT a.*, e.ost, e.cliente, e.tipo_equipo, e.eliminado
            FROM equipos_auditoria a
            LEFT JOIN equipos e ON a.equipo_id = e.id
            WHERE a.equipo_id = %s
            ORDER BY a.fecha_cambio DESC
        """, (equipo_id,))
    else:
        # 👇 CLAVE: No filtramos por eliminado, mostramos TODO
        cursor.execute("""
            SELECT a.*, e.ost, e.cliente, e.tipo_equipo, e.eliminado
            FROM equipos_auditoria a
            LEFT JOIN equipos e ON a.equipo_id = e.id
            ORDER BY a.fecha_cambio DESC
            LIMIT 500
        """)
    
    cambios = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return render_template('auditoria.html', cambios=cambios, equipo_id=equipo_id)
# ============================================
# API ENDPOINTS
# ============================================

@app.route('/api/solicitud/<int:id>', methods=['PUT'])
@permission_required('edit')
def update_solicitud(id):
    """API para actualizar solicitud"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión'}), 500
    
    data = request.json
    cursor = conn.cursor()
    
    try:
        campos = []
        valores = []
        
        # Lista de campos actualizables
        campos_permitidos = [
            'email_solicitante', 'quien_completa', 'area_solicitante',
            'solicitante', 'nivel_urgencia', 'logistica_cargo',
            'equipo_corresponde_a', 'motivo_solicitud', 'estado'
        ]
        
        for campo in campos_permitidos:
            if campo in data:
                campos.append(f'{campo} = %s')
                valores.append(data[campo])
        
        if not campos:
            return jsonify({'error': 'No hay campos para actualizar'}), 400
        
        valores.append(id)
        query = f"UPDATE solicitudes SET {', '.join(campos)} WHERE id = %s"
        
        cursor.execute(query, valores)
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        return jsonify({'error': str(e)}), 500

@app.route('/api/proximo-ost', methods=['GET'])
@login_required
def obtener_proximo_ost():
    """API para obtener el próximo número OST disponible"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión'}), 500
    
    cursor = conn.cursor()
    
    try:
        # Obtener el último OST
        cursor.execute("SELECT MAX(ost) as max_ost FROM equipos")
        result = cursor.fetchone()
        max_ost = result['max_ost'] if result and result['max_ost'] else 0
        proximo_ost = max_ost + 1
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'proximo_ost': proximo_ost
        })
    except Exception as e:
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/equipos', methods=['POST'])
@app.route('/api/equipo/crear', methods=['POST'])
@permission_required('edit')
def crear_equipo():
    """API para crear nuevo equipo"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión'}), 500
    
    data = request.json
    cursor = conn.cursor()
    
    try:
        fecha_ingreso = data.get('fecha_ingreso')
        if isinstance(fecha_ingreso, str):
            try:
                fecha_ingreso = datetime.strptime(fecha_ingreso, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'success': False, 'error': 'Formato de fecha inválido'}), 400
        
        def empty_to_none(value):
            return None if value == '' or value is None else value
        
        cursor.execute("""
            INSERT INTO equipos (
                cliente, tipo_equipo, marca, modelo, numero_serie,
                fecha_ingreso, remito, accesorios, prioridad, 
                observacion_ingreso, estado
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, ost
        """, (
            data.get('cliente'),
            data.get('tipo_equipo'),
            empty_to_none(data.get('marca')),
            empty_to_none(data.get('modelo')),
            empty_to_none(data.get('numero_serie')),
            fecha_ingreso,
            empty_to_none(data.get('remito')),
            empty_to_none(data.get('accesorios')),
            data.get('prioridad', 'Media'),
            empty_to_none(data.get('observacion_ingreso')),
            'Pendiente'
        ))
        result = cursor.fetchone()
        
        # Registrar en auditoría
        registrar_auditoria(
            conn, 
            result['id'], 
            current_user.id, 
            current_user.username,
            'CREACIÓN',
            '',
            f"OST: {result['ost']}, Cliente: {data.get('cliente')}",
            'INSERT'
        )
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'id': result['id'],
            'ost': result['ost'],
            'message': 'Equipo creado exitosamente'
        }), 201
    
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/equipo/<int:id>', methods=['PUT'])
@permission_required('edit')
def update_equipo(id):
    """API para actualizar equipo (requiere permiso de edición)"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión'}), 500
    
    data = request.json
    cursor = conn.cursor()
    
    try:
        # Primero obtener los valores actuales
        cursor.execute("SELECT * FROM equipos WHERE id = %s", (id,))
        equipo_actual = cursor.fetchone()
        
        if not equipo_actual:
            return jsonify({'error': 'Equipo no encontrado'}), 404
        
        # Construir la consulta dinámicamente solo con los campos que vienen
        campos = []
        valores = []
        
        # Mapeo de campos JSON a columnas DB
        campo_map = {
            'cliente': 'cliente',
            'tipo_equipo': 'tipo_equipo',
            'marca': 'marca',
            'modelo': 'modelo',
            'numero_serie': 'numero_serie',
            'accesorios': 'accesorios',
            'prioridad': 'prioridad',
            'remito': 'remito',
            'observacion_ingreso': 'observacion_ingreso',
            'detalle_reparacion': 'detalles_reparacion',
            'horas_trabajo': 'horas_trabajo',
            'reingreso': 'reingreso',
            'informe_tecnico': 'informe',
            'costo_reparacion': 'costo',
            'precio_cliente': 'precio',
            'numero_ov': 'ov',
            'estado_ov': 'estado_ov',
            'fecha_ingreso': 'fecha_ingreso',
            'fecha_envio_proveedor': 'fecha_envio',
            'fecha_entrega': 'fecha_entrega',
            'remito_entrega': 'remito_entrega',
            'estado': 'estado',
            'proveedor': 'proveedor'
        }
        
        for campo_json, campo_db in campo_map.items():
            if campo_json in data:
                valor_anterior = equipo_actual.get(campo_db)
                valor_nuevo = data.get(campo_json)
                
                # Solo actualizar si el valor cambió
                if str(valor_anterior) != str(valor_nuevo):
                    campos.append(f'{campo_db} = %s')
                    valores.append(valor_nuevo)
                    
                    # Registrar en auditoría
                    registrar_auditoria(
                        conn,
                        id,
                        current_user.id,
                        current_user.username,
                        campo_db,
                        valor_anterior,
                        valor_nuevo
                    )
        
        if not campos:
            return jsonify({'success': True, 'message': 'No hay cambios para guardar'})
        
        # Agregar el ID al final
        valores.append(id)
        
        query = f"UPDATE equipos SET {', '.join(campos)} WHERE id = %s"
        
        cursor.execute(query, valores)
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error al actualizar: {e}")
        conn.rollback()
        cursor.close()
        conn.close()
        return jsonify({'error': str(e)}), 500

# REEMPLAZA los endpoints de DELETE y RESTAURAR en app.py con estos:

@app.route('/api/equipo/<int:id>', methods=['DELETE'])
@permission_required('delete')
def delete_equipo(id):
    """API para eliminar equipo (Soft Delete - requiere permiso de eliminación)"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión'}), 500
    
    cursor = conn.cursor()
    
    try:
        # Obtener datos del equipo antes de "eliminar"
        cursor.execute("SELECT * FROM equipos WHERE id = %s AND eliminado = FALSE", (id,))
        equipo = cursor.fetchone()
        
        if not equipo:
            return jsonify({'success': False, 'error': 'Equipo no encontrado o ya está eliminado'}), 404
        
        # Marcar como eliminado (Soft Delete)
        cursor.execute("""
            UPDATE equipos 
            SET eliminado = TRUE, fecha_eliminacion = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (id,))
        
        # Registrar la eliminación en auditoría
        registrar_auditoria(
            conn, 
            id, 
            current_user.id, 
            current_user.username,
            'ELIMINACIÓN',
            f"OST: {equipo['ost']}, Cliente: {equipo['cliente']}, Tipo: {equipo['tipo_equipo']}",
            'EQUIPO ELIMINADO (SOFT DELETE)',
            'DELETE'
        )
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Equipo eliminado correctamente'})
    except Exception as e:
        print(f"Error al eliminar: {e}")
        conn.rollback()
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/equipo/<int:id>/restaurar', methods=['POST'])
@permission_required('delete')
def restaurar_equipo(id):
    """API para restaurar un equipo eliminado (requiere permiso de eliminación)"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión'}), 500
    
    cursor = conn.cursor()
    
    try:
        # Buscar el equipo eliminado
        cursor.execute("""
            SELECT * FROM equipos 
            WHERE id = %s AND eliminado = TRUE
        """, (id,))
        
        equipo = cursor.fetchone()
        
        if not equipo:
            return jsonify({
                'success': False, 
                'error': 'No se encontró el equipo eliminado'
            }), 404
        
        # Restaurar el equipo (marcar eliminado = FALSE)
        cursor.execute("""
            UPDATE equipos 
            SET eliminado = FALSE, fecha_eliminacion = NULL
            WHERE id = %s
        """, (id,))
        
        # Registrar la restauración en auditoría
        registrar_auditoria(
            conn,
            id,
            current_user.id,
            current_user.username,
            'RESTAURACIÓN',
            f'Equipo eliminado - OST: {equipo["ost"]}, Cliente: {equipo["cliente"]}',
            f'Equipo restaurado con OST: {equipo["ost"]}',
            'INSERT'
        )
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'id': equipo['id'],
            'ost': equipo['ost'],
            'message': 'Equipo restaurado exitosamente'
        })
    
    except Exception as e:
        print(f"Error al restaurar: {e}")
        conn.rollback()
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500
    
# ============================================
# API ENDPOINTS PARA GESTIÓN DE USUARIOS (SOLO ADMIN)
# ============================================

@app.route('/api/users/create', methods=['POST'])
@permission_required('manage_users')
def api_create_user():
    """API para crear usuario (solo admin)"""
    try:
        data = request.json
        user_id = create_user(
            username=data['username'],
            email=data['email'],
            password=data['password'],
            role=data['role']
        )
        return jsonify({'success': True, 'user_id': user_id})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/users/update-role', methods=['POST'])
@permission_required('manage_users')
def api_update_role():
    """API para actualizar rol de usuario (solo admin)"""
    try:
        data = request.json
        update_user_role(data['user_id'], data['role'])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/users/update-password', methods=['POST'])
@permission_required('manage_users')
def api_update_password():
    """API para actualizar contraseña de usuario (solo admin)"""
    try:
        data = request.json
        update_user_password(data['user_id'], data['new_password'])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/users/toggle-status', methods=['POST'])
@permission_required('manage_users')
def api_toggle_status():
    """API para activar/desactivar usuario (solo admin)"""
    try:
        data = request.json
        toggle_user_status(data['user_id'])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================
# CONTEXT PROCESSOR PARA TEMPLATES
# ============================================

@app.context_processor
def inject_user():
    """Inyecta información del usuario actual en todos los templates"""
    return dict(current_user=current_user)

if __name__ == '__main__':
    app.run(debug=True)