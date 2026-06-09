import json
import os
from datetime import datetime
from functools import wraps
from pathlib import Path
import concurrent.futures
from ai_service import ai_service
import json

import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'secret-key-12345')

# Настройки загрузки файлов
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'avi', 'mov'}
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# Пути к файлам
DATA_DIR = BASE_DIR / 'data_pack'
ROLES_FILE = DATA_DIR / 'roles.json'
SPAMER_FILE = DATA_DIR / 'spamer.json'
USER_ROLES_FILE = DATA_DIR / 'user_roles.json'
LOG_FILE = DATA_DIR / 'log_inf.json'
GROUPS_FILE = DATA_DIR / 'groups.json'
TRAINING_DATA_FILE = DATA_DIR / 'training_data.json'

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

# VK настройки
VK_API_VERSION = '5.131'

# OK.ru настройки
OK_API_VERSION = '10.0'


# ========== Вспомогательные функции ==========

def load_json(file_path):
    """Загружает данные из JSON файла"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return [] if 'spamer' in str(file_path) or 'log' in str(file_path) or 'groups' in str(file_path) else {}


def save_json(file_path, data):
    """Сохраняет данные в JSON файл"""
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def log_action(username, action):
    """Логирует действие пользователя"""
    logs = load_json(LOG_FILE)
    logs.append({
        'username': username,
        'action': action,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })
    save_json(LOG_FILE, logs)


# ========== Декораторы для проверки ролей ==========

def login_required(f):
    """Проверяет, авторизован ли пользователь"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            flash('Пожалуйста, войдите в систему', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


def role_required(allowed_roles):
    """Проверяет, есть ли у пользователя нужная роль"""

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if session.get('role') not in allowed_roles and session.get('role') != 'ADMIN':
                flash('У вас нет доступа к этой странице', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)

        return decorated_function

    return decorator


# ========== VK API функции ==========

def upload_photo_to_vk(photo_path, access_token, group_id):
    """Загружает фото в VK"""
    try:
        get_server_url = 'https://api.vk.com/method/photos.getWallUploadServer'
        params = {
            'access_token': access_token,
            'v': VK_API_VERSION,
            'group_id': group_id
        }
        response = requests.get(get_server_url, params=params)
        data = response.json()

        if 'error' in data:
            return None

        upload_url = data['response']['upload_url']

        with open(photo_path, 'rb') as f:
            files = {'photo': f}
            upload_response = requests.post(upload_url, files=files)
            upload_data = upload_response.json()

        save_url = 'https://api.vk.com/method/photos.saveWallPhoto'
        save_params = {
            'access_token': access_token,
            'v': VK_API_VERSION,
            'group_id': group_id,
            'photo': upload_data['photo'],
            'server': upload_data['server'],
            'hash': upload_data['hash']
        }
        save_response = requests.post(save_url, params=save_params)
        save_data = save_response.json()

        if 'error' in save_data:
            return None

        photo = save_data['response'][0]
        return f"photo{photo['owner_id']}_{photo['id']}"
    except Exception as e:
        print(f"Ошибка загрузки фото в VK: {e}")
        return None


