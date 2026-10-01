// ============================================================
// ОБЩИЕ ФУНКЦИИ
// ============================================================
function showToast(message, isError) {
    var toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.className = 'toast' + (isError ? ' error' : '');
    toast.style.display = 'block';
    setTimeout(function() { toast.style.display = 'none'; }, 3000);
}

function logout(event) {
    event.preventDefault();
    localStorage.removeItem('master_login');
    localStorage.removeItem('master_name');
    window.location.href = '/logout';
}

// ============================================================
// КОНТАКТЫ (ТЕЛЕФОН ИЛИ ТЕЛЕГРАМ)
// ============================================================
function openContact(contact) {
    if (!contact) return;
    var trimmed = contact.trim();
    
    if (trimmed.startsWith('@')) {
        var username = trimmed.slice(1);
        window.open('https://t.me/' + username, '_blank');
        return;
    }
    
    if (trimmed.match(/[\d\(\)\-\+]/)) {
        var phone = trimmed.replace(/[^0-9+]/g, '');
        if (phone) {
            window.location.href = 'tel:' + phone;
        }
        return;
    }
    
    alert('Контакт: ' + contact);
}

// ============================================================
// ЗАКРЫТИЕ МОДАЛОК
// ============================================================
function closeModal(modalId) {
    var modal = document.getElementById(modalId);
    if (modal) modal.classList.remove('active');
}

document.addEventListener('DOMContentLoaded', function() {
    var modals = document.querySelectorAll('.modal');
    for (var i = 0; i < modals.length; i++) {
        (function(modal) {
            modal.addEventListener('click', function(e) {
                if (e.target === this) {
                    this.classList.remove('active');
                }
            });
        })(modals[i]);
    }
});

// ============================================================
// ФУНКЦИИ ДЛЯ ЭВАКУАЦИИ (ДЛЯ ВСЕХ МАСТЕРОВ)
// ============================================================

// Редактирование поля
function editField(uid, className) {
    var input = document.querySelector('#' + className + '_' + uid);
    if (input) {
        input.readOnly = false;
        input.focus();
        input.classList.remove('readonly');
    }
}

// Проверка поля
function checkField(uid, className) {
    var input = document.querySelector('#' + className + '_' + uid);
    var statusIcon = document.querySelector('#' + className + 'Status_' + uid);
    
    if (!input) return;
    
    if (input.value.trim()) {
        input.classList.add('valid');
        if (statusIcon) {
            statusIcon.textContent = '✅';
            statusIcon.style.color = '#22c55e';
        }
        showToast('✅ Поле заполнено', false);
    } else {
        if (statusIcon) {
            statusIcon.textContent = '❌';
            statusIcon.style.color = '#ef4444';
        }
        showToast('❌ Поле пустое!', true);
    }
}

// Отправка эвакуации (для всех мастеров)
function submitEvacuation(name, darksNumber, uid, gos) {
    // Проверяем поля старого велосипеда
    var oldSerial = document.getElementById('oldSerial_' + uid);
    var oldGos = document.getElementById('oldGos_' + uid);
    var oldIot = document.getElementById('oldIot_' + uid);
    
    // Проверяем поля нового велосипеда
    var newSerial = document.getElementById('newSerial_' + uid);
    var newGos = document.getElementById('newGos_' + uid);
    var newIot = document.getElementById('newIot_' + uid);
    
    // Проверка заполнения
    if (!oldSerial.value.trim() || !oldGos.value.trim() || !oldIot.value.trim()) {
        showToast('❌ Заполните все поля СТАРОГО велосипеда!', true);
        return false;
    }
    
    if (!newSerial.value.trim() || !newGos.value.trim() || !newIot.value.trim()) {
        showToast('❌ Заполните все поля НОВОГО велосипеда!', true);
        return false;
    }
    
    // Подтверждение
    var message = 'Отправить данные по замене велосипеда?\n\n';
    message += '📌 ЗАБРАТЬ:\n';
    message += '  Серийный: ' + oldSerial.value + '\n';
    message += '  Гос: ' + oldGos.value + '\n';
    message += '  Айот: ' + oldIot.value + '\n\n';
    message += '📌 ОТДАТЬ:\n';
    message += '  Серийный: ' + newSerial.value + '\n';
    message += '  Гос: ' + newGos.value + '\n';
    message += '  Айот: ' + newIot.value;
    
    if (!confirm(message)) return false;
    
    // Собираем данные
    var data = {
        uid: uid,
        master: name,
        darks_number: darksNumber,
        address: document.querySelector('input[name="address"]').value || '',
        old_data: {
            serial: oldSerial.value.trim(),
            gos: oldGos.value.trim(),
            iot: oldIot.value.trim()
        },
        new_data: {
            serial: newSerial.value.trim(),
            gos: newGos.value.trim(),
            iot: newIot.value.trim()
        }
    };
    
    // Отправка
    fetch('/master/evacuation/replace', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(data)
    })
    .then(readJsonResponse)
    .then(function(result) {
        if (result.success) {
            var card = document.getElementById('ticket-' + uid);
            if (card) card.style.display = 'none';
            showToast('Замена отправлена');
        } else {
            showToast(result.error || 'Не получилось', true);
        }
    })
    .catch(function() {
        queueAction({
            url: '/master/evacuation/replace',
            method: 'POST',
            body: JSON.stringify(data),
            contentType: 'application/json',
            uid: uid
        });
        var card = document.getElementById('ticket-' + uid);
        if (card) card.style.display = 'none';
        showToast('Сохранено на телефоне. Отправится, когда будет сеть.');
    });
    
    return false;
}

