"""Копия данных в PostgreSQL.

Адрес базы берётся из настроек Amvera, не из кода.
Если настройки нет или база недоступна, сайт продолжает работать через таблицу.
"""
import logging
import os
import threading
import time
from datetime import datetime
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    psycopg2 = None
    execute_values = None

_lock = threading.Lock()
_wake = threading.Event()
_started = False
_catalog = []
_latest_tickets = None
_latest_darks = None
_latest_trunks = None
_ticket_updates = []
_moves = []
_events = []
_replacements = []
_state = {
    'configured': False,
    'connected': False,
    'last_error': '',
    'last_sync': '',
    'tickets': 0,
}


def _database_url():
    url = (os.environ.get('DATABASE_URL') or '').strip()
    if url:
        return url
    host = (os.environ.get('DB_HOST') or os.environ.get('PGHOST') or '').strip()
    user = (os.environ.get('DB_USER') or os.environ.get('PGUSER') or '').strip()
    password = os.environ.get('DB_PASSWORD') or os.environ.get('PGPASSWORD') or ''
    name = (os.environ.get('DB_NAME') or os.environ.get('PGDATABASE') or '').strip()
    port = (os.environ.get('DB_PORT') or os.environ.get('PGPORT') or '5432').strip()
    if not (host and user and name and password):
        return ''
    sslmode = (os.environ.get('DB_SSLMODE') or 'prefer').strip()
    return (
        f'postgresql://{quote_plus(user)}:{quote_plus(password)}'
        f'@{host}:{port}/{name}?sslmode={sslmode}&connect_timeout=4'
    )


def configure(parts):
    global _catalog
    _catalog = [(name, int(price)) for name, price in (parts or [])]


def start():
    global _started
    url = _database_url()
    with _lock:
        _state['configured'] = bool(url)
        if _started:
            return
        _started = True
    if not url:
        logger.info('База не настроена. Сайт работает только с Google Таблицей.')
        return
    if psycopg2 is None:
        _state['last_error'] = 'Не установлена библиотека psycopg2'
        logger.error('База не подключена: нет psycopg2')
        return
    threading.Thread(target=_worker, name='postgres-mirror', daemon=True).start()


def status():
    with _lock:
        return dict(_state)


def mirror_event(task):
    """Любое нажатие мастера или админа: кто, что сделал, с какой заявкой."""
    if not _state['configured'] or not isinstance(task, dict) or not task.get('uid'):
        return
    data = task.get('data') or {}
    row = (
        task.get('uid') or '',
        task.get('type') or '',
        task.get('source') or '',
        data.get('master') or '',
        str(data.get('darks_number') or data.get('darks') or ''),
        data.get('gos') or '',
        data.get('note') or data.get('parts') or data.get('reason') or data.get('status') or '',
        data.get('new_status') or '',
    )
    with _lock:
        _events.append(row)
    _wake.set()


def mirror_replacement(item):
    """Замена велосипеда. В таблицу замен уходит копия, здесь оригинал."""
    if not _state['configured'] or not isinstance(item, dict):
        return
    old = item.get('old') or {}
    new = item.get('new') or {}
    row = (
        item.get('uid') or '',
        item.get('master') or '',
        str(item.get('darks') or ''),
        item.get('address') or '',
        item.get('kind') or '',
        old.get('serial') or '',
        old.get('gos') or '',
        old.get('iot') or '',
        new.get('serial') or '',
        new.get('gos') or '',
        new.get('iot') or '',
        item.get('reason') or '',
    )
    with _lock:
        _replacements.append(row)
    _wake.set()


