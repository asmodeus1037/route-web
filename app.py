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
CREDENTIALS_FILE = "/data/credentials.json"
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
    'admin': {'password': 'admin123', 'name': 'Админ', 'role': 'admin'},
    'anton': {'password': 'anton1987', 'name': 'Антон', 'role': 'master'},
    'sergey': {'password': 'sergey1992', 'name': 'Сергей', 'role': 'master'},
    'ruslan': {'password': 'ruslan1985', 'name': 'Руслан', 'role': 'master'},
    'transit': {'password': 'transit2024', 'name': 'Транзит', 'role': 'master'},
    'alexey': {'password': 'alexey0304', 'name': 'Алексей', 'role': 'master'}
}

# ============================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ============================================================
uid_index = {}
darks_ref = {}
queue_lock = threading.Lock()

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
    # Всегда показываем все 4 направления + Без направления
    directions_data = {
        'Напр 1': {'tickets': [], 'active_count': 0},
        'Напр 2': {'tickets': [], 'active_count': 0},
        'Напр 3': {'tickets': [], 'active_count': 0},
        'Напр 4': {'tickets': [], 'active_count': 0},
        'Без направления': {'tickets': [], 'active_count': 0}
    }
    
    # Распределяем заявки по направлениям
    for t in tickets:
        if not is_active_status(t.get('status')):
            continue
        dir_name = t.get('direction') or 'Без направления'
        if dir_name not in directions_data:
            directions_data[dir_name] = {'tickets': [], 'active_count': 0}
        directions_data[dir_name]['tickets'].append(t)
        directions_data[dir_name]['active_count'] += 1
    
    # Сохраняем в порядке: Напр 1, Напр 2, Напр 3, Напр 4, Без направления
    order = ['Напр 1', 'Напр 2', 'Напр 3', 'Напр 4', 'Без направления']
    sorted_directions = {}
    for key in order:
        if key in directions_data:
            sorted_directions[key] = directions_data[key]
    
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

def find_uid_in_sheets(uid):
    """Ищет UID в Google Sheets и возвращает строку"""
    try:
        sheet_client = get_sheet_client()
        
        # Ищем в листе "Заявки"
        worksheet = sheet_client.worksheet("Заявки")
        cell = worksheet.find(uid)
        if cell:
            return {'source': 'Заявки', 'row_index': cell.row}
        
        # Ищем в листе "Импорт М4"
        worksheet = sheet_client.worksheet("Импорт М4")
        cell = worksheet.find(uid)
        if cell:
            return {'source': 'Импорт М4', 'row_index': cell.row}
        
        return None
    except Exception as e:
        logger.error(f"Ошибка поиска UID {uid} в Google Sheets: {e}")
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
    except Exception as e:
        logger.error(f"Ошибка обновления admin_cache для {uid}: {e}")
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
    global darks_ref
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
    except Exception as e:
        logger.error(f"Ошибка загрузки Дарксторов: {e}")
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
    global darks_ref
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
        logger.error(f"Ошибка чтения 'Заявки': {e}")
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
        logger.error(f"Ошибка чтения 'Импорт М4': {e}")
    return tickets

def refresh_admin_cache():
    try:
        tickets = get_tickets_from_sheets()
        save_admin_cache(tickets)
        logger.info(f"✅ Админ кэш обновлен: {len(tickets)} заявок")
    except Exception as e:
        logger.error(f"Ошибка обновления админ кэша: {e}")

def refresh_master_cache(master_name):
    try:
        tickets = get_tickets_from_sheets()
        master_tickets = [t for t in tickets if t.get('master') == master_name and is_active_status(t.get('status'))]
        save_master_cache(master_name, master_tickets, '')
        logger.info(f"✅ Кэш для {master_name} обновлен: {len(master_tickets)} заявок")
    except Exception as e:
        logger.error(f"Ошибка обновления кэша {master_name}: {e}")

def refresh_all_master_caches():
    try:
        tickets = get_tickets_from_sheets()
        for master in MASTERS:
            master_tickets = [t for t in tickets if t.get('master') == master and is_active_status(t.get('status'))]
            save_master_cache(master, master_tickets, '')
        logger.info("✅ Кэши всех мастеров обновлены")
    except Exception as e:
        logger.error(f"Ошибка обновления кэшей мастеров: {e}")

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
    except Exception as e:
        logger.error(f"Ошибка batch_update_masters: {e}")
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
    except Exception as e:
        logger.error(f"Ошибка снятия заявок: {e}")
        return 0