// ============================================================
// ОЧЕРЕДЬ НА ТЕЛЕФОНЕ
// ============================================================
var OUTBOX_KEY = 'master_outbox_v1';
var outboxFlushing = false;

function readOutbox() {
    try {
        return JSON.parse(localStorage.getItem(OUTBOX_KEY) || '[]');
    } catch (e) {
        return [];
    }
}

function writeOutbox(items) {
    localStorage.setItem(OUTBOX_KEY, JSON.stringify(items));
    renderOutboxBar();
    hideQueuedTickets();
}

function queueAction(item) {
    var items = readOutbox().filter(function(x) { return x.uid !== item.uid; });
    item.id = Date.now() + '-' + Math.random().toString(16).slice(2);
    items.push(item);
    writeOutbox(items);
}

function hideQueuedTickets() {
    var uids = {};
    readOutbox().forEach(function(x) { if (x.uid) uids[x.uid] = 1; });
    var cards = document.querySelectorAll('[data-uid]');
    for (var i = 0; i < cards.length; i++) {
        if (uids[cards[i].getAttribute('data-uid')]) cards[i].style.display = 'none';
    }
}

function renderOutboxBar() {
    var count = readOutbox().length;
    var bar = document.getElementById('outboxBar');
    if (!bar) {
        bar = document.createElement('div');
        bar.id = 'outboxBar';
        bar.className = 'outbox-bar';
        document.body.insertBefore(bar, document.body.firstChild);
    }
    if (!count) {
        bar.style.display = 'none';
        return;
    }
    bar.style.display = 'block';
    bar.textContent = 'На телефоне ждёт отправки: ' + count + '. Уйдёт само, когда появится сеть.';
}

function formBody(formData) {
    var params = new URLSearchParams();
    formData.forEach(function(value, key) { params.append(key, value); });
    return params.toString();
}

function readJsonResponse(response) {
    var type = response.headers.get('content-type') || '';
    if (type.indexOf('application/json') === -1) {
        throw new Error('not json');
    }
    return response.json();
}

function flushOutbox() {
    if (outboxFlushing || !navigator.onLine) return;
    var items = readOutbox();
    if (!items.length) return;
    outboxFlushing = true;
    var item = items[0];
    var opts = { method: item.method || 'POST', credentials: 'same-origin' };
    if (item.body) {
        opts.body = item.body;
        opts.headers = { 'Content-Type': item.contentType || 'application/x-www-form-urlencoded' };
    }
    fetch(item.url, opts)
        .then(readJsonResponse)
        .then(function(data) {
            outboxFlushing = false;
            if (data && data.success) {
                writeOutbox(readOutbox().filter(function(x) { return x.id !== item.id; }));
                flushOutbox();
            }
        })
        .catch(function() {
            outboxFlushing = false;
        });
}

function sendOrQueue(opts) {
    var card = document.getElementById('ticket-' + opts.uid);
    function hideCard(saved) {
        if (card) card.style.display = 'none';
        showToast(saved ? 'Сохранено на телефоне. Отправится, когда будет сеть.' : (opts.okText || 'Готово'));
    }
    var fetchOpts = { method: opts.method || 'POST', credentials: 'same-origin' };
    if (opts.body) {
        fetchOpts.body = opts.body;
        fetchOpts.headers = { 'Content-Type': opts.contentType || 'application/x-www-form-urlencoded' };
    }
    if (!navigator.onLine) {
        queueAction(opts);
        hideCard(true);
        return;
    }
    fetch(opts.url, fetchOpts)
        .then(readJsonResponse)
        .then(function(data) {
            if (!data || data.success === false) {
                showToast((data && data.error) ? data.error : 'Не получилось', true);
                return;
            }
            hideCard(false);
        })
        .catch(function() {
            queueAction(opts);
            hideCard(true);
        });
}

function togglePanel(uid) {
    var panel = document.getElementById('panel-' + uid);
    var more = document.getElementById('more-' + uid);
    if (more) more.hidden = true;
    if (!panel) return;
    panel.hidden = !panel.hidden;
    if (!panel.hidden) {
        var input = panel.querySelector('input, textarea');
        if (input) input.focus();
    }
}

function toggleMore(uid) {
    var panel = document.getElementById('panel-' + uid);
    var more = document.getElementById('more-' + uid);
    if (panel) panel.hidden = true;
    if (!more) return;
    more.hidden = !more.hidden;
}