def write_statuses_now(tickets):
    """Сразу записать статус, мастера и комментарий. Не через очередь."""
    if not _state['configured'] or psycopg2 is None:
        raise RuntimeError('База не настроена')
    rows = []
    for ticket in tickets or []:
        uid = (ticket or {}).get('uid')
        if not uid:
            continue
        rows.append((
            ticket.get('status') or '',
            ticket.get('master') or '',
            ticket.get('note') or '',
            ticket.get('evac_reason') or '',
            ticket.get('timer_from') or '',
            str(uid),
        ))
    rows = _unique_last(rows, 5)
    if not rows:
        return 0
    conn = _connect()
    try:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                UPDATE tickets AS t SET
                    status = v.status,
                    master = v.master,
                    note = v.note,
                    evac_reason = v.evac_reason,
                    timer_from = v.timer_from,
                    updated_at = now()
                FROM (VALUES %s) AS v(status, master, note, evac_reason, timer_from, uid)
                WHERE t.uid = v.uid
                """,
                rows,
                page_size=500,
            )
            changed = cur.rowcount
        conn.commit()
        with _lock:
            _state['connected'] = True
            _state['last_error'] = ''
            _state['last_sync'] = time.strftime('%Y-%m-%d %H:%M:%S')
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_tickets():
    """Прочитать заявки из базы. None — базы нет, [] — база пустая."""
    if not _state['configured'] or psycopg2 is None:
        return None
    url = _database_url()
    if not url:
        return None
    conn = None
    try:
        conn = psycopg2.connect(url)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT uid, source, darks, type, bike_type, supplier, description,
                       gos, contact, created_at, timer_from, master, status, note,
                       evac_reason, direction, address, parts, row_index
                FROM tickets
            """)
            fetched = cur.fetchall()
        tickets = []
        for row in fetched:
            status = row[12] or ''
            tickets.append({
                'uid': row[0] or '',
                'source': row[1] or '',
                'darks': row[2] or '',
                'type': row[3] or '',
                'bike_type': row[4] or '',
                'bike_subtype': '',
                'supplier': row[5] or '',
                'desc': row[6] or '',
                'display_desc': row[6] or '',
                'gos': row[7] or '',
                'contact': row[8] or '',
                'created': row[9] or '',
                'timer_from': row[10] or '',
                'master': row[11] or '',
                'status': status,
                'note': row[13] or '',
                'evac_reason': row[14] or '',
                'direction': row[15] or '',
                'address': row[16] or '',
                'parts': row[17] or '',
                'row_index': row[18] or 0,
                'coords': '',
                'sent_date': '',
            })
        with _lock:
            _state['connected'] = True
            _state['last_error'] = ''
            _state['tickets'] = len(tickets)
        return tickets
    except Exception as e:
        logger.warning(f'Не прочитал заявки из базы: {e}')
        with _lock:
            _state['connected'] = False
            _state['last_error'] = str(e)[:300]
        return None
    finally:
        if conn is not None:
            conn.close()


def mirror_tickets(tickets):
    if not _state['configured']:
        return
    rows = [_ticket_row(t) for t in tickets or []]
    rows = [row for row in rows if row]
    with _lock:
        global _latest_tickets
        _latest_tickets = rows
    _wake.set()


def mirror_ticket(ticket):
    row = _ticket_row(ticket)
    if not row or not _state['configured']:
        return
    with _lock:
        _ticket_updates.append(row)
    _wake.set()


def mirror_darks(darks):
    if not _state['configured'] or not darks:
        return
    rows = []
    for number, info in darks.items():
        info = info or {}
        rows.append((
            str(number),
            info.get('address') or '',
            info.get('direction') or '',
            info.get('coords') or '',
            info.get('master') or '',
            info.get('master_tg') or '',
        ))
    with _lock:
        global _latest_darks
        _latest_darks = rows
    _wake.set()


def mirror_trunks(data):
    if not _state['configured'] or not isinstance(data, dict):
        return
    rows = []
    for master, bag in data.items():
        if not isinstance(bag, dict):
            continue
        for name, qty in bag.items():
            try:
                amount = int(qty or 0)
            except (TypeError, ValueError):
                amount = 0
            rows.append((str(master), str(name), amount))
    with _lock:
        global _latest_trunks
        _latest_trunks = rows
    _wake.set()