# ============================================================
# ОЧЕРЕДЬ ЗАДАЧ
# ============================================================
def get_queue_path():
    return os.path.join(CACHE_DIR, "queue.json")

def read_queue():
    with queue_lock:
        try:
            path = get_queue_path()
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except:
            pass
        return {'tasks': [], 'last_sync': None}

def write_queue(queue_data):
    with queue_lock:
        try:
            path = get_queue_path()
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(queue_data, f, ensure_ascii=False, indent=2)
            return True
        except:
            return False

def add_to_queue(task):
    queue_data = read_queue()
    for existing in queue_data['tasks']:
        if existing.get('uid') == task.get('uid'):
            return
    queue_data['tasks'].append(task)
    write_queue(queue_data)
    logger.info(f"✅ Задача добавлена в очередь: {task.get('uid')}")

def clear_queue():
    write_queue({'tasks': [], 'last_sync': get_msk_now().strftime('%Y-%m-%d %H:%M:%S')})

def send_tasks_to_google_sheets(tasks):
    """Отправляет задачи в Google Sheets"""
    try:
        sheet_client = get_sheet_client()
        now = get_msk_now().strftime('%Y-%m-%d %H:%M:%S')
        updates_status = []
        updates_note = []
        updates_import = []
        report_updates = []
        
        # Получаем лист "Отчет мастера"
        try:
            report_sheet = sheet_client.worksheet("Отчет мастера")
        except:
            report_sheet = sheet_client.add_worksheet("Отчет мастера", 100, 20)
            headers = ['Дата выполнения', 'Мастер', 'ID заявки', 'Госномер', 'Описание', 'Тип техники', 'Количество', 'Статус', 'Запчасти', 'Комментарий', 'Даркстор', 'Время создания']
            for i, h in enumerate(headers, start=1):
                report_sheet.update_cell(1, i, h)
        
        # Получаем текущие строки для отчета
        current_rows = report_sheet.get_all_values()
        start_row = len(current_rows) + 1
        
        for idx, task in enumerate(tasks):
            uid = task.get('uid')
            source = task.get('source')
            task_type = task.get('type')
            data = task.get('data', {})
            master = data.get('master', '')
            darks_number = data.get('darks_number', '')
            parts = data.get('parts', '')
            reason = data.get('reason', '')
            extra = data.get('extra', '')
            
            # Ищем заявку в Google Sheets по UID
            found = find_uid_in_sheets(uid)
            if not found:
                logger.warning(f"UID {uid} не найден в Google Sheets")
                continue
            
            row_idx = found['row_index']
            source = found['source']
            
            # Обновляем в зависимости от источника
            if source == 'Заявки':
                if task_type == 'done':
                    updates_status.append({'range': f'H{row_idx}', 'values': [['✅ Выполнено']]})
                    if parts:
                        updates_note.append({'range': f'K{row_idx}', 'values': [[parts]]})
                elif task_type == 'fail':
                    updates_status.append({'range': f'H{row_idx}', 'values': [['⏹️ Обработано']]})
                    updates_note.append({'range': f'K{row_idx}', 'values': [[f'Вело отсутствует {now}']]})
                elif task_type == 'evacuation':
                    updates_status.append({'range': f'H{row_idx}', 'values': [['🔵 Доделать']]})
                    updates_note.append({'range': f'K{row_idx}', 'values': [[f'ЭВАКУАЦИЯ: {reason}']]})
                elif task_type == 'replace_yes':
                    updates_status.append({'range': f'H{row_idx}', 'values': [['✅ Выполнено']]})
                    updates_note.append({'range': f'K{row_idx}', 'values': [[f'Заменено {parts} шт.']]})
                elif task_type == 'taken_no_replace':
                    updates_status.append({'range': f'H{row_idx}', 'values': [['🔵 Доделать']]})
                    updates_note.append({'range': f'K{row_idx}', 'values': [[f'ЗАБРАЛИ: {parts} АКБ']]})
                elif task_type == 'replace_no':
                    updates_status.append({'range': f'H{row_idx}', 'values': [['🔵 Доделать']]})
                    updates_note.append({'range': f'K{row_idx}', 'values': [['Куратор не предоставил']]})
                elif task_type == 'transit_replace':
                    updates_status.append({'range': f'H{row_idx}', 'values': [['✅ Выполнено']]})
                    updates_note.append({'range': f'K{row_idx}', 'values': [['Заменен Транзитом']]})
                
                # Добавляем запись в отчет
                ticket = None
                if uid in uid_index:
                    ticket = uid_index[uid]['ticket']
                if not ticket:
                    # Пытаемся получить данные из Google Sheets
                    worksheet = sheet_client.worksheet("Заявки")
                    row_data = worksheet.row_values(row_idx)
                    if len(row_data) >= 15:
                        ticket = {
                            'gos': row_data[3].strip() if len(row_data) > 3 else '',
                            'desc': row_data[2].strip() if len(row_data) > 2 else '',
                            'type': row_data[1].strip() if len(row_data) > 1 else '',
                            'created': row_data[5].strip() if len(row_data) > 5 else ''
                        }
                
                if ticket:
                    report_row = start_row + len(report_updates) // 12
                    report_updates.append({'range': f'A{report_row}', 'values': [[now]]})
                    report_updates.append({'range': f'B{report_row}', 'values': [[master]]})
                    report_updates.append({'range': f'C{report_row}', 'values': [[uid]]})
                    report_updates.append({'range': f'D{report_row}', 'values': [[ticket.get('gos', '')]]})
                    report_updates.append({'range': f'E{report_row}', 'values': [[ticket.get('desc', '')]]})
                    report_updates.append({'range': f'F{report_row}', 'values': [[ticket.get('type', '')]]})
                    report_updates.append({'range': f'G{report_row}', 'values': [[parts or extra]]})
                    report_updates.append({'range': f'H{report_row}', 'values': [[task_type]]})
                    report_updates.append({'range': f'I{report_row}', 'values': [[parts or extra]]})
                    report_updates.append({'range': f'J{report_row}', 'values': [[reason]]})
                    report_updates.append({'range': f'K{report_row}', 'values': [[darks_number]]})
                    report_updates.append({'range': f'L{report_row}', 'values': [[ticket.get('created', '')]]})
            
            elif source == 'Импорт М4':
                if task_type == 'done':
                    updates_import.append({'range': f'L{row_idx}', 'values': [['Выполнено']]})
                elif task_type == 'fail':
                    updates_import.append({'range': f'L{row_idx}', 'values': [['Вело отсутствует']]})
                    updates_import.append({'range': f'M{row_idx}', 'values': [[f'Вело отсутствует на дарксторе {now}']]})
                elif task_type == 'evacuation':
                    updates_import.append({'range': f'L{row_idx}', 'values': [['Эвакуация']]})
                    updates_import.append({'range': f'M{row_idx}', 'values': [['Запланирована эвакуация велосипеда']]})
        
        # Применяем обновления
        if updates_status or updates_note:
            worksheet = sheet_client.worksheet("Заявки")
            if updates_status:
                worksheet.batch_update(updates_status)
            if updates_note:
                worksheet.batch_update(updates_note)
        
        if updates_import:
            worksheet_import = sheet_client.worksheet("Импорт М4")
            worksheet_import.batch_update(updates_import)
        
        if report_updates:
            report_sheet.batch_update(report_updates)
            logger.info(f"✅ Записано {len(report_updates)//12} записей в Отчет мастера")
        
        logger.info(f"✅ Отправлено {len(tasks)} задач в Google Sheets")
        
    except Exception as e:
        logger.error(f"Ошибка отправки в Google Sheets: {e}")
        raise

