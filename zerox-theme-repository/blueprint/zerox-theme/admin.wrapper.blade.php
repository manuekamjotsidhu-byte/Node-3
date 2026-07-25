<script>
(() => {
  const addZeroXThemeTab = () => {
    if (document.querySelector('[data-zerox-theme-admin-link]')) return true;
    const nodesLink = document.querySelector('a[href*="/admin/nodes"]');
    const serversLink = document.querySelector('a[href*="/admin/servers"]');
    const anchor = nodesLink || serversLink;
    const list = anchor?.closest('ul') || document.querySelector('#sidebar ul.nav, .sidebar ul.nav, ul.nav.nav-pills');
    if (!list) return false;
    const item = document.createElement('li');
    item.dataset.zeroxThemeAdminLink = 'true';
    if (location.pathname.includes('/admin/extensions/zeroxtheme')) item.classList.add('active');
    item.innerHTML = '<a href="/admin/extensions/zeroxtheme"><i class="fa fa-paint-brush"></i> <span>ZeroX Theme</span></a>';
    const reference = anchor?.closest('li');
    reference ? reference.insertAdjacentElement('afterend', item) : list.appendChild(item);
    return true;
  };
  if (!addZeroXThemeTab()) {
    const observer = new MutationObserver(() => { if (addZeroXThemeTab()) observer.disconnect(); });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }
})();
</script>
