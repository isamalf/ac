(function () {
    function getCookie(name) {
        const parts = document.cookie.split('; ');
        for (const p of parts) {
            const idx = p.indexOf('=');
            if (idx > -1 && p.slice(0, idx) === name) {
                return decodeURIComponent(p.slice(idx + 1));
            }
        }
        return '';
    }

    document.addEventListener('DOMContentLoaded', function () {
        const emailForm = document.getElementById('emailForm');
        const passwordForm = document.getElementById('passwordForm');
        const emailInput = document.getElementById('emailInput');
        const passwordInput = document.getElementById('passwordInput');
        const emailSubmitBtn = document.getElementById('emailSubmitBtn');
        const passwordSubmitBtn = document.getElementById('passwordSubmitBtn');
        const verifiedEmailText = document.getElementById('verifiedEmailText');
        const changeEmailBtn = document.getElementById('changeEmailBtn');
        const authAlert = document.getElementById('authAlert');
        const authSubtitle = document.getElementById('authSubtitle');

        let verifiedEmail = '';

        function showError(msg) {
            authAlert.textContent = msg;
            authAlert.hidden = false;
        }

        function clearError() {
            authAlert.textContent = '';
            authAlert.hidden = true;
        }

        emailForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            clearError();
            const email = emailInput.value.trim().toLowerCase();
            if (!email) {
                showError('Please enter your email address.');
                return;
            }

            emailSubmitBtn.disabled = true;
            emailSubmitBtn.textContent = 'Verifying...';

            try {
                const res = await fetch('/api/auth/check-email', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': getCookie('bsr_csrf'),
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ email }),
                });

                const data = await res.json().catch(function () { return {}; });
                if (!res.ok) {
                    showError(data.detail || 'Access restricted.');
                    return;
                }

                verifiedEmail = data.email || email;
                verifiedEmailText.textContent = verifiedEmail;
                emailForm.hidden = true;
                passwordForm.hidden = false;
                authSubtitle.textContent = 'Enter your password to access live strategy telemetry.';
                passwordInput.value = '';
                passwordInput.focus();
            } catch (err) {
                showError('Connection error. Please try again.');
            } finally {
                emailSubmitBtn.disabled = false;
                emailSubmitBtn.textContent = 'Next';
            }
        });

        changeEmailBtn.addEventListener('click', function () {
            clearError();
            verifiedEmail = '';
            passwordInput.value = '';
            passwordForm.hidden = true;
            emailForm.hidden = false;
            authSubtitle.textContent = 'Authorized personnel only. Enter your registered email address to continue.';
            emailInput.focus();
        });

        passwordForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            clearError();
            const password = passwordInput.value;
            if (!password) {
                showError('Please enter your password.');
                return;
            }

            passwordSubmitBtn.disabled = true;
            passwordSubmitBtn.textContent = 'Authenticating...';

            try {
                const res = await fetch('/api/auth/login', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': getCookie('bsr_csrf'),
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ email: verifiedEmail, password }),
                });

                const data = await res.json().catch(function () { return {}; });
                if (!res.ok) {
                    showError(data.detail || 'Invalid credentials.');
                    passwordInput.value = '';
                    passwordInput.focus();
                    return;
                }

                window.location.href = data.redirect || '/dashboard';
            } catch (err) {
                showError('Connection error. Please try again.');
            } finally {
                passwordSubmitBtn.disabled = false;
                passwordSubmitBtn.textContent = 'Sign In';
            }
        });
    });
})();