def process_queue_background():
    last_gs_sync = time.time()
    while True:
        try:
            time.sleep(10)
            queue_data = read_queue()
            if not queue_data['tasks']:
                continue
            
            tasks = queue_data['tasks']
            logger.info(f"📋 Обработка {len(tasks)} задач из очереди")
            
            for task in tasks:
                uid = task.get('uid')
                task_type = task.get('type')
                data = task.get('data', {})
                
                if task_type == 'done':
                    update_ticket_in_admin_cache(uid, 'done', data.get('parts', ''))
                elif task_type == 'fail':
                    update_ticket_in_admin_cache(uid, 'fail', 'Вело отсутствует')
                elif task_type == 'evacuation':
                    update_ticket_in_admin_cache(uid, 'todo', f'ЭВАКУАЦИЯ: {data.get("reason", "")}')
                elif task_type == 'replace_yes':
                    update_ticket_in_admin_cache(uid, 'done', f'Заменено {data.get("parts", "")} шт.')
                elif task_type == 'taken_no_replace':
                    update_ticket_in_admin_cache(uid, 'todo', f'ЗАБРАЛИ: {data.get("parts", "")} АКБ')
                elif task_type == 'replace_no':
                    update_ticket_in_admin_cache(uid, 'todo', f'Куратор не предоставил')
                elif task_type == 'transit_replace':
                    update_ticket_in_admin_cache(uid, 'done', 'Заменен Транзитом')
            
            current_time = time.time()
            if current_time - last_gs_sync >= 30:
                send_tasks_to_google_sheets(tasks)
                clear_queue()
                last_gs_sync = current_time
                logger.info("✅ Очередь очищена")
            
        except Exception as e:
            logger.error(f"Ошибка в фоновом процессе: {e}")

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

