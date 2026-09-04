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
    
    // Если начинается с @ — открываем Telegram
    if (trimmed.startsWith('@')) {
        var username = trimmed.slice(1);
        window.open('https://t.me/' + username, '_blank');
        return;
    }
    
    // Если есть цифры — открываем звонок
    if (trimmed.match(/[\d\(\)\-\+]/)) {
        var phone = trimmed.replace(/[^0-9+]/g, '');
        if (phone) {
            window.location.href = 'tel:' + phone;
        }
        return;
    }
    
    // Если ничего не подошло — просто показываем
    alert('Контакт: ' + contact);
}

// ============================================================
// АКБ И ЗАРЯДКИ — НОВАЯ ЛОГИКА
// ============================================================

// Функция для кнопки "Забираю без замены" — открывает модалку
function openTakenModal(name, darks, uid, action, title, isBattery) {
    var modal = document.getElementById('takenModal');
    if (!modal) return;
    
    document.getElementById('takenModalTitle').textContent = title;
    var form = document.getElementById('takenForm');
    var input = document.getElementById('takenInput');
    input.value = '';
    input.type = 'number';
    input.placeholder = isBattery ? 'Введите количество АКБ...' : 'Введите количество...';
    input.min = 1;
    
    // Настраиваем форму
    form.onsubmit = function(e) {
        e.preventDefault();
        var count = parseInt(input.value);
        if (!count || count <= 0) {
            alert('Укажите количество больше 0!');
            return false;
        }
        
        // Отправляем запрос
        var card = document.getElementById('ticket-' + uid);
        if (card) {
            card.style.transition = 'opacity 0.3s';
            card.style.opacity = '0';
            setTimeout(function() { card.remove(); }, 300);
        }
        
        // Создаём форму для отправки
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

// Функция для кнопки "Куратор не смог предоставить"
function openReplaceNoModal(name, darks, uid) {
    var modal = document.getElementById('reasonModal');
    if (!modal) return;
    
    document.getElementById('modalTitle').textContent = '❌ Куратор не смог предоставить';
    var form = document.getElementById('reasonForm');
    var textarea = document.getElementById('reasonInput');
    textarea.value = '';
    textarea.placeholder = 'Причина (необязательно)...';
    
    form.onsubmit = function(e) {
        e.preventDefault();
        var reason = textarea.value || 'Куратор не предоставил';
        
        var card = document.getElementById('ticket-' + uid);
        if (card) {
            card.style.transition = 'opacity 0.3s';
            card.style.opacity = '0';
            setTimeout(function() { card.remove(); }, 300);
        }
        
        var formData = new FormData();
        formData.append('reason', reason);
        
        fetch('/master/' + name + '/darks/' + darks + '/replace_no/' + uid, {
            method: 'POST',
            body: formData
        });
        
        showToast('❌ Отмечено как "Куратор не предоставил"');
        closeModal('reasonModal');
        return false;
    };
    
    form.action = '/master/' + name + '/darks/' + darks + '/replace_no/' + uid;
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
    } else {
        form.onsubmit = null;
    }
    
    form.action = '/master/' + name + '/darks/' + darks + '/' + type + '/' + uid;
    modal.classList.add('active');
}

function closeModal(modalId) {
    var modal = document.getElementById(modalId);
    if (modal) modal.classList.remove('active');
}

// ============================================================
// ФУНКЦИИ ДЛЯ ТРАНЗИТА
// ============================================================

function editField(btn, className) {
    var input = btn.closest('.field-group').querySelector('.' + className);
    input.readOnly = false;
    input.focus();
    btn.textContent = '💾';
    btn.onclick = function() {
        input.readOnly = true;
        this.textContent = '✏️';
        this.onclick = function() { editField(this, className); };
    };
}

function checkField(btn, className) {
    var input = btn.closest('.field-group').querySelector('.' + className);
    var statusIcon = btn.closest('.field-group').querySelector('.status-icon');
    if (input.value.trim()) {
        statusIcon.textContent = '✅';
        statusIcon.style.color = '#22c55e';
    } else {
        alert('Поле пустое! Сначала заполните данные.');
    }
}

function submitTransitReplace(btn) {
    var form = btn.closest('.transit-form');
    var uid = form.dataset.uid;
    var oldSerial = form.querySelector('.old-serial').value;
    var oldGos = form.querySelector('.old-gos').value;
    var oldIot = form.querySelector('.old-iot').value;
    var newSerial = form.querySelector('.new-serial').value;
    var newGos = form.querySelector('.new-gos').value;
    var newIot = form.querySelector('.new-iot').value;
    
    if (!oldSerial || !oldGos || !oldIot) {
        alert('Проверьте все поля старого велосипеда!');
        return;
    }
    if (!newSerial || !newGos || !newIot) {
        alert('Заполните все поля нового велосипеда!');
        return;
    }
    if (!confirm('Отправить замену велосипеда?')) return;
    
    var card = document.getElementById('ticket-' + uid);
    if (card) {
        card.style.transition = 'opacity 0.3s';
        card.style.opacity = '0';
        setTimeout(function() { card.remove(); }, 300);
    }
    
    var data = {
        uid: uid,
        master: form.dataset.master || '',
        darks_number: form.dataset.darks || '',
        address: form.dataset.address || '',
        old_data: { serial: oldSerial, gos: oldGos, iot: oldIot },
        new_data: { serial: newSerial, gos: newGos, iot: newIot }
    };
    
    fetch('/master/transit/replace', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    showToast('🚲 Замена велосипеда отправлена');
}

// ============================================================
// ЗАКРЫТИЕ МОДАЛОК ПО КЛИКУ ВНЕ
// ============================================================
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