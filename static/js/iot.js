// ============================================================
// ОБЩИЕ ФУНКЦИИ
// ============================================================
function showToast(message, isError) {
    var toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.className = 'toast' + (isError ? ' error' : '');
    toast.style.display = 'block';
    setTimeout(function() { toast.style.display = 'none'; }, 3500);
}

function logout(event) {
    event.preventDefault();
    localStorage.removeItem('master_login');
    localStorage.removeItem('master_name');
    window.location.href = '/logout';
}

// ============================================================
// ЗАГРУЗКА БАГАЖНИКА
// ============================================================
function openBagModal() {
    var modal = document.getElementById('bagModal');
    if (modal) {
        document.getElementById('bagInput').value = '';
        modal.classList.add('active');
    }
}

function closeBagModal() {
    var modal = document.getElementById('bagModal');
    if (modal) modal.classList.remove('active');
}

function closeResultModal() {
    var modal = document.getElementById('resultModal');
    if (modal) modal.classList.remove('active');
}

function closeResultModalAndReload() {
    closeResultModal();
    location.reload();
}

async function loadBag() {
    var input = document.getElementById('bagInput');
    if (!input) return;
    
    var rawText = input.value.trim();
    if (!rawText) {
        showToast('❌ Введите хотя бы один IOT', true);
        return;
    }
    
    // Разбиваем на строки
    var lines = rawText.split(/[\n,;]+/);
    var iots = [];
    
    for (var i = 0; i < lines.length; i++) {
        var line = lines[i].trim();
        if (!line) continue;
        // Нормализуем формат (заменяем разные тире на дефис)
        line = line.replace(/[—–−]/g, '-');
        iots.push(line);
    }
    
    if (iots.length === 0) {
        showToast('❌ Нет валидных IOT', true);
        return;
    }
    
    try {
        var response = await fetch('/api/iot/load_bag', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ iots: iots })
        });
        
        var data = await response.json();
        
        if (data.success) {
            closeBagModal();
            showResult(data);
        } else {
            showToast('❌ Ошибка: ' + (data.error || 'Неизвестная ошибка'), true);
        }
    } catch (error) {
        console.error('Ошибка загрузки багажника:', error);
        showToast('❌ Ошибка соединения', true);
    }
}

// ============================================================
// РЕЗУЛЬТАТ ПРОВЕРКИ
// ============================================================
function showResult(data) {
    var modal = document.getElementById('resultModal');
    var content = document.getElementById('resultContent');
    if (!modal || !content) return;
    
    var html = '';
    
    // Успешные
    if (data.added && data.added.length > 0) {
        html += '<div class="result-section">';
        html += '<div class="result-title success">✅ Добавлено (' + data.added.length + '):</div>';
        for (var i = 0; i < data.added.length; i++) {
            html += '<div class="result-item success">✓ ' + data.added[i] + '</div>';
        }
        html += '</div>';
    }
    
    // Ошибки
    if (data.errors && data.errors.length > 0) {
        html += '<div class="result-section">';
        html += '<div class="result-title error">❌ Ошибки (' + data.errors.length + '):</div>';
        for (var j = 0; j < data.errors.length; j++) {
            var err = data.errors[j];
            html += '<div class="result-item error">';
            html += '✗ ' + err.iot;
            if (err.reason) {
                html += '<span class="reason">' + err.reason + '</span>';
            }
            html += '</div>';
        }
        html += '</div>';
    }
    
    // Дубликаты (уже в багажнике)
    if (data.duplicates && data.duplicates.length > 0) {
        html += '<div class="result-section">';
        html += '<div class="result-title error">⚠️ Уже в багажнике (' + data.duplicates.length + '):</div>';
        for (var k = 0; k < data.duplicates.length; k++) {
            html += '<div class="result-item error">⚠ ' + data.duplicates[k] + '</div>';
        }
        html += '</div>';
    }
    
    // Итог
    var totalAdded = data.added ? data.added.length : 0;
    var totalInput = data.total_input || 0;
    html += '<div class="result-summary">Итого: ' + totalAdded + ' из ' + totalInput + ' добавлено</div>';
    
    content.innerHTML = html;
    modal.classList.add('active');
}

// ============================================================
// СИНХРОНИЗАЦИЯ ИСТОЧНИКА
// ============================================================
async function syncSource() {
    showToast('⏳ Обновление данных...', false);
    try {
        var response = await fetch('/api/iot/sync_source', { method: 'POST' });
        var data = await response.json();
        
        if (data.success) {
            showToast('✅ Данные обновлены (' + data.count + ' IOT)', false);
            setTimeout(function() { location.reload(); }, 1000);
        } else {
            showToast('❌ Ошибка: ' + (data.error || 'Неизвестная'), true);
        }
    } catch (error) {
        showToast('❌ Ошибка соединения', true);
    }
}

// ============================================================
// ЗАМЕНА IOT
// ============================================================
var replaceTarget = { old_iot: null, new_iot: null, frame_number: null, gos: null, darks: null };

