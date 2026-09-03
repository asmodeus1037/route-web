import os
import re
import json
import random
import time
import threading
import requests
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, send_from_directory
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import urllib.parse
import pytz
import logging
import secrets
from functools import wraps

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

# ============================================================
# НАСТРОЙКА
# ============================================================
CREDENTIALS_FILE = "credentials.json"
SHEET_NAME = "Учет ремонта ВкусВилл"
START_COORDS = "55.775267, 37.745690"
MASTERS = ['Антон', 'Сергей', 'Руслан', 'Транзит', 'Алексей']
CACHE_TTL = 300
CACHE_DIR = "/data/cache"
BOT_API_URL = "https://route-bot-dzufear.waw0.amvera.tech"

os.makedirs(CACHE_DIR, exist_ok=True)

MSK = pytz.timezone('Europe/Moscow')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MASTER_CREDENTIALS = {
    'admin': {'password': 'admin123', 'name': 'Админ'},
    'anton': {'password': 'anton1987', 'name': 'Антон'},
    'sergey': {'password': 'sergey1992', 'name': 'Сергей'},
    'ruslan': {'password': 'ruslan1985', 'name': 'Руслан'},
    'transit': {'password': 'transit2024', 'name': 'Транзит'},
    'alexey': {'password': 'alexey0304', 'name': 'Алексей'}
}

# ============================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ============================================================
uid_index = {}

# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================
def get_msk_now():
    return datetime.now(MSK)

def is_active_status(status):
    return status in ['pending', 'todo', 'fail']

def extract_numbers(s):
    if not s:
        return ''
    return ''.join(re.findall(r'\d', str(s)))

# ============================================================
# РАБОТА С КЭШЕМ
# ============================================================
def get_cache_path(filename):
    return os.path.join(CACHE_DIR, filename)

def read_cache(filename):
    try:
        path = get_cache_path(filename)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except:
        pass
    return None

def write_cache(filename, data):
    try:
        path = get_cache_path(filename)
        data['updated_at'] = get_msk_now().strftime('%Y-%m-%d %H:%M:%S')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

def get_master_cache(name):
    return read_cache(f"master_{name}.json")

def save_master_cache(name, tickets, sent_date=''):
    active_tickets = [t for t in tickets if is_active_status(t.get('status'))]
    return write_cache(f"master_{name}.json", {
        'master': name,
        'sent_date': sent_date,
        'tickets': active_tickets
    })

def get_admin_cache():
    return read_cache("admin_cache.json")

def save_admin_cache(tickets):
    directions_data = {}
    for t in tickets:
        if not is_active_status(t.get('status')):
            continue
        dir_name = t.get('direction') or 'Без направления'
        if dir_name not in directions_data:
            directions_data[dir_name] = {'tickets': [], 'active_count': 0}
        directions_data[dir_name]['tickets'].append(t)
        directions_data[dir_name]['active_count'] += 1
    
    order = ['Напр 1', 'Напр 2', 'Напр 3', 'Напр 4', 'Без направления']
    sorted_directions = {}
    for key in order:
        if key in directions_data:
            sorted_directions[key] = directions_data[key]
    for key, value in directions_data.items():
        if key not in sorted_directions:
            sorted_directions[key] = value
    
    return write_cache("admin_cache.json", {'directions': sorted_directions})

def build_uid_index(tickets):
    index = {}
    for t in tickets:
        uid = t.get('uid')
        if uid:
            index[uid] = {
                'source': t.get('source'),
                'row_index': t.get('row_index'),
                'ticket': t
            }
    return index

def get_ticket_row_by_uid(uid):
    if uid in uid_index:
        return uid_index[uid]['row_index']
    return None

def update_ticket_in_admin_cache(uid, new_status, note='', display_desc=''):
    try:
        admin_cache = get_admin_cache()
        if not admin_cache:
            return False
        updated = False
        for dir_name, dir_data in admin_cache.get('directions', {}).items():
            for t in dir_data.get('tickets', []):
                if t.get('uid') == uid:
                    t['status'] = new_status
                    if note:
                        t['note'] = note
                    if display_desc:
                        t['display_desc'] = display_desc
                    updated = True
                    break
            if updated:
                break
        if updated:
            all_tickets = []
            for dir_name, dir_data in admin_cache.get('directions', {}).items():
                all_tickets.extend(dir_data.get('tickets', []))
            save_admin_cache(all_tickets)
            return True
        return False
    except:
        return False