def generate_curator_message(tickets_data):
    """Генерирует красивое сообщение для кураторов"""
    now = get_msk_now().strftime('%d.%m.%Y %H:%M')
    
    message = f"📢 Привет, на связи Vanta Bikes! ({now})\n\n"
    
    # Группируем по дарксторам
    groups = {}
    for t in tickets_data:
        darks = t.get('darks', 'без номера')
        if darks not in groups:
            groups[darks] = {
                'address': t.get('address', 'Адрес не указан'),
                'tickets': []
            }
        groups[darks]['tickets'].append(t)
    
    for darks, group in groups.items():
        message += f"📍 {group['address']} (даркстор {darks})\n"
        message += f"📋 Заявки ({len(group['tickets'])}):\n"
        for t in group['tickets']:
            # Для АКБ и зарядок показываем тип вместо госномера
            if t.get('type') in ['Аккумуляторная батарея', 'Зарядное устройство']:
                identifier = t.get('type', 'Без номера')
            else:
                identifier = t.get('gos', 'Без номера')
            
            message += f"   {identifier} | {t.get('desc', '-')}\n"
        message += "\n"
    
    message += "Подготовьте, пожалуйста, технику к ремонту\n"
    message += "Хорошего дня! 🙌"
    
    return message

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
        role = MASTER_CREDENTIALS[login].get('role', 'master')
        return jsonify({
            'success': True, 
            'master': MASTER_CREDENTIALS[login]['name'], 
            'login': login,
            'role': role
        })
    return jsonify({'success': False, 'error': 'Неверный логин или пароль'})

@app.route('/auto_login/<login>')
def auto_login(login):
    if login in MASTER_CREDENTIALS:
        master_name = MASTER_CREDENTIALS[login]['name']
        role = MASTER_CREDENTIALS[login].get('role', 'master')
        session['master_login'] = login
        session['master_name'] = master_name
        session['authenticated'] = True
        session['role'] = role
        
        if role == 'admin':
            return redirect(url_for('admin_panel'))
        else:
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
    if session.get('role') != 'admin':
        return redirect(url_for('master_overview', name=session.get('master_name')))
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
        logger.error(f"Ошибка синхронизации: {e}")
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
            update_ticket_in_admin_cache(uid, 'todo', f'ЗАБРАЛИ: {extra} АКБ')
        else:
            return jsonify({'success': False, 'error': 'Неизвестное действие'})
        add_to_queue({
            'uid': uid,
            'source': 'Заявки',
            'type': action,
            'data': {'extra': extra}
        })
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/build_route_for_master')
@login_required
def api_build_route_for_master():
    master = request.args.get('master', '')
    if not master:
        return 'Нет мастера', 400
    
    darks_ref = load_darks_reference()
    
    cache_data = get_master_cache(master)
    if cache_data:
        master_tickets = cache_data.get('tickets', [])
    else:
        tickets = get_tickets_from_sheets()
        master_tickets = [t for t in tickets if t.get('master') == master and is_active_status(t.get('status'))]
    
    coords_set = set()
    for t in master_tickets:
        darks_num = t.get('darks')
        if darks_num and darks_num in darks_ref:
            coords = darks_ref[darks_num].get('coords', '')
            if coords:
                coords_set.add(coords)
    
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
    
    route_url = f'/api/build_route_for_master?master={name}'
    
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

@app.route('/master/<name>/history')
@login_required
def master_history(name):
    if session.get('master_name') != name:
        return redirect(url_for('login_page'))
    return render_template('master_history.html', name=name, now=get_msk_now().strftime('%H:%M:%S'))

