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
PARTS = [
    ('Покрышка', 1200),
    ('Камера', 300),
    ('Обод', 1200),
    ('Ось', 180),
    ('Крыло', 400),
    ('Ручка тормоза с бачком', 550),
    ('Ручка тормоза', 140),
    ('Колодки', 200),
    ('Концевик', 220),
    ('Суппорт', 550),
    ('Подшипник', 50),
    ('Вилка', 2500),
    ('Зеркала для электровелосипеда (2 шт.)', 500),
    ('Хомут для зеркал комплект', 200),
    ('Подножка', 850),
    ('Пружина', 50),
    ('Багажник задний для Monster', 1000),
    ('Багажник передний 39х32см для электровелосипедов', 800),
    ('Сиденье', 700),
    ('Подседельный штырь', 450),
    ('Сигналка', 350),
    ('Ручка газа', 1000),
    ('Блок переключения скоростей /фар/сигнал/поворотники', 450),
    ('Треугольник', 300),
    ('Стойка под руль', 550),
    ('Руль', 550),
    ('Фара', 750),
]
PARTS_PRICE = {name: price for name, price in PARTS}
PARTS_SET = set(PARTS_PRICE)
CACHE_TTL = 300
CACHE_DIR = "/data/cache"
BOT_API_URL = "https://route-bot-dzufear.waw0.amvera.tech"

# IOT настройки
IOT_SOURCE_SHEET_ID = "1BoZ7GFmL2q56bjqt1CFVV_PeqnKVaeb3anTJkf0s6xA"
IOT_VEHICLES_SHEET_ID = "1s_hXPSWueMAo3W1pgLCG0eHhYKoW0uoIZ6CGWFf55VU"
IOT_REPORT_SHEET_ID = "1s_hXPSWueMAo3W1pgLCG0eHhYKoW0uoIZ6CGWFf55VU"

IOT_BAG_FILE = "iot_bag.json"
IOT_SOURCE_FILE = "iot_source.json"
IOT_VEHICLES_FILE = "iot_vehicles.json"
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
_darks_loaded_at = 0
queue_lock = threading.Lock()
flush_lock = threading.Lock()

# Кэш в памяти для IOT (мгновенный доступ)
_iot_source_memory = None
_iot_vehicles_memory = None

# Глобальный gspread-клиент (переиспользование)
_gspread_client = None
_gspread_client_lock = threading.Lock()
_workbook = None
_tickets_mem = []
_tickets_mem_at = 0
_sheet_backoff_until = 0
_sheet_read_lock = threading.Lock()
_app_started_at = time.time()

# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================
def get_msk_now():
    return datetime.now(MSK)

def is_active_status(status):
    return status in ['pending', 'todo', 'processed', 'fail', 'evacuation']

def is_evacuation_ticket(ticket):
    return (ticket or {}).get('status') == 'evacuation'

def is_supply(ticket):
    text = ' '.join([
        str((ticket or {}).get('type') or ''),
        str((ticket or {}).get('bike_type') or ''),
    ]).lower()
    return 'аккумулятор' in text or 'зарядн' in text

def ticket_hours(ticket):
    if not ticket:
        return 0
    return get_hours_since(ticket.get('timer_from') or ticket.get('created') or '')

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

def update_ticket_in_admin_cache(uid, new_status, note='', display_desc='', extra=None):
    try:
        admin_cache = get_admin_cache()
        if not admin_cache:
            return False
        updated = False
        for dir_name, dir_data in admin_cache.get('directions', {}).items():
            for t in dir_data.get('tickets', []):
                if t.get('uid') == uid:
                    t['status'] = new_status
                    t['is_done'] = new_status == 'done'
                    t['is_active'] = is_active_status(new_status)
                    if note:
                        t['note'] = note
                    if display_desc:
                        t['display_desc'] = display_desc
                    if extra:
                        t.update(extra)
                    t['hours_since'] = ticket_hours(t)
                    updated = True
                    break
            if updated:
                break
        for ticket in _tickets_mem:
            if ticket.get('uid') != uid:
                continue
            ticket['status'] = new_status
            ticket['is_done'] = new_status == 'done'
            ticket['is_active'] = is_active_status(new_status)
            if note:
                ticket['note'] = note
            if display_desc:
                ticket['display_desc'] = display_desc
            if extra:
                ticket.update(extra)
            ticket['hours_since'] = ticket_hours(ticket)
        if _tickets_mem:
            store_ticket_memory(_tickets_mem, fresh=False)
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
def get_gspread_client():
    """Возвращает переиспользуемый gspread-клиент"""
    global _gspread_client
    with _gspread_client_lock:
        if _gspread_client is None:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
            _gspread_client = gspread.authorize(creds)
        return _gspread_client

def get_sheet_client():
    global _workbook
    if _workbook is None:
        _workbook = get_gspread_client().open(SHEET_NAME)
    return _workbook

def get_iot_sheet_by_id(sheet_id, sheet_name=None):
    client = get_gspread_client()
    spreadsheet = client.open_by_key(sheet_id)
    if sheet_name:
        return spreadsheet.worksheet(sheet_name)
    return spreadsheet

def clean_name(value):
    return ' '.join(str(value or '').replace('\xa0', ' ').split()).lower()

def column_index(header, needles, default):
    for index, title in enumerate(header or []):
        text = str(title or '').strip().lower().replace('\xa0', ' ')
        if all(needle in text for needle in needles):
            return index
    return default

def is_quota_error(exc):
    text = str(exc)
    return '429' in text or 'Quota exceeded' in text or 'RATE_LIMIT' in text

def load_darks_reference(force=False):
    global darks_ref, _darks_loaded_at
    if darks_ref and not force and time.time() - _darks_loaded_at < 90:
        return darks_ref
    try:
        sheet_client = get_sheet_client()
        worksheet = sheet_client.worksheet("Дарксторы")
        rows = worksheet.get_all_values()
        fresh = {}
        if len(rows) > 1:
            header = rows[0]
            i_address = column_index(header, ['адрес'], 1)
            i_direction = column_index(header, ['направ'], 2)
            i_coords = column_index(header, ['коорд'], 3)
            i_master = column_index(header, ['мастер'], 7)
            i_tg = column_index(header, ['тг', 'id'], 8)
            for row in rows[1:]:
                if not row or not str(row[0]).strip():
                    continue
                darks_num = str(row[0]).strip()
                def cell(index):
                    return row[index].strip() if len(row) > index and row[index] else ''
                fresh[darks_num] = {
                    'address': cell(i_address),
                    'direction': cell(i_direction),
                    'coords': cell(i_coords),
                    'master': cell(i_master),
                    'master_tg': cell(i_tg),
                }
        if fresh:
            darks_ref = fresh
            _darks_loaded_at = time.time()
    except Exception as e:
        logger.error(f"Ошибка загрузки Дарксторов: {e}")
        if is_quota_error(e):
            raise
    return darks_ref

def enrich_tickets(tickets):
    if not darks_ref:
        load_darks_reference()
    for ticket in tickets or []:
        info = darks_ref.get(str(ticket.get('darks') or '').strip()) or {}
        if info.get('address'):
            ticket['address'] = info['address']
        if info.get('coords'):
            ticket['coords'] = info['coords']
        if info.get('direction') and not ticket.get('direction'):
            ticket['direction'] = info['direction']
    return tickets

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

def normalize_gos(value):
    return re.sub(r'[^0-9A-Za-zА-Яа-я]', '', str(value or '')).upper()

_bike_lookup_cache = {'ts': 0, 'by_dark_gos': {}}
_bike_type_cache = {'ts': 0, 'by_gos': {}, 'supplier': {}}
_bike_rows_cache = {'ts': 0, 'rows': []}

def sheet_col(header, titles, fallback):
    wanted = {title.lower() for title in titles}
    for index, cell in enumerate(header or []):
        if str(cell).strip().lower() in wanted:
            return index
    return fallback

def load_bike_rows():
    now_ts = time.time()
    if _bike_rows_cache['rows'] and now_ts - _bike_rows_cache['ts'] < 600:
        return _bike_rows_cache['rows']
    rows = get_sheet_client().worksheet('База данных вело').get_all_values()
    _bike_rows_cache['rows'] = rows or []
    _bike_rows_cache['ts'] = now_ts
    return _bike_rows_cache['rows']

def bike_lookup():
    now_ts = time.time()
    if _bike_lookup_cache['by_dark_gos'] and now_ts - _bike_lookup_cache['ts'] < 600:
        return _bike_lookup_cache['by_dark_gos']
    mapping = {}
    try:
        rows = load_bike_rows()
        for row in rows[1:]:
            gos = normalize_gos(row[1] if len(row) > 1 else '')
            darks = row[4].strip() if len(row) > 4 else ''
            if not gos or not darks:
                continue
            mapping[(darks, gos)] = {
                'serial': row[0].strip() if row else '',
                'gos': (row[1] if len(row) > 1 else '').strip(),
                'iot': (row[2] if len(row) > 2 else '').strip(),
            }
        _bike_lookup_cache['by_dark_gos'] = mapping
        _bike_lookup_cache['ts'] = now_ts
    except Exception as e:
        logger.error(f'Ошибка чтения базы вело: {e}')
        if is_quota_error(e):
            raise
    return _bike_lookup_cache['by_dark_gos']

