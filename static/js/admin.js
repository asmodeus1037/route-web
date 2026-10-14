// ============================================================
// ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
// ============================================================
var DATA = window.__DATA__ || {};
var masters = DATA.masters || [];
var directionsData = DATA.directions || {};
var curatorSent = DATA.curatorSent || {};
var pendingChanges = {};
var selectedMaster = '';
var clearMasterTarget = null;
var actionTarget = { uid: null, source: null, action: null };

// Переменные для массового выделения
var selectedRequests = new Set();
var bulkModeActive = false;

// Маппинг вкладок
var TAB_NAMES = ['Напр 1', 'Напр 2', 'Напр 3', 'Напр 4', 'Без направления'];

// ============================================================
// ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
// ============================================================
function readApi(response) {
    if (response.status === 401) {
        throw new Error('Сессия истекла. Обновите страницу и войдите снова.');
    }
    var type = response.headers.get('content-type') || '';
    if (type.indexOf('application/json') === -1) {
        throw new Error('Сессия истекла. Обновите страницу и войдите снова.');
    }
    return response.json();
}

function isSupplyTicket(ticket) {
    var text = ((ticket && (ticket.type || '')) + ' ' + ((ticket && ticket.bike_type) || '')).toLowerCase();
    return text.indexOf('аккумулятор') !== -1 || text.indexOf('зарядн') !== -1;
}

function showToast(message, isError) {
    var toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        toast.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#22c55e;color:white;padding:12px 24px;border-radius:10px;font-weight:600;z-index:1000;display:none;';
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.className = 'toast' + (isError ? ' error' : '');
    toast.style.display = 'block';
    setTimeout(function() { toast.style.display = 'none'; }, 3000);
}

function showToastModern(message, type) {
    type = type || 'info';
    var container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    
    var toast = document.createElement('div');
    toast.className = 'toast toast-' + type;
    toast.innerHTML = '<span>' + message + '</span><button class="toast-close">&times;</button>';
    container.appendChild(toast);
    
    setTimeout(function() {
        if (toast.parentNode) {
            toast.style.opacity = '0';
            toast.style.transform = 'translateX(100%)';
            setTimeout(function() { toast.remove(); }, 300);
        }
    }, 4000);
    
    toast.querySelector('.toast-close').addEventListener('click', function() {
        toast.remove();
    });
}

function adminLogout(event) {
    if (event) event.preventDefault();
    localStorage.removeItem('master_login');
    localStorage.removeItem('master_name');
    window.location.href = '/logout';
}

function getAllTickets() {
    var all = [];
    for (var dirName in directionsData) {
        var dir = directionsData[dirName];
        if (dir && dir.tickets) {
            for (var i = 0; i < dir.tickets.length; i++) {
                all.push(dir.tickets[i]);
            }
        }
    }
    return all;
}

// ============================================================
// УВЕДОМЛЕНИЯ КУРАТОРАМ
// ============================================================
function openNotifyModal() {
    document.getElementById('notifyModal').classList.add('active');
    generateNotifyPreview();
}

function closeNotifyModal() {
    document.getElementById('notifyModal').classList.remove('active');
}

function previewText(value) {
    return String(value == null ? '' : value).replace(/[&<>]/g, function(ch) {
        if (ch === '&') return '&';
        if (ch === '<') return '<';
        return '>';
    });
}

function generateNotifyPreview() {
    var allTickets = getAllTickets();
    var assigned = [];
    for (var i = 0; i < allTickets.length; i++) {
        var t = allTickets[i];
        if (t.master && !t.is_done) assigned.push(t);
    }
    var box = document.getElementById('notifyPreview');
    if (assigned.length === 0) {
        box.textContent = 'Нет назначенных заявок для оповещения';
        return;
    }
    var people = (window.__DATA__ && window.__DATA__.darksPeople) || {};
    var groups = {};
    for (var i = 0; i < assigned.length; i++) {
        var t = assigned[i];
        var key = String(t.darks || 'без номера');
        if (!groups[key]) {
            groups[key] = { address: t.address || 'Адрес не указан', darks: key, tickets: [] };
        }
        groups[key].tickets.push(t);
    }
    var html = '';
    var keys = Object.keys(groups).sort(function(a, b) {
        return (parseInt(a, 10) || 999999) - (parseInt(b, 10) || 999999);
    });
    for (var k = 0; k < keys.length; k++) {
        var key = keys[k];
        var group = groups[key];
        var info = people[key] || {};
        var masters = [];
        for (var j = 0; j < group.tickets.length; j++) {
            var master = (group.tickets[j].master || '').trim();
            if (master && masters.indexOf(master) === -1) masters.push(master);
        }
        var curators = (info.curators || '').trim() || 'не указан';
        html += '<div style="padding:12px 0;border-bottom:1px solid #e7e5e4;">';
        html += '<div style="font-weight:800;">' + previewText(group.address) + ' (даркстор ' + previewText(key) + ')</div>';
        html += '<div>Мастер: ' + previewText(masters.join(', ') || 'не назначен') + '</div>';
        html += '<div>Куратор: ' + previewText(curators) + '</div>';
        html += '<div style="margin-top:6px;">Заявки (' + group.tickets.length + '):</div>';
        for (var n = 0; n < group.tickets.length; n++) {
            var ticket = group.tickets[n];
            var identifier = (ticket.type === 'Аккумуляторная батарея' || ticket.type === 'Зарядное устройство')
                ? ticket.type
                : (ticket.gos || 'Без номера');
            html += '<div>• ' + previewText(identifier) + ' | ' + previewText(ticket.desc || '-') + '</div>';
        }
        html += '</div>';
    }
    box.innerHTML = html;
}

function sendNotification() {
    var preview = document.getElementById('notifyPreview').textContent;
    if (preview.indexOf('Нет назначенных заявок') !== -1) {
        alert('Нет назначенных заявок для оповещения');
        return;
    }
    if (!confirm('Отправить уведомления кураторам?')) return;
    fetch('/api/notify_curators', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: preview })
    })
    .then(readApi)
    .then(function(data) {
        if (data.success) {
            if (data.marks) curatorSent = data.marks;
            showToastModern('Уведомления отправлены', 'success');
            closeNotifyModal();
            renderCurrentTab();
        } else {
            showToastModern('❌ Ошибка: ' + (data.error || 'неизвестная'), 'error');
        }
    })
    .catch(function() {
        showToastModern('❌ Ошибка отправки', 'error');
    });
}

// ============================================================
// ОБНОВЛЕНИЕ СТАТУСА В АДМИНКЕ (ОДНА ЗАЯВКА)
// ============================================================
function askStatusNote(status) {
    if (status === '🔵 Доделать') {
        var text = window.prompt('Комментарий: что нужно доделать?');
        if (text === null) return null;
        text = text.trim();
        if (!text) {
            alert('Без комментария статус «Доделать» не ставлю');
            return null;
        }
        return text;
    }
    if (status === '🔧 Эвакуация') {
        var reason = window.prompt('Причина замены велосипеда');
        if (reason === null) return null;
        reason = reason.trim();
        if (!reason) {
            alert('Без причины эвакуацию не ставлю');
            return null;
        }
        return reason;
    }
    return '';
}