def publish_to_vk_group(post_text, media_files, access_token, group_id):
    """Публикует пост в VK группу"""
    if not access_token or not group_id:
        return False, "Токен или ID группы VK не указаны"

    attachments = []

    if media_files:
        for file_path in media_files:
            if file_path and os.path.exists(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                if ext in ['.jpg', '.jpeg', '.png', '.gif']:
                    attachment = upload_photo_to_vk(file_path, access_token, group_id)
                    if attachment:
                        attachments.append(attachment)

    if len(post_text) > 10000:
        post_text = post_text[:9997] + "..."

    params = {
        'access_token': access_token,
        'v': VK_API_VERSION,
        'owner_id': f'-{group_id}',
        'from_group': 1,
        'message': post_text
    }

    if attachments:
        params['attachments'] = ','.join(attachments)

    try:
        response = requests.post('https://api.vk.com/method/wall.post', params=params)
        data = response.json()

        if 'error' in data:
            return False, f"Ошибка VK: {data['error']['error_msg']}"

        post_id = data['response']['post_id']
        return True, post_id
    except Exception as e:
        return False, f"Ошибка: {str(e)}"


def test_vk_token(access_token, group_id):
    """Проверяет токен VK для группы"""
    try:
        params = {
            'access_token': access_token,
            'v': VK_API_VERSION,
            'group_id': group_id
        }
        response = requests.get('https://api.vk.com/method/groups.getById', params=params)
        data = response.json()
        return 'error' not in data
    except:
        return False


# ========== OK.ru API функции ==========

def upload_photo_to_ok(photo_path, access_token, group_id):
    """Загружает фото в Одноклассники"""
    try:
        with open(photo_path, 'rb') as f:
            files = {'file': f}
            upload_response = requests.post(
                'https://api.ok.ru/api/photos/upload',
                params={
                    'access_token': access_token,
                    'gid': group_id
                },
                files=files
            )
            data = upload_response.json()

            if 'photo_id' in data:
                return data['photo_id']
            return None
    except Exception as e:
        print(f"Ошибка загрузки фото в OK: {e}")
        return None


def publish_to_ok_group(post_text, media_files, access_token, group_id):
    """Публикует пост в группу Одноклассников"""
    if not access_token or not group_id:
        return False, "Токен или ID группы OK не указаны"

    attachments = []

    if media_files:
        for file_path in media_files:
            if file_path and os.path.exists(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                if ext in ['.jpg', '.jpeg', '.png', '.gif']:
                    photo_id = upload_photo_to_ok(file_path, access_token, group_id)
                    if photo_id:
                        attachments.append(photo_id)

    post_data = {
        'access_token': access_token,
        'gid': group_id,
        'type': 'group',
        'message': post_text[:5000]
    }

    if attachments:
        post_data['media_id'] = ','.join(attachments)
        post_data['media_type'] = 'photo'

    try:
        response = requests.post('https://api.ok.ru/api/mediatopic/post', data=post_data)
        data = response.json()

        if 'error_code' in data:
            return False, f"Ошибка OK: {data.get('error_msg', 'Неизвестная ошибка')}"

        post_id = data.get('id')
        return True, post_id
    except Exception as e:
        return False, f"Ошибка: {str(e)}"


def test_ok_token(access_token, group_id):
    """Проверяет токен OK для группы"""
    try:
        params = {
            'access_token': access_token,
            'gid': group_id,
            'method': 'group.getInfo'
        }
        response = requests.get('https://api.ok.ru/api/group/getInfo', params=params)
        data = response.json()
        return 'error_code' not in data
    except:
        return False


# ========== Универсальная публикация ==========

def publish_to_group(post_text, media_files, group):
    """Универсальная публикация в зависимости от платформы"""
    platform = group.get('platform')

    if platform == 'vk':
        return publish_to_vk_group(
            post_text,
            media_files,
            group.get('token'),
            group.get('id')
        )
    elif platform == 'ok':
        return publish_to_ok_group(
            post_text,
            media_files,
            group.get('token'),
            group.get('id')
        )
    else:
        return False, f"Неизвестная платформа: {platform}"


# ========== Управление сообществами ==========

def load_groups():
    groups = load_json(GROUPS_FILE)
    if not groups:
        return []
    return groups


def save_groups(groups):
    save_json(GROUPS_FILE, groups)


# ========== Маршруты ==========

@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        users = {
            'writer': {'password': 'writer123', 'role': 'WRITER'},
            'viewer': {'password': 'viewer123', 'role': 'VIEWER'},
            'publisher': {'password': 'publisher123', 'role': 'PUBLISHER'},
            'censor': {'password': 'censor123', 'role': 'CENSOR'},
            'admin': {'password': 'admin123', 'role': 'ADMIN'}
        }

        if username in users and users[username]['password'] == password:
            session['username'] = username
            session['role'] = users[username]['role']
            log_action(username, f'Вход с ролью {users[username]["role"]}')
            flash(f'Добро пожаловать, {username}! Роль: {users[username]["role"]}', 'success')
            return redirect(url_for('dashboard'))
        else:
            log_action(username, 'Неудачная попытка входа')
            flash('Неверный логин или пароль', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    username = session.get('username')
    if username:
        log_action(username, 'Выход из системы')
    session.clear()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    user_role = session.get('role', 'VIEWER')
    posts = load_json(SPAMER_FILE)

    pending_posts = [p for p in posts if p.get('status_') in ['pending', 'на модерации']]
    approved_posts = [p for p in posts if p.get('status_') in ['approved', 'одобрен']]

    return render_template('dashboard.html',
                           role=user_role,
                           total_posts=len(posts),
                           pending=len(pending_posts),
                           approved=len(approved_posts))


@app.route('/create-post', methods=['GET', 'POST'])
@login_required
@role_required(['WRITER', 'PUBLISHER'])
def create_post():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        text = request.form.get('text', '').strip()

        if not title or not text:
            flash('Заголовок и текст не могут быть пустыми', 'danger')
            return redirect(url_for('create_post'))

        uploaded_files = request.files.getlist('media')
        saved_files = []

        UPLOAD_FOLDER.mkdir(exist_ok=True)

        for file in uploaded_files:
            if file and file.filename and allowed_file(file.filename):
                original_filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                unique_filename = f"{timestamp}_{original_filename}"
                filepath = UPLOAD_FOLDER / unique_filename
                file.save(filepath)
                saved_files.append(str(filepath))

        posts = load_json(SPAMER_FILE)

        new_post = {
            'id': max([p.get('id', 0) for p in posts], default=0) + 1,
            'user_id': session.get('username'),
            'title': title,
            'text': text,
            'media': saved_files,
            'status_': 'pending',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

        posts.append(new_post)
        save_json(SPAMER_FILE, posts)

        log_action(session.get('username'), f'Создан пост #{new_post["id"]}: {title} с {len(saved_files)} файлами')

        if saved_files:
            flash(f'Пост "{title}" создан. Загружено файлов: {len(saved_files)}', 'success')
        else:
            flash(f'Пост "{title}" создан и отправлен на модерацию', 'success')

        return redirect(url_for('dashboard'))

    return render_template('create_post.html')


@app.route('/moderate-posts')
@login_required
@role_required(['VIEWER', 'PUBLISHER'])
def moderate_posts():
    posts = load_json(SPAMER_FILE)
    pending_posts = [p for p in posts if p.get('status_') in ['pending', 'на модерации']]
    pending_posts.reverse()
    return render_template('moderate_posts.html', posts=pending_posts)


@app.route('/approve-post/<int:post_id>')
@login_required
@role_required(['VIEWER', 'PUBLISHER'])
def approve_post(post_id):
    posts = load_json(SPAMER_FILE)

    for post in posts:
        if post['id'] == post_id:
            if post.get('status_') in ['pending', 'на модерации']:
                post['status_'] = 'approved'
                post['approved_by'] = session.get('username')
                post['approved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                save_json(SPAMER_FILE, posts)
                log_action(session.get('username'), f'Одобрен пост #{post_id}')
                flash(f'Пост "{post["title"]}" одобрен', 'success')
            break

    return redirect(url_for('moderate_posts'))


@app.route('/reject-post/<int:post_id>')
@login_required
@role_required(['VIEWER', 'PUBLISHER'])
def reject_post(post_id):
    posts = load_json(SPAMER_FILE)

    for post in posts:
        if post['id'] == post_id:
            if post.get('status_') in ['pending', 'на модерации']:
                post['status_'] = 'rejected'
                post['rejected_by'] = session.get('username')
                post['rejected_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                save_json(SPAMER_FILE, posts)
                log_action(session.get('username'), f'Отклонен пост #{post_id}')
                flash(f'Пост "{post["title"]}" отклонен', 'warning')
            break

    return redirect(url_for('moderate_posts'))


@app.route('/publish-posts')
@login_required
@role_required(['PUBLISHER'])
def publish_posts():
    posts = load_json(SPAMER_FILE)
    approved_posts = [p for p in posts if p.get('status_') in ['approved', 'одобрен']]
    approved_posts.reverse()
    published_posts = [p for p in posts if p.get('status_') == 'published']
    published_posts.reverse()

    groups = load_groups()

    return render_template('publish_posts.html',
                           posts=approved_posts,
                           published_posts=published_posts,
                           groups=groups)


@app.route('/publish-to-multiple-groups/<int:post_id>', methods=['POST'])
@login_required
@role_required(['PUBLISHER'])
def publish_to_multiple_groups(post_id):
    """МНОГОПОТОЧНАЯ публикация поста в выбранные сообщества"""
    selected_group_ids = request.form.getlist('groups')

    if not selected_group_ids:
        flash('Выберите хотя бы одно сообщество для публикации', 'warning')
        return redirect(url_for('publish_posts'))

    # Загружаем пост
    posts = load_json(SPAMER_FILE)
    post = None
    for p in posts:
        if p['id'] == post_id:
            post = p
            break

    if not post:
        flash('Пост не найден', 'danger')
        return redirect(url_for('publish_posts'))

    if post.get('status_') not in ['approved', 'одобрен']:
        flash('Можно публиковать только одобренные посты', 'warning')
        return redirect(url_for('publish_posts'))

    # Загружаем группы
    groups = load_groups()
    selected_groups = [g for g in groups if str(g['id']) in selected_group_ids]

    publish_text = f"{post['title']}\n\n{post['text']}"

    # РЕЗУЛЬТАТЫ МНОГОПОТОЧНОЙ ПУБЛИКАЦИИ
    published_results = []

    # МНОГОПОТОЧНОСТЬ - все группы публикуются одновременно
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # Запускаем публикацию во все выбранные группы параллельно
        future_to_group = {
            executor.submit(publish_to_group, publish_text, post.get('media', []), group): group
            for group in selected_groups
        }

        # Собираем результаты по мере завершения
        for future in concurrent.futures.as_completed(future_to_group):
            group = future_to_group[future]
            try:
                success, result = future.result()
                published_results.append({
                    'name': group.get('name'),
                    'id': group.get('id'),
                    'platform': group.get('platform'),
                    'success': success,
                    'result': result
                })

                if success:
                    log_action(session.get('username'),
                               f'Опубликован пост #{post_id} в {group["platform"].upper()} группу {group["name"]} (ID: {result})')
                else:
                    log_action(session.get('username'),
                               f'Ошибка публикации #{post_id} в {group["platform"].upper()} группу {group["name"]}: {result}')
            except Exception as e:
                published_results.append({
                    'name': group.get('name'),
                    'id': group.get('id'),
                    'platform': group.get('platform'),
                    'success': False,
                    'result': str(e)
                })

    # Подсчитываем успешные публикации
    success_count = len([r for r in published_results if r['success']])

    # Обновляем статус поста
    if success_count == len(selected_groups):
        post['status_'] = 'published'
        post['published_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        post['published_to_groups'] = [
            {'id': r['id'], 'name': r['name'], 'platform': r['platform'], 'post_id': r['result']}
            for r in published_results if r['success']
        ]
        save_json(SPAMER_FILE, posts)
        flash(f'✅ Пост ОДНОВРЕМЕННО опубликован в {success_count} сообществ!', 'success')
    elif success_count > 0:
        post['status_'] = 'published'
        post['published_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        post['published_to_groups'] = [
            {'id': r['id'], 'name': r['name'], 'platform': r['platform'], 'post_id': r['result']}
            for r in published_results if r['success']
        ]
        save_json(SPAMER_FILE, posts)
        flash(
            f'⚠️ Частичная публикация: успешно в {success_count} из {len(selected_groups)} сообществ (публикация шла параллельно)',
            'warning')
    else:
        flash(f'❌ Не удалось опубликовать ни в одно сообщество', 'danger')

    return redirect(url_for('publish_posts'))


@app.route('/all-posts')
@login_required
def all_posts():
    posts = load_json(SPAMER_FILE)
    posts.reverse()
    return render_template('all_posts.html', posts=posts)


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


# ========== Управление сообществами (CRUD) ==========

@app.route('/manage-groups')
@login_required
@role_required(['PUBLISHER'])
def manage_groups():
    groups = load_groups()
    return render_template('manage_groups.html', groups=groups)


@app.route('/add-group', methods=['POST'])
@login_required
@role_required(['PUBLISHER'])
def add_group():
    name = request.form.get('name', '').strip()
    platform = request.form.get('platform', '').strip()
    group_id = request.form.get('group_id', '').strip()
    description = request.form.get('description', '').strip()
    token = request.form.get('token', '').strip()
    test_token_flag = request.form.get('test_token') == '1'

    if not name or not group_id or not token or not platform:
        flash('Заполните все обязательные поля', 'danger')
        return redirect(url_for('manage_groups'))

    if test_token_flag:
        if platform == 'vk':
            valid = test_vk_token(token, group_id)
        elif platform == 'ok':
            valid = test_ok_token(token, group_id)
        else:
            valid = False

        if not valid:
            flash('Токен недействителен или не имеет доступа к группе', 'danger')
            return redirect(url_for('manage_groups'))

    groups = load_groups()

    for g in groups:
        if str(g.get('id')) == group_id and g.get('platform') == platform:
            flash('Сообщество с таким ID и платформой уже существует', 'danger')
            return redirect(url_for('manage_groups'))

    new_group = {
        'id': group_id,
        'name': name,
        'platform': platform,
        'description': description,
        'token': token
    }

    groups.append(new_group)
    save_groups(groups)

    log_action(session.get('username'), f'Добавлено сообщество {name} ({platform}, ID: {group_id})')
    flash(f'Сообщество "{name}" успешно добавлено', 'success')
    return redirect(url_for('manage_groups'))


@app.route('/edit-group/<group_id>')
@login_required
@role_required(['PUBLISHER'])
def edit_group(group_id):
    groups = load_groups()
    group = None
    for g in groups:
        if str(g.get('id')) == group_id:
            group = g
            break

    if not group:
        flash('Сообщество не найдено', 'danger')
        return redirect(url_for('manage_groups'))

    return render_template('edit_group.html', group=group)


@app.route('/update-group/<group_id>', methods=['POST'])
@login_required
@role_required(['PUBLISHER'])
def update_group(group_id):
    name = request.form.get('name', '').strip()
    new_group_id = request.form.get('group_id', '').strip()
    platform = request.form.get('platform', '').strip()
    description = request.form.get('description', '').strip()
    token = request.form.get('token', '').strip()

    if not name or not new_group_id or not token or not platform:
        flash('Заполните все обязательные поля', 'danger')
        return redirect(url_for('edit_group', group_id=group_id))

    groups = load_groups()

    for i, g in enumerate(groups):
        if str(g.get('id')) == group_id:
            groups[i] = {
                'id': new_group_id,
                'name': name,
                'platform': platform,
                'description': description,
                'token': token
            }
            break

    save_groups(groups)

    log_action(session.get('username'), f'Обновлено сообщество {name} ({platform}, ID: {new_group_id})')
    flash(f'Сообщество "{name}" успешно обновлено', 'success')
    return redirect(url_for('manage_groups'))


@app.route('/delete-group/<group_id>')
@login_required
@role_required(['PUBLISHER'])
def delete_group(group_id):
    groups = load_groups()
    group_name = None
    group_platform = None

    new_groups = []
    for g in groups:
        if str(g.get('id')) == group_id:
            group_name = g.get('name')
            group_platform = g.get('platform')
        else:
            new_groups.append(g)

    if group_name:
        save_groups(new_groups)
        log_action(session.get('username'), f'Удалено сообщество {group_name} ({group_platform}, ID: {group_id})')
        flash(f'Сообщество "{group_name}" удалено', 'success')
    else:
        flash('Сообщество не найдено', 'danger')

    return redirect(url_for('manage_groups'))


@app.route('/test-group/<group_id>')
@login_required
@role_required(['PUBLISHER'])
def test_group(group_id):
    groups = load_groups()
    group = None

    for g in groups:
        if str(g.get('id')) == group_id:
            group = g
            break

    if not group:
        return jsonify({'success': False, 'message': 'Сообщество не найдено'})

    platform = group.get('platform')
    token = group.get('token')
    gid = group.get('id')

    if platform == 'vk':
        valid = test_vk_token(token, gid)
        platform_name = 'VK'
    elif platform == 'ok':
        valid = test_ok_token(token, gid)
        platform_name = 'OK.ru'
    else:
        valid = False
        platform_name = 'Неизвестно'

    if valid:
        return jsonify({'success': True, 'message': f'✅ Токен {platform_name} работает корректно'})
    else:
        return jsonify(
            {'success': False, 'message': f'❌ Токен {platform_name} недействителен или не имеет доступа к группе'})


@app.route('/ai/improve-text', methods=['POST'])
@login_required
@role_required(['WRITER', 'PUBLISHER'])
def ai_improve_text():
    """AI endpoint to improve text quality"""
    data = request.json
    text = data.get('text', '')
    
    if not text:
        return jsonify({'success': False, 'error': 'Текст не предоставлен'})
    
    success, result = ai_service.improve_text(text)
    
    if success:
        return jsonify({'success': True, 'improved_text': result})
    else:
        return jsonify({'success': False, 'error': result})


@app.route('/ai/check-grammar', methods=['POST'])
@login_required
@role_required(['WRITER', 'PUBLISHER'])
def ai_check_grammar():
    """AI endpoint to check grammar"""
    data = request.json
    text = data.get('text', '')
    
    if not text:
        return jsonify({'success': False, 'error': 'Текст не предоставлен'})
    
    success, result = ai_service.check_grammar(text)
    
    if success:
        return jsonify({'success': True, 'result': result})
    else:
        return jsonify({'success': False, 'error': result})


@app.route('/ai/suggest-titles', methods=['POST'])
@login_required
@role_required(['WRITER', 'PUBLISHER'])
def ai_suggest_titles():
    """AI endpoint to suggest titles"""
    data = request.json
    text = data.get('text', '')
    
    if not text:
        return jsonify({'success': False, 'error': 'Текст не предоставлен'})
    
    success, titles = ai_service.suggest_titles(text)
    
    if success:
        return jsonify({'success': True, 'titles': titles})
    else:
        return jsonify({'success': False, 'error': titles})


# ========== AI CENSOR ROUTES ==========

@app.route('/ai/moderate-post', methods=['POST'])
@login_required
@role_required(['CENSOR', 'PUBLISHER'])
def ai_moderate_post():
    """AI endpoint to moderate a post"""
    data = request.json
    post_id = data.get('post_id')

    print(f"Looking for post with ID: {post_id} (type: {type(post_id)})")
    
    posts = load_json(SPAMER_FILE)
    post = None
    
    for p in posts:
        if str(p.get('id')) == str(post_id):
            post = p
            break
    
    if not post:
        print(f"Post {post_id} not found. Available posts: {[(p.get('id'), type(p.get('id'))) for p in posts]}")
        return jsonify({'success': False, 'error': f'Пост #{post_id} не найден'})
    
    print(f"Found post: {post.get('title')}")
    
    success, analysis = ai_service.analyze_post_for_moderation(
        post.get('title', ''), 
        post.get('text', '')
    )
    
    if success:
        return jsonify({
            'success': True, 
            'analysis': analysis
        })
    else:
        return jsonify({'success': False, 'error': analysis})


@app.route('/ai/quick-check', methods=['POST'])
@login_required
@role_required(['CENSOR', 'PUBLISHER', 'VIEWER'])
def ai_quick_check():
    """Quick content check"""
    data = request.json
    text = data.get('text', '')
    
    if not text:
        return jsonify({'success': False, 'error': 'Текст не предоставлен'})
    
    success, result = ai_service.quick_content_check(text)
    
    if success:
        return jsonify({'success': True, 'result': result})
    else:
        return jsonify({'success': False, 'error': result})


# ========== AI PUBLISHER ROUTES ==========

@app.route('/ai/analyze-publication', methods=['POST'])
@login_required
@role_required(['PUBLISHER'])
def ai_analyze_publication():
    """Analyze post for publication"""
    data = request.json
    post_id = data.get('post_id')
    platforms = data.get('platforms', ['vk', 'ok'])
    
    posts = load_json(SPAMER_FILE)
    post = None
    
    for p in posts:
        if p['id'] == post_id:
            post = p
            break
    
    if not post:
        return jsonify({'success': False, 'error': 'Пост не найден'})
    
    success, analysis = ai_service.analyze_for_publication(
        post.get('title', ''),
        post.get('text', ''),
        platforms
    )
    
    if success:
        return jsonify({'success': True, 'analysis': analysis})
    else:
        return jsonify({'success': False, 'error': analysis})


@app.route('/ai/optimize-for-platform', methods=['POST'])
@login_required
@role_required(['PUBLISHER'])
def ai_optimize_for_platform():
    """Optimize post text for specific platform"""
    data = request.json
    text = data.get('text', '')
    platform = data.get('platform', 'vk')
    
    if not text:
        return jsonify({'success': False, 'error': 'Текст не предоставлен'})
    
    success, optimized = ai_service.optimize_for_platform(text, platform)
    
    if success:
        return jsonify({'success': True, 'optimized_text': optimized})
    else:
        return jsonify({'success': False, 'error': optimized})


@app.route('/ai/seo-analysis', methods=['POST'])
@login_required
@role_required(['PUBLISHER'])
def ai_seo_analysis():
    """Analyze SEO of text"""
    data = request.json
    text = data.get('text', '')
    
    if not text:
        return jsonify({'success': False, 'error': 'Текст не предоставлен'})
    
    success, analysis = ai_service.analyze_seo(text)
    
    if success:
        return jsonify({'success': True, 'analysis': analysis})
    else:
        return jsonify({'success': False, 'error': analysis})


# ========== AI GENERATOR ROUTES ==========

@app.route('/ai/get-training-data')
@login_required
@role_required(['WRITER', 'PUBLISHER'])
def ai_get_training_data():
    """Get previous approved/published posts for AI training"""
    posts = load_json(SPAMER_FILE)
    
    successful_posts = [
        p for p in posts 
        if p.get('status_') in ['approved', 'одобрен', 'published']
    ]
    
    recent_successful = successful_posts[-10:]
    
    formatted_posts = []
    for post in recent_successful:
        formatted_posts.append({
            'title': post.get('title', ''),
            'text': post.get('text', ''),
            'created_at': post.get('created_at', ''),
            'user_id': post.get('user_id', '')
        })
    
    context = "\n\n---\n\n".join([
        f"Пост {i+1}:\nЗаголовок: {p['title']}\nТекст: {p['text'][:500]}{'...' if len(p['text']) > 500 else ''}"
        for i, p in enumerate(formatted_posts)
    ])
    
    return jsonify({
        'success': True,
        'context': context,
        'posts_count': len(formatted_posts),
        'posts': formatted_posts
    })


@app.route('/ai/generate-post', methods=['POST'])
@login_required
@role_required(['WRITER', 'PUBLISHER'])
def ai_generate_post():
    """Generate a new post based on static training JSON"""
    data = request.json
    topic = data.get('topic', '').strip()
    
    if not topic:
        return jsonify({'success': False, 'error': 'Тема не указана'})
    
    training_data = load_json(TRAINING_DATA_FILE)
    
    if not training_data or not training_data.get('posts'):
        return jsonify({
            'success': False, 
            'error': 'Файл training_data.json пуст или не содержит постов. Добавьте примеры постов вручную.'
        })
    
    posts = training_data.get('posts', [])
    
    if not posts:
        return jsonify({
            'success': False, 
            'error': 'Нет постов в training_data.json для анализа стиля'
        })
    
    context_parts = []
    for i, post in enumerate(posts[:5], 1):
        title = post.get('title', 'Без заголовка')
        text = post.get('text', '')
        text_preview = text[:400] + '...' if len(text) > 400 else text
        
        context_parts.append(f"""
Пример {i}:
Заголовок: {title}
Текст: {text_preview}
        """.strip())
    
    context = "\n\n---\n\n".join(context_parts)
    success, generated_text = ai_service.generate_post(topic, context)
    
    if success:
        return jsonify({
            'success': True,
            'generated_text': generated_text
        })
    else:
        return jsonify({'success': False, 'error': generated_text})


@app.route('/ai/analyze-style', methods=['POST'])
@login_required
@role_required(['WRITER', 'PUBLISHER'])
def ai_analyze_style():
    """Analyze the writing style from static training JSON"""
    training_data = load_json(TRAINING_DATA_FILE)
    
    if not training_data or not training_data.get('posts'):
        return jsonify({'success': False, 'error': 'Файл training_data.json пуст или не содержит постов'})
    
    posts = training_data.get('posts', [])[:10]
    
    context_parts = []
    for post in posts:
        context_parts.append(f"Заголовок: {post.get('title', '')}\nТекст: {post.get('text', '')[:300]}")
    
    context = "\n\n---\n\n".join(context_parts)
    success, analysis = ai_service.analyze_style(context)
    
    if success:
        return jsonify({'success': True, 'analysis': analysis})
    else:
        return jsonify({'success': False, 'error': analysis})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