def fill_old_bike(tickets):
    mapping = bike_lookup()
    for ticket in tickets or []:
        bike = mapping.get((str(ticket.get('darks') or ''), normalize_gos(ticket.get('gos'))))
        if not bike:
            continue
        ticket['old_serial'] = bike['serial']
        ticket['old_gos'] = bike['gos']
        ticket['old_iot'] = bike['iot']

def ticket_label(ticket):
    gos = ((ticket or {}).get('gos') or '').strip()
    if gos:
        return gos
    return (ticket or {}).get('type') or 'Заявка'

def evacuation_reason(ticket):
    note = ((ticket or {}).get('note') or '').strip()
    for line in reversed(note.splitlines()):
        if 'ЭВАКУАЦИЯ' in line.upper():
            text = line.split(':', 1)[-1].strip()
            if text:
                return text
    return ((ticket or {}).get('desc') or '').strip()

def notify_dark_event(darks, text):
    if not darks or not text:
        return
    def run():
        try:
            requests.post(
                f'{BOT_API_URL}/notify_event',
                json={'darks': str(darks), 'text': text},
                timeout=8,
            )
        except Exception as e:
            logger.warning(f'Не ушло сообщение куратору {darks}: {e}')
    threading.Thread(target=run, daemon=True).start()

def supplier_of(gos_value):
    return (_bike_type_cache.get('supplier') or {}).get(normalize_gos(gos_value), '')

def get_bike_type_by_gos_map():
    now_ts = time.time()
    if _bike_type_cache['by_gos'] and now_ts - _bike_type_cache['ts'] < 600:
        return _bike_type_cache['by_gos']
    mapping = {}
    suppliers = {}
    try:
        rows = load_bike_rows()
        header = rows[0] if rows else []
        gos_i = sheet_col(header, ['гос номер', 'госномер'], 1)
        kind_i = sheet_col(header, ['тип вело'], 5)
        company_i = sheet_col(header, ['компания', 'поставщик'], 8)
        for row in rows[1:]:
            gos = normalize_gos(row[gos_i] if len(row) > gos_i else '')
            kind = row[kind_i].strip() if len(row) > kind_i else ''
            company = row[company_i].strip() if len(row) > company_i else ''
            if gos and kind:
                mapping[gos] = kind
            if gos and company:
                suppliers[gos] = company
        _bike_type_cache['by_gos'] = mapping
        _bike_type_cache['supplier'] = suppliers
        _bike_type_cache['ts'] = now_ts
    except Exception as e:
        logger.error(f"Ошибка чтения типов вело: {e}")
        if is_quota_error(e):
            raise
    return _bike_type_cache['by_gos']

def _read_tickets_from_google():
    global darks_ref
    sheet_client = get_sheet_client()
    tickets = []
    darks_ref = load_darks_reference()
    bike_types = get_bike_type_by_gos_map()
    
    for sheet_name in ('Заявки', 'Заявки бот'):
        try:
            worksheet = sheet_client.worksheet(sheet_name)
            rows = worksheet.get_all_values()
            if len(rows) > 1:
                for idx, row in enumerate(rows[1:], start=2):
                    if len(row) < 6 or not row[0].strip():
                        continue
                    if row[0].strip() in ('Номер дарка', 'Номер_дарка'):
                        continue
                    darks_num = row[0].strip()
                    status_raw = row[7].strip() if len(row) > 7 else ''
                    note = row[9].strip() if len(row) > 9 else ''
                
                    if status_raw in ['Выполнено', '✅ Выполнено', 'done']:
                        status = 'done'
                    elif status_raw in ['🔵 Доделать', 'Доделать']:
                        status = 'todo'
                    elif status_raw in ['⏹️ Обработано', 'Обработано', 'Вело отсутствует']:
                        status = 'processed'
                    elif status_raw in ['🔧 Эвакуация', 'Эвакуация']:
                        status = 'evacuation'
                    else:
                        status = 'pending'
                
                    is_done = status == 'done'
                    created_str = row[5].strip() if len(row) > 5 else ''
                    timer_from = row[14].strip() if len(row) > 14 else ''
                    hours_since = get_hours_since(timer_from or created_str)
                    bike_type = row[1].strip() if len(row) > 1 else ''
                    bike_subtype = row[8].strip() if len(row) > 8 else ''
                    gos_value = row[3].strip() if len(row) > 3 else ''
                    if bike_type == 'Электровелосипед':
                        display_type = bike_types.get(normalize_gos(gos_value)) or 'Неопределен тип вело, ошибка в гос номере'
                    elif bike_subtype:
                        display_type = bike_subtype
                    else:
                        display_type = bike_type or 'Не указан'
                    supplier = supplier_of(gos_value)
                    uid = row[10].strip() if len(row) > 10 else ''
                    if not uid and created_str:
                        uid = generate_ticket_id(created_str)
                        if uid:
                            try:
                                worksheet.update_cell(idx, 11, uid)
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
                
                    evac_reason = ''
                    if status == 'evacuation':
                        if note and not note.startswith('ЭВАКУАЦИЯ:'):
                            note = f'ЭВАКУАЦИЯ: {note}'
                        evac_reason = evacuation_reason({'note': note, 'desc': display_desc})
                
                    tickets.append({
                        'source': sheet_name,
                        'darks': darks_num,
                        'type': bike_type,
                        'bike_type': display_type,
                        'bike_subtype': bike_subtype,
                        'supplier': supplier,
                        'desc': display_desc,
                        'gos': gos_value,
                        'contact': row[4].strip() if len(row) > 4 else '',
                        'created': created_str,
                        'timer_from': timer_from,
                        'hours_since': hours_since,
                        'master': row[6].strip() if len(row) > 6 else '',
                        'status': status,
                        'note': note,
                        'evac_reason': evac_reason,
                        'uid': uid,
                        'row_index': idx,
                        'address': darks_ref.get(darks_num, {}).get('address', ''),
                        'direction': direction,
                        'coords': darks_ref.get(darks_num, {}).get('coords', ''),
                        'sent_date': '',
                        'is_done': is_done,
                        'is_active': not is_done,
                        'parts': row[9].strip() if len(row) > 9 else '',
                        'display_desc': display_desc
                    })
        except Exception as e:
            logger.error(f"Ошибка чтения '{sheet_name}': {e}")
            if is_quota_error(e):
                raise

    
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
                
                if status_raw == 'Решено' or status_l in ('Выполнено', '✅ Выполнено'):
                    status = 'done'
                    is_done = True
                elif status_l in ('Эвакуация', '🔧 Эвакуация'):
                    status = 'evacuation'
                    is_done = False
                    if note and not note.startswith('ЭВАКУАЦИЯ:'):
                        note = f'ЭВАКУАЦИЯ: {note}'
                elif status_l in ('Доделать', '🔵 Доделать'):
                    status = 'todo'
                    is_done = False
                elif status_l in ('Обработано', '⏹️ Обработано', 'Вело отсутствует'):
                    status = 'processed'
                    is_done = False
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
                    'supplier': supplier_of(gos),
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
        if is_quota_error(e):
            raise
    
    return tickets

def store_ticket_memory(tickets, fresh=False):
    global _tickets_mem, _tickets_mem_at, uid_index
    _tickets_mem = list(tickets or [])
    uid_index = build_uid_index(_tickets_mem)
    if fresh:
        _tickets_mem_at = time.time()
    return _tickets_mem

def publish_caches(tickets):
    enrich_tickets(tickets)
    save_admin_cache(tickets)
    for master in MASTERS:
        master_tickets = [t for t in tickets if clean_name(t.get('master')) == clean_name(master) and is_active_status(t.get('status'))]
        save_master_cache(master, master_tickets, '')

def get_tickets_from_sheets(force=False):
    global _sheet_backoff_until
    now = time.time()
    if _tickets_mem and not force and now - _tickets_mem_at < 120:
        return _tickets_mem
    if now < _sheet_backoff_until:
        logger.info('Google ограничил чтение, берём прошлую копию')
        return _tickets_mem
    if not _sheet_read_lock.acquire(blocking=False):
        return _tickets_mem
    try:
        now = time.time()
        if _tickets_mem and not force and now - _tickets_mem_at < 120:
            return _tickets_mem
        if now < _sheet_backoff_until:
            return _tickets_mem
        tickets = _read_tickets_from_google()
        store_ticket_memory(tickets, fresh=True)
        logger.info(f'Таблица прочитана: {len(tickets)} заявок')
        return _tickets_mem
    except Exception as e:
        if is_quota_error(e):
            _sheet_backoff_until = time.time() + 75
            logger.warning('Лимит Google. Пауза 75 секунд, работаем на прошлой копии.')
        else:
            logger.error(f'Ошибка чтения таблицы: {e}')
        return _tickets_mem
    finally:
        _sheet_read_lock.release()

def refresh_admin_cache():
    try:
        flush_queue()
        tickets = get_tickets_from_sheets()
        save_admin_cache(tickets)
        logger.info(f"✅ Админ кэш обновлен: {len(tickets)} заявок")
    except Exception as e:
        logger.error(f"Ошибка обновления админ кэша: {e}")

def refresh_master_cache(master_name):
    try:
        tickets = get_tickets_from_sheets()
        wanted = clean_name(master_name)
        master_tickets = [t for t in tickets if clean_name(t.get('master')) == wanted and is_active_status(t.get('status'))]
        enrich_tickets(master_tickets)
        save_master_cache(master_name, master_tickets, '')
        logger.info(f"✅ Кэш для {master_name} обновлен: {len(master_tickets)} заявок")
        return len(master_tickets)
    except Exception as e:
        logger.error(f"Ошибка обновления кэша {master_name}: {e}")
        return 0

