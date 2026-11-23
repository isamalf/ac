(function () {
    function typeWriter(element, text, speed, onComplete) {
        let i = 0;
        element.textContent = '';
        function step() {
            if (i < text.length) {
                element.innerHTML =
                    text.substring(0, i + 1) +
                    '<span style="border-right:2px solid #ffffff;margin-left:2px;"></span>';
                i += 1;
                window.setTimeout(step, speed);
            } else {
                element.innerHTML =
                    text +
                    '<span style="border-right:2px solid #ffffff;margin-left:2px;"></span>';
                window.setTimeout(function () {
                    element.textContent = text;
                    if (onComplete) onComplete();
                }, 650);
            }
        }
        step();
    }

    document.addEventListener('DOMContentLoaded', function () {
        const typedLogo = document.getElementById('typedLogo');
        const loadingScreen = document.getElementById('loadingScreen');
        const mainContent = document.getElementById('mainContent');

        if (!typedLogo || !loadingScreen || !mainContent) return;

        typeWriter(typedLogo, 'BEARDSLEE ST. RESEARCH', 65, function () {
            loadingScreen.style.opacity = '0';
            window.setTimeout(function () {
                loadingScreen.style.display = 'none';
                mainContent.style.opacity = '1';
            }, 550);
        });
    });
})();
