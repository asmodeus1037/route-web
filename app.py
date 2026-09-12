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
SHEET_NAME = "Система ремонта ВВ"
START_COORDS = "55.775267, 37.745690"
MASTERS = ['Антон', 'Сергей', 'Руслан', 'Сергей Транзит', 'Алексей']
CACHE_TTL = 300
CACHE_DIR = "/data/cache"
BOT_API_URL = "https://route-bot-dzufear.waw0.amvera.tech"

# IOT настройки
IOT_SOURCE_SHEET_ID = "1BoZ7GFmL2q56bjqt1CFVV_PeqnKVaeb3anTJkf0s6xA"
IOT_VEHICLES_SHEET_ID = "1s_hXPSWueMAo3W1pgLCG0eHhYKoW0uoIZ6CGWFf55VU"
IOT_REPORT_SHEET_ID = "1s_hXPSWueMAo3W1pgLCG0eHhYKoW0uoIZ6CGWFf55VU"

IOT_BAG_FILE = "iot_bag.json"
IOT_SOURCE_FILE = "iot_source.json"
IOT_HISTORY_FILE = "iot_history.json"

os.makedirs(CACHE_DIR, exist_ok=True)

MSK = pytz.timezone('Europe/Moscow')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MASTER_CREDENTIALS = {
    'admin': {'password': 'admin123', 'name': 'Админ', 'role': 'admin'},
    'anton': {'password': 'anton1987', 'name': 'Антон', 'role': 'master'},
    'sergey': {'password': 'sergey1992', 'name': 'Сергей', 'role': 'master'},
    'ruslan': {'password': 'ruslan1985', 'name': 'Руслан', 'role': 'master'},
    'transit': {'password': 'transit2024', 'name': 'Сергей Транзит', 'role': 'master'},
    'alexey': {'password': 'alexey0304', 'name': 'Алексей', 'role': 'master'},
    'iot': {'password': 'iot2026', 'name': 'IOT-Мастер', 'role': 'iot'}
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

def get_iot_sheet_by_id(sheet_id, sheet_name=None):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    if sheet_name:
        return spreadsheet.worksheet(sheet_name)
    return spreadsheet

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
                if len(row) < 14:
                    continue
                darks_num = row[0].strip()
                status_raw = row[7].strip() if len(row) > 7 else ''
                note = row[9].strip() if len(row) > 9 else ''
                
                if status_raw in ['Выполнено', '✅ Выполнено', 'done']:
                    status = 'done'
                elif status_raw in ['🔵 Доделать', 'Доделать']:
                    status = 'todo'
                elif status_raw in ['⏹️ Обработано', 'Обработано', 'Вело отсутствует']:
                    status = 'fail'
                elif status_raw in ['🔧 Эвакуация', 'Эвакуация']:
                    status = 'todo'
                    if note:
                        note = f'ЭВАКУАЦИЯ: {note}'
                    else:
                        desc = row[2].strip() if len(row) > 2 else ''
                        note = f'ЭВАКУАЦИЯ: {desc}'
                else:
                    status = 'pending'
                
                is_done = status in ['done', 'fail']
                created_str = row[5].strip() if len(row) > 5 else ''
                hours_since = get_hours_since(created_str)
                sent_date = row[13].strip() if len(row) > 13 else ''
                bike_type = row[1].strip() if len(row) > 1 else ''
                bike_subtype = row[8].strip() if len(row) > 8 else ''
                if bike_type == 'Электровелосипед' and bike_subtype:
                    display_type = bike_subtype
                else:
                    display_type = bike_type
                uid = row[10].strip() if len(row) > 10 else ''
                if not uid and created_str:
                    uid = generate_ticket_id(created_str)
                    if uid:
                        try:
                            worksheet.update_cell(idx, 11, uid)
                        except:
                            pass
                if not row[7].strip():
                    try:
                        worksheet.update_cell(idx, 8, '🟡 В работе')
                        status = 'pending'
                    except:
                        pass
                direction = row[11].strip() if len(row) > 11 else ''
                if not direction and darks_num in darks_ref:
                    direction = darks_ref[darks_num].get('direction', '')
                display_desc = row[2].strip() if len(row) > 2 else ''
                if status == 'todo' and note and 'ЗАБРАЛИ:' in note:
                    match = re.search(r'ЗАБРАЛИ:\s*(\d+)', note)
                    if match:
                        count = match.group(1)
                        display_desc = f'Вернуть {count} АКБ (забирали на ремонт)'
                
                if status == 'todo' and note and note.startswith('ЭВАКУАЦИЯ:'):
                    display_desc = note.replace('ЭВАКУАЦИЯ: ', '')
                
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
                    'parts': row[9].strip() if len(row) > 9 else '',
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
                note = row[12].strip() if len(row) > 12 else ''
                
                if status_raw == 'Решено' or status_l == 'Выполнено':
                    status = 'done'
                    is_done = True
                elif status_l == 'Эвакуация':
                    status = 'todo'
                    is_done = False
                    if note:
                        note = f'ЭВАКУАЦИЯ: {note}'
                else:
                    status = 'pending'
                    is_done = False
                created_str = row[6].strip() if len(row) > 6 else ''
                hours_since = get_hours_since(created_str)
                sent_date = row[13].strip() if len(row) > 13 else ''
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
                    'note': note,
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

def clear_all_masters():
    try:
        sheet_client = get_sheet_client()
        cleared = 0
        updates_zayavki = []
        updates_import = []
        
        worksheet = sheet_client.worksheet("Заявки")
        all_rows = worksheet.get_all_values()
        for idx, row in enumerate(all_rows, start=1):
            if idx == 1:
                continue
            if len(row) > 7:
                status = row[7].strip() if len(row) > 7 else ''
                if status in ['🟡 В работе', '🔵 Доделать', 'В работе', 'Доделать', 'pending', 'todo']:
                    current_master = row[6].strip() if len(row) > 6 else ''
                    if current_master:
                        updates_zayavki.append({'range': f'G{idx}', 'values': [['']]})
                        cleared += 1
        
        try:
            worksheet_import = sheet_client.worksheet("Импорт М4")
            import_rows = worksheet_import.get_all_values()
            for idx, row in enumerate(import_rows, start=1):
                if idx == 1:
                    continue
                if len(row) > 14:
                    status = row[11].strip() if len(row) > 11 else ''
                    if status in ['В работе', 'Доделать', 'pending', 'todo']:
                        current_master = row[13].strip() if len(row) > 13 else ''
                        if current_master:
                            updates_import.append({'range': f'N{idx}', 'values': [['']]})
                            cleared += 1
        except:
            pass
        
        if updates_zayavki:
            worksheet.batch_update(updates_zayavki)
            logger.info(f"✅ Снято {len(updates_zayavki)} мастеров в 'Заявки'")
        
        if updates_import:
            worksheet_import.batch_update(updates_import)
            logger.info(f"✅ Снято {len(updates_import)} мастеров в 'Импорт М4'")
        
        tickets = get_tickets_from_sheets()
        uid_index = build_uid_index(tickets)
        save_admin_cache(tickets)
        refresh_all_master_caches()
        
        return cleared
    except Exception as e:
        logger.error(f"Ошибка снятия всех мастеров: {e}")
        return 0

def update_status_in_google_sheets(uid, status, note=''):
    try:
        sheet_client = get_sheet_client()
        
        found = find_uid_in_sheets(uid)
        if not found:
            logger.error(f"UID {uid} не найден в Google Sheets")
            return False
        
        row_idx = found['row_index']
        source = found['source']
        
        if source == 'Заявки':
            worksheet = sheet_client.worksheet("Заявки")
            worksheet.update_cell(row_idx, 8, status)
            if note:
                worksheet.update_cell(row_idx, 10, note)
            logger.info(f"✅ Обновлён статус заявки {uid} в 'Заявки': {status}")
            
        elif source == 'Импорт М4':
            worksheet = sheet_client.worksheet("Импорт М4")
            status_map = {
                '🟡 В работе': 'В работе',
                '✅ Выполнено': 'Выполнено',
                '🔵 Доделать': 'Доделать',
                '🔧 Эвакуация': 'Эвакуация'
            }
            new_status = status_map.get(status, 'В работе')
            worksheet.update_cell(row_idx, 12, new_status)
            if note:
                worksheet.update_cell(row_idx, 13, note)
            logger.info(f"✅ Обновлён статус заявки {uid} в 'Импорт М4': {new_status}")
        
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления статуса в Google Sheets: {e}")
        return False

def find_uid_in_sheets(uid):
    try:
        sheet_client = get_sheet_client()
        
        worksheet = sheet_client.worksheet("Заявки")
        cell = worksheet.find(uid)
        if cell:
            return {'source': 'Заявки', 'row_index': cell.row}
        
        worksheet = sheet_client.worksheet("Импорт М4")
        cell = worksheet.find(uid)
        if cell:
            return {'source': 'Импорт М4', 'row_index': cell.row}
        
        return None
    except Exception as e:
        logger.error(f"Ошибка поиска UID {uid}: {e}")
        return None

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

# ============================================================
# ЗАПИСЬ В "ОТЧЕТ МАСТЕРА"
# ============================================================
def write_to_report(tasks):
    try:
        sheet_client = get_sheet_client()
        now = get_msk_now().strftime('%Y-%m-%d %H:%M:%S')
        
        try:
            report_sheet = sheet_client.worksheet("Отчет мастера")
        except:
            report_sheet = sheet_client.add_worksheet("Отчет мастера", 100, 20)
            headers = ['Дата выполнения', 'Мастер', 'ID заявки', 'Госномер', 'Описание', 
                      'Тип техники', 'Количество', 'Статус', 'Запчасти', 'Комментарий', 
                      'Номер даркстора', 'Время создания заявки', 'Статус обработки']
            for i, h in enumerate(headers, start=1):
                report_sheet.update_cell(1, i, h)
        
        current_rows = report_sheet.get_all_values()
        start_row = len(current_rows) + 1
        
        updates_report = []
        
        for idx, task in enumerate(tasks):
            uid = task.get('uid')
            task_type = task.get('type')
            data = task.get('data', {})
            master = data.get('master', '')
            darks_number = data.get('darks_number', '')
            parts = data.get('parts', '')
            reason = data.get('reason', '')
            extra = data.get('extra', '')
            
            ticket = None
            if uid in uid_index:
                ticket = uid_index[uid]['ticket']
            
            if not ticket:
                logger.warning(f"❌ Заявка {uid} не найдена в кэше, пропускаем")
                continue
            
            status_map = {
                'done': '✅ Выполнено',
                'fail': '🔵 Доделать',
                'evacuation': '🔧 Эвакуация',
                'replace_yes': '✅ Выполнено',
                'taken_no_replace': '🔵 Доделать',
                'replace_no': '🔵 Доделать',
                'transit_replace': '✅ Выполнено'
            }
            status = status_map.get(task_type, '✅ Выполнено')
            
            if ticket.get('type') in ['Аккумуляторная батарея', 'Зарядное устройство']:
                quantity = parts or extra or '1'
            else:
                quantity = '1'
            
            row_idx = start_row + idx
            
            updates_report.append({'range': f'A{row_idx}', 'values': [[now]]})
            updates_report.append({'range': f'B{row_idx}', 'values': [[master]]})
            updates_report.append({'range': f'C{row_idx}', 'values': [[uid]]})
            updates_report.append({'range': f'D{row_idx}', 'values': [[ticket.get('gos', '')]]})
            updates_report.append({'range': f'E{row_idx}', 'values': [[ticket.get('desc', '')]]})
            updates_report.append({'range': f'F{row_idx}', 'values': [[ticket.get('type', '')]]})
            updates_report.append({'range': f'G{row_idx}', 'values': [[quantity]]})
            updates_report.append({'range': f'H{row_idx}', 'values': [[status]]})
            updates_report.append({'range': f'I{row_idx}', 'values': [[parts or extra or '-']]})
            updates_report.append({'range': f'J{row_idx}', 'values': [[reason or '-']]})
            updates_report.append({'range': f'K{row_idx}', 'values': [[darks_number or ticket.get('darks', '')]]})
            updates_report.append({'range': f'L{row_idx}', 'values': [[ticket.get('created', '')]]})
            updates_report.append({'range': f'M{row_idx}', 'values': [['Новый']]})
        
        if updates_report:
            report_sheet.batch_update(updates_report)
            logger.info(f"✅ Записано {len(updates_report)//13} записей в Отчет мастера")
        
    except Exception as e:
        logger.error(f"❌ Ошибка записи в Отчет мастера: {e}")
        raise

# ============================================================
# ФОНОВЫЙ ПРОЦЕСС ОБРАБОТКИ ОЧЕРЕДИ
# ============================================================
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
            
            tasks_for_report = []
            tasks_skip_report = []
            
            for task in tasks:
                data = task.get('data', {})
                skip_report = data.get('skip_report', False)
                
                if skip_report:
                    tasks_skip_report.append(task)
                else:
                    tasks_for_report.append(task)
            
            for task in tasks_skip_report:
                uid = task.get('uid')
                task_type = task.get('type')
                data = task.get('data', {})
                
                if task_type == 'status_update':
                    new_status = data.get('new_status', 'pending')
                    status_display = data.get('status', '🟡 В работе')
                    note = data.get('note', '')
                    update_ticket_in_admin_cache(uid, new_status, note, status_display)
            
            if tasks_for_report:
                write_to_report(tasks_for_report)
            
            if tasks:
                clear_queue()
                last_gs_sync = time.time()
                logger.info("✅ Очередь очищена")
            
        except Exception as e:
            logger.error(f"❌ Ошибка в фоновом процессе: {e}")

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
    now = get_msk_now().strftime('%d.%m.%Y %H:%M')
    message = f"📢 Привет, на связи Vanta Bikes! ({now})\n\n"
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
        elif role == 'iot':
            return redirect(url_for('iot_main'))
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
    global uid_index, darks_ref
    try:
        logger.info("🔄 Начинаем синхронизацию с Google Sheets...")
        darks_ref = load_darks_reference()
        tickets = get_tickets_from_sheets()
        uid_index = build_uid_index(tickets)
        save_admin_cache(tickets)
        refresh_all_master_caches()
        logger.info(f"✅ Синхронизация завершена: {len(tickets)} заявок")
        return jsonify({'success': True, 'tickets': tickets, 'count': len(tickets)})
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
    cleared = clear_all_masters()
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

@app.route('/api/update_status', methods=['POST'])
@login_required
def api_update_status():
    data = request.json
    uid = data.get('uid')
    status_display = data.get('status')
    note = data.get('note', '')
    skip_report = data.get('skip_report', True)
    
    if not uid or not status_display:
        return jsonify({'success': False, 'error': 'Недостаточно данных'})
    
    try:
        status_map = {
            '🟡 В работе': 'pending',
            '✅ Выполнено': 'done',
            '🔵 Доделать': 'todo',
            '🔧 Эвакуация': 'evacuation'
        }
        
        new_status = status_map.get(status_display, 'pending')
        
        if status_display == '🔧 Эвакуация' and not note:
            tickets = get_tickets_from_sheets()
            for t in tickets:
                if t.get('uid') == uid:
                    note = t.get('desc', 'Эвакуация')
                    break
        
        update_ticket_in_admin_cache(uid, new_status, note, status_display)
        update_status_in_google_sheets(uid, status_display, note)
        
        add_to_queue({
            'uid': uid,
            'source': 'Заявки',
            'type': 'status_update',
            'data': {
                'status': status_display,
                'new_status': new_status,
                'note': note,
                'skip_report': skip_report
            }
        })
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/bulk_update_status', methods=['POST'])
@login_required
def api_bulk_update_status():
    try:
        data = request.json
        uids = data.get('uids', [])
        status_display = data.get('status', '')
        comment = data.get('comment', '')
        skip_report = data.get('skip_report', True)
        
        if not uids or not status_display:
            return jsonify({'success': False, 'error': 'Не указаны UID или статус'}), 400
        
        all_tickets = get_tickets_from_sheets()
        tickets_by_uid = {t.get('uid'): t for t in all_tickets}
        
        updates_zayavki = []
        updates_import = []
        updated_count = 0
        
        for uid in uids:
            found = find_uid_in_sheets(uid)
            if not found:
                continue
            
            row_idx = found['row_index']
            source = found['source']
            
            final_comment = comment
            if status_display == '🔧 Эвакуация':
                ticket = tickets_by_uid.get(uid)
                if ticket:
                    final_comment = ticket.get('desc', 'Эвакуация')
                else:
                    final_comment = 'Эвакуация'
            
            if source == 'Заявки':
                updates_zayavki.append({'range': f'H{row_idx}', 'values': [[status_display]]})
                if final_comment:
                    updates_zayavki.append({'range': f'J{row_idx}', 'values': [[final_comment]]})
                updated_count += 1
            elif source == 'Импорт М4':
                status_map_import = {
                    '🟡 В работе': 'В работе',
                    '✅ Выполнено': 'Выполнено',
                    '🔵 Доделать': 'Доделать',
                    '🔧 Эвакуация': 'Эвакуация'
                }
                new_status_import = status_map_import.get(status_display, 'В работе')
                updates_import.append({'range': f'L{row_idx}', 'values': [[new_status_import]]})
                if final_comment:
                    updates_import.append({'range': f'M{row_idx}', 'values': [[final_comment]]})
                updated_count += 1
        
        sheet_client = get_sheet_client()
        
        if updates_zayavki:
            worksheet = sheet_client.worksheet("Заявки")
            worksheet.batch_update(updates_zayavki)
            logger.info(f"✅ Обновлено {len(updates_zayavki)} ячеек в 'Заявки'")
        
        if updates_import:
            worksheet = sheet_client.worksheet("Импорт М4")
            worksheet.batch_update(updates_import)
            logger.info(f"✅ Обновлено {len(updates_import)} ячеек в 'Импорт М4'")
        
        tickets = get_tickets_from_sheets()
        save_admin_cache(tickets)
        
        global uid_index
        uid_index = build_uid_index(tickets)
        
        refresh_all_master_caches()
        
        return jsonify({
            'success': True,
            'updated': updated_count,
            'message': f'Обновлено {updated_count} заявок'
        })
        
    except Exception as e:
        logger.error(f'Ошибка при массовом обновлении статусов: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin_action', methods=['POST'])
@login_required
def api_admin_action():
    data = request.json
    uid = data.get('uid')
    action = data.get('action')
    extra = data.get('extra', '')
    skip_report = data.get('skip_report', True)
    if not uid or not action:
        return jsonify({'success': False, 'error': 'Недостаточно данных'})
    try:
        if action == 'done':
            status_display = '✅ Выполнено'
            update_ticket_in_admin_cache(uid, 'done', extra or 'Выполнено админом', status_display)
            update_status_in_google_sheets(uid, status_display, extra)
        elif action == 'evacuation':
            status_display = '🔧 Эвакуация'
            tickets = get_tickets_from_sheets()
            desc = ''
            for t in tickets:
                if t.get('uid') == uid:
                    desc = t.get('desc', 'Эвакуация')
                    break
            update_ticket_in_admin_cache(uid, 'todo', f'ЭВАКУАЦИЯ: {desc}', status_display)
            update_status_in_google_sheets(uid, status_display, desc)
        elif action == 'fail':
            status_display = '🔵 Доделать'
            update_ticket_in_admin_cache(uid, 'fail', 'Вело отсутствует', status_display)
            update_status_in_google_sheets(uid, status_display, 'Вело отсутствует')
        elif action == 'todo':
            status_display = '🔵 Доделать'
            update_ticket_in_admin_cache(uid, 'todo', 'Доделать', status_display)
            update_status_in_google_sheets(uid, status_display, 'Доделать')
        elif action == 'taken':
            status_display = '🔵 Доделать'
            note = f'ЗАБРАЛИ: {extra} АКБ'
            update_ticket_in_admin_cache(uid, 'todo', note, status_display)
            update_status_in_google_sheets(uid, status_display, note)
        else:
            return jsonify({'success': False, 'error': 'Неизвестное действие'})
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
    
    if name == 'Сергей Транзит':
        gos_counts = {}
        for t in filtered:
            gos = t.get('gos')
            if gos:
                if gos not in gos_counts:
                    gos_counts[gos] = 0
                gos_counts[gos] += 1
        for t in filtered:
            gos = t.get('gos')
            if gos and gos in gos_counts:
                t['duplicate_count'] = gos_counts[gos]
    
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
    logger.info(f"📝 master_done вызван: name={name}, uid={uid}")
    if session.get('master_name') != name:
        logger.warning(f"❌ Сессия не совпадает: {session.get('master_name')} != {name}")
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
# ФУНКЦИИ ДЛЯ ЭВАКУАЦИИ
# ============================================================
def find_bike_in_database(gos_number, darks_number):
    try:
        sheet_client = get_sheet_client()
        worksheet = sheet_client.worksheet("База данных вело")
        rows = worksheet.get_all_values()
        
        if len(rows) <= 1:
            return None
        
        gos_clean = re.sub(r'[^0-9A-Za-zА-Яа-я]', '', gos_number).upper() if gos_number else ''
        
        for row in rows[1:]:
            if len(row) >= 7:
                gos = row[1].strip() if len(row) > 1 else ''
                darks = row[4].strip() if len(row) > 4 else ''
                gos_digits = re.sub(r'[^0-9A-Za-zА-Яа-я]', '', gos).upper()
                
                if gos and darks:
                    if (gos == gos_number or (gos_clean and gos_digits == gos_clean)) and darks == darks_number:
                        return {
                            'serial': row[0].strip() if len(row) > 0 else '',
                            'gos': gos,
                            'iot': row[2].strip() if len(row) > 2 else '',
                            'address': row[3].strip() if len(row) > 3 else '',
                            'darks': darks,
                            'bike_type': row[5].strip() if len(row) > 5 else '',
                            'direction': row[6].strip() if len(row) > 6 else ''
                        }
        return None
    except Exception as e:
        logger.error(f"Ошибка поиска велосипеда в БД: {e}")
        return None

@app.route('/api/get_bike_data')
@login_required
def api_get_bike_data():
    try:
        gos = request.args.get('gos', '')
        darks = request.args.get('darks', '')
        
        if not gos:
            return jsonify({'success': False, 'error': 'Не указан госномер'})
        
        bike_data = find_bike_in_database(gos, darks)
        if bike_data:
            return jsonify({'success': True, 'data': bike_data})
        return jsonify({'success': False, 'error': 'Велосипед не найден'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

def write_evacuation_to_sheet(uid, master_name, darks_number, address, old_data, new_data):
    try:
        sheet_client = get_sheet_client()
        now = get_msk_now().strftime('%Y-%m-%d %H:%M:%S')
        
        try:
            report_sheet = sheet_client.worksheet("Эвакуация Транзит")
        except:
            report_sheet = sheet_client.add_worksheet("Эвакуация Транзит", 100, 20)
            headers = ['Отметка времени', 'Мастер', 'ID заявки', 'Адрес даркстора', 'Номер даркстора',
                      'ЗАБРАЛ - Серийный номер', 'ЗАБРАЛ - Гос номер', 'ЗАБРАЛ - Номер айот',
                      'ОТДАЛ - Серийный номер', 'ОТДАЛ - Гос номер', 'ОТДАЛ - Номер айот', 'Тип']
            for i, h in enumerate(headers, start=1):
                report_sheet.update_cell(1, i, h)
        
        current_rows = report_sheet.get_all_values()
        new_row_idx = len(current_rows) + 1
        
        updates = [
            {'range': f'A{new_row_idx}', 'values': [[now]]},
            {'range': f'B{new_row_idx}', 'values': [[master_name]]},
            {'range': f'C{new_row_idx}', 'values': [[uid]]},
            {'range': f'D{new_row_idx}', 'values': [[address]]},
            {'range': f'E{new_row_idx}', 'values': [[darks_number]]},
            {'range': f'F{new_row_idx}', 'values': [[old_data.get('serial', '')]]},
            {'range': f'G{new_row_idx}', 'values': [[old_data.get('gos', '')]]},
            {'range': f'H{new_row_idx}', 'values': [[old_data.get('iot', '')]]},
            {'range': f'I{new_row_idx}', 'values': [[new_data.get('serial', '')]]},
            {'range': f'J{new_row_idx}', 'values': [[new_data.get('gos', '')]]},
            {'range': f'K{new_row_idx}', 'values': [[new_data.get('iot', '')]]},
            {'range': f'L{new_row_idx}', 'values': [['Эвакуация']]}
        ]
        report_sheet.batch_update(updates)
        logger.info(f"✅ Записана эвакуация для заявки {uid}")
        return True
    except Exception as e:
        logger.error(f"Ошибка записи эвакуации: {e}")
        return False

@app.route('/master/evacuation/replace', methods=['POST'])
@login_required
def master_evacuation_replace():
    try:
        data = request.json
        uid = data.get('uid')
        master_name = data.get('master')
        darks_number = data.get('darks_number')
        address = data.get('address', '')
        old_data = data.get('old_data', {})
        new_data = data.get('new_data', {})
        
        if not uid or not master_name or not darks_number:
            return jsonify({'success': False, 'error': 'Недостаточно данных'}), 400
        
        if not old_data.get('serial') or not old_data.get('gos') or not old_data.get('iot'):
            return jsonify({'success': False, 'error': 'Заполните все поля СТАРОГО велосипеда'}), 400
        
        if not new_data.get('serial') or not new_data.get('gos') or not new_data.get('iot'):
            return jsonify({'success': False, 'error': 'Заполните все поля НОВОГО велосипеда'}), 400
        
        write_evacuation_to_sheet(uid, master_name, darks_number, address, old_data, new_data)
        
        update_ticket_in_admin_cache(uid, 'done', 'Заменен при эвакуации', '✅ Выполнено')
        update_status_in_google_sheets(uid, '✅ Выполнено', 'Заменен при эвакуации')
        
        add_to_queue({
            'uid': uid,
            'source': 'Заявки',
            'type': 'evacuation_and_replace',
            'data': {
                'master': master_name,
                'darks_number': darks_number,
                'old_data': old_data,
                'new_data': new_data
            }
        })
        
        cache_data = get_master_cache(master_name)
        if cache_data:
            tickets = cache_data.get('tickets', [])
            tickets = [t for t in tickets if t.get('uid') != uid]
            save_master_cache(master_name, tickets, '')
        
        return jsonify({'success': True, 'message': 'Эвакуация выполнена'})
        
    except Exception as e:
        logger.error(f"Ошибка эвакуации: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================
# ФУНКЦИИ ДЛЯ ТРАНЗИТА (ОБЪЕДИНЕНИЕ ЗАЯВОК)
# ============================================================
def get_transit_tickets_by_gos(master_name, gos_number):
    try:
        tickets = get_tickets_from_sheets()
        result = []
        for t in tickets:
            if (t.get('master') == master_name and 
                t.get('gos') == gos_number and 
                is_active_status(t.get('status'))):
                result.append(t)
        return result
    except Exception as e:
        logger.error(f"Ошибка получения заявок Транзита: {e}")
        return []

def close_all_transit_tickets(uid, master_name, gos_number, parts=''):
    try:
        tickets = get_transit_tickets_by_gos(master_name, gos_number)
        
        if not tickets:
            return 0
        
        closed_count = 0
        
        for t in tickets:
            ticket_uid = t.get('uid')
            if not ticket_uid:
                continue
            
            update_ticket_in_admin_cache(ticket_uid, 'done', parts or 'Заменено Транзитом', '✅ Выполнено')
            update_status_in_google_sheets(ticket_uid, '✅ Выполнено', parts or 'Заменено Транзитом')
            
            add_to_queue({
                'uid': ticket_uid,
                'source': 'Заявки',
                'type': 'transit_bulk_close',
                'data': {
                    'master': master_name,
                    'gos': gos_number,
                    'parts': parts,
                    'closed_with': uid
                }
            })
            closed_count += 1
        
        refresh_master_cache(master_name)
        return closed_count
        
    except Exception as e:
        logger.error(f"Ошибка закрытия заявок Транзита: {e}")
        return 0

@app.route('/master/transit/done/<uid>', methods=['POST'])
@login_required
def transit_done(uid):
    try:
        master_name = session.get('master_name')
        if master_name != 'Сергей Транзит':
            return jsonify({'success': False, 'error': 'Только для Сергея Транзита'}), 403
        
        parts = request.form.get('parts', '')
        if not parts.strip():
            return jsonify({'success': False, 'error': 'Укажите запчасти'}), 400
        
        tickets = get_tickets_from_sheets()
        target_ticket = None
        for t in tickets:
            if t.get('uid') == uid:
                target_ticket = t
                break
        
        if not target_ticket:
            return jsonify({'success': False, 'error': 'Заявка не найдена'}), 404
        
        gos_number = target_ticket.get('gos')
        if not gos_number:
            return jsonify({'success': False, 'error': 'У заявки нет госномера'}), 400
        
        closed_count = close_all_transit_tickets(uid, master_name, gos_number, parts)
        
        if closed_count == 0:
            return jsonify({'success': False, 'error': 'Не найдено заявок для закрытия'}), 404
        
        return jsonify({
            'success': True, 
            'message': f'Закрыто {closed_count} заявок на велосипед {gos_number}',
            'closed_count': closed_count
        })
        
    except Exception as e:
        logger.error(f"Ошибка закрытия заявок Транзита: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================
# IOT-СИСТЕМА (ЗАМЕНА IOT-МОДУЛЕЙ)
# ============================================================

def load_iot_source():
    """Загружает список IOT из листа 'Все IoT' и кэширует"""
    try:
        sheet = get_iot_sheet_by_id(IOT_SOURCE_SHEET_ID, "Все IoT")
        rows = sheet.get_all_values()
        
        iot_data = {}
        if len(rows) > 1:
            for row in rows[1:]:
                if len(row) >= 9:
                    iot = row[0].strip()
                    if not iot:
                        continue
                    status_velo = row[7].strip() if len(row) > 7 else ''
                    status_iot = row[8].strip() if len(row) > 8 else ''
                    # Читаем столбец J (индекс 9) - там запись о замене
                    replaced_text = row[9].strip() if len(row) > 9 else ''
                    iot_data[iot] = {
                        'status_velo': status_velo,
                        'status_iot': status_iot,
                        'replaced_text': replaced_text
                    }
        
        cache_data = {
            'updated_at': get_msk_now().strftime('%Y-%m-%d %H:%M:%S'),
            'iot_list': iot_data
        }
        write_cache(IOT_SOURCE_FILE, cache_data)
        logger.info(f"✅ IOT Source загружен: {len(iot_data)} записей")
        return iot_data
    except Exception as e:
        logger.error(f"Ошибка загрузки IOT Source: {e}")
        return {}

def get_iot_source():
    """Возвращает кэш IOT Source"""
    cache = read_cache(IOT_SOURCE_FILE)
    if not cache or 'iot_list' not in cache:
        return load_iot_source()
    return cache.get('iot_list', {})


def load_iot_vehicles():
    """Загружает данные велосипедов из таблицы №2 (лист 'Учет вело ВВ')"""
    try:
        sheet = get_iot_sheet_by_id(IOT_VEHICLES_SHEET_ID, "Учет вело ВВ")
        rows = sheet.get_all_values()
        
        vehicles = {}
        if len(rows) > 1:
            for row in rows[1:]:
                if len(row) >= 5:
                    frame_number = row[0].strip()
                    gos = row[1].strip()
                    iot = row[2].strip()
                    address = row[3].strip()
                    darks = row[4].strip()
                    
                    if iot:
                        vehicles[iot] = {
                            'frame_number': frame_number,
                            'gos': gos,
                            'iot': iot,
                            'address': address,
                            'darks': darks
                        }
        
        logger.info(f"✅ IOT Vehicles загружен: {len(vehicles)} записей")
        return vehicles
    except Exception as e:
        logger.error(f"Ошибка загрузки IOT Vehicles: {e}")
        return {}


def get_iot_bag():
    cache = read_cache(IOT_BAG_FILE)
    if not cache:
        return {'bag': []}
    return cache


def save_iot_bag(bag_data):
    return write_cache(IOT_BAG_FILE, bag_data)


def get_iot_history():
    cache = read_cache(IOT_HISTORY_FILE)
    if not cache:
        return {'history': []}
    return cache


def save_iot_history(history_data):
    return write_cache(IOT_HISTORY_FILE, history_data)

def update_iot_source_column_j(old_iot, new_iot):
    """Записывает в столбец J листа 'Все IoT' информацию о замене (в строку СТАРОГО IOT)"""
    try:
        sheet = get_iot_sheet_by_id(IOT_SOURCE_SHEET_ID, "Все IoT")
        rows = sheet.get_all_values()
        
        # Ищем строку со старым IOT
        for idx, row in enumerate(rows, start=1):
            if len(row) > 0 and row[0].strip() == old_iot:
                now = get_msk_now()
                date_str = now.strftime('%d.%m')
                text = f"{date_str} поменяли на прошитый (новый IOT: {new_iot})"
                sheet.update_cell(idx, 10, text)  # Столбец J = 10
                logger.info(f"✅ Запись в J для {old_iot}: {text}")
                return True
        
        logger.warning(f"⚠️ IOT {old_iot} не найден в листе 'Все IoT'")
        return False
    except Exception as e:
        logger.error(f"Ошибка записи в столбец J: {e}")
        return False


def write_iot_report(frame_number, new_iot, old_iot=''):
    """Записывает отчёт в лист 'КОРРЕКТИРОВКИ ВЕЛО'"""
    try:
        sheet = get_iot_sheet_by_id(IOT_REPORT_SHEET_ID, "КОРРЕКТИРОВКИ ВЕЛО")
        
        now = get_msk_now()
        date_str = now.strftime('%d.%m')
        
        all_values = sheet.get_all_values()
        new_row = len(all_values) + 1
        
        sheet.update(f'A{new_row}:F{new_row}', [[
            date_str,
            'Изменить IOT',
            frame_number,
            new_iot,
            '',
            'IOT с прошивкой'
        ]])
        
        logger.info(f"✅ Отчёт записан: {frame_number} → {new_iot}")
        return True
    except Exception as e:
        logger.error(f"Ошибка записи отчёта IOT: {e}")
        return False
    

@app.route('/iot')
@login_required
def iot_main():
    """Главная страница IOT-мастера"""
    if session.get('role') != 'iot':
        return redirect(url_for('login_page'))
    
    bag_data = get_iot_bag()
    bag_items = [item.get('iot') for item in bag_data.get('bag', [])]
    
    history_data = get_iot_history()
    today = get_msk_now().strftime('%d.%m')
    replaced_today = 0
    for h in history_data.get('history', []):
        if h.get('date') == today:
            replaced_today += 1
    
    if not bag_items:
        return render_template('iot_empty.html', now=get_msk_now().strftime('%H:%M:%S'))
    
    vehicles_data = load_iot_vehicles()
    source_data = get_iot_source()
    
    darks_groups = {}
    for iot, info in source_data.items():
        if (info.get('status_velo') == 'В аренде' 
            and info.get('status_iot') == 'Требует перепрошивки'
            and not info.get('replaced_text')):
            if iot in vehicles_data:
                v = vehicles_data[iot]
                darks = v.get('darks', 'без номера')
                if darks not in darks_groups:
                    darks_groups[darks] = {
                        'darks_number': darks,
                        'address': v.get('address', 'Адрес не указан'),
                        'vehicles_count': 0,
                        'preview': []
                    }
                darks_groups[darks]['vehicles_count'] += 1
                if len(darks_groups[darks]['preview']) < 3:
                    darks_groups[darks]['preview'].append({
                        'gos': v.get('gos', ''),
                        'old_iot': iot
                    })
    
    darks_list = list(darks_groups.values())
    darks_list.sort(key=lambda x: int(x['darks_number']) if x['darks_number'].isdigit() else 999999)
    
    return render_template('iot_main.html',
                          bag_items=bag_items,
                          bag_count=len(bag_items),
                          replaced_today=replaced_today,
                          darks_groups=darks_list,
                          now=get_msk_now().strftime('%H:%M:%S'))



@app.route('/iot/darks/<darks_number>')
@login_required
def iot_darks(darks_number):
    """Страница даркстора для IOT-мастера"""
    if session.get('role') != 'iot':
        return redirect(url_for('login_page'))
    
    bag_data = get_iot_bag()
    bag_items = [item.get('iot') for item in bag_data.get('bag', [])]
    
    vehicles_data = load_iot_vehicles()
    source_data = get_iot_source()
    
    vehicles = []
    address = ''
    for iot, info in source_data.items():
        if (info.get('status_velo') == 'В аренде' 
            and info.get('status_iot') == 'Требует перепрошивки'
            and not info.get('replaced_text')):
            if iot in vehicles_data:
                v = vehicles_data[iot]
                if v.get('darks') == darks_number:
                    if not address:
                        address = v.get('address', '')
                    vehicles.append({
                        'old_iot': iot,
                        'frame_number': v.get('frame_number', ''),
                        'gos': v.get('gos', ''),
                        'address': v.get('address', ''),
                        'darks': v.get('darks', '')
                    })
    
    return render_template('iot_darks.html',
                          darks_number=darks_number,
                          address=address or 'Адрес не указан',
                          vehicles=vehicles,
                          bag_items=bag_items,
                          bag_count=len(bag_items),
                          now=get_msk_now().strftime('%H:%M:%S'))


@app.route('/iot/history')
@login_required
def iot_history_page():
    """Страница истории замен"""
    if session.get('role') != 'iot':
        return redirect(url_for('login_page'))
    
    history_data = get_iot_history()
    history = history_data.get('history', [])
    
    history_by_darks = {}
    for h in reversed(history):
        darks = h.get('darks', 'без номера')
        if darks not in history_by_darks:
            history_by_darks[darks] = []
        history_by_darks[darks].append(h)
    
    return render_template('iot_history.html',
                          history_by_darks=history_by_darks,
                          now=get_msk_now().strftime('%H:%M:%S'))


@app.route('/api/iot/load_bag', methods=['POST'])
@login_required
def api_iot_load_bag():
    """Загружает IOT в багажник БЕЗ проверок - можно добавить любой IOT"""
    if session.get('role') != 'iot':
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    
    try:
        data = request.json
        iots = data.get('iots', [])
        
        if not iots:
            return jsonify({'success': False, 'error': 'Пустой список'})
        
        bag_data = get_iot_bag()
        current_bag = [item.get('iot') for item in bag_data.get('bag', [])]
        
        added = []
        duplicates = []
        seen_in_input = set()
        
        for iot in iots:
            iot = iot.strip()
            if not iot:
                continue
            
            if iot in seen_in_input:
                continue
            seen_in_input.add(iot)
            
            if iot in current_bag:
                duplicates.append(iot)
                continue
            
            added.append(iot)
        
        for iot in added:
            bag_data['bag'].append({
                'iot': iot,
                'added': get_msk_now().strftime('%Y-%m-%d %H:%M:%S')
            })
        save_iot_bag(bag_data)
        
        return jsonify({
            'success': True,
            'added': added,
            'errors': [],
            'duplicates': duplicates,
            'total_input': len(seen_in_input)
        })
        
    except Exception as e:
        logger.error(f"Ошибка загрузки багажника: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/iot/remove_from_bag', methods=['POST'])
@login_required
def api_iot_remove_from_bag():
    """Удаляет один IOT из багажника"""
    if session.get('role') != 'iot':
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    
    try:
        data = request.json
        iot = data.get('iot', '').strip()
        
        if not iot:
            return jsonify({'success': False, 'error': 'Не указан IOT'})
        
        bag_data = get_iot_bag()
        current_bag = bag_data.get('bag', [])
        
        # Проверяем, есть ли этот IOT в багажнике
        if not any(item.get('iot') == iot for item in current_bag):
            return jsonify({'success': False, 'error': f'IOT {iot} не найден в багажнике'})
        
        # Удаляем
        bag_data['bag'] = [item for item in current_bag if item.get('iot') != iot]
        save_iot_bag(bag_data)
        
        logger.info(f"✅ IOT {iot} удалён из багажника")
        
        return jsonify({
            'success': True,
            'message': f'IOT {iot} удалён из багажника'
        })
        
    except Exception as e:
        logger.error(f"Ошибка удаления IOT из багажника: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/iot/clear_bag', methods=['POST'])
@login_required
def api_iot_clear_bag():
    """Полностью очищает багажник"""
    if session.get('role') != 'iot':
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    
    try:
        bag_data = get_iot_bag()
        count = len(bag_data.get('bag', []))
        
        bag_data['bag'] = []
        save_iot_bag(bag_data)
        
        logger.info(f"✅ Багажник очищен ({count} IOT удалено)")
        
        return jsonify({
            'success': True,
            'message': f'Багажник очищен ({count} IOT удалено)',
            'count': count
        })
        
    except Exception as e:
        logger.error(f"Ошибка очистки багажника: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/iot/sync_source', methods=['POST'])
@login_required
def api_iot_sync_source():
    if session.get('role') != 'iot':
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    
    try:
        source_data = load_iot_source()
        return jsonify({'success': True, 'count': len(source_data)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/iot/bag')
@login_required
def api_iot_bag():
    if session.get('role') != 'iot':
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    
    bag_data = get_iot_bag()
    return jsonify({'success': True, 'bag': bag_data})


@app.route('/api/iot/replace', methods=['POST'])
@login_required
def api_iot_replace():
    """Заменяет IOT: пишет в 'Все IoT' столбец J и в 'КОРРЕКТИРОВКИ ВЕЛО'"""
    if session.get('role') != 'iot':
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    
    try:
        data = request.json
        old_iot = data.get('old_iot', '').strip()
        new_iot = data.get('new_iot', '').strip()
        frame_number = data.get('frame_number', '').strip()
        gos = data.get('gos', '').strip()
        darks = data.get('darks', '').strip()
        address = data.get('address', '').strip()
        
        if not old_iot or not new_iot or not frame_number:
            return jsonify({'success': False, 'error': 'Недостаточно данных'})
        
        bag_data = get_iot_bag()
        current_bag = [item.get('iot') for item in bag_data.get('bag', [])]
        
        if new_iot not in current_bag:
            return jsonify({'success': False, 'error': f'IOT {new_iot} не в багажнике'})
        
        vehicles_data = load_iot_vehicles()
        if new_iot in vehicles_data:
            bag_data['bag'] = [item for item in bag_data.get('bag', []) if item.get('iot') != new_iot]
            save_iot_bag(bag_data)
            return jsonify({
                'success': False,
                'error': f'IOT {new_iot} уже установлен на велосипеде {vehicles_data[new_iot].get("frame_number", "")}! Верните его в цех.'
            })
        
        # 1. Запись в "КОРРЕКТИРОВКИ ВЕЛО"
        write_iot_report(frame_number, new_iot, old_iot)
        
        # 2. Запись в "Все IoT" столбец J (в строку СТАРОГО IOT)
        update_iot_source_column_j(old_iot, new_iot)
        
        # 3. НОВОЕ: Обновляем кэш iot_source
        try:
            source_cache = read_cache(IOT_SOURCE_FILE)
            if source_cache and 'iot_list' in source_cache:
                if old_iot in source_cache['iot_list']:
                    now_str = get_msk_now().strftime('%d.%m')
                    source_cache['iot_list'][old_iot]['replaced_text'] = f"{now_str} поменяли на прошитый (новый IOT: {new_iot})"
                    write_cache(IOT_SOURCE_FILE, source_cache)
                    logger.info(f"✅ Кэш iot_source обновлён: {old_iot} помечен как заменённый")
        except Exception as e:
            logger.error(f"Ошибка обновления кэша iot_source: {e}")
        
        # 4. Убираем новый IOT из багажника
        bag_data['bag'] = [item for item in bag_data.get('bag', []) if item.get('iot') != new_iot]
        save_iot_bag(bag_data)
        
        # 5. Сохраняем в локальную историю
        now = get_msk_now()
        history_data = get_iot_history()
        history_data['history'].append({
            'date': now.strftime('%d.%m'),
            'time': now.strftime('%H:%M'),
            'old_iot': old_iot,
            'new_iot': new_iot,
            'frame_number': frame_number,
            'gos': gos,
            'darks': darks,
            'address': address,
            'timestamp': now.strftime('%Y-%m-%d %H:%M:%S')
        })
        save_iot_history(history_data)
        
        logger.info(f"✅ IOT заменён: {old_iot} → {new_iot} (рама {frame_number})")
        
        return jsonify({
            'success': True,
            'message': f'IOT {old_iot} заменён на {new_iot}'
        })
        
    except Exception as e:
        logger.error(f"Ошибка замены IOT: {e}")
        return jsonify({'success': False, 'error': str(e)})

# ============================================================
# ЗАПУСК
# ============================================================
if __name__ == "__main__":
    logger.info("🚀 Запуск приложения...")
    
    background_thread = threading.Thread(target=process_queue_background, daemon=True)
    background_thread.start()
    logger.info("🚀 Фоновый процесс обработки очереди запущен")
    
    try:
        logger.info("📂 Загрузка данных из Google Sheets...")
        load_darks_reference()
        tickets = get_tickets_from_sheets()
        uid_index = build_uid_index(tickets)
        
        save_admin_cache(tickets)
        logger.info(f"✅ Админ кэш создан: {len(tickets)} заявок")
        
        logger.info("📂 Создание кэшей для мастеров...")
        for master in MASTERS:
            master_tickets = [t for t in tickets if t.get('master') == master and is_active_status(t.get('status'))]
            save_master_cache(master, master_tickets, '')
            logger.info(f"   ✅ Кэш для {master}: {len(master_tickets)} заявок")
        
        queue_path = get_queue_path()
        if not os.path.exists(queue_path):
            write_queue({'tasks': [], 'last_sync': get_msk_now().strftime('%Y-%m-%d %H:%M:%S')})
            logger.info("✅ Создана пустая очередь")
        
        logger.info("✅ ВСЕ КЭШИ УСПЕШНО СОЗДАНЫ!")
        
    except Exception as e:
        logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ СОЗДАНИИ КЭШЕЙ: {e}")
        import traceback
        traceback.print_exc()
    
    port = int(os.environ.get("PORT", 5000))
    
    ssl_context = None
    try:
        if os.path.exists('/etc/ssl/certs/amvera.crt') and os.path.exists('/etc/ssl/private/amvera.key'):
            ssl_context = ('/etc/ssl/certs/amvera.crt', '/etc/ssl/private/amvera.key')
            logger.info("✅ SSL сертификаты найдены, запуск с HTTPS")
    except:
        pass
    
    if ssl_context:
        app.run(host="0.0.0.0", port=port, ssl_context=ssl_context)
    else:
        app.run(host="0.0.0.0", port=port)