def refresh_all_master_caches():
    try:
        tickets = get_tickets_from_sheets()
        publish_caches(tickets)
        logger.info("✅ Кэши всех мастеров обновлены из копии")
    except Exception as e:
        logger.error(f"Ошибка обновления кэшей мастеров: {e}")

def batch_update_masters(changes):
    global uid_index
    updated_count = 0
    try:
        flush_queue()
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
            if source in ('Заявки', 'Заявки бот'):
                updates_zayavki.append((source, {'range': f'G{row_idx}', 'values': [[master]]}))
                updated_count += 1
            elif source == 'Импорт М4':
                updates_import.append({'range': f'N{row_idx}', 'values': [[master]]})
                updated_count += 1
        grouped = {}
        for source, update in updates_zayavki:
            grouped.setdefault(source, []).append(update)
        for source, updates in grouped.items():
            sheet_client.worksheet(source).batch_update(updates)
        if updates_import:
            worksheet = sheet_client.worksheet("Импорт М4")
            worksheet.batch_update(updates_import)
        for change in changes:
            for ticket in _tickets_mem:
                if ticket.get('uid') == change.get('uid'):
                    ticket['master'] = change.get('master') or ''
        store_ticket_memory(_tickets_mem, fresh=False)
        publish_caches(_tickets_mem)
        return updated_count
    except Exception as e:
        logger.error(f"Ошибка batch_update_masters: {e}")
        return 0

def clear_masters(master_name=None):
    wanted = clean_name(master_name) if master_name else ''
    tickets = list(_tickets_mem or get_tickets_from_sheets())
    grouped = {}
    cleared = 0
    for ticket in tickets:
        if not ticket.get('master') or not is_active_status(ticket.get('status')):
            continue
        if wanted and clean_name(ticket.get('master')) != wanted:
            continue
        row_idx = ticket.get('row_index')
        source = ticket.get('source') or 'Заявки'
        if not row_idx:
            continue
        column = 'N' if source == 'Импорт М4' else 'G'
        grouped.setdefault(source, []).append({'range': f'{column}{row_idx}', 'values': [['']]})
        ticket['master'] = ''
        cleared += 1
    if grouped:
        book = get_sheet_client()
        for source, updates in grouped.items():
            book.worksheet(source).batch_update(updates)
            logger.info(f"✅ Снято {len(updates)} мастеров в '{source}'")
    store_ticket_memory(tickets, fresh=False)
    publish_caches(tickets)
    return cleared

def clear_all_masters():
    try:
        return clear_masters()
    except Exception as e:
        logger.error(f"Ошибка снятия всех мастеров: {e}")
        return 0

def clear_one_master(master_name):
    if not clean_name(master_name):
        return 0
    try:
        return clear_masters(master_name)
    except Exception as e:
        logger.error(f"Ошибка снятия мастера {master_name}: {e}")
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
        
        if source in ('Заявки', 'Заявки бот'):
            worksheet = sheet_client.worksheet(source)
            worksheet.update_cell(row_idx, 8, status)
            if note:
                worksheet.update_cell(row_idx, 10, note)
            logger.info(f"✅ Обновлён статус заявки {uid} в '{source}': {status}")
            
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
    item = uid_index.get(uid) if uid else None
    if not item:
        return None
    ticket = item.get('ticket') or {}
    return {'source': ticket.get('source') or 'Заявки', 'row_index': item.get('row_index')}

# ============================================================
# ОЧЕРЕДЬ ЗАДАЧ
# ============================================================
def get_queue_path():
    return os.path.join(CACHE_DIR, "queue.json")

def read_queue():
    with queue_lock:
        return _load_queue()

def write_queue(queue_data):
    with queue_lock:
        try:
            _save_queue(queue_data)
            return True
        except:
            return False

def _load_queue():
    try:
        path = get_queue_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get('tasks'), list):
                    return data
    except:
        pass
    return {'tasks': [], 'last_sync': None}

def _save_queue(queue_data):
    path = get_queue_path()
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(queue_data, f, ensure_ascii=False, indent=2)

def add_to_queue(task):
    with queue_lock:
        queue_data = _load_queue()
        uid = task.get('uid')
        task['qid'] = f"{time.time():.6f}-{uid}"
        queue_data['tasks'] = [t for t in queue_data.get('tasks', []) if t.get('uid') != uid]
        queue_data['tasks'].append(task)
        _save_queue(queue_data)
    logger.info(f"✅ Задача добавлена в очередь: {uid}")

def remove_ticket_from_master_cache(master_name, uid):
    if not master_name or not uid:
        return
    cache_data = get_master_cache(master_name)
    if not cache_data:
        return
    tickets = [t for t in cache_data.get('tickets', []) if t.get('uid') != uid]
    save_master_cache(master_name, tickets, cache_data.get('sent_date', ''))

def find_cached_ticket(uid):
    admin_cache = get_admin_cache() or {}
    for dir_data in admin_cache.get('directions', {}).values():
        for t in dir_data.get('tickets', []):
            if t.get('uid') == uid:
                return t
    if uid in uid_index:
        return uid_index[uid].get('ticket')
    return None

def source_of(uid):
    ticket = find_cached_ticket(uid) or {}
    source = ticket.get('source')
    if source in ('Заявки', 'Заявки бот', 'Импорт М4'):
        return source
    if uid in uid_index:
        return uid_index[uid].get('source') or 'Заявки'
    return 'Заявки'

def mark_bike_missing(uid, actor_name):
    now = get_msk_now()
    timer_from = now.strftime('%Y-%m-%d %H:%M:%S')
    line = f"Вело отсутствует {now.strftime('%d.%m %H:%M')}, {actor_name}"
    ticket = find_cached_ticket(uid)
    previous = ''
    master_name = ''
    if ticket:
        previous = (ticket.get('note') or '').strip()
        master_name = ticket.get('master') or ''
    note = f'{previous}\n{line}'.strip() if previous else line
    update_ticket_in_admin_cache(uid, 'processed', note, extra={
        'master': '',
        'timer_from': timer_from,
        'hours_since': 0,
        'is_done': False,
        'is_active': True
    })
    remove_ticket_from_master_cache(master_name, uid)
    if actor_name and actor_name != master_name:
        remove_ticket_from_master_cache(actor_name, uid)
    add_to_queue({
        'uid': uid,
        'source': source_of(uid),
        'type': 'fail',
        'data': {
            'master': actor_name,
            'note': note,
            'detail': line,
            'parts': line,
            'timer_from': timer_from,
            'darks_number': (ticket or {}).get('darks', ''),
            'gos': (ticket or {}).get('gos', ''),
            'desc': (ticket or {}).get('desc', ''),
            'skip_report': False
        }
    })
    notify_dark_event((ticket or {}).get('darks', ''), f'{ticket_label(ticket)} нет вело на дарксторе, заявка перенесена')
    return note

