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

    if (!login || !password) {
        errorMsg.textContent = 'Заполните логин и пароль';
        errorMsg.classList.add('show');
        return false;
    }

    btn.disabled = true;
    btn.textContent = 'Вход...';

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
            window.location.href = '/auto_login/' + login;
        } else {
            errorMsg.textContent = data.error || 'Неверный логин или пароль';
            errorMsg.classList.add('show');
            btn.disabled = false;
            btn.textContent = 'Войти';
        }
    } catch (error) {
        errorMsg.textContent = 'Нет связи с сервером';
        errorMsg.classList.add('show');
        btn.disabled = false;
        btn.textContent = 'Войти';
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
