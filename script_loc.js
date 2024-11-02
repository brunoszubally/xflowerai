document.addEventListener('DOMContentLoaded', () => {
    // DOM elemek
    const modal = document.getElementById('email-modal');
    const sendEmailButton = document.getElementById('send-email-button');
    const sendButtonModal = document.getElementById('send-button-modal');
    const cancelButtonModal = document.getElementById('cancel-button-modal');
    const modalLoading = document.getElementById('modal-loading');
    const input = document.getElementById('input');
    const sendButton = document.getElementById('send-button');
    const messagesContainer = document.getElementById('messages');
    const loadingContainer = document.getElementById('loading-container');
    const loadingText = document.getElementById('loading-text');

    // Loading szövegek
    const loadingTexts = [
        "Kávét főzünk a diagramhoz...",
        "A vonalak helyére húzása folyamatban...",
        "Összekapcsoljuk a pontokat...",
        "Még pár pixel, és készen vagyunk...",
        "Egy pillanat, éppen átírjuk a valóságot...",
        "Üzleti varázslás folyamatban...",
        "Várj, keresünk egy elveszett algoritmust...",
        "Lézerrel égetjük be az adatokat...",
        "Egy kis türelmet, éppen diagramra vadászunk...",
        "Gyorsan összekötjük a szálakat...",
        "Töltjük a varázskódot a rendszerbe...",
        "Ez a diagram most tényleg bonyolult...",
        "Az információk szépen sorban rendeződnek...",
        "A diagramformátumban kiszolgált világot készítjük elő...",
        "Elhelyezünk néhány tökéletes adatpontot...",
        "A folyamatok zárt hurkot kapnak...",
        "Lépésről lépésre összerakjuk...",
        "Üzleti titkokat szerkesztünk...",
        "Új adatokat építünk be...",
        "Csak egy perc, elrendezzük a folyamatokat...",
        "Diagramot faragunk a bitekből..."
    ];

    let loadingInterval;

    // Segédfüggvények
    function toggleModal(show) {
        if (modal) {
            modal.style.display = show ? 'flex' : 'none';
            if (modalLoading) {
                modalLoading.style.display = 'none';
            }
        }
    }

    function showError(field, message) {
        const errorDiv = document.getElementById(`${field}-error`);
        if (errorDiv) {
            errorDiv.textContent = message;
            errorDiv.classList.add('show');
        }
    }

    function clearError(field) {
        const errorElement = document.getElementById(`${field}-error`);
        if (errorElement) {
            errorElement.textContent = '';
            errorElement.classList.remove('show');
        }
    }

    function addMessage(content, isUser = false) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${isUser ? 'user-message' : 'assistant-message'}`;
        messageDiv.textContent = content;
        messagesContainer.appendChild(messageDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    function startLoadingTextRotation() {
        let index = Math.floor(Math.random() * loadingTexts.length);
        loadingText.textContent = loadingTexts[index];
        loadingInterval = setInterval(() => {
            index = Math.floor(Math.random() * loadingTexts.length);
            loadingText.textContent = loadingTexts[index];
        }, 3000);
    }

    function stopLoadingTextRotation() {
        clearInterval(loadingInterval);
    }

    // Hibaüzenetek törlése
    function clearErrors() {
        const errorDivs = document.querySelectorAll('.error-message');
        errorDivs.forEach(div => {
            div.textContent = '';
            div.classList.remove('show');
        });
    }

    // Fő függvények
    async function generateDiagram() {
        try {
            const userMessage = input.value.trim();
            if (!userMessage) return;

            input.value = '';
            loadingContainer.style.display = 'flex';
            sendButton.disabled = true;
            addMessage(userMessage, true);

            startLoadingTextRotation();

            console.log("Küldés a szervernek:", userMessage);

            const response = await fetch('http://localhost:5000/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ message: userMessage })
            });

            console.log("Szerver válasz státusz:", response.status);

            // Ha nem 200-as válasz jött
            if (!response.ok) {
                const errorData = await response.json();
                console.error("Szerver hiba részletek:", errorData);
                throw new Error(errorData.error || 'Szerver hiba történt');
            }

            const data = await response.json();
            
            if (data.error) {
                throw new Error(data.error);
            }

            const svgContainer = document.getElementById('svg-container');
            svgContainer.innerHTML = `<img src="${data.image}" alt="Folyamatábra" class="diagram-image">`;
            
            sendEmailButton.style.display = 'block';

        } catch (error) {
            console.error('Hiba:', error);
            addMessage('Hiba történt a folyamatábra generálása során. Kérjük, próbálja újra!');
        } finally {
            loadingContainer.style.display = 'none';
            sendButton.disabled = false;
            stopLoadingTextRotation();
        }
    }

    async function sendEmail() {
        const nameInput = document.getElementById('name-input');
        const emailInput = document.getElementById('email-input');
        const privacyCheckbox = document.getElementById('privacy-checkbox');
        const modalMainContent = document.querySelector('.modal-main-content');

        clearErrors();

        // Validáció
        let isValid = true;
        if (!nameInput.value.trim()) {
            showError('name', 'A név megadása kötelező');
            isValid = false;
        }
        if (!emailInput.value.trim()) {
            showError('email', 'Az e-mail cím megadása kötelező');
            isValid = false;
        }
        if (!privacyCheckbox.checked) {
            showError('privacy', 'El kell fogadnod az adatvédelmi nyilatkozatot');
            isValid = false;
        }
        if (!isValid) {
            modalMainContent.style.display = 'block';
            modalLoading.style.display = 'none';
            return;
        }

        modalMainContent.style.display = 'none';
        modalLoading.style.display = 'flex';

        try {
            const svgImage = document.querySelector('#svg-container img');
            if (!svgImage || !svgImage.src) {
                throw new Error('Nem található kép a küldéshez');
            }

            console.log("Kép adat ellenőrzése:", svgImage.src.substring(0, 100)); // Debug log

            const response = await fetch('http://localhost:5000/send-email', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    name: nameInput.value.trim(),
                    email: emailInput.value.trim(),
                    image: svgImage.src
                })
            });

            const data = await response.json();
            
            if (!response.ok) {
                throw new Error(data.error || 'Hiba történt az e-mail küldése során');
            }

            if (data.error) {
                throw new Error(data.error);
            }

            toggleModal(false);
            nameInput.value = '';
            emailInput.value = '';
            privacyCheckbox.checked = false;

        } catch (error) {
            console.error('Részletes hiba:', error);
            modalMainContent.style.display = 'block';
            modalLoading.style.display = 'none';
            showError('email', error.message || 'Hiba történt az e-mail küldése során. Kérjük, próbáld újra!');
        }
    }

    // Event listeners
    if (sendButton) {
        sendButton.addEventListener('click', generateDiagram);
    }

    if (input) {
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                generateDiagram();
            }
        });
    }

    if (sendEmailButton) {
        sendEmailButton.addEventListener('click', () => toggleModal(true));
    }

    if (sendButtonModal) {
        sendButtonModal.addEventListener('click', sendEmail);
    }

    if (cancelButtonModal) {
        cancelButtonModal.addEventListener('click', () => toggleModal(false));
    }

    // SVG konténer figyelése e-mail gomb megjelenítéséhez
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.type === 'childList' && document.querySelector('#svg-container svg')) {
                sendEmailButton.style.display = 'block';
            }
        });
    });

    observer.observe(document.getElementById('svg-container'), { childList: true });
});

