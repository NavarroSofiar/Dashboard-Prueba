"""
Sistema de autenticación para el dashboard
Incluye gestión de usuarios y roles
"""

from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from flask import redirect, url_for, flash
import psycopg2
from psycopg2.extras import RealDictCursor
import os

# Configuración de Flask-Login
login_manager = LoginManager()

# Roles disponibles con sus permisos
ROLES = {
    'viewer': {
        'name': 'Visualizador',
        'permissions': ['view']
    },
    'editor_v2': {
        'name': 'Editor Simple',
        'permissions': ['view', 'edit']
    },
    'editor': {
        'name': 'Editor Full',
        'permissions': ['view', 'edit', 'delete']
    },
    'admin': {
        'name': 'Administrador',
        'permissions': ['view', 'edit', 'delete', 'manage_users', 'view_audit']
    }
}

class User(UserMixin):
    """Clase de usuario para Flask-Login"""
    def __init__(self, id, username, email, role):
        self.id = id
        self.username = username
        self.email = email
        self.role = role
    
    def has_permission(self, permission):
        """Verifica si el usuario tiene un permiso específico"""
        return permission in ROLES.get(self.role, {}).get('permissions', [])
    
    def get_role_name(self):
        """Obtiene el nombre legible del rol"""
        return ROLES.get(self.role, {}).get('name', 'Desconocido')


def get_db_connection():
    """Obtiene conexión a la base de datos"""
    # Opción 1: Usar os.getenv (recomendado)
    DATABASE_URL = os.getenv('DATABASE_URL')
    db_url_clean = DATABASE_URL.replace('&channel_binding=require', '')
    conn = psycopg2.connect(db_url_clean, cursor_factory=RealDictCursor)
    return conn


@login_manager.user_loader
def load_user(user_id):
    """Carga un usuario desde la base de datos"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, username, email, role FROM usuarios WHERE id = %s", (user_id,))
    user_data = cur.fetchone()
    cur.close()
    conn.close()
    
    if user_data:
        return User(user_data['id'], user_data['username'], user_data['email'], user_data['role'])
    return None


def authenticate_user(username_or_email, password):
    """Autentica un usuario con username O email y password"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Buscar por username O email
    cur.execute("""
        SELECT id, username, email, role, password_hash 
        FROM usuarios 
        WHERE (username = %s OR email = %s) AND activo = true
    """, (username_or_email, username_or_email))
    
    user_data = cur.fetchone()
    cur.close()
    conn.close()
    
    if user_data and check_password_hash(user_data['password_hash'], password):
        return User(user_data['id'], user_data['username'], user_data['email'], user_data['role'])
    return None

def create_user(username, email, password, role='viewer'):
    """Crea un nuevo usuario en la base de datos"""
    if role not in ROLES:
        raise ValueError(f"Rol inválido. Debe ser uno de: {', '.join(ROLES.keys())}")
    
    password_hash = generate_password_hash(password)
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO usuarios (username, email, password_hash, role, activo)
            VALUES (%s, %s, %s, %s, true)
            RETURNING id
        """, (username, email, password_hash, role))
        user_id = cur.fetchone()['id']
        conn.commit()
        cur.close()
        conn.close()
        return user_id
    except psycopg2.IntegrityError:
        conn.rollback()
        cur.close()
        conn.close()
        raise ValueError("El username o email ya existe")


def update_user_password(user_id, new_password):
    """Actualiza la contraseña de un usuario"""
    password_hash = generate_password_hash(new_password)
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE usuarios SET password_hash = %s WHERE id = %s", (password_hash, user_id))
    conn.commit()
    cur.close()
    conn.close()


def permission_required(permission):
    """Decorador para requerir un permiso específico"""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not current_user.has_permission(permission):
                flash(f'⛔ No tienes permiso para realizar esta acción. Se requiere: {permission}', 'error')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def role_required(role):
    """Decorador para requerir un rol específico"""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if current_user.role != role and current_user.role != 'admin':
                flash(f'⛔ Se requiere rol de {ROLES[role]["name"]}', 'error')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# Funciones de utilidad para gestión de usuarios (solo admin)
def get_all_users():
    """Obtiene todos los usuarios (solo para admin)"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, username, email, role, activo, created_at, last_login
        FROM usuarios
        ORDER BY created_at DESC
    """)
    users = cur.fetchall()
    cur.close()
    conn.close()
    return users


def toggle_user_status(user_id):
    """Activa/desactiva un usuario"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE usuarios SET activo = NOT activo WHERE id = %s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()


def update_user_role(user_id, new_role):
    """Actualiza el rol de un usuario"""
    if new_role not in ROLES:
        raise ValueError(f"Rol inválido. Debe ser uno de: {', '.join(ROLES.keys())}")
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE usuarios SET role = %s WHERE id = %s", (new_role, user_id))
    conn.commit()
    cur.close()
    conn.close()


def update_last_login(user_id):
    """Actualiza el último login del usuario"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE usuarios SET last_login = CURRENT_TIMESTAMP WHERE id = %s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

def update_own_password(user_id, current_password, new_password):
    """Permite a un usuario actualizar su propia contraseña (requiere contraseña actual)"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Verificar contraseña actual
    cur.execute("SELECT password_hash FROM usuarios WHERE id = %s", (user_id,))
    user_data = cur.fetchone()
    
    if not user_data:
        cur.close()
        conn.close()
        raise ValueError("Usuario no encontrado")
    
    if not check_password_hash(user_data['password_hash'], current_password):
        cur.close()
        conn.close()
        raise ValueError("Contraseña actual incorrecta")
    
    # Actualizar contraseña
    password_hash = generate_password_hash(new_password)
    cur.execute("UPDATE usuarios SET password_hash = %s WHERE id = %s", (password_hash, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return True


def get_user_profile(user_id):
    """Obtiene el perfil completo de un usuario"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, username, email, role, activo, created_at, last_login
        FROM usuarios
        WHERE id = %s
    """, (user_id,))
    user_data = cur.fetchone()
    cur.close()
    conn.close()
    return user_data