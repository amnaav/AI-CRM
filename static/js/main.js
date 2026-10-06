/**
 * AI CRM - Core Client-side Scripts
 */

document.addEventListener('DOMContentLoaded', () => {
    // 1. Sidebar Toggle for Mobile & Compact mode
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('mainSidebar');

    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', (e) => {
            e.stopPropagation();
            sidebar.classList.toggle('show');
        });

        // Close sidebar when clicking outside on mobile
        document.addEventListener('click', (e) => {
            if (window.innerWidth <= 992 && sidebar.classList.contains('show')) {
                if (!sidebar.contains(e.target) && e.target !== sidebarToggle) {
                    sidebar.classList.remove('show');
                }
            }
        });
    }

    // 2. Initialize Bootstrap Toasts
    const toastElList = [].slice.call(document.querySelectorAll('.toast'));
    toastElList.map((toastEl) => {
        const toast = new bootstrap.Toast(toastEl, { delay: 5000 });
        toast.show();
    });

    // 3. Global Confirmation Modal Handler for dangerous/critical actions
    const confirmModalEl = document.getElementById('confirmActionModal');
    if (confirmModalEl) {
        const confirmModal = new bootstrap.Modal(confirmModalEl);
        const confirmBtn = document.getElementById('confirmModalBtn');
        const confirmBody = document.getElementById('confirmModalBody');
        const confirmTitle = document.getElementById('confirmModalTitle');

        document.querySelectorAll('[data-confirm]').forEach((button) => {
            button.addEventListener('click', (e) => {
                e.preventDefault();
                const targetForm = button.closest('form');
                const targetHref = button.getAttribute('href');
                const message = button.getAttribute('data-confirm') || 'Are you sure you want to proceed with this action?';
                const title = button.getAttribute('data-confirm-title') || 'Confirm Action';

                confirmBody.textContent = message;
                confirmTitle.textContent = title;

                confirmBtn.onclick = () => {
                    confirmModal.hide();
                    if (targetForm) {
                        targetForm.submit();
                    } else if (targetHref) {
                        window.location.href = targetHref;
                    }
                };

                confirmModal.show();
            });
        });
    }

    // 4. Initialize Bootstrap Tooltips
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map((tooltipTriggerEl) => new bootstrap.Tooltip(tooltipTriggerEl));
});
