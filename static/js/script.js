// DOM Elements
const hamburger = document.getElementById('hamburger');
const navLinks = document.getElementById('nav-links');
const header = document.querySelector('.main-header');
const uploadArea = document.getElementById('uploadArea');
const fileInput = document.getElementById('fileInput');
const progressContainer = document.getElementById('progressContainer');
const progressBar = document.getElementById('progressBar');
const loadingOverlay = document.getElementById('loadingOverlay');
const successNotification = document.getElementById('successNotification');
const toolCards = document.querySelectorAll('.tool-card');

// Header scroll effect
window.addEventListener('scroll', () => {
    if (window.scrollY > 50) {
        header.classList.add('scrolled');
    } else {
        header.classList.remove('scrolled');
    }
});

// Hamburger menu toggle
hamburger.addEventListener('click', (e) => {
    e.stopPropagation();
    hamburger.classList.toggle('active');
    navLinks.classList.toggle('open');
});

// Close mobile menu when clicking a link
document.querySelectorAll('.header-nav a').forEach(link => {
    link.addEventListener('click', () => {
        hamburger.classList.remove('active');
        navLinks.classList.remove('open');
    });
});

// Close mobile menu when clicking outside
document.addEventListener('click', (e) => {
    if (!header.contains(e.target) && navLinks.classList.contains('open')) {
        hamburger.classList.remove('active');
        navLinks.classList.remove('open');
    }
});

// Active navigation link on scroll
const sections = document.querySelectorAll('section[id]');
window.addEventListener('scroll', () => {
    let current = '';
    sections.forEach(section => {
        const sectionTop = section.offsetTop;
        const sectionHeight = section.clientHeight;
        if (window.scrollY >= sectionTop - 200) {
            current = section.getAttribute('id');
        }
    });

    document.querySelectorAll('.header-nav a').forEach(a => {
        a.classList.remove('active');
        if (a.getAttribute('href') === `#${current}`) {
            a.classList.add('active');
        }
    });
});

// Drag & Drop
['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
    uploadArea.addEventListener(eventName, preventDefaults, false);
    document.body.addEventListener(eventName, preventDefaults, false);
});

function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

['dragenter', 'dragover'].forEach(eventName => {
    uploadArea.addEventListener(eventName, () => {
        uploadArea.classList.add('dragover');
    }, false);
});

['dragleave', 'drop'].forEach(eventName => {
    uploadArea.addEventListener(eventName, () => {
        uploadArea.classList.remove('dragover');
    }, false);
});

uploadArea.addEventListener('drop', handleDrop, false);

function handleDrop(e) {
    const dt = e.dataTransfer;
    const files = dt.files;
    handleFiles({ target: { files } });
}

// Upload area click - only if not clicking the button
uploadArea.addEventListener('click', (e) => {
    if (e.target.classList.contains('upload-btn') || e.target.closest('.upload-btn')) {
        return;
    }
    fileInput.click();
});

// Upload button click
document.querySelector('.upload-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    fileInput.click();
});

fileInput.addEventListener('change', handleFiles);

// Tool selection
let selectedTool = null;

toolCards.forEach(card => {
    card.addEventListener('click', () => {
        const tool = card.dataset.tool;
        if (!tool) return;

        selectedTool = tool;
        toolCards.forEach(c => c.classList.remove('selected'));
        card.classList.add('selected');

        // Scroll to upload area
        uploadArea.scrollIntoView({ behavior: 'smooth' });
        document.querySelector('.upload-area h3').textContent =
            `Upload files to ${card.querySelector('h4').textContent}`;
    });
});

function handleFiles(e) {
    const files = e.target.files;

    if (!selectedTool) {
        showNotification('Please select a tool first', 'error');
        return;
    }

    if (files.length > 0) {
        uploadFiles(files);
    }
}

function uploadFiles(files) {
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
        formData.append('files', files[i]);
    }
    formData.append('tool', selectedTool);

    // Show loading
    loadingOverlay.style.display = 'flex';
    progressContainer.style.display = 'block';

    // Simulate progress (for UX)
    let progress = 0;
    const interval = setInterval(() => {
        progress += Math.random() * 30;
        if (progress >= 100) {
            progress = 100;
            clearInterval(interval);
        }
        progressBar.style.width = progress + '%';
    }, 300);

    fetch('/convert', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        clearInterval(interval);
        progressBar.style.width = '100%';

        setTimeout(() => {
            loadingOverlay.style.display = 'none';
            progressContainer.style.display = 'none';
            progressBar.style.width = '0%';

            if (data.success) {
                showNotification('File converted successfully!');
                // Trigger download
                const a = document.createElement('a');
                a.href = data.download_url;
                a.download = data.filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
            } else {
                showNotification(data.error || 'Conversion failed', 'error');
            }
        }, 1000);
    })
    .catch(error => {
        clearInterval(interval);
        loadingOverlay.style.display = 'none';
        progressContainer.style.display = 'none';
        progressBar.style.width = '0%';
        showNotification('An error occurred', 'error');
        console.error(error);
    });
}

// Notification
function showNotification(message, type = 'success') {
    const notification = document.getElementById('successNotification');
    const icon = notification.querySelector('i');
    const span = notification.querySelector('span');

    if (type === 'success') {
        icon.className = 'fas fa-check-circle';
        notification.style.background = 'linear-gradient(135deg, #00b09b, #96c93d)';
    } else {
        icon.className = 'fas fa-exclamation-circle';
        notification.style.background = 'linear-gradient(135deg, #ff6b6b, #e63946)';
    }

    span.textContent = message;
    notification.classList.add('show');

    setTimeout(() => {
        notification.classList.remove('show');
    }, 3000);
}

// Auth Modals
const signupModal = document.getElementById('signup-modal');
const loginModal = document.getElementById('login-modal');

document.getElementById('open-signup')?.addEventListener('click', (e) => {
    e.preventDefault();
    signupModal.style.display = 'flex';
});

document.getElementById('open-login')?.addEventListener('click', (e) => {
    e.preventDefault();
    loginModal.style.display = 'flex';
});

document.getElementById('close-signup').addEventListener('click', () => {
    signupModal.style.display = 'none';
});

document.getElementById('close-login').addEventListener('click', () => {
    loginModal.style.display = 'none';
});

window.addEventListener('click', (e) => {
    if (e.target === signupModal) signupModal.style.display = 'none';
    if (e.target === loginModal) loginModal.style.display = 'none';
});

document.getElementById('switch-login').addEventListener('click', () => {
    signupModal.style.display = 'none';
    loginModal.style.display = 'flex';
});

document.getElementById('switch-signup').addEventListener('click', () => {
    loginModal.style.display = 'none';
    signupModal.style.display = 'flex';
});

// Show modal if error exists
window.addEventListener('DOMContentLoaded', () => {
    if (document.querySelector('.modal-error')) {
        if (document.querySelector('.modal-error').innerText.toLowerCase().includes('login')) {
            loginModal.style.display = 'flex';
        } else {
            signupModal.style.display = 'flex';
        }
    }
});

// Smooth scrolling
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
        e.preventDefault();
        const target = document.querySelector(this.getAttribute('href'));
        if (target) {
            target.scrollIntoView({ behavior: 'smooth' });
        }
    });
});