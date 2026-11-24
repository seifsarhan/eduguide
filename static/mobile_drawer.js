(function () {
  function initMobileDrawer() {
    const btn = document.getElementById('mobile-menu-btn');
    const sidebar = document.querySelector('.sidebar');
    if (!btn || !sidebar) return;

    let backdrop = document.querySelector('.sidebar-backdrop');
    if (!backdrop) {
      backdrop = document.createElement('div');
      backdrop.className = 'sidebar-backdrop';
      document.body.appendChild(backdrop);
    }

    function toggleMenu(forceClose) {
      if (forceClose === true) {
        sidebar.classList.remove('open');
        backdrop.classList.remove('open');
      } else {
        sidebar.classList.toggle('open');
        backdrop.classList.toggle('open');
      }
    }

    btn.addEventListener('click', () => toggleMenu());
    backdrop.addEventListener('click', () => toggleMenu(true));
    sidebar.querySelectorAll('a.nav-item').forEach((a) => {
      a.addEventListener('click', () => {
        if (window.matchMedia('(max-width: 560px), (max-width: 820px) and (hover: none) and (pointer: coarse)').matches) {
          toggleMenu(true);
        }
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initMobileDrawer);
  } else {
    initMobileDrawer();
  }
})();