function updateStatus(select) {
    var uid = select.dataset.uid;
    var status = select.value;
    var oldStatus = select.dataset.oldStatus || '🟡 В работе';
    var ticket = null;
    for (var dirName in directionsData) {
        var dir = directionsData[dirName];
        if (!dir || !dir.tickets) continue;
        for (var i = 0; i < dir.tickets.length; i++) {
            if (dir.tickets[i].uid === uid) {
                ticket = dir.tickets[i];
                break;
            }
        }
        if (ticket) break;
    }
    var note = askStatusNote(status);
    if (note === null) {
        select.value = oldStatus;
        return;
    }
    if (status === '🔧 Эвакуация' && isSupplyTicket(ticket)) {
        alert('Для аккумулятора и зарядного устройства эвакуацию ставить нельзя');
        select.value = oldStatus;
        return;
    }
    applyStatusToTicket(ticket, status, note);
    if (select) select.dataset.oldStatus = status;
    renderCurrentTab();
    fetch('/api/update_status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uid: uid, status: status, note: note, skip_report: true })
    })
    .then(readApi)
    .then(function(data) {
        if (!data.success) {
            showToastModern('Не сохранилось: ' + (data.error || ''), 'error');
            return;
        }
        showToastModern('Статус сохранён', 'success');
        refreshQueueStatus();
    })
    .catch(function(err) {
        showToastModern(err.message || 'Нет сети. Статус на экране уже изменён, в таблицу уйдёт при связи.', 'error');
    });
}

function applyStatusToTicket(ticket, status, note) {
    if (!ticket) return;
    var statusMap = {
        '🟡 В работе': 'pending',
        '✅ Выполнено': 'done',
        '🔵 Доделать': 'todo',
        '⏹️ Обработано': 'processed',
        '🔧 Эвакуация': 'evacuation'
    };
    ticket.status = statusMap[status] || 'pending';
    ticket.is_done = ticket.status === 'done';
    ticket.is_active = ticket.status !== 'done';
    if (status === '🔧 Эвакуация') {
        ticket.status = 'evacuation';
        ticket.note = ((ticket.note || '') + '\nЭВАКУАЦИЯ: ' + (note || 'Эвакуация')).trim();
    }
    if (status === '🔵 Доделать' && note) {
        ticket.note = ((ticket.note || '') + '\n' + note).trim();
    }
    if (status === '⏹️ Обработано') {
        ticket.master = '';
        ticket.hours_since = 0;
    }
}

// ============================================================
// ДЕЙСТВИЯ АДМИНА (⚙️)
// ============================================================
function openActionModal(uid, source) {
    actionTarget.uid = uid;
    actionTarget.source = source;
    actionTarget.action = null;
    document.getElementById('actionUidDisplay').textContent = uid;
    document.getElementById('actionExtra').style.display = 'none';
    document.getElementById('actionExtraInput').value = '';
    var btns = document.querySelectorAll('#actionButtons .btn');
    for (var i = 0; i < btns.length; i++) {
        btns[i].style.border = '2px solid transparent';
    }
    document.getElementById('actionModal').classList.add('active');
}

function closeActionModal() {
    document.getElementById('actionModal').classList.remove('active');
}

function selectAction(action) {
    actionTarget.action = action;
    var btns = document.querySelectorAll('#actionButtons .btn');
    for (var i = 0; i < btns.length; i++) {
        btns[i].style.border = '2px solid transparent';
    }
    var actions = ['done', 'evacuation', 'fail', 'todo', 'taken'];
    var index = actions.indexOf(action);
    if (index !== -1 && btns[index]) {
        btns[index].style.border = '2px solid #000';
    }
    if (['done', 'evacuation', 'taken'].indexOf(action) !== -1) {
        document.getElementById('actionExtra').style.display = 'block';
        if (action === 'evacuation') {
            document.getElementById('actionExtraInput').placeholder = 'Причина эвакуации (будет взято из описания)';
            document.getElementById('actionExtraInput').readOnly = true;
        } else if (action === 'taken') {
            document.getElementById('actionExtraInput').placeholder = 'Количество АКБ...';
            document.getElementById('actionExtraInput').readOnly = false;
        } else {
            document.getElementById('actionExtraInput').placeholder = 'Запчасти...';
            document.getElementById('actionExtraInput').readOnly = false;
        }
    } else {
        document.getElementById('actionExtra').style.display = 'none';
    }
}

function submitAction() {
    if (!actionTarget.action) {
        alert('Выберите действие!');
        return;
    }
    var extra = document.getElementById('actionExtraInput').value.trim();
    if (['done', 'evacuation', 'taken'].indexOf(actionTarget.action) !== -1 && !extra) {
        if (actionTarget.action === 'evacuation') {
            // Для эвакуации берем описание из заявки автоматически
            var tickets = getAllTickets();
            for (var i = 0; i < tickets.length; i++) {
                if (tickets[i].uid === actionTarget.uid) {
                    extra = tickets[i].desc || 'Эвакуация';
                    break;
                }
            }
            if (!extra) {
                alert('Не удалось получить описание заявки!');
                return;
            }
        } else {
            alert('Заполните дополнительную информацию!');
            return;
        }
    }
    if (!confirm('Применить действие "' + actionTarget.action + '" к заявке ' + actionTarget.uid + '?')) return;
    fetch('/api/admin_action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            uid: actionTarget.uid,
            source: actionTarget.source,
            action: actionTarget.action,
            extra: extra,
            skip_report: true
        })
    })
    .then(readApi)
    .then(function(data) {
        if (data.success) {
            showToastModern('✅ Действие выполнено!', 'success');
            closeActionModal();
            location.reload();
        } else {
            showToastModern('❌ Ошибка: ' + data.error, 'error');
        }
    })
    .catch(function() {
        showToastModern('❌ Ошибка сети', 'error');
    });
}

// ============================================================
// МАССОВОЕ ИЗМЕНЕНИЕ СТАТУСОВ
// ============================================================

function toggleBulkMode() {
    bulkModeActive = !bulkModeActive;
    var btn = document.getElementById('bulkModeBtn');
    var panel = document.getElementById('bulkPanel');
    
    if (bulkModeActive) {
        btn.textContent = '❌ Выйти из массового режима';
        btn.classList.add('active');
        panel.classList.add('active');
        selectedRequests.clear();
        enableBulkMode();
    } else {
        btn.textContent = '📋 Массовое изменение статуса';
        btn.classList.remove('active');
        panel.classList.remove('active');
        disableBulkMode();
        selectedRequests.clear();
        updateBulkUI();
    }
}

function enableBulkMode() {
    var rows = document.querySelectorAll('.ticket-row');
    rows.forEach(function(row) {
        row.classList.add('bulk-mode');
        if (!row.querySelector('.select-checkbox')) {
            var checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'select-checkbox';
            var uid = row.dataset.uid || row.getAttribute('data-uid');
            if (uid) {
                checkbox.dataset.uid = uid;
                checkbox.id = 'select_' + uid;
            }
            row.prepend(checkbox);
            
            checkbox.addEventListener('change', function(e) {
                e.stopPropagation();
                var row = this.closest('.ticket-row');
                var uid = this.dataset.uid;
                handleTicketSelect(this, uid, row);
            });
        }
        row.addEventListener('click', function(e) {
            if (e.target.closest('button') || e.target.closest('select') || 
                e.target.closest('a') || e.target.closest('.status-select') ||
                e.target.closest('.master-select') || e.target.closest('.action-btn') ||
                e.target.closest('.select-checkbox')) {
                return;
            }
            var cb = this.querySelector('.select-checkbox');
            if (cb) {
                cb.checked = !cb.checked;
                cb.dispatchEvent(new Event('change'));
            }
        });
    });
    updateBulkUI();
}