@app.route('/api/master_history/<name>')
@login_required
def api_master_history(name):
    if session.get('master_name') != name and session.get('role') != 'admin':
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    history = read_cache(f"history_{name}.json") or []
    return jsonify({'success': True, 'history': history})

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
    
    add_to_queue({
        'uid': uid,
        'source': 'Заявки',
        'type': 'done',
        'data': {'parts': parts, 'master': name, 'darks_number': darks_number}
    })
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
    add_to_queue({
        'uid': uid,
        'source': 'Заявки',
        'type': 'fail',
        'data': {'master': name, 'darks_number': darks_number}
    })
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
    add_to_queue({
        'uid': uid,
        'source': 'Заявки',
        'type': 'evacuation',
        'data': {'reason': reason, 'master': name, 'darks_number': darks_number}
    })
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
    add_to_queue({
        'uid': uid,
        'source': 'Заявки',
        'type': 'taken_no_replace',
        'data': {'parts': parts, 'master': name, 'darks_number': darks_number}
    })
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
    add_to_queue({
        'uid': uid,
        'source': 'Заявки',
        'type': 'replace_yes',
        'data': {'parts': parts, 'master': name, 'darks_number': darks_number}
    })
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
    add_to_queue({
        'uid': uid,
        'source': 'Заявки',
        'type': 'replace_no',
        'data': {'master': name, 'darks_number': darks_number}
    })
    return jsonify({'success': True})

# ============================================================
# ТРАНЗИТ - ЗАМЕНА ВЕЛОСИПЕДА
# ============================================================
def write_transit_replacement(uid, master_name, darks_number, address, old_data, new_data):
    try:
        sheet_client = get_sheet_client()
        now = get_msk_now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            report_sheet = sheet_client.worksheet("Эвакуация Транзит")
        except:
            report_sheet = sheet_client.add_worksheet("Эвакуация Транзит", 100, 20)
            headers = ['Отметка времени', 'Адрес даркстора', 'Номер даркстора',
                      'ЗАБРАЛ - Серийный номер', 'ЗАБРАЛ - Гос номер', 'ЗАБРАЛ - Номер айот',
                      'ОТДАЛ - Серийный номер', 'ОТДАЛ - Гос номер', 'ОТДАЛ - Номер айот']
            for i, h in enumerate(headers, start=1):
                report_sheet.update_cell(1, i, h)
        current_rows = report_sheet.get_all_values()
        new_row_idx = len(current_rows) + 1
        updates = [
            {'range': f'A{new_row_idx}', 'values': [[now]]},
            {'range': f'B{new_row_idx}', 'values': [[address]]},
            {'range': f'C{new_row_idx}', 'values': [[darks_number]]},
            {'range': f'D{new_row_idx}', 'values': [[old_data.get('serial', '')]]},
            {'range': f'E{new_row_idx}', 'values': [[old_data.get('gos', '')]]},
            {'range': f'F{new_row_idx}', 'values': [[old_data.get('iot', '')]]},
            {'range': f'G{new_row_idx}', 'values': [[new_data.get('serial', '')]]},
            {'range': f'H{new_row_idx}', 'values': [[new_data.get('gos', '')]]},
            {'range': f'I{new_row_idx}', 'values': [[new_data.get('iot', '')]]}
        ]
        report_sheet.batch_update(updates)
        logger.info(f"✅ Записана замена велосипеда для заявки {uid}")
    except Exception as e:
        logger.error(f"Ошибка записи замены велосипеда: {e}")
        raise

@app.route('/master/transit/replace', methods=['POST'])
@login_required
def transit_replace():
    data = request.json
    uid = data.get('uid')
    master_name = data.get('master')
    darks_number = data.get('darks_number')
    address = data.get('address', '')
    old_data = data.get('old_data', {})
    new_data = data.get('new_data', {})
    try:
        write_transit_replacement(uid, master_name, darks_number, address, old_data, new_data)
        add_to_queue({
            'uid': uid,
            'source': 'Заявки',
            'type': 'transit_replace',
            'data': {'master': master_name, 'darks_number': darks_number}
        })
        return jsonify({'success': True, 'message': 'Замена выполнена'})
    except Exception as e:
        logger.error(f"Ошибка замены велосипеда: {e}")
        return jsonify({'success': False, 'error': str(e)})

# ============================================================
# ЗАПУСК
# ============================================================
if __name__ == "__main__":
    logger.info("🚀 Запуск приложения...")
    
    # Запускаем фоновый процесс
    background_thread = threading.Thread(target=process_queue_background, daemon=True)
    background_thread.start()
    logger.info("🚀 Фоновый процесс обработки очереди запущен")
    
    try:
        load_darks_reference()
        tickets = get_tickets_from_sheets()
        uid_index = build_uid_index(tickets)
        save_admin_cache(tickets)
        logger.info(f"✅ Кеш загружен: {len(tickets)} заявок")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки кеша: {e}")
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