# ============================================================
# GOOGLE SHEETS
# ============================================================
def get_sheet_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME)

def load_darks_reference():
    darks_ref = {}
    try:
        sheet_client = get_sheet_client()
        worksheet = sheet_client.worksheet("Дарксторы")
        rows = worksheet.get_all_values()
        if len(rows) > 1:
            for row in rows[1:]:
                if len(row) >= 4:
                    darks_num = row[0].strip()
                    address = row[1].strip()
                    direction = row[2].strip() if len(row) > 2 else ''
                    coords = row[3].strip() if len(row) > 3 else ''
                    darks_ref[darks_num] = {'address': address, 'direction': direction, 'coords': coords}
    except:
        pass
    return darks_ref

def parse_created_date(date_str):
    if not date_str:
        return None
    date_str = date_str.strip()
    match = re.match(r'(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})', date_str)
    if match:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)),
                       int(match.group(4)), int(match.group(5)), int(match.group(6)), tzinfo=MSK)
    match = re.match(r'(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})', date_str)
    if match:
        return datetime(int(match.group(3)), int(match.group(2)), int(match.group(1)),
                       int(match.group(4)), int(match.group(5)), 0, tzinfo=MSK)
    return None

def get_hours_since(created_str):
    if not created_str:
        return 0
    created = parse_created_date(created_str)
    if not created:
        return 0
    delta = get_msk_now() - created
    return round(delta.total_seconds() / 3600, 1)

def generate_ticket_id(date_str):
    if not date_str:
        return None
    try:
        date_obj = parse_created_date(date_str)
        if not date_obj:
            return None
        date_part = date_obj.strftime('%d%m%y')
        random_part = str(random.randint(1000, 9999))
        return f"{date_part}-{random_part}"
    except:
        return None