function disableBulkMode() {
    var rows = document.querySelectorAll('.ticket-row');
    rows.forEach(function(row) {
        row.classList.remove('bulk-mode');
        var checkbox = row.querySelector('.select-checkbox');
        if (checkbox) checkbox.remove();
        var newRow = row.cloneNode(true);
        row.parentNode.replaceChild(newRow, row);
    });
    selectedRequests.clear();
    updateBulkUI();
}

function handleTicketSelect(checkbox, uid, row) {
    if (checkbox.checked) {
        selectedRequests.add(uid);
        row.classList.add('selected');
    } else {
        selectedRequests.delete(uid);
        row.classList.remove('selected');
    }
    updateBulkUI();
}

function updateBulkUI() {
    var count = selectedRequests.size;
    var countEl = document.getElementById('selectedCount');
    var applyBtn = document.getElementById('applyBulkStatus');
    
    if (countEl) countEl.textContent = 'Выбрано: ' + count;
    if (applyBtn) applyBtn.disabled = count === 0;
}

async function applyBulkStatus() {
    var statusSelect = document.getElementById('bulkStatusSelect');
    var status = statusSelect.value;
    
    if (!status) {
        showToastModern('Выберите статус для применения', 'error');
        return;
    }
    
    if (selectedRequests.size === 0) {
        showToastModern('Нет выбранных заявок', 'error');
        return;
    }
    
    var note = askStatusNote(status);
    if (note === null) return;
    if (!confirm('Изменить статус на "' + status + '" для ' + selectedRequests.size + ' заявок?')) {
        return;
    }
    
    var uids = Array.from(selectedRequests);
    var all = getAllTickets();
    var skipped = 0;
    for (var i = 0; i < all.length; i++) {
        if (uids.indexOf(all[i].uid) === -1) continue;
        if (status === '🔧 Эвакуация' && isSupplyTicket(all[i])) {
            skipped++;
            uids = uids.filter(function(uid) { return uid !== all[i].uid; });
            continue;
        }
        applyStatusToTicket(all[i], status, note);
    }
    if (!uids.length) {
        alert('Для аккумулятора и зарядного устройства эвакуацию ставить нельзя');
        return;
    }
    selectedRequests.clear();
    updateBulkUI();
    if (bulkModeActive) toggleBulkMode();
    renderCurrentTab();
    showToastModern('Статусы изменены. В таблицу дойдут сами.', 'success');
    fetch('/api/bulk_update_status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uids: uids, status: status, comment: note, skip_report: true })
    })
    .then(readApi)
    .then(function(data) {
        if (!data.success) showToastModern('Таблица не приняла: ' + (data.error || ''), 'error');
        else refreshQueueStatus();
    })
    .catch(function() {
        showToastModern('Нет сети. На экране уже изменено.', 'error');
    });
}

function clearBulkSelection() {
    document.querySelectorAll('.select-checkbox:checked').forEach(function(cb) {
        cb.checked = false;
        var row = cb.closest('.ticket-row');
        if (row) row.classList.remove('selected');
    });
    selectedRequests.clear();
    updateBulkUI();
}

// ============================================================
// ОТПРАВКА МАРШРУТА
// ============================================================
function openSendModal() {
    var modal = document.getElementById('sendModal');
    var list = document.getElementById('sendMasterList');
    var allTickets = getAllTickets();
    var counts = {};
    for (var i = 0; i < allTickets.length; i++) {
        var t = allTickets[i];
        if (!t.is_done && t.master) {
            if (!counts[t.master]) counts[t.master] = 0;
            counts[t.master]++;
        }
    }
    var html = '';
    for (var j = 0; j < masters.length; j++) {
        var master = masters[j];
        var count = counts[master] || 0;
        var status = count > 0 ? '<span class="send-status sent">✅ ' + count + ' заявок</span>' : '<span class="send-status">❌ нет заявок</span>';
        html += '<div style="padding:8px 12px;border-bottom:1px solid #e2e8f0;display:flex;justify-content:space-between;align-items:center;">';
        html += '<span><strong>' + master + '</strong> ' + status + '</span>';
        html += '<button class="btn btn-sm btn-send" onclick="sendRouteTo(\'' + master + '\')"' + (count === 0 ? ' disabled style="opacity:0.5;"' : '') + '>Отправить заявки</button>';
        html += '</div>';
    }
    list.innerHTML = html;
    modal.classList.add('active');
}

function closeSendModal() {
    document.getElementById('sendModal').classList.remove('active');
}

function sendRouteTo(master) {
    selectedMaster = master;
    sendRoute();
}

function sendRoute() {
    if (!selectedMaster) {
        alert('Выберите мастера!');
        return;
    }
    if (!confirm('Отправить заявки мастеру ' + selectedMaster + '?')) return;
    document.getElementById('syncSpinner').style.display = 'block';
    document.getElementById('syncStatus').textContent = '⏳ Обновление...';
    document.getElementById('syncStatus').style.color = '#f59e0b';
    fetch('/api/send_route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ master: selectedMaster })
    })
    .then(readApi)
    .then(function(data) {
        if (data.success) {
            var countText = typeof data.tickets === 'number' ? ' В таблице сейчас ' + data.tickets + ' открытых заявок.' : '';
            var note = countText;
            if (data.notify_reason === 'ok' || data.notified) {
                note += ' Сообщение отправлено в Telegram.';
            } else if (data.notify_reason === 'no_id') {
                note += ' В столбце H напишите имя мастера, а в столбце I той же строки — его Telegram ID.';
            } else {
                note += ' ID найден, но бот ответил не сразу. Проверьте Telegram через минуту.';
            }
            document.getElementById('syncStatus').textContent = 'Кэш мастера ' + selectedMaster + ' обновлен.' + note;
            document.getElementById('syncStatus').style.color = '#22c55e';
            closeSendModal();
        } else {
            document.getElementById('syncStatus').textContent = '❌ Ошибка: ' + data.error;
            document.getElementById('syncStatus').style.color = '#ef4444';
        }
        document.getElementById('syncSpinner').style.display = 'none';
    })
    .catch(function() {
        document.getElementById('syncStatus').textContent = '❌ Ошибка сети';
        document.getElementById('syncStatus').style.color = '#ef4444';
        document.getElementById('syncSpinner').style.display = 'none';
    });
}

function sendRouteToAll() {
    if (!confirm('Отправить заявки всем мастерам?')) return;
    document.getElementById('syncSpinner').style.display = 'block';
    document.getElementById('syncStatus').textContent = '⏳ Обновление...';
    document.getElementById('syncStatus').style.color = '#f59e0b';
    fetch('/api/send_route_all', { method: 'POST' })
    .then(readApi)
    .then(function(data) {
        if (data.success) {
            document.getElementById('syncStatus').textContent = '✅ Кэши всех мастеров обновлены';
            document.getElementById('syncStatus').style.color = '#22c55e';
            closeSendModal();
        } else {
            document.getElementById('syncStatus').textContent = '❌ Ошибка: ' + data.error;
            document.getElementById('syncStatus').style.color = '#ef4444';
        }
        document.getElementById('syncSpinner').style.display = 'none';
    })
    .catch(function() {
        document.getElementById('syncStatus').textContent = '❌ Ошибка сети';
        document.getElementById('syncStatus').style.color = '#ef4444';
        document.getElementById('syncSpinner').style.display = 'none';
    });
}