function openReplaceModal(oldIot, frameNumber, gos, address, darks) {
    var select = document.getElementById('new_iot_' + oldIot);
    if (!select) return;
    
    var newIot = select.value;
    if (!newIot) {
        showToast('❌ Выберите новый IOT из багажника', true);
        return;
    }
    
    // Проверка что этот IOT ещё в багажнике
    if (BAG_ITEMS.indexOf(newIot) === -1) {
        showToast('❌ Этот IOT уже недоступен в багажнике', true);
        return;
    }
    
    replaceTarget = {
        old_iot: oldIot,
        new_iot: newIot,
        frame_number: frameNumber,
        gos: gos,
        address: address,
        darks: darks
    };
    
    var modal = document.getElementById('replaceModal');
    var content = document.getElementById('replaceContent');
    if (!modal || !content) return;
    
    var html = '';
    html += '<div class="replace-info">';
    html += '<div class="replace-info-row"><span class="label">Номер рамы:</span><span class="value">' + frameNumber + '</span></div>';
    html += '<div class="replace-info-row"><span class="label">Гос номер:</span><span class="value">' + (gos || '-') + '</span></div>';
    html += '<div class="replace-info-row"><span class="label">Даркстор:</span><span class="value">' + darks + '</span></div>';
    html += '<div class="replace-info-row"><span class="label">Адрес:</span><span class="value">' + address + '</span></div>';
    html += '</div>';
    
    html += '<div class="replace-iot-comparison">';
    html += '<span class="replace-old">🔴 ' + oldIot + '</span>';
    html += '<span class="replace-arrow">→</span>';
    html += '<span class="replace-new">🟢 ' + newIot + '</span>';
    html += '</div>';
    
    html += '<div class="replace-warning">⚠️ Не забудьте вернуть старый IOT <strong>' + oldIot + '</strong> в цех!</div>';
    
    content.innerHTML = html;
    modal.classList.add('active');
}

function closeReplaceModal() {
    var modal = document.getElementById('replaceModal');
    if (modal) modal.classList.remove('active');
}

async function confirmReplace() {
    if (!replaceTarget.old_iot || !replaceTarget.new_iot) {
        showToast('❌ Недостаточно данных', true);
        return;
    }
    
    try {
        var response = await fetch('/api/iot/replace', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                old_iot: replaceTarget.old_iot,
                new_iot: replaceTarget.new_iot,
                frame_number: replaceTarget.frame_number,
                gos: replaceTarget.gos,
                darks: replaceTarget.darks,
                address: replaceTarget.address
            })
        });
        
        var data = await response.json();
        
        if (data.success) {
            showToast('✅ IOT заменён: ' + replaceTarget.old_iot + ' → ' + replaceTarget.new_iot, false);
            closeReplaceModal();
            setTimeout(function() { location.reload(); }, 1200);
        } else {
            showToast('❌ Ошибка: ' + (data.error || 'Неизвестная'), true);
        }
    } catch (error) {
        showToast('❌ Ошибка соединения', true);
    }
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

// ============================================================
// УДАЛЕНИЕ IOT ИЗ БАГАЖНИКА
// ============================================================
async function removeFromBag(iot) {
    if (!confirm('Удалить IOT ' + iot + ' из багажника?')) {
        return;
    }
    
    try {
        var response = await fetch('/api/iot/remove_from_bag', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ iot: iot })
        });
        
        var data = await response.json();
        
        if (data.success) {
            showToast('✅ IOT ' + iot + ' удалён', false);
            
            var item = document.getElementById('bag-item-' + iot);
            if (item) {
                item.style.transition = 'opacity 0.3s, transform 0.3s';
                item.style.opacity = '0';
                item.style.transform = 'translateX(-20px)';
                setTimeout(function() {
                    item.remove();
                    
                    var remaining = document.querySelectorAll('.bag-item-row');
                    if (remaining.length === 0) {
                        location.reload();
                    } else {
                        updateBagCount();
                    }
                }, 300);
            } else {
                location.reload();
            }
        } else {
            showToast('❌ Ошибка: ' + (data.error || 'Неизвестная'), true);
        }
    } catch (error) {
        console.error('Ошибка удаления:', error);
        showToast('❌ Ошибка соединения', true);
    }
}

// ============================================================
// ОЧИСТКА БАГАЖНИКА
// ============================================================
async function clearBag() {
    var count = document.querySelectorAll('.bag-item-row').length;
    
    if (count === 0) {
        showToast('❌ Багажник уже пуст', true);
        return;
    }
    
    if (!confirm('Удалить ВСЕ ' + count + ' IOT из багажника?')) {
        return;
    }
    
    try {
        var response = await fetch('/api/iot/clear_bag', {
            method: 'POST'
        });
        
        var data = await response.json();
        
        if (data.success) {
            showToast('✅ Багажник очищен (' + data.count + ' IOT удалено)', false);
            setTimeout(function() { location.reload(); }, 800);
        } else {
            showToast('❌ Ошибка: ' + (data.error || 'Неизвестная'), true);
        }
    } catch (error) {
        console.error('Ошибка очистки:', error);
        showToast('❌ Ошибка соединения', true);
    }
}

// ============================================================
// ОБНОВЛЕНИЕ СЧЁТЧИКА БАГАЖНИКА
// ============================================================
function updateBagCount() {
    var count = document.querySelectorAll('.bag-item-row').length;
    
    var sectionTitle = document.querySelector('.bag-section .section-title span');
    if (sectionTitle) {
        sectionTitle.textContent = count + ' шт.';
    }
    
    var subTitle = document.querySelector('.sub-title strong');
    if (subTitle) {
        subTitle.textContent = count;
    }
}