def mirror_move(move):
    if not _state['configured'] or not isinstance(move, dict):
        return
    row = (
        move.get('time') or '',
        move.get('master') or '',
        move.get('action') or '',
        move.get('name') or '',
        int(move.get('qty') or 0),
        int(move.get('price') or 0),
        int(move.get('sum') or 0),
        move.get('uid') or '',
        move.get('gos') or '',
        str(move.get('darks') or ''),
    )
    with _lock:
        _moves.append(row)
    _wake.set()


def _ticket_row(ticket):
    if not ticket or not ticket.get('uid'):
        return None
    row_index = ticket.get('row_index')
    try:
        row_index = int(row_index) if row_index not in (None, '') else None
    except (TypeError, ValueError):
        row_index = None
    return (
        str(ticket.get('uid')),
        ticket.get('source') or '',
        str(ticket.get('darks') or ''),
        ticket.get('type') or '',
        ticket.get('bike_type') or '',
        ticket.get('supplier') or '',
        ticket.get('desc') or ticket.get('display_desc') or '',
        ticket.get('gos') or '',
        ticket.get('contact') or '',
        ticket.get('created') or '',
        ticket.get('timer_from') or '',
        ticket.get('master') or '',
        ticket.get('status') or '',
        ticket.get('note') or '',
        ticket.get('evac_reason') or '',
        ticket.get('direction') or '',
        ticket.get('address') or '',
        ticket.get('parts') or '',
        row_index,
    )


def _connect():
    conn = psycopg2.connect(_database_url())
    conn.autocommit = False
    return conn