// ============================================================
// СНЯТИЕ ЗАЯВОК
// ============================================================
function openClearMasterModal() {
    var modal = document.getElementById('clearMasterModal');
    var list = document.getElementById('clearMasterList');
    var allTickets = getAllTickets();
    var counts = {};
    for (var i = 0; i < allTickets.length; i++) {
        var t = allTickets[i];
        if (t.source === 'Заявки') {
            if (t.status === 'pending' || t.status === 'todo' || t.status === 'fail') {
                if (t.master) {
                    if (!counts[t.master]) counts[t.master] = 0;
                    counts[t.master]++;
                }
            }
        } else if (t.source === 'Импорт М4') {
            if (t.status === 'pending') {
                if (t.master) {
                    if (!counts[t.master]) counts[t.master] = 0;
                    counts[t.master]++;
                }
            }
        }
    }
    var total = 0;
    for (var key in counts) { total += counts[key]; }
    var html = '<div style="padding:8px 12px;border-bottom:1px solid #e2e8f0;display:flex;justify-content:space-between;align-items:center;background:#f0fdf4;">';
    html += '<span><strong>👤 Все мастера</strong></span>';
    html += '<span class="count">' + total + ' заявок</span>';
    html += '<button class="btn btn-sm btn-danger" onclick="clearAllMasters()">🧹 Снять ВСЕХ</button>';
    html += '</div>';
    for (var j = 0; j < masters.length; j++) {
        var master = masters[j];
        var count = counts[master] || 0;
        html += '<div style="padding:8px 12px;border-bottom:1px solid #e2e8f0;display:flex;justify-content:space-between;align-items:center;">';
        html += '<span><strong>👤 ' + master + '</strong></span>';
        html += '<span class="count">' + (count > 0 ? count + ' заявок' : 'нет заявок') + '</span>';
        html += '<button class="btn btn-sm btn-clear-master" onclick="clearMaster(\'' + master + '\')"' + (count === 0 ? ' disabled style="opacity:0.5;"' : '') + '>Снять</button>';
        html += '</div>';
    }
    list.innerHTML = html;
    modal.classList.add('active');
}

function closeClearMasterModal() {
    document.getElementById('clearMasterModal').classList.remove('active');
}

function clearMaster(master) {
    if (!master) {
        alert('Выберите мастера!');
        return;
    }
    if (!confirm('Снять все заявки с мастера ' + master + '?')) return;
    document.getElementById('syncSpinner').style.display = 'block';
    document.getElementById('syncStatus').textContent = '⏳ Снятие...';
    document.getElementById('syncStatus').style.color = '#f59e0b';
    fetch('/api/clear_master', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ master: master })
    })
    .then(readApi)
    .then(function(data) {
        if (data.success) {
            document.getElementById('syncStatus').textContent = '✅ Снято ' + data.cleared + ' заявок';
            document.getElementById('syncStatus').style.color = '#22c55e';
            closeClearMasterModal();
            location.reload();
        } else {
            document.getElementById('syncStatus').textContent = '❌ Ошибка: ' + data.error;
            document.getElementById('syncStatus').style.color = '#ef4444';
        }
        document.getElementById('syncSpinner').style.display = 'none';
    })
    .catch(function(err) {
        document.getElementById('syncStatus').textContent = '❌ ' + (err.message || 'Ошибка сети');
        document.getElementById('syncStatus').style.color = '#ef4444';
        document.getElementById('syncSpinner').style.display = 'none';
    });
}

function clearAllMasters() {
    if (!confirm('Снять ВСЕХ мастеров со всех заявок?')) return;
    document.getElementById('syncSpinner').style.display = 'block';
    document.getElementById('syncStatus').textContent = '⏳ Снятие...';
    document.getElementById('syncStatus').style.color = '#f59e0b';
    fetch('/api/clear_dates', { method: 'POST' })
    .then(readApi)
    .then(function(data) {
        if (data.success) {
            document.getElementById('syncStatus').textContent = '✅ Снято ' + data.cleared + ' заявок';
            document.getElementById('syncStatus').style.color = '#22c55e';
            closeClearMasterModal();
            location.reload();
        } else {
            document.getElementById('syncStatus').textContent = '❌ Ошибка: ' + data.error;
            document.getElementById('syncStatus').style.color = '#ef4444';
        }
        document.getElementById('syncSpinner').style.display = 'none';
    })
    .catch(function() {
        document.getElementById('syncStatus').textContent = '❌ Ошибка сети';
        document.getElementById('syncStatus').style.color = '#ef4444';
        document.getElementById('syncSpinner').style.display = 'none';
    });
}

// ============================================================
// МАРШРУТЫ
// ============================================================
function closeRoutesModal() {
    document.getElementById('routesModal').classList.remove('active');
}

function showRoutes() {
    var modal = document.getElementById('routesModal');
    var content = document.getElementById('routesContent');
    var allTickets = getAllTickets();
    var routes = {};
    for (var i = 0; i < allTickets.length; i++) {
        var t = allTickets[i];
        if (!t.is_done && t.master) {
            if (!routes[t.master]) routes[t.master] = [];
            routes[t.master].push(t);
        }
    }
    var keys = Object.keys(routes);
    if (keys.length === 0) {
        content.innerHTML = '<p style="color:#94a3b8;">Нет назначенных заявок</p>';
    } else {
        var html = '';
        for (var m = 0; m < keys.length; m++) {
            var master = keys[m];
            var tickets = routes[master];
            html += '<div style="margin-bottom:12px;background:#f8fafc;padding:10px;border-radius:8px;">';
            html += '<div class="route-item"><span class="master-name">👤 ' + master + '</span><span class="count">' + tickets.length + ' заявок</span></div>';
            var darksGroups = {};
            for (var j = 0; j < tickets.length; j++) {
                var t = tickets[j];
                var key = t.darks || 'без номера';
                if (!darksGroups[key]) darksGroups[key] = { address: t.address || 'Адрес не указан', coords: t.coords || '', tickets: [] };
                darksGroups[key].tickets.push(t);
            }
            for (var darks in darksGroups) {
                var group = darksGroups[darks];
                html += '<div style="margin-left:12px;padding:4px 8px;background:white;border-radius:4px;margin-top:4px;border-left:2px solid #3b82f6;">';
                html += '<div style="font-size:12px;font-weight:600;">📍 ' + group.address + ' (ДС ' + darks + ')</div>';
                for (var k = 0; k < group.tickets.length; k++) {
                    var t = group.tickets[k];
                    var hoursDisplay = t.hours_since !== undefined ? t.hours_since.toFixed(1) : '0';
                    html += '<div style="font-size:11px;color:#475569;padding:1px 0;">' + (t.gos || '-') + ' — ' + (t.desc || '-') + ' (' + hoursDisplay + 'ч)</div>';
                }
                html += '</div>';
            }
            var url = '/api/build_route_for_master?master=' + encodeURIComponent(master);
            html += '<button class="btn btn-primary btn-sm" style="margin-top:6px;" onclick="window.open(\'' + url + '\', \'_blank\')">🗺️ Проложить маршрут</button>';
            html += '</div>';
        }
        content.innerHTML = html;
    }
    modal.classList.add('active');
}

// ============================================================
// ФИЛЬТРЫ И ОТРИСОВКА
// ============================================================
function getActiveTabId() {
    var activeBtn = document.querySelector('.tab-btn.active');
    return activeBtn ? activeBtn.dataset.tab : null;
}

