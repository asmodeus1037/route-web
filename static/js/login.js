// Проверяем сохранённый логин
var savedLogin = localStorage.getItem('master_login');
if (savedLogin) {
    window.location.href = '/auto_login/' + savedLogin;
}

async function login(event) {
    event.preventDefault();
    
    var login = document.getElementById('loginInput').value.trim().toLowerCase();
    var password = document.getElementById('passwordInput').value.trim();
    var errorMsg = document.getElementById('errorMsg');
    var btn = document.getElementById('loginBtn');
    var spinner = document.getElementById('spinner');
    
    if (!login || !password) {
        errorMsg.textContent = '❌ Заполните все поля!';
        errorMsg.classList.add('show');
        return false;
    }
    
    btn.disabled = true;
    spinner.classList.add('show');
    btn.innerHTML = '<span class="loading show"></span> Вход...';
    
    try {
        var response = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ login: login, password: password })
        });
        
        var data = await response.json();
        
        if (data.success) {
            localStorage.setItem('master_login', login);
            localStorage.setItem('master_name', data.master);
            window.location.href = '/master/' + data.master;
        } else {
            errorMsg.textContent = '❌ ' + data.error;
            errorMsg.classList.add('show');
            btn.disabled = false;
            spinner.classList.remove('show');
            btn.innerHTML = '🔓 Войти';
        }
    } catch (error) {
        errorMsg.textContent = '❌ Ошибка соединения с сервером';
        errorMsg.classList.add('show');
        btn.disabled = false;
        spinner.classList.remove('show');
        btn.innerHTML = '🔓 Войти';
    }
    
    return false;
}

document.getElementById('loginInput').addEventListener('input', function() {
    document.getElementById('errorMsg').classList.remove('show');
});

document.getElementById('passwordInput').addEventListener('input', function() {
    document.getElementById('errorMsg').classList.remove('show');
});

document.getElementById('loginForm').addEventListener('submit', login);