def get_tickets_from_sheets():
    sheet_client = get_sheet_client()
    tickets = []
    darks_ref = load_darks_reference()
    try:
        worksheet = sheet_client.worksheet("Заявки")
        rows = worksheet.get_all_values()
        if len(rows) > 1:
            for idx, row in enumerate(rows[1:], start=2):
                if len(row) < 15:
                    continue
                darks_num = row[0].strip()
                status = row[7].strip() if len(row) > 7 else ''
                if status in ['Выполнено', '✅ Выполнено', 'done']:
                    status = 'done'
                elif status in ['🔵 Доделать', 'Доделать']:
                    status = 'todo'
                elif status in ['⏹️ Обработано', 'Обработано', 'Вело отсутствует']:
                    status = 'fail'
                else:
                    status = 'pending'
                is_done = status in ['done', 'fail']
                created_str = row[5].strip() if len(row) > 5 else ''
                hours_since = get_hours_since(created_str)
                sent_date = row[14].strip() if len(row) > 14 else ''
                bike_type = row[1].strip() if len(row) > 1 else ''
                bike_subtype = row[8].strip() if len(row) > 8 else ''
                if bike_type == 'Электровелосипед' and bike_subtype:
                    display_type = bike_subtype
                else:
                    display_type = bike_type
                uid = row[12].strip() if len(row) > 12 else ''
                if not uid and created_str:
                    uid = generate_ticket_id(created_str)
                    if uid:
                        try:
                            worksheet.update_cell(idx, 13, uid)
                        except:
                            pass
                if not row[7].strip():
                    try:
                        worksheet.update_cell(idx, 8, '🟡 В работе')
                        status = 'pending'
                    except:
                        pass
                direction = row[13].strip() if len(row) > 13 else ''
                if not direction and darks_num in darks_ref:
                    direction = darks_ref[darks_num].get('direction', '')
                note = row[10].strip() if len(row) > 10 else ''
                display_desc = row[2].strip() if len(row) > 2 else ''
                if status == 'todo' and note and 'ЗАБРАЛИ:' in note:
                    match = re.search(r'ЗАБРАЛИ:\s*(\d+)', note)
                    if match:
                        count = match.group(1)
                        display_desc = f'Вернуть {count} АКБ (забирали на ремонт)'
                tickets.append({
                    'source': 'Заявки',
                    'darks': darks_num,
                    'type': bike_type,
                    'bike_type': display_type,
                    'bike_subtype': bike_subtype,
                    'desc': display_desc,
                    'gos': row[3].strip() if len(row) > 3 else '',
                    'contact': row[4].strip() if len(row) > 4 else '',
                    'created': created_str,
                    'hours_since': hours_since,
                    'master': row[6].strip() if len(row) > 6 else '',
                    'status': status,
                    'note': note,
                    'uid': uid,
                    'row_index': idx,
                    'address': darks_ref.get(darks_num, {}).get('address', ''),
                    'direction': direction,
                    'coords': darks_ref.get(darks_num, {}).get('coords', ''),
                    'sent_date': sent_date,
                    'is_done': is_done,
                    'is_active': not is_done,
                    'parts': row[10].strip() if len(row) > 10 else '',
                    'display_desc': display_desc
                })
    except Exception as e:
        print(f"Ошибка чтения 'Заявки': {e}")
    try:
        worksheet = sheet_client.worksheet("Импорт М4")
        rows = worksheet.get_all_values()
        if len(rows) > 1:
            for idx, row in enumerate(rows[1:], start=2):
                if len(row) < 15:
                    continue
                obj = row[4].strip() if len(row) > 4 else ''
                darks_match = re.search(r'^(\d{4})', obj)
                darks_num = darks_match.group(1) if darks_match else ''
                model = row[8].strip() if len(row) > 8 else ''
                bike_type = 'Не указан'
                if 'Электровелосипед' in model or 'электро' in model:
                    bike_type = 'Электровелосипед'
                elif 'Аккумуляторная батарея' in model or 'аккумуляторная' in model or '🔋' in model:
                    bike_type = 'Аккумуляторная батарея'
                elif 'Зарядное устройство' in model or 'зарядное' in model:
                    bike_type = 'Зарядное устройство'
                elif 'Багажник' in model:
                    bike_type = 'Багажник'
                elif 'Номерной знак' in model:
                    bike_type = 'Номерной знак'
                elif 'IoT' in model:
                    bike_type = 'IoT'
                gos = row[9].strip() if len(row) > 9 else ''
                uid = row[1].strip() if len(row) > 1 else ''
                status_raw = row[3].strip() if len(row) > 3 else ''
                status_l = row[11].strip() if len(row) > 11 else ''
                if status_raw == 'Решено' or status_l == 'Выполнено':
                    status = 'done'
                    is_done = True
                else:
                    status = 'pending'
                    is_done = False
                created_str = row[6].strip() if len(row) > 6 else ''
                hours_since = get_hours_since(created_str)
                sent_date = row[14].strip() if len(row) > 14 else ''
                tickets.append({
                    'source': 'Импорт М4',
                    'darks': darks_num,
                    'type': bike_type,
                    'bike_type': bike_type,
                    'desc': row[7].strip() if len(row) > 7 else '',
                    'gos': gos,
                    'created': created_str,
                    'hours_since': hours_since,
                    'master': row[13].strip() if len(row) > 13 else '',
                    'status': status,
                    'uid': uid,
                    'row_index': idx,
                    'address': darks_ref.get(darks_num, {}).get('address', ''),
                    'direction': darks_ref.get(darks_num, {}).get('direction', ''),
                    'coords': darks_ref.get(darks_num, {}).get('coords', ''),
                    'sent_date': sent_date,
                    'is_done': is_done,
                    'is_active': not is_done,
                    'parts': row[12].strip() if len(row) > 12 else ''
                })
    except Exception as e:
        print(f"Ошибка чтения 'Импорт М4': {e}")
    return tickets

def refresh_admin_cache():
    try:
        tickets = get_tickets_from_sheets()
        save_admin_cache(tickets)
    except:
        pass

def refresh_master_cache(master_name):
    try:
        tickets = get_tickets_from_sheets()
        master_tickets = [t for t in tickets if t.get('master') == master_name and is_active_status(t.get('status'))]
        save_master_cache(master_name, master_tickets, '')
    except:
        pass