def _ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                uid TEXT PRIMARY KEY,
                source TEXT,
                darks TEXT,
                type TEXT,
                bike_type TEXT,
                supplier TEXT,
                description TEXT,
                gos TEXT,
                contact TEXT,
                created_at TEXT,
                timer_from TEXT,
                master TEXT,
                status TEXT,
                note TEXT,
                evac_reason TEXT,
                direction TEXT,
                address TEXT,
                parts TEXT,
                row_index INTEGER,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS tickets_status_idx ON tickets (status)")
        cur.execute("CREATE INDEX IF NOT EXISTS tickets_master_idx ON tickets (master)")
        cur.execute("CREATE INDEX IF NOT EXISTS tickets_darks_idx ON tickets (darks)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS darkstores (
                number TEXT PRIMARY KEY,
                address TEXT,
                direction TEXT,
                coords TEXT,
                master TEXT,
                master_tg TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trunks (
                master TEXT NOT NULL,
                part_name TEXT NOT NULL,
                qty INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (master, part_name)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS part_moves (
                id BIGSERIAL PRIMARY KEY,
                happened_at TEXT,
                master TEXT,
                action TEXT,
                part_name TEXT,
                qty INTEGER,
                price INTEGER,
                total INTEGER,
                uid TEXT,
                gos TEXT,
                darks TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS work_log (
                id BIGSERIAL PRIMARY KEY,
                uid TEXT,
                action TEXT,
                source TEXT,
                master TEXT,
                darks TEXT,
                gos TEXT,
                note TEXT,
                new_status TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS replacements (
                id BIGSERIAL PRIMARY KEY,
                uid TEXT,
                master TEXT,
                darks TEXT,
                address TEXT,
                kind TEXT,
                old_serial TEXT,
                old_gos TEXT,
                old_iot TEXT,
                new_serial TEXT,
                new_gos TEXT,
                new_iot TEXT,
                reason TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS parts_catalog (
                name TEXT PRIMARY KEY,
                price INTEGER NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS shift_reports (
                id BIGSERIAL PRIMARY KEY,
                master TEXT NOT NULL,
                shift_date DATE NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                mileage TEXT,
                expenses TEXT,
                status TEXT,
                comment TEXT,
                closed_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS shift_reports_master_day
            ON shift_reports (master, shift_date)
        """)
        if _catalog:
            execute_values(
                cur,
                """
                INSERT INTO parts_catalog (name, price) VALUES %s
                ON CONFLICT (name) DO UPDATE SET price = EXCLUDED.price
                """,
                _unique_last(_catalog, 0),
            )
    conn.commit()


def save_shift_report(report):
    """Сразу пишет отчёт смены. Если базы нет, сайт всё равно хранит свою копию."""
    if not _state.get('configured') or psycopg2 is None:
        return False
    conn = None
    try:
        conn = _connect()
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO shift_reports (
                    master, shift_date, started_at, finished_at, mileage,
                    expenses, status, comment, closed_count
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (master, shift_date) DO UPDATE SET
                    started_at = EXCLUDED.started_at,
                    finished_at = EXCLUDED.finished_at,
                    mileage = EXCLUDED.mileage,
                    expenses = EXCLUDED.expenses,
                    status = EXCLUDED.status,
                    comment = EXCLUDED.comment,
                    closed_count = EXCLUDED.closed_count,
                    created_at = now()
                """,
                (
                    report.get('master') or '',
                    report.get('date') or '',
                    report.get('start') or '',
                    report.get('end') or '',
                    report.get('mileage') or '',
                    report.get('expenses') or '',
                    report.get('status') or '',
                    report.get('comment') or '',
                    int(report.get('closed_count') or 0),
                ),
            )
        conn.commit()
        return True
    except Exception as exc:
        logger.warning('Отчёт смены в базу не записался: %s', exc)
        if conn is not None:
            conn.rollback()
        return False
    finally:
        if conn is not None:
            conn.close()


def close_hours(days=60):
    """Среднее время от создания заявки до статуса «выполнено», часы."""
    if not _state.get('configured') or psycopg2 is None:
        return {}
    conn = None
    try:
        conn = _connect()
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(NULLIF(w.master, ''), NULLIF(t.master, ''), ''),
                       w.created_at,
                       t.created_at
                FROM work_log w
                LEFT JOIN tickets t ON t.uid = w.uid
                WHERE w.created_at > now() - make_interval(days => %s)
                  AND w.new_status = 'done'
                """,
                (int(days),),
            )
            fetched = cur.fetchall()
        conn.commit()
    except Exception as exc:
        logger.warning('Среднее время из базы: %s', exc)
        if conn is not None:
            conn.rollback()
        return {}
    finally:
        if conn is not None:
            conn.close()
    buckets = {}
    for master, closed_at, opened in fetched:
        master = (master or '').strip()
        if not master or closed_at is None or not opened:
            continue
        try:
            opened_at = datetime.strptime(str(opened)[:19], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
        closed_naive = closed_at.replace(tzinfo=None) if getattr(closed_at, 'tzinfo', None) else closed_at
        hours = (closed_naive - opened_at).total_seconds() / 3600
        if hours < 0 or hours > 24 * 21:
            continue
        bucket = buckets.setdefault(master, [])
        bucket.append(hours)
    result = {}
    for master, values in buckets.items():
        result[master] = round(sum(values) / len(values), 1)
    return result


def _unique_last(rows, indexes=0):
    """В одной пачке не должно быть двух строк с одним и тем же ключом."""
    if isinstance(indexes, int):
        indexes = (indexes,)
    found = {}
    for row in rows or []:
        if not row:
            continue
        found[tuple(row[i] for i in indexes)] = row
    return list(found.values())


def _upsert_tickets(conn, rows):
    rows = _unique_last(rows, 0)
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO tickets (
                uid, source, darks, type, bike_type, supplier, description, gos, contact,
                created_at, timer_from, master, status, note, evac_reason, direction,
                address, parts, row_index, updated_at
            ) VALUES %s
            ON CONFLICT (uid) DO UPDATE SET
                source = EXCLUDED.source,
                darks = EXCLUDED.darks,
                type = EXCLUDED.type,
                bike_type = EXCLUDED.bike_type,
                supplier = EXCLUDED.supplier,
                description = EXCLUDED.description,
                gos = EXCLUDED.gos,
                contact = EXCLUDED.contact,
                created_at = EXCLUDED.created_at,
                timer_from = EXCLUDED.timer_from,
                master = EXCLUDED.master,
                status = EXCLUDED.status,
                note = EXCLUDED.note,
                evac_reason = EXCLUDED.evac_reason,
                direction = EXCLUDED.direction,
                address = EXCLUDED.address,
                parts = EXCLUDED.parts,
                row_index = EXCLUDED.row_index,
                updated_at = now()
            """,
            [row + (datetime.utcnow(),) for row in rows],
            page_size=300,
        )
    conn.commit()


def _upsert_darks(conn, rows):
    rows = _unique_last(rows, 0)
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO darkstores (number, address, direction, coords, master, master_tg, updated_at)
            VALUES %s
            ON CONFLICT (number) DO UPDATE SET
                address = EXCLUDED.address,
                direction = EXCLUDED.direction,
                coords = EXCLUDED.coords,
                master = EXCLUDED.master,
                master_tg = EXCLUDED.master_tg,
                updated_at = now()
            """,
            [row + (datetime.utcnow(),) for row in rows],
            page_size=300,
        )
    conn.commit()


def _upsert_trunks(conn, rows):
    rows = _unique_last(rows, (0, 1))
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO trunks (master, part_name, qty, updated_at) VALUES %s
            ON CONFLICT (master, part_name) DO UPDATE SET
                qty = EXCLUDED.qty,
                updated_at = now()
            """,
            [row + (datetime.utcnow(),) for row in rows],
            page_size=300,
        )
    conn.commit()


def _insert_moves(conn, rows):
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO part_moves (
                happened_at, master, action, part_name, qty, price, total, uid, gos, darks
            ) VALUES %s
            """,
            rows,
            page_size=300,
        )
    conn.commit()


def _insert_events(conn, rows):
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO work_log (uid, action, source, master, darks, gos, note, new_status)
            VALUES %s
            """,
            rows,
            page_size=200,
        )
    conn.commit()


def _insert_replacements(conn, rows):
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO replacements (
                uid, master, darks, address, kind,
                old_serial, old_gos, old_iot, new_serial, new_gos, new_iot, reason
            ) VALUES %s
            """,
            rows,
            page_size=100,
        )
    conn.commit()


def _worker():
    global _latest_tickets, _latest_darks, _latest_trunks
    conn = None
    ready = False
    while True:
        _wake.wait(15)
        _wake.clear()
        with _lock:
            tickets = _latest_tickets
            darks = _latest_darks
            trunks = _latest_trunks
            updates = list(_ticket_updates)
            moves = list(_moves)
            events = list(_events)
            replacements = list(_replacements)
        if not any((tickets, darks, trunks, updates, moves, events, replacements)):
            continue
        try:
            if conn is None or conn.closed:
                conn = _connect()
                ready = False
            if not ready:
                _ensure_schema(conn)
                ready = True
            if tickets:
                _upsert_tickets(conn, tickets)
                with _lock:
                    if _latest_tickets is tickets:
                        _latest_tickets = None
                    _state['tickets'] = len(tickets)
            if updates:
                _upsert_tickets(conn, updates)
                with _lock:
                    del _ticket_updates[:len(updates)]
            if darks:
                _upsert_darks(conn, darks)
                with _lock:
                    if _latest_darks is darks:
                        _latest_darks = None
            if trunks:
                _upsert_trunks(conn, trunks)
                with _lock:
                    if _latest_trunks is trunks:
                        _latest_trunks = None
            if moves:
                _insert_moves(conn, moves)
                with _lock:
                    del _moves[:len(moves)]
            if events:
                _insert_events(conn, events)
                with _lock:
                    del _events[:len(events)]
            if replacements:
                _insert_replacements(conn, replacements)
                with _lock:
                    del _replacements[:len(replacements)]
            with _lock:
                _state['connected'] = True
                _state['last_error'] = ''
                _state['last_sync'] = time.strftime('%Y-%m-%d %H:%M:%S')
            logger.info('База обновлена')
        except Exception as e:
            logger.warning(f'База недоступна, сайт работает через таблицу: {e}')
            with _lock:
                _state['connected'] = False
                _state['last_error'] = str(e)[:300]
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            conn = None
            ready = False
            time.sleep(10)
            _wake.set()
