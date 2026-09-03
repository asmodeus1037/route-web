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

// ============================================================
// ВЫХОД
// ============================================================
function adminLogout(event) {
    if (event) event.preventDefault();
    localStorage.removeItem('master_login');
    localStorage.removeItem('master_name');
    window.location.href = '/logout';
}

// ============================================================
// ВСПОМОГАТЕЛЬНЫЕ
// ============================================================
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
// УВЕДОМЛЕНИЯ
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
    var preview = '📢 Будет отправлено кураторам:\n\n';
    for (var key in groups) {
        var group = groups[key];
        preview += '📍 ' + group.address + ' (даркстор ' + key + ')\n';
        preview += '📋 Заявки (' + group.tickets.length + '):\n';
        for (var j = 0; j < group.tickets.length; j++) {
            var t = group.tickets[j];
            preview += '   ' + (t.gos || 'Без номера') + ' | ' + (t.desc || '-') + '\n';
        }
        preview += '\n';
    }
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
            alert('✅ Уведомления отправлены!');
            closeNotifyModal();
        } else {
            alert('❌ Ошибка: ' + (data.error || 'неизвестная'));
        }
    })
    .catch(function() {
        alert('❌ Ошибка отправки');
    });
}

// ============================================================
// ДЕЙСТВИЯ АДМИНА
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
            document.getElementById('actionExtraInput').placeholder = 'Причина эвакуации...';
        } else if (action === 'taken') {
            document.getElementById('actionExtraInput').placeholder = 'Количество АКБ...';
        } else {
            document.getElementById('actionExtraInput').placeholder = 'Запчасти...';
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
        alert('Заполните дополнительную информацию!');
        return;
    }
    if (!confirm('Применить действие "' + actionTarget.action + '" к заявке ' + actionTarget.uid + '?')) return;
    fetch('/api/admin_action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            uid: actionTarget.uid,
            source: actionTarget.source,
            action: actionTarget.action,
            extra: extra
        })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.success) {
            alert('✅ Действие выполнено!');
            closeActionModal();
            location.reload();
        } else {
            alert('❌ Ошибка: ' + data.error);
        }
    })
    .catch(function() {
        alert('❌ Ошибка сети');
    });
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
            document.getElementById('syncStatus').textContent = '✅ Кэш мастера ' + selectedMaster + ' обновлен';
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
    html += '<button class="btn btn-sm btn-danger" onclick="selectClearMaster(\'all\')">Выбрать</button>';
    html += '</div>';
    for (var j = 0; j < masters.length; j++) {
        var master = masters[j];
        var count = counts[master] || 0;
        html += '<div style="padding:8px 12px;border-bottom:1px solid #e2e8f0;display:flex;justify-content:space-between;align-items:center;">';
        html += '<span><strong>👤 ' + master + '</strong></span>';
        html += '<span class="count">' + (count > 0 ? count + ' заявок' : 'нет заявок') + '</span>';
        html += '<button class="btn btn-sm btn-clear-master" onclick="selectClearMaster(\'' + master + '\')"' + (count === 0 ? ' disabled style="opacity:0.5;"' : '') + '>Выбрать</button>';
        html += '</div>';
    }
    list.innerHTML = html;
    modal.classList.add('active');
}

function closeClearMasterModal() {
    document.getElementById('clearMasterModal').classList.remove('active');
}

function selectClearMaster(master) {
    clearMasterTarget = master;
    var btns = document.querySelectorAll('#clearMasterList .btn-clear-master, #clearMasterList .btn-danger');
    for (var i = 0; i < btns.length; i++) {
        btns[i].style.background = '#e2e8f0';
    }
    var allBtns = document.querySelectorAll('#clearMasterList .btn-clear-master, #clearMasterList .btn-danger');
    for (var i = 0; i < allBtns.length; i++) {
        var btn = allBtns[i];
        if (btn.textContent.indexOf('Выбрать') !== -1 && btn.parentElement.textContent.indexOf(master === 'all' ? 'Все мастера' : master) !== -1) {
            btn.style.background = '#ef4444';
            btn.style.color = 'white';
        }
    }
    document.getElementById('syncStatus').textContent = '📝 Выбран: ' + (master === 'all' ? 'Все мастера' : master);
    document.getElementById('syncStatus').style.color = '#f59e0b';
}

function clearMaster() {
    if (!clearMasterTarget) {
        alert('Выберите мастера!');
        return;
    }
    var name = clearMasterTarget === 'all' ? 'ВСЕХ мастеров' : clearMasterTarget;
    if (!confirm('Снять заявки с ' + name + '?')) return;
    document.getElementById('syncSpinner').style.display = 'block';
    document.getElementById('syncStatus').textContent = '⏳ Снятие...';
    document.getElementById('syncStatus').style.color = '#f59e0b';
    fetch('/api/clear_master', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ master: clearMasterTarget === 'all' ? null : clearMasterTarget })
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