def refresh_all_master_caches():
    try:
        tickets = get_tickets_from_sheets()
        for master in MASTERS:
            master_tickets = [t for t in tickets if t.get('master') == master and is_active_status(t.get('status'))]
            save_master_cache(master, master_tickets, '')
    except:
        pass

def batch_update_masters(changes):
    global uid_index
    updated_count = 0
    try:
        if not uid_index:
            tickets = get_tickets_from_sheets()
            uid_index = build_uid_index(tickets)
        sheet_client = get_sheet_client()
        updates_zayavki = []
        updates_import = []
        for change in changes:
            uid = change.get('uid')
            source = change.get('source')
            master = change.get('master')
            if not uid or not master:
                continue
            row_idx = get_ticket_row_by_uid(uid)
            if not row_idx:
                continue
            if source == 'Заявки':
                updates_zayavki.append({'range': f'G{row_idx}', 'values': [[master]]})
                updated_count += 1
            elif source == 'Импорт М4':
                updates_import.append({'range': f'N{row_idx}', 'values': [[master]]})
                updated_count += 1
        if updates_zayavki:
            worksheet = sheet_client.worksheet("Заявки")
            worksheet.batch_update(updates_zayavki)
        if updates_import:
            worksheet = sheet_client.worksheet("Импорт М4")
            worksheet.batch_update(updates_import)
        tickets = get_tickets_from_sheets()
        uid_index = build_uid_index(tickets)
        save_admin_cache(tickets)
        return updated_count
    except:
        return 0

def clear_master_assignments(master_name=None):
    try:
        sheet_client = get_sheet_client()
        cleared = 0
        updates = []
        worksheet = sheet_client.worksheet("Заявки")
        all_rows = worksheet.get_all_values()
        for idx, row in enumerate(all_rows, start=1):
            if idx == 1:
                continue
            if len(row) > 14:
                status = row[7].strip() if len(row) > 7 else ''
                if status in ['🟡 В работе', '🔵 Доделать', '⏹️ Обработано', 'В работе', 'Доделать', 'Обработано', 'pending', 'todo', 'fail']:
                    current_master = row[6].strip() if len(row) > 6 else ''
                    if master_name is None or current_master == master_name:
                        updates.append({'range': f'G{idx}', 'values': [['']]})
                        cleared += 1
        if updates:
            worksheet.batch_update(updates)
        tickets = get_tickets_from_sheets()
        uid_index = build_uid_index(tickets)
        save_admin_cache(tickets)
        refresh_all_master_caches()
        return cleared
    except:
        return 0

# ============================================================
# ОТПРАВКА УВЕДОМЛЕНИЙ
# ============================================================
def notify_curators(message):
    try:
        response = requests.post(
            f"{BOT_API_URL}/send_notification",
            json={"message": message, "type": "curator_notification"},
            timeout=10
        )
        return response.status_code == 200
    except:
        return False

# ============================================================
# АВТОРИЗАЦИЯ
# ============================================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# ============================================================
# МАРШРУТЫ
# ============================================================
@app.route('/')
def index():
    return redirect(url_for('login_page'))

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    login = data.get('login', '').strip().lower()
    password = data.get('password', '').strip()
    if login in MASTER_CREDENTIALS and MASTER_CREDENTIALS[login]['password'] == password:
        return jsonify({'success': True, 'master': MASTER_CREDENTIALS[login]['name'], 'login': login})
    return jsonify({'success': False, 'error': 'Неверный логин или пароль'})

