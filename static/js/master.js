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
        body: JSON.stringify(data)
    })
    .then(function(response) { return response.json(); })
    .then(function(result) {
        if (result.success) {
            showToast('✅ Замена отправлена!', false);
            // Удаляем карточку
            var card = document.getElementById('ticket-' + uid);
            if (card) {
                card.style.transition = 'opacity 0.3s';
                card.style.opacity = '0';
                setTimeout(function() { card.remove(); }, 300);
            }
        } else {
            showToast('❌ Ошибка: ' + result.error, true);
        }
    })
    .catch(function() {
        showToast('❌ Ошибка соединения', true);
    });
    
    return false;
}

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
    
    var card = document.getElementById('ticket-' + uid);
    if (card) {
        card.style.transition = 'opacity 0.3s';
        card.style.opacity = '0';
        setTimeout(function() { card.remove(); }, 300);
    }
    
    showToast('✅ Все заявки на велосипед закрыты');
    var formData = new FormData(form);
    fetch(form.action, { method: 'POST', body: formData });
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
        
        var card = document.getElementById('ticket-' + uid);
        if (card) {
            card.style.transition = 'opacity 0.3s';
            card.style.opacity = '0';
            setTimeout(function() { card.remove(); }, 300);
        }
        
        var formData = new FormData();
        formData.append('parts', count);
        
        fetch('/master/' + name + '/darks/' + darks + '/taken_no_replace/' + uid, {
            method: 'POST',
            body: formData
        });
        
        showToast('📦 Забрано ' + count + ' шт. без замены');
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
        
        var card = document.getElementById('ticket-' + uid);
        if (card) {
            card.style.transition = 'opacity 0.3s';
            card.style.opacity = '0';
            setTimeout(function() { card.remove(); }, 300);
        }
        
        var formData = new FormData();
        formData.append('reason', textarea.value || 'Куратор не предоставил');
        
        fetch('/master/' + name + '/darks/' + darks + '/replace_no/' + uid, {
            method: 'POST',
            body: formData
        });
        
        showToast('❌ Отмечено как "Куратор не предоставил"');
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
    if (!confirm('Отметить заявку как "Вело отсутствует"?')) return;
    var card = document.getElementById('ticket-' + uid);
    if (card) {
        card.style.transition = 'opacity 0.3s';
        card.style.opacity = '0';
        setTimeout(function() { card.remove(); }, 300);
    }
    fetch('/master/' + name + '/darks/' + darks + '/' + action + '/' + uid, { method: 'GET' });
    showToast('✅ Заявка отмечена как "Вело отсутствует"');
    return false;
}

function closeTicket(form, action) {
    var parts = form.querySelector('input[name="parts"]');
    if (!parts.value.trim()) {
        alert('Укажите запчасти!');
        return false;
    }
    var uid = form.action.split('/').pop();
    var card = document.getElementById('ticket-' + uid);
    if (card) {
        card.style.transition = 'opacity 0.3s';
        card.style.opacity = '0';
        setTimeout(function() { card.remove(); }, 300);
    }
    showToast('✅ Заявка закрыта');
    var formData = new FormData(form);
    fetch(form.action, { method: 'POST', body: formData });
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
            var card = document.getElementById('ticket-' + uid);
            if (card) {
                card.style.transition = 'opacity 0.3s';
                card.style.opacity = '0';
                setTimeout(function() { card.remove(); }, 300);
            }
            var formData = new FormData();
            formData.append('reason', reason);
            fetch('/master/' + name + '/darks/' + darks + '/evacuation/' + uid, {
                method: 'POST',
                body: formData
            });
            showToast('🚚 Заявка отправлена на эвакуацию');
            closeModal('reasonModal');
            return false;
        };
    }
    
    form.action = '/master/' + name + '/darks/' + darks + '/' + type + '/' + uid;
    modal.classList.add('active');
}