function getFilteredTickets(tickets) {
    var masterFilter = document.getElementById('masterFilter').value;
    var sourceFilter = document.getElementById('sourceFilter').value;
    var search = document.getElementById('searchInput').value.toLowerCase();
    var result = [];
    for (var i = 0; i < tickets.length; i++) {
        var t = tickets[i];
        if (t.is_done) continue;
        var masterMatch = masterFilter === 'all' ? true :
                          masterFilter === 'unassigned' ? !t.master :
                          t.master === masterFilter;
        var sourceMatch = sourceFilter === 'all' ? true : t.source === sourceFilter;
        var searchMatch = (t.uid || '').toLowerCase().indexOf(search) !== -1 ||
                          (t.desc || '').toLowerCase().indexOf(search) !== -1 ||
                          (t.gos || '').toLowerCase().indexOf(search) !== -1 ||
                          (t.iot || '').toLowerCase().indexOf(search) !== -1 ||
                          (t.supplier || '').toLowerCase().indexOf(search) !== -1;
        if (masterMatch && sourceMatch && searchMatch) {
            result.push(t);
        }
    }
    return result;
}

function resetFilters() {
    document.getElementById('masterFilter').value = 'all';
    document.getElementById('sourceFilter').value = 'all';
    document.getElementById('searchInput').value = '';
    renderCurrentTab();
}

function renderCurrentTab() {
    var tabId = getActiveTabId();
    if (!tabId) return;
    var tabIndex = parseInt(tabId.split('-')[1]);
    var dirName = TAB_NAMES[tabIndex - 1];
    if (!dirName) return;
    
    var tickets = (directionsData[dirName] && directionsData[dirName].tickets) ? directionsData[dirName].tickets : [];
    var containerId = 'renderDir' + tabIndex;
    
    var filtered = getFilteredTickets(tickets);
    renderTicketsGrouped(filtered, containerId);
}