@app.route('/auto_login/<login>')
def auto_login(login):
    if login in MASTER_CREDENTIALS:
        master_name = MASTER_CREDENTIALS[login]['name']
        session['master_login'] = login
        session['master_name'] = master_name
        session['authenticated'] = True
        return redirect(url_for('master_overview', name=master_name))
    return redirect(url_for('login_page'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

# ============================================================
# СТАТИКА
# ============================================================
@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

@app.route('/data/<path:filename>')
def data_files(filename):
    return send_from_directory('/data', filename)

# ============================================================
# АДМИН-ПАНЕЛЬ
# ============================================================
@app.route('/admin')
@login_required
def admin_panel():
    cache_data = get_admin_cache()
    if cache_data and 'directions' in cache_data:
        directions = cache_data.get('directions', {})
    else:
        tickets = get_tickets_from_sheets()
        save_admin_cache(tickets)
        cache_data = get_admin_cache()
        directions = cache_data.get('directions', {})
    return render_template('admin.html',
                          directions=directions,
                          masters=MASTERS,
                          now=get_msk_now().strftime('%H:%M:%S'))

# ============================================================
# API ДЛЯ АДМИНКИ
# ============================================================
@app.route('/api/sync')
@login_required
def api_sync():
    global uid_index
    try:
        tickets = get_tickets_from_sheets()
        darks_ref = load_darks_reference()
        uid_index = build_uid_index(tickets)
        save_admin_cache(tickets)
        refresh_all_master_caches()
        return jsonify({'success': True, 'tickets': tickets})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/batch_update', methods=['POST'])
@login_required
def api_batch_update():
    data = request.json
    changes = data.get('changes', [])
    if not changes:
        return jsonify({'success': False, 'error': 'Нет изменений'})
    updated = batch_update_masters(changes)
    return jsonify({'success': True, 'updated': updated})

@app.route('/api/send_route', methods=['POST'])
@login_required
def api_send_route():
    data = request.json
    master = data.get('master')
    if not master:
        return jsonify({'success': False, 'error': 'Не указан мастер'})
    refresh_master_cache(master)
    return jsonify({'success': True, 'message': f'Кэш мастера {master} обновлен'})

@app.route('/api/send_route_all', methods=['POST'])
@login_required
def api_send_route_all():
    for master in MASTERS:
        threading.Thread(target=refresh_master_cache, args=(master,)).start()
    return jsonify({'success': True, 'message': 'Кэши всех мастеров обновляются'})

@app.route('/api/clear_dates', methods=['POST'])
@login_required
def api_clear_dates():
    cleared = clear_master_assignments(None)
    return jsonify({'success': True, 'cleared': cleared})

@app.route('/api/clear_master', methods=['POST'])
@login_required
def api_clear_master():
    data = request.json
    master = data.get('master')
    cleared = clear_master_assignments(master)
    return jsonify({'success': True, 'cleared': cleared})

@app.route('/api/notify_curators', methods=['POST'])
@login_required
def api_notify_curators():
    data = request.json
    message = data.get('message', '')
    if not message:
        return jsonify({'success': False, 'error': 'Нет сообщения'})
    success = notify_curators(message)
    return jsonify({'success': success})

@app.route('/api/admin_action', methods=['POST'])
@login_required
def api_admin_action():
    data = request.json
    uid = data.get('uid')
    action = data.get('action')
    extra = data.get('extra', '')
    if not uid or not action:
        return jsonify({'success': False, 'error': 'Недостаточно данных'})
    try:
        if action == 'done':
            update_ticket_in_admin_cache(uid, 'done', extra or 'Выполнено админом')
        elif action == 'evacuation':
            update_ticket_in_admin_cache(uid, 'todo', f'ЭВАКУАЦИЯ: {extra}')
        elif action == 'fail':
            update_ticket_in_admin_cache(uid, 'fail', 'Вело отсутствует')
        elif action == 'todo':
            update_ticket_in_admin_cache(uid, 'todo', 'Доделать')
        elif action == 'taken':
            update_ticket_in_admin_cache(uid, 'todo', f'ЗАБРАЛИ: {extra} АКБ',
                                        f'Вернуть {extra} АКБ (забирали на ремонт)')
        else:
            return jsonify({'success': False, 'error': 'Неизвестное действие'})
        threading.Thread(target=refresh_admin_cache).start()
        threading.Thread(target=refresh_all_master_caches).start()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/build_route_for_master')
@login_required
def api_build_route_for_master():
    master = request.args.get('master', '')
    if not master:
        return 'Нет мастера', 400
    cache_data = get_master_cache(master)
    if cache_data:
        master_tickets = cache_data.get('tickets', [])
    else:
        tickets = get_tickets_from_sheets()
        master_tickets = [t for t in tickets if t.get('master') == master and is_active_status(t.get('status'))]
    coords_set = set()
    for t in master_tickets:
        if t.get('coords'):
            coords_set.add(t['coords'])
    coords_list = list(coords_set)
    if not coords_list:
        return 'Нет координат', 400
    user_agent = request.headers.get('User-Agent', '').lower()
    is_mobile = any(x in user_agent for x in ['android', 'iphone', 'ipad', 'mobile'])
    if is_mobile:
        coords_list_str = '~'.join([START_COORDS] + coords_list)
        url = f'yandexnavi://build_route_on_map?lat_lon={coords_list_str}'
    else:
        points = [START_COORDS] + coords_list
        url = 'https://yandex.ru/maps/?rtext=' + '~'.join(points)
    return redirect(url)

# ============================================================
# МАРШРУТЫ МАСТЕРОВ
# ============================================================
@app.route('/master/<name>')
@login_required
def master_overview(name):
    if session.get('master_name') != name:
        return redirect(url_for('login_page'))
    cache_data = get_master_cache(name)
    if cache_data:
        master_tickets = cache_data.get('tickets', [])
    else:
        tickets = get_tickets_from_sheets()
        master_tickets = [t for t in tickets if t.get('master') == name and is_active_status(t.get('status'))]
        save_master_cache(name, master_tickets, '')
    if not master_tickets:
        return render_template('master_empty.html', name=name)
    for t in master_tickets:
        t['hours_since'] = get_hours_since(t.get('created', ''))
        t['is_today_done'] = t.get('status') in ['done', 'fail']
    groups_dict = {}
    for t in master_tickets:
        darks_num = t.get('darks') or 'без номера'
        if darks_num not in groups_dict:
            groups_dict[darks_num] = {
                'darks_number': darks_num,
                'address': t.get('address', 'Адрес не указан'),
                'contact': t.get('contact', ''),
                'tickets': [],
                'pending': 0,
                'done': 0
            }
        groups_dict[darks_num]['tickets'].append(t)
        if t.get('status') in ['pending', 'todo']:
            groups_dict[darks_num]['pending'] += 1
        else:
            groups_dict[darks_num]['done'] += 1
    groups = []
    for darks_num in sorted(groups_dict.keys(), key=lambda x: int(x) if x.isdigit() else 999999):
        groups.append(groups_dict[darks_num])
    addresses = []
    for g in groups:
        addr = g['address'].replace('📍', '').strip()
        if addr:
            addresses.append(addr)
    if addresses:
        route_url = 'https://yandex.ru/maps/?rtext=55.775267,37.745690~' + '~'.join([urllib.parse.quote(a) for a in addresses])
    else:
        route_url = ''
    return render_template('master_main.html',
                          name=name,
                          groups=groups,
                          total_tickets=len(master_tickets),
                          route_url=route_url,
                          now=get_msk_now().strftime('%H:%M:%S'))

@app.route('/master/<name>/darks/<darks_number>')
@login_required
def master_darks(name, darks_number):
    if session.get('master_name') != name:
        return redirect(url_for('login_page'))
    cache_data = get_master_cache(name)
    if cache_data:
        all_tickets = cache_data.get('tickets', [])
        filtered = [t for t in all_tickets if t.get('darks') == darks_number]
    else:
        tickets = get_tickets_from_sheets()
        filtered = [t for t in tickets if t.get('master') == name and t.get('darks') == darks_number and is_active_status(t.get('status'))]
        save_master_cache(name, filtered, '')
    for t in filtered:
        t['hours_since'] = get_hours_since(t.get('created', ''))
    address = filtered[0].get('address', 'Адрес не указан') if filtered else ''
    contact = filtered[0].get('contact', '') if filtered else ''
    return render_template('master_darks.html',
                          name=name,
                          darks_number=darks_number,
                          address=address,
                          contact=contact,
                          tickets=filtered,
                          now=get_msk_now().strftime('%H:%M:%S'))

@app.route('/master/<name>/text_plan')
@login_required
def master_text_plan(name):
    if session.get('master_name') != name:
        return redirect(url_for('login_page'))
    cache_data = get_master_cache(name)
    if cache_data:
        master_tickets = cache_data.get('tickets', [])
    else:
        tickets = get_tickets_from_sheets()
        master_tickets = [t for t in tickets if t.get('master') == name and is_active_status(t.get('status'))]
    for t in master_tickets:
        t['hours_since'] = get_hours_since(t.get('created', ''))
    pending_count = len([t for t in master_tickets if t.get('status') == 'pending'])
    todo_count = len([t for t in master_tickets if t.get('status') == 'todo'])
    return render_template('text_plan.html',
                          name=name,
                          tickets=master_tickets,
                          pending_count=pending_count,
                          todo_count=todo_count,
                          now=get_msk_now().strftime('%H:%M:%S'))

# ============================================================
# API ДЛЯ МАСТЕРОВ
# ============================================================
@app.route('/master/<name>/darks/<darks_number>/done/<uid>', methods=['POST'])
@login_required
def master_done(name, darks_number, uid):
    if session.get('master_name') != name:
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    parts = request.form.get('parts', '')
    if not parts.strip():
        return jsonify({'success': False, 'error': 'Укажите запчасти'})
    cache_data = get_master_cache(name)
    if cache_data:
        tickets = cache_data.get('tickets', [])
        tickets = [t for t in tickets if t.get('uid') != uid]
        save_master_cache(name, tickets, '')
    return jsonify({'success': True})

@app.route('/master/<name>/darks/<darks_number>/fail/<uid>')
@login_required
def master_fail(name, darks_number, uid):
    if session.get('master_name') != name:
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    cache_data = get_master_cache(name)
    if cache_data:
        tickets = cache_data.get('tickets', [])
        tickets = [t for t in tickets if t.get('uid') != uid]
        save_master_cache(name, tickets, '')
    return jsonify({'success': True})

@app.route('/master/<name>/darks/<darks_number>/evacuation/<uid>', methods=['POST'])
@login_required
def master_evacuation(name, darks_number, uid):
    if session.get('master_name') != name:
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    reason = request.form.get('reason', '')
    if not reason.strip():
        return jsonify({'success': False, 'error': 'Не указана причина'})
    cache_data = get_master_cache(name)
    if cache_data:
        tickets = cache_data.get('tickets', [])
        tickets = [t for t in tickets if t.get('uid') != uid]
        save_master_cache(name, tickets, '')
    return jsonify({'success': True})

@app.route('/master/<name>/darks/<darks_number>/taken_no_replace/<uid>', methods=['POST'])
@login_required
def master_taken_no_replace(name, darks_number, uid):
    if session.get('master_name') != name:
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    parts = request.form.get('parts', '')
    if not parts or int(parts) <= 0:
        return jsonify({'success': False, 'error': 'Укажите количество'})
    cache_data = get_master_cache(name)
    if cache_data:
        tickets = cache_data.get('tickets', [])
        tickets = [t for t in tickets if t.get('uid') != uid]
        save_master_cache(name, tickets, '')
    return jsonify({'success': True})

@app.route('/master/<name>/darks/<darks_number>/reset/<uid>')
@login_required
def master_reset(name, darks_number, uid):
    if session.get('master_name') != name:
        return redirect(url_for('login_page'))
    return redirect(url_for('master_darks', name=name, darks_number=darks_number))

@app.route('/master/<name>/darks/<darks_number>/replace_yes/<uid>', methods=['POST'])
@login_required
def master_replace_yes(name, darks_number, uid):
    if session.get('master_name') != name:
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    parts = request.form.get('parts', '')
    if not parts or int(parts) <= 0:
        return jsonify({'success': False, 'error': 'Укажите количество'})
    cache_data = get_master_cache(name)
    if cache_data:
        tickets = cache_data.get('tickets', [])
        tickets = [t for t in tickets if t.get('uid') != uid]
        save_master_cache(name, tickets, '')
    return jsonify({'success': True})

@app.route('/master/<name>/darks/<darks_number>/replace_no/<uid>', methods=['POST'])
@login_required
def master_replace_no(name, darks_number, uid):
    if session.get('master_name') != name:
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    cache_data = get_master_cache(name)
    if cache_data:
        tickets = cache_data.get('tickets', [])
        tickets = [t for t in tickets if t.get('uid') != uid]
        save_master_cache(name, tickets, '')
    return jsonify({'success': True})

# ============================================================
# ЗАПУСК
# ============================================================
if __name__ == "__main__":
    logger.info("🚀 Запуск приложения...")
    try:
        tickets = get_tickets_from_sheets()
        uid_index = build_uid_index(tickets)
        save_admin_cache(tickets)
        logger.info("✅ Кеш загружен")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки кеша: {e}")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