function clearAllDates() {
    if (!confirm('Очистить всех мастеров у заявок в работе/доделать/обработано?')) return;
    document.getElementById('syncSpinner').style.display = 'block';
    document.getElementById('syncStatus').textContent = '⏳ Очистка...';
    document.getElementById('syncStatus').style.color = '#f59e0b';
    fetch('/api/clear_dates', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.success) {
            document.getElementById('syncStatus').textContent = '✅ Мастера очищены';
            document.getElementById('syncStatus').style.color = '#22c55e';
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
    var tickets = [];
    var containerId = '';
    var dirIndex = 0;
    for (var dirName in directionsData) {
        dirIndex++;
        if (dirIndex === tabIndex) {
            tickets = directionsData[dirName].tickets || [];
            containerId = 'renderDir' + tabIndex;
            break;
        }
    }
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
    var pending = [];
    var todo = [];
    var unassigned = [];
    for (var i = 0; i < active.length; i++) {
        var t = active[i];
        if (t.status !== 'todo') pending.push(t);
        else todo.push(t);
        if (!t.master) unassigned.push(t);
    }
    document.getElementById('totalCount').textContent = active.length;
    document.getElementById('pendingCount').textContent = pending.length;
    document.getElementById('todoCount').textContent = todo.length;
    document.getElementById('unassignedCount').textContent = unassigned.length;
    
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
            var statusLabel = t.status === 'todo' ? '🔧 Доделать' : '🟡 В работе';
            var hoursDisplay = t.hours_since !== undefined ? t.hours_since.toFixed(1) : '0';
            var uidKey = t.uid + '|' + t.source;
            var currentMaster = pendingChanges[uidKey] !== undefined ? pendingChanges[uidKey] : (t.master || '');
            var typeDisplay = t.bike_type || t.type || 'Не указан';
            var isEvacuation = false;
            if (t.status === 'todo' && t.note && t.note.indexOf('ЭВАКУАЦИЯ:') !== -1) {
                isEvacuation = true;
            }
            var hoursClass = '';
            if (t.hours_since > 48) hoursClass = 'overdue';
            else if (t.hours_since >= 32) hoursClass = 'warning';
            
            html += '<div class="ticket-row' + (isEvacuation ? ' evacuation-row' : '') + '">';
            html += '<span class="id">' + (t.gos || '-') + '</span>';
            html += '<span class="desc">' + (t.display_desc || t.desc || '-') + '</span>';
            html += '<span class="type-badge">' + typeDisplay + '</span>';
            html += '<span><select class="master-select" data-uid="' + t.uid + '" data-source="' + t.source + '" onchange="onMasterChange(this)"><option value="">—</option>';
            for (var mi = 0; mi < masters.length; mi++) {
                var m = masters[mi];
                html += '<option value="' + m + '"' + (currentMaster === m ? ' selected' : '') + '>' + m + '</option>';
            }
            html += '</select></span>';
            html += '<span class="status-badge status-pending">' + statusLabel + '</span>';
            html += '<span class="hours ' + hoursClass + '">⏱️ ' + hoursDisplay + ' ч</span>';
            html += '<button class="action-btn" onclick="openActionModal(\'' + t.uid + '\', \'' + t.source + '\')" title="Действия">⚙️</button>';
            html += '</div>';
        }
        html += '<div class="assign-all-bar"><label>📌 Все:</label><select id="assignMaster_' + group.darks + '"><option value="">—</option>';
        for (var mi2 = 0; mi2 < masters.length; mi2++) {
            html += '<option value="' + masters[mi2] + '">' + masters[mi2] + '</option>';
        }
        html += '</select><button class="btn btn-success" onclick="assignAllDarks(\'' + group.darks + '\')">✅ Назначить</button></div>';
        html += '</div>';
    }
    container.innerHTML = html;
    updateChangesInfo();
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
        el.textContent = '💡 Изменения сохраняются по кнопке';
        document.getElementById('syncStatus').textContent = '✅ Готово';
        document.getElementById('syncStatus').style.color = '#22c55e';
    }
}

// ============================================================
// СОХРАНЕНИЕ И СИНХРОНИЗАЦИЯ
// ============================================================
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
                var contents = document.querySelectorAll('.tab-content');
                for (var j = 0; j < contents.length; j++) {
                    contents[j].classList.remove('active');
                }
                document.getElementById(tabId).classList.add('active');
                renderCurrentTab();
            });
        })(tabs[i]);
    }
    renderCurrentTab();
});