function bikeButtons(iot, gos, darks) {
    var safeIot = String(iot || '').replace(/[&<>"']/g, '');
    var safeGos = String(gos || '').replace(/[&<>"']/g, '');
    var safeDark = String(darks || '').replace(/[&<>"']/g, '');
    if (!safeIot && !safeGos) return '';
    var attrs = ' data-iot="' + safeIot + '" data-gos="' + safeGos + '" data-darks="' + safeDark + '"';
    return '<span class="bike-actions">'
        + '<button type="button" class="btn-mini map"' + attrs + ' onclick="openBikeMap(this)">Карта</button>'
        + '</span>';
}

function openBikeMap(button) {
    var iot = button.getAttribute('data-iot') || '';
    var gos = button.getAttribute('data-gos') || '';
    var darks = button.getAttribute('data-darks') || '';
    button.disabled = true;
    showToastModern('Ищу точку...', 'success');
    var query = 'iot=' + encodeURIComponent(iot) + '&gos=' + encodeURIComponent(gos) + '&darks=' + encodeURIComponent(darks);
    fetch('/api/bike_location?' + query)
        .then(readApi)
        .then(function(data) {
            button.disabled = false;
            if (!data.success || !data.url) {
                showToastModern(data.error || 'Точки нет', 'error');
                return;
            }
            var note = data.online ? 'На связи' : 'Последняя точка';
            if (data.at) note += ', ' + data.at;
            showToastModern(note, 'success');
            window.open(data.url, '_blank');
        })
        .catch(function() {
            button.disabled = false;
            showToastModern('Нет сети', 'error');
        });
}

function setBikePower(button, action) {
    var gos = button.getAttribute('data-gos') || 'велосипед';
    var question = action === 'on'
        ? 'Включить ' + gos + '? Он сможет поехать.'
        : 'Выключить ' + gos + '? Он заблокируется.';
    if (!confirm(question)) return;
    button.disabled = true;
    fetch('/api/bike_power', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: action,
            iot: button.getAttribute('data-iot') || '',
            gos: button.getAttribute('data-gos') || '',
            darks: button.getAttribute('data-darks') || ''
        })
    })
    .then(readApi)
    .then(function(data) {
        button.disabled = false;
        showToastModern(data.success ? (data.message || 'Готово') : (data.error || 'Не вышло'), data.success ? 'success' : 'error');
    })
    .catch(function() {
        button.disabled = false;
        showToastModern('Нет сети', 'error');
    });
}

function toggleRowMenu(event, button) {
    if (event) event.stopPropagation();
    var menu = button.parentNode;
    var open = menu.classList.contains('open');
    var opened = document.querySelectorAll('.row-menu.open');
    for (var i = 0; i < opened.length; i++) opened[i].classList.remove('open');
    if (!open) menu.classList.add('open');
}

function searchBike() {
    var input = document.getElementById('bikeQuery');
    var box = document.getElementById('bikeSearchResult');
    var query = (input.value || '').trim();
    if (query.length < 3) {
        box.innerHTML = '<p style="color:#b45309;">Введите хотя бы 3 символа.</p>';
        return;
    }
    box.innerHTML = '<p>Ищу...</p>';
    fetch('/api/bike_search?q=' + encodeURIComponent(query))
        .then(readApi)
        .then(function(data) {
            if (!data.success) {
                box.innerHTML = '<p style="color:#b91c1c;">' + (data.error || 'Не найдено') + '</p>';
                return;
            }
            var items = data.items || [];
            if (!items.length) {
                box.innerHTML = '<p>Такого велосипеда в базе нет.</p>';
                return;
            }
            var html = '';
            for (var i = 0; i < items.length; i++) {
                var item = items[i];
                html += '<div class="bike-card">';
                html += '<div><b>Госномер:</b> ' + (item.gos || '—') + '</div>';
                html += '<div><b>IoT:</b> ' + (item.iot || '—') + '</div>';
                html += '<div><b>Серийный:</b> ' + (item.serial || '—') + '</div>';
                html += '<div><b>Тип:</b> ' + (item.kind || '—') + '</div>';
                html += '<div><b>Поставщик:</b> ' + (item.supplier || '—') + '</div>';
                html += '<div><b>Даркстор:</b> ' + (item.darks || '—') + (item.address ? (', ' + item.address) : '') + '</div>';
                if (item.url) html += '<div style="margin-top:8px;"><a href="' + item.url + '" target="_blank">Открыть на карте</a>' + (item.online ? ' · на связи' : ' · последняя точка') + '</div>';
                else html += '<div style="margin-top:8px;color:#64748b;">Точки на карте сейчас нет</div>';
                html += '</div>';
            }
            box.innerHTML = html;
        })
        .catch(function(error) {
            box.innerHTML = '<p style="color:#b91c1c;">' + (error.message || 'Нет сети') + '</p>';
        });
}

function openCreateTicket() {
    document.getElementById('createTicketModal').classList.add('active');
}

function closeCreateTicket() {
    document.getElementById('createTicketModal').classList.remove('active');
}

function toggleCustomType() {
    var chosen = document.querySelector('input[name="createType"]:checked');
    var field = document.getElementById('createCustom');
    field.style.display = chosen && chosen.value === 'Свой вариант' ? 'block' : 'none';
}

function submitCreateTicket() {
    var chosen = document.querySelector('input[name="createType"]:checked');
    var payload = {
        darks: document.getElementById('createDarks').value.trim(),
        type: chosen ? chosen.value : '',
        custom_type: document.getElementById('createCustom').value.trim(),
        desc: document.getElementById('createDesc').value.trim(),
        gos: document.getElementById('createGos').value.trim()
    };
    fetch('/api/admin_ticket', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(readApi)
    .then(function(data) {
        if (!data.success) {
            alert(data.error || 'Не создалось');
            return;
        }
        var ticket = data.ticket;
        if (ticket) {
            var dir = ticket.direction || 'Без направления';
            if (!directionsData[dir]) directionsData[dir] = { tickets: [], active_count: 0 };
            directionsData[dir].tickets.push(ticket);
            directionsData[dir].active_count += 1;
        }
        closeCreateTicket();
        document.getElementById('createDarks').value = '';
        document.getElementById('createDesc').value = '';
        document.getElementById('createGos').value = '';
        document.getElementById('createCustom').value = '';
        showToastModern('Заявка создана', 'success');
        renderCurrentTab();
    })
    .catch(function(error) {
        alert(error.message || 'Нет сети');
    });
}

document.addEventListener('click', function() {
    var opened = document.querySelectorAll('.row-menu.open');
    for (var i = 0; i < opened.length; i++) opened[i].classList.remove('open');
});

function originBadge(source, contact) {
    if (String(contact || '').trim().toLowerCase() === 'админ') return '<span class="origin-badge origin-admin">Админ</span>';
    if (source === 'Заявки бот') return '<span class="origin-badge origin-bot">Бот</span>';
    if (source === 'Заявки') return '<span class="origin-badge origin-site">Сайт</span>';
    if (source === 'Импорт М4') return '<span class="origin-badge origin-m4">М4</span>';
    return '';
}

function supplierBadge(name) {
    var text = String(name || '').trim();
    if (!text) return '';
    var kind = 'supplier-badge';
    var low = text.toLowerCase();
    if (low.indexOf('vanta') !== -1) kind += ' supplier-vanta';
    else if (low.indexOf('степ') !== -1) kind += ' supplier-step';
    return '<span class="' + kind + '">' + text.replace(/[&<>]/g, '') + '</span>';
}

function sentMark(darks, ticketCount) {
    var mark = curatorSent[String(darks)];
    if (!mark || !mark.at) return '';
    var changed = mark.tickets && mark.tickets !== ticketCount;
    var text = mark.sent ? ('Куратору отправлено ' + mark.at) : ('Куратору не ушло ' + mark.at + ', нет Telegram');
    if (mark.sent && changed) text += ', список изменился';
    return '<div class="sent-mark' + (changed || !mark.sent ? ' warn' : '') + '">' + text + '</div>';
}

function renderTicketsGrouped(tickets, containerId) {
    var container = document.getElementById(containerId);
    if (!container) return;
    var allTickets = getAllTickets();
    var active = [];
    for (var i = 0; i < allTickets.length; i++) {
        if (!allTickets[i].is_done) active.push(allTickets[i]);
    }
    
    // Считаем статусы КОРРЕКТНО - эвакуация определяется по статусу 'todo' И note с 'ЭВАКУАЦИЯ:'
    var pending = 0;
    var evacuation = 0;
    var todo = 0;
    var processed = 0;
    var unassigned = 0;
    var site = 0;
    var bot = 0;
    
    for (var i = 0; i < active.length; i++) {
        var t = active[i];
        // Проверяем на эвакуацию
        if (t.status === 'evacuation') {
            evacuation++;
        } else if (t.status === 'pending') {
            pending++;
        } else if (t.status === 'todo') {
            todo++;
        } else if (t.status === 'processed' || t.status === 'fail') {
            processed++;
        }
        if (!t.master) unassigned++;
        if (t.source === 'Заявки') site++;
        else if (t.source === 'Заявки бот') bot++;
    }
    
    // Обновляем статистику
    document.getElementById('totalCount').textContent = active.length;
    document.getElementById('pendingCount').textContent = pending;
    document.getElementById('evacuationCount').textContent = evacuation;
    document.getElementById('todoCount').textContent = todo;
    var processedEl = document.getElementById('processedCount');
    if (processedEl) processedEl.textContent = processed;
    document.getElementById('unassignedCount').textContent = unassigned;
    var siteEl = document.getElementById('siteCount');
    var botEl = document.getElementById('botCount');
    if (siteEl) siteEl.textContent = site;
    if (botEl) botEl.textContent = bot;
    
    if (tickets.length === 0) { 
        container.innerHTML = '<div class="empty-state">📭 Нет активных заявок</div>'; 
        return; 
    }
    
    var darksGroups = {};
    for (var i = 0; i < tickets.length; i++) {
        var t = tickets[i];
        var key = t.darks || 'без номера';
        if (!darksGroups[key]) {
            darksGroups[key] = { darks: key, address: t.address || 'Адрес не указан', contact: t.contact || '', tickets: [] };
        }
        darksGroups[key].tickets.push(t);
        if (t.contact && !darksGroups[key].contact) { 
            darksGroups[key].contact = t.contact; 
        }
    }

    Object.keys(darksGroups).forEach(function(key) {
        darksGroups[key].tickets.sort(function(a, b) {
            var ap = (a.status === 'processed' || a.status === 'fail') ? 1 : 0;
            var bp = (b.status === 'processed' || b.status === 'fail') ? 1 : 0;
            return ap - bp;
        });
    });
    
    var sortedKeys = Object.keys(darksGroups).sort(function(a, b) {
        var numA = parseInt(a) || 999999;
        var numB = parseInt(b) || 999999;
        return numA - numB;
    });
    
    var html = '';
    for (var si = 0; si < sortedKeys.length; si++) {
        var key = sortedKeys[si];
        var group = darksGroups[key];
        html += '<div class="darks-group">';
        html += '<div class="darks-header"><span class="address">📍 ' + group.address + '</span><span class="darks-num">ДС ' + group.darks + ' · ' + group.tickets.length + '</span></div>';
        html += sentMark(group.darks, group.tickets.length);
        if (group.contact) { html += '<div class="darks-contact">📞 ' + group.contact + '</div>'; }
        for (var j = 0; j < group.tickets.length; j++) {
            var t = group.tickets[j];
            
            // Определяем статус для отображения
            var statusDisplay = t.status;
            var isEvacuation = false;
            
            if (t.status === 'evacuation') {
                isEvacuation = true;
                statusDisplay = '🔧 Эвакуация';
            } else if (t.status === 'pending') {
                statusDisplay = '🟡 В работе';
            } else if (t.status === 'done') {
                statusDisplay = '✅ Выполнено';
            } else if (t.status === 'todo') {
                statusDisplay = '🔵 Доделать';
            } else if (t.status === 'processed' || t.status === 'fail') {
                statusDisplay = '⏹️ Обработано';
            } else if (t.status === 'evacuation') {
                statusDisplay = '🔧 Эвакуация';
            }
            
            var hoursDisplay = t.hours_since !== undefined ? t.hours_since.toFixed(1) : '0';
            var uidKey = t.uid + '|' + t.source;
            var currentMaster = pendingChanges[uidKey] !== undefined ? pendingChanges[uidKey] : (t.master || '');
            var typeDisplay = t.bike_type || t.type || 'Не указан';
            
            var isProcessed = t.status === 'processed' || t.status === 'fail';
            var isQueued = !!(window.queuedUids && window.queuedUids[t.uid]);
            
            var hoursClass = '';
            if (!isProcessed && t.hours_since > 48) hoursClass = 'overdue';
            else if (!isProcessed && t.hours_since >= 32) hoursClass = 'warning';
            
            html += '<div class="ticket-row' + (isEvacuation ? ' evacuation-row' : '') + (isProcessed ? ' processed-row' : '') + (isQueued ? ' queued-row' : '') + (String(t.contact || '').trim().toLowerCase() === 'админ' ? ' admin-made' : '') + '" data-uid="' + t.uid + '">';
            html += '<span class="id">' + (t.gos || '—') + '</span>';
            html += '<span class="desc" title="' + (t.display_desc || t.desc || '-') + '">' + originBadge(t.source, t.contact) + supplierBadge(t.supplier) + (t.display_desc || t.desc || '-');
            if (t.contact) {
                html += '<span class="who">отправил: ' + String(t.contact).replace(/[&<>]/g, '') + '</span>';
            }
            html += '</span>';
            html += '<span class="type-badge">' + typeDisplay + '</span>';
            html += '<span class="field"><span class="field-label">Мастер</span><select class="master-select" data-uid="' + t.uid + '" data-source="' + t.source + '" onchange="onMasterChange(this)"><option value="">—</option>';
            for (var mi = 0; mi < masters.length; mi++) {
                var m = masters[mi];
                html += '<option value="' + m + '"' + (currentMaster === m ? ' selected' : '') + '>' + m + '</option>';
            }
            html += '</select></span></span>';
            
            html += '<span class="field"><span class="field-label">Статус</span>';
            html += '<select class="status-select" data-uid="' + t.uid + '" data-old-status="' + statusDisplay + '" onchange="updateStatus(this)" style="padding:2px 6px;border-radius:4px;border:1px solid #d1d5db;font-size:10px;background:white;width:100%;max-width:100px;cursor:pointer;">';
            html += '<option value="🟡 В работе"' + (statusDisplay === '🟡 В работе' ? ' selected' : '') + '>🟡 В работе</option>';
            html += '<option value="✅ Выполнено"' + (statusDisplay === '✅ Выполнено' ? ' selected' : '') + '>✅ Выполнено</option>';
            html += '<option value="🔵 Доделать"' + (statusDisplay === '🔵 Доделать' ? ' selected' : '') + '>🔵 Доделать</option>';
            html += '<option value="⏹️ Обработано"' + (statusDisplay === '⏹️ Обработано' ? ' selected' : '') + '>⏹️ Обработано</option>';
            if (!isSupplyTicket(t)) {
                html += '<option value="🔧 Эвакуация"' + (statusDisplay === '🔧 Эвакуация' ? ' selected' : '') + '>🔧 Эвакуация</option>';
            }
            html += '</select>';
            html += '</span></span>';
            
            html += '<span class="hours ' + hoursClass + '">⏱️ ' + hoursDisplay + ' ч</span>';
            if (!isSupplyTicket(t) && (t.gos || t.iot)) {
                html += '<span class="row-menu"><button type="button" class="dots" onclick="toggleRowMenu(event, this)">⋯</button><span class="menu-pop">' + bikeButtons(t.iot, t.gos, t.darks) + '</span></span>';
            } else {
                html += '<span></span>';
            }
            html += '</div>';
        }
        html += '<div class="assign-all-bar"><label>📌 Все:</label><select id="assignMaster_' + group.darks + '"><option value="">—</option>';
        for (var mi2 = 0; mi2 < masters.length; mi2++) {
            html += '<option value="' + masters[mi2] + '">' + masters[mi2] + '</option>';
        }
        html += '</select><button type="button" class="btn btn-success" onclick="assignAllDarks(\'' + String(group.darks).replace(/'/g, '') + '\')">Назначить</button></div>';
        html += '</div>';
    }
    container.innerHTML = html;
    updateChangesInfo();
    
    if (bulkModeActive) {
        enableBulkMode();
    }
}

function onMasterChange(select) {
    var uid = select.dataset.uid;
    var source = select.dataset.source;
    var master = select.value;
    var key = uid + '|' + source;
    pendingChanges[key] = master;
    updateChangesInfo();
    savePendingKeys([key], false);
}

function assignAllDarks(darks) {
    var select = document.getElementById('assignMaster_' + darks);
    var master = select.value;
    if (!master) { alert('Выберите мастера'); return; }
    var allTickets = getAllTickets();
    var keys = [];
    for (var i = 0; i < allTickets.length; i++) {
        var t = allTickets[i];
        if (String(t.darks) === String(darks) && !t.is_done) {
            var key = t.uid + '|' + t.source;
            pendingChanges[key] = master;
            keys.push(key);
        }
    }
    updateChangesInfo();
    renderCurrentTab();
    savePendingKeys(keys, false);
}

function updateChangesInfo() {
    var count = 0;
    for (var key in pendingChanges) { count++; }
    var el = document.getElementById('changesInfo');
    if (count > 0) {
        el.textContent = '📝 ' + count + ' изменений ожидают сохранения';
        document.getElementById('syncStatus').textContent = '📝 Есть изменения';
        document.getElementById('syncStatus').style.color = '#f59e0b';
    } else {
        el.textContent = 'Назначение сразу пишется в базу. «Сохранить назначения» нужно, только если сеть оборвалась.';
        document.getElementById('syncStatus').textContent = '✅ Готово';
        document.getElementById('syncStatus').style.color = '#22c55e';
    }
}

// ============================================================
// СОХРАНЕНИЕ И СИНХРОНИЗАЦИЯ
// ============================================================
function saveDarksChanges(button) {
    var darks = String(button.getAttribute('data-darks') || '');
    var allTickets = getAllTickets();
    var byUid = {};
    for (var i = 0; i < allTickets.length; i++) {
        byUid[allTickets[i].uid] = allTickets[i];
    }
    var changeList = [];
    var drop = [];
    for (var key in pendingChanges) {
        var uid = key.split('|')[0];
        var ticket = byUid[uid];
        if (!ticket || String(ticket.darks) !== darks) continue;
        var parts = key.split('|');
        changeList.push({ uid: parts[0], source: parts[1], master: pendingChanges[key] });
        drop.push(key);
    }
    if (!changeList.length) {
        alert('На этом дарксторе нет новых назначений');
        return;
    }
    button.disabled = true;
    fetch('/api/batch_update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ changes: changeList })
    })
    .then(readApi)
    .then(function(data) {
        button.disabled = false;
        if (!data.success) {
            alert('Не сохранилось: ' + (data.error || ''));
            return;
        }
        for (var i = 0; i < changeList.length; i++) {
            var ticket = byUid[changeList[i].uid];
            if (ticket && ticket.source === changeList[i].source) ticket.master = changeList[i].master;
        }
        for (var j = 0; j < drop.length; j++) delete pendingChanges[drop[j]];
        updateChangesInfo();
        renderCurrentTab();
        showToastModern('Даркстор ' + darks + ' сохранён', 'success');
    })
    .catch(function() {
        button.disabled = false;
        alert('Нет сети. Назначения остались на экране, таблица их ещё не получила.');
    });
}

function savePendingKeys(keys, ask) {
    if (!keys || !keys.length) {
        if (ask) alert('Нет несохранённых назначений');
        return;
    }
    if (ask && !confirm('Сохранить ' + keys.length + ' назначений?')) return;
    var changeList = [];
    for (var i = 0; i < keys.length; i++) {
        var parts = keys[i].split('|');
        changeList.push({ uid: parts[0], source: parts[1], master: pendingChanges[keys[i]] || '' });
    }
    fetch('/api/batch_update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ changes: changeList })
    })
    .then(readApi)
    .then(function(data) {
        if (!data.success) {
            showToastModern('Не сохранилось: ' + (data.error || ''), 'error');
            return;
        }
        var allTickets = getAllTickets();
        for (var i = 0; i < changeList.length; i++) {
            var c = changeList[i];
            for (var j = 0; j < allTickets.length; j++) {
                if (allTickets[j].uid === c.uid) allTickets[j].master = c.master;
            }
            delete pendingChanges[c.uid + '|' + c.source];
        }
        updateChangesInfo();
        showToastModern('Назначение записано', 'success');
    })
    .catch(function() {
        showToastModern('Нет сети. Нажмите «Сохранить назначения», когда связь появится.', 'error');
    });
}

function saveAllChanges() {
    savePendingKeys(Object.keys(pendingChanges), true);
}

function openBroadcastModal() {
    document.getElementById('broadcastModal').classList.add('active');
}

function closeBroadcastModal() {
    document.getElementById('broadcastModal').classList.remove('active');
}

function sendBroadcast() {
    var text = (document.getElementById('broadcastText').value || '').trim();
    if (!text) {
        alert('Напишите текст');
        return;
    }
    if (!confirm('Отправить это сообщение всем кураторам?')) return;
    fetch('/api/broadcast_curators', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text })
    })
    .then(readApi)
    .then(function(data) {
        if (!data.success) {
            showToastModern('Не отправилось: ' + (data.error || ''), 'error');
            return;
        }
        document.getElementById('broadcastText').value = '';
        closeBroadcastModal();
        showToastModern('Отправлено: ' + (data.count || 0), 'success');
    })
    .catch(function() {
        showToastModern('Нет сети', 'error');
    });
}