def apply_queue_to_sheets(tasks):
    buckets = {'Заявки': [], 'Заявки бот': []}
    for task in tasks:
        source = task.get('source')
        if source not in buckets:
            continue
        updates = buckets[source]
        uid = task.get('uid')
        row_idx = get_ticket_row_by_uid(uid)
        if not row_idx:
            found = find_uid_in_sheets(uid)
            if not found or found.get('source') != source:
                continue
            row_idx = found['row_index']
        data = task.get('data') or {}
        task_type = task.get('type')
        if task_type == 'fail':
            updates.append({'range': f'G{row_idx}', 'values': [['']]})
            updates.append({'range': f'H{row_idx}', 'values': [['⏹️ Обработано']]})
            updates.append({'range': f'J{row_idx}', 'values': [[data.get('note', '')]]})
            updates.append({'range': f'O{row_idx}', 'values': [[data.get('timer_from', '')]]})
        elif task_type in ('done', 'replace_yes', 'transit_replace', 'evacuation_and_replace', 'transit_bulk_close'):
            updates.append({'range': f'H{row_idx}', 'values': [['✅ Выполнено']]})
            parts = data.get('parts') or data.get('extra') or ''
            if task_type == 'done':
                parts = data.get('parts') or ''
            elif task_type == 'evacuation_and_replace':
                parts = 'Заменен'
            if parts:
                updates.append({'range': f'J{row_idx}', 'values': [[parts]]})
        elif task_type == 'evacuation':
            updates.append({'range': f'H{row_idx}', 'values': [['🔧 Эвакуация']]})
            updates.append({'range': f'J{row_idx}', 'values': [[data.get('reason') or 'Эвакуация']]})
        elif task_type == 'taken_no_replace':
            updates.append({'range': f'H{row_idx}', 'values': [['🔵 Доделать']]})
            updates.append({'range': f'J{row_idx}', 'values': [[f"ЗАБРАЛИ: {data.get('parts', '')} АКБ"]]})
        elif task_type == 'replace_no':
            updates.append({'range': f'H{row_idx}', 'values': [['🔵 Доделать']]})
            updates.append({'range': f'J{row_idx}', 'values': [[data.get('reason') or 'Куратор не предоставил']]})
        elif task_type == 'status_update':
            updates.append({'range': f'H{row_idx}', 'values': [[data.get('status') or '🟡 В работе']]})
            if data.get('note'):
                updates.append({'range': f'J{row_idx}', 'values': [[data.get('note')]]})
    import_updates = []
    for task in tasks:
        if task.get('source') != 'Импорт М4':
            continue
        uid = task.get('uid')
        row_idx = get_ticket_row_by_uid(uid)
        if not row_idx:
            found = find_uid_in_sheets(uid)
            if not found or found.get('source') != 'Импорт М4':
                continue
            row_idx = found['row_index']
        data = task.get('data') or {}
        task_type = task.get('type')
        status_map_import = {
            '🟡 В работе': 'В работе',
            '✅ Выполнено': 'Выполнено',
            '🔵 Доделать': 'Доделать',
            '⏹️ Обработано': 'Обработано',
            '🔧 Эвакуация': 'Эвакуация'
        }
        clear_master = False
        if task_type == 'fail':
            sheet_status, sheet_note = 'Обработано', data.get('note') or ''
            clear_master = True
        elif task_type in ('done', 'replace_yes', 'transit_replace', 'evacuation_and_replace', 'transit_bulk_close'):
            sheet_status = 'Выполнено'
            sheet_note = 'Заменен' if task_type == 'evacuation_and_replace' else (data.get('parts') or data.get('extra') or '')
        elif task_type == 'evacuation':
            sheet_status, sheet_note = 'Эвакуация', data.get('reason') or 'Эвакуация'
        elif task_type == 'taken_no_replace':
            sheet_status, sheet_note = 'Доделать', f"ЗАБРАЛИ: {data.get('parts', '')} АКБ"
        elif task_type == 'replace_no':
            sheet_status, sheet_note = 'Доделать', data.get('reason') or 'Куратор не предоставил'
        elif task_type == 'status_update':
            sheet_status = status_map_import.get(data.get('status'), 'В работе')
            sheet_note = data.get('note') or ''
            clear_master = data.get('status') == '⏹️ Обработано'
        else:
            continue
        import_updates.append({'range': f'L{row_idx}', 'values': [[sheet_status]]})
        if sheet_note:
            import_updates.append({'range': f'M{row_idx}', 'values': [[sheet_note]]})
        if clear_master:
            import_updates.append({'range': f'N{row_idx}', 'values': [['']]})
    written = 0
    sheet_client = get_sheet_client()
    for sheet_name, sheet_updates in buckets.items():
        if not sheet_updates:
            continue
        sheet_client.worksheet(sheet_name).batch_update(sheet_updates)
        written += len(sheet_updates)
        logger.info(f"✅ Очередь записана в {sheet_name}: {len(sheet_updates)} ячеек")
    if import_updates:
        get_sheet_client().worksheet('Импорт М4').batch_update(import_updates)
        written += len(import_updates)
        logger.info(f"✅ Очередь записана в Импорт М4: {len(import_updates)} ячеек")
    return written

def append_master_history(master, action, data):
    if not master:
        return
    labels = {
        'done': 'Выполнено',
        'fail': 'Велосипеда не было',
        'evacuation': 'Эвакуация',
        'evacuation_and_replace': 'Замена велосипеда',
        'transit_replace': 'Замена велосипеда',
        'replace_yes': 'Замена',
        'replace_no': 'Куратор не дал',
        'taken_no_replace': 'Забрали без замены',
    }
    raw = read_cache(f"history_{master}.json")
    if isinstance(raw, dict):
        items = raw.get('items') or raw.get('history') or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    data = data or {}
    detail = data.get('detail') or data.get('parts') or data.get('reason') or ''
    if detail == 'Отказ от эвакуации':
        labels['done'] = 'Отказ от эвакуации'
    items.append({
        'timestamp': get_msk_now().strftime('%Y-%m-%d %H:%M:%S'),
        'action': {
            'action': action,
            'label': 'Отказ от эвакуации' if detail == 'Отказ от эвакуации' else labels.get(action, 'Отметка'),
            'parts': '' if detail == 'Отказ от эвакуации' else detail,
            'detail': detail,
            'darks_number': data.get('darks_number', ''),
            'gos': data.get('gos') or ((data.get('old_data') or {}).get('gos') or ''),
        }
    })
    write_cache(f"history_{master}.json", {'items': items[-300:]})

TRUNK_FILE = os.path.join(CACHE_DIR, "trunks.json")
PARTS_QUEUE_FILE = os.path.join(CACHE_DIR, "parts_queue.json")
_trunk_lock = threading.Lock()

def _read_json(path, fallback):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data
    except Exception:
        pass
    return fallback

def _write_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_trunks():
    data = _read_json(TRUNK_FILE, {})
    return data if isinstance(data, dict) else {}

def save_trunks(data):
    _write_json(TRUNK_FILE, data)

def trunk_rows(master):
    bag = load_trunks().get(master) or {}
    rows = []
    total_qty = 0
    for name, price in PARTS:
        qty = int(bag.get(name) or 0)
        rows.append({'name': name, 'qty': qty})
        total_qty += qty
    return rows, total_qty

def trunk_qty(master, name):
    return int((load_trunks().get(master) or {}).get(name) or 0)

def change_trunk(master, name, delta):
    if name not in PARTS_SET:
        return False, 0
    with _trunk_lock:
        data = load_trunks()
        bag = data.get(master) or {}
        current = int(bag.get(name) or 0)
        new_qty = current + int(delta)
        if new_qty < 0:
            return False, current
        bag[name] = new_qty
        data[master] = bag
        save_trunks(data)
    return True, new_qty

def queue_part_move(master, action, name, qty, uid='', gos='', darks=''):
    price = PARTS_PRICE.get(name, 0)
    move = {
        'time': get_msk_now().strftime('%Y-%m-%d %H:%M:%S'),
        'master': master,
        'action': action,
        'name': name,
        'qty': int(qty),
        'price': price,
        'sum': price * int(qty),
        'uid': uid or '',
        'gos': gos or '',
        'darks': str(darks or ''),
    }
    with _trunk_lock:
        data = _read_json(PARTS_QUEUE_FILE, {'moves': []})
        if not isinstance(data, dict):
            data = {'moves': []}
        data.setdefault('moves', []).append(move)
        data['log'] = (data.get('log') or [])[-1000:]
        data['log'].append(move)
        _write_json(PARTS_QUEUE_FILE, data)

def parts_log():
    data = _read_json(PARTS_QUEUE_FILE, {})
    log = data.get('log') if isinstance(data, dict) else []
    return log if isinstance(log, list) else []

def flush_parts_queue():
    with _trunk_lock:
        data = _read_json(PARTS_QUEUE_FILE, {'moves': [], 'log': []})
        moves = list(data.get('moves') or []) if isinstance(data, dict) else []
    if not moves:
        return 0
    sheet = get_sheet_client()
    try:
        ws = sheet.worksheet('Движение запчастей 2')
    except Exception:
        ws = sheet.add_worksheet('Движение запчастей 2', 2000, 10)
        ws.update('A1:J1', [['Дата', 'Мастер', 'Действие', 'Запчасть', 'Количество', 'Цена', 'Сумма', 'Заявка', 'Госномер', 'Даркстор']], value_input_option='RAW')
    values = [[m['time'], m['master'], m['action'], m['name'], m['qty'], m.get('price', 0), m.get('sum', 0), m.get('uid', ''), m.get('gos', ''), m.get('darks', '')] for m in moves]
    ws.append_rows(values, value_input_option='RAW')
    write_trunk_sheet(sheet)
    with _trunk_lock:
        data = _read_json(PARTS_QUEUE_FILE, {'moves': [], 'log': []})
        left = data.get('moves') or []
        data['moves'] = left[len(moves):] if len(left) >= len(moves) else []
        _write_json(PARTS_QUEUE_FILE, data)
    logger.info(f"✅ Движение запчастей записано: {len(moves)}")
    return len(moves)

def write_trunk_sheet(sheet):
    try:
        ws = sheet.worksheet('Багажник')
    except Exception:
        ws = sheet.add_worksheet('Багажник', 400, 5)
    rows = [['Мастер', 'Запчасть', 'Количество', 'Цена', 'Сумма']]
    stored = load_trunks()
    for master in MASTERS:
        bag = stored.get(master) or {}
        for name, price in PARTS:
            qty = int(bag.get(name) or 0)
            if qty <= 0:
                continue
            rows.append([master, name, qty, price, qty * price])
    ws.clear()
    ws.update('A1', rows, value_input_option='RAW')

def spend_parts(master, items, uid='', gos='', darks=''):
    clean = []
    for item in items:
        name = (item.get('name') or '').strip()
        qty = int(item.get('qty') or 0)
        if qty <= 0 or name not in PARTS_SET:
            continue
        clean.append((name, qty))
    if not clean:
        return True, ''
    with _trunk_lock:
        data = load_trunks()
        bag = dict(data.get(master) or {})
        for name, qty in clean:
            if int(bag.get(name) or 0) < qty:
                have = int(bag.get(name) or 0)
                return False, f'В багажнике «{name}» только {have} шт.'
        for name, qty in clean:
            bag[name] = int(bag.get(name) or 0) - qty
        data[master] = bag
        save_trunks(data)
    lines = []
    for name, qty in clean:
        queue_part_move(master, 'Поставил', name, qty, uid, gos, darks)
        lines.append(f'{name} x{qty}')
    return True, ', '.join(lines)

