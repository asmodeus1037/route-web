// ============================================================
// ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
// ============================================================
var DATA = window.__DATA__ || {};
var masters = DATA.masters || [];
var directionsData = DATA.directions || {};
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

function generateNotifyPreview() {
    var allTickets = getAllTickets();
    var assigned = [];
    for (var i = 0; i < allTickets.length; i++) {
        var t = allTickets[i];
        if (t.master && !t.is_done) assigned.push(t);
    }
    if (assigned.length === 0) {
        document.getElementById('notifyPreview').textContent = 'Нет назначенных заявок для оповещения';
        return;
    }
    var groups = {};
    for (var i = 0; i < assigned.length; i++) {
        var t = assigned[i];
        var key = t.darks || 'без номера';
        if (!groups[key]) {
            groups[key] = { address: t.address || 'Адрес не указан', darks: key, tickets: [] };
        }
        groups[key].tickets.push(t);
    }
    var preview = '📢 Привет, на связи Vanta Bikes!\n\n';
    for (var key in groups) {
        var group = groups[key];
        preview += '📍 ' + group.address + ' (даркстор ' + key + ')\n';
        preview += '📋 Заявки (' + group.tickets.length + '):\n';
        for (var j = 0; j < group.tickets.length; j++) {
            var t = group.tickets[j];
            var identifier = (t.type && (t.type === 'Аккумуляторная батарея' || t.type === 'Зарядное устройство')) 
                ? t.type 
                : (t.gos || 'Без номера');
            preview += '   ' + identifier + ' | ' + (t.desc || '-') + '\n';
        }
        preview += '\n';
    }
    preview += 'Подготовьте, пожалуйста, технику к ремонту\n';
    preview += 'Хорошего дня! 🙌';
    document.getElementById('notifyPreview').textContent = preview;
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
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.success) {
            showToastModern('✅ Уведомления отправлены!', 'success');
            closeNotifyModal();
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
    applyStatusToTicket(ticket, status, note);
    if (select) select.dataset.oldStatus = status;
    renderCurrentTab();
    fetch('/api/update_status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uid: uid, status: status, note: note, skip_report: true })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (!data.success) {
            showToastModern('Не сохранилось: ' + (data.error || ''), 'error');
            return;
        }
        showToastModern('Статус сохранён', 'success');
        refreshQueueStatus();
    })
    .catch(function() {
        showToastModern('Нет сети. Статус на экране уже изменён, в таблицу уйдёт при связи.', 'error');
    });
}