function saveAllChangesOld() {
    var keys = Object.keys(pendingChanges);
    if (keys.length === 0) { alert('Нет изменений'); return; }
    if (!confirm('Сохранить ' + keys.length + ' изменений?')) return;
    document.getElementById('syncSpinner').style.display = 'block';
    document.getElementById('syncStatus').textContent = '⏳ Сохранение...';
    document.getElementById('syncStatus').style.color = '#f59e0b';
    var changeList = [];
    for (var i = 0; i < keys.length; i++) {
        var parts = keys[i].split('|');
        changeList.push({ uid: parts[0], source: parts[1], master: pendingChanges[keys[i]] });
    }
    fetch('/api/batch_update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ changes: changeList })
    })
    .then(readApi)
    .then(function(data) {
        if (data.success) {
            var allTickets = getAllTickets();
            for (var i = 0; i < changeList.length; i++) {
                var c = changeList[i];
                for (var j = 0; j < allTickets.length; j++) {
                    if (allTickets[j].uid === c.uid && allTickets[j].source === c.source) {
                        allTickets[j].master = c.master;
                    }
                }
            }
            pendingChanges = {};
            document.getElementById('syncStatus').textContent = '✅ Сохранено: ' + data.updated;
            document.getElementById('syncStatus').style.color = '#22c55e';
        } else {
            document.getElementById('syncStatus').textContent = '❌ Ошибка: ' + data.error;
            document.getElementById('syncStatus').style.color = '#ef4444';
        }
        document.getElementById('syncSpinner').style.display = 'none';
        updateChangesInfo();
        renderCurrentTab();
    })
    .catch(function() {
        document.getElementById('syncStatus').textContent = '❌ Ошибка сети';
        document.getElementById('syncStatus').style.color = '#ef4444';
        document.getElementById('syncSpinner').style.display = 'none';
    });
}