def flush_queue():
    if not flush_lock.acquire(blocking=False):
        return 0
    try:
        with queue_lock:
            queue_data = _load_queue()
            tasks = list(queue_data.get('tasks') or [])
            if not tasks:
                return 0
            for i, task in enumerate(tasks):
                if not task.get('qid'):
                    task['qid'] = f"legacy-{i}-{task.get('uid')}"
            _save_queue(queue_data)
            taken = [dict(task) for task in tasks]
            taken_ids = {task['qid'] for task in taken}
        apply_queue_to_sheets(taken)
        report_tasks = []
        for task in taken:
            data = task.get('data') or {}
            if data.get('skip_report') or task.get('type') == 'status_update':
                continue
            report_tasks.append(task)
        if report_tasks:
            write_to_report(report_tasks)
            for task in report_tasks:
                data = dict(task.get('data') or {})
                data['uid'] = task.get('uid')
                append_master_history(data.get('master'), task.get('type'), data)
        with queue_lock:
            queue_data = _load_queue()
            queue_data['tasks'] = [task for task in (queue_data.get('tasks') or []) if task.get('qid') not in taken_ids]
            queue_data['last_sync'] = get_msk_now().strftime('%Y-%m-%d %H:%M:%S')
            _save_queue(queue_data)
        logger.info(f"✅ Очередь записана и очищена: {len(taken)}")
        return len(taken)
    finally:
        try:
            flush_parts_queue()
        except Exception as e:
            logger.error(f"Ошибка записи запчастей: {e}")
        flush_lock.release()


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
                ticket = find_cached_ticket(uid) or {}
            if not ticket:
                logger.warning(f"Заявка {uid} не найдена в кэше, в отчёт пишем то, что пришло от мастера")
            
            status_map = {
                'done': '✅ Выполнено',
                'fail': 'Вело отсутствует',
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
                quantity = '-'
            
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
    while True:
        try:
            time.sleep(10)
            flush_queue()
            quiet = time.time() < _sheet_backoff_until
            stale = (not _tickets_mem_at) or (time.time() - _tickets_mem_at >= 120)
            if stale and not quiet and time.time() - _app_started_at > 20:
                tickets = get_tickets_from_sheets(force=True)
                if tickets:
                    publish_caches(tickets)
                    logger.info(f'Фоновое обновление: {len(tickets)} заявок')
        except Exception as e:
            logger.error(f"❌ Ошибка в фоновом процессе: {e}")

# ============================================================
# ОТПРАВКА УВЕДОМЛЕНИЙ
# ============================================================
def curator_notice_groups():
    tickets = []
    cache = get_admin_cache() or {}
    for dir_data in cache.get('directions', {}).values():
        tickets.extend(dir_data.get('tickets') or [])
    if not tickets:
        tickets = get_tickets_from_sheets()
    groups = {}
    for ticket in tickets:
        if not ticket.get('master') or not is_active_status(ticket.get('status')):
            continue
        groups.setdefault(str(ticket.get('darks') or ''), []).append(ticket)
    payload = []
    for dark, items in groups.items():
        if not dark:
            continue
        address = (items[0].get('address') or '').strip()
        lines = [
            'Привет, на связи Vanta Bikes!',
            '',
            f'{address} (даркстор {dark})'.strip(),
            f'Заявки ({len(items)}):',
        ]
        for ticket in items:
            if ticket.get('type') in ('Аккумуляторная батарея', 'Зарядное устройство'):
                name = ticket.get('type')
            else:
                name = ticket.get('gos') or 'Без номера'
            mark = ' — ЗАМЕНА' if is_evacuation_ticket(ticket) else ''
            lines.append(f'   {name} | {(ticket.get("desc") or "-")}{mark}')
        lines.append('')
        lines.append('Подготовьте, пожалуйста, технику к ремонту')
        lines.append('Хорошего дня!')
        payload.append({'darks': dark, 'text': '\n'.join(lines)})
    return payload

def notify_curators(message):
    try:
        groups = curator_notice_groups()
        if not groups:
            return False
        response = requests.post(
            f"{BOT_API_URL}/send_notification",
            json={"groups": groups},
            timeout=20,
        )
        return response.status_code == 200
    except Exception as e:
        logger.warning(f'Не отправились сообщения кураторам: {e}')
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
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Сессия истекла. Обновите страницу и войдите снова.'}), 401
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

@app.after_request
def no_store_master_pages(response):
    if request.path.startswith('/master'):
        response.headers['Cache-Control'] = 'no-store'
    return response

# ============================================================
# СТАТИКА
# ============================================================
@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

@app.route('/sw.js')
def service_worker():
    response = send_from_directory('static', 'sw.js')
    response.headers['Cache-Control'] = 'no-cache'
    return response

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
        age = time.time() - _tickets_mem_at if _tickets_mem_at else 999
        if _tickets_mem and age < 90:
            return jsonify({
                'success': True,
                'cached': True,
                'count': len(_tickets_mem),
                'message': 'Данные уже свежие, таблицу не открывал',
            })
        if time.time() < _sheet_backoff_until:
            return jsonify({
                'success': False,
                'error': 'Google ограничил чтение. Подождите минуту, работаем на прошлой копии.',
            })
        logger.info("🔄 Начинаем синхронизацию с Google Sheets...")
        flush_queue()
        tickets = get_tickets_from_sheets(force=True)
        if not _tickets_mem and time.time() < _sheet_backoff_until:
            return jsonify({'success': False, 'error': 'Google ограничил чтение. Подождите минуту.'})
        publish_caches(_tickets_mem or tickets)
        logger.info(f"✅ Синхронизация завершена: {len(tickets)} заявок")
        return jsonify({'success': True, 'cached': False, 'count': len(tickets)})
    except Exception as e:
        logger.error(f"Ошибка синхронизации: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/queue_status')
@login_required
def api_queue_status():
    tasks = read_queue().get('tasks') or []
    uids = []
    for task in tasks:
        uid = task.get('uid')
        if uid and uid not in uids:
            uids.append(uid)
    return jsonify({'success': True, 'count': len(tasks), 'uids': uids})

@app.route('/api/flush_now', methods=['POST'])
@login_required
def api_flush_now():
    try:
        flushed = flush_queue()
        return jsonify({'success': True, 'flushed': flushed})
    except Exception as e:
        logger.error(f"Ошибка записи очереди: {e}")
        return jsonify({'success': False, 'error': str(e)})

def master_chat_ids(master_name):
    load_darks_reference()
    wanted = clean_name(master_name)
    found = []
    for info in darks_ref.values():
        if clean_name(info.get('master')) != wanted:
            continue
        digits = ''.join(ch for ch in str(info.get('master_tg') or '') if ch.isdigit())
        if digits and digits not in found:
            found.append(digits)
    logger.info(f'ТГ мастера {master_name}: {found or "не найден"}')
    return found

def master_route_text(master_name):
    cache = get_master_cache(master_name) or {}
    tickets = [t for t in cache.get('tickets') or [] if is_active_status(t.get('status'))]
    enrich_tickets(tickets)
    lines = [f'Вам назначены заявки: {len(tickets)}']
    if not tickets:
        return 'На вас сейчас нет открытых заявок.'
    by_dark = {}
    for ticket in tickets:
        by_dark.setdefault(str(ticket.get('darks') or '—'), []).append(ticket)
    for dark, group in by_dark.items():
        address = (group[0].get('address') or '').strip()
        lines.append('')
        lines.append(f'{address} (даркстор {dark})'.strip())
        for ticket in group[:20]:
            label = ticket_label(ticket)
            desc = (ticket.get('desc') or '').replace('\n', ' ').strip()
            prefix = 'ЗАМЕНА. ' if is_evacuation_ticket(ticket) else ''
            lines.append(f'• {label} — {prefix}{desc[:90]}')
        extra = len(group) - 20
        if extra > 0:
            lines.append(f'и ещё {extra}')
    return '\n'.join(lines)

def notify_master_route(master_name):
    chat_ids = master_chat_ids(master_name)
    if not chat_ids:
        logger.info(f'У мастера {master_name} нет ТГ ID в столбце I листа Дарксторы')
        return {'sent': 0, 'reason': 'no_id'}
    text = master_route_text(master_name)
    sent = 0
    last_error = ''
    for chat_id in chat_ids:
        try:
            response = requests.post(
                f'{BOT_API_URL}/notify_master',
                json={'chat_id': chat_id, 'text': text},
                timeout=25,
            )
            if response.status_code == 200 and (response.json() or {}).get('success'):
                sent += 1
            else:
                last_error = response.text[:200]
                logger.warning(f'Бот не принял сообщение мастеру {master_name}: {last_error}')
        except Exception as e:
            last_error = str(e)
            logger.warning(f'Не отправилось сообщение мастеру {master_name}: {e}')
    if sent:
        return {'sent': sent, 'reason': 'ok'}
    return {'sent': 0, 'reason': 'bot', 'error': last_error}

@app.route('/api/batch_update', methods=['POST'])
@login_required
def api_batch_update():
    data = request.json
    changes = data.get('changes', [])
    if not changes:
        return jsonify({'success': False, 'error': 'Нет изменений'})
    updated = batch_update_masters(changes)
    if updated:
        names = []
        for change in changes:
            master = (change.get('master') or '').strip()
            if master and master not in names:
                names.append(master)
        for master in names:
            refresh_master_cache(master)
            notify_master_route(master)
    return jsonify({'success': True, 'updated': updated})

@app.route('/api/send_route', methods=['POST'])
@login_required
def api_send_route():
    data = request.json
    master = data.get('master')
    if not master:
        return jsonify({'success': False, 'error': 'Не указан мастер'})
    count = refresh_master_cache(master)
    info = notify_master_route(master)
    return jsonify({
        'success': True,
        'message': f'Кэш мастера {master} обновлен',
        'notified': info.get('sent', 0),
        'notify_reason': info.get('reason', ''),
        'tickets': count,
    })

@app.route('/api/send_route_all', methods=['POST'])
@login_required
def api_send_route_all():
    refresh_all_master_caches()
    for master in MASTERS:
        notify_master_route(master)
    return jsonify({'success': True, 'message': 'Кэши всех мастеров обновляются'})

@app.route('/api/clear_dates', methods=['POST'])
@login_required
def api_clear_dates():
    cleared = clear_all_masters()
    return jsonify({'success': True, 'cleared': cleared})

@app.route('/api/clear_master', methods=['POST'])
@login_required
def api_clear_master():
    data = request.json or {}
    master = (data.get('master') or '').strip()
    if not master:
        return jsonify({'success': False, 'error': 'Не указан мастер'})
    cleared = clear_one_master(master)
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

def refresh_master_caches_from_admin():
    admin_cache = get_admin_cache() or {}
    all_tickets = []
    for dir_data in admin_cache.get('directions', {}).values():
        all_tickets.extend(dir_data.get('tickets', []))
    for master in MASTERS:
        master_tickets = [t for t in all_tickets if t.get('master') == master and is_active_status(t.get('status'))]
        save_master_cache(master, master_tickets, '')

def queue_admin_status(uid, status_display, note='', skip_report=True, actor='Админ'):
    now = get_msk_now().strftime('%d.%m %H:%M')
    actor = actor or 'Админ'
    if status_display == '⏹️ Обработано':
        mark_bike_missing(uid, actor)
        return
    ticket = find_cached_ticket(uid) or {}
    previous = (ticket.get('note') or '').strip()
    extra = None
    if status_display == '🔧 Эвакуация':
        if is_supply(ticket):
            logger.info(f'Эвакуация запрещена для {uid}: это аккумулятор или зарядка')
            return
        new_status = 'evacuation'
        plain = (note or 'Эвакуация').replace('ЭВАКУАЦИЯ: ', '').strip() or 'Эвакуация'
        line = 'ЭВАКУАЦИЯ: ' + plain
        sheet_note = f'{previous}\n{line}'.strip() if previous else line
        cache_note = sheet_note
        extra = {'evac_reason': plain}
    elif status_display == '🔵 Доделать':
        new_status = 'todo'
        text = (note or '').strip() or 'Доделать'
        line = f'{now}, {actor}. Доделать: {text}'
        sheet_note = f'{previous}\n{line}'.strip() if previous else line
        cache_note = sheet_note
    else:
        status_map = {
            '🟡 В работе': 'pending',
            '✅ Выполнено': 'done',
            '🔵 Доделать': 'todo'
        }
        new_status = status_map.get(status_display, 'pending')
        cache_note = note
        sheet_note = note
    source = 'Заявки'
    if uid in uid_index:
        source = (uid_index[uid].get('ticket') or {}).get('source') or 'Заявки'
    update_ticket_in_admin_cache(uid, new_status, cache_note, extra=extra)
    add_to_queue({
        'uid': uid,
        'source': source,
        'type': 'status_update',
        'data': {
            'status': status_display,
            'new_status': new_status,
            'note': sheet_note,
            'skip_report': skip_report
        }
    })

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
    ticket = find_cached_ticket(uid) or {}
    if status_display == '🔧 Эвакуация' and is_supply(ticket):
        return jsonify({'success': False, 'error': 'Для аккумулятора и зарядки эвакуацию ставить нельзя'})
    try:
        queue_admin_status(uid, status_display, note, skip_report, 'Админ' if session.get('role') == 'admin' else (session.get('master_name') or 'Админ'))
        refresh_master_caches_from_admin()
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
        actor = 'Админ' if session.get('role') == 'admin' else (session.get('master_name') or 'Админ')
        for uid in uids:
            queue_admin_status(uid, status_display, comment, skip_report, actor)
        refresh_master_caches_from_admin()
        return jsonify({'success': True, 'updated': len(uids), 'message': f'Обновлено {len(uids)} заявок'})
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
        actor = session.get('master_name') or 'Админ'
        if action == 'done':
            queue_admin_status(uid, '✅ Выполнено', extra or 'Выполнено админом', True, actor)
        elif action == 'evacuation':
            queue_admin_status(uid, '🔧 Эвакуация', extra or 'Эвакуация', True, actor)
        elif action == 'fail':
            mark_bike_missing(uid, actor)
        elif action == 'todo':
            queue_admin_status(uid, '🔵 Доделать', extra or 'Доделать', True, actor)
        elif action == 'taken':
            note = f'ЗАБРАЛИ: {extra} АКБ'
            update_ticket_in_admin_cache(uid, 'todo', note)
            add_to_queue({
                'uid': uid,
                'source': source_of(uid),
                'type': 'taken_no_replace',
                'data': {'parts': extra, 'note': note, 'master': actor}
            })
        else:
            return jsonify({'success': False, 'error': 'Неизвестное действие'})
        refresh_master_caches_from_admin()
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
def master_groups(name):
    cache_data = get_master_cache(name) or {}
    master_tickets = list(cache_data.get('tickets') or [])
    if not master_tickets and not cache_data:
        tickets = get_tickets_from_sheets()
        master_tickets = [t for t in tickets if clean_name(t.get('master')) == clean_name(name) and is_active_status(t.get('status'))]
        enrich_tickets(master_tickets)
        save_master_cache(name, master_tickets, '')
    else:
        enrich_tickets(master_tickets)
    groups = []
    if master_tickets:
        groups_dict = {}
        for t in master_tickets:
            darks_num = str(t.get('darks') or 'без номера')
            if darks_num not in groups_dict:
                groups_dict[darks_num] = {
                    'darks_number': darks_num,
                    'address': t.get('address') or 'Адрес не указан',
                    'contact': t.get('contact', ''),
                    'tickets': [],
                    'pending': 0,
                    'done': 0
                }
            groups_dict[darks_num]['tickets'].append(t)
            if t.get('status') in ['pending', 'todo', 'processed', 'fail']:
                groups_dict[darks_num]['pending'] += 1
            else:
                groups_dict[darks_num]['done'] += 1
        for darks_num in sorted(groups_dict.keys(), key=lambda x: int(x) if str(x).isdigit() else 999999):
            item = groups_dict[darks_num]
            groups.append({
                'darks_number': item['darks_number'],
                'address': item['address'],
                'contact': item['contact'],
                'pending': item['pending'],
                'done': item['done'],
                'tickets': item['tickets'],
            })
    return groups, master_tickets, cache_data.get('updated_at', '')

@app.route('/master/<name>')
@login_required
def master_overview(name):
    if session.get('master_name') != name:
        return redirect(url_for('login_page'))
    groups, master_tickets, updated_at = master_groups(name)
    for t in master_tickets:
        t['hours_since'] = ticket_hours(t)
        t['is_today_done'] = t.get('status') == 'done'
    route_url = f'/api/build_route_for_master?master={name}'
    return render_template('master_home.html',
                          name=name,
                          groups=groups,
                          total_tickets=len(master_tickets),
                          route_url=route_url,
                          updated_at=updated_at,
                          now=get_msk_now().strftime('%H:%M:%S'))

@app.route('/api/master/<name>/home')
@login_required
def api_master_home(name):
    if session.get('master_name') != name and session.get('role') != 'admin':
        return jsonify({'success': False, 'error': 'Доступ запрещён'}), 403
    groups, master_tickets, updated_at = master_groups(name)
    return jsonify({
        'success': True,
        'total': len(master_tickets),
        'updated_at': updated_at,
        'groups': [{
            'darks_number': g['darks_number'],
            'address': g['address'],
            'pending': g['pending'],
        } for g in groups],
    })

@app.route('/master/<name>/darks/<darks_number>')
@login_required
def master_darks(name, darks_number):
    if session.get('master_name') != name:
        return redirect(url_for('login_page'))
    cache_data = get_master_cache(name)
    if cache_data:
        all_tickets = cache_data.get('tickets', [])
        filtered = [t for t in all_tickets if str(t.get('darks')) == str(darks_number)]
    else:
        tickets = get_tickets_from_sheets()
        filtered = [t for t in tickets if clean_name(t.get('master')) == clean_name(name) and str(t.get('darks')) == str(darks_number) and is_active_status(t.get('status'))]
        enrich_tickets(filtered)
        save_master_cache(name, filtered, '')
    enrich_tickets(filtered)
    
    for t in filtered:
        t['hours_since'] = ticket_hours(t)
    fill_old_bike(filtered)
    
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
        t['hours_since'] = ticket_hours(t)
    pending_count = len([t for t in master_tickets if t.get('status') in ('pending', 'processed', 'fail')])
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
    if isinstance(history, dict):
        history = history.get('items') or history.get('history') or []
    if not isinstance(history, list):
        history = []
    return jsonify({'success': True, 'history': history})

@app.route('/api/master/<name>/trunk')
@login_required
def api_master_trunk(name):
    if session.get('master_name') != name and session.get('role') != 'admin':
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    rows, total_qty = trunk_rows(name)
    return jsonify({'success': True, 'parts': rows, 'total_qty': total_qty})

@app.route('/master/<name>/trunk', methods=['GET', 'POST'])
@login_required
def master_trunk(name):
    if session.get('master_name') != name:
        return redirect(url_for('login_page'))
    notice = ''
    error = ''
    if request.method == 'POST':
        names = request.form.getlist('part')
        qtys = request.form.getlist('qty')
        chosen = set(request.form.getlist('take'))
        loaded = []
        for part_name, qty_raw in zip(names, qtys):
            if part_name not in chosen or part_name not in PARTS_SET:
                continue
            try:
                qty = int(qty_raw or 0)
            except Exception:
                qty = 0
            if qty <= 0:
                continue
            change_trunk(name, part_name, qty)
            queue_part_move(name, 'Взял', part_name, qty)
            loaded.append(f'{part_name} × {qty}')
        if not loaded:
            error = 'Отметьте запчасти и укажите количество'
        else:
            notice = 'Загружено: ' + ', '.join(loaded)
    rows, total_qty = trunk_rows(name)
    stock = [row for row in rows if row['qty'] > 0]
    return render_template(
        'trunk.html',
        name=name,
        parts=rows,
        stock=stock,
        total_qty=total_qty,
        error=error,
        notice=notice,
        now=get_msk_now().strftime('%H:%M:%S'),
    )

@app.route('/admin/trunks')
@login_required
def admin_trunks():
    if session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    masters = []
    for master in MASTERS:
        rows, total_qty = trunk_rows(master)
        stock = [row for row in rows if row['qty'] > 0]
        masters.append({'name': master, 'rows': stock, 'total_qty': total_qty})
    spent = {}
    for move in parts_log():
        if move.get('action') != 'Поставил':
            continue
        key = move.get('name') or ''
        bucket = spent.setdefault(key, {'name': key, 'qty': 0})
        bucket['qty'] += int(move.get('qty') or 0)
    spent_rows = sorted(spent.values(), key=lambda item: item['qty'], reverse=True)
    return render_template('admin_trunks.html', masters=masters, spent=spent_rows, now=get_msk_now().strftime('%H:%M:%S'))

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
    parts_raw = request.form.get('parts_json', '').strip()
    comment = request.form.get('comment', '').strip()
    legacy = request.form.get('parts', '').strip()
    selected = []
    if parts_raw:
        try:
            selected = json.loads(parts_raw)
        except Exception:
            return jsonify({'success': False, 'error': 'Не понял список запчастей'})
        if not isinstance(selected, list):
            selected = []
    ticket = find_cached_ticket(uid) or {}
    raw = (legacy or comment or '').strip()
    if raw.lower() == 'нет' and not selected:
        parts = 'нет'
    elif raw == 'Отказ от эвакуации' and not selected:
        parts = raw
    else:
        ok, parts_text = spend_parts(
            name,
            selected,
            uid,
            ticket.get('gos', ''),
            darks_number,
        )
        if not ok:
            return jsonify({'success': False, 'error': parts_text})
        if not parts_text:
            return jsonify({'success': False, 'error': 'Выберите запчасть или напишите «нет»'})
        parts = parts_text
    event = parts
    update_ticket_in_admin_cache(uid, 'done', event)
    refresh_master_caches_from_admin()
    
    add_to_queue({
        'uid': uid,
        'source': source_of(uid),
        'type': 'done',
        'data': {'parts': event, 'detail': event, 'note': event, 'master': name, 'darks_number': darks_number, 'gos': ticket.get('gos', '')}
    })
    label = ticket_label(ticket)
    if parts == 'Отказ от эвакуации':
        notify_dark_event(darks_number, f'{label} отказ от эвакуации')
    else:
        notify_dark_event(darks_number, f'{label} заявка выполнена')
    return jsonify({'success': True})

@app.route('/master/<name>/darks/<darks_number>/fail/<uid>')
@login_required
def master_fail(name, darks_number, uid):
    if session.get('master_name') != name:
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    mark_bike_missing(uid, name)
    return jsonify({'success': True})

@app.route('/master/<name>/darks/<darks_number>/evacuation/<uid>', methods=['POST'])
@login_required
def master_evacuation(name, darks_number, uid):
    if session.get('master_name') != name:
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    reason = request.form.get('reason', '')
    if not reason.strip():
        return jsonify({'success': False, 'error': 'Не указана причина'})
    ticket = find_cached_ticket(uid) or {}
    if is_supply(ticket):
        return jsonify({'success': False, 'error': 'Для аккумулятора и зарядки эвакуацию ставить нельзя'})
    reason = reason.strip()
    previous = (ticket.get('note') or '').strip()
    line = 'ЭВАКУАЦИЯ: ' + reason
    note = f'{previous}\n{line}'.strip() if previous else line
    update_ticket_in_admin_cache(uid, 'evacuation', note, extra={'evac_reason': reason})
    refresh_master_caches_from_admin()
    add_to_queue({
        'uid': uid,
        'source': source_of(uid),
        'type': 'evacuation',
        'data': {'reason': reason, 'note': note, 'master': name, 'darks_number': darks_number, 'gos': ticket.get('gos', '')}
    })
    label = ticket_label(find_cached_ticket(uid) or {'gos': request.form.get('gos', '')})
    notify_dark_event(darks_number, f'{label} нужно эвакуировать: {reason.strip()}')
    return jsonify({'success': True})

@app.route('/master/<name>/darks/<darks_number>/taken_no_replace/<uid>', methods=['POST'])
@login_required
def master_taken_no_replace(name, darks_number, uid):
    if session.get('master_name') != name:
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    parts = request.form.get('parts', '')
    if not parts or int(parts) <= 0:
        return jsonify({'success': False, 'error': 'Укажите количество'})
    note = f'ЗАБРАЛИ: {parts} АКБ'
    update_ticket_in_admin_cache(uid, 'todo', note)
    refresh_master_caches_from_admin()
    add_to_queue({
        'uid': uid,
        'source': source_of(uid),
        'type': 'taken_no_replace',
        'data': {'parts': parts, 'note': note, 'master': name, 'darks_number': darks_number}
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
    update_ticket_in_admin_cache(uid, 'done', parts)
    refresh_master_caches_from_admin()
    add_to_queue({
        'uid': uid,
        'source': source_of(uid),
        'type': 'replace_yes',
        'data': {'parts': parts, 'master': name, 'darks_number': darks_number}
    })
    return jsonify({'success': True})

@app.route('/master/<name>/darks/<darks_number>/replace_no/<uid>', methods=['POST'])
@login_required
def master_replace_no(name, darks_number, uid):
    if session.get('master_name') != name:
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    reason = (request.form.get('reason') or '').strip() or 'Куратор не предоставил'
    update_ticket_in_admin_cache(uid, 'todo', reason)
    refresh_master_caches_from_admin()
    add_to_queue({
        'uid': uid,
        'source': source_of(uid),
        'type': 'replace_no',
        'data': {'reason': reason, 'master': name, 'darks_number': darks_number}
    })
    return jsonify({'success': True})

# ============================================================
# ТРАНЗИТ - ЗАМЕНА ВЕЛОСИПЕДА
# ============================================================
REPLACEMENT_BOOK_ID = "1s_hXPSWueMAo3W1pgLCG0eHhYKoW0uoIZ6CGWFf55VU"
REPLACEMENT_GID = 1296698244

def append_bike_swap(address, darks_number, old_data, new_data, master='', uid='', kind='Эвакуация'):
    try:
        book = get_gspread_client().open_by_key(REPLACEMENT_BOOK_ID)
    except Exception as e:
        text = str(e).lower()
        if '403' in text or 'permission' in text:
            raise RuntimeError('Нет доступа к таблице замен. Откройте её для route-cache@telegramsenderbot.iam.gserviceaccount.com') from e
        raise
    sheet = None
    for item in book.worksheets():
        title = (item.title or '').strip().lower()
        try:
            gid = int(item.id)
        except Exception:
            gid = item.id
        if title == 'замены вело' or gid == REPLACEMENT_GID:
            sheet = item
            break
    if sheet is None:
        names = ', '.join(item.title for item in book.worksheets())
        raise RuntimeError(f'Лист «Замены вело» не найден. В файле есть: {names}')
    filled = sheet.get('A:I')
    last = 1
    for index, row in enumerate(filled, start=1):
        if any(str(cell).strip() for cell in row):
            last = index
    target = last + 1
    sheet.update(
        f'A{target}:I{target}',
        [[
            get_msk_now().strftime('%d.%m.%Y %H:%M:%S'),
            address or '',
            str(darks_number or ''),
            (old_data or {}).get('serial', ''),
            (old_data or {}).get('gos', ''),
            (old_data or {}).get('iot', ''),
            (new_data or {}).get('serial', ''),
            (new_data or {}).get('gos', ''),
            (new_data or {}).get('iot', ''),
        ]],
        value_input_option='USER_ENTERED',
    )
    try:
        if sheet.col_count < 20:
            sheet.add_cols(20 - sheet.col_count)
        if not (sheet.acell('R1').value or '').strip():
            sheet.update('R1:T1', [['Мастер', 'ID заявки', 'Тип']], value_input_option='RAW')
        sheet.update(f'R{target}:T{target}', [[master or '', uid or '', kind or 'Эвакуация']], value_input_option='USER_ENTERED')
    except Exception as e:
        logger.warning(f'Строка {target} записана, мастер и номер заявки в конец не встали: {e}')
    logger.info(f"✅ Замена записана в «{sheet.title}», строка {target}, даркстор {darks_number}")

def write_transit_replacement(uid, master_name, darks_number, address, old_data, new_data):
    try:
        append_bike_swap(address, darks_number, old_data, new_data, master_name, uid, 'Замена')
        sheet_client = get_sheet_client()
        now = get_msk_now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            report_sheet = sheet_client.worksheet("Эвакуация Транзит")
        except Exception:
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
        export_removal_reason(old_data, evacuation_reason(find_cached_ticket(uid) or {'desc': 'Замена'}))
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
            'source': source_of(uid),
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

def export_removal_reason(old_data, reason):
    try:
        book = get_gspread_client().open_by_key('1BoZ7GFmL2q56bjqt1CFVV_PeqnKVaeb3anTJkf0s6xA')
        sheet = None
        for item in book.worksheets():
            title = (item.title or '').strip().lower()
            try:
                gid = int(item.id)
            except Exception:
                gid = item.id
            if title == 'общее' or gid == 1658833390:
                sheet = item
                break
        if sheet is None:
            logger.warning('Лист «Общее» не найден')
            return
        rows = sheet.get('A:B')
        iot = re.sub(r'\s+', '', str((old_data or {}).get('iot') or '')).lower()
        serial = re.sub(r'\s+', '', str((old_data or {}).get('serial') or '')).lower()
        iot_row = None
        serial_row = None
        for index, row in enumerate(rows, start=1):
            if index == 1:
                continue
            frame = re.sub(r'\s+', '', str(row[0] if row else '')).lower()
            module = re.sub(r'\s+', '', str(row[1] if len(row) > 1 else '')).lower()
            if iot and module == iot:
                iot_row = index
                break
            if serial and frame == serial and serial_row is None:
                serial_row = index
        target = iot_row or serial_row
        if not target:
            logger.warning(f'Велосипед не найден в «Общее»: IoT {iot or "-"}, рама {serial or "-"}')
            return
        sheet.update(f'J{target}', [[(reason or '').strip() or 'Замена']], value_input_option='USER_ENTERED')
        logger.info(f'Причина вывоза записана в «Общее», строка {target}')
    except Exception as e:
        logger.error(f'Не записал причину вывоза: {e}')

def write_evacuation_to_sheet(uid, master_name, darks_number, address, old_data, new_data, reason=''):
    try:
        append_bike_swap(address, darks_number, old_data, new_data, master_name, uid, 'Эвакуация')
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
        export_removal_reason(old_data, reason)
        logger.info(f"✅ Записана эвакуация для заявки {uid}")
        return True, ''
    except Exception as e:
        logger.error(f"Ошибка записи эвакуации: {e}")
        return False, str(e)

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
        
        ticket = find_cached_ticket(uid) or {}
        reason = evacuation_reason(ticket)
        saved, err = write_evacuation_to_sheet(uid, master_name, darks_number, address, old_data, new_data, reason)
        if not saved:
            return jsonify({'success': False, 'error': err or 'Не удалось записать замену в таблицу'})
        
        update_ticket_in_admin_cache(uid, 'done', 'Заменен при эвакуации', '✅ Выполнено')
        update_status_in_google_sheets(uid, '✅ Выполнено', 'Заменен при эвакуации')
        notify_dark_event(darks_number, f'{old_data.get("gos") or ticket_label(ticket)} заменен и готов к эксплуатации')
        
        add_to_queue({
            'uid': uid,
            'source': source_of(uid),
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
                'source': t.get('source') or 'Заявки',
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

def load_iot_source(force_reload=False):
    """Загружает список IOT из листа 'Все IoT' (кэш в памяти + JSON)"""
    global _iot_source_memory
    
    # Если не force_reload и есть кэш в памяти — вернуть
    if not force_reload and _iot_source_memory is not None:
        return _iot_source_memory
    
    # Если не force_reload и есть JSON — прочитать
    if not force_reload:
        cache = read_cache(IOT_SOURCE_FILE)
        if cache and 'iot_list' in cache:
            _iot_source_memory = cache['iot_list']
            logger.info(f"✅ IOT Source загружен из кэша: {len(_iot_source_memory)} записей")
            return _iot_source_memory
    
    # Иначе — тянем из Google Sheets
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
        _iot_source_memory = iot_data
        logger.info(f"✅ IOT Source загружен из Google Sheets: {len(iot_data)} записей")
        return iot_data
    except Exception as e:
        logger.error(f"Ошибка загрузки IOT Source: {e}")
        return {}


def get_iot_source():
    """Возвращает кэш IOT Source (из памяти)"""
    return load_iot_source(force_reload=False)


def load_iot_vehicles(force_reload=False):
    """Загружает данные велосипедов из листа 'Учет вело ВВ' (кэш в памяти + JSON)"""
    global _iot_vehicles_memory
    
    # Если не force_reload и есть кэш в памяти — вернуть
    if not force_reload and _iot_vehicles_memory is not None:
        return _iot_vehicles_memory
    
    # Если не force_reload и есть JSON — прочитать
    if not force_reload:
        cache = read_cache(IOT_VEHICLES_FILE)
        if cache and 'vehicles' in cache:
            _iot_vehicles_memory = cache['vehicles']
            logger.info(f"✅ IOT Vehicles загружен из кэша: {len(_iot_vehicles_memory)} записей")
            return _iot_vehicles_memory
    
    # Иначе — тянем из Google Sheets
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
        
        cache_data = {
            'updated_at': get_msk_now().strftime('%Y-%m-%d %H:%M:%S'),
            'vehicles': vehicles
        }
        write_cache(IOT_VEHICLES_FILE, cache_data)
        _iot_vehicles_memory = vehicles
        logger.info(f"✅ IOT Vehicles загружен из Google Sheets: {len(vehicles)} записей")
        return vehicles
    except Exception as e:
        logger.error(f"Ошибка загрузки IOT Vehicles: {e}")
        return {}


def get_iot_vehicles():
    """Возвращает кэш IOT Vehicles (из памяти)"""
    return load_iot_vehicles(force_reload=False)


def reset_iot_memory():
    """Сбрасывает кэш в памяти (при обновлении данных)"""
    global _iot_source_memory, _iot_vehicles_memory
    _iot_source_memory = None
    _iot_vehicles_memory = None


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
        
        for idx, row in enumerate(rows, start=1):
            if len(row) > 0 and row[0].strip() == old_iot:
                now = get_msk_now()
                date_str = now.strftime('%d.%m')
                text = f"{date_str} поменяли на прошитый (новый IOT: {new_iot})"
                sheet.update_cell(idx, 10, text)
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
    """Главная страница IOT-мастера (МГНОВЕННО из кэша)"""
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
    
    # Читаем из кэша (мгновенно)
    vehicles_data = get_iot_vehicles()
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
    """Страница даркстора для IOT-мастера (МГНОВЕННО из кэша)"""
    if session.get('role') != 'iot':
        return redirect(url_for('login_page'))
    
    bag_data = get_iot_bag()
    bag_items = [item.get('iot') for item in bag_data.get('bag', [])]
    
    # Читаем из кэша (мгновенно)
    vehicles_data = get_iot_vehicles()
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
    """Загружает IOT в багажник БЕЗ проверок"""
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
        
        if not any(item.get('iot') == iot for item in current_bag):
            return jsonify({'success': False, 'error': f'IOT {iot} не найден в багажнике'})
        
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
    """Обновляет ОБА кэша: iot_source и iot_vehicles"""
    if session.get('role') != 'iot':
        return jsonify({'success': False, 'error': 'Доступ запрещён'})
    
    try:
        # Сбрасываем кэш в памяти
        reset_iot_memory()
        
        # Принудительно тянем из Google Sheets
        source_data = load_iot_source(force_reload=True)
        vehicles_data = load_iot_vehicles(force_reload=True)
        
        logger.info(f"✅ Обновлено: IOT Source = {len(source_data)}, IOT Vehicles = {len(vehicles_data)}")
        
        return jsonify({
            'success': True,
            'source_count': len(source_data),
            'vehicles_count': len(vehicles_data)
        })
    except Exception as e:
        logger.error(f"Ошибка синхронизации IOT: {e}")
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
        
        # 3. Обновляем кэш в памяти: помечаем старый IOT как заменённый
        global _iot_source_memory
        if _iot_source_memory and old_iot in _iot_source_memory:
            now_str = get_msk_now().strftime('%d.%m')
            _iot_source_memory[old_iot]['replaced_text'] = f"{now_str} поменяли на прошитый (новый IOT: {new_iot})"
            # Сохраняем в JSON
            cache_data = {
                'updated_at': get_msk_now().strftime('%Y-%m-%d %H:%M:%S'),
                'iot_list': _iot_source_memory
            }
            write_cache(IOT_SOURCE_FILE, cache_data)
            logger.info(f"✅ Кэш в памяти обновлён: {old_iot} помечен как заменённый")
        
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
        tickets = get_tickets_from_sheets(force=True)
        publish_caches(tickets)
        logger.info(f"✅ Кэши созданы: {len(tickets)} заявок")
        
        # Создаём кэш IOT при старте
        logger.info("📂 Создание IOT кэшей...")
        try:
            load_iot_source(force_reload=True)
            load_iot_vehicles(force_reload=True)
            logger.info("   ✅ IOT кэши созданы")
        except Exception as e:
            logger.error(f"   ❌ Ошибка создания IOT кэшей: {e}")
        
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
