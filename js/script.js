const MODAL_COURSE_FILE = 'html/modal-cours.html';
const MODAL_NEWS_FILE = 'html/modal-news.html';

function openModal(id) {
  const modal = new bootstrap.Modal(document.getElementById(id));
  const contentId = id + 'Content';
  const file = id === 'modalCourse' ? MODAL_COURSE_FILE : MODAL_NEWS_FILE;

  fetch(file)
    .then(res => res.text())
    .then(html => {
      document.getElementById(contentId).innerHTML = html;
      modal.show();
    });
}

// Smooth scroll for anchor links
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
  anchor.addEventListener('click', function (e) {
    e.preventDefault();
    const target = document.querySelector(this.getAttribute('href'));
    if (target) {
      target.scrollIntoView({ behavior: 'smooth' });
    }
  });
});

// Animate elements on scroll
function animateOnScroll() {
  const elements = document.querySelectorAll('.fade-in, .slide-up');
  const windowBottom = window.innerHeight + window.scrollY;

  elements.forEach(el => {
    const elementTop = el.offsetTop;
    if (windowBottom > elementTop + 100) {
      el.classList.add('visible');
    }
  });
}

window.addEventListener('scroll', animateOnScroll);
window.addEventListener('load', animateOnScroll);

// Enhanced form validation with Bootstrap feedback
function validateForm(formId, toastId) {
  const form = document.getElementById(formId);
  if (!form) return;  // Guard clause to prevent errors if form not found
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (!form.checkValidity()) {
      form.classList.add('was-validated');
      return;
    }
    if (formId === 'registerUserForm') {
      const password = form.querySelector('#password');
      const confirmPassword = form.querySelector('#confirmPassword');
      if (password && confirmPassword && password.value !== confirmPassword.value) {
        alert('كلمتا المرور غير متطابقتين');
        return;
      }
    }
    const toast = new bootstrap.Toast(document.getElementById(toastId));
    toast.show();
    form.reset();
    form.classList.remove('was-validated');
  });
}

function initializePageInteractions() {
  // Initialize validation for all forms
  validateForm('registerUserForm', 'registerToast');
  validateForm('loginForm', 'loginToast');

  // Initialize tooltips
  var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
  var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
    return new bootstrap.Tooltip(tooltipTriggerEl);
  });

  // Fix jQuery conflict or error if any
  if (window.jQuery) {
    jQuery.noConflict();
  }
}

document.addEventListener('DOMContentLoaded', function() {
  initializePageInteractions();
});

document.addEventListener('DOMContentLoaded', function() {
  var imageModal = document.getElementById('imageModal');
  if (imageModal) {
    imageModal.addEventListener('show.bs.modal', function (event) {
      var button = event.relatedTarget;
      var imageSrc = button.getAttribute('data-bs-image');
      var caption = button.getAttribute('data-bs-caption');

      var modalImage = imageModal.querySelector('#modalImage');
      var modalCaption = imageModal.querySelector('#modalCaption');

      modalImage.src = imageSrc;
      modalImage.alt = caption;
      modalCaption.textContent = caption;
    });
  }
});
