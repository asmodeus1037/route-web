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
// ФУНКЦИИ ДЛЯ MASTER_DARKS
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
            fetch('/master/' + name + '/darks/' + darks + '/evacuation/' + uid, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: 'reason=' + encodeURIComponent(reason)
            });
            showToast('🚚 Заявка отправлена на эвакуацию');
            closeModal();
            return false;
        };
    } else {
        form.onsubmit = null;
    }
    
    form.action = '/master/' + name + '/darks/' + darks + '/' + type + '/' + uid;
    modal.classList.add('active');
}

function closeModal() {
    var modal = document.getElementById('reasonModal');
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
// ЗАКРЫТИЕ МОДАЛКИ ПО КЛИКУ ВНЕ
// ============================================================
document.addEventListener('DOMContentLoaded', function() {
    var modal = document.getElementById('reasonModal');
    if (modal) {
        modal.addEventListener('click', function(e) {
            if (e.target === this) closeModal();
        });
    }
});