function syncAll() {
    var keys = Object.keys(pendingChanges);
    if (keys.length > 0) {
        if (!confirm('Есть несохранённые изменения. Синхронизация их отменит. Продолжить?')) return;
        pendingChanges = {};
        updateChangesInfo();
    }
    document.getElementById('syncSpinner').style.display = 'block';
    document.getElementById('syncStatus').textContent = '⏳ Синхронизация...';
    document.getElementById('syncStatus').style.color = '#f59e0b';
    fetch('/api/sync')
    .then(readApi)
    .then(function(data) {
        if (data.success) {
            document.getElementById('syncStatus').textContent = data.cached ? 'Данные уже свежие, таблицу не открывал' : 'Синхронизация завершена';
            document.getElementById('syncStatus').style.color = '#22c55e';
            setTimeout(function() { location.reload(); }, 1500);
        } else {
            document.getElementById('syncStatus').textContent = '❌ Ошибка: ' + data.error;
            document.getElementById('syncStatus').style.color = '#ef4444';
        }
        document.getElementById('syncSpinner').style.display = 'none';
    })
    .catch(function() {
        document.getElementById('syncStatus').textContent = '❌ Ошибка сети';
        document.getElementById('syncStatus').style.color = '#ef4444';
        document.getElementById('syncSpinner').style.display = 'none';
    });
}

// ============================================================
// ИНИЦИАЛИЗАЦИЯ
// ============================================================
document.addEventListener('DOMContentLoaded', function() {
    var tabs = document.querySelectorAll('.tab-btn');
    for (var i = 0; i < tabs.length; i++) {
        (function(btn) {
            btn.addEventListener('click', function() {
                var allTabs = document.querySelectorAll('.tab-btn');
                for (var j = 0; j < allTabs.length; j++) {
                    allTabs[j].classList.remove('active');
                }
                this.classList.add('active');
                var tabId = this.dataset.tab;
                try { localStorage.setItem('adminTab', tabId); } catch (e) {}
                var contents = document.querySelectorAll('.tab-content');
                for (var j = 0; j < contents.length; j++) {
                    contents[j].classList.remove('active');
                }
                document.getElementById(tabId).classList.add('active');
                renderCurrentTab();
            });
        })(tabs[i]);
    }
    var savedTab = '';
    try { savedTab = localStorage.getItem('adminTab') || ''; } catch (e) {}
    var savedBtn = savedTab ? document.querySelector('.tab-btn[data-tab="' + savedTab + '"]') : null;
    if (savedBtn) savedBtn.click();
    else renderCurrentTab();
    refreshQueueStatus();
    setInterval(refreshQueueStatus, 15000);
});

function refreshQueueStatus() {
    fetch('/api/queue_status')
        .then(readApi)
        .then(function(data) {
            window.queuedUids = {};
            var uids = data.uids || [];
            for (var i = 0; i < uids.length; i++) window.queuedUids[uids[i]] = 1;
            var el = document.getElementById('queueText');
            if (el) {
                el.textContent = data.count
                    ? ('В таблицу ещё не дошло: ' + data.count)
                    : 'Таблица догоняет сама';
            }
            var rows = document.querySelectorAll('.ticket-row');
            for (var j = 0; j < rows.length; j++) {
                var uid = rows[j].getAttribute('data-uid');
                if (window.queuedUids[uid]) rows[j].classList.add('queued-row');
                else rows[j].classList.remove('queued-row');
            }
        })
        .catch(function() {});
}

function flushQueueNow() {
    var el = document.getElementById('queueText');
    if (el) el.textContent = 'Записываем в таблицу…';
    fetch('/api/flush_now', { method: 'POST' })
        .then(readApi)
        .then(function(data) {
            if (!data.success) {
                showToastModern('Не удалось записать: ' + (data.error || ''), 'error');
                return;
            }
            showToastModern('Таблица обновлена', 'success');
            refreshQueueStatus();
        })
        .catch(function() {
            showToastModern('Нет сети', 'error');
        });
}