document.addEventListener('DOMContentLoaded', function() {
    renderOutboxBar();
    hideQueuedTickets();
    flushOutbox();
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js').catch(function() {});
    }
});
window.addEventListener('online', flushOutbox);
setInterval(flushOutbox, 20000);

// ============================================================
// ФУНКЦИИ ДЛЯ ТРАНЗИТА (ЗАКРЫВАЕТ ВСЕ ЗАЯВКИ С ОДНИМ ГОСНОМЕРОМ)
// ============================================================

function transitCloseTicket(form, action) {
    var parts = form.querySelector('input[name="parts"]');
    if (!parts.value.trim()) {
        alert('Укажите запчасти!');
        return false;
    }
    
    var uid = form.action.split('/').pop();
    
    if (!confirm('Закрыть ВСЕ заявки на этот велосипед?')) {
        return false;
    }
    var fd = new FormData(form);
    sendOrQueue({
        url: form.action,
        method: 'POST',
        body: formBody(fd),
        uid: uid,
        okText: 'Готово'
    });
    return false;
}

// ============================================================
// АКБ И ЗАРЯДКИ
// ============================================================

function openTakenModal(name, darks, uid) {
    var modal = document.getElementById('takenModal');
    if (!modal) return;
    
    var form = document.getElementById('takenForm');
    var input = document.getElementById('takenInput');
    input.value = '';
    
    form.onsubmit = function(e) {
        e.preventDefault();
        var count = parseInt(input.value);
        if (!count || count <= 0) {
            alert('Укажите количество больше 0!');
            return false;
        }
        
        var fd = new FormData();
        fd.append('parts', count);
        sendOrQueue({
            url: '/master/' + name + '/darks/' + darks + '/taken_no_replace/' + uid,
            method: 'POST',
            body: formBody(fd),
            uid: uid,
            okText: 'Забрано ' + count + ' шт. без замены'
        });
        closeModal('takenModal');
        return false;
    };
    
    modal.classList.add('active');
}

function openReplaceNoModal(name, darks, uid) {
    var modal = document.getElementById('replaceNoModal');
    if (!modal) return;
    
    var form = document.getElementById('replaceNoForm');
    var textarea = document.getElementById('replaceNoInput');
    textarea.value = '';
    
    form.onsubmit = function(e) {
        e.preventDefault();
        
        var fd = new FormData();
        fd.append('reason', textarea.value || 'Куратор не предоставил');
        sendOrQueue({
            url: '/master/' + name + '/darks/' + darks + '/replace_no/' + uid,
            method: 'POST',
            body: formBody(fd),
            uid: uid,
            okText: 'Отмечено: куратор не предоставил'
        });
        closeModal('replaceNoModal');
        return false;
    };
    
    modal.classList.add('active');
}

// ============================================================
// СТАНДАРТНЫЕ ФУНКЦИИ (велосипеды)
// ============================================================

function validateQuantity(form) {
    var input = form.querySelector('input[name="parts"]');
    if (parseInt(input.value) <= 0) {
        alert('Укажите количество больше 0!');
        return false;
    }
    return true;
}

function closeTicketInstant(name, darks, uid, action) {
    if (!confirm('Велосипеда нет на месте? Заявка останется у руководителя, таймер начнётся заново.')) return;
    sendOrQueue({
        url: '/master/' + name + '/darks/' + darks + '/' + action + '/' + uid,
        method: 'GET',
        uid: uid,
        okText: 'Заявка перенесена: вело не найдено'
    });
    return false;
}

function closeTicket(form) {
    var parts = form.querySelector('[name="parts"]');
    if (!parts || !String(parts.value).trim()) {
        alert('Напишите, что сделали');
        return false;
    }
    if (parts.type === 'number' && parseInt(parts.value, 10) <= 0) {
        alert('Укажите количество больше 0');
        return false;
    }
    var uid = form.action.split('/').filter(Boolean).pop();
    var fd = new FormData(form);
    sendOrQueue({
        url: form.action,
        method: 'POST',
        body: formBody(fd),
        uid: uid,
        okText: 'Готово'
    });
    return false;
}

function openModal(name, darks, uid, type, title) {
    var modal = document.getElementById('reasonModal');
    if (!modal) return;
    document.getElementById('modalTitle').textContent = title;
    var form = document.getElementById('reasonForm');
    var textarea = document.getElementById('reasonInput');
    textarea.value = '';
    
    if (type === 'evacuation') {
        textarea.placeholder = 'Укажите причину эвакуации...';
        form.onsubmit = function(e) {
            e.preventDefault();
            var reason = textarea.value;
            if (!reason.trim()) {
                alert('Укажите причину эвакуации!');
                return false;
            }
            var fd = new FormData();
            fd.append('reason', reason);
            sendOrQueue({
                url: '/master/' + name + '/darks/' + darks + '/evacuation/' + uid,
                method: 'POST',
                body: formBody(fd),
                uid: uid,
                okText: 'Эвакуация отмечена'
            });
            closeModal('reasonModal');
            return false;
        };
    }
    
    form.action = '/master/' + name + '/darks/' + darks + '/' + type + '/' + uid;
    modal.classList.add('active');
}