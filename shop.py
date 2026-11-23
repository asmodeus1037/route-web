import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from flask import Blueprint, redirect, render_template, request, session, url_for

SHOP_BOOK_ID = '1g_TmLwaSeEDjYEp_fAzxhbEbsHxup79LcmQ2M7kB00g'
MSK = timezone(timedelta(hours=3))
LOCK = threading.Lock()

STATUSES = (
    'ОЖИДАЕТ РЕМОНТА',
    'РЕМОНТ',
    'ОЖИДАЕТ ЗАПЧАСТИ',
    'НА ПРОВЕРКУ',
    'НА ДОРАБОТКУ',
    'СВОБОДЕН',
)

PARTS = [
    'Камера',
    'Покрышка',
    'Колодки',
    'Ручка газа',
    'Ручка тормоза',
    'Подножка',
    'Фара',
    'Крыло',
    'Мотор-колесо',
]

HEADERS = {
    'Велосипеды': ['S/N', 'IoT', 'Гос', 'Тип', 'Статус', 'Причина вывоза', 'Комментарий', 'Мастер', 'Приёмщик', 'Обновлено'],
    'История': ['Время', 'S/N', 'Было', 'Стало', 'Кто', 'Комментарий', 'Запчасти'],
    'Запчасти': ['Название', 'Цена'],
    'Списание': ['Время', 'S/N', 'Кто', 'Запчасть', 'Количество'],
}

shop_bp = Blueprint('shop', __name__)


def now_text():
    return datetime.now(MSK).strftime('%d.%m.%Y %H:%M')


def shop_file():
    folder = '/data/cache' if os.path.isdir('/data') else os.path.join(os.path.dirname(__file__), 'cache')
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, 'shop.json')


def empty_state():
    seed_path = os.path.join(os.path.dirname(__file__), 'shop_seed.json')
    bikes = []
    if os.path.exists(seed_path):
        with open(seed_path, encoding='utf-8') as handle:
            bikes = json.load(handle)
    return {'bikes': bikes, 'history': [], 'uses': []}


def load_state():
    path = shop_file()
    if not os.path.exists(path):
        state = empty_state()
        save_state(state, sync=False)
        return state
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def service_email():
    path = '/data/credentials.json'
    if not os.path.exists(path):
        return ''
    try:
        with open(path, encoding='utf-8') as handle:
            return (json.load(handle).get('client_email') or '').strip()
    except Exception:
        return ''


def push_sheet(state):
    try:
        mirror_to_sheet(state)
        return ''
    except Exception as exc:
        logging.getLogger(__name__).warning('Цех не записал таблицу: %s', exc)
        return str(exc)


def save_state(state, sync=True):
    path = shop_file()
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8') as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    os.replace(temp, path)
    if sync:
        try:
            mirror_to_sheet(state)
        except Exception:
            pass


def find_bike(state, sn):
    for bike in state.get('bikes') or []:
        if bike.get('sn') == sn:
            return bike
    return None


def add_event(state, bike, new_status, who, comment='', parts=''):
    state.setdefault('history', []).append({
        'time': now_text(),
        'sn': bike.get('sn') or '',
        'old': bike.get('status') or '',
        'new': new_status,
        'who': who,
        'comment': comment,
        'parts': parts,
    })
    bike['status'] = new_status
    bike['updated'] = now_text()
    if comment:
        bike['info'] = comment


def mirror_to_sheet(state):
    from app import get_gspread_client
    book = get_gspread_client().open_by_key(SHOP_BOOK_ID)
    existing = {item.title: item for item in book.worksheets()}
    for title, header in HEADERS.items():
        sheet = existing.get(title)
        if sheet is None:
            sheet = book.add_worksheet(title, rows=200, cols=len(header) + 2)
        if title == 'Велосипеды':
            rows = [header]
            for bike in state.get('bikes') or []:
                rows.append([
                    bike.get('sn') or '', bike.get('iot') or '', bike.get('gos') or '',
                    bike.get('kind') or '', bike.get('status') or '', bike.get('reason') or '',
                    bike.get('info') or '', bike.get('master') or '', bike.get('checker') or '',
                    bike.get('updated') or '',
                ])
        elif title == 'История':
            rows = [header]
            for item in state.get('history') or []:
                rows.append([
                    item.get('time') or '', item.get('sn') or '', item.get('old') or '',
                    item.get('new') or '', item.get('who') or '', item.get('comment') or '',
                    item.get('parts') or '',
                ])
        elif title == 'Запчасти':
            rows = [header] + [[name, ''] for name in PARTS]
        else:
            rows = [header]
            for item in state.get('uses') or []:
                rows.append([
                    item.get('time') or '', item.get('sn') or '', item.get('who') or '',
                    item.get('part') or '', item.get('qty') or '1',
                ])
        sheet.clear()
        sheet.update(values=rows, range_name='A1', value_input_option='USER_ENTERED')


def guard():
    role = session.get('role')
    if role not in ('shop', 'shop_admin'):
        return redirect(url_for('login_page'))
    return None


def visible(bike, role, name):
    status = bike.get('status') or ''
    if role == 'shop_admin':
        return status != 'СВОБОДЕН'
    if status in ('ОЖИДАЕТ РЕМОНТА', 'НА ДОРАБОТКУ'):
        return True
    return (bike.get('master') or '') == name and status in ('РЕМОНТ', 'ОЖИДАЕТ ЗАПЧАСТИ', 'НА ПРОВЕРКУ')


@shop_bp.route('/shop')
def home():
    blocked = guard()
    if blocked:
        return blocked
    state = load_state()
    role = session.get('role')
    name = session.get('master_name') or ''
    query = (request.args.get('q') or '').strip().lower()
    bikes = []
    for bike in state.get('bikes') or []:
        if not visible(bike, role, name):
            continue
        blob = ' '.join([
            bike.get('sn') or '', bike.get('gos') or '', bike.get('iot') or '', bike.get('kind') or '',
        ]).lower()
        if query and query not in blob:
            continue
        bikes.append(bike)
    order = {status: index for index, status in enumerate(STATUSES)}
    bikes.sort(key=lambda bike: (order.get(bike.get('status'), 9), bike.get('sn') or ''))
    counts = {}
    for bike in state.get('bikes') or []:
        counts[bike.get('status') or ''] = counts.get(bike.get('status') or '', 0) + 1
    history = {}
    for item in state.get('history') or []:
        history.setdefault(item.get('sn') or '', []).append(item)
    sheet_error = push_sheet(state)
    return render_template(
        'shop.html',
        bikes=bikes,
        counts=counts,
        history=history,
        parts=PARTS,
        role=role,
        name=name,
        query=request.args.get('q') or '',
        book_id=SHOP_BOOK_ID,
        sheet_error=sheet_error,
        service_email=service_email(),
    )


def posted_sn():
    return (request.form.get('sn') or '').strip()


@shop_bp.route('/shop/take', methods=['POST'])
def take():
    blocked = guard()
    if blocked:
        return blocked
    if session.get('role') != 'shop':
        return redirect(url_for('shop.home'))
    name = session.get('master_name') or ''
    with LOCK:
        state = load_state()
        bike = find_bike(state, posted_sn())
        if bike and bike.get('status') in ('ОЖИДАЕТ РЕМОНТА', 'НА ДОРАБОТКУ', 'ОЖИДАЕТ ЗАПЧАСТИ'):
            if bike.get('status') != 'РЕМОНТ':
                bike['master'] = name
                add_event(state, bike, 'РЕМОНТ', name, bike.get('info') or '')
                save_state(state)
    return redirect(url_for('shop.home'))


@shop_bp.route('/shop/finish', methods=['POST'])
def finish():
    blocked = guard()
    if blocked:
        return blocked
    name = session.get('master_name') or ''
    comment = (request.form.get('comment') or '').strip()
    chosen = [part for part in PARTS if request.form.get('part_' + part) == '1']
    with LOCK:
        state = load_state()
        bike = find_bike(state, posted_sn())
        if bike and bike.get('master') == name and bike.get('status') in ('РЕМОНТ', 'ОЖИДАЕТ ЗАПЧАСТИ', 'НА ДОРАБОТКУ'):
            if not comment:
                return redirect(url_for('shop.home'))
            for part in chosen:
                state.setdefault('uses', []).append({
                    'time': now_text(), 'sn': bike.get('sn'), 'who': name, 'part': part, 'qty': '1',
                })
            add_event(state, bike, 'НА ПРОВЕРКУ', name, comment, ', '.join(chosen))
            save_state(state)
    return redirect(url_for('shop.home'))


@shop_bp.route('/shop/wait-parts', methods=['POST'])
def wait_parts():
    blocked = guard()
    if blocked:
        return blocked
    name = session.get('master_name') or ''
    comment = (request.form.get('comment') or '').strip() or 'Нет запчасти'
    with LOCK:
        state = load_state()
        bike = find_bike(state, posted_sn())
        if bike and bike.get('master') == name and bike.get('status') == 'РЕМОНТ':
            add_event(state, bike, 'ОЖИДАЕТ ЗАПЧАСТИ', name, comment)
            save_state(state)
    return redirect(url_for('shop.home'))


@shop_bp.route('/shop/approve', methods=['POST'])
def approve():
    blocked = guard()
    if blocked:
        return blocked
    if session.get('role') != 'shop_admin':
        return redirect(url_for('shop.home'))
    name = session.get('master_name') or ''
    with LOCK:
        state = load_state()
        bike = find_bike(state, posted_sn())
        if bike and bike.get('status') == 'НА ПРОВЕРКУ':
            bike['checker'] = name
            add_event(state, bike, 'СВОБОДЕН', name, 'Принято')
            save_state(state)
    return redirect(url_for('shop.home'))


@shop_bp.route('/shop/rework', methods=['POST'])
def rework():
    blocked = guard()
    if blocked:
        return blocked
    if session.get('role') != 'shop_admin':
        return redirect(url_for('shop.home'))
    name = session.get('master_name') or ''
    comment = (request.form.get('comment') or '').strip()
    with LOCK:
        state = load_state()
        bike = find_bike(state, posted_sn())
        if bike and bike.get('status') == 'НА ПРОВЕРКУ' and comment:
            bike['checker'] = name
            add_event(state, bike, 'НА ДОРАБОТКУ', name, comment)
            save_state(state)
    return redirect(url_for('shop.home'))