function applyStatusToTicket(ticket, status, note) {
    if (!ticket) return;
    var statusMap = {
        '🟡 В работе': 'pending',
        '✅ Выполнено': 'done',
        '🔵 Доделать': 'todo',
        '⏹️ Обработано': 'processed',
        '🔧 Эвакуация': 'todo'
    };
    ticket.status = statusMap[status] || 'pending';
    ticket.is_done = ticket.status === 'done';
    ticket.is_active = ticket.status !== 'done';
    if (status === '🔧 Эвакуация') {
        ticket.note = ((ticket.note || '') + '\nЭВАКУАЦИЯ: ' + (note || 'Эвакуация')).trim();
        ticket.display_desc = ticket.desc;
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
    .then(function(r) { return r.json(); })
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
    for (var i = 0; i < all.length; i++) {
        if (uids.indexOf(all[i].uid) !== -1) {
            applyStatusToTicket(all[i], status, note);
        }
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
    .then(function(r) { return r.json(); })
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
        html += '<button class="btn btn-sm btn-send" onclick="selectMaster(\'' + master + '\')"' + (count === 0 ? ' disabled style="opacity:0.5;"' : '') + '>Выбрать</button>';
        html += '</div>';
    }
    list.innerHTML = html;
    selectedMaster = '';
    document.getElementById('sendToMasterBtn').style.display = 'none';
    document.getElementById('sendToAllBtn').style.display = 'inline-block';
    modal.classList.add('active');
}

function closeSendModal() {
    document.getElementById('sendModal').classList.remove('active');
}

function selectMaster(master) {
    selectedMaster = master;
    var btns = document.querySelectorAll('#sendMasterList .btn-send');
    for (var i = 0; i < btns.length; i++) {
        btns[i].style.background = '#e2e8f0';
    }
    for (var i = 0; i < btns.length; i++) {
        if (btns[i].textContent.indexOf('Выбрать') !== -1 && btns[i].parentElement.textContent.indexOf(master) !== -1) {
            btns[i].style.background = '#22c55e';
            btns[i].style.color = 'white';
        }
    }
    var sendBtn = document.getElementById('sendToMasterBtn');
    sendBtn.textContent = '📤 Обновить ' + master;
    sendBtn.style.display = 'inline-block';
    document.getElementById('sendToAllBtn').style.display = 'inline-block';
    document.getElementById('syncStatus').textContent = '📝 Выбран: ' + master;
    document.getElementById('syncStatus').style.color = '#f59e0b';
}

function sendRoute() {
    if (!selectedMaster) {
        alert('Выберите мастера!');
        return;
    }
    if (!confirm('Обновить кэш мастера ' + selectedMaster + '?')) return;
    document.getElementById('syncSpinner').style.display = 'block';
    document.getElementById('syncStatus').textContent = '⏳ Обновление...';
    document.getElementById('syncStatus').style.color = '#f59e0b';
    fetch('/api/send_route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ master: selectedMaster })
    })
    .then(function(r) { return r.json(); })
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
    if (!confirm('Обновить кэши ВСЕХ мастеров?')) return;
    document.getElementById('syncSpinner').style.display = 'block';
    document.getElementById('syncStatus').textContent = '⏳ Обновление...';
    document.getElementById('syncStatus').style.color = '#f59e0b';
    fetch('/api/send_route_all', { method: 'POST' })
    .then(function(r) { return r.json(); })
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
    .then(function(r) { return r.json(); })
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

function clearAllMasters() {
    if (!confirm('Снять ВСЕХ мастеров со всех заявок?')) return;
    document.getElementById('syncSpinner').style.display = 'block';
    document.getElementById('syncStatus').textContent = '⏳ Снятие...';
    document.getElementById('syncStatus').style.color = '#f59e0b';
    fetch('/api/clear_dates', { method: 'POST' })
    .then(function(r) { return r.json(); })
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
                          (t.gos || '').toLowerCase().indexOf(search) !== -1;
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
    
    for (var i = 0; i < active.length; i++) {
        var t = active[i];
        // Проверяем на эвакуацию
        if (t.status === 'evacuation' || 
            (t.status === 'todo' && t.note && t.note.indexOf('ЭВАКУАЦИЯ:') !== -1)) {
            evacuation++;
        } else if (t.status === 'pending') {
            pending++;
        } else if (t.status === 'todo') {
            todo++;
        } else if (t.status === 'processed' || t.status === 'fail') {
            processed++;
        }
        if (!t.master) unassigned++;
    }
    
    // Обновляем статистику
    document.getElementById('totalCount').textContent = active.length;
    document.getElementById('pendingCount').textContent = pending;
    document.getElementById('evacuationCount').textContent = evacuation;
    document.getElementById('todoCount').textContent = todo;
    var processedEl = document.getElementById('processedCount');
    if (processedEl) processedEl.textContent = processed;
    document.getElementById('unassignedCount').textContent = unassigned;
    
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
        if (group.contact) { html += '<div class="darks-contact">📞 ' + group.contact + '</div>'; }
        for (var j = 0; j < group.tickets.length; j++) {
            var t = group.tickets[j];
            
            // Определяем статус для отображения
            var statusDisplay = t.status;
            var isEvacuation = false;
            
            if (t.status === 'todo' && t.note && t.note.indexOf('ЭВАКУАЦИЯ:') !== -1) {
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
            
            html += '<div class="ticket-row' + (isEvacuation ? ' evacuation-row' : '') + (isProcessed ? ' processed-row' : '') + (isQueued ? ' queued-row' : '') + '" data-uid="' + t.uid + '">';
            html += '<span class="id">' + (t.gos || '-') + '</span>';
            html += '<span class="desc" title="' + (t.display_desc || t.desc || '-') + '">' + (t.display_desc || t.desc || '-');
            if (t.contact) {
                html += '<span class="who">отправил: ' + String(t.contact).replace(/[&<>]/g, '') + '</span>';
            }
            html += '</span>';
            html += '<span class="type-badge">' + typeDisplay + '</span>';
            html += '<span><select class="master-select" data-uid="' + t.uid + '" data-source="' + t.source + '" onchange="onMasterChange(this)"><option value="">—</option>';
            for (var mi = 0; mi < masters.length; mi++) {
                var m = masters[mi];
                html += '<option value="' + m + '"' + (currentMaster === m ? ' selected' : '') + '>' + m + '</option>';
            }
            html += '</select></span>';
            
            html += '<span style="min-width:80px;display:inline-block;">';
            html += '<select class="status-select" data-uid="' + t.uid + '" data-old-status="' + statusDisplay + '" onchange="updateStatus(this)" style="padding:2px 6px;border-radius:4px;border:1px solid #d1d5db;font-size:10px;background:white;width:100%;max-width:100px;cursor:pointer;">';
            html += '<option value="🟡 В работе"' + (statusDisplay === '🟡 В работе' ? ' selected' : '') + '>🟡 В работе</option>';
            html += '<option value="✅ Выполнено"' + (statusDisplay === '✅ Выполнено' ? ' selected' : '') + '>✅ Выполнено</option>';
            html += '<option value="🔵 Доделать"' + (statusDisplay === '🔵 Доделать' ? ' selected' : '') + '>🔵 Доделать</option>';
            html += '<option value="⏹️ Обработано"' + (statusDisplay === '⏹️ Обработано' ? ' selected' : '') + '>⏹️ Обработано</option>';
            html += '<option value="🔧 Эвакуация"' + (statusDisplay === '🔧 Эвакуация' ? ' selected' : '') + '>🔧 Эвакуация</option>';
            html += '</select>';
            html += '</span>';
            
            html += '<span class="hours ' + hoursClass + '">⏱️ ' + hoursDisplay + ' ч</span>';
            html += '</div>';
        }
        html += '<div class="assign-all-bar"><label>📌 Все:</label><select id="assignMaster_' + group.darks + '"><option value="">—</option>';
        for (var mi2 = 0; mi2 < masters.length; mi2++) {
            html += '<option value="' + masters[mi2] + '">' + masters[mi2] + '</option>';
        }
        html += '</select><button type="button" class="btn btn-success" onclick="assignAllDarks(\'' + String(group.darks).replace(/'/g, '') + '\')">Назначить</button>';
        html += '<button type="button" class="btn btn-success" data-darks="' + group.darks + '" onclick="saveDarksChanges(this)">Сохранить</button></div>';
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
}

function assignAllDarks(darks) {
    var select = document.getElementById('assignMaster_' + darks);
    var master = select.value;
    if (!master) { alert('Выберите мастера'); return; }
    var allTickets = getAllTickets();
    for (var i = 0; i < allTickets.length; i++) {
        var t = allTickets[i];
        if (t.darks === darks && !t.is_done) {
            var key = t.uid + '|' + t.source;
            pendingChanges[key] = master;
        }
    }
    updateChangesInfo();
    renderCurrentTab();
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
        el.textContent = 'Назначения уходят в таблицу кнопкой «Сохранить» у даркстора';
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
    .then(function(r) { return r.json(); })
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

function saveAllChanges() {
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
    .then(function(r) { return r.json(); })
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
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.success) {
            document.getElementById('syncStatus').textContent = '✅ Синхронизация завершена';
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
        .then(function(r) { return r.json(); })
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
        .then(function(r) { return r.